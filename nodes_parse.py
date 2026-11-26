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
           "alter_id", "public_key", "short_id", "fingerprint", "congestion",
           "insecure")

SKIP_SINGBOX = {"selector", "urltest", "direct", "block", "dns", "relay", "tor"}

# 已知但主动不入库的协议：不是解析不出来，是不该收录
#   http 明文代理：跨境链路上用户名密码与全部流量明文可读，且这类条目
#   绝大多数是带广告的公开 http 代理池，收录等于给用户一个「看起来能用」的陷阱。
#   但也不能静默丢掉——要进隔离区，让「为什么少了几百条」有据可查。
DROP_TYPES = {"http": "明文代理（流量与凭据均可被链路上任何人读取）"}

# 隔离区原因码 → 人话。键会被写进 data/quarantine.json，方便统计。
REASON = {
    "no_addr": "缺少地址或端口",
    "bad_port": "端口不在 1–65535 有效范围内",
    "nonpublic": "非公网地址（回环/私有/保留网段）",
    "plaintext": DROP_TYPES["http"],
    "unknown_type": "不支持的协议类型",
    "bad_record": "条目结构异常（不是对象或字段类型不对）",
    # 生成不出能被解析回来的链接 = 用户拿了也用不了。这种节点不能入库，
    # 否则 all.txt 会比库里少几条，而且少得莫名其妙
    "no_uri": "无法生成可解析的订阅链接",
    # reality 缺公钥：握手必然失败，而且 sing-box 会因为一条坏记录拒绝整份配置
    "no_key": "reality 缺少公钥（pbk），无法完成握手",
    # vmess/vless 的 id 必须是 UUID。上游偶尔把占位符（monacoVPN、@Geek_Spot）
    # 当 id 发出来，这类节点 100% 连不上，而且会让 Xray 拒绝整份配置
    "bad_uuid": "凭据不是合法 UUID（vmess/vless 无法握手）",
}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                      r"[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


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


def _port_number(value):
    """只接受整数或十进制整数字符串，避免 int() 对 bool/小数的宽松转换。"""
    if type(value) is int:
        return value
    if isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"[0-9]+", value):
            return int(value)
    return None


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


def _hp(host, port):
    """host:port 的正确写法。IPv6 必须带方括号，否则 urlsplit 会把最后一段
    当成端口：`2001:db8::1:443` 会被解析成 host=`2001:db8::1`、port 缺失，
    整条链接就此不可用。实测全量里有 18 条节点是这么坏的（14 条 hysteria2、
    4 条 vless），全是 IPv6。"""
    h = f"[{host}]" if ":" in str(host) and not str(host).startswith("[") else str(host)
    return f"{h}:{port}"


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
        return f"ss://{userinfo}@{_hp(host, port)}{_frag(name)}"

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
        return f"vless://{n.get('cred') or n.get('uuid', '')}@{_hp(host, port)}{q}{_frag(name)}"

    if t == "trojan":
        q = _qs(security="tls", sni=n.get("sni"), type=n.get("network") or "tcp",
                path=n.get("path"), host=n.get("host"), alpn=n.get("alpn"))
        return f"trojan://{up.quote(str(n.get('cred', '')), safe='')}@{_hp(host, port)}{q}{_frag(name)}"

    if t == "hysteria2":
        q = _qs(sni=n.get("sni"), obfs=n.get("obfs"), **{"obfs-password": n.get("obfs_password")})
        return f"hysteria2://{up.quote(str(n.get('cred', '')), safe='')}@{_hp(host, port)}{q}{_frag(name)}"

    if t == "tuic":
        q = _qs(sni=n.get("sni"), alpn=n.get("alpn"),
                congestion_control=n.get("congestion"), **{"allow_insecure": 1 if n.get("insecure") else None})
        cred = n.get("cred") or n.get("uuid", "")
        return f"tuic://{cred}@{_hp(host, port)}{q}{_frag(name)}"

    if t == "socks5":
        cred = n.get("cred") or ""
        auth = (up.quote(cred, safe=":") + "@") if cred else ""
        return f"socks5://{auth}{_hp(host, port)}{_frag(name)}"

    if t == "anytls":
        # AnyTLS 就是「TLS 里套一层自适应填充」，参数只有 sni / 证书校验 / 指纹
        q = _qs(sni=n.get("sni"), insecure=1 if n.get("insecure") else None,
                fp=n.get("fingerprint"))
        return f"anytls://{up.quote(str(n.get('cred', '')), safe='')}@{_hp(host, port)}{q}{_frag(name)}"

    # http 只用于「解析得出来但被隔离」的统计口径，不参与正常导出
    if t == "http":
        cred = n.get("cred") or ""
        auth = (up.quote(cred, safe=":") + "@") if cred else ""
        scheme = "https" if n.get("tls") else "http"
        return f"{scheme}://{auth}{_hp(host, port)}{_frag(name)}"

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
                    "server": norm_host(str(j.get("add", ""))), "port": _port_number(j.get("port")),
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
            port = port.split("?", 1)[0].split("/", 1)[0]
            return {"type": "ss", "name": name, "server": norm_host(host),
                    "port": _port_number(port), "cred": pwd, "method": method}

        if scheme in ("vless", "trojan", "hysteria2", "hy2", "tuic", "socks5",
                      "anytls", "http", "https"):
            t = {"hy2": "hysteria2", "https": "http"}.get(scheme, scheme)
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
            if t == "http":
                # http://user:pass@host:port —— 凭据在 userinfo 里，两个都要留
                uu, pp = up.unquote(u.username or ""), up.unquote(u.password or "")
                node["cred"] = f"{uu}:{pp}" if pp else uu
                node["tls"] = "tls" if scheme == "https" else ""
            if t == "anytls":
                node["cred"] = up.unquote(u.username or "") or up.unquote(u.password or "")
                node.setdefault("sni", "")
                node["insecure"] = q.get("insecure") in ("1", "true") or q.get("allowInsecure") in ("1", "true")
            return node
    except Exception:
        return None
    return None


