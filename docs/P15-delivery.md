# 最终交付

这份文档回答三件事：**交付了什么、怎么验证、还有什么没做到**。每一项都指向仓库里能跑的命令或能看的文件，不放「已完成」三个字了事。

---

## 1. 交付物

### 采集与解析

| 文件 | 作用 | 怎么验证 |
|---|---|---|
| `collector.py` | 拉 15 个源、解析、去重、渲染页面 | `python3 collector.py`（联网）/ `--render`（离线） |
| `nodes_parse.py` | Clash YAML / sing-box JSON / URI / Base64 四类载体统一成节点模型 | 内置 `_finalize` 校验；隔离区留痕 |
| `sources.yaml` | 唯一的来源清单（页面卡片、采集目标、探活都读这份） | `python3 sources_io.py` 校验必填字段 |
| `lifecycle.py` | 节点首次/最近出现、近 7 天新增、中位存活天数 | `python3 lifecycle.py` |
| `discover.py` | 自动发现候选仓库，只写 `data/candidates.json` | `--dry-run` 只看不落盘 |
| `screen.py` | 候选源四关审核（试采→解析→去重→真实检测） | `python3 screen.py <url>` |

### 检测与评分

| 文件 | 作用 | 怎么验证 |
|---|---|---|
| `nodecheck.py` | 把每个节点绑到独立 socks 端口，经 mihomo 真握手 | `--selftest`（11 项） |
| `nodescore.py` | 六维评分、地区归类、稳定性标签 | 输出 `data/node_scores.json` |
| `score.py` | 来源评分与淘汰状态机 | `python3 score.py --report` |

### 输出

| 文件 | 作用 | 怎么验证 |
|---|---|---|
| `export_subs.py` | 12 组订阅 × 4 种格式，生成后立刻反解 | `--selftest`（含「故意传错条数必须判假」） |
| `index.html` + `template.html` | 单文件页面，无外部依赖 | 双击打开；首屏总览读 `DATA.stats` |

### 安全与运维

| 文件 | 作用 | 怎么验证 |
|---|---|---|
| `netsafe.py` | 出网四道闸 + 日志脱敏 + 原子写 | `python3 netsafe.py`（30 项） |
| `restore.py` | 数据恢复与回滚 | `list` / `diff` / `to` / `undo` |
| `bench.py` | 500/2k/5k/10k 规模压测 | `python3 bench.py` → `docs/P14-bench.md` |
| `drill.py` | 七项故障演练 | `python3 drill.py` → `docs/P14-drills.md` |
| `clienttest.py` | Mihomo / sing-box / Xray 真实导入 | `python3 clienttest.py` → `docs/P15-clients.md` |
| `Dockerfile` + `docker/run.sh` | 一条命令跑完一整轮 | `docker compose run --rm collect` |
| `dockerprobe/probe.py` | 评估一台机器能不能跑本项目（15 项） | `python3 dockerprobe/probe.py run` |

### 文档

`docs/P4-source-quality.md`（每轮自动重写）、`docs/P5-normalization.md`、`docs/P6-P8-realdetect.md`、`docs/P9-storage.md`、`docs/P10-actions.md`、`docs/P11-docker-platforms.md`、`docs/P13-security.md`、`docs/P14-bench.md`、`docs/P14-drills.md`、`docs/P15-clients.md`。

---

## 2. 验收底线对照

| 任务书要求 | 结论 | 证据 |
|---|---|---|
| 现有网站与订阅入口不破坏 | ✅ | GitHub Pages 仍是同一路径；`export/subs/` 的 12 组文件名与分组键未变 |
| 来源真实接入且可追溯 | ✅ | `sources.yaml` 15 个源全部 `enabled`；`data/sources_stats.json` 记录每源的原始/独立/重复/可连/HTTP 码 |
| 去重准确 | ✅ | 全参数指纹（27 个字段），截断 16 位 hex；`docs/P5-normalization.md` 有碰撞概率计算 |
| 真实检测有效 | ✅ | mihomo 真握手 + DIRECT 自检（出口不通时中止，不写结论）+ 回归闸门 |
| 优选订阅不含未验证节点 | ✅ | `best` 只收 `ok=true` 且分数达标的；隔离源整体剔除 |
| 多格式客户端导入成功 | ✅ | Mihomo 12/12、sing-box 12/12、Xray 9,845 条 `Configuration OK`（`docs/P15-clients.md`） |
| 自动更新稳定 | ✅ | 每 3 小时一轮；检测步骤 `continue-on-error`；写盘前有回归闸门 |
| 历史数据保留 | ✅ | `node_lifecycle.json`（首次/最近）、`node_score_history.json`（近 10 轮）、git 每轮一次提交 |
| 2,000 节点稳定性测试 | ✅ | `bench.py --net 2000`：555 秒、107 MB 流量、失败率 97.8%（沙箱出口受限，见下） |
| 10,000 节点真实能力评估 | ⚠️ 部分 | 每轮抽样 1,200 条做真实握手，按天轮换种子，约一周覆盖全库；**没有做过「一万条同时握手」**——那需要约 45 分钟连续出网，Actions 单 job 上限是 6 小时，但 quota 与失败重试成本不划算 |

