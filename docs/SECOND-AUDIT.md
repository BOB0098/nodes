# P17–P22 第二轮独立工程审计报告（整改 P23–P28）

审计触发：用户要求对刚完成的 P17–P22 做第二轮独立审计，列出 10 个必须核实的问题，明确"不要直接宣称全部验收通过、先审计再整改、不未经验证就标记完成、等待最终验收"。

本报告是**独立验收材料**，不是完成声明。结论分三类：**通过 / 未通过 / 无法验证**。

## 一、审计前结论（10 项核实结果）

| # | 用户指出的问题 | 审计判定 | 证据 |
|---|---|---|---|
| 1 | 6 个关键步骤 `continue-on-error: true` | **属实** | daily.yml 第 100/110/120/125/132/141 行 |
| 2 | 无统一 run_id / 阶段依赖 | **属实** | 全仓无 run_id / stage_status 字段 |
| 3 | manifest 只证哈希不证执行成功 | **属实** | manifest 顶层 8 字段，无阶段成功证明 |
| 4 | verify.sh 缺核心模块回归 | **属实** | 6 个模块 selftest 存在但未被调用 |
| 5 | 抽样轮换不够 | **部分属实** | 有 tries 少优先，无新/稳定/失败三类轮换 |
| 6 | 客户端沿用旧测试 | **属实** | 二进制实际存在，本轮未测却沿用 P15 |
| 7 | 真机/暗色/对比度/触控 | **部分属实** | 沙箱有 Chromium 可测模拟；无 adb/实体手机 |
| 8 | 敏感审计不够深 | **属实** | 不解码 Base64、不查 URL 参数、不扫历史 |
| 9 | best 65 门槛缺评估 | **属实且更严重** | 代码根本没有 65 门槛，best 最低分 56.7 |
| 10 | 365/30 天历史当成已有 | **属实** | trend 仅 1 天；history 全为今天+迁移记录 |

## 二、整改提交（P23–P28，均已推送 main）

| 阶段 | 提交 | 针对问题 |
|---|---|---|
| P23 | `bed862a5` | 1/2/3：阶段失败门禁、run_id 台账、manifest 执行证明 |
| P24 | `23469b61` | 4：verify.sh 补齐 6 个核心模块回归 |
| P25 | `d55330a0` | 5：新/失败复核/稳定复核三类轮换抽样 |
| P26 | `9663eee6` | 6：客户端三内核本轮真测 + 台账离线降级修复 |
| P27 | `b6ed5e98` | 7/8：深度敏感审计（Base64/URL/git）+ 对比度与触控修复 |
| P28 | `67a82550` | 9/10：门槛持续观测 + 历史数据诚实化 |

每项整改的详细文件、代码变更、测试命令、真实结果见对应报告：`docs/P23-25-gates-sampling.md`（问题 1–5）、`docs/P26-clients.md`（问题 6）、`docs/P27-privacy-ui.md`（问题 7–8）、`docs/P28-threshold-history.md`（问题 9–10）。

## 三、最终验收状态

### 通过（有真实命令与输出证据）

