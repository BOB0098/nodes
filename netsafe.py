#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P13：出网与写盘的统一安全边界。

这个仓库的输入是「互联网上别人维护的订阅文件」，属于完全不可信的输入。
采集链路原来直接用 `urllib.request.urlopen(...).read()`，同时踩了四个坑：

1. **没有大小上限**。`read()` 读到 EOF 为止，一个坏掉的源（或者被挂了大文件
   的地址）能把 CI runner 的内存吃干净，然后整个 job 被 OOM 杀掉——表现出来
   是「今天没更新」，而不是「有个源有问题」。
2. **没有总时长上限**。`urlopen(timeout=25)` 的 timeout 是**单次 socket 操作**
   的超时，不是总时长。源按每秒 1 字节滴水，每滴都不超时，这个请求可以挂到
   天荒地老。
3. **默认 opener 支持 `file://` 和 `ftp://`**。`urlopen("file:///etc/passwd")`
   会老老实实读本地文件。目前源清单写在仓库里、不是用户提交的，所以这不是
   现实的攻击面；但纵深防御的意义就在于「上游哪天变了也不至于直接失守」。
4. **重定向不做检查**。一个源被劫持后 302 到 `http://169.254.169.254/`
   （云厂商元数据服务，能拿到实例凭据），urllib 会照跳不误。

所以这里的做法是：

* 只保留 HTTP/HTTPS 两个 handler，其它 scheme 一律 unknown url type；
* 每一跳重定向都重新做一次地址检查（不是只看第一个 URL）；
* 域名解析后逐个比对解析结果，任何一个落在内网就整体拒绝；
* 流式读 + 字节上限 + 总时长预算，两个上限都在读循环里持续检查。

一个必须说明的残余风险：DNS rebinding。校验时解析出公网 IP，真正连接时
再解析一次，两次之间攻击者可以把记录改成 127.0.0.1（TOCTOU）。彻底解决要把
解析出的 IP 钉死再连，那需要自写 HTTP 客户端、自己处理 TLS SNI 与证书校验，
为了一个当前不存在的攻击面引入整条采集链路失败的风险，不划算。这里的取舍是
「挡住所有静态内网地址 + 所有重定向」，并在文档里写明这个窗口。前提是源清单
本身可信（`sources.yaml` 在仓库里，走 PR 审核），这个前提在 P13 的报告里单列。

