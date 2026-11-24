# P1：成熟开源项目核验与集成结论

> 核验时间：2026-11-24 20:30–21:10（UTC+8）
> 核验人：自动核查脚本 + 人工交叉验证
> 原则：所有结论必须有可复现的证据；无法验证的写「未验证」，不写「应该可以」

## 0. 一句话结论

| 项目 | 任务书给的地址是否可用 | 结论 |
|---|---|---|
| Subs-Check PRO | ✅ 可用 | **主检测层首选**，功能几乎覆盖 P4–P9 全部需求 |
| Sub-Store | ✅ 可用 | 解析/转换能力参考对象；AGPL 限制下不引入运行时依赖 |
| Mihomo | ❌ **地址已失效** | 该 URL 现在是无关项目；组织内已无 Go 内核仓库。**必须替换** |
| Sing-box | ✅ 可用 | **替代 mihomo 做配置校验与协议覆盖**，GPL-3.0 需遵守 |

**最重要的发现**：`https://github.com/MetaCubeX/mihomo` 已经**不是代理内核**了。

## 1. Mihomo：地址失效的证据链

任务书把它列为「代理内核、配置校验、真实连接测试」。实际核验结果：

| 证据 | 内容 |
|---|---|
| REST API `/repos/MetaCubeX/mihomo` | `description` = "A simple Python Pydantic model for Honkai: Star Rail parsed data from the Mihomo API."；`language` = `Python`；`topics` = `["honkai-star-rail", "mihomo", "python", "star-rail-api"]` |
| 页面正文（独立通道抓取） | 标题即 "GitHub - MetaCubeX/mihomo: A simple Python Pydantic model for Honkai: Star Rail..."，README 在讲 `api.mihomo.me` 游戏角色数据查询、`pip install mihomo` |
| 仓库内文件结构 | `.github` / `examples` / `mihomo`（Python 包）——没有任何 Go 源码、Makefile、Dockerfile |
| 组织仓库全量（93 个） | 按语言筛选 **Go 的高星仓库为零**：最高是 `Clash.Mini`（⭐618）；`mipstack`⭐43、`sing-tun`⭐11 都是库，不是内核 |
| 第三方 fork 的许可证 | `cyenxchen/mihomo`、`vernesong/mihomo-oix`、`lux5am/mihomo-smart` 均为 **GPL-3.0** |
| 搜索结果 | 按 `mihomo in:name language:go` 搜索，没有原内核仓库，只有小 fork 与 `clash-next/clash-meta`（"Clash.Meta backup"，⭐79） |

**同时纠正上一阶段的错误**：P0 报告里写的「mihomo ⭐36,045、MIT、最近 push 11-24、最宽松」**是错的**。star 数确实是 36,045，但那是这个仓库 ID 的历史计数，内容已经不是内核；许可证从 fork 反推应为 **GPL-3.0**，不是 MIT。

**这意味着**：任何按任务书地址去 `git clone` 或 `docker pull` mihomo 内核的做法都会失败或拿到无关代码。

### 替代方案

| 方案 | 可行性 | 说明 |
|---|---|---|
| **sing-box 承担内核职责** | ✅ 推荐 | 同为 Go 单二进制，覆盖 VMess/VLESS/SS/Trojan/Hysteria2/TUIC 等，自带 `check` 校验与 Clash 兼容 API |
| subs-check-pro 内置检测 | ✅ 推荐 | 它本身就做「真实可用性检测」，可直接复用，不必自己驱动内核 |
| 用第三方备份仓库（`clash-next/clash-meta`） | ⚠️ 不推荐 | 来源非官方、无签名、star 79，供应链风险高于收益 |
| 自建 mihomo 分支 | ❌ 不可行 | 无上游源码即无法维护 |

## 2. Subs-Check PRO

| 项 | 实测值 |
|---|---|
| 仓库 | `sinspired/subs-check-pro`，未归档 |
| 最新 release | **v3.6.1**（2026-11-15） |
| 许可证 | GPL-3.0 |
| star | 160（小众，需读代码） |
| 安全公告 | 0 条 |
| 资产 | `Linux_x86_64` / `Linux_aarch64` / `Linux_armv7` / `Darwin_*` / `Windows_*`（tar.gz / zip + checksums.txt） |
| Docker | `ghcr.io/sinspired/subs-check-pro` —— **实测 manifest 含 `amd64` / `arm` / `arm64`**；README 给出的启动方式：`-p 8199:8199`（WebUI `/admin`）+ `-p 8299:8299` |
| 接口 | WebUI + 内置文件服务 `/files` + 分析报告 `/analysis`；CLI 可独立运行 |
| 能力（README 原文） | 自适应流水线高并发、增强位置标签、智能乱序、保存历次可用节点、统计成功率、生成 sing-box 配置、检测 ISP/原生-广播 IP/住宅-机房、支持千万级节点库 |

