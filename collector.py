#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
免费节点订阅采集器
页面主体 = 各仓库官方发布的订阅地址（小白可直接复制使用）
采集用于统计每个源的节点条数、抽样连通率和今日增减

页面模板在 template.html，本文件只负责采集与数据渲染

用法:
    python3 collector.py            # 联网采集
    python3 collector.py --cache    # 用本地缓存(调试)
    python3 collector.py --render   # 只按 data/nodes.json 重渲染页面，不联网
"""
import base64, hashlib, json, os, re, socket, sys, time, urllib.error, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
# 缓存目录默认在仓库上一级。容器里上一级是 /（不可写），用 NODES_CACHE 覆盖
CACHE = os.environ.get("NODES_CACHE") or os.path.join(os.path.dirname(BASE), ".nodes-cache")
OUT_DATA = os.path.join(BASE, "data")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(OUT_DATA, exist_ok=True)

# 数据结构版本，写进 nodes.json，消费方（页面/脚本/第三方）据此判断怎么读。
# 2 = P17：卡片字段新增 rank_real/rank_has_real，源评分改口径。
SCHEMA = 2

sys.path.insert(0, BASE)
import nodes_parse  # noqa: E402  同目录模块，负责把 YAML/JSON/URI 统一解析成节点
import lifecycle  # noqa: E402  节点首次/最近出现时间（P5）
import netsafe  # noqa: E402  出网边界与日志脱敏（P13）
import score as scoring  # noqa: E402  来源评分与淘汰状态机（P4）
import sources_io  # noqa: E402  来源登记表读取（sources.yaml）

# ── 来源清单：唯一事实来源是 sources.yaml ──────────────────────────
# 旧实现里 GROUPS(14 张卡片) 与 SOURCES(9 个采集项) 是两张互不对应的表，
# 7 张卡片拿不到统计、2 个源采了却不展示。现在都从 YAML 生成。
GROUPS, SOURCES_ALL, GROUP_META = sources_io.load()
SOURCES = sources_io.collect_targets(SOURCES_ALL)
# 相同主 URL 共享一次抓取结果；镜像按登记顺序合并并去重。
SOURCE_GROUPS = sources_io.collect_groups(SOURCES_ALL)
URLS_BY_URL = {g["url"]: g["urls"] for g in SOURCE_GROUPS}
SOURCE_NAMES_BY_URL = {g["url"]: g["names"] for g in SOURCE_GROUPS}
SOURCE_IDS_BY_URL = {g["url"]: g["ids"] for g in SOURCE_GROUPS}
SRC_NAME_BY_ID = {s["id"]: s["name"] for s in SOURCES_ALL if s.get("enabled")}
SRC_ID_BY_URL = {g["url"]: g["ids"][0] for g in SOURCE_GROUPS}
# 旧调用仍可读取单个代表源；完整归属以 SRC_NAMES/IDS_BY_URL 为准。
SRC_NAME_BY_URL = {g["url"]: g["names"][0] for g in SOURCE_GROUPS}

PROTO_LABEL = {"vmess": "VMess", "vless": "VLESS", "ss": "Shadowsocks", "ssr": "SSR",
               "trojan": "Trojan", "hysteria": "Hysteria", "hysteria2": "Hysteria2", "hy2": "Hysteria2",
               "tuic": "TUIC", "socks5": "Socks5", "anytls": "AnyTLS", "http": "HTTP"}

REGIONS = [("香港", "香港"), ("HK", "香港"), ("Hong Kong", "香港"), ("台湾", "中国台湾"), ("TW", "中国台湾"),
           ("日本", "日本"), ("JP", "日本"), ("Japan", "日本"), ("新加坡", "新加坡"), ("SG", "新加坡"),
           ("美国", "美国"), ("US", "美国"), ("United States", "美国"), ("韩国", "韩国"), ("KR", "韩国"),
           ("英国", "英国"), ("GB", "英国"), ("德国", "德国"), ("DE", "德国"), ("Germany", "德国"),
           ("法国", "法国"), ("FR", "法国"), ("荷兰", "荷兰"), ("NL", "荷兰"), ("土耳其", "土耳其"),
           ("TR", "土耳其"), ("俄罗斯", "俄罗斯"), ("RU", "俄罗斯"), ("印度", "印度"), ("IN", "印度"),
           ("加拿大", "加拿大"), ("CA", "加拿大"), ("澳大利亚", "澳大利亚"), ("AU", "澳大利亚"),
           ("马来西亚", "马来西亚"), ("MY", "马来西亚"), ("泰国", "泰国"), ("TH", "泰国"),
           ("越南", "越南"), ("VN", "越南"), ("菲律宾", "菲律宾"), ("PH", "菲律宾"), ("印尼", "印尼"),
           ("ID", "印尼"), ("巴西", "巴西"), ("BR", "巴西"), ("意大利", "意大利"), ("IT", "意大利"),
           ("西班牙", "西班牙"), ("ES", "西班牙"), ("波兰", "波兰"), ("PL", "波兰"), ("瑞典", "瑞典"),
           ("SE", "瑞典"), ("瑞士", "瑞士"), ("CH", "瑞士"), ("乌克兰", "乌克兰"), ("UA", "乌克兰"),
           ("阿联酋", "阿联酋"), ("AE", "阿联酋"), ("伊朗", "伊朗"), ("IR", "伊朗"), ("埃及", "埃及"),
           ("墨西哥", "墨西哥"), ("MX", "墨西哥"), ("芬兰", "芬兰"), ("FI", "芬兰"),
           ("罗马尼亚", "罗马尼亚"), ("RO", "罗马尼亚"), ("白俄罗斯", "白俄罗斯"), ("BY", "白俄罗斯")]


def wjson(path, obj, indent=None):
    """JSON 原子写。indent=1 让 diff 按行走，只改一行不会整文件重写。"""
    netsafe.write_atomic(path, json.dumps(obj, ensure_ascii=False, indent=indent))


def fetch(urls, name, use_cache=False, budget=150):
    """拉一个源的原文。

    出网统一走 netsafe：协议只留 HTTP/HTTPS、目标必须是公网地址、每跳重定向
    都重查、单源 12MB 上限、整体 90s 预算。这里不再直接用 urlopen——旧实现
    的 `.read()` 没有上限，一个坏源就能把 runner 的内存吃干净。

    安全检查失败不重试：同一个地址再试两次还是被拦，只是把「源配错了」
    拖成三倍时间，还会在日志里刷三行同样的错。
    """
    path = os.path.join(CACHE, re.sub(r"[^\w\-]", "_", name) + ".txt")
    if use_cache and os.path.exists(path):
        return open(path, "rb").read()
    if isinstance(urls, str):
        urls = [urls]
    # 单源总预算。重试 2 次 × 多镜像 × 每次 90s 读预算，最坏能到 10 分钟以上：
    # 一个半死的源能把整轮采集拖到 workflow 超时。150 秒封顶，
    # 15 个源 6 并发最坏约 6 分钟，采集步骤单独给了 15 分钟余量。
    deadline = time.time() + budget
    last = None
    for u in urls:
        for _ in range(2):
            left = deadline - time.time()
            if left < 5:
                raise last or TimeoutError(f"{name}：单源预算 {budget}s 已用尽")
            try:
                data = netsafe.open_url(
                    u, timeout=min(netsafe.CONNECT_TIMEOUT, left),
                    total_timeout=min(netsafe.TOTAL_TIMEOUT, left))
                if len(data) > 200:
                    netsafe.write_atomic(path, data)
                    return data
                # 200 字节以下基本是错误页/占位文件。留着会让解析器
                # 得到 0 条，看起来像「源今天没更新」。
                last = RuntimeError(f"只拿到 {len(data)} 字节，不像订阅文件")
            except netsafe.Blocked:
                raise
            except Exception as e:
                last = e
    raise last or RuntimeError("全部地址失败")


def region_of(name):
    for k, v in REGIONS:
        if k in name:
            return v
    return "其他"


def collect(use_cache=False):
    """拉取全部源并解析成统一节点模型。

    解析交给 nodes_parse：Clash YAML、sing-box JSON、URI 列表三种载体都能入库。
    旧实现只保留含 "://" 的行，导致 YAML/JSON 源一条都进不来。
    去重改用全参数指纹，不再用 host:port（那会误杀同机不同凭据的节点）。
    """
    stats, errors, results, rejects = [], [], {}, {}
    groups = sources_io.collect_groups(SOURCES_ALL)
    order = [s["name"] for s in SOURCES_ALL if s.get("enabled")]

    def work(group):
        name = group["names"][0]
        t0 = time.time()
        data = fetch(group["urls"], name, use_cache)
        # 每条被丢弃的条目都留个记录（不含凭据），否则页面上「这个源少了 800 条」
        # 永远只能靠猜。rejects 是线程内的独立 list，不用担心并发串写。
        rj = []
        nodes, declared = nodes_parse.parse_any(data, rj)
        return group, nodes, declared, int((time.time() - t0) * 1000), rj

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(work, group): group for group in groups}
        for fu in as_completed(futs):
            group = futs[fu]
            try:
                _group, nodes, declared, ms, rj = fu.result()
                for source_id, name in zip(group["ids"], group["names"]):
                    results[name] = nodes
                    rejects[name] = [dict(item, src=name) for item in rj]
                    stats.append({"id": source_id, "name": name, "count": len(nodes),
                                  "ms": ms, "declared": declared})
                    print(f"  ✔ {name:<10} {len(nodes):>5} / {declared:<5} 条  {ms}ms")
            except Exception as e:
                # 异常文本里可能带完整 URL（HTTPError 的 str 就是 URL），
                # 订阅地址的 query 常放 token，所以统一过 mask_text
                msg = netsafe.mask_text(str(e))
                for name in group["names"]:
                    errors.append(f"{name}: {msg[:50]}")
                    print(f"  ✘ {name:<10} 失败 {msg[:40]}")

    stats.sort(key=lambda x: order.index(x["name"]))
    raw_total = sum(s.get("declared") or 0 for s in stats)

    # 每个来源的指纹集合 + 全局出现次数。共享主 URL 的同一批节点作为每个来源
    # 的成员参与独立度计算，因此不会把共享内容误报成任一来源的独立新增。
    from collections import Counter
    fp_appear = Counter()
    src_fps = {}
    for name, ns in results.items():
        fps = {n["fp"] for n in ns}
        src_fps[name] = fps
        fp_appear.update(fps)
    uniq = {name: sum(1 for fp in fps if fp_appear[fp] == 1)
            for name, fps in src_fps.items()}
    for row in stats:
        row["unique"] = uniq.get(row["name"])

    owners = {}
    for group in groups:
        for node in results.get(group["names"][0], []):
            # 只把同一主 URL 组的 ID 作为共享所有者；不同主 URL 碰巧重复的
            # 节点仍沿用全局去重的首组归属，不擅自扩展来源策略。
            owners.setdefault(node["fp"], {"ids": list(group["ids"]),
                                            "names": list(group["names"])})

    seen, kept = set(), []
    for name in order:
        for node in results.get(name, []):
            if node["fp"] in seen:
                continue
            seen.add(node["fp"])
            owner = owners[node["fp"]]
            kept.append({"src": owner["names"][0], "src_names": owner["names"],
                         "src_ids": owner["ids"], "uri": node["uri"], "fp": node["fp"],
                         "proto": PROTO_LABEL.get(node["type"], "其他"),
                         "region": region_of(node["name"]),
                         "name": node["name"][:40], "addr": node["addr"],
                         # 完整参数留给 nodecheck.py 做真实检测：只测端口不需要这些，
                         # 但要让 mihomo 真的连上，必须有 network/sni/flow/reality 这些。
                         # 不进页面、不进 data/nodes.json，只进 gitignore 的 export/
                         "cfg": {k: v for k, v in node.items() if k != "uri"}})
    return kept, stats, errors, raw_total, uniq, rejects


TEMPLATE_PATH = os.path.join(BASE, "template.html")
_template_cache = None


def load_template():
    """读取页面模板。模板与采集逻辑分家：改样式只碰 template.html，不碰 Python。"""
    global _template_cache
    if _template_cache is None:
        with open(TEMPLATE_PATH, encoding="utf-8") as f:
            _template_cache = f.read()
    return _template_cache


def _load_page_json(filename):
    """读取构建时嵌入页面的只读侧车数据；缺失或损坏时页面显示原有降级提示。"""
    try:
        with open(os.path.join(OUT_DATA, filename), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _script_json(value):
    """序列化 HTML script-data 上下文中的 JSON 字面量，阻断标签/脚本闭合。"""
    return (json.dumps(value, ensure_ascii=False)
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def render_page(page, *, governance=None, picks=None, daily_top50=None):
    r"""把数据填进模板。__DATA__ 是整页 JSON。

    治理与榜单 JSON 也在构建时内嵌，避免 file:// 页面运行时请求本地文件。
    所有数据字面量按 HTML script-data 上下文转义，避免恶意字符串截断脚本块。
    模板里已经没有 __N__ 占位符（源条数由页面自己数），原先那步替换是空转，去掉。
    """
    tpl = load_template()
    governance = _load_page_json("source_governance.json") if governance is None else governance
    picks = _load_page_json("picks.json") if picks is None else picks
    daily_top50 = (_load_page_json("daily_top50.json")
                   if daily_top50 is None else daily_top50)
    return (tpl.replace("__DATA__", _script_json(page))
            .replace("__GOVERNANCE__", _script_json(governance))
            .replace("__PICKS__", _script_json(picks))
            .replace("__DAILY_TOP50__", _script_json(daily_top50)))


def count_of(data):
    """卡片上显示的条数 = 该源能解析出的有效节点数。

    走和 collect() 完全相同的解析路径，保证「页面写的数」和「库里存的数」
    是同一个口径。旧实现只数 proxies 的 "- " 行，YAML 源显示 2144 条，
    实际一条都进不了库——两个数互相解释不了。
    """
    try:
        nodes, _ = nodes_parse.parse_any(data)
        return len(nodes) or None
    except Exception:
        return None


def http_probe(url, timeout=12):
    """源站自身可达性：只读 512 字节就断开，记录状态码与首字节耗时。

    和「节点抽样可连率」是两件事：前者回答“这个源还活着吗”，
    后者回答“它发出来的节点通不通”。源站 5xx / 超时时百分比仍是上次的值，
    页面上必须能看出这一层，否则会拿旧数字当现状。
    """
    t0 = time.time()
    try:
        with netsafe.open_response(
                url, timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 v2rayN/6.0"}) as r:
            r.read(512)
            return {"code": r.status, "ms": int((time.time() - t0) * 1000)}
    except netsafe.Blocked:
        # 被安全规则拦下的地址，页面标「不可达」比标「超时」误导性小
        return {"code": 0, "ms": int((time.time() - t0) * 1000), "err": "Blocked"}
    except urllib.error.HTTPError as e:
        return {"code": e.code, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"code": 0, "ms": int((time.time() - t0) * 1000), "err": type(e).__name__}


def probe_sources(items):
    """并发测每个订阅地址本身。失败不抛，返回 code=0 + 异常类名。

    主地址不通时会试镜像：页面要回答的是「这个源还活着吗」，而不是
    「GitHub raw 此刻通不通」。
    """
    urls = list(dict.fromkeys(i["url"] for i in items))

    def best(u):
        r = http_probe(u)
        if r.get("code") == 200:
            return r
        for m in URLS_BY_URL.get(u, [])[1:]:
            r2 = http_probe(m)
            if r2.get("code") == 200:
                return r2
        return r

    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(best, urls))
    out = {}
    for u, r in zip(urls, res):
        out[u] = r
        flag = "✔" if r["code"] == 200 else ("✘" if not r["code"] else f"✘{r['code']}")
        print(f"  {flag} 源站 {r['ms']:>5}ms  {netsafe.mask_url(u)[:60]}")
    return out


def src_meta(url, timeout=10):
    """源最后一次改动距今多少小时。

    GitHub 上的文件走 commits API（CI 里有 GITHUB_TOKEN，额度足够）；
    其余站点读 HTTP Last-Modified。都拿不到就返回 None —— 页面不显示，
    不拿「采集时间」冒充「源更新时间」。
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)", url)
    if m:
        owner, repo, branch, path = m.groups()
        api = (f"https://api.github.com/repos/{owner}/{repo}/commits"
               f"?path={path}&sha={branch}&per_page=1")
        hdr = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github+json"}
        if token:
            hdr["Authorization"] = "Bearer " + token
        try:
            # 走 netsafe：这段响应里没有凭据，但 api 地址由源 URL 拼出来，
            # 同样是外部输入。CI 里带了 GITHUB_TOKEN，更不能让它顺手连到别处。
            body = netsafe.open_url(api, timeout=timeout, headers={
                k: v for k, v in hdr.items() if k.lower() != "user-agent"})
            arr = json.loads(body.decode("utf-8", "ignore"))
            if arr:
                dt = datetime.strptime(arr[0]["commit"]["committer"]["date"],
                                       "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                return {"ts": int(dt.timestamp()),
                        "age_h": round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)}
        except Exception:
            return None
    try:
        with netsafe.open_response(
                url, method="HEAD", timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"}) as r:
            lm = r.headers.get("Last-Modified")
        if lm:
            dt = datetime.strptime(lm, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc)
            return {"ts": int(dt.timestamp()),
                    "age_h": round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)}
    except Exception:
        pass
    return None


