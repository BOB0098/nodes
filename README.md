# 节点配置链接

> 每日自动收集的免费节点订阅链接，一键复制即用。

免费节点订阅**索引站**。GitHub Actions 每 3 小时采集一次公开订阅源，统计真实条数、实测可连率与源新鲜度，按质量排序后整理成一页。打开网页 → 复制任意一条链接 → 粘进客户端 → 更新即可。

**页面只做索引**：卡片上的链接原样指向各仓库的发布地址，不改写、不合并、不转格式。你在客户端里订阅到的，就是源作者发布的原始文件。

本仓库**不托管、不分发节点凭据**。采集器会把去重后的全量清单导出到 CI 产物（`export/`，Actions artifact，保留 1 天）供自用，但该目录已在 `.gitignore` 中，不会进入版本历史——节点 URI 里带着 uuid/password 等完整凭据，公开托管等于泄露他人的账号。`data/nodes.json` 同样经过脱敏，只保留名称／协议／地区／地址／来源，不含 URI。

## 特性

- **每 3 小时自动采集** — GitHub Actions 定时拉取全部源，自动重建页面，无需人工维护
- **采集时间写在页头** — 直接显示「数据更新于 11-24 17:54」；超过 6 小时会标红提示「已 N 小时未刷新」，不让人拿旧数字当现状
- **实测数据** — 每条源抽 24 个节点测 TCP 连通率，同时探测源站 HTTP 状态；节点数按文件真实条目统计
- **新鲜度透明** — 源仓库最后提交时间单独显示，超过 30 小时自动标「可能已停更」；与作者自称的更新频率分开，以实测为准
- **按质量排序** — 卡片按「可连率 × 节点数」排，能用的排前面
- **三类 15 条源** — Clash 5 条、V2Ray 6 条、sing-box 4 条，每卡附适用客户端标签；清单只有一份（`sources.yaml`），页面与采集共用，不会再出现「页面有卡、后台没数」
- **一键复制 / 扫码导入** — 复制按钮直接进剪贴板，二维码弹窗支持手机相机扫码
- **筛选开关** — 可连 ≥80% / 国内可直连，一键过滤
- **纯静态零后端** — GitHub Pages 免费托管，无账号、无跟踪、无后端

## 网页

GitHub Pages 已启用（main 分支根目录）：

```
https://bob0098.github.io/nodes/
```

页面是纯静态单文件：所有数据在构建时整份内嵌进 `index.html`，打开即显示，不发任何网络请求，断网也能打开。

## 每 3 小时自动更新

`.github/workflows/daily.yml` 每 3 小时跑一次 `collector.py`（cron `17 */3 * * *`，避开整点排队；GitHub 仍可能延迟，所以页面写「约 3 小时」）：

1. 并发拉取各订阅源，统计真实条数
2. 每条源抽 24 个节点测 TCP 连通率，并测源站自身 HTTP 状态
3. 用 GitHub API 查每条源最后提交时间，超过 30 小时的标「可能已停更」
4. 重建 `index.html` 与 `data/nodes.json`（含 `data/status.json`）
5. 自动 commit 回本仓库（Pages 随之更新）

带 2 小时新鲜度闸门：cron 迟到导致重复触发时直接跳过。也可以手动触发：Actions → 节点数据更新 → Run workflow。

## 源怎么挑的

清单在 `sources.yaml`（唯一事实来源），新源必须先过 `screen.py` 四关才可能启用：

1. **试采集** — 限 12 MB，记录 HTTP 状态与体积
2. **解析** — 用与入库完全相同的解析器（Clash YAML / sing-box JSON / URI / base64）
3. **去重** — 全参数 SHA-256 指纹；地址级比对会骗人，指纹级才看得出「包含关系」
4. **真实检测** — 抽样 24 个地址做 TCP 握手，算「预计可用新增 = 真正新增 × 可连率」

硬门槛：HTTP 200、解析 ≥20 条、独立新增 ≥10%、预计可用新增 ≥250 条、30 天内有提交。

P2 的实测结果（17 个候选）：

| 候选 | 解析 | 真正新增 | 可连 | 结论 |
|---|---:|---:|---:|---|
| MatinGhanbari/v2ray-configs | 3,969 | 1,764 | 25% | ✅ 启用 |
| Surfboardv2ray/TGParse | 8,189 | 4,002 | 12% | ❌ 全是死条目 |
| NoMoreWalls | 283 | **0** | 46% | ❌ 已被尔毛子全量包含 |
| AutoMergePublicNodes | 267 | 0（83% 与 NoMoreWalls 重合） | 50% | ❌ 派生项目 |

结论很反直觉但很明确：**文件大不等于节点多，节点多不等于能用**。
详见 [`docs/P2-sources.md`](docs/P2-sources.md)。

