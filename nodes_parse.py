"""节点解析与标准化。

原采集器只保留含 "://" 的行，导致 Clash YAML 与 sing-box JSON 里的节点
一条都进不了库（anaer 2169 条、Barabama 2050 条贡献 0）。本模块把三种
常见载体统一解析成同一套节点模型，再合成标准 URI 供导出。

统一模型（dict）：
    type   协议键，如 ss / vmess / vless / trojan / hysteria2 / tuic
    name   节点名（可能为空）
    server 主机名或 IP
    port   端口（int）
    cred   凭据：ss/vless/vmess 的 method+password 或 uuid，trojan 用 password
    params 其余影响连通性的参数（cipher / network / tls / sni / path / host ...）

指纹 fp 用全部字段的规范化哈希，用来去重。旧实现按 host:port 去重，
实测把 1926 条「同机不同凭据」的节点误判为重复（样本的 40%）。
"""

import base64
import hashlib
import ipaddress
import json
import re
import urllib.parse as up

import yaml

# 参与指纹计算的参数键（顺序无关，缺失即空串）
FP_KEYS = ("type", "server", "port", "cred", "method", "cipher", "uuid",
           "network", "security", "tls", "sni", "alpn", "path", "host",
           "flow", "obfs", "obfs_password", "plugin", "plugin_opts",
           "alter_id", "public_key", "short_id", "fingerprint", "congestion")

SKIP_SINGBOX = {"selector", "urltest", "direct", "block", "dns", "relay", "tor"}


def _b64d(s):
    """宽松 base64 解码：补 padding、兼容 -_ 与无 padding。"""
    s = s.strip().replace("\n", "")
    s = s.replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "ignore")


def _b64e(s):
    return base64.b64encode(s.encode()).decode().rstrip("=")


