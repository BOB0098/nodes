#!/usr/bin/env python3
"""Snapshot and restore the container pipeline's mutable outputs.

The data directory is a bind mount, so the directory itself cannot be atomically
swapped. Restore replaces its contents except for ``data/logs``; it also restores
the page, generated quality/governance reports, and private subscription exports.
Docker bind mounts are supported when their APP_ROOT paths are ordinary mount
points; symlinked output paths are rejected before snapshot or restore.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

OUTPUTS = (
    "data",
    "index.html",
    "docs/P4-source-quality.md",
    "docs/P19-governance.md",
    "export",
)


def _read_mountinfo() -> str:
    try:
        return Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("cannot inspect mount boundaries via /proc/self/mountinfo") from exc


def _unescape_mount_path(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _validate_output_mounts(root: Path) -> set[Path]:
    """Allow compose's two output mounts and its non-output mihomo cache volume."""
    root = root.resolve()
    points = set()
    for line in _read_mountinfo().splitlines():
        fields = line.split(" - ", 1)[0].split()
        if len(fields) < 5:
            continue
        point = Path(_unescape_mount_path(fields[4]))
        if point == root or root in point.parents:
            points.add(point)

    allowed = {root / "data", root / "index.html", root / ".cache"}
    for point in points:
        if point not in allowed:
            raise ValueError(f"unexpected mount inside APP_ROOT: {point}")
        if point == root / "data" and not point.is_dir():
            raise ValueError("compose data output mount must target a directory")
        if point == root / "index.html" and not point.is_file():
            raise ValueError("compose index.html output mount must target an existing file")
        if point == root / ".cache" and not point.is_dir():
            raise ValueError("compose mihomo cache volume must target a directory")
    return points


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


def _replace_item_from_snapshot(source: Path, destination: Path) -> None:
    """Stage one item beside its destination, then replace it without partial copy."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".nodes-restore-", dir=destination.parent))
    staged = stage_dir / "new"
    previous = stage_dir / "previous"
    try:
        _copy_item(source, staged)
    except BaseException:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise

    had_previous = _exists(destination)
    try:
        if had_previous:
            os.replace(destination, previous)
        try:
            os.replace(staged, destination)
        except BaseException:
            if had_previous:
                try:
                    os.replace(previous, destination)
                except OSError as rollback_error:
                    raise RuntimeError(
                        f"replacement failed; previous output remains at {previous}"
                    ) from rollback_error
            raise
    except BaseException:
        if _exists(previous):
            # If rollback itself failed, preserve the recovery copy for inspection.
            raise
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise

    if had_previous:
        _remove(previous)
    shutil.rmtree(stage_dir, ignore_errors=True)


def _validate_output_paths(root: Path) -> None:
    """Fail closed if an output path or parent can redirect writes outside root."""
    for rel in OUTPUTS:
        relative = Path(rel)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"output path is not relative to APP_ROOT: {rel}")
        path = root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise ValueError(f"symlink in APP_ROOT output path: {rel}")
        parent = (root / relative).parent.resolve()
        if parent != root and root not in parent.parents:
            raise ValueError(f"output path escapes APP_ROOT: {rel}")


def snapshot(root: Path, backup: Path) -> None:
    root, backup = _outside_root(root, backup)
    _validate_output_paths(root)
    _validate_output_mounts(root)
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
    restored = set()
    errors = []
    if originally_existed and backup_data.is_dir():
        for child in backup_data.iterdir():
            if child.name == "logs":
                continue
            restored.add(child.name)
            try:
                _replace_item_from_snapshot(child, root_data / child.name)
            except Exception as exc:
                errors.append(f"data/{child.name}: {exc}")
    for child in list(root_data.iterdir()):
        if child.name != "logs" and child.name not in restored:
            if (child.is_dir() and not child.is_symlink()
                    and child.name.startswith(".nodes-restore-")
                    and _exists(child / "previous")):
                continue
            try:
                _remove(child)
            except Exception as exc:
                errors.append(f"data/{child.name}: {exc}")
    if errors:
        raise RuntimeError("data restore incomplete (best-effort): " + "; ".join(errors))


def restore(root: Path, backup: Path) -> None:
    root, backup = _outside_root(root, backup)
    _validate_output_paths(root)
    mount_points = _validate_output_mounts(root)
    state_path = backup / "state.json"
    existed = json.loads(state_path.read_text(encoding="utf-8"))
    errors = []
    for rel in OUTPUTS:
        destination = root / rel
        source = backup / rel
        try:
            if rel == "data":
                _restore_data(destination, source, bool(existed.get(rel)))
                continue
            if not existed.get(rel) or not _exists(source):
                _remove(destination)
                continue
            if (destination in mount_points and source.is_file() and not source.is_symlink()
                    and destination.is_file() and not destination.is_symlink()):
                # The compose single-file index mount cannot be atomically replaced in
                # the container. Keep this best-effort in-place copy; the backup remains
                # available if the host filesystem fails during the write.
                shutil.copy2(source, destination)
            else:
                _replace_item_from_snapshot(source, destination)
        except Exception as exc:
            errors.append(f"{rel}: {exc}")
    if errors:
        raise RuntimeError("restore incomplete (best-effort): " + "; ".join(errors))


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