def probe_meta(items):
    """并发取各源的新鲜度。失败静默——这一层是锦上添花，不能拖垮采集。"""
    out = {}
    items = list({i["url"]: i for i in items}.values())
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(lambda i: src_meta(i["url"]), items))
    for it, r in zip(items, res):
        if r:
            out[it["url"]] = r
            print(f"  · 源更新于 {r['age_h']:>5.1f}h 前  {netsafe.mask_url(it['url'])[:56]}")
        else:
            print(f"  · 源更新时间未知        {netsafe.mask_url(it['url'])[:56]}")
    return out


def measure(items, use_cache=False):
    """逐条拉取订阅，统计真实节点条数 + 抽样测 TCP 连通性"""
    counts, addrs, fps = {}, {}, {}
    items = list({i["url"]: i for i in items}.values())

    def work(it):
        nm = "c_" + hashlib.md5(it["url"].encode()).hexdigest()[:10]
        data = fetch(URLS_BY_URL.get(it["url"], [it["url"]]), nm, use_cache)
        # 指纹用来判断「这条源的内容到底有没有变」：
        # 非 GitHub 的站点拿不到提交时间，只能靠它算新鲜度
        return it["url"], count_of(data), extract_addrs(data), hashlib.md5(data).hexdigest()

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(work, it) for it in items]
        for fu in as_completed(futs):
            try:
                u, n, a, fp = fu.result()
                counts[u] = n
                addrs[u] = a
                fps[u] = fp
                print(f"  · {n if n else '—':>5} 条 / {len(a):>4} 个地址可测  {netsafe.mask_url(u)[:56]}")
            except Exception as e:
                print(f"  ✘ 计数失败 {netsafe.mask_text(str(e))[:40]}")

    probes = probe_all(addrs)
    return counts, probes, fps, addrs


