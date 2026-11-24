#!/usr/bin/env bash
# 一轮完整采集。和 .github/workflows/daily.yml 的顺序一一对应，
# 这样「CI 上跑出来的」和「自己机器上跑出来的」是同一件事。
#
# 顺序不能换：
#   1 collector   采源、解析、去重、写 data/nodes.json 与页面
#   2 nodecheck   mihomo 真握手，写 data/nodecheck.json
#   3 nodescore   打分、地区、标签，写 data/node_scores.json
#   4 collector --render  把 2、3 的结果读回来重渲染（不联网）
#   5 export_subs 生成各类订阅与校验
#
# 失败处理：2、3、5 失败不阻断后面（和 CI 的 continue-on-error 一致），
# 但 1 失败必须停下——采不到数据就往下走，后面的步骤会拿旧数据算出
# 一份看起来正常的结果，那是最危险的情况。
set -uo pipefail

cd /app || exit 1
export NODES_FORCE_WRITE=1          # 本地/自建环境没有 GITHUB_ACTIONS 变量
export PYTHONUNBUFFERED=1

LOG_DIR="${LOG_DIR:-/app/data/logs}"
mkdir -p "$LOG_DIR"

# 互斥：两轮重叠会互相覆盖 data/*.json，而且两边都在写 index.html
exec 9>"$LOG_DIR/.run.lock"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] 已有一轮在跑，本次跳过"
  exit 0
fi

# 新鲜度闸门：和 CI 里那段一样的道理，cron 迟到或手动补跑时挡住重复执行
if [ "${SKIP_GATE:-0}" != "1" ] && [ -f data/nodes.json ]; then
  age=$(( ( $(date +%s) - $(stat -c %Y data/nodes.json) ) / 60 ))
  if [ "$age" -lt "${MIN_AGE_MIN:-120}" ]; then
    echo "[$(date '+%F %T')] 数据 ${age} 分钟前刚更新，跳过（SKIP_GATE=1 可强制）"
    exit 0
  fi
fi

step() {                            # step <名字> <必须成功 1/0> <命令...>
  local name="$1" must="$2"; shift 2
  local t0; t0=$(date +%s)
  echo "[$(date '+%F %T')] ▶ $name"
  if "$@"; then
    echo "[$(date '+%F %T')] ✔ $name，用时 $(( $(date +%s) - t0 ))s"
    return 0
  fi
  local rc=$?
  echo "[$(date '+%F %T')] ✘ $name 失败（rc=$rc），用时 $(( $(date +%s) - t0 ))s"
  if [ "$must" = "1" ]; then
    echo "关键步骤失败，中止。上一轮数据保持不动。"
    exit "$rc"
  fi
  echo "非关键步骤，继续（本轮对应数据沿用上一轮）。"
  return 0
}

step "采集+解析+去重"  1 python3 collector.py
step "真实握手检测"    0 python3 nodecheck.py --speed
step "评分与分类"      0 python3 nodescore.py
step "重渲染页面"      0 python3 collector.py --render
step "生成订阅"        0 python3 export_subs.py

# 一行结论，方便从 cron 日志里直接看出这轮值不值得看
python3 - <<'PY'
import json, os
try:
    d = json.load(open("data/nodes.json", encoding="utf-8"))
    s = d.get("stats") or {}
    q = (s.get("quarantine") or {}).get("total")
    print(f"入库 {s.get('dedup')} 条，真实可用 {s.get('real_ok')}/{s.get('real_tested')}"
          f"，隔离 {q if q is not None else '—'} 条")
except Exception as e:
    print(f"读不到汇总：{e}")
PY
