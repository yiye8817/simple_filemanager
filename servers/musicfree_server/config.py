"""musicfree_server 配置 (全部从环境变量读取, 方便服务托管 + Docker 部署)。"""
from __future__ import annotations

import os


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or v == '':
        return default
    return v.lower() in ('1', 'true', 'yes', 'on')


class Config:
    """实例化一次, 启动时打印生效配置。"""

    def __init__(self) -> None:
        # ----- 必填或半必填 -----
        # 自身对外可访问的 base URL; 用来在日志/健康检查里告诉用户怎么把插件指到这里。
        # 例: http://192.168.1.100:8765 或 http://your-frpc-host:28080
        self.public_base_url: str = os.environ.get('MFS_PUBLIC_BASE_URL', '').rstrip('/')

        # ----- webhook 安全 -----
        # 来自 file_manager 的 webhook secret; 配了之后必须带 X-Signature 头
        self.webhook_secret: str = os.environ.get('MFS_WEBHOOK_SECRET', '')

        # ----- 持久化 -----
        self.state_path: str = os.environ.get('MFS_STATE_PATH', './data/state.json')

        # ----- 客户端鉴权 (查询接口) -----
        # 如果设置了, /api/music/* 都需要 X-Music-Token 头或 ?token= 才能访问;
        # 不配则任何人都能查清单 (因为 file_manager 已经做了下载鉴权, 清单泄露危害有限).
        self.music_query_token: str = os.environ.get('MFS_QUERY_TOKEN', '')

        # ----- 实际播放 URL 改写 -----
        # webhook 里给到的 download_url 默认指向 file_manager 内网地址;
        # 如果 MusicFree 客户端在外网, 需要把 host 替换成可访问的地址。
        # 形如 MFS_REWRITE_URL_FROM=http://127.0.0.1:5000  MFS_REWRITE_URL_TO=https://files.example.com
        self.rewrite_url_from: str = os.environ.get('MFS_REWRITE_URL_FROM', '').rstrip('/')
        self.rewrite_url_to: str = os.environ.get('MFS_REWRITE_URL_TO', '').rstrip('/')

        # ----- 透传 X-API-Key header -----
        # MusicFree 拉流时需要 file_manager 的 API Key; 如果 webhook URL 已经是 public-签名 URL
        # (file_manager 的 /api/public/download/<id>?sig=...) 则不需要 header, 可不配。
        self.file_manager_api_key: str = os.environ.get('MFS_FILE_MANAGER_API_KEY', '')

        # ----- 日志 -----
        self.log_level: str = os.environ.get('MFS_LOG_LEVEL', 'INFO').upper()

    def rewrite(self, url: str | None) -> str | None:
        """根据 rewrite_url_from/to 规则把内网 URL 改成外网可访问 URL。"""
        if not url or not self.rewrite_url_from or not self.rewrite_url_to:
            return url
        if url.startswith(self.rewrite_url_from):
            return self.rewrite_url_to + url[len(self.rewrite_url_from):]
        return url

    def as_summary(self) -> dict:
        """脱敏后的配置摘要 — 写到日志/health 端点都安全。"""
        def _mask(s: str) -> str:
            if not s:
                return ''
            if len(s) <= 6:
                return '***'
            return s[:2] + '***' + s[-2:]
        return {
            'public_base_url': self.public_base_url or '(未配置)',
            'webhook_secret': '(已配置)' if self.webhook_secret else '(未配置, webhook 不验签)',
            'state_path': self.state_path,
            'music_query_token': _mask(self.music_query_token) or '(未配置, /api/music/* 公开访问)',
            'rewrite_url_from': self.rewrite_url_from or '(无)',
            'rewrite_url_to': self.rewrite_url_to or '(无)',
            'file_manager_api_key': _mask(self.file_manager_api_key) or '(未配置, 播放 URL 不附带 X-API-Key)',
            'log_level': self.log_level,
        }
