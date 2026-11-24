# P11：三个免费云服务器的 Docker 能力评估

> 任务书原文：**「在实际验证前，不得认定任何平台支持完整 Docker 或适合长期运行。」**
> 所以这份文档分成两半：一半是**在各平台官网上确凿查到的**东西（带链接和抓取日期），
> 一半是**必须在那台机器上跑一遍才能知道**的东西。后者我没有替你猜。

## 一、结论

| 平台 | Docker 能力（公开文档） | 免费档能不能常驻 | 我们该怎么用 |
|---|---|---|---|
| **InstaBox** | 完整。Firecracker 微虚机，自带内核、root、systemd、原生 Docker | **不能**。空闲 30 分钟自动暂停，单次运行上限 120 分钟（用得多才涨到 480） | 适合「唤醒 → 跑一轮 → 睡觉」的批处理，需要外部触发器 |
| **Altare** | 完整（Docker Apps），支持 Docker Hub / GHCR / GitLab / Quay / Harbor / 私有 registry | **未知**。页面只写资源配额，没写是否休眠 | 镜像来源最灵活；但只有 TCP 入站，且免费额度是 4 台共享 6 GB 内存 |
| **Nodaroo** | **无法核验**。`free.nodaroo.com` 强制 Discord 登录，无公开文档；主站是付费游戏主机（$1 起） | 未知 | 暂不采用。没有证据就不写「支持」 |

**没有一个平台被认定为「可以长期跑」。** 三家里只有 InstaBox 的 Docker 能力有明确文档支撑，
而它恰好是唯一一家把「不能常驻」写在明面上的。

## 二、查到的事实（2026-11-25 抓取）

### InstaBox — https://instabox.sh/