日志脱敏也在这里：采集失败时打印的异常信息里经常带完整 URL（`HTTPError` 的
str 就是 URL），而订阅 URL 的 query 里常放 token。`mask_url` / `mask_text`
是所有日志的唯一出口，别在别处直接 f-string 拼 URL。
"""

import errno
import ipaddress
import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- 上限 ----------------------------------------------------------------
# 单源 12 MB：现在最大的源是 1.7 MB（v2ray-configs），留 7 倍余量。
# 再大就不像订阅文件了，更像有人在往里塞二进制。
MAX_BYTES = 12 << 20
CONNECT_TIMEOUT = 15          # 单次 socket 操作
TOTAL_TIMEOUT = 90            # 整个请求的墙钟预算，含所有重定向
MAX_REDIRECTS = 3
ALLOW_SCHEMES = ("https", "http")
ALLOW_PORTS = {80, 443}


class Blocked(Exception):
    """安全检查拒绝。和网络错误的区别很重要：这是「不该访问」，不是「访问不到」。"""


class TooLarge(Exception):
    """超过字节上限。带上限值，日志里要能看出是上限拦的还是源真的坏了。"""


class TooSlow(Exception):
    """超过总时长预算。"""


class Unresolvable(Exception):
    """域名解析失败。不是安全问题，是网络问题，单独一类便于排查。"""


# ---- 地址检查 -------------------------------------------------------------

def _banned(ip):
    """这个地址是否禁止访问。

    `is_private` 在 Python 3.11 里已经覆盖 10/8、172.16/12、192.168/16、
    100.64/10（CGNAT）和 IPv6 的 fc00::/7，但没覆盖 169.254/16 的
    全部语义（link_local 单独判）和 0.0.0.0，所以逐项列出来。
    """
    a = ipaddress.ip_address(ip)
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped          # ::ffff:127.0.0.1 就是 127.0.0.1
    return (a.is_private or a.is_loopback or a.is_link_local
            or a.is_multicast or a.is_reserved or a.is_unspecified)


def check_url(url, allow_ports=ALLOW_PORTS):
    """校验一个 URL 是否可以访问。不满足就抛 Blocked / Unresolvable。

    只做检查，不发请求。重定向处理器会对每一跳再调一次。
    """
    if not isinstance(url, str) or not url:
        raise Blocked("空 URL")
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError as e:
        raise Blocked(f"URL 解析失败：{e}")

    if p.scheme not in ALLOW_SCHEMES:
        # file:// ftp:// data: gopher: 一类的都到这里，包括 urlopen 默认
        # 支持的本地文件读取
        raise Blocked(f"不允许的协议：{p.scheme or '(无)'}")

    if p.username or p.password:
        # `http://user:pass@host/` 里的凭据会被原样写进日志和异常信息
        raise Blocked("URL 内嵌用户名/密码")

    host = p.hostname
    if not host:
        raise Blocked("缺少主机名")

    try:
        port = p.port
    except ValueError:
        raise Blocked("端口不是数字")
    port = port or (443 if p.scheme == "https" else 80)
    if port not in allow_ports:
        raise Blocked(f"端口 {port} 不在允许范围 {sorted(allow_ports)}")

    # 字面量 IP：直接判，不走 DNS
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if _banned(host):
            raise Blocked(f"目标是非公网地址：{host}")
        return

    # 域名：解析后逐个比对。全部必须公网。
    # getaddrinfo 会把 hosts 文件、DNS、甚至 systemd-resolved 的结果都算进来，
    # 所以 `localhost`、`*.internal`、被劫持到 127.0.0.1 的域名都挡得住。
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise Unresolvable(f"解析不了 {host}：{e}")
    addrs = {i[4][0] for i in infos}
    if not addrs:
        raise Unresolvable(f"{host} 没有解析结果")
    for a in addrs:
        if _banned(a):
            raise Blocked(f"{host} 解析到非公网地址 {a}")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """每一跳都重查地址。

    urllib 自带的处理器只看 `max_redirections` 次数，不看跳去哪。
    这里在 `redirect_request` 里插一道检查——它正好在发下一跳之前被调用。
    """

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    """只含 HTTP/HTTPS 的 opener。

    不能用 `build_opener()` 然后指望它别带 FileHandler——默认 opener 里
    FileHandler 和 FTPHandler 是存在的。手工组装能明确表达「就这两个协议」。
    """
    ctx = None
    try:
        import ssl
        ctx = ssl.create_default_context()
    except Exception:
        pass
    o = urllib.request.OpenerDirector()
    for h in (urllib.request.ProxyHandler(),        # 沿用环境变量里的代理设置
              urllib.request.UnknownHandler(),
              urllib.request.HTTPHandler(),
              urllib.request.HTTPSHandler(context=ctx),
              urllib.request.HTTPDefaultErrorHandler(),
              _SafeRedirect(),
              urllib.request.HTTPErrorProcessor()):
        o.add_handler(h)
    return o


_OPENER = None


def opener():
    global _OPENER
    if _OPENER is None:
        _OPENER = _opener()
    return _OPENER


# ---- 读取 -----------------------------------------------------------------

def read_capped(fp, headers=None, max_bytes=MAX_BYTES, total_timeout=TOTAL_TIMEOUT):
    """流式读到 EOF，两个上限持续检查。

    抽成独立函数是为了能测：selftest 里用一个假 fp 喂 1 字节给一次，
    验证「总时长超了要抛」不需要真的等 90 秒。
    """
    headers = headers or {}
    cl = headers.get("Content-Length")
    if cl and str(cl).isdigit() and int(cl) > max_bytes:
        # 有 Content-Length 时先拒绝，省得白读 12MB 才发现
        raise TooLarge(f"声明 {int(cl)} 字节，超过上限 {max_bytes}")

    buf = bytearray()
    t0 = time.time()
    while True:
        chunk = fp.read(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > max_bytes:
            raise TooLarge(f"已读超过 {max_bytes} 字节，中止")
        if time.time() - t0 > total_timeout:
            raise TooSlow(f"读取超过 {total_timeout}s 预算")
    return bytes(buf)


def open_url(url, timeout=CONNECT_TIMEOUT, max_bytes=MAX_BYTES,
             total_timeout=TOTAL_TIMEOUT, headers=None):
    """校验后 GET，返回 bytes。任何一步不合规都抛异常，不返回半截数据。

    半截数据是最坏的结果：解析器会把它当成一份「少了很多条」的正常订阅，
    于是页面上的条数悄悄变少，看起来像源正常衰败。
    """
    check_url(url)
    hdr = {"User-Agent": "Mozilla/5.0 v2rayN/6.0"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, headers=hdr)
    with opener().open(req, timeout=timeout) as r:
        return read_capped(r, dict(r.headers), max_bytes, total_timeout)


# ---- 脱敏 -----------------------------------------------------------------

# 参数名命中就整体打码。`sub` 也在这里：订阅地址常写成 ?sub=xxx。
_SECRET_KEY = re.compile(
    r"(token|key|uuid|passw|pass|pwd|auth|secret|sig|signature|sub|code|user)",
    re.I)

# 路径里的长随机段（订阅 token 常放在路径里，如 /sub/a1b2c3...）
_SEG_RANDOM = re.compile(r"[A-Za-z0-9_\-]{20,}")


def _mask_seg(seg):
    if _SEG_RANDOM.fullmatch(seg) and sum(c.isdigit() for c in seg) >= 2:
        return seg[:3] + "***" + seg[-2:]
    return seg


def mask_url(u):
    """把 URL 里可能带凭据的部分打码，保留可读的域名和路径形状。

    目标是「看日志能认出是哪个源，但认不出 token」。所以域名全留，
    路径只打长随机段，query 里敏感名全打码。
    """
    if not isinstance(u, str):
        return "***"
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return "***"
    netloc = p.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rsplit("@", 1)[-1]
    path = "/".join(_mask_seg(s) for s in p.path.split("/"))
    q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    q = [(k, "***" if _SECRET_KEY.search(k) else v) for k, v in q]
    return urllib.parse.urlunsplit(
        (p.scheme, netloc, path, urllib.parse.urlencode(q), p.fragment))


_TOKEN = re.compile(
    r"\b(gh[pousr]_[A-Za-z0-9]{20,}"          # GitHub PAT
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-[A-Za-z0-9\-_]{16,}"                 # OpenAI 风格
    r"|AKIA[0-9A-Z]{16}"                       # AWS
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"          # Slack
    r")")
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# vmess/ss/ssr 的凭据整体是一个 base64 段，长度 20 起；vless/trojan 一类是
# `scheme://userinfo@host`。分开处理，因为打码的位置不一样。
_URI_BLOB = re.compile(r"\b(vmess|ssr|ss)://[A-Za-z0-9+/=_\-\.]{20,}")
_URI_USERINFO = re.compile(
    r"\b(vless|trojan|hysteria2?|hy2|tuic|anytls|socks5?|http)://[^@\s/]{1,200}@",
    re.I)


def mask_text(s):
    """给自由文本（异常信息、日志行、来源备注）脱敏。

    顺序有讲究：先把整段 URI 处理掉，再处理裸露的 token/uuid。
    反过来的话，base64 里恰好包含的 uuid 会被替换，URI 就残缺了。
    """
    if not isinstance(s, str):
        s = str(s)
    s = _URI_BLOB.sub(lambda m: f"{m.group(1)}://***", s)
    s = _URI_USERINFO.sub(lambda m: f"{m.group(1)}://***@", s)
    s = _TOKEN.sub("***token***", s)
    s = _UUID.sub("***uuid***", s)
    return s


# ---- 原子写 ---------------------------------------------------------------

def _clean_stale(dirpath, older=3600):
    """清掉别的进程留下的半截临时文件。

    `write_atomic` 的异常分支会 unlink 自己的临时文件，但 SIGKILL 走不到
    异常分支——进程被 kill -9 / 容器被强制停止时，`.tmp-xxx.part` 就留在
    那儿了。不清的话跑一年能攒出几个 GB（每次写完不删，只是不再被引用）。
    """
    now = time.time()
    try:
        names = os.listdir(dirpath)
    except OSError:
        return
    for name in names:
        if not (name.startswith(".tmp-") and name.endswith(".part")):
            continue
        p = os.path.join(dirpath, name)
        try:
            if now - os.path.getmtime(p) > older:
                os.unlink(p)
        except OSError:
            pass


def write_atomic(path, data, encoding="utf-8"):
    """先写同目录临时文件再 rename。

    `open(path, "w")` 被中断（容器被暂停、Actions 超时、磁盘满）会留下一个
    截断的 JSON。下一轮 render 读它就 `JSONDecodeError`，然后整页空白——
    「故障演练：容器重启」要防的正是这个。rename 在同一文件系统上是原子的，
    要么是旧内容，要么是新内容，没有中间态。
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    _clean_stale(d)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
        with os.fdopen(fd, mode, **({} if mode == "wb" else {"encoding": encoding})) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---- 自测 -----------------------------------------------------------------

