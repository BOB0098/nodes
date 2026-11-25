# P20 订阅系统与发布可靠性报告

> 阶段 4（P20）实施记录。目标仓库 `BOB0098/nodes`，基于 main 分支 `2c69f4b7`（P19 完成态）。
> 本报告所有数字来自 2026-11-25 14:15 那一轮真实产物，未虚构任何检测结果。

## 1. 开工前核对的事实

按任务书要求先核验现状，不沿用旧结论：

| 核对项 | 实际状态 |
|---|---|
| 订阅分组 | 12 组（all / best / stable / lowlat / fast / hk / tw / jp / sg / us / kr / eu）× 4 格式（clash / singbox / uri / b64） |
| 订阅入口兼容 | `export/subs/<组>.<格式>` 路径与文件名未变；页面复制/二维码/手动复制备用入口齐全（P15 已验证） |
| 原子写 | `netsafe.write_atomic()` 已在所有 JSON 与订阅产物使用（临时文件 + fsync + rename） |
| 反解校验 | `export_subs.verify()` 生成即用自己的解析器读回，条数不符即失败（已覆盖四种格式） |
| 客户端内核导入 | P15 已用 Mihomo 12/12、sing-box 12/12、Xray 9,845 条 `Configuration OK` 真实验证 |
| 回滚机制 | `restore.py` 支持 list / diff / to / undo，git 历史即快照（不额外存副本） |
| **统一 manifest** | **缺失** —— 这是 P20 的真实缺口（见下） |

## 2. 发现并修复的真实问题

### 问题：时间戳口径分裂，相差 13 分钟被报成「差 0 分钟」

`check_consistency.py` 第 4 项用正则 `(\d{2})-(\d{2})\s+(\d{2}):(\d{2})` 解析时间戳：

- `nodes.json` 写 `11-25 14:02` → 能解析
- `subscriptions.json` 写 `2026-11-25 14:15` → **正则不匹配，返回 `None`，被排除在比对之外**

结果日志显示「时间戳同一轮（差 0 分钟）：nodes 14:02 / page 14:02」——其实只比了格式相同的两项，
真正相差 13 分钟的订阅/检测/榜单全被跳过。数据版本错位就这么漏过发布门禁。
这也是检查脚本里一句注释「P20 引入 manifest 后这一项会变成硬性要求」长期没落地的原因。

### 修复：统一构建 manifest + 硬校验

新增 `build_manifest.py`，在每轮全部产物落盘后运行（CI 中放在 `collector.py --render` 之后），
生成 `data/build_manifest.json`：

- **build_id**：数据基准时间 + 9 个产物哈希的摘要（前 10 位），同输入必得同 id，可复现
- **generated_at**：ISO 8601，明确 `+08:00` 时区
- **norm_ts()**：统一解析三种时间写法（`11-25 14:02` / `2026-11-25 14:15` / epoch 秒），
  无年份的用带年份产物补全；无法解析返回 `None`，绝不伪造 0
- **counts**：统一口径计数（节点总数 / 去重 / 实测 / 真实可用 / 12 组订阅 / 4 类榜单）
- **artifacts**：9 个文件逐个记录 sha256、字节数、原始时间戳、规范化时间戳

修复后的同一个检查输出变成：

```
时间戳同一轮（差 13 分钟，9 个产物）：index_html 06:02 / nodes 06:02 / nodecheck 06:15 /
scores 06:15 / picks 06:15 / subscriptions 06:15 / status 06:02 / governance 06:15 / trend 06:15
```

9 个产物全部纳入，差 13 分钟如实显示，且在 90 分钟阈值内判为同一轮。

## 3. 改动文件清单

| 文件 | 改动 |
|---|---|
| `build_manifest.py`（新增） | 统一构建清单生成器，含时间规范化、sha256、计数、可复现 build_id、13 项自测 |
| `data/build_manifest.json`（新增） | 本轮生成的清单实体，纳入版本库随数据同提交 |
| `check_consistency.py` | 时间解析改走 `norm_ts()`；新增第 8 项 manifest 硬校验（逐产物哈希 + 计数 + 时间戳）；无 manifest 时降级为旧口径以兼容历史数据 |
| `drill_publish.py`（新增） | 发布失败回滚演练：备份→注入坏产物→验证拦下→验证备份完好→恢复→复验，不改仓库 |
| `restore.py` | 白名单加入 `data/build_manifest.json`，保证回滚时清单与产物一起回退 |
| `.github/workflows/daily.yml` | render 之后插入「生成统一构建清单」步骤；一致性检查明确为无 `continue-on-error` 的发布门禁；提交说明补充 manifest |
| `README.md` | 「安全与回滚」补 manifest 机制说明与三条新命令；本地运行清单补齐四个脚本 |

## 4. 测试命令与真实输出

### 4.1 全部自测

```
$ python3 build_manifest.py --selftest
  ✔ 「11-25 14:02」与「2026-11-25 14:02」规范化为同一时刻
  ✔ build_id 可复现（同输入同 id）
  ✔ 覆盖全部 9 个产物
  ✔ sha256 被改后校验失败
  ... 全部通过

$ python3 export_subs.py --selftest   → 全部通过
$ python3 netsafe.py --selftest       → 全部通过
$ python3 -c "import yaml; yaml.safe_load(open('.github/workflows/daily.yml'))" → OK
```

