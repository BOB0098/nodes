# P9：存储方案评估（SQLite vs JSON）

> 任务书 P9 要求「评估 SQLite 保存节点、来源、测速、失效/恢复和评分历史；
> 保留现有 health.json 功能」。本文是评估结论，**结论是不引入 SQLite**，
> 理由与实测数据如下。

## 1. 现在存了什么

| 文件 | 大小 | 内容 | 更新频率 |
|---|---:|---|---|
| `data/nodes.json` | 1.6 MB | 10,520 条脱敏节点 + 卡片数据 | 每轮 |
| `data/node_lifecycle.json` | 347 KB | 10,520 个指纹的首次/最近出现 | 每轮 |
| `data/nodecheck.json` | 130 KB | 997 条真实检测结果 | 每轮 |
| `data/node_scores.json` | 148 KB | 节点评分、标签、地区 | 每轮 |
| `data/node_score_history.json` | 92 KB | 每个指纹的连续成功/失败轮次 | 每轮 |
| `data/sources_stats.json` | 21 KB | 15 个源的解析/去重/可连 | 每轮 |
| `data/source_history.json` | 12 KB | 30 轮评分历史 | 每轮 |
| `data/quarantine.json` | 13 KB | 隔离区汇总 | 每轮 |
| `data/health.json` | 4 KB | 每源连续失败轮次 | 每轮 |

合计约 2.4 MB，全部是 JSON。

## 2. SQLite 好在哪、坏在哪

好的部分很明确：单文件、事务、SQL 查询、索引、跨表 join。如果这个项目要跑在
一台常驻服务器上，用 SQLite 存节点 + 检测历史 + 评分历史，几乎是标准答案。

**但本项目的约束不是查询，是「状态必须存在 git 里」。**

GitHub Actions 每次都是全新 checkout，没有持久磁盘。唯一的持久化就是提交回
仓库。这就把「数据库选型」变成了「git 友好度」问题：

- SQLite 是**二进制单文件**，一个字节的改动会让整页变脏，git 几乎无法做 delta
- JSON 是文本行结构，改几十条节点只影响对应的几百行，delta 压缩效率极高

## 3. 实测

用当前 10,520 条节点建了一张表（含 fp/name/proto/addr/ok/ms/cc/asn/isp/score/
tags 等 17 个字段 + 一轮检测历史），和 `nodes.json` 对比：

| 指标 | SQLite | JSON |
|---|---:|---:|
| 单份文件 | 3.3 MB | 1.6 MB |
| 两版本在 git 里打包后 | **532 KB** | **170 KB** |
| 单版本增量 | ~266 KB | ~85 KB |
| 每天 8 轮的增量（估算） | **2.1 MB** | 0.68 MB |
| 一年（估算） | **~770 MB** | ~250 MB |

同样的数据，SQLite 在 git 里是 JSON 的 **3.1 倍**。原因就是第 2 节说的：
SQLite 的页结构让每次提交都接近全量新 blob，而 JSON 的 delta 能压到很小。

770 MB/年 对 clone 是灾难级的。这个数字直接否掉了方案。

## 4. 结论

**不引入 SQLite 到仓库。** 现有 JSON 方案继续，理由：

1. git 不友好（3.1 倍膨胀，且无法 delta）
2. 没有并发写需求——CI 每轮是全新进程、全新 checkout，事务和锁都无意义
3. 所有查询都是「一次算完写文件」，没有 ad-hoc 查询场景
4. SQLite 的索引优势在这个数据量（1 万行）下体现不出来，读全量 JSON 只要十几毫秒

## 5. 什么情况下应该改

如果出现下面任一情况，就应该认真考虑 SQLite（放到 artifact 或外部存储，不进仓库）：

- 需要在 Actions 之外做交互式查询（比如一个看板要按地区/协议/时间切片）
- 检测轮次积累到几百轮，需要按时间窗口聚合（「过去 7 天可用率」）
- 节点规模到 10 万级，JSON 全量读写的耗时才成为问题

## 6. 归档策略（保持现状的前提下）

JSON 方案最大的隐患不是单轮体积，是**历史无限增长**。目前已经做的：

- `source_history.json` 只保留 30 轮
- `node_lifecycle.json` 只保留 45 天内出现过的指纹
- `node_score_history.json` 只保留连续成功/失败计数，不留逐轮明细

不做的：把每轮 `nodecheck.json` 都留档。那会以 130 KB/轮 × 8 轮/天 的速度
膨胀，而且历史检测结果的价值随时间衰减极快——三天前的「可用」说明不了今天。
真要趋势，看 `source_history.json` 的评分曲线就够了。
