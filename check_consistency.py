#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前一致性检查：页面、数据、订阅是不是同一轮、同一个数。

这个脚本存在的理由是一次真实事故：同一轮里 `nodes.json` 写 9,845、
`subscriptions.json` 写 9,846、内嵌到 `index.html` 的又是 9,846。
三个数字差 1，没人发现，因为没有任何一处会把它们放在一起比。

用法：
    python3 check_consistency.py          # 校验，不一致退出码 1
    python3 check_consistency.py --quiet  # 只输出失败项

检查项：
1. nodes.json 的 stats.dedup 与 nodes 数组长度一致
2. nodes.json 里 stats.subs.*.count 与 subscriptions.json 同名分组一致
3. index.html 内嵌 DATA 的 stats.dedup 与 nodes.json 一致
4. 各文件的 updated 时间戳差值 ≤ 阈值（默认 90 分钟，超过说明不是同一轮）
5. schema 版本存在且为已知值
6. picks.json 的四类榜单条数与订阅同名分组一致（P18）

第 4 项在现阶段是「警告」不是「失败」：订阅在检测之后生成，天然晚十几分钟。
P20 引入 manifest 后，这一项会变成硬性要求。
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
SCHEMA = 2
MAX_SKEW_MIN = 90


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}


def mins(t):
    """'11-25 07:50' → 当日分钟数。跨日不管，差一天会自然超过阈值。"""
    m = re.match(r"(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", str(t or ""))
    if not m:
        return None
    return int(m.group(3)) * 60 + int(m.group(4))


def embedded(index_html):
    try:
        with open(index_html, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return None
    m = re.search(r"const DATA = (\{.*?\});\s*\n", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def main():
    quiet = "--quiet" in sys.argv
    fails, warns = [], []

    def ok(msg):
        if not quiet:
            print("  ✔ " + msg)

    def bad(msg):
        fails.append(msg)
        print("  ✘ " + msg)

    def warn(msg):
        warns.append(msg)
        print("  ! " + msg)

    nodes = load(os.path.join(DATA, "nodes.json"))
    subs = load(os.path.join(DATA, "subscriptions.json"))
    page = embedded(os.path.join(BASE, "index.html"))

    if "__error__" in nodes:
        bad("data/nodes.json 读不出来：" + nodes["__error__"])
        return 1

    print("一致性检查")

    # 1. 节点数组长度
    n = len(nodes.get("nodes") or [])
    dedup = (nodes.get("stats") or {}).get("dedup")
    if dedup == n:
        ok(f"nodes.json 条数 {n} = stats.dedup {dedup}")
    else:
        bad(f"nodes.json 条数 {n} ≠ stats.dedup {dedup}")

    # 2. 订阅分组
    if "__error__" in subs:
        warn("data/subscriptions.json 读不出来，跳过订阅比对")
    else:
        sg = {g.get("key"): g.get("count") for g in (subs.get("groups") or [])}
        ns = (nodes.get("stats") or {}).get("subs") or {}
        if not ns:
            warn("nodes.json 没有 stats.subs，跳过")
        for key, v in ns.items():
            cnt = v.get("count") if isinstance(v, dict) else v
            got = sg.get(key)
            if got is None:
                warn(f"订阅分组 {key} 在 subscriptions.json 里不存在")
            elif got != cnt:
                bad(f"订阅分组 {key}：页面记 {cnt}，订阅文件 {got}")
        if ns and all(sg.get(k) == (v.get("count") if isinstance(v, dict) else v)
                      for k, v in ns.items() if k in sg):
            ok(f"订阅 {len(ns)} 个分组条数与页面一致")

    # 3. 内嵌数据
    if page is None:
        bad("index.html 里找不到内嵌 DATA")
    else:
        pd = (page.get("stats") or {}).get("dedup")
        if pd == n:
            ok(f"index.html 内嵌条数 {pd} = nodes.json {n}")
        else:
            bad(f"index.html 内嵌条数 {pd} ≠ nodes.json {n}")

    # 4. 时间戳
    ts = {
        "nodes": mins(nodes.get("updated")),
        "subs": mins((subs or {}).get("updated")),
    }
    if page is not None:
        ts["page"] = mins(page.get("updated"))
    known = {k: v for k, v in ts.items() if v is not None}
    if len(known) >= 2:
        skew = max(known.values()) - min(known.values())
        detail = " / ".join(f"{k} {v // 60:02d}:{v % 60:02d}" for k, v in known.items())
        if skew <= MAX_SKEW_MIN:
            ok(f"时间戳同一轮（差 {skew} 分钟）：{detail}")
        else:
            warn(f"时间戳相差 {skew} 分钟，超过 {MAX_SKEW_MIN}：{detail}")
    else:
        warn("时间戳字段缺失，无法比对")

    # 5. schema
    sv = nodes.get("schema")
    if sv is None:
        warn("nodes.json 没有 schema 字段（旧格式，下次采集会补上）")
    elif sv != SCHEMA:
        bad(f"nodes.json schema={sv}，本脚本认 {SCHEMA}")

    # 6. 榜单与订阅。订阅的 best/stable/lowlat/fast 四组直接由 picks.json 决定，
    # 所以两边的条数必须相等。不相等只有两种可能：picks 里混进了节点库中
    # 已下架的节点，或者订阅生成时漏掉了某些节点——两种都该在发布前拦住
    picks = load(os.path.join(DATA, "picks.json"))
    if "__error__" in picks:
        warn("data/picks.json 读不出来，跳过榜单比对")
    elif "__error__" in subs:
        warn("subscriptions.json 不可用，跳过榜单比对")
    else:
        pl = {k: len(v) for k, v in (picks.get("lists") or {}).items()}
        sg = {g.get("key"): g.get("count") for g in (subs.get("groups") or [])}
        diff = [f"{k} 榜 {pl[k]} / 订阅 {sg.get(k)}"
                for k in sorted(pl) if k in sg and sg.get(k) != pl[k]]
        if diff:
            bad("榜单与订阅条数不一致：" + "；".join(diff))
        elif pl:
            dropped = picks.get("dropped") or 0
            ok(f"榜单 {len(pl)} 组条数与订阅一致"
               + (f"（已剔除下架 {dropped} 条）" if dropped else ""))
        # 榜单里出现订阅不支持的分组，说明两边定义已经分叉
        extra = [k for k in pl if k not in sg]
        if extra:
            warn("榜单有分组未出现在订阅清单：" + "、".join(extra))

    # 7. 来源治理（P19）：治理记录必须覆盖当轮所有启用源，
    # 否则「异常源」可能只是治理步骤没跑到，看着像正常
    gov = load(os.path.join(DATA, "source_governance.json"))
    if "__error__" in gov:
        warn("data/source_governance.json 读不出来，跳过来源治理比对")
    else:
        covered = gov.get("sources") or {}
        declared = int((gov.get("whitelist") or {}).get("enabled") or 0)
        if declared and len(covered) != declared:
            bad(f"来源治理覆盖 {len(covered)} 个源，但白名单启用 {declared} 个")
        elif covered:
            obs = sum(1 for r in covered.values() if r.get("under_observation"))
            dup = len(gov.get("duplicates") or [])
            ok(f"来源治理覆盖 {len(covered)} 个源（同源地址 {dup} 组，观察期 {obs} 个）")

    print()
    if fails:
        print(f"结论：{len(fails)} 项不一致，{len(warns)} 项警告")
        return 1
    print(f"结论：全部一致" + (f"，{len(warns)} 项警告" if warns else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