def norm_host(h):
    h = (h or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return h


def make_fp(node):
    """全参数指纹。同一 IP 上凭据/传输参数不同 → 指纹不同 → 都保留。"""
    parts = []
    for k in FP_KEYS:
        v = node.get(k)
        if v in (None, "", 0, False):
            continue
        parts.append(f"{k}={v}")
    raw = "|".join(sorted(parts))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def addr_of(node):
    host, port = node.get("server"), node.get("port")
    if not host or not port:
        return "—"
    return f"{host}:{port}"


def is_public_host(h):
    """过滤回环 / 私有 / 保留地址。

    anaer 的 YAML 里混了 10 条 127.0.0.53:80..89（systemd-resolved 的本地
    DNS 占位），这类地址在用户机器上指向它自己，检测必然"通"但毫无意义。
    """
    h = (h or "").strip().lower().strip("[]")
    if not h or h in ("localhost", "0.0.0.0", "::"):
        return False
    try:
        ip = ipaddress.ip_address(h)
        if ip.is_loopback or ip.is_private or ip.is_reserved or ip.is_unspecified:
            return False
        return True
    except ValueError:
        return not (h.endswith(".local") or h.endswith(".lan") or h.endswith(".internal"))


# ── URI 合成 ────────────────────────────────────────────────────────────────

def _qs(**kw):
    items = [(k.replace("_", "-"), str(v)) for k, v in kw.items() if v not in (None, "", [])]
    return ("?" + "&".join(f"{up.quote(k)}={up.quote(str(v), safe='')}" for k, v in items)) if items else ""


def _frag(name):
    return "#" + up.quote(name or "", safe="") if name else ""


def to_uri(n):
    """把统一模型合成为标准分享链接。不支持的组合返回空串。"""
    t = n.get("type")
    host, port = n.get("server", ""), n.get("port", 0)
    name = n.get("name", "")
    if not host or not port:
        return ""

    if t == "ss":
        method = n.get("method") or n.get("cipher") or "aes-256-gcm"
        userinfo = _b64e(f"{method}:{n.get('cred', '')}")
        return f"ss://{userinfo}@{host}:{port}{_frag(name)}"

    if t == "vmess":
        j = {"v": "2", "ps": name, "add": host, "port": str(port),
             "id": n.get("cred") or n.get("uuid", ""), "aid": str(n.get("alter_id") or 0),
             "scy": n.get("cipher") or "auto", "net": n.get("network") or "tcp",
             "type": "none", "host": n.get("host", ""), "path": n.get("path", ""),
             "tls": n.get("tls") or "", "sni": n.get("sni", ""), "alpn": n.get("alpn", "")}
        return "vmess://" + _b64e(json.dumps(j, ensure_ascii=False))

    if t == "vless":
        q = _qs(encryption="none", security=n.get("security") or n.get("tls") or "none",
                sni=n.get("sni"), type=n.get("network") or "tcp", path=n.get("path"),
                host=n.get("host"), flow=n.get("flow"), pbk=n.get("public_key"),
                sid=n.get("short_id"), fp=n.get("fingerprint"), alpn=n.get("alpn"))
        return f"vless://{n.get('cred') or n.get('uuid', '')}@{host}:{port}{q}{_frag(name)}"

    if t == "trojan":
        q = _qs(security="tls", sni=n.get("sni"), type=n.get("network") or "tcp",
                path=n.get("path"), host=n.get("host"), alpn=n.get("alpn"))
        return f"trojan://{up.quote(str(n.get('cred', '')), safe='')}@{host}:{port}{q}{_frag(name)}"

    if t == "hysteria2":
        q = _qs(sni=n.get("sni"), obfs=n.get("obfs"), **{"obfs-password": n.get("obfs_password")})
        return f"hysteria2://{up.quote(str(n.get('cred', '')), safe='')}@{host}:{port}{q}{_frag(name)}"

    if t == "tuic":
        q = _qs(sni=n.get("sni"), alpn=n.get("alpn"),
                congestion_control=n.get("congestion"), **{"allow_insecure": 1 if n.get("insecure") else None})
        cred = n.get("cred") or n.get("uuid", "")
        return f"tuic://{cred}@{host}:{port}{q}{_frag(name)}"

    if t == "socks5":
        cred = n.get("cred") or ""
        auth = (up.quote(cred, safe=":") + "@") if cred else ""
        return f"socks5://{auth}{host}:{port}{_frag(name)}"

    return ""


# ── URI 解析 ────────────────────────────────────────────────────────────────

def parse_uri(line):
    line = line.strip()
    if "://" not in line:
        return None
    scheme = line.split("://")[0].lower()
    name = ""
    if "#" in line:
        name = up.unquote(line.split("#", 1)[1])[:60]
    body = line.split("#")[0]
    node = _parse_uri_body(scheme, body, name)
    if node:
        # 原文是分享链接就直接原样保留：合成一遍会丢未知参数，
        # 订阅者拿到的应和源作者发布的一字不差
        node["_raw_uri"] = line
    return node


def _parse_uri_body(scheme, body, name):
    try:
        if scheme == "vmess":
            j = json.loads(_b64d(body[8:]))
            return {"type": "vmess", "name": name or j.get("ps", ""),
                    "server": norm_host(str(j.get("add", ""))), "port": int(j.get("port") or 0),
                    "cred": j.get("id", ""), "alter_id": j.get("aid") or 0,
                    "cipher": j.get("scy") or "auto", "network": j.get("net") or "tcp",
                    "tls": j.get("tls") or "", "sni": j.get("sni") or j.get("host") or "",
                    "host": j.get("host") or "", "path": j.get("path") or "",
                    "alpn": j.get("alpn") or ""}

        if scheme == "ss":
            rest = body[5:]
            if "@" in rest:                       # SIP002
                ui, hostport = rest.rsplit("@", 1)
                try:
                    dec = _b64d(ui)
                except Exception:
                    dec = up.unquote(ui)
                method, _, pwd = dec.partition(":")
            else:                                 # 旧式整体 base64
                dec = _b64d(rest)
                hostport, _, _ = dec.partition("#")
                method, _, pwd = dec.split("@")[0].partition(":")
                hostport = dec.split("@")[-1]
            host, _, port = hostport.rpartition(":")
            host = host.split("?")[0]
            return {"type": "ss", "name": name, "server": norm_host(host),
                    "port": int(re.sub(r"\D", "", port) or 0), "cred": pwd, "method": method}

        if scheme in ("vless", "trojan", "hysteria2", "hy2", "tuic", "socks5"):
            t = "hysteria2" if scheme == "hy2" else scheme
            u = up.urlsplit(body)
            q = {k: v[0] for k, v in up.parse_qs(u.query).items()}
            node = {"type": t, "name": name, "server": norm_host(u.hostname or ""),
                    "port": u.port or 0,
                    "cred": up.unquote(u.username or ""),
                    "network": q.get("type") or "tcp",
                    "security": q.get("security") or "",
                    "sni": q.get("sni") or q.get("peer") or "",
                    "path": q.get("path") or "", "host": q.get("host") or "",
                    "flow": q.get("flow") or "", "obfs": q.get("obfs") or "",
                    "obfs_password": q.get("obfs-password") or "",
                    "public_key": q.get("pbk") or "", "short_id": q.get("sid") or "",
                    "fingerprint": q.get("fp") or "", "alpn": q.get("alpn") or "",
                    "congestion": q.get("congestion_control") or ""}
            if t == "tuic":
                node["cred"] = f"{up.unquote(u.username or '')}:{up.unquote(u.password or '')}"
            if t == "trojan" and u.password:
                node["cred"] = f"{node['cred']}:{up.unquote(u.password)}" if node["cred"] else up.unquote(u.password)
            if t == "trojan" and ":" in node["cred"]:
                node["cred"] = node["cred"].split(":", 1)[0]
            return node
    except Exception:
        return None
    return None


# ── Clash / Mihomo YAML ────────────────────────────────────────────────────

def _ws_params(p):
    """Clash 的 ws-opts / grpc-opts / reality-opts 提取成扁平参数。"""
    out = {}
    net = p.get("network") or "tcp"
    out["network"] = net
    ws = p.get("ws-opts") or {}
    if isinstance(ws, dict):
        out["path"] = ws.get("path") or ""
        hdr = ws.get("headers") or {}
        if isinstance(hdr, dict):
            out["host"] = hdr.get("Host") or hdr.get("host") or ""
    grpc = p.get("grpc-opts") or {}
    if isinstance(grpc, dict):
        out["path"] = grpc.get("grpc-service-name") or out.get("path", "")
    ro = p.get("reality-opts") or {}
    if isinstance(ro, dict):
        out["public_key"] = ro.get("public-key") or ""
        out["short_id"] = ro.get("short-id") or ""
        out["security"] = "reality"
    if p.get("tls") or p.get("security") == "reality":
        out.setdefault("security", "reality" if p.get("reality-opts") else "tls")
        out["tls"] = "tls"
    if p.get("servername") or p.get("sni"):
        out["sni"] = p.get("servername") or p.get("sni")
    if p.get("client-fingerprint"):
        out["fingerprint"] = p["client-fingerprint"]
    return out


def _clash_one(p):
    if not isinstance(p, dict):
        return None
    t = str(p.get("type", "")).lower()
    server = norm_host(str(p.get("server", "")))
    try:
        port = int(p.get("port") or 0)
    except Exception:
        return None
    if not server or not port:
        return None
    name = str(p.get("name", ""))[:60]
    base = {"name": name, "server": server, "port": port}

    if t == "ss":
        base.update({"type": "ss", "cred": str(p.get("password", "")),
                     "method": p.get("cipher") or "aes-256-gcm"})
        if p.get("plugin"):
            base["plugin"] = str(p["plugin"])
            po = p.get("plugin-opts")
            if isinstance(po, dict):
                base["plugin_opts"] = ",".join(f"{k}={v}" for k, v in po.items())
        return base

    if t == "vmess":
        base.update({"type": "vmess", "cred": str(p.get("uuid", "")),
                     "alter_id": p.get("alterId") or p.get("alter-id") or 0,
                     "cipher": p.get("cipher") or "auto"})
        base.update(_ws_params(p))
        return base

    if t == "vless":
        base.update({"type": "vless", "cred": str(p.get("uuid", "")), "flow": p.get("flow") or ""})
        base.update(_ws_params(p))
        return base

    if t == "trojan":
        base.update({"type": "trojan", "cred": str(p.get("password", ""))})
        base.update(_ws_params(p))
        base["security"] = "tls"          # trojan 恒为 TLS，即便没写 tls 字段
        base["tls"] = "tls"
        return base

    if t in ("hysteria2", "hy2"):
        base.update({"type": "hysteria2", "cred": str(p.get("password") or p.get("auth") or ""),
                     "sni": p.get("sni") or p.get("servername") or ""})
        if p.get("obfs"):
            base["obfs"] = str(p["obfs"])
            base["obfs_password"] = str(p.get("obfs-password") or "")
        return base

    if t == "tuic":
        base.update({"type": "tuic", "cred": f"{p.get('uuid', '')}:{p.get('password', '')}",
                     "sni": p.get("sni") or "", "alpn": ",".join(p.get("alpn") or []),
                     "congestion": p.get("congestion-controller") or ""})
        return base

    if t == "socks5":
        base.update({"type": "socks5",
                     "cred": f"{p.get('username', '')}:{p.get('password', '')}".strip(":"),
                     "tls": "tls" if p.get("tls") else ""})
        return base

    # http 明文代理不做：跨境链路上毫无隐私，且绝大多数是占位/广告条目
    return None


def parse_clash(text):
    """解析 Clash / Mihomo YAML。返回 (nodes, 原始条目数)。"""
    try:
        doc = yaml.safe_load(text)
    except Exception:
        # 有些源带自定义标签，退回宽松 loader 再试一次
        try:
            doc = yaml.load(text, Loader=yaml.BaseLoader)
        except Exception:
            return [], 0
    if not isinstance(doc, dict):
        return [], 0
    proxies = doc.get("proxies")
    if not isinstance(proxies, list):
        return [], 0
    out = []
    for p in proxies:
        n = _clash_one(p)
        if n:
            out.append(n)
    return out, len(proxies)


# ── sing-box JSON ──────────────────────────────────────────────────────────

def _sb_one(o):
    if not isinstance(o, dict):
        return None
    t = str(o.get("type", "")).lower()
    if t in SKIP_SINGBOX:
        return None
    server = norm_host(str(o.get("server", "")))
    try:
        port = int(o.get("server_port") or 0)
    except Exception:
        return None
    if not server or not port:
        return None
    name = str(o.get("tag", ""))[:60]
    base = {"name": name, "server": server, "port": port}

    tls = o.get("tls") if isinstance(o.get("tls"), dict) else {}
    tr = o.get("transport") if isinstance(o.get("transport"), dict) else {}
    common = {"network": tr.get("type") or ("ws" if tr else "tcp")}
    if tr.get("path"):
        common["path"] = tr["path"]
    hdr = tr.get("headers") or {}
    if isinstance(hdr, dict) and hdr.get("Host"):
        common["host"] = hdr["Host"]
    if tls.get("enabled"):
        common["tls"] = "tls"
        common["security"] = "tls"
        if tls.get("server_name"):
            common["sni"] = tls["server_name"]
        if tls.get("reality") and isinstance(tls["reality"], dict):
            common["security"] = "reality"
            common["public_key"] = tls["reality"].get("public_key") or ""
            common["short_id"] = tls["reality"].get("short_id") or ""
        if tls.get("alpn"):
            common["alpn"] = ",".join(tls["alpn"])

    if t == "shadowsocks":
        base.update({"type": "ss", "method": o.get("method") or "aes-256-gcm",
                     "cred": str(o.get("password", ""))})
        return base
    if t == "vmess":
        base.update({"type": "vmess", "cred": str(o.get("uuid", "")),
                     "alter_id": o.get("alter_id") or 0,
                     "cipher": o.get("security") or "auto"})
        base.update(common)
        return base
    if t == "vless":
        base.update({"type": "vless", "cred": str(o.get("uuid", "")),
                     "flow": o.get("flow") or ""})
        base.update(common)
        return base
    if t == "trojan":
        base.update({"type": "trojan", "cred": str(o.get("password", ""))})
        base.update(common)
        base["security"] = base.get("security") or "tls"
        base["tls"] = "tls"
        return base
    if t in ("hysteria2", "hy2"):
        base.update({"type": "hysteria2", "cred": str(o.get("password", "")),
                     "sni": (tls.get("server_name") or "")})
        ob = o.get("obfs") if isinstance(o.get("obfs"), dict) else {}
        if ob.get("type"):
            base["obfs"] = ob["type"]
            base["obfs_password"] = ob.get("password") or ""
        return base
    if t == "tuic":
        base.update({"type": "tuic", "cred": f"{o.get('uuid', '')}:{o.get('password', '')}",
                     "sni": tls.get("server_name") or "",
                     "congestion": o.get("congestion_control") or ""})
        return base
    return None


def parse_singbox(text):
    try:
        doc = json.loads(text)
    except Exception:
        return [], 0
    if not isinstance(doc, dict):
        return [], 0
    obs = doc.get("outbounds")
    if not isinstance(obs, list):
        return [], 0
    out = [n for n in (_sb_one(o) for o in obs) if n]
    return out, len(obs)


# ── 统一入口 ────────────────────────────────────────────────────────────────

def parse_any(data):
    """自动识别格式并解析。

    返回 (nodes, raw_count)：
      nodes      统一模型列表，每条带 uri / fp / addr / type / name
      raw_count  文件里声明的条目总数（含被判定无效的），用于「收录/总数」比
    """
    text = data.decode("utf-8", "ignore").strip() if isinstance(data, bytes) else str(data)

    # 1) Clash YAML：看顶层是否出现 proxies:
    if re.search(r"^proxies\s*:", text, re.M):
        nodes, raw = parse_clash(text)
        if nodes:
            return _finalize(nodes), raw
        # YAML 里没有可用节点，继续按其它格式尝试（有些文件两者混排）

    # 2) sing-box JSON
    if text[:1] == "{":
        nodes, raw = parse_singbox(text)
        if nodes:
            return _finalize(nodes), raw
        # 可能是单节点 JSON 或 base64，落到下面

    # 3) base64 包着的 URI 列表
    body = text
    if "://" not in body:
        try:
            body = _b64d(body)
        except Exception:
            return [], 0

    # 4) 逐行 URI
    nodes, raw = [], 0
    for line in body.splitlines():
        line = line.strip()
        if "://" not in line:
            continue
        raw += 1
        n = parse_uri(line)
        if n:
            nodes.append(n)
    return _finalize(nodes), raw


def _finalize(nodes):
    """补齐 uri / fp / addr；丢弃回环、私有地址与无地址条目。"""
    out = []
    for n in nodes:
        if not n.get("server") or not n.get("port"):
            continue
        if not is_public_host(n["server"]):
            continue
        n.setdefault("name", "")
        n["fp"] = make_fp(n)
        n["addr"] = addr_of(n)
        n["uri"] = n.pop("_raw_uri", None) or to_uri(n)
        out.append(n)
    return out
