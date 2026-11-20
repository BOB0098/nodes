# 节点配置链接

每天自动收集公开免费节点订阅，只做一件事：把各仓库**原样发布**的订阅链接整理到一页。

打开网页 → 复制任意一条链接 → 粘进客户端 → 更新即可。

## 网页

启用 GitHub Pages 后访问：

```
https://<你的用户名>.github.io/nodes/
```

## 每天自动更新

`.github/workflows/daily.yml` 每天北京时间 06:00 跑一次 `collector.py`：

1. 并发拉取各仓库订阅源
2. 统计条数、过滤掉一天内未更新的源
3. 重建 `index.html` 与 `data/nodes.json`
4. 自动 commit 回本仓库（Pages 随之更新）

也可以手动触发：Actions → 每日更新节点数据 → Run workflow。

## 客户端

| 订阅类型 | 内核 | 客户端 |
| --- | --- | --- |
| Clash 系 | mihomo | Clash Verge Rev（电脑）、FlClash（手机/电脑）、Clash Meta for Android |
| V2Ray 系 | Xray | v2rayNG（安卓）、v2rayN（电脑）、Shadowrocket（iOS）、Karing |
| sing-box 系 | sing-box | Hiddify（手机/电脑）、sing-box（安卓/苹果） |

## 说明

- 链接均为各仓库原样发布，本项目不改写、不合并、不去重。
- 条数在打开页面时实时读取，读不到时显示最近一次采集值。
- 免费节点随时失效，一条不通就换下一条。

## 本地运行

```bash
python3 collector.py      # 采集并重建 index.html
open index.html           # 或直接双击
```
