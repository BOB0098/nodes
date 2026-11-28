#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日优选 Top50（GitHub 版 v2：按国家分类）。

只做一件事：从 data/picks.json 的综合榜 best 里选 50 条，
写成脱敏的 data/daily_top50.json，供网站展示和邮件使用。

v1 的问题（2026-11-28 实测）：只按评分取前 50，
结果是美国 19 条、未知 14 条、欧洲 10 条、香港 5 条、台湾 1 条，
日本 / 韩国 / 新加坡一条都没有。不是因为那些国家没有好节点，
而是它们的最好成绩本身就比美国低（日本最高 61.5 分，新加坡 65.2 分，
美国最高 86.7 分），纯按分排永远轮不到它们。

v2 的做法：质量最好 + 按国家保底，两段式——
  第 1 段（保底，每国 3 条）：11 个常用国家各取本国最好的 3 条，
    香港 / 台湾 / 日本 / 韩国 / 新加坡 / 美国 / 英国 / 德国 / 法国 /
    荷兰 / 加拿大。哪个国家在综合榜里不够 3 条就取现有的几条，不硬凑。
  第 2 段（补齐到 50）：剩下名额按全球评分顺序补，但单个国家最多 8 条，
    “未知”国家最多 5 条，避免又被美国 / 未知占满。
  这样每个常用国家至少有 3 条（有货的前提下），同时整体仍是高分段。
  代价要说清楚：为了让日本 / 新加坡进来，最低分会从 74.9 降到约 58 分，
  那是日本 / 新加坡本国最好的 3 条，不是放水。

国家怎么定：优先用 cc（出口 IP 所在国，node_scores 里的实测值，
比节点名字可靠，名字经常写错国）；cc 为空时看名字里的国旗
（🇭🇰 🇹🇼 🇯🇵 🇰🇷 🇸🇬 🇺🇸 🇬🇧 🇩🇪 🇫🇷 🇳🇱 🇨🇦），再没有就按 rg 映射。
输出里每条都带 country 字段，by_country 里按国家分组，网站和邮件
都可以直接按国家展示。

其他要点和 v1 一样：
- 不重新检测、不重新评分，只复用 picks.py 已经排好的综合榜。
- 脱敏：只保留 picks.json 里本来就公开的字段，不含 URI / 凭据 / 地址。
- 探测口径：GitHub 海外 runner，延迟和连通性是海外视角，速度排名可用；
  切到国内服务器后只改 probe 字段，逻辑不变。
- 不碰 run_ledger / build_manifest / check_consistency，单独由
  .github/workflows/daily_top50.yml 生成和提交。

用法：
    python3 daily_top50.py --selftest
    python3 daily_top50.py
    NODES_FORCE_WRITE=1 python3 daily_top50.py
输入：data/picks.json（lists.best）
输出：data/daily_top50.json
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
PICKS = os.path.join(BASE, "data", "picks.json")
OUT = os.path.join(BASE, "data", "daily_top50.json")

CST = timezone(timedelta(hours=8))
LIMIT = 50
PER_COUNTRY_GUARANTEE = 3
PER_COUNTRY_MAX = 8
UNKNOWN_MAX = 5

# 11 个常用国家。顺序不重要，保底时每国轮流取。
PRIORITY_COUNTRIES = ["HK", "TW", "JP", "KR", "SG",
                       "US", "GB", "DE", "FR", "NL", "CA"]
COUNTRY_LABEL = {
    "HK": "香港", "TW": "中国台湾", "JP": "日本", "KR": "韩国",
    "SG": "新加坡", "US": "美国", "GB": "英国", "DE": "德国",
    "FR": "法国", "NL": "荷兰", "CA": "加拿大",
}
FLAG_TO_CC = {
    "🇭🇰": "HK", "🇹🇼": "TW", "🇯🇵": "JP", "🇰🇷": "KR",
    "🇸🇬": "SG", "🇺🇸": "US", "🇬🇧": "GB", "🇩🇪": "DE",
    "🇫🇷": "FR", "🇳🇱": "NL", "🇨🇦": "CA", "🇦🇺": "AU",
    "🇧🇷": "BR",
}
RG_TO_CC = {
    "香港": "HK", "中国台湾": "TW", "日本": "JP", "韩国": "KR",
    "新加坡": "SG", "美国": "US",
}

