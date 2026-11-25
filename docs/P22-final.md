# P22 安全、自动化与最终验收报告

发布门槛阶段。对照任务书 §5 P22 的 8 项要求逐条核验，**先查缺口再补门禁**，不把"看起来正常"当通过。提交基线：远端 main `188a8826`（P21 完成态）。

## 一、P22 八项要求逐条结论

| # | 任务书要求 | 结论 | 证据 |
|---|---|---|---|
| 1 | SSRF / DNS rebinding / 重定向 / 私网·环回·链路本地拦截 / 解析器限制 / 超时 | 已具备 | `netsafe.py`：`check_url` 协议+端口白名单、字面量 IP 与非公网判定、域名解析后逐地址比对（挡 DNS rebinding 到私网）、`_SafeRedirect` 每跳重查、`read_capped` 12MB+90s 双上限；`netsafe.py --selftest` 30+ 项含 `169.254.169.254`、IPv4-mapped 回环、file/gopher、非白名单端口 |
| 2 | 令牌/密钥/认证/订阅 URL 泄漏审计；公开数据脱敏 | 本轮补强 | git 全历史真令牌 `B1qkHAL8` 出现 **0 次**；2 处 `ghp_` 均为 selftest 假值。新增 `audit_privacy.py` 作可复现门禁，扫 20 个公开产物干净 |
| 3 | Actions 最小权限 + 并发 + 超时 + 失败通知 + 缓存 + 发布保护 | 本轮补失败通知 | 权限 `contents: write` + (新增) `issues: write`；concurrency `collect`；采集 15min / 检测 40min 超时；mihomo 缓存；一致性检查无 `continue-on-error`；新增失败通知步骤 |
| 4 | 数据质量门禁：数量异常下降 / 口径不一致 / 格式错误 / 页面与订阅不一致 | 本轮补数量门禁 | `check_consistency.py` 新增第 9 项：对照上一版入库数，<60% 拦下；其余 8 项 + manifest 哈希硬校验已覆盖口径/格式/一致性 |
| 5 | 一键本地验证命令 / 干净 clone 可复现 | 本轮新增 | `verify.sh` 8 项；在 `/tmp/nodes-clean-clone`（无 .git、无 export、95 文件）跑通 8/8 |
| 6 | README：功能/部署/订阅/检测口径/隐私安全/限制/维护 | 已更新 | README 补充 P22 三道门禁说明与 `verify.sh` 命令；原有章节覆盖其余项 |
| 7 | 最终回归 + 真实 Pages 页面与订阅链接验证 | 通过 | 线上 Pages HTTP 200；index.html / subscriptions.json / build_manifest.json 与本地逐字节一致；nodes.json 远端 blob sha 一致（1.5MB 下载受沙箱网络限制，改用 sha 核验） |
| 8 | 验收测试：安全 / 失败演练 / 干净构建 / 客户端导入 / 移动端 / 一致性全通过 | 见下 | 安全+一致性+演练见一键验证；客户端导入沿用 P15 真值；移动端交互与五档截图见 P21 报告 |

## 二、本轮实际改动（5 个文件）

| 文件 | 改动 |
|---|---|
| `verify.sh` | 新增一键本地验证：8 项（安全/清单/订阅/脱敏/一致性+数量/回滚演练/YAML/JS），`--quick` 跳过演练，干净 clone 可跑 |
| `audit_privacy.py` | 新增公开产物脱敏审计：扫 `data/*.json` + `index.html`，拦节点 URI / 明文凭据字段 / token / URL 内嵌凭据；带 7 项 selftest |
| `check_consistency.py` | 新增第 9 项"节点数量异常下降"门禁：`git show HEAD:data/nodes.json` 读上一版，<60% 拦下；`NODES_PREV_COUNT` 可覆盖；无 git 历史降级警告 |
| `.github/workflows/daily.yml` | 权限增 `issues: write`；脱敏审计入发布门禁；末尾加 `if: failure()` 失败通知（gh 开/更新 `ci-failure` issue，去重）；20 步 |
| `README.md` | 补 P22 三道门禁、一键验证命令、数量下降演练方法 |

## 三、真实验证输出

### 一键验证（本地，完整）
```
一键验证（P22 发布门槛）  python=Python 3.11.1
  [PASS] 安全边界自测 (netsafe)
  [PASS] 构建清单自测 (build_manifest)
  [PASS] 订阅反解自测 (export_subs)
  [PASS] 脱敏审计 (audit_privacy)
  [PASS] 一致性/数量门禁 (check_consistency)
  [PASS] 发布回滚演练 (drill_publish)
  [PASS] 工作流语法 (daily.yml)
  [PASS] 页面 JS 语法 (index.html)
结果：8 项通过，0 项失败 → 发布门槛通过
```

