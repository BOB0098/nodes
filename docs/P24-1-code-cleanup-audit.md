# P24-1 代码清理审计报告

- **基线 commit**：`126a76a64ea210820913686c1982fed0ebe33b6d`
- **分支**：`refactor/nodes-code-cleanup`
- **审计模式**：只读静态审计（源码、AST、grep、CLI/workflow/产物调用链及离线纯函数样例）；未修改代码。
- **审计结论范围**：不把“暂未搜到引用”直接视为可删除；动态 CLI、workflow、Docker 入口、selftest 和生成产物引用均按保守原则处理。

## 一、执行摘要

本轮共汇总 **49 项发现**。最高优先级是 `run_ledger` 的产物所有权与 `collector --render` 双写冲突：collect 阶段标记 `data/nodes.json` 后，render 阶段又覆盖同一文件，但台账只把该文件归入 collect，随后 `run_ledger check` 会把正常 render 识别为 collect 产物被改动。该问题会直接阻断或污染发布门禁，应优先确定 `nodes.json` 的唯一写入阶段和最终哈希语义。

其他高价值问题包括：容器入口在关键阶段失败后仍继续并混用本轮/上一轮产物；采集低计数保护路径零退出却仍被标记成功；真实代理检测缺少运行时 DNS/私网防护；导出订阅继承检测配置而默认关闭 TLS 证书校验；页面数据中心违反单文件离线承诺；候选榜单允许本轮失败节点进入 lowlat/fast；来源配置校验、重复 URL 索引、原子写入和治理/历史幂等性存在缺陷。

明确可以安全删除的内容仅有：`Dockerfile` 中与实际入口不符的单步调用示例/相关错误契约（但应以显式参数分派替代，而非简单删掉安全门禁）、`run_daily.sh` 中吞掉失败码的错误行为（应改为保留真实退出码，而不是删除脚本），以及一组确认未使用的顶层导入。其余重复实现均为“暂不删除/待验证”，需先设计兼容公共接口并补回归。

## 二、优先级 P0：run_ledger collect/render 数据所有权故障

### 1. render 阶段覆盖 collect 已标记的 `nodes.json`（确定性故障）

- **文件/位置**：`collector.py:780-834`（`rerender`，尤其 783、824-826、833）；`collector.py:765-777`（`build_html`）；`collector.py:866-869`；`.github/workflows/daily.yml:92,153,160`；`run_ledger.py:43-51,104-123,137-150`。
- **问题**：`rerender()` 先读取 `data/nodes.json`，再通过 `assemble()` 读取当前 `node_scores.json`、`subscriptions.json`、`quarantine.json` 等重新组装数据，最后执行 `wjson(.../nodes.json, data, 1)`。这与函数注释、CLI 帮助和 README 所说的“`--render` 只按 `data/nodes.json` 重渲染页面、不联网”不一致。workflow 在 collect 后 mark collect，随后执行 `collector.py --render` 并只 mark render；`STAGE_FILES` 将 `nodes.json` 仅归 collect，故 render 后的当前哈希与 collect 标记哈希不一致，`run_ledger check` 可报“标记后又被覆盖”。
- **证据**：`collector.py` 的 rerender 写入行 833；workflow 的 collect mark 在 92 行、render 在 153 行、check 在 160 行；`run_ledger.STAGES` 在 44-51 行只将 `nodes.json` 归 collect。
- **安全删除判断**：**不安全删除**（`no`）。不能只删 line 833，否则页面与 `nodes.json` 的最新检测汇总/评分可能分裂；应先决定 render 是纯页面预览，还是“重建页面数据快照”。
- **建议合并**：是。统一产物所有权、台账阶段和 CLI 契约。
- **预期收益**：让 workflow 正常到达 manifest/一致性门禁，避免 UI 预览意外改变节点数据、时间/统计/字段及发布差异。
- **风险**：删除写回会造成页面与数据快照暂时不一致；保留写回则必须将文档改为“重建页面数据快照”，并让 render 显式拥有该产物或在最终版本重新记录哈希。
- **回归测试**：临时目录模拟 `start → mark collect → render → mark render → check`；断言修复后最终 `nodes.json`、`index.html` 的哈希与 manifest 对应且台账通过。另测纯 `--render` 只改变声明允许的产物，不写 `status.json`、`health.json`、`source_keys.json`、`quarantine.json`、`export/`。

### 2. 采集低计数保护路径零退出，旧产物被误记为本轮成功

- **文件/位置**：`collector.py:874-883`；`.github/workflows/daily.yml:92`；`run_ledger.py:104-123`。
- **问题**：当采集条数低于旧数据 60% 时，collector 打印“保留旧数据”后直接 `return`，退出码为 0；workflow 因而继续 `mark collect`。`mark()` 只检查文件存在并计算当前哈希，不检查文件是否由本轮生成、`updated/ts/run_id` 是否变化，于是上一轮的 `nodes.json/status.json` 被当成本轮 collect 成功产物。
- **安全删除判断**：`no`；**建议合并**：是。
- **收益**：避免旧产物冒充本轮执行结果，保持 run_id/阶段证明真实。
- **风险**：改为非零会改变“保留上一轮数据”的本地调用行为，但这是发布安全所需；需核对 cron/workflow 入口兼容性。
- **回归测试**：mock 低计数分支，断言退出非零、shell `collector && mark` 不调用 mark；正常路径仍可 mark，旧文件不被误记。

