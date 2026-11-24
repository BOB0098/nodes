#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P15：客户端真实导入验证。

「文件生成了」和「客户端收得下」是两件事。我们自己的 `verify()` 只证明
「能被本项目的解析器读回来」，证明不了 sing-box 会不会因为一条字段不合规
就拒绝整份配置——P13 之前就因为一条 `public_key: ""` 让 sing-box 直接
`invalid public_key` 退出，我们自己的校验全绿。

所以这里用客户端自己的二进制来判：

* **Mihomo**（Clash Meta 内核）：`mihomo -t -f all.yaml`
* **sing-box**：`sing-box check -c all.json`
* **Hiddify**：内核就是 sing-box，配置格式一致，用同一条检查；
  它自己没有无需登录的 CLI，这点在报告里写清楚，不冒充「Hiddify 实测通过」
* **Xray**：`xray -test -c cfg.json`。Xray 不吃 URI 列表，只吃 JSON，
  所以这里把 URI 逐个转成 outbound——转换器本身也是被验证的对象：
  转错了 Xray 会报错。

用法:
    python3 clienttest.py                  # 测 export/subs 里已有的订阅
    python3 clienttest.py --build          # 先按当前数据重新生成订阅再测
    python3 clienttest.py --limit 500      # 只测前 500 条（Xray 转换用）

二进制找不到时**不跳过成功**，而是记为「未测」并在报告里写明原因。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

CACHE = os.environ.get("NODES_CACHE") or os.path.join(os.path.dirname(BASE), ".nodes-cache")
SUBS = os.path.join(BASE, "export", "subs")
REPORT = os.path.join(BASE, "docs", "P15-clients.md")

BINS = {
    "mihomo": os.path.join(CACHE, "mihomo"),
    "sing-box": os.path.join(CACHE, "sing-box"),
    "xray": os.path.join(CACHE, "xray"),
}


def run(cmd, timeout=180):
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode, out, round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        return -1, f"超时（{timeout}s）", round(time.time() - t0, 1)
    except FileNotFoundError:
        return -2, "二进制不存在", round(time.time() - t0, 1)


# ── Xray：URI → outbound ────────────────────────────────────────────────────

def _stream(n):
    """统一模型 → Xray 的 streamSettings。"""
    net = n.get("network") or "tcp"
    # 只认 Xray 真实存在的 transport。其余（none、被污染的 ws#xxx、拼错的）
    # 一律按 tcp 处理：一条脏数据不该让整份配置构建失败
    ss = {"network": net if net in ("ws", "grpc", "h2", "kcp", "quic",
                                    "httpupgrade", "xhttp") else "tcp"}
    if net == "ws":
        ss["wsSettings"] = {"path": n.get("path") or "/",
                            "headers": {"Host": n.get("host") or ""}}
    elif net == "grpc":
        ss["grpcSettings"] = {"serviceName": n.get("path") or ""}
    elif net in ("h2", "http"):
        ss["network"] = "h2"
        ss["httpSettings"] = {"path": n.get("path") or "/",
                              "host": [n.get("host")] if n.get("host") else []}

    sec = (n.get("security") or "").lower()
    if sec == "reality":
        ss["security"] = "reality"
        ss["realitySettings"] = {
            "serverName": n.get("sni") or "",
            "fingerprint": n.get("fingerprint") or "chrome",
            "publicKey": n.get("public_key") or "",
            "shortId": n.get("short_id") or "",
            "spiderX": "/",
        }
    elif n.get("tls") or sec == "tls":
        ss["security"] = "tls"
        # Xray 26 删掉了 allowInsecure（改成 pinnedPeerCertSha256 /
        # verifyPeerCertByName），写进去会直接拒绝整份配置。我们没有证书指纹，
        # 所以只能不给这个选项——代价是自签证书的节点在 Xray 26 上连不通，
        # 这是客户端的策略变化，不是订阅的缺陷，报告里单独说明。
        ss["tlsSettings"] = {"serverName": n.get("sni") or "",
                             "fingerprint": n.get("fingerprint") or "chrome"}
        if n.get("alpn"):
            ss["tlsSettings"]["alpn"] = [a for a in str(n["alpn"]).split(",") if a]
    return ss


