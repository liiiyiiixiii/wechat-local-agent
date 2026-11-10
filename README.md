# WeChat Local Agent

WeChat Local Agent 将微信已同步到本机的聊天记录解密并保存在你的电脑上，作为 Codex 和其他支持 MCP 的 AI 助手可以查询的本地知识库。

归档既保留明文 SQLite 数据库，也提供按会话和月份整理的 Markdown 聊天记录，并保存本机可用的附件。Codex 可以通过 MCP 检索聊天正文和已提取的文档内容，查看一条消息的前后文，并在回答中用消息 ID 引用原话。

**Windows 优先。** 已在 Windows 11 x64、Python 3.11 上用人工加密数据库验证安装、查询、归档和 MCP 全流程。真实微信版本的兼容性仍需按账号核验。macOS 保留既有入口，本版尚未实机复验。

## 开始前

- **先体验**只需要 Git 和 64 位 Python 3.11。下面的试跑会创建临时账号和样例消息，无需微信登录。
- **读取自己的记录**需要已获授权的账号目录和对应的 SQLCipher 数据库密钥。当前适配的布局包含 `db_storage/message/message_0.db`、`Name2Id` 和 `Msg_<哈希>` 会话表。密钥由使用者提供；旧版 `MSG*.db` 布局尚未适配。
- **数据保存在本机。** 服务只读打开微信源库，另外保存可检索的明文索引。导出时还会生成解密快照和附件副本。Windows 默认数据目录是 `%LOCALAPPDATA%\WeChatLocalAgent`。默认离线；显式启用 `--allow-cdn` 才会尝试从限定的微信 CDN 补齐表情。

## Windows 快速开始

### 1. 安装并用样例试跑

在 PowerShell 中运行：

```powershell
git clone https://github.com/liiiyiiixiii/wechat-local-agent.git
cd wechat-local-agent
py -3.11 -m venv .venv
$py = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $py -m pip install -c requirements-windows-py311.lock -e ".[documents]"
```

保持当前 PowerShell 窗口打开。以下命令会在临时目录建立索引，通过 CLI 和 MCP 查询样例、导出归档，再停止测试服务。结尾显示 `OK` 即为通过。

```powershell
$env:PYTHONUTF8 = '1'
& $py -m unittest discover -s tests -p test_windows_flow.py -v
```

### 2. 接入已授权的账号

准备一份 UTF-8 编码的密钥清单，存放在仅自己能访问的目录中。下面是格式示例，每个分库填写其对应的 64 个十六进制字符：

```json
{
  "keys": {
    "message/message_0.db": "<该分库的原始密钥>"
  }
}
```

将下面两个路径替换为实际位置。`$account` 指向包含 `db_storage` 的账号目录。

```powershell
$account = 'D:\示例\账号目录'
$keys = 'D:\示例\私有配置\keys.json'
& $py -m wxlocal configure --account "$account" --keys "$keys"
& $py -m wxlocal doctor
```

看到 `source_check` 为 `passed_first_message_database` 后启动服务。首次启动会从源库建立索引，无需预先导出归档。

```powershell
& $py -m wxlocal start
& $py -m wxlocal status
```

### 3. 搜索并查看前后文

等 `initialized` 为 `true`、`sync.state` 为 `ready` 后，把「项目排期」换成一段自己记得的聊天文字。示例先列出命中结果，再读取第一条消息前后各两条记录。

```powershell
$hits = & $py -m wxlocal search --query '项目排期' | ConvertFrom-Json
$hits.items | Select-Object id, time, text, citation
if ($hits.items.Count -gt 0) {
    & $py -m wxlocal context $hits.items[0].id --before 2 --after 2
}
```

## 接入 AI 助手

先启动本机服务。Codex 用户可运行 `& $py -m wxlocal register-codex`，然后重新加载 MCP。其他 MCP 客户端选择 **stdio**，将启动程序设为 `$py` 对应的 Python 绝对路径，参数填写 `["-m", "wxlocal", "mcp"]`。

| 想做什么 | MCP 工具 |
| --- | --- |
| 确认索引是否就绪，按名称查找会话 | `get_status`、`find_conversations` |
| 搜索消息，展开来源和上下文 | `search_messages`、`get_context` |
| 读取已匹配的本地附件或文档文本 | `read_attachment` |
| 接收新增和更新，处理后确认进度 | `get_changes`、`ack_changes` |

例如，可以让助手「查找群里最近一次讨论交付日期的消息，列出日期、发送者和消息引用」。聊天和附件中的文字应作为引用材料处理。做增量同步时，每个工作流使用独立的消费者 ID，处理成功后再确认游标。

## 归档与停用

```powershell
& $py -m wxlocal.archive.pipeline --offline
```

导出结果位于数据目录的 `exports/` 下。打开 `index.json` 找到会话，再阅读对应的月度 Markdown。`export-report.json` 分别记录消息数量、媒体缺失和文档提取情况。附件未下载或已过期时，仍会保留已有的消息记录。[归档格式、断点恢复与审计](src/wxlocal/archive/README.md)

`& $py -m wxlocal stop` 停止当前服务。登录自动启动需单独执行 `enable-autostart`。完整停用时，已注册 Codex 的用户先执行 `unregister-codex`，再执行 `uninstall`；索引、归档及密钥会保留。[数据位置、媒体组件与故障排查](docs/operations.md)

<details>
<summary>macOS 安装与旧版升级</summary>

准备 Python 3.11 或以上版本及兼容的 SQLCipher 4 动态库。若使用已有 WCDB，可通过 `SQLCIPHER_LIBRARY` 指定路径。在克隆后的仓库根目录运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[documents]'
.venv/bin/python -m wxlocal configure --account '/已授权账号目录' --keys '/私有目录/keys.json'
.venv/bin/python -m wxlocal doctor
.venv/bin/python -m wxlocal start
.venv/bin/python -m wxlocal status
```

旧版用户先用旧版本停止 LaunchAgent，并备份整个 `private/`。原地升级且未另设数据目录时，工具会识别原来的 `private/real-test.json`。升级后请核对一条已知消息的引用和一个消费者的确认游标。原有 `.command` 和 `agent_service/` 入口保留。

</details>

## 开发与验证

```powershell
& $py -m unittest discover -s tests -v
& $py -m wxlocal.archive.sqlcipher self-test
```

Windows 测试覆盖加密样例、WAL 增量读取、服务重启、MCP stdio、文档提取和归档篡改检查。
