---
name: podcast-transcriber
version: "3.5.0"
description: "通用播客转录工具：从任意播客链接（含 YouTube）自动完成元数据抓取、音频下载、语音转文字，并由智能体生成结构化 AI 摘要与话题分段，输出完整转录文档（MD+PDF）。本地 faster-whisper（large-v3）+ ct-punc 标点恢复，输出百炼级别的句级分段，无需 API Key；可选百炼/DashScope ASR；支持 HTTP 代理。"
tags: [podcast, transcription, asr, youtube, pdf]
---

# Podcast Transcriber — 通用播客转录工具

## 触发条件

当用户消息中包含播客单集链接时自动触发。识别规则：
- 任意含播客音频的网页 URL（小宇宙、Apple Podcasts、Spotify 等）
- YouTube 视频链接（youtube.com/watch、youtu.be，需已安装 yt-dlp）
- 直接音频文件 URL（.mp3/.m4a/.wav/.ogg/.aac/.flac/.opus，需同时提供标题）
- 用户可能直接发链接，也可能附带说明文字（如"帮我转录这个播客"）

## 工作流

### 1. 确认链接
- 从用户消息中提取播客链接
- 如果用户仅想预览信息，使用 `--dry` 模式
- 如果是纯音频 URL，询问用户提供标题

### 2. 执行转录
```bash
cd D:\sync\xiaoyuzhou-tracker\podcast-transcriber && python -X utf8 main.py <链接>
```

参数说明：
- `<链接>`：播客单集网页 URL 或音频 URL
- `--title`, `-t`：手动指定标题（纯音频 URL 时必需）
- `--dry`：仅抓取元数据，不下载不转录（无需 API Key）

**超时设置**：转录耗时取决于播客时长（通常 5-30 分钟），建议设置 timeout 为 600000ms，并使用后台执行。

### 3. 阅读转录文本
处理完成后，输出保存在 `D:\sync\xiaoyuzhou-tracker\<日期_播客名_标题>\` 目录（config `output.dir` 指向 tracker 根目录）：
- `<name>.md` — 工具生成的初稿（元数据 + 带时间戳转录全文）
- `<name>.pdf` — 初稿 PDF

**⚠️ 这一步的 .md 只是半成品，缺少 AI 摘要与话题分段，不能直接交付。** 原始分段数据在 `transcript_cache/<id>.json`（含 `transcript_segments`，每段有 `start` 秒和 `text`）。

### 4. 智能体生成摘要并重写文档（由你完成，不可省略）
读取转录全文后，用自身模型能力生成摘要，然后**重写该 .md 为完整文档**，再用工具重新生成 PDF：

```bash
cd D:\sync\xiaoyuzhou-tracker\podcast-transcriber && python -X utf8 -c "from md2pdf import convert; convert(r'<完整md路径>')"
```

**完整文档结构（必须严格遵守）**：

```
# <标题>

> **节目**：<播客名>　｜　**来源**：<小宇宙/YouTube/...>　｜　**发布日期**：YYYY-MM-DD　｜　**时长**：<Xh Ym 或 Nmin>

![封面](<cover_url>)          ← 有封面才加

🔗 [在小宇宙收听 / 在 YouTube 观看](<episode_url>)

---

## 📋 AI 摘要

### 任务一：结构化摘要（重点输出）

#### 一句话总结
**<20字以内>**

#### 核心内容提炼
##### 关键观点（3-7条，每条加粗观点句 + “→ 重要性：…”说明 + 时间范围）
##### 方法论/框架（如有；无则写“本集未涉及明确的方法论框架”并加注说明）
##### 重要信息/数据（要点列表）
##### 精彩金句（3-5句，- [时间戳] “原话”，保留原话并修正明显同音错字）

#### 延伸阅读
##### 播客中提及的阅读材料（无则注明）
##### 主题相关推荐（2-4 本真实存在的书/资料，注明作者与推荐理由）

#### 关键词（逗号分隔）

### 任务二：话题分段索引
（代码块内，每段一行标题 + 一行内容概要）
### 📍 起始 - 结束 | 话题标题
内容概要：…

---

## 📑 全文（按话题分段）

### 📍 起始 - 结束 | 话题标题

