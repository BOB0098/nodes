#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P20：发布失败回滚演练（不联网、不改仓库、可重复）。

要证明的命题：新一轮产物验证失败时，发布被拦下，上一版可用产物不受影响。

真实的失败方式不是「整个文件没了」，而是「文件被写了一半」或「产物改了、
manifest 没跟上」——一致性门禁必须能发现，且此时绝不能 push。脚本做的事：

  1. 备份 manifest 覆盖的全部产物到临时目录（模拟「上一版」）
  2. 故意把一个产物改坏（篡改计数，模拟半写/版本错位），但不重生成 manifest
  3. 跑 check_consistency.py，断言退出码非 0（发布被拦）
  4. 核对备份完好（上一版没被污染）
  5. 从备份恢复，再跑一次门禁，断言退出码 0（恢复后允许发布）
  6. 清理临时目录，输出演练报告

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

BASE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(BASE, "data", "build_manifest.json")
CHECK = os.path.join(BASE, "check_consistency.py")


def run_check():
    r = subprocess.run([sys.executable, CHECK], cwd=BASE,
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    quiet = "--quiet" in sys.argv
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  ✔ " if cond else "  ✘ ") + msg)
        ok = ok and cond

    print("P20 发布失败回滚演练")
    if not os.path.isfile(MANIFEST):
        print("  ✘ 没有 build_manifest.json，先跑 python3 build_manifest.py --write")
        return 1
    mf = json.load(open(MANIFEST, encoding="utf-8"))
    artifacts = mf.get("artifacts") or {}
    rels = [v.get("path") for v in artifacts.values() if v.get("path") and not v.get("missing")]
    chk(len(rels) >= 5, f"manifest 覆盖 {len(rels)} 个可备份产物")

    # 基线：当前产物应当是通过门禁的
    rc, out = run_check()
    chk(rc == 0, "演练前基线门禁通过（当前产物是「上一版」）")
    if rc != 0 and not quiet:
        print(out[-600:])

    tmp = tempfile.mkdtemp(prefix=".drill-", dir=BASE)
    backup = os.path.join(tmp, "last_good")
    os.makedirs(backup)
    try:
        # 1. 备份上一版
        for rel in rels:
            src = os.path.join(BASE, rel)
            dst = os.path.join(backup, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        print(f"  已备份 {len(rels)} 个产物到 {os.path.relpath(backup, BASE)}")

        # 2. 注入故障：改 nodes.json 计数，不重生成 manifest
        target = os.path.join(BASE, "data", "nodes.json")
        orig = open(target, "rb").read()
        doc = json.loads(orig)
        doc["stats"]["dedup"] = (doc["stats"].get("dedup") or 0) - 1
        with open(target, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        print("  注入故障：nodes.json 计数被改（模拟产物半写），manifest 未更新")

        # 3. 门禁必须拦下
        rc_bad, out_bad = run_check()
        chk(rc_bad != 0, f"坏产物被门禁拦下（退出码 {rc_bad}）")
        blocked = ("manifest 产物校验失败" in out_bad) or ("不一致" in out_bad)
        chk(blocked, "拦截原因是产物与 manifest 不一致（不是别的偶发错误）")
        if not quiet and rc_bad == 0:
            print(out_bad[-600:])

        # 4. 备份完好——上一版没被污染
        good_copy = os.path.join(backup, "data", "nodes.json")
        chk(open(good_copy, "rb").read() == orig, "备份的上一版 nodes.json 完好无损")

        # 5. 恢复并重验
        for rel in rels:
            shutil.copy2(os.path.join(backup, rel), os.path.join(BASE, rel))
        rc_ok, _ = run_check()
        chk(rc_ok == 0, "从备份恢复后门禁重新通过（可安全发布）")
        chk(open(target, "rb").read() == orig, "恢复后产物与演练前逐字节一致")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("演练通过：失败被拦下，上一版完好，恢复后正常"
                  if ok else "演练失败：存在未通过项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
