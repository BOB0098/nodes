#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""来源治理（P19）：重复源识别、接入依据、逐源指标、异常与观察期。

本模块**不联网**：每轮 CI 里从已有的 sources.yaml / sources_stats.json /
node_scores.json / candidates.json / screening.json 汇总出一份可复核的
`data/source_governance.json`。这样重复源识别用的是当轮真实结果，
不会因为治理步骤自己又去抓一遍而制造第二个口径。

任务书 §P19 的落点：
  · 重复仓库 / 镜像 / 转载 / 高度重合来源 → link_dups() + overlap_pairs()
  · 发现依据                            → discovery()，URL 经 mask_url 脱敏
  · 抓取成功率 / 有效节点率 / 重复率 /
    最近成功时间 / 连续失败次数          → per_source()
  · 异常隔离或降权、单次失败不永久删除  → 状态沿用 score.py 的三级状态机，
    这里只记录迁移，不做删除
  · 新源观察期                          → baseline 之外的源前 3 轮等级封顶 B

接入审核仍在 screen.py；出网仍只走 netsafe.py。本模块额外提供
content_type_ok() / read_limited() / open_source()，供审核路径复用：
"HTTP 200 但返回登录页 HTML" 这种源以前会被当成订阅解析失败，
现在能在拉取阶段就有明确原因。

用法：
    python3 governance.py --selftest
    python3 governance.py            # 汇总；CI 或 NODES_FORCE_WRITE=1 才落盘
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import netsafe
import score
import sources_io

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
GOV_PATH = os.path.join(DATA, "source_governance.json")
MD_PATH = os.path.join(BASE, "docs", "P19-governance.md")
STATS_PATH = os.path.join(DATA, "sources_stats.json")
SCORES_PATH = os.path.join(DATA, "node_scores.json")
NODES_PATH = os.path.join(DATA, "nodes.json")
CAND_PATH = os.path.join(DATA, "candidates.json")
SCREEN_PATH = os.path.join(DATA, "screening.json")
KEYS_PATH = os.path.join(DATA, "source_keys.json")

SCHEMA = 1
WINDOW = 30            # 逐源保留最近 30 轮
OBS_ROUNDS = 3         # 新增源观察轮数
MIN_KEYS = 20          # 重合度计算的最小样本，低于此数不判重合
OVERLAP_WARN = 0.5     # 部分重合
OVERLAP_HIGH = 0.8     # 高度重合
UA = "Mozilla/5.0 (compatible; nodes-governance/1.0)"


class BadContent(Exception):
    """内容类型校验失败。和「请求失败」分开记，避免误判成源挂了。"""