| 项 | 证据 |
|---|---|
| 阶段失败不再冒充新轮 | 场景 A（漏 mark detect）、B（产物被替换）、C（台账不完整）均退出码 1 拦下；manifest 不完整时拒绝写盘（比对文件未变） |
| 同轮 run_id 贯穿 | `run_ledger.py check`：7/7 阶段、run_id 一致、产物哈希吻合；跨轮 run_id 被 selftest 拦下 |
| manifest 含执行证明 | manifest 新增 pipeline（run_id/stages/complete），build_id 摘要纳入；篡改 pipeline 被拦 |
| 核心模块回归 | verify.sh 16 项全过：netsafe/nodecheck/nodescore/history/governance/picks/score/threshold_watch/build_manifest/run_ledger/export_subs/audit_privacy/check_consistency/drill_publish/YAML/JS |
| 三类抽样轮换 | selftest 6 项；用**真实历史 4133 条**分类 new860/retry2899/stable374，抽样 90 条三类均命中 |
| 客户端三内核本轮真测 | Mihomo pass / sing-box pass / Xray pass（4 条 Configuration OK）；P15 历史报告未被覆盖（hash 与远端一致） |
| 深度敏感审计 | selftest 9 项；21 个公开产物 + git 全历史干净；注入 URI 被拦下 |
| 文字对比度 | WCAG AA 4.5:1：修复后 .sec-tag 浅 5.18 / 暗 5.57，其余 10.9–17.0 |
| 触控区 | 全部 ≥44px：搜索/下拉/开关/折叠条/复制按钮/二维码按钮 |
| 移动端无溢出 | 390px scrollWidth==clientWidth，console 无错 |
| 历史数据诚实化 | README 区分"策略上限"与"实际已积累"；trend 1 天、history 今日+迁移记录如实写明 |
| 门槛证据取得 | best 分数范围 56.7~90.1（65 门槛不存在）；≥65 仅 144/952（15.1%）；75.8% 只测 1 次 |

### 未通过 / 待持续观测（明确不宣称完成）

| 项 | 状态 | 原因 |
|---|---|---|
| 连续 7 天 best 门槛评估 | **未完成，不下结论** | 时间不可压缩；本会话只写入第 1 天观测。已建立 CI 每轮观测机制，满 7 天后用 `threshold_watch.py --report` 决策。**本轮不调整阈值** |
| best 65 门槛本身 | **未实现** | 审计发现代码从无此门槛；本轮不擅自加阈值（缺多轮数据），列为待评估 |
| 30 天 / 365 天历史曲线 | **无数据可验** | 当前 trend 仅 1 天、history 全为今日+迁移；需 CI 自然积累，不伪造 |

### 无法验证（环境限制，不冒充）

| 项 | 原因 |
|---|---|
| 真实安卓手机浏览器 / 触控 / 暗色 | 沙箱无 adb、无实体手机；Chromium 390px 模拟不冒充真机 |
| Hiddify GUI 免登录导入 | 无 GUI、无 CLI 免登录路径；内核同 sing-box 的逻辑等价不等于 GUI 实测 |
| Actions 运行日志内容 | 日志在 GitHub 侧不在仓库；提供 `--log` 参数供 CI 落盘后扫，本地不冒充已扫 |
| 真实节点客户端导入 | 含凭据的 export/nodes_full.json 只在 CI artifact；本轮用格式测试样本验证内核接受度，明确标注不证明节点可用性 |
| nodes.json 线上完整正文比对 | 1.5MB 下载受沙箱网速限制；已用远端 blob sha 一致替代，非完整正文 |

## 四、风险与回滚

- **风险**：P23 把 6 个关键步骤从 continue-on-error 改为硬失败，任何一步失败都会中断整轮、页面停在上一版。这是刻意选择——宁可不发布，也不允许旧数据冒充新轮。代价是检测环境抖动时更新会暂停，需人工看 `ci-failure` issue。
- **回滚**：数据用 `python3 restore.py list` / `to <sha>`；代码 revert 对应提交即可。`check_consistency.py` 在无台账/无 git 历史时自动降级警告，不会卡住旧环境。
- **待人工验收后动作**：满 7 天跑 `python3 threshold_watch.py --report` 决定是否调整 best 门槛；真机与 Hiddify GUI 在有设备环境补测。

## 五、结论（等待最终验收）

本轮整改解决了审计发现的全部 10 项问题中的 7 项"可在本会话完成"的整改（1–8、10 的文档诚实化），并取得问题 9 的真实证据。但**以下事项明确未通过或无法验证**：连续 7 天门槛评估、30/365 天历史曲线、真实安卓手机、Hiddify GUI、Actions 日志、真实节点客户端导入、线上 nodes.json 完整正文。

因此本报告**不宣称 P17–P28 全部验收通过**，提交以上材料等待最终验收。
