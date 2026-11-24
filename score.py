#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""来源质量评分与淘汰状态机（P4）。

任务书 §5 的要求逐条落在本文件：

- 等级 S 90–100 / A 80–89 / B 65–79 / C 50–64 / D <50，**只由实测数据算**，
  不读 README 里的宣传数字；公式与权重写在下面，可复算。
- 单轮失败 → 标记异常（watch）；连续两轮 → 降级（degraded）；
  连续三轮 → 隔离（isolated）。隔离只是「不进入优选」，不删除来源。
- 网络恢复后下一轮成功即进入 recovering，再下一轮成功回 ok。
- 连续三轮无有效新增且分数持续下降 → 优先级下调（priority_penalty）。
- 报告必须列出原始数、去重数、独立新增数、有效数、重复率。

为什么不把等级写进 sources.yaml：等级是**每轮都会变的观测值**，
写进人工维护的清单会让 diff 全是噪声，也会让人误以为它是人工评定。
YAML 里只保留人工判断（enabled / priority / note），等级落在
data/sources_stats.json 与 data/source_history.json。
"""
import json
import os
from datetime import datetime, timedelta, timezone

import netsafe

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
STATS_PATH = os.path.join(DATA, "sources_stats.json")
HISTORY_PATH = os.path.join(DATA, "source_history.json")
REPORT_PATH = os.path.join(BASE, "docs", "P4-source-quality.md")

# ── 权重：合计 100。可用率占 40，是这里唯一能证明「节点真的能用」的指标 ──
W_ALIVE = 40      # 抽样 TCP 可连率
W_UNIQUE = 12     # 独立新增（对其他源不重复的指纹数，对数刻度）
W_RATIO = 8       # 独立率 = 独立新增 / 原始解析
W_FRESH = 12      # 源内容新鲜度
W_STABLE = 18     # 采集连续失败 + 可连率波动
W_COMPLY = 10     # 许可证声明 4 + 原创/聚合 6

GRADES = ((90, "S"), (80, "A"), (65, "B"), (50, "C"), (0, "D"))
GRADE_ORDER = ["S", "A", "B", "C", "D"]

# 历史保留轮数：3 小时一轮，30 轮 ≈ 4 天，够判「连续三轮」也够看趋势
KEEP_ROUNDS = 30


def _num(v, default=None):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return default


def grade_of(score):
    for cut, g in GRADES:
        if score >= cut:
            return g
    return "D"


def downgrade(g, n=1):
    """降一级。已到 D 就停在 D，不会因为失败被「降成不存在」。"""
    i = min(len(GRADE_ORDER) - 1, GRADE_ORDER.index(g) + n)
    return GRADE_ORDER[i]


# ── 五个维度 ────────────────────────────────────────────────────────

def _d_alive(row):
    """可连率 → 40 分。曲线压过 70% 即满分：这个领域没人能做到 100%，
    拿 100% 当满分会让所有人都在及格线下。低于 15% 迅速掉到个位数。"""
    pct = _num(row.get("alive_pct"))
    if pct is None:
        # 没抽样不等于不可用，给中位数分并标注 unknown，避免「没测=0 分」误杀
        return W_ALIVE * 0.5, "未抽样，按中位计"
    ratio = min(1.0, max(0.0, pct / 100.0) / 0.7)
    return round(W_ALIVE * (ratio ** 0.8), 1), f"可连 {pct:.0f}%"


def _d_unique(row):
    """独立新增 → 12 分，对数刻度。1,500 条以上封顶：
    再多的「独有节点」也还是同一批免费机场的池子，边际价值递减。"""
    u = _num(row.get("unique"), _num(row.get("kept"), 0))
    if not u or u <= 0:
        return 0.0, "无独立新增"
    import math
    v = min(1.0, math.log(1 + u) / math.log(1501))
    return round(W_UNIQUE * v, 1), f"独立 {int(u)} 条"


def _d_ratio(row):
    """独立率 → 8 分。50% 以上满分：说明这个源不是别人的下游镜像。"""
    u = _num(row.get("unique"), _num(row.get("kept"), 0)) or 0
    p = _num(row.get("parsed")) or 0
    if p <= 0:
        return 0.0, "原始数为 0"
    r = u / p
    return round(W_RATIO * min(1.0, r / 0.5), 1), f"独立率 {r * 100:.0f}%"


def _d_fresh(row):
    """新鲜度 → 12 分。超过 7 天没变化基本等于停更。"""
    age = _num(row.get("source_age_h"))
    if age is None:
        return W_FRESH * 0.5, "更新时间未知"
    for cut, v in ((2, 1.0), (6, 0.87), (24, 0.67), (72, 0.4), (168, 0.2)):
        if age <= cut:
            return round(W_FRESH * v, 1), f"{age:.0f} 小时前更新"
    return 0.0, f"{age / 24:.0f} 天未更新"


def _d_stable(row, hist):
    """稳定性 → 18 分 = 连续失败 12 + 可连率波动 6。

    连续失败是「源本身挂了」的直接证据，权重必须高；
    波动只惩罚忽高忽低的源，避免把「本来就 20% 但一直 20%」判成不稳。
    """
    streak = int(_num(row.get("fails_streak"), 0) or 0)
    fail_pts = {0: 12, 1: 8, 2: 4}.get(streak, 0)
    rates = [h.get("alive_pct") for h in (hist or [])[-6:]
             if h.get("alive_pct") is not None]
    if len(rates) < 2:
        var_pts, var_note = 3.0, "历史不足"
    else:
        spread = max(rates) - min(rates)
        var_pts = 6.0 if spread <= 8 else 4.5 if spread <= 15 else 3.0 if spread <= 25 else 1.5
        var_note = f"近 {len(rates)} 轮波动 {spread:.0f}pp"
    return round(fail_pts + var_pts, 1), f"连败 {streak} · {var_note}"


def _d_comply(row):
    """合规与来源类型 → 10 分。

    许可证不是质量问题，但它决定「这个源能不能被合法再分发」，
    所以在总分里只占 4 分，同时单独列在报告里提示人工确认。
    """
    lic = (row.get("license") or "").strip()
    lic_pts = 4.0 if lic else 0.0
    type_pts = 6.0 if row.get("type") == "original" else 3.0
    note = ("有许可证 " + lic) if lic else "仓库未声明许可证"
    note += " · 原创" if row.get("type") == "original" else " · 聚合"
    return round(lic_pts + type_pts, 1), note


def score_row(row, hist=None):
    """→ (score, dims)。dims 是 [(维度, 得分, 满分, 说明)]，报告直接用它展示扣分点。"""
    parts = [
        ("可连率", _d_alive(row), W_ALIVE),
        ("独立新增", _d_unique(row), W_UNIQUE),
        ("独立率", _d_ratio(row), W_RATIO),
        ("新鲜度", _d_fresh(row), W_FRESH),
        ("稳定性", _d_stable(row, hist), W_STABLE),
        ("合规/类型", _d_comply(row), W_COMPLY),
    ]
    dims = [(name, v[0], full, v[1]) for name, v, full in parts]
    total = round(sum(d[1] for d in dims), 1)
    return total, dims


# ── 状态机 ──────────────────────────────────────────────────────────

def is_failed(row):
    """本轮是否算「采集失败」。

    只看「拿不到数据」，不看「节点连不上」——后者是质量下降，
    走降级链路；把两者混在一起会让一次网络抖动直接触发隔离。
    """
    return row.get("http") != 200 or row.get("parsed") is None


def next_state(prev, failed, fails_streak):
    prev = prev or "ok"
    if failed:
        if fails_streak >= 3:
            return "isolated"
        if fails_streak == 2:
            return "degraded"
        return "watch"
    # 成功一次就从异常里出来，但先记一轮 recovering 再回 ok：
    # 「刚恢复」和「一直稳定」在页面上不是一回事
    if prev in ("watch", "degraded", "isolated", "recovering"):
        return "recovering" if prev != "recovering" else "ok"
    return "ok"


STATE_LABEL = {
    "ok": "正常",
    "watch": "单轮异常",
    "degraded": "降级",
    "isolated": "已隔离",
    "recovering": "恢复观察",
}


def penalty_of(hist):
    """连续三轮无有效新增且分数持续下降 → 每满 3 轮降一级优先级，上限 3。

    为什么要「且分数下降」：一个稳定在 20 分的源不该被淘汰，
    它在自己的水平上是可靠的；掉队的是「没有新东西且在变差」的源。
    """
    if not hist or len(hist) < 3:
        return 0
    n = 0
    for i in range(len(hist) - 1, 0, -1):
        cur, prev = hist[i], hist[i - 1]
        if (cur.get("unique") or 0) == 0 and (cur.get("score") or 0) < (prev.get("score") or 0):
            n += 1
        else:
            break
    return min(3, n // 3)


# ── 历史 ────────────────────────────────────────────────────────────

def load_history():
    try:
        doc = json.load(open(HISTORY_PATH, encoding="utf-8"))
        if isinstance(doc.get("sources"), dict):
            return doc
    except Exception:
        pass
    return {"updated": None, "rounds": KEEP_ROUNDS, "sources": {}}


def env_name():
    """数据来源环境。机房和家用网络的出口差得远，两边的可连率不能接在一起算趋势。

    ci    = GitHub Actions（权威，页面上的数据来自这里）
    local = 本地强制写入（调试用，NODES_FORCE_WRITE=1）
    dry   = 只读，不落盘
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "ci"
    if os.environ.get("NODES_FORCE_WRITE") == "1":
        return "local"
    return "dry"


