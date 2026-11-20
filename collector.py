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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.dirname(BASE), ".nodes-cache")
OUT_DATA = os.path.join(BASE, "data")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(OUT_DATA, exist_ok=True)

# ── 订阅清单：页面按此结构展示，url 均为仓库原版地址 ──────────────
GROUPS = [
    {
        "title": "Clash 系",
        "kern": "mihomo 内核",
        "clients": "Clash Verge Rev（电脑）· FlClash（手机 / 电脑）· Clash Meta for Android",
        "items": [
            {"tag": "推荐 · 只含可连节点", "note": "已剔除连不上的，成功率高",
             "fit": "Clash 系专用，v2rayNG 导入会报错",
             "freq": "每日更新", "url": "https://blog.ermao.net/sub/reachable/clash/ermao.net"},
            {"tag": "带规则分流", "note": "开箱即用，但国内直连实测常不通",
             "fit": "Clash 系专用，自带分流规则",
             "freq": "6 小时更新", "cn": True, "url": "https://radarhub.top/clash.yaml"},
            {"tag": "全量节点", "note": "含未测速的节点，量最大",
             "fit": "Clash 系专用，节点最全",
             "freq": "每日更新", "url": "https://blog.ermao.net/sub/clash/ermao.net"},
        ],
    },
    {
        "title": "V2Ray 系",
        "kern": "Xray 内核",
        "clients": "v2rayNG（安卓）· v2rayN（电脑）· Shadowrocket（iOS）· Karing",
        "items": [
            {"tag": "推荐 · 精选稳定", "note": "量少质高，失效慢",
             "fit": "v2rayNG / 小火箭直接导，Clash 不认",
             "freq": "6 小时更新",
             "url": "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub"},
            {"tag": "自动测速过", "note": "每 6 小时实测一遍再发布，但国内直连常不通",
             "fit": "v2rayNG / 小火箭直接导，Clash 不认",
             "freq": "6 小时更新", "cn": True, "url": "https://radarhub.top/v2ray.txt"},
            {"tag": "每日抓取", "note": "每天自动抓全网机场并检测连通性",
             "fit": "v2rayNG / 小火箭直接导，Clash 不认",
             "freq": "每日更新", "url": "https://blog.ermao.net/sub/v2ray/ermao.net"},
            {"tag": "全网合并", "note": "自动聚合多个公开来源",
             "fit": "明文节点，v2rayNG 可导，Clash 不认",
             "freq": "每日更新",
             "url": "https://raw.githubusercontent.com/chengaopan/AutoMergePublicNodes/master/list.txt"},
            {"tag": "老牌仓库", "note": "每小时更新一次",
             "fit": "Base64 订阅，v2rayNG / 小火箭",
             "freq": "每小时更新",
             "url": "https://raw.githubusercontent.com/free-nodes/v2rayfree/main/sub"},
            {"tag": "每日分享", "note": "节点和机场一起发",
             "fit": "明文节点，v2rayNG / 小火箭",
             "freq": "每日更新",
             "url": "https://raw.githubusercontent.com/free18/v2ray/main/v.txt"},
            {"tag": "备用源", "note": "前面几条都失败时用这条",
             "fit": "仓库名带 clash，实际是 v2ray 格式",
             "freq": "每日更新",
             "url": "https://raw.githubusercontent.com/marshall888666/free_clash_subscribe/main/subscribe/v2ray.txt"},
        ],
    },
    {
        "title": "sing-box 系",
        "kern": "sing-box 内核",
        "clients": "Hiddify（手机 / 电脑）· sing-box（安卓 / 苹果）",
        "items": [
            {"tag": "带规则 · 与 Clash 同源", "note": "格式不同，国内直连实测常不通",
             "fit": "仅 Hiddify / sing-box，Clash 不认",
             "freq": "6 小时更新", "cn": True, "url": "https://radarhub.top/sing-box.json"},
        ],
    },
]


