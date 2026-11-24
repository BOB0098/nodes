# P13：安全边界

这个项目的威胁模型有点反直觉：**我们不是在保护一个服务，而是在决定「愿意执行什么别人的数据」**。

每轮 CI 会从 15 个第三方仓库拉取约 15,000 行文本，把它们解析成 10,000 多个节点，然后拿这些节点去真的发握手包。这中间有三个地方会出事：

1. **拉取**：地址由上游内容间接决定（GitHub 返回的链接、仓库里的文件可以引用任意 URL），而解析出来的东西会流进 `curl`、`mihomo` 和页面。
2. **日志**：订阅地址的 query 里常放 token，节点 URI 里全是 uuid 和密码。一次 `print(e)` 就可能把凭据写进 Actions 日志——那是公开可读的。
3. **写盘**：`data/*.json` 同时是页面和订阅的唯一数据源，写坏一次就是全站空白。

下面逐条对照任务书 §13 的要求，写清楚做了什么、没做什么、为什么。

---

## 1. 凭据不进公开仓库

| 对象 | 存放位置 | 是否入库 |
|---|---|---|
| `GITHUB_TOKEN` | Actions 自动注入，运行期环境变量 | 否 |
| 节点凭据（uuid / password / 订阅 token） | `export/`（运行时生成） | 否，`.gitignore` 整目录 |
| 源地址 | `sources.yaml` | 是（全是公开仓库地址，无凭据） |

这一条有过一次真实事故：2024-11 之前 `export/` 被提交过 **78 次、3544 条凭据**，P0 用 `git filter-repo` 重写历史清掉了。所以现在的做法不是「记得别提交」，而是**结构性保证**：

* `export/` 在 `.gitignore` 里，且 `nodes_full.json` 写完后立刻 `chmod 600`；
* 容器部署时 `export/` 不挂到宿主机（`docker-compose.yml` 只挂 `data/` 与 `index.html`），要取订阅得显式挂载，取舍写在文件注释里；
* `restore.py` 用**白名单**而不是黑名单——以后加了带凭据的新文件，不会因为「忘了加黑名单」被顺手复制出去。

## 2. 日志脱敏

`netsafe.mask_url()` 与 `netsafe.mask_text()` 是所有日志的唯一出口。覆盖范围：

| 形态 | 例子 | 处理后 |
|---|---|---|
| query 里的敏感参数 | `?token=abc123` | `?token=***` |
| URL 内嵌凭据 | `https://u:p@host/` | `https://***@host/` |
| 路径里的长随机段 | `/sub/a1b2c3d4e5f6g7h8i9j0` | `/sub/a1b***j0` |
| vmess / ss 的 base64 段 | `vmess://eyJhZGQi...` | `vmess://***` |
| vless / trojan / tuic 的 userinfo | `vless://uuid@host` | `vless://***@host` |
| 裸 token | `ghp_...` / `sk-...` / `AKIA...` | `***token***` |
| 裸 UUID | `11111111-2222-...` | `***uuid***` |

指标是「看日志能认出是哪个源，但认不出 token」，所以域名和路径形状一律保留。

改之前有 7 处 `print` 直接打 URL、2 处把 `str(e)` 原样写进 `errors`——而 `HTTPError` 的 `str()` 就是完整 URL。现在这些位置全部走脱敏函数，`grep -n "print" *.py` 里已经没有裸 URL 了。

**没做的**：节点名（remarks）不过滤。它是上游作者写的展示文本，不是凭据，且页面上本来就显示（用户需要靠它认节点）。如果哪天上游把 token 写进节点名，那是上游的问题，这里不做内容审查。

## 3. 出网四道闸

`netsafe.open_url()` 是唯一允许的拉取方式。旧实现 `urllib.request.urlopen(...).read()` 同时踩了四个坑：