KEEP = ("fp", "name", "type", "cc", "rg", "score", "rate", "tries", "hits",
        "ok_run", "ms", "bps", "stability", "stability_note", "src", "now_ok",
        "last_ok")


def now_str():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M")


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def country_of(r):
    """cc 优先（出口 IP 实测），其次名字国旗，最后 rg。"""
    cc = (r.get("cc") or "").strip().upper()
    if cc:
        return cc
    name = r.get("name") or ""
    for flag, code in FLAG_TO_CC.items():
        if flag in name:
            return code
    return RG_TO_CC.get(r.get("rg"), "UNKNOWN")


def select_top50(best):
    """best 已按评分排好。返回按 rank 排好的 50 条（每条带 country）。"""
    by_country = defaultdict(list)
    for r in best:
        by_country[country_of(r)].append(r)
    selected, seen = [], set()

    def take(cc, n):
        cnt = 0
        for r in by_country.get(cc, []):
            if r.get("fp") in seen:
                continue
            if cnt >= n:
                break
            selected.append(r)
            seen.add(r.get("fp"))
            cnt += 1
        return cnt

    # 第 1 段：每国保底 3 条
    for cc in PRIORITY_COUNTRIES:
        take(cc, PER_COUNTRY_GUARANTEE)
    # 第 2 段：按全球评分补到 50，单国最多 8 条，未知最多 5 条
    counts = Counter(country_of(r) for r in selected)
    for r in best:
        if len(selected) >= LIMIT:
            break
        if r.get("fp") in seen:
            continue
        c = country_of(r)
        if c == "UNKNOWN" and counts["UNKNOWN"] >= UNKNOWN_MAX:
            continue
        if counts[c] >= PER_COUNTRY_MAX:
            continue
        selected.append(r)
        seen.add(r.get("fp"))
        counts[c] += 1
    # 最终排名：保底段打乱了全局顺序，重新按评分排，rank 1 就是全场最高分
    selected.sort(key=lambda r: (-(r.get("score") or 0),
                                 -(r.get("rate") or 0),
                                 r.get("fp") or ""))
    return selected[:LIMIT]


def build(picks=None, today=None):
    picks = picks if picks is not None else load(PICKS, {})
    today = now_str() if today is None else today
    best = (picks.get("lists") or {}).get("best") or []
    picked = select_top50(best)
    rows = []
    for i, r in enumerate(picked, start=1):
        row = {k: r.get(k) for k in KEEP}
        row["rank"] = i
        row["country"] = country_of(r)
        row["country_label"] = COUNTRY_LABEL.get(row["country"], row["country"])
        rows.append(row)
    by_country = {}
    for r in rows:
        by_country.setdefault(r["country"], []).append(r["fp"])
    return {
        "updated": today,
        "source_picks_updated": picks.get("updated"),
        "limit": LIMIT,
        "count": len(rows),
        "probe": "github-actions",
        "probe_label": "海外 runner 实测（非国内视角）",
        "probe_note": ("延迟和连通性是海外 runner 测出来的，不代表国内用户体验；"
                       "速度排名可用。切到国内服务器后 probe 改为国内实测，"
                       "Top50 逻辑不变。"),
        "basis": {
            "title": "每日优选 Top50",
            "rule": ("11 个常用国家各保底 3 条（香港/台湾/日本/韩国/新加坡/"
                     "美国/英国/德国/法国/荷兰/加拿大），剩余名额按评分补齐，"
                     "单国最多 8 条，未知最多 5 条"),
            "order": "评分 ↓ → 成功率 ↓ → 指纹",
        },
        "by_country": {k: len(v) for k, v in sorted(by_country.items())},
        "lists": {"top50": rows},
    }