### 数量下降门禁（三种情形）
- 正常：上一版 10059 → 本轮 10059（保留 100%）→ 放行
- 异常：`NODES_PREV_COUNT=20000` → 保留 50%，退出码 **1**，拦下
- 边界：上一版 16766 → 保留 59.99%，退出码 **1**，拦下；16765 → 60% 放行
- 无 git 历史：降级为警告，不拦下（干净 clone 场景）

### 脱敏审计
- 基线：扫描 20 个公开产物，0 命中
- 注入 `vmess://eyJ2...` 到 `data/nodes.json` → 命中并拦下（退出码 1）；恢复后放行

### 干净环境构建
`/tmp/nodes-clean-clone`（tar 复制提交文件，排除 `.git`/`export`/日志/缓存，95 文件）：
```
[PASS] ×8，0 失败
! 取不到上一版节点条数（无 git 历史），跳过数量下降检查  ← 预期降级
→ 发布门槛通过
```

### 线上验证（真实 Pages）
| 项 | 结果 |
|---|---|
| `https://bob0098.github.io/nodes/` | HTTP 200，82500 bytes |
| index.html vs 本地 main | 逐字节一致；含 `fSearch`/`fProto`/`fSort`/`fold`（P21 已上线） |
| data/subscriptions.json | 逐字节一致，updated `2026-11-25 14:15` |
| data/build_manifest.json | build_id `20261125T1415-83f54311ea` 一致，口径 10059/952/315 |
| data/nodes.json | 远端 blob sha `c0141924...` 与本地 `git hash-object` 一致（沙箱下载 1.5MB 超时，改用 sha 核验，非 Pages 故障） |

### git 历史脱敏审计
- 真令牌 `B1qkHAL8`：`git log --all -p | grep -c` = **0**
- `ghp_` 相关提交 2 个：`92618e8`（`ghp_1234567890...`）、`daf445e`（`ghp_abcdef...`），均为 selftest 假值，非真凭据

## 四、关键设计决策

1. **脱敏审计只扫公开产物，不扫源码/文档**：`netsafe.py`/`nodes_parse.py` 的 selftest 故意写假 URI 和假 token 来验证脱敏逻辑，P13 文档里的 `u:p@` 是讲 SSRF 的语法示例。全扫会产生 17 处误报，把真泄漏淹没在噪声里。真凭据写进源码属 code review 范畴，不是"公开数据"。
2. **数量下降阈值 60%**：与 `nodecheck.py` 现有的"可用数掉到上轮 40% 以下判环境异常"同数量级；只比同口径的入库条数，不混用来源数/检测数。设环境变量覆盖是为了能离线演练。
3. **失败通知用 gh CLI 而非外部 action**：runner 自带，无新增依赖；issue 去重（复用 open 的 `ci-failure` issue 追加评论）避免每 3 小时刷屏；`continue-on-error` 保证通知失败不反过来影响发布结论。`issues: write` 是唯一新增权限。
4. **验证脚本与 CI 一一对应**：`verify.sh` 每项都能在 daily.yml 找到对应步骤，本地跑通再去改工作流，避免"本地没验证、推上去才发现门禁挂了"。

## 五、未解决 / 已知限制

- **用户原始令牌仍未撤销**：按用户"项目迭代完后晚点再撤销"的指示保留；本次审计确认它从未进入仓库历史，故无需紧急轮换，仍建议在方便时撤销。
- **客户端内核导入沿用 P15 真值**（Mihomo/sing-box 12/12、Xray 9845 条 OK）：沙箱无客户端二进制缓存，不冒充本机实测；P20 订阅改动的格式反解已由 `export_subs.py --selftest` 覆盖。
- **Hiddify GUI 免登录导入**无法在无 GUI 环境测试。
- **1.5MB nodes.json 下载**受沙箱出网速率限制，线上一致性改用 blob sha 核验（已证一致）。
- **`best` 订阅阈值 65 分**待历史积累后重估（P18 遗留）。

## 六、回滚方式

数据回滚：`python3 restore.py list` → `python3 restore.py to <sha> --dry-run` → 去掉 `--dry-run` 执行。代码回滚：Revert 本提交即可；`check_consistency.py` 的新增项在无 manifest / 无 git 历史环境会自动降级，不会卡住旧环境。失败通知即使开错 issue 也不影响数据——关掉对应 issue 即可。
