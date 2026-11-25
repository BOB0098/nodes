# UI 全面重设计 · P1 现状审计报告

> 分支 `ui-redesign` · 基线 commit `e3b1bb3` · 审计时间 2026-11-25

## 1. 审计范围与结论

| 项目 | 现状 | 处置 |
|---|---|---|
| 线上 main | `e3b1bb3`（P23–P28 独立审计） | 作为改造基线 |
| 页面生成链路 | `sources.yaml` → `collector.assemble()` → `__DATA__` 注入 `template.html` → `index.html` | **改 UI 必须改 `template.html`**，否则下次采集覆盖 |
| 模板结构 | 1335 行；`<style>` 30–458、`<body>` 460–549、卡片模板 551–566、二维码层 568–575、脚本 579–1333 | 全量重建模板，保留数据契约 |
| 来源清单 | `sources.yaml` 15 条 enabled：Clash 5 / V2Ray 6 / sing-box 4 | 与 README 第 20 行「三类 15 条源」**一致，无需改文案** |
| 数据契约 | `groups[].items[]` 23 字段；`stats` 含 real_tested/real_ok/sources/subs/checked_at 等 | 全部保留，UI 只读不改 |
| 二维码 | 内联 `QR` 模块（685–866 行），`QR.draw(canvas,text)` 真编码 | **复用原算法**，不引入新库 |
| 复制 | `navigator.clipboard` + Range 选中降级 | 复用并接入 Toast |
| 明暗 | `light-dark()` 随系统 + `theme-color` 同步 | 新设计保持兼容 |

## 2. 现有功能清单（改版必须保留）

1. 15 条来源按三协议分组展示，条数来自 `count_of()` 同口径解析。
2. 真实可用率（`rok/rtest`，mihomo 真握手口径）与端口可达率（`probe`）分开展示。
3. 来源质量等级（grade）、状态标签、`src_http`、生命周期 `age_h/streak`。
4. 复制链接（含降级选中）+ 二维码弹层（真编码、可关闭、焦点回退）。
5. 折叠区：怎么订阅、数据总览、客户端下载。
6. 搜索 / 客户端筛选 / 排序 / 真实可用率筛选 / 国内直连筛选（P21，本次按要求删除）。
7. 系统深浅色跟随、reduced-motion、`aria-live` 播报、44px 触控区（P27 修复）。

## 3. 关键约束（业务保护）

- **禁止改** collector / sources.yaml / nodes_parse / nodecheck / score / history / lifecycle / governance / picks / export_subs / build_manifest / daily.yml（采集、检测、评分、订阅、清单、部署全部只读）。
- 原始订阅 URL 一字不改；来源 ID 不变；不合并重复来源；不上传节点 URI。
- 展示名走**前端独立映射表**（URL → 英文简称），不覆盖 `sources.yaml` 的 `name` 字段，不新增采集字段。两个 ermaozi 来源用 `Reachable / Full` 副标签区分。
- 数据中心需要 `picks/governance/trend/threshold_watch`，这些**不在 `__DATA__` 内**，须异步 `fetch('data/*.json')`；本地 `file://` 测试需起 HTTP server，线上 Pages 同源可用。缺数据时明确显示「暂不可用 / 跟踪 N 天」，不伪造曲线。

## 4. 设计 Skill 兼容性结论

| Skill | 结论 | 借鉴点 |
|---|---|---|
| RCS Frontend Skill | 非 WorkBuddy 技能格式，不安装 | 渲染后审查、reduced-motion、证据化交付、反 AI 模板化 |
| Impeccable | 非 WorkBuddy 技能格式，不安装 | 语义层级 1.25x、反模式清单、WCAG 2.2 AA、价值级豁免 |
| Skill Kit | 非 WorkBuddy 技能格式，不安装 | glassmorphism/neumorphism token、palette 阶梯 |
| 本地 `dev-expert/frontend-design` | 可用，已读 | 双钻流程、原型先行、无障碍优先 |

## 5. 改版前基线截图

- `/workspace/ui-redesign/baseline/before-mobile-390.png`
- `/workspace/ui-redesign/baseline/before-desktop-1440.png`
- 现状观测：浅灰蓝底、大白色圆角卡、黑色实心按钮、筛选控件占位偏高，首屏仅露出 1 张完整卡片。

## 6. P1 判定

**通过**。业务接口与数据字段已确认，生成链路已锁定，基线截图已建立，README 与 sources.yaml 来源数一致（15 条）。可进入 P2 视觉设计系统。
