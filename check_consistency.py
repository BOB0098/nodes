#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前一致性检查：页面、数据、订阅、状态是不是同一轮、同一个数。

这个脚本存在的理由是一次真实事故：同一轮里 `nodes.json` 写 9,845、
`subscriptions.json` 写 9,846、内嵌到 `index.html` 的又是 9,846。
三个数字差 1，没人发现，因为没有任何一处会把它们放在一起比。

P20 起增加 build manifest 硬校验（data/build_manifest.json）：
旧版时间正则只认「11-25 14:02」，认不出「2026-11-25 14:15」，后者被
跳过，于是「差 13 分钟」的两份数据被报成「差 0 分钟，同一轮」。现在
所有时间统一用 build_manifest.norm_ts 解析，且 manifest 存在时逐产物
核对 sha256 与计数——任何一个产物被单独改过而不重生成 manifest，
发布就被拦下，远端保留上一版。

兼容：manifest 缺失时（P20 之前的历史数据或本地手工环境）自动降级为
旧口径——时间戳为警告、跳过哈希校验。CI 里 daily.yml 会先跑
build_manifest.py，所以正式发布一定是硬校验。

P22 起增加第 9 项「节点数量异常下降」门禁：对照上一版已提交数据
（git HEAD 里的 nodes.json），本轮入库数掉到上一版的 60% 以下即判为
采集/解析链路故障，拦下发布——避免上游改格式/被封时把 9846 条悄悄
发成 300 条，页面还显示「正常更新」。无 git 历史时降级为警告。
可用 NODES_PREV_COUNT 环境变量覆盖上一版条数（演练/离线环境用）。

用法：
    python3 check_consistency.py            # 校验，不一致退出码 1
    python3 check_consistency.py --quiet    # 只输出失败项