def write_source_keys(addrs, stamp=None):
    """P19：按源落盘**地址哈希**，供重复源识别使用。

    nodes.json 是去重后的结果，同一个地址只会归属一个源，拿它算源间重合度
    永远是 0——这是口径错误，不是「源不重复」。这里在去重之前截住每源原始
    地址，用 sha256 前 16 位存集合：既能算 Jaccard，又不把地址明文写进
    公开仓库（地址加端口本身就是可直接订阅的节点信息）。
    """
    doc = {
        "updated": stamp or datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "method": "sha256(addr)[:16]，地址明文不落盘；样本 <20 不判重合",
        "sources": {},
    }
    for url, arr in (addrs or {}).items():
        source_ids = SOURCE_IDS_BY_URL.get(url) or []
        if not source_ids:
            continue
        hashes = sorted({hashlib.sha256(str(a).encode()).hexdigest()[:16]
                         for a in (arr or [])})
        for source_id in source_ids:
            doc["sources"][source_id] = hashes
    netsafe.write_atomic(os.path.join(OUT_DATA, "source_keys.json"),
                         json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    return sum(len(v) for v in doc["sources"].values())


def extract_addrs(data):
    """从订阅内容里抽 host:port 供 TCP 抽样，走和入库同一套解析器。

    旧实现用正则截 proxies 段、按 server/port 出现顺序 zip 配对，字段顺序
    一变就错位；现在直接复用 nodes_parse，顺带白拿「回环地址已过滤」。

    UDP 系（QUIC）没法用 TCP 握手探测，跳过，避免误判成死节点。
    """
    try:
        nodes, _ = nodes_parse.parse_any(data)
    except Exception:
        return []
    udp = {"hysteria", "hysteria2", "tuic"}
    return [n["addr"] for n in nodes
            if n.get("type") not in udp and n.get("addr") not in (None, "", "—")]


def _tcp_ok(host, port, timeout):
    try:
        with socket.create_connection((host, int(port)), timeout):
            return True
    except Exception:
        return False


def probe_all(addrs, sample=24, timeout=3.0):
    """对每条订阅抽样若干地址并发测 TCP 握手，返回 {url: {ok, total}}"""
    jobs = []
    for url, lst in addrs.items():
        uniq = list(dict.fromkeys(lst))[:sample]
        for a in uniq:
            host, _, port = a.rpartition(":")
            host = host.strip("[]")
            if not host or not port.isdigit():
                continue
            jobs.append((url, host, port))
    if not jobs:
        return {}
    tally = {}
    with ThreadPoolExecutor(max_workers=64) as ex:
        fmap = {ex.submit(_tcp_ok, h, p, timeout): u for u, h, p in jobs}
        for fu in as_completed(fmap):
            u = fmap[fu]
            t = tally.setdefault(u, {"ok": 0, "total": 0})
            t["total"] += 1
            try:
                if fu.result():
                    t["ok"] += 1
            except Exception:
                pass
    for u, t in sorted(tally.items(), key=lambda x: -x[1]["total"]):
        rate = t["ok"] / t["total"] * 100 if t["total"] else 0
        print(f"  ↳ 抽样 {t['total']:>2} 个，可连 {t['ok']:>2} 个（{rate:.0f}%）  {netsafe.mask_url(u)[:56]}")
    return tally



HEALTH_PATH = os.path.join(OUT_DATA, "health.json")


def load_health():
    try:
        return json.load(open(HEALTH_PATH, encoding="utf-8"))
    except Exception:
        return {}


def update_health(old, probes, fps=None):
    """累计每个源的连续低连通轮次；连续 2 轮 <30% 判定为不稳定。

    同时记内容指纹：内容没变就沿用上次的变化时间，变了就记现在。
    非 GitHub 的源拿不到提交时间，这是唯一能证明「它到底更没更新」的办法。
    """
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    now_ts = int(time.time())
    new = {}
    for url, p in probes.items():
        h = old.get(url) or {}
        streak = int(h.get("streak_bad") or 0)
        total = int(p.get("total") or 0)
        ok = int(p.get("ok") or 0)
        rate = (ok / total) if total else 0
        if total:
            streak = streak + 1 if rate < 0.3 else 0
        new[url] = {"streak_bad": streak, "rate": round(rate * 100),
                    "total": total, "ok": ok, "seen": stamp}
    for url, fp in (fps or {}).items():
        prev = old.get(url) or {}
        ts = prev.get("fp_ts") if prev.get("fp") == fp else None
        row = new.setdefault(url, {})
        row["fp"] = fp
        row["fp_ts"] = ts or now_ts
        # 第一次记录没有参照物，写 0 会被读成「刚刚更新过」，所以留空
        row["fp_age_h"] = None if not prev.get("fp") else round((now_ts - row["fp_ts"]) / 3600, 1)
    for url, h in old.items():          # 本轮没测到的源保留旧记录
        new.setdefault(url, h)
    # 只在 CI（美国机房）里落盘：本地出口网络差异极大，会污染健康记录
    if os.environ.get("GITHUB_ACTIONS") == "true":
        wjson(HEALTH_PATH, new, 1)
    else:
        print("  （本地运行，不写入健康记录）")
    return new


def build_source_stats(sources, counts, probes, srcstat, meta, health, nodes, prev=None,
                       uniq=None, declared=None, real=None):
    """按任务书 §4 的字段口径，为每个启用的源生成一行动态记录。

    与页面上那点数字的区别：这里记的是「这一轮采集的原始事实」——
    解析出多少、去重后实际留下多少、抽样可连多少、HTTP 几、连续失败几次。
    页面只展示其中一部分，全量落在 data/sources_stats.json 供 P4 评分使用。

    declared: {name: 源文件自己声明的条数}。与 parsed 分开记，
    因为「声明 2169 条、实际解析 2133 条」本身就是源的可靠性证据。

    real: {id: {tested, ok}}，来自最近一轮 nodecheck（经 mihomo 真握手）。
    采集时检测还没跑，所以这是上一轮的数——rtest/rok 都随行落盘，
    报告里标清「基于最近一轮检测」，不把上轮的结论说成本轮的。
    """
    from collections import Counter
    kept = Counter()
    for node in nodes:
        source_ids = node.get("src_ids")
        if isinstance(source_ids, list) and source_ids:
            kept.update(set(source_ids))
        elif node.get("src"):
            kept[node["src"]] += 1
    prev_map = {r.get("id"): r for r in (prev or []) if isinstance(r, dict)}
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    rows = []
    for s in sources:
        if not s.get("enabled"):
            continue
        u = s["url"]
        p = probes.get(u) or {}
        total, ok = int(p.get("total") or 0), int(p.get("ok") or 0)
        parsed = counts.get(u)
        kept_n = kept.get(s["id"], kept.get(s["name"], 0))
        http = (srcstat.get(u) or {})
        code = http.get("code")
        old = prev_map.get(s["id"]) or {}
        fails_total = int(old.get("fails_total") or 0)
        fails_streak = int(old.get("fails_streak") or 0)
        err = None
        if code != 200 or parsed is None:
            fails_total += 1
            fails_streak += 1
            err = f"HTTP {code}" if code != 200 else "解析失败"
        else:
            fails_streak = 0
        rate = (ok / total * 100) if total else None
        rl = (real or {}).get(s["id"]) or (real or {}).get(s["name"]) or {}
        rtest = int(rl.get("tested") or 0)
        rok = int(rl.get("ok") or 0)
        rows.append({
            "id": s["id"], "name": s["name"], "url": u,
            "type": s["type"], "format": s["format"], "license": s.get("license"),
            "priority": s.get("priority"),
            "checked_at": stamp,
            "http": code, "http_ms": http.get("ms"),
            "source_age_h": _age_of(meta, health, u),
            "parsed": parsed,                 # 解析出的节点数
            "declared": (declared or {}).get(s["name"]),   # 源文件自己声明的条数
            "kept": kept_n,                   # 去重后实际入库归属该源的条数
            "unique": ((uniq or {}).get(s["name"]) if isinstance(parsed, int) else None),
            "dup": (parsed - kept_n) if isinstance(parsed, int) else None,
            "probe_total": total, "probe_ok": ok,
            "alive_pct": round(rate) if rate is not None else None,
            # 真实握手（最近一轮）：这才是「装进客户端能不能用」的证据
            "rtest": rtest, "rok": rok,
            "real_pct": round(rok / rtest * 100, 1) if rtest else None,
            "fails_total": fails_total, "fails_streak": fails_streak,
            "last_error": err,
            # 抽样可连率。P4 的综合评分是 score 字段，两者不要混用：
            # quality 回答「它发的节点通不通」，score 回答「这个源值不值得留」
            "quality": round(rate) if rate is not None else None,
        })
    rows.sort(key=lambda r: (-(r.get("quality") or -1), -(r.get("kept") or 0)))
    return rows


def preferred_nodes(nodes, rows):
    """仅剔除所有已知所有者均隔离的节点；共享节点不因单一来源状态被误删。"""
    state_by_id = {r.get("id"): r.get("state") for r in rows or [] if r.get("id")}
    state_by_name = {r.get("name"): r.get("state") for r in rows or [] if r.get("name")}
    selected = []
    for node in nodes or []:
        source_ids = node.get("src_ids")
        source_names = node.get("src_names")
        if isinstance(source_ids, list) and source_ids:
            states = [state_by_id.get(source_id) or
                      (state_by_name.get(source_names[index])
                       if isinstance(source_names, list) and index < len(source_names)
                       else None)
                      for index, source_id in enumerate(source_ids)]
        elif isinstance(source_names, list) and source_names:
            states = [state_by_name.get(name) for name in source_names]
        else:
            states = [state_by_name.get(node.get("src"))]
        if not states or not all(state == "isolated" for state in states):
            selected.append(node)
    return selected


def print_source_stats(rows):
    print("\n来源质量（评分见 docs/P4-source-quality.md）")
    print(f"  {'来源':<17}{'等级':>4}{'分':>5}{'原始':>7}{'独立':>7}{'重复':>7}"
          f"{'可连':>7}{'HTTP':>7}{'失败':>5}  状态")
    for r in sorted(rows, key=lambda x: -(x.get("score") or 0)):
        print(f"  {r['name'][:16]:<17}{r.get('grade', '—'):>4}{r.get('score', 0):>5}"
              f"{str(r['parsed'] or '—'):>7}{str(r.get('unique') if r.get('unique') is not None else '—'):>7}"
              f"{str(r['dup'] if r['dup'] is not None else '—'):>7}"
              f"{(str(r['alive_pct']) + '%') if r['alive_pct'] is not None else '—':>7}"
              f"{str(r['http'] or '—'):>7}{r['fails_streak']:>5}  {r.get('state_label', '')}")


def _age_of(meta, health, url):
    """优先用仓库提交时间（权威），没有再退回内容指纹的变化时间（本地实测）。
    两者都没有就返回 None —— 页面宁可不显示，也不拿采集时间冒充源更新时间。"""
    m = (meta.get(url) or {}).get("age_h")
    if m is not None:
        return m
    return ((health or {}).get(url) or {}).get("fp_age_h")


def _source_data(item, data):
    """按卡片 sid 取源数据；旧节点/旧统计仍可退回名称键。"""
    source_id = item.get("sid") or SRC_ID_BY_URL.get(item.get("url"))
    name = SRC_NAME_BY_ID.get(source_id) or SRC_NAME_BY_URL.get(item.get("url"))
    return ((data or {}).get(source_id) or (data or {}).get(name)
            or (data or {}).get(item.get("url")) or {})


def _rank_real(item, counts, probes, real):
    """卡片排序分 = 真实可用条数（绝对值）。

    以前是「TCP 可连率 × 条数」：端口开着就算能用，于是真握手 0/90 的源
    排第一、真握手 85/90 的排第五。现在直接按「这张卡里能用几条」排，
    用户看卡片上的「真实可用 85/90」就知道它为什么在前面。

    试过用「可用率 × 条数」和「向 25% 先验收缩」两种写法，都会让 0 可用的
    大源靠条数拿到保底分（1,770 条 × 25% 先验 = 440，仍排前列），
    与「可用条数」这个口径自相矛盾，所以放弃收缩，只排绝对值。
    没有真实数据（rtest=0）的一律排在后面，按 TCP 打三折估个位置。
    """
    n = int(counts.get(item["url"]) or 0)
    rl = _source_data(item, real)
    rok = int(rl.get("ok") or 0)
    rtest = int(rl.get("tested") or 0)
    if rtest:
        return float(rok), 1
    p = probes.get(item["url"]) or {}
    total, ok = int(p.get("total") or 0), int(p.get("ok") or 0)
    if not total or not n:
        return -1.0, 0
    return round(0.3 * (ok / total) * n, 1), 0


def _load_real():
    """每源的真实握手结果，优先稳定 ID，兼容旧名称键。

    读的是 data/node_scores.json 的 by_source_id（旧文件退回 by_source）：
    那个文件由 nodescore.py 在 nodecheck.py 之后生成，所以采集这一段拿到的是**上一轮**的结果——
    延迟一轮总比页面显示「本轮还没测」好，而且下一轮就自动对上了。
    """
    try:
        d = json.load(open(os.path.join(OUT_DATA, "node_scores.json"), encoding="utf-8"))
        out = dict(d.get("by_source") or {})
        out.update(d.get("by_source_id") or {})
        return out
    except Exception:
        return {}


def _load_scores():
    try:
        return json.load(open(os.path.join(OUT_DATA, "node_scores.json"), encoding="utf-8"))
    except Exception:
        return {}


def _load_quarantine():
    """隔离区条数与主因。这是「声明 15,306 → 入库 10,216」缺口的另一半：
    一半是重复，另一半是根本不该入库的（明文代理、非公网地址）。"""
    try:
        d = json.load(open(os.path.join(OUT_DATA, "quarantine.json"), encoding="utf-8"))
        by = d.get("by_reason") or {}
        top = max(by.items(), key=lambda kv: kv[1]) if by else None
        return {"total": d.get("total") or 0,
                "top_reason": top[0] if top else None,
                "top_count": top[1] if top else 0}
    except Exception:
        return {}


def _load_sub_counts():
    """订阅各分组的条数。订阅文件本身含凭据不进仓库，页面只能显示「有几条」。
    读不到就是空 dict，页面那块自动不显示，不编造数字。"""
    try:
        d = json.load(open(os.path.join(OUT_DATA, "subscriptions.json"), encoding="utf-8"))
        return {g["key"]: {"count": g.get("count"), "label": g.get("label")}
                for g in d.get("groups") or []}
    except Exception:
        return {}


def assemble(nodes, raw_total, errors, counts, probes, delta, health, srcstat=None,
             meta=None, include_uri=False, grades=None, fresh=None, life_span=None,
             real=None):
    """组装页面数据。

    include_uri=False（默认）时节点不带 uri 字段：uri 里含 uuid/password 等
    完整凭据，落盘到公开仓库等于把 3500 多个节点的账号密码公开托管。
    页面本身不需要 uri（卡片只显示源站链接），所以默认脱敏。

    grades: {来源 ID: stats_row}，把 P4 的等级/状态带给页面；兼容旧 URL/名称键。
    页面不作为评分依据，只是让用户知道「这张卡背后的源目前被判成什么水平」。

    fresh:  {来源 ID: {new7, median_age}}，P5 的生命周期统计；兼容旧源名键。
    """
    probes = probes or {}
    srcstat = srcstat or {}
    meta = meta or {}
    grades = grades or {}
    fresh = fresh or {}
    real = real if real is not None else _load_real()
    groups = []
    for g in GROUPS:
        items = [{"sid": i.get("sid"), "tag": i["tag"], "note": i["note"],
                  "ok": i.get("ok", []), "no": i.get("no", []),
                  "freq": i["freq"], "url": i["url"], "count": counts.get(i["url"]),
                  "probe": probes.get(i["url"]),
                  "cnbad": bool(i.get("cn")),
                  "src_http": srcstat.get(i["url"]),
                  "age_h": _age_of(meta, health, i["url"]),
                  "streak": int((health or {}).get(i["url"], {}).get("streak_bad") or 0),
                  "grade": _source_data(i, grades).get("grade"),
                  "grade_score": _source_data(i, grades).get("score"),
                  "src_state": _source_data(i, grades).get("state"),
                  "src_state_label": _source_data(i, grades).get("state_label"),
                  "prio": _source_data(i, grades).get("priority_eff"),
                  "rok": _source_data(i, real).get("ok"),
                  "rtest": _source_data(i, real).get("tested"),
                  "new7": _source_data(i, fresh).get("new7"),
                  "med_age": _source_data(i, fresh).get("median_age"),
                  } for i in g["items"]]
        # 排序分在这里算：需要 item 上的 count/probe，放不进上面那次 comprehension。
        # rank_real 有真实样本时是实际可用条数；否则是 TCP 可连率 × 节点数 × 0.3 的估值。
        for it, i in zip(items, g["items"]):
            it["rank_real"], it["rank_has_real"] = _rank_real(i, counts, probes, real)
        # 国内直连不通的固定沉底；已隔离的再沉一层（不是删除，是「先别信」）；
        # 其余按真实可用条数降序，没有真实样本的一律排在后面。
        # 同分时才看 P4 优先级——连续三轮没带来新节点且在变差的源，
        # 不该和同水平的源平起平坐
        items.sort(key=lambda it: (it["cnbad"], it.get("src_state") == "isolated",
                                   -it["rank_has_real"], -it["rank_real"],
                                   it.get("prio") or 0))
        groups.append({"title": g["title"], "kern": g["kern"],
                       "clients": g["clients"], "items": items})

    # 页面顶部的总览。全部来自实测文件，任何一个缺了就整块不显示，
    # 不用占位符凑数——「—」和「0」在数据页里是完全不同的两件事
    sc = _load_scores()
    sm = sc.get("summary") or {}
    subs = _load_sub_counts()
    # 页头用的是「11-25 04:48」，这里把「2026-11-24 21:09」砍成同款，
    # 两个时间戳并排出现时格式不一致会被当成两个不同的东西
    _ck = str(sc.get("updated") or "")
    if len(_ck) >= 16 and _ck[:2] == "20":
        _ck = _ck[5:16]
    stats = {
        "raw": raw_total,
        "dedup": len(nodes),
        "real_tested": sm.get("tested"),
        "real_ok": sm.get("ok"),
        "regions": sm.get("regions") or {},
        "tags": sm.get("tags") or {},
        "checked_at": _ck or None,
        "subs": subs,
        # 隔离区是「声明数与入库数的缺口」的另一半，比「几个源被隔离」有用得多：
        # 用户看到 15,306 变 10,216，第一反应就是「剩下的去哪了」
        "quarantine": _load_quarantine(),
        "isolated": sum(1 for g in groups for it in g["items"]
                        if it.get("src_state") == "isolated"),
        "protos": dict(Counter(n["proto"] for n in nodes).most_common()),
        "sources": sum(len(g["items"]) for g in groups),
    }

    return {
        # 数据结构版本。加字段不改语义时不升版本；删字段、改字段含义时升，
        # 消费方（页面、脚本、第三方）先看这个再决定怎么读
        "schema": SCHEMA,
        "updated": datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M"),
        "ts": int(time.time()),
        # 生命周期跟踪了几天。不足 7 天时页面不报「近 7 天新增」——
        # 冷启动那轮所有节点都是首次见到，报出来是假的新增
        "life_span": life_span,
        "raw": raw_total, "errors": errors, "groups": groups, "delta": delta,
        "stats": stats,
        "nodes": [({"name": n["name"], "proto": n["proto"], "region": n["region"],
                    "addr": n["addr"], "src": n["src"],
                    "src_ids": n.get("src_ids", []), "src_names": n.get("src_names", []),
                    "uri": n["uri"]}
                   if include_uri else
                   {"name": n["name"], "proto": n["proto"], "region": n["region"],
                    "addr": n["addr"], "src": n["src"],
                    "src_ids": n.get("src_ids", []), "src_names": n.get("src_names", [])})
                   for n in nodes],
    }


def build_html(nodes, stats, raw_total, errors, counts, probes=None, delta=None,
               health=None, srcstat=None, meta=None, grades=None, fresh=None, life_span=None):
    data = assemble(nodes, raw_total, errors, counts, probes, delta, health, srcstat, meta,
                    grades=grades, fresh=fresh, life_span=life_span)
    page = {k: v for k, v in data.items() if k != "nodes"}
    netsafe.write_atomic(os.path.join(BASE, "index.html"), render_page(page))
    wjson(os.path.join(OUT_DATA, "nodes.json"), data, 1)
    if srcstat:
        # 给第三方/自己监控用的机器可读状态：每条源的 HTTP 码与耗时
        wjson(os.path.join(OUT_DATA, "status.json"),
              {"updated": data["updated"], "ts": data["ts"],
               "sources": [{"url": u, **v} for u, v in srcstat.items()]}, 1)
    return data


def rerender():
    """不联网地重建最终页面快照与 index.html。

    读取采集阶段的 nodes.json，并叠加本轮已经生成的评分、订阅统计；写回的
    nodes.json 是供脚本读取的最终快照，不是仅渲染 HTML。阶段台账明确授权
    render 对 collect 初稿的这一次覆写，并记录最终文件哈希。
    """
    with open(os.path.join(OUT_DATA, "nodes.json"), encoding="utf-8") as f:
        old = json.load(f)
    counts, probes, health, srcstat, meta, grades, fresh = {}, {}, {}, {}, {}, {}, {}
    for g in old.get("groups", []):
        for it in g.get("items", []):
            counts[it["url"]] = it.get("count")
            if it.get("probe"):
                probes[it["url"]] = it["probe"]
            if it.get("src_http"):
                srcstat[it["url"]] = it["src_http"]
            if it.get("age_h") is not None:
                meta[it["url"]] = {"age_h": it["age_h"]}
            health[it["url"]] = {"streak_bad": it.get("streak") or 0}
            # 生命周期统计按稳定 ID 索引；旧快照仍可退回名称键。
            if it.get("new7") is not None:
                sid = it.get("sid") or SRC_ID_BY_URL.get(it["url"])
                nm = SRC_NAME_BY_ID.get(sid) or SRC_NAME_BY_URL.get(it["url"])
                key = sid or nm
                if key:
                    fresh[key] = {"new7": it.get("new7"), "median_age": it.get("med_age")}
    # 评分优先按 sources_stats.json 重算：改了公式之后，只重渲染会把旧等级
    # 一直带到页面上，看起来像「改了没用」。读不到才退回 nodes.json 里的旧值。
    rows = sources_io.load_stats().get("sources") or []
    if rows:
        # 真实检测结果按稳定 ID 回填；旧 node_scores.json 仍按名称兼容。
        realmap = _load_real()
        for r in rows:
            if not r.get("rtest"):
                b = realmap.get(r.get("id")) or realmap.get(r.get("name")) or {}
                r["rtest"] = int(b.get("tested") or 0)
                r["rok"] = int(b.get("ok") or 0)
                r["real_pct"] = (round(r["rok"] / r["rtest"] * 100, 1)
                                 if r["rtest"] else None)
        rows, _h = scoring.annotate(rows, scoring.load_history(), stamp=old.get("updated"),
                                    persist=False, record=False)
        grades = {r.get("id") or r["url"]: r for r in rows}
        print(f"  按新公式重算 {len(rows)} 个源："
              + " ".join(f"{r.get('grade')}{r.get('score')}" for r in rows[:6]) + " …")
    else:
        for it in [i for g in old.get("groups", []) for i in g.get("items", [])]:
            if it.get("grade"):
                grades[it["url"]] = {"grade": it.get("grade"), "score": it.get("grade_score"),
                                     "state": it.get("src_state"),
                                     "priority_eff": it.get("prio")}
    data = assemble(old.get("nodes") or [], old.get("raw") or 0, old.get("errors") or [],
                    counts, probes, old.get("delta"), health, srcstat, meta, grades=grades,
                    fresh=fresh, life_span=old.get("life_span"))
    data["updated"], data["ts"] = old.get("updated"), old.get("ts")
    page = {k: v for k, v in data.items() if k != "nodes"}
    out = os.path.join(BASE, "index.html")
    netsafe.write_atomic(out, render_page(page))
    # 同步保存最终快照：真实检测汇总和本轮订阅统计在前序阶段才可用。
    # run_ledger 明确记录 collect 初稿与 render 最终版，不放宽最终哈希校验。
    wjson(os.path.join(OUT_DATA, "nodes.json"), data, 1)
    print(f"已重渲染 {out}（数据保持 {data['updated']}，未联网）")


def write_quarantine(rejects, stamp):
    """把本轮被丢弃的条目按原因汇总落盘，供排查「这个源为什么少了几百条」。

    只留协议/名字/地址/端口，不留原始订阅行：那个文件要提交进公开仓库，
    搬原文等于把 uuid、password 换个文件继续公开托管。
    """
    rows, by_reason, samples, total = [], {}, [], 0
    for src, items in rejects.items():
        if not items:
            continue
        c = {}
        for r in items:
            c[r["reason"]] = c.get(r["reason"], 0) + 1
            by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
        total += len(items)
        rows.append({"src": src, "count": len(items), "by_reason": c})
        # 每源抽 5 条做样本，避免样本全被条数最多的源占满
        samples.extend(items[:5])
    rows.sort(key=lambda x: -x["count"])
    doc = {"updated": stamp, "total": total, "by_reason": by_reason,
           "sources": rows, "samples": samples[:300]}
    wjson(os.path.join(OUT_DATA, "quarantine.json"), doc, 1)
    if total:
        detail = "，".join(f"{k} {v}" for k, v in
                          sorted(by_reason.items(), key=lambda kv: -kv[1]))
        print(f"  隔离区：{total} 条未入库（{detail}）")
    return doc


def main():
    if "--render" in sys.argv:      # 只重渲染页面，不联网
        rerender()
        return
    use_cache = "--cache" in sys.argv
    print("开始采集节点...")
    nodes, stats, errors, raw, uniq, rejects = collect(use_cache)

    old_json = os.path.join(OUT_DATA, "nodes.json")
    delta = None
    if os.path.exists(old_json):
        try:
            with open(old_json, encoding="utf-8") as f:
                old = json.load(f)
            old_nodes = old.get("nodes", [])
            old_n = len(old_nodes)
            if old_n >= 50 and len(nodes) < old_n * 0.6:
                print(f"\n⚠ 本次仅 {len(nodes)} 条，低于上次 {old_n} 条的 60%，判定采集不完整，保留旧数据")
                raise SystemExit(2)
            old_set = {n["addr"] for n in old_nodes if n.get("addr") not in (None, "", "—")}
            new_set = {n["addr"] for n in nodes if n.get("addr") not in (None, "", "—")}
            if old_set and new_set:
                delta = {"add": len(new_set - old_set), "drop": len(old_set - new_set)}
                print(f"  对比上次：新增 {delta['add']} 个地址，消失 {delta['drop']} 个")
        except Exception:
            pass

    print("\n统计各条订阅的节点数...")
    items = [it for g in GROUPS for it in g["items"]]
    counts, probes, fps, addrs = measure(items, use_cache)
    nkeys = write_source_keys(addrs)
    print(f"  源地址哈希已落盘：{nkeys} 条（去重前口径，供 P19 重合度分析）")
    print("\n测各条源站自身可达性...")
    srcstat = probe_sources(items)

    print("\n查各条源最后更新时间...")
    meta = probe_meta(items)

    health = update_health(load_health(), probes, fps)
    for u, h in health.items():
        if h.get("streak_bad"):
            print(f"  ⚠ 连续 {h['streak_bad']} 轮低连通：{netsafe.mask_url(u)[:60]}")

    # P5：节点生命周期。先 update 再 annotate，顺序反了拿不到 first/last。
    # update 会顺手清掉 45 天没再出现的指纹，避免历史文件无限增长。
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    life, lstat = lifecycle.update(nodes, lifecycle.load())
    lifecycle.annotate(nodes, life)
    fresh = lifecycle.freshness(nodes, life)
    print("  " + lifecycle.stats_line(lstat))
    lifecycle.save(life)

    # P5：异常配置隔离区。被丢弃的条目不再静默消失，按原因留痕
    write_quarantine(rejects, stamp)

    # 每源动态统计：任务书 §4 要求的原始/解析/重复/新增/有效节点数与失败次数
    rows = build_source_stats(SOURCES_ALL, counts, probes, srcstat, meta, health, nodes,
                              sources_io.load_stats().get("sources"), uniq=uniq,
                              declared={s["name"]: s.get("declared") for s in stats},
                              real=_load_real())
    # P4：加权评分 + 连续失败状态机（单轮异常 / 两轮降级 / 三轮隔离）。
    # 先评分再渲染，页面卡片才能带上等级；顺序反了页面会全是 null。
    rows, hist = scoring.annotate(rows, scoring.load_history(), stamp=stamp)
    print_source_stats(rows)
    sources_io.save_stats(rows, updated=stamp)
    netsafe.write_atomic(scoring.REPORT_PATH, scoring.render_report(rows, hist))
    print(f"  评分报告 -> {os.path.relpath(scoring.REPORT_PATH, BASE)}")

    grades = {r.get("id") or r["url"]: r for r in rows}
    build_html(nodes, stats, raw, errors, counts, probes, delta, health, srcstat, meta,
               grades=grades, fresh=fresh, life_span=fresh.get("_span"))

    # 全量导出口：含完整凭据，只能留在本地/CI 产物里，绝不能进仓库
    # （2024-11 曾提交到公开仓库 78 次，已重写历史清除；export/ 已在 .gitignore）
    uris = [n["uri"] for n in nodes]
    exp = os.path.join(BASE, "export")
    os.makedirs(exp, exist_ok=True)
    # 这里要写的东西含全量凭据：同机器上的其它用户读不到比较稳妥。
    # 容器里通常只有一个用户，属于顺手加固（失败不阻断）。
    try:
        os.chmod(exp, 0o700)
    except OSError:
        pass
    netsafe.write_atomic(os.path.join(exp, "全部节点 base64.txt"),
                         base64.b64encode("\n".join(uris).encode()).decode())
    netsafe.write_atomic(os.path.join(exp, "全部节点 明文.txt"), "\n".join(uris))

    # 完整参数导出：nodecheck.py 拿它做真实代理检测（mihomo 需要 network/sni/flow
    # 这些结构化的参数，光有 URI 还得再解析一遍）。同样含凭据，同样不入库。
    # 完整参数：每条节点都要带凭据，写出后立刻 chmod 600，
    # 同机器上的其它用户读不到（容器里通常只有一个用户，属于顺手加固）
    full = os.path.join(exp, "nodes_full.json")
    netsafe.write_atomic(full, json.dumps(
        [{"src": n["src"], "src_ids": n.get("src_ids", []),
          "src_names": n.get("src_names", []),
          "fp": n["fp"], "uri": n["uri"], **n["cfg"]} for n in nodes],
        ensure_ascii=False))
    try:
        os.chmod(full, 0o600)
    except OSError:
        pass

    # 共享节点只有在所有已知所有者都隔离时才剔除；一个来源隔离不应抹掉
    # 另一个健康来源对同一节点的归属。全量导出始终保持完整。
    iso = {r["name"] for r in rows if r.get("state") == "isolated"}
    best = [n["uri"] for n in preferred_nodes(nodes, rows)]
    if iso:
        netsafe.write_atomic(os.path.join(exp, "优选节点 明文.txt"), "\n".join(best))
        netsafe.write_atomic(os.path.join(exp, "优选节点 base64.txt"),
                             base64.b64encode("\n".join(best).encode()).decode())
        print(f"      优选导出 {len(best)} 条（剔除隔离源 {len(iso)} 个：{'、'.join(sorted(iso))}）")
    else:
        print("      优选导出 = 全量（无隔离源）")

    print(f"\n完成: 校验 {raw} 条，去重后 {len(nodes)} 条")
    print("输出: index.html / data/nodes.json（脱敏，不含凭据）")
    print(f"      导出 {len(uris)} 条完整订阅 -> export/（本地文件，不入库）")
    if errors:
        print("失败源:", "; ".join(errors))


if __name__ == "__main__":
    main()
