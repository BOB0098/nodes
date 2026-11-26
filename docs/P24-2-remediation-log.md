# P24-2 整改记录

- **基线提交**：`126a76a64ea210820913686c1982fed0ebe33b6d`
- **工作分支**：`refactor/nodes-code-cleanup`；没有修改 `main`，没有创建 PR。
- **原则**：本记录只跟踪本分支的阶段性修复；不以跳过测试、重写哈希、伪造 ledger 或降低发布门禁的方式消除失败。

## 批次 A：run ledger 的 collect/render 产物归属

**原因**：CI 在 collect 已标记后，`collector.py --render` 会重建最终 `data/nodes.json` 和 `index.html`。此前 render 未声明拥有 `nodes.json`，台账因此把正常重建误判为产物被篡改。

**改动**：

- `run_ledger.py` 将最终 `data/nodes.json` 明确列入 render 产物；只增加一条白名单：`collect → render` 对 `data/nodes.json` 的覆写。
- 台账仍保留 collect 初稿和 render 最终版的阶段哈希；覆写仅在后续阶段成功、`run_id` 相同且当前哈希等于 render 已记录哈希时才获准。最终哈希校验、跨轮拦截及其他阶段文件保护不变。
- `collector.py`、README 和架构说明明确：render 是离线重建最终数据快照与页面，不只是重画 HTML；同时关闭 rerender 读取文件的句柄。
- 新增 `tests/test_p24_ledger_render.py` 并接入 `verify.sh`，覆盖授权覆写、最终哈希、未 mark、跨轮和未授权改动等行为。

**删除/合并及功能影响**：未删除或合并业务代码；没有改变节点结构、评分规则、订阅格式、页面布局或节点数据。只明确已有的最终快照写入语义和台账所有权。

## 批次 B：采集失败语义、页面 DOM 清空

**原因**：低计数保护虽保留旧 `nodes.json`，但原先正常返回，调用方可能继续将旧快照标记为本轮成功；本地 cron 包装器又吞掉 collector 的非零退出码。模板中另有四处纯清空 `innerHTML` 的 DOM 写入。

**改动**：

- `collector.py` 低计数保护改为 `SystemExit(2)`，并用上下文管理器读取旧快照。旧数据仍原样保留；失败状态会阻止 workflow 的 `collector && mark collect` 继续执行。
- `run_daily.sh` 现在在成功时写完成日志，在失败时记录实际退出码并原样返回；脚本仍使用原来的 cron 入口。
- `template.html` 四处 `element.innerHTML = ''` 改为 `replaceChildren()`，并通过项目的 `collector.py --render` 生成 `index.html`。新增模板生成一致性测试。
- 新增 `tests/test_collector_low_count.py` 与 `tests/test_run_daily_exit.py`，都在临时目录/替身命令中运行，不访问网络，也不改写正式数据；并接入 `verify.sh`。

**删除/合并及功能影响**：未删除或合并业务逻辑。低计数情形从“保留旧数据并报告成功”纠正为“保留旧数据并报告失败”；页面 DOM 清空操作保持原交互结果，不改变 UI、数据字段或嵌入数据。

## 验证结果

最新完整命令：

```bash
PATH=/workspace/p24-baseline/venv/bin:$PATH bash verify.sh
```

结果：**18 项通过、2 项失败**。本批新增的台账/render、低计数、cron 退出码、模板一致性测试均通过；安全、解析/评分/历史/治理/榜单/订阅、manifest 自测、隐私审计、YAML 与页面 JS 检查也通过。`python3 -m py_compile`、`bash -n run_daily.sh verify.sh`、`git diff --check` 通过；`data/nodes.json` 与基线无差异。

两项失败是 `check_consistency` 的 `index.html` manifest 哈希不一致，以及依赖该基线门禁的发布回滚演练。它们在本批开始前的基线验证中已经存在；本批模板修复按项目入口更新了 `index.html`，而没有伪造同轮 ledger 或手工重写 manifest，因此当前门禁仍会拦下发布。未绕过这些失败。

## 后续计划（记录当时的顺序）

原计划先为 `docker/run.sh` 建立隔离的阶段失败/停止顺序回归，再评估容器入口与正式 workflow 的失败语义和旧数据保护；该项随后记录于 `docs/P24-3-container-failure-safety.md`。之后仍按审计证据继续处理高风险项。任何门禁失败都在原因修复并通过完整验收前保留，不创建 PR、不发布、不合并 `main`。
