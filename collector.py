#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
免费节点订阅采集器
页面主体 = 各仓库官方发布的订阅地址（小白可直接复制使用）
采集仅用于统计每个源今天抓到多少条、并在页面底部提供节点明细

用法:
    python3 collector.py            # 联网采集
    python3 collector.py --cache    # 用本地缓存(调试)
"""
import base64, hashlib, json, os, re, socket, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.dirname(BASE), ".nodes-cache")
OUT_DATA = os.path.join(BASE, "data")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(OUT_DATA, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

# ── 订阅清单：页面按此结构展示，url 均为仓库原版地址 ──────────────
GROUPS = [
    {
        "title": "Clash 系",
        "kern": "mihomo 内核",
        "clients": "Clash Verge Rev（电脑）· FlClash（手机 / 电脑）· Clash Meta for Android",
        "items": [
            {"tag": "推荐 · 只含可连节点", "note": "已剔除连不上的，成功率高",
             "freq": "每日更新", "url": "https://blog.ermao.net/sub/reachable/clash/ermao.net"},
            {"tag": "带规则分流", "note": "开箱即用，不用自己配规则",
             "freq": "6 小时更新", "url": "https://radarhub.top/clash.yaml"},
            {"tag": "全量节点", "note": "含未测速的节点，量最大",
             "freq": "每日更新", "url": "https://blog.ermao.net/sub/clash/ermao.net"},
        ],
    },
    {
        "title": "V2Ray 系",
        "kern": "Xray 内核",
        "clients": "v2rayNG（安卓）· v2rayN（电脑）· Shadowrocket（iOS）· Karing",
        "items": [
            {"tag": "推荐 · 精选稳定", "note": "量少质高，失效慢",
             "freq": "6 小时更新",
             "url": "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub"},
            {"tag": "自动测速过", "note": "每 6 小时实测一遍再发布",
             "freq": "6 小时更新", "url": "https://radarhub.top/v2ray.txt"},
            {"tag": "每日抓取", "note": "每天自动抓全网机场并检测连通性",
             "freq": "每日更新", "url": "https://blog.ermao.net/sub/v2ray/ermao.net"},
            {"tag": "全网合并", "note": "自动聚合多个公开来源",
             "freq": "每日更新",
             "url": "https://raw.githubusercontent.com/chengaopan/AutoMergePublicNodes/master/list.txt"},
            {"tag": "老牌仓库", "note": "每小时更新一次",
             "freq": "每小时更新",
             "url": "https://raw.githubusercontent.com/free-nodes/v2rayfree/main/sub"},
            {"tag": "每日分享", "note": "节点和机场一起发",
             "freq": "每日更新",
             "url": "https://raw.githubusercontent.com/free18/v2ray/main/v.txt"},
            {"tag": "备用源", "note": "前面几条都失败时用这条",
             "freq": "每日更新",
             "url": "https://raw.githubusercontent.com/marshall888666/free_clash_subscribe/main/subscribe/v2ray.txt"},
        ],
    },
    {
        "title": "sing-box 系",
        "kern": "sing-box 内核",
        "clients": "Hiddify（手机 / 电脑）· sing-box（安卓 / 苹果）",
        "items": [
            {"tag": "推荐 · 带规则", "note": "与上面的 Clash 同源，格式不同",
             "freq": "6 小时更新", "url": "https://radarhub.top/sing-box.json"},
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


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="theme-color" content="#E9EDF5">
<title>节点配置链接</title>
<style>
:root{--bg:#E4E9F2;--surf:#fff;--surf2:#F4F6FB;--line:#E2E6EF;--line2:#EDF0F6;
--fg:#0E1726;--dim:#667085;--dim2:#98A2B3;--pri:#0B57D0;--priC:#E3ECFB;--ok:#0B8A44;
--sh1:0 1px 2px rgba(16,24,40,.05),0 3px 8px rgba(16,24,40,.05),0 16px 26px -20px rgba(16,24,40,.18);
--sh2:0 1px 2px rgba(16,24,40,.05),0 12px 26px -10px rgba(16,24,40,.16)}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{overflow-x:hidden}
body{background:var(--bg);color:var(--fg);padding:0 14px 44px;-webkit-font-smoothing:antialiased;
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Roboto","PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif}
body:before{content:"";position:fixed;left:0;right:0;top:0;height:360px;pointer-events:none;z-index:0;
background:radial-gradient(880px 260px at 50% -70px,#cddafa 0%,rgba(233,237,245,0) 74%)}
body:after{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
background-image:radial-gradient(rgba(15,23,42,.085) .8px,transparent .8px);background-size:15px 15px;
-webkit-mask-image:linear-gradient(180deg,rgba(0,0,0,.85),rgba(0,0,0,.3) 50%,transparent 88%);
mask-image:linear-gradient(180deg,rgba(0,0,0,.85),rgba(0,0,0,.3) 50%,transparent 88%)}
.wrap{position:relative;z-index:1;max-width:560px;margin:0 auto;width:100%}
header{padding:26px 2px 15px}
h1{font-size:27px;font-weight:800;letter-spacing:-.6px;text-align:center;line-height:1.25;
background:linear-gradient(96deg,#0B3FA6 0%,#2C6FE8 48%,#0B57D0 100%);
-webkit-background-clip:text;background-clip:text;color:transparent;
filter:drop-shadow(0 2px 6px rgba(11,87,208,.18))}
.sub{color:var(--dim);font-size:12.5px;margin-top:7px;text-align:center}
.pills{display:flex;gap:6px;flex-wrap:wrap;margin-top:11px;justify-content:center}
.pill{background:rgba(255,255,255,.72);border:1px solid var(--line);color:#475467;font-size:11.5px;padding:3px 9px;border-radius:99px}
.mirror{margin-top:9px;display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap;font-size:11.5px;color:var(--dim2)}
.mirror a{color:#0B57D0;text-decoration:none;background:rgba(255,255,255,.8);border:1px solid #C9D9F5;
border-radius:99px;padding:2px 9px;font-weight:600;transition:.15s}
.mirror a:active{background:#E3ECFB}
.sec{background:linear-gradient(180deg,#fff,#FBFCFE);border:1px solid var(--line);border-radius:18px;
padding:13px 14px 3px;box-shadow:var(--sh2);margin-bottom:20px}
.sec-t{font-size:14.5px;font-weight:700;margin-bottom:0}
.sec-t span{display:block;font-size:11.5px;color:var(--dim);font-weight:400;margin-top:2px}
.gs{display:flex;gap:10px;padding:7px 0;border-bottom:1px dashed var(--line2)}
.gs .i{flex:0 0 22px;height:22px;border-radius:8px;color:#fff;font-size:12.5px;font-weight:700;
display:flex;align-items:center;justify-content:center;background:linear-gradient(180deg,#2C6FE8,#0B57D0);
box-shadow:0 2px 6px rgba(11,87,208,.32),inset 0 1px 0 rgba(255,255,255,.42)}
.gs b{font-size:13px;font-weight:650}
.gs p{font-size:12px;color:var(--dim);margin-top:0;line-height:1.5}
h2.gt{display:flex;align-items:center;gap:8px;font-size:19.5px;font-weight:800;margin:22px 0 3px;
color:#0B3FA6;letter-spacing:-.3px}
.badge{flex:0 0 25px;height:25px;border-radius:8px;color:#fff;font-size:12.5px;font-weight:700;
display:flex;align-items:center;justify-content:center;background:linear-gradient(180deg,#2C6FE8,#0B57D0);
box-shadow:0 2px 6px rgba(11,87,208,.3),inset 0 1px 0 rgba(255,255,255,.35)}
.kern{margin-left:auto;font-size:10.5px;font-weight:700;color:#0A47A9;background:var(--priC);padding:3px 9px;border-radius:99px}
.gdesc{font-size:11.5px;color:var(--dim);margin:0 0 11px 31px}
.card{background:linear-gradient(180deg,#FFF,#FCFDFF);border:1px solid var(--line);border-radius:16px;
padding:12px 14px 13px;margin-bottom:10px;box-shadow:var(--sh1);transition:.18s;overflow:hidden;position:relative}
.card.hi{background:linear-gradient(180deg,#FBFDFF,#F2F7FF);border-color:#B7CDF4}
.card:after{content:"";position:absolute;left:0;right:0;top:0;height:1px;
background:linear-gradient(90deg,transparent,rgba(255,255,255,.95),transparent)}
.ch{display:flex;align-items:center;gap:8px}
.tag{font-size:14.5px;font-weight:800;color:#0B57D0;letter-spacing:-.2px}
.freq{margin-left:auto;font-size:10.5px;color:var(--dim);background:var(--surf2);
border:1px solid var(--line2);padding:2px 8px;border-radius:99px;white-space:nowrap}
.cnum{display:flex;align-items:baseline;gap:4px;margin:3px 0 2px}
.cnum.live b{color:#0B57D0}
.cnum b{font-size:17px;font-weight:700;letter-spacing:0;line-height:1.3;
font-variant-numeric:tabular-nums;color:#475467}
.cnum span{font-size:11px;color:var(--dim2)}
.cnote{font-size:11.5px;color:var(--dim2);margin-bottom:6px}
.probe{display:flex;align-items:center;gap:7px;margin:0 0 9px;font-size:10.5px;color:#5A6B85}
.probe .pl{white-space:nowrap;font-variant-numeric:tabular-nums}
.probe .bar{flex:1;min-width:40px;height:4px;border-radius:99px;background:#E8ECF3;overflow:hidden}
.probe .bar i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#34A853,#0B8A44)}
.probe .pv{white-space:nowrap;font-weight:700;color:#0B8A44;font-variant-numeric:tabular-nums}
.probe.warn .bar i{background:linear-gradient(90deg,#FDB022,#DC6803)}
.probe.warn .pv{color:#B54708}
.probe.bad .bar i{background:linear-gradient(90deg,#FDA29B,#D92D20)}
.probe.bad .pv{color:#B42318}
.url{display:block;max-width:100%;background:var(--surf2);border:1px solid var(--line2);border-radius:11px;
padding:9px 10px;font:11.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#31405A;
word-break:break-all;overflow-wrap:anywhere;white-space:normal;user-select:all;cursor:pointer}
.cbtn{width:100%;margin-top:9px;border:0;border-radius:12px;padding:11px 0;font-size:13.5px;font-weight:650;
color:#fff;cursor:pointer;background:linear-gradient(180deg,#2C6FE8,#0B57D0);
box-shadow:0 2px 6px rgba(11,87,208,.28),inset 0 1px 0 rgba(255,255,255,.28);transition:.15s}
.cbtn:active{transform:scale(.99)}
.cbtn.ok{background:linear-gradient(180deg,#12A150,#0B8A44);box-shadow:0 2px 6px rgba(11,138,68,.28)}
background:#fff;color:#475467;cursor:pointer;white-space:nowrap}
th:nth-child(2),td:nth-child(2){width:15%}
th:nth-child(3),td:nth-child(3){width:16%}
th:nth-child(4),td:nth-child(4){width:31%}
th:nth-child(5),td:nth-child(5){width:11%}
.foot{color:#8B94A3;font-size:11px;text-align:center;margin-top:22px;line-height:1.8}
</style></head><body><div class="wrap">
<header>
  <h1>节点配置链接</h1>
  <p class="sub">复制任意一条 → 粘进客户端 → 更新，就能用</p>
  <div class="pills"><span class="pill" id="p2">14 条链接</span>
  <span class="pill" id="p3">数据 3 小时更新</span>
  <span class="pill" id="p4" style="display:none"></span></div>
  <div class="mirror"><span>打不开时用备用入口</span>
    <a href="https://cdn.jsdelivr.net/gh/BOB0098/nodes@main/index.html" target="_blank" rel="noopener">① jsDelivr</a>
    <a href="https://ghproxy.net/https://raw.githubusercontent.com/BOB0098/nodes/main/index.html" target="_blank" rel="noopener">② ghproxy</a>
  </div>
</header>

<section class="sec">
  <div class="sec-t">怎么用<span>所有客户端都这三步</span></div>
  <div class="gs"><span class="i">1</span><div><b>复制一条链接</b><p>点下面任意卡片的「复制链接」按钮</p></div></div>
  <div class="gs"><span class="i">2</span><div><b>导入客户端</b><p>客户端的「配置」或「订阅」页 → 点 + → 从剪贴板导入</p></div></div>
  <div class="gs"><span class="i">3</span><div><b>更新并连接</b><p>点这条配置的「更新」→ 选中它 → 打开代理开关</p></div></div>
</section>

<div id="groups"></div>

<div class="foot">链接均为仓库原样发布，未做任何改写<br>条数为采集时统计 · 百分比 = 从该源抽 24 个节点测 TCP 连通的比例<br>只测 TCP 类节点（hy2 / tuic 走 UDP，不计入）· 测试点在美国机房，国内实连率会低一些</div>
</div>
<script>
const DATA = __DATA__;
const CN = ['','一','二','三','四'];
function probeLine(p) {
  if (!p || !p.total) return '';
  const r = p.ok / p.total;
  const cls = r >= 0.5 ? '' : (r >= 0.2 ? 'warn' : 'bad');
  const pct = Math.round(r * 100);
  const txt = p.ok === 0 ? `抽样 ${p.total} 个 · 全部不通` : `抽样 ${p.total} 个 · 可连 ${p.ok}`;
  return `<div class="probe ${cls}"><span class="pl">${txt}</span>
    <span class="bar"><i style="width:${Math.max(pct, 4)}%"></i></span><span class="pv">${pct}%</span></div>`;
}
const gEl = document.getElementById('groups');
DATA.groups.forEach((g, gi) => {
  const head = document.createElement('div');
  head.innerHTML = `<h2 class="gt"><span class="badge">${CN[gi+1]}</span>${g.title}<span class="kern">${g.kern}</span></h2>
    <div class="gdesc">${g.clients}</div>`;
  gEl.appendChild(head);
  g.items.forEach(it => {
    const c = document.createElement('div');
    c.className = it.tag.indexOf('推荐') === 0 ? 'card hi' : 'card';
    c.innerHTML = `<div class="ch"><span class="tag">${it.tag}</span><span class="freq">${it.freq}</span></div>
      <div class="cnum"><b>${it.count === null ? '—' : it.count.toLocaleString()}</b><span>条节点</span></div>
      ${probeLine(it.probe)}
      <div class="cnote">${it.note}</div>
      <code class="url">${it.url}</code>
      <button class="cbtn">复制链接</button>`;
    const btn = c.querySelector('.cbtn'), url = c.querySelector('.url');
    const done = () => { btn.textContent = '已复制 ✓'; btn.classList.add('ok');
      setTimeout(() => { btn.textContent = '复制链接'; btn.classList.remove('ok'); }, 1600); };
    const cp = () => navigator.clipboard.writeText(it.url).then(done).catch(() => {
      const r = document.createRange(); r.selectNodeContents(url);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r);
      btn.textContent = '已选中，长按复制';
      setTimeout(() => { btn.textContent = '复制链接'; }, 2200); });
    btn.onclick = cp; url.onclick = cp;
    gEl.appendChild(c);
  });
});
document.getElementById('p2').textContent = DATA.groups.reduce((a, g) => a + g.items.length, 0) + ' 条链接';
if (DATA.delta && (DATA.delta.add || DATA.delta.drop)) {
  const p4 = document.getElementById('p4');
  p4.textContent = `今日 +${DATA.delta.add} / −${DATA.delta.drop}`;
  p4.style.display = '';
}
</script></body></html>"""


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



