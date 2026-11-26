# P24-0 安全基线与验证清单

## 安全基线

- 基线提交：`126a76a64ea210820913686c1982fed0ebe33b6d`（2026-11-26 核查时 `main` 与重构分支指向同一提交）。
- 工作分支：`refactor/nodes-code-cleanup`；独立于 `main`，本工程不直接修改 `main`。
- 克隆时工作树干净；开始审计后唯一未跟踪项为 `docs/P24-1-code-cleanup-audit.md`。
- 恢复材料：`/workspace/p24-baseline/nodes-main-history.bundle` 与 `/workspace/p24-baseline/nodes-main-source-data.tar.gz`；SHA-256 清单：`/workspace/p24-baseline/SHA256SUMS`。
- 最近已知 Actions：`45683383645`（run #46）失败；用户提供的故障摘要显示 collect、detect、score、governance、picks、subs、render 成功，最终台账校验因 `data/nodes.json` 在 collect 标记后被 render 覆写而失败。

## 运行入口与自动化

- 页面源模板为 `template.html`，由 `collector.py` 生成根目录 `index.html`；页面和主要数据均为公开静态产物。
- 主采集入口：`python3 collector.py`；`--cache` 使用本地源缓存；`--render` 不联网，按磁盘节点快照与后续派生数据重渲染页面。
- 主自动化：`.github/workflows/daily.yml`，cron `17 */3 * * *`，并支持 `workflow_dispatch`。流程包括安全自测、初始化 run ledger、采集、真实 mihomo 检测、评分、来源治理、榜单、订阅、render、台账校验、manifest、隐私审计、一致性门禁及提交发布。
- 候选发现自动化：`.github/workflows/discover.yml`，cron `23 4 * * 1`，并支持手动触发；入口 `discover.py`，只更新候选队列，不自动修改正式 `sources.yaml`。
- 自建入口：Dockerfile 默认交给 `docker/run.sh` 执行完整流程；`docker-compose.yml` 提供容器运行方式；`run_daily.sh` 是本地 cron 包装脚本。
- 其他人工/诊断入口：`nodecheck.py`、`nodescore.py`、`governance.py`、`picks.py`、`export_subs.py`、`build_manifest.py`、`check_consistency.py`、`screen.py`、`discover.py`、`restore.py`、`drill.py`、`drill_publish.py`、`audit_privacy.py`、`bench.py`、`clienttest.py`、`history.py`、`lifecycle.py`、`netsafe.py`、`threshold_watch.py`。

## 运行环境与依赖

- 基线沙箱 Python：3.12.3；CI 与 Docker 固定 Python 3.11。
- 唯一声明的第三方依赖：`PyYAML>=6.0`（`requirements.txt`）；无其他声明依赖。
- 基线验证使用隔离环境 `/workspace/p24-baseline/venv`，未改写仓库依赖。

基线规模（只统计 `git ls-files` 中的源码；物理行，不含未跟踪审计报告）：160 个跟踪文件；25 个 Python 文件、10,462 行、354 个函数/方法、11 个类；`template.html` 1,032 行，其中内嵌 CSS 301 行、JavaScript 596 行；生成页面 `index.html` 1,032 行；requirements 中 1 个非注释依赖声明。HTML 总行数包含内嵌 CSS/JS，分类数字仅用于分项追踪，不相加。两份恢复备份均已通过 SHA-256 校验。

## 基线测试清单与结果

完整入口为 `PATH=/workspace/p24-baseline/venv/bin:$PATH bash verify.sh`；`verify.sh` 当前不带可执行权限，故通过 `bash` 显式调用，不修改权限或跳过测试。基线结果：14 项通过、2 项失败。

现有离线覆盖包括：

1. `netsafe.py` 安全边界自测（SSRF、重定向、大小/时间上限、脱敏、原子写）。
2. `nodecheck.py --selftest`、`nodescore.py --selftest`、`history.py --selftest`、`governance.py --selftest`、`picks.py --selftest`、`score.py --selftest`、`threshold_watch.py --selftest`。
3. `run_ledger.py --selftest` 与 `build_manifest.py --selftest`。
4. `export_subs.py --selftest`、`audit_privacy.py --git`、`check_consistency.py`。
5. 完整模式附加 `drill_publish.py`、daily workflow YAML 解析及 `index.html` JavaScript 语法检查；`--quick` 跳过演练和扩展检查。

两个已知失败均源自基线产物状态，而非本次改动：`check_consistency.py` 发现磁盘 `index.html` SHA-256 为 `0e2df98e442ce6f799357d01ac1d1ea8a8165636a9947a25ebd0d00bb9a561d1`，与已提交 `data/build_manifest.json` 中 `b3cb0688b7a0d10a307ee1cf54f3db71736ad7551c31a433eedd9057c181da48` 不符；`drill_publish.py` 因演练前基线门禁不能通过及恢复后门禁仍失败而报错。未重写 manifest 来掩盖该问题；`build_manifest.py` 要求同轮 ledger 证明，离线标记旧数据会伪造构建证明。

## P24 功能回归验收清单

重构必须保持以下行为及门禁：采集与去重；节点解析与隔离；真实代理握手；评分公式、阈值及排序；来源信誉与治理；历史和生命周期；榜单规则；Clash、sing-box、URI、Base64 订阅及反解；页面现有布局与搜索、筛选、排序、详情、复制、二维码交互；run_id 与阶段成功证明；最终文件 SHA-256；跨轮产物拦截；隐私审计、数据一致性、数量下降检查、失败阻止发布及上一版恢复能力。

P24 回归新增项将以离线临时目录实现，不访问真实代理或改写仓库正式数据；完整工作流仍须在分支上验证后才能申请 PR。
