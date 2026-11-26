#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P20：发布失败回滚演练（不联网、不改仓库、可重复）。

演练在系统临时目录中的工作副本上运行：注入产物错误、验证发布门禁拦截、
恢复副本并重验。正式工作树始终只读，因此进程被强制终止也不会留下被篡改的
正式 data/ 文件。

用法：
    python3 drill_publish.py
    python3 drill_publish.py --quiet
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

from build_manifest import ARTIFACTS

BASE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(BASE, "data", "build_manifest.json")
CHECK = os.path.join(BASE, "check_consistency.py")
SUPPORT_FILES = ("check_consistency.py", "build_manifest.py", "run_ledger.py")
RUNTIME_FILES = ("data/build_manifest.json", "data/run_ledger.json")


def run_check(root):
    """在副本根目录执行发布一致性门禁。"""
    check = os.path.join(root, os.path.basename(CHECK))
    env = os.environ.copy()
    # 临时副本不是 git checkout；将节点数量门禁置于其合法的首次采集口径。
    env["NODES_PREV_COUNT"] = "0"
    result = subprocess.run([sys.executable, check, "--quiet"], cwd=root,
                            capture_output=True, text=True, env=env)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def copy_workspace(root):
    """把门禁脚本、运行状态和已知产物复制到隔离目录，不写回 BASE。"""
    paths = list(SUPPORT_FILES) + list(RUNTIME_FILES)
    paths.extend(rel for _, rel, _ in ARTIFACTS)
    for rel in dict.fromkeys(paths):
        source = os.path.join(BASE, rel)
        if not os.path.isfile(source):
            if rel in SUPPORT_FILES:
                raise FileNotFoundError(f"缺少演练所需脚本：{rel}")
            continue
        destination = os.path.join(root, rel)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)


def main():
    quiet = "--quiet" in sys.argv
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  ✔ " if cond else "  ✘ ") + msg)
        ok = ok and cond

    print("P20 发布失败回滚演练（隔离副本）")
    if not os.path.isfile(MANIFEST):
        print("  ✘ 没有 build_manifest.json，先跑 python3 build_manifest.py --write")
        return 1
    with open(MANIFEST, encoding="utf-8") as source:
        manifest = json.load(source)
    artifacts = manifest.get("artifacts") or {}
    rels = []
    for key, rel, _ in ARTIFACTS:
        meta = artifacts.get(key)
        if (isinstance(meta, dict) and meta.get("path") == rel
                and not meta.get("missing")):
            rels.append(rel)
    chk(len(rels) >= 5, f"manifest 覆盖 {len(rels)} 个可备份产物")
    if "data/nodes.json" not in rels:
        chk(False, "manifest 未包含可演练的 data/nodes.json")
        return 1

    tmp = tempfile.mkdtemp(prefix="drill-publish-")
    backup = os.path.join(tmp, ".last_good")
    try:
        copy_workspace(tmp)
        os.makedirs(backup)

        # 基线：在副本上运行门禁。现有生产基线有问题时仍报告失败，不掩盖它。
        rc, out = run_check(tmp)
        chk(rc == 0, "演练前基线门禁通过（副本当前产物是「上一版」）")
        if rc != 0 and not quiet:
            print(out[-600:])

        # 1. 备份副本上一版
        for rel in rels:
            src = os.path.join(tmp, rel)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(backup, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        print(f"  已备份 {len(rels)} 个产物到临时副本")

        # 2. 仅篡改副本中的 nodes.json，不重生成 manifest
        target = os.path.join(tmp, "data", "nodes.json")
        with open(target, "rb") as source:
            original = source.read()
        doc = json.loads(original)
        stats = doc.get("stats")
        if not isinstance(stats, dict):
            chk(False, "副本 data/nodes.json 缺少 stats 对象，无法注入演练故障")
            return 1
        stats["dedup"] = (stats.get("dedup") or 0) - 1
        with open(target, "w", encoding="utf-8") as output:
            json.dump(doc, output, ensure_ascii=False)
        print("  注入故障：副本 nodes.json 计数被改（模拟产物半写），manifest 未更新")

        # 3. 门禁必须拦下
        rc_bad, out_bad = run_check(tmp)
        chk(rc_bad != 0, f"坏产物被门禁拦下（退出码 {rc_bad}）")
        blocked = ("manifest 产物校验失败" in out_bad) or ("不一致" in out_bad)
        chk(blocked, "拦截原因是产物与 manifest 不一致（不是别的偶发错误）")
        if not quiet and rc_bad == 0:
            print(out_bad[-600:])

        # 4. 副本备份完好
        good_copy = os.path.join(backup, "data", "nodes.json")
        with open(good_copy, "rb") as source:
            backup_bytes = source.read()
        chk(backup_bytes == original, "备份的副本上一版 nodes.json 完好无损")

        # 5. 恢复副本并重验
        for rel in rels:
            src = os.path.join(backup, rel)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        rc_ok, _ = run_check(tmp)
        chk(rc_ok == 0, "从备份恢复后副本门禁重新通过（可安全发布）")
        with open(target, "rb") as source:
            restored = source.read()
        chk(restored == original, "副本恢复后与演练前逐字节一致")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("演练通过：失败被拦下，副本上一版完好，恢复后正常"
                  if ok else "演练失败：存在未通过项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
