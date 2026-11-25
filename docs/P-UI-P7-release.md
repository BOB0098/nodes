# UI 重设计 · P7 最终验收发布报告

> 分支 `ui-redesign` → `main`，基线 `e3b1bb3` → 发布 `7e87e1b`
> 发布时间：2026-11-25

## 0. 发布与线上验证结论（已实测）

- 快进合并 `ui-redesign` → `main`，线上 main = `7e87e1b`。
- Actions `pages build and deployment`：**success**。
- 线上 <https://bob0098.github.io/nodes/> 实测：15 卡 / 3 组、英文简称、暖米白详情变量、底部导航、旧搜索控件已移除；数据中心线上 fetch 显示 20 行榜单；0 页面错误。
- 线上截图：`docs/ui-screenshots/live-home-390.png`、`live-data-390.png`。

## 1. Git 提交记录（每阶段独立提交）

| commit | 阶段 | 说明 |
|---|---|---|
| `0a3f3f5` | P1 | 现状审计——锁定生成链路、15 条来源与业务保护边界 |
| `38b33e5` | P2 | 视觉设计系统——浅灰紫白 + 暖米白 + 三页线框（旧规范归档） |
| `35d7a42` | P3–P5 | 首页重建 + 详情交互 + 其他页面 |
| `90dc45f` | P6 | 适配与测试——16 项全通过、二维码解码验证、对比度修复 |

## 2. 验收标准逐项核对

| 验收项 | 结果 | 证据 |
|---|---|---|
| 视觉符合第三版效果图方向 | 通过 | 浅灰紫白纯色背景、紧凑卡片、暖米白详情 |
| 页面背景纯色无装饰 | 通过 | 无山脉/气泡/插画/渐变光效 |
| 15 条启用来源全展示 | 通过 | 三分组 5/6/4，Playwright 实测 15 卡 |
| 英文简称无中文「尔毛子」 | 通过 | ermaozi/Radarhub/anaer…；副标签 Reachable/Full |
| 信息密度简洁 | 通过 | 卡片仅两行：名称+状态 / 节点+可用率+时间 |
| 搜索筛选完全移除 | 通过 | 模板中无 fSearch/fProto/fSort/fAlive/fCn 等控件 |
| 详情弹层独立暖米白 | 通过 | 独立 CSS 变量，与首页明显区分 |
| 复制功能真实 | 通过 | 剪贴板读回 = 真实 URL |
| 二维码正确 | 通过 | cv2 解码 = 真实订阅 URL |
| 数据全部真实 | 通过 | 来自 `__DATA__` 与 data/*.json，无伪造 |
| 数据中心统计保留 | 通过 | 检测/评分/节点/质量/治理/四类榜单齐全 |
| 手机适配 | 通过 | 320–430 四尺寸零横向溢出 |
| 电脑适配 | 通过 | 1024/1440 居中布局合理 |
| 加载性能 | 通过 | 模板减小 25%，首卡 82ms，无外部依赖 |
| 原有采集/检测/自动更新 | 通过 | 业务文件零改动，Actions 工作流未改 |
| GitHub Pages 部署 | 通过 | Actions success + 线上实测 15 卡/20 榜行/0 错误 |

## 3. 业务保护核验（硬证据）

- `git diff e3b1bb3 HEAD` 文件清单仅含：`docs/*`、`template.html`、`index.html`。
- 以下文件**零改动**：collector.py、sources.yaml、nodes_parse.py、nodecheck.py、score.py、history.py、lifecycle.py、governance.py、picks.py、export_subs.py、build_manifest.py、verify.sh、README.md、`.github/workflows/daily.yml`。
- 15 条订阅 URL 与来源 ID 未变；DISPLAY 展示名映射 15 个 key 全部命中真实 URL，未覆盖采集 name 字段。
- 未上传任何节点 URI / 凭据；无新增后端；无前端框架依赖。

## 4. 已知限制与后续建议

1. **manifest 哈希**：UI 改造改了 `index.html`，本地台账/manifest 仍记录旧哈希（P23 门禁正确拦截手写）。**下一轮 CI 会用新 template 重新 render → mark → build_manifest → consistency，自动恢复一致**，无需人工改 manifest。本轮沙箱无法触发真实 Actions，故未实测该轮日志。
2. **真机未测**：Android/iPhone 真机、微信 X5、Safari 在沙箱无法实测；已用视口模拟 + 特性回退（color-mix 纯色回退、`-webkit-` 前缀、@media 深浅色）规避，建议发布后用真机复测。
3. **历史趋势**：数据中心不显示趋势曲线，因实际跟踪数据不足（见 P28 观测）；待积累满 7 天再启用，当前不伪造。
4. **榜单名称**：四类榜单条目名来自真实检测数据（含位置/ISP 描述），未做额外美化改写，保证真实性。

## 5. 部署地址

- 线上：<https://bob0098.github.io/nodes/>
- 仓库：<https://github.com/BOB0098/nodes>
- 合并后需访问线上确认新页面生效；Pages 通常由 Actions 推送 main 后自动构建，约 1–2 分钟。
