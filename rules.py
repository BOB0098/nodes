#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最优分流规则（自动生成，一键可用）。

来源全部是社区维护的规则集，不手写几千条域名：
- Clash / Mihomo：Loyalsoldier/clash-rules，轻量、每 12 小时自动构建。
  用 Mihomo 原生二进制 .mrs（CRThu/clash-rules-mrs 每日同步编译），
  比文本 .txt 匹配更快、内存更小，Clash Verge Rev / FlClash /
  Clash Meta for Android（仓库 README 推荐的三个客户端，全是 Mihomo 内核）
  都支持。jsDelivr 做 CDN，国内拉取也通。
- sing-box：SagerNet/sing-geosite + sing-geoip 官方规则集，
  二进制 .srs，sing-box 直接加载，比明文快 3-5 倍。

模式：白名单绕过大陆（国内直连、其余走代理）。
顺序就是优先级，第一条命中即生效，所以顺序不能乱：
  广告拦截 > 私有/局域网直连 > 国内域名直连 > 国内 IP 直连
  > GEOIP CN 直连 > GFW/常用代理域名走代理 > 其余走代理

为什么不用 ACL4SSR 的 ChinaDomain / ProxyDomain 做主规则？
它也很好，但 Loyalsoldier 更轻（direct 约 500 条、reject 约 400 条、
gfw 约 1000 条），且 .mrs 是 Mihomo 原生格式；
ACL4SSR 的 Clash 碎片更适合 subconverter 转换场景。
这里把 ACL4SSR 作为备选写在注释里，不混用两套，避免规则冲突。

