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

## 批次 A/B 验证结果（历史）

最新完整命令：

```bash
PATH=/workspace/p24-baseline/venv/bin:$PATH bash verify.sh
```

结果：**18 项通过、2 项失败**。本批新增的台账/render、低计数、cron 退出码、模板一致性测试均通过；安全、解析/评分/历史/治理/榜单/订阅、manifest 自测、隐私审计、YAML 与页面 JS 检查也通过。`python3 -m py_compile`、`bash -n run_daily.sh verify.sh`、`git diff --check` 通过；`data/nodes.json` 与基线无差异。

两项失败是 `check_consistency` 的 `index.html` manifest 哈希不一致，以及依赖该基线门禁的发布回滚演练。它们在本批开始前的基线验证中已经存在；本批模板修复按项目入口更新了 `index.html`，而没有伪造同轮 ledger 或手工重写 manifest，因此当前门禁仍会拦下发布。未绕过这些失败。

## 批次 C：C4 请求总预算与 C6 URL/日志脱敏

**原因**：`netsafe.open_url()` 已覆盖初始检查、建连、重定向和 body 读取，但审计发现 collector 探活/HEAD、discover 内容嗅探、governance 审核拉取与 screen fetch 仍直接打开 opener，建连耗时不计入总预算。另有长纯字母/单数字路径 token、普通 query 参数中的已知 token/高熵值，以及异常文本里的完整 URL 可能泄漏。

**改动**：

- 在 `netsafe.py` 增加无共享可变状态的 `open_response()` 上下文管理器和 deadline response；每个 Request 持有自己的绝对 deadline，GET/HEAD、响应头访问和分段读取沿用同一时限。`open_url()` 复用该 helper；重定向仍逐跳校验，并关闭中间响应而不无界 drain。
- 对照 Python 3.12 `urllib.request.HTTPRedirectHandler`：保留 301/302/303/307/308 处理、标准 `redirect_request()` 方法转换和循环计数；`OpenerDirector.open()` 会先设置 `req.timeout`，剩余连接预算继续沿这条标准路径传递。唯一有意的读取差异是关闭未使用的跳转响应，不再无界 `fp.read()`。
- 将 `collector.http_probe`、HEAD 元数据、`discover.looks_like_subscription`、`governance.open_source`、`screen.fetch` 和 `discover.api` 迁入统一请求入口；保留 HEAD、原请求头、状态码、响应头、512 字节探活、sniff/read 上限及既有返回字段。
- C6 对长路径片段、普通参数和 fragment 使用已知 token/UUID 形状或长度、字符多样性、Shannon 熵判定；异常文本中的 HTTP(S) URL 也经 `mask_url()`。`screen.py` 的 JSON/Markdown 报告和 discover stderr 均在输出边界脱敏；Markdown 每条记录独立计算理由，并用上下文管理器关闭报告文件。新增 `tests/test_netsafe_redaction.py`、`tests/test_redaction_output_boundaries.py` 并接入 `verify.sh`；保留普通长路径可读性的反误伤测试。
- RED/GREEN 证据：迁移前新增的五条直连调用慢建连回归按预期失败；迁移后 C4 专项 12/12 通过。C6 首轮泄漏回归先失败、修复后 6/6 通过；后续多记录理由归属和 tracked JSON 落盘用例也先复现失败，修复后脱敏定向套件 **14/14 通过**。

**DNS 时限边界**：Python 标准库同步 `socket.getaddrinfo()` 无法通过 socket timeout 强制取消。实现会在 DNS 返回后检查绝对 deadline，并拒绝迟到结果、不给后续建连留超预算时间；但 resolver 阻塞期间本身不能被强行中断，因此不宣称 DNS 阶段具有可取消的硬墙钟上限。

**删除/合并及功能影响**：未改动节点数据、评分、订阅格式、页面 UI 或自动化阶段顺序；合并的是重复 HTTP 请求入口，不是业务结果。没有真实采集或代理检测；不再发现应用模块绕过 `netsafe` 的 `opener().open`/`urlopen` 调用。所有改动仍只在 `refactor/nodes-code-cleanup` 未提交工作树中，未触碰 `main`。

**本批验证**：48 项 unittest 全通过；`netsafe.py` 安全自测通过；变更 Python 文件 `py_compile`、`bash -n verify.sh run_daily.sh docker/run.sh` 与 `git diff --check` 均通过。最新完整命令 `PATH=/workspace/p24-baseline/venv/bin:$PATH bash verify.sh` 为 **25 项通过、2 项失败**：仍是基线已有的 `index.html` 与 manifest SHA256 不一致，以及依赖该失败的 `drill_publish` 回滚演练无法通过前置/恢复基线门禁。未跳过或修改这些门禁、未重写哈希；`verify.sh` 现有失败码展示缺陷会把这两项的括号退出码打印为 0，但总体命令仍以非零退出。