# ── 地址识别 ─────────────────────────────────────────────────────────────────
_GH_PATTERNS = (
    re.compile(r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/", re.I),
    re.compile(r"^https?://github\.com/([^/]+)/([^/]+)(?:/|$)", re.I),
    re.compile(r"^https?://(?:cdn|gcore)\.jsdelivr\.net/gh/([^/]+)/([^/@]+)[@/]", re.I),
    re.compile(r"^https?://fastly\.jsdelivr\.net/gh/([^/]+)/([^/@]+)[@/]", re.I),
)


def canonical_repo(url):
    """把 GitHub 主站 / raw / 三种 jsdelivr 通道归一成 `gh:owner/repo`。

    同一个仓库经 CDN 换个域名就当成新源，是重复接入最常见的来源。
    不认识的地址返回 None——blog.ermao.net 这类自有域名只能按完整地址比。
    """
    if not isinstance(url, str):
        return None
    for pat in _GH_PATTERNS:
        m = pat.match(url)
        if m:
            return "gh:" + (m.group(1) + "/" + m.group(2)).lower()
    return None


def canonical_file(url):
    """仓库内文件身份。分支名不进身份：main/master 互切还是同一份数据。"""
    repo = canonical_repo(url)
    if not repo:
        return None
    p = urllib.parse.urlsplit(url).path
    seg = [x for x in p.split("/") if x]
    # raw: owner/repo/branch/rest...  jsdelivr: gh/owner/repo@branch/rest...
    for i, s in enumerate(seg):
        if "@" in s:
            seg = seg[i + 1:]
            break
    else:
        if len(seg) >= 3:
            seg = seg[3:]
    path = "/".join(seg).lower()
    return repo + ":" + path if path else repo


def norm_url(url):
    """用于判等的地址：小写 host、去 www、去 query/fragment（token 常在 query）。"""
    try:
        p = urllib.parse.urlsplit(url or "")
    except ValueError:
        return ""
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # scheme 也不进身份：http 与 https 指向同一仓库同一文件仍是重复
    return f"{host}{p.path.rstrip('/') or '/'}"


def link_dups(sources):
    """同源地址识别。返回 (kind, a, b, evidence, severity) 列表。

    kind 三档：
      same-url    两个启用源指向同一地址 —— 明确重复，应合并
      mirror      同一文件走不同通道 —— 合法镜像，但只应有一个卡片
      same-repo   同一仓库不同文件 —— 信息项，可能是「全量 vs 可连」的定位差异
    """
    rows = []
    for s in sources:
        if not s.get("enabled"):
            continue
        u = s.get("url") or ""
        rows.append({
            "id": s.get("id"), "name": s.get("name"),
            "urls": [u] + [m for m in (s.get("mirrors") or []) if m],
            "repo": canonical_repo(u),
            "repos": {canonical_repo(m) for m in [u] + list(s.get("mirrors") or [])
                      if canonical_repo(m)},
        })
    out = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            au = {norm_url(u): u for u in a["urls"]}
            bu = {norm_url(u): u for u in b["urls"]}
            hit = sorted(set(au) & set(bu))
            if hit:
                kind, sev = "same-url", "warning"
                ev = "地址相同：" + netsafe.mask_url(au[hit[0]])
            else:
                af = {canonical_file(u) for u in a["urls"]} - {None}
                bf = {canonical_file(u) for u in b["urls"]} - {None}
                same_file = af & bf
                if same_file:
                    kind, sev = "mirror", "warning"
                    ev = "同一文件：" + next(iter(same_file))
                elif a["repos"] & b["repos"]:
                    kind, sev = "same-repo", "info"
                    ev = "同一仓库：" + sorted(a["repos"] & b["repos"])[0]
                else:
                    continue
            out.append(dict(kind=kind, a=a["name"], b=b["name"],
                            evidence=ev, severity=sev))
    return out


def node_keys(nodes):
    """按源归集「协议+地址」键。nodes.json 已脱敏没有 uri 和 fp，
    这里只能近似（screen.py 也用同一口径）；真实全参数指纹只会更不重合。"""
    out = {}
    for n in nodes or []:
        src = n.get("src")
        key = f'{str(n.get("proto") or "").lower()}|{n.get("addr")}'
        if src and n.get("addr"):
            out.setdefault(src, set()).add(key)
    return out


def overlap_pairs(keys, names=None):
    """高度重合来源。names 限定只看启用源，避免候选源干扰当轮结论。"""
    items = [(k, v) for k, v in (keys or {}).items()
             if (names is None or k in names) and len(v) >= MIN_KEYS]
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (na, a), (nb, b) = items[i], items[j]
            inter = len(a & b)
            union = len(a | b)
            ratio = round(inter / union, 3) if union else 0.0
            if ratio < OVERLAP_WARN:
                continue
            out.append(dict(a=na, b=nb, jaccard=ratio, shared=inter,
                            size_a=len(a), size_b=len(b),
                            kind="高度重合" if ratio >= OVERLAP_HIGH else "部分重合"))
    return sorted(out, key=lambda x: -x["jaccard"])


# ── 接入拉取的辅助校验 ───────────────────────────────────────────────────────
_ALLOWED_CT = {
    "text/plain", "text/yaml", "text/x-yaml", "application/yaml",
    "application/x-yaml", "application/json", "application/ndjson",
    "application/octet-stream", "application/xml", "text/xml", "text/csv",
    "application/zip", "application/gzip",
}
_HTML = (b"<!doctype html", b"<html", b"<head", b"<meta")


def content_type_ok(ctype, head=b""):
    """→ (bool, 原因)。校验内容类型，顺便嗅探"200 但返回登录页"的假订阅。"""
    sniff = (head or b"").lstrip()[:256].lower()
    if sniff.startswith(_HTML):
        return False, "内容是 HTML 页面，不是订阅数据"
    ct = (ctype or "").split(";")[0].strip().lower()
    if not ct:
        return True, "未声明 Content-Type，按内容判断"
    if ct in _ALLOWED_CT:
        return True, ct
    if ct == "text/html":
        return False, "Content-Type=text/html，疑似错误页或登录页"
    return False, f"不接受的内容类型 {ct}"


def read_limited(fp, max_bytes=netsafe.MAX_BYTES,
                 total_timeout=netsafe.TOTAL_TIMEOUT, prefix=b"", chunk=65536):
    """带字节上限和墙钟预算的流式读取。prefix 是已嗅探过的前 512 字节。"""
    t0 = time.monotonic()
    parts = [prefix] if prefix else []
    total = len(prefix)
    while True:
        if time.monotonic() - t0 > total_timeout:
            raise netsafe.TooSlow(f"读取超过 {total_timeout}s")
        b = fp.read(chunk)
        if not b:
            break
        total += len(b)
        if total > max_bytes:
            raise netsafe.TooLarge(max_bytes)
        parts.append(b)
    return b"".join(parts)


def guard_url(url):
    """接入前的 SSRF 防护入口。错误信息也过脱敏，token 不进日志。"""
    try:
        netsafe.check_url(url)
    except (netsafe.Blocked, netsafe.Unresolvable) as e:
        raise type(e)(netsafe.mask_text(str(e)))
    return True


def open_source(url, max_bytes=netsafe.MAX_BYTES):
    """审核 / 复测用的安全拉取。异常类型区分「不该访问」「太大」「太慢」「内容不对」，
    不把四种情况都压成「HTTP 000」。"""
    guard_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    t0 = time.monotonic()
    with netsafe.opener().open(req, timeout=netsafe.CONNECT_TIMEOUT) as r:
        peek = r.read(512)
        ok, why = content_type_ok(r.headers.get("Content-Type"), peek)
        if not ok:
            raise BadContent(why)
        data = read_limited(r, max_bytes=max_bytes, prefix=peek)
        return {"code": r.status, "data": data, "size": len(data),
                "ctype": r.headers.get("Content-Type", ""),
                "ms": int((time.monotonic() - t0) * 1000)}


# ── 逐源指标 ────────────────────────────────────────────────────────────────
def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _stamp():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


def _pct(a, b):
    return round(a / b * 100, 1) if b else None


def discovery(sources, screening):
    """来源发现依据：每条都在 candidates.json / screening.json / sources.yaml
    三处之一有可追溯记录，URL 一律脱敏（可能带订阅 token）。"""
    cands = {c.get("url"): c for c in (_load(CAND_PATH, {}).get("candidates") or [])}
    verdicts = {}
    for r in (screening.get("results") or []):
        if r.get("url"):
            verdicts[norm_url(r["url"])] = r
        elif r.get("repo"):
            verdicts[r["repo"].lower()] = r
    out = []
    for s in sources:
        u = s.get("url") or ""
        c = cands.get(u) or {}
        v = verdicts.get(norm_url(u)) or verdicts.get(str(c.get("repo") or "").lower()) or {}
        repo = canonical_repo(u) or c.get("repo")
        if c:
            basis = "discover.py 自动发现"
        elif s.get("enabled"):
            basis = "sources.yaml 人工登记（上线前四关见 P2 审核报告）"
        else:
            basis = "screen.py 内置候选（人工核验）"
        out.append({
            "id": s.get("id"), "name": s.get("name"), "enabled": bool(s.get("enabled")),
            "repo": repo, "url": netsafe.mask_url(u),
            "basis": basis,
            "first_seen": c.get("first_seen") or v.get("pushed") or None,
            "stars": c.get("stars") or v.get("stars"),
            "license": s.get("license") or c.get("license") or v.get("license"),
            "screening": v.get("verdict"),
            "screen_reason": (v.get("reasons") or [None])[0],
            "note": s.get("note"),
        })
    return out


def per_source(sources, stats, scores, prev=None):
    """逐源指标 + 30 轮滚动历史 + 观察期判定。

    状态直接取 score.py 当轮结果：治理层再跑一遍状态机会把 watch 推成
    degraded，一个单轮失败就变成降级，和评分口径打架。
    """
    prev_doc = prev or {}
    prev_src = prev_doc.get("sources") or {}
    baseline = set(prev_doc.get("baseline") or [])
    if not baseline:
        # 首次启用治理：现有源已有 P2 审核和多轮实测，作为存量不追溯观察期。
        # 之后新增的源才走 3 轮观察。这个决定写进文档，不悄悄改口径。
        baseline = {s["name"] for s in sources if s.get("enabled")}
    by_name = {r.get("name"): r for r in stats.get("sources") or []}
    tested = scores.get("by_source") or {}
    now = _stamp()
    out = {}
    for s in sources:
        name = s.get("name")
        if not s.get("enabled"):
            continue
        r = by_name.get(name) or {}
        prev_r = prev_src.get(name) or {}
        rounds = list(prev_r.get("rounds") or [])
        ok_http = r.get("http") == 200 and isinstance(r.get("parsed"), int)
        rounds.append({"ts": r.get("checked_at") or now, "ok": bool(ok_http),
                       "http": r.get("http"), "parsed": r.get("parsed"),
                       "kept": r.get("kept"), "dup_pct": r.get("dup_rate")})
        rounds = rounds[-WINDOW:]
        t = tested.get(name) or {}
        ntest, nok = int(t.get("tested") or 0), int(t.get("ok") or 0)
        state = r.get("state") or "ok"
        old_state = prev_r.get("state")
        trans = list(prev_r.get("transitions") or [])
        if old_state and old_state != state:
            trans.append({"from": old_state, "to": state, "ts": now})
        trans = trans[-10:]
        seen = int(prev_r.get("rounds_seen") or 0) + 1
        under_obs = name not in baseline and seen < OBS_ROUNDS
        out[name] = {
            "id": s.get("id"), "enabled": True,
            "first_seen": prev_r.get("first_seen") or now,
            "rounds_seen": seen,
            # 存量源标记为 legacy，新增源标记 new，观察期只压后者
            "intake": "legacy" if name in baseline else "new",
            "state": state, "state_label": score.STATE_LABEL.get(state, state),
            "transitions": trans,
            "fetch_success_pct": _pct(sum(1 for x in rounds if x.get("ok")), len(rounds)),
            "last_success": next((x["ts"] for x in reversed(rounds) if x.get("ok")), None),
            "fails_streak": int(r.get("fails_streak") or 0),
            "fails_total": int(r.get("fails_total") or 0),
            "last_error": r.get("last_error"),
            "parsed": r.get("parsed"), "kept": r.get("kept"),
            "dup_pct": r.get("dup_rate"),
            "valid_ok": nok if ntest else None,
            "valid_tested": ntest or None,
            "valid_pct": _pct(nok, ntest) if ntest else None,
            "grade_cap": "B" if under_obs else None,
            "under_observation": under_obs,
            "rounds": rounds,
        }
    return {"baseline": sorted(baseline), "sources": out}


def build(prev=None):
    _, sources, _ = sources_io.load()
    stats = _load(STATS_PATH, {"sources": []})
    scores = _load(SCORES_PATH, {"by_source": {}})
    nodes = _load(NODES_PATH, {"nodes": []}).get("nodes") or []
    screening = _load(SCREEN_PATH, {"results": []})
    enabled_names = {s["name"] for s in sources if s.get("enabled")}
    # source_keys.json 是 collector 在**去重前**写的地址哈希；nodes.json 是
    # 去重后的结果，拿后者算源间重合永远是 0。旧数据没有前者时退回后者，
    # 但它只能当「暂无重合数据」，不能当「源不重合」。
    sk = _load(KEYS_PATH, {})
    if sk.get("sources"):
        keys = {k: set(v) for k, v in sk["sources"].items()}
        key_src = f"source_keys.json（{sk.get('updated')}）"
    else:
        keys = node_keys(nodes)
        key_src = "nodes.json（去重后，仅兜底）"
    m = per_source(sources, stats, scores, prev)
    d = {
        "schema": SCHEMA, "updated": _stamp(),
        "window": WINDOW, "obs_rounds": OBS_ROUNDS,
        "whitelist": {
            "file": "sources.yaml",
            "registered": len(sources), "enabled": len(enabled_names),
            "audit": "screen.py 四关 + 人工置 enabled；治理层不会自行启用任何源",
        },
        "discovery": discovery(sources, screening),
        "duplicates": link_dups(sources),
        "overlap": overlap_pairs(keys, enabled_names),
        "overlap_source": key_src,
        "overlap_note": "地址以 sha256 前 16 位存储（明文不落盘）。"
                        "旧轮次无 source_keys.json 时用去重后的 nodes.json 兜底，"
                        "那种口径下重合恒为 0，不能当成「没有重合」。样本 <20 条不判。",
        "sources": m["sources"], "baseline": m["baseline"],
    }
    return d


def render_md(d):
    L = ["# P19 来源治理报告\n",
         f"> 由 `governance.py` 生成于 {d['updated']}。不联网、不改 sources.yaml；"
         "所有结论取自当轮采集与审核产物。\n",
         f"白名单：`sources.yaml` 登记 {d['whitelist']['registered']} 个，"
         f"启用 {d['whitelist']['enabled']} 个。"
         "新源必须过 `screen.py` 四关并人工置 enabled；治理层无权自行接入。\n"]
    rounds = [len(r.get("rounds") or []) for r in d["sources"].values()]
    if rounds and min(rounds) <= 1:
        L.append("> **注意**：治理刚启用，每源只有 1 轮记录，抓取成功率当前恒为 100%；"
                 "这个数字要跑满几轮才有区分度，不能当成「这些源从没出过问题」。\n")

    L.append("## 1. 重复与重合\n")
    dups = d["duplicates"]
    if dups:
        L.append("| 类型 | 源 A | 源 B | 证据 | 处置 |")
        L.append("|---|---|---|---|---|")
        for x in dups:
            act = "应合并，避免重复计数" if x["severity"] == "warning" else "定位差异，保留但看重合度"
            L.append(f"| {x['kind']} | {x['a']} | {x['b']} | {x['evidence']} | {act} |")
    else:
        L.append("未发现同一地址或同一文件的启用源。\n")
    ov = d["overlap"]
    if ov:
        L.append("\n内容重合（近似口径，见注）：\n")
        L.append("| 源 A | 源 B | 共有 | Jaccard | 判定 |")
        L.append("|---|---|---:|---:|---|")
        for x in ov:
            L.append(f"| {x['a']} | {x['b']} | {x['shared']} | {x['jaccard']:.0%} | {x['kind']} |")
        L.append(f"\n> 数据来自 {d.get('overlap_source')}。" + d["overlap_note"] + "\n")

    L.append("\n## 2. 逐源指标\n")
    L.append("| 源 | 状态 | 抓取成功率 | 最近成功 | 连败 | 真实可用 | 重复率 | 观察期 |")
    L.append("|---|---|---:|---|---:|---:|---:|---|")
    for n, r in d["sources"].items():
        vp = f"{r['valid_ok']}/{r['valid_tested']}（{r['valid_pct']}%）" if r.get("valid_pct") is not None else "—"
        dp = f"{r['dup_pct']}%" if r.get("dup_pct") is not None else "—"
        L.append(f"| {n} | {r['state_label']} | {r.get('fetch_success_pct') or '—'}% | "
                 f"{r.get('last_success') or '—'} | {r['fails_streak']} | {vp} | {dp} | "
                 f"{'是（上限 B）' if r['under_observation'] else '否'} |")

    L.append("\n## 3. 发现依据\n")
    L.append("| 源 | 依据 | 仓库 | 首次出现 | 审核结论 | 星数 | 备注 |")
    L.append("|---|---|---|---|---|---:|---|")
    for x in d["discovery"]:
        note = (x.get("screen_reason") or x.get("note") or "").replace("|", "／")[:64]
        L.append(f"| {x['name']} | {x['basis']} | {x.get('repo') or '—'} | "
                 f"{x.get('first_seen') or '—'} | {x.get('screening') or '—'} | "
                 f"{x.get('stars') or '—'} | {note or '—'} |")
    L.append("\n> 地址经过脱敏（query 中的订阅 token 不写进公开报告）。"
             "审核结论来自 `data/screening.json`，`skip` 表示已在册不重复审核。\n")
    return "\n".join(L) + "\n"


def selftest():
    ok = True

    def chk(cond, label):
        nonlocal ok
        if not cond:
            ok = False
            print("  ✘", label)

    # 1) 重复仓库 / 镜像 / 高度重合识别
    raw = "https://raw.githubusercontent.com/Owner/Repo/main/clash.yaml"
    cdn = "https://cdn.jsdelivr.net/gh/Owner/Repo@main/clash.yaml"
    gcore = "https://gcore.jsdelivr.net/gh/Owner/Repo@master/clash.yaml"
    chk(canonical_repo(raw) == canonical_repo(cdn) == canonical_repo(gcore) == "gh:owner/repo",
        "三种 GitHub 通道归一为同一仓库")
    chk(canonical_file(raw) == canonical_file(cdn) == canonical_file(gcore),
        "分支名不进文件身份")
    chk(canonical_repo("https://blog.ermao.net/sub/clash") is None, "非 GitHub 地址不猜仓库")
    chk(norm_url("https://A.com/x?token=SECRET") == norm_url("http://a.com/x"),
        "判等忽略协议/host 大小写与 token")
    s = [
        {"id": "a", "name": "A", "enabled": True, "url": raw, "mirrors": []},
        {"id": "b", "name": "B", "enabled": True, "url": cdn, "mirrors": []},
        {"id": "c", "name": "C", "enabled": True,
         "url": "https://raw.githubusercontent.com/Owner/Repo/main/v2ray.txt", "mirrors": []},
        {"id": "d", "name": "D", "enabled": True,
         "url": "https://other.example.com/sub", "mirrors": []},
    ]
    kinds = {(x["kind"], x["a"], x["b"]) for x in link_dups(s)}
    chk(("mirror", "A", "B") in kinds, "镜像源被识别")
    chk(("same-repo", "A", "C") in kinds, "同仓库不同文件被识别")
    chk(not any("D" in (x[1], x[2]) for x in kinds), "无关源不误判")
    keys = {"甲": {f"ss|a{i}:443" for i in range(40)},
            "乙": {f"ss|a{i}:443" for i in range(38)} | {"ss|z:443", "ss|y:443"},
            "丙": {f"vmess|b{i}:443" for i in range(40)}}
    ov = overlap_pairs(keys)
    chk(len(ov) == 1 and ov[0]["a"] in ("甲", "乙") and ov[0]["kind"] == "高度重合",
        f"高度重合被识别（{ov}）")
    chk(not overlap_pairs({"小源": {"ss|a:443"}}), "样本不足不判重合")

    # 2) 恶意 URL / SSRF
    bad = ["http://127.0.0.1/sub", "http://169.254.169.254/latest/meta-data",
           "file:///etc/passwd", "http://user:pass@example.com/x",
           "https://[::1]/x", "http://example.com:22/x", "not-a-url", ""]
    for u in bad:
        try:
            guard_url(u)
        except (netsafe.Blocked, netsafe.Unresolvable):
            continue
        chk(False, f"恶意地址未被拦截：{u}")
    chk(guard_url("https://1.1.1.1/clash.yaml") is True, "公网地址放行")
    try:
        guard_url("https://secret.example.com/x?token=ghp_123456789012345678901234567890123456")
    except netsafe.Unresolvable as e:
        chk("ghp_" not in str(e), "解析失败信息不泄漏 token")

    # 3) 超大响应 / 超时 / 内容类型
    class Stream:
        def __init__(self, chunks, delay=0):
            self.chunks, self.delay, self.i = chunks, delay, 0

        def read(self, n):
            if self.delay:
                time.sleep(self.delay)
            if self.i >= len(self.chunks):
                return b""
            b = self.chunks[self.i]
            self.i += 1
            return b

    try:
        read_limited(Stream([b"x" * 64, b"y" * 64]), max_bytes=100)
    except netsafe.TooLarge:
        pass
    else:
        chk(False, "超过字节上限未拦截")
    try:
        read_limited(Stream([b"x" * 10], delay=0.12), max_bytes=1000, total_timeout=0.05)
    except netsafe.TooSlow:
        pass
    else:
        chk(False, "超过墙钟预算未拦截")
    chk(content_type_ok("text/html; charset=utf-8", b"<!DOCTYPE html><html>")[0] is False,
        "HTML 登录页被拒绝")
    chk(content_type_ok("application/octet-stream", b"proxies:\n")[0] is True,
        "正常二进制订阅放行")
    chk(content_type_ok("", b"ss://")[0] is True, "缺 Content-Type 不误杀")

    # 4) 暂时故障恢复：不删除、按三级状态迁移
    st = "ok"
    for _ in range(1):
        st = score.next_state(st, True, 1)
    chk(st == "watch", "单轮失败 → 观察")
    st = score.next_state(st, True, 2)
    chk(st == "degraded", "两轮失败 → 降级")
    st = score.next_state(st, True, 3)
    chk(st == "isolated", "三轮失败 → 隔离（不是删除）")
    st = score.next_state(st, False, 0)
    chk(st == "recovering", "恢复一轮 → 恢复观察")
    st = score.next_state(st, False, 0)
    chk(st == "ok", "恢复两轮 → 正常")
    chk(score.downgrade("D", 1) == "D", "等级下限不会被降穿")

    # 观察期只压新增源，存量源不受影响
    prev = {"baseline": ["老源"], "sources": {}}
    fake_src = [{"id": "n", "name": "新源", "enabled": True},
                {"id": "o", "name": "老源", "enabled": True}]
    fake_stats = {"sources": [{"name": n, "http": 200, "parsed": 100, "kept": 100,
                               "fails_streak": 0, "checked_at": _stamp()}
                              for n in ("新源", "老源")]}
    mm = per_source(fake_src, fake_stats, {"by_source": {}}, prev)
    chk(mm["sources"]["新源"]["under_observation"] and
        mm["sources"]["新源"]["grade_cap"] == "B", "新源首轮观察期封顶 B")
    chk(not mm["sources"]["老源"]["under_observation"], "存量源不追溯观察期")
    mm2 = per_source(fake_src, fake_stats, {"by_source": {}}, mm)
    mm3 = per_source(fake_src, fake_stats, {"by_source": {}}, mm2)
    chk(not mm3["sources"]["新源"]["under_observation"], "三轮后解除观察期")

    # 观察期必须真的压到评分上：治理文件写了 cap，score 要读得到
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"sources": {"新源": {"under_observation": True, "grade_cap": "B"}}}, f)
        capfile = f.name
    old_path = score.GOV_PATH
    score.GOV_PATH = capfile
    try:
        chk(score.observation_caps().get("新源") == "B" and
            score.min_grade("S", score.observation_caps()["新源"]) == "B",
            "观察期封顶接进评分")
    finally:
        score.GOV_PATH = old_path
        os.remove(capfile)

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