# 采集校验用（与卡片对应，只用于统计条数和生成明细）
SOURCES = [
    ("Pawdroid",   ["https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub",
                    "https://cdn.jsdelivr.net/gh/Pawdroid/Free-servers@main/sub"]),
    ("nodesradar", ["https://radarhub.top/v2ray.txt"]),
    ("尔毛子",     ["https://blog.ermao.net/sub/v2ray/ermao.net"]),
    ("v2rayfree",  ["https://raw.githubusercontent.com/free-nodes/v2rayfree/main/sub",
                    "https://cdn.jsdelivr.net/gh/free-nodes/v2rayfree@main/sub"]),
    ("AutoMerge",  ["https://raw.githubusercontent.com/chengaopan/AutoMergePublicNodes/master/list.txt",
                    "https://cdn.jsdelivr.net/gh/chengaopan/AutoMergePublicNodes@master/list.txt"]),
    ("free18",     ["https://raw.githubusercontent.com/free18/v2ray/main/v.txt",
                    "https://cdn.jsdelivr.net/gh/free18/v2ray@main/v.txt"]),
    ("marshall",   ["https://raw.githubusercontent.com/marshall888666/free_clash_subscribe/main/subscribe/v2ray.txt",
                    "https://cdn.jsdelivr.net/gh/marshall888666/free_clash_subscribe@main/subscribe/v2ray.txt"]),
    ("zhuhaiuk",   ["https://raw.githubusercontent.com/zhuhaiuk/free-nodes/main/nodes.txt",
                    "https://cdn.jsdelivr.net/gh/zhuhaiuk/free-nodes@main/nodes.txt"]),
]

VALID_PROTO = {"vmess", "vless", "ss", "ssr", "trojan", "hysteria", "hysteria2", "hy2", "tuic", "socks5"}
PROTO_LABEL = {"vmess": "VMess", "vless": "VLESS", "ss": "Shadowsocks", "ssr": "SSR",
               "trojan": "Trojan", "hysteria": "Hysteria", "hysteria2": "Hysteria2", "hy2": "Hysteria2",
               "tuic": "TUIC", "socks5": "Socks5"}

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


def fetch(urls, name, use_cache=False):
    path = os.path.join(CACHE, re.sub(r"[^\w\-]", "_", name) + ".txt")
    if use_cache and os.path.exists(path):
        return open(path, "rb").read()
    if isinstance(urls, str):
        urls = [urls]
    last = None
    for u in urls:
        for _ in range(2):
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 v2rayN/6.0"})
                data = urllib.request.urlopen(req, timeout=25).read()
                if len(data) > 200:
                    open(path, "wb").write(data)
                    return data
            except Exception as e:
                last = e
    raise last or RuntimeError("全部地址失败")


def to_text(data):
    t = data.decode("utf-8", "ignore").strip()
    if "://" in t:
        return t
    try:
        return base64.b64decode(t + "=" * (-len(t) % 4)).decode("utf-8", "ignore")
    except Exception:
        return ""


def addr_key(line):
    try:
        if line.startswith("vmess://"):
            b = line[8:].split("#")[0]
            j = json.loads(base64.b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "ignore"))
            return f"{j.get('add','')}:{j.get('port','')}"
        body = line.split("#")[0]
        m = re.search(r"@([^@/?#]+?):(\d+)", body)
        if m:
            return m.group(1) + ":" + m.group(2)
        m = re.search(r"([a-zA-Z0-9][a-zA-Z0-9.\-]*\.[a-zA-Z]{2,}|\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})", body)
        if m:
            return m.group(1) + ":" + m.group(2)
    except Exception:
        pass
    if line.startswith("ss://"):
        try:
            b = line[5:].split("#")[0].rstrip("/")
            t = base64.b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "ignore")
            m = re.search(r"@?([^@:/]+):(\d+)", t.split("@")[-1])
            if m:
                return m.group(1) + ":" + m.group(2)
        except Exception:
            pass
    return None


def node_name(line):
    if "#" in line:
        import urllib.parse
        return urllib.parse.unquote(line.split("#", 1)[1])[:40]
    return ""


def region_of(name):
    for k, v in REGIONS:
        if k in name:
            return v
    return "其他"


def collect(use_cache=False):
    raw, stats, errors, results = [], [], [], {}

    def work(item):
        name, urls = item
        t0 = time.time()
        data = fetch(urls, name, use_cache)
        lines = [l.strip() for l in to_text(data).splitlines() if "://" in l]
        lines = [l for l in lines if l.split("://")[0].lower() in VALID_PROTO]
        return name, lines, int((time.time() - t0) * 1000)

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(work, it): it for it in SOURCES}
        for fu in as_completed(futs):
            name = futs[fu][0]
            try:
                n, lines, ms = fu.result()
                results[n] = lines
                stats.append({"name": n, "count": len(lines), "ms": ms})
                print(f"  ✔ {n:<10} {len(lines):>5} 条  {ms}ms")
            except Exception as e:
                errors.append(f"{name}: {str(e)[:50]}")
                print(f"  ✘ {name:<10} 失败 {str(e)[:40]}")

    order = [s[0] for s in SOURCES]
    stats.sort(key=lambda x: order.index(x["name"]))
    for name in order:
        for l in results.get(name, []):
            raw.append((name, l))

    seen, kept = set(), []
    for src, line in raw:
        k = addr_key(line) or line
        if k in seen:
            continue
        seen.add(k)
        nm = node_name(line)
        kept.append({"src": src, "uri": line, "proto": PROTO_LABEL.get(line.split("://")[0].lower(), "其他"),
                     "region": region_of(nm), "name": nm,
                     "addr": k if not k.startswith(("ss://", "vmess://", "vless://", "trojan://")) else "—"})
    return kept, stats, errors, len(raw)