## C5 mihomo 来源真实性：未验收

- 官方 Release API 当前确认 `mihomo-linux-amd64-compatible-v1.19.34.gz` 大小为 `22766457` 字节，API digest 为 `sha256:2763f5b414527f7baf6dc57e3f82f3ab561eedf34bfd7c2f2cde1f7e592efe06`，与本地固定值一致。
- 该 digest 是 GitHub Release API 的官方资产元数据，不是发布者签名或独立信任根。Release 资产列表没有单独 checksum/signature 附件；按 digest 查询 attestation 的未认证请求返回 404，但 GitHub 文档说明该查询需要仓库读取权限认证，因此不能据此断言不存在 attestation。来源、查询 URL 和解释已补记于 `/workspace/P24-external-sources.md`。
- C5 仍未闭环：尚未找到可验证且 signer 身份可信的独立签名/attestation。代码风险已作隔离缓解：下载与安装使用唯一临时文件，缓存校验/安装由 `flock` 串行化，启动时通过已验证且由子进程继承的 `/proc/self/fd/...` 描述符执行，避免校验后按可变路径替换；`clienttest.py` 也走同一安全执行器。`tests/test_mihomo_integrity.py` 覆盖并发临时文件、锁互斥、描述符继承和路径替换，专项 **10/10 通过**。这些措施不能证明上游发布者身份；在没有独立信任根前，C5 仍阻断验收。

## 批次 D：C1-C3 回归补齐、候选 URL 边界与验证入口修正

- C1 端点专项测试改为 mock `nodecheck.popen_mihomo()` 高层安全启动包装器，保留 `run_batch()` 的真实 DNS/公私网判断与配置固定路径；不再让 C5 的真实缓存校验意外触碰工作区外二进制。环回、云元数据、IPv4-mapped、混合 DNS、DNS 失败及 SNI 保留测试 **12/12 通过**。
- C2/C3 增加 `probe()` 捕获 `subprocess.TimeoutExpired` 的离线断言，确认返回 `timeout:超时`；连同仅 204 成功及 bench 按阶段前缀统计超时/失败的契约，共 **3/3 通过**。
- 修复 `verify.sh` `run()` 失败分支先增加计数再读取 `$?` 的问题：现在先捕获实际退出码。新增回归用替身命令返回 7，实证输出为退出码 7，成功路径仍返回 0，**2/2 通过**。完整验证现同时解析 `.github/workflows/*.yml`（包含 `discover.yml`），并运行 `audit_privacy.py --selftest`。
- 候选 URL 不直接脱敏或改写为不可抓取的内容：`screen.py` 需要原始 raw URL 复核。新增 `build_candidate_url()` 校验 repo 形状并逐段 URL 编码 branch/path，避免文件名中的 `?` 或 `#` 被解释成 query/fragment；常规队列 URL 保持不变，审核仍可按原文件读取。专项 **3/3 通过**；没有改动 `data/candidates.json`。
- README 已同步扩展后的验证覆盖说明。全量 unittest **68/68 通过**；Python 编译、`bash -n verify.sh run_daily.sh docker/run.sh` 与 `git diff --check` 通过。
- 最新完整 `PATH=/workspace/p24-baseline/venv/bin:$PATH bash verify.sh` 为 **29 项通过、2 项失败**。`check_consistency.py` 仍发现现存 `index.html` SHA-256 `07f7fc82412b6fa622c7288806b9dd357dc78110ca2621ca37032f19179f039d` 与 `data/build_manifest.json` 中 `index_html.sha256=b3cb0688b7a0d10a307ee1cf54f3db71736ad7551c31a433eedd9057c181da48` 不符；`drill_publish.py` 因演练前与恢复后的基线门禁均被该差异阻断而失败。失败码已能准确显示为 1。未跳过门禁、未伪造 ledger/manifest 或哈希，也未重写正式数据；这两项未解除前不申请 PR、不合并、不发布。

## 后续计划（记录当时的顺序）

原计划先为 `docker/run.sh` 建立隔离的阶段失败/停止顺序回归，再评估容器入口与正式 workflow 的失败语义和旧数据保护；该项随后记录于 `docs/P24-3-container-failure-safety.md`。之后仍按审计证据继续处理高风险项。任何门禁失败都在原因修复并通过完整验收前保留，不创建 PR、不发布、不合并 `main`。


## 批次 E：manifest / 发布回滚基线生命周期调查（2026-11-26）

### 根因与正确生命周期