def save_history(doc, force=None):
    """CI 里落盘，本地只打印。评分依赖历史，本地出口网络差异大，
    写进去会污染「连续几轮」的判定。NODES_FORCE_WRITE=1 可强制写（调试/回放）。

    注意这里必须认 GITHUB_ACTIONS：漏了它，CI 读完历史算完级却不写回，
    下一轮又读到同一份旧历史，「连续三轮」永远数不到——状态机会静默失效。
    """
    if force is None:
        force = (os.environ.get("GITHUB_ACTIONS") == "true"
                 or os.environ.get("NODES_FORCE_WRITE") == "1")
    if not force:
        print("  （本地运行，不写入 source_history.json）")
        return False
    netsafe.write_atomic(HISTORY_PATH, json.dumps(doc, ensure_ascii=False, indent=1))
    return True


def annotate(rows, history=None, stamp=None, persist=True):
    """给每行 stats 补上 score / grade / state / dims，并把本轮写进历史。

    返回 (rows, history)。rows 原地修改，顺序不变。
    """
    hist = history if history is not None else load_history()
    hist.setdefault("sources", {})
    stamp = stamp or datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    # 环境切换就重来：本地的 8% 和机房的 8% 不是同一个数，
    # 接在一起会把「换了个出口」算成「源在变差」
    env = env_name()
    if env != "dry" and hist.get("env") and hist["env"] != env:
        print(f"  （采集环境 {hist['env']} → {env}，历史不可比，重新积累）")
        hist["sources"] = {}
    if env != "dry":
        hist["env"] = env

    for row in rows:
        sid = row.get("id")
        series = hist["sources"].setdefault(sid, [])
        prev_state = series[-1].get("state") if series else "ok"
        prev_penalty = series[-1].get("penalty") if series else 0

        score, dims = score_row(row, series)
        failed = is_failed(row)
        state = next_state(prev_state, failed, int(row.get("fails_streak") or 0))
        g = grade_of(score)
        if state == "degraded":
            g = downgrade(g, 1)
        elif state == "isolated":
            g = "D"
        penalty = penalty_of(series) if not failed else 0

        row["score"] = score
        row["grade"] = g
        row["grade_raw"] = grade_of(score)
        row["state"] = state
        row["state_label"] = STATE_LABEL[state]
        row["dims"] = [{"name": n, "score": s, "max": m, "note": note}
                       for n, s, m, note in dims]
        row["priority_penalty"] = penalty
        row["priority_eff"] = int(row.get("priority") or 0) + penalty
        row["unique"] = int(_num(row.get("unique"), _num(row.get("kept"), 0)) or 0)
        row["dup"] = (int(row["parsed"] - row["unique"])
                      if isinstance(row.get("parsed"), int) else None)
        row["dup_rate"] = (round(row["dup"] / row["parsed"] * 100, 1)
                           if isinstance(row.get("parsed"), int) and row["parsed"] else None)
        al = _num(row.get("alive_pct"))
        row["effective"] = (round(row["unique"] * al / 100) if al is not None else None)

        # 轮次标识用 stamp 而不是 row.checked_at：后者在回放/重算时可能被复用，
        # 会让多轮历史互相覆盖，penalty 永远算不出来
        entry = {"ts": stamp, "score": score, "grade": g,
                 "state": state, "parsed": row.get("parsed"), "unique": row["unique"],
                 "alive_pct": row.get("alive_pct"), "fails_streak": row.get("fails_streak"),
                 "penalty": penalty}
        # 同一轮重跑（--render、本地调试）应该覆盖而不是追加
        if series and series[-1].get("ts") == entry["ts"]:
            series[-1] = entry
        else:
            series.append(entry)
        del series[:-KEEP_ROUNDS]

    hist["updated"] = stamp
    if persist:
        save_history(hist)
    return rows, hist


