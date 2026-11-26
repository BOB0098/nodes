#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P23：同轮构建台账（run ledger）——证明每个阶段真的在本轮跑成功了。

解决的真实缺陷（第二轮审计确认）：daily.yml 里检测/评分/治理/榜单/订阅/
重渲染 6 个步骤带 continue-on-error，任一失败仍继续跑 manifest，旧产物被
打上新的 build_id、通过一致性检查，以「新一轮」身份发布。manifest 当时只
证明哈希一致，不证明各阶段执行成功。

本模块让每个关键脚本「成功退出后」记一笔：

  - start(run_id)   本轮开始，清空台账、写入 run_id
  - mark(stage)     某阶段成功，记录状态/时间/该阶段产物的 sha256
  - check()         发布前校验：所有必需阶段都 ok、run_id 一致、产物哈希吻合

CI 顺序：start → collect → detect → score → governance → picks → subs →
render，每步 `python3 xxx.py && python3 run_ledger.py mark <stage>`（脚本
退出码非 0 就不 mark），最后 build_manifest 之前 check。

失败即不 mark、且 workflow 已去掉 continue-on-error，job 中断、不 push，
远端保留上一版可用产物——旧数据不会再冒充新一轮。

用法：
    python3 run_ledger.py start [--run-id ID]   # 初始化本轮台账
    python3 run_ledger.py mark <stage>          # 标记阶段成功
    python3 run_ledger.py check                 # 发布前硬校验（失败退出 1）
    python3 run_ledger.py show                  # 打印台账
    python3 run_ledger.py --selftest            # 自测
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
LEDGER = os.path.join(DATA, "run_ledger.json")
SCHEMA_VERSION = 1
TZ = timezone(timedelta(hours=8))

# 必需阶段 → 该阶段负责的产物（相对 BASE）。顺序即 CI 执行顺序。
# nodes.json 先由 collect 生成初稿，再由 render 汇总本轮评分/订阅信息成为最终
# 页面快照；两次哈希都留在台账中，只有下方显式声明的 collect→render 覆写获准。
STAGES = [
    ("collect", "采集与去重", ["data/nodes.json", "data/status.json"]),
    ("detect", "真实代理检测", ["data/nodecheck.json"]),
    ("score", "节点评分", ["data/node_scores.json"]),
    ("governance", "来源治理", ["data/source_governance.json"]),
    ("picks", "优选榜单", ["data/picks.json"]),
    ("subs", "订阅生成", ["data/subscriptions.json"]),
    ("render", "页面重渲染", ["data/nodes.json", "index.html"]),
]
STAGE_KEYS = [s[0] for s in STAGES]
STAGE_FILES = {s[0]: s[2] for s in STAGES}
AUTHORIZED_REWRITES = {("collect", "render", "data/nodes.json")}


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save(doc):
    os.makedirs(DATA, exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LEDGER)


def resolve_run_id():
    """CI 用 GITHUB_RUN_ID（天然唯一），本地用 NODES_RUN_ID 或时间戳。"""
    return (os.environ.get("GITHUB_RUN_ID")
            or os.environ.get("NODES_RUN_ID")
            or f"local-{int(time.time())}")


def start(run_id=None):
    rid = run_id or resolve_run_id()
    doc = {
        "schema_version": SCHEMA_VERSION,
        "run_id": rid,
        "started_at": now_iso(),
        "stages": {},
    }
    save(doc)
    return doc


def mark(stage):
    if stage not in STAGE_KEYS:
        raise SystemExit(f"未知阶段 {stage}，应为：{', '.join(STAGE_KEYS)}")
    doc = load()
    if not doc.get("run_id"):
        raise SystemExit("台账未初始化，先跑 run_ledger.py start")
    paths = STAGE_FILES[stage]
    hashes = {}
    for rel in paths:
        path = os.path.join(BASE, rel)
        if not os.path.isfile(path):
            raise SystemExit(f"阶段 {stage} 的产物缺失：{rel}")
        hashes[rel] = sha256_file(path)
    doc["stages"][stage] = {
        "status": "ok",
        "at": now_iso(),
        "run_id": doc["run_id"],
        "artifacts": hashes,
    }
    save(doc)
    return doc