**结论：采用。** 它是四个候选里唯一「拿来就能覆盖 P4–P9」的项目：解析、去重、真实检测、地区识别、生成订阅、历史留存全都有。

**风险**：star 160、维护者单一，且 GPL-3.0。集成方式选择「独立进程 + 文件交换」（不改它的代码、不链接），避免许可证传染，也便于随时替换。

## 3. Sub-Store

| 项 | 实测值 |
|---|---|
| 仓库 | `sub-store-org/Sub-Store`，未归档 |
| 最新 release | **2.44.29**（2026-11-24，当天） |
| 许可证 | **AGPL-3.0** |
| star | 10,981 |
| 安全公告 | 0 条 |
| Docker | `ghcr.io/sub-store-org/sub-store` 返回 **403**（无公开镜像）；官方部署是 Node 后端 `SUB_STORE_BACKEND_API_PORT=3000` + 前端资源 |
| 能力 | 多格式订阅转换、regex / region / type / script 过滤器、合并重命名；后端 CORS 白名单 |

**结论：不引入运行时依赖，只借鉴其解析规则。**

理由：
1. AGPL-3.0 的网络条款意味着「作为服务提供给用户」需开放全部源码。本项目是公开静态站点，判定边界模糊，避免争议最省事。
2. 它解决的核心问题（YAML/JSON → 统一节点）**本次已自行实现**（`nodes_parse.py`，实测把两个 YAML 源从 40 条提升到 3516 条），没有再引入一个 Node 服务的必要。
3. 若将来需要复杂转换（脚本过滤、多格式输出），再以独立进程方式接入，并单独评估 AGPL。

## 4. Sing-box

| 项 | 实测值 |
|---|---|
| 仓库 | `SagerNet/sing-box`，未归档，default branch = `testing` |
| 最新 release | **v1.14.4**（2026-10-24） |
| 许可证 | GPL-3.0（LICENSE 为正版 GPLv3 全文，GitHub 标 NOASSERTION 是因附加说明） |
| star | 40,258 |
| 安全公告 | 1 条：**CVE-2023-43644**（SOCKS inbound 认证绕过，CVSS 9.1，影响 `< 1.5.0-rc.5`，1.4.5 已修复）→ **当前版本不受影响** |
| 资产 | `linux-arm64` / `linux-arm64-glibc` / `linux-arm64-musl` 等 7 个 arm64 相关包 |
| Docker | `ghcr.io/sagernet/sing-box` —— **实测 manifest 含 386/amd64/arm/arm64/loong64/ppc64le/riscv64/s390x** |
| 接口 | CLI `sing-box check` / `format` / `run`；`experimental.clash_api` 提供 Clash 兼容的 `/proxies`、`/proxies/{name}/delay` |

**结论：采用，承担 mihomo 原定的两项职责**——配置合法性校验、按协议做真实连接测试。

**注意**：GPL-3.0。以独立二进制 + 子进程方式调用（`sing-box check -c x.json`），不修改不链接，属于聚合而非衍生，风险可控；但**分发**修改版必须开源。

## 5. 集成架构决定

```
采集层（本项目 Python）
  nodes_parse.py  多格式解析 → 统一模型 + 全参数指纹
        ↓
检测层（独立进程，可替换）
  subs-check-pro  批量真实可用性 / 地区 / 延迟
  sing-box check  配置合法性兜底
        ↓
产出层
  index.html（静态，零请求）
  data/*.json（脱敏）
  export/（含凭据，不入库，走 artifact）
```

不引入 mihomo，理由是它已不可获取；不引入 Sub-Store 服务，理由是重复实现且 AGPL 成本高。两项职责由 sing-box 与自研解析器承接。

## 6. 未验证项（如实记录）

| 项 | 状态 | 原因 |
|---|---|---|
| mihomo 的 Docker Hub 镜像 | **无法验证** | 沙箱内 `auth.docker.io` 返回 HTTP 000（不可达）。但该结论已不影响决策——GitHub 上已无内核源码 |
| subs-check-pro 实际运行、CPU / 内存占用 | **未实测** | P1 阶段只做可行性核验；实机压测放在 P11（三台云服务器评估），届时给出真实数值 |
| sing-box 真实连通性检测效果 | **未实测** | 同上；另需先有稳定的检测目标与超时策略 |
| Sub-Store 无镜像的结论 | 已核验 | ghcr 三个候选名均 403 |

## 7. 下一步（P2）

1. 扩充来源：任务书列出的 12 个候选仓库逐个核验「真实存在 / 活跃 / 有订阅文件 / 格式」，只启用通过小批量采集 + 解析 + 去重 + 真实检测的。
2. 把 `SOURCES` 与页面 `GROUPS` 两张清单合并成单一 `sources.yaml`，消除「7 张卡片不参与节点库、2 个源采集却不展示」的错配。
3. 给检测层加「真实代理可用性」判定（当前只有 TCP 握手），先做小样本对照实验证明 TCP 通 ≠ 代理可用。
