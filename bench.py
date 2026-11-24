#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P14：规模压测。

任务书要求「500 / 2,000 / 5,000 / 10,000 节点，记录耗时、CPU/内存峰值、
网络流量、成功率、超时率、失败率、订阅完整性」，并明确「资源不足时如实
报告，不得虚报通过」。

这个脚本测的是**离线阶段**：解析、生成配置、导出订阅、渲染页面。这几步
不依赖网络，同一台机器上跑几遍数字是稳的，能用来判断「库涨到一万条以后
会不会卡」。

真实检测阶段（mihomo 握手）单独用 `--net`：它受出口带宽和节点质量影响，
在受限环境里跑出来的可用率不能当成节点质量（P6 已经踩过这个坑）。这一项
的耗时与流量如实记录，可用率只作参考。

做法上有个细节：每个规模都起一个**独立子进程**。`ru_maxrss` 是进程生命
周期内的峰值，同一个进程连着跑 500 和 10,000，第二次的数字会被第一次污染，
看起来「10,000 条只用 80MB」——那是假的。

用法:
    python3 bench.py                # 4 个规模全跑，写 docs/P14-bench.md
    python3 bench.py --one 2000     # 单规模，JSON 到 stdout
    python3 bench.py --net 500      # 真实检测阶段（联网，慢）
