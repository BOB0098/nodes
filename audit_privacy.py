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
import math
import os
import re
import stat
import sys
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))

# 完整节点 URI：scheme 后跟 base64/hex 片段，长度足够才判，避免误伤文档示例
URI_RE = re.compile(r"\b(vmess|vless|ssr|ss|trojan|hysteria2?|tuic|anytls)://[A-Za-z0-9+/=_\-.]{20,}")
# 凭据字段名（作为 JSON key 或赋值出现）
CRED_KEY_RE = re.compile(
    r'["\']?(?:cred|password|passwd|uuid|private[_-]?key|auth|api[_-]?key|access[_-]?key|client[_-]?secret)'
    r'["\']?\s*[:=]\s*["\']?(?P<value>[^"\'\n,;}\]]{6,})',
    re.I,
)
PLACEHOLDER_VALUE_RE = re.compile(
    r'^(?:none|null|empty|unset|not[\s_-]+(?:set|configured|provided|available)|'
    r'replace[\s_-]+me|your[\s_-]+(?:api[\s_-]?key|access[\s_-]?key|client[\s_-]?secret)|'
    r'example|sample|dummy|placeholder|redacted|change[\s_-]?me|<[^>]+>)$',
    re.I,
)
# 真 token：36+ 位。测试假值用固定前缀豁免
TOKEN_RE = re.compile(r"\b(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{30,})")
TEST_TOKEN_PREFIX = ("ghp_1234", "ghp_abcdef", "ghp_B1qkHAL8TEST")
# 明确 Bearer 上下文中的较短高熵令牌，以及匿名 URL-safe 高熵令牌
BEARER_RE = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/\-]{20,}={0,2})", re.I)
HIGH_ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}={0,2}(?![A-Za-z0-9_-])")
# URL 内嵌凭据
URL_CRED_RE = re.compile(r"https?://[^/\s:@]+:[^/\s@]+@")
# URL query 里放 token / 凭据的参数（P27：订阅地址常把 token 塞在 query）
QUERY_SECRET_RE = re.compile(
    r"[?&](token|access_token|api[_-]?key|access[_-]?key|client[_-]?secret|key|secret|passwd|password|auth|sub)=([^&#\s]{8,})",
    re.I,
)
# 长 base64 片段：解码后可能藏着节点 URI（P27）
B64_BLOB_RE = re.compile(r"(?<![A-Za-z0-9_+/\-])[A-Za-z0-9_+/\-]{40,}={0,2}(?![A-Za-z0-9_+/\-])")
REDACTED = "[已隐藏]"


def _entropy(value):
    """返回字符串的 Shannon 熵，用于过滤低复杂度普通标识符。"""
    value = value.rstrip("=")
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _is_placeholder(value):
    return bool(PLACEHOLDER_VALUE_RE.fullmatch(value.strip()))


def _is_obvious_sequence(value):
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    examples = (
        "abcdefghijklmnopqrstuvwxyz" * 3,
        "0123456789" * 8,
        "abcdefghijklmnopqrstuvwxyz0123456789",
        "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz0123456789",
    )
    return any(normalized in example for example in examples)


def _is_high_entropy(value, minimum_length=32, minimum_entropy=4.6):
    value = value.rstrip("=")
    return (
        len(value) >= minimum_length
        and not _is_obvious_sequence(value)
        and _entropy(value) >= minimum_entropy
    )


def public_files():
    """只返回发布给用户的数据产物。"""
    out = []
    for f in sorted(os.listdir(os.path.join(BASE, "data"))):
        if f.endswith(".json"):
            out.append(os.path.join("data", f))
    out.append("index.html")
    return out


def _decode_b64(s):
    """尝试标准或 URL-safe base64 解码；只看可打印文本，失败返回空。"""
    import base64
    try:
        pad = s + "=" * (-len(s) % 4)
        raw = base64.b64decode(pad, altchars=b"-_", validate=True)
        text = raw.decode("utf-8")
        # 只接受可打印占比高的结果，避免二进制噪声
        if sum(1 for c in text if c.isprintable()) / max(1, len(text)) < 0.9:
            return ""
        return text
    except Exception:
        return ""


