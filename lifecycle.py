"""节点生命周期：同一指纹「第一次见到」和「最后一次见到」分别是哪天。

两个用途，都直接关系到「这条订阅还活着吗」：

1. 页面给每条源标「近 7 天新增 N」。总量大不代表还在更新——一个源可能只是
   旧节点被到处转发，连续一个月不出新节点，基本等于停更。
2. 导出时能区分「老节点」和「今天才出现的节点」。前者活过一周说明有人在维护，
   后者出故障的概率高得多。

为什么不写进 nodes.json：那是脱敏的页面数据，节点会随源上下架进出。指纹历史
必须独立留存，否则「消失两周又回来」会被误记成新节点。文件体积方面，一条是
32 字符指纹加两个整数，一万节点约 350 KB，可接受。

时间粒度取「天」而不是「小时」：每 3 小时采一轮，存小时会让文件大三倍，
而「已存活 12 天」比「已存活 12 天 3 小时」有用得多。
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import netsafe

BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE, "data", "node_lifecycle.json")

# 45 天没再出现就从历史里删掉。留太久只会让文件无限增长，
# 而且隔了 45 天又冒出来的节点，按「新节点」处理才是对的。
KEEP_DAYS = 45

# 指纹截断长度。完整指纹是 32 位 hex，这里只留前 16 位（64 bit）：
# 一万个节点撞车概率约 3e-12，实际为 0，但每条记录省下 16 字节——
# 这个文件每轮都要重写一次、提交一次，没必要为不可能发生的碰撞付这份体积。
KEY_LEN = 16

CST = timezone(timedelta(hours=8))


def _key(fp):
    return (fp or "")[:KEY_LEN]



def day_of(ts=None):
    """自 1970-01-01 起的天数（按东八区算，跨零点采集不会错记成前一天）。"""
    if ts is None:
        dt = datetime.now(CST)
    elif isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts, CST)
    else:
        dt = ts.astimezone(CST)
    return (dt.date() - datetime(1970, 1, 1).date()).days


def load():
    try:
        doc = json.load(open(PATH, encoding="utf-8"))
        if isinstance(doc.get("nodes"), dict):
            return doc
    except Exception:
        pass
    return {"updated": None, "kept_days": KEEP_DAYS, "nodes": {}}


def update(nodes, doc=None, today=None):
    """把本轮入库的节点写进历史。

    nodes  本轮去重后最终入库的节点，需要带 fp 字段
    today  天数；默认今天

    返回 (doc, stat)：
      stat.new   首次出现的节点数
      stat.back  曾经消失、这次又回来的节点数
      stat.seen  本轮见过的总数
      stat.total 历史里在跟踪的指纹总数
      stat.gone  本轮没出现、但还没到清理期限的节点数
    """
    doc = doc if doc is not None else load()
    book = doc.setdefault("nodes", {})
    today = day_of() if today is None else today

    # 满 45 天未出现即过期；先裁剪再刷新本轮节点，避免久别重现的指纹
    # 先更新 last 而绕过清理，从而被误认成旧生命周期。
    cutoff = today - KEEP_DAYS
    for fp in [fp for fp, r in book.items() if r[1] <= cutoff]:
        book.pop(fp, None)

    new = back = seen = 0
    for n in nodes:
        fp = _key(n.get("fp"))
        if not fp:
            continue
        seen += 1
        rec = book.get(fp)
        if rec is None:
            book[fp] = [today, today]
            new += 1
        else:
            # [首次, 最近]。last == today-1 是「昨天也见过」，属于正常连续性；
            # 只有 last 早于昨天（中间至少漏掉一整天）才算断档回归。
            # 用 last < today 判断会把每一条跨天连续出现的节点都算成回归。
            if rec[1] < today - 1:
                back += 1
            rec[1] = today
            rec[0] = min(rec[0], today)

    # 已到期的记录已在本轮刷新前移除；此处只计仍在 45 天窗口中的缺席节点。
    gone = sum(1 for r in book.values() if r[1] < today)

    doc["kept_days"] = KEEP_DAYS
    return doc, {"new": new, "back": back, "seen": seen, "gone": gone,
                 "total": len(book)}


def annotate(nodes, doc, today=None):
    """给节点补 first_day / last_day / age_days（已存活天数，含首次当天）。"""
    book = (doc or {}).get("nodes") or {}
    today = day_of() if today is None else today
    for n in nodes:
        rec = book.get(_key(n.get("fp")))
        if not rec:
            n["age_days"] = None
            continue
        n["first_day"], n["last_day"] = rec[0], rec[1]
        # +1 是因为首次出现的当天就算第 1 天，显示「已存活 0 天」很怪
        n["age_days"] = max(today, rec[1]) - rec[0] + 1
    return nodes


def freshness(nodes, doc=None, today=None):
    """按来源汇总「新鲜度」：近 7 天新增多少条、中位存活天数。

    页面只放得下两个数，选这两个是因为它们回答不同问题：
    new7 说明「还在产新节点吗」，median_age 说明「拿到的节点能活多久」。

    返回值额外带一个 "span"（全局跟踪了几天）：冷启动那天所有节点都是
    「首次见到」，直接报「近 7 天新增 10519」是假信息——它说明的是我们
    刚开始记录，不是源在产新节点。span < 7 时页面改显示「已跟踪 N 天」。
    """
    today = day_of() if today is None else today
    out = {}
    ages = {}
    for n in nodes:
        source_ids = n.get("src_ids")
        source_names = n.get("src_names")
        owners = []
        if isinstance(source_ids, list):
            owners.extend(source_ids)
        if isinstance(source_names, list):
            owners.extend(source_names)
        if not owners and n.get("src"):
            owners.append(n["src"])
        owners = list(dict.fromkeys(owner for owner in owners if owner))
        if not owners:
            continue
        rec = ((doc or {}).get("nodes") or {}).get(_key(n.get("fp")))
        if not rec:
            continue
        for owner in owners:
            if today - rec[0] < 7:
                out.setdefault(owner, {"new7": 0})["new7"] = (
                    out.get(owner, {}).get("new7", 0) + 1)
            ages.setdefault(owner, []).append(max(today, rec[1]) - rec[0] + 1)

    for src, arr in ages.items():
        arr.sort()
        rec = out.setdefault(src, {})
        rec["new7"] = rec.get("new7", 0)
        rec["median_age"] = arr[(len(arr) - 1) // 2]   # 偶数取偏小的那个，不夸大
        rec["max_age"] = arr[-1]

    # 全局跟踪跨度：从最早的那条记录算起，至少 1 天
    book = (doc or {}).get("nodes") or {}
    span = (today - min(r[0] for r in book.values()) + 1) if book else 0
    out["_span"] = span
    return out


def save(doc, force=None):
    """CI 里落盘，本地默认只打印。

    和 health / source_history 同一口径：本地出口看到的节点集合与机房不同，
    写进去会把「本地没拉到」记成「节点消失了」，直接污染存活天数。
    """
    force = (os.environ.get("GITHUB_ACTIONS") == "true" or
             os.environ.get("NODES_FORCE_WRITE") == "1") if force is None else force
    if not force:
        print("  （本地运行，不写入 node_lifecycle.json）")
        return False
    doc["updated"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    netsafe.write_atomic(PATH, json.dumps(doc, ensure_ascii=False,
                                          separators=(",", ":")))
    return True


def stats_line(stat):
    return (f"节点生命周期：见过 {stat['seen']} 条，新增 {stat['new']}，"
            f"回归 {stat['back']}，跟踪中 {stat['total']}")


if __name__ == "__main__":
    import sys
    d = load()
    book = d.get("nodes") or {}
    if not book:
        print("还没有 data/node_lifecycle.json（CI 跑过一轮后生成）")
        sys.exit(0)
    today = day_of()
    ages = sorted(v[1] - v[0] + 1 for v in book.values())
    fresh = sum(1 for v in book.values() if today - v[0] < 7)
    print(f"跟踪 {len(book)} 个指纹，更新于 {d.get('updated')}")
    print(f"  近 7 天新增 {fresh}，已存活中位数 {ages[len(ages)//2]} 天，最长 {ages[-1]} 天")
    print(f"  本轮已消失 {sum(1 for v in book.values() if v[1] < today)}"
          f"（{KEEP_DAYS} 天后从历史清除）")