TEMPLATE_PATH = os.path.join(BASE, "template.html")
_template_cache = None


def load_template():
    """读取页面模板。模板与采集逻辑分家：改样式只碰 template.html，不碰 Python。"""
    global _template_cache
    if _template_cache is None:
        with open(TEMPLATE_PATH, encoding="utf-8") as f:
            _template_cache = f.read()
    return _template_cache


def render_page(page):
    """把数据填进模板。__N__ 是条数，__DATA__ 是 JSON。

    JSON 里的 </ 转义成 <\/，否则数据中出现 </script> 会截断脚本块。
    """
    tpl = load_template()
    n = sum(len(g["items"]) for g in page["groups"])
    payload = json.dumps(page, ensure_ascii=False).replace("</", "<\\/")
    return tpl.replace("__N__", str(n)).replace("__DATA__", payload)


def count_of(data):
    """按内容格式统计节点条数：Clash 数 proxies、sing-box 数 outbounds、其余数 URI 行"""
    t = data.decode("utf-8", "ignore").strip()
    m = re.search(r"^proxies:", t, re.M)
    if m:
        n = 0
        for line in t[m.end():].splitlines():
            if not line.strip():
                continue
            s = line.lstrip()
            if s.startswith("- "):            # 列表项（缩进与否都算节点）
                n += 1
                continue
            if line[:1] not in (" ", "\t"):   # 顶层键 → proxies 段结束
                break
        return n or None
    if t[:1] == "{":
        try:
            j = json.loads(t)
            if isinstance(j.get("outbounds"), list):
                skip = {"selector", "urltest", "direct", "block", "dns", "relay"}
                return sum(1 for o in j["outbounds"] if isinstance(o, dict) and o.get("type") not in skip) or None
        except Exception:
            pass
    if "://" not in t:
        try:
            t = base64.b64decode(t + "=" * (-len(t) % 4)).decode("utf-8", "ignore")
        except Exception:
            return None
    ls = [l for l in t.splitlines() if "://" in l]
    ls = [l for l in ls if l.split("://")[0].lower() in VALID_PROTO]
    return len(ls) or None


def http_probe(url, timeout=12):
    """源站自身可达性：只读 512 字节就断开，记录状态码与首字节耗时。

    和「节点抽样可连率」是两件事：前者回答“这个源还活着吗”，
    后者回答“它发出来的节点通不通”。源站 5xx / 超时时百分比仍是上次的值，
    页面上必须能看出这一层，否则会拿旧数字当现状。
    """
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 v2rayN/6.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(512)
            return {"code": r.status, "ms": int((time.time() - t0) * 1000)}
    except urllib.error.HTTPError as e:
        return {"code": e.code, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"code": 0, "ms": int((time.time() - t0) * 1000), "err": type(e).__name__}


def probe_sources(items):
    """并发测每个订阅地址本身。失败不抛，返回 code=0 + 异常类名。"""
    urls = [i["url"] for i in items]
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(lambda u: http_probe(u), urls))
    out = {}
    for u, r in zip(urls, res):
        out[u] = r
        flag = "✔" if r["code"] == 200 else ("✘" if not r["code"] else f"✘{r['code']}")
        print(f"  {flag} 源站 {r['ms']:>5}ms  {u[:60]}")
    return out


def measure(items, use_cache=False):
    """逐条拉取订阅，统计真实节点条数 + 抽样测 TCP 连通性"""
    counts, addrs, texts = {}, {}, {}

    def work(it):
        nm = "c_" + hashlib.md5(it["url"].encode()).hexdigest()[:10]
        data = fetch([it["url"]], nm, use_cache)
        return it["url"], count_of(data), extract_addrs(data)

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(work, it) for it in items]
        for fu in as_completed(futs):
            try:
                u, n, a = fu.result()
                counts[u] = n
                addrs[u] = a
                print(f"  · {n if n else '—':>5} 条 / {len(a):>4} 个地址可测  {u[:56]}")
            except Exception as e:
                print(f"  ✘ 计数失败 {str(e)[:40]}")

    probes = probe_all(addrs)
    return counts, probes


