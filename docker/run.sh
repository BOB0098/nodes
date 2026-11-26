#!/usr/bin/env bash
# 一轮完整采集。核心阶段顺序和发布门禁与 .github/workflows/daily.yml 对齐。
#
# 顺序不能换：
#   1 collector   采源、解析、去重
#   2 nodecheck   mihomo 真握手
#   3 nodescore   评分、地区、标签及历史
#   4 governance  重复源识别、逐源指标、新源观察期
#   5 picks       四类优选榜单
#   6 export_subs 生成各类订阅并校验
#   7 collector --render 生成最终 nodes.json 与页面
#   8 run ledger / manifest / 隐私 / 一致性发布门禁
#
# 任一核心步骤失败即停止，并把 data、页面、报告和订阅导出恢复为本轮前快照。
set -uo pipefail

APP_ROOT="${APP_ROOT:-/app}"
cd "$APP_ROOT" || exit 1
export NODES_FORCE_WRITE=1          # 本地/自建环境没有 GITHUB_ACTIONS 变量
export PYTHONUNBUFFERED=1

LOG_DIR="${LOG_DIR:-$APP_ROOT/data/logs}"
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

# 先备份实际可变产物；data/logs 保留运行日志，私密 export 只在本地权限受限的
# 临时目录中保存。Compose 将该目录放入持久 named volume，避免 --rm 删除失败快照。
# 快照失败时尚未运行任何业务阶段，因此可以安全中止。
BACKUP_ROOT="${NODES_BACKUP_DIR:-${TMPDIR:-/tmp}}"
mkdir -p "$BACKUP_ROOT" || exit 1
BACKUP_DIR=$(mktemp -d "$BACKUP_ROOT/nodes-output-backup.XXXXXX") || exit 1
if ! python3 docker/transaction.py snapshot "$APP_ROOT" "$BACKUP_DIR"; then
  rm -rf "$BACKUP_DIR"
  exit 1
fi
finish_run() {
  local rc=$?
  local cleanup_backup=1
  trap - EXIT
  if [ "$rc" -ne 0 ]; then
    echo "[$(date '+%F %T')] 本轮失败（rc=$rc），恢复运行前产物"
    if ! python3 docker/transaction.py restore "$APP_ROOT" "$BACKUP_DIR"; then
      echo "[$(date '+%F %T')] ✘ 产物恢复失败；请检查数据目录，快照保留于 $BACKUP_DIR"
      echo "恢复按逐项 best-effort 执行；不保证整个 bind-mount 树全局原子回滚。"
      echo "可在 Nodes 仓库目录手动重试恢复："
      printf '  docker compose run --rm --entrypoint python3 collect docker/transaction.py restore /app "%s"\n' "$BACKUP_DIR"
      cleanup_backup=0
      rc=1
    fi
  fi
  if [ "$cleanup_backup" -eq 1 ]; then
    rm -rf "$BACKUP_DIR"
  fi
  exit "$rc"
}
trap finish_run EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

step() {                            # step <名字> <命令...>；所有阶段都是硬门禁
  local name="$1"; shift
  local t0 rc
  t0=$(date +%s)
  echo "[$(date '+%F %T')] ▶ $name"
  # 先保存实际退出码，避免 if 语句的状态覆盖被测命令状态。
  "$@"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "[$(date '+%F %T')] ✔ $name，用时 $(( $(date +%s) - t0 ))s"
    return 0
  fi
  echo "[$(date '+%F %T')] ✘ $name 失败（rc=$rc），用时 $(( $(date +%s) - t0 ))s"
  echo "关键步骤失败，中止；退出时恢复运行前产物。"
  exit "$rc"
}

# 安全层先自测：这一层失败时，不允许采集或写入新产物。
step "安全边界自测" python3 netsafe.py
step "初始化同轮台账" python3 run_ledger.py start
step "采集+解析+去重" python3 collector.py
step "标记 collect" python3 run_ledger.py mark collect
step "真实握手检测" python3 nodecheck.py --speed
step "标记 detect" python3 run_ledger.py mark detect
step "评分与分类" python3 nodescore.py
step "标记 score" python3 run_ledger.py mark score
step "来源治理" python3 governance.py
step "标记 governance" python3 run_ledger.py mark governance
step "生成优选榜单" python3 picks.py
step "标记 picks" python3 run_ledger.py mark picks
step "生成订阅" python3 export_subs.py
step "标记 subs" python3 run_ledger.py mark subs
step "重渲染页面" python3 collector.py --render
step "标记 render" python3 run_ledger.py mark render
step "同轮构建校验" python3 run_ledger.py check
step "生成构建清单" python3 build_manifest.py --write
step "公开产物脱敏审计" python3 audit_privacy.py
step "一致性检查" python3 check_consistency.py

# 一行结论，方便从 cron 日志里直接看出这轮值不值得看
python3 - <<'PY'
import json
try:
    with open("data/nodes.json", encoding="utf-8") as f:
        d = json.load(f)
    s = d.get("stats") or {}
    q = (s.get("quarantine") or {}).get("total")
    print(f"入库 {s.get('dedup')} 条，真实可用 {s.get('real_ok')}/{s.get('real_tested')}"
          f"，隔离 {q if q is not None else '—'} 条")
except Exception as e:
    print(f"读不到汇总：{e}")
PY