### 3. 无 ledger 时一致性检查降级为 warning，跨轮 manifest 可能放行

- **文件/位置**：`check_consistency.py:256-280`；`build_manifest.py:241-253`。
- **问题**：无 `run_ledger.json` 时 `check_consistency` 警告并跳过当前台账校验，只要 manifest 有 `pipeline.run_id/complete` 仍可继续；`build_manifest.verify()` 对 ledger 缺失则直接失败。已有 `data/build_manifest.json` 含 `pipeline.run_id=p23-final`，当前工作树无 ledger，说明路径可触发。
- **安全删除判断**：**暂不删除/待验证**（`uncertain`）。
- **建议合并**：是。统一发布模式与离线只读模式的策略。
- **收益**：发布入口不再放行无法证明属于当前磁盘产物的 manifest。
- **风险**：无条件改为 failure 会阻断历史数据/干净 clone 的离线检查；应提供显式 `--offline-allow-missing-ledger`。
- **回归测试**：完整 manifest + 缺 ledger + 跨轮 run_id：发布模式失败，显式离线兼容模式才 warning；存在但 run_id 不同或阶段缺失时两入口均失败。

### 4. run_ledger 相关测试缺口

- **文件/位置**：`run_ledger.py:153-197`、`build_manifest.py:257-324`、`check_consistency.py:106-348`、`verify.sh:50-77`。
- **问题**：现有 selftest 覆盖静态 mark、哈希篡改和 run_id 字段篡改，但不覆盖“mark collect → render 改 nodes.json → check”、低计数零退出保留旧产物、无 ledger 跨轮行为。
- **安全删除判断**：`not-applicable`；**建议合并**：是。
- **收益/风险**：把本次故障固定为离线门禁；测试需隔离 DATA/LEDGER/BASE，不能真实运行 collector、代理或网络。
- **回归测试**：新增 unittest/pytest，临时目录 + monkeypatch 常量/写入函数；用 YAML/命令字符串检查 workflow 顺序，并接入 `verify.sh`。

## 三、各域发现明细

### A. collector.py、导入方与模板渲染链

#### A1. 完整采集对同一源重复下载和解析

- **位置/证据**：`collector.py:134-147`（collect）；`346-370`（measure.work，尤其 350-355）；`224-235`（count_of）；`397-411`（extract_addrs）。collect 已 `fetch + parse_any`，measure 又 fetch；`count_of` 与 `extract_addrs` 各自再 parse_any。每源至少两次读取、三次解析。
- **问题/判断**：同轮重复下载/解析；`safe_to_delete=no`，**建议合并**。不是简单删除任一 fetch：collect 与 measure 使用不同 cache key（源名 vs `c_+URL hash`），需共享原始快照/解析结果并保留失败隔离。
- **收益**：减少源站请求、超时面、CPU/内存，并固定同一快照。
- **风险**：缓存命中、失败语义和源内容变化时的行为可能改变。
- **回归**：fake fetch/parse 计数；每 URL 只读一次，节点数/地址/fp/source_keys/页面计数保持；另测两次读取内容变化。

#### A2. collector.py 职责过宽

- **位置/证据**：全文件约 985 行；采集 `76-197`，探活/元数据 `238-343`，measure/TCP `346-450`，health/source stats `454-581`，assemble/render `664-834`，quarantine/export/main `837-981`；`main:866-981`，`build_html:765-777`。
- **问题/判断**：采集、解析、探活、健康、生命周期、评分装配、HTML、公开 JSON、凭据导出和 CLI 编排集中；`no`，**建议合并**（重构合并，不是删除）。
- **收益**：按副作用拆分后可独立测试，减小回归范围。
- **风险**：模块常量、动态导入、CLI、CI/Docker 顺序和缓存路径可能破坏；应先提取纯函数/读写适配器，保留兼容壳。
- **回归**：固定 sources/假 fetch golden fixture，比较所有公开产物、`__DATA__`、凭据权限及失败降级；运行 verify、ledger、consistency 和 `--cache/--render` subprocess 测试。

#### A3. CLI 参数基于字符串存在性，未知/冲突参数静默处理

- **位置/证据**：`collector.py:866-870`。
- **问题/判断**：仅判断 `'--render' in sys.argv`、`'--cache' in sys.argv`，无 argparse、未知参数拒绝或 mutually exclusive；`--renderr` 会联网，`--cache --render` 静默忽略 cache；`no`，**建议合并**。
- **收益**：减少误联网、误发布和调试误判，明确实际写盘产物。
- **风险**：argparse 可能改变 cron/workflow 退出码和日志；需保持兼容参数。
- **回归**：无参采集、`--render` 不 fetch、`--cache` 仅采集、冲突/未知参数非零并打印 usage；stdout 列出产物。

#### A4. collector 缺少专属回归

