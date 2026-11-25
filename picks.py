"""P18：四类优选榜单。

稳定 / 低延迟 / 高速 / 综合质量。每类都附带**排序依据**（`basis`），
页面上直接显示，用户不用猜「为什么这条排第一」。

## 候选池怎么来

只用「本轮测过且通过」的节点会让榜单严重偏小：每轮只抽 1,200 / 9,846，
就算通过率 100%，也只能覆盖 12%。所以候选是两部分的并集：

1. **本轮实测通过**的节点——证据最新。
2. **历史成功率 ≥90% 且测过 ≥2 次、最近 7 天成功过**的节点——本轮没抽到，
   不代表它坏了。

第二部分的每条结果都标 `src: "history"`，延迟/速度取历史中位数而不是
伪造成本轮实测。页面必须能区分这两种来源，否则「没有数据」会被读成「数据很好」。

## 缺失数据不伪装成零

没有延迟记录的节点不进低延迟榜（不是记 0ms 排第一），
没有测速记录的不进高速榜。这是任务书 §6 的硬要求：
「缺失测速不等于 0 Mbps」。

## 可复现

所有排序键最后都带 `fp` 兜底，保证同分同延迟时顺序稳定。
两次相同输入跑出不同顺序，用户就会觉得榜单是随机的。
"""

import json
import os
from datetime import datetime, timedelta, timezone

import history
import netsafe

BASE = os.path.dirname(os.path.abspath(__file__))
NODECHECK = os.path.join(BASE, "data", "nodecheck.json")
SCORES = os.path.join(BASE, "data", "node_scores.json")
FULL = os.path.join(BASE, "export", "nodes_full.json")
OUT = os.path.join(BASE, "data", "picks.json")

CST = timezone(timedelta(hours=8))

# 阈值。两个数字都对着实测分布定，不是拍脑袋：
# 本轮 P50 延迟 675ms、P90 3010ms → 300ms 落在前 25% 左右，是「明显快」而不是「还行」
LOWLAT_MS = 300
# 免费节点测速 P50 约 400KB/s；1MB/s 约在前 20%
FAST_BPS = 1_000_000
MIN_TRIES = 2          # 榜单最低样本量：一次成功可能只是运气
LIMIT = 200            # 每榜上限，页面/订阅都用不完
HIST_KEEP_DAYS = 7     # 历史候选的最近成功窗口


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def _cands(check, hist, today):
    """→ {fp: {"rec": 本轮结果 or None, "h": 历史记录}}"""
    out = {}
    for fp, r in (check.get("nodes") or {}).items():
        k = fp[:16]
        h = (hist.get("nodes") or {}).get(k)
        if r.get("ok"):
            out[fp] = {"rec": r, "h": h or {}}
            continue
        # 本轮不通但历史很好：保留在候选里，但后面排序会落后于本轮通过的。
        # 不直接删掉的理由是它可能只是这轮线路抖动，一次失败不该抹掉五次成功。
        if h and history.rate(h) is not None and history.rate(h) >= 0.5 \
                and int(h.get("last_ok") or 0) >= today - 1:
            out[fp] = {"rec": r, "h": h}
    # 补上本轮完全没抽到的历史好节点。用集合查，别拿每个指纹去扫一遍候选
    seen = {fp[:16] for fp in out}
    for k, h in (hist.get("nodes") or {}).items():
        if k in seen:
            continue
        if int(h.get("tries") or 0) < MIN_TRIES:
            continue
        r = history.rate(h)
        if r is None or r < 0.9:
            continue
        if int(h.get("last_ok") or 0) < today - HIST_KEEP_DAYS:
            continue
        # 用历史记录里的 fp 全名找不到，只有 16 位；下游按前 16 位匹配
        out[k] = {"rec": None, "h": h}
    return out


