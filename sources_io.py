#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""来源登记表的读取与校验。

collector.py 和 screen.py 共用这一层，保证「页面展示的源」与「实际采集的源」
是同一份清单 —— 旧代码里这是两张表，直接导致 7 张卡片没有统计数据。
"""
import ipaddress
import json
import os
import re
import urllib.parse

import yaml

import netsafe

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(BASE, "sources.yaml")
STATS_PATH = os.path.join(BASE, "data", "sources_stats.json")

REQUIRED = ("id", "name", "group", "url", "type", "format", "enabled")
TYPES = {"original", "aggregator"}
FORMATS = {"clash", "v2ray-base64", "v2ray-plain", "singbox-json", "sing-box", "mixed", "unknown"}
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class SourceError(ValueError):
    pass


def load_doc(path=DEFAULT_PATH):
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict) or "sources" not in doc or "groups" not in doc:
        raise SourceError("sources.yaml 缺少 sources / groups 顶层键")
    return doc


def _url_issue(value):
    """Return a syntax-only URL error; registration must never perform DNS."""
    if not isinstance(value, str) or not value.strip():
        return "必须是非空 URL 字符串"
    if any(char.isspace() or ord(char) < 32 for char in value):
        return "包含空白或控制字符"
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return "URL 结构非法"
    if parsed.scheme.lower() not in ("http", "https"):
        return "协议必须是 HTTP 或 HTTPS"
    if not parsed.netloc or not parsed.hostname:
        return "缺少主机名"
    if parsed.username is not None or parsed.password is not None:
        return "不允许 URL 内嵌用户名或密码"
    try:
        port = parsed.port
    except ValueError:
        return "端口格式非法"
    if port is not None and port not in netsafe.ALLOW_PORTS:
        return f"端口不在允许范围 {sorted(netsafe.ALLOW_PORTS)}"

    host = parsed.hostname
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "%" in host:
            return "主机名格式非法"
        try:
            ascii_host = host.rstrip(".").encode("idna").decode("ascii")
        except UnicodeError:
            return "主机名格式非法"
        labels = ascii_host.split(".")
        if (not ascii_host or len(ascii_host) > 253
                or any(not _DNS_LABEL.fullmatch(label) for label in labels)):
            return "主机名格式非法"
    return None


def validate(doc):
    """校验来源登记表的结构和 URL 语法；不做联网或 DNS 检查。"""
    if not isinstance(doc, dict):
        raise SourceError("sources.yaml 顶层必须是对象")
    for field in ("groups", "sources"):
        if field not in doc:
            raise SourceError(f"sources.yaml 缺少顶层键 {field}")
        if not isinstance(doc[field], list):
            raise SourceError(f"sources.yaml schema 错误: {field} 必须是列表")

    problems = []
    gids = set()
    for index, group in enumerate(doc["groups"], start=1):
        prefix = f"groups[{index}]"
        if not isinstance(group, dict):
            problems.append(f"{prefix} 必须是对象")
            continue
        for field in ("id", "title", "kern", "clients"):
            value = group.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{prefix}.{field} 必须是非空字符串")
        group_id = group.get("id")
        if isinstance(group_id, str) and group_id:
            if group_id in gids:
                problems.append(f"groups 存在重复 id: {group_id}")
            gids.add(group_id)

    seen = set()
    names = set()
    for index, source in enumerate(doc["sources"], start=1):
        prefix = f"sources[{index}]"
        if not isinstance(source, dict):
            problems.append(f"{prefix} 必须是对象")
            continue
        for field in REQUIRED:
            if field not in source:
                problems.append(f"{prefix} 缺少字段 {field}")

        source_id = source.get("id")
        label = source_id if isinstance(source_id, str) and source_id else prefix
        for field in ("id", "name", "group", "type", "format"):
            value = source.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{label}: {field} 必须是非空字符串")

        if isinstance(source_id, str) and source_id:
            if source_id in seen:
                problems.append(f"id 重复: {source_id}")
            seen.add(source_id)

        name = source.get("name")
        if isinstance(name, str) and name:
            # name 是节点库里的 src 归属键，重名会让两个源的去重统计混在一起
            if name in names:
                problems.append(f"name 重复: {name}")
            names.add(name)

        group_id = source.get("group")
        if isinstance(group_id, str) and group_id not in gids:
            problems.append(f"{label}: group={group_id!r} 不在 groups 里")
        source_type = source.get("type")
        if isinstance(source_type, str) and source_type not in TYPES:
            problems.append(f"{label}: type={source_type!r} 非法")
        source_format = source.get("format")
        if isinstance(source_format, str) and source_format not in FORMATS:
            problems.append(f"{label}: format={source_format!r} 非法")

        enabled = source.get("enabled")
        if not isinstance(enabled, bool):
            problems.append(f"{label}: enabled 必须是布尔值")

        url_problem = _url_issue(source.get("url"))
        if url_problem:
            problems.append(f"{label}: url {url_problem}")

        mirrors = source.get("mirrors")
        if mirrors is not None:
            if not isinstance(mirrors, list):
                problems.append(f"{label}: mirrors 必须是列表")
            else:
                for mirror_index, mirror in enumerate(mirrors, start=1):
                    mirror_problem = _url_issue(mirror)
                    if mirror_problem:
                        problems.append(
                            f"{label}: mirrors[{mirror_index}] {mirror_problem}")

        card = source.get("card")
        if card is not None and not isinstance(card, dict):
            problems.append(f"{label}: card 必须是对象")
        elif enabled is True and not (card or {}).get("tag"):
            problems.append(f"{label}: enabled 但没有 card.tag，页面会出空白卡片")

    if problems:
        raise SourceError("sources.yaml 校验失败:\n  " + "\n  ".join(problems))
    return True


def build(doc):
    """→ (groups, sources)

    groups 只含 enabled 的源，可直接喂给 assemble() 生成页面；
    sources 是全量（含 enabled=False 的候选），供 screen.py 审核。
    """
    validate(doc)
    enabled = [s for s in doc["sources"] if s.get("enabled")]
    groups = []
    for g in doc["groups"]:
        items = []
        for s in enabled:
            if s["group"] != g["id"]:
                continue
            c = s.get("card") or {}
            items.append({
                "tag": c.get("tag", s["name"]),
                "note": c.get("note", ""),
                "ok": c.get("ok", []),
                "no": c.get("no", []),
                "freq": c.get("freq", ""),
                "url": s["url"],
                "cn": bool(c.get("cn")),
                "sid": s["id"],
            })
        groups.append({"title": g["title"], "kern": g["kern"],
                       "clients": g["clients"], "items": items})
    return groups, list(doc["sources"])


def collect_targets(sources):
    """采集用清单：[(name, [url, *mirrors])]，只含 enabled。"""
    out = []
    for s in sources:
        if not s.get("enabled"):
            continue
        urls = [s["url"]] + [m for m in (s.get("mirrors") or []) if m]
        out.append((s["name"], urls))
    return out


def collect_groups(sources):
    """按相同主 URL 合并启用来源，保留每个来源并合并镜像顺序。

    一个主 URL 只产生一个采集组；`ids` / `names` 保存该结果的全部归属。
    备用地址按 sources.yaml 登记顺序拼接，并按首次出现去重。
    """
    groups, by_url = [], {}
    for source in sources:
        if not source.get("enabled"):
            continue
        url = source["url"]
        group = by_url.get(url)
        if group is None:
            group = {"url": url, "ids": [], "names": [], "urls": [url]}
            by_url[url] = group
            groups.append(group)
        group["ids"].append(source["id"])
        group["names"].append(source["name"])
        for mirror in source.get("mirrors") or []:
            if mirror and mirror not in group["urls"]:
                group["urls"].append(mirror)
    return groups


def id_by_url(sources):
    return {s["url"]: s["id"] for s in sources if s.get("url")}


def load(path=DEFAULT_PATH):
    doc = load_doc(path)
    groups, sources = build(doc)
    return groups, sources, doc.get("groups") or []


# ── 动态统计（每轮采集写一次，不进 sources.yaml，避免 diff 噪声）────

def load_stats():
    """读取动态统计；仅首次缺失时兼容为空状态。

    损坏或结构不符的数据必须中止当前流程，避免 collector 把空统计写回并
    覆盖原始证据文件。
    """
    try:
        with open(STATS_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return {"sources": []}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceError(f"sources_stats.json JSON 损坏: {exc}") from exc
    except OSError as exc:
        raise SourceError(f"读取 sources_stats.json 失败: {exc}") from exc

    if not isinstance(doc, dict) or not isinstance(doc.get("sources"), list):
        raise SourceError("sources_stats.json schema 错误: sources 必须是列表")
    if any(not isinstance(row, dict) for row in doc["sources"]):
        raise SourceError("sources_stats.json schema 错误: sources 条目必须是对象")
    seen_ids = set()
    for index, row in enumerate(doc["sources"], start=1):
        source_id = row.get("id")
        if not isinstance(source_id, str) or not source_id:
            raise SourceError(
                f"sources_stats.json schema 错误: 第 {index} 条 id 必须是非空字符串")
        if source_id in seen_ids:
            raise SourceError(
                f"sources_stats.json schema 错误: 来源 id 重复: {source_id}")
        seen_ids.add(source_id)
        for field in ("fails_total", "fails_streak"):
            value = row.get(field)
            if (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise SourceError(
                    f"sources_stats.json schema 错误: 第 {index} 条 {field} "
                    "必须是非负整数")
    return doc


def save_stats(rows, updated=None, in_ci=None):
    """rows: [{id, ...}]。CI 里落盘，本地只打印摘要。

    与 health.json 同理：本地出口网络与机房差异极大，本地数据不具可比性。
    NODES_FORCE_WRITE=1 强制落盘，用于本地验证评分链路（数据会被下一轮 CI 覆盖）。
    """
    force = os.environ.get("NODES_FORCE_WRITE") == "1"
    in_ci = (os.environ.get("GITHUB_ACTIONS") == "true" or force) if in_ci is None else in_ci
    doc = {"updated": updated, "sources": rows}
    if in_ci:
        netsafe.write_atomic(STATS_PATH, json.dumps(doc, ensure_ascii=False, indent=1))
    else:
        print("  （本地运行，不写入 sources_stats.json）")
    return doc