- **位置/证据**：未发现 `test_*.py` 或 `collector.py --selftest`；`verify.sh:50-67` 无 collector；现有 consistency 不能发现 render 双写/重复 fetch。
- **判断**：`not-applicable`；**建议合并**。收益是锁定 render 写盘边界、重复读取、旧数据保真和 HTML 注入；测试需临时 OUT_DATA/猴子补丁，避免真实出网。

### B. nodes_parse.py、nodescore.py、score.py、screen/export 调用方

#### B1. screen.py 使用不完整指纹导致候选误判重复

- **位置/证据**：`nodes_parse.py:15-16,82-91` 将影响连通性的字段纳入 fp；`screen.py:225-227` 的 `lib_keys`、`317-318` 的 `screen_one` 却用 `type|addr`，虽计算 `fps` 但 `new_keys` 不用；规则 `248-251`。
- **问题/判断**：同 host:port 不同 UUID/network/TLS/SNI 被当重复；`no`，**建议合并**。
- **收益**：统一正式采集与候选审核去重口径。
- **风险**：screening 数字和审核结论会改变；需确认 data/nodes 是否保留完整 fp。
- **回归**：相同 type/addr 不同 cred/network 应计新 fp；完全同 fp 仍重复。

#### B2. measure 中 count_of 与 extract_addrs 重复 parse_any

- **位置/证据**：`measure.work:352,355`；`count_of:231-233`；`extract_addrs:405-411`。
- **问题/判断**：同一数据完整解析两次；`no`，**建议合并**。应一次 parse 得到 nodes/raw_count，再派生 count/addrs，保留异常/rejects。
- **收益**：降低 YAML/JSON 解码、标准化、URI 反解成本。
- **风险/回归**：Clash、sing-box、URI/base64 样本 mock parse 计数为一次，结果与旧实现一致。

#### B3. URI 正常/失败路径重复解析

- **位置/证据**：`nodes_parse.py:205-219` 的 parse_uri；`613-615` 的 parse_any；`677-712` 的 `_finalize`。`_raw_uri` 被再次 parse_uri，失败后 `to_uri` 再 parse_uri。
- **判断**：`no`，**建议合并**。不能简单删回读校验；应传递已验证结构/标志或缓存结果。
- **收益**：减少 URI-heavy CPU，统一解析结果。
- **风险/回归**：合法原 URI、不可回读但可重建、重建失败三类统计 parse/to_uri 次数；保持原文、节点数和 quarantine `no_uri`。

#### B4. score.py 文档权重与实际公式不一致

- **位置/证据**：常量 `W_UNIQ=10,W_RATIO=6,W_FRESH=10`（33-46）；`score_row:232-234`；但 `_d_unique:136` 写 12 分、`_d_ratio:147` 写 8、`_d_fresh:157` 写 12；`_d_tcp:121` 写 8，实际无样本上限 `8+0.6*32=27.2`（120-132、224-236）；METHOD 表 `456-465` 为 10/6/10。
- **判断**：`no`，**建议合并**（统一真值和注释）。
- **收益**：避免维护者按错误注释改动等级/阈值。
- **风险/回归**：断言维度满分和有/无 TCP 样本总分、等级边界。

#### B5. parser 无 selftest，verify 未直接覆盖

- **位置/证据**：`nodes_parse.py:64-713` 无 selftest/assert；调用方 `collector.py:141,232,406`、`screen.py:312`、`export_subs.py:264,268` 非 parser 边界回归；`verify.sh:50-58` 未运行 parser。
- **判断**：`not-applicable`；**建议合并**。应覆盖 BOM/空白 JSON、Clash/sing-box、IPv6、reality、UUID、私网/HTTP quarantine、全参数 fp、parse→to_uri→parse。

### C. netsafe.py、nodecheck.py、bench.py

#### C1. 真实检测未在运行时重新做私网/DNS 防护（高风险）

- **位置/证据**：`nodecheck.py:186-192,437-455,595-603`；`collector.py:193-196`；`nodes_parse.py:101-116`。`to_proxy()` 原样使用 `server/port`，mihomo 再解析；解析阶段 `is_public_host` 仅静态一次。
- **判断**：`no`，**建议合并**。
- **收益**：阻断 127.0.0.1、169.254.169.254、解析后内网及 DNS rebinding SSRF。
- **风险**：历史私网节点可能改变；应入口过滤并记录 reject，解析并固定公网地址并处理 DNS 失败。
- **回归**：环回、云元数据、IPv4-mapped、解析到私网、DNS TOCTOU 均不得启动代理。

#### C2. probe 把 HTTP 200 与 204 都判可用

- **位置/证据**：`nodecheck.py:338-363`；TEST_URL 为 `https://www.gstatic.com/generate_204`；README:88-89、P6-P8 文档要求 204。
- **判断**：`not-applicable`；**建议合并**。
- **收益**：避免 captive portal/劫持页使可用率虚高。
- **风险**：依赖 200 的节点会转失败，这是恢复既有口径。
- **回归**：mock 200 返回 `ok=False, err=http:200`；仅 204 为成功。

