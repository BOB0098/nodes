# P2 候选来源审核报告

> 自动生成于 `screen.py`。比对基准：现有节点库 **10502** 条。

> 判定口径：HTTP 必须 200；解析 ≥20 条；独立新增 ≥10%（低于 30% 记 watch）；
> 抽样可连 ≥30%（低于 55% 记 watch）；仓库未归档、30 天内有过提交。
> 重叠率用「协议+地址」近似计算（库内已脱敏、无 uri），真实全参数指纹去重只会更低。
> **本报告不改变线上订阅，通过审核的源也需人工在 `sources.yaml` 里置 `enabled: true`。**

## 结论表

| 判定 | 仓库 | 解析 | 独立新增 | 新增占比 | 抽样可连 | 预计可用 | HTTP | 说明 |
|---|---|---|---|---|---|---|---|---|
| ⚠️ 观察 | `Surfboardv2ray/TGParse` | 8189 | 2167 | 65% | 17% | 368 | 200 | 预计可用新增 368 条（2167 × 17%），中等；自身可连率仅 17%，大量死条目会稀释库内可用率；仓库未声明许可证（合规风险，需人工确认） |
| ❌ 拒绝 | `MhdiTaheri/V2rayCollector` | 1845 | 945 | 81% | 4% | 38 | 200 | 预计可用新增仅 38 条（945 新增 × 可连 4%），低于 250 门槛；自身可连率仅 4%，大量死条目会稀释库内可用率 |
| ❌ 拒绝 | `rtwo2/FastNodes` | 998 | 440 | 66% | 21% | 92 | 200 | 预计可用新增仅 92 条（440 新增 × 可连 21%），低于 250 门槛 |
| ❌ 拒绝 | `freenodess/freenodess` | 1751 | 403 | 72% | 4% | 16 | 200 | 预计可用新增仅 16 条（403 新增 × 可连 4%），低于 250 门槛；自身可连率仅 4%，大量死条目会稀释库内可用率；仓库未声明许可证（合规风险，需人工确认） |
| ❌ 拒绝 | `igareck/vpn-configs-for-russia` | 767 | 165 | 67% | 38% | 63 | 200 | 预计可用新增仅 63 条（165 新增 × 可连 38%），低于 250 门槛 |
| ❌ 拒绝 | `peasoft/NoMoreWalls` | 307 | 105 | 37% | 46% | 48 | 200 | 预计可用新增仅 48 条（105 新增 × 可连 46%），低于 250 门槛 |
| ❌ 拒绝 | `chengaopan/AutoMergePublicNodes` | 282 | 75 | 29% | 46% | 34 | 200 | 预计可用新增仅 34 条（75 新增 × 可连 46%），低于 250 门槛 |
| ❌ 拒绝 | `xiaoji235/airport-free` | 21 | 18 | 86% | 43% | 8 | 200 | 预计可用新增仅 8 条（18 新增 × 可连 43%），低于 250 门槛；仓库未声明许可证（合规风险，需人工确认） |
| ❌ 拒绝 | `share-daily/node` | 18 | 12 | 100% | 83% | — | 200 | 只解析出 18 条，量不足以单独成卡 |
| ❌ 拒绝 | `free-nodes/clashfree` | — | — | — | — | — | 200 | 只解析出 0 条，量不足以单独成卡 |
| ⏭ 跳过 | `nodesradar/nodesradar-en` | 30 | 18 | 67% | 12% | — | 200 | 不是节点发布源（index），不参与节点库；可用于自动发现 |
| ⏭ 跳过 | `Leon406/SubCrawler` | 6 | 6 | 100% | 17% | — | 200 | 不是节点发布源（index），不参与节点库；可用于自动发现 |
| ⏭ 跳过 | `735754647/Free-Nodes` | — | — | — | — | — | 200 | 不是节点发布源（tool），不参与节点库；可用于自动发现 |
| ⏭ 跳过 | `learnLi/free-nodes` | — | — | — | — | — | 200 | 不是节点发布源（tool），不参与节点库；可用于自动发现 |
| ⏭ 跳过 | `Pawdroid/Free-servers` | — | — | — | — | — | — | 已在 sources.yaml 中启用，不重复审核 |
| ⏭ 跳过 | `aiboboxx/v2rayfree` | — | — | — | — | — | — | 已在 sources.yaml 中启用，不重复审核 |
| ⏭ 跳过 | `ermaozi/get_subscribe` | — | — | — | — | — | — | 已在 sources.yaml 中启用，不重复审核 |
| ⏭ 跳过 | `Barabama/FreeNodes` | — | — | — | — | — | — | 已在 sources.yaml 中启用，不重复审核 |
| ⏭ 跳过 | `MatinGhanbari/v2ray-configs` | — | — | — | — | — | — | 已在 sources.yaml 中启用，不重复审核 |