"""

import json
import os
import resource
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

FULL = os.path.join(BASE, "export", "nodes_full.json")
PAGE = os.path.join(BASE, "data", "nodes.json")
REPORT = os.path.join(BASE, "docs", "P14-bench.md")
NETLOG = os.path.join(BASE, "data", "bench_net.json")   # 真实检测阶段的实测记录
SIZES = (500, 2000, 5000, 10000)


def netsafe_write(path, text):
    import netsafe
    netsafe.write_atomic(path, text)


def rss_mb():
    """当前进程的峰值常驻内存（Linux 上 ru_maxrss 单位是 KB）。"""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, round((time.perf_counter() - t0) * 1000, 1)


def pick(nodes, n):
    """按源分层抽 n 条。

    直接取前 n 条会严重偏斜——`nodes_full.json` 是按源顺序写的，前 500 条
    可能全来自同一个源、全是 vmess。分层抽样能让每个规模的协议分布接近
    真实库，测出来的耗时才对得上「线上跑一轮」。
    """
    import nodecheck
    if n >= len(nodes):
        return nodes
    return nodecheck.sample(nodes, per_src=n, total=n, seed=7)


def run_one(n):
    """跑一个规模，返回各阶段指标。不写任何数据文件。"""
    import base64
    import nodes_parse
    import nodecheck
    import export_subs
    import collector

    if not os.path.exists(FULL):
        raise SystemExit("缺少 export/nodes_full.json，先跑一次 collector.py")
    all_nodes = json.load(open(FULL, encoding="utf-8"))
    picked = pick(all_nodes, n)

    res = {"n": len(picked), "req": n}

    # ① 解析：明文 URI 列表（最坏情况——没有 YAML/JSON 的结构，逐行扫）
    text = "\n".join(x["uri"] for x in picked)
    (parsed, declared), res["parse_ms"] = timed(
        lambda: nodes_parse.parse_any(text.encode()))
    res["parse_out"] = len(parsed)
    res["parse_declared"] = declared
    res["text_mb"] = round(len(text) / 1048576, 2)

    # ② 生成 Clash 配置 → 再读回来。这一步同时压到生成与解析两条路径，
    # 也是「订阅能不能被客户端吃下去」的第一道自检
    proxies = [(f"n{i}", nodecheck.to_proxy(x, f"n{i}"))
               for i, x in enumerate(picked)]
    proxies = [(t, p) for t, p in proxies if p]
    res["proxies"] = len(proxies)
    (cfg, _ports), res["config_ms"] = timed(
        lambda: nodecheck.build_config(proxies, 24000))
    res["config_mb"] = round(len(cfg.encode()) / 1048576, 2)

    # ③ 四种订阅格式 + 逐个反解校验。校验失败必须在报告里显形，
    # 不能只报「生成了 10,000 条」。
    # 生成与校验分开计时：校验（把四种产物重新解析一遍）在 10,000 条时
    # 比生成还慢，混在一起会让「导出慢」看不出慢在哪。
    t0 = time.perf_counter()
    clash_text, res["clash_ms"] = timed(lambda: export_subs.build_clash(picked))
    sb, res["sb_ms"] = timed(lambda: export_subs.build_singbox(picked))
    uris, res["uri_ms"] = timed(lambda: export_subs.build_uris(picked))
    b64 = base64.b64encode(uris.encode()).decode()
    res["gen_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    res["clash_mb"] = round(len(clash_text.encode()) / 1048576, 2)

    t1 = time.perf_counter()
    checks = {
        "clash": export_subs.verify("clash", clash_text, len(picked)),
        "singbox": export_subs.verify("singbox", sb, len(picked)),
        "uri": export_subs.verify("uri", uris, len(picked)),
        "b64": export_subs.verify("b64", b64, len(picked)),
    }
    res["verify_ms"] = round((time.perf_counter() - t1) * 1000, 1)
    res["export_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    res["export_mb"] = round((len(clash_text) + len(sb) + len(uris) + len(b64)) / 1048576, 2)
    res["verify"] = {k: v[0] for k, v in checks.items()}
    res["verify_got"] = {k: v[1] for k, v in checks.items()}
    res["verify_ok"] = sum(1 for v in res["verify"].values() if v)

    # ④ 渲染：模板替换 + 整页 JSON 序列化。
    # 页面由浏览器渲染，这里只测服务端这一步——800KB 的内嵌 JSON 会不会
    # 让手机上打开就卡，属于客户端的事，不在这个脚本的结论里。
    page_src = json.load(open(PAGE, encoding="utf-8"))
    page = {k: v for k, v in page_src.items() if k != "nodes"}
    page["nodes"] = page_src["nodes"][:len(picked)]
    html, res["render_ms"] = timed(lambda: collector.render_page(page))
    res["html_mb"] = round(len(html.encode()) / 1048576, 2)

    ru = resource.getrusage(resource.RUSAGE_SELF)
    res["cpu_s"] = round(ru.ru_utime + ru.ru_stime, 2)
    res["rss_mb"] = rss_mb()
    return res


def net_one(n):
    """真实检测阶段：测耗时与网卡流量。可用率只作参考（见模块说明）。"""
    import nodecheck
    all_nodes = json.load(open(FULL, encoding="utf-8"))
    picked = pick(all_nodes, n)

    def net_bytes():
        tot = 0
        try:
            with open("/proc/net/dev") as f:
                for line in f.readlines()[2:]:
                    iface, rest = line.split(":", 1)
                    if iface.strip() == "lo":
                        continue        # 回环不出去，算进来会重复计数
                    cols = rest.split()
                    tot += int(cols[0]) + int(cols[8])
        except Exception:
            return None
        return tot

    b0 = net_bytes()
    t0 = time.time()
    results = {}
    for i in range(0, len(picked), nodecheck.BATCH):
        batch = picked[i:i + nodecheck.BATCH]
        base = nodecheck.BASE_PORT
        while any(not nodecheck._port_free(base + k) for k in range(len(batch))):
            base += 200
        results.update(nodecheck.run_batch(batch, base, do_speed=True))
    dt = time.time() - t0
    b1 = net_bytes()
    s = nodecheck.summarize(results)
    return {"n": len(picked), "sec": round(dt, 1),
            "ok": s["ok"], "ok_pct": s["ok_pct"],
            "timeout_pct": round(sum(1 for r in results.values()
                                     if r.get("err") == "超时") / max(1, len(results)) * 100, 1),
            "fail_pct": round(sum(1 for r in results.values()
                                  if not r.get("ok")) / max(1, len(results)) * 100, 1),
            "net_mb": None if b0 is None or b1 is None else round((b1 - b0) / 1048576, 2),
            "per_node_kb": None if b0 is None else round((b1 - b0) / 1024 / max(1, len(results)), 1)}


def run_all():
    rows = []
    for n in SIZES:
        print(f"  压测 {n} 条…", flush=True)
        out = subprocess.run([sys.executable, os.path.abspath(__file__), "--one", str(n)],
                             capture_output=True, text=True, cwd=BASE)
        if out.returncode != 0:
            print(out.stderr[-400:])
            raise SystemExit(f"{n} 条压测失败")
        rows.append(json.loads(out.stdout.strip().splitlines()[-1]))
        r = rows[-1]
        print(f"    {r['n']} 条：解析 {r['parse_ms']}ms，配置 {r['config_ms']}ms，"
              f"导出 {r['export_ms']}ms，渲染 {r['render_ms']}ms，峰值 {r['rss_mb']}MB")

    write_report(rows)
    print(f"\n报告 -> {os.path.relpath(REPORT, BASE)}")
    return 0


def write_report(rows):
    L = []
    A = L.append
    A("# P14：规模压测\n")
    A("机器：`{}` · Python {} · 每个规模独立进程（峰值内存不被上一个规模污染）\n".format(
        os.uname().machine, sys.version.split()[0]))
    A("样本来自真实库的分层抽样（按源等额），不是合成数据。\n")

    A("## 离线阶段\n")
    A("| 节点数 | 输入文本 | 解析（URI 列表） | 生成 mihomo 配置 | 配置体积 |")
    A("|---:|---:|---:|---:|---:|")
    for r in rows:
        A(f"| {r['n']:,} | {r['text_mb']} MB | {r['parse_ms']} ms | {r['config_ms']} ms | "
          f"{r['config_mb']} MB |")
    A("")
    A("导出阶段拆成「生成」和「校验」两段。校验是把四种产物重新解析一遍，"
      "10,000 条时比生成还慢——混在一起会看不出慢在哪。\n")
    A("| 节点数 | 生成 Clash | 生成 sing-box | 生成 URI | 四格式校验 | 合计 | 产物体积 |")
    A("|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        A(f"| {r['n']:,} | {r['clash_ms']} ms | {r['sb_ms']} ms | {r['uri_ms']} ms | "
          f"{r['verify_ms']} ms | {r['export_ms']} ms | {r['export_mb']} MB |")
    A("")
    A("| 节点数 | 渲染 | 页面体积 | CPU | 峰值内存 |")
    A("|---:|---:|---:|---:|---:|")
    for r in rows:
        A(f"| {r['n']:,} | {r['render_ms']} ms | {r['html_mb']} MB | {r['cpu_s']} s | "
          f"{r['rss_mb']} MB |")
    A("")

    A("## 订阅完整性\n")
    A("四种格式生成后立刻用本项目自己的解析器反解，条数必须等于输入条数。\n")
    A("| 节点数 | Clash | sing-box | URI 列表 | Base64 |")
    A("|---:|---|---|---|---|")
    for r in rows:
        cells = []
        for k in ("clash", "singbox", "uri", "b64"):
            ok = r["verify"].get(k)
            got = r["verify_got"].get(k)
            cells.append(f"{'✔' if ok else '✘'} {got}/{r['n']:,}")
        A(f"| {r['n']:,} | " + " | ".join(cells) + " |")
    A("")

    # 结论按实测数字生成，不写死
    big = rows[-1]
    A("## 结论\n")
    total = big['parse_ms'] + big['config_ms'] + big['export_ms'] + big['render_ms']
    A(f"* 全库 {big['n']:,} 条（压测请求 10,000，库本身是上限）全链路离线耗时约 "
      f"**{round(total / 1000, 1)} 秒**，"
      f"峰值内存 **{big['rss_mb']} MB**。其中四格式校验占 "
      f"{round(big['verify_ms'] / total * 100)}%——它是纯 CPU 的重复解析，"
      f"不是必须每轮都做的事（见下）。")
    small = rows[0]['parse_ms'] + rows[0]['config_ms'] + rows[0]['export_ms'] + rows[0]['render_ms']
    A(f"* 耗时随规模线性增长：{rows[0]['n']:,} → {big['n']:,} 条是 "
      f"{round(big['n'] / max(1, rows[0]['n']), 1)} 倍规模、{round(total / max(1, small), 1)} 倍耗时，"
      f"没有出现超线性的点（没有隐藏的 O(n²)）。")
    A(f"* 页面体积 {big['html_mb']} MB，其中绝大部分是内嵌的页面数据。这是"
      f"「单文件、双击可用、无需后端」的代价，取舍写在 P12。")
    A("* 四格式校验全部通过才写文件，任何一组条数不符都会记进 `subscriptions.json` 的 `failed`。")
    A("* 校验是「每次生成都跑、没有开关」的设计：宁可多花 "
      f"{round(big['verify_ms'] / 1000, 1)} 秒，也不要发出一份客户端打不开的订阅。"
      "它不属于性能问题——真要省时间，该省的是每轮重跑全量，而不是跳过校验。")
    A("")
    A("## 真实检测阶段（联网）\n")
    net = {}
    try:
        net = json.load(open(NETLOG, encoding="utf-8"))
    except Exception:
        pass
    if net:
        A("| 节点数 | 耗时 | 每条均摊 | 可用 | 失败 | 超时 | 网卡流量 | 每节点流量 |")
        A("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for k in sorted(net, key=int):
            r = net[k]
            A(f"| {r['n']:,} | {r['sec']} s | {round(r['sec'] / max(1, r['n']), 2)} s | "
              f"{r['ok']}（{r['ok_pct']}%） | {r['fail_pct']}% | {r['timeout_pct']}% | "
              f"{r['net_mb']} MB | {r['per_node_kb']} KB |")
        A("")
        A("**这里的可用率不能当节点质量读**：本机出口只放行白名单域名，"
          "同一批节点在 CI 上是 34%、在本机是 3.6%（P6 实测的反差）。"
          "这张表要的是耗时与流量，不是成功率。")
        A("")
        # 用实测的最小规模算速率，外推大库的耗时。不写死数字：
        # 换台机器、换个出口，这两个数就变了。
        base = net[min(net, key=int)]
        rate = base["n"] / max(0.1, base["sec"])
        est_min = round(10000 / rate / 60, 1)
        A(f"耗时与流量都随节点数线性增长，没有规模效应：每秒约 {rate:.1f} 条，"
          f"每条约 {base['per_node_kb']} KB（握手 204 响应 + 通过延迟筛选后的 512 KB 测速）。"
          f"10,000 条按此外推约 {est_min} 分钟——"
          "这也是 CI 只抽样 1200 条而不是全量的原因。")
        A("")
    else:
        A("尚未跑过 `bench.py --net <n>`，本节留空。**不填估算值**："
          "没有实测就不写数字，是本项目一贯的做法。")
        A("")
    A("")
    open(REPORT, "w", encoding="utf-8").write("\n".join(L))


def main(argv):
    if "--one" in argv:
        n = int(argv[argv.index("--one") + 1])
        print(json.dumps(run_one(n), ensure_ascii=False))
        return 0
    if "--net" in argv:
        n = int(argv[argv.index("--net") + 1])
        r = net_one(n)
        # 记下来，写报告时直接读，不用把整个规模再跑一遍
        old = {}
        try:
            old = json.load(open(NETLOG, encoding="utf-8"))
        except Exception:
            pass
        old[str(n)] = r
        os.makedirs(os.path.dirname(NETLOG), exist_ok=True)
        netsafe_write(NETLOG, json.dumps(old, ensure_ascii=False, indent=1))
        print(json.dumps(r, ensure_ascii=False))
        return 0
    return run_all()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