#### C3. bench 超时统计与 probe 错误格式不一致

- **位置/证据**：`probe()` 返回 `timeout:超时`（`nodecheck.py:364-365`）；`bench.net_one()` 仅比较 `err == '超时'`（`bench.py:179-186`），报告在 `278-284`。
- **判断**：`not-applicable`；**建议合并**。
- **收益**：恢复 timeout_pct 真实统计。
- **风险**：历史报告不可直接横比。
- **回归**：`timeout:超时` 计入，`tcp:...` 不计入，并覆盖 TimeoutExpired。

#### C4. total_timeout 不覆盖建连和重定向

- **位置/证据**：`netsafe.py:202-225,228-240`；`collector.py:94-104`。计时在 `read_capped()` 内才开始，`_SafeRedirect` 不共享 deadline。
- **判断**：`no`，**建议合并**。
- **收益**：限制从请求发起到 body 读完的总墙钟时间。
- **风险**：过严会使抖动合法源失败；需在 opener/redirect 传 deadline。
- **回归**：mock 建连/多跳各耗时，超总预算即 TooSlow。

#### C5. mihomo 下载无 hash/签名校验

- **位置/证据**：`nodecheck.py:60-65,119-146`；`MIHOMO_URLS` 含镜像和 GitHub，仅检查 4-64MB、解压、`-v`；daily 仅缓存路径。
- **判断**：`no`，**建议合并**。
- **收益**：防镜像/发布链路篡改后执行恶意二进制。
- **风险**：需随版本维护官方 checksum/signature，配置缺失会使检测启动失败。
- **回归**：同尺寸错误 gzip 拒绝；合法 digest 通过，镜像失败可回退官方。

#### C6. URL/日志脱敏可漏出 token

- **位置/证据**：`netsafe.py:246-280`，调用 `collector.py:160,285,340,367,449,906`。路径仅对数字数≥2的随机段掩码；query 仅按敏感参数名。
- **判断**：`no`，**建议合并**。
- **收益**：降低异常、状态和来源日志泄露订阅凭据。
- **风险**：过度泛化会损害排障可读性，应按长度/熵/已知 token 形状处理。
- **回归**：纯字母长 token、单数字 token、普通参数中的 ghp/token、高熵串均不出现在 mask 输出。

#### C7. 检测用 skip-cert-verify 泄漏到用户订阅

- **位置/证据**：`nodecheck.py:170-184,223-242,244-269`；`export_subs.py:115-138,161-165`。`to_proxy()` 的检测宽松配置被 `build_clash` 复用；sing-box 写 `tls.insecure=True`。
- **判断**：`no`，**建议合并`。
- **收益**：恢复用户 TLS 身份校验，降低中间人风险。
- **风险**：自签节点可能无法连接；应拆 detection/export profile 或显式用户开关。
- **回归**：检测 profile 可宽松，默认导出不含 skip/insecure；reality 公钥/SNI 不受影响。

### D. sources_io.py、discover.py、screen.py

#### D1. sources.yaml URL 登记校验过弱且与运行时语义分裂

- **位置/证据**：`sources_io.py:44-65`，尤其 64 行仅 `startswith("http")`；运行时 `collector.py:102-104`、`screen.py:154-160`、`discover.py:63-65` 调 `check_url`。
- **判断**：`no`，**建议合并**。
- **收益**：在加载时指出源 ID，避免正式采集静默空数据；但只能做纯语法校验，不可在 load 时做 DNS。
- **风险**：直接调用 `netsafe.check_url` 会把本地启动变成联网/DNS 依赖。
- **回归**：HTTP/HTTPS 大小写、明显非法 URL、`file://`、端口白名单；登记错误含源 ID且无网络调用，运行时仍拦私网。

#### D2. 坏结构配置抛 TypeError/KeyError 而非 SourceError

- **位置/证据**：`sources_io.py:31-46,56-61`；调用方 `collector.py:41`、`discover.py:82`、`screen.py:344`、`governance.py:387`。
- **判断**：`no`，**建议合并**。
- **收益**：统一配置错误处理和 CLI/workflow 行为。
- **风险**：过度收紧可能拒绝旧格式；先定义兼容范围。
- **回归**：groups/sources 非 list、元素缺 id、source 为字符串/null 等均抛带路径/索引的 SourceError。

#### D3. 主 URL 重复导致 collector URL-keyed 映射静默覆盖

- **位置/证据**：`sources_io.validate:41-55` 只校验 id/name；`collector.py:45-48` 以 URL 为 dict key。
- **判断**：`no`，**建议合并**。
- **收益**：避免重复采集但统计、页面归属仅落到后一条。
- **风险**：历史上可能允许共享 URL；应决定拒绝重复还是改用稳定 id 映射。
- **回归**：重复主 URL、主 URL与 mirror 相同、重复 mirror，按最终策略报错或稳定按 id 映射。

#### D4. discover/screen 候选产物非原子写入

