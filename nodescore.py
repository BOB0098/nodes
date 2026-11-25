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

import history
import netsafe

BASE = os.path.dirname(os.path.abspath(__file__))
NODECHECK = os.path.join(BASE, "data", "nodecheck.json")
OUT = os.path.join(BASE, "data", "node_scores.json")
EXPORT = os.path.join(BASE, "export")

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
    """把本轮检测写进逐节点历史。保留这个薄封装是为了自测能单独调，
    真正的累积、时段分桶、窗口裁剪都在 history.py 里。

    `ever_ok` 的坑在 P18 修掉了：旧版本用布尔字段标「曾经成功过」，字段是后加的，
    早期记录没有它，导致 152 条「明明刚成功过」的节点被当成从没通过。
    现在直接看累计成功次数 hits。
    """
    return hist, history.observe(hist, results)


def score_one(rec, h):
    """返回 (分数, 标签列表)。不可用的节点直接 0 分，不参与排序。"""
    tags = []
    if not rec.get("ok"):
        fs = int((h or {}).get("fail_run") or 1)
        # 「曾经成功过」看累计成功次数。早期版本用独立布尔字段记这个，
        # 字段是后加的、老记录里没有，152 条刚成功过的节点被误判成从未通过
        ever = int((h or {}).get("hits") or 0) > 0
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

    # 稳定性 15 分按「累计成功率 + 样本量」给，不再按连续成功次数：
    # 抽样率 12% 时，连续三轮都被抽中且都成功是极小概率事件，
    # 一个真正稳定的节点也拿不到「稳定」标签（实测 2,800 条里只有 122 条）。
    # unknown（样本不足）给 5 分，既不当满分也不当 0——测过一次就成功，
    # 不足以证明稳定，但也不该比「从没测过」还低。
    stab, stab_note = history.stability(h)
    s += {"stable": 15.0, "fair": 9.0, "poor": 0.0}.get(stab, 5.0)
    if stab == "stable":
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

    hist = history.load()
    if not hist.get("nodes"):
        # 首次切到新结构时把旧记录带过来。只迁能确定的部分，其余标 migrated，
        # 样本攒够之前不参与「稳定」判定
        hist, n_old = history.migrate_legacy(hist)
        if n_old:
            print(f"  已从 node_score_history.json 迁移 {n_old} 条旧记录")
    today = history.day_of()
    hist, stat = update_history(results, hist)
    print(f"  历史：本轮见 {stat['seen']}，新增 {stat['new']}，"
          f"回归 {stat['revived']}，跟踪中 {stat['total']}")

    scores, regions, per_src, stabs = {}, Counter(), {}, Counter()
    for fp, rec in results.items():
        h = hist["nodes"].get(fp[:16])
        s, tags = score_one(rec, h)
        g = grade_of(s, rec.get("ok"))
        cc = rec.get("cc") or ""
        region = REGION.get(cc, "其他") if cc else "未知"
        stab, stab_note = history.stability(h)
        stabs[stab] += 1
        scores[fp] = {"s": s, "g": g, "t": tags, "cc": cc, "rg": region,
                      "ms": rec.get("ms"), "bps": rec.get("bps"),
                      "asn": rec.get("asn"), "isp": rec.get("isp"),
                      # 稳定性判定要看样本量，页面上不能只给一个「稳定」了事
                      "stab": stab, "stab_note": stab_note,
                      "tries": int((h or {}).get("tries") or 0),
                      "hits": int((h or {}).get("hits") or 0)}
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
                       # 稳定性三态分布。unknown 占比高说明抽样还没覆盖够，
                       # 这不是缺陷，是「还在攒证据」——页面上要如实显示
                       "stability": dict(stabs.most_common()),
                       "avg_score": round(sum(v["s"] for v in scores.values())
                                          / max(1, len(scores)), 1)},
           "by_source": per_src, "nodes": scores}

    force = (os.environ.get("GITHUB_ACTIONS") == "true"
             or os.environ.get("NODES_FORCE_WRITE") == "1")
    if not force:
        print("  （本地运行，不写入 node_scores.json）")
    else:
        # 原子写：三个文件都是页面与订阅的数据源，写到一半被中断会留下
        # 截断的 JSON，下一轮 collector 读到就整页空白
        netsafe.write_atomic(OUT, json.dumps(doc, ensure_ascii=False,
                                             separators=(",", ":")))
        pruned = history.prune(hist, today)
        history.save(hist)
        # 旧历史文件已经并入 node_history.json，留着会让人以为还有两套历史。
        # 原始值（ok_streak / checked）在 Git 历史里可查，不丢信息。
        if os.path.exists(history.LEGACY):
            try:
                os.remove(history.LEGACY)
                print("  旧 node_score_history.json 已并入并移除")
            except OSError:
                pass
        if pruned:
            print(f"  历史裁剪：{pruned} 条超出 {history.WINDOW_DAYS} 天窗口")

        # 日趋势：一天一行。拿它画 7/30 天曲线比每次重算几万条历史快两个数量级
        tdoc = history.load_trend()
        ms_all = [r["ms"] for r in results.values()
                  if r.get("ok") and isinstance(r.get("ms"), (int, float))]
        by_proto = {}
        for r in results.values():
            p = r.get("proto") or "?"
            cell = by_proto.setdefault(p, [0, 0])
            cell[0] += 1
            cell[1] += 1 if r.get("ok") else 0
        history.trend_add(tdoc, len(results), doc["summary"]["ok"], ms_all,
                          new=stat["new"], gone=stat["gone"],
                          by_proto=by_proto, today=today)
        history.save_trend(tdoc)
        print(f"  节点评分 -> {os.path.relpath(OUT, BASE)}"
              f"  历史 {history.size_kb()} KB / 趋势 {len(tdoc['days'])} 天")

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
    h_fast = {"tries": 3, "hits": 3, "ok_run": 3, "fail_run": 0, "slots": "333000"}
    s, t = score_one(fast, h_fast)
    chk(s == 95.0, f"满分场景 {s}（上限 95，媒体 5 分未实现）")
    chk("低延迟" in t and "高速" in t and "稳定" in t and "非机房" in t, "标签齐全")

    slow = {"ok": True, "ms": 900, "isp": "Hetzner", "country": "Germany"}
    s2, t2 = score_one(slow, {"tries": 1, "hits": 1, "ok_run": 1, "fail_run": 0})
    chk(s2 < 45, f"慢且机房 {s2}")
    chk("机房 IP" in t2 and "低延迟" not in t2, "机房标签")
    chk("稳定" not in t2, "只测过一次不给稳定标签")

    # 稳定标签必须来自样本量，不是来自「连续」：三条同分但样本不同
    one = score_one(fast, {"tries": 1, "hits": 1, "ok_run": 1, "fail_run": 0})[0]
    five = score_one(fast, {"tries": 5, "hits": 5, "ok_run": 5, "fail_run": 0})[0]
    chk(five - one == 10.0, f"样本从 1 涨到 5，稳定分从 5 到 15（差 {five - one}）")
    flaky = score_one(fast, {"tries": 5, "hits": 2, "ok_run": 0, "fail_run": 1})[0]
    chk(flaky < five, f"成功率 40% 的节点评分低于全成功（{flaky} < {five}）")

    dead = {"ok": False}
    chk(score_one(dead, {"fail_run": 3, "hits": 5})[1] == ["隔离"],
        "连续三轮失败 → 隔离")
    chk(score_one(dead, {"fail_run": 2, "hits": 5})[1] == ["不稳定"],
        "曾能用 + 两轮失败 → 不稳定")
    chk(score_one(dead, {"fail_run": 2, "hits": 0})[1] == ["待复测"],
        "从未成功 + 两轮失败 → 仍是待复测，不叫不稳定")
    chk(score_one(dead, {"fail_run": 1, "hits": 5})[1] == ["待复测"],
        "一轮失败 → 待复测")
    # 旧版本用 ever_ok 布尔字段，字段后加导致老记录缺失；
    # 现在看 hits，一条「刚成功过但 ever_ok 没写」的记录也能正确判成不稳定
    chk(score_one(dead, {"fail_run": 2, "hits": 1})[1] == ["不稳定"],
        "hits>0 即可判曾成功，不依赖 ever_ok 字段")

    chk(grade_of(95, True) == "S" and grade_of(0, False) == "—", "等级边界")
    chk(REGION["JP"] == "日本" and REGION.get("ZZ", "其他") == "其他", "地区映射")

    h = {"nodes": {}}
    h, _ = update_history({"a" * 32: {"ok": True}, "b" * 32: {"ok": False}}, h)
    h, _ = update_history({"a" * 32: {"ok": True}}, h)
    chk(h["nodes"]["a" * 16]["ok_run"] == 2 and h["nodes"]["b" * 16]["ok_run"] == 0,
        "连续成功计数，未测节点保持原状")
    chk(h["nodes"]["a" * 16]["hits"] == 2 and h["nodes"]["b" * 16]["hits"] == 0,
        "累计成功只对真正成功过的节点累加")

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main() or 0)
