#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""来源登记表的读取与校验。

collector.py 和 screen.py 共用这一层，保证「页面展示的源」与「实际采集的源」
是同一份清单 —— 旧代码里这是两张表，直接导致 7 张卡片没有统计数据。
"""
import json
import os

import yaml

import netsafe

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(BASE, "sources.yaml")
STATS_PATH = os.path.join(BASE, "data", "sources_stats.json")

REQUIRED = ("id", "name", "group", "url", "type", "format", "enabled")
TYPES = {"original", "aggregator"}
FORMATS = {"clash", "v2ray-base64", "v2ray-plain", "singbox-json", "sing-box", "mixed", "unknown"}


class SourceError(ValueError):
    pass


def load_doc(path=DEFAULT_PATH):
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict) or "sources" not in doc or "groups" not in doc:
        raise SourceError("sources.yaml 缺少 sources / groups 顶层键")
    return doc


def validate(doc):
    """结构校验。宁可启动就报错，也不要带着坏清单跑一轮采集。"""
    gids = [g["id"] for g in doc["groups"]]
    if len(gids) != len(set(gids)):
        raise SourceError("groups 存在重复 id")
    seen = set()
    names = set()
    problems = []
    for i, s in enumerate(doc["sources"]):
        for k in REQUIRED:
            if k not in s:
                problems.append(f"第 {i+1} 条缺少字段 {k}")
        sid = s.get("id")
        if sid in seen:
            problems.append(f"id 重复: {sid}")
        seen.add(sid)
        # name 是节点库里的 src 归属键，重名会让两个源的去重统计混在一起
        if s.get("name") in names:
            problems.append(f"name 重复: {s.get('name')}")
        names.add(s.get("name"))
        if s.get("group") not in gids:
            problems.append(f"{sid}: group={s.get('group')!r} 不在 groups 里")
        if s.get("type") not in TYPES:
            problems.append(f"{sid}: type={s.get('type')!r} 非法")
        if s.get("format") not in FORMATS:
            problems.append(f"{sid}: format={s.get('format')!r} 非法")
        if s.get("enabled") and not (s.get("card") or {}).get("tag"):
            problems.append(f"{sid}: enabled 但没有 card.tag，页面会出空白卡片")
        if not str(s.get("url", "")).startswith("http"):
            problems.append(f"{sid}: url 非法")
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


def id_by_url(sources):
    return {s["url"]: s["id"] for s in sources if s.get("url")}


def load(path=DEFAULT_PATH):
    doc = load_doc(path)
    groups, sources = build(doc)
    return groups, sources, doc.get("groups") or []


# ── 动态统计（每轮采集写一次，不进 sources.yaml，避免 diff 噪声）──────

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
