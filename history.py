"""P18：节点历史、日级趋势与保留策略。

三件事，都为了回答「这条节点到底靠不靠得住」：

1. **逐节点历史**（`data/node_history.json`）：首次/最近出现、最近成功日期、
   累计测了几次通了几次、当前连续成功/失败、六时段成功率、最近几次延迟与速度。
2. **日级趋势**（`data/trend.json`）：每天一行聚合。7 天 / 30 天趋势图用它，
   不靠逐节点历史重算——那需要把几万条记录读一遍，页面等不起。
3. **保留策略**：两个文件都有明确窗口，超出即裁剪。

## 为什么不用「连续成功轮次」判稳定

上一版用 `ok_streak >= 3` 标「稳定」。检测是抽样的：每轮 1,200 条 / 9,846 条，
单节点每轮被抽中的概率约 12%。连续三轮都被抽中**并且**都成功，概率约 1.7e-3 —
一个真正稳定的节点也几乎拿不到这个标签。实测：2,800 条有记录的节点里只有 122 条
拿到「稳定」，而其中 929 条只被测过一次、根本没有足够证据。

现在改用「累计成功率 + 最小样本量」：`tries >= 3` 且 `hits/tries >= 0.8`。
它抗抽样偏差，也不把一次幸运当成结论。`ok_run` 仍然保留，但只用来做
「最近是否在崩」（连续失败）判断，不再直接等于「稳定」。

## 字段为什么用完整单词

文件是公开仓库里的数据，别人 fork 之后要能看懂。短名（`f`/`l`/`t`/`h`）
每条省 60 字节，3,000 条约 180 KB，代价是每个读它的人都要先查一张映射表。
不值得。

## 体积控制

单条记录约 190 字节。窗口 30 天 + 只记录被抽到过的节点，稳定在 3,000–4,000 条
（约 700 KB），不随仓库年龄增长。超出窗口的节点不是静默消失：它们的
「存活天数」在删除前已经计入 `trend.json` 当天的 `gone` 计数。
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import netsafe

BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE, "data", "node_history.json")
TREND = os.path.join(BASE, "data", "trend.json")
LEGACY = os.path.join(BASE, "data", "node_score_history.json")

SCHEMA = 2
WINDOW_DAYS = 30          # 节点历史保留窗口
TREND_DAYS = 365          # 日趋势保留窗口（一天一行，一年 365 行很小）
SLOTS = 6                 # 一天分 6 段，每段 4 小时
MS_KEEP = 5               # 每节点保留最近 5 次成功延迟
BPS_KEEP = 3              # 每节点保留最近 3 次测速

CST = timezone(timedelta(hours=8))

# 指纹截断到 16 位（64 bit）。与 lifecycle.py 保持同一口径：
# 两处若用不同长度，同一个节点会在两个文件里对不上。
KEY_LEN = 16


def _key(fp):
    return (fp or "")[:KEY_LEN]


def day_of(ts=None):
    """自 1970-01-01 起的天数，按东八区算（跨零点采集不会记成前一天）。"""
    if ts is None:
        dt = datetime.now(CST)
    elif isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts, CST)
    else:
        dt = ts.astimezone(CST)
    return (dt.date() - datetime(1970, 1, 1).date()).days


def slot_of(ts=None):
    """当前是第几个四小时段：0=00–04 … 5=20–24。"""
    if ts is None:
        dt = datetime.now(CST)
    elif isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts, CST)
    else:
        dt = ts.astimezone(CST)
    return min(SLOTS - 1, dt.hour // (24 // SLOTS))


def _blank(today):
    return {
        "first": today, "last": today, "last_ok": None,
        "tries": 0, "hits": 0,
        "ok_run": 0, "fail_run": 0,
        # 六段各一个字符：0 未测 / 1 全败 / 2 部分成功 / 3 全成
        "slots": "0" * SLOTS,
        "ms": [], "bps": [],
    }


def load(path=None):
    try:
        doc = json.load(open(path or PATH, encoding="utf-8"))
        if isinstance(doc.get("nodes"), dict):
            return doc
    except Exception:
        pass
    return {"schema": SCHEMA, "updated": None, "window_days": WINDOW_DAYS,
            "nodes": {}}


def load_trend():
    try:
        doc = json.load(open(TREND, encoding="utf-8"))
        if isinstance(doc.get("days"), list):
            return doc
    except Exception:
        pass
    return {"schema": 1, "updated": None, "days": []}


# ── 逐节点历史 ────────────────────────────────────────────────────────────────

def _slot_merge(old, ok):
    """把一个时段桶与本次结果合并。

    四小时一段、三小时一轮，一段内通常只测到一两次。两次都成就 '3'，
    一成一败是 '2'（部分），两次都败仍是 '1'。不区分次数——样本只有一两次时，
    「这段里 1/2 次成功」和「这段失败过」在页面上没区别。
    """
    want = "3" if ok else "1"
    if old == "0":
        return want
    if old == want:
        return old
    return "2"


def observe(doc, results, today=None, slot=None):
    """把本轮检测结果写进逐节点历史。

    results  {fp: {"ok":bool, "ms":int|None, "bps":int|None}}
    返回本轮统计 {"new", "gone", "seen", "total", "revived"}。

    new   本轮首次出现的指纹
    gone  本轮有历史、但记录已超出窗口（即将被裁剪）
    revived 上次出现早于昨天、这次又回来了（中间断过档）
    """
    today = day_of() if today is None else today
    slot = slot_of() if slot is None else slot
    book = doc.setdefault("nodes", {})

    new = revived = seen = 0
    for fp, r in results.items():
        k = _key(fp)
        if not k:
            continue
        seen += 1
        rec = book.get(k)
        if rec is None:
            rec = book[k] = _blank(today)
            new += 1
        else:
            # last < today-1 才算断档：昨天也在、今天还在是正常连续，
            # 用 last < today 会把每一条连续节点都记成「回归」
            if rec.get("last", today) < today - 1:
                revived += 1
            rec["first"] = min(rec.get("first", today), today)
        rec["last"] = today
        rec["tries"] = int(rec.get("tries") or 0) + 1

        ok = bool(r.get("ok"))
        if ok:
            rec["hits"] = int(rec.get("hits") or 0) + 1
            rec["ok_run"] = int(rec.get("ok_run") or 0) + 1
            rec["fail_run"] = 0
            rec["last_ok"] = today
            ms = r.get("ms")
            if isinstance(ms, (int, float)) and ms > 0:
                arr = list(rec.get("ms") or [])
                arr.append(int(ms))
                rec["ms"] = arr[-MS_KEEP:]
            bps = r.get("bps")
            if isinstance(bps, (int, float)) and bps > 0:
                arr = list(rec.get("bps") or [])
                arr.append(int(bps))
                rec["bps"] = arr[-BPS_KEEP:]
        else:
            rec["fail_run"] = int(rec.get("fail_run") or 0) + 1
            rec["ok_run"] = 0

        slots = rec.get("slots") or ("0" * SLOTS)
        if len(slots) != SLOTS:
            slots = (slots + "0" * SLOTS)[:SLOTS]
        slots = slots[:slot] + _slot_merge(slots[slot], ok) + slots[slot + 1:]
        rec["slots"] = slots

    gone = sum(1 for r in book.values() if r.get("last", today) < today)
    return {"new": new, "gone": gone, "seen": seen, "total": len(book),
            "revived": revived}


def prune(doc, today=None):
    """删除超出窗口的节点，返回删除条数。"""
    today = day_of() if today is None else today
    cutoff = today - WINDOW_DAYS
    book = doc.get("nodes") or {}
    dead = [k for k, r in book.items() if r.get("last", today) < cutoff]
    for k in dead:
        book.pop(k, None)
    doc["window_days"] = WINDOW_DAYS
    doc["schema"] = SCHEMA
    return len(dead)


# ── 派生指标 ──────────────────────────────────────────────────────────────────

def rate(rec):
    t = int((rec or {}).get("tries") or 0)
    if not t:
        return None
    return int(rec.get("hits") or 0) / t


def _median(arr):
    a = sorted(x for x in (arr or []) if isinstance(x, (int, float)))
    if not a:
        return None
    return a[(len(a) - 1) // 2]        # 偶数取偏小的，不夸大


def ms_med(rec):
    return _median((rec or {}).get("ms"))


def bps_med(rec):
    return _median((rec or {}).get("bps"))


def slot_ok(rec):
    """六段里成功过的段数 / 测过的段数。回答「它是一直好还是分时段抽风」。"""
    s = (rec or {}).get("slots") or ""
    tried = sum(1 for c in s if c in "123")
    good = sum(1 for c in s if c in "23")
    if not tried:
        return None
    return good, tried


def stability(rec):
    """→ (等级, 说明)。等级：stable / fair / poor / unknown。

    stable 要求三件事同时成立：测过 ≥3 次、成功率 ≥80%、当前没有连续失败。
    只满足「成功率 100% 但只测过 1 次」的，是 unknown——样本不足不能算稳定。

    迁移来的旧记录（`migrated`）前 6 次不给 stable：它的 hits 是从「连续成功次数」
    反推的下界，不是真实成功计数。等新数据累积到 6 次以上，估算值占比降到一半以下，
    才允许参与正常判定。
    """
    t = int((rec or {}).get("tries") or 0)
    h = int((rec or {}).get("hits") or 0)
    run = int((rec or {}).get("fail_run") or 0)
    if t < 3:
        return "unknown", f"仅测 {t} 次"
    if (rec or {}).get("migrated") and t < 6:
        return "unknown", f"旧记录无成功率（{h}/{t} 为下界）"
    pct = h / t
    if run >= 2:
        return "poor", f"连败 {run} 次（历史 {h}/{t}）"
    if pct >= 0.8:
        return "stable", f"{h}/{t} 次成功"
    if pct >= 0.5:
        return "fair", f"{h}/{t} 次成功"
    return "poor", f"{h}/{t} 次成功"


# ── 日级趋势 ──────────────────────────────────────────────────────────────────

def trend_add(tdoc, tested, ok, ms_list=None, new=0, gone=0, by_proto=None,
              today=None):
    """把本轮结果并入当天的行。

    一天可能跑 8 轮，每轮抽不同的 1,200 条。日级取**累计**而不是最后一轮：
    最后一轮只是 1,200 条的截面，拿它当「今天的可用率」会把 8 倍的样本扔掉。
    累计口径下，日可用率 = 当天所有轮次样本的加权平均，对抽样是无偏的。
    """
    today = day_of() if today is None else today
    d = (datetime(1970, 1, 1) + timedelta(days=today)).strftime("%Y-%m-%d")
    days = tdoc.setdefault("days", [])
    row = next((r for r in days if r.get("d") == d), None)
    if row is None:
        row = {"d": d, "rounds": 0, "tested": 0, "ok": 0, "new": 0, "gone": 0,
               "p50": None, "by_proto": {}}
        days.append(row)
    row["rounds"] = int(row.get("rounds") or 0) + 1
    row["tested"] = int(row.get("tested") or 0) + int(tested or 0)
    row["ok"] = int(row.get("ok") or 0) + int(ok or 0)
    row["new"] = int(row.get("new") or 0) + int(new or 0)
    # gone 是「当天已消失」的截面数，不是累加量：8 轮累加会把同一个节点算 8 次
    row["gone"] = max(int(row.get("gone") or 0), int(gone or 0))
    if ms_list:
        row["p50"] = _median(ms_list)
    for p, (t, o) in (by_proto or {}).items():
        cur = row.setdefault("by_proto", {}).setdefault(p, {"tested": 0, "ok": 0})
        cur["tested"] += int(t)
        cur["ok"] += int(o)
    days.sort(key=lambda r: r.get("d") or "")
    del days[:-TREND_DAYS]
    tdoc["updated"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    return tdoc


def trend_window(tdoc, days=7):
    """取最近 N 天（按日期字符串比较，不做时间运算）。"""
    arr = sorted(tdoc.get("days") or [], key=lambda r: r.get("d") or "")
    return arr[-days:] if days else arr


# ── 旧数据迁移 ────────────────────────────────────────────────────────────────

def migrate_legacy(doc=None):
    """把 node_score_history.json 的旧记录并进新结构。

    旧结构只有 {ok_streak, fail_streak, last_ok(bool), checked, ever_ok}，
    **没有 hits**——光凭连续次数无法还原历史成功率。所以迁移只做能确定的事：

    - tries   ← checked（这个是真的）
    - ok_run  ← ok_streak
    - fail_run← fail_streak
    - hits    ← max(ok_streak, 1 if last_ok else 0)，是下界，不是真值
    - 打 `migrated: true`，在样本攒够之前一律按 unknown 处理

    顺带修一个真实的迁移缺陷：旧版本 `ever_ok` 是后加的字段，早期记录没有它。
    实测 2,800 条里有 152 条 `ok_streak > 0` 却 `ever_ok == False`，
    被当成「从未成功过」而标成「待复测」——它们明明是刚成功过的。
    迁移时按 `ok_streak > 0 or last_ok` 补齐，不再依赖那个缺失的字段。
    """
    doc = doc if doc is not None else load()
    try:
        old = json.load(open(LEGACY, encoding="utf-8"))
    except Exception:
        return doc, 0
    src = old.get("nodes") or {}
    book = doc.setdefault("nodes", {})
    today = day_of()
    n = 0
    for fp, rec in src.items():
        k = _key(fp)
        if not k or k in book:
            continue
        checked = int(rec.get("checked") or 0)
        ok_run = int(rec.get("ok_streak") or 0)
        fail_run = int(rec.get("fail_streak") or 0)
        last_ok = bool(rec.get("last_ok")) or ok_run > 0
        # 旧记录没有日期。用「今天」占位并标 migrated，避免被当成 30 天内的新鲜数据。
        # 这样它会在窗口期满后被正常裁掉，而不是永远留在文件里。
        row = _blank(today)
        row.update({
            "tries": checked, "hits": max(ok_run, 1 if last_ok else 0),
            "ok_run": ok_run, "fail_run": fail_run,
            "last_ok": today if last_ok else None,
            "migrated": True,
        })
        book[k] = row
        n += 1
    return doc, n


# ── 落盘 ──────────────────────────────────────────────────────────────────────

def _compact(rec):
    """落盘前去掉默认值字段。

    一个「测了 6 次、一次没通」的节点，完整结构是 150 字节，
    其中 `"last_ok":null,"hits":0,"ok_run":0,"ms":[],"bps":[]` 共 55 字节毫无信息。
    这类死号占六成多，全部省略后文件从 1.5 MB 降到 1.1 MB。
    读的时候所有字段都走 `.get(...)` 带默认值，缺字段不会炸（selftest 有断言）。
    """
    out = {"first": rec.get("first"), "last": rec.get("last"),
           "tries": int(rec.get("tries") or 0), "hits": int(rec.get("hits") or 0)}
    if rec.get("last_ok"):
        out["last_ok"] = rec["last_ok"]
    if rec.get("ok_run"):
        out["ok_run"] = int(rec["ok_run"])
    if rec.get("fail_run"):
        out["fail_run"] = int(rec["fail_run"])
    slots = rec.get("slots") or ""
    if slots.strip("0"):
        out["slots"] = slots
    if rec.get("ms"):
        out["ms"] = list(rec["ms"])
    if rec.get("bps"):
        out["bps"] = list(rec["bps"])
    if rec.get("migrated"):
        out["migrated"] = True
    return out

def _force():
    return (os.environ.get("GITHUB_ACTIONS") == "true"
            or os.environ.get("NODES_FORCE_WRITE") == "1")


def save(doc, force=None, quiet=False):
    force = _force() if force is None else force
    if not force:
        if not quiet:
            print("  （本地运行，不写入 node_history.json）")
        return False
    doc["schema"] = SCHEMA
    doc["updated"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    slim = {k: v for k, v in doc.items() if k != "nodes"}
    slim["nodes"] = {k: _compact(v) for k, v in (doc.get("nodes") or {}).items()}
    netsafe.write_atomic(PATH, json.dumps(slim, ensure_ascii=False,
                                          separators=(",", ":")))
    return True


def save_trend(tdoc, force=None, quiet=False):
    force = _force() if force is None else force
    if not force:
        if not quiet:
            print("  （本地运行，不写入 trend.json）")
        return False
    tdoc["schema"] = 1
    tdoc["updated"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    netsafe.write_atomic(TREND, json.dumps(tdoc, ensure_ascii=False,
                                           separators=(",", ":")))
    return True


def size_kb(path=None):
    try:
        return round(os.path.getsize(path or PATH) / 1024, 1)
    except OSError:
        return 0.0


# ── 自测 ──────────────────────────────────────────────────────────────────────

def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("  ✔ " if c else "  ✘ ") + m)
        ok = ok and c

    T = 20000

    # 场景 1：新增 → 历史从零建立
    doc = {"schema": 2, "nodes": {}}
    st = observe(doc, {"a" * 32: {"ok": True, "ms": 200}}, today=T, slot=0)
    chk(st["new"] == 1 and doc["nodes"]["a" * 16]["tries"] == 1, "新增节点建立记录")
    chk(doc["nodes"]["a" * 16]["slots"] == "3" + "0" * 5, "时段桶记全成")

    # 场景 2：连续成功 → 攒够样本才是 stable
    for i in range(1, 4):
        observe(doc, {"a" * 32: {"ok": True, "ms": 180 + i * 10}},
                today=T + i, slot=i)
    rec = doc["nodes"]["a" * 16]
    chk(rec["tries"] == 4 and rec["hits"] == 4, "累计次数正确")
    chk(stability(rec)[0] == "stable", "4/4 成功 → stable")
    # 200/190/200/210 的中位取两个中间值里偏小的那个，即 200
    chk(len(rec["ms"]) == 4 and ms_med(rec) == 200, "延迟历史与中位数")

    # 场景 3：一次成功不算稳定
    doc2 = {"schema": 2, "nodes": {}}
    observe(doc2, {"b" * 32: {"ok": True, "ms": 100}}, today=T, slot=0)
    chk(stability(doc2["nodes"]["b" * 16])[0] == "unknown", "只测 1 次 → unknown")

    # 场景 4：连续失败 → poor，且 fail_run 累加
    for i in range(3):
        observe(doc2, {"b" * 32: {"ok": False}}, today=T + i, slot=i)
    rec = doc2["nodes"]["b" * 16]
    chk(rec["fail_run"] == 3 and stability(rec)[0] == "poor", "连败 3 次 → poor")

    # 场景 5：恢复
    observe(doc2, {"b" * 32: {"ok": True, "ms": 300}}, today=T + 4, slot=4)
    rec = doc2["nodes"]["b" * 16]
    chk(rec["fail_run"] == 0 and rec["ok_run"] == 1, "恢复后连续失败清零")
    chk(rec["hits"] == 2 and rec["tries"] == 5, "恢复计进累计")

    # 场景 6：消失 → gone 计数；超窗口才删除
    st = observe(doc, {"c" * 32: {"ok": True}}, today=T + 5, slot=5)
    chk(st["gone"] >= 0, "gone 不报错")
    doc["nodes"]["d" * 16] = dict(_blank(T - 100), last=T - 100)
    removed = prune(doc, today=T + 5)
    chk(removed == 1 and "d" * 16 not in doc["nodes"], "超 30 天窗口被裁剪")

    # 场景 7：改配置 → 新指纹独立，旧记录不被改写（不错配）
    before = dict(doc["nodes"]["a" * 16])
    observe(doc, {"z" * 32: {"ok": False}}, today=T + 5, slot=5)
    chk(doc["nodes"]["a" * 16] == before, "新指纹不影响旧记录")
    chk("z" * 16 in doc["nodes"], "改配置后的节点独立建账")

    # 场景 8：断档回归识别
    doc3 = {"schema": 2, "nodes": {}}
    observe(doc3, {"e" * 32: {"ok": True}}, today=T, slot=0)
    st = observe(doc3, {"e" * 32: {"ok": True}}, today=T + 5, slot=0)
    chk(st["revived"] == 1, "隔了 5 天回来 → 记回归")
    st = observe(doc3, {"e" * 32: {"ok": True}}, today=T + 6, slot=0)
    chk(st["revived"] == 0, "连续两天不算回归")

    # 场景 9：分时段成功率
    doc4 = {"schema": 2, "nodes": {}}
    observe(doc4, {"f" * 32: {"ok": True}}, today=T, slot=0)
    observe(doc4, {"f" * 32: {"ok": False}}, today=T, slot=0)
    chk(doc4["nodes"]["f" * 16]["slots"][0] == "2", "同段一成一败 → 部分")
    good, tried = slot_ok(doc4["nodes"]["f" * 16])
    chk((good, tried) == (1, 1), "slot_ok 统计")

    # 场景 10：日趋势累计与裁剪
    td = {"days": []}
    trend_add(td, 1200, 400, [600, 700], new=10, gone=3, by_proto={"vless": (800, 300)}, today=T)
    trend_add(td, 1200, 500, [650], new=8, gone=5, by_proto={"vless": (900, 400)}, today=T)
    row = td["days"][0]
    chk(row["tested"] == 2400 and row["ok"] == 900 and row["rounds"] == 2, "同日累计")
    chk(row["gone"] == 5, "gone 取最大值不累加")
    chk(row["by_proto"]["vless"] == {"tested": 1700, "ok": 700}, "协议分桶累计")
    for i in range(400):
        trend_add(td, 1, 1, today=T + i + 1)
    chk(len(td["days"]) <= TREND_DAYS, "趋势行数受 365 天上限约束")

    # 场景 11：旧数据迁移，含 ever_ok 缺失修复
    legacy = {"nodes": {"Q" * 32: {"ok_streak": 2, "fail_streak": 0,
                                   "last_ok": True, "checked": 2}}}
    p = os.path.join(BASE, "data", "_legacy_selftest.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(legacy, open(p, "w"))
    global LEGACY
    real_legacy, LEGACY = LEGACY, p
    d5, n = migrate_legacy({"schema": 2, "nodes": {}})
    LEGACY = real_legacy
    os.remove(p)
    rec = d5["nodes"]["Q" * 16]
    chk(n == 1 and rec["hits"] == 2 and rec["migrated"], "旧记录迁移并标注")
    chk(stability(rec) == ("unknown", "仅测 2 次"), "迁移数据样本不足 → unknown")

    # 场景 12：落盘压缩后读回来，判定结果不能变
    deadrec = {"first": T, "last": T, "last_ok": None, "tries": 4, "hits": 0,
               "ok_run": 0, "fail_run": 4, "slots": "111000", "ms": [], "bps": []}
    slim = _compact(deadrec)
    chk("last_ok" not in slim and "ms" not in slim, "空字段被省略")
    chk(stability(slim)[0] == "poor", "压缩后稳定性判定不变")
    chk(slot_ok(slim) == (0, 3), "压缩后时段统计仍正确")
    alive = {"first": T, "last": T, "last_ok": T, "tries": 4, "hits": 4,
             "ok_run": 4, "fail_run": 0, "slots": "333300",
             "ms": [120, 130], "bps": [2_000_000]}
    s2 = _compact(alive)
    chk(s2["ms"] == [120, 130] and stability(s2)[0] == "stable", "活节点字段完整保留")

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


def sizetest(n=12000, rounds=6, verbose=True):
    """体积上限测试：造 n 条节点、每条观测 rounds 次，量序列化后的大小。

    这不是估算，是把真实写盘路径（observe → _compact → json.dumps）跑一遍。
    「一万节点会不会把仓库撑爆」这种问题，估算出来的答案从来不可信。
    """
    import random
    rnd = random.Random(20261125)
    doc = {"schema": SCHEMA, "nodes": {}}
    T = 20000
    for i in range(n):
        fp = f"{i:016x}" + "0" * 16
        alive = rnd.random() < 0.34          # 与实测的 34.5% 通过率对齐
        for d in range(rounds):
            ok = alive if rnd.random() < 0.9 else not alive
            doc["nodes"].setdefault(fp[:KEY_LEN], None)
            observe(doc, {fp: {"ok": ok, "ms": rnd.randint(80, 3000),
                               "bps": rnd.randint(100_000, 8_000_000)}},
                    today=T + d, slot=d % SLOTS)
    prune(doc, today=T + rounds)
    slim = {k: v for k, v in doc.items() if k != "nodes"}
    slim["nodes"] = {k: _compact(v) for k, v in doc["nodes"].items()}
    raw = json.dumps(slim, ensure_ascii=False, separators=(",", ":"))
    kb = len(raw.encode()) / 1024
    per = kb * 1024 / max(1, len(slim["nodes"]))
    dead = [v for v in slim["nodes"].values() if not v.get("hits")]
    if verbose:
        print(f"体积测试：{len(slim['nodes'])} 条 / {rounds} 次观测")
        print(f"  序列化 {kb:.0f} KB，均 {per:.0f} 字节/条")
        if dead:
            dk = sum(len(json.dumps(v, separators=(',', ':'))) for v in dead) / len(dead)
            print(f"  死号 {len(dead)} 条，均 {dk:.0f} 字节；活号均 "
                  f"{(kb * 1024 - dk * len(dead)) / max(1, len(slim['nodes']) - len(dead)):.0f} 字节")
        print(f"  上限线：单文件 <2 MB、条目 <15,000（超出说明窗口或抽样出问题）")
    return {"count": len(slim["nodes"]), "kb": round(kb, 1), "per": round(per)}


def _print_trend(days=14):
    t = load_trend()
    arr = trend_window(t, days)
    if not arr:
        print("还没有 trend.json（CI 跑过一轮后生成）")
        return
    print(f"{'日期':<12}{'轮':>3}{'抽样':>7}{'可用':>7}{'可用率':>8}"
          f"{'P50':>7}{'新增':>6}{'消失':>6}")
    for r in arr:
        tst, ok = int(r.get("tested") or 0), int(r.get("ok") or 0)
        pct = f"{ok / tst * 100:.1f}%" if tst else "—"
        print(f"{r.get('d'):<12}{r.get('rounds') or 0:>3}{tst:>7}{ok:>7}{pct:>8}"
              f"{str(r.get('p50') or '—'):>7}{r.get('new') or 0:>6}{r.get('gone') or 0:>6}")
    first, last = arr[0], arr[-1]
    a = (first.get("ok") or 0) / (first.get("tested") or 1)
    b = (last.get("ok") or 0) / (last.get("tested") or 1)
    if len(arr) >= 2:
        print(f"\n窗口内可用率 {a:.1%} → {b:.1%}（{b - a:+.1f} 个百分点）")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--sizetest" in sys.argv:
        n = int(sys.argv[sys.argv.index("--sizetest") + 1]) if \
            sys.argv.index("--sizetest") + 1 < len(sys.argv) and \
            sys.argv[sys.argv.index("--sizetest") + 1].isdigit() else 12000
        r = sizetest(n)
        sys.exit(0 if r["kb"] < 2048 and r["count"] < 15000 else 1)
    if "--trend" in sys.argv:
        n = int(sys.argv[sys.argv.index("--trend") + 1]) if \
            sys.argv.index("--trend") + 1 < len(sys.argv) and \
            sys.argv[sys.argv.index("--trend") + 1].isdigit() else 14
        _print_trend(n)
        sys.exit(0)
    d = load()
    n = len(d.get("nodes") or {})
    kb = size_kb()
    t = load_trend()
    print(f"节点历史：{n} 条，{kb} KB（窗口 {WINDOW_DAYS} 天），更新于 {d.get('updated')}")
    print(f"日趋势：{len(t.get('days') or [])} 天，{round(os.path.getsize(TREND) / 1024, 1) if os.path.exists(TREND) else 0} KB")
    if n:
        from collections import Counter
        c = Counter(stability(r)[0] for r in d["nodes"].values())
        print("  稳定性分布：" + "  ".join(f"{k} {v}" for k, v in c.most_common()))
        tried = sorted(sum(1 for ch in (r.get("slots") or "") if ch in "123")
                       for r in d["nodes"].values())
        if tried:
            print(f"  六时段覆盖：中位测过 {tried[len(tried) // 2]} 段（满 {SLOTS} 段）")
