#!/usr/bin/env python3
"""
清理脚本：递归删除 ``File`` 表里的两类脏数据。

本脚本支持两种清理模式（``--mode`` 选择），底层都会做**递归删除**：

1. ``null-parent`` —— 清除 ``parent_id IS NULL`` 的"孤儿子树"
   --------------------------------------------------------
   正常情况下层级关系应为：

   - 用户根目录占位 ``parent_id = 0``（``name = '/'``）
   - 普通文件/目录 ``parent_id`` 指向其所属目录的 ``File.id``

   历史上若直接在数据库层删除某个目录、或导入数据时没补 ``parent_id``，会留
   下 ``parent_id IS NULL`` 的"孤儿"。前端 ``file_routes.get_files`` 有兜底
   把它们并入根目录展示，但本质属于脏数据。

2. ``missing-physical`` —— 清除"磁盘上不存在对应文件/目录"的记录
   -----------------------------------------------------------
   DB 里有记录，但 ``uploads/<user_id>/<path>`` 实际不存在，属于"反向孤儿"。
   常见来源：

   - 人工或脚本绕过 API 直接删了磁盘文件，未清 DB
   - 同名重建过程中老物理目录被外部清掉但旧 DB 行没及时回收

   清理时只对"顶层缺失"节点（即父记录仍存在或父磁盘文件还在的最浅缺失节点）
   做入口，自动递归其子孙。

   两类记录天然不该被该模式碰到，已硬编码跳过：

   - **用户根占位**（``parent_id=0, name='/'``）即便物理目录还没建出来也跳过
     —— 它是延迟创建的占位
   - **虚拟类型**（``file_type='linker'``，由 ``/api/add_linker`` 创建，``path``
     字段存 URL）天然没有对应磁盘文件 —— 见 ``_VIRTUAL_FILE_TYPES``

3. ``all`` —— 上面两步串行跑

不会触发 webhook
----------------
两种模式都不会调 ``webhook_service.notify_file_event``，避免后台维护操作骚
扰订阅了 deleted 事件的业务方。需要补发通知请走正常的删除 API。

用法
----
::

    # dry-run 默认看 null-parent 影响范围
    ./yenv/bin/python scripts/cleanup_orphan_files.py --dry-run

    # 实际清 null-parent
    ./yenv/bin/python scripts/cleanup_orphan_files.py

    # 清"磁盘不存在"那一类
    ./yenv/bin/python scripts/cleanup_orphan_files.py --mode missing-physical --dry-run
    ./yenv/bin/python scripts/cleanup_orphan_files.py --mode missing-physical

    # 一把梭：两种都清
    ./yenv/bin/python scripts/cleanup_orphan_files.py --mode all --dry-run
    ./yenv/bin/python scripts/cleanup_orphan_files.py --mode all

    # 只清某个用户
    ./yenv/bin/python scripts/cleanup_orphan_files.py --mode all --user-id 1

    # 只动 DB，保留磁盘上残留的物理文件/目录
    ./yenv/bin/python scripts/cleanup_orphan_files.py --keep-physical

执行前建议先备份 ``instance/filemanager.db``。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from typing import List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flask import current_app  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import File  # noqa: E402


# ---------- 工具函数 ----------

def _physical_path(node: File) -> str:
    """返回 ``node`` 在磁盘上的绝对路径；拿不到 upload root 就返回空串。"""
    upload_root = current_app.config.get('UPLOAD_FOLDER', '')
    if not upload_root:
        return ''
    return os.path.join(upload_root, str(node.user_id), node.path or '')


def _is_user_root_placeholder(node: File) -> bool:
    """识别用户根目录占位：parent_id=0 且 name='/'。这种记录不可清除。"""
    return node.parent_id == 0 and node.name == '/'


# file_type 中表示"非物理实体"的虚拟类型，自然没有对应磁盘文件，本脚本一律
# 跳过；当前已知:
#   - 'linker': /api/add_linker 创建的链接记录, path 字段存 URL
# 后续若新增类似的虚拟类型, 加进这里即可。
_VIRTUAL_FILE_TYPES = frozenset({'linker'})


def _is_virtual_record(node: File) -> bool:
    """是否是"无物理实体"的虚拟记录（如 linker），不应被磁盘缺失误判为脏。"""
    return (node.file_type or '') in _VIRTUAL_FILE_TYPES


def _collect_postorder(node: File) -> List[File]:
    """后序遍历：子节点排在父节点前面，便于自底向上删除。"""
    out: List[File] = []
    for child in File.query.filter_by(parent_id=node.id).all():
        out.extend(_collect_postorder(child))
    out.append(node)
    return out


def _remove_physical(node: File) -> Tuple[int, int, str]:
    """物理层面清除一个节点对应的路径。

    Returns:
        ``(removed_files, removed_dirs, message)`` —— message 是供日志打印的描述。
    """
    phys = _physical_path(node)
    if not phys or not os.path.exists(phys):
        return 0, 0, f'skip (not exist) {phys}'
    try:
        if os.path.isdir(phys):
            removed_files = sum(len(files) for _, _, files in os.walk(phys))
            removed_dirs = sum(1 for _ in os.walk(phys))
            shutil.rmtree(phys)
            return removed_files, removed_dirs, (
                f'rmtree {phys} ({removed_files} files, {removed_dirs} dirs)'
            )
        os.unlink(phys)
        return 1, 0, f'unlink {phys}'
    except OSError as exc:
        return 0, 0, f'FAILED {phys} -> {exc}'


# ---------- 入口节点发现 ----------

def _find_null_parent_roots(user_id: int | None) -> List[File]:
    """``parent_id IS NULL`` 的所有 File 记录都是入口节点。

    虚拟记录（如 ``file_type='linker'``）即使 parent_id 为空也是脏数据应清理，
    但本脚本保守起见同样放过——这类记录恢复成本极低（用户重新粘 URL 即可），
    误删反而难找回。
    """
    q = File.query.filter(File.parent_id.is_(None))
    if user_id is not None:
        q = q.filter(File.user_id == user_id)
    return [f for f in q.all() if not _is_virtual_record(f)]


def _find_missing_physical_roots(user_id: int | None) -> List[File]:
    """找出"磁盘缺失"的顶层节点。

    顶层 = 这个节点磁盘不存在，但它的 ``parent_id`` 对应的记录磁盘还存在（或
    parent 是用户根占位）。父都缺失的子孙不重复列入，递归删时会被 rmtree 顺
    带处理；DB 层则交给 ``_collect_postorder``。

    跳过的合法记录：
    - 用户根占位（``parent_id=0, name='/'``）即便物理目录还没建出来也跳过
    - 虚拟类型（``file_type='linker'`` 等）天然没有对应磁盘文件，跳过
    """
    q = File.query
    if user_id is not None:
        q = q.filter(File.user_id == user_id)
    all_files = q.all()

    by_id = {f.id: f for f in all_files}

    def exists_on_disk(node: File) -> bool:
        phys = _physical_path(node)
        return bool(phys) and os.path.exists(phys)

    roots: List[File] = []
    for f in all_files:
        if _is_user_root_placeholder(f):
            continue
        if _is_virtual_record(f):
            continue
        if exists_on_disk(f):
            continue
        # 此节点磁盘缺失。判断是否是"顶层缺失"——即父在 DB 里且父磁盘还在，
        # 或父是用户根占位（视作存在）
        parent = by_id.get(f.parent_id)
        if parent is None:
            # 父记录不在（包括 parent_id=0 这种指向虚拟根的情况）：算顶层
            roots.append(f)
            continue
        if _is_user_root_placeholder(parent) or exists_on_disk(parent):
            roots.append(f)
    return roots


# ---------- 删除主流程 ----------

def _cleanup_subtrees(
    label: str,
    roots: List[File],
    *,
    dry_run: bool,
    keep_physical: bool,
) -> dict:
    """对一组入口节点做"后序删 DB + rmtree 磁盘"的统一清理。

    ``label`` 仅用于打印区分（"null-parent" / "missing-physical"）。
    """
    stats = {
        'label': label,
        'roots': len(roots),
        'db_deleted': 0,
        'fs_removed_files': 0,
        'fs_removed_dirs': 0,
        'fs_errors': 0,
    }

    print(f'\n========== mode = {label} ==========')
    if not roots:
        print('没有需要清理的入口节点。')
        return stats

    print(f'入口节点数: {len(roots)}\n')

    for r in roots:
        if _is_user_root_placeholder(r):
            # 双保险：发现函数应已过滤，这里再兜一道，绝不动用户根
            print(f'   !! 跳过用户根占位 id={r.id} user={r.user_id}')
            continue

        # 可疑提示：看起来像根 (name/path 为 / 或空) 但 parent_id 不是 0
        looks_like_root = (r.name in ('/', '', None)) or (r.path in ('/', '', None))
        if looks_like_root and not _is_user_root_placeholder(r):
            print(
                f'!! WARNING: id={r.id} user={r.user_id} name={r.name!r} '
                f'path={r.path!r} 看起来像根但 parent_id={r.parent_id}, 仍会被删除'
            )

        nodes = _collect_postorder(r)
        print(
            f'-- root id={r.id} user={r.user_id} parent={r.parent_id} '
            f'name={r.name!r} path={r.path!r} (子树共 {len(nodes)} 节点)'
        )

        # 1. DB 删除
        for n in nodes:
            kind = '[dir]' if n.is_directory else '[file]'
            print(f'   DB  delete id={n.id} {kind} name={n.name!r}')
            if not dry_run:
                db.session.delete(n)
            stats['db_deleted'] += 1

        # 2. 物理删除（只对 root 做一次，rmtree 会清掉子树）
        if not keep_physical:
            if dry_run:
                phys = _physical_path(r)
                exists = os.path.exists(phys) if phys else False
                msg = f'(dry-run) would {"rmtree" if exists else "skip (not exist)"} {phys}'
                print(f'   FS  {msg}')
            else:
                files_rm, dirs_rm, msg = _remove_physical(r)
                print(f'   FS  {msg}')
                stats['fs_removed_files'] += files_rm
                stats['fs_removed_dirs'] += dirs_rm
                if msg.startswith('FAILED'):
                    stats['fs_errors'] += 1

    if not dry_run:
        db.session.commit()

    return stats


# ---------- 校验 ----------

def _verify(user_id: int | None) -> Tuple[int, int]:
    """清理后再扫一次，看 null-parent / missing-physical 各还剩几条。"""
    n_null = len(_find_null_parent_roots(user_id))
    n_miss = len(_find_missing_physical_roots(user_id))
    return n_null, n_miss


# ---------- CLI ----------

def main() -> int:
    p = argparse.ArgumentParser(
        description='递归清理 File 表的两类脏数据：parent_id IS NULL / 物理路径缺失',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        '--mode',
        choices=['null-parent', 'missing-physical', 'all'],
        default='null-parent',
        help='清理模式 (默认: null-parent，兼容旧行为)',
    )
    p.add_argument('--dry-run', action='store_true', help='只打印不写库、不动磁盘')
    p.add_argument(
        '--keep-physical',
        action='store_true',
        help='只删 DB 记录，保留磁盘上的物理文件/目录',
    )
    p.add_argument(
        '--user-id',
        type=int,
        default=None,
        help='只清理指定用户的孤儿（默认清所有用户）',
    )
    args = p.parse_args()

    app = create_app()
    with app.app_context():
        all_stats: List[dict] = []

        if args.mode in ('null-parent', 'all'):
            roots = _find_null_parent_roots(args.user_id)
            all_stats.append(
                _cleanup_subtrees(
                    'null-parent', roots,
                    dry_run=args.dry_run, keep_physical=args.keep_physical,
                )
            )

        if args.mode in ('missing-physical', 'all'):
            # 注意：如果上一轮跑完已经清了一些行，这里要重新扫
            roots = _find_missing_physical_roots(args.user_id)
            all_stats.append(
                _cleanup_subtrees(
                    'missing-physical', roots,
                    dry_run=args.dry_run, keep_physical=args.keep_physical,
                )
            )

        # 验证
        remain_null, remain_miss = _verify(args.user_id)

        print()
        print('== Summary ==')
        print(f'  mode:               {args.mode}')
        print(f'  dry_run:            {args.dry_run}')
        print(f'  keep_physical:      {args.keep_physical}')
        print(f'  user_id filter:     {args.user_id}')
        for s in all_stats:
            print(
                f'  [{s["label"]:<17}] roots={s["roots"]} '
                f'db_deleted={s["db_deleted"]} '
                f'fs_files={s["fs_removed_files"]} fs_dirs={s["fs_removed_dirs"]} '
                f'fs_errors={s["fs_errors"]}'
            )
        print(f'  剩余 parent_id IS NULL:    {remain_null}')
        print(f'  剩余 物理路径缺失:          {remain_miss}')

        if not args.dry_run and args.user_id is None:
            expect_remain_null = args.mode in ('missing-physical',)
            expect_remain_miss = args.mode in ('null-parent',)
            if (not expect_remain_null and remain_null) or (not expect_remain_miss and remain_miss):
                print('\n!! 清理后仍有残留, 请人工排查 !!')
                return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