def _meta(fp, by_fp, scores):
    """节点展示信息。

    名字取自 export/nodes_full.json（CI 里一定有；本地跑没有 export 是因为
    .gitignore 排除了含凭据的导出）。缺失时拼一个 `协议-地区-指纹前6位` 的
    标识，而不是留空——榜单上出现一排 None 会让人以为是数据坏了。
    这里不回落到节点地址：地址属于「能用但不必公开到榜单」的信息。
    """
    n = by_fp.get(fp) or by_fp.get(fp[:16]) or {}
    sc = (scores.get("nodes") or {}).get(fp) or {}
    typ = n.get("type") or sc.get("proto")
    rg = sc.get("rg") or "未知"
    name = (n.get("name") or "").strip()[:40]
    if not name:
        name = f"{typ or 'node'}-{rg}-{fp[:6]}"
    return {
        "name": name,
        "type": typ,
        "cc": sc.get("cc") or n.get("cc"),
        "rg": rg,
        "score": sc.get("s"),
    }


def build(check=None, hist=None, scores=None, full=None, today=None):
    check = check if check is not None else load(NODECHECK, {})
    hist = hist if hist is not None else history.load()
    scores = scores if scores is not None else load(SCORES, {})
    full = full if full is not None else load(FULL, [])
    today = history.day_of() if today is None else today

    by_fp = {}
    for n in (full or []):
        fp = n.get("fp") or ""
        by_fp[fp] = n
        by_fp[fp[:16]] = n

    cands = _cands(check, hist, today)
    rows = []
    dropped = 0
    for fp, c in cands.items():
        # 历史里还有、但已经不在当前节点库里的（源下架了）：不能进榜。
        # 订阅是按节点库生成的，榜单里有而库里没有，两边条数就对不上，
        # 一致性检查会报错——那个报错是对的，但根因在这里。
        if by_fp and fp not in by_fp and fp[:16] not in by_fp:
            dropped += 1
            continue
        h = c["h"]
        rec = c["rec"]
        # 延迟/速度：本轮有就用本轮的，否则用历史中位数（并标 src=history）
        ms = (rec or {}).get("ms") if rec else None
        bps = (rec or {}).get("bps") if rec else None
        src = "probe" if rec and rec.get("ok") else "history"
        if ms is None:
            ms = history.ms_med(h)
        if bps is None:
            bps = history.bps_med(h)
        st, st_note = history.stability(h)
        r = history.rate(h)
        m = _meta(fp, by_fp, scores)
        rows.append({
            "fp": fp, "src": src,
            "rate": round(r, 3) if r is not None else None,
            "tries": int(h.get("tries") or 0),
            "hits": int(h.get("hits") or 0),
            "ok_run": int(h.get("ok_run") or 0),
            "ms": ms, "bps": bps,
            "stability": st, "stability_note": st_note,
            "last_ok": h.get("last_ok"),
            "now_ok": bool(rec and rec.get("ok")),
            **m,
        })

    def take(arr, limit=LIMIT):
        return arr[:limit]

    # 稳定：先看成功率，再看样本量，最后看延迟。
    # 成功率相同的两条，测过 8 次的比测过 2 次的更可信——这就是 tries 排在第二位的理由
    stable = [r for r in rows if r["stability"] == "stable"]
    stable.sort(key=lambda r: (-r["rate"], -r["tries"], r["ms"] or 9999, r["fp"]))

    # 低延迟：只看有延迟数据的，缺的不参与（不记 0）
    lowlat = [r for r in rows if r["ms"] is not None and r["tries"] >= MIN_TRIES]
    lowlat = [r for r in lowlat if r["ms"] <= LOWLAT_MS]
    lowlat.sort(key=lambda r: (r["ms"], -(r["rate"] or 0), r["fp"]))

    # 高速：同样只收有测速的
    fast = [r for r in rows if r["bps"] is not None and r["tries"] >= MIN_TRIES]
    fast = [r for r in fast if r["bps"] >= FAST_BPS]
    fast.sort(key=lambda r: (-r["bps"], -(r["rate"] or 0), r["fp"]))

    # 综合：评分优先，但正在崩的（poor）不进榜——分数是历史积累的，
    # 一条连败三次的节点分数再高也不该推荐
    best = [r for r in rows if r["score"] is not None
            and r["stability"] != "poor" and r["now_ok"]]
    best.sort(key=lambda r: (-(r["score"] or 0), -(r["rate"] or 0), r["fp"]))

    basis = {
        "stable": {
            "title": "稳定",
            "rule": f"测过 ≥3 次且成功率 ≥80%，当前没有连续失败",
            "order": "成功率 ↓ → 测试次数 ↓ → 中位延迟 ↑",
        },
        "lowlat": {
            "title": "低延迟",
            "rule": f"中位延迟 ≤{LOWLAT_MS}ms，测过 ≥{MIN_TRIES} 次",
            "order": "中位延迟 ↑ → 成功率 ↓",
        },
        "fast": {
            "title": "高速",
            "rule": f"中位速度 ≥{FAST_BPS // 1000}KB/s，测过 ≥{MIN_TRIES} 次",
            "order": "中位速度 ↓ → 成功率 ↓",
        },
        "best": {
            "title": "综合质量",
            "rule": "本轮检测通过，且不在「连续失败」状态",
            "order": "评分 ↓ → 成功率 ↓",
        },
    }

    lists = {"stable": take(stable), "lowlat": take(lowlat),
             "fast": take(fast), "best": take(best)}
    matched = {"stable": len(stable), "lowlat": len(lowlat),
               "fast": len(fast), "best": len(best)}
    doc = {
        "updated": datetime.now(CST).strftime("%Y-%m-%d %H:%M"),
        "window_days": history.WINDOW_DAYS,
        "min_tries": MIN_TRIES,
        "limit": LIMIT,
        "basis": basis,
        # counts 是**实际输出**的条数（订阅按它对账），matched 是符合条件的总数。
        # 两个数分开记：综合榜符合条件 315 条但只输出 200 条，
        # 只写一个数就会出现「榜单说 315、订阅只有 200」，一致性检查必然报错
        "counts": {k: len(v) for k, v in lists.items()},
        "matched": matched,
        # 已从库里消失、被剔除出榜的候选数。不静默丢：这个数变大说明
        # 历史窗口比节点库的上下架速度慢，值得调整窗口
        "dropped": dropped,
        "lists": lists,
    }
    return doc


