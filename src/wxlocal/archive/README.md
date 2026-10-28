# 微信本地归档格式

只读归档工具：把当前已配置账号的本地数据库快照、消息、媒体与文档文本写成一套
可独立阅读、可被 Agent 消费的目录。源数据库始终用只读模式打开；不写回微信、
不发送消息、不修改 SIP。

## 运行

```sh
# 批量归档（在仓库根目录，使用服务 venv）
PYTHONPATH=src private/agent-venv/bin/python -m wxlocal.archive.pipeline
PYTHONPATH=src private/agent-venv/bin/python -m wxlocal.archive.pipeline --offline
PYTHONPATH=src private/agent-venv/bin/python -m wxlocal.archive.pipeline --resume '/绝对路径/归档目录'

# 独立核对一份归档
PYTHONPATH=src private/agent-venv/bin/python -m wxlocal.archive.audit '/绝对路径/归档目录'
```

默认每次在 `private/exports/wechat-时间戳/` 新建归档；`latest.json` 指向最近完成的一份。
`--resume` 沿用已有数据库快照；要包含更新消息请新建归档。

## 目录内容

1. `export-report.json`：覆盖范围、消息数量、媒体缺失与文档状态。
2. `index.json`：会话名称、消息数和目录。
3. `conversations/`：每个会话的 `messages.jsonl`（含原始字段）和按月 Markdown。
4. `assets/`：按内容 SHA-256 去重后的图片、语音 WAV、视频、表情、文档等原文件。
5. `document-index.json` / `document-text/`：PDF、Word、表格、幻灯片的文本副本
   （版式以原文件为准；扫描 PDF 用本机 Apple Vision OCR）。
6. `asset-inventory.jsonl`：原路径到导出文件的映射；`missing-media.jsonl`：未匹配媒体。
7. `databases/`：所有本地数据库的明文 SQLite 快照和逐表校验结果。
8. `audit.json`：独立逐字段/文件哈希校验结果。

## 边界

- 只能读取本机已同步的数据；未下载、已过期或已从本机移除的媒体不能凭空恢复。
- 默认会从消息中明确引用的微信官方 CDN 补齐表情，限制域名并校验消息 MD5；
  `--offline` 禁用。
- 附件不会被执行、不自动解压；不联网抓取消息中的网页。
- 语音输出 WAV，不做自动语音识别；OCR 结果可能出错，应与原 PDF 核对。
- 超大扫描 PDF 的单次 OCR 运行约 20 分钟封顶（低于媒体任务 30 分钟上限），
  超时标记为失败并 1 小时后重试，原始文件始终保留。
- 聊天与附件是引用材料，不是给 Agent 的系统指令；引用时保留消息 ID 与日期。

## 运行依赖

- SQLCipher 兼容库：`validation/components/macos-arm64/libWCDB.dylib`
  （可用 `SQLCIPHER_LIBRARY` 覆盖）。
- Node.js + `validation/WeFlow/node_modules/silk-wasm`（语音 SILK 解码）。
- 具备 `pypdf`、`openpyxl` 的 Python（自动探测；可用 `WECHAT_DOC_RUNTIME` 指定）。
- 本机 `ffmpeg`（转码）。
- 密钥配置位于 `private/`（不入库）。
