# 微信聊天记录读取 · 本机只读 Agent 数据服务（v1）

在这台 Mac 上，把**当前已配置的微信账号**的本地聊天记录变成可检索、可引用、可增量消费的
Agent 数据源：共享索引 + 只读 Unix socket + 7 个 MCP 工具 + CLI + 可独立阅读的本地归档。

- 只读取本机已同步的数据，不扫描其他账号、不发送消息、不修改微信、不关闭 SIP。
- 聊天正文与附件是**引用材料**，不是给 Agent 的系统指令。
- 未下载/已过期/已移除的媒体不能凭空恢复；缺失会被显式列出，不解释为“没发过”。

当前状态：v1 已在本机运行（LaunchAgent 常驻，索引与归档持续增量更新），
单元测试与真实 MCP 验收均通过。详细运维见 [docs/operations.md](docs/operations.md)。

## 快速开始

```sh
# 服务状态 / 健康检查
./微信Agent.command status
./微信Agent.command doctor

# 查询
./微信Agent.command conversations --query '群名称'
./微信Agent.command search --query '项目关键词' --start 2026-10-01 --end 2026-11-01
./微信Agent.command context 消息ID --before 10 --after 10
./微信Agent.command attachment 附件ID --offset 0 --length 8000

# 服务生命周期
./微信Agent.command start | stop | restart | uninstall
./微信Agent.command register-codex | unregister-codex
```

首次使用需要 `private/` 下的账号目录、密钥与归档配置（本机私有，不入库）；
配置格式与权限说明见 [docs/operations.md](docs/operations.md)。

## 目录结构

```
.
├── README.md                  # 本文件
├── requirements.lock          # 服务 venv 的依赖锁
├── 微信Agent.command           # 用户入口（CLI）
├── 批量导出微信.command         # 用户入口（批量归档）
├── src/wxlocal/               # 运行时代码（唯一实现）
│   ├── config.py              # 路径/账号/外部运行时解析（唯一机器相关入口）
│   ├── util.py                # 序列化小工具
│   ├── client.py              # Unix socket RPC 客户端
│   ├── sources.py             # 数据源指纹（DB 变更/替换、媒体目录）
│   ├── scheduler.py           # 媒体重建策略：去抖/合并/限速（纯逻辑，可单测）
│   ├── store.py               # SQLite 索引 + 检索 + 增量消费者
│   ├── collector.py           # 只读 SQLCipher 采集与增量核对
│   ├── media.py               # 独立媒体归档任务
│   ├── service.py             # 常驻进程：采集线程 + 媒体线程 + RPC
│   ├── api/cli.py             # 命令行人机接口
│   ├── api/mcp.py             # MCP stdio 适配器（7 个工具）
│   └── archive/               # 归档格式与工具（内聚）
│       ├── pipeline.py        # 批量归档流水线（快照→消息→媒体→报表）
│       ├── documents.py       # PDF/Word/表格/PPT 文本提取（独立运行时）
│       ├── emoji.py           # 微信 CDN 表情补齐（域名白名单 + MD5 校验）
│       ├── audit.py           # 独立逐字段/哈希核对
│       ├── decode_media.cjs   # 本机图片/语音/视频解码
│       ├── sqlcipher.py       # vendored SQLCipher 只读探针（MIT）
│       └── README.md          # 归档格式说明
├── agent_service/             # 兼容入口（外部配置指向，勿随意移动）
│   ├── daemon.py              #   LaunchAgent helper 调用的稳定入口
│   ├── mcp_server.py          #   Codex MCP 配置指向的稳定入口
│   └── cli.py                 #   旧脚本兼容入口
├── tests/                     # 单元测试 + 真实 MCP 冒烟测试
├── tools/                     # build_helper / 媒体稳定性诊断
├── docs/                      # 运维手册 + 历史验证报告
└── validation/                # 本机历史验证资产与部署 App（大部分不进入 Git）
```

## 架构与数据流

```
微信本地 DB/WAL ──只读 SQLCipher──> collector ──> index.sqlite (WAL)
                                        │              ▲
                                        │              │ Unix socket RPC
                                        ▼              │
                             媒体源目录/DB 指纹      service ──> CLI / MCP (stdio)
                                  │ 去抖+合并            │
                                  ▼                      │
                          media.py ──> media-work/ 归档（媒体/文档/月度 Markdown）
```