# ── Clash / Mihomo YAML ────────────────────────────────────────────────────

def _ws_params(p):
    """Clash 的 ws-opts / grpc-opts / reality-opts 提取成扁平参数。"""
    out = {}
    net = (p["network"] if "network" in p and not isinstance(p["network"], str)
           else p.get("network") or "tcp")
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
    # 必须判「非空 dict」。写成 `p.get("reality-opts") or {}` 再 isinstance，
    # 一个没有 reality-opts 的普通节点也会走进这个分支：空 dict 是 dict，
    # 于是每条 Clash 节点都被标成 reality、public_key 清空。
    # 后果是 2,374 条被误标（其中 1,987 条公钥为空），sing-box 直接拒绝整份
    # 订阅（invalid public_key）。页面上的「安全方式」标注也跟着全错。
    # 这个 bug 是靠客户端实测才暴露的：我们自己的解析器读得回来，只是读回来的
    # 是错的东西。
    #
    # 一开始我把它当成 anaer 可用率低的根因（那个源 90 条只通 2 条）。修完再测，
    # anaer 仍是 0/90——mihomo 对缺公钥的 reality 会当普通 TLS 处理，照样能连。
    # 所以低可用率是节点本身的问题，不是这个 bug。这种「改一个 bug 顺手解释
    # 另一个现象」的推断最危险，记在这里提醒自己。
    ro = p.get("reality-opts")
    if isinstance(ro, dict) and ro:
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
    port = _port_number(p.get("port"))
    if port is None:
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

    if t == "anytls":
        base.update({"type": "anytls", "cred": str(p.get("password") or p.get("uuid") or ""),
                     "sni": p.get("sni") or p.get("servername") or "",
                     "tls": "tls", "security": "tls",
                     "insecure": bool(p.get("skip-cert-verify")),
                     "fingerprint": p.get("client-fingerprint") or ""})
        return base

    if t in ("http", "https"):
        # 解析得出来，但 _finalize 会以「明文代理」为由拦下并计入隔离区。
        # 直接 return None 的话，页面上就只会看到「这个源条数莫名少了」。
        base.update({"type": "http",
                     "cred": f"{p.get('username', '')}:{p.get('password', '')}".strip(":"),
                     "tls": "tls" if (t == "https" or p.get("tls")) else ""})
        return base

    return None


def parse_clash(text, rejects=None):
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
        elif rejects is not None:
            rejects.append(_reject_of(p, "clash"))
    return out, len(proxies)


# ── sing-box JSON ──────────────────────────────────────────────────────────