- **位置/证据**：`discover.py:231-245` 直接 dump；`screen.py:401-404,448` 直接写；对照 `sources_io.py:142` 使用 `netsafe.write_atomic`；解析失败在 `discover.py:233-235`、`screen.py:364-368` 宽泛当空数据。
- **判断**：`no`，**建议合并**。
- **收益**：中断/磁盘故障不截断队列和报告，保留上一份完整产物。
- **风险**：原子替换涉及权限、inode、fsync 和 git diff 语义。
- **回归**：中途抛异常时旧 JSON/Markdown 不变；正常写入无残留临时文件。

### E. history.py、governance.py、lifecycle.py、threshold_watch.py

#### E1. history 日内 P50 被最后一轮覆盖而非累计

- **位置/证据**：`history.py:276-307`，尤其 298-299；文档 280-282 明确日级累计；`nodescore.py:235-248` 每轮传 `ms_all`。
- **判断**：`no`，**建议合并**。
- **收益**：恢复日 P50 累计语义，避免轮次顺序影响趋势。
- **风险**：不能简单取各轮 P50 的中位数；需保存样本或可合并摘要并兼容旧 trend。
- **回归**：两轮明显不同分布，断言定义中的累计 P50；空样本不覆盖旧值、跨日及 365 天裁剪。

#### E2. governance 未识别 GitHub blob/tree 同文件

- **位置/证据**：`governance.py:89-105` canonical_file；`152-160` link_dups；`487-495` 现有 selftest。web URL 会把 branch 当路径前缀，raw/CDN/web 不能归为 mirror。
- **判断**：`no`，**建议合并**。
- **收益**：治理报告正确识别 mirror，减少重复源漏报。
- **风险**：分支名可含斜杠，必须保留文件路径边界。
- **回归**：raw/jsdelivr/blob/main/tree/feature/x 同文件一致；不同文件/分支不误判。

#### E3. governance 重跑重复追加同一轮 rounds

- **位置/证据**：`governance.py:345-350`；`threshold_watch.py:94-96` 已按同 ts 去重；workflow governance 独立步骤 `daily.yml:125-127`。
- **判断**：`no`，**建议合并**。
- **收益**：重跑幂等，30 轮窗口和 fetch_success_pct 不被污染。
- **风险**：同 ts 可能代表不同快照；建议按源名+checked_at 替换同轮记录。
- **回归**：同 ts 调两次长度/成功率不变，新 ts 追加，窗口仍 30。

#### E4. lifecycle 缺少回归测试

- **位置/证据**：`lifecycle.py:66-109,112-162`；`verify.sh:50-58` 不含 lifecycle。关键跨日、45 天裁剪、freshness 边界无自动断言。
- **判断**：`no`，**建议合并：否**（这是测试补齐项，原审计建议不合并到删除重构）。
- **收益/风险/回归**：新增离线 selftest，覆盖首次出现、连续/跳日、边界裁剪、age_days、new7/median/span 和空输入；隔离时间/PATH，不调用真实产物或网络。

#### E5. history 与 lifecycle 表面重复但语义不可合并

- **位置/证据**：`history.py:56-62,65-73`；`lifecycle.py:32-53,40-41`。两者均截断 16 位 fp、做 CST 天数和首末信息，但调用链显示 history 是“被检测历史”，lifecycle 是“被采集出现历史”。
- **判断**：**暂不删除/待验证**，不建议合并。
- **收益**：当前无删除收益；可仅抽共享 key/day helper。
- **风险**：合并会把未被抽到误当未出现，破坏评分/生命周期完整性。

### F. picks.py、export_subs.py、客户端校验

#### F1. 本轮失败节点以 50% 历史成功率进入 lowlat/fast

- **位置/证据**：`picks.py:62-75`；`README.md:171-187`；`docs/P18-history.md:112-116`；纯内存样例 `tries=2,hits=1,last_ok=today`、本轮失败、合格 ms/bps 会进入 lowlat/fast；未抽到分支 `81-87` 却用 90%/7 天。
- **判断**：`no`，**建议合并**。
- **收益**：失败节点不再进入性能优选订阅，榜单、页面和候选池一致。
- **风险**：若产品有意保留线路抖动节点，直接改门槛会减少候选；需先统一规则和文案。
- **回归**：失败 + 50% 不在 lowlat/fast；90% 且 today-7 保留，today-8 排除；通过节点行为不变。

#### F2. export_subs 自反解校验不等于真实客户端校验

- **位置/证据**：`export_subs.py:246-272`；`verify.sh:66-67`；`clienttest.py:201-218,237-274,303-327`；`docs/P15-clients.md:42`。
- **判断**：`no`，**建议合并**。
- **收益**：避免内部 parser 通过但 Mihomo/sing-box 拒绝。
- **风险**：客户端二进制不一定存在；建议 CI 单独 job，缺二进制明确 skip，存在则失败即门禁。
- **回归**：TLS/reality/hysteria2/tuic/anytls/SNI/flow/WS/gRPC/H2 fixture；有客户端时运行 `clienttest`。

#### F3. sing-box TLS 输出默认 `insecure=true`

