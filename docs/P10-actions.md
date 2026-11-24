# P10：GitHub Actions 编排

> 任务书 P10 建议拆成 9 个 job（discovery / collect / parse / dedupe / test /
> score / export / deploy）。本项目的结论是**只拆了两个**，理由如下。

## 1. 现在怎么跑

```
daily.yml（单 job，顺序执行）
  ├─ 新鲜度闸门            <120 分钟则跳过
  ├─ 安装依赖
  ├─ collector.py          采集 15 源 → 解析 → 去重 → 渲染页面
  ├─ nodecheck.py          抽样 1200 条 → mihomo 真握手 → 延迟/出口 IP
  ├─ nodescore.py          评分 → 标签 → 地区
  ├─ collector.py --render 把真实可用率写回卡片
  ├─ export_subs.py        4 种格式 × 12 个分组 + 反解校验
  ├─ git commit & push
  └─ 上传 artifact（订阅文件含凭据，保留 1 天）
```

实测单轮 **18 分钟**，其中采集 8 分钟、检测 7 分钟、其余 3 分钟。

## 2. 为什么不拆成 9 个

拆 job 的价值是「并行」和「失败隔离」。逐条对照：

| 建议拆分 | 评估 | 结论 |
|---|---|---|
| source-discovery | 每周一次，已经是独立 workflow | 已拆 |
| source-collect / node-parse / node-deduplicate | 同一份下载数据的三步，串行执行、共享内存 | **不拆**：拆开要把 10 MB 原始订阅通过 artifact 传三次 |
| node-test | 耗时最长，理论上可 matrix 并行 | 见下 |
| node-score / node-export | 各 30 秒，依赖检测结果 | **不拆**：30 秒的收益抵不上 artifact 往返 |
| pages-deploy | GitHub Pages 自带 workflow | 已由平台负责 |

**node-test 为什么也没拆**：GitHub hosted runner 是 2 vCPU，检测的瓶颈是
网络 IO 不是 CPU，单机 20 并发已经打满出口带宽。拆成 4 个 runner 要各自
checkout（~10 秒）+ 装 mihomo（缓存后 ~3 秒）+ 下载 export artifact，
净收益不到 3 分钟，换来的是 4 份部分结果需要用 artifact 合并。

但**接口已经留好**：`nodecheck.py --shard i --shards n` 会把同一份抽样切成
n 份，各跑一份、各自落 `data/nodecheck.shardN.json`。节点数再涨一倍时，
改 workflow 加一个 matrix 就行，不用动检测逻辑。

## 3. 已有的工程约束

| 要求 | 实现 |
|---|---|
| 分批 | 每批 40 个节点一个 mihomo 进程，避免单进程持有上千 listener |
| 增量 | 抽样种子按天轮换：同一天内多轮抽同一批（波动可比），跨天换一批（一周覆盖全库） |
| 缓存 | mihomo 二进制走 actions/cache，key 带版本号 |
| 超时 | 单节点 8s、curl 硬超时 11s、检测步骤 40 分钟上限 |
| 有限重试 | 订阅下载每地址重试 2 次（共 3 次尝试），检测不重试——失败就记为不可用 |
| 任务锁 | `concurrency: collect`，同一时间只跑一个 |
| 并发上限 | 采集 6 线程、检测 20 并发、每批 40 节点 |
| 失败不覆盖 | 采集条数低于上轮 60% 直接放弃；检测自检不通过则不写数据；各步骤 `continue-on-error` |

## 4. 失败时不会毁掉上一轮数据

这是任务书特别强调的一条，实现方式是「先算完再落盘」：

- 采集阶段若总条数 < 上轮 60%，直接 `return`，`nodes.json` 不动
- 检测的 DIRECT 自检不通过，直接退出，`nodecheck.json` 保持上轮
- 订阅生成时任一格式校验失败，`subscriptions.json` 的 `failed` 会列出，
  但文件仍写入（方便排查），不影响已提交的数据
- 所有步骤都能 `continue-on-error`，最后一步提交时如果内容没变就跳过提交

## 5. 免费额度

单轮 18 分钟 × 每天 8 轮 = 2.4 小时/天 ≈ 72 小时/月。公开仓库的 Actions
是免费的，但如果将来转私有仓库，这个量级会吃掉免费额度（2000 分钟/月）的
两倍多。那时候第一件事是把检测频率降到每天 2–4 轮，而不是削减检测内容。