自动发现由 `discover.py` 每周跑一次，只写 `data/candidates.json` 候选队列——
**永远不会自动把新源加进订阅**，必须人工审核后置 `enabled: true`。

## 真实可用率

前面说的「可连率」都是 TCP 握手——端口开着就算通。但端口通不等于代理能用：
一个 vmess 节点完全可能端口开着、uuid 早已失效。

每轮抽样 1,200 条节点交给 mihomo（这些协议的参考实现）真握手，通过它访问
`gstatic/generate_204`，拿到 204 才算可用。卡片上那一行「真实握手 90 个 ·
可用 21（23%）」就是它。

两个口径的差距是这套系统的核心发现：

| 来源 | TCP 可连 | 真实可用 |
|---|---:|---:|
| radarhub | 100% | 87% |
| anaer | 88% | **2%** |
| ALIILAPRO | 54% | **3%** |

一个源的价值是真实可用条数，不是节点总数，也不是 TCP 可连率。
详见 [`docs/P6-P8-realdetect.md`](docs/P6-P8-realdetect.md)。

## 源质量怎么评的

每轮采集后 `score.py` 按**实测数据**打分（不读 README 宣传），卡片标题旁的
字母徽章就是它：S 90–100 / A 80–89 / B 65–79 / C 50–64 / D <50。

| 维度 | 满分 | 依据 |
|---|---:|---|
| 可连率 | 40 | 抽样 TCP 可连率，70% 封顶 |
| 独立新增 | 12 | 只被本源包含的指纹数，对数刻度 |
| 独立率 | 8 | 独立新增 ÷ 原始解析 |
| 新鲜度 | 12 | 源内容多久没变 |
| 稳定性 | 18 | 连续失败 + 近 6 轮可连率波动 |
| 合规/类型 | 10 | 有 SPDX 许可证 + 是否原创 |

可连率占 40 分是故意的：**它是唯一能证明「节点真能用」的指标**。独立新增只给 12 分，
因为「多」和「能用」在这个领域是负相关的（P2 §4③ 实测过）。

淘汰只看采集失败，不看节点质量：单轮失败标「单轮异常」→ 连续两轮降级 →
连续三轮隔离（不进优选导出，但**仍然继续采集**，恢复后自动回来）。
不允许因为一次网络故障永久删源，也不允许因为分数低就悄悄下架。

完整公式、逐源明细与扣分点见 [`docs/P4-source-quality.md`](docs/P4-source-quality.md)，
每轮采集自动重写。

## 解析与去重

一条订阅可能是 Clash YAML、sing-box JSON、URI 列表或整体 base64，三种载体都由
`nodes_parse.py` 解析成同一套模型。当前支持 VMess / VLESS / Shadowsocks / Trojan /
Hysteria2 / TUIC / SOCKS5 / AnyTLS。

**明文 HTTP 代理不入库**：流量和凭据在链路上明文可读，这类条目绝大多数还是带广告的
公开代理池。但它不会被静默丢掉——每轮被丢弃的条目会按原因写进
`data/quarantine.json`（只记协议/地址/端口，不记原文），页面上「这个源少了几百条」
因此有据可查。

去重按**全参数指纹**（协议+地址+端口+凭据+传输参数一起哈希），不是 host:port。
同一台机器上不同 uuid/不同 ws 路径的节点是不同节点，按地址去重会误杀 40%。

## 节点存活多久

`lifecycle.py` 按天记录每个指纹第一次和最近一次出现的时间，派生两个指标：
卡片上的「近 7 天新增 N」和悬浮提示里的「中位存活天数」。

前者回答「这个源还在产新节点吗」——总量大不代表还在更新，一个源连续几周零新增，
通常只是旧节点在被到处转发。45 天没再出现的指纹会从历史里清掉，隔这么久又冒出来的
按新节点算才合理。

## 客户端

| 订阅类型 | 内核 | 客户端 |
| --- | --- | --- |
| Clash 系 | mihomo | Clash Verge Rev（电脑）、FlClash（手机/电脑）、Clash Meta for Android |
| V2Ray 系 | Xray | v2rayNG（安卓）、v2rayN（电脑）、Shadowrocket（iOS）、Karing |
| sing-box 系 | sing-box | Hiddify（手机/电脑）、sing-box（安卓/苹果） |

## 安全与回滚

外部输入全是别人仓库里的文本，所以出网统一走 `netsafe`：只允许 HTTP/HTTPS、
目标必须是公网地址（解析结果逐个比对）、每跳重定向都重查、单源 12 MB 上限、
整体 90 秒预算。日志里所有 URL 与节点 URI 都过脱敏——订阅地址的 query 常放
token，`print(e)` 一次就可能把凭据写进公开的 Actions 日志。

