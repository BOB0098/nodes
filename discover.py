#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动发现：定期检索活跃的节点项目，生成候选队列。

边界（任务书 §4 要求）：
  · 只写 data/candidates.json，**绝不**改 sources.yaml；
  · 候选必须再过 screen.py 四关 + 人工置 enabled，才可能上线；
  · 已在本库或已在队列里的仓库不重复进队。

用法:
    python3 discover.py                 # 发现并写队列（CI 每周跑一次）
    python3 discover.py --dry-run       # 只打印，不落盘
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import sources_io  # noqa: E402
import netsafe  # noqa: E402

OUT = os.path.join(BASE, "data", "candidates.json")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""

# 检索式：覆盖「Clash 订阅」「v2ray 免费节点」「sing-box 配置」三类主流命名
QUERIES = [
    "clash subscribers in:name,description,readme",
    "free v2ray nodes in:name,description,readme",
    "sing-box config in:name,description,readme",
    "免费节点 订阅 in:name,description,readme",
]
MIN_STARS = 30
MAX_AGE_DAYS = 14

# 文件 → 订阅格式的推断（只用于给审核排序，最终以 screen.py 实际解析为准）
FORMAT_HINT = [
    (re.compile(r"clash.*\.(ya?ml)$", re.I), "clash"),
    (re.compile(r"\.(ya?ml)$", re.I), "clash"),
    (re.compile(r"sing.*\.json$", re.I), "singbox-json"),
    (re.compile(r"base64", re.I), "v2ray-base64"),
    (re.compile(r"\.txt$", re.I), "unknown"),
]


def build_candidate_url(repo, branch, path):
    """Build a reviewable GitHub raw URL without letting path text add URL parts."""
    if not isinstance(repo, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid GitHub repository name")
    if not isinstance(branch, str) or not isinstance(path, str):
        raise ValueError("branch and path must be strings")

    branch_parts = branch.split("/")
    path_parts = path.split("/")
    if (not branch or not path
            or any(part in ("", ".", "..") for part in branch_parts + path_parts)):
        raise ValueError("invalid GitHub branch or file path")

    branch_path = "/".join(urllib.parse.quote(part, safe="") for part in branch_parts)
    file_path = "/".join(urllib.parse.quote(part, safe="") for part in path_parts)
    url = f"https://raw.githubusercontent.com/{repo}/{branch_path}/{file_path}"
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "raw.githubusercontent.com"
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError("candidate URL is not a credential-free GitHub raw URL")
    return url


def api(path):
    url = path if path.startswith("http") else "https://api.github.com" + path
    hdr = {"User-Agent": "nodes-discover", "Accept": "application/vnd.github+json"}
    if TOKEN:
        hdr["Authorization"] = "Bearer " + TOKEN
    for attempt in range(2):
        try:
            # 搜索接口的 URL 由本模块拼装（词表在代码里），正常不会越界；
            # 但 GitHub 返回的 next 链接是外部内容，万一被拿来当 path 传进来，
            # 没有 check_url 就等于把出网目标交给响应体决定。
            body, response_headers = netsafe.open_url(
                url, timeout=25, max_bytes=8 << 20, headers=hdr,
                with_headers=True)
            return json.loads(body), response_headers
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                time.sleep(15)          # 触发搜索限流，等一档
                continue
            return None, {}
        except Exception:
            time.sleep(3)
    return None, {}


def known_repos():
    """已在册的仓库 + 已有队列，避免重复进队。"""
    out = set()
    try:
        _, srcs, _ = sources_io.load()
        for s in srcs:
            u = s.get("url") or ""
            m = re.search(r"https?://(?:raw\.githubusercontent\.com|cdn\.jsdelivr\.net/gh)/([^/]+/[^/]+)", u)
            if m:
                out.add(m.group(1).lower())
    except FileNotFoundError:
        # 首次运行尚未建立 sources.yaml 时，按空登记表兼容启动。
        pass
    try:
        with open(OUT, encoding="utf-8") as f:
            q = json.load(f)
        for c in q.get("candidates") or []:
            out.add(c["repo"].lower())
    except Exception:
        pass
    return out


def looks_like_subscription(url, limit=120_000):
    """内容嗅探：必须真的像订阅，否则只是"文件名像"。

    判定：含 URI 的 `://`、或含 Clash 的 `proxies:`、或整体 base64 解出来含 `://`。
    这一步把 zsh(subst.c)、v2rayA(.nfpm.yaml) 这类误报挡在队列之外。
    """
    cand_urls = [url]
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)", url)
    if m:
        o, r_, b, path = m.groups()
        cand_urls.append(f"https://cdn.jsdelivr.net/gh/{o}/{r_}@{b}/{path}")
        cand_urls.append(f"https://gcore.jsdelivr.net/gh/{o}/{r_}@{b}/{path}")
    data = None
    for u in cand_urls:
        try:
            # 候选仓库里的文件属于不可信内容。截断式读取（read(limit)）
            # 本身不会吃满内存，但协议与地址仍然要卡。
            with netsafe.open_response(
                    u, timeout=10, headers={"User-Agent": "nodes-discover"}) as resp:
                data = resp.read(limit)
            break
        except Exception:
            continue
    if data is None:
        return None            # 全通道取不到就不进队，不猜
    t = data.decode("utf-8", "ignore")
    # 单个 "://" 太弱：打包描述、README 里也有官网链接。
    # 真正的订阅要么成批 URI 行，要么是 Clash 的 proxies: 段，要么整体 base64。
    uri_lines = sum(1 for l in t.splitlines() if "://" in l and not l.lstrip().startswith("#"))
    if uri_lines >= 5:
        return True
    if re.search(r"^proxies\s*:\s*$", t, re.M) and re.search(r"^\s*-\s*(name|type)\s*:", t, re.M):
        return True
    head = t.strip()[:8000]
    if head and re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", head):
        try:
            dec = base64.b64decode(head + "=" * (-len(head) % 4)).decode("utf-8", "ignore")
            return "://" in dec
        except Exception:
            return False
    return False