def _sb_one(o):
    if not isinstance(o, dict):
        return None
    t = str(o.get("type", "")).lower()
    if t in SKIP_SINGBOX:
        return None
    server = norm_host(str(o.get("server", "")))
    port = _port_number(o.get("server_port"))
    if port is None:
        return None
    if not server or not port:
        return None
    name = str(o.get("tag", ""))[:60]
    base = {"name": name, "server": server, "port": port}

    tls = o.get("tls") if isinstance(o.get("tls"), dict) else {}
    tr = o.get("transport") if isinstance(o.get("transport"), dict) else {}
    network = (tr["type"] if "type" in tr and not isinstance(tr["type"], str)
               else tr.get("type") or ("ws" if tr else "tcp"))
    common = {"network": network}
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
    if t == "anytls":
        utls = tls.get("utls") if isinstance(tls.get("utls"), dict) else {}
        base.update({"type": "anytls", "cred": str(o.get("password", "")),
                     "sni": tls.get("server_name") or "",
                     "tls": "tls", "security": "tls",
                     "insecure": bool(tls.get("insecure")),
                     "fingerprint": utls.get("fingerprint") or ""})
        return base
    if t in ("http", "https"):
        base.update({"type": "http",
                     "cred": f"{o.get('username', '')}:{o.get('password', '')}".strip(":"),
                     "tls": "tls" if (t == "https" or tls.get("enabled")) else ""})
        return base
    return None


def _reject_of(raw, kind, reason=None):
    """把一条被丢弃的原始条目压成隔离区记录。

    只留协议/名字/地址/端口，**不留凭据**：隔离区要提交进公开仓库，
    把 uuid、password 或原始订阅行写进去，等于换个文件继续泄密。
    """
    if not isinstance(raw, dict):
        return {"kind": kind, "type": "", "name": "", "server": "", "port": 0,
                "reason": REASON["bad_record"]}
    t = str(raw.get("type") or "").lower()
    return {"kind": kind, "type": t,
            "name": str(raw.get("name") or raw.get("tag") or "")[:40],
            "server": str(raw.get("server") or "")[:60],
            "port": raw.get("port") or raw.get("server_port") or 0,
            "reason": reason or (REASON["unknown_type"] if t else REASON["bad_record"])}


def parse_singbox(text, rejects=None):
    try:
        doc = json.loads(text)
    except Exception:
        return [], 0
    if not isinstance(doc, dict):
        return [], 0
    obs = doc.get("outbounds")
    if not isinstance(obs, list):
        return [], 0
    out = []
    for o in obs:
        n = _sb_one(o)
        if n:
            out.append(n)
        elif rejects is not None and not (
                isinstance(o, dict) and str(o.get("type", "")).lower() in SKIP_SINGBOX):
            # selector/urltest 之类是路由构件，不是「被拒绝的节点」，不记
            rejects.append(_reject_of(o, "singbox"))
    return out, len(obs)


# ── 统一入口 ────────────────────────────────────────────────────────────────

def parse_any(data, rejects=None):
    """自动识别格式并解析。

    返回 (nodes, raw_count)：
      nodes      统一模型列表，每条带 uri / fp / addr / type / name
      raw_count  文件里声明的条目总数（含被判定无效的），用于「收录/总数」比

    rejects    传入 list 时，被丢弃的条目以 {type,name,server,port,reason}
               的形式追加进去（不含凭据）。默认 None 不收集。
    """
    # 不论入参是 bytes 还是 str 都要 strip：格式嗅探靠首个字符判断（text[:1] == "{"），
    # 文件开头有个空行就会一路掉到「逐行 URI」分支，把整个 JSON 源当成 0 条。
    text = (data.decode("utf-8", "ignore") if isinstance(data, bytes) else str(data)).strip()

    # 1) Clash YAML：看顶层是否出现 proxies:
    if re.search(r"^proxies\s*:", text, re.M):
        nodes, raw = parse_clash(text, rejects)
        if nodes:
            return _finalize(nodes, rejects), raw
        # YAML 里没有可用节点，继续按其它格式尝试（有些文件两者混排）

    # 2) sing-box JSON
    if text[:1] == "{":
        nodes, raw = parse_singbox(text, rejects)
        if nodes:
            return _finalize(nodes, rejects), raw
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
        elif rejects is not None:
            # 原文含凭据，只取协议与主机，不整行搬运
            scheme = line.split("://")[0].lower()
            rejects.append({"kind": "uri", "type": scheme, "name": "",
                            "server": _host_of_uri(line), "port": 0,
                            "reason": REASON["bad_record"]})
    return _finalize(nodes, rejects), raw


