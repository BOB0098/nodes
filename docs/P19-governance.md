# P19 来源治理报告

> 由 `governance.py` 生成于 2026-11-25 14:15。不联网、不改 sources.yaml；所有结论取自当轮采集与审核产物。

白名单：`sources.yaml` 登记 26 个，启用 15 个。新源必须过 `screen.py` 四关并人工置 enabled；治理层无权自行接入。

## 1. 重复与重合

| 类型 | 源 A | 源 B | 证据 | 处置 |
|---|---|---|---|---|
| same-repo | 尔毛子·可连Clash | 尔毛子·全量Clash | 同一仓库：gh:ermaozi/get_subscribe | 定位差异，保留但看重合度 |
| same-repo | 尔毛子·可连Clash | 尔毛子·V2Ray | 同一仓库：gh:ermaozi/get_subscribe | 定位差异，保留但看重合度 |
| same-repo | 尔毛子·全量Clash | 尔毛子·V2Ray | 同一仓库：gh:ermaozi/get_subscribe | 定位差异，保留但看重合度 |

内容重合（近似口径，见注）：

| 源 A | 源 B | 共有 | Jaccard | 判定 |
|---|---|---:|---:|---|
| radarhub | radarhub·singbox | 283 | 100% | 高度重合 |
| 尔毛子·可连Clash | 尔毛子·全量Clash | 208 | 93% | 高度重合 |
| 尔毛子·全量Clash | bzfq21 | 199 | 80% | 高度重合 |
| 尔毛子·全量Clash | 尔毛子·V2Ray | 218 | 78% | 部分重合 |
| 尔毛子·可连Clash | bzfq21 | 184 | 74% | 部分重合 |
| 尔毛子·可连Clash | 尔毛子·V2Ray | 203 | 72% | 部分重合 |
| 尔毛子·V2Ray | bzfq21 | 203 | 68% | 部分重合 |

> 数据来自 source_keys.json（2026-11-25 14:02）。地址以 sha256 前 16 位存储（明文不落盘）。旧轮次无 source_keys.json 时用去重后的 nodes.json 兜底，那种口径下重合恒为 0，不能当成「没有重合」。样本 <20 条不判。


## 2. 逐源指标

| 源 | 状态 | 抓取成功率 | 最近成功 | 连败 | 真实可用 | 重复率 | 观察期 |
|---|---|---:|---|---:|---:|---:|---|
| 尔毛子·可连Clash | 正常 | 100.0% | 11-25 14:02 | 0 | 56/90（62.2%） | 100.0% | 否 |
| radarhub | 正常 | 100.0% | 11-25 14:02 | 0 | 88/90（97.8%） | 99.2% | 否 |
| 尔毛子·全量Clash | 正常 | 100.0% | 11-25 14:02 | 0 | 16/76（21.1%） | 96.7% | 否 |
| anaer | 正常 | 100.0% | 11-25 14:02 | 0 | 0/90（0.0%） | 1.1% | 否 |
| Barabama | 正常 | 100.0% | 11-25 14:02 | 0 | 34/90（37.8%） | 49.9% | 否 |
| Pawdroid | 正常 | 100.0% | 11-25 14:02 | 0 | 4/6（66.7%） | 100.0% | 否 |
| 尔毛子·V2Ray | 正常 | 100.0% | 11-25 14:02 | 0 | 17/90（18.9%） | 48.4% | 否 |
| ALIILAPRO | 正常 | 100.0% | 11-25 14:02 | 0 | 4/90（4.4%） | 47.8% | 否 |
| v2rayfree | 正常 | 100.0% | 11-25 14:02 | 0 | 2/10（20.0%） | 56.5% | 否 |
| free18 | 正常 | 100.0% | 11-25 14:02 | 0 | 37/90（41.1%） | 19.6% | 否 |
| v2ray-configs | 正常 | 100.0% | 11-25 14:02 | 0 | 0/90（0.0%） | 54.7% | 否 |
| radarhub·singbox | 正常 | 100.0% | 11-25 14:02 | 0 | 12/12（100.0%） | 97.6% | 否 |
| bzfq21 | 正常 | 100.0% | 11-25 14:02 | 0 | 30/90（33.3%） | 44.5% | 否 |
| awesome-vpn | 正常 | 100.0% | 11-25 14:02 | 0 | 15/20（75.0%） | 42.9% | 否 |
| Andrejewild | 正常 | 100.0% | 11-25 14:02 | 0 | 0/18（0.0%） | 10.0% | 否 |

## 3. 发现依据

