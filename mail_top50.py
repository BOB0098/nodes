#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给收件人发每日 Top50 邮件（GitHub 版 v1）。

收件人：默认 742801510@qq.com，可用 MAIL_TO 覆盖。
发件：全部走环境变量，不写进代码、不进仓库：
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM
    举例（QQ 邮箱）：SMTP_HOST=smtp.qq.com SMTP_PORT=465
    SMTP_USER=你的 QQ 邮箱 SMTP_PASS=QQ 授权码（不是 QQ 密码）
    举例（Gmail）：SMTP_HOST=smtp.gmail.com SMTP_PORT=465
    SMTP_USER=你的 Gmail SMTP_PASS=Google 应用密码
  GitHub Actions 里这 5 个都放仓库 Secrets，工作流里以 env 传入。
  本地跑之前 export 同名变量即可。

正文：HTML 表格，只含脱敏信息（排名 / 名字 / 协议 / 地区 / 评分 /
成功率 / 延迟 / 速度 / 来源），不含 URI。
附件（如果有）：export/subs/daily_top50.yaml / .json / .txt，
由 daily_top50_subs.py 在 collect 的产物里生成；
没有附件时只发榜单表格，并在正文里写明“本次无订阅附件”，
不拿旧附件冒充今天的。

行为：
- Top50 为空 → 不发，退出码 0（不是失败，别让工作流报红）。
- SMTP 变量缺失 → 不发，打印缺了哪一项，退出码 0。
  这样 Secrets 没配好时，Top50 的 JSON 照常生成和提交，只是邮件跳过。
- 发送失败（认证 / 连接）→ 打印原因，退出码 1，让工作流报出来，
  别静默吞掉。

用法：
    python3 mail_top50.py --dry-run    # 只打印标题、正文预览和附件清单，不发
    python3 mail_top50.py              # 真发
    python3 mail_top50.py --selftest   # 自测 HTML 转义和附件逻辑
