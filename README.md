# 节点订阅 · Nodes

> 免费节点订阅**索引站**。React + TypeScript + Vite + Tailwind CSS v4，Swiss Minimalism 设计。

免费节点订阅索引站：GitHub Actions 每 3 小时采集公开订阅源，统计真实条数、实测可连率与源新鲜度，按质量排序。打开网页 → 复制任意一条链接 → 粘进客户端 → 更新即可。

**只做索引，不做聚合**：所有链接原样指向各仓库的发布地址，不合并、不去重、不转格式、不托管节点。

## 特性

- **每 3 小时自动采集** — GitHub Actions 定时拉取全部源，自动重建并部署
- **实测数据** — 每条源抽节点测 TCP 连通率，同时探测源站 HTTP 状态
- **新鲜度透明** — 源仓库最后提交时间单独显示，超 30 小时标「可能已停更」
- **按质量排序** — 卡片按「可连率 × 节点数」排
- **一键复制 / 扫码导入** — 复制进剪贴板，二维码弹窗手机相机扫码
- **搜索 + 筛选** — 关键字搜索；可连 ≥80% / 国内可直连 一键过滤
- **响应式** — 桌面三栏（图标侧栏 + 内容区），移动端单列，无横向溢出

## 技术栈

| 层 | 选型 |
|----|------|
| 框架 | React 19 |
| 语言 | TypeScript 5.9 |
| 构建 | Vite 8 |
| 样式 | Tailwind CSS v4（oklch 设计令牌） |
| 图标 | Lucide React |
| 字体 | Sora / Manrope / JetBrains Mono（@fontsource 自托管） |
| 二维码 | qrcode.react |

设计规范见 [`DESIGN_SYSTEM.md`](./DESIGN_SYSTEM.md)。

## 目录结构

```
├── src/                 # 前端源码
│   ├── components/      # 组件（侧栏 / 顶栏 / 卡片 / 弹窗…）
│   ├── data/display.ts  # 内嵌回退数据（裁剪自 data/nodes.json）
│   ├── lib/utils.ts     # 可连率 / 新鲜度 / 格式化工具
│   ├── types.ts         # 数据模型
│   └── App.tsx          # 页面编排与状态
├── public/data/         # 运行时拉取的展示数据（构建时拷入 dist）
├── scripts/trim_data.py # 采集后裁剪 nodes.json → display.json
├── collector.py         # 数据采集器（保留）
├── data/                # 采集产物（nodes.json 等）
└── .github/workflows/   # 采集 + 构建 + 部署
```

## 数据流

```
collector.py  →  data/nodes.json（全量）
      ↓ scripts/trim_data.py
public/data/display.json（裁剪展示数据）
      ↓ vite build
dist/data/display.json（站点运行时 fetch，失败回退内嵌数据）
```

## 本地开发

```bash
pnpm install          # 安装依赖
pnpm dev              # 开发服务器（http://localhost:5173）
pnpm typecheck        # TypeScript 检查
pnpm build            # 生产构建（产物在 dist/）
pnpm preview          # 预览构建产物
```

## 部署

`main` 分支保存源码与数据管道，`gh-pages` 分支托管构建产物。

1. 仓库 Settings → Pages → Source 选 `gh-pages` 分支
2. Settings → Actions → General → Workflow permissions 选 `Read and write`
3. 推送代码或等待定时任务（每 3 小时）自动采集、构建并部署

## 免责声明

本项目仅整理公开渠道的订阅链接，不生产、不存储、不修改任何节点资源。订阅内容来自第三方仓库，可用性与安全性由源作者负责。请遵守当地法律法规，仅作学习交流用途。