def _host_of_uri(line):
    try:
        return (up.urlsplit(line.split("#")[0]).hostname or "")[:60]
    except Exception:
        return ""


def _why_drop(n):
    """一条已解析的节点为什么不该入库。空串表示可以留。"""
    if not n.get("server") or not n.get("port"):
        return REASON["no_addr"]
    port = n.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        return REASON["bad_port"]
    if n.get("type") in DROP_TYPES:
        return REASON["plaintext"]
    if not is_public_host(n["server"]):
        return REASON["nonpublic"]
    if (n.get("security") or "").lower() == "reality" and \
            not (n.get("public_key") or "").strip():
        return REASON["no_key"]
    if n.get("type") in ("vmess", "vless"):
        if not _UUID_RE.match(str(n.get("cred") or "")):
            return REASON["bad_uuid"]
    if n.get("type") == "tuic":
        # tuic 的 cred 是 uuid:password，只看冒号前
        if not _UUID_RE.match(str(n.get("cred") or "").split(":", 1)[0]):
            return REASON["bad_uuid"]
    return ""


NET_OK = {"tcp", "raw", "ws", "websocket", "grpc", "h2", "http",
          "httpupgrade", "xhttp", "kcp", "quic", "none"}


def _norm_net(v):
    """规范传输层名字。

    上游数据里存在 `type=ws%23🍪100@xxx` 这种把 fragment 编码进 query 的写法——
    解码后 network 变成 `ws#🍪100@xxx`，原样写进客户端配置会让整份订阅
    因为一个「未知传输协议」被拒绝。这里只认白名单里的名字，
    被污染的值取第一个能匹配的前缀。
    """
    if not isinstance(v, str):
        return "tcp"
    v = (v or "").strip().lower()
    if v in NET_OK:
        if v == "none":
            return "tcp"
        return "ws" if v == "websocket" else v
    for k in ("httpupgrade", "websocket", "grpc", "xhttp", "http", "quic",
              "kcp", "raw", "ws", "tcp"):
        if v.startswith(k):
            return "ws" if k in ("websocket", "ws") else k
    return "tcp"


def _finalize(nodes, rejects=None):
    """补齐 uri / fp / addr；丢弃回环、私有地址与无地址条目。"""
    out = []
    for n in nodes:
        if "network" in n and not isinstance(n["network"], str):
            if rejects is not None:
                rejects.append({"kind": "node", "type": n.get("type") or "",
                                "name": (n.get("name") or "")[:40],
                                "server": (n.get("server") or "")[:60],
                                "port": n.get("port") or 0,
                                "reason": REASON["bad_record"]})
            continue
        # 必须在 make_fp 之前做：network 进了指纹，规范化后再算才不会
        # 让「同一个节点的脏写法和干净写法」算成两条
        n["network"] = _norm_net(n.get("network"))
        why = _why_drop(n)
        if why:
            if rejects is not None:
                rejects.append({"kind": "node", "type": n.get("type") or "",
                                "name": (n.get("name") or "")[:40],
                                "server": (n.get("server") or "")[:60],
                                "port": n.get("port") or 0, "reason": why})
            continue
        n.setdefault("name", "")
        n["fp"] = make_fp(n)
        n["addr"] = addr_of(n)
        # 原样保留源站的分享链接；但必须验一遍它能不能被解析回来。
        # 实测全量里有 18 条 URI（12216 里）自己解析不回来——base64 里带换行、
        # 或参数残缺。它们留在库里只会让「库里 10,216 条 / 导出 10,198 条」
        # 这种差额永远说不清，所以宁可在这里就拦下。
        uri = n.pop("_raw_uri", None) or ""
        if not uri or not parse_uri(uri):
            rebuilt = to_uri(n)
            uri = rebuilt if rebuilt and parse_uri(rebuilt) else ""
        if not uri:
            if rejects is not None:
                rejects.append({"kind": "node", "type": n.get("type") or "",
                                "name": (n.get("name") or "")[:40],
                                "server": (n.get("server") or "")[:60],
                                "port": n.get("port") or 0,
                                "reason": REASON["no_uri"]})
            continue
        n["uri"] = uri
        out.append(n)
    return out