| 风险 | 旧行为 | 现在 |
|---|---|---|
| SSRF | 任意 URL 都发 | 协议白名单 + 逐跳校验 + 解析结果全部必须公网 |
| 任意文件读取 | `urlopen("file:///etc/passwd")` 能读 | opener 只组装 HTTP/HTTPS 两个 handler |
| 无限重定向 | urllib 默认 10 跳，不检查目标 | 3 跳，每跳重跑一遍地址检查 |
| 超大下载 | `.read()` 读到 EOF | 12 MB 上限（Content-Length 预检 + 读循环累计） |
| 慢速滴水 | `timeout` 只管单次 socket 操作 | 90 秒墙钟预算，读循环里持续检查 |
| 并发 | 6 线程（已有） | 不变，6 线程 + 每源 2 次重试 |

具体拦截规则：

```
http://127.0.0.1/             → 目标是非公网地址
http://localhost/             → localhost 解析到非公网地址 ::1
http://169.254.169.254/       → 目标是非公网地址（云元数据，能拿实例凭据）
http://[::ffff:127.0.0.1]/    → 目标是非公网地址（IPv4-mapped 也要拆开判）
file:///etc/passwd            → 不允许的协议
http://user:pw@example.com/   → URL 内嵌用户名/密码
http://example.com:6379/      → 端口不在允许范围 [80, 443]
```

域名会先 `getaddrinfo` 再**逐个**比对解析结果，所以 `*.internal`、被 hosts 文件劫持的域名、解析到内网的域名都挡得住。

### 残余风险：DNS rebinding

校验时解析出公网 IP，连接时再解析一次，两次之间攻击者可以改 DNS 记录（TOCTOU）。彻底解决要把解析出的 IP 钉死再连，那需要自写 HTTP 客户端、手动处理 TLS SNI 与证书校验——为了一个当前不存在的攻击面，引入整条采集链路失败的风险，不划算。

**这个取舍成立的前提**：源清单本身可信。`sources.yaml` 在仓库里，改动要走 PR；`discover.py` 发现的候选源只写进 `data/candidates.json`，必须人工 `enabled: true` 才会上线。也就是说，能让 DNS 指向内网的人，已经有仓库写权限了——那时候他能做的事比 SSRF 严重得多。

### 有一处「故意不拦」

`nodecheck.py` 里 `curl -x socks5h://127.0.0.1:PORT` 打的是本地回环地址，没有走 netsafe。这不是漏了：那次请求是**连我们自己的代理**，目标是脚本自己分配的端口，方向与 SSRF 相反。如果套上 netsafe 的「禁止内网」，整条真实检测链路直接停摆。

## 4. 不执行不可信上游脚本

全链路没有 `eval` / `exec`，也没有 `shell=True`。逐项说明：

* **YAML**：`nodes_parse` 用 `yaml.safe_load()`；解析 Clash 配置里可能有重复键的那条路径用了 `Loader=yaml.BaseLoader`——BaseLoader 把所有标量当字符串返回，**不构造任何 Python 对象**，`!!python/object/apply` 这类标签对它无效。
* **JSON**：`json.loads`，标准库，不执行代码。
* **subprocess**：全部是 list 形式（`["curl", "-s", ...]`），参数是端口号和固定 URL，没有拼字符串。
* **mihomo**：固定版本 `v1.19.34` 的官方 release，经镜像下载后校验体积在 4–64 MB 之间（挡住「返回一个 HTML 错误页被当成 gz 解压出坏二进制」），然后才 `chmod +x`。配置文件由我们的代码生成，节点参数只作为**数据**写进 YAML，不参与任何命令行拼接。
* **页面**：`template.html` 里节点数据经 `json.dumps` 后替换 `</` 为 `<\/`，不会因为节点名里带 `</script>` 就截断脚本块。

## 5. 不把订阅转发给第三方

链路里唯一的外部 API 是 `ip-api.com`，每次只发一个**已经通过代理的出口 IP 查询**：查询串里没有节点凭据、没有订阅 URL，返回的是公开的 IP 归属数据。除此之外没有任何遥测、上报、统计。

