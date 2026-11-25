#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P22：公开产物脱敏审计——检查入库/发布文件里有没有泄漏凭据。

要防的事故：某个脚本改了输出字段，把带 uuid/password 的完整 URI 或明文
token 写进了会进公开仓库的 data/*.json 或 index.html。人工很难逐文件看，
所以做成门禁：发布前扫一遍，命中就拦下。

审计范围（发布给用户的数据产物，即"公开数据"）：
  - data/*.json   入库数据
  - index.html    发布页面（含内嵌 DATA）

为什么不扫源码/文档：
  - netsafe.py / nodes_parse.py / governance.py 的 selftest 里故意写了假
    URI、假 token、假凭据来验证脱敏与解析逻辑，它们是测试样本不是真泄
    漏；P13 等文档里的 u:p@ 是讲 SSRF 时的语法示例。把这些纳入扫描会
    产生大量误报，反而让真泄漏淹没在噪声里。真凭据若被写进源码，Code
    inspect / 人工 review 才是合适手段，不属于"公开数据"审计范畴。
  - export/ 不扫：它含完整凭据但已 gitignore，只作为 CI artifact 保留
    1 天，不进公开仓库。

用法：
    python3 audit_privacy.py            # 命中退出码 1
    python3 audit_privacy.py --selftest # 自测（喂假数据验证每条规则）
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

# 完整节点 URI：scheme 后跟 base64/hex 片段，长度足够才判，避免误伤文档示例
URI_RE = re.compile(r"\b(vmess|vless|ssr|ss|trojan|hysteria2?|tuic|anytls)://[A-Za-z0-9+/=_\-.]{20,}")
# 凭据字段名（作为 JSON key 或赋值出现）
CRED_KEY_RE = re.compile(r'["\']?(cred|password|passwd|uuid|private[_-]?key|auth)["\']?\s*[:=]\s*["\'][^"\']{6,}')
# 真 token：36+ 位。测试假值用固定前缀豁免
TOKEN_RE = re.compile(r"\b(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{30,})")
TEST_TOKEN_PREFIX = ("ghp_1234", "ghp_abcdef", "ghp_B1qkHAL8TEST")
# URL 内嵌凭据
URL_CRED_RE = re.compile(r"https?://[^/\s:@]+:[^/\s@]+@")


def public_files():
    """只返回发布给用户的数据产物。"""
    out = []
    for f in sorted(os.listdir(os.path.join(BASE, "data"))):
        if f.endswith(".json"):
            out.append(os.path.join("data", f))
    out.append("index.html")
    return out


def audit_text(text, rel):
    """返回命中项列表 [(类型, 片段)]。"""
    hits = []
    for m in URI_RE.finditer(text):
        hits.append(("节点URI", m.group(0)[:28]))
    for m in CRED_KEY_RE.finditer(text):
        hits.append(("凭据字段", m.group(0)[:40]))
    for m in TOKEN_RE.finditer(text):
        v = m.group(0)
        if not v.startswith(TEST_TOKEN_PREFIX):
            hits.append(("令牌", v[:16] + "..."))
    for m in URL_CRED_RE.finditer(text):
        hits.append(("URL内嵌凭据", m.group(0)[:40]))
    return hits


def selftest():
    ok = True
    def chk(cond, msg):
        nonlocal ok
        print(("  ✔ " if cond else "  ✘ ") + msg)
        ok = ok and bool(cond)

    # 应命中
    chk(len(audit_text('"cred": "secret-value-123"', "t.json")) >= 1, "命中明文 cred")
    chk(len(audit_text('vmess://eyJ2IjoxMjM0NTY3ODkwYWJjZGVmZ2hpamts', "t.json")) >= 1, "命中节点 URI")
    chk(len(audit_text("token=ghp_" + "A" * 36, "t.py")) >= 1, "命中真 token")
    chk(len(audit_text("http://user:pw123456@host/x", "t.json")) >= 1, "命中 URL 内嵌凭据")
    # 应豁免
    chk(len(audit_text("token=ghp_123456789012345678901234567890123456", "governance.py")) == 0, "豁免测试假 token")
    chk(len(audit_text('"reason": "凭据不是合法 UUID（vmess/vless 无法握手）"', "q.json")) == 0, "豁免中文原因说明里的词")
    chk(len(audit_text('"name": "示例 ss 节点"', "t.json")) == 0, "豁免无 scheme 的说明文字")
    print("  selftest:", "通过" if ok else "失败")
    return ok


def main():
    if "--selftest" in sys.argv:
        return 0 if selftest() else 1
    all_hits = []
    scanned = 0
    for rel in public_files():
        path = os.path.join(BASE, rel)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        scanned += 1
        for typ, frag in audit_text(text, rel):
            all_hits.append((rel, typ, frag))
    print(f"脱敏审计：扫描 {scanned} 个公开产物")
    if all_hits:
        for rel, typ, frag in all_hits[:20]:
            print(f"  ✘ {rel}: {typ} → {frag}")
        print(f"结论：命中 {len(all_hits)} 处疑似凭据 → 拦下发布")
        return 1
    print("结论：未发现凭据/令牌泄漏 → 允许发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
