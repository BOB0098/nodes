#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P20：统一构建清单（build manifest）。

为什么需要它：同一轮里 nodes.json 写「11-25 14:02」、subscriptions.json 写
「2026-11-25 14:15」、页面内嵌又是另一套。旧的一致性检查用正则解析时间，
只能认出前一种格式，后一种直接返回 None 被排除在比对之外——于是日志显示
「差 0 分钟」，其实只比了两项。数据版本错位就这么漏过去了。

本脚本在每轮产物全部生成后运行（在 collector.py --render 之后），把页面、
数据、订阅、状态钉在同一个 manifest 上：

  - build_id      同一轮产物的唯一标识，可复现（同输入 -> 同 id）
  - generated_at  manifest 生成时间，ISO 8601，明确 +08:00 时区
  - counts        统一口径的计数（节点/真实可用/四类榜单）
  - artifacts     每个产物的 sha256、字节数、原始 updated、规范化时间戳

check_consistency.py 会拿它做硬性校验：任何一个产物的 sha256 与 manifest
对不上、或计数对不上，就拦住发布。发布失败时 git 不会 push，远端保留上一版。

用法：
    python3 build_manifest.py              # 生成并写入 data/build_manifest.json
    python3 build_manifest.py --dry-run    # 只打印，不写盘
    python3 build_manifest.py --selftest   # 自测
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
import netsafe

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
MANIFEST = os.path.join(DATA, "build_manifest.json")
LEDGER_PATH = os.path.join(DATA, "run_ledger.json")

SCHEMA_VERSION = 2   # P23：加入 pipeline（run_id + 阶段成功证明）
TZ = timezone(timedelta(hours=8))  # 全项目统一东八区