def save(doc, force=None):
    force = (os.environ.get("GITHUB_ACTIONS") == "true"
             or os.environ.get("NODES_FORCE_WRITE") == "1") if force is None else force
    if not force:
        print("  （本地运行，不写入 daily_top50.json）")
        return False
    import netsafe
    netsafe.write_atomic(OUT, json.dumps(doc, ensure_ascii=False,
                                          separators=(",", ":")))
    return True


def summarize(doc):
    rows = doc["lists"]["top50"]
    if not rows:
        return "每日优选 Top50：0 条（综合榜为空，今天不发邮件）"
    by = doc.get("by_country") or {}
    by_s = " · ".join(f"{k} {v}" for k, v in sorted(by.items()))
    top = rows[0]
    return (f"每日优选 Top50：{len(rows)} 条 · 更新于 {doc['updated']} · "
            f"第 1 名 {top.get('name')}（{top.get('score')} 分）\n"
            f"按国家：{by_s}")


def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("  ✔ " if c else "  ✘ ") + m)
        ok = ok and c

    # 造 3 个国家各 5 条 + 未知 10 条，分数递减，验证保底和上限
    best = []
    for ci, cc in enumerate(["HK", "JP", "US"]):
        for i in range(5):
            best.append({
                "fp": f"{cc}{i:030d}", "name": f"{cc}-{i}", "type": "vless",
                "cc": cc, "rg": "日本" if cc == "JP" else "香港",
                "score": 90 - ci * 10 - i, "rate": 1.0, "tries": 5,
                "hits": 5, "ok_run": 5, "ms": 100, "bps": 1000000,
                "stability": "stable", "stability_note": "5/5",
                "src": "probe", "now_ok": True, "last_ok": 20785,
                "uri": "vless://secret", "addr": "1.2.3.4"})
    for i in range(10):
        best.append({
            "fp": f"UN{i:030d}", "name": f"未知-{i}", "type": "vless",
            "cc": None, "rg": "未知", "score": 85 - i, "rate": 1.0,
            "tries": 5, "hits": 5, "ok_run": 5, "ms": 100, "bps": 1000000,
            "stability": "stable", "stability_note": "5/5",
            "src": "probe", "now_ok": True, "last_ok": 20785})
    # best 必须按评分排好（和 picks.py 的输出一致）
    best.sort(key=lambda r: (-r["score"], r["fp"]))
    doc = build(picks={"updated": "2026-11-28 07:39",
                       "lists": {"best": best}}, today="2026-11-28 08:00")
    by = doc["by_country"]
    chk(by.get("JP", 0) >= 3, f"日本至少保底 3 条（实际 {by.get('JP', 0)} 条）")
    chk(by.get("HK", 0) >= 3, f"香港至少保底 3 条（实际 {by.get('HK', 0)} 条）")
    chk(by.get("UNKNOWN", 0) <= 5, f"未知最多 5 条（实际 {by.get('UNKNOWN', 0)} 条）")
    chk(doc["count"] == 20, f"总数 20 条：3 国各 5 条 + 未知被上限卡到 5 条（出了 {doc['count']} 条）")
    chk(doc["lists"]["top50"][0]["rank"] == 1, "第 1 条 rank=1")
    chk("uri" not in doc["lists"]["top50"][0], "不含 URI（脱敏）")
    chk("country" in doc["lists"]["top50"][0], "每条带 country 字段")
    chk(doc["probe"] == "github-actions", "probe 标明是海外 runner")
    empty = build(picks={"lists": {"best": []}}, today="2026-11-28 08:00")
    chk(empty["count"] == 0, "综合榜为空时 Top50 为空且不报错")
    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    d = build()
    save(d)
    print(summarize(d))
    for r in d["lists"]["top50"][:10]:
        print(f"   {r['rank']:>2}. [{r['country']}] {str(r['name'])[:24]:<26}"
              f"{r['score']} 分  {r['ms']}ms  {r['rate'] or 0:.0%}")
    sys.exit(0)
