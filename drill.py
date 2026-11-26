#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P14：故障演练。

任务书列了七项：来源失效、容器重启、云服务器暂停、Actions 失败、检测超时、
数据库异常、订阅生成失败。每一项都要回答同一个问题——**上一轮的有效数据
还在不在**。

这个脚本的原则是「真的把故障造出来」，不是读一遍代码说「这里应该没问题」：

* 来源失效：真去请求一个 404 地址；
* 容器重启：真的 `kill -9` 一个正在写盘的子进程；
* 云服务器暂停：真的 `SIGSTOP` 再 `SIGCONT`；
* 检测超时：真的把 mihomo 换成一个只会 sleep 的假程序。

所有破坏性操作都在临时目录里做，仓库里的正式产物只读。第 4 项在临时应用
副本中省略 `export/nodes_full.json`，验证缺输入会失败且不会改写检测数据。

用法:
    python3 drill.py              # 全部七项
    python3 drill.py --only 2     # 只跑第 2 项
    python3 drill.py --md         # 写完 docs/P14-drills.md
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

NODECHECK = os.path.join(BASE, "data", "nodecheck.json")
FULL = os.path.join(BASE, "export", "nodes_full.json")
REPORT = os.path.join(BASE, "docs", "P14-drills.md")


def md5(p):
    try:
        with open(p, "rb") as source:
            return hashlib.md5(source.read()).hexdigest()
    except FileNotFoundError:
        return None


def ok(fn):
    """把「演练过程」和「结论」分开：函数抛异常=演练没做成，返回 False=演练失败。"""
    try:
        passed, detail = fn()
    except Exception as e:
        return False, f"演练过程本身出错：{type(e).__name__}: {str(e)[:120]}"
    return passed, detail


# ── 1. 来源失效 ──────────────────────────────────────────────────────

def drill_source_fail():
    import collector
    cache_dir = collector.CACHE
    probe_file = os.path.join(cache_dir, "drill_404.txt")
    if os.path.exists(probe_file):
        os.unlink(probe_file)
    # 同一目录下已有一份正常缓存，用来验证「一个源坏了不会连累别的源」
    others = [f for f in os.listdir(cache_dir) if f.endswith(".txt")]
    before = {f: md5(os.path.join(cache_dir, f)) for f in others}

    url = "https://raw.githubusercontent.com/BOB0098/nodes/main/__drill_missing__.txt"
    t0 = time.time()
    try:
        collector.fetch([url], "drill_404")
        return False, "404 地址竟然拉取成功"
    except Exception as e:
        err = type(e).__name__
    dt = round(time.time() - t0, 1)

    after = {f: md5(os.path.join(cache_dir, f)) for f in others}
    changed = [f for f in before if before[f] != after.get(f)]
    wrote = os.path.exists(probe_file)

    good = (not wrote) and (not changed)
    return good, (f"{dt}s 内抛出 {err}；未写缓存={not wrote}；"
                  f"其它 {len(before)} 个缓存文件{'未被改动' if not changed else '被改了'}"
                  + (f"（{changed[:3]}）" if changed else ""))


# ── 2. 容器重启（写盘中途 kill -9）───────────────────────────────────

CHILD_WRITE = r'''
import sys
sys.path.insert(0, sys.argv[1])
import netsafe
blob = "x" * int(sys.argv[3])          # 先分配，把「启动开销」挤出计时窗口
open(sys.argv[4], "w").write("go")     # 告诉父进程：马上进入写盘
netsafe.write_atomic(sys.argv[2], blob)
'''