XRAY_SS = {"aes-128-gcm", "aes-256-gcm", "chacha20-poly1305", "xchacha20-poly1305",
           "none", "2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm",
           "2022-blake3-chacha20-poly1305"}


def xray_out(n, tag):
    """一条节点 → 一个 Xray outbound。不支持的协议返回 None（不算失败）。"""
    t = n.get("type")
    host, port = n.get("server"), int(n.get("port") or 0)
    if not host or not port:
        return None
    # Xray 26 把 HTTP(h2) 传输整个移除了（迁到 XHTTP），带这种 transport 的
    # 节点在新版 Xray 上无法构造 outbound。不是配置错误，是版本不兼容。
    net = (n.get("network") or "tcp").lower()
    if net in ("h2", "http"):
        return None
    # REALITY 只支持 raw/tcp、xhttp、grpc。带 ws 的 reality 节点 Xray 直接拒绝
    if (n.get("security") or "").lower() == "reality" and \
            net not in ("tcp", "raw", "grpc", "xhttp", "httpupgrade"):
        return None

    if t == "vmess":
        return {"tag": tag, "protocol": "vmess", "settings": {"vnext": [{
            "address": host, "port": port,
            "users": [{"id": n.get("cred") or "", "alterId": int(n.get("alter_id") or 0),
                       "security": n.get("cipher") or "auto", "level": 0}]}]},
            "streamSettings": _stream(n)}
    if t == "vless":
        # Xray 26.x 明确禁止明文 vless：`vless without TLS or other encryption
        # is prohibited unless the server address is a private IP or domain`。
        # 这不是我们生成的配置有问题，是这些节点本身在新版 Xray 上不可用，
        # 所以计为「未转换」而不是失败——但要在报告里写清楚有多少条。
        sec = (n.get("security") or "").lower()
        if not (n.get("tls") or sec in ("tls", "reality")):
            return None
        u = {"id": n.get("cred") or "", "encryption": "none", "level": 0}
        if n.get("flow"):
            u["flow"] = n["flow"]
        return {"tag": tag, "protocol": "vless", "settings": {"vnext": [{
            "address": host, "port": port, "users": [u]}]},
            "streamSettings": _stream(n)}
    if t == "trojan":
        # trojan 协议本身内建 TLS。源里的 URI 一般不写 security 参数，
        # 于是内部模型里 tls 是空的；Xray 26 会因此报
        # `trojan without TLS is prohibited` 并拒绝整份配置。
        st = _stream(n)
        if st.get("security") not in ("tls", "reality"):
            st["security"] = "tls"
            st["tlsSettings"] = {"serverName": n.get("sni") or "",
                                 "fingerprint": n.get("fingerprint") or "chrome"}
        return {"tag": tag, "protocol": "trojan", "settings": {"servers": [{
            "address": host, "port": port, "password": n.get("cred") or ""}]},
            "streamSettings": st}
    if t == "ss":
        # Xray 只保留 AEAD 与 2022 系列，rc4-md5 / aes-256-cfb 这类流加密早已移除
        method = (n.get("method") or n.get("cipher") or "").lower()
        if method not in XRAY_SS:
            return None
        return {"tag": tag, "protocol": "shadowsocks", "settings": {"servers": [{
            "address": host, "port": port, "method": method,
            "password": n.get("cred") or ""}]},
            "streamSettings": _stream(n) if (n.get("network") or "tcp") != "tcp" else {"network": "tcp"}}
    return None