| 源 | 依据 | 仓库 | 首次出现 | 审核结论 | 星数 | 备注 |
|---|---|---|---|---|---:|---|
| 尔毛子·可连Clash | sources.yaml 人工登记（上线前四关见 P2 审核报告） | — | — | — | — | 上游会先自测再发布，是「少而可用」路线；页面地址走作者 CDN，raw 只作镜像。 |
| radarhub | sources.yaml 人工登记（上线前四关见 P2 审核报告） | — | — | — | — | 站点无开源仓库、无许可证声明；国内直连常不通，需代理才能更新。 |
| 尔毛子·全量Clash | sources.yaml 人工登记（上线前四关见 P2 审核报告） | — | — | — | — | 与 ermao-clash-reachable 同仓库不同文件，重合度高，但定位不同（全量 vs 可连）。 |
| anaer | sources.yaml 人工登记（上线前四关见 P2 审核报告） | gh:anaer/sub | — | — | — | 仓库无 LICENSE；含少量回环地址（127.0.0.53），已在解析层过滤。 |
| Barabama | sources.yaml 人工登记（上线前四关见 P2 审核报告） | gh:barabama/freenodes | — | — | — | 该仓库有多个近义文件（merged.yaml / clashnode.yaml / freeclashnode.yaml），只取 |
| Pawdroid | sources.yaml 人工登记（上线前四关见 P2 审核报告） | gh:pawdroid/free-servers | — | — | — | 主文件 sub 仅约 2 KB（十几条），偏「精选」而非铺量；仓库无 LICENSE。 |
| 尔毛子·V2Ray | sources.yaml 人工登记（上线前四关见 P2 审核报告） | — | — | — | — | 与 Clash 版同源同批节点，只是编码不同；两卡片并存是为了客户端适配，不是两份独立库存。 |
| ALIILAPRO | sources.yaml 人工登记（上线前四关见 P2 审核报告） | gh:aliilapro/v2rayng-config | — | — | — | 单源占比过高（曾达库内 84%），是「铺量」路线的主要来源；含部分推广命名节点。 |
| v2rayfree | sources.yaml 人工登记（上线前四关见 P2 审核报告） | gh:free-nodes/v2rayfree | — | skip | — | 已在 sources.yaml 中启用，不重复审核 |
| free18 | sources.yaml 人工登记（上线前四关见 P2 审核报告） | gh:free18/v2ray | — | — | — | 同仓库另有 c.yaml（Clash 版），未接入，避免同源重复计数。 |
| v2ray-configs | sources.yaml 人工登记（上线前四关见 P2 审核报告） | gh:matinghanbari/v2ray-configs | — | skip | — | 已在 sources.yaml 中启用，不重复审核 |
| radarhub·singbox | sources.yaml 人工登记（上线前四关见 P2 审核报告） | — | — | — | — | 与 radarhub-clash 同一份节点、不同封装。二者在「独立节点比例」上算一个来源。 |
| bzfq21 | sources.yaml 人工登记（上线前四关见 P2 审核报告） | gh:bzfq21/proxy-singbox | — | — | — | 仓库仅 8 星但构建频率高；无 LICENSE，配置里含规则集，属「可用但无背书」。 |
| awesome-vpn | sources.yaml 人工登记（上线前四关见 P2 审核报告） | gh:awesome-vpn/awesome-vpn | — | — | — | 仓库另有 config/node_ledger.json（节点台账），可作为后续「来源可追溯」的参考实现。 |
| Andrejewild | sources.yaml 人工登记（上线前四关见 P2 审核报告） | gh:andrejewild/hiddify-singbox-configs-ru | — | — | — | 体量很小但独立度高；format 需在首轮采集时按实际内容确认（sub.txt 可能是 URI 列表而非 json）。 |
| AutoMergePublicNodes | screen.py 内置候选（人工核验） | gh:chengaopan/automergepublicnodes | 2026-11-24 | reject | 8438 | 预计可用新增仅 34 条（75 新增 × 可连 46%），低于 250 门槛 |
| TGParse | screen.py 内置候选（人工核验） | gh:surfboardv2ray/tgparse | 2026-11-24 | watch | 297 | 预计可用新增 368 条（2167 × 17%），中等 |
| V2rayCollector | screen.py 内置候选（人工核验） | gh:mhditaheri/v2raycollector | 2026-11-24 | reject | 323 | 预计可用新增仅 38 条（945 新增 × 可连 4%），低于 250 门槛 |
| freenodess | screen.py 内置候选（人工核验） | gh:freenodess/freenodess | 2026-11-24 | reject | 24 | 预计可用新增仅 16 条（403 新增 × 可连 4%），低于 250 门槛 |
| FastNodes | screen.py 内置候选（人工核验） | gh:rtwo2/fastnodes | 2026-11-24 | reject | 110 | 预计可用新增仅 92 条（440 新增 × 可连 21%），低于 250 门槛 |
| airport-free | screen.py 内置候选（人工核验） | gh:xiaoji235/airport-free | 2026-11-24 | reject | 146 | 预计可用新增仅 8 条（18 新增 × 可连 43%），低于 250 门槛 |
| share-daily | screen.py 内置候选（人工核验） | gh:share-daily/node | 2026-11-24 | reject | 22 | 只解析出 18 条，量不足以单独成卡 |
| learnLi-free-nodes | screen.py 内置候选（人工核验） | gh:learnli/free-nodes | 2026-11-24 | skip | 58 | 不是节点发布源（tool），不参与节点库；可用于自动发现 |
| subbench | screen.py 内置候选（人工核验） | gh:735754647/free-nodes | 2026-11-24 | skip | 1102 | 不是节点发布源（tool），不参与节点库；可用于自动发现 |
| aiboboxx-v2rayfree | screen.py 内置候选（人工核验） | gh:aiboboxx/v2rayfree | — | — | — | 重复源：301 到 free-nodes/v2rayfree，两仓库 README(37,608B) 与 sub(5,060B) |
| NoMoreWalls | screen.py 内置候选（人工核验） | gh:peasoft/nomorewalls | 2026-11-24 | reject | 3636 | 预计可用新增仅 48 条（105 新增 × 可连 46%），低于 250 门槛 |

> 地址经过脱敏（query 中的订阅 token 不写进公开报告）。审核结论来自 `data/screening.json`，`skip` 表示已在册不重复审核。