def pick_file(full, branch):
    """从仓库树里挑最可能是订阅的文件：优先名字匹配 + 体积合理。"""
    t, _ = api(f"/repos/{full}/git/trees/{branch}?recursive=1")
    if not t or "tree" not in t:
        return None
    best = None
    for f in t["tree"]:
        if f.get("type") != "blob":
            continue
        p, sz = f["path"], f.get("size") or 0
        if sz < 3000 or sz > 6_000_000:
            continue
        if "/vendor/" in p or "/.github/" in p or p.startswith(("docs/", "test")):
            continue
        # 源码/文档/打包描述不是订阅。加这层是因为首轮发现了 zsh 的 subst.c
        # 和 v2rayA 的 .nfpm.yaml —— 文件名匹配「sub / v2ray」但内容完全无关
        if re.search(r"\.(c|h|go|py|js|ts|rs|java|rb|php|md|rst|png|jpg|svg|zip|gz|exe|sh)$", p, re.I):
            continue
        if re.search(r"(^|/)(install|deploy|docker|systemd|packaging|dist)/", p, re.I):
            continue
        base = os.path.basename(p).lower()
        score = 0
        if re.search(r"(sub|node|clash|v2ray|sing|proxy|merge|collect)", base):
            score += 2
        if p.count("/") <= 1:
            score += 1
        fmt = "unknown"
        for rx, name in FORMAT_HINT:
            if rx.search(base):
                fmt = name
                break
        if fmt in ("clash", "singbox-json"):
            score += 1
        if score >= 3 and (best is None or score > best[0] or
                           (score == best[0] and sz > best[1]["size"])):
            best = (score, {"path": p, "size": sz, "format": fmt})
    return best[1] if best else None


def main():
    dry = "--dry-run" in sys.argv
    since = (date.today() - timedelta(days=MAX_AGE_DAYS)).isoformat()
    have = known_repos()
    found, seen = [], set()

    for q in QUERIES:
        query = f"{q} stars:>={MIN_STARS} pushed:>={since}"
        path = "/search/repositories?" + urllib.parse.urlencode(
            {"q": query, "sort": "stars", "order": "desc", "per_page": 10})
        d, hdr = api(path)
        if not d or "items" not in d:
            print(f"  ✘ 检索失败: {q}", file=sys.stderr)
            continue
        print(f"  检索「{q}」→ {d.get('total_count')} 命中，取前 {len(d['items'])}", file=sys.stderr)
        for it in d["items"][:5]:   # 每次只深挖 8 个，避免逐个查树把整个任务拖死
            full = it["full_name"]
            if full.lower() in have or full.lower() in seen:
                continue
            seen.add(full.lower())
            f = pick_file(full, it.get("default_branch") or "main")
            if not f:
                continue
            try:
                raw_url = build_candidate_url(
                    full, it.get("default_branch") or "main", f["path"])
            except (KeyError, TypeError, ValueError):
                continue
            if looks_like_subscription(raw_url) is not True:
                continue
            found.append({
                "repo": full,
                "stars": it.get("stargazers_count"),
                "license": (it.get("license") or {}).get("spdx_id"),
                "pushed_at": (it.get("pushed_at") or "")[:10],
                "desc": (it.get("description") or "")[:120],
                "url": raw_url,
                "format_guess": f["format"],
                "size": f["size"],
                "status": "queued",
                "first_seen": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"),
            })
        print(f"    → 本轮新增 {len(found)} 个", file=sys.stderr)
        time.sleep(2)   # 搜索接口限流较紧

    found.sort(key=lambda x: -(x["stars"] or 0))
    print(f"\n新候选 {len(found)} 个（已排除在册与重复）：", file=sys.stderr)
    for c in found[:15]:
        print(f"  ⭐{c['stars']:<6} {c['repo']:<40} {c['format_guess']:<12} "
              f"{netsafe.mask_url(c['url'])[:70]}",
              file=sys.stderr)
    if dry:
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    old = []
    try:
        old = json.load(open(OUT, encoding="utf-8")).get("candidates") or []
    except Exception:
        pass
    # 保留已在队列里的（含已审核结论），只追加新的
    have2 = {c["repo"].lower() for c in old}
    merged = old + [c for c in found if c["repo"].lower() not in have2]
    json.dump({
        "generated_at": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "queries": QUERIES, "min_stars": MIN_STARS, "max_age_days": MAX_AGE_DAYS,
        "note": "候选队列。仅经 screen.py 审核 + 人工置 enabled 后才会上线。",
        "candidates": merged,
    }, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n已写 {OUT}（队列共 {len(merged)} 条）", file=sys.stderr)


if __name__ == "__main__":
    main()