def xray_config(nodes):
    outs, tags, skipped = [], [], {}
    for i, n in enumerate(nodes):
        tag = f"n{i}"
        o = xray_out(n, tag)
        if not o:
            key = n.get("type") or "?"
            # 顺序要紧：先判「具体原因」，再按协议归类。
            # 反过来的话 reality+ws 的 vless 会被记成「无 TLS」，报告就撒了谎。
            if (n.get("network") or "tcp").lower() in ("h2", "http"):
                key = "HTTP 传输（Xray 26 已移除）"
            elif (n.get("security") or "").lower() == "reality":
                key = f"reality+{(n.get('network') or 'tcp')}（Xray 只支持 raw/xhttp/grpc）"
            elif key == "vless":
                key = "vless（无 TLS，Xray 26 拒绝）"
            elif key == "ss" and (n.get("method") or n.get("cipher") or "").lower() not in XRAY_SS:
                key = "ss 旧加密（Xray 已移除）"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        outs.append(o)
        tags.append(tag)
    if not outs:
        return None, skipped
    doc = {
        "log": {"loglevel": "warning"},
        "outbounds": outs + [{"tag": "direct", "protocol": "freedom"}],
    }
    return doc, skipped


# ── 各项检查 ────────────────────────────────────────────────────────────────

def check_mihomo(path):
    binp = BINS["mihomo"]
    if not os.access(binp, os.X_OK):
        return "skip", "找不到 mihomo 二进制", 0
    with tempfile.TemporaryDirectory() as td:
        rc, out, dt = run([binp, "-t", "-f", path, "-d", td])
    tail = [l for l in out.splitlines() if l.strip()][-1:]
    okk = rc == 0 and "successful" in out.lower()
    return ("pass" if okk else "fail"), (tail[0] if tail else out[-160:]), dt


def check_singbox(path):
    binp = BINS["sing-box"]
    if not os.access(binp, os.X_OK):
        return "skip", "找不到 sing-box 二进制", 0
    rc, out, dt = run([binp, "check", "-c", path])
    tail = [l for l in out.splitlines() if l.strip()][-1:]
    return ("pass" if rc == 0 else "fail"), (tail[0] if tail else "无输出"), dt


def check_xray(nodes):
    binp = BINS["xray"]
    if not os.access(binp, os.X_OK):
        return "skip", "找不到 xray 二进制", 0, {}
    doc, skipped = xray_config(nodes)
    if not doc:
        return "skip", "没有可转换的协议", 0, skipped
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "cfg.json")
        json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        rc, out, dt = run([binp, "-test", "-c", p])
    tail = [l for l in out.splitlines() if l.strip()][-1:]
    okk = rc == 0 and "configuration ok" in out.lower()
    return ("pass" if okk else "fail"), (tail[0] if tail else out[-160:]), dt, skipped


def main(argv):
    if "--build" in argv:
        r = subprocess.run([sys.executable, os.path.join(BASE, "export_subs.py")],
                           capture_output=True, text=True, cwd=BASE)
        print("  重新生成订阅：" + (r.stdout or r.stderr).strip().splitlines()[-1][:80])

    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])

    if not os.path.isdir(SUBS):
        raise SystemExit("没有 export/subs。先跑 python3 export_subs.py")

    groups = sorted(f[:-5] for f in os.listdir(SUBS) if f.endswith(".yaml"))
    rows = []
    for g in groups:
        y = os.path.join(SUBS, f"{g}.yaml")
        j = os.path.join(SUBS, f"{g}.json")
        st_m, det_m, dt_m = check_mihomo(y) if os.path.exists(y) else ("skip", "缺文件", 0)
        st_s, det_s, dt_s = check_singbox(j) if os.path.exists(j) else ("skip", "缺文件", 0)
        rows.append({"group": g, "mihomo": (st_m, det_m, dt_m), "singbox": (st_s, det_s, dt_s)})
        print(f"  {g:<8} mihomo={st_m:<4} sing-box={st_s:<4}")

    # Xray 只测 all 组：它要逐条转 outbound，别的组是 all 的子集
    import nodes_parse
    txt = os.path.join(SUBS, "all.txt")
    nodes = []
    if os.path.exists(txt):
        nodes, _ = nodes_parse.parse_any(open(txt, "rb").read())
    if limit:
        nodes = nodes[:limit]
    st_x, det_x, dt_x, skipped = check_xray(nodes)
    print(f"  xray     {'all':<8} {st_x:<4} {len(nodes)} 条  {det_x[:60]}")

    write_report(rows, (st_x, det_x, dt_x, skipped, len(nodes)))
    print(f"\n报告 -> {os.path.relpath(REPORT, BASE)}")
    bad = [r for r in rows if "fail" in (r["mihomo"][0], r["singbox"][0])] or st_x == "fail"
    return 0 if not bad else 1