## 逐个说明

### `Surfboardv2ray/TGParse` — watch

- 数据文件：`https://raw.githubusercontent.com/Surfboardv2ray/TGParse/main/splitted/mixed`
- 体积：2628 KB
- 解析 8189 条 / 唯一指纹 4642 / 与现有库重合 6022 条
- 协议分布：{'hysteria2': 82, 'socks5': 30, 'ss': 1517, 'trojan': 702, 'vless': 5340, 'vmess': 518}
- 判定理由：预计可用新增 368 条（2167 × 17%），中等；自身可连率仅 17%，大量死条目会稀释库内可用率；仓库未声明许可证（合规风险，需人工确认）
- 备注：Telegram 频道抓取；仓库里有 50+ 份历史归档，会持续膨胀

### `MhdiTaheri/V2rayCollector` — reject

- 数据文件：`https://raw.githubusercontent.com/MhdiTaheri/V2rayCollector/main/sub/mix`
- 体积：546 KB
- 解析 1845 条 / 唯一指纹 1232 / 与现有库重合 900 条
- 协议分布：{'ss': 912, 'trojan': 92, 'vless': 696, 'vmess': 145}
- 判定理由：预计可用新增仅 38 条（945 新增 × 可连 4%），低于 250 门槛；自身可连率仅 4%，大量死条目会稀释库内可用率
- 备注：MIT；按协议拆分（mix/vless/ss/...），取混合版

### `rtwo2/FastNodes` — reject

- 数据文件：`https://raw.githubusercontent.com/rtwo2/FastNodes/main/sub/top.txt`
- 体积：325 KB
- 解析 998 条 / 唯一指纹 916 / 与现有库重合 558 条
- 协议分布：{'trojan': 112, 'vless': 742, 'vmess': 144}
- 判定理由：预计可用新增仅 92 条（440 新增 × 可连 21%），低于 250 门槛
- 备注：MIT；主文件被 GitHub 50MB 上限截断（everything.txt 恰为 52,428,780 字节），只取 top.txt

### `freenodess/freenodess` — reject

- 数据文件：`https://raw.githubusercontent.com/freenodess/freenodess/main/nodes/clash.yaml`
- 体积：601 KB
- 解析 1751 条 / 唯一指纹 1029 / 与现有库重合 1348 条
- 协议分布：{'hysteria2': 171, 'ss': 134, 'trojan': 234, 'vless': 1002, 'vmess': 210}
- 判定理由：预计可用新增仅 16 条（403 新增 × 可连 4%），低于 250 门槛；自身可连率仅 4%，大量死条目会稀释库内可用率；仓库未声明许可证（合规风险，需人工确认）
- 备注：三种格式并存（clash.yaml / singbox.json / v2ray-base64.txt），同源同批

### `igareck/vpn-configs-for-russia` — reject

- 数据文件：`https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/Export/Clash/RU_DIRECT/BLACK_VLESS_RUS_clash_rudirect.yaml`
- 体积：431 KB
- 解析 767 条 / 唯一指纹 619 / 与现有库重合 602 条
- 协议分布：{'vless': 767}
- 判定理由：预计可用新增仅 63 条（165 新增 × 可连 38%），低于 250 门槛

### `peasoft/NoMoreWalls` — reject

- 数据文件：`https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.txt`
- 体积：76 KB
- 解析 307 条 / 唯一指纹 304 / 与现有库重合 202 条
- 协议分布：{'hysteria2': 51, 'socks5': 15, 'ss': 76, 'trojan': 23, 'vless': 141, 'vmess': 1}
- 判定理由：预计可用新增仅 48 条（105 新增 × 可连 46%），低于 250 门槛
- 备注：老牌项目，带 list.yml/meta.yml；LICENSE.md 为 NOASSERTION，需人工看

### `chengaopan/AutoMergePublicNodes` — reject

- 数据文件：`https://raw.githubusercontent.com/chengaopan/AutoMergePublicNodes/master/list.txt`
- 体积：72 KB
- 解析 282 条 / 唯一指纹 276 / 与现有库重合 207 条
- 协议分布：{'hysteria2': 49, 'socks5': 6, 'ss': 73, 'trojan': 22, 'vless': 131, 'vmess': 1}
- 判定理由：预计可用新增仅 34 条（75 新增 × 可连 46%），低于 250 门槛
- 备注：与 NoMoreWalls 高度相似（fetch.py 同为 63,200B、LICENSE 同为 2,113B），疑为派生，需看去重后还剩多少

### `xiaoji235/airport-free` — reject