def selftest():
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  ✔ " if cond else "  ✘ ") + msg)
        ok = ok and bool(cond)

    def must_block(url, why):
        try:
            check_url(url)
        except Blocked as e:
            chk(True, f"拦截 {url[:44]}（{why}）→ {str(e)[:30]}")
        except Exception as e:
            chk(False, f"拦截 {url[:44]}：抛了 {type(e).__name__} 而不是 Blocked")
        else:
            chk(False, f"拦截 {url[:44]}：竟然通过了")

    must_block("http://127.0.0.1:80/", "回环")
    must_block("http://localhost/", "localhost")
    must_block("http://169.254.169.254/latest/meta-data/", "云元数据")
    must_block("http://10.0.0.5/x", "内网")
    must_block("http://[::1]/", "IPv6 回环")
    must_block("http://[::ffff:127.0.0.1]/", "IPv4-mapped 回环")
    must_block("file:///etc/passwd", "本地文件")
    must_block("ftp://example.com/a", "FTP")
    must_block("http://user:pw@example.com/", "内嵌凭据")
    must_block("http://example.com:6379/", "非白名单端口")
    must_block("gopher://example.com/", "gopher")

    # 正常的源地址必须通过。这条如果挂了，采集会全线停摆，
    # 所以它和上面那些「必须拦」同等重要。
    for u in ("https://raw.githubusercontent.com/a/b/main/c.txt",
              "https://example.com/sub?token=abc",
              "http://ip-api.com/json/"):
        try:
            check_url(u)
            chk(True, f"放行 {u[:44]}")
        except Exception as e:
            chk(False, f"放行 {u[:44]} 失败：{type(e).__name__} {e}")

    # 重定向处理器：直接喂一个内网目标，不建连接
    try:
        _SafeRedirect().redirect_request(None, None, 302, "Found", {},
                                         "http://169.254.169.254/")
        chk(False, "重定向到内网：未拦截")
    except Blocked:
        chk(True, "重定向到内网被拦截")
    except Exception as e:
        chk(False, f"重定向检查抛了 {type(e).__name__}")

    # 大小上限
    class Fake:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        def read(self, n=65536):
            return self.chunks.pop(0) if self.chunks else b""

    try:
        read_capped(Fake([b"x" * 100] * 5), {}, max_bytes=200, total_timeout=5)
        chk(False, "字节上限未生效")
    except TooLarge:
        chk(True, "字节上限生效（200 字节）")

    try:
        read_capped(Fake([b"x" * 10] * 3), {"Content-Length": "99999"},
                    max_bytes=1000, total_timeout=5)
        chk(False, "Content-Length 预检未生效")
    except TooLarge:
        chk(True, "Content-Length 预检生效")

    # 总时长预算：用一个 read 里 sleep 的假 fp，避免真等 90 秒
    class Slow:
        def read(self, n=1):
            time.sleep(0.12)
            return b"x"

    try:
        read_capped(Slow(), {}, max_bytes=1 << 20, total_timeout=0.2)
        chk(False, "总时长预算未生效")
    except TooSlow:
        chk(True, "总时长预算生效")

    # 正常读完
    chk(read_capped(Fake([b"ab", b"cd"]), {}, 100, 5) == b"abcd", "正常读取不误伤")

    # 脱敏
    m = mask_url("https://raw.githubusercontent.com/a/b/main/n.txt?token=SECRET123")
    chk("SECRET123" not in m and "raw.githubusercontent.com" in m, "URL query 打码")
    m = mask_url("https://sub.example.com/sub/abc123def456ghi789jkl012")
    chk("abc123def456ghi789jkl012" not in m, "URL 路径长随机段打码")
    m = mask_url("https://user:pw@example.com/x")
    chk("pw" not in m.split("@")[0], "URL userinfo 打码")

    t = mask_text("vmess://eyJhZGQiOiIxLjIuMy40IiwiaWQiOiJhYmMifQ==")
    chk("eyJhZGQi" not in t and t.startswith("vmess://"), "vmess blobs 打码")
    t = mask_text("vless://11111111-2222-3333-4444-555555555555@1.2.3.4:443")
    chk("11111111" not in t, "vless uuid 打码")
    t = mask_text("token=ghp_abcdefghijklmnopqrstuvwxyz0123")
    chk("ghp_abcdefghij" not in t, "GitHub token 打码")
    t = mask_text("ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ@1.2.3.4:8388")
    chk("YWVzLTI1Ni1nY206" not in t, "ss base64 打码")

    # 原子写：正常写 + 内容正确 + 不留临时文件
    import json
    p = os.path.join(tempfile.gettempdir(), "netsafe-selftest.json")
    write_atomic(p, json.dumps({"a": 1}))
    chk(json.load(open(p, encoding="utf-8")) == {"a": 1}, "原子写内容正确")
    left = [f for f in os.listdir(os.path.dirname(p)) if f.startswith(".tmp-")]
    chk(not left, "原子写不留临时文件")
    os.unlink(p)

    # 被拦截的 URL 不能真的去连：check_url 只做 DNS，不建连接。
    # 用一个不存在的域名验证「解析失败」和「被拦」是两类错误。
    try:
        check_url("http://no-such-host-9f3a2.invalid/")
        chk(False, "不存在的域名应该报错")
    except Unresolvable:
        chk(True, "不存在的域名报 Unresolvable（非 Blocked）")
    except Exception as e:
        chk(False, f"不存在的域名抛了 {type(e).__name__}")

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest())