def build_html(nodes, stats, raw_total, errors, counts, probes=None, delta=None):
    probes = probes or {}
    groups = []
    for g in GROUPS:
        groups.append({
            "title": g["title"], "kern": g["kern"], "clients": g["clients"],
            "items": [{"tag": i["tag"], "note": i["note"], "freq": i["freq"],
                       "url": i["url"], "count": counts.get(i["url"]),
                       "probe": probes.get(i["url"])} for i in g["items"]],
        })
    data = {
        "updated": datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M"),
        "raw": raw_total, "errors": errors, "groups": groups, "delta": delta,
        "nodes": [{"name": n["name"], "proto": n["proto"], "region": n["region"],
                   "addr": n["addr"], "src": n["src"], "uri": n["uri"]} for n in nodes],
    }
    page = {k: v for k, v in data.items() if k != "nodes"}
    html = HTML.replace("__DATA__", json.dumps(page, ensure_ascii=False))
    open(os.path.join(BASE, "index.html"), "w", encoding="utf-8").write(html)
    open(os.path.join(OUT_DATA, "nodes.json"), "w", encoding="utf-8").write(
        json.dumps(data, ensure_ascii=False, indent=1))
    return data


def main():
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

    build_html(nodes, stats, raw, errors, counts, probes, delta)
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
