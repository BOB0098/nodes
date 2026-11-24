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

import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "nodecheck.json")
# 默认把 mihomo 放在仓库上一级，不进版本库。容器里上一级是 /（不可写），
# 所以允许用 NODES_CACHE 覆盖到挂载出来的目录——否则 Docker 部署第一次
# 下载就会失败，而失败信息是「Permission denied: /.nodes-cache」
CACHE = os.environ.get("NODES_CACHE") or os.path.join(os.path.dirname(BASE), ".nodes-cache")
MIHOMO = os.path.join(CACHE, "mihomo")

MIHOMO_VER = "v1.19.34"
# 官方 release 在沙箱/部分机房直连不通，走镜像；多个镜像按顺序试
MIHOMO_URLS = [
    f"https://gh-proxy.com/https://github.com/MetaCubeX/mihomo/releases/download/{MIHOMO_VER}/mihomo-linux-amd64-compatible-{MIHOMO_VER}.gz",
    f"https://github.com/MetaCubeX/mihomo/releases/download/{MIHOMO_VER}/mihomo-linux-amd64-compatible-{MIHOMO_VER}.gz",
]

BATCH = 40            # 每批节点数：一批一个 mihomo 进程，太多会吃内存
CONC = 20             # 单批内并发
TIMEOUT = 8           # 单节点总超时（秒）
BASE_PORT = 23000 + (os.getpid() % 200) * 60   # 避开并发跑的其它实例
TEST_URL = "https://www.gstatic.com/generate_204"
IP_URL = ("http://ip-api.com/json/?fields=status,country,countryCode,"
          "regionName,city,as,asname,isp,org,query")

# 抽样预算：全量 1 万节点逐个握手要几小时，GitHub Actions 跑不完。
# 每源配额 + 总量上限，按源均分，保证小源也能拿到样本。
PER_SRC = 90
MAX_TOTAL = 1200
SEED = 20261125       # 种子基数


def daily_seed(now=None):
    """按天轮换的抽样种子。

    固定种子会让每轮都测同一批节点：省事，但那些节点之外的部分永远没人验证，
    「稳定」这个标签也就只对几百条成立。按天换种子，一周能把全库覆盖一遍；
    同一天内的多次采集仍然抽到同一批，这样两轮之间的波动才有可比性。
    """
    days = int((now or time.time()) // 86400)
    return SEED + days


def ensure_mihomo():
    if os.path.exists(MIHOMO) and os.access(MIHOMO, os.X_OK):
        return MIHOMO
    os.makedirs(CACHE, exist_ok=True)
    last = None
    for u in MIHOMO_URLS:
        try:
            print(f"  下载 mihomo… {u.split('/')[2]}")
            subprocess.run(["curl", "-sL", "--max-time", "180", "-o", MIHOMO + ".gz", u],
                           check=True, timeout=200)
            subprocess.run(["gunzip", "-f", MIHOMO + ".gz"], check=True, timeout=60)
            os.chmod(MIHOMO, 0o755)
            v = subprocess.run([MIHOMO, "-v"], capture_output=True, text=True, timeout=30)
            print("  " + (v.stdout or v.stderr).strip().splitlines()[0])
            return MIHOMO
        except Exception as e:
            last = e
    raise RuntimeError(f"mihomo 下载失败：{last}")


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
        p.update({"password": cred, "udp": True})
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
    """通过本地 socks 端口访问测试地址。返回 (ok, ms, err)。"""
    proxy = f"socks5h://127.0.0.1:{port}"
    t0 = time.time()
    try:
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-x", proxy, "--max-time", str(timeout), TEST_URL],
            capture_output=True, text=True, timeout=timeout + 3)
        ms = int((time.time() - t0) * 1000)
        code = (r.stdout or "").strip()
        if code in ("204", "200"):
            return True, ms, None
        return False, ms, f"HTTP {code or '000'}"
    except subprocess.TimeoutExpired:
        return False, TIMEOUT * 1000, "超时"
    except Exception as e:
        return False, int((time.time() - t0) * 1000), type(e).__name__


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
        open(path, "w", encoding="utf-8").write(_to_yaml(cfg))
        proc = subprocess.Popen([MIHOMO, "-f", path, "-d", td],
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
    pairs, meta = [], {}
    for i, n in enumerate(items):
        name = f"n{i}"
        p = to_proxy(n, name)
        if not p:
            meta[name] = n
            continue
        pairs.append((name, p))
        meta[name] = n
    if not pairs:
        return {}

    cfg, ports = build_config(pairs, base_port)
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "config.yaml")
        open(path, "w", encoding="utf-8").write(cfg)
        proc = subprocess.Popen([MIHOMO, "-f", path, "-d", td],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            # 等端口起来。mihomo 通常 100ms 内就绪，给 6 秒余量足够
            deadline = time.time() + 6
            while time.time() < deadline:
                if all(_port_free(port) is False for port in ports.values()):
                    break
                time.sleep(0.1)
            time.sleep(0.2)

            out = {}
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

def sample(nodes, per_src=PER_SRC, total=MAX_TOTAL, seed=None):
    """按来源分层抽样。分层而不是纯随机：小源只有几十条，纯随机可能一条都抽不到，
    「这个源真实可用率 0%」就成了「没抽到」的假象。"""
    rnd = random.Random(daily_seed() if seed is None else seed)
    by_src = {}
    for n in nodes:
        by_src.setdefault(n.get("src") or "?", []).append(n)
    picked = []
    for src in sorted(by_src):
        arr = by_src[src]
        rnd.shuffle(arr)
        picked.extend(arr[:per_src])
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
    return {
        "tested": len(results), "ok": len(ok),
        "ok_pct": round(len(ok) / len(results) * 100, 1) if results else None,
        "ms_median": ms[len(ms) // 2] if ms else None,
        "ms_p90": ms[int(len(ms) * 0.9)] if ms else None,
    }


def save(results, extra=None):
    """CI 里落盘；本地默认不写。

    与 health / lifecycle 同一口径：本地出口的连通性与机房不可比，
    写进去会让页面上的「真实可用率」变成环境的函数而不是节点的函数。
    NODES_FORCE_WRITE=1 可强制（调试用）。
    """
    doc = {"updated": time.strftime("%Y-%m-%d %H:%M"), "env":
           "ci" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
           "summary": summarize(results), "nodes": results}
    if extra:
        doc.update(extra)
    force = (os.environ.get("GITHUB_ACTIONS") == "true"
             or os.environ.get("NODES_FORCE_WRITE") == "1")
    if not force:
        print("  （本地运行，不写入 nodecheck.json）")
        return doc
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    return doc


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

    picked = sample(nodes, per_src=per_src, total=max_total)
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
        json.dump({"updated": time.strftime("%Y-%m-%d %H:%M"), "env": "ci",
                   "summary": summarize(results), "nodes": results,
                   "shard": shard, "shards": shards},
                  open(out, "w", encoding="utf-8"), ensure_ascii=False,
                  separators=(",", ":"))
        print(f"\n分片结果 -> {os.path.relpath(out, BASE)}")
        return 0
    doc = save(results, {"budget": max_total, "per_src": per_src})
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

    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
