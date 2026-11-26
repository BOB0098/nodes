# P24-3 容器失败安全

- **前序提交**：`ac0914a1165ed76dae6cd7bad1bbc918c2fa2d5a`
- **工作分支**：`refactor/nodes-code-cleanup`；`main` 未修改，未创建 PR。
- **背景**：静态审计发现 `docker/run.sh` 把检测、评分、治理、榜单、订阅、重渲染设为软失败，和 GitHub workflow 的核心硬门禁不一致；容器又直接写宿主机挂载数据，失败后会留下混合快照。

## 改动与影响

- `docker/run.sh` 中所有 pipeline 步骤均成为硬门禁，不再接受 `must=0` 软失败参数。流程现在按同轮台账执行 `start → collect → detect → score → governance → picks → subs → render → check`，随后运行 `build_manifest.py --write`、当前产物隐私审计和一致性门禁。
- 新增 `docker/transaction.py`：容器启动业务阶段前，将整个 `data/`（保留且不回滚 `data/logs/`）、`index.html`、生成的来源质量/治理报告及 `export/` 保存到 `APP_ROOT` 外的临时目录。任何非零退出以及脚本可处理的 INT/TERM 都触发恢复；成功时删除快照并保留新产物；若恢复操作本身失败，会保留快照并报告位置。
- 对原有 `index.html` 使用原位文件复制恢复，不删除 Docker Compose 的单文件 bind mount；快照路径位于项目目录内会被拒绝，防止递归复制或误删。
- README 和架构说明同步更新。容器仍不运行 CI 的可选 `threshold_watch.py` 与全历史 Git 扫描/提交推送；这些差异已明确写出。`Dockerfile` 已有 `COPY docker/ ./docker/`，会包含新适配器，无需改镜像构建步骤。

**删除/合并及功能影响**：没有删除或合并业务模块，也未改变评分、节点、订阅或页面规则。移除了容器 `step()` 未使用的软失败开关，目的是让未来步骤不能意外降级门禁。容器的失败行为改为返回实际错误码并恢复旧产物；成功路径仍执行相同核心业务阶段。

## 回归与验收

- `tests/test_docker_transaction.py`：3 项通过，覆盖旧快照恢复、新产物清理、日志保留、已存在页面原位覆盖，以及备份路径安全。
- `tests/test_docker_run.py`：3 项通过，使用临时 `APP_ROOT` 和假 Python 阶段命令；覆盖检测失败后的停止与旧产物恢复、成功闭环，以及恢复器自身失败时保留快照并报告其位置。未执行真实采集、代理握手或 Docker daemon。
- 最新完整验证：`PATH=/workspace/p24-baseline/venv/bin:$PATH bash verify.sh`，**20 项通过、2 项失败**。两项失败仍是 `index.html` 与现存 manifest 哈希不一致，以及依赖该基线门禁的回滚演练；本阶段没有修改 `data/nodes.json` 或 `data/build_manifest.json`，也没有绕过门禁。
- `bash -n docker/run.sh verify.sh run_daily.sh`、`python3 -m py_compile`（容器模块和测试）、`git diff --check` 均通过。

## 已知边界与下一步

恢复发生在脚本检测到失败后，不是目录级原子切换；强制 `SIGKILL`、断电或恢复操作自身失败时仍需人工核查挂载数据。恢复自身失败时的快照留在容器临时目录；若外层运行器随后删除容器，快照也会随之消失，故不是持久备份。下一批先评估 Dockerfile 注释承诺的单步命令模式（当前入口仍未显式分派参数），确保诊断入口只接受白名单命令且不会绕过正常发布门禁；随后按审计处理检测时 DNS/私网地址的运行时防护。完整验收通过前不创建 PR、不发布、不合并 `main`。