"""

import json
import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formatdate
from html import escape

BASE = os.path.dirname(os.path.abspath(__file__))
TOP50 = os.path.join(BASE, "data", "daily_top50.json")
SUBS_DIR = os.path.join(BASE, "export", "subs")

DEFAULT_TO = "742801510@qq.com"

ATTACHMENTS = [
    ("daily_top50.yaml", "Clash / Mihomo 订阅", "application/yaml"),
    ("daily_top50.json", "sing-box 订阅", "application/json"),
    ("daily_top50.txt", "URI 列表", "text/plain"),
]


def load_top50():
    try:
        with open(TOP50, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"lists": {"top50": []}}


def fmt_bps(bps):
    if bps is None:
        return "—"
    try:
        bps = float(bps)
    except (TypeError, ValueError):
        return "—"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} MB/s"
    return f"{bps / 1000:.0f} KB/s"


def build_html(doc):
    rows = (doc.get("lists") or {}).get("top50") or []
    updated = doc.get("updated") or "—"
    probe = doc.get("probe_label") or ""
    probe_note = doc.get("probe_note") or ""
    lines = [
        f"<p>每日优选 Top50 · 更新于 {escape(str(updated))} · {escape(str(probe))}</p>",
        f"<p style='color:#666;font-size:12px'>{escape(str(probe_note))}</p>",
    ]
    if not rows:
        lines.append("<p>今天综合榜为空，没有可发的 Top50。</p>")
        return "".join(lines), rows
    lines.append(
        "<table border='1' cellspacing='0' cellpadding='6' "
        "style='border-collapse:collapse;font-size:13px'>"
        "<tr><th>#</th><th>节点</th><th>协议</th><th>地区</th>"
        "<th>评分</th><th>成功率</th><th>延迟</th><th>速度</th><th>来源</th></tr>")
    for r in rows:
        rate = r.get("rate")
        rate_s = f"{rate:.0%}" if isinstance(rate, (int, float)) else "—"
        ms = r.get("ms")
        ms_s = f"{ms}ms" if ms is not None else "—"
        lines.append(
            "<tr>"
            f"<td>{r.get('rank')}</td>"
            f"<td>{escape(str(r.get('name') or ''))}</td>"
            f"<td>{escape(str(r.get('type') or ''))}</td>"
            f"<td>{escape(str(r.get('rg') or ''))}</td>"
            f"<td>{r.get('score')}</td>"
            f"<td>{rate_s} ({r.get('hits')}/{r.get('tries')})</td>"
            f"<td>{ms_s}</td>"
            f"<td>{fmt_bps(r.get('bps'))}</td>"
            f"<td>{escape(str(r.get('src') or ''))}</td>"
            "</tr>")
    lines.append("</table>")
    has_attach = any(os.path.exists(os.path.join(SUBS_DIR, f))
                     for f, _, _ in ATTACHMENTS)
    if has_attach:
        lines.append("<p>附件是今天 Top50 的三种订阅：Clash、sing-box、URI 列表，"
                     "导入客户端即可用。</p>")
    else:
        lines.append("<p>本次没有订阅附件（只发榜单）。"
                     "订阅文件在 collect 产物里生成，见工作流日志。</p>")
    lines.append("<p style='color:#666;font-size:12px'>"
                 "免费节点随时失效，一条不通就换下一条。"
                 "本邮件只发给收件人本人，订阅附件含节点凭据，请勿转发公开。"
                 "</p>")
    return "".join(lines), rows


def collect_attachments():
    out = []
    for fname, _label, mime in ATTACHMENTS:
        path = os.path.join(SUBS_DIR, fname)
        if os.path.exists(path):
            maintype, subtype = mime.split("/", 1)
            out.append((fname, path, maintype, subtype))
    return out


def send(doc=None, dry_run=False):
    doc = doc if doc is not None else load_top50()
    rows = (doc.get("lists") or {}).get("top50") or []
    if not rows:
        print("Top50 为空，今天不发邮件。")
        return 0
    html, _ = build_html(doc)
    to_addr = os.environ.get("MAIL_TO", DEFAULT_TO)
    subject = f"每日优选 Top50 · {doc.get('updated')}（{len(rows)} 条）"
    if dry_run:
        print(f"收件人：{to_addr}")
        print(f"标题：{subject}")
        print(f"附件：{[a[0] for a in collect_attachments()] or '无'}")
        print("--- 正文预览（前 500 字）---")
        print(html[:500])
        return 0

    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "465") or 465)
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    from_addr = os.environ.get("SMTP_FROM", user)
    missing = [k for k, v in (("SMTP_HOST", host), ("SMTP_USER", user),
                              ("SMTP_PASS", password)) if not v]
    if missing:
        print(f"邮件跳过：缺 SMTP 配置 {', '.join(missing)}，"
              "请在仓库 Secrets 里配好再跑。Top50 JSON 不受影响。")
        return 0

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    msg.set_content("请用支持 HTML 的邮箱查看这封邮件。", charset="utf-8")
    msg.add_alternative(html, subtype="html", charset="utf-8")
    for fname, path, maintype, subtype in collect_attachments():
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                               filename=fname)
    try:
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            smtp = smtplib.SMTP(host, port, timeout=30)
            smtp.starttls()
        try:
            smtp.login(user, password)
            smtp.send_message(msg)
        finally:
            smtp.quit()
    except Exception as e:
        print(f"邮件发送失败：{type(e).__name__}: {e}")
        return 1
    print(f"已发送给 {to_addr}：{subject}，"
          f"附件 {[a[0] for a in collect_attachments()] or '无'}")
    return 0


def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("  ✔ " if c else "  ✘ ") + m)
        ok = ok and c

    doc = {"updated": "2026-11-28 08:00", "probe_label": "海外 runner 实测",
           "probe_note": "测试说明",
           "lists": {"top50": [
               {"rank": 1, "name": "<b>坏名字</b>", "type": "vless",
                "rg": "日本", "score": 88.2, "rate": 1.0, "tries": 7,
                "hits": 7, "ms": 229, "bps": 1372215, "src": "probe"},
           ]}}
    html, rows = build_html(doc)
    chk("&lt;b&gt;" in html, "名字里的 HTML 被转义，不会注入邮件")
    chk("88.2" in html and "229ms" in html, "评分和延迟进了表格")
    chk("1.37 MB/s" in html, "速度换算成 MB/s 显示")
    chk(len(rows) == 1, "行数正确")
    chk(fmt_bps(None) == "—", "没有测速时显示 —，不是 0")
    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(send(dry_run="--dry-run" in sys.argv))
