# P15：客户端真实导入验证

判据是客户端自己的内核接受配置，不是我们「觉得」它接受。

## 各分组

| 分组 | Mihomo | sing-box | 说明 |
|---|---|---|---|
| all | ✔ 通过 | ✔ 通过 |  |
| best | ✔ 通过 | ✔ 通过 |  |
| eu | ✔ 通过 | ✔ 通过 |  |
| fast | ✔ 通过 | ✔ 通过 |  |
| hk | ✔ 通过 | ✔ 通过 |  |
| jp | ✔ 通过 | ✔ 通过 |  |
| kr | ✔ 通过 | ✔ 通过 |  |
| lowlat | ✔ 通过 | ✔ 通过 |  |
| sg | ✔ 通过 | ✔ 通过 |  |
| stable | ✔ 通过 | ✔ 通过 |  |
| tw | ✔ 通过 | ✔ 通过 |  |
| us | ✔ 通过 | ✔ 通过 |  |

## Xray / Hiddify

* **Xray**：✔ 通过，把 9,845 条 URI 逐条转成 outbound 后交给 `xray -test`。结果：Configuration OK.
* 未转换的协议：anytls 30 条、vless（无 TLS，Xray 26 拒绝） 209 条、ss 旧加密（Xray 已移除） 396 条、socks5 46 条、HTTP 传输（Xray 26 已移除） 58 条、hysteria2 208 条、reality+ws（Xray 只支持 raw/xhttp/grpc） 22 条（Xray 不支持这些协议，不算失败）
* **Hiddify**：内核是 sing-box，配置格式完全一致，上面的 sing-box 检查等价于「Hiddify 能不能吃下这份订阅」。但 Hiddify 没有免登录的命令行，**本项目没有做过 Hiddify GUI 的导入测试**——这一点如实写在这里，不冒充。

## 这一步抓到过什么

第一次跑的时候 sing-box 报 `invalid public_key`，之后一路修了六处。全部是「我们自己的校验全绿、客户端内核拒绝」的类型：

| 客户端的报错 | 根因 | 修在哪 | 影响 |
|---|---|---|---|
| `invalid public_key` | `reality-opts` 用 `p.get(..) or {}` 再判 `isinstance(dict)`，空 dict 也是 dict，导致**每个** Clash 节点都被标成 reality、公钥清空 | `nodes_parse._ws_params` | 2,374 条误标，其中 1,987 条公钥为空 |
| `invalid public_key`（第二处） | reality 节点缺 pbk 时照样生成订阅 | `_why_drop` 新增 `no_key` 隔离 | 见隔离区 |
| `TLS required` | hysteria2 / tuic / anytls 的 TLS 是协议内建，源里没写 sni 时 tls 块为空、`if tls` 判假 | `export_subs._sb_out` | 208 条 |
| `unsupported flow: xtls-rprx-vision-udp443` | 上游混用 Xray 的变体写法 | `_sb_out` 归一为 `xtls-rprx-vision` | 4 条 |
| `trojan without TLS is prohibited` | trojan 内建 TLS，但内部模型里 tls 为空 | 三处生成器补 TLS | 数百条 |
| `unknown transport protocol: ws#🍪...` | URI 把 fragment 编码进 `type` 参数 | `_finalize` 新增 `_norm_net` 白名单归一 | 数十条 |
| `invalid byte: U+0078 'x'` | vmess 的 id 是 `...2cdx`，末位不是 hex（上游打错） | `_why_drop` 新增 `bad_uuid` 隔离 | 359 条 |

这件事说明 P8 的「多格式反解校验」这一层**不够**：反解用的是同一个解析器，解析器错了它跟着错，`verify()` 全绿也说明不了任何问题。客户端内核是独立实现，才拦得住这一类错误。所以这一项不是走过场，它抓出来的问题比压测更多。
