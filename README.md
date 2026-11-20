# 节点配置链接

每天自动收集公开免费节点订阅，只做一件事：把各仓库**原样发布**的订阅链接整理到一页。

打开网页 → 复制任意一条链接 → 粘进客户端 → 更新即可。

## 网页

启用 GitHub Pages 后访问：

```
https://<你的用户名>.github.io/nodes/
```

## 每 3 小时自动更新

`.github/workflows/daily.yml` 每 3 小时跑一次 `collector.py`（cron `17 */3 * * *`，避开整点排队；GitHub 仍可能延迟，所以页面写「约 3 小时」）：

1. 并发拉取各订阅源，统计真实条数
2. 每条源抽 24 个节点测 TCP 连通率，并测源站自身 HTTP 状态
3. 用 GitHub API 查每条源最后提交时间，超过 30 小时的标「可能已停更」
4. 重建 `index.html` 与 `data/nodes.json`（含 `data/status.json`）
5. 自动 commit 回本仓库（Pages 随之更新）

带 2 小时新鲜度闸门：cron 迟到导致重复触发时直接跳过。也可以手动触发：Actions → 节点数据更新 → Run workflow。

## 源怎么挑的

每类约 5 条，三条硬标准：

- **一天内更新过**：仓库最后一次提交超过 24 小时的不进清单（页面仍会标出已加入源的实际新鲜度）
- **来源不重复**：同一仓库的全量版 + 筛选版算一条来源的两个入口，不重复计数；纯聚合器（内容是别家源的合集）只留增量最大的一个
- **有独有节点**：新源要能带来别处没有的地址。实测 `anaer/Sub` 带来 1638 个独有地址、`ALIILAPRO` 带来 2429 个，而 `radarhub` 的 V2Ray 入口独有数为 0，已从清单移除

## 客户端

| 订阅类型 | 内核 | 客户端 |
| --- | --- | --- |
| Clash 系 | mihomo | Clash Verge Rev（电脑）、FlClash（手机/电脑）、Clash Meta for Android |
| V2Ray 系 | Xray | v2rayNG（安卓）、v2rayN（电脑）、Shadowrocket（iOS）、Karing |
| sing-box 系 | sing-box | Hiddify（手机/电脑）、sing-box（安卓/苹果） |

## 说明

- 链接均为各仓库原样发布，本项目不改写、不合并、不去重。
- 条数与可连率在打开页面时读取 `data/nodes.json`，读不到时用页面内嵌的最近一次采集值。
- 「源更新于」来自仓库最后一次提交时间，与作者自称的更新频率分开显示，两者不一致时以实测为准。
- 不在 GitHub 上的源（如 radarhub）拿不到提交时间，改用内容指纹：每轮采集比对内容 md5，没变就沿用上次变化时间。这类源从第二轮采集起才有数值。
- 页面顶部两个筛选开关（可连 ≥80% / 国内可直连）只改显示，不改数据。
- 免费节点随时失效，一条不通就换下一条。

## 本地运行

```bash
python3 collector.py            # 采集并重建 index.html
python3 collector.py --render   # 只按 data/nodes.json 重渲染页面，不联网
open index.html                 # 或直接双击
```