- **位置/证据**：`export_subs.py:150-165`；检测路径 `nodecheck.py` 同样设置宽松 TLS；`clienttest.py:91-100` 对 Xray 不写 allowInsecure。
- **判断**：`no`，**建议合并**。
- **收益**：默认恢复证书校验，降低 MITM。
- **风险**：自签节点可能失效；应显式节点级 allow-insecure 或分发两个明确配置。
- **回归**：普通 TLS 默认无 insecure；显式标记才 true；两种配置通过 `sing-box check`。

### G. run_ledger 之外的重复实现、明确未使用导入

#### G1. build_manifest/run_ledger 重复 sha256_file

- **位置/证据**：`build_manifest.py:65-70`、`run_ledger.py:61-66`，AST 归一化函数体完全相同；调用分别在 `build_manifest.py:155`、`check_consistency.py:317` 和 `run_ledger.py:116,148`。
- **判断**：**暂不删除/待验证**（`uncertain`），**建议合并**但必须先抽公共模块，避免循环依赖和 standalone CLI 变化。
- **收益**：减少约 10 行重复，统一哈希行为。
- **风险/回归**：空文件、>64KiB、二进制、缺失文件，以及两 CLI selftest/consistency 门禁。

#### G2. collector/screen 重复 TCP connect helper

- **位置/证据**：`collector.py:414-419` `_tcp_ok`；`screen.py:177-182` `tcp_ok`；AST 体相同，前者由 `collector.py:437` 调，后者由 `screen.py:208` 调。
- **判断**：**暂不删除/待验证**，**建议合并**但保留兼容包装器。collector 显式传 timeout，screen 默认 3 秒。
- **收益**：统一异常语义并减少约 6 行重复。
- **风险/回归**：socket stub 覆盖成功、异常、IPv6、字符串端口和 timeout，不得真实联网。

#### G3. 确认未使用的顶层导入

- **位置/证据**：`audit_privacy.py:34` 的 `json`；`check_consistency.py:36` 的 `ARTIFACTS`；`history.py:37`、`lifecycle.py:20` 的 `time`；`netsafe.py:39` 的 `errno`；`nodecheck.py:24-25` 的 `re, shutil`；`threshold_watch.py:27` 的 `datetime`。AST 无 Name Load，全仓 grep 未见反射/字符串入口使用。
- **安全删除判断**：**可安全删除**（`yes`），确认删除的是这些导入，不是整行中其他实际使用的符号。
- **建议合并**：是。
- **收益**：减少无效绑定和 lint 噪声。
- **风险/回归**：低；执行 compileall、verify quick、各模块 help/selftest；特别保留 `check_consistency.py` 中实际使用的 `norm_ts/sha256_file/SCHEMA_VERSION` 等导入。

### H. drill.py、drill_publish.py、restore.py、audit_privacy.py

#### H1. restore.py 无回归测试

- **位置/证据**：`restore.py:119-160` 的 `cmd_to/cmd_undo`；`verify.sh:75-81` 覆盖 drill_publish/YAML/JS，但不覆盖 restore；脚本无 selftest。
- **判断**：`not-applicable`；**建议合并**（补测试而非删除业务）。
- **收益/风险/回归**：覆盖 dry-run 零写入、rebase/merge 拒绝、blob 预取失败原子性、重复 restore 备份、缺失文件 undo、ALLOW 外不受影响；必须使用隔离临时 git 工作树，不触碰真实仓库。

### I. template.html、index.html 与页面构建

#### I1. 数据中心运行时 fetch，违反单文件离线承诺

- **位置/证据**：`template.html:966` `getJson(fetch)`；`1012` 请求 `data/source_governance.json`；`1024` 请求 `data/picks.json`；`collector.py:213-221` 只注入 DATA；README:33、270 承诺页面不发请求、断网可打开。index.html 对应同脚本。
- **判断**：`no`，**建议合并**。
- **收益**：双击/file:// 或断网时数据中心仍可展示治理与榜单。
- **风险**：不能只删 fetch；需把两个数据集构建时注入并同步 template/build/产物，或修正文档。
- **回归**：本地 HTTP 与 file:// 打开 index，数据中心有内容且无 fetch 依赖；render 后注入数据一致。

#### I2. aria-modal 弹层无焦点陷阱/背景隔离

- **位置/证据**：dialog 声明 `template.html:408,425`；打开/关闭 `895-942` 只锁 overflow、聚焦关闭按钮、处理 Escape，无 Tab 循环、focusin、inert/aria-hidden。
- **判断**：`no`，**建议合并**。
- **收益**：键盘/屏幕阅读器用户不会离开当前 modal。
- **风险**：不能仅移除 aria-modal；需保留 Escape、遮罩、嵌套二维码和焦点恢复。
- **回归**：Tab/Shift+Tab 在详情/二维码内循环，关闭后恢复触发按钮；axe-core 检查。

#### I3. 数据中心首次失败后永久不重试

- **位置/证据**：`template.html:947` `dataLoaded=false`；`956` 先置 true；`966-967` catch 返回 null；`1012-1027` fallback 无 retry。
- **判断**：`no`，**建议合并**。
- **收益**：瞬时失败后可恢复。
- **风险**：简单删 dataLoaded 会重复 append；应采用 loading/success/failure 状态或显式重试并清理旧内容。
- **回归**：首次请求失败显示 fallback，恢复后离开再进或点击重试成功，且不重复请求/卡片。