### 4.2 发布门禁：无 manifest → 有 manifest → 篡改

```
# 无 manifest：降级旧口径，仍如实报出 13 分钟差
$ python3 check_consistency.py
一致性检查（无 manifest，旧口径）
  ✔ 时间戳同一轮（差 13 分钟，3 个产物）
  ! 没有 build_manifest.json，跳过产物哈希校验
结论：全部一致，1 项警告 → 允许发布

# 生成 manifest 后：9 个产物全部纳入，哈希吻合
$ python3 build_manifest.py --write
写入 data/build_manifest.json：build_id=20261125T1415-a47216686f
  9 个产物  节点 10059  实测 952  真实可用 315（33.1%）

$ python3 check_consistency.py
  ✔ manifest 覆盖的 9 个产物 sha256 全部吻合
  ✔ manifest build_id=20261125T1415-a47216686f
结论：全部一致 → 允许发布

# 篡改 nodes.json 计数（不重生成 manifest）：三处同时报错，退出码 1
  ✘ nodes.json 条数 10059 ≠ stats.dedup 10058
  ✘ manifest 产物校验失败：data/nodes.json 已被改动（与 manifest 哈希不符）
  ✘ manifest.dedup=10059 ≠ 磁盘 10058
结论：3 项不一致 → 拦下发布   [退出码 1]
```

### 4.3 发布失败回滚演练

```
$ python3 drill_publish.py
  ✔ manifest 覆盖 9 个可备份产物
  ✔ 演练前基线门禁通过（当前产物是「上一版」）
  已备份 9 个产物到 .drill-*/last_good
  注入故障：nodes.json 计数被改（模拟产物半写），manifest 未更新
  ✔ 坏产物被门禁拦下（退出码 1）
  ✔ 拦截原因是产物与 manifest 不一致
  ✔ 备份的上一版 nodes.json 完好无损
  ✔ 从备份恢复后门禁重新通过（可安全发布）
  ✔ 恢复后产物与演练前逐字节一致
演练通过：失败被拦下，上一版完好，恢复后正常
```

### 4.4 订阅端到端生成（小样本）

用项目自身的 `nodes_parse.to_uri()` 造 3 条合法节点（vmess / anytls / ss），
`export_subs.py` 四种格式全部反解校验通过，退出码 0，`all.yaml` 有 3 个 proxy、
`all.txt` 有 3 行。临时 `export/`（含凭据）演练后已删除，符合不入库设计。

> 过程中刻意用残缺 URI 与非 UUID 凭据做过两次测试，都被反解校验拦下（退出码 1）。
> 这不是代码缺陷，恰证明「生成即读回」的门禁有效：脏数据在导出前就被挡住。

### 4.5 客户端导入

P15 已用真实客户端内核验证（见 `docs/P15-clients.md`）：Mihomo 12/12、sing-box 12/12、
Xray 9,845 条 `Configuration OK`。P20 未改订阅格式、文件名与生成逻辑（只新增校验层），
该结论继续有效。本地沙箱无 Mihomo/sing-box/Xray 二进制缓存，未重复跑内核导入——
如实说明，不冒充本机测试结果。

## 5. 本轮真值（manifest build_id `20261125T1415-a47216686f`）

| 指标 | 值 |
|---|---|
| 节点总数 / 去重后 | 10,059 / 10,059 |
| 真实握手实测 | 952 条 |
| 真实可用 | 315 条（33.1%） |
| 订阅分组 | 12 组全部与页面/榜单计数一致 |
| manifest 覆盖产物 | 9 个，sha256 全部吻合 |

## 6. 发布保护链路（CI 实际顺序）

```
采集 → 真握手 → 评分 → 治理 → 榜单 → 订阅 → 重渲染页面
  → 生成统一构建清单（build_manifest.py --write）
  → 一致性检查（check_consistency.py，无 continue-on-error）
  → git add + commit + push
  → 上传含凭据的 export/ 为 artifact（1 天，不入库）
  → 清 jsDelivr 缓存
```

一致性检查失败即中断 job，下面的提交不会执行，远端 main 保留上一版可用产物。

## 7. 未解决问题 / 已知限制

1. **Hiddify GUI 导入未测**：内核与 sing-box 一致，命令行检查通过，但无免登录 GUI 可做端到端导入（P15 已如实记录）。
2. **本机客户端内核未复跑**：沙箱无二进制缓存，客户端导入沿用 P15 真实结论；下次 CI 有缓存时可补跑。
3. **`best` 订阅阈值 65 分**：待两三轮历史积累后重估（P18 遗留，P20 未改评分口径）。
4. **令牌未撤销**：按用户要求，「项目迭代完后晚点再撤销」，P22 前仍有效。
5. **时间戳窗口**：manifest 用 90 分钟阈值判同一轮；若某轮检测耗时异常超过 90 分钟，会被判为跨轮而拦下——这是刻意的保守设计，需人工确认后放行。

## 8. 回滚方式

- 单轮产物回退：`python3 restore.py to <sha>`（manifest 已在白名单，会一起回退），`undo` 可撤销。
- 代码层回退：本阶段改动集中在 7 个文件，按 commit 反向操作即可；manifest 与产物必须同版本回退，否则一致性检查会拦截。
