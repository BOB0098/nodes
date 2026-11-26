#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P28：best 评分门槛的持续观测（不拍脑袋定阈值）。

背景：第二轮审计问题 9 要求对「best 65 分门槛」做连续 7 天评估。审计发现
picks.py 当前并没有 65 分硬门槛——best 是「有评分 + 非 poor + 当前通过」
取前 200。所以先要搞清楚：如果加一个分数门槛，会砍掉多少节点？这个比例
随轮次怎么变？单次会话拿不到 7 天数据，本脚本让 CI 每轮追加一条真实观测，
攒够 7 天再定论，绝不把一天的快照当结论。

每轮写一行到 data/threshold_watch.json：
  - ts            本轮时间（取自 node_scores.json 的 updated；仅作时间字段，不作身份）
  - snapshot_id   node_scores.json 与 picks.json 原始内容的 SHA-256 输入指纹
  - total         本轮有评分的节点数
  - quantiles     p25/p50/p75/p90 分数
  - at_least      各门槛(50/55/60/65/70/75)的真实条数与占比
  - best_n        picks.json 里 best 组当前成员数
  - best_min/max  best 组实际分数范围（证明「65 门槛」是否已经在起作用）

用法：
    python3 threshold_watch.py            # 追加本轮观测（本地需 NODES_FORCE_WRITE=1）
    python3 threshold_watch.py --report   # 打印观测汇总与是否满 7 天
    python3 threshold_watch.py --selftest # 自测
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
SCORES = os.path.join(DATA, "node_scores.json")
PICKS = os.path.join(DATA, "picks.json")
OUT = os.path.join(DATA, "threshold_watch.json")
SCHEMA_VERSION = 1
TZ = timezone(timedelta(hours=8))
THRESHOLDS = (50, 55, 60, 65, 70, 75)
NEEDED_DAYS = 7


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _read_input(path):
    """Read an observation input once, retaining its exact bytes for identity."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        return None, {}
    try:
        return raw, json.loads(raw.decode("utf-8"))
    except Exception:
        return raw, {}


def _snapshot_id(scores_raw, picks_raw):
    """Fingerprint exact source bytes; legacy rows intentionally have no match."""
    digest = hashlib.sha256()
    for name, raw in ((b"scores", scores_raw), (b"picks", picks_raw)):
        digest.update(name + b"\0")
        if raw is None:
            digest.update(b"MISSING\0")
        else:
            digest.update(b"PRESENT\0")
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
    return digest.hexdigest()


def percentile(vals, q):
    """线性插值分位数。空列表返回 None，绝不返回 0（缺失不是 0 分）。"""
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return round(s[0], 1)
    pos = (len(s) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    frac = pos - lo
    return round(s[lo] * (1 - frac) + s[hi] * frac, 1)


def observe():
    """从真实评分/榜单数据算一行观测。不写盘。"""
    scores_raw, sc = _read_input(SCORES)
    picks_raw, pk = _read_input(PICKS)
    sc = sc or {}
    pk = pk or {}
    nodes = sc.get("nodes") or {}
    vals = [float(r["s"]) for r in (nodes.values() if isinstance(nodes, dict) else nodes)
            if isinstance(r, dict) and isinstance(r.get("s"), (int, float))]
    best = (pk.get("lists") or {}).get("best") or []
    best_scores = []
    for r in best:
        fp = r.get("fp") if isinstance(r, dict) else None
        rec = nodes.get(fp) if isinstance(nodes, dict) else None
        if isinstance(rec, dict) and isinstance(rec.get("s"), (int, float)):
            best_scores.append(float(rec["s"]))
    return {
        "ts": sc.get("updated"),
        "snapshot_id": _snapshot_id(scores_raw, picks_raw),
        "total": len(vals),
        "quantiles": {f"p{q}": percentile(vals, q / 100) for q in (25, 50, 75, 90)},
        "at_least": {str(t): {"n": sum(1 for v in vals if v >= t),
                              "pct": round(sum(1 for v in vals if v >= t) / len(vals) * 100, 1)
                              if vals else None} for t in THRESHOLDS},
        "best_n": len(best),
        "best_min": round(min(best_scores), 1) if best_scores else None,
        "best_max": round(max(best_scores), 1) if best_scores else None,
    }


def append_observation(force=False):
    row = observe()
    doc = load(OUT)
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        doc = {"schema_version": SCHEMA_VERSION, "days_needed": NEEDED_DAYS, "rows": []}
    rows = doc.setdefault("rows", [])
    # ts 只有分钟精度，不作为身份。相同输入快照即使非相邻重跑也只记一次；
    # 不同输入指纹可有相同 ts。旧行无指纹，不推断其身份，也不改写历史。
    if any(isinstance(existing, dict)
           and existing.get("snapshot_id") == row["snapshot_id"]
           for existing in rows):
        return doc, False
    rows.append(row)
    if force or os.environ.get("GITHUB_ACTIONS") == "true":
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
    return doc, True


def distinct_days(doc):
    """观测覆盖的不同自然日数（按 ts 的日期部分）。"""
    days = set()
    for r in doc.get("rows") or []:
        ts = str(r.get("ts") or "")
        if len(ts) >= 10:
            days.add(ts[:10])
    return len(days)


def selftest():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  ✔ " if cond else "  ✘ ") + msg)
        ok = ok and bool(cond)

    print("threshold_watch 自测")
    chk(percentile([], 0.5) is None, "空样本分位数返回 None 而非 0")
    chk(percentile([1, 2, 3, 4], 0.5) == 2.5, "偶数样本中位数插值正确")
    row = observe()
    chk(row.get("total", 0) > 0, "能从真实 node_scores.json 读到评分")
    chk(all(isinstance(row["at_least"][str(t)]["n"], int) for t in THRESHOLDS),
        "各门槛条数都是真实整数")
    chk(row.get("best_min") is not None, "best 组分数范围可计算")
    doc = {"rows": [{"ts": "2026-11-20 10:00"}, {"ts": "2026-11-20 12:00"},
                    {"ts": "2026-11-21 10:00"}]}
    chk(distinct_days(doc) == 2, "按自然日去重计天数（同日两轮算一天）")
    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if "--report" in sys.argv:
        doc = load(OUT, {"rows": []})
        rows = doc.get("rows") or []
        days = distinct_days(doc)
        print(f"门槛观测：{len(rows)} 轮，覆盖 {days}/{NEEDED_DAYS} 个自然日")
        if rows:
            last = rows[-1]
            print(f"  最近一轮 {last.get('ts')}：有评分 {last.get('total')} 条，"
                  f"p50={last['quantiles'].get('p50')}，"
                  f"≥65 有 {last['at_least'].get('65', {}).get('n')} 条"
                  f"（{last['at_least'].get('65', {}).get('pct')}%）")
            print(f"  best 组 {last.get('best_n')} 条，分数范围 "
                  f"{last.get('best_min')} ~ {last.get('best_max')}")
        if days < NEEDED_DAYS:
            print(f"  结论：未满 {NEEDED_DAYS} 天，不下阈值结论（本会话无法加速）")
        else:
            print(f"  结论：已满 {NEEDED_DAYS} 天，可据 rows 评估是否调整 best 门槛")
        return 0
    force = "--force" in sys.argv or os.environ.get("NODES_FORCE_WRITE") == "1"
    doc, added = append_observation(force=force)
    row = doc["rows"][-1] if added else observe()
    print(f"门槛观测：{'已追加' if added else '同轮不重复'} "
          f"{row.get('ts')}，有评分 {row['total']} 条，"
          f"≥65 {row['at_least']['65']['n']} 条")
    if not added:
        return 0
    print(f"  覆盖 {distinct_days(doc)}/{NEEDED_DAYS} 天"
          + ("" if force else "（本地未写盘，用 --force 或 NODES_FORCE_WRITE=1）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