# ── 报告 ────────────────────────────────────────────────────────────

METHOD = """# P4：来源质量评分与淘汰

> 由 `score.py` 从 `data/sources_stats.json` + `data/source_history.json` 生成，**每次采集自动重写**。
> 不要手工改表里的数字——要改就改公式，然后重跑。

## 1. 评分公式（合计 100）

| 维度 | 满分 | 计算方式 |
|---|---:|---|
| 可连率 | 40 | 抽样 TCP 可连率，70% 封顶；`40 × min(1, 率/0.7)^0.8` |
| 独立新增 | 12 | 只被本源包含的指纹数，对数刻度，1,500 条封顶 |
| 独立率 | 8 | 独立新增 / 原始解析，50% 封顶 |
| 新鲜度 | 12 | 源内容 2 小时内更新满分，7 天以上 0 |
| 稳定性 | 18 | 连续失败 12（0/1/2/≥3 轮 → 12/8/4/0）+ 近 6 轮可连率波动 6 |
| 合规/类型 | 10 | 有 SPDX 许可证 4 + 原创 6 / 聚合 3 |

等级：**S 90–100 · A 80–89 · B 65–79 · C 50–64 · D <50**。

三个刻意的设计：

1. **可连率占 40 分**，因为它是唯一能证明「节点真能用」的指标。任务书要求
   「不盲目堆数量」，把独立新增压到 12 分就是这句话的量化版本。
2. **独立新增按「指纹」算，不按地址**。同机不同 UUID 是两个节点，
   同 UUID 换域名是一个节点——地址级比对两头都会错。
3. **许可证只占 4 分**。它决定能不能合法再分发，是合规问题不是质量问题；
   但每一行都会在报告里标出来，`未声明` 需要人工确认。

## 2. 淘汰状态机

| 触发 | 动作 | 效果 |
|---|---|---|
| 单轮采集失败（HTTP≠200 或解析失败） | `watch` | 页面标「单轮异常」，等级不变 |
| 连续 2 轮失败 | `degraded` | 等级降一级 |
| 连续 3 轮失败 | `isolated` | 强制 D，**不进入优选导出**，仍继续采集 |
| 恢复后首轮成功 | `recovering` | 解除隔离，等级回到实测值 |
| 再一轮成功 | `ok` | 完全恢复 |
| 连续 3 轮无独立新增且分数下降 | 优先级 +1（最多 +3） | 卡片排序下沉，不删除 |

隔离的语义是「先别信它」，不是「删掉它」。历史、来源关系、订阅地址全部保留，
网络恢复后下一轮就能自己回来——任务书 §5 明确禁止因单轮故障永久删除来源。

状态机有三条分支平时跑不到（没有源会主动连续挂三轮），行为用固定用例钉住：
`python3 score.py --selftest`（等级边界、三级失败迁移、恢复观察、优先级下调、同轮幂等）。

## 3. 本轮实测

"""


