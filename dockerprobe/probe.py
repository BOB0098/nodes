#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P11：云服务器 Docker 能力自检。

任务书要求「在实际验证前，不得认定任何平台支持完整 Docker 或适合长期运行」。
这句话反过来就是：结论必须来自在**那台机器上**跑出来的东西，而不是官网的
功能清单。官网写的是「支持 Docker」，我们关心的是「这台免费的机器能不能在
被自动暂停之前，把一轮采集跑完，并且下一轮还认得上一轮的数据」。

所以这里只做一件事：在目标机器上逐项测，输出一份可比的 JSON。
测不了就写 skip 加上原因，绝不把「没测」写成「通过」——一份自己骗自己的
体检报告，比没有报告更糟。

用法：
    python3 probe.py run --platform instabox --json out/instabox.json
    python3 probe.py run --note "第二次，验证持久化"     # 隔一段时间再跑一次
    python3 probe.py report out/*.json                   # 汇总成 markdown 表格
    python3 probe.py selftest                            # 验证自身逻辑

判定口径（全在这几条上，别处不加规则）：
    docker_ok   镜像能拉、容器能跑，缺一不算
    persist_ok  上一次写的标记文件还在（跨会话/跨重启都算，只要还在）
    longrun_ok  墙钟走过的时间和开机时长对得上，即期间没有被停过
这三项都 pass 才叫「可以长期跑」。注意 longrun 只能证伪不能证明：
跑一次通过只说明「这次没被停」，要连续几天都对得上才算稳。
"""

import json
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import time

VERSION = 1
TMO = 25                        # 单条外部命令超时
MARKER = ".probe_marker.json"   # 持久化标记，落在数据盘上


# ── 基础工具 ────────────────────────────────────────────────────────────
def sh(cmd, timeout=TMO, stdin=None):
    """跑一条命令，永不抛异常。返回 (rc, stdout+stderr 合并, 耗时ms)。"""
    t0 = time.time()
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                           text=True, timeout=timeout, input=stdin)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode, out, int((time.time() - t0) * 1000)
    except FileNotFoundError:
        return 127, "命令不存在", int((time.time() - t0) * 1000)
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{timeout}s）", int((time.time() - t0) * 1000)
    except Exception as e:                                   # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}", int((time.time() - t0) * 1000)


def ok(detail, **kw):
    return dict(status="pass", detail=detail, **kw)


def bad(detail, **kw):
    return dict(status="fail", detail=detail, **kw)


def skip(detail, **kw):
    # skip 和 fail 必须分开：fail 是「测了，不行」，skip 是「没测成」。
    # 混在一起，一份全是 skip 的报告会看起来像一份全是 fail 的报告
    return dict(status="skip", detail=detail, **kw)


def read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:                                        # noqa: BLE001
        return default


def meminfo():
    d = {}
    for line in read("/proc/meminfo").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip()
    return d


def mib(s):
    """'8153728 kB' -> 7963（MiB）"""
    try:
        return int(s.split()[0]) // 1024
    except Exception:                                        # noqa: BLE001
        return None


# ── 各项检查 ────────────────────────────────────────────────────────────
def c_os():
    osname = ""
    for line in read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            osname = line.split("=", 1)[1].strip().strip('"')
    kernel = read("/proc/sys/kernel/osrelease").strip()
    arch = os.uname().machine
    return ok(f"{osname or '未知发行版'} / kernel {kernel} / {arch}",
              os=osname, kernel=kernel, arch=arch)


def c_spec():
    """规格：宿主机看得见的 + cgroup 实际允许的。

    容器里 /proc/meminfo 报的是宿主机的内存，必须读 cgroup 才是真正能用的量。
    少读这一层，8 GiB 的机器在 512 MiB 的容器里会显示成 8 GiB。
    """
    spec = {"cpu": os.cpu_count(), "mem_mib": mib(meminfo().get("MemTotal"))}
    # cgroup v2 先看，v1 兜底
    for p, key in (("/sys/fs/cgroup/memory.max", "cg_mem_mib"),
                   ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "cg_mem_mib")):
        v = read(p).strip()
        if v and v != "max":
            try:
                spec[key] = int(v) // (1024 * 1024)
                break
            except ValueError:
                pass
    for p in ("/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"):
        v = read(p).strip()
        if v and " " in v:                       # v2: "200000 100000"
            q, per = v.split()[:2]
            if q != "max":
                spec["cg_cpu"] = round(int(q) / int(per), 2)
                break
        elif v and v.isdigit() and int(v) > 0:   # v1: 微秒配额，按 100ms 周期算
            spec["cg_cpu"] = round(int(v) / 100000, 2)
            break
    du = shutil.disk_usage("/")
    spec["disk_total_gib"] = round(du.total / 2**30, 1)
    spec["disk_free_gib"] = round(du.free / 2**30, 1)
    return ok(f"{spec['cpu']} vCPU / {spec['mem_mib']} MiB / 可用 {spec['disk_free_gib']} GiB",
              spec=spec)


def c_uptime():
    """开机时长 + boot_id。用来和墙钟对照，判断期间有没有被停过。"""
    up = None
    try:
        up = float(read("/proc/uptime").split()[0])
    except Exception:                                        # noqa: BLE001
        pass
    boot = read("/proc/sys/kernel/random/boot_id").strip()
    if up is None:
        return skip("读不到 /proc/uptime")
    return ok(f"已开机 {up/60:.0f} 分钟", uptime_s=int(up), boot_id=boot)


def c_docker_cli():
    rc, out, ms = sh(["docker", "--version"])
    if rc == 127:
        return skip("没有 docker 命令")
    return (ok if rc == 0 else bad)(out.splitlines()[0] if out else f"rc={rc}", ms=ms)


def c_docker_daemon():
    rc, out, ms = sh("docker info --format '{{.ServerVersion}}|{{.Driver}}|"
                     "{{.CgroupVersion}}|{{.SecurityOptions}}'", timeout=30)
    if rc == 127:
        return skip("没有 docker 命令")
    if rc != 0:
        return bad(f"daemon 连不上：{out.splitlines()[0][:160] if out else 'rc!=0'}", ms=ms)
    parts = out.split("|")
    seco = parts[3] if len(parts) > 3 else ""
    rootless = "rootless" in seco
    return ok(f"server {parts[0]} / 存储 {parts[1]} / cgroup v{parts[2]}"
              + (" / rootless" if rootless else ""),
              server=parts[0], driver=parts[1], cgroup=parts[2], rootless=rootless, ms=ms)


def c_docker_run():
    """真的跑一个容器。拉的是 hello-world（十几 KB），只为确认 run 不被拦。"""
    rc, out, ms = sh(["docker", "run", "--rm", "hello-world"], timeout=120)
    if rc == 127:
        return skip("没有 docker 命令")
    if rc == 124:
        return bad("docker run 超时（>120s），大概率拉不到镜像", ms=ms)
    if rc != 0:
        return bad(f"docker run 失败：{(out.splitlines() or [''])[-1][:160]}", ms=ms)
    return ok("容器能起能退", ms=ms)


def c_pull_dockerhub():
    rc, out, ms = sh(["docker", "pull", "--platform", "linux/amd64", "alpine:3.20"],
                     timeout=180)
    if rc == 127:
        return skip("没有 docker 命令")
    if rc != 0:
        return bad(f"拉取失败：{(out.splitlines() or [''])[-1][:160]}", ms=ms)
    return ok("Docker Hub 可拉取（linux/amd64）", ms=ms)


def c_pull_ghcr():
    """GHCR 用探 /v2/ 的 401 挑战来判断，不去拉具体镜像。

    理由：随便挑一个 ghcr 镜像，那个镜像可能被删了或者改成私有，测出来的失败
    跟平台能力无关。而「能不能对 ghcr.io 完成未认证挑战」是平台的属性。
    """
    r = registry_probe("ghcr.io")
    return r


def registry_probe(host, path="/v2/"):
    """对 registry 发一个未认证请求，看是否返回 401 + WWW-Authenticate。

    401 挑战说明：网络通、TLS 通、registry 协议在跑，只是要 token——
    这正是「平台允许拉公共镜像」的表现。超时/连不上才是平台问题。
    """
    import ssl
    t0 = time.time()
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                ss.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                           f"User-Agent: nodes-probe/1\r\nConnection: close\r\n\r\n".encode())
                buf = b""
                while len(buf) < 4096:
                    chunk = ss.recv(1024)
                    if not chunk:
                        break
                    buf += chunk
    except Exception as e:                                   # noqa: BLE001
        return bad(f"{host} 不可达：{type(e).__name__}: {e}",
                   ms=int((time.time() - t0) * 1000))
    ms = int((time.time() - t0) * 1000)
    head = buf.decode("latin-1", "ignore")
    code = head.split(" ")[1] if head.startswith("HTTP/") and " " in head else "?"
    if code == "401":
        return ok(f"{host} 返回 401 未认证挑战（可拉公共镜像）", ms=ms, code=401)
    if code == "200":
        return ok(f"{host} 匿名可读", ms=ms, code=200)
    return bad(f"{host} 返回 {code}，不是预期的 401", ms=ms, code=code)


def c_egress_tcp():
    """出站 TCP。这是整条检测链路的命门：连不出去，节点检测全是 0%。

    这里刻意**不**放 registry-1.docker.io：容器能不能拉镜像走的是 daemon 的
    网络（可能是 registry mirror 或代理），和 Python 直连不是一条路。实测过
    这种情况——daemon 拉得下来，进程直连却不通。两件事分开测才不互相误导。
    """
    targets = [("api.github.com", 443), ("raw.githubusercontent.com", 443),
               ("www.gstatic.com", 443), ("gh-proxy.com", 443),
               ("ip-api.com", 80)]
    rows, good = [], 0
    for host, port in targets:
        t0 = time.time()
        try:
            with socket.create_connection((host, port), timeout=6):
                rows.append({"host": f"{host}:{port}", "ok": True,
                             "ms": int((time.time() - t0) * 1000)})
                good += 1
        except Exception as e:                               # noqa: BLE001
            rows.append({"host": f"{host}:{port}", "ok": False, "err": type(e).__name__})
    detail = f"{good}/{len(targets)} 可达"
    return (ok if good == len(targets) else bad)(detail, targets=rows)


def c_egress_udp():
    """出站 UDP/53。手搓一个 DNS 查询包：很多平台的免费层只放 TCP。

    不引第三方库是因为这一项在体检里只占一小格，为它加一个依赖不值。
    """
    q = struct.pack(">HHHHHH", random.randint(0, 0xFFFF), 0x0100, 1, 0, 0, 0)
    q += b"\x07example\x03com\x00" + struct.pack(">HH", 1, 1)
    t0 = time.time()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(5)
        s.sendto(q, ("1.1.1.1", 53))
        data, _ = s.recvfrom(512)
        s.close()
        ancount = struct.unpack(">H", data[6:8])[0] if len(data) > 12 else 0
        if ancount:
            return ok(f"UDP/53 通，收到 {ancount} 条应答（{int((time.time()-t0)*1000)}ms）")
        return bad("收到应答但答案数为 0")
    except Exception as e:                                   # noqa: BLE001
        return bad(f"UDP/53 不通：{type(e).__name__}")


def c_bind_ports():
    """能不能绑 80/443。只影响「要不要对外提供订阅」，不影响采集本身。"""
    res = {}
    for p in (80, 443, 8080):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", p))
            res[str(p)] = True
        except Exception as e:                               # noqa: BLE001
            res[str(p)] = f"{type(e).__name__}"
        finally:
            s.close()
    good = [k for k, v in res.items() if v is True]
    detail = "可绑定 " + (", ".join(good) if good else "无（80/443 需要 root 或已被占用）")
    return ok(detail, ports=res)


def c_docker_restart():
    """重启策略：容器崩了会不会自己起来。决定「能不能当常驻服务」。"""
    rc, out, _ = sh("docker info --format '{{json .Plugins}}'", timeout=20)
    rc2, pol, _ = sh("docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "
                     "$(docker ps -aq | head -1)", timeout=20)
    if rc2 == 0 and pol.strip() and "No such" not in pol:
        return ok(f"可读容器重启策略，当前示例为 {pol.strip()}", sample=pol.strip())
    return skip("当前没有容器可查，重启策略需在部署后复测")


def c_compose():
    for cmd in (["docker", "compose", "version"], ["docker-compose", "--version"]):
        rc, out, _ = sh(cmd, timeout=20)
        if rc == 0 and out:
            return ok(out.splitlines()[0])
    return skip("没有 docker compose")


def _persist_dir():
    for d in (os.environ.get("PERSIST_DIR"), "/data", "/app/data",
              os.path.expanduser("~")):
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    return None


def c_persist():
    """持久化：这次写，下次还在不在。

    单次运行证明不了持久化——只能留下标记，等下一轮（或重启后）再跑一次。
    所以第一次必然返回 skip，这是设计，不是缺陷。
    """
    d = _persist_dir()
    if not d:
        return bad("没有可写的数据目录")
    path = os.path.join(d, MARKER)
    prev = None
    try:
        prev = json.load(open(path, encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        pass
    now = int(time.time())
    boot = read("/proc/sys/kernel/random/boot_id").strip()
    cur = {"first_seen": (prev or {}).get("first_seen") or now, "last_seen": now,
           "runs": ((prev or {}).get("runs") or 0) + 1,
           "boot_id": boot, "prev_boot_id": (prev or {}).get("boot_id"),
           "dir": d}
    try:
        json.dump(cur, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:                                   # noqa: BLE001
        return bad(f"{d} 写不进去：{e}")
    if not prev:
        return skip(f"已写入标记（{d}），持久化要下次运行才能确认",
                    marker=path, runs=1)
    gap = now - (prev.get("last_seen") or now)
    rebooted = prev.get("boot_id") != boot
    return ok(f"第 {cur['runs']} 次运行仍看到上次标记：距上次 {gap//60} 分钟"
              + ("，且已重启过（boot_id 变了）" if rebooted else "，未重启"),
              marker=path, gap_s=gap, rebooted=rebooted, runs=cur["runs"])


def c_longrun(t_uptime):
    """墙钟 vs 开机时长。差值大 = 期间被暂停或重启过。

    这是判断「免费层会不会偷偷停我」的唯一实证手段：机器不会被问，
    但它的开机计时器会说。
    """
    m = _load_marker()
    if not m:
        return skip("没有上一次记录，连续运行无法判断（需隔一段时间再跑一次）")
    gap = int(time.time()) - m["last_seen"]
    up = t_uptime.get("uptime_s")
    if up is None:
        return skip("读不到开机时长")
    # 上次运行到现在过去了 gap 秒，机器却只开机了 up 秒 —— 说明这中间机器是停的
    stopped = gap - up
    if stopped > 300:
        return bad(f"两次运行间隔 {gap//60} 分钟，但只开机了 {up//60} 分钟："
                   f"中间约 {stopped//60} 分钟机器是停的",
                   gap_s=gap, uptime_s=up, stopped_s=stopped)
    return ok(f"间隔 {gap//60} 分钟、开机 {up//60} 分钟，基本对得上（未被停）",
              gap_s=gap, uptime_s=up, stopped_s=max(0, stopped))


def _load_marker():
    d = _persist_dir()
    if not d:
        return None
    try:
        return json.load(open(os.path.join(d, MARKER), encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return None


# ── 组装 ────────────────────────────────────────────────────────────────
def run(platform, note=""):
    t0 = time.time()
    checks, spec = {}, {}

    for name, fn in (("os", c_os), ("spec", c_spec), ("uptime", c_uptime),
                     ("docker_cli", c_docker_cli), ("docker_daemon", c_docker_daemon),
                     ("docker_run", c_docker_run), ("compose", c_compose)):
        r = fn()
        checks[name] = r
        if "spec" in r:
            spec = r["spec"]
    # 先拿开机时长，后面 longrun 要用
    up = checks.get("uptime") or {}

    checks["pull_dockerhub"] = c_pull_dockerhub()
    checks["registry_ghcr"] = c_pull_ghcr()
    checks["egress_tcp"] = c_egress_tcp()
    checks["egress_udp"] = c_egress_udp()
    checks["bind_ports"] = c_bind_ports()
    checks["docker_restart"] = c_docker_restart()
    checks["persist"] = c_persist()
    checks["longrun"] = c_longrun(up)

    st = {k: v["status"] for k, v in checks.items()}
    docker_ok = all(st.get(k) == "pass"
                    for k in ("docker_daemon", "docker_run", "pull_dockerhub"))
    verdict = {
        "docker_ok": docker_ok,
        "persist_ok": st.get("persist") == "pass",
        "longrun_ok": st.get("longrun") == "pass",
        "egress_ok": st.get("egress_tcp") == "pass",
        # 有 skip 就不下否定结论：说「不行」要有证据，说「不知道」才是现状
        "unknown": [k for k, v in st.items() if v == "skip"],
        "failed": [k for k, v in st.items() if v == "fail"],
    }
    verdict["suitable_for_daily"] = bool(docker_ok and verdict["egress_ok"]
                                         and verdict["longrun_ok"])
    return {"version": VERSION, "platform": platform, "note": note,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_s": round(time.time() - t0, 1),
            "spec": spec, "checks": checks, "verdict": verdict}


def report(paths):
    docs = []
    for p in paths:
        try:
            docs.append(json.load(open(p, encoding="utf-8")))
        except Exception as e:                               # noqa: BLE001
            print(f"跳过 {p}：{e}")
    if not docs:
        print("没有可汇总的结果")
        return
    names = [d.get("platform") or os.path.basename(p) for d, p in zip(docs, paths)]
    keys = []
    for d in docs:
        for k in d.get("checks", {}):
            if k not in keys:
                keys.append(k)
    mark = {"pass": "通过", "fail": "失败", "skip": "未测"}
    print("| 检查项 | " + " | ".join(names) + " |")
    print("|---|" + "---|" * len(names))
    for k in keys:
        cells = []
        for d in docs:
            c = (d.get("checks") or {}).get(k) or {}
            txt = c.get("detail") or ""
            # 截断要留痕：悄悄截成半句话，读的人会以为内容就这么多
            if len(txt) > 58:
                txt = txt[:57] + "…"
            cells.append(f"{mark.get(c.get('status'), '?')}：{txt}")
        print(f"| {k} | " + " | ".join(cells) + " |")
    print()
    print("| 结论 | " + " | ".join(names) + " |")
    print("|---|" + "---|" * len(names))
    for key in ("docker_ok", "egress_ok", "persist_ok", "longrun_ok", "suitable_for_daily"):
        cells = []
        for d in docs:
            v = (d.get("verdict") or {}).get(key)
            cells.append({True: "是", False: "否", None: "未知"}.get(v, str(v)))
        print(f"| {key} | " + " | ".join(cells) + " |")
    print()
    for d, n in zip(docs, names):
        u = (d.get("verdict") or {}).get("unknown") or []
        f = (d.get("verdict") or {}).get("failed") or []
        print(f"- {n}：未测 {', '.join(u) or '无'}；失败 {', '.join(f) or '无'}")


# ── 自测 ────────────────────────────────────────────────────────────────
def selftest():
    import tempfile
    n = 0

    def eq(a, b, msg):
        nonlocal n
        n += 1
        assert a == b, f"{msg}: {a!r} != {b!r}"

    # 1 skip 不能当 pass
    eq(ok("x")["status"], "pass", "ok")
    eq(skip("y")["status"], "skip", "skip")
    eq(ok("x")["status"] == skip("y")["status"], False, "skip 与 pass 必须分开")

    # 2 缺命令要报 skip 而不是 fail
    sh_rc = sh(["definitely-not-a-command-xyz"])[0]
    eq(sh_rc, 127, "缺命令返回 127")

    # 3 超时要返回 124 而不是抛异常
    eq(sh(["sleep", "3"], timeout=1)[0], 124, "超时返回 124")

    # 4 DNS 查询包结构：12 字节头 + qname + 4 字节 qtype/qclass
    q = struct.pack(">HHHHHH", 1, 0x0100, 1, 0, 0, 0) + b"\x07example\x03com\x00" \
        + struct.pack(">HH", 1, 1)
    eq(len(q), 12 + 13 + 4, "DNS 包长度")
    eq(struct.unpack(">H", q[:2])[0], 1, "DNS id")

    # 5 verdict：docker 三项缺一即 False
    for st, want in (
        ({"docker_daemon": "pass", "docker_run": "pass", "pull_dockerhub": "pass"}, True),
        ({"docker_daemon": "pass", "docker_run": "fail", "pull_dockerhub": "pass"}, False),
        ({"docker_daemon": "skip", "docker_run": "pass", "pull_dockerhub": "pass"}, False),
    ):
        got = all(st.get(k) == "pass"
                  for k in ("docker_daemon", "docker_run", "pull_dockerhub"))
        eq(got, want, f"docker_ok {st}")

    # 6 持久化第一次必须 skip（写进去了但要下次才能确认）
    tmp = tempfile.mkdtemp()
    os.environ["PERSIST_DIR"] = tmp
    first = c_persist()
    eq(first["status"], "skip", "首次持久化检查应为 skip")
    second = c_persist()
    eq(second["status"], "pass", "第二次持久化检查应为 pass")
    eq(second["runs"], 2, "运行次数应累加")
    # 伪造一次「上次运行在 10 分钟前、但机器只开了 1 分钟」→ 判为被停
    m = json.load(open(os.path.join(tmp, MARKER), encoding="utf-8"))
    m["last_seen"] = int(time.time()) - 600
    json.dump(m, open(os.path.join(tmp, MARKER), "w", encoding="utf-8"))
    lr = c_longrun({"uptime_s": 60})
    eq(lr["status"], "fail", "墙钟远大于开机时长应判 fail")

    # 7 表格：三种状态各自有词，不会把 skip 印成通过
    eq(mark_of("skip"), "未测", "skip 的显示名")
    eq(mark_of("pass"), "通过", "pass 的显示名")

    print(f"selftest 通过 {n} 项")
    shutil.rmtree(tmp, ignore_errors=True)


def mark_of(status):
    return {"pass": "通过", "fail": "失败", "skip": "未测"}.get(status, "?")


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return
    if a[0] == "selftest":
        selftest()
        return
    if a[0] == "report":
        report(a[1:])
        return
    if a[0] == "run":
        platform = os.environ.get("PLATFORM", "unknown")
        note, out = "", None
        if "--platform" in a:
            platform = a[a.index("--platform") + 1]
        if "--note" in a:
            note = a[a.index("--note") + 1]
        if "--json" in a:
            out = a[a.index("--json") + 1]
        r = run(platform, note)
        if out:
            os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
            json.dump(r, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        v = r["verdict"]
        print(f"{platform}: docker={'可用' if v['docker_ok'] else '不可用'} "
              f"出网={'通' if v['egress_ok'] else '不通'} "
              f"持久化={'确认' if v['persist_ok'] else '未确认'} "
              f"连续运行={'确认' if v['longrun_ok'] else '未确认'}"
              f" → 日常采集={'适合' if v['suitable_for_daily'] else '不适合/待确认'}")
        for k, c in r["checks"].items():
            print(f"  [{c['status']:4}] {k}: {c['detail']}")
        return
    print(f"未知子命令：{a[0]}（试试 run / report / selftest）")


if __name__ == "__main__":
    main()