- 当前分支 HEAD 为 `d80ab32`。工作区中的 `index.html` SHA-256 为 `07f7fc82412b6fa622c7288806b9dd357dc78110ca2621ca37032f19179f039d`、大小 66,861 bytes；已提交 `data/build_manifest.json` 仍记录 `index_html.sha256=b3cb0688b7a0d10a307ee1cf54f3db71736ad7551c31a433eedd9057c181da48`、大小 82,826 bytes。
- Git 历史确认：`126a76a`（P23b）按新模板执行 `collector.py --render` 并提交了新 `index.html`，但没有在同一构建后更新 manifest；`origin/main` 上的旧页面（SHA-256 `0e2df98e442ce6f799357d01ac1d1ea8a8165636a9947a25ebd0d00bb9a561d1`）也与同一旧 manifest 不符。故本地门禁失败是继承的生成产物基线过期，不是通过改哈希可以安全修复的发布流程问题。
- 当前 `.github/workflows/daily.yml` 的合法顺序为：`start` → collect/mark → detect/mark → score/mark → governance/mark → picks/mark → subs/mark → render/mark → `run_ledger.py check` → `build_manifest.py --write` → 隐私审计 → `check_consistency.py` → 提交。`build_manifest.py` 写入前本身也调用 `verify()`，要求当前完整 run ledger 通过；离线缺少同轮台账时会拒绝写入。
- `drill_publish.py` 在演练前与恢复后都调用同一个严格 `check_consistency.py`。当前基线非法，因此两次失败是预期保护行为，不是独立的回滚实现缺陷。没有手动改哈希、伪造阶段标记、跳过/放宽门禁或改写生成数据。
- 只读检查 [最近的 GitHub Actions 运行](https://github.com/BOB0098/nodes/actions/runs/45683383645)：失败运行的 `head_sha=126a76a`，在步骤 17“同轮构建校验”失败；步骤 18“生成统一构建清单”被跳过。它运行的是尚未包含 `d80ab32` 修复的 main 快照，不能作为当前分支修复已在 CI 验证的证据。没有触发工作流。

### C5 Mihomo 来源证明复核

- [v1.19.34 Release API](https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/v1.19.34) 确认目标 gzip 资产为 22,766,457 bytes、digest `sha256:2763f5b414527f7baf6dc57e3f82f3ab561eedf34bfd7c2f2cde1f7e592efe06`，与代码固定值吻合；该 API digest 仅验证下载内容与 GitHub Release 元数据一致，不独立证明发布者身份。
- 对 127 个正式 release asset 名称筛查，没有找到独立 checksum、签名、attestation 或 provenance 文件。[v1.19.34 上游 build workflow](https://github.com/MetaCubeX/mihomo/blob/v1.19.34/.github/workflows/build.yml) 的正式 `Upload-Release` 路径未生成 attestation/签名；checksum 计算步骤只在 `Upload-Prerelease` 路径出现。
- 按 [GitHub repository attestations API](https://docs.github.com/en/rest/repos/attestations) 查询目标 digest，本次未认证请求返回 404；官方文档要求调用者对仓库有读取权限，并要求对 attestation 的签名、时间戳及 signer 身份作密码学验证。当前执行环境的 `gh auth status` 显示未登录，所以此 404 不能证明 attestation 不存在，也无法完成可信身份验证。上游构建流程及文件列表未显示可独立验证的证明材料。
- C5 仍为未验收限制；临时文件、锁和已校验文件描述符的运行时缓解及 10/10 专项测试不等于供应链来源认证。没有建立或假设新的信任根。

### 本轮验证与处置

- `PATH=/workspace/p24-baseline/venv/bin:$PATH bash verify.sh`：**29 项通过、2 项失败**。唯一失败项仍为 `check_consistency` 的 `index.html` 哈希不符，以及依赖有效基线的 `drill_publish`。
- `PYTHONPATH=. python3 -m unittest discover -s tests -v`：**68/68 通过**；变更 Python 文件 `py_compile`（16 个文件）、`bash -n verify.sh run_daily.sh docker/run.sh`、`git diff --check` 全通过。
- 本批调查未改动 `index.html`、`data/build_manifest.json` 或其他业务产物；现有未提交 C1–C6 代码和测试保持原样。未创建 PR、未合并、未发布。完整验收仍被过期生成物基线及 C5 独立来源验证阻断。


## 批次 F：governance 逻辑 run 轮次幂等（2026-11-26）

**根因与身份规则**：`per_source()` 原先每次执行都追加 `rounds` 并递增 `rounds_seen`。`sources_stats.checked_at` 只有分钟精度，因此它不能区分同一分钟的不同 workflow run，也不能可靠证明两次调用属于同一轮。本批选择 governance 自身的逻辑执行身份：CI 使用 `GITHUB_RUN_ID`，本地可用 `NODES_RUN_ID` 显式指定；不沿用 threshold_watch 的输入指纹规则。

**改动**：

- 同一 run ID 重跑时替换该轮当前观测，不重复增加 `rounds_seen`；不同 run ID 即使 `checked_at` 相同仍分别计轮。非相邻重跑也按 run ID 更新。
- 为每个源保留已处理的 run ID 索引，因此某轮已从最近 30 轮窗口裁出后再重跑，也不会被重新追加或累计。无显式 run ID 的本地调用保持每次调用一轮。
- 对旧的无 `run_id` 历史不按时间戳回配、不自动折叠重复行；其现有 `rounds_seen` 原样继承。历史重复轮次身份不明，不做追溯修正。
- 新增 6 条 `tests/test_governance_idempotency.py`，覆盖同轮重复/输入更新、非相邻重跑、同时间戳不同 run、30 轮窗口外重跑及旧历史保留；接入 `verify.sh`。governance 自测使用显式不同 run ID 推进观察期。

**验证与边界**：全量 unittest **129/129 通过**；`GITHUB_RUN_ID=424242 python3 governance.py --selftest` 通过；`GITHUB_RUN_ID=424242 bash verify.sh --quick` 为 **36 项通过、1 项失败**，唯一失败仍是已知的 `index.html` 与 `data/build_manifest.json` 中 `index_html.sha256` 不一致。本批未改动 `index.html`、`data/build_manifest.json`、`data/source_governance.json`、`docs/P19-governance.md` 或 `data/run_ledger.json`；未提交、未发布。`git diff --check` 通过。


## 批次 G：history 日内 P50 累计（2026-11-26）

**根因与契约**：`nodescore.py` 每轮将原始成功延迟样本 `ms_all` 传给 `history.trend_add()`；后者此前直接把当天 `p50` 设为本轮中位数，因而同日后续 run 覆盖前轮。P18 的 trend 契约是日级聚合，P24-1 E1 也明确要求按累计原始样本计算，不能对各轮 P50 再取中位数。

**改动与兼容边界**：

- 当前日期行保存延迟值频次表，并按全部成功延迟样本重算精确偏小中位数；新一天开始时保留旧日最终 P50、丢弃旧频次表，避免趋势历史因保留原始样本而膨胀。
- 旧版只有 `p50`、没有原始样本的行无法精确合并；保持原值，不伪造样本、不回填或改写正式 `trend.json`。节点历史迁移、生命周期字段与语义均未改动。
- 新增 `tests/test_history_daily_p50.py` 6 项，覆盖原始样本累计（含不同于最后一轮及轮次 P50 中位数的期望）、JSON 往返、空样本、跨日清理、旧行保真与 365 天窗口；接入 `verify.sh`。先观察到旧实现返回 110、而全日合并样本应为 30，修复后专项 6/6 通过。

**验证与边界**：全量 unittest **140/140 通过**；`bash verify.sh --quick` 为 **38 项通过、1 项失败**，唯一失败仍为已知的 `index.html` 与 manifest `index_html.sha256` 不一致。变更文件编译、`bash -n verify.sh` 与 `git diff --check` 通过。未改写 `data/trend.json`、`data/node_history.json` 或 lifecycle 数据；未提交、未发布。


## 批次 H：PyYAML 最低版本安全收紧（2026-11-26）

**独立安全评估**：项目需要 PyYAML 来解析外部 Clash YAML、内部 `sources.yaml` 并生成订阅，因此不移除这项唯一的第三方运行时依赖。PyYAML 上游 [issue #897](https://github.com/yaml/pyyaml/issues/897) 报告 merge-key（`<<`）与 anchors/aliases 组合可导致解析期间 CPU 和内存呈指数级扩展；对应修复已在 [pull request #937](https://github.com/yaml/pyyaml/pull/937) 合入。原约束 `PyYAML>=6.0` 仍允许 6.0.0–6.0.3，因此只将最低版本收紧为 `PyYAML>=6.0.4`，排除该已知修复前范围。没有升级其他依赖，也没有修改应用代码或运行行为。

**Python 3.11 兼容性与验证**：`.github/workflows/daily.yml`、`.github/workflows/discover.yml` 均通过 `setup-python` 使用 Python 3.11 并从 `requirements.txt` 安装；`Dockerfile` 基于 `python:3.11-slim-bookworm`，同样安装该文件。隔离虚拟环境中使用 Python 3.11.16 执行该依赖安装，解析并安装到 PyYAML 6.0.4，`uv pip check` 通过。完整 unittest 为 **149/149 通过**。`bash verify.sh --quick` 为 **39 项通过、1 项失败**；唯一失败是 `check_consistency` 报告现有 `index.html` 与 `data/build_manifest.json` 记录的 SHA-256 不一致。该基线问题保持原样，未跳过或绕过门禁。当前执行器没有 Docker CLI，因此没有实际构建镜像；Dockerfile 的 3.11 基线及统一依赖安装路径已静态核对，并由 Python 3.11 隔离安装验证。