def render_report(rows, hist):
    rows = sorted(rows, key=lambda r: (-(r.get("score") or 0), -(r.get("unique") or 0)))
    out = [METHOD]
    env = hist.get("env") or "dry"
    env_label = {"ci": "GitHub Actions 机房", "local": "本地网络（NODES_FORCE_WRITE=1）"}.get(env, "只读")
    out.append(f"生成时间：{hist.get('updated')}　来源数：{len(rows)}　"
               f"历史轮次上限：{hist.get('rounds', KEEP_ROUNDS)}　采集环境：{env_label}\n")
    if env == "local":
        out.append("> ⚠ 本轮由本地网络实测。可连率高度依赖出口线路，radarhub 这类源在本地"
                   "可能测出 0%，与机房结果不可比；CI 下一轮会用同一公式重算并覆盖本表。\n")

    # 汇总
    from collections import Counter
    gc = Counter(r.get("grade") for r in rows)
    sc = Counter(r.get("state") for r in rows)
    out.append("| 等级 | " + " | ".join("S A B C D".split()) + " |")
    out.append("|---|" + "---:|" * 5)
    out.append("| 数量 | " + " | ".join(str(gc.get(g, 0)) for g in "SABCD") + " |\n")
    out.append("状态：" + "　".join(f"{STATE_LABEL[k]} {v}" for k, v in sc.items()) + "\n")

    out.append("## 4. 来源明细\n")
    out.append("| 等级 | 分数 | 来源 | 类型 | 声明 | 解析 | 与他源重复 | 独立新增 | 有效 | 重复率 | 可连 | 新鲜 | 状态 |")
    out.append("|:--:|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|")
    for r in rows:
        fresh = r.get("source_age_h")
        fresh_s = "—" if fresh is None else (f"{fresh:.0f}h" if fresh < 48 else f"{fresh / 24:.0f}d")
        out.append(
            f"| **{r.get('grade')}** | {r.get('score')} | {r.get('name')} | "
            f"{'原创' if r.get('type') == 'original' else '聚合'} | "
            f"{r.get('declared') if r.get('declared') is not None else '—'} | "
            f"{r.get('parsed') if r.get('parsed') is not None else '—'} | "
            f"{r.get('dup') if r.get('dup') is not None else '—'} | "
            f"{r.get('unique')} | "
            f"{r.get('effective') if r.get('effective') is not None else '—'} | "
            f"{(str(r.get('dup_rate')) + '%') if r.get('dup_rate') is not None else '—'} | "
            f"{(str(r.get('alive_pct')) + '%') if r.get('alive_pct') is not None else '—'} | "
            f"{fresh_s} | {r.get('state_label')} |")

    out.append("\n> **声明**是源文件自己写的条数，**解析**是实际能解析出来的条数，"
               "两者差距大说明源文格式有问题；**与他源重复**是与至少一个其他源指纹相同的条数；"
               "**有效** = 独立新增 × 抽样可连率，是**估计值**，抽样只有 24 个，"
               "±10 个百分点属正常波动，不能当成精确的可用节点数。\n")

    # 扣分点
    out.append("## 5. 各源扣分点\n")
    for r in rows:
        lost = [(d["name"], d["score"], d["max"], d["note"])
                for d in (r.get("dims") or []) if d["score"] < d["max"] * 0.7]
        if not lost:
            continue
        out.append(f"**{r.get('name')}（{r.get('grade')} {r.get('score')}）**")
        for name, s, m, note in lost:
            out.append(f"- {name} {s}/{m} — {note}")
        out.append("")

    # 需要人工关注
    out.append("## 6. 需要人工判断的\n")
    iso = [r for r in rows if r.get("state") == "isolated"]
    deg = [r for r in rows if r.get("state") == "degraded"]
    nolic = [r for r in rows if not (r.get("license") or "").strip()]
    pen = [r for r in rows if (r.get("priority_penalty") or 0) > 0]
    if iso:
        out.append("**已隔离（不进入优选导出，仍在采集等待恢复）：** "
                   + "、".join(f"{r['name']}（{r.get('last_error') or '连续失败'}）" for r in iso))
    if deg:
        out.append("**降级：** " + "、".join(r["name"] for r in deg))
    if pen:
        out.append("**连续无新增、优先级已下调：** "
                   + "、".join(f"{r['name']} +{r['priority_penalty']}" for r in pen))
    if nolic:
        out.append(f"**未声明许可证（{len(nolic)} 个）：** " + "、".join(r["name"] for r in nolic)
                   + "。再分发前需人工确认授权。")
    if not (iso or deg or pen):
        out.append("本轮无隔离、无降级、无优先级下调。")
    out.append("")

    # ── 结论：只写数据支持得住的话，不写「整体健康」这类安慰句 ──
    out.append("## 7. 结论\n")
    if rows:
        top, bot = rows[0], rows[-1]
        out.append(f"- 最高 **{top['name']}**（{top['grade']} {top['score']}）："
                   f"可连 {top.get('alive_pct')}%，独立新增 {top.get('unique')} 条。")
        out.append(f"- 最低 **{bot['name']}**（{bot['grade']} {bot['score']}）："
                   f"可连 {bot.get('alive_pct')}%，独立新增 {bot.get('unique')} 条。")
        if "S" not in [r.get("grade") for r in rows]:
            out.append("- **本轮没有 S 级源**。S 要求可连率 ≥70% 且独立新增 ≥1,500 条同时成立——"
                       "这个领域里「量」和「可用」是负相关的（P2 §4③ 已实测），"
                       "没有源能同时满足，不是数据缺失。")
        dead = [r for r in rows if (r.get("alive_pct") or 100) < 10]
        if dead:
            out.append("- 可连率 <10%（发出的节点基本用不了）："
                       + "、".join(f"{r['name']} {r.get('alive_pct')}%" for r in dead)
                       + "。按任务书 §5 属于异常，但**单轮不足以淘汰**——先看下一轮是否复现。")
        sub = [r for r in rows if (r.get("unique") or 0) == 0]
        if sub:
            out.append("- 独立新增为 0（完全被其他源包含）："
                       + "、".join(r["name"] for r in sub)
                       + "。这类源的价值是「别人已经帮你筛过」，不是扩容；"
                       "保留是因为可连率可能更高，但不必期待它带来新节点。")
        big_dead = [r for r in rows if (r.get("unique") or 0) >= 500 and (r.get("alive_pct") or 100) < 20]
        if big_dead:
            out.append("- 独立新增多但可连率 <20%："
                       + "、".join(f"{r['name']}（{r.get('unique')} 条 / {r.get('alive_pct')}%）" for r in big_dead)
                       + "。这是最需要警惕的一类——**看起来贡献很大，实际能用的很少**，"
                       "订阅里塞进去的主要是死条目。")
    out.append("")
    return "\n".join(out)