### J. CI、Docker、cron 与验证约定

#### J1. docker/run.sh 将关键阶段当非关键并继续

- **位置/证据**：`docker/run.sh:15-17,68-76`；`step()` `58-63` 对 must=0 返回 0 继续；daily 的硬门禁在 `daily.yml:73-184`；ledger `run_ledger.py:43-52,127-150`。
- **判断**：`no`，**建议合并**。
- **收益**：容器与 CI 失败语义一致，不让旧检测/榜单冒充本轮。
- **风险**：直接硬失败可能留下部分新文件；应先临时目录原子切换或等价台账/manifest。
- **回归**：nodecheck stub 非零且旧文件不变，后续不执行，退出非零；正常阶段逐一 mark 后 check 通过。

#### J2. 容器失败声明“旧数据不动”但实际直接写挂载目录

- **位置/证据**：`docker/run.sh:59,69-76`；`collector.py:770,830,918-930`；`docker-compose.yml:29-30` 挂载 data/index；`check_consistency.py:16-18,187-209` 无 manifest 时还可降级。
- **判断**：`no`，**建议合并**。
- **收益**：失败轮不污染旧产物。
- **风险**：临时拷贝需避免复制 mihomo 缓存、凭据；白名单回滚需覆盖 index、data、docs。
- **回归**：预置旧产物，某阶段失败后逐文件 SHA-256 不变；成功场景一次性切换。

#### J3. Dockerfile 单步调用示例与 ENTRYPOINT 不符

- **位置/证据**：`Dockerfile:42-44` 固定 exec-form ENTRYPOINT；`docker/run.sh:18,43-64` 不消费 `$@`、无 `exec "$@"`。
- **判断**：原 finding 标记 **可安全删除（yes）**，但执行建议是修正文档/实现契约，不是删除安全门禁。
- **收益**：避免追加命令被吞而意外执行全轮。
- **风险/回归**：不能让任意命令绕过门禁；应支持明确白名单单步模式，参数启动只执行指定安全命令，无参数仍完整流程。

#### J4. run_daily.sh 吞掉 collector 失败码

- **位置/证据**：`run_daily.sh:3-5` 无 `set -e`，collector 后无条件 echo；最终 echo 成功使脚本退出 0。
- **判断**：原 finding 标记 **可安全删除（yes）**，实质是删除“吞失败”的错误行为，保留脚本并返回真实码；**建议合并**。
- **收益**：cron/systemd/监控能感知失败，不再记录虚假完成。
- **风险/回归**：依赖“始终 0”的调度器会开始告警，这是预期纠正；stub python 返回 7 应令脚本返回 7且无成功标记，成功返回 0。

#### J5. daily 与 discover 并发组不同，共写 main

- **位置/证据**：`daily.yml:22-24,186-215`；`discover.yml:12-14,37-47`。两者 contents:write，discover 裸 push，无 fetch/rebase/retry；daily 提交整个 data/。
- **判断**：`not-applicable`；**建议合并**。
- **收益**：减少定时任务 push 竞态。
- **风险**：统一 concurrency 只能排队，人工 push 仍可能冲突；建议 discover 采用限定范围的 fetch/merge/retry，禁止 `-X ours` 静默丢候选。
- **回归**：两个临时 clone 并发提交，验证非冲突文件均保留，冲突明确失败。

#### J6. CI 与 Docker 入口没有漂移回归

- **位置/证据**：`docker/run.sh:66-76` 与 `daily.yml:73-184` 阶段集合不同；architecture 文档 `118-124` 仍称一一对应。
- **判断**：`not-applicable`；**建议合并**。
- **收益/风险/回归**：离线 harness 用每阶段 stub 记录顺序、失败门槛、ledger/manifest/privacy 是否跳过；不执行网络。

#### J7. verify.sh 失败码展示错误

- **位置/证据**：`verify.sh:26-37`，`FAIL=$((FAIL + 1))` 先执行导致 `$?` 变 0，再打印退出码。
- **判断**：`no`，**建议合并**。
- **收益**：准确排障；风险低。回归：命令返回 7 时输出“退出码 7”且最终失败，成功路径不变。

#### J8. verify.sh 未检查 discover.yml

- **位置/证据**：仅 `yaml.safe_load('.github/workflows/daily.yml')`（`verify.sh:79-81`）；`discover.yml:1-33` 有独立 setup/dependency/入口。
- **判断**：`no`，**建议合并**。
- **收益/风险/回归**：循环检查所有 `*.yml`；注入缩进错误必须非零，纯离线。

#### J9. verify.sh 未运行 audit_privacy.py --selftest

- **位置/证据**：`audit_privacy.py` 有 selftest 和 `--selftest` 分支（约 133-160）；`verify.sh:69-70` 仅 `--git`。
- **判断**：`not-applicable`；**建议合并**。
- **收益**：脱敏检测器正负样例成为门禁；风险是需确认临时文件清理。
- **回归**：接入 `audit_privacy.py --selftest`，注入 URI/token 被拦，安全文本不误报。

