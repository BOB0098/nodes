# 架构

一条数据从「别人仓库里的一行文本」到「用户手机上能连的节点」，中间有六步。每一步的产物都是磁盘上的一个文件——这既是缓存，也是出问题时唯一能查的东西。

```
sources.yaml（15 个源）
      │
      ▼
①  采集 collector.py ──────────► .nodes-cache/<源名>.txt（原文，不进库）
      │   netsafe：协议白名单 / SSRF / 12MB / 90s / 每跳重定向
      ▼
②  解析 nodes_parse.py ────────► 统一节点模型（27 个字段）
      │   Clash YAML / sing-box JSON / URI 列表 / Base64
      │   入库前校验：地址、协议、UUID、reality 公钥、URI 可解析
      ├──► data/quarantine.json（隔离区，按原因计数，不含凭据）
      ▼
③  去重 + 生命周期 ────────────► data/nodes.json（脱敏，页面数据）
      │   全参数指纹 16 位 hex；lifecycle 记 [首次, 最近]
      │   export/nodes_full.json（完整参数 + 凭据，gitignore）
      ▼
④  真实检测 nodecheck.py ──────► data/nodecheck.json
      │   每个节点绑独立 socks 端口 → mihomo 真握手 → 204 才算可用
      │   DIRECT 自检不过就中止；可用数骤降 60% 不落盘
      ▼
⑤  评分 nodescore.py ──────────► data/node_scores.json
      │   可用 30 / 延迟 20 / 速度 20 / 稳定 15 / IP 质量 10 / 流媒体 5(恒 0)
      ▼
⑥  输出 export_subs.py ────────► export/subs/<组>.<4 种格式>
          collector.py --render ─► index.html（单文件，数据内嵌）
```

## 为什么是这个顺序

**采集必须在检测之前**：检测要读 `export/nodes_full.json`，那是采集的产物。`docker/run.sh` 里采集失败即停——采不到数据还往下走，检测会拿上一轮的节点列表去测，最后渲染出一份「看起来正常」的页面。

**检测必须在评分之前**：评分的最大一项（30 分）是真实可用性。顺序反了，页面上的等级全是 null。

**渲染必须在最后**：`collector.py --render` 会把 `node_scores.json` 读回来补进卡片。采集那一步也渲染一次，但那时的卡片还没有真实检测结果——这一步是覆盖，不是重复。

**订阅生成在渲染之后**：它读 `node_scores.json` 挑「优选」「低延迟」，也读 `nodes.json` 里的隔离状态剔除坏源。顺序反了会发出含隔离源节点的订阅。

## 模块依赖

```
collector.py ──┬─ nodes_parse.py ─┬─ （无外部依赖，只用 yaml）
               │                  └─ netsafe.py
               ├─ lifecycle.py ──── netsafe.py
               ├─ score.py ──────── netsafe.py
               ├─ sources_io.py ─── netsafe.py
               └─ netsafe.py

nodecheck.py ── netsafe.py（写盘）+ mihomo 二进制（外部）
nodescore.py ── netsafe.py
export_subs.py ─ nodes_parse.py + nodecheck.to_proxy（复用协议映射）
                  └─ netsafe.py

bench.py ── 以上全部（只读）
drill.py ── 以上全部（在临时目录里造故障）
clienttest.py ─ nodes_parse.py + 三个客户端二进制
```

`netsafe.py` 被所有写盘模块依赖，但它不知道任何业务语义——它只管「这个 URL 能不能访问」「这段文本要不要脱敏」「这个文件怎么写才安全」。

## 失败传播

| 哪一步失败 | 后果 | 上一轮数据 |
|---|---|---|
| 某个源采集失败 | 该源本轮无数据，卡片显示上次的值 + HTTP 码 | 保留 |
| 所有源都失败 | 采集步骤非零退出，后续全不跑 | 保留 |
| 解析异常 | 坏条目进隔离区，其余正常入库 | 保留 |
| 真实检测失败 | `continue-on-error`，页面用上一轮的可用率 | 保留 |
| 检测结果异常下滑 | 回归闸门拦下，不写 `nodecheck.json`，退出码 3 | **保留** |
| 评分失败 | 卡片等级为 null，订阅退回「只按可用性挑」 | 保留 |
| 订阅生成失败 | `subscriptions.json` 的 `failed` 里逐条记原因 | 保留上一份订阅文件 |
| 渲染失败 | 页面不更新，数据文件已写 | 数据是新的、页面是旧的（可 `--render` 补） |
| 写盘中途被中断 | 原子写：要么旧文件，要么新文件 | 保留（`restore.py undo` 可回） |

## 数据文件的三种性质

理解这一点才能理解为什么文件这么摆：

1. **进仓库的**（`data/*.json`、`index.html`）：脱敏，不含任何凭据。CI 每轮提交，git 就是历史。
2. **不进仓库的**（`export/`）：含全量 uuid / password / 订阅 token。`chmod 600`，容器里不挂到宿主机。
3. **本地缓存的**（`.nodes-cache/`、`.restore-backup/`、`data/logs/`）：可随时删，删了只是慢一轮。

## 页面为什么是单文件

`index.html` 内嵌了约 1.3 MB 的 JSON，没有外部请求、没有构建步骤。代价是首屏要解析这么多数据，收益是：

* 双击就能看，不需要起服务；
* 扔到任何静态托管都能用（Pages、对象存储、U 盘）；
* 没有「后端挂了页面白屏」这种失败模式。

一万条节点时页面 1.33 MB（实测），gzip 后约 300 KB。这个量级不值得为它引入一个前端框架。

## CI 与自建的差异

`docker/run.sh` 与 `.github/workflows/daily.yml` 的步骤顺序、失败处理一一对应，唯一不同的是：

* CI 有 `GITHUB_TOKEN`（查源最后更新时间，60 次/小时 → 5000 次/小时）；
* CI 缓存 mihomo 到 `actions/cache`，容器用 named volume；
* 容器里没有 `GITHUB_ACTIONS` 变量，所以 `NODES_FORCE_WRITE=1` 显式打开写盘。

这两条路径跑出来的数据口径一致——`docs/P11-docker-platforms.md` 里有容器内完整跑一轮的记录（6m37s）。
