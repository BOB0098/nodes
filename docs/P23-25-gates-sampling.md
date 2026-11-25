# P23–P25 整改报告：失败门禁、回归覆盖与三类抽样

对应第二轮审计问题 1–5。每项含文件路径、代码变更、测试命令、真实结果。

## P23｜阶段失败门禁 + run_id 同轮构建证明（问题 1/2/3）

### 代码变更
- **新增 `run_ledger.py`**：同轮台账。`start(run_id)` 用 `GITHUB_RUN_ID` 初始化；`mark(stage)` 在脚本成功退出后记录该阶段产物 sha256；`check()` 校验 7 个必需阶段齐全、run_id 一致、产物哈希吻合。
- **改 `build_manifest.py`**：SCHEMA_VERSION 1→2；`build()` 把 pipeline（run_id/stages/complete/errors）纳入 manifest 和 build_id 摘要；`verify()` 校验 pipeline；main 改为**校验不通过就不写盘**（原先先写后报错）。
- **改 `check_consistency.py`**：新增第 10 项 pipeline 硬校验；无台账文件时降级警告（保证干净 clone 可复现），台账存在但不完整则硬拦下。
- **改 `daily.yml`**：6 个关键步骤去掉 `continue-on-error: true`（真实检测/评分/治理/榜单/订阅/重渲染），每步 `python3 xxx.py && python3 run_ledger.py mark <stage>`；采集前 `start`，manifest 前 `check`。仅缓存清理与失败通知保留 continue-on-error（非发布步骤）。
- **改 `restore.py`**：回滚白名单加入 `data/run_ledger.json`。
- **改 `verify.sh`**：加入 run_ledger 自测。

### 测试命令与真实结果
```
python3 run_ledger.py --selftest              → 6 项全过
场景A 漏 mark detect：check 退出码 1「阶段 detect 未标记成功」
场景B 检测产物被换：check 退出码 1「detect 的产物被改动」
场景C 台账不完整--write：退出码 1，manifest 未变（cmp 一致）
正常轮 start→7×mark→check→build→consistency：7/7 阶段，退出码 0
干净 clone 无台账：降级警告、允许发布（P22 可复现性保留）
```
后两者交叉验证：P27 改页面后哈希不符被拦，重新 mark render 才通过。

## P24｜核心模块回归覆盖（问题 4）

### 代码变更
- **改 `verify.sh`**：在安全自测后加入 nodecheck / nodescore / history / governance / picks / score 六个 `--selftest` 调用。

### 真实结果
```
verify.sh：15 项全过（P23 时）；P28 后 16 项全过
无任一 FAIL；6 个模块 selftest 退出码均 0（单独复测确认）
```
*注：P24 后新增 threshold_watch 使总数到 16。*

## P25｜三类轮换抽样（问题 5）

### 代码变更
- **改 `nodecheck.py`**：新增 `classify(n, rec)`——按真实历史分 new（tries=0）/retry（成功率<50%）/stable（≥3 次且≥80%）；`sample()` 新增 `history_records` 参数，同源配额 new60%/retry25%/stable15%，不足回流；**向后兼容**：只给 tries 时保持 P18 纯覆盖度优先。主流程改读 `history.load()['nodes']` 完整记录。
- selftest 新增 6 项：三类都被抽到、可复现、无历史归 new、多次成功归 stable、成功率<一半归 retry。

### 真实结果
```
python3 nodecheck.py --selftest → 全部通过（含 6 项新断言）
用真实 node_history.json 4133 条验证代码路径：
  分类 new 860 / retry 2899 / stable 374
  抽样 90 条：new 55 / retry 22 / stable 13（三类均命中）
```

### 无法验证（诚实标注）
含完整 fp 的 `export/nodes_full.json` 只在 CI artifact，本地已清理；`data/nodes.json` 是脱敏库（字段 name/proto/region/addr/src，无 fp），因此**无法用真实节点全量验证命中率**。上述用真实历史记录构造带 fp 的候选，验证的是分类代码路径正确，不是线上抽样覆盖率实测。