来源：[定价页](https://instabox.sh/pricing)、[文档索引 llms.txt](https://instabox.sh/llms.txt)。

* 免费档 `$0`：**1 台机器，4 vCPU / 8 GiB，20 GiB 持久盘（挂在 `/data`），100 GiB 出站/月**
* 机器是 Firecracker microVM，Debian 13，自带内核 → `sudo`、`systemd`、原生 Docker 都在
* **空闲自动暂停固定 30 分钟，单次运行上限 120 分钟**（用得多了才逐步放宽到 480 分钟）
* 文件在会话之间保留（`/data` 持久，暂停不丢）
* 出站带宽：突发后限到 ~50 Mbit/s（约 6 MB/s）
* 有公网端口能力（给机器里的 web 服务一个 HTTPS 地址）

对我们意味着什么：4 vCPU / 8 GiB 跑一轮 10,000 节点的采集+检测绰绰有余（CI 上 2 核 7 GB ，
13 分钟跑完）。问题是**跑到第 120 分钟会被强制停**，而且停了不会自己起来。
这恰好不是缺陷——一轮采集本来 15–25 分钟就结束了。真正麻烦的是**谁来触发**：
一台会自己暂停的机器没有可靠的 cron。要么从 GitHub Actions 调它的 API 唤醒并执行，
要么放弃「全自动」，接受「想更新时手动跑一轮」。

### Altare — https://console.altare.gg/apps

来源：[Docker Apps](https://altare.gg/en/products/docker-apps)、[首页定价块](https://altare.gg/)。

* Always Free：**6 GB 内存 / 150% CPU / 10 GB 磁盘 / 最多 4 台 server**（团队共享一个资源池）
* Docker Apps 支持从 **Docker Hub、GitHub(GHCR)、GitLab、Quay、Harbor 和任意 HTTPS registry** 拉镜像
* 私有镜像可用「用户名 + token」，token 加密存储且不再显示
* **只有 TCP 能到应用**（页面原话：*Only TCP traffic reaches the app*）
* 镜像必须是 `linux/amd64`；容器以镜像自己的用户运行，**没有 root**
* `--memory` / `--cpu` 可调；`/data` 目录在重新部署时保留
* 另一条产品线 Altare Go（沙箱机器）**最长 12 小时**，用完即删，不适合承载定时任务

对我们意味着什么：镜像分发这块是三家里最顺的（GHCR 直连，正好对应我们的仓库），
`/data` 持久也够用。但有两条硬限制：**没有 root**（我们的镜像不需要，但 `probe.py` 里
绑定 80/443 那项会失败——那本来就是「不需要通过」的检查项），以及**入站只有 TCP**。
入站 TCP 对我们**不影响**：我们只出网，不开监听。

### Nodaroo — https://free.nodaroo.com/

* `free.nodaroo.com` 打开就是 Discord 登录页，除此之外没有任何信息
* `nodaroo.com` 是付费游戏主机销售页（Minecraft/Rust/ARK，**$1.00 起**），机房在法兰克福和孟买
* 没有公开文档提到 Docker、Compose 或容器

结论：**证据不足**。按任务书的规则「若候选项目不可用，先说明证据并寻找成熟替代方案」，
这里的证据就是「它没有公开任何可核验的信息，且免费层需要第三方账号绑定」。
不去猜它支持什么。如果要试，用下面的自检脚本测完再谈。

## 三、自检脚本：`dockerprobe/probe.py`

评估一个平台**唯一可信的方式是在那台机器上跑一遍**。脚本只用标准库，
目标机上只要有 Python 3.8+ 就能跑（不需要先装依赖——这是故意的，
装依赖本身就是在测「这平台能不能装东西」）。

```bash
# 在目标机器上（或 docker run 进去）
python3 dockerprobe/probe.py run --platform instabox --json probe/instabox.json

# 隔一段时间（跨过一次自动暂停/重启）再跑一次，持久化和连续运行才有结论
python3 dockerprobe/probe.py run --platform instabox --note "第二次" --json probe/instabox-2.json

# 多平台汇总成一张表
python3 dockerprobe/probe.py report probe/*.json
```

检查项与**为什么要有这一项**：

| 检查 | 不通会怎样 |
|---|---|
| `docker_daemon` | 只有 client 没有 daemon 的平台（很多「支持 Docker」的免费面板）在这里露馅 |
| `docker_run` | 能 `pull` 不能 `run` 的平台不少：seccomp / 无特权容器会拦在 run 这一步 |
| `pull_dockerhub` / `registry_ghcr` | 拉不到镜像，后面全是空谈 |
| `egress_tcp` | **命门**。连不出去，节点检测结果会全是 0%，而页面看起来只是「今天节点都不行」 |
| `egress_udp` | hysteria2 / tuic 是 QUIC，**走 UDP**。UDP 被封的环境测出来的可用率会系统性低估这两类协议 |
| `persist` | 第一次必然返回 `未测`：写进去要等下次才看得见。这是设计 |
| `longrun` | 用「墙钟间隔 vs 开机时长」反推中途有没有被停。机器不会回答，但它的开机计时器会说 |
| `bind_ports` | 只影响「要不要对外提供订阅」，不影响采集。免费平台不给 80/443 是正常的 |

三项判定，缺一不认：

```
docker_ok    = docker_daemon ∧ docker_run ∧ pull_dockerhub
egress_ok    = egress_tcp
longrun_ok   = 墙钟与开机时长对得上（上一轮到现在机器没被停过）
适合日常采集 = 以上三项全通过
```

`skip` 和 `fail` 是分开的：`fail` 是「测了，不行」，`skip` 是「没测成」。
一份全是 skip 的报告不能读成一份全是 fail 的报告——这个区分在 `selftest` 里有断言。

### 在本项目的沙箱里跑出来的样子（对照组）

沙箱本身是个容器，正好当一次阴性/阳性对照：

```
workbuddy-sandbox: docker=可用 出网=通 持久化=确认 连续运行=确认 → 日常采集=适合
  [pass] docker_daemon: server 27.5.1 / 存储 overlay2 / cgroup v2
  [pass] docker_run: 容器能起能退
  [pass] registry_ghcr: ghcr.io 返回 401 未认证挑战（可拉公共镜像）
  [pass] egress_tcp: 5/5 可达
  [pass] egress_udp: UDP/53 通，收到 2 条应答（64ms）
  [skip] docker_restart: 当前没有容器可查，重启策略需在部署后复测
  [skip] persist: 已写入标记（/tmp），持久化要下次运行才能确认
```

这份结果只证明脚本本身能跑通，**不代表三个平台的任何结论**。

## 四、还没验证的部分（不猜）

1. 三个平台**实际**的 Docker 行为——需要各自注册一个免费账号并在机器上跑 `probe.py`。
   这一步涉及创建账号、绑定 Discord / 邮箱，属于「需要你本人操作」的范围。
2. InstaBox 的暂停是否**计费**、暂停后 API 唤醒的延迟。
3. Altare 的 app 在免费额度下**是否会被回收**（页面只写资源，没写生命周期）。
4. Nodaroo 的免费层到底给什么（连登录墙都过不去，不做假设）。
5. 三家对**出站 UDP** 的策略。这条最要紧：如果 UDP 被封，hysteria2/tuic 会被判死，
   我们在那个环境里得到的「可用率」会低于真实值。

## 五、所以我们到底用哪个

**主力仍然是 GitHub Actions。** 理由不是「免费」，而是：

* 出口稳定在一个地区（美国），每轮检测的口径一致，数据可比
* 有 `GITHUB_TOKEN`，能自己提交数据，不需要额外凭证
* 有 `concurrency` 锁和 6 小时上限，正好匹配一轮 15–25 分钟的任务

云服务器的价值不在替代它，而在**换一个出口看同一批节点**。
节点是否可用和「从哪儿连」强相关：一个只对欧洲友好的节点，从美国测是死的，
从法兰克福测是活的。等 Actions 稳定跑满几周、地区分布有基线之后，
再考虑用一台欧洲机器做补充抽样——那时才有对比的意义。

在此之前，云服务器只做一件事：**跑 `probe.py`，把结果放进 `probe/` 目录**。
没有 probe 结果的平台，在本文档里一律写「未验证」。