"""
import json
import os
import re
import subprocess
import sys

from build_manifest import norm_ts, sha256_file, ARTIFACTS, SCHEMA_VERSION

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
MANIFEST = os.path.join(DATA, "build_manifest.json")
SCHEMA = 2
MAX_SKEW_MIN = 90
MIN_KEEP_RATIO = 0.60   # 本轮条数不得低于上一版的 60%


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}


def mins(t, year_hint=None):
    """旧格式的兼容入口，内部走统一规范化。返回当日分钟数或 None。"""
    ts = norm_ts(t, year_hint)
    if ts is None:
        return None
    from datetime import datetime
    from build_manifest import TZ
    dt = datetime.fromtimestamp(ts, TZ)
    return dt.hour * 60 + dt.minute


def prev_nodes_count():
    """上一版已提交的节点条数。

    CI 里 fetch-depth=0，HEAD 是上一轮提交（本轮数据还没提交），
    所以 git show HEAD:data/nodes.json 就是「上一版」。
    环境变量优先（演练/离线环境），取不到返回 None。
    """
    override = os.environ.get("NODES_PREV_COUNT")
    if override is not None:
        try:
            return max(0, int(override))
        except ValueError:
            return None
    try:
        out = subprocess.run(
            ["git", "-C", BASE, "show", "HEAD:data/nodes.json"],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            return None
        d = json.loads(out.stdout)
        return len(d.get("nodes") or [])
    except Exception:
        return None


def embedded(index_html):
    try:
        with open(index_html, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return None
    m = re.search(r"const DATA = (\{.*?\});\s*\n", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def main():
    quiet = "--quiet" in sys.argv
    fails, warns = [], []

    def ok(msg):
        if not quiet:
            print("  ✔ " + msg)

    def bad(msg):
        fails.append(msg)
        print("  ✘ " + msg)

    def warn(msg):
        warns.append(msg)
        if not quiet:
            print("  ! " + msg)

    nodes = load(os.path.join(DATA, "nodes.json"))
    subs = load(os.path.join(DATA, "subscriptions.json"))
    page = embedded(os.path.join(BASE, "index.html"))
    mf = load(MANIFEST)
    manifest_exists = os.path.exists(MANIFEST)
    manifest_loaded = isinstance(mf, dict) and "__error__" not in mf
    manifest_schema = mf.get("schema_version") if manifest_loaded else None
    has_manifest = manifest_loaded and manifest_schema == SCHEMA_VERSION
    legacy_manifest = (not manifest_exists or
                       (manifest_loaded and manifest_schema == 1))
    if manifest_exists and not has_manifest and not legacy_manifest:
        detail = (mf.get("__error__") if isinstance(mf, dict) else None)
        if not detail:
            detail = f"不支持 schema_version={manifest_schema!r} 或 manifest 根节点无效"
        bad("build_manifest.json 无效：" + str(detail))

    if "__error__" in nodes:
        bad("data/nodes.json 读不出来：" + nodes["__error__"])
        return 1

    mode = ("manifest 硬校验" if has_manifest else
            "历史兼容旧口径" if legacy_manifest else "manifest 无效")
    print(f"一致性检查（{mode}）")

    # 1. 节点数组长度
    n = len(nodes.get("nodes") or [])
    dedup = (nodes.get("stats") or {}).get("dedup")
    if dedup == n:
        ok(f"nodes.json 条数 {n} = stats.dedup {dedup}")
    else:
        bad(f"nodes.json 条数 {n} ≠ stats.dedup {dedup}")

    # 2. 订阅分组
    if "__error__" in subs:
        warn("data/subscriptions.json 读不出来，跳过订阅比对")
    else:
        sg = {g.get("key"): g.get("count") for g in (subs.get("groups") or [])}
        ns = (nodes.get("stats") or {}).get("subs") or {}
        if not ns:
            warn("nodes.json 没有 stats.subs，跳过")
        for key, v in ns.items():
            cnt = v.get("count") if isinstance(v, dict) else v
            got = sg.get(key)
            if got is None:
                warn(f"订阅分组 {key} 在 subscriptions.json 里不存在")
            elif got != cnt:
                bad(f"订阅分组 {key}：页面记 {cnt}，订阅文件 {got}")
        if ns and all(sg.get(k) == (v.get("count") if isinstance(v, dict) else v)
                      for k, v in ns.items() if k in sg):
            ok(f"订阅 {len(ns)} 个分组条数与页面一致")

    # 3. 内嵌数据
    if page is None:
        bad("index.html 里找不到内嵌 DATA")
    else:
        pd = (page.get("stats") or {}).get("dedup")
        if pd == n:
            ok(f"index.html 内嵌条数 {pd} = nodes.json {n}")
        else:
            bad(f"index.html 内嵌条数 {pd} ≠ nodes.json {n}")

    # 4. 时间戳：统一用 norm_ts 解析所有格式。有 manifest 时取各产物的
    #    updated_ts，无 manifest 时用各文件 updated 兜底。
    year_hint = None
    for doc in (subs, load(os.path.join(DATA, "nodecheck.json")),
                load(os.path.join(DATA, "picks.json"))):
        t = norm_ts(doc.get("updated") if isinstance(doc, dict) else None)
        if t:
            from datetime import datetime
            from build_manifest import TZ
            year_hint = datetime.fromtimestamp(t, TZ).year
            break
    if has_manifest:
        ts = {k: (v or {}).get("updated_ts")
              for k, v in (mf.get("artifacts") or {}).items()
              if isinstance(v, dict) and (v or {}).get("updated_ts")}
    else:
        ts = {
            "nodes": norm_ts(nodes.get("updated"), year_hint),
            "subs": norm_ts(subs.get("updated") if isinstance(subs, dict) else None, year_hint),
        }
        if page is not None:
            ts["page"] = norm_ts(page.get("updated"), year_hint)
    known = {k: v for k, v in ts.items() if v is not None}
    if len(known) >= 2:
        skew = (max(known.values()) - min(known.values())) // 60
        detail = " / ".join(
            f"{k} {(v % 86400) // 3600:02d}:{(v % 3600) // 60:02d}"
            for k, v in known.items())
        if skew <= MAX_SKEW_MIN:
            ok(f"时间戳同一轮（差 {skew} 分钟，{len(known)} 个产物）：{detail}")
        else:
            msg = f"时间戳相差 {skew} 分钟，超过 {MAX_SKEW_MIN}：{detail}"
            if has_manifest:
                bad(msg + "（同一 manifest 内不允许）")
            else:
                warn(msg)
    else:
        warn("可解析的时间戳不足 2 个，无法比对")

    # 5. schema
    sv = nodes.get("schema")
    if sv is None:
        warn("nodes.json 没有 schema 字段（旧格式，下次采集会补上）")
    elif sv != SCHEMA:
        bad(f"nodes.json schema={sv}，本脚本认 {SCHEMA}")

    # 6. 榜单与订阅
    picks = load(os.path.join(DATA, "picks.json"))
    if "__error__" in picks:
        warn("data/picks.json 读不出来，跳过榜单比对")
    elif "__error__" in subs:
        warn("subscriptions.json 不可用，跳过榜单比对")
    else:
        pl = {k: len(v) for k, v in (picks.get("lists") or {}).items()}
        sg = {g.get("key"): g.get("count") for g in (subs.get("groups") or [])}
        diff = [f"{k} 榜 {pl[k]} / 订阅 {sg.get(k)}"
                for k in sorted(pl) if k in sg and sg.get(k) != pl[k]]
        if diff:
            bad("榜单与订阅条数不一致：" + "；".join(diff))
        elif pl:
            dropped = picks.get("dropped") or 0
            ok(f"榜单 {len(pl)} 组条数与订阅一致"
               + (f"（已剔除下架 {dropped} 条）" if dropped else ""))
        extra = [k for k in pl if k not in sg]
        if extra:
            warn("榜单有分组未出现在订阅清单：" + "、".join(extra))

    # 7. 来源治理
    gov = load(os.path.join(DATA, "source_governance.json"))
    if "__error__" in gov:
        warn("data/source_governance.json 读不出来，跳过来源治理比对")
    else:
        covered = gov.get("sources") or {}
        declared = int((gov.get("whitelist") or {}).get("enabled") or 0)
        if declared and len(covered) != declared:
            bad(f"来源治理覆盖 {len(covered)} 个源，但白名单启用 {declared} 个")
        elif covered:
            obs = sum(1 for r in covered.values() if r.get("under_observation"))
            dup = len(gov.get("duplicates") or [])
            ok(f"来源治理覆盖 {len(covered)} 个源（同源地址 {dup} 组，观察期 {obs} 个）")

    # 10. 同轮构建证明（P23）：manifest 必须带完整 pipeline，且当前台账
    #     校验通过。这堵住的真实缺陷是：检测/评分等步骤 continue-on-error
    #     失败后仍继续，旧产物被新 build_id 冒充新一轮发布。
    if has_manifest:
        pipe = mf.get("pipeline") or {}
        if not pipe.get("run_id"):
            bad("manifest 缺 pipeline.run_id（无法证明同轮构建）")
        elif not pipe.get("complete"):
            bad("manifest 的 pipeline 不完整：" + "；".join(pipe.get("errors") or ["未知原因"]))
        else:
            import run_ledger
            # 干净 clone / 离线校验时磁盘上没有台账（CI 每轮 start 才生成），
            # 此时降级为警告——与数量门禁「无 git 历史降级」同口径，保证
            # 「干净 clone 后 verify.sh 可复现」。真实 CI 里 start 一定先跑，
            # 台账存在且阶段缺失时下面这段会硬拦下，不放宽。
            if not os.path.isfile(run_ledger.LEDGER):
                warn("磁盘无 run_ledger.json（离线/干净 clone），跳过当前台账校验；"
                     "CI 每轮 start 后会硬校验")
            else:
                live_ok, live_errs = run_ledger.check()
                if not live_ok:
                    bad("当前阶段台账校验失败：" + "；".join(live_errs))
                elif (run_ledger.load().get("run_id")) != pipe.get("run_id"):
                    bad("台账 run_id 与 manifest 不一致（跨轮产物）")
                else:
                    ok(f"同轮构建证明通过：run_id={pipe.get('run_id')}，"
                       f"{pipe.get('stage_count')}/{len(run_ledger.STAGE_KEYS)} 个阶段成功")
    elif legacy_manifest:
        warn("无 manifest 或历史 schema=1，跳过同轮构建证明检查")

    # 9. 节点数量异常下降（P22）：上游改格式/被封时，最常见的事故不是
    #    数据错，而是数据「少了但看起来正常」。只比同一口径的入库条数，
    #    不拿来源数/检测次数混比。下降超过阈值就拦下发布。
    prev = prev_nodes_count()
    if prev is None:
        warn("取不到上一版节点条数（无 git 历史），跳过数量下降检查")
    elif prev == 0:
        ok("上一版为空（首次采集），跳过数量下降检查")
    else:
        keep = n / prev
        if keep < MIN_KEEP_RATIO:
            bad(f"节点数量异常下降：上一版 {prev} → 本轮 {n}（保留 {keep:.0%}，"
                f"低于 {MIN_KEEP_RATIO:.0%}），疑似采集/解析链路故障")
        else:
            delta = n - prev
            sign = "+" if delta > 0 else ""
            ok(f"节点数量正常：上一版 {prev} → 本轮 {n}（{sign}{delta}，保留 {keep:.0%}）")

    # 8. build manifest 硬校验（P20）
    if legacy_manifest:
        warn("无 manifest 或历史 schema=1，跳过产物哈希校验（CI 应先跑 build_manifest.py）")
    elif has_manifest:
        # 8a. 逐产物 sha256——任何一个被单独改过就拦下发布
        artifact_errors = []
        checked = 0
        artifacts = mf.get("artifacts")
        if not isinstance(artifacts, dict):
            artifact_errors.append("manifest.artifacts 必须是对象")
            artifacts = {}
        expected_paths = {key: rel for key, rel, _ in ARTIFACTS}
        missing_keys = set(expected_paths) - set(artifacts)
        extra_keys = set(artifacts) - set(expected_paths)
        if missing_keys:
            artifact_errors.append("缺少必需 artifact：" + ", ".join(sorted(missing_keys)))
        if extra_keys:
            artifact_errors.append("包含未知 artifact 键：" + ", ".join(sorted(extra_keys)))
        for key, rel, _ in ARTIFACTS:
            meta = artifacts.get(key)
            if not isinstance(meta, dict):
                if key in artifacts:
                    artifact_errors.append(f"{key} 元数据必须是对象")
                continue
            if meta.get("path") != rel:
                artifact_errors.append(f"{key} 的 path 应为 {rel}")
                continue
            expected = meta.get("sha256")
            if (not isinstance(expected, str)
                    or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
                artifact_errors.append(f"{rel} 的 sha256 缺失或格式无效")
                continue
            path = os.path.join(BASE, rel)
            if not os.path.isfile(path):
                artifact_errors.append(f"{rel} 文件缺失")
                continue
            if sha256_file(path) != expected:
                artifact_errors.append(f"{rel} 已被改动（与 manifest 哈希不符）")
            checked += 1
        if artifact_errors:
            bad("manifest artifact 校验失败：" + "；".join(artifact_errors))
        else:
            ok(f"manifest 覆盖必需的 {checked} 个产物 sha256 全部吻合")

        # 8b. manifest 计数与磁盘一致
        c = mf.get("counts") or {}
        if c.get("nodes_total") not in (None, n):
            bad(f"manifest.nodes_total={c.get('nodes_total')} ≠ 磁盘 {n}")
        if c.get("dedup") not in (None, dedup):
            bad(f"manifest.dedup={c.get('dedup')} ≠ 磁盘 {dedup}")
        if "__error__" not in subs:
            sg_now = {g.get("key"): g.get("count") for g in (subs.get("groups") or [])}
            if c.get("groups") and c["groups"] != sg_now:
                bad("manifest.counts.groups 与磁盘订阅分组不一致")
            elif c.get("groups"):
                ok(f"manifest 订阅 {len(c['groups'])} 组计数与磁盘一致")
        if (c.get("picks") and "__error__" not in picks
                and c["picks"] != {k: len(v) for k, v in (picks.get("lists") or {}).items()}):
            bad("manifest.counts.picks 与磁盘榜单不一致")
        ok(f"manifest build_id={mf.get('build_id')}，口径节点 {c.get('nodes_total')} / "
           f"实测 {c.get('tested')} / 真实可用 {c.get('real_ok')}")

    print()
    if fails:
        print(f"结论：{len(fails)} 项不一致，{len(warns)} 项警告 → 拦下发布")
        return 1
    print(f"结论：全部一致" + (f"，{len(warns)} 项警告" if warns else "") + " → 允许发布")
    return 0


if __name__ == "__main__":
    sys.exit(main())
