---
name: podcast-transcriber
version: "2.1.0"
description: "通用播客智能转录工具：从任意播客单集链接到结构化 PDF 的全自动流水线。支持 JSON-LD / OpenGraph / HTML5 多源元数据抓取，阿里云百炼语音转录，AI 摘要+话题分段，PDF 归档。"
tags: [podcast, transcription, summary, asr, pdf]
---

# Podcast Transcriber — 通用播客转录工具

## 触发条件

当用户消息中包含播客单集链接时自动触发。识别规则：
- 任意含播客音频的网页 URL（小宇宙、Apple Podcasts、Spotify 等）
- 直接音频文件 URL（.mp3/.m4a/.wav/.ogg/.aac/.flac/.opus，需同时提供标题）
- 用户可能直接发链接，也可能附带说明文字（如"帮我转录这个播客"）

## 工作流

收到播客链接后，按以下步骤执行：

### 1. 确认链接
- 从用户消息中提取播客链接
- 如果用户仅想预览信息，使用 `--dry` 模式
- 如果用户只要摘要不要逐字全文，使用 `--no-fulltext` 模式
- 如果是纯音频 URL，询问用户提供标题

### 2. 执行处理
```bash
cd <项目目录> && python -X utf8 main.py <链接>
```

参数说明：
- `<链接>`：播客单集网页 URL 或音频 URL
- `--title`, `-t`：手动指定标题（纯音频 URL 时必需）
- `--dry`：仅抓取元数据，不下载不转录（无需 API Key）
- `--no-fulltext`：仅输出摘要和话题索引，不包含逐字全文

**超时设置**：转录耗时取决于播客时长（通常 5-30 分钟），建议设置 timeout 为 600000ms，并使用后台执行。

### 3. 结果交付
处理完成后：
- 命令输出显示 Markdown 和 PDF 的完整路径，归档于 `output/` 目录
- 向用户简要汇报：标题、时长、摘要要点
- 使用 `present_files` 将 PDF 文件发送给用户

### 4. 错误处理
- 元数据抓取失败 → 提示用户检查 URL，或手动提供音频链接 + `--title`
- 音频下载失败 → 检查网络和源站可访问性
- API Key 过期 → 提醒用户检查环境变量 `DASHSCOPE_API_KEY`
- PDF 转换失败 → 交付 Markdown 文件作为降级方案

## 注意事项

- **Windows 必须使用 `python -X utf8`**，避免 GBK 编码错误
- **不要暴露 API Key**
- 转录耗时与播客时长正相关（约为时长的 30%-100%）
- 超过 3 小时的超长节目可能触发 ASR 分片上传，耗时显著增加
- 工具会缓存音频和转录结果（`audio_cache/`、`transcript_cache/`），重复运行同一集时会提示复用缓存
- PDF 生成需要 CJK 字体：Windows 自带 SimSun/SimHei；Linux 需安装 `fonts-wqy-zenhei`
- 依赖：`requests`, `reportlab`, `markdown`, `openai`

## 验证

- `--dry` 模式应输出标题、节目名、时长，无报错
- 完整运行应在 `output/` 下生成 .md 和 .pdf
- .md 结构：头部元数据 → AI 摘要 → 话题分段全文（--no-fulltext 时跳过）→ 页脚
- PDF 文件大小 > 0，页数 > 0
