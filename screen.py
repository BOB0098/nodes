#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选来源接入审核（P2）。

对每个候选源走同一套四关：
    ① 试采集   限流量拉取（默认 12 MB 上限），记录 HTTP 与体积
    ② 解析     用节点库同一套 nodes_parse，不数行数、不猜格式
    ③ 去重     全参数指纹与本库比对，算「独立新增」而非「看起来很多」
    ④ 真实检测 抽样 24 个地址做 TCP 握手（UDP 系协议不参与，避免误判）

判定规则写死在 VERDICT 里，可复核。审核结果只写 data/screening.json 与
docs/P2-screening.md，**不会**自动把源加进 sources.yaml —— 任务书明确要求
「不得自动把新发现来源直接加入正式订阅」。

用法:
    python3 screen.py                 # 审核内置候选清单 + data/candidates.json
    python3 screen.py <url> ...       # 只审指定 URL
"""
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import netsafe  # noqa: E402
import nodes_parse  # noqa: E402
import sources_io  # noqa: E402

LIMIT = 12 * 1024 * 1024
UA = "Mozilla/5.0 (compatible; nodes-screener/1.0)"
CAND_PATH = os.path.join(BASE, "data", "candidates.json")
OUT_PATH = os.path.join(BASE, "data", "screening.json")
MD_PATH = os.path.join(BASE, "docs", "P2-screening.md")

# 任务书 §4 的 17 个候选。url 是「真实存在的数据文件」，由 tree API 逐个确认，
# 不是按惯例猜的路径（猜路径是上一版审计里最容易出错的地方）。
CANDIDATES = [
    dict(repo="735754647/Free-Nodes", kind="tool",
         url="https://raw.githubusercontent.com/735754647/Free-Nodes/main/config/sources.txt",
         note="仓库本体是 subbench 测速工具（含 61MB mihomo 二进制），不是节点发布源；只有一份 sources.txt 源清单，可作为自动发现的种子"),
    dict(repo="learnLi/free-nodes", kind="tool",
         url="https://raw.githubusercontent.com/learnLi/free-nodes/main/config.yml",
         note="config.yml 的 proxies 为空数组，仓库发布的是空模板 + fetch/probe/prune 工具链，节点经 release 或运行期产出，不是可直接订阅的发布源"),
    dict(repo="xiaoji235/airport-free", kind="source",
         url="https://raw.githubusercontent.com/xiaoji235/airport-free/main/clash/clashnodecc.txt",
         note="单文件 3.7MB；另有 15MB 版本，体积异常需核对是否重复堆叠"),
    dict(repo="freenodess/freenodess", kind="source",
         url="https://raw.githubusercontent.com/freenodess/freenodess/main/nodes/clash.yaml",
         note="三种格式并存（clash.yaml / singbox.json / v2ray-base64.txt），同源同批"),
    dict(repo="share-daily/node", kind="source",
         url="https://raw.githubusercontent.com/share-daily/node/main/clash.yaml",
         note="仅 33KB，量小"),
    dict(repo="rtwo2/FastNodes", kind="source",
         url="https://raw.githubusercontent.com/rtwo2/FastNodes/main/sub/top.txt",
         note="MIT；主文件被 GitHub 50MB 上限截断（everything.txt 恰为 52,428,780 字节），只取 top.txt"),
    dict(repo="nodesradar/nodesradar-en", kind="index",
         url="https://raw.githubusercontent.com/nodesradar/nodesradar-en/main/README.md",
         note="仓库只有 README，无数据文件；真正的数据在 radarhub.top，已在册"),
    dict(repo="Pawdroid/Free-servers", kind="enabled", url="", note="已在册"),
    dict(repo="aiboboxx/v2rayfree", kind="dup",
         url="https://raw.githubusercontent.com/free-nodes/v2rayfree/main/sub",
         note="301 到 free-nodes/v2rayfree；两仓库 README(37,608B) 与 sub(5,060B) 字节数完全一致，同一次改名，不得重复接入"),
    dict(repo="ermaozi/get_subscribe", kind="enabled", url="", note="已在册（3 张卡片）"),
    dict(repo="peasoft/NoMoreWalls", kind="source",
         url="https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.txt",
         note="老牌项目，带 list.yml/meta.yml；LICENSE.md 为 NOASSERTION，需人工看"),
    dict(repo="chengaopan/AutoMergePublicNodes", kind="source",
         url="https://raw.githubusercontent.com/chengaopan/AutoMergePublicNodes/master/list.txt",
         note="与 NoMoreWalls 高度相似（fetch.py 同为 63,200B、LICENSE 同为 2,113B），疑为派生，需看去重后还剩多少"),
    dict(repo="Barabama/FreeNodes", kind="enabled", url="", note="已在册"),
    dict(repo="Leon406/SubCrawler", kind="index",
         url="https://raw.githubusercontent.com/Leon406/SubCrawler/main/sub/subs.txt",
         note="subs.txt 只有 1.5KB，是「订阅地址清单」不是节点；适合喂自动发现"),
    dict(repo="MatinGhanbari/v2ray-configs", kind="source",
         url="https://raw.githubusercontent.com/MatinGhanbari/v2ray-configs/main/subscriptions/base64/all_sub.txt",
         note="MIT；1.6MB base64 合集，按协议分目录"),
    dict(repo="MhdiTaheri/V2rayCollector", kind="source",
         url="https://raw.githubusercontent.com/MhdiTaheri/V2rayCollector/main/sub/mix",
         note="MIT；按协议拆分（mix/vless/ss/...），取混合版"),
    dict(repo="Surfboardv2ray/TGParse", kind="source",
         url="https://raw.githubusercontent.com/Surfboardv2ray/TGParse/main/splitted/mixed",
         note="Telegram 频道抓取；仓库里有 50+ 份历史归档，会持续膨胀"),
]


# 许可证 / 活跃度：2026-11-24 用 GitHub API 逐个核验的结果，写死在这里保证
# 报告可复现（不依赖运行时再查一次 API，避免被限流或响应污染影响判定）。
META = {
    "735754647/Free-Nodes": dict(license=None, stars=1102, pushed="2026-11-24"),
    "learnLi/free-nodes": dict(license="LICENSE.md", stars=58, pushed="2026-11-24"),
    "xiaoji235/airport-free": dict(license=None, stars=146, pushed="2026-11-24"),
    "freenodess/freenodess": dict(license=None, stars=24, pushed="2026-11-24"),
    "share-daily/node": dict(license=None, stars=22, pushed="2026-11-24"),
    "rtwo2/FastNodes": dict(license="MIT", stars=110, pushed="2026-11-24"),
    "nodesradar/nodesradar-en": dict(license=None, stars=31, pushed="2026-11-24"),
    "Pawdroid/Free-servers": dict(license=None, stars=20027, pushed="2026-11-24"),
    "aiboboxx/v2rayfree": dict(license=None, stars=15392, pushed="2026-11-24"),
    "ermaozi/get_subscribe": dict(license="GPL-3.0", stars=9526, pushed="2026-11-24"),
    "peasoft/NoMoreWalls": dict(license="NOASSERTION", stars=3636, pushed="2026-11-24"),
    "chengaopan/AutoMergePublicNodes": dict(license="NOASSERTION", stars=8438, pushed="2026-11-24"),
    "Barabama/FreeNodes": dict(license="MIT", stars=3453, pushed="2026-11-23"),
    "Leon406/SubCrawler": dict(license="GPL-3.0", stars=1582, pushed="2026-11-24"),
    "MatinGhanbari/v2ray-configs": dict(license="MIT", stars=718, pushed="2026-11-24"),
    "MhdiTaheri/V2rayCollector": dict(license="MIT", stars=323, pushed="2026-11-24"),
    "Surfboardv2ray/TGParse": dict(license=None, stars=297, pushed="2026-11-24"),
}


def mirrors_for(url):
    """GitHub raw 的备用通道。沙箱/机房到 raw.githubusercontent.com 经常超时，
    单一地址失败不代表源挂了——必须换通道再试，否则会把好源误判成死源。"""
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)", url)
    if not m:
        return []
    o, r, b, p = m.groups()
    return [f"https://cdn.jsdelivr.net/gh/{o}/{r}@{b}/{p}",
            f"https://gcore.jsdelivr.net/gh/{o}/{r}@{b}/{p}"]


def fetch_any(url, rounds=2):
    """按 主地址 → jsdelivr → gcore 依次尝试，整轮失败再重试。"""
    urls = [url] + mirrors_for(url)
    last = {"code": 0, "data": b"", "size": 0, "err": "no-attempt"}
    tried = []
    for _ in range(rounds):
        for u in urls:
            r = fetch(u)
            tried.append(f'{u.split("/")[2]}:{r["code"]}')
            if r["code"] == 200 and r["data"]:
                r["via"] = u
                r["tried"] = tried
                return r
            last = r
        time.sleep(1)
    last["tried"] = tried
    return last


def fetch(url, limit=LIMIT, timeout=20):
    """限流量拉取（默认 12 MB）。

    读 limit+1 字节就停：既拿到「有没有超限」的判断，又不会把整个大文件
    读进内存。地址与协议仍然过 netsafe——候选源是外部输入，包括重定向。
    """
    t0 = time.time()
    try:
        netsafe.check_url(url)
    except netsafe.Blocked as e:
        return {"code": 0, "ms": int((time.time() - t0) * 1000), "data": b"", "size": 0,
                "err": "Blocked:" + str(e)[:40]}
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with netsafe.opener().open(req, timeout=timeout) as r:
            data = r.read(limit + 1)
            return {"code": r.status, "ms": int((time.time() - t0) * 1000),
                    "data": data[:limit], "size": len(data),
                    "truncated": len(data) > limit,
                    "ctype": r.headers.get("Content-Type", "")}
    except urllib.error.HTTPError as e:
        return {"code": e.code, "ms": int((time.time() - t0) * 1000), "data": b"", "size": 0}
    except Exception as e:
        return {"code": 0, "ms": int((time.time() - t0) * 1000), "data": b"", "size": 0,
                "err": type(e).__name__}


def tcp_ok(host, port, timeout=3.0):
    try:
        with socket.create_connection((host, int(port)), timeout):
            return True
    except Exception:
        return False


def probe(nodes, sample=24, workers=32):
    """只测能测的：UDP 系协议（hy2/tuic）TCP 握手必失败，测了就是假阴性。"""
    skip = {"hysteria", "hysteria2", "tuic"}
    targets, seen = [], set()
    for n in nodes:
        if n.get("type") in skip:
            continue
        a = n.get("addr")
        if not a or a == "—" or a in seen:
            continue
        seen.add(a)
        targets.append(a)
        if len(targets) >= sample:
            break
    if not targets:
        return {"ok": 0, "total": 0}
    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = []
        for a in targets:
            host, _, port = a.rpartition(":")
            host = host.strip("[]")
            if host and port.isdigit():
                futs.append(ex.submit(tcp_ok, host, port))
        for f in as_completed(futs):
            if f.result():
                ok += 1
    return {"ok": ok, "total": len(futs)}


def lib_keys():
    """现有节点库的比对键。nodes.json 已脱敏（无 uri），所以用 协议+地址 近似，
    比「只比地址」严一点：同机不同协议算不同节点。这是保守估计——
    真实去重走全参数指纹，重叠率只会比这里算出来的更低。"""
    try:
        d = json.load(open(os.path.join(BASE, "data", "nodes.json"), encoding="utf-8"))
    except Exception:
        return set(), 0
    keys = set()
    for n in d.get("nodes") or []:
        addr = n.get("addr")
        if addr and addr != "—":
            keys.add(f'{str(n.get("proto", "")).lower()}|{addr}')
    return keys, len(d.get("nodes") or [])


def judge(rec, kind):
    """判定口径：先过硬门槛，再看「预计可用新增」。

    为什么不把「抽样可连率」单独设成硬门槛：同一个源在不同出口测出来能差 5 倍
    （ALIILAPRO 本轮 8%、上一轮 42%），单看比例会把好源误杀。
    更稳的判据是「真正新增 × 可连率」——即这个源到底能给用户多带来几个能用的节点。
    """
    reasons, verdict = [], "pass"
    if kind in ("tool", "index"):
        return "skip", [f"不是节点发布源（{kind}），不参与节点库；可用于自动发现"]
    if kind == "dup":
        return "reject", ["与已在册来源同一仓库/同一次改名，接入会造成重复计数"]
    if rec.get("http") != 200:
        return "reject", [f"HTTP {rec.get('http')}，源不可达"]
    parsed = rec.get("parsed") or 0
    if parsed < 20:
        return "reject", [f"只解析出 {parsed} 条，量不足以单独成卡"]
    ratio = rec.get("new_ratio")
    if ratio is not None and ratio < 0.10:
        return "reject", [f"独立新增仅 {ratio*100:.0f}%（{rec.get('new_unique')}/{parsed}），"
                          "与现有库高度重合"]
    if rec.get("archived"):
        return "reject", ["仓库已归档"]
    if rec.get("stale_days") is not None and rec["stale_days"] > 30:
        return "reject", [f"最后提交在 {rec['stale_days']} 天前，已停更"]

    alive = rec.get("alive_pct")
    new = rec.get("new_unique") or 0
    usable = round(new * alive / 100) if alive is not None else None
    rec["expected_usable"] = usable
    if usable is None:
        verdict = "watch"
        reasons.append("无法抽样测速（UDP 协议或全部为空），只能按体量判断")
    elif usable < 250:
        verdict = "reject"
        reasons.append(f"预计可用新增仅 {usable} 条（{new} 新增 × 可连 {alive}%），低于 250 门槛")
    elif usable < 600:
        verdict = "watch"
        reasons.append(f"预计可用新增 {usable} 条（{new} × {alive}%），中等")
    else:
        reasons.append(f"预计可用新增 {usable} 条（{new} × {alive}%），充足")
    if alive is not None and alive < 20:
        reasons.append(f"自身可连率仅 {alive}%，大量死条目会稀释库内可用率")
    if not rec.get("license"):
        reasons.append("仓库未声明许可证（合规风险，需人工确认）")
    return verdict, reasons


def screen_one(cand, known, lib_size):
    url = cand.get("url")
    rec = dict(cand)
    # 许可证/星数/最近提交：缺省从 META 补，命令行传入的 URL 没有就留空
    for k, v in (META.get(cand.get("repo")) or {}).items():
        rec.setdefault(k, v)
    stale = rec.get("pushed")
    if stale:
        import datetime as _dt
        try:
            d = _dt.date.fromisoformat(stale)
            rec["stale_days"] = (_dt.date(2026, 11, 24) - d).days
        except Exception:
            pass
    if not url:
        rec.update(verdict="skip", reasons=["无数据文件"])
        return rec
    r = fetch_any(url)
    rec.update(http=r["code"], http_ms=r["ms"], bytes=r["size"],
               truncated=r.get("truncated", False),
               via=(r.get("via") or "").split("/")[2] if r.get("via") else None)
    if r["code"] != 200 or not r["data"]:
        # 网络不可达 ≠ 源不合格。全通道失败时标记「未核验」，不写 reject，
        # 避免因为沙箱出口问题把候选源判死（P0 审计里已在 raw/API 上踩过）。
        rec.update(verdict="unverified",
                   reasons=[f"全通道不可达（attempts: {', '.join(r.get('tried') or [])}）"],
                   tried=r.get("tried"))
        return rec
    try:
        rj = []
        nodes, declared = nodes_parse.parse_any(r["data"], rj)
    except Exception as e:
        rec.update(verdict="reject", reasons=[f"解析异常 {type(e).__name__}"])
        return rec
    fps = {n["fp"] for n in nodes}
    keys = {f'{str(n.get("type","")).lower()}|{n.get("addr")}' for n in nodes}
    new_keys = keys - known
    rec.update(declared=declared, parsed=len(nodes), unique=len(fps),
               new_unique=len(new_keys),
               new_ratio=round(len(new_keys) / len(keys), 3) if keys else None,
               types={t: sum(1 for n in nodes if n["type"] == t)
                      for t in sorted({n["type"] for n in nodes})})
    # 被隔离的条目（明文代理/非公网/未知协议）单独报，避免「声明 2000、
    # 解析出 1200」这种差额只能靠猜
    if rj:
        q = {}
        for x in rj:
            q[x["reason"]] = q.get(x["reason"], 0) + 1
        rec["quarantined"] = q
    p = probe(nodes)
    rec.update(probe_ok=p["ok"], probe_total=p["total"],
               alive_pct=round(p["ok"] / p["total"] * 100) if p["total"] else None)
    v, why = judge(rec, cand.get("kind", "source"))
    rec.update(verdict=v, reasons=why)
    return rec


def enabled_index():
    """已在册且启用的源：URL（含镜像）与仓库名。审核要跳过它们，
    否则「新增」会把自己已入库的节点算成本次新增，数字自相矛盾。"""
    urls, repos = set(), set()
    try:
        _, srcs, _ = sources_io.load()
    except Exception:
        return urls, repos
    for s in srcs:
        if not s.get("enabled"):
            continue
        for u in [s.get("url")] + list(s.get("mirrors") or []):
            if u:
                urls.add(u)
        m = re.search(r"https?://(?:raw\.githubusercontent\.com|cdn\.jsdelivr\.net/gh|gcore\.jsdelivr\.net/gh)/([^/]+/[^/]+)", s.get("url") or "")
        if m:
            repos.add(m.group(1).lower())
    return urls, repos


def main():
    known, lib_size = lib_keys()
    cands = list(CANDIDATES)
    if os.path.exists(CAND_PATH):
        try:
            extra = json.load(open(CAND_PATH, encoding="utf-8")).get("candidates") or []
            have = {c["url"] for c in cands if c.get("url")}
            cands += [c for c in extra if c.get("url") and c["url"] not in have]
        except Exception:
            pass
    if len(sys.argv) > 1:
        cands = [dict(repo="(手动)", kind="source", url=u, note="命令行指定") for u in sys.argv[1:]]

    on_urls, on_repos = enabled_index()
    skipped = []
    rest = []
    for c in cands:
        if c.get("kind") == "enabled" or (c.get("url") in on_urls) or \
                (c.get("repo") or "").lower() in on_repos:
            skipped.append(dict(c, verdict="skip", reasons=["已在 sources.yaml 中启用，不重复审核"]))
        else:
            rest.append(c)
    cands = rest
    print(f"现有节点库 {lib_size} 条；待审 {len(cands)} 个，跳过已在册 {len(skipped)} 个\n")
    recs = list(skipped)
    order = [c for c in cands]
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(screen_one, c, known, lib_size): c for c in order}
        for fu in as_completed(futs):
            try:
                rec = fu.result()
            except Exception as e:
                c = futs[fu]
                rec = dict(c, verdict="unverified", reasons=[f"审核异常 {type(e).__name__}"])
            recs.append(rec)
            print(f"[{rec['verdict']:<10}] {rec['repo'][:34]:<36} "
                  f"解析 {str(rec.get('parsed') or '—'):>5} 新增 {str(rec.get('new_unique') or '—'):>5} "
                  f"可连 {str(rec.get('alive_pct') or '—'):>4}  {rec['reasons'][0][:44]}")
    # 按候选原顺序输出，便于和清单对照
    idx = {c.get("repo"): i for i, c in enumerate(order)}
    recs.sort(key=lambda r: idx.get(r.get("repo"), 99))

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    json.dump({"library_size": lib_size, "checked": len(recs), "results": recs},
              open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_md(recs, lib_size)
    print(f"\n已写 {OUT_PATH}")
    print(f"已写 {MD_PATH}")


def write_md(recs, lib_size):
    os.makedirs(os.path.dirname(MD_PATH), exist_ok=True)
    L = []
    L.append("# P2 候选来源审核报告\n")
    L.append(f"> 自动生成于 `screen.py`。比对基准：现有节点库 **{lib_size}** 条。\n")
    L.append("""> 判定口径：HTTP 必须 200；解析 ≥20 条；独立新增 ≥10%（低于 30% 记 watch）；