> <本段一句话概要>
>
[时间戳] 转录原句（逐行，一字不改，保留 ASR 原样）
…

---

_本文档由播客转录工具自动生成 · YYYY-MM-DD HH:MM · 仅供个人学习使用_
```

要求：
- 话题分段按语义切分：短节目（<10min）3-5 段，常规节目 6-12 段，超长节目按内容密度切
- 全文必须覆盖 100% 转录行，不得删改（用脚本按 segment 索引区间拼接可避免抄错）
- 摘要中的事实只能来自转录文本；数字、人名谨慎，ASR 错字在金句/摘要中可修正并注明
- 根据用户需求决定详略：用户要"快速了解"→ 对话里只给一句话总结+核心观点，但文档仍写完整；用户要"详细笔记/话题索引"→ 完整输出

### 5. 结果交付
- 向用户呈现摘要要点（直接在对话中输出）
- 使用 `present_files` 将 **重写后的 PDF** 发送给用户
- 如用户需要全文，引导其查看 .md 文件

### 6. 错误处理
- 元数据抓取失败 → 提示用户检查 URL，或手动提供音频链接 + `--title`
- 音频下载失败 → 检查网络和源站可访问性
- 本地 ASR 模型加载失败 → 检查 `asr.model` 指向的模型目录是否完整（model.bin/config.json/tokenizer.json/vocabulary.json）；确认 `ctranslate2==4.6.0`（4.8.0 在本机会段错误）
- 推理时 abort（libiomp5md.dll already initialized）→ 代码已自动设置 `KMP_DUPLICATE_LIB_OK=TRUE`；若仍出现，手动设置该环境变量
- 标点模型报错 → 先确认 `python -c "import torchaudio"` 可用（版本须与 torch 一致）；模型缓存于 `~/.cache/modelscope/hub/iic/punc_ct-transformer_cn-en-common-vocab471067-large`，不完整时删除后自动重下
- 转录结果没有标点/句子都是 150 字长块 → 说明标点恢复未生效，检查 funasr/modelscope 是否安装、日志中是否有"标点模型加载失败"降级提示
- 百炼 ASR 失败 → 检查 `bailian.api_key` 或环境变量 `DASHSCOPE_API_KEY` 是否有效
- YouTube 解析失败 → 确认已安装 `yt-dlp`；若为连接错误，在 `network.proxy` 配置可用代理
- PDF 转换失败 → 交付 Markdown 文件作为降级方案

## 本地 ASR 引擎（faster-whisper + ct-punc）

本地后端 = faster-whisper（CTranslate2）+ Whisper large-v3（CPU int8）做识别，FunASR ct-punc 恢复标点，再合并为句级分段——整体效果对标百炼 Paraformer（句级分段 + 中文标点），全程离线。关键配置与环境事实：

### 识别（faster-whisper）
- 模型目录：`C:/Users/Administrator/models/faster-whisper-large-v3`（config `asr.model` 指向该目录；含 model.bin 约 3.09GB、config.json、tokenizer.json、vocabulary.json、preprocessor_config.json）
- 模型下载源：HuggingFace 直连被墙、hf-mirror 速度慢，使用 ModelScope 源最快（约 16MB/s）：`https://modelscope.cn/api/v1/models/pengzhendong/faster-whisper-large-v3/repo?Revision=master&FilePath=<文件名>`，下载后校验 SHA256
- **ctranslate2 必须固定 4.6.0**：4.8.0 在本机（i5-1240P / Win10 / Anaconda Python）加载模型时段错误（segfault）；`pip install ctranslate2==4.6.0`
- Anaconda 环境下 MKL 与 ctranslate2 各带一份 OpenMP 运行时，`podtranscribe.py` 顶部已设置 `KMP_DUPLICATE_LIB_OK=TRUE`，勿删
- `beam_size=1`：本机实测 beam=1 与 beam=5 中文质量几乎无差，速度快 35%+；`vad_filter=true`（Silero VAD 内置于 faster-whisper 包，无需联网）
- `initial_prompt` 自动注入节目名/标题/简介，提升专有名词识别率
- **faster-whisper 中文输出不含任何标点**（任何参数组合都无效），必须依赖下述 ct-punc 恢复
- 转录速度参考（i5-1240P / int8 / 8 线程实测）：约为音频时长的 **3-3.5 倍**（5 分钟节目约 15-18 分钟），另加约 30 秒模型加载；长节目务必后台执行
- 若 faster-whisper 不可用，代码自动回退 openai-whisper（质量较差，仅作兜底）