def _print_summary(d):
    print(f"来源治理：{d['whitelist']['enabled']} 个启用源，"
          f"{len(d['duplicates'])} 组同源地址，{len(d['overlap'])} 组内容重合，"
          f"{sum(1 for r in d['sources'].values() if r['under_observation'])} 个观察期")
    for n, r in d["sources"].items():
        vp = f"{r['valid_pct']:.0f}%" if r.get("valid_pct") is not None else "—"
        print(f"  {n:<18} {r['state_label']:<6} 抓取 {str(r.get('fetch_success_pct')):>5}%  "
              f"有效 {vp:>5}  重复 {str(r.get('dup_pct')):>5}%  连败 {r['fails_streak']}")


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    prev = _load(GOV_PATH, None)
    d = build(prev)
    _print_summary(d)
    force = os.environ.get("NODES_FORCE_WRITE") == "1"
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true" or force
    if not in_ci:
        print("  （本地运行，不写入 source_governance.json）")
        return
    netsafe.write_atomic(GOV_PATH, json.dumps(d, ensure_ascii=False, indent=1))
    netsafe.write_atomic(MD_PATH, render_md(d))
    print(f"  已写 {os.path.relpath(GOV_PATH, BASE)} 与 {os.path.relpath(MD_PATH, BASE)}")


if __name__ == "__main__":
    main()