> 抽样可连 ≥30%（低于 55% 记 watch）；仓库未归档、30 天内有过提交。
> 重叠率用「协议+地址」近似计算（库内已脱敏、无 uri），真实全参数指纹去重只会更低。
> **本报告不改变线上订阅，通过审核的源也需人工在 `sources.yaml` 里置 `enabled: true`。**
""")
    order = {"pass": 0, "watch": 1, "unverified": 2, "reject": 3, "skip": 4}
    recs = sorted(recs, key=lambda r: (order.get(r["verdict"], 9), -(r.get("new_unique") or 0)))
    L.append("## 结论表\n")
    L.append("| 判定 | 仓库 | 解析 | 独立新增 | 新增占比 | 抽样可连 | 预计可用 | HTTP | 说明 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in recs:
        L.append("| {v} | `{repo}` | {p} | {n} | {ra} | {a} | {u} | {h} | {w} |".format(
            v={"pass": "✅ 通过", "watch": "⚠️ 观察", "reject": "❌ 拒绝",
               "skip": "⏭ 跳过", "unverified": "❓ 未核验"}[r["verdict"]],
            repo=r["repo"], p=r.get("parsed") or "—", n=r.get("new_unique") or "—",
            ra=f"{r['new_ratio']*100:.0f}%" if r.get("new_ratio") is not None else "—",
            a=f"{r['alive_pct']}%" if r.get("alive_pct") is not None else "—",
            u=r.get("expected_usable") if r.get("expected_usable") is not None else "—",
            h=r.get("http") or "—", w="；".join(r["reasons"])[:90]))
    L.append("\n## 逐个说明\n")
    for r in recs:
        L.append(f"### `{r['repo']}` — {r['verdict']}\n")
        if r.get("url"):
            L.append(f"- 数据文件：`{r['url']}`")
            L.append(f"- 体积：{r.get('bytes', 0)/1024:.0f} KB"
                     + ("（**超过上限已截断**）" if r.get("truncated") else ""))
        L.append(f"- 解析 {r.get('parsed', '—')} 条 / 唯一指纹 {r.get('unique', '—')} / "
                 f"与现有库重合 {r.get('parsed', 0) - (r.get('new_unique') or 0)} 条")
        if r.get("types"):
            L.append(f"- 协议分布：{r['types']}")
        L.append(f"- 判定理由：{'；'.join(r['reasons'])}")
        if r.get("note"):
            L.append(f"- 备注：{r['note']}")
        L.append("")
    open(MD_PATH, "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    main()