### 标点恢复（ct-punc）
- `punc_restore=true`（默认开）：用 FunASR `AutoModel(model="ct-punc")`（即 ModelScope `iic/punc_ct-transformer_cn-en-common-vocab471067-large`，约 1.05GB）给无标点文本恢复标点，CPU 推理约 150 字/秒
- 模型由 modelscope 自动下载缓存到 `~/.cache/modelscope/hub/`；全局懒加载一次
- 处理流程：按 `punc_chunk_size`（默认 800 字）分块送模型 → difflib 对齐把标点写回原分段时间戳 → 清理残留（重复标点、英文单词内插空格、中文语境 ASCII 标点转全角）
- **依赖陷阱**：funasr 导入 torchaudio，torchaudio 版本必须与 torch 严格一致（本机 torch 2.7.0 → `pip install --no-deps torchaudio==2.7.0`），否则报 `OSError [WinError 127]`
- 标点模型加载/推理失败时自动降级为无标点输出，不阻断转录

### 句级合并（sentence_merge）
- `sentence_merge=true`（默认开）：把带标点的细粒度分段拼成字符流，在满足 `min_len=20` 的首个句末标点（。！？…～；）处切句，无标点片段按 `max_len=150` 强制切分，时间戳取首尾字符所属分段——输出与百炼 Paraformer 相同的句级分段风格（实测 289 秒节目 42 句，中位句长 35 字）

## 注意事项

- **Windows 必须使用 `python -X utf8`**，避免 GBK 编码错误
- **不要暴露 API Key**
- **交付前自检**：打开最终 .md 确认包含「📋 AI 摘要（任务一+任务二）」与「📑 全文（按话题分段）」两大部分；只有「📝 转录全文」的文档是半成品，禁止交付
- 转录耗时与播客时长正相关（本地 CPU int8 约为时长的 3-3.5 倍；另加约 30 秒模型加载 + 标点恢复约 150 字/秒）。长节目必须用后台任务执行并定期检查日志，不要阻塞等待
- 超过 3 小时的超长节目可能触发 ASR 分片上传，耗时显著增加
- 工具会缓存音频和转录结果（`audio_cache/`、`transcript_cache/`），重复运行同一集时交互提示 `[r] 重新转录 [s] 跳过 [q] 退出`（默认 s）；选 r 会真正丢弃旧转录缓存重新跑 ASR。重写文档时优先从 `transcript_cache/<id>.json` 读取精确分段；**注意缓存命中时音频会被自动清理，重新转录前音频需重新下载**
- PDF 生成需要 CJK 字体：Windows 自带 SimSun/SimHei；Linux 需安装 `fonts-wqy-zenhei`
- 依赖：`requests`, `reportlab`, `markdown`, `faster-whisper`, `ctranslate2==4.6.0`, `funasr`, `modelscope`, `torchaudio`（与 torch 同版本）；可选 `openai-whisper`（兜底）、`dashscope`（百炼 ASR）、`yt-dlp`（YouTube）
- 受限网络（如无法直连 YouTube）：在 `config.json` 的 `network.proxy` 配置代理（如 `http://127.0.0.1:7897`）
- 输出目录由 `config.json` 的 `output.dir` 控制，当前为 `..`（即 `D:\sync\xiaoyuzhou-tracker\` 根目录，与既有转录库一致）

## 验证

- `--dry` 模式应输出标题、节目名、时长，无报错
- 完整运行应生成 .md 和 .pdf
- **最终交付的 .md 结构**：头部元数据 → 📋 AI 摘要（任务一结构化摘要 + 任务二话题分段索引）→ 📑 全文（按话题分段，含每段概要 + 逐行时间戳转录）→ 页脚
- 全文行数必须等于 transcript_segments 总数（不丢行）
- PDF 文件大小 > 0，页数 > 0