def extract_addrs(data):
    """从订阅内容里抽出 host:port 列表（兼容 Clash YAML / sing-box JSON / URI 明文）"""
    t = data.decode("utf-8", "ignore")
    out = []

    # 1. Clash YAML：截取 proxies 段，按 server / port 顺序配对
    m = re.search(r"^proxies:", t, re.M)
    if m:
        seg = []
        for line in t[m.end():].splitlines():
            if line and line[0] not in (" ", "\t", "-"):
                break
            seg.append(line)
        body = "\n".join(seg)
        servers = re.findall(r"^\s*server:\s*[\"']?([^\"'\s,}]+)", body, re.M)
        ports = re.findall(r"^\s*port:\s*[\"']?(\d+)", body, re.M)
        for s, p in zip(servers, ports):
            out.append(f"{s}:{p}")
        if out:
            return out

    # 2. sing-box JSON
    if t.lstrip()[:1] == "{":
        try:
            j = json.loads(t)
            for o in j.get("outbounds", []):
                if not isinstance(o, dict):
                    continue
                s, p = o.get("server"), o.get("server_port")
                if s and p:
                    out.append(f"{s}:{p}")
            if out:
                return out
        except Exception:
            pass

    # 3. URI 明文 / Base64
    body = t
    if "://" not in body:
        try:
            body = base64.b64decode(body.strip() + "=" * (-len(body.strip()) % 4)).decode("utf-8", "ignore")
        except Exception:
            return out
    for line in body.splitlines():
        line = line.strip()
        if "://" not in line:
            continue
        proto = line.split("://")[0].lower()
        if proto not in VALID_PROTO:
            continue
        # UDP 类协议（QUIC）没法用 TCP 探测，跳过，避免误判成"死节点"
        if proto in ("hysteria", "hysteria2", "hy2", "tuic"):
            continue
        k = addr_key(line)
        if k and ":" in k and not k.startswith(("ss://", "vmess://", "vless://", "trojan://")):
            out.append(k)
    return out


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
        print(f"  ↳ 抽样 {t['total']:>2} 个，可连 {t['ok']:>2} 个（{rate:.0f}%）  {u[:56]}")
    return tally



HEALTH_PATH = os.path.join(OUT_DATA, "health.json")


def load_health():
    try:
        return json.load(open(HEALTH_PATH, encoding="utf-8"))
    except Exception:
        return {}


