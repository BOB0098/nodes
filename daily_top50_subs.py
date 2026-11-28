#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Top50 指纹生成三种订阅文件（GitHub 版 v1）。

只在 collect 的产物还在的时候才能跑：输入是 export/nodes_full.json
（含完整 URI，只在 CI 产物里，不进仓库）和 data/daily_top50.json
（脱敏，只有指纹和展示信息）。

为什么不直接改 export_subs.py 加一个 daily_top50 分组？
- export_subs.py 的输出要进 data/subscriptions.json，
  而 subscriptions.json 的分组计数要和 build_manifest.py 的清单对账，
  加一组就得同步改 manifest 和 check_consistency，动静太大，
  容易把刚稳住的 collect 流水线再拖垮。
- 这份脚本只写 export/subs/daily_top50.yaml / .json / .txt / .b64，
  不碰 subscriptions.json、不碰 manifest，失败也不影响主订阅。

生成完每种格式都用 export_subs.verify() 反解校验，条数对不上就报错退出，
和主订阅同一套校验口径。

用法：
    python3 daily_top50_subs.py --selftest   # 合成数据自测，不读写 export/
    python3 daily_top50_subs.py              # 真跑，需要上面两个输入文件
"""

import base64
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
FULL = os.path.join(BASE, "export", "nodes_full.json")
TOP50 = os.path.join(BASE, "data", "daily_top50.json")
SUBS = os.path.join(BASE, "export", "subs")

sys.path.insert(0, BASE)
import netsafe  # noqa: E402


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def select_top50(nodes, top50_doc):
    wanted = []
    for r in (top50_doc.get("lists") or {}).get("top50") or []:
        fp = (r.get("fp") or "")[:16]
        if fp:
            wanted.append(fp)
    by_fp = {}
    for n in nodes:
        fp = n.get("fp") or ""
        by_fp[fp] = n
        by_fp[fp[:16]] = n
    picked, missing = [], []
    for fp16 in wanted:
        n = by_fp.get(fp16)
        if n is None:
            missing.append(fp16)
        else:
            picked.append(n)
    return picked, missing


def main():
    import export_subs
    nodes = load(FULL, [])
    if not nodes:
        print("没有 export/nodes_full.json，先跑 collector.py 或下载 collect 产物")
        return 1
    top50_doc = load(TOP50, {})
    picked, missing = select_top50(nodes, top50_doc)
    if not picked:
        print("Top50 里没有能在节点库里找到的节点，不生成订阅")
        return 1
    if missing:
        print(f"  注意：Top50 里 {len(missing)} 条已不在节点库（源下架了），"
              f"只生成剩下的 {len(picked)} 条")
    os.makedirs(SUBS, exist_ok=True)
    ok_all = True
    for fmt, builder, ext in (("clash", export_subs.build_clash, "yaml"),
                              ("singbox", export_subs.build_singbox, "json"),
                              ("uri", export_subs.build_uris, "txt")):
        text = builder(picked)
        if fmt == "uri":
            text = "\n".join(x for x in (text or "").splitlines() if x.strip())
        ok, got, why = export_subs.verify(fmt, text or "", len(picked))
        print(f"  {fmt}: {why}（期望 {len(picked)}）")
        if not ok:
            ok_all = False
            continue
        netsafe.write_atomic(os.path.join(SUBS, f"daily_top50.{ext}"), text)
    uri_text = export_subs.build_uris(picked)
    b64 = base64.b64encode(uri_text.encode()).decode()
    ok, got, why = export_subs.verify("b64", b64, len(picked))
    if ok:
        netsafe.write_atomic(os.path.join(SUBS, "daily_top50.b64"), b64)
        print(f"  b64: {why}")
    else:
        print(f"  b64 校验失败：{why}")
        ok_all = False
    print(f"Top50 订阅 -> export/subs/daily_top50.*（{len(picked)} 条）")
    return 0 if ok_all else 1


def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("  ✔ " if c else "  ✘ ") + m)
        ok = ok and c

    nodes = [
        {"fp": "a" * 32, "name": "JP-1", "type": "vmess", "server": "1.2.3.4",
         "port": 443, "cred": "u-1", "network": "ws", "path": "/x",
         "host": "a.com", "tls": "tls", "sni": "a.com",
         "uri": "vmess://abc"},
        {"fp": "b" * 32, "name": "HK-1", "type": "vless", "server": "5.6.7.8",
         "port": 443, "cred": "u-2", "uri": "vless://u-2@5.6.7.8:443"},
    ]
    doc = {"lists": {"top50": [{"fp": "a" * 32}, {"fp": "b" * 32},
                                {"fp": "c" * 32}]}}
    picked, missing = select_top50(nodes, doc)
    chk(len(picked) == 2, "按指纹顺序取 Top50，不重排")
    chk(missing == ["c" * 16], "不在库里的指纹单独记为缺失，不静默丢")
    chk(picked[0]["fp"] == "a" * 32, "顺序和 Top50 一致")
    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