---

## 3. 三条运行路径

**GitHub Actions（默认）**：每 3 小时一轮，`workflow_dispatch` 可手动触发。采集失败即停（保留上一轮数据），检测/评分/订阅失败不阻断后续。

**Docker（自建机器）**：

```bash
cp .env.example .env          # 按需改 NODES_UID
docker compose run --rm collect
# 定时：17 */3 * * * cd /path/to/nodes && docker compose run --rm collect >> data/logs/cron.log 2>&1
```

容器的 `data/` 与 `index.html` 挂到宿主机；`export/` 不挂（要取订阅得显式挂载，因为里面有全量凭据）。

**本地**：`python3 collector.py` 后 `open index.html`。本地出口的可连率与机房不可比，所以 `nodecheck` / `lifecycle` / 评分默认不落盘，需要 `NODES_FORCE_WRITE=1`。

---

## 4. 回滚与恢复

```bash
python3 restore.py list                 # 最近 20 次数据改动
python3 restore.py diff <sha>           # 该提交改了哪些数据文件
python3 restore.py to <sha> --dry-run   # 只看会改什么
python3 restore.py to <sha>             # 恢复（当前内容自动备份到 .restore-backup/）
python3 restore.py undo                 # 撤销上一次恢复
```

代码回滚就是 `git revert`。数据与代码分开回滚是刻意的：数据坏了不该连带把代码退回旧版本。

---

## 5. 已知限制（不粉饰）

1. **真实可用率随出口变化**。同一批节点：CI（美国机房）34%，本地沙箱 3%。页面上永远标注检测环境与抽样规模，不把「本机测出来 3%」写成节点质量。
2. **每轮只测 1,200 条**。全量一万条要 45 分钟，成本不划算。按天换随机种子，一周覆盖全库，所以单条节点的「最近检测时间」可能是一周前。
3. **DNS rebinding 的 TOCTOU 窗口**没堵死（见 `docs/P13-security.md`）。前提是源清单可信；能让 DNS 指向内网的人已经有仓库写权限。
4. **流媒体 / AI 解锁不检测**。评分里那 5 分恒为 0，总分上限 95。做不准的项宁可不做。
5. **Hiddify 只做了配置格式验证**，没做 GUI 导入测试（它没有免登录 CLI）。
6. **Xray 26 的策略变化**导致一批节点无法用它连接：明文 vless/trojan、`allowInsecure`（已移除）、HTTP(h2) 传输（已移除）、rc4-md5 等旧加密、reality+ws。这些节点在 Mihomo 与 sing-box 上仍然可用，报告里逐类列了条数。
7. **三个免费云服务器都不能认定为「适合长期常驻」**（`docs/P11-docker-platforms.md`）：InstaBox 空闲 30 分钟自动暂停，Altare 未说明回收策略，Nodaroo 没有 Docker 文档。

---

## 6. 这一轮改出来的东西（按影响排序）

| 问题 | 影响 | 怎么发现的 |
|---|---|---|
| `reality-opts` 空 dict 被当成 reality | 2,374 条误标、1,987 条公钥为空；sing-box 拒绝整份订阅，页面安全标注全错 | 客户端实测 |
| 检测失败直接覆盖 `nodecheck.json` | 一次环境抖动就永久抹掉上一轮 340 条有效结论 | 故障演练 |
| `urlopen().read()` 无上限 | 一个坏源能把 runner 内存吃干净，表现成「今天没更新」 | 代码审计 |
| 非法 UUID（`monacoVPN`、`@Geek_Spot`）入库 | 359 条 100% 连不上的节点白占检测配额 | 客户端实测 |
| hysteria2/tuic/anytls 缺 TLS 块 | sing-box `TLS required`，整份配置被拒 | 客户端实测 |
| `network` 被 fragment 污染（`ws#🍪...`） | Xray `unknown transport protocol` | 客户端实测 |
| 日志直接打完整 URL | 订阅 token 会进公开的 Actions 日志 | 代码审计 |
| 非原子写 | 中断留下半截 JSON，下一轮整页空白 | 故障演练 |
| 单源无总预算 | 半死的源能把采集拖到 workflow 超时 | 演练中第 1 项跑了 46 秒才暴露 |

九个问题里五个是「客户端/演练」发现的，不是读代码发现的。这也是为什么 P14–P15 值得真跑一遍。

### 一次被数据打脸的推断

修 reality 误标时，我顺手把它写成了 anaer 可用率低的原因：那个源 90 条只通 2 条，而它的 1,796 条里有 1,450 条被误标成 reality。时间上挨着、数量级也对得上，看起来完全成立。

修完再跑一轮 CI，anaer 还是 **0/90**。原因是 mihomo 对「reality 但没有公钥」会当普通 TLS 处理，照样能连——误标真正影响的是 sing-box（拒绝整份配置）和页面上的安全方式标注，不是 mihomo 的握手。anaer 连不上是它节点本身的问题。

两个现象挨在一起，不代表因果。这条留在这里，因为它正是本项目一直在防的那种错误：**改完一个 bug，顺手给它记一份功劳**。commit `f582838` 的说明里那句话是错的，我在 `nodes_parse.py` 的注释里已更正，这里再记一次。