def selftest():
    """状态机自测。这些分支平时跑不到（没有源会主动连续挂三轮），
    所以用固定用例钉住行为，避免以后改公式时把淘汰逻辑改坏。"""
    base = {"id": "t", "name": "T", "url": "http://x", "type": "original",
            "format": "clash", "license": "MIT", "priority": 5, "checked_at": "t",
            "http": 200, "parsed": 1000, "declared": 1000, "kept": 800, "unique": 800,
            "probe_total": 24, "probe_ok": 20, "alive_pct": 83, "fails_streak": 0,
            "last_error": None, "source_age_h": 1.0}
    checks = []

    sc, _ = score_row(base)
    checks.append(("高质量源落在 S/A", grade_of(sc) in ("S", "A"), f"{sc} {grade_of(sc)}"))

    bad = dict(base, alive_pct=5, unique=0, parsed=2000, type="aggregator",
               license=None, source_age_h=400, fails_streak=1)
    checks.append(("差源落在 D", grade_of(score_row(bad)[0]) == "D", f"{score_row(bad)[0]}"))

    seq, prev = [], "ok"
    for k in (1, 2, 3, 4):
        prev = next_state(prev, True, k)
        seq.append(prev)
    checks.append(("单轮异常→两轮降级→三轮隔离", seq == ["watch", "degraded", "isolated", "isolated"], str(seq)))
    checks.append(("恢复先观察一轮", next_state("isolated", False, 0) == "recovering"
                   and next_state("recovering", False, 0) == "ok", "recovering→ok"))

    hist = {"updated": None, "env": "dry", "rounds": KEEP_ROUNDS, "sources": {}}
    for i in range(9):
        r = dict(base, unique=0, alive_pct=50 - i * 5)
        rows, hist = annotate([r], hist, stamp=f"r{i}", persist=False)
    checks.append(("连续三轮下降才降优先级", rows[0]["priority_penalty"] >= 1,
                   f"penalty={rows[0]['priority_penalty']}"))

    rows, hist = annotate([dict(base)], hist, stamp="same", persist=False)
    n = len(hist["sources"]["t"])
    rows, hist = annotate([dict(base)], hist, stamp="same", persist=False)
    checks.append(("同轮重跑不重复记历史", len(hist["sources"]["t"]) == n, f"{n} 条"))

    ok = 0
    for name, passed, detail in checks:
        print(f"  {'✔' if passed else '✘'} {name}　{detail}")
        ok += bool(passed)
    print(f"\n{ok}/{len(checks)} 通过")
    return 0 if ok == len(checks) else 1


def main():
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    rows = (json.load(open(STATS_PATH, encoding="utf-8")) if os.path.exists(STATS_PATH)
            else {"sources": []}).get("sources") or []
    if not rows:
        print("没有 data/sources_stats.json，先在 CI 或 NODES_FORCE_WRITE=1 下跑一轮采集")
        return
    hist = load_history()
    rows, hist = annotate(rows, hist, persist="--no-write" not in sys.argv)
    if "--report" in sys.argv:
        netsafe.write_atomic(REPORT_PATH, render_report(rows, hist))
        print(f"已生成 {REPORT_PATH}")
    print(f"\n{'等级':<4}{'分数':>6}  {'来源':<18}{'原始':>7}{'独立':>7}{'可连':>7}  状态")
    for r in sorted(rows, key=lambda x: -(x.get("score") or 0)):
        print(f"{r['grade']:<4}{r['score']:>6}  {r['name'][:17]:<18}"
              f"{str(r.get('parsed') or '—'):>7}{r.get('unique'):>7}"
              f"{(str(r.get('alive_pct')) + '%') if r.get('alive_pct') is not None else '—':>7}"
              f"  {r['state_label']}")


if __name__ == "__main__":
    main()