真实检测失败不会覆盖上一轮的有效结果：本轮可用数掉到上轮的 40% 以下时判定为
环境异常，`nodecheck.json` 保持不动，页面继续用旧数据。所有 JSON 都是原子写
（先写临时文件再 rename），中断时留下的是旧文件而不是半个 JSON。

数据回滚不用记 git 命令：

```bash
python3 restore.py list                 # 最近 20 次数据改动
python3 restore.py to <sha> --dry-run   # 只看会改什么
python3 restore.py to <sha>             # 恢复（当前内容自动备份）
python3 restore.py undo                 # 撤销上一次恢复
```

细节与残余风险见 [docs/P13-security.md](docs/P13-security.md)。

## 说明

- 卡片链接均为各仓库原样发布，本项目不改写、不合并。节点库为了统计与去重会把各源解析成统一模型，但不会回写源文件。
- 条数与可连率在构建时内嵌进 `index.html`，页面本身不发请求；`data/nodes.json` 是同一份数据的结构化版本，供脚本或监控读取，页面不依赖它。
- 「源更新于」来自仓库最后一次提交时间，与作者自称的更新频率分开显示，两者不一致时以实测为准。
- 不在 GitHub 上的源（如 radarhub）拿不到提交时间，改用内容指纹：每轮采集比对内容 md5，没变就沿用上次变化时间。这类源从第二轮采集起才有数值。
- 页面顶部两个筛选开关（可连 ≥80% / 国内可直连）只改显示，不改数据。
- 免费节点随时失效，一条不通就换下一条。

## 免责声明

本项目仅整理公开渠道的订阅链接，不生产、不存储、不修改任何节点资源。订阅内容来自第三方仓库，可用性与安全性由源作者负责。请遵守当地法律法规，仅作学习交流用途。

## Docker 部署（自建机器）

不想依赖 GitHub Actions 的话，任意一台带 Docker 的机器都能跑同一套链路：

```bash
cp .env.example .env      # 填你的 uid/gid，`id -u` / `id -g`
docker compose run --rm collect
```

一条命令跑完「采集 → 真实握手 → 评分 → 渲染 → 生成订阅」，顺序和 CI 里一模一样。
定时执行交给宿主机的 cron：

```
17 */3 * * * cd /path/to/nodes && docker compose run --rm collect >> data/logs/cron.log 2>&1
```

几个刻意的设计：

* **采集失败就停，检测失败继续**。采不到数据还往下走，后面的步骤会拿旧数据算出一份
  看起来正常的结果——那比直接失败危险得多
* `data/` 是宿主机挂进去的，容器只是干活的。换机器只要把目录搬走
* mihomo 放在命名卷里，第二次跑不用再下载
* 不假设多台机器共享磁盘。想并行就在各自机器上跑，再去合并 `data/nodes.json`

评估一台云服务器能不能用，先在那台机器上跑体检：

```bash
python3 dockerprobe/probe.py run --platform 你的平台名 --json probe/x.json
```

它只回答三个问题：Docker 真的能跑吗、出网通不通、中途会不会被平台停掉。
三个都过才叫「适合日常采集」。结论和三家免费平台的实测情况见
[docs/P11-docker-platforms.md](docs/P11-docker-platforms.md)。

## 本地运行

```bash
python3 collector.py            # 采集并重建 index.html（读 sources.yaml）
python3 collector.py --render   # 只按 data/nodes.json 重渲染页面，不联网
python3 nodecheck.py --speed    # mihomo 真握手 + 测速（需 curl，会自己下 mihomo）
python3 nodescore.py            # 评分、地区、标签
python3 export_subs.py          # 生成各格式订阅并逐个反解校验
python3 screen.py               # 审核候选源（试采集→解析→去重→真实检测）
python3 discover.py --dry-run   # 检索新的候选仓库，只看结果不落盘
python3 score.py --report       # 用现有 stats 重算评分并重写质量报告
python3 lifecycle.py            # 看节点存活统计（跟踪数/近 7 天新增/中位存活）
python3 netsafe.py              # 出网边界自测（SSRF/大小/超时/脱敏/原子写，30 项）
python3 restore.py list         # 看数据文件最近改了什么
python3 bench.py                # 规模压测（500/2k/5k/10k，写 docs/P14-bench.md）
python3 drill.py                # 故障演练七项（写 docs/P14-drills.md）
python3 clienttest.py           # 用 Mihomo / sing-box / Xray 真实导入订阅
open index.html                 # 或直接双击
```

`score.py` 依赖 `data/sources_stats.json`（CI 每轮生成）。本地想跑通整条评分链路，
用 `NODES_FORCE_WRITE=1 python3 collector.py`：会强制落盘，并把历史标记为本地环境；
CI 下一轮检测到环境切换会重新积累——本地出口网络的可连率和机房不可比，
混在一起会把「换了个网络」算成「源在变差」。
