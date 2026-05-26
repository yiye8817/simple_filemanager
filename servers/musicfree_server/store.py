"""musicfree_server 的内部清单存储 (line-of-sight: in-memory + JSON 持久化)。

为什么不用真正的 DB?
    这个服务的状态完全可以从 webhook 增量重建 + 偶尔从 file_manager 全量同步,
    JSON dump 已经足够; 也方便在「服务托管」沙箱里跑 (无需 sqlite 路径配置)。
    线程安全用一把 RLock; flush 节流避免每条 webhook 都落盘。

数据形态:
    Track   = 一首音频 (id 用 file_manager 的 file.id; 全局唯一)
    Sheet   = 一个目录 (id 用 file_manager 的 folder file.id)
              -> 是个聚合视图, 不显式存; sheets() 实时按 parent_id 聚合
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional


logger = logging.getLogger(__name__)

# 文件管理 API 把以下扩展名视为 audio; 这里跟它对齐, 避免 file_type 不是 'audio'
# 但实际是 mp3 的情况漏过 (file_manager 早期记录可能没补 file_type)。
AUDIO_EXTS = {'.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a'}


def _is_audio(name: str, file_type: Optional[str]) -> bool:
    if (file_type or '').lower() == 'audio':
        return True
    _stem, ext = os.path.splitext(name or '')
    return ext.lower() in AUDIO_EXTS


@dataclass
class Track:
    """音频文件元信息 — 直接对应 file_manager webhook payload 的 ``file`` 字段。"""
    id: int                       # file_manager 的 file.id
    name: str                     # 含扩展名
    path: str
    size: int = 0
    file_type: str = 'audio'
    parent_id: Optional[int] = None
    parent_name: Optional[str] = None  # 用作 album
    modified_at: Optional[str] = None
    download_url: Optional[str] = None  # 直接从 webhook 拿 (file_manager 提供)
    serve_url: Optional[str] = None     # 流式 URL (登录态用, 不能给客户端拉流)
    # file_manager 的 /api/external/download/<id>; 走 @api_key_required,
    # 配合 MFS_FILE_MANAGER_API_KEY 头才是 MusicFree 客户端真正能用的拉流 URL。
    # 老版本 file_manager webhook 不带这个字段时, app 会按路径规则从 download_url
    # 兜底推导出来 (见 app._streamable_url)。
    external_download_url: Optional[str] = None
    # 同目录 .lrc 的内容缓存; 上传 .lrc 时若开 modified 事件会被同步更新
    lyric: Optional[str] = None
    # 额外: 接收时间戳, 用于排序"最新加入"
    added_at: float = field(default_factory=lambda: time.time())

    @property
    def title(self) -> str:
        stem, _ext = os.path.splitext(self.name)
        return stem or self.name


@dataclass
class Sheet:
    """以 parent_id 聚合出来的 "歌单"。"""
    id: int
    name: str
    path: Optional[str] = None
    track_ids: List[int] = field(default_factory=list)

    @property
    def works_num(self) -> int:
        return len(self.track_ids)


class TrackStore:
    """线程安全 + 节流 flush 的清单存储。"""

    FLUSH_INTERVAL_S = 2.0

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._tracks: Dict[int, Track] = {}
        self._folder_name_cache: Dict[int, str] = {}
        # 持久化用的 watch 上下文: 哪条 watch 来的, 用来做 "rebind" 校验 (可选)
        self._meta: Dict[str, object] = {'created_at': time.time(), 'events_seen': 0}

        self._dirty = False
        self._last_flush_at = 0.0
        self._flush_lock = threading.Lock()

        self.load()

    # ------------------------------------------------------------------ I/O
    def load(self) -> None:
        if not os.path.exists(self.path):
            logger.info('state file 不存在, 从空状态启动: %s', self.path)
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as fp:
                data = json.load(fp)
            with self._lock:
                self._tracks = {
                    int(k): Track(**v) for k, v in (data.get('tracks') or {}).items()
                }
                self._folder_name_cache = {
                    int(k): v for k, v in (data.get('folder_names') or {}).items()
                }
                self._meta.update(data.get('meta') or {})
            logger.info('载入 %d 条 track 从 %s', len(self._tracks), self.path)
        except Exception:  # noqa: BLE001
            logger.exception('载入 state 失败, 已清空 (旧文件保留)')

    def flush(self, force: bool = False) -> None:
        """落盘; 默认带节流, force=True 时立即写。"""
        now = time.time()
        with self._lock:
            if not force and not self._dirty:
                return
            if not force and (now - self._last_flush_at) < self.FLUSH_INTERVAL_S:
                return
            snapshot = {
                'tracks': {str(k): asdict(v) for k, v in self._tracks.items()},
                'folder_names': {str(k): v for k, v in self._folder_name_cache.items()},
                'meta': dict(self._meta),
            }
            self._dirty = False
            self._last_flush_at = now
        # 在锁外写; 减少阻塞 (snapshot 是深拷贝级别的字典)
        with self._flush_lock:
            tmp = self.path + '.tmp'
            os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as fp:
                json.dump(snapshot, fp, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    # ----------------------------------------------------------- 事件入口
    def apply_event(self, event: str, file_data: dict, watch_data: Optional[dict] = None) -> str:
        """按 file_manager webhook payload 应用一条事件。

        Returns: 'added' / 'updated' / 'removed' / 'ignored'。
        """
        if not file_data:
            return 'ignored'
        is_dir = bool(file_data.get('is_dir'))
        if is_dir:
            # 目录变化: 更新名字缓存 (rename 时让后续 track 显示新 album 名)
            fid = file_data.get('id')
            if fid is not None:
                with self._lock:
                    if event == 'deleted':
                        self._folder_name_cache.pop(int(fid), None)
                    else:
                        self._folder_name_cache[int(fid)] = file_data.get('name') or ''
                    self._meta['events_seen'] = int(self._meta.get('events_seen', 0)) + 1
                    self._dirty = True
                self.flush()
            return 'ignored'

        name = file_data.get('name') or ''
        # .lrc 文件 — 不进 tracks 表, 但找同目录 stem 相同的 track 把歌词附上
        if name.lower().endswith('.lrc'):
            return self._apply_lrc(event, file_data)

        if not _is_audio(name, file_data.get('file_type')):
            return 'ignored'

        fid = file_data.get('id')
        if fid is None:
            return 'ignored'
        fid = int(fid)

        with self._lock:
            self._meta['events_seen'] = int(self._meta.get('events_seen', 0)) + 1
            if event == 'deleted':
                if fid in self._tracks:
                    del self._tracks[fid]
                    self._dirty = True
                    return 'removed'
                return 'ignored'

            # created / modified — 同一逻辑: upsert
            parent_id = file_data.get('parent_id')
            parent_name = None
            if watch_data:
                # webhook 里 watch.folder_name 表示监听根; 但更准确的"专辑名" 应该是
                # 直接父目录, 而非监听根。webhook payload 没给父目录名, 退而求其次:
                # 如果 parent_id 等于 watch.folder_id, 用 watch.folder_name
                if parent_id is not None and parent_id == watch_data.get('folder_id'):
                    parent_name = watch_data.get('folder_name')
            if parent_name is None and parent_id is not None:
                parent_name = self._folder_name_cache.get(int(parent_id))

            t = Track(
                id=fid,
                name=name,
                path=file_data.get('path') or '',
                size=int(file_data.get('size') or 0),
                file_type=file_data.get('file_type') or 'audio',
                parent_id=int(parent_id) if parent_id is not None else None,
                parent_name=parent_name,
                modified_at=file_data.get('modified_at'),
                download_url=file_data.get('download_url'),
                serve_url=file_data.get('serve_url'),
                external_download_url=file_data.get('external_download_url'),
                lyric=(self._tracks[fid].lyric if fid in self._tracks else None),
            )
            was = fid in self._tracks
            self._tracks[fid] = t
            self._dirty = True

            if t.parent_id is not None:
                self._folder_name_cache.setdefault(t.parent_id, t.parent_name or '')

        self.flush()
        return 'updated' if was else 'added'

    def _apply_lrc(self, event: str, file_data: dict) -> str:
        """.lrc 同目录配对 — 用 stem 在同 parent 内找 track 注入 lyric_url。"""
        parent_id = file_data.get('parent_id')
        if parent_id is None:
            return 'ignored'
        name = file_data.get('name') or ''
        stem, _ext = os.path.splitext(name)
        with self._lock:
            for t in self._tracks.values():
                if t.parent_id == int(parent_id):
                    t_stem, _ = os.path.splitext(t.name)
                    if t_stem == stem:
                        if event == 'deleted':
                            t.lyric = None
                        else:
                            # 标记一个 marker: 真实内容用 lyric_url 懒加载;
                            # 这里把 download_url 当 fetch URL, 由调用方下载
                            t.lyric = file_data.get('download_url') or '__lrc_placeholder__'
                        self._dirty = True
        self.flush()
        return 'updated'

    # ---------------------------------------------------------- 查询接口
    def get_track(self, track_id: int) -> Optional[Track]:
        with self._lock:
            return self._tracks.get(int(track_id))

    def all_tracks(self) -> List[Track]:
        with self._lock:
            return list(self._tracks.values())

    def sheets(self) -> List[Sheet]:
        """以 parent_id 聚合得到 sheet 列表; 按 works_num 倒序。"""
        with self._lock:
            grouped: Dict[int, List[int]] = {}
            name_map: Dict[int, str] = {}
            path_map: Dict[int, str] = {}
            for t in self._tracks.values():
                if t.parent_id is None:
                    continue
                grouped.setdefault(t.parent_id, []).append(t.id)
                name_map.setdefault(t.parent_id, t.parent_name or self._folder_name_cache.get(t.parent_id, ''))
                if t.path:
                    parent_path = os.path.dirname(t.path)
                    path_map.setdefault(t.parent_id, parent_path)
            sheets = [
                Sheet(id=pid, name=name_map.get(pid, ''), path=path_map.get(pid),
                      track_ids=sorted(ids))
                for pid, ids in grouped.items()
            ]
            sheets.sort(key=lambda s: s.works_num, reverse=True)
            return sheets

    def sheet(self, sheet_id: int) -> Optional[Sheet]:
        sheet_id = int(sheet_id)
        for s in self.sheets():
            if s.id == sheet_id:
                return s
        return None

    def search_tracks(self, query: str, limit: int = 50) -> List[Track]:
        if not query:
            return []
        q = query.lower()
        with self._lock:
            hits = [
                t for t in self._tracks.values()
                if q in (t.name or '').lower() or q in (t.title or '').lower()
            ]
        hits.sort(key=lambda t: -t.added_at)  # 最新加入排前面
        return hits[:limit]

    def search_sheets(self, query: str) -> List[Sheet]:
        if not query:
            return []
        q = query.lower()
        return [s for s in self.sheets() if q in (s.name or '').lower()]

    def stats(self) -> dict:
        with self._lock:
            t_cnt = len(self._tracks)
            s_cnt = len({t.parent_id for t in self._tracks.values() if t.parent_id is not None})
            return {
                'tracks': t_cnt,
                'sheets': s_cnt,
                'events_seen': int(self._meta.get('events_seen', 0)),
                'state_path': self.path,
                'flushed_at': self._last_flush_at,
            }

    # ----------------------------------------------------------- 管理操作
    def clear(self) -> None:
        with self._lock:
            self._tracks.clear()
            self._folder_name_cache.clear()
            self._meta['events_seen'] = 0
            self._dirty = True
        self.flush(force=True)