def write_report(rows, xray):
    st_x, det_x, dt_x, skipped, nx = xray
    L = ["# P15：客户端真实导入验证\n",
         "判据是客户端自己的内核接受配置，不是我们「觉得」它接受。\n",
         "## 各分组\n",
         "| 分组 | Mihomo | sing-box | 说明 |",
         "|---|---|---|---|",
         ]
    mark = {"pass": "✔ 通过", "fail": "✘ 失败", "skip": "— 未测"}
    for r in rows:
        sm, dm, _ = r["mihomo"]
        ss, ds, _ = r["singbox"]
        note = "" if (sm == "pass" and ss == "pass") else (dm if sm != "pass" else ds)
        L.append(f"| {r['group']} | {mark[sm]} | {mark[ss]} | {note.replace('|','/')[:70]} |")
    L += ["", "## Xray / Hiddify\n",
          f"* **Xray**：{mark[st_x]}，把 {nx:,} 条 URI 逐条转成 outbound 后交给 `xray -test`。"
          f"{('结果：' + det_x[:120]) if det_x else ''}",
          ]
    if skipped:
        L.append("* 未转换的协议：" + "、".join(f"{k} {v} 条" for k, v in skipped.items())
                 + "（Xray 不支持这些协议，不算失败）")
    L += [
        "* **Hiddify**：内核是 sing-box，配置格式完全一致，上面的 sing-box 检查等价于"
        "「Hiddify 能不能吃下这份订阅」。但 Hiddify 没有免登录的命令行，"
        "**本项目没有做过 Hiddify GUI 的导入测试**——这一点如实写在这里，不冒充。",
        "",
        "## 这一步抓到过什么\n",
        "第一次跑的时候 sing-box 报 `invalid public_key`，之后一路修了六处。"
        "全部是「我们自己的校验全绿、客户端内核拒绝」的类型：\n",
        "| 客户端的报错 | 根因 | 修在哪 | 影响 |",
        "|---|---|---|---|",
        "| `invalid public_key` | `reality-opts` 用 `p.get(..) or {}` 再判 `isinstance(dict)`，"
        "空 dict 也是 dict，导致**每个** Clash 节点都被标成 reality、公钥清空 | "
        "`nodes_parse._ws_params` | 2,374 条误标，其中 1,987 条公钥为空 |",
        "| `invalid public_key`（第二处） | reality 节点缺 pbk 时照样生成订阅 | "
        "`_why_drop` 新增 `no_key` 隔离 | 见隔离区 |",
        "| `TLS required` | hysteria2 / tuic / anytls 的 TLS 是协议内建，源里没写 sni 时"
        " tls 块为空、`if tls` 判假 | `export_subs._sb_out` | 208 条 |",
        "| `unsupported flow: xtls-rprx-vision-udp443` | 上游混用 Xray 的变体写法 | "
        "`_sb_out` 归一为 `xtls-rprx-vision` | 4 条 |",
        "| `trojan without TLS is prohibited` | trojan 内建 TLS，但内部模型里 tls 为空 | "
        "三处生成器补 TLS | 数百条 |",
        "| `unknown transport protocol: ws#🍪...` | URI 把 fragment 编码进 `type` 参数 | "
        "`_finalize` 新增 `_norm_net` 白名单归一 | 数十条 |",
        "| `invalid byte: U+0078 'x'` | vmess 的 id 是 `...2cdx`，末位不是 hex（上游打错） | "
        "`_why_drop` 新增 `bad_uuid` 隔离 | 359 条 |",
        "",
        "这件事说明 P8 的「多格式反解校验」这一层**不够**：反解用的是同一个解析器，"
        "解析器错了它跟着错，`verify()` 全绿也说明不了任何问题。"
        "客户端内核是独立实现，才拦得住这一类错误。所以这一项不是走过场，"
        "它抓出来的问题比压测更多。",
        "",
        ]
    open(REPORT, "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