CHILD_SLOW = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
import netsafe, os
target = sys.argv[2]
n = int(sys.argv[3])
# 分块写再 rename：模拟真实的大文件写盘过程，好让父进程有机会在中间打断
d = os.path.dirname(os.path.abspath(target))
tmp = os.path.join(d, ".tmp-drill.part")
with open(tmp, "w") as f:
    for _ in range(n // (1 << 20)):
        f.write("y" * (1 << 20))
        f.flush()
        time.sleep(0.03)
os.replace(tmp, target)
'''


def drill_kill9():
    import netsafe
    tmp = tempfile.mkdtemp(prefix="drill-kill9-")
    try:
        target = os.path.join(tmp, "state.json")
        netsafe.write_atomic(target, '{"v": 1, "note": "上一轮的有效数据"}')
        before = open(target, "rb").read()

        child = os.path.join(tmp, "child.py")
        open(child, "w", encoding="utf-8").write(CHILD_WRITE)
        started = os.path.join(tmp, "started")
        # 不能「启动后睡 0.12 秒再杀」：解释器启动 + import 就要 0.1 秒，
        # 杀早了根本没进写盘阶段，断言「文件没变」也就没有意义。
        # 让子进程在进入写盘前打个标记，等到标记再杀，才真的落在写入窗口里。
        p = subprocess.Popen([sys.executable, child, BASE, target,
                              str(120 << 20), started])
        deadline = time.time() + 20
        while not os.path.exists(started) and time.time() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)               # 让它真的开始写
        p.kill()
        p.wait(timeout=10)

        after = open(target, "rb").read()
        left = [f for f in os.listdir(tmp) if f.startswith(".tmp-")]
        restorable = json.loads(after.decode()) == {"v": 1, "note": "上一轮的有效数据"}

        # 有残留临时文件 = 确实中断在「写临时文件」阶段，而不是写完之后才杀
        interrupted = bool(left)
        good = restorable and after == before
        return good, (f"目标文件{'保持原样' if after == before else '被破坏'}，"
                      f"可解析={restorable}；"
                      + (f"残留 {left[0]}（证明中断发生在 rename 之前）" if interrupted
                         else "未见残留临时文件（子进程可能已写完）"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 3. 云服务器暂停（SIGSTOP / SIGCONT）──────────────────────────────

def drill_pause():
    tmp = tempfile.mkdtemp(prefix="drill-pause-")
    try:
        target = os.path.join(tmp, "state.json")
        child = os.path.join(tmp, "child.py")
        open(child, "w", encoding="utf-8").write(CHILD_SLOW)
        # 30MB / 1MB 一块 / 每块 30ms ≈ 0.9 秒，足够在中间暂停
        p = subprocess.Popen([sys.executable, child, BASE, target, str(30 << 20)])
        time.sleep(0.2)
        p.send_signal(signal.SIGSTOP)
        time.sleep(2.0)                 # 模拟主机被暂停两秒
        p.send_signal(signal.SIGCONT)
        rc = p.wait(timeout=60)

        exists = os.path.exists(target)
        size = os.path.getsize(target) if exists else 0
        left = [f for f in os.listdir(tmp) if f.startswith(".tmp-")]
        good = rc == 0 and exists and size == 30 << 20 and not left
        return good, (f"退出码 {rc}，最终文件 {size // (1 << 20)}MB"
                      f"（期望 30MB），残留临时文件 {len(left)} 个")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 4. Actions 失败（某步挂了，后面不能拿旧数据算出新结论）──────────

def drill_actions_fail():
    before = md5(NODECHECK)
    if before is None:
        return False, "缺少 data/nodecheck.json，先跑一轮 CI"
    with tempfile.TemporaryDirectory(prefix="drill-actions-fail-") as tmp:
        for name in ("nodecheck.py", "netsafe.py"):
            shutil.copy2(os.path.join(BASE, name), os.path.join(tmp, name))
        tmp_data = os.path.join(tmp, "data")
        os.makedirs(tmp_data)
        tmp_nodecheck = os.path.join(tmp_data, "nodecheck.json")
        shutil.copy2(NODECHECK, tmp_nodecheck)
        # 故意不复制 export/nodes_full.json，模拟 Actions 阶段输入缺失。
        result = subprocess.run(
            [sys.executable, os.path.join(tmp, "nodecheck.py")],
            capture_output=True, text=True, cwd=tmp, timeout=120,
        )
        after = md5(NODECHECK)
        clone_after = md5(tmp_nodecheck)
        good = result.returncode != 0 and after == before and clone_after == before
        tail = (result.stdout or result.stderr).strip().splitlines()
        return good, (f"临时副本退出码 {result.returncode}（期望非 0），正式 nodecheck.json "
                      f"{'未变' if after == before else '被改写'}，副本检测数据 "
                      f"{'未变' if clone_after == before else '被改写'}；"
                      f"最后一行：{tail[-1][:60] if tail else '—'}")


# ── 5. 检测超时（mihomo 起不来）──────────────────────────────────────

def drill_detect_timeout():
    import nodecheck
    tmp = tempfile.mkdtemp(prefix="drill-mihomo-")
    fake = os.path.join(tmp, "mihomo")
    open(fake, "w").write("#!/bin/sh\nsleep 30\n")
    os.chmod(fake, 0o755)

    orig = nodecheck.MIHOMO
    nodecheck.MIHOMO = fake
    try:
        nodes = [{"fp": "drill000000000001", "type": "vmess", "server": "1.2.3.4",
                  "port": 443, "cred": "d1", "network": "ws", "path": "/", "tls": "tls"}]
        t0 = time.time()
        # 端口全空闲，run_batch 会等 6 秒后放弃，再给每个节点跑一次探测
        # 固定一个冷门端口段：演练可能和真实检测同时跑，别撞在一起
        res = nodecheck.run_batch(nodes, 39200, do_speed=False)
        dt = round(time.time() - t0, 1)
        all_fail = all(not r.get("ok") for r in res.values())
        good = dt < 60 and all_fail and len(res) == 1
        return good, (f"{dt}s 内返回（上限 60s），{len(res)} 条结果全部失败={all_fail}"
                      f"，无异常抛出")
    finally:
        nodecheck.MIHOMO = orig
        shutil.rmtree(tmp, ignore_errors=True)


# ── 6. 数据库异常（半截 JSON）────────────────────────────────────────

def drill_bad_json():
    import nodescore
    import collector
    tmp = tempfile.mkdtemp(prefix="drill-json-")
    try:
        bad = os.path.join(tmp, "node_scores.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write('{"summary": {"tested": 994, "ok": 33')     # 截断
        got = nodescore.load(bad, {"fallback": True})
        ok1 = got == {"fallback": True}

        orig = collector.OUT_DATA
        collector.OUT_DATA = tmp
        try:
            s = collector._load_scores()
            real = collector._load_real()
        finally:
            collector.OUT_DATA = orig
        ok2 = s == {} and real == {}

        # 页面上必须能看出「这轮没有评分」，而不是把它当 0 分
        good = ok1 and ok2
        return good, (f"nodescore.load 回退到默认值={ok1}；"
                      f"collector._load_scores→{s}、_load_real→{real}"
                      f"（空 dict 而不是抛异常）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 7. 订阅生成失败（条数不符）───────────────────────────────────────

def drill_verify_fail():
    import export_subs
    text = "\n".join(f"vless://00000000-0000-0000-0000-00000000000{i}@1.2.3.4:443"
                     for i in range(10))
    okk, got, why = export_subs.verify("uri", text, 99)      # 故意要 99 条
    good = (okk is False) and got == 10 and bool(why)
    # 反过来：条数对上时必须判真，否则这道闸门等于永远关着
    okk2, got2, _ = export_subs.verify("uri", text, 10)
    good = good and okk2 and got2 == 10
    return good, (f"期望 99 实得 10 → 判假={not okk}（{why[:40]}）；"
                  f"期望 10 实得 10 → 判真={okk2}")


DRILLS = [
    ("来源失效", drill_source_fail),
    ("容器重启（写盘中途 kill -9）", drill_kill9),
    ("云服务器暂停（SIGSTOP/SIGCONT）", drill_pause),
    ("Actions 失败（缺输入时退出非 0）", drill_actions_fail),
    ("检测超时（mihomo 起不来）", drill_detect_timeout),
    ("数据库异常（半截 JSON）", drill_bad_json),
    ("订阅生成失败（条数不符）", drill_verify_fail),
]


def main(argv):
    only = None
    if "--only" in argv:
        only = int(argv[argv.index("--only") + 1])

    rows, all_ok = [], True
    for i, (name, fn) in enumerate(DRILLS, 1):
        if only and i != only:
            continue
        t0 = time.time()
        passed, detail = ok(fn)
        dt = round(time.time() - t0, 1)
        all_ok = all_ok and passed
        print(f"  {'✔' if passed else '✘'} {i}. {name}  {dt}s\n      {detail}")
        rows.append({"i": i, "name": name, "pass": passed, "detail": detail, "sec": dt})

    if "--md" in argv or not only:
        write_report(rows)
        print(f"\n报告 -> {os.path.relpath(REPORT, BASE)}")
    print("\n" + ("七项全部通过" if all_ok else "有失败项"))
    return 0 if all_ok else 1


def write_report(rows):
    L = ["# P14：故障演练\n",
         "每一项都要回答同一个问题：**上一轮的有效数据还在不在**。\n",
         "不是读代码推断，是真的把故障造出来——真请求 404、真 `kill -9`、"
         "真 `SIGSTOP`、真把 mihomo 换成只会 sleep 的假程序。\n",
         "| # | 故障 | 结果 | 证据 | 用时 |",
         "|---:|---|---|---|---:|"]
    for r in rows:
        L.append(f"| {r['i']} | {r['name']} | {'✔ 通过' if r['pass'] else '✘ 失败'} | "
                 f"{r['detail'].replace('|', '/')} | {r['sec']}s |")
    L.append("")
    L.append("## 说明\n")
    L.append("* 第 1 项的耗时取决于当时的网络：404 正常情况下十几秒，链路拥塞时"
             "会一直走到 90 秒读预算才超时。判据不是「多久失败」，而是"
             "「没写缓存、其它源的文件一个字节没变」——一个源变坏不能连累别的源。")
    L.append("* 第 4 项在临时应用副本中省略 `export/nodes_full.json`，"
             "验证 Actions 缺输入时非零退出；不会挪动或改写仓库正式产物。")
    L.append("* 第 2 项判定「真的中断在写入窗口内」的依据是**残留的 `.tmp-*.part`**："
             "它在，说明进程死在 rename 之前，此时目标文件必须还是旧内容。"
             "如果没有残留，说明子进程已经写完，这一轮演练不算数。")
    L.append("* 第 5 项里 `run_batch` 会等 6 秒端口，再逐个探测。"
             "mihomo 起不来时结果为「全部失败」而不是抛异常——"
             "抛异常会让整轮检测中止，全失败会正常走到评分阶段并被打上「待复测」。")
    L.append("* 第 6 项的期望值是**空 dict 而不是 0 分**：读不到评分和「大家都 0 分」"
             "在页面上必须能区分，否则一次文件损坏会被读成「所有节点都不行了」。")
    open(REPORT, "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