一键可用是指：导出的 Clash YAML / sing-box JSON 里已经带好了
rule-providers / rule_set、proxy-groups、rules / route、DNS，
导入客户端不用再手动加规则、不用再改 MATCH。
"""

# ── Clash / Mihomo ──────────────────────────────────────────────────
# jsDelivr 上的 Loyalsoldier .mrs（CRThu 每日同步编译）。
# interval 86400 = 每天自动拉一次新规则。
CLASH_RULE_PROVIDERS = {
    "reject": {
        "type": "http", "behavior": "domain", "format": "mrs",
        "url": "https://cdn.jsdelivr.net/gh/CRThu/clash-rules-mrs@release/loyalsoldier/reject.mrs",
        "path": "./ruleset/reject.mrs", "interval": 86400,
    },
    "private": {
        "type": "http", "behavior": "domain", "format": "mrs",
        "url": "https://cdn.jsdelivr.net/gh/CRThu/clash-rules-mrs@release/loyalsoldier/private.mrs",
        "path": "./ruleset/private.mrs", "interval": 86400,
    },
    "lancidr": {
        "type": "http", "behavior": "ipcidr", "format": "mrs",
        "url": "https://cdn.jsdelivr.net/gh/CRThu/clash-rules-mrs@release/loyalsoldier/lancidr.mrs",
        "path": "./ruleset/lancidr.mrs", "interval": 86400,
    },
    "direct": {
        "type": "http", "behavior": "domain", "format": "mrs",
        "url": "https://cdn.jsdelivr.net/gh/CRThu/clash-rules-mrs@release/loyalsoldier/direct.mrs",
        "path": "./ruleset/direct.mrs", "interval": 86400,
    },
    "cncidr": {
        "type": "http", "behavior": "ipcidr", "format": "mrs",
        "url": "https://cdn.jsdelivr.net/gh/CRThu/clash-rules-mrs@release/loyalsoldier/cncidr.mrs",
        "path": "./ruleset/cncidr.mrs", "interval": 86400,
    },
    "gfw": {
        "type": "http", "behavior": "domain", "format": "mrs",
        "url": "https://cdn.jsdelivr.net/gh/CRThu/clash-rules-mrs@release/loyalsoldier/gfw.mrs",
        "path": "./ruleset/gfw.mrs", "interval": 86400,
    },
    "proxy": {
        "type": "http", "behavior": "domain", "format": "mrs",
        "url": "https://cdn.jsdelivr.net/gh/CRThu/clash-rules-mrs@release/loyalsoldier/proxy.mrs",
        "path": "./ruleset/proxy.mrs", "interval": 86400,
    },
}

# 注意：策略组名必须和 export_subs.py 里的“节点选择 / 自动选择”一致，
# 否则规则引用的组不存在，客户端会拒绝整份配置。
# REJECT / DIRECT 是 Clash 内置策略，不用在 proxy-groups 里定义。
CLASH_RULES = [
    "RULE-SET,reject,REJECT",
    "RULE-SET,private,DIRECT",
    "RULE-SET,lancidr,DIRECT,no-resolve",
    "RULE-SET,direct,DIRECT",
    "RULE-SET,cncidr,DIRECT,no-resolve",
    "GEOIP,CN,DIRECT,no-resolve",
    "RULE-SET,gfw,节点选择",
    "RULE-SET,proxy,节点选择",
    "MATCH,节点选择",
]

CLASH_DNS = {
    "enable": True,
    "ipv6": False,
    "nameserver": ["223.5.5.5", "119.29.29.29"],
    "fallback": ["8.8.8.8", "1.1.1.1"],
    # 国内域名走国内 DNS，不走代理，避免 DNS 污染和绕路
    "fallback-filter": {"geoip-code": "CN", "ipcidr": ["240.0.0.0/4"]},
    "nameserver-policy": {
        "geosite:cn,private": ["223.5.5.5", "119.29.29.29"],
    },
}

# ── sing-box ────────────────────────────────────────────────────────
# SagerNet 官方 rule-set，.srs 二进制，远程拉取，download_detour 走代理
# （规则文件本身在 GitHub 上，国内直连可能拉不到，走代理最稳）。
SINGBOX_RULE_SETS = [
    {"type": "remote", "tag": "geosite-ads", "format": "binary",
     "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-ads.srs",
     "download_detour": "节点选择"},
    {"type": "remote", "tag": "geosite-cn", "format": "binary",
     "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs",
     "download_detour": "节点选择"},
    {"type": "remote", "tag": "geosite-private", "format": "binary",
     "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-private.srs",
     "download_detour": "节点选择"},
    {"type": "remote", "tag": "geoip-cn", "format": "binary",
     "url": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-cn.srs",
     "download_detour": "节点选择"},
    {"type": "remote", "tag": "geoip-private", "format": "binary",
     "url": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-private.srs",
     "download_detour": "节点选择"},
]

# route.rules 从上到下，第一条命中即生效。
# 顺序：广告拦截 > 私有直连 > 国内直连 > 其余走自动选择。
# AI / 流媒体不单独分组：免费节点 IP 纯净度不够，单独分组也解锁不了，
# 反而会让用户以为“走了 AI 专线就一定能用”；统一走自动选择，
# 靠 Top50 的真实握手和速度来保证可用性。
SINGBOX_ROUTE_RULES = [
    {"action": "route", "rule_set": ["geosite-ads"], "outbound": "block"},
    {"action": "route", "rule_set": ["geosite-private", "geoip-private"],
     "outbound": "direct"},
    {"action": "route", "rule_set": ["geosite-cn", "geoip-cn"],
     "outbound": "direct"},
    {"action": "route", "outbound": "自动选择"},
]

SINGBOX_DNS = {
    "servers": [
        {"tag": "dns-direct", "address": "223.5.5.5", "detour": "direct"},
        {"tag": "dns-proxy", "address": "8.8.8.8", "detour": "节点选择"},
    ],
    "rules": [
        {"rule_set": ["geosite-cn"], "server": "dns-direct"},
        {"outbound": "any", "server": "dns-proxy"},
    ],
    "final": "dns-proxy",
}


def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(("  ✔ " if c else "  ✘ ") + m)
        ok = ok and c

    chk(CLASH_RULES[0].endswith(",REJECT"), "Clash 第一条是广告拦截")
    chk(CLASH_RULES[-1] == "MATCH,节点选择", "Clash 最后一条是 MATCH 走代理")
    chk(any(r.startswith("GEOIP,CN,DIRECT") for r in CLASH_RULES),
        "Clash 有 GEOIP CN 直连兜底")
    chk(all("cdn.jsdelivr.net/gh/CRThu/clash-rules-mrs" in v["url"]
            for v in CLASH_RULE_PROVIDERS.values()),
        "Clash 规则集全部走 jsDelivr 的 .mrs")
    chk(all(v.get("interval") == 86400
            for v in CLASH_RULE_PROVIDERS.values()),
        "Clash 规则集每天自动更新一次")
    chk(SINGBOX_ROUTE_RULES[0]["outbound"] == "block",
        "sing-box 第一条是广告拦截")
    chk(SINGBOX_ROUTE_RULES[-1]["outbound"] == "自动选择",
        "sing-box 最后一条是自动选择兜底")
    chk(any(r["tag"] == "geosite-cn" for r in SINGBOX_RULE_SETS),
        "sing-box 有 geosite-cn")
    chk(any(r["tag"] == "geoip-cn" for r in SINGBOX_RULE_SETS),
        "sing-box 有 geoip-cn")
    print("\n" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