#### J10. README 验证项数/覆盖说明过时

- **位置/证据**：`README.md:248` 写“8 项”，但 `verify.sh:48,52-58,61,64,67,70,73,77,80,91` 至少 17 个入口；`README.md:326` 又称全部 selftest/门禁/演练。
- **判断**：`not-applicable`；**建议合并**（文档修正）。
- **收益**：贡献者正确理解覆盖和耗时。
- **风险/回归**：避免再硬编码固定总数，可改为“包含核心 selftest、门禁、演练、workflow/JS 检查”。

## 四、明确无问题/未形成可操作缺陷的覆盖域

以下内容已核对，**没有据此提出删除或修复结论**：

1. `history.py` 与 `lifecycle.py` 虽共享 16 位指纹截断、CST 天数和首末日期的表面实现，但调用链证明语义分别是“被检测历史”和“被采集出现历史”，不能删除或合并；仅可在保持语义的前提下抽共享 helper。
2. `picks.py` 的候选池、历史窗口、四类榜单过滤、排序、LIMIT/counts/matched 及下架节点处理除已列 F1 外未发现可安全删除实现。
3. `export_subs.py` 的 base64 全量/优选本地 artifact 不是 `export_subs.py` 分组订阅的替代入口，未因表面重复而删除。
4. `template.html`/`index.html` 的 CSS 同名媒体查询属于有意覆盖；未发现可确认删除的无用 CSS/JS、DOM 选择器或重复规则。
5. 正式来源与发现/候选来源的隔离边界有代码和 workflow 证据，未误报为问题；`sources.yaml` 当前 26 条登记记录的 enabled/disabled 兼容字段和 format 设计已核对。
6. `requirements.txt` 中 PyYAML 不是无用依赖：`nodes_parse.py:419/423`、`sources_io.py:30`、`export_subs.py:138/253` 均运行时使用；CI/Docker 均固定 Python 3.11，未发现可由静态证据确认的 Python 语法兼容缺陷。
7. `drill.py`、`drill_publish.py`、`restore.py`、`audit_privacy.py` 未发现可安全删除的无引用业务函数；发现的是 restore 测试缺口和 audit_privacy selftest 未接入。
8. 本轮 AST/grep 审计未将动态 import、脚本 `main`、workflow 命令、selftest 或产物入口因缺乏直接 Python 引用误判为无用。

## 五、执行建议（仅建议，不修改代码）

1. **先解决 P0 台账所有权**：在设计文档中明确 `nodes.json` 的唯一写入阶段；二选一：render 真正只写页面，或将 render 明确声明为重建快照并在台账/manifest 中记录最终哈希。同步修正低计数保护的退出码和 workflow mark 条件。
2. **统一失败语义**：docker/run.sh 应与 daily 的硬门禁一致；整轮输出优先写入临时目录并通过一致性检查后原子切换，避免本轮采集 + 上轮检测混合。修复 `run_daily.sh` 真实退出码。
3. **先补离线门禁，再做重构**：新增 collector、parser、ledger/render 顺序、lifecycle、restore、客户端校验和入口漂移测试；不以真实采集、代理网络或联网作为回归依赖。
4. **按安全优先修复运行时边界**：nodecheck 运行时重新做解析后公网校验；固定 mihomo digest/signature；拆分 detection/export TLS profile；修复 204、timeout 和总超时口径；加强日志脱敏。
5. **再统一数据口径**：修正 screen 全参数 fp、measure 单次解析、URI 解析缓存、score 注释、picks 失败节点规则、history 累计 P50、governance URL canonicalization/幂等。
6. **最后处理低风险清理**：删除已确认未使用导入；对 sha256/TCP helper 先设计公共模块与兼容包装器，再在回归通过后合并。Dockerfile 单步行为和文档应改为明确白名单参数，而不是简单删除入口安全控制。
7. **文档同步**：修正 README 的离线数据中心承诺、verify 覆盖描述、Docker 单步命令和各阶段失败语义；若功能暂不修复，必须如实降低承诺。

## 六、审计盲区

本次结构化结果中“子任务失败：[]”，没有报告失败子任务。因此没有额外的失败子任务盲区可列。但以下是主动声明的验证边界：

- 未执行真实采集、真实代理握手、真实 DNS/SSRF 检测、真实发布或联网下载；相关结论来自源码、调用链、AST、workflow 和离线样例。
- 当前 sandbox 缺少 PyYAML；部分 governance 纯函数验证使用内存 stub 绕过 `sources_io` 导入，未据此推断真实网络或 YAML 行为。
- 未执行浏览器 file:///HTTP 交互、axe-core、Mihomo/sing-box 客户端二进制验证；页面离线、焦点和客户端兼容问题需按报告中的回归测试补实证。
- 未做磁盘故障、进程中断、DNS rebinding、下载篡改等真实故障注入；原子写入、SSRF、供应链和事务回滚结论是代码路径风险判断。
- 未修改仓库文件；报告中的执行建议不代表已合并任何修复。
