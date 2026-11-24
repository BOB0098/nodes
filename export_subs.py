#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P8：多格式订阅输出。

把统一模型里的节点转成客户端真正能吃下的四种形态，并按质量/地区分组：

    Mihomo/Clash YAML · sing-box JSON · V2Ray URI 列表 · 整体 Base64

分组：all / best / stable / lowlat / fast / hk / tw / jp / sg / us / kr / eu。

三条硬规则：

1. **未通过真实检测的节点不进任何分组**（除 all）。P6 测过才知道谁能用，
   没测过的按「未验证」处理——任务书 §8 明确要求「待测、失败和未验证节点
   不得进入优选订阅」。
2. **每种格式都要反解校验**。生成完立刻用自己的解析器读回来，条数对不上
   就报错。只改扩展名冒充转换成功，是最容易也最难发现的一类错误。
3. **输出含凭据，只留在 export/**（gitignore + artifact 1 天）。
   订阅文件本身就是凭据集合，公开托管等于把别人的账号挂在网上。

streaming / ai 两个分组没有实现：那需要流媒体与 AI 服务的解锁检测，
P6 没有做（见 nodecheck.py 的说明）。不生成空文件充数，报告里写清楚。
"""

import base64
import json
import os
import sys

import yaml

import netsafe
import nodes_parse
from nodecheck import to_proxy

BASE = os.path.dirname(os.path.abspath(__file__))
EXPORT = os.path.join(BASE, "export")
SUBS = os.path.join(EXPORT, "subs")
MANIFEST = os.path.join(BASE, "data", "subscriptions.json")

UA = "clash.meta"

# 分组定义。(键, 中文名, 筛选函数)，顺序就是页面/清单里的顺序
GROUPS = [
    ("all", "全部节点", lambda n, s, c: True),
    ("best", "优选（真实可用且评分 B 以上）",
     lambda n, s, c: (c.get("ok") and s.get("s", 0) >= 65)),
    ("stable", "稳定（连续三轮可用）",
     lambda n, s, c: "稳定" in (s.get("t") or [])),
    ("lowlat", "低延迟（<200ms）",
     lambda n, s, c: c.get("ok") and (c.get("ms") or 9999) < 200),
    ("fast", "高速（≥2MB/s）",
     lambda n, s, c: c.get("ok") and (c.get("bps") or 0) >= 2_000_000),
    ("hk", "香港", lambda n, s, c: s.get("rg") == "香港"),
    ("tw", "中国台湾", lambda n, s, c: s.get("rg") == "中国台湾"),
    ("jp", "日本", lambda n, s, c: s.get("rg") == "日本"),
    ("sg", "新加坡", lambda n, s, c: s.get("rg") == "新加坡"),
    ("us", "美国", lambda n, s, c: s.get("rg") == "美国"),
    ("kr", "韩国", lambda n, s, c: s.get("rg") == "韩国"),
    ("eu", "欧洲", lambda n, s, c: s.get("rg") == "欧洲"),
]


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def tag_of(n, i):
    """客户端里的显示名。带序号保证唯一——不同源经常有同名节点，
    重名会让 Clash 直接拒绝整份配置。"""
    name = (n.get("name") or n.get("server") or "").strip()[:24]
    return f"{name or 'node'}-{i:04d}"


# ── 三种格式的生成 ──────────────────────────────────────────────────────────

def build_clash(nodes):
    proxies, names = [], []
    for i, n in enumerate(nodes):
        p = to_proxy(n, tag_of(n, i))
        if p:
            proxies.append(p)
            names.append(p["name"])
    if not proxies:
        return None
    cfg = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "proxies": proxies,
        "proxy-groups": [
            {"name": "节点选择", "type": "select", "proxies": ["自动选择"] + names},
            {"name": "自动选择", "type": "url-test", "url":
             "http://www.gstatic.com/generate_204", "interval": 300,
             "tolerance": 50, "proxies": names},
        ],
        "rules": ["MATCH,节点选择"],
    }
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=200)


def _sb_out(n, tag):
    """统一模型 → sing-box outbound。字段名按 sing-box 1.11+ 的 schema。"""
    t = n.get("type")
    mode = {"ss": "shadowsocks"}.get(t, t)
    if mode == "socks5":
        mode = "socks"
    o = {"type": mode, "tag": tag, "server": n["server"], "server_port": int(n["port"])}
    cred = str(n.get("cred") or "")

    tls = {}
    if n.get("sni"):
        tls["server_name"] = n["sni"]
    if n.get("security") == "reality":
        tls["reality"] = {"enabled": True, "public_key": n.get("public_key") or "",
                          "short_id": n.get("short_id") or ""}
        tls["utls"] = {"enabled": True, "fingerprint": n.get("fingerprint") or "chrome"}
    # hysteria2 / tuic / anytls 的 TLS 是协议的一部分，不是可选参数：
    # 这三类节点如果源里没写 sni，tls 会是个空 dict，`if tls` 判假、不写 tls 块，
    # sing-box 直接 `initialize outbound[N]: TLS required` 拒绝整份配置。
    # 之前只测到「我们自己读得回来」，这类错误只有客户端内核才拦得住。
    if tls or n.get("tls") or t in ("trojan", "hysteria2", "tuic", "anytls"):
        tls.setdefault("enabled", True)
        # 与 nodecheck 同口径：免费节点大量用自签证书，强制校验会让它们全部导入失败
        tls["insecure"] = True
        o["tls"] = tls

    tr = {}
    net = (n.get("network") or "tcp").lower()
    if net in ("ws", "websocket"):
        tr = {"type": "ws", "path": n.get("path") or "/"}
        if n.get("host"):
            tr["headers"] = {"Host": n["host"]}
    elif net == "grpc":
        tr = {"type": "grpc", "service_name": n.get("path") or ""}
    elif net in ("h2", "http"):
        tr = {"type": "http", "path": n.get("path") or "/"}
        if n.get("host"):
            tr["host"] = [n["host"]]
    if tr:
        o["transport"] = tr

    if t == "ss":
        o["method"] = n.get("method") or n.get("cipher") or "aes-256-gcm"
        o["password"] = cred
    elif t == "vmess":
        o.update({"uuid": cred, "security": n.get("cipher") or "auto",
                  "alter_id": int(n.get("alter_id") or 0)})
    elif t == "vless":
        o["uuid"] = cred
        if n.get("flow"):
            # sing-box 只认 `xtls-rprx-vision`。源里混着 Xray 的变体写法
            # `xtls-rprx-vision-udp443`（意思是 vision + 放行 udp/443），
            # 直接透传会让 sing-box 报 `unsupported flow` 并拒绝整份配置。
            # 丢掉后缀语义不变——那个开关在 sing-box 里没有对应项。
            flow = str(n["flow"])
            o["flow"] = "xtls-rprx-vision" if flow.startswith("xtls-rprx-vision") else flow
    elif t == "trojan":
        o["password"] = cred
    elif t == "hysteria2":
        o["password"] = cred
        if n.get("obfs"):
            o["obfs"] = {"type": n["obfs"], "password": n.get("obfs_password") or ""}
    elif t == "tuic":
        uuid, _, pwd = cred.partition(":")
        o.update({"uuid": uuid, "password": pwd})
        if n.get("congestion"):
            o["congestion_control"] = n["congestion"]
    elif t == "socks5":
        user, _, pwd = cred.partition(":")
        if user:
            o.update({"username": user, "password": pwd})
    elif t == "anytls":
        o["password"] = cred
    else:
        return None
    return o


def build_singbox(nodes):
    outs, tags = [], []
    for i, n in enumerate(nodes):
        tag = tag_of(n, i)
        o = _sb_out(n, tag)
        if o:
            outs.append(o)
            tags.append(tag)
    if not outs:
        return None
    head = [
        {"type": "selector", "tag": "节点选择", "outbounds": ["自动选择"] + tags,
         "default": "自动选择"},
        {"type": "urltest", "tag": "自动选择", "outbounds": tags,
         "url": "http://www.gstatic.com/generate_204", "interval": "5m"},
    ]
    doc = {"log": {"level": "info"},
           "outbounds": head + outs + [{"type": "direct", "tag": "direct"}]}
    return json.dumps(doc, ensure_ascii=False, indent=1)


def build_uris(nodes):
    return "\n".join(n["uri"] for n in nodes if n.get("uri"))


# ── 校验：生成完立刻用解析器读回来 ──────────────────────────────────────────

def verify(kind, text, expect):
    """返回 (ok, 实际条数, 说明)。这里的目的是抓「生成了但读不回来」，
    不是重复测一遍语义——读得回来且条数对得上，就说明结构没过期。"""
    if not text:
        return False, 0, "空内容"
    try:
        if kind == "clash":
            doc = yaml.safe_load(text)
            n = len(doc.get("proxies") or [])
            grp = len(doc.get("proxy-groups") or [])
            return (n == expect and grp >= 2), n, f"{n} 个节点 / {grp} 个策略组"
        if kind == "singbox":
            doc = json.loads(text)
            outs = doc.get("outbounds") or []
            n = sum(1 for o in outs if o.get("type") not in
                    ("selector", "urltest", "direct", "block", "dns"))
            return (n == expect), n, f"{len(outs)} 个 outbound（其中 {n} 个节点）"
        if kind == "uri":
            parsed, _ = nodes_parse.parse_any(text)
            return (len(parsed) == expect), len(parsed), f"{len(parsed)} 条可解析"
        if kind == "b64":
            raw = base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8", "ignore")
            parsed, _ = nodes_parse.parse_any(raw)
            return (len(parsed) == expect), len(parsed), f"解出 {len(parsed)} 条"
    except Exception as e:
        return False, 0, f"{type(e).__name__}: {e}"
    return False, 0, "未知格式"


def main():
    nodes = load(os.path.join(EXPORT, "nodes_full.json"), [])
    if not nodes:
        print("没有 export/nodes_full.json，先跑 collector.py")
        return 1
    check = load(os.path.join(BASE, "data", "nodecheck.json"), {})
    scores = load(os.path.join(BASE, "data", "node_scores.json"), {})
    ck = check.get("nodes") or {}
    sc = scores.get("nodes") or {}

    os.makedirs(SUBS, exist_ok=True)
    # 生成物每一行都是一个可用凭据。目录只给属主，别的用户列不出来
    for d in (os.path.dirname(SUBS), SUBS):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    manifest = {"updated": check.get("updated"), "formats": ["clash", "singbox", "uri", "b64"],
                "groups": [], "failed": []}

    for key, label, pred in GROUPS:
        picked = []
        for n in nodes:
            fp = n.get("fp")
            c = ck.get(fp) or {}
            s = sc.get(fp) or {}
            # all 之外一律要求「真实测过且通过」：没测过 = 未验证，不能算可用
            if key != "all" and not c.get("ok"):
                continue
            try:
                if pred(n, s, c):
                    picked.append(n)
            except Exception:
                continue
        if not picked:
            manifest["groups"].append({"key": key, "label": label, "count": 0,
                                       "note": "无符合条件的节点"})
            continue

        row = {"key": key, "label": label, "count": len(picked), "files": {}}
        for fmt, builder, ext in (("clash", build_clash, "yaml"),
                                  ("singbox", build_singbox, "json"),
                                  ("uri", build_uris, "txt")):
            text = builder(picked)
            if fmt == "uri":
                # 空行会让部分客户端把下一行当注释，过滤掉
                text = "\n".join(x for x in (text or "").splitlines() if x.strip())
            ok, got, why = verify(fmt, text or "", len(picked))
            if not ok:
                manifest["failed"].append(f"{key}.{ext}: {why}（期望 {len(picked)}）")
                continue
            path = os.path.join(SUBS, f"{key}.{ext}")
            netsafe.write_atomic(path, text)
            row["files"][fmt] = {"file": f"{key}.{ext}", "verified": got}

        # Base64 版本给只吃整体编码的客户端（v2rayN 的「订阅内容」）
        uri_text = build_uris(picked)
        b64 = base64.b64encode(uri_text.encode()).decode()
        ok, got, why = verify("b64", b64, len(picked))
        if ok:
            netsafe.write_atomic(os.path.join(SUBS, f"{key}.b64"), b64)
            row["files"]["b64"] = {"file": f"{key}.b64", "verified": got}
        else:
            manifest["failed"].append(f"{key}.b64: {why}（期望 {len(picked)}）")

        manifest["groups"].append(row)

    # 未实现的分组如实写进清单，不留空文件冒充
    manifest["not_implemented"] = {
        "streaming": "需要流媒体解锁检测，P6 未实现",
        "ai": "需要 AI 服务可用性检测，P6 未实现",
    }

    force = (os.environ.get("GITHUB_ACTIONS") == "true"
             or os.environ.get("NODES_FORCE_WRITE") == "1")
    if force:
        netsafe.write_atomic(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))

    print(f"\n订阅输出 -> {os.path.relpath(SUBS, BASE)}")
    print(f"  {'分组':<8}{'条数':>7}  格式校验")
    for g in manifest["groups"]:
        fmts = " ".join(f"{k}✔" for k in g.get("files", {}))
        print(f"  {g['key']:<8}{g['count']:>7}  {fmts or g.get('note', '')}")
    if manifest["failed"]:
        print("\n校验失败：")
        for f in manifest["failed"]:
            print("  ✘", f)
        return 1
    print("\n全部格式反解校验通过")
    return 0


def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("  ✔ " if c else "  ✘ ") + m)
        ok = ok and c

    ns = [
        {"type": "vmess", "server": "1.2.3.4", "port": 443, "cred": "u-1",
         "network": "ws", "path": "/x", "host": "a.com", "tls": "tls",
         "sni": "a.com", "name": "JP-1", "uri": "vmess://abc"},
        {"type": "anytls", "server": "5.6.7.8", "port": 8443, "cred": "pw",
         "sni": "b.com", "name": "HK-1", "uri": "anytls://pw@5.6.7.8:8443"},
        {"type": "socks5", "server": "9.9.9.9", "port": 1080, "cred": "u:p",
         "name": "S5", "uri": "socks5://u:p@9.9.9.9:1080"},
    ]
    y = build_clash(ns)
    chk(verify("clash", y, 3)[0], "Clash YAML 可反解且条数一致")
    chk("节点选择" in y and "自动选择" in y, "策略组存在")
    j = build_singbox(ns)
    chk(verify("singbox", j, 3)[0], "sing-box JSON 可反解")
    chk('"selector"' in j and '"urltest"' in j, "selector/urltest 存在")
    u = build_uris(ns)
    chk(len(u.splitlines()) == 3, "URI 行数")

    # 名字重复必须被序号化解，否则 Clash 整份配置会被拒
    dup = [dict(ns[0]), dict(ns[0])]
    tags = [tag_of(n, i) for i, n in enumerate(dup)]
    chk(tags[0] != tags[1], "重名节点自动加序号")

    # 校验函数必须能发现条数不符，否则等于没校验
    chk(not verify("clash", y, 4)[0], "条数不符时校验失败")
    chk(not verify("singbox", j, 99)[0], "sing-box 条数不符时失败")

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main() or 0)
