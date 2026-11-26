#!/usr/bin/env python3
"""Snapshot and restore the container pipeline's mutable outputs.

The data directory is a bind mount, so the directory itself cannot be atomically
swapped. Restore replaces its contents except for ``data/logs``; it also restores
the page, generated quality/governance reports, and private subscription exports.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

OUTPUTS = (
    "data",
    "index.html",
    "docs/P4-source-quality.md",
    "docs/P19-governance.md",
    "export",
)


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _copy_item(source: Path, destination: Path) -> None:
    if source.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_data_without_logs(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        return
    for child in source.iterdir():
        if child.name == "logs":
            continue
        _copy_item(child, destination / child.name)


def _outside_root(root: Path, backup: Path) -> tuple[Path, Path]:
    root = root.resolve()
    backup = backup.resolve()
    if backup == root or root in backup.parents:
        raise ValueError("backup directory must be outside APP_ROOT")
    return root, backup


def snapshot(root: Path, backup: Path) -> None:
    root, backup = _outside_root(root, backup)
    backup.mkdir(parents=True, exist_ok=True)
    existed = {}
    for rel in OUTPUTS:
        source = root / rel
        dest = backup / rel
        _remove(dest)
        existed[rel] = _exists(source)
        if not existed[rel]:
            continue
        if rel == "data":
            _copy_data_without_logs(source, dest)
        else:
            _copy_item(source, dest)
    state = backup / "state.json"
    temp = backup / ".state.json.tmp"
    temp.write_text(json.dumps(existed, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, state)


def _restore_data(root_data: Path, backup_data: Path, originally_existed: bool) -> None:
    root_data.mkdir(parents=True, exist_ok=True)
    for child in list(root_data.iterdir()):
        if child.name != "logs":
            _remove(child)
    if originally_existed and backup_data.is_dir():
        for child in backup_data.iterdir():
            _copy_item(child, root_data / child.name)


def restore(root: Path, backup: Path) -> None:
    root, backup = _outside_root(root, backup)
    state_path = backup / "state.json"
    existed = json.loads(state_path.read_text(encoding="utf-8"))
    for rel in OUTPUTS:
        destination = root / rel
        source = backup / rel
        if rel == "data":
            _restore_data(destination, source, bool(existed.get(rel)))
            continue
        if not existed.get(rel) or not _exists(source):
            _remove(destination)
            continue
        if (source.is_file() and not source.is_symlink()
                and destination.is_file() and not destination.is_symlink()):
            # index.html may be a Docker single-file bind mount; unlink/rename of
            # its mountpoint can fail, while copying onto the existing file works.
            shutil.copy2(source, destination)
        else:
            _remove(destination)
            _copy_item(source, destination)


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in {"snapshot", "restore"}:
        print("usage: transaction.py {snapshot|restore} ROOT BACKUP", file=sys.stderr)
        return 2
    root, backup = Path(argv[2]), Path(argv[3])
    try:
        if argv[1] == "snapshot":
            snapshot(root, backup)
        else:
            restore(root, backup)
    except Exception as exc:
        print(f"transaction {argv[1]} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