- 数据文件：`https://raw.githubusercontent.com/xiaoji235/airport-free/main/clash/clashnodecc.txt`
- 体积：3674 KB
- 解析 21 条 / 唯一指纹 21 / 与现有库重合 3 条
- 协议分布：{'vless': 19, 'vmess': 2}
- 判定理由：预计可用新增仅 8 条（18 新增 × 可连 43%），低于 250 门槛；仓库未声明许可证（合规风险，需人工确认）
- 备注：单文件 3.7MB；另有 15MB 版本，体积异常需核对是否重复堆叠

### `share-daily/node` — reject

- 数据文件：`https://raw.githubusercontent.com/share-daily/node/main/clash.yaml`
- 体积：33 KB
- 解析 18 条 / 唯一指纹 12 / 与现有库重合 6 条
- 协议分布：{'ss': 16, 'vmess': 2}
- 判定理由：只解析出 18 条，量不足以单独成卡
- 备注：仅 33KB，量小

### `free-nodes/clashfree` — reject

- 数据文件：`https://raw.githubusercontent.com/free-nodes/clashfree/main/clash20261122.yml`
- 体积：621 KB
- 解析 0 条 / 唯一指纹 0 / 与现有库重合 0 条
- 判定理由：只解析出 0 条，量不足以单独成卡

### `nodesradar/nodesradar-en` — skip

- 数据文件：`https://raw.githubusercontent.com/nodesradar/nodesradar-en/main/README.md`
- 体积：12 KB
- 解析 30 条 / 唯一指纹 30 / 与现有库重合 12 条
- 协议分布：{'hysteria2': 6, 'ss': 18, 'vmess': 6}
- 判定理由：不是节点发布源（index），不参与节点库；可用于自动发现
- 备注：仓库只有 README，无数据文件；真正的数据在 radarhub.top，已在册

### `Leon406/SubCrawler` — skip

- 数据文件：`https://raw.githubusercontent.com/Leon406/SubCrawler/main/sub/subs.txt`
- 体积：1 KB
- 解析 6 条 / 唯一指纹 6 / 与现有库重合 0 条
- 协议分布：{'ss': 2, 'trojan': 2, 'vmess': 2}
- 判定理由：不是节点发布源（index），不参与节点库；可用于自动发现
- 备注：subs.txt 只有 1.5KB，是「订阅地址清单」不是节点；适合喂自动发现

### `735754647/Free-Nodes` — skip

- 数据文件：`https://raw.githubusercontent.com/735754647/Free-Nodes/main/config/sources.txt`
- 体积：2 KB
- 解析 0 条 / 唯一指纹 0 / 与现有库重合 0 条
- 判定理由：不是节点发布源（tool），不参与节点库；可用于自动发现
- 备注：仓库本体是 subbench 测速工具（含 61MB mihomo 二进制），不是节点发布源；只有一份 sources.txt 源清单，可作为自动发现的种子

### `learnLi/free-nodes` — skip

- 数据文件：`https://raw.githubusercontent.com/learnLi/free-nodes/main/config.yml`
- 体积：88 KB
- 解析 0 条 / 唯一指纹 0 / 与现有库重合 0 条
- 判定理由：不是节点发布源（tool），不参与节点库；可用于自动发现
- 备注：config.yml 的 proxies 为空数组，仓库发布的是空模板 + fetch/probe/prune 工具链，节点经 release 或运行期产出，不是可直接订阅的发布源

### `Pawdroid/Free-servers` — skip

- 解析 — 条 / 唯一指纹 — / 与现有库重合 0 条
- 判定理由：已在 sources.yaml 中启用，不重复审核
- 备注：已在册

### `aiboboxx/v2rayfree` — skip

- 数据文件：`https://raw.githubusercontent.com/free-nodes/v2rayfree/main/sub`
- 体积：0 KB
- 解析 — 条 / 唯一指纹 — / 与现有库重合 0 条
- 判定理由：已在 sources.yaml 中启用，不重复审核
- 备注：301 到 free-nodes/v2rayfree；两仓库 README(37,608B) 与 sub(5,060B) 字节数完全一致，同一次改名，不得重复接入

### `ermaozi/get_subscribe` — skip

- 解析 — 条 / 唯一指纹 — / 与现有库重合 0 条
- 判定理由：已在 sources.yaml 中启用，不重复审核
- 备注：已在册（3 张卡片）

### `Barabama/FreeNodes` — skip

- 解析 — 条 / 唯一指纹 — / 与现有库重合 0 条
- 判定理由：已在 sources.yaml 中启用，不重复审核
- 备注：已在册

### `MatinGhanbari/v2ray-configs` — skip

- 数据文件：`https://raw.githubusercontent.com/MatinGhanbari/v2ray-configs/main/subscriptions/base64/all_sub.txt`
- 体积：0 KB
- 解析 — 条 / 唯一指纹 — / 与现有库重合 0 条
- 判定理由：已在 sources.yaml 中启用，不重复审核
- 备注：MIT；1.6MB base64 合集，按协议分目录
