#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据备份、恢复与回滚。

为什么需要这个：这个仓库的数据文件（nodes.json / nodecheck.json /
node_scores.json / subscriptions.json）每轮都会被 CI 重写，而它们同时是
页面和订阅的唯一数据源。真出事的时候——某轮解析逻辑写错、某个源灌进来
一批垃圾、评分口径改坏了——需要的是「回到昨天 04:48 的样子」，不是
「去翻 git 手册」。

git 本身已经保存了每一轮的历史，所以不做额外快照（每天一个 1.6MB 的
JSON 副本，一年就是 600MB，还全是重复内容）。这里做的是把「翻历史」
这件事变成两条命令。

用法:
    python3 restore.py list                  # 最近 20 次数据改动
    python3 restore.py diff <sha>            # 该提交改了哪些数据文件
    python3 restore.py to <sha>              # 把数据文件恢复到该提交
    python3 restore.py to <sha> --dry-run    # 只看会改什么
    python3 restore.py undo                  # 撤销上一次 restore

恢复前会把当前文件复制到 .restore-backup/<时间戳>/，undo 就是从这里拿回来。
备份目录不进版本库（.gitignore 里），也不会被 CI 碰到。
"""

import os
import shutil
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
BACKUP_ROOT = os.path.join(BASE, ".restore-backup")

# 只允许恢复这些。白名单而不是黑名单：万一以后加了带凭据的文件，
# 不会因为「忘了加进黑名单」而被 restore 顺手复制到别处。
ALLOW = [
    "index.html",
    "data/nodes.json",
    "data/status.json",
    "data/quarantine.json",
    "data/nodecheck.json",
    "data/node_scores.json",
    "data/node_history.json",
    "data/trend.json",
    "data/picks.json",
    "data/node_lifecycle.json",
    "data/subscriptions.json",
    "data/sources_stats.json",
    "data/source_history.json",
    "data/health.json",
    "docs/P4-source-quality.md",
]


def git(*args, check=True):
    r = subprocess.run(["git", "-C", BASE, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return r.stdout


def ensure_clean_state():
    """rebase/merge 进行中不能动文件：这时候 HEAD 不是你以为的那个提交。"""
    d = os.path.join(BASE, ".git")
    for name, why in (("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
                      ("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick")):
        if os.path.exists(os.path.join(d, name)):
            raise SystemExit(f"当前有未完成的 {why}，先处理完再恢复。")


def exists_in(sha, path):
    return subprocess.run(["git", "-C", BASE, "cat-file", "-e", f"{sha}:{path}"],
                          capture_output=True).returncode == 0


def cmd_list(n=20):
    out = git("log", f"-{n}", "--date=format:%m-%d %H:%M", "--format=%h\t%ad\t%s", "--", *ALLOW)
    if not out.strip():
        print("没有数据文件的历史提交。")
        return 0
    print(f"{'提交':<9}{'时间':<13}说明")
    for line in out.strip().splitlines():
        h, d, s = line.split("\t", 2)
        print(f"{h:<9}{d:<13}{s[:56]}")
    return 0


def cmd_diff(sha):
    out = git("log", "-1", "--stat", "--format=%h %s", sha, "--", *ALLOW)
    print(out.rstrip())
    return 0


def cmd_to(sha, dry=False):
    ensure_clean_state()
    full = git("rev-parse", sha).strip()
    files = [f for f in ALLOW if exists_in(full, f)]
    if not files:
        print(f"{sha} 里没有任何可恢复的数据文件。")
        return 1

    print(f"目标提交 {full[:8]}  {git('log', '-1', '--format=%s', full).strip()[:60]}")
    for f in files:
        local = os.path.join(BASE, f)
        cur = os.path.getsize(local) if os.path.exists(local) else -1
        print(f"  {f:<34}{'本地不存在' if cur < 0 else f'{cur} 字节'} → 恢复为该提交版本")
    if dry:
        print("\n--dry-run：没有改动任何文件。")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bk = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(bk, exist_ok=True)
    for f in files:
        src = os.path.join(BASE, f)
        if os.path.exists(src):
            dst = os.path.join(bk, f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)          # 备份当前，undo 用
        blob = subprocess.run(["git", "-C", BASE, "show", f"{full}:{f}"],
                              capture_output=True, check=True).stdout
        dst = os.path.join(BASE, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, dst)

    print(f"\n已恢复 {len(files)} 个文件，恢复前的内容在 .restore-backup/{stamp}/")
    print("撤销：python3 restore.py undo")
    print("提交：git add -A && git commit -m '恢复数据到 " + full[:8] + "'")
    return 0


def cmd_undo():
    if not os.path.isdir(BACKUP_ROOT):
        print("没有可撤销的恢复记录。")
        return 1
    stamps = sorted(d for d in os.listdir(BACKUP_ROOT)
                    if os.path.isdir(os.path.join(BACKUP_ROOT, d)))
    if not stamps:
        print("没有可撤销的恢复记录。")
        return 1
    last = stamps[-1]
    src_root = os.path.join(BACKUP_ROOT, last)
    n = 0
    for root, _, names in os.walk(src_root):
        for name in names:
            src = os.path.join(root, name)
            rel = os.path.relpath(src, src_root)
            dst = os.path.join(BASE, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            n += 1
    shutil.rmtree(src_root)
    print(f"已撤销 {last} 的恢复，拿回 {n} 个文件。")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "list":
        return cmd_list()
    if cmd == "diff":
        if not rest:
            print("用法：restore.py diff <sha>")
            return 2
        return cmd_diff(rest[0])
    if cmd == "to":
        if not rest:
            print("用法：restore.py to <sha> [--dry-run]")
            return 2
        return cmd_to(rest[0], dry="--dry-run" in rest)
    if cmd == "undo":
        return cmd_undo()
    print(f"未知命令：{cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