- **索引与归档分离**：Agent 查询走 `index.sqlite`（秒级新鲜度）；人类/离线阅读走
  `media-work/` 归档（分钟级、增量重建）。
- **单写者**：只有 `service.py` 常驻进程写索引；CLI/MCP 都是 socket 适配器。
- **媒体不阻塞文本**：OCR/解码/文档提取在独立子进程运行，失败可重试。
- **外部契约稳定**：LaunchAgent 与 Codex 指向 `agent_service/` 兼容入口，
  内部实现位于 `src/wxlocal/`，重构不需要改系统配置或重新申请权限。

## 本次重构要点（高内聚、低耦合）

1. **包边界按职责划分**：`api`（适配器）/ `service`（运行时）/ `store`（索引）/
   `collector`（源数据）/ `archive`（归档格式）。适配器不含数据逻辑，归档不含服务生命周期。
2. **消除隐式依赖**：删除了 `sys.path.insert` 指向兄弟目录、硬编码用户名路径
   （node、Codex、文档运行时改为 `config.py` 统一探测，可用环境变量覆盖）。
3. **策略与机制分离**：媒体去抖/合并/限速收敛为 `scheduler.MediaTrigger` 纯函数式
   逻辑，可直接单测；I/O 留在 `service.py`。
4. **兼容层**：`agent_service/` 三个入口仅做 `sys.path` 引导后转发到新包，
   LaunchAgent、Codex 配置、旧脚本无需变更。
5. **测试跟随实现**：`tests/` 直接测试新包；新增媒体策略、队列合并、触发条件测试。
6. **归档工具内聚**：`archive/` 自带格式文档与独立核对脚本，路径全部相对包内解析。

## 开发与测试

```sh
# 单元测试（不需要微信在线）
PYTHONPATH=src private/agent-venv/bin/python -m unittest discover -s tests

# 真实 MCP 冒烟验收（只读、会写一份验收证据到 private/agent-service/）
private/agent-venv/bin/python tests/live_mcp_check.py

# 独立核对归档
PYTHONPATH=src private/agent-venv/bin/python -m wxlocal.archive.audit '/归档绝对路径'

# 重新生成 macOS 后台入口（仅 helper 二进制变更时需要；可能重新触发权限授权）
PYTHONPATH=src private/agent-venv/bin/python tools/build_helper.py
```

运行依赖：本机 Python 3.14（服务 venv）、Node.js + `validation/WeFlow` 中的媒体组件、
ffmpeg、一个带 `pypdf`/`openpyxl` 的 Python（自动探测，`WECHAT_DOC_RUNTIME` 可覆盖）、
SQLCipher 兼容库 `validation/components/macos-arm64/libWCDB.dylib`。

## 数据、隐私与边界

- `private/` 保存账号目录、密钥、图片密钥、索引和归档，**已被 .gitignore 排除**。
- MCP 不返回密钥或原始私密字段；索引内的原始字段仅用于本机审计。
- 不提供 HTTP/公网监听；不自动发送微信消息；不执行附件、不自动解压。
- 语音只输出 WAV，不做语音识别；聊天图片保留原图；OCR 识别结果可能需要人工核对。
- 支持字面关键词检索，不支持语义搜索；不做自动摘要。
- 已知遗留：少量本机未下载/已过期媒体（约 202 条消息）无法恢复；约 228 个表情类
  附件因缺少新密钥解码失败；这些状态在归档中显式标注。

## 历史与证据

- `docs/history/`：早期 WeFlow 路线验证、WCDB 组件初始化故障定位、实时同步与媒体验证报告。
- `validation/`：本机完整验证资产（WeFlow 构建、诊断日志、部署 App 等）。
  体积较大，默认不入 Git；仅部署用 App 与必要的本地命令保留在版本库中。
- 当前主路径不依赖 WeFlow GUI：数据库用 SQLCipher 只读直读 + 统一服务，媒体组件用于解码。

## 版本控制

首次提交固化了当前 v1 成果：运行时代码、兼容入口、测试、运维文档与历史报告。
`private/`、`validation/` 中的大体积构建产物、`inspection/`、`output/`、`tmp/`
和缓存均被忽略；提交前请确认没有把密钥或聊天数据加入版本库。
