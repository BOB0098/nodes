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

P27 增强：
  - Base64 解码：长 base64 片段解码后再扫，防止凭据被编码后塞进产物
  - URL query：扫 ?token= / ?sub= 等参数值
  - git 历史：--git 显式开启，扫全历史真令牌（离线，需 .git）
  - Actions 日志：--log <路径> 显式指定；日志不在仓库里，不默认扫也不冒充已扫

用法：
    python3 audit_privacy.py            # 扫公开产物，命中退出码 1
    python3 audit_privacy.py --git      # 加扫 git 全历史
    python3 audit_privacy.py --log X    # 加扫指定日志（CI 落盘后传入）
    python3 audit_privacy.py --selftest # 自测
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
# URL query 里放 token / 凭据的参数（P27：订阅地址常把 token 塞在 query）
QUERY_SECRET_RE = re.compile(r"[?&](token|access_token|key|secret|passwd|password|auth|sub)=([^&#\s]{8,})", re.I)
# 长 base64 片段：解码后可能藏着节点 URI（P27）
B64_BLOB_RE = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")


def public_files():
    """只返回发布给用户的数据产物。"""
    out = []
    for f in sorted(os.listdir(os.path.join(BASE, "data"))):
        if f.endswith(".json"):
            out.append(os.path.join("data", f))
    out.append("index.html")
    return out


def _decode_b64(s):
    """尝试 base64 解码长片段；只看可打印文本，失败返回空。"""
    import base64
    try:
        pad = s + "=" * (-len(s) % 4)
        raw = base64.b64decode(pad, validate=True)
        text = raw.decode("utf-8")
        # 只接受可打印占比高的结果，避免二进制噪声
        if sum(1 for c in text if c.isprintable()) / max(1, len(text)) < 0.9:
            return ""
        return text
    except Exception:
        return ""


def audit_text(text, rel):
    """返回命中项列表 [(类型, 片段)]。P27 增加 Base64 解码与 query 值扫描。"""
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
    # P27：URL query 里的 token 类参数值
    for m in QUERY_SECRET_RE.finditer(text):
        val = m.group(2)
        if not val.startswith(TEST_TOKEN_PREFIX):
            hits.append(("URL参数凭据", f"{m.group(1)}={val[:20]}"))
    # P27：长 base64 片段解码后再扫一次（避免把凭据编码后塞进产物）
    for m in B64_BLOB_RE.finditer(text):
        decoded = _decode_b64(m.group(0))
        if not decoded:
            continue
        if URI_RE.search(decoded):
            hits.append(("Base64内节点URI", decoded[:28]))
        if CRED_KEY_RE.search(decoded):
            hits.append(("Base64内凭据", decoded[:40]))
        for tm in TOKEN_RE.finditer(decoded):
            if not tm.group(0).startswith(TEST_TOKEN_PREFIX):
                hits.append(("Base64内令牌", tm.group(0)[:16] + "..."))
    return hits


def audit_git_history():
    """P27：审计 git 全历史里的真令牌。需 .git 存在；返回 (命中列表, 是否可检查)。"""
    import subprocess
    if not os.path.isdir(os.path.join(BASE, ".git")):
        return [], False
    try:
        out = subprocess.run(
            ["git", "-C", BASE, "log", "--all", "-p"],
            capture_output=True, text=True, timeout=120,
        ).stdout
    except Exception:
        return [], False
    hits = []
    for m in TOKEN_RE.finditer(out):
        if not m.group(0).startswith(TEST_TOKEN_PREFIX):
            hits.append(("git历史令牌", m.group(0)[:16] + "..."))
    return hits, True


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
    # P27 新增规则
    chk(len(audit_text("https://x.com/sub?token=abcdefgh12345678", "t.json")) >= 1, "命中 URL query 里的 token")
    import base64
    encoded = base64.b64encode(b"vmess://eyJ2IjoxMjM0NTY3ODkwYWJjZGVmZ2hpamts").decode()
    chk(len(audit_text(encoded, "t.json")) >= 1, "命中 Base64 解码后的节点 URI")
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
    print(f"脱敏审计：扫描 {scanned} 个公开产物（含 Base64 解码 / URL 参数）")
    if "--git" in sys.argv:
        ghits, can_check = audit_git_history()
        if can_check:
            print("  git 全历史令牌检查：" + ("干净" if not ghits else f"命中 {len(ghits)} 处"))
            all_hits += [("git历史", typ, frag) for typ, frag in ghits]
        else:
            print("  git 全历史令牌检查：无 .git 或超时，跳过")
    # P27：可选扫描指定日志文件（CI 可把 Actions 日志落盘后传入），
    # 日志不在仓库里，无法默认扫描——不冒充「已扫日志」。
    if "--log" in sys.argv:
        i = sys.argv.index("--log")
        if i + 1 < len(sys.argv):
            lp = sys.argv[i + 1]
            try:
                text = open(lp, encoding="utf-8", errors="ignore").read()
                for typ, frag in audit_text(text, lp):
                    all_hits.append(("Actions日志", typ, frag))
                print(f"  日志扫描：{lp}")
            except Exception as e:
                print(f"  日志扫描失败（{type(e).__name__}），不冒充已扫")
    if all_hits:
        for rel, typ, frag in all_hits[:20]:
            print(f"  ✘ {rel}: {typ} → {frag}")
        print(f"结论：命中 {len(all_hits)} 处疑似凭据 → 拦下发布")
        return 1
    print("结论：未发现凭据/令牌泄漏 → 允许发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
