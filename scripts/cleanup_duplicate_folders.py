#!/usr/bin/env python3
"""
一次性修复脚本：清理 `file_routes.create_folder` 历史 bug 留下的脏数据。

背景：旧版 `create_folder` 检查同名时用 ``parent_id=None``，
而新建却把 ``parent_id`` 写成硬编码的 ``1``，导致每次「自动同步」都会在根目录
重复创建一份相同名字的文件夹，并且部分历史记录是 ``parent_id IS NULL`` 的孤儿。

本脚本会：

1. 把 ``parent_id IS NULL`` 的"孤儿"目录/文件挂到该用户真实根目录下。
2. 合并每个用户、同 ``parent_id`` 下"同名 + 同 is_directory=True"的重复目录：
   保留 id 最小的那条；其他重复目录的子项 ``parent_id`` 重指到保留项；
   重复目录本身从数据库里删除（物理目录不动，避免误删用户文件）。
3. （可选 ``--prune-empty-dups``）若被合并的物理目录为空，则一并删除磁盘空目录。

用法::

    ./yenv/bin/python scripts/cleanup_duplicate_folders.py --dry-run
    ./yenv/bin/python scripts/cleanup_duplicate_folders.py
    ./yenv/bin/python scripts/cleanup_duplicate_folders.py --prune-empty-dups

执行前建议先备份 ``instance/filemanager.db``。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

# 让脚本能 import 项目模块
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import create_app, db  # noqa: E402
from app.models import File  # noqa: E402


def get_or_create_user_root(user_id: int) -> File:
    root = (
        File.query.filter_by(user_id=user_id, is_directory=True, parent_id=0)
        .order_by(File.id.asc())
        .first()
    )
    if root:
        return root
    root = File(
        name='/', path='/', size=0, file_type='文件夹',
        is_directory=True, user_id=user_id, parent_id=0,
    )
    db.session.add(root)
    db.session.commit()
    return root


def reattach_orphans(dry_run: bool) -> int:
    """parent_id IS NULL 的记录全部挂到该用户的真实根目录下。"""
    orphans = File.query.filter(File.parent_id.is_(None)).all()
    fixed = 0
    for o in orphans:
        # File.id=1 的 / 占位记录本身允许 parent_id=0；不动其他特殊记录
        if o.parent_id == 0:
            continue
        root = get_or_create_user_root(o.user_id)
        if o.id == root.id:
            continue
        print(f"[orphan] file_id={o.id} name={o.name!r} user={o.user_id} -> parent_id={root.id}")
        if not dry_run:
            o.parent_id = root.id
        fixed += 1
    if not dry_run and fixed:
        db.session.commit()
    return fixed


def merge_duplicate_dirs(dry_run: bool, prune_empty: bool) -> int:
    """同一 user_id+parent_id 下、同名的目录记录合并为 id 最小的那条。"""
    # 拿出所有目录，按 (user_id, parent_id, name) 分桶
    dirs = (
        File.query.filter_by(is_directory=True)
        .order_by(File.id.asc())
        .all()
    )
    bucket = defaultdict(list)
    for d in dirs:
        # 跳过用户根目录占位（parent_id=0, name='/')
        if d.parent_id == 0 and d.name == '/':
            continue
        bucket[(d.user_id, d.parent_id, d.name)].append(d)

    removed = 0
    for (user_id, parent_id, name), items in bucket.items():
        if len(items) < 2:
            continue
        keeper = items[0]
        dups = items[1:]
        print(
            f"[dup] user={user_id} parent={parent_id} name={name!r} "
            f"keep id={keeper.id} drop ids={[d.id for d in dups]}"
        )
        for d in dups:
            # 重指子项
            children = File.query.filter_by(parent_id=d.id).all()
            for c in children:
                print(f"    reparent child id={c.id} name={c.name!r} -> parent_id={keeper.id}")
                if not dry_run:
                    c.parent_id = keeper.id

            # 物理目录处理
            phys = _physical_path(d)
            if prune_empty and phys and os.path.isdir(phys):
                try:
                    if not os.listdir(phys):
                        print(f"    rmdir empty {phys}")
                        if not dry_run:
                            os.rmdir(phys)
                except OSError as exc:
                    print(f"    rmdir failed: {exc}")

            # 删除重复目录记录
            if not dry_run:
                db.session.delete(d)
            removed += 1

    if not dry_run and removed:
        db.session.commit()
    return removed


def _physical_path(d: File) -> str:
    from flask import current_app

    upload_root = current_app.config.get('UPLOAD_FOLDER', '')
    if not upload_root:
        return ''
    return os.path.join(upload_root, str(d.user_id), d.path)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dry-run', action='store_true', help='只打印不写库')
    p.add_argument(
        '--prune-empty-dups',
        action='store_true',
        help='被合并的重复目录若磁盘上为空，则一并 rmdir',
    )
    args = p.parse_args()

    app = create_app()
    with app.app_context():
        n_orphan = reattach_orphans(args.dry_run)
        n_dup = merge_duplicate_dirs(args.dry_run, args.prune_empty_dups)
        print(
            f"\n完成：reattached_orphans={n_orphan}, removed_duplicates={n_dup}, "
            f"dry_run={args.dry_run}, prune_empty_dups={args.prune_empty_dups}"
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