def save(doc, force=None):
    force = (os.environ.get("GITHUB_ACTIONS") == "true"
             or os.environ.get("NODES_FORCE_WRITE") == "1") if force is None else force
    if not force:
        print("  （本地运行，不写入 picks.json）")
        return False
    netsafe.write_atomic(OUT, json.dumps(doc, ensure_ascii=False,
                                         separators=(",", ":")))
    return True


def summarize(doc):
    c, m = doc["counts"], doc.get("matched") or doc["counts"]
    parts = []
    for k in ("stable", "lowlat", "fast", "best"):
        tail = f"（符合 {m[k]}）" if m.get(k, 0) != c[k] else ""
        parts.append(f"{doc['basis'][k]['title']} {c[k]}{tail}")
    return "优选榜单：" + " · ".join(parts)


def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("  ✔ " if c else "  ✘ ") + m)
        ok = ok and c

    T = 20000
    # 造 4 条节点：稳定快、稳定慢、只测一次、连续失败
    H = {"nodes": {
        "a" * 16: {"first": T - 9, "last": T, "last_ok": T, "tries": 6, "hits": 6,
                   "ok_run": 6, "fail_run": 0, "slots": "333333",
                   "ms": [120, 130, 125], "bps": [3_000_000, 3_200_000]},
        "b" * 16: {"first": T - 9, "last": T, "last_ok": T, "tries": 5, "hits": 5,
                   "ok_run": 5, "fail_run": 0, "slots": "333333",
                   "ms": [800, 900], "bps": [400_000]},
        "c" * 16: {"first": T - 1, "last": T, "last_ok": T, "tries": 1, "hits": 1,
                   "ok_run": 1, "fail_run": 0, "slots": "300000",
                   "ms": [90], "bps": []},
        "d" * 16: {"first": T - 20, "last": T, "last_ok": T - 3, "tries": 6, "hits": 5,
                   "ok_run": 0, "fail_run": 3, "slots": "233331",
                   "ms": [200], "bps": [2_000_000]},
    }}
    CK = {"nodes": {
        "a" * 32: {"ok": True, "ms": 120, "bps": 3_000_000, "cc": "JP"},
        "b" * 32: {"ok": True, "ms": 800, "cc": "US"},
        "c" * 32: {"ok": True, "ms": 90, "cc": "HK"},
        "d" * 32: {"ok": False, "err": "timeout"},
    }}
    SC = {"nodes": {
        "a" * 32: {"s": 88, "cc": "JP", "rg": "日本"},
        "b" * 32: {"s": 70, "cc": "US", "rg": "美国"},
        "c" * 32: {"s": 60, "cc": "HK", "rg": "香港"},
        "d" * 32: {"s": 85, "cc": "SG", "rg": "新加坡"},
    }}
    doc = build(check=CK, hist=H, scores=SC, full=[], today=T)

    st = [r["fp"] for r in doc["lists"]["stable"]]
    chk(st == ["a" * 32, "b" * 32], f"稳定榜只收样本够的（{len(st)} 条）")

    ll = [r["fp"] for r in doc["lists"]["lowlat"]]
    chk(ll == ["a" * 32], f"低延迟榜不含 800ms、不含仅测过一次的（{ll}）")

    fa = [r["fp"] for r in doc["lists"]["fast"]]
    chk(fa == ["a" * 32], "高速榜只收 ≥1MB/s 且样本足够的")

    be = [r["fp"] for r in doc["lists"]["best"]]
    chk("d" * 32 not in be, "连败的节点不进综合榜（分数高也不进）")
    chk("c" * 32 in be, "仅测一次但本轮通过的仍可进综合榜")

    # 缺失数据不伪装成零
    row = next(r for r in doc["lists"]["lowlat"] if r["fp"] == "a" * 32)
    chk(row["ms"] == 120 and row["src"] == "probe", "本轮数据优先")
    c_row = next(r for r in doc["lists"]["best"] if r["fp"] == "c" * 32)
    chk(c_row["bps"] is None, "没有测速时 bps 为 null，不是 0")

    # 可复现：重建一次顺序必须一致
    doc2 = build(check=CK, hist=H, scores=SC, full=[], today=T)
    chk([r["fp"] for r in doc2["lists"]["best"]] == be, "同输入排序可复现")

    # 平局靠 fp 兜底：三条完全一样的数据，顺序必须按 fp
    H2 = {"nodes": {}}
    for i, k in enumerate("xyz"):
        H2["nodes"][k * 16] = {"first": T, "last": T, "last_ok": T, "tries": 3,
                               "hits": 3, "ok_run": 3, "fail_run": 0,
                               "slots": "333000", "ms": [100], "bps": []}
    CK2 = {"nodes": {k * 32: {"ok": True, "ms": 100} for k in "xyz"}}
    d3 = build(check=CK2, hist=H2, scores={"nodes": {}}, full=[], today=T)
    chk([r["fp"] for r in d3["lists"]["lowlat"]] == ["x" * 32, "y" * 32, "z" * 32],
        "完全平局按指纹排序，结果稳定")

    # 空输入不炸
    d4 = build(check={"nodes": {}}, hist={"nodes": {}}, scores={"nodes": {}},
               full=[], today=T)
    chk(all(v == 0 for v in d4["counts"].values()), "无数据时四榜为空且不报错")
    chk(d4["basis"]["stable"]["order"], "排序依据总能给出")

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    d = build()
    save(d)
    print(summarize(d))
    for k, v in d["lists"].items():
        print(f"\n== {d['basis'][k]['title']}（{len(v)}）{d['basis'][k]['rule']}")
        print(f"   排序：{d['basis'][k]['order']}")
        for r in v[:5]:
            tag = "实测" if r["src"] == "probe" else "历史"
            print(f"   {str(r['name'])[:18]:<20}{r['rg'] or '':<6}"
                  f"{r['rate'] or 0:.0%} ({r['hits']}/{r['tries']})  "
                  f"{r['ms'] or '-'}ms  {r['score']}  {tag}")
    sys.exit(0)
