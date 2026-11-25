# P26：客户端四端本轮真实验证报告

第二轮审计问题 6：上轮（P22）客户端导入沿用 P15 结论，未在本轮真测。本报告用当前仓库的二进制和当前导出代码**本轮真实跑一遍**，不沿用旧结论。

## 判据

用客户端自己的内核判定"接受配置"，而不是我们自己的解析器"觉得能读回"。

| 客户端 | 判定方式 | 本轮结果 |
|---|---|---|
| Mihomo（Clash Meta） | `mihomo -t -f all.yaml` | ✔ pass |
| sing-box | `sing-box check -c all.json` | ✔ pass |
| Xray | 逐条 URI 转 outbound → `xray -test -c cfg.json` | ✔ pass（Configuration OK，4 条） |
| Hiddify | 内核即 sing-box，配置格式一致 | ◐ 未独立实测（见下） |

## 测试输入与诚实边界

**输入是格式测试样本，不是真实节点**：本地 `data/nodes.json` 是脱敏库（字段只有 name/proto/region/addr/src），不含 server/cred/URI；含完整字段与凭据的 `export/nodes_full.json` 只存在于 CI artifact，本地已清理。因此本轮用项目自己的 `nodes_parse.to_uri()` 构造了 4 条**合法格式**样本（vmess / vless / anytls / ss，src 明确标注 `format-test`），经 `export_subs.py` 生成四种订阅格式后交给内核解析。

**这证明什么**：本轮导出代码生成的 YAML/JSON/URI 能被三个内核正确接受——即导出格式兼容性在本轮仍然成立。

**这不证明什么**：不证明任何真实节点可用，也不证明 Hiddify GUI 能导入。节点可用性由 nodecheck 真握手判定，与此无关。

## 本轮真实命令与输出

```
# 二进制（真实存在，可执行）
gh/.nodes-cache/mihomo   61948030 B  executable
gh/.nodes-cache/sing-box 81416421 B  executable
gh/.nodes-cache/xray     35434620 B  executable

# 生成订阅（本轮）
python3 export_subs.py
  all   4  clash✔ singbox✔ uri✔ b64✔
  全部格式反解校验通过

# 内核验证（本轮，NODES_CACHE 指向真实二进制目录）
NODES_CACHE=.../gh/.nodes-cache python3 clienttest.py
  all      mihomo=pass sing-box=pass
  xray     all      pass 4 条  Configuration OK.
  报告 -> docs/P15-clients.md   （该报告已恢复为 P15 原版，本结果另存本文件）
```

Xray 未转换的协议：anytls 1 条（Xray 不支持 anytls，属内核能力边界，不算失败）。

## Hiddify 为何是"无法验证"而非"通过"

Hiddify 的代理内核就是 sing-box，所以它能否吃下这份订阅，逻辑上等价于上面的 sing-box pass。但 Hiddify 客户端本身没有免登录的命令行导入路径，沙箱内也没有 GUI 与实体手机可做扫码导入。因此**如实标注为未独立实测**，不把 sing-box 的结果冒充成"Hiddify GUI 实测通过"。

## 结论

- 本轮真测通过：Mihomo、sing-box、Xray 三端内核（基于格式测试样本）。
- 无法验证：Hiddify GUI 免登录导入（无 CLI / 无 GUI / 无实体手机）。
- P15 历史真值报告未改动（hash `65eae148...` 与远端一致），本轮结果独立存档，不覆盖历史证据。