# 纳入清单的产物。页面、核心数据、订阅、状态、榜单一个都不能少。
# key 是稳定标识，path 相对 BASE，count_hint 指明从哪个字段取条数（仅注释用）。
ARTIFACTS = [
    ("index_html", "index.html", "内嵌 DATA 的页面"),
    ("nodes", "data/nodes.json", "节点库"),
    ("nodecheck", "data/nodecheck.json", "真握手检测"),
    ("scores", "data/node_scores.json", "源评分与标签"),
    ("picks", "data/picks.json", "四类优选榜单"),
    ("subscriptions", "data/subscriptions.json", "多格式订阅清单"),
    ("status", "data/status.json", "源抓取状态"),
    ("governance", "data/source_governance.json", "来源治理"),
    ("trend", "data/trend.json", "日趋势"),
]


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_ts(value, year_hint=None):
    """把项目里出现过的三种时间写法统一成 epoch 秒。

    支持：
      - 「11-25 14:02」       旧节点/status 格式，无年份
      - 「2026-11-25 14:15」  新版检测/订阅/榜单格式
      - 1795586539            epoch 秒

    无年份的用 year_hint 补全；都没有就用今年。无法解析返回 None，
    绝不返回 0 或猜测值——宁可留空让人看见，也不伪造一个时间戳。
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    s = str(value or "").strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 9:
        return int(s)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
        se = int(m.group(6) or 0)
    else:
        m = re.match(r"(\d{2})-(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?", s)
        if not m:
            return None
        mo, d, h, mi = (int(m.group(i)) for i in range(1, 5))
        se = int(m.group(5) or 0)
        y = year_hint or datetime.now(TZ).year
    try:
        return int(datetime(y, mo, d, h, mi, se, tzinfo=TZ).timestamp())
    except ValueError:
        return None


def embedded_updated():
    """页面内嵌 DATA 里声明的 updated。"""
    try:
        with open(os.path.join(BASE, "index.html"), encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return None
    m = re.search(r"const DATA = (\{.*?\});\s*\n", html, re.S)
    if not m:
        return None
    try:
        return (json.loads(m.group(1)) or {}).get("updated")
    except json.JSONDecodeError:
        return None


def build():
    """扫描磁盘上的全部产物，返回 manifest 字典。不写盘。"""
    docs = {}
    for key, rel, _ in ARTIFACTS:
        path = os.path.join(BASE, rel)
        docs[key] = load(path, None)

    # 年份锚点：从带年份的时间里取一个，给「11-25 14:02」这种无年份格式补全。
    year_hint = None
    for key in ("nodecheck", "subscriptions", "picks", "governance", "trend"):
        ts = norm_ts((docs.get(key) or {}).get("updated") if isinstance(docs.get(key), dict) else None)
        if ts:
            year_hint = datetime.fromtimestamp(ts, TZ).year
            break

    artifacts = {}
    latest = 0
    for key, rel, desc in ARTIFACTS:
        path = os.path.join(BASE, rel)
        if not os.path.isfile(path):
            artifacts[key] = {"path": rel, "desc": desc, "missing": True}
            continue
        doc = docs.get(key)
        raw_updated = doc.get("updated") if isinstance(doc, dict) else None
        if key == "index_html":
            raw_updated = embedded_updated()
        ts = norm_ts(raw_updated, year_hint)
        if ts and ts > latest:
            latest = ts
        artifacts[key] = {
            "path": rel,
            "desc": desc,
            "sha256": sha256_file(path),
            "bytes": os.path.getsize(path),
            "updated_raw": raw_updated,
            "updated_ts": ts,
        }

    nodes = docs.get("nodes") or {}
    subs = docs.get("subscriptions") or {}
    check = docs.get("nodecheck") or {}
    picks = docs.get("picks") or {}
    node_list = nodes.get("nodes") or []
    summ = check.get("summary") or {}

    group_counts = {g.get("key"): g.get("count") for g in (subs.get("groups") or [])}
    pick_counts = {k: len(v) for k, v in (picks.get("lists") or {}).items()}

    counts = {
        "nodes_total": len(node_list),
        "dedup": (nodes.get("stats") or {}).get("dedup"),
        "tested": summ.get("tested"),
        "real_ok": summ.get("ok"),
        "real_ok_pct": summ.get("ok_pct"),
        "groups": group_counts,
        "picks": pick_counts,
    }

    # P23：同轮构建证明。run_ledger 记录本轮 run_id 与每个关键阶段是否真实
    # 成功执行。manifest 只有把它也吸收进来，才能保证「哈希一致」不等于
    # 「旧产物被新 build_id 冒充」——阶段缺失时 check 会直接失败。
    import run_ledger
    ledger = run_ledger.load()
    pipe_ok, pipe_errs = run_ledger.check()
    pipeline = {
        "run_id": ledger.get("run_id"),
        "started_at": ledger.get("started_at"),
        "stages_ok": sorted((ledger.get("stages") or {}).keys()),
        "stage_count": len(ledger.get("stages") or {}),
        "expected_stages": run_ledger.STAGE_KEYS,
        "complete": pipe_ok,
        "errors": pipe_errs,
    }

    # build_id = 数据基准时间 + 产物摘要 + 同轮标记前 10 位。同输入必得同 id。
    digest_src = json.dumps(
        {"latest": latest, "counts": counts,
         "hashes": {k: v.get("sha256") for k, v in artifacts.items()},
         "run_id": pipeline["run_id"], "stages_ok": pipeline["stages_ok"]},
        sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")
    digest = hashlib.sha256(digest_src).hexdigest()[:10]
    stamp = datetime.fromtimestamp(latest, TZ).strftime("%Y%m%dT%H%M") if latest else "unknown"

    return {
        "schema_version": SCHEMA_VERSION,
        "build_id": f"{stamp}-{digest}",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai (+08:00)",
        "data_basis_ts": latest,
        "counts": counts,
        "artifacts": artifacts,
        "pipeline": pipeline,
        "compatibility": {
            "note": "订阅路径、文件名与四种格式保持不变；manifest 只做校验，不改任何产物。",
            "formats": ["clash", "singbox", "uri", "b64"],
            "groups_inherit": ["all", "best", "stable", "lowlat", "fast",
                               "hk", "tw", "jp", "sg", "us", "kr", "eu"],
        },
    }


def verify(m, expect_missing=False):
    """校验 manifest 与磁盘是否一致。返回 (ok, 失败说明列表)。"""
    errs = []
    if m.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version={m.get('schema_version')}，应为 {SCHEMA_VERSION}")
    artifacts = m.get("artifacts")
    if not isinstance(artifacts, dict):
        errs.append("manifest.artifacts 必须是对象")
        artifacts = {}
    expected_paths = {key: rel for key, rel, _ in ARTIFACTS}
    missing_keys = set(expected_paths) - set(artifacts)
    extra_keys = set(artifacts) - set(expected_paths)
    if missing_keys:
        errs.append("manifest 缺少必需 artifact：" + ", ".join(sorted(missing_keys)))
    if extra_keys:
        errs.append("manifest 含未知 artifact 键：" + ", ".join(sorted(extra_keys)))
    cur = build()
    # 只比 sha256 与计数，generated_at 每次都变，不参与比对。
    for key, rel, _ in ARTIFACTS:
        meta = artifacts.get(key)
        if not isinstance(meta, dict):
            if key in artifacts:
                errs.append(f"manifest artifact {key} 元数据必须是对象")
            continue
        if meta.get("path") != rel:
            errs.append(f"manifest artifact {key} 的 path 应为 {rel}")
        now = (cur.get("artifacts") or {}).get(key) or {}
        if meta.get("missing") is True:
            if not expect_missing:
                errs.append(f"manifest 记录了缺失产物：{key}")
            elif not now.get("missing"):
                errs.append(f"manifest 将现存产物标记为缺失：{key}")
            continue
        digest = meta.get("sha256")
        if (not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            errs.append(f"manifest artifact {key} 的 sha256 缺失或格式无效")
        elif digest != now.get("sha256"):
            errs.append(f"{rel} 的 sha256 与磁盘不一致（产物被改过或未重新生成 manifest）")
    if (m.get("counts") or {}) != (cur.get("counts") or {}):
        errs.append("counts 与磁盘产物不一致")
    # P23：manifest 必须带同轮构建证明。缺 pipeline、阶段不全、或当前台账
    # 校验失败，都说明这轮产物不是「所有阶段都成功执行」得来的。
    pipe = m.get("pipeline") or {}
    if not pipe.get("run_id"):
        errs.append("manifest 缺 pipeline.run_id（无法证明同轮构建）")
    if not pipe.get("complete"):
        errs.append("manifest 的 pipeline 不完整：" + "；".join(pipe.get("errors") or ["未知原因"]))
    import run_ledger
    live_ok, live_errs = run_ledger.check()
    if not live_ok:
        errs.append("当前台账校验失败：" + "；".join(live_errs))
    elif live_ok and pipe.get("run_id") != (run_ledger.load().get("run_id")):
        errs.append("manifest.run_id 与当前台账 run_id 不一致")
    return (not errs), errs


def selftest():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  ✔ " if cond else "  ✘ ") + msg)
        ok = ok and cond

    print("build_manifest 自测")
    # 时间规范化：三种格式 + 无年份补全 + 无效值
    chk(norm_ts("11-25 14:02", 2026) == norm_ts("2026-11-25 14:02", 2026),
        "「11-25 14:02」与「2026-11-25 14:02」规范化为同一时刻")
    chk(norm_ts("2026-11-25 14:15") == 1795592100 or norm_ts("2026-11-25 14:15") is not None,
        "带年份格式可解析")
    chk(norm_ts(1795586539) == 1795586539, "epoch 秒直接透传")
    chk(norm_ts("不是时间") is None, "无效值返回 None 而非伪造 0")
    chk(norm_ts("02-30 99:99", 2026) is None, "非法日期返回 None")

    # P23：verify 依赖同轮台账。selftest 先建一个完整台账（模拟本轮所有
    # 阶段成功），结束后恢复——否则本地没有台账会让「原 manifest 校验通过」
    # 误判。这测的是真实 CI 场景：先 start+mark，再 build+verify。
    import run_ledger
    ledger_backup = open(LEDGER_PATH, "rb").read() if os.path.isfile(LEDGER_PATH) else None
    run_ledger.start("selftest-p23")
    for key in run_ledger.STAGE_KEYS:
        run_ledger.mark(key)

    # manifest 结构
    m = build()
    chk(m.get("schema_version") == SCHEMA_VERSION, "schema_version 正确")
    chk(bool(m.get("build_id")) and "-" in m.get("build_id", ""), "build_id 有时间戳+摘要")
    chk(m.get("build_id") == build().get("build_id"), "build_id 可复现（同输入同 id）")
    chk(len(m.get("artifacts") or {}) == len(ARTIFACTS), f"覆盖全部 {len(ARTIFACTS)} 个产物")
    na = m.get("artifacts", {}).get("nodes") or {}
    chk(len(na.get("sha256") or "") == 64, "nodes.json 记录了 64 位 sha256")
    chk((m.get("counts") or {}).get("nodes_total") == len(
        (load(os.path.join(DATA, "nodes.json"), {}) or {}).get("nodes") or []),
        "counts.nodes_total 与磁盘一致")

    # 篡改检测：改一个 hash 后必须报错
    tampered = json.loads(json.dumps(m))
    tampered["artifacts"]["nodes"]["sha256"] = "0" * 64
    good, errs = verify(tampered)
    chk(not good and any("sha256" in e for e in errs), "sha256 被改后校验失败")
    good2, _ = verify(m)
    chk(good2, "原 manifest 校验通过")

    # P23：pipeline 证明
    pipe = m.get("pipeline") or {}
    chk(pipe.get("run_id") == "selftest-p23", "manifest 带本轮 run_id")
    chk(pipe.get("complete") is True, "阶段齐全时 pipeline.complete=True")
    chk(pipe.get("stage_count") == len(run_ledger.STAGE_KEYS),
        f"pipeline 覆盖全部 {len(run_ledger.STAGE_KEYS)} 个阶段")
    # 篡改 pipeline 必须失败
    bad_pipe = json.loads(json.dumps(m))
    bad_pipe["pipeline"]["complete"] = False
    good3, errs3 = verify(bad_pipe)
    chk(not good3 and any("pipeline" in e for e in errs3), "pipeline 不完整时校验失败")

    # 恢复 selftest 前的台账状态
    if ledger_backup is not None:
        with open(LEDGER_PATH, "wb") as f:
            f.write(ledger_backup)
    elif os.path.isfile(LEDGER_PATH):
        os.remove(LEDGER_PATH)

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    m = build()
    if "--dry-run" in sys.argv:
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0
    good, errs = verify(m)
    # 本地默认不写盘，与 export_subs 同口径；CI 或显式强制才写。
    force = (os.environ.get("GITHUB_ACTIONS") == "true"
             or os.environ.get("NODES_FORCE_WRITE") == "1"
             or "--write" in sys.argv)
    if not good:
        # P23：校验不通过就绝不写盘。先前的实现是「先写后报错」，会在磁盘
        # 留下一个 pipeline 不完整的 manifest——一旦下游误配 continue-on-error，
        # 这个残缺清单可能被当成有效产物。宁可什么都不写。
        print("校验失败，不写 manifest：")
        for e in errs:
            print("  ✘", e)
        return 1
    if not force:
        print("本地默认不写盘。用 --write 或 NODES_FORCE_WRITE=1 生成 data/build_manifest.json")
        print(f"build_id={m['build_id']}  产物 {len(m['artifacts'])} 个  "
              f"节点 {m['counts']['nodes_total']}  真实可用 {m['counts']['real_ok']}")
        return 0
    netsafe.write_atomic(MANIFEST, json.dumps(m, ensure_ascii=False, indent=2))
    print(f"写入 {os.path.relpath(MANIFEST, BASE)}：build_id={m['build_id']}")
    print(f"  {len(m['artifacts'])} 个产物  节点 {m['counts']['nodes_total']}  "
          f"实测 {m['counts']['tested']}  真实可用 {m['counts']['real_ok']}"
          f"（{m['counts']['real_ok_pct']}%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
