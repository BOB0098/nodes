#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P6：真实代理检测。

TCP 端口能连 ≠ 代理可用。一个 vmess 节点可能端口开着、但 uuid 早已失效，
或者 TLS 握手在中间被掐断——这些节点在只测端口的方案里算「可连」，
装进客户端却一条都不通。P1–P4 的抽样可连率测的就是这个端口，
所以它只能回答「这个源看起来还活着吗」，回答不了「这条节点能不能用」。

本模块把节点批量喂给 mihomo（这些协议的参考实现），每个节点绑一个本地
socks 端口，再通过它真的去访问外网：拿到 204 才算可用，延迟、出口 IP、
ASN 都从这条真实链路里量出来。

为什么不自研协议实现：vmess 的 AEAD、vless 的 reality、hysteria2 的 QUIC
都是完整的加密与传输栈，重写既不可能也没意义。用 mihomo 的结果比自研可信。

不测什么：不做流媒体/AI 解锁检测。那类判定需要登录态、地区版权库和
大量启发式，误差大且结果会过期，「不得伪造结果」的前提下宁可不做。
"""

import fcntl
import gzip
import hashlib
import ipaddress
import json
import os
import random
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import netsafe  # noqa: E402

# 时间戳一律用东八区。CI runner 是 UTC，直接用 time.strftime 会让页面出现
# 「数据更新于 07:17」和「检测于 23:32」并排（其实是同一时刻，差 8 小时），
# 用户读到第一反应是「检测数据是昨天的」。
CST = timezone(timedelta(hours=8))


def now_str():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M")


def write_json(path, obj):
    """原子写：中断时留下的是旧文件，不是半个 JSON。"""
    netsafe.write_atomic(path, json.dumps(obj, ensure_ascii=False,
                                          separators=(",", ":")))

OUT = os.path.join(BASE, "data", "nodecheck.json")
# 默认把 mihomo 放在仓库上一级，不进版本库。容器里上一级是 /（不可写），
# 所以允许用 NODES_CACHE 覆盖到挂载出来的目录——否则 Docker 部署第一次
# 下载就会失败，而失败信息是「Permission denied: /.nodes-cache」
CACHE = os.environ.get("NODES_CACHE") or os.path.join(os.path.dirname(BASE), ".nodes-cache")
MIHOMO = os.path.join(CACHE, "mihomo")

MIHOMO_VER = "v1.19.34"
# GitHub Release API digest for the exact linux-amd64-compatible .gz asset.
# Update only after verifying the digest from trusted release metadata:
# https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/v1.19.34
MIHOMO_SHA256 = "2763f5b414527f7baf6dc57e3f82f3ab561eedf34bfd7c2f2cde1f7e592efe06"
# 官方 release 在沙箱/部分机房直连不通，走镜像；多个镜像按顺序试
MIHOMO_URLS = [
    f"https://gh-proxy.com/https://github.com/MetaCubeX/mihomo/releases/download/{MIHOMO_VER}/mihomo-linux-amd64-compatible-{MIHOMO_VER}.gz",
    f"https://github.com/MetaCubeX/mihomo/releases/download/{MIHOMO_VER}/mihomo-linux-amd64-compatible-{MIHOMO_VER}.gz",
]

BATCH = 40            # 每批节点数：一批一个 mihomo 进程，太多会吃内存
CONC = 20             # 单批内并发
TIMEOUT = 8           # 单节点总超时（秒）
MIN_KEEP_RATIO = 0.4  # 回归闸门：本轮可用数低于上轮这个比例就不覆盖
BASE_PORT = 23000 + (os.getpid() % 200) * 60   # 避开并发跑的其它实例
TEST_URL = "https://www.gstatic.com/generate_204"
IP_URL = ("http://ip-api.com/json/?fields=status,country,countryCode,"
          "regionName,city,as,asname,isp,org,query")

# 抽样预算：全量 1 万节点逐个握手要几小时，GitHub Actions 跑不完。
# 每源配额 + 总量上限，按源均分，保证小源也能拿到样本。
PER_SRC = 90
MAX_TOTAL = 1200
SEED = 20261125       # 种子基数

# curl 退出码 → 失败阶段。只映射我们真的会遇到的：域名、连接、TLS、超时，
# 其余归 other，不为了「看起来完整」硬凑一张长表。
CURL_ERR = {
    5: "proxy",     # 代理地址解析失败（本地 mihomo 没起来）
    6: "dns",       # 域名解析失败
    7: "tcp",       # 连接被拒 / 不可达
    28: "timeout",  # 超时
    35: "tls",      # TLS 握手失败
    52: "empty",    # 服务端空响应
    56: "recv",     # 接收数据失败
    60: "cert",     # 证书校验失败
}
ERR_LABEL = {
    "proxy": "本地代理未就绪",
    "dns": "域名解析失败",
    "tcp": "连接被拒或不可达",
    "timeout": "超时",
    "tls": "TLS 握手失败",
    "empty": "服务端空响应",
    "recv": "接收数据失败",
    "cert": "证书校验失败",
    "http": "目标站返回异常",
    "other": "其他失败",
}


def daily_seed(now=None):
    """按天轮换的抽样种子。

    固定种子会让每轮都测同一批节点：省事，但那些节点之外的部分永远没人验证，
    「稳定」这个标签也就只对几百条成立。按天换种子，一周能把全库覆盖一遍；
    同一天内的多次采集仍然抽到同一批，这样两轮之间的波动才有可比性。
    """
    days = int((now or time.time()) // 86400)
    return SEED + days


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_mihomo_archive(path):
    try:
        size = os.path.getsize(path)
        return ((4 << 20) <= size <= (64 << 20)
                and _sha256_file(path) == MIHOMO_SHA256)
    except OSError:
        return False


def verified_cached_mihomo(binary_path=None):
    """Only trust an executable that matches the pinned, verified release archive."""
    try:
        with verified_mihomo_executable(binary_path):
            return True
    except (OSError, RuntimeError, EOFError, zlib.error):
        return False


class MihomoIntegrityError(RuntimeError):
    """A cached executable cannot be proven to match the pinned release bytes."""


def _sha256_stream(source):
    source.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    source.seek(0)
    return digest.hexdigest()


def _verified_archive_contents_digest(archive_path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(archive_path, flags)
    with os.fdopen(fd, "rb") as archive:
        size = os.fstat(archive.fileno()).st_size
        if not ((4 << 20) <= size <= (64 << 20)):
            raise MihomoIntegrityError("mihomo 压缩资产体积不符合预期")
        if _sha256_stream(archive) != MIHOMO_SHA256:
            raise MihomoIntegrityError("mihomo 压缩资产 SHA-256 校验失败")
        archive.seek(0)
        digest = hashlib.sha256()
        with gzip.GzipFile(fileobj=archive, mode="rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


@contextmanager
def verified_mihomo_executable(binary_path=None):
    """Yield a /proc fd path whose already-open inode matches the pinned archive.

    The child inherits the descriptor with pass_fds, so replacing the cache path
    after verification cannot change which bytes are executed.
    """
    binary_path = os.fspath(binary_path or MIHOMO)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(binary_path, flags)
    except OSError as e:
        raise MihomoIntegrityError("mihomo 可执行文件无法安全打开") from e
    with os.fdopen(fd, "rb") as executable:
        info = os.fstat(executable.fileno())
        if not stat.S_ISREG(info.st_mode) or not (info.st_mode & 0o111):
            raise MihomoIntegrityError("mihomo 缓存不是可执行普通文件")
        archive_path = binary_path + ".gz"
        archive_digest = _verified_archive_contents_digest(archive_path)
        if _sha256_stream(executable) != archive_digest:
            raise MihomoIntegrityError("mihomo 可执行文件与已校验压缩资产不匹配")
        if not os.path.isdir("/proc/self/fd"):
            raise MihomoIntegrityError("系统不支持通过已校验文件描述符执行 mihomo")
        yield f"/proc/self/fd/{executable.fileno()}", executable.fileno()


def run_mihomo(arguments, binary_path=None, **kwargs):
    """Run mihomo through a verified inherited file descriptor, not a mutable path."""
    with verified_mihomo_executable(binary_path) as (executable, fd):
        kwargs["pass_fds"] = tuple(sorted(set(kwargs.pop("pass_fds", ())) | {fd}))
        return subprocess.run([executable, *arguments], **kwargs)


def popen_mihomo(arguments, binary_path=None, **kwargs):
    """Popen counterpart to run_mihomo; child inherits the verified executable fd."""
    with verified_mihomo_executable(binary_path) as (executable, fd):
        kwargs["pass_fds"] = tuple(sorted(set(kwargs.pop("pass_fds", ())) | {fd}))
        return subprocess.Popen([executable, *arguments], **kwargs)


@contextmanager
def mihomo_cache_lock(binary_path=None):
    """Serialize cache validation/download/install across processes and threads."""
    lock_path = os.fspath(binary_path or MIHOMO) + ".lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    with os.fdopen(fd, "r+b") as lock_file:
        os.fchmod(lock_file.fileno(), 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _download_mihomo_archive(archive_path):
    directory = os.path.dirname(os.path.abspath(archive_path))
    os.makedirs(directory, exist_ok=True)
    last = None
    for url in MIHOMO_URLS:
        fd, temporary = tempfile.mkstemp(
            prefix=".mihomo-download-", suffix=".part", dir=directory)
        os.close(fd)
        try:
            print(f"  下载 mihomo… {url.split('/')[2]}")
            subprocess.run(["curl", "-sL", "--max-time", "180", "--max-redirs", "3",
                            "--proto", "=https", "--max-filesize", "67108864",
                            "-o", temporary, url],
                           check=True, timeout=200)
            if not _verified_mihomo_archive(temporary):
                raise RuntimeError("mihomo 压缩资产体积或 SHA-256 校验失败")
            os.replace(temporary, archive_path)
            return
        except Exception as e:
            last = e
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass
    raise RuntimeError(f"mihomo 下载失败：{last}")


def _install_mihomo_archive(archive_path):
    directory = os.path.dirname(os.path.abspath(MIHOMO))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".mihomo-install-", suffix=".part", dir=directory)
    try:
        with os.fdopen(fd, "wb") as output:
            subprocess.run(["gunzip", "-c", archive_path], check=True,
                           timeout=60, stdout=output)
            output.flush()
            os.fsync(output.fileno())
        if _sha256_file(temporary) != _sha256_gzip_contents(archive_path):
            raise RuntimeError("解压后的 mihomo 与已校验压缩资产不匹配")
        os.chmod(temporary, 0o755)
        os.replace(temporary, MIHOMO)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _sha256_gzip_contents(path):
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_mihomo():
    os.makedirs(CACHE, exist_ok=True)
    with mihomo_cache_lock():
        archive_path = MIHOMO + ".gz"
        if not _verified_mihomo_archive(archive_path):
            _download_mihomo_archive(archive_path)
        if not verified_cached_mihomo():
            _install_mihomo_archive(archive_path)
        if not verified_cached_mihomo():
            raise RuntimeError("mihomo 安装后完整性校验失败")
        v = run_mihomo(["-v"], capture_output=True, text=True, timeout=30)
        version = (v.stdout or v.stderr).strip().splitlines()
        print("  " + (version[0] if version else "mihomo 启动成功但未输出版本"))
        return MIHOMO


# ── 统一模型 → mihomo proxy ────────────────────────────────────────────────

def _transport(p, n):
    """传输层：ws / grpc / h2 / tcp + TLS。参数名照抄 mihomo 的配置键。"""
    net = (n.get("network") or "tcp").lower()
    if net in ("ws", "websocket"):
        p["network"] = "ws"
        opts = {"path": n.get("path") or "/"}
        if n.get("host"):
            opts["headers"] = {"Host": n["host"]}
        p["ws-opts"] = opts
    elif net == "grpc":
        p["network"] = "grpc"
        p["grpc-opts"] = {"grpc-service-name": n.get("path") or ""}
    elif net in ("h2", "http"):
        p["network"] = "h2"
        p["h2-opts"] = {"path": n.get("path") or "/",
                        "host": [n["host"]] if n.get("host") else []}
    else:
        p["network"] = "tcp"

    sec = (n.get("security") or "").lower()
    if sec in ("tls", "reality") or n.get("tls"):
        p["tls"] = True
        if n.get("sni"):
            p["servername"] = n["sni"]
        if sec == "reality":
            p["reality-opts"] = {"public-key": n.get("public_key") or "",
                                 "short-id": n.get("short_id") or ""}
            p["client-fingerprint"] = n.get("fingerprint") or "chrome"
        if n.get("alpn"):
            p["alpn"] = [a.strip() for a in str(n["alpn"]).split(",") if a.strip()]
        # 检测目的是「能不能出网」，不是「证书链是否规范」。
        # 大量免费节点用自签证书，不跳过校验会把它们全部误判为不可用。
        p["skip-cert-verify"] = True


def to_proxy(n, name):
    """统一节点模型 → mihomo proxies 条目。不支持的协议返回 None。"""
    t = n.get("type")
    if t not in ("ss", "vmess", "vless", "trojan", "hysteria2", "tuic", "socks5", "anytls"):
        return None
    cred = str(n.get("cred") or "")
    p = {"name": name, "type": t, "server": n["server"], "port": int(n["port"])}

    if t == "ss":
        p["cipher"] = n.get("method") or n.get("cipher") or "aes-256-gcm"
        p["password"] = cred
        if n.get("plugin"):
            p["plugin"] = n["plugin"]
            po = {}
            for kv in str(n.get("plugin_opts") or "").split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    po[k.strip()] = v.strip()
            if po:
                p["plugin-opts"] = po
        p["udp"] = True
        return p

    if t == "vmess":
        p.update({"uuid": cred, "alterId": int(n.get("alter_id") or 0),
                  "cipher": n.get("cipher") or "auto", "udp": True})
        _transport(p, n)
        return p

    if t == "vless":
        p["uuid"] = cred
        if n.get("flow"):
            p["flow"] = n["flow"]
        p["udp"] = True
        _transport(p, n)
        return p

    if t == "trojan":
        # trojan 的 TLS 是协议内建的。mihomo 默认 tls=true，但显式写出来
        # 才能让「配置里看到的」和「实际连的」一致
        p.update({"password": cred, "udp": True, "tls": True})
        if n.get("sni"):
            p["sni"] = n["sni"]
        p["skip-cert-verify"] = True
        if (n.get("network") or "tcp") != "tcp":
            _transport(p, n)
        return p

    if t == "hysteria2":
        p["password"] = cred
        if n.get("sni"):
            p["sni"] = n["sni"]
        if n.get("obfs"):
            p["obfs"] = n["obfs"]
            p["obfs-password"] = n.get("obfs_password") or ""
        p["skip-cert-verify"] = True
        return p

    if t == "tuic":
        uuid, _, pwd = cred.partition(":")
        p.update({"uuid": uuid, "password": pwd, "udp": True,
                  "skip-cert-verify": True})
        if n.get("sni"):
            p["sni"] = n["sni"]
        if n.get("alpn"):
            p["alpn"] = [a.strip() for a in str(n["alpn"]).split(",") if a.strip()]
        if n.get("congestion"):
            p["congestion-controller"] = n["congestion"]
        return p

    if t == "socks5":
        user, _, pwd = cred.partition(":")
        if user:
            p["username"] = user
            p["password"] = pwd
        p["udp"] = True
        return p

    if t == "anytls":
        p.update({"password": cred, "udp": True, "skip-cert-verify": True})
        if n.get("sni"):
            p["sni"] = n["sni"]
        if n.get("fingerprint"):
            p["client-fingerprint"] = n["fingerprint"]
        return p

    return None


class ProxyEndpointError(ValueError):
    """拒绝无法安全固定到公网 IP 的代理端点。"""


def _is_public_endpoint(address):
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return address.is_global and not netsafe._banned(address)


def pin_proxy_endpoint(proxy, node):
    """Resolve once, reject every non-global answer, and pin mihomo to a literal IP."""
    raw_host = str(node.get("server") or "").strip()
    host = (raw_host[1:-1] if raw_host.startswith("[") and raw_host.endswith("]")
            else raw_host)
    if not host:
        raise ProxyEndpointError("代理主机为空")

    try:
        endpoint = ipaddress.ip_address(host)
        is_hostname = False
    except ValueError:
        resolver_type = (socket.SOCK_DGRAM
                         if proxy.get("type") in ("hysteria2", "tuic")
                         else socket.SOCK_STREAM)
        try:
            answers = socket.getaddrinfo(host, proxy["port"], type=resolver_type)
        except (OSError, TypeError, UnicodeError, OverflowError) as e:
            raise ProxyEndpointError("代理主机 DNS 解析失败") from e
        if not answers:
            raise ProxyEndpointError("代理主机 DNS 无可用地址")
        addresses = []
        for answer in answers:
            try:
                address = ipaddress.ip_address(answer[4][0])
            except (IndexError, TypeError, ValueError) as e:
                raise ProxyEndpointError("代理主机 DNS 返回无效地址") from e
            if not _is_public_endpoint(address):
                raise ProxyEndpointError("代理主机 DNS 返回非公网地址")
            mapped = getattr(address, "ipv4_mapped", None)
            addresses.append(mapped or address)
        endpoint = addresses[0]
        is_hostname = True

    if not _is_public_endpoint(endpoint):
        raise ProxyEndpointError("代理端点不是公网地址")
    mapped = getattr(endpoint, "ipv4_mapped", None)
    if mapped is not None:
        endpoint = mapped
    proxy["server"] = endpoint.compressed

    # Keep the protocol name separate from the pinned transport address.
    # Explicit SNI always wins; only fill the server-name default for DNS hosts.
    if is_hostname:
        proto = proxy.get("type")
        if proto in ("vmess", "vless") and proxy.get("tls"):
            proxy.setdefault("servername", host)
        elif proto == "trojan":
            proxy.setdefault("sni", host)
            if proxy.get("network"):
                proxy.setdefault("servername", host)
        elif proto in ("hysteria2", "tuic", "anytls"):
            proxy.setdefault("sni", host)
    return endpoint.compressed


# ── 单批执行 ────────────────────────────────────────────────────────────────

def _port_free(port):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def build_config(batch, base_port):
    """batch: [(name, proxy_dict)]，返回 (yaml 文本, {name: port})。"""
    listeners, proxies, ports = [], [], {}
    for i, (name, p) in enumerate(batch):
        port = base_port + i
        ports[name] = port
        listeners.append({"name": f"l{i}", "type": "socks", "listen": "127.0.0.1",
                          "port": port, "proxy": name})
        proxies.append(p)
    cfg = {"log-level": "silent", "mode": "global",
           "listeners": listeners, "proxies": proxies}
    return _to_yaml(cfg), ports


def _to_yaml(obj, indent=0):
    """极简 YAML 输出。只处理 dict/list/标量，避免为一个配置文件引入依赖。
    字符串一律加双引号并转义，节点名里的冒号、井号不会再破坏结构。"""
    pad = "  " * indent
    if isinstance(obj, dict):
        out = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:\n{_to_yaml(v, indent + 1)}")
            elif isinstance(v, (dict, list)):
                out.append(f"{pad}{k}: {'{}' if isinstance(v, dict) else '[]'}")
            else:
                out.append(f"{pad}{k}: {_scalar(v)}")
        return "\n".join(out) + "\n"
    if isinstance(obj, list):
        out = []
        for it in obj:
            if isinstance(it, dict):
                body = _to_yaml(it, indent + 1)
                first, _, rest = body.partition("\n")
                out.append(f"{pad}- {first.strip()}")
                if rest.strip():
                    out.append(rest.rstrip("\n"))
            else:
                out.append(f"{pad}- {_scalar(it)}")
        return "\n".join(out) + "\n"
    return f"{pad}{_scalar(obj)}\n"


def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def probe(port, timeout=TIMEOUT):
    """通过本地 socks 端口访问测试地址。返回 (ok, ms, err)。

    err 带阶段前缀（`dns:` / `tcp:` / `tls:` / `timeout:` / `http:`），
    因为「连不上」至少有四种完全不同的原因：域名解析不出来、端口没人听、
    TLS 握不上手、以及连上了但目标站回的不是 204。以前一律记 `HTTP 000`，
    609 条失败全挤在一个标签里，既没法归因到源，也没法判断是节点死还是
    机房出口在掐这类流量。curl 的退出码本来就区分这些，只是之前没读。
    """
    proxy = f"socks5h://127.0.0.1:{port}"
    t0 = time.time()
    try:
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-x", proxy, "--max-time", str(timeout), TEST_URL],
            capture_output=True, text=True, timeout=timeout + 3)
        ms = int((time.time() - t0) * 1000)
        code = (r.stdout or "").strip()
        if code == "204":
            return True, ms, None
        if code and code != "000":
            # 连上了，但目标站给的不是 204：可能是被劫持、被限流，或目标站自己挂了。
            # 这一类不能算「节点死」——同一条链路换个目标可能就通
            return False, ms, f"http:{code}"
        cls = CURL_ERR.get(r.returncode, "other")
        return False, ms, f"{cls}:{ERR_LABEL[cls]}"
    except subprocess.TimeoutExpired:
        return False, TIMEOUT * 1000, "timeout:超时"
    except Exception as e:
        return False, int((time.time() - t0) * 1000), f"other:{type(e).__name__}"


def exit_info(port, timeout=TIMEOUT):
    """经代理查出口 IP 与归属。ip-api 免费版只给 http，走代理访问没问题：
    查询串里没有隐私信息，返回的也是公开的 IP 归属数据。"""
    proxy = f"socks5h://127.0.0.1:{port}"
    try:
        r = subprocess.run(
            ["curl", "-s", "-x", proxy, "--max-time", str(timeout), IP_URL],
            capture_output=True, text=True, timeout=timeout + 3)
        d = json.loads(r.stdout or "{}")
        if d.get("status") != "success":
            return None
        return {"ip": d.get("query"), "cc": d.get("countryCode"),
                "country": d.get("country"), "city": d.get("city"),
                "asn": d.get("as"), "isp": d.get("isp") or d.get("org")}
    except Exception:
        return None


def speed_test(port, timeout=10, size=524288):
    """下载测速（字节/秒）。只对已经握手的节点做，避免无谓流量。"""
    proxy = f"socks5h://127.0.0.1:{port}"
    url = f"https://speed.cloudflare.com/__down?bytes={size}"
    try:
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-x", proxy, "--max-time", str(timeout),
             "-w", "%{speed_download}", url],
            capture_output=True, text=True, timeout=timeout + 3)
        v = float((r.stdout or "0").strip() or 0)
        return int(v) if v > 0 else None
    except Exception:
        return None


def selfcheck():
    """用 DIRECT 出站验证「mihomo 本身能不能出网」。

    这一步不是多余的：如果整轮检测全 0，必须先能区分「所有节点都挂了」和
    「这个运行环境根本不让出网」。前者是数据结论，后者是环境故障，把后者
    写成报告等于伪造结果。CI 机房直连正常，本地沙箱只放行部分域名，
    两者表现都是「节点不可用」——只有 DIRECT 自检能把它们分开。
    """
    cfg = {"log-level": "silent", "mode": "global",
           "listeners": [{"name": "s", "type": "socks", "listen": "127.0.0.1",
                          "port": BASE_PORT + 190, "proxy": "DIRECT"}],
           # 不能在这里自己定义 {name: DIRECT, type: direct}：DIRECT 是 mihomo
           # 内置策略，重复定义会让配置加载直接失败（表现就是端口压根没起来）
           "proxies": []}
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "c.yaml")
        with open(path, "w", encoding="utf-8") as output:
            output.write(_to_yaml(cfg))
        proc = popen_mihomo(["-f", path, "-d", td],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 6
            while time.time() < deadline and _port_free(BASE_PORT + 190):
                time.sleep(0.1)
            time.sleep(0.2)
            ok, ms, err = probe(BASE_PORT + 190, timeout=10)
            return ok, ms, err
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


def run_batch(items, base_port, do_speed=False):
    pairs, meta, rejected = [], {}, {}
    for i, n in enumerate(items):
        name = f"n{i}"
        p = to_proxy(n, name)
        if not p:
            meta[name] = n
            continue
        try:
            pin_proxy_endpoint(p, n)
        except ProxyEndpointError as e:
            fp = n.get("fp")
            if fp:
                rejected[fp] = {"ok": False, "ms": None,
                                "err": f"proxy:{e}", "proto": n.get("type")}
            continue
        pairs.append((name, p))
        meta[name] = n
    if not pairs:
        return rejected

    cfg, ports = build_config(pairs, base_port)
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(cfg)
        proc = popen_mihomo(["-f", path, "-d", td],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            # 等端口起来。mihomo 通常 100ms 内就绪，给 6 秒余量足够
            deadline = time.time() + 6
            while time.time() < deadline:
                if all(_port_free(port) is False for port in ports.values()):
                    break
                time.sleep(0.1)
            time.sleep(0.2)

            out = dict(rejected)
            with ThreadPoolExecutor(max_workers=CONC) as ex:
                futs = {ex.submit(probe, port): name for name, port in ports.items()}
                for fu in as_completed(futs):
                    name = futs[fu]
                    ok, ms, err = fu.result()
                    n = meta[name]
                    rec = {"ok": ok, "ms": ms if ok else None, "err": err,
                           "proto": n.get("type")}
                    if ok:
                        g = exit_info(ports[name])
                        if g:
                            rec.update({"exit_ip": g["ip"], "cc": g["cc"],
                                        "country": g["country"], "city": g["city"],
                                        "asn": g["asn"], "isp": g["isp"]})
                        # 只对延迟已经合格的节点测速。慢节点跑 512KB 往往耗到
                        # 超时才返回，把整轮时间拖长一倍，而它本来也进不了 any
                        # 分组——延迟 3 秒的节点，带宽再高也没人用
                        if do_speed and (rec["ms"] or 9999) < 1500:
                            rec["bps"] = speed_test(ports[name])
                    out[n["fp"]] = rec
            return out
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


# ── 抽样与主流程 ────────────────────────────────────────────────────────────

def classify(n, rec):
    """P25：把节点按历史表现分成三类，供分层轮换。

    只用真实历史字段，不编造：
      - new     没测过（tries=0 或无记录）→ 优先覆盖，把全库走一遍
      - retry   测过但成功率 < 50% → 复核是否已恢复（失败节点不能永久拉黑）
      - stable  测过 ≥3 次且成功率 ≥ 80% → 少量复核，防止「稳定」标签过期

    rec 为历史记录 dict（tries/hits/last_ok/slots）。缺字段一律当 new，
    绝不把「没数据」猜成「稳定」。
    """
    rec = rec or {}
    tries = int(rec.get("tries") or 0)
    if tries <= 0:
        return "new"
    hits = int(rec.get("hits") or 0)
    if tries >= 3 and hits / tries >= 0.8:
        return "stable"
    if hits / tries < 0.5:
        return "retry"
    return "new"          # 成功率 50–80%：既不算稳定也不算失败，按常规覆盖处理


def sample(nodes, per_src=PER_SRC, total=MAX_TOTAL, seed=None, tries=None,
           history_records=None):
    """按来源分层 + P25 三类轮换抽样。

    分层而不是纯随机：小源只有几十条，纯随机可能一条都抽不到，「这个源
    真实可用率 0%」就成了「没抽到」的假象。

    每源配额按三类切分：新节点 60%、失败复核 25%、稳定复核 15%（向下取整，
    余数回流到新节点）。这样：
      - 新节点被优先覆盖，一周内把全库走一遍；
      - 失败过的节点每轮都有复核名额，恢复后能被重新发现；
      - 稳定标签会被持续抽样复核，不会一次打上就永远不验证。
    某一类不足时名额回流给其他类，不浪费预算。

    tries（旧参数，{fp16: 次数}）与 history_records（新参数，{fp16: 完整记录}）
    都可给；history_records 优先。兼容旧调用：只给 tries 时等价于按覆盖度排序
    （try 少的优先），行为与 P18 一致。

    同种子抽出结果完全可复现（selftest 有断言）。
    """
    rnd = random.Random(daily_seed() if seed is None else seed)
    by_src = {}
    for n in nodes:
        source_ids = n.get("src_ids")
        sources = source_ids if isinstance(source_ids, list) and source_ids else [n.get("src") or "?"]
        for src in dict.fromkeys(sources):
            by_src.setdefault(src, []).append(n)

    def rec_of(n):
        fp16 = (n.get("fp") or "")[:16]
        if history_records is not None:
            return history_records.get(fp16) or {}
        if tries is not None:
            return {"tries": tries.get(fp16, 0)}
        return {}

    picked = []
    for src in sorted(by_src):
        arr = list(by_src[src])
        rnd.shuffle(arr)                      # 先打散，保证同权重内随机且可复现
        # 向后兼容：只有提供了完整 history_records 才启用三类轮换。
        # 只给 tries（P18 旧调用）时保持纯覆盖度优先，不改老语义。
        if history_records is None:
            if tries is not None:
                arr = sorted(arr, key=lambda n: tries.get((n.get("fp") or "")[:16], 0))
            picked.extend(arr[:per_src])
            continue
        # P25：按三类分桶
        buckets = {"new": [], "retry": [], "stable": []}
        for n in arr:
            buckets[classify(n, rec_of(n))].append(n)
        # 配额：新 60% / 失败复核 25% / 稳定复核 15%，余数给 new
        q_new = int(per_src * 0.60)
        q_retry = int(per_src * 0.25)
        q_stable = int(per_src * 0.15)
        q_new += per_src - (q_new + q_retry + q_stable)
        take = {}
        for cat, quota in (("new", q_new), ("retry", q_retry), ("stable", q_stable)):
            take[cat] = buckets[cat][:quota]
        # 名额回流：不足的类别把剩余名额让给有余量的类别
        used = sum(len(v) for v in take.values())
        if used < per_src:
            for cat in ("new", "retry", "stable"):
                extra = buckets[cat][len(take[cat]):]
                room = per_src - used
                if room <= 0:
                    break
                add = extra[:room]
                take[cat] = take[cat] + add
                used += len(add)
        src_pick = []
        for cat in ("new", "retry", "stable"):
            src_pick.extend(take[cat])
        picked.extend(src_pick[:per_src])
    unique = {}
    for n in picked:
        fp = n.get("fp")
        key = ("fp", fp) if fp else ("object", id(n))
        unique.setdefault(key, n)
    picked = list(unique.values())
    rnd.shuffle(picked)
    return picked[:total]


def load_nodes(path=None):
    """读含完整参数与凭据的导出。data/nodes.json 是脱敏的，测不了。

    优先 export/nodes_full.json（collector 本轮生成），其次 export 里的明文 URI。
    """
    p = path or os.path.join(BASE, "export", "nodes_full.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return []


def summarize(results):
    ok = [r for r in results.values() if r.get("ok")]
    ms = sorted(r["ms"] for r in ok if r.get("ms"))
    # 失败按阶段归类：页面和报告都要能回答「这批节点是死在 DNS 还是死在 TLS」。
    # 只统计失败项，成功项的 err 是 None。
    fails = {}
    for r in results.values():
        e = r.get("err")
        if r.get("ok") or not e:
            continue
        cls = e.split(":", 1)[0]
        fails[cls] = fails.get(cls, 0) + 1
    return {
        "tested": len(results), "ok": len(ok),
        "ok_pct": round(len(ok) / len(results) * 100, 1) if results else None,
        "ms_median": ms[len(ms) // 2] if ms else None,
        "ms_p90": ms[int(len(ms) * 0.9)] if ms else None,
        "err_classes": dict(sorted(fails.items(), key=lambda kv: -kv[1])),
    }


def save(results, extra=None):
    """CI 里落盘；本地默认不写。

    与 health / lifecycle 同一口径：本地出口的连通性与机房不可比，
    写进去会让页面上的「真实可用率」变成环境的函数而不是节点的函数。
    NODES_FORCE_WRITE=1 可强制（调试用）。

    写之前过一道**回归闸门**：本轮可用数不到上一轮的四成、而上一轮本身
    测得过，就不覆盖。这挡的是「mihomo 下载到了坏包」「机房出口被限速」
    「某个协议实现整批崩了」这类事故——它们不会让 selfcheck 失败（DIRECT
    仍然通），但会让 340 条掉到 30 条。没有这道闸门，一次环境抖动就会
    把上一轮的 340 条有效结论永久抹掉，第二天再好回来也读不到了。
    """
    doc = {"schema": 2, "updated": now_str(), "env":
           "ci" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
           "summary": summarize(results), "nodes": results}
    if extra:
        doc.update(extra)
    force = (os.environ.get("GITHUB_ACTIONS") == "true"
             or os.environ.get("NODES_FORCE_WRITE") == "1")
    if not force:
        print("  （本地运行，不写入 nodecheck.json）")
        return doc

    cur = doc["summary"]
    prev = load_prev_summary()
    if prev and not os.environ.get("NODES_SKIP_GATE"):
        pt, pct = prev.get("tested") or 0, prev.get("ok") or 0
        # 只有「上一轮本身测够了」才拿来比。上一轮只测了 5 条时，
        # 4 成的阈值没有统计意义。
        if pt >= 200 and pct > 0 and cur["ok"] < pct * MIN_KEEP_RATIO:
            print(f"  ⚠ 回归闸门：本轮可用 {cur['ok']}，上一轮 {pct}（{pt} 条）"
                  f"，不足 {int(MIN_KEEP_RATIO * 100)}%，判定为环境异常")
            print("  ⚠ 不覆盖 nodecheck.json，上一轮结果保持有效。"
                  "确认无误可设 NODES_SKIP_GATE=1 强制写入")
            raise GateFail(f"可用数从 {pct} 掉到 {cur['ok']}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    write_json(OUT, doc)
    return doc


class GateFail(Exception):
    """回归闸门拦下写盘。和「跑失败了」不同：数据是好的，只是不敢信这一轮。"""


def load_prev_summary():
    """读上一轮的 summary，读不到返回 None（第一轮、文件损坏都算没有）。"""
    try:
        d = json.load(open(OUT, encoding="utf-8"))
        s = d.get("summary")
        return s if isinstance(s, dict) and s.get("tested") else None
    except Exception:
        return None


def main():
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()

    def arg(name, default):
        if name in argv:
            i = argv.index(name)
            if i + 1 < len(argv) and argv[i + 1].isdigit():
                return int(argv[i + 1])
        return default

    per_src = arg("--per-src", PER_SRC)
    max_total = arg("--limit", MAX_TOTAL)
    # 分片：把同一份抽样切成 n 份，每份跑一个 runner。目前单机 8 分钟能跑完，
    # 用不上；但源和节点数再涨一倍时，这是唯一不用重写逻辑的加速方式
    shard = arg("--shard", 0)
    shards = max(1, arg("--shards", 1))

    nodes = load_nodes()
    if not nodes:
        print("没有 export/nodes_full.json。先跑 collector.py（会生成完整参数导出）")
        return 1

    ensure_mihomo()

    ok, ms, err = selfcheck()
    print(f"  出口自检（DIRECT）：{'通' if ok else '不通'} {ms}ms {err or ''}")
    if not ok:
        print("  ⚠ 本机无法直连外网，节点检测结果只反映环境限制，不能当作「节点不可用」")
        print("  ⚠ 已中止，避免把环境故障写成数据结论")
        return 2

    # P25：读历史完整记录，做「新节点 / 失败复核 / 稳定复核」三类轮换。
    # 历史文件读失败时退回纯随机，不能让一个可选优化把整轮检测拖下水。
    hist_records = None
    tries = None
    try:
        import history as _hist
        hist_records = _hist.load().get("nodes") or {}
        tries = {k: int(v.get("tries") or 0) for k, v in hist_records.items()}
        if hist_records:
            cats = {"new": 0, "retry": 0, "stable": 0}
            for n in nodes:
                cats[classify(n, hist_records.get((n.get("fp") or "")[:16]))] += 1
            print(f"  历史覆盖：{len(hist_records)} 条有记录；"
                  f"新 {cats['new']} / 失败复核 {cats['retry']} / 稳定复核 {cats['stable']}")
    except Exception as e:
        print(f"  （历史读取失败，退回随机抽样：{type(e).__name__}）")
        hist_records = None

    picked = sample(nodes, per_src=per_src, total=max_total,
                    tries=tries, history_records=hist_records)
    if shards > 1:
        picked = picked[shard::shards]
    print(f"真实检测：备选 {len(nodes)} 条，抽样 {len(picked)} 条"
          f"（每源上限 {per_src}，总量上限 {max_total}"
          + (f"，分片 {shard + 1}/{shards}" if shards > 1 else "") + "）")

    results, t0 = {}, time.time()
    for i in range(0, len(picked), BATCH):
        batch = picked[i:i + BATCH]
        base = BASE_PORT
        while any(not _port_free(base + k) for k in range(len(batch))):
            base += 200
        results.update(run_batch(batch, base, do_speed="--speed" in argv))
        done = min(i + BATCH, len(picked))
        s = summarize(results)
        print(f"  {done:>4}/{len(picked)}  可用 {s['ok']}（{s['ok_pct']}%）"
              f"  中位延迟 {s['ms_median']}ms  {time.time() - t0:.0f}s")

    if shards > 1:
        out = OUT.replace(".json", f".shard{shard}.json")
        write_json(out, {"updated": now_str(), "env": "ci",
                         "summary": summarize(results), "nodes": results,
                         "shard": shard, "shards": shards})
        print(f"\n分片结果 -> {os.path.relpath(out, BASE)}")
        return 0
    try:
        doc = save(results, {"budget": max_total, "per_src": per_src})
    except GateFail as e:
        print(f"\n本轮结果未写入 nodecheck.json：{e}")
        print("页面继续使用上一轮的有效数据。")
        return 3
    s = doc["summary"]
    print(f"\n完成：测 {s['tested']}，真实可用 {s['ok']}（{s['ok_pct']}%），"
          f"中位延迟 {s['ms_median']}ms")
    print(f"输出: {os.path.relpath(OUT, BASE)}")
    return 0


def selftest():
    """不联网的部分：配置生成、协议转换、抽样分层。"""
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  ✔ " if cond else "  ✘ ") + msg)
        ok = ok and cond

    n = {"fp": "fp1", "type": "vmess", "server": "1.2.3.4", "port": 443,
         "cred": "uuid-1", "alter_id": 0, "cipher": "auto", "network": "ws",
         "path": "/ray", "host": "a.com", "tls": "tls", "sni": "a.com"}
    p = to_proxy(n, "n0")
    chk(p["ws-opts"]["path"] == "/ray" and p["ws-opts"]["headers"]["Host"] == "a.com",
        "vmess ws 参数")
    chk(p["servername"] == "a.com" and p["skip-cert-verify"] is True, "TLS 与 sni")

    n2 = {"fp": "fp2", "type": "tuic", "server": "5.6.7.8", "port": 443,
          "cred": "uuid:pwd", "sni": "b.com"}
    p2 = to_proxy(n2, "n1")
    chk(p2["uuid"] == "uuid" and p2["password"] == "pwd", "tuic 凭据拆分")

    n3 = {"fp": "fp3", "type": "ss", "server": "9.9.9.9", "port": 8388,
          "cred": "pw", "method": "aes-128-gcm"}
    chk(to_proxy(n3, "n2")["cipher"] == "aes-128-gcm", "ss cipher")
    chk(to_proxy({"type": "hysteria", "server": "x", "port": 1}, "n") is None,
        "hysteria v1 不支持时返回 None")

    cfg, ports = build_config([("n0", p), ("n1", p2)], 24000)
    chk('proxy: "n0"' in cfg and ports["n1"] == 24001, "listener 绑定与端口分配")
    chk('path: "/ray"' in cfg, "YAML 嵌套正确")
    chk("\n" in cfg and cfg.count("proxies:") == 1, "YAML 结构完整")

    # 分层抽样：小源必须也被抽到
    nodes = [{"fp": f"big{i}", "src": "big"} for i in range(500)]
    nodes += [{"fp": f"sml{i}", "src": "small"} for i in range(5)]
    got = sample(nodes, per_src=90, total=1000)
    chk(sum(1 for x in got if x["src"] == "small") == 5, "小源不被大源挤掉")
    chk(len(sample(nodes, per_src=90, total=50)) == 50, "总量上限生效")

    # 同样的种子必须抽出同样的结果，否则两轮之间无法比较
    a = [x["fp"] for x in sample(nodes, 20, 100)]
    b = [x["fp"] for x in sample(nodes, 20, 100)]
    chk(a == b, "抽样可复现")

    # 覆盖度优先：给了 tries 之后，同源内先抽没测过的。这是 P18 的核心改了——
    # 纯随机下攒到「测过 3 次」要三天，覆盖度优先能把这段压到一天左右
    # 前 16 位必须各不相同，否则 tries 表会把不同节点当成同一个
    nodes2 = [{"fp": f"{i:016d}" + "b" * 16, "src": "s"} for i in range(60)]
    tr = {f"{i:016d}": 4 for i in range(30)}          # 前 30 个测过 4 次
    got = sample(nodes2, per_src=30, total=30, seed=7, tries=tr)
    chk(len(got) == 30 and all(tr.get(x["fp"][:16], 0) == 0 for x in got),
        "覆盖度优先：未测过的节点先被抽中")
    got2 = sample(nodes2, per_src=60, total=60, seed=7, tries=tr)
    chk(len(got2) == 60 and sum(1 for x in got2 if tr.get(x["fp"][:16], 0) == 0) == 30,
        "预算够时两者都要，测过的排在后面")

    # P25 三类轮换：新节点 / 失败复核 / 稳定复核 都要有代表，且可复现。
    # 这里用构造的历史记录只验证「分类与配额逻辑」，不是宣称真实检测覆盖。
    hist = {}
    for i in range(100):
        fp = f"{i:016d}"
        if i < 60:
            hist[fp] = {"tries": 0, "hits": 0}          # 新
        elif i < 80:
            hist[fp] = {"tries": 4, "hits": 1}          # 失败多 → retry
        else:
            hist[fp] = {"tries": 5, "hits": 5}          # 稳定 → stable
    nodes3 = [{"fp": f"{i:016d}" + "c" * 16, "src": "s"} for i in range(100)]
    p1 = sample(nodes3, per_src=100, total=100, seed=7, history_records=hist)
    cats1 = [classify(n, hist.get(n["fp"][:16])) for n in p1]
    chk(cats1.count("new") > 0 and cats1.count("retry") > 0 and cats1.count("stable") > 0,
        "三类轮换：新/失败复核/稳定复核都被抽到")
    p2 = sample(nodes3, per_src=100, total=100, seed=7, history_records=hist)
    chk([n["fp"] for n in p1] == [n["fp"] for n in p2], "三类轮换同种子可复现")
    chk(classify({"fp": "x"}, None) == "new", "无历史记录归为新节点（不猜稳定）")
    chk(classify({"fp": "x"}, {"tries": 6, "hits": 6}) == "stable",
        "多次成功归为稳定复核")
    chk(classify({"fp": "x"}, {"tries": 4, "hits": 1}) == "retry",
        "成功率低于一半归为失败复核")

    # 失败分类：以前全记 HTTP 000，四种原因挤成一个标签
    chk(CURL_ERR[6] == "dns" and CURL_ERR[35] == "tls" and CURL_ERR[28] == "timeout",
        "curl 退出码映射到失败阶段")
    s = summarize({"a": {"ok": False, "err": "dns:域名解析失败"},
                   "b": {"ok": True, "ms": 100},
                   "c": {"ok": False, "err": "tls:TLS 握手失败"},
                   "d": {"ok": False, "err": "dns:域名解析失败"}})
    chk(s["err_classes"] == {"dns": 2, "tls": 1}, "失败按阶段汇总，成功项不计入")
    chk(s["tested"] == 4 and s["ok"] == 1, "汇总基数正确")

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