def audit_text(text, rel):
    """返回命中项列表 [(类型, 已隐藏)]，绝不把秘密片段带出扫描器。"""
    hits = []
    for m in URI_RE.finditer(text):
        hits.append(("节点URI", REDACTED))
    for m in CRED_KEY_RE.finditer(text):
        if not _is_placeholder(m.group("value")):
            hits.append(("凭据字段", REDACTED))
    for m in TOKEN_RE.finditer(text):
        v = m.group(0)
        if not v.startswith(TEST_TOKEN_PREFIX):
            hits.append(("令牌", REDACTED))
    for m in BEARER_RE.finditer(text):
        if _is_high_entropy(m.group(1), minimum_length=20, minimum_entropy=4.0):
            hits.append(("高熵Bearer令牌", REDACTED))
    for m in HIGH_ENTROPY_RE.finditer(text):
        if _is_high_entropy(m.group(0)):
            hits.append(("高熵URL-safe令牌", REDACTED))
    for m in URL_CRED_RE.finditer(text):
        hits.append(("URL内嵌凭据", REDACTED))
    # P27：URL query 里的 token 类参数值
    for m in QUERY_SECRET_RE.finditer(text):
        val = m.group(2)
        if not val.startswith(TEST_TOKEN_PREFIX) and not _is_placeholder(val):
            hits.append(("URL参数凭据", REDACTED))
    # 长标准或 URL-safe base64 片段解码后再扫一次。
    for m in B64_BLOB_RE.finditer(text):
        decoded = _decode_b64(m.group(0))
        if not decoded:
            continue
        if URI_RE.search(decoded):
            hits.append(("Base64内节点URI", REDACTED))
        if any(not _is_placeholder(cm.group("value")) for cm in CRED_KEY_RE.finditer(decoded)):
            hits.append(("Base64内凭据", REDACTED))
        for tm in TOKEN_RE.finditer(decoded):
            if not tm.group(0).startswith(TEST_TOKEN_PREFIX):
                hits.append(("Base64内令牌", REDACTED))
        if any(_is_high_entropy(hm.group(0)) for hm in HIGH_ENTROPY_RE.finditer(decoded)):
            hits.append(("Base64内高熵令牌", REDACTED))
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
            hits.append(("git历史令牌", REDACTED))
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
    args = sys.argv[1:]
    log_paths = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--log":
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                print("日志扫描失败：--log 缺少路径，拒绝通过")
                return 2
            log_paths.append(args[i + 1])
            i += 2
            continue
        if arg.startswith("--log="):
            path = arg.partition("=")[2]
            if not path:
                print("日志扫描失败：--log 缺少路径，拒绝通过")
                return 2
            log_paths.append(path)
        i += 1
    if "--selftest" in args:
        return 0 if selftest() else 1
    all_hits = []
    log_scan_failed = False
    public_scan_failed = 0
    scanned = 0
    try:
        files = public_files()
    except Exception:
        print("公开产物清单检查失败，拒绝通过")
        return 2
    for rel in files:
        path = os.path.join(BASE, rel)
        try:
            if not stat.S_ISREG(os.stat(path).st_mode):
                raise OSError
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except Exception:
            public_scan_failed += 1
            continue
        scanned += 1
        for typ, frag in audit_text(text, rel):
            all_hits.append((rel, typ, frag))
    print(f"脱敏审计：扫描 {scanned} 个公开产物（含 Base64 解码 / URL 参数）")
    if "--git" in args:
        ghits, can_check = audit_git_history()
        if can_check:
            print("  git 全历史令牌检查：" + ("干净" if not ghits else f"命中 {len(ghits)} 处"))
            all_hits += [("git历史", typ, frag) for typ, frag in ghits]
        else:
            print("  git 全历史令牌检查：无 .git 或超时，跳过")
    # 指定日志必须全部成功扫描；任何读取失败都不能被误报成通过。
    for lp in log_paths:
        try:
            with open(lp, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            for typ, frag in audit_text(text, lp):
                all_hits.append(("Actions日志", typ, frag))
            print("  日志扫描：完成")
        except Exception as e:
            print(f"  日志扫描失败（{type(e).__name__}），拒绝通过")
            log_scan_failed = True
    if all_hits:
        for rel, typ, frag in all_hits[:20]:
            print(f"  ✘ {rel}: {typ} → 内容已隐藏")
        print(f"结论：命中 {len(all_hits)} 处疑似凭据 → 拦下发布")
        return 1
    if public_scan_failed:
        print(f"  公开产物检查失败：{public_scan_failed} 个目标无法检查（错误详情已隐藏）")
        print("结论：公开产物扫描不完整 → 拒绝通过")
        return 2
    if log_scan_failed:
        print("结论：日志扫描不完整 → 拒绝通过")
        return 2
    print("结论：未发现凭据/令牌泄漏 → 允许发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