def _authorized_rewrite(stage, rel, stages, run_id, current_hash):
    """仅允许显式登记的后续阶段覆写，并要求当前哈希等于后续阶段记录。"""
    if stage not in STAGE_KEYS:
        return False
    later_keys = STAGE_KEYS[STAGE_KEYS.index(stage) + 1:]
    for later in later_keys:
        if (stage, later, rel) not in AUTHORIZED_REWRITES:
            continue
        rec = stages.get(later) or {}
        if rec.get("status") != "ok" or rec.get("run_id") != run_id:
            continue
        if (rec.get("artifacts") or {}).get(rel) == current_hash:
            return True
    return False


def check():
    """校验阶段齐全、run_id 一致和最终哈希；仅接受明示的阶段间覆写。"""
    doc = load()
    errs = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"台账 schema_version={doc.get('schema_version')}，应为 {SCHEMA_VERSION}")
    rid = doc.get("run_id")
    if not rid:
        errs.append("台账没有 run_id（本轮未 start）")
    stages = doc.get("stages") or {}
    for key in STAGE_KEYS:
        rec = stages.get(key)
        if not rec or rec.get("status") != "ok":
            errs.append(f"阶段 {key} 未标记成功（脚本失败或漏 mark）")
            continue
        if rec.get("run_id") != rid:
            errs.append(f"阶段 {key} 的 run_id 与本轮不一致（跨轮产物）")
        for rel, h in (rec.get("artifacts") or {}).items():
            path = os.path.join(BASE, rel)
            if not os.path.isfile(path):
                errs.append(f"阶段 {key} 的产物缺失：{rel}")
            else:
                current_hash = sha256_file(path)
                if current_hash != h and not _authorized_rewrite(
                        key, rel, stages, rid, current_hash):
                    errs.append(f"阶段 {key} 的产物被改动：{rel}（标记后又被覆盖）")
    return (not errs), errs


def selftest():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  ✔ " if cond else "  ✘ ") + msg)
        ok = ok and bool(cond)

    print("run_ledger 自测")
    backup = None
    if os.path.isfile(LEDGER):
        backup = open(LEDGER, "rb").read()
    try:
        start("selftest-run-001")
        d = load()
        chk(d.get("run_id") == "selftest-run-001", "start 写入 run_id")
        chk(d.get("stages") == {}, "start 清空阶段")

        # 未 mark 时 check 必须失败
        good, errs = check()
        chk(not good and any("未标记" in e for e in errs), "阶段不全时 check 失败")

        # 依次 mark 全部阶段（用磁盘现有产物）
        for key in STAGE_KEYS:
            mark(key)
        good, errs = check()
        chk(good, "全部阶段 mark 后 check 通过")

        # run_id 不一致必须失败
        d = load()
        d["stages"]["detect"]["run_id"] = "other-run"
        save(d)
        good, errs = check()
        chk(not good and any("run_id" in e for e in errs), "跨轮 run_id 被拦下")
        d = load()
        d["stages"]["detect"]["run_id"] = "selftest-run-001"
        save(d)

        # 阶段成功后产物被改必须失败
        d = load()
        for rel in STAGE_FILES["detect"]:
            d["stages"]["detect"]["artifacts"][rel] = "0" * 64
        save(d)
        good, errs = check()
        chk(not good and any("被改动" in e for e in errs), "产物被改动被拦下")
    finally:
        if backup is not None:
            with open(LEDGER, "wb") as f:
                f.write(backup)
        elif os.path.isfile(LEDGER):
            os.remove(LEDGER)
    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "start":
        rid = None
        if "--run-id" in args:
            i = args.index("--run-id")
            if i + 1 < len(args):
                rid = args[i + 1]
        d = start(rid)
        print(f"本轮开始：run_id={d['run_id']}，阶段 {len(STAGE_KEYS)} 个待执行")
    elif cmd == "mark":
        if len(args) < 2:
            raise SystemExit("用法：run_ledger.py mark <stage>")
        d = mark(args[1])
        print(f"阶段 {args[1]} 标记成功（run_id={d['run_id']}）")
    elif cmd == "check":
        good, errs = check()
        if good:
            d = load()
            print(f"同轮构建校验通过：run_id={d['run_id']}，"
                  f"{len(d.get('stages') or {})}/{len(STAGE_KEYS)} 个阶段成功")
            return 0
        for e in errs:
            print("  ✘", e)
        print(f"结论：{len(errs)} 项未通过 → 拦下发布")
        return 1
    elif cmd == "show":
        print(json.dumps(load(), ensure_ascii=False, indent=2))
    else:
        raise SystemExit(f"未知命令：{cmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
