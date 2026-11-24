#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P7：节点质量评分、地区分类与标签。

输入是 nodecheck.py 的真实检测结果（能不能出网、延迟、出口 IP），
输出是每条节点的分数、标签，和按真实出口 IP 归类后的地区分布。

评分的权重照任务书的建议：真实可用性 30 / 延迟 20 / 速度 20 / 多轮稳定性 15 /
IP 质量 10 / 流媒体与 AI 5。其中**流媒体与 AI 这一项本模块恒为 0**，因为没做
那类检测——那需要登录态、地区版权库和持续维护的判定规则，做不准就会给出
「看起来精确但会过期」的结论。少 5 分比编 5 分强，所以总分上限实际是 95。

同样，「IP 质量」是启发式：按 ISP/ASN 里的关键词判断是否机房 IP。
机房 IP 更容易被目标站风控，但它不是「风险 IP」的完整定义（黑名单、
代理库命中这些都没有数据源），所以这一项只给 10 分且标签写「机房 IP」
而不是「高风险」。
"""

import json
import math
import os
import time
from collections import Counter

import netsafe

BASE = os.path.dirname(os.path.abspath(__file__))
NODECHECK = os.path.join(BASE, "data", "nodecheck.json")
OUT = os.path.join(BASE, "data", "node_scores.json")
HIST = os.path.join(BASE, "data", "node_score_history.json")
EXPORT = os.path.join(BASE, "export")

KEEP_ROUNDS = 10

# 出口国家 → 页面上那 8 个分类。任务书要求按真实出口 IP 分，
# 不用节点名里的国旗：名字是作者自己写的，与实际出口经常对不上。
REGION = {
    "HK": "香港", "TW": "中国台湾", "JP": "日本", "SG": "新加坡",
    "US": "美国", "KR": "韩国",
    "GB": "欧洲", "DE": "欧洲", "FR": "欧洲", "NL": "欧洲", "SE": "欧洲",
    "FI": "欧洲", "PL": "欧洲", "CH": "欧洲", "IT": "欧洲", "ES": "欧洲",
    "IE": "欧洲", "AT": "欧洲", "BE": "欧洲", "DK": "欧洲", "NO": "欧洲",
    "CZ": "欧洲", "RO": "欧洲", "UA": "欧洲", "RU": "欧洲", "HU": "欧洲",
    "PT": "欧洲", "GR": "欧洲", "BG": "欧洲", "LT": "欧洲", "LV": "欧洲",
    "EE": "欧洲", "SK": "欧洲", "SI": "欧洲", "HR": "欧洲", "RS": "欧洲",
    "MD": "欧洲", "BY": "欧洲", "LU": "欧洲", "IS": "欧洲", "MT": "欧洲",
    "CY": "欧洲", "AL": "欧洲", "BA": "欧洲", "MK": "欧洲", "ME": "欧洲",
}

# 机房/云厂商关键词。命中说明这是 VPS 而不是家宽，被风控的概率更高。
DC_WORDS = ("amazon", "aws", "google", "microsoft", "azure", "digitalocean",
            "linode", "vultr", "hetzner", "ovh", "oracle", "alibaba", "aliyun",
            "tencent", "cloud", "hosting", "server", "data center", "datacenter",
            "colo", "vps", "leaseweb", "contabo", "rackspace", "equinix",
            "choopa", "kamatera", "scaleway", "upcloud", "hostinger")


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def is_datacenter(rec):
    txt = " ".join(str(rec.get(k) or "") for k in ("isp", "asn", "country")).lower()
    return any(w in txt for w in DC_WORDS)


def update_history(results, hist):
    """连续成功/失败轮次。只更新本轮测到的节点，没测的保持原样——
    抽样每轮覆盖不同节点，把「这轮没抽到」当成「失败」会误伤。"""
    nodes = hist.setdefault("nodes", {})
    for fp, r in results.items():
        h = nodes.setdefault(fp, {"ok_streak": 0, "fail_streak": 0,
                                  "last_ok": None, "checked": 0, "ever_ok": False})
        h["checked"] = h.get("checked", 0) + 1
        if r.get("ok"):
            h["ok_streak"] = h.get("ok_streak", 0) + 1
            h["fail_streak"] = 0
            # 「曾经成功过」是区分「不稳定」和「一直就没通过」的唯一依据。
            # 不记这个，一个从没连上过的节点连续两轮失败也会被标成「不稳定」，
            # 读起来像「它曾经好过、后来坏了」，与事实不符。
            h["ever_ok"] = True
        else:
            h["fail_streak"] = h.get("fail_streak", 0) + 1
            h["ok_streak"] = 0
        h["last_ok"] = bool(r.get("ok"))
    return hist


def score_one(rec, h):
    """返回 (分数, 标签列表)。不可用的节点直接 0 分，不参与排序。"""
    tags = []
    if not rec.get("ok"):
        fs = (h or {}).get("fail_streak", 1)
        ever = (h or {}).get("ever_ok", False)
        if fs >= 3:
            tags.append("隔离")
        elif fs == 2 and ever:
            # 只有「曾经能用、现在连续两轮不行」才叫不稳定
            tags.append("不稳定")
        else:
            # 从没连上过的节点，两轮失败不叫不稳定，叫还没验证成功过
            tags.append("待复测")
        return 0.0, tags

    s = 30.0                                    # 真实可用性
    tags.append("可用")

    ms = rec.get("ms") or 3000
    # 150ms 内满分，1500ms 归零。这个尺度是按实测分布定的：
    # 本轮 P50 675ms、P90 3010ms，超过 1.5 秒的节点在实际使用中已经接近不可用。
    # 第一版用的是 100/800ms，结果 675ms 只能拿 3 分，绝大多数可用节点被压到 C 级，
    # best 分组只剩 4 条——那不是节点差，是尺度和现实分布不匹配。
    s += 20 * max(0.0, min(1.0, (1500 - ms) / 1350))
    if ms < 200:
        tags.append("低延迟")

    bps = rec.get("bps")
    if bps:
        # 速度用对数刻度。带宽差异是数量级的（50KB/s 到 20MB/s），
        # 线性刻度会让「2MB/s」和「200KB/s」看起来差不多，而实际体验差十倍。
        # 50KB/s 记 0 分，5MB/s 记满分。
        s += 20 * max(0.0, min(1.0, math.log10(max(bps, 1) / 50_000) / math.log10(100)))
        if bps >= 2_000_000:
            tags.append("高速")

    ok_streak = (h or {}).get("ok_streak", 1)
    s += 15 * min(1.0, ok_streak / 3)           # 连续三轮可用才拿满
    if ok_streak >= 3:
        tags.append("稳定")

    if not is_datacenter(rec):
        s += 10.0
        tags.append("非机房")
    else:
        tags.append("机房 IP")

    # 流媒体 / AI 检测未实现，这 5 分不给（见模块说明）
    return round(s, 1), tags


def grade_of(s, ok):
    if not ok:
        return "—"
    if s >= 88:
        return "S"
    if s >= 78:
        return "A"
    if s >= 65:
        return "B"
    if s >= 50:
        return "C"
    return "D"


def main():
    check = load(NODECHECK, {})
    results = check.get("nodes") or {}
    if not results:
        print("没有 data/nodecheck.json，先跑 nodecheck.py")
        return 1

    nodes = load(os.path.join(EXPORT, "nodes_full.json"), [])
    by_fp = {n["fp"]: n for n in nodes} if nodes else {}

    hist = load(HIST, {"nodes": {}, "rounds": KEEP_ROUNDS})
    hist = update_history(results, hist)

    scores, regions, per_src = {}, Counter(), {}
    for fp, rec in results.items():
        h = hist["nodes"].get(fp)
        s, tags = score_one(rec, h)
        g = grade_of(s, rec.get("ok"))
        cc = rec.get("cc") or ""
        region = REGION.get(cc, "其他") if cc else "未知"
        scores[fp] = {"s": s, "g": g, "t": tags, "cc": cc, "rg": region,
                      "ms": rec.get("ms"), "bps": rec.get("bps"),
                      "asn": rec.get("asn"), "isp": rec.get("isp")}
        if rec.get("ok"):
            regions[region] += 1
        src = (by_fp.get(fp) or {}).get("src")
        if src:
            d = per_src.setdefault(src, {"tested": 0, "ok": 0})
            d["tested"] += 1
            d["ok"] += 1 if rec.get("ok") else 0

    doc = {"updated": check.get("updated"), "checked_at": int(time.time()),
           "summary": {"tested": len(results),
                       "ok": sum(1 for r in results.values() if r.get("ok")),
                       "regions": dict(regions.most_common()),
                       # 标签分布：页面上不逐个列节点，但要知道「有多少条稳定/低延迟」，
                       # 生成优选订阅时也是按这些标签分桶
                       "tags": dict(Counter(t for v in scores.values()
                                            for t in v["t"]).most_common()),
                       "avg_score": round(sum(v["s"] for v in scores.values())
                                          / max(1, len(scores)), 1)},
           "by_source": per_src, "nodes": scores}

    force = (os.environ.get("GITHUB_ACTIONS") == "true"
             or os.environ.get("NODES_FORCE_WRITE") == "1")
    if not force:
        print("  （本地运行，不写入 node_scores.json）")
    else:
        # 原子写：两个文件都是页面与订阅的数据源，写到一半被中断会留下
        # 截断的 JSON，下一轮 collector 读到就整页空白
        netsafe.write_atomic(OUT, json.dumps(doc, ensure_ascii=False,
                                             separators=(",", ":")))
        hist["updated"] = check.get("updated")
        netsafe.write_atomic(HIST, json.dumps(hist, ensure_ascii=False,
                                              separators=(",", ":")))
        print(f"  节点评分 -> {os.path.relpath(OUT, BASE)}")

    # 打印按源的真实可用率：这是 P7 对页面最有用的一个数
    print(f"\n真实检测小结：测 {doc['summary']['tested']}，"
          f"可用 {doc['summary']['ok']}，平均分 {doc['summary']['avg_score']}")
    if regions:
        print("出口地区：" + "  ".join(f"{k} {v}" for k, v in regions.most_common(8)))
    print(f"\n  {'来源':<18}{'测':>5}{'可用':>6}{'可用率':>8}")
    for src, d in sorted(per_src.items(), key=lambda kv: -kv[1]["ok"]):
        pct = round(d["ok"] / d["tested"] * 100) if d["tested"] else 0
        print(f"  {src[:17]:<18}{d['tested']:>5}{d['ok']:>6}{str(pct) + '%':>8}")
    return 0


def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("  ✔ " if c else "  ✘ ") + m)
        ok = ok and c

    fast = {"ok": True, "ms": 80, "bps": 6_000_000, "isp": "Comcast Cable",
            "country": "United States"}
    s, t = score_one(fast, {"ok_streak": 3})
    chk(s == 95.0, f"满分场景 {s}（上限 95，媒体 5 分未实现）")
    chk("低延迟" in t and "高速" in t and "稳定" in t and "非机房" in t, "标签齐全")

    slow = {"ok": True, "ms": 900, "isp": "Hetzner", "country": "Germany"}
    s2, t2 = score_one(slow, {"ok_streak": 1})
    chk(s2 < 45, f"慢且机房 {s2}")
    chk("机房 IP" in t2 and "低延迟" not in t2, "机房标签")

    dead = {"ok": False}
    chk(score_one(dead, {"fail_streak": 3, "ever_ok": True})[1] == ["隔离"],
        "连续三轮失败 → 隔离")
    chk(score_one(dead, {"fail_streak": 2, "ever_ok": True})[1] == ["不稳定"],
        "曾能用 + 两轮失败 → 不稳定")
    chk(score_one(dead, {"fail_streak": 2, "ever_ok": False})[1] == ["待复测"],
        "从未成功 + 两轮失败 → 仍是待复测，不叫不稳定")
    chk(score_one(dead, {"fail_streak": 1, "ever_ok": True})[1] == ["待复测"],
        "一轮失败 → 待复测")

    chk(grade_of(95, True) == "S" and grade_of(0, False) == "—", "等级边界")
    chk(REGION["JP"] == "日本" and REGION.get("ZZ", "其他") == "其他", "地区映射")

    h = {"nodes": {}}
    h = update_history({"a": {"ok": True}, "b": {"ok": False}}, h)
    h = update_history({"a": {"ok": True}}, h)
    chk(h["nodes"]["a"]["ok_streak"] == 2 and h["nodes"]["b"]["ok_streak"] == 0,
        "连续成功计数，未测节点保持原状")
    chk(h["nodes"]["a"]["ever_ok"] and not h["nodes"]["b"]["ever_ok"],
        "ever_ok 只对真正成功过的节点置位")

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main() or 0)
