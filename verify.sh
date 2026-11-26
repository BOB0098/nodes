#!/usr/bin/env bash
# P22：一键本地验证——干净 clone 后一条命令复现主要构建与测试。
#
# 覆盖发布门槛要求的全部离线检查：出网安全自测、构建清单自测、订阅反解
# 自测、一致性+数量门禁、发布回滚演练、工作流 YAML 语法。全部离线、不改
# 仓库（drill_publish 自己在临时目录里篡改再恢复）、不需要节点凭据。
#
# 用法：
#   bash verify.sh              # 跑全部，任一失败则退出码 1
#   bash verify.sh --quick      # 只跑秒级自测，跳过演练
#   python3 -m pip install -r requirements.txt   # 首次需装 PyYAML
#
# 与 CI 的关系：daily.yml 每一步都在这里有一一对应项；本地跑通再去动
# 工作流，避免「本地没验证、推上去才发现门禁挂了」。

set -u
cd "$(dirname "$0")" || exit 1

PY="${PY:-python3}"
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

PASS=0
FAIL=0

run() {
  local label="$1"; shift
  printf '\n── %s ' "$label"
  printf '%.0s─' $(seq 1 $(( 60 - ${#label} )))
  printf '\n'
  if "$@"; then
    PASS=$((PASS + 1))
    echo "  [PASS] $label"
  else
    FAIL=$((FAIL + 1))
    echo "  [FAIL] $label (退出码 $?)"
  fi
}

# 0. 解释器与依赖
printf '一键验证（P22 发布门槛）  python=%s\n' "$("$PY" --version 2>&1)"
if ! "$PY" -c 'import yaml' 2>/dev/null; then
  echo "缺少 PyYAML，请先运行：python3 -m pip install -r requirements.txt"
  exit 2
fi

# 1. 出网安全边界：SSRF/DNS 私网/重定向/大小/超时/脱敏/原子写（离线自测）
run "安全边界自测 (netsafe)" "$PY" netsafe.py

# 1b. P24：核心模块回归——这些 selftest 此前存在但 verify.sh 没调用，
#     改动核心逻辑时不会被一键验证发现。全部离线、不写数据、不联网。
run "真检测逻辑回归 (nodecheck)" "$PY" nodecheck.py --selftest
run "评分逻辑回归 (nodescore)" "$PY" nodescore.py --selftest
run "历史压缩回归 (history)" "$PY" history.py --selftest
run "来源治理回归 (governance)" "$PY" governance.py --selftest
run "优选榜单回归 (picks)" "$PY" picks.py --selftest
run "源评分回归 (score)" "$PY" score.py --selftest
run "门槛观测回归 (threshold_watch)" "$PY" threshold_watch.py --selftest

# 2a. 同轮构建台账自测：run_id、阶段成功标记、跨轮与篡改拦截
run "同轮台账自测 (run_ledger)" "$PY" run_ledger.py --selftest
run "台账/渲染产物归属回归 (P24)" "$PY" tests/test_p24_ledger_render.py
run "容器事务快照恢复回归 (docker)" "$PY" tests/test_docker_transaction.py
run "容器入口失败回滚回归 (docker)" "$PY" tests/test_docker_run.py
run "低计数旧数据保护回归 (collector)" "$PY" tests/test_collector_low_count.py
run "本地 cron 失败码传播回归 (run_daily)" "$PY" tests/test_run_daily_exit.py
run "模板/生成页一致性回归 (template)" "$PY" tests/test_template_build_output.py

# 2b. 统一构建清单自测：时间规范化、sha256、计数、pipeline 证明
run "构建清单自测 (build_manifest)" "$PY" build_manifest.py --selftest

# 3. 订阅生成/反解自测：四种格式能被自己读回来
run "订阅反解自测 (export_subs)" "$PY" export_subs.py --selftest

# 4. 公开产物脱敏审计：data/*.json 与页面里不许有 URI/凭据/令牌；带 --git 扫历史
run "脱敏审计 (audit_privacy)" "$PY" audit_privacy.py --git

# 5. 一致性 + 数量异常下降 + manifest 哈希硬校验（无 manifest 会降级警告）
run "一致性/数量门禁 (check_consistency)" "$PY" check_consistency.py

if [ "$QUICK" -eq 0 ]; then
  # 5. 发布失败回滚演练：篡改产物→拦下→恢复→放行，证明上一版不被覆盖
  run "发布回滚演练 (drill_publish)" "$PY" drill_publish.py

  # 6. 工作流 YAML 语法
  run "工作流语法 (daily.yml)" "$PY" -c \
    "import yaml; yaml.safe_load(open('.github/workflows/daily.yml'))"

  # 7. 页面 JS 语法（node 可用时；没有不判失败，CI 不依赖 node）
  if command -v node >/dev/null 2>&1; then
    "$PY" - <<'PY'
import re
h = open('index.html', encoding='utf-8').read()
m = re.search(r'<script>(.*?)</script>', h, re.S)
open('/tmp/nodes-verify.js', 'w', encoding='utf-8').write(m.group(1) if m else '')
PY
    run "页面 JS 语法 (index.html)" node --check /tmp/nodes-verify.js
  fi
fi

printf '\n════════════════════════════════════════════════════════════\n'
echo "结果：$PASS 项通过，$FAIL 项失败"
if [ "$FAIL" -gt 0 ]; then
  echo "→ 发布门槛未通过，禁止提交/发布"
  exit 1
fi
echo "→ 发布门槛通过"
exit 0