「节点流量经过节点本身」不算转发——那是检测的定义。检测时访问的是 `gstatic.com/generate_204` 和 `speed.cloudflare.com` 的固定 512KB 文件，不携带任何本项目的数据。

## 6. 检测失败不覆盖有效结果

这是最容易写错的一条。原来的 `nodecheck.save()` 无条件覆盖 `nodecheck.json`，意味着一次环境抖动（mihomo 下载到坏包、机房出口被限速、某个协议实现整批崩）就会把上一轮 340 条有效结论**永久抹掉**——第二天环境恢复了，那批数据也读不回来了。

现在写盘前过一道**回归闸门**：

```
上一轮 tested ≥ 200  且  上一轮 ok > 0
        且  本轮 ok < 上一轮 ok × 40%
        → 不写，退出码 3，页面继续用上一轮数据
```

40% 这个阈值是「明显不对」而不是「有波动」：正常轮次之间可用数在 ±15% 内浮动，掉到四成以下只有可能是环境或代码出了问题。上一轮只测了几十条时不做比较——样本太小时比例没有统计意义。

`NODES_SKIP_GATE=1` 可强制写入（调试用），日志里会写明「判定为环境异常，未覆盖」。

配套的三层保护：

1. **CI**：检测步骤 `continue-on-error: true`，失败时后面用旧数据继续；
2. **原子写**：所有 JSON 先写 `.tmp-xxx.part` 再 `os.replace`，中断时留下的是旧文件而不是半个 JSON（下一轮 `json.load` 直接 `JSONDecodeError` 才是真的灾难）；
3. **恢复工具**：`restore.py`。

## 7. 备份、恢复与回滚

git 本身就是每轮一份快照，不做额外备份（每天一个 1.6 MB 的 JSON 副本，一年 600 MB，还全是重复内容）。`restore.py` 把「翻历史」变成两条命令：

```bash
python3 restore.py list                 # 最近 20 次数据改动
python3 restore.py diff <sha>           # 该提交改了哪些数据文件
python3 restore.py to <sha> --dry-run   # 只看会改什么
python3 restore.py to <sha>             # 恢复，当前内容先备份
python3 restore.py undo                 # 撤销上一次恢复
```

三个约束：只允许恢复白名单里的文件（`index.html`、`data/*.json`、评分报告）；rebase/merge 进行中直接拒绝（这时候 HEAD 不是你以为的那个提交）；恢复前把当前文件复制到 `.restore-backup/<时间戳>/`，`undo` 从那里拿回来。

实测往返：`to HEAD` → `md5sum -c` 全部一致 → `undo` → `md5sum -c` 再次一致。

---

## 验证记录

| 项目 | 结果 |
|---|---|
| `python3 netsafe.py` | 30 项全过（11 条必须拦、3 条必须放行、重定向、三种上限、7 项脱敏、原子写、Unresolvable 分类） |
| 真实链路 | 4 个源实拉 124 KB–489 KB，1.5–9.2s，无一条被误拦 |
| 源站探活 | `http_probe` 返回 200 / 1231ms |
| 新鲜度探测 | 15 个源里 11 个拿到 `Last-Modified`，4 个返回 None（源站本来就不给这个头，不是被拦） |
| 离线渲染 | `collector.py --render` 后 `stats.dedup` 与渲染前一致（10,352） |
| 恢复往返 | `to` → md5 一致 → `undo` → md5 一致 |
| CI | 新增「安全边界自测」步骤，跑在采集之前 |

## 已知未做

* **DNS rebinding 的 TOCTOU 窗口**（上面已说明取舍）。
* **出口 IP 归属查询依赖 `ip-api.com` 的免费额度**（45 次/分钟）。不做重试、不做缓存——失败就是这一条没有地区信息，按「未知」统计，不猜。
* **节点名不做内容审查**（上游作者写的展示文本）。
* **`screen.py` 的对候选源是截断式读取**（`read(limit+1)` 就停），比 `netsafe.open_url` 的全量读更省内存，但只适用于「判断有没有超限」的探测场景，不适用于要完整解析的正式采集。