def update_health(old, probes):
    """累计每个源的连续低连通轮次；连续 2 轮 <30% 判定为不稳定"""
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
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
    for url, h in old.items():          # 本轮没测到的源保留旧记录
        new.setdefault(url, h)
    # 只在 CI（美国机房）里落盘：本地出口网络差异极大，会污染健康记录
    if os.environ.get("GITHUB_ACTIONS") == "true":
        json.dump(new, open(HEALTH_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    else:
        print("  （本地运行，不写入健康记录）")
    return new


def _rank(item, counts, probes):
    """排序分 = 可连率 × 条数，即「预计可用节点数」。
    没有测速或有没条数的源返回 -1，排在最后。"""
    p = probes.get(item["url"]) or {}
    total = int(p.get("total") or 0)
    ok = int(p.get("ok") or 0)
    n = counts.get(item["url"]) or 0
    if not total or not n:
        return -1.0
    return ok / total * n


def assemble(nodes, raw_total, errors, counts, probes, delta, health, srcstat=None):
    probes = probes or {}
    srcstat = srcstat or {}
    groups = []
    for g in GROUPS:
        items = [{"tag": i["tag"], "note": i["note"], "fit": i.get("fit", ""),
                  "freq": i["freq"], "url": i["url"], "count": counts.get(i["url"]),
                  "probe": probes.get(i["url"]),
                  "cnbad": bool(i.get("cn")),
                  "src_http": srcstat.get(i["url"]),
                  "score": round(_rank(i, counts, probes)),
                  "streak": int((health or {}).get(i["url"], {}).get("streak_bad") or 0)
                  } for i in g["items"]]
        # 国内直连不通的固定沉底；其余按「可连率 × 条数」降序，同分保持清单原顺序
        items.sort(key=lambda it: (it["cnbad"], -it["score"]))
        groups.append({"title": g["title"], "kern": g["kern"],
                       "clients": g["clients"], "items": items})
    return {
        "updated": datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M"),
        "ts": int(time.time()),
        "raw": raw_total, "errors": errors, "groups": groups, "delta": delta,
        "nodes": [{"name": n["name"], "proto": n["proto"], "region": n["region"],
                   "addr": n["addr"], "src": n["src"], "uri": n["uri"]} for n in nodes],
    }


def build_html(nodes, stats, raw_total, errors, counts, probes=None, delta=None,
               health=None, srcstat=None):
    data = assemble(nodes, raw_total, errors, counts, probes, delta, health, srcstat)
    page = {k: v for k, v in data.items() if k != "nodes"}
    open(os.path.join(BASE, "index.html"), "w", encoding="utf-8").write(render_page(page))
    open(os.path.join(OUT_DATA, "nodes.json"), "w", encoding="utf-8").write(
        json.dumps(data, ensure_ascii=False, indent=1))
    if srcstat:
        # 给第三方/自己监控用的机器可读状态：每条源的 HTTP 码与耗时
        json.dump({"updated": data["updated"], "ts": data["ts"],
                   "sources": [{"url": u, **v} for u, v in srcstat.items()]},
                  open(os.path.join(OUT_DATA, "status.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    return data


def rerender():
    """只按 data/nodes.json 重渲染 index.html：不联网、不改节点数据。
    改模板后本地预览用，避免为了看一眼样式又跑一遍采集。"""
    old = json.load(open(os.path.join(OUT_DATA, "nodes.json"), encoding="utf-8"))
    counts, probes, health, srcstat = {}, {}, {}, {}
    for g in old.get("groups", []):
        for it in g.get("items", []):
            counts[it["url"]] = it.get("count")
            if it.get("probe"):
                probes[it["url"]] = it["probe"]
            if it.get("src_http"):
                srcstat[it["url"]] = it["src_http"]
            health[it["url"]] = {"streak_bad": it.get("streak") or 0}
    data = assemble(old.get("nodes") or [], old.get("raw") or 0, old.get("errors") or [],
                    counts, probes, old.get("delta"), health, srcstat)
    data["updated"], data["ts"] = old.get("updated"), old.get("ts")
    page = {k: v for k, v in data.items() if k != "nodes"}
    out = os.path.join(BASE, "index.html")
    open(out, "w", encoding="utf-8").write(render_page(page))
    print(f"已重渲染 {out}（数据保持 {data['updated']}，未联网）")


def main():
    if "--render" in sys.argv:      # 只重渲染页面，不联网
        rerender()
        return
    use_cache = "--cache" in sys.argv
    print("开始采集节点...")
    nodes, stats, errors, raw = collect(use_cache)

    old_json = os.path.join(OUT_DATA, "nodes.json")
    delta = None
    if os.path.exists(old_json):
        try:
            old = json.load(open(old_json, encoding="utf-8"))
            old_nodes = old.get("nodes", [])
            old_n = len(old_nodes)
            if old_n >= 50 and len(nodes) < old_n * 0.6:
                print(f"\n⚠ 本次仅 {len(nodes)} 条，低于上次 {old_n} 条的 60%，判定采集不完整，保留旧数据")
                return
            old_set = {n["addr"] for n in old_nodes if n.get("addr") not in (None, "", "—")}
            new_set = {n["addr"] for n in nodes if n.get("addr") not in (None, "", "—")}
            if old_set and new_set:
                delta = {"add": len(new_set - old_set), "drop": len(old_set - new_set)}
                print(f"  对比上次：新增 {delta['add']} 个地址，消失 {delta['drop']} 个")
        except Exception:
            pass

    print("\n统计各条订阅的节点数...")
    items = [it for g in GROUPS for it in g["items"]]
    counts, probes = measure(items, use_cache)

    print("\n测各条源站自身可达性...")
    srcstat = probe_sources(items)

    health = update_health(load_health(), probes)
    for u, h in health.items():
        if h.get("streak_bad"):
            print(f"  ⚠ 连续 {h['streak_bad']} 轮低连通：{u[:60]}")

    build_html(nodes, stats, raw, errors, counts, probes, delta, health, srcstat)
    uris = [n["uri"] for n in nodes]
    with open(os.path.join(BASE, "订阅 全部节点 base64.txt"), "w") as f:
        f.write(base64.b64encode("\n".join(uris).encode()).decode())
    with open(os.path.join(BASE, "订阅 全部节点 明文.txt"), "w") as f:
        f.write("\n".join(uris))

    print(f"\n完成: 校验 {raw} 条，去重后 {len(nodes)} 条")
    print("输出: index.html / data/nodes.json")
    if errors:
        print("失败源:", "; ".join(errors))


if __name__ == "__main__":
    main()
