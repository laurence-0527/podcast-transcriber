# Podcast Transcriber

**[English](README_EN.md) | [日本語](README_JA.md)**

把播客变成可检索、可速读的知识库。

## 为什么做这个工具

我订阅了几十档高质量播客——AI 前沿、商业洞察、技术深谈、投资方法论。每集 1-3 小时，每周新增十几集。听完是不可能的，但错过又可惜。

后来我找到了一个适合自己的节奏：**先转录，再速读，最后选听。**

1. **批量转录**：把攒下来的单集链接丢给工具，自动完成下载、语音转文字
2. **速读筛选**：花 2 分钟读一篇摘要（核心观点 + 金句 + 话题索引），判断这集值不值得花 1 小时去听
3. **深度收听**：对真正感兴趣的内容，带着话题索引去听，直接跳到最相关的段落

这本质上是一种 **"先广后深"的学习方法**——用 AI 把 1 小时音频压缩成 2 分钟阅读，让你的注意力只花在真正高价值的信息上。播客不再是"听完就忘"的流水账，而是可检索、可回顾、可引用的个人知识资产。

这个工具负责其中最机械的环节：**下载音频、语音转文字、生成带时间戳的转录文档**。摘要和分析则交给你（或你的 AI 助手）来完成——毕竟，理解内容本来就是学习者的事。

![AI 摘要示例](docs/screenshots/example_summary.png)

> 上图：由 AI 助手基于转录文本生成的结构化摘要（核心观点 + 方法论 + 金句 + 延伸阅读）。工具本身只输出时间戳转录，摘要部分交由你或你的 AI 助手完成。

## 功能

- **一条命令完成转录**：URL → 元数据抓取 → 音频下载 → 语音转文字 → Markdown + PDF
- **多源识别**：JSON-LD / OpenGraph / HTML5 audio / 直接音频 URL，不绑定特定平台
- **带时间戳的逐字转录**：每句话标注精确时间，方便跳转收听原文
- **缓存复用**：音频和转录结果本地缓存，重复处理同一集无需重新下载
- **默认完全本地运行**：faster-whisper（Whisper large-v3，CPU int8）识别 + FunASR ct-punc 标点恢复，输出与百炼 Paraformer 相当的**句级带标点分段**，无需任何云端 API Key，音频不离开本机
- **可选百炼 ASR**：配置 `asr.backend: bailian` 即可切换到 DashScope Paraformer，适合追求速度或本地算力不足的场景（见下文「本地转录 vs 云端转录」）
- **支持 YouTube 链接**：直接传入 YouTube 视频 URL，自动解析音频流并转录（需 `pip install yt-dlp`）
- **代理支持**：`network.proxy` 可配置 HTTP 代理（也支持环境变量 `HTTPS_PROXY`），用于访问 YouTube 等受限站点；代理失效时音频下载自动回退直连

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

如果你使用 CPU 运行，建议安装 CPU 版 PyTorch 以节省空间和下载时间：

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### 2. 本地转录的机器要求与耗时预估

默认本地管线 = **faster-whisper（Whisper large-v3，CPU int8 量化）识别 + FunASR ct-punc 标点恢复**，输出句级带标点分段，全程离线。

**首次运行会自动下载两个模型**（之后缓存复用）：

| 模型 | 用途 | 大小 | 下载源 |
|------|------|------|--------|
| Whisper large-v3（CTranslate2 格式） | 语音识别 | ~3.1GB | HuggingFace 或 ModelScope |
| ct-punc（CT-Transformer） | 标点恢复 | ~1.05GB | ModelScope（自动） |

**硬件要求（纯 CPU 即可，无需显卡）**：

| 配置档 | CPU | 内存 | 适用性 |
|--------|-----|------|--------|
| 最低 | 4 核 x86-64（2018 年后） | 8GB | 能跑，耗时约为音频时长的 6-8 倍 |
| 推荐 | 8 核+（如 i5-1240P / R7-5800U / M1 及以上） | 16GB | 耗时约为音频时长的 3-3.5 倍 |
| 宽裕 | 12 核+ 高频 / Apple Silicon | 16GB+ | 耗时约为音频时长的 2-3 倍 |

> 内存占用峰值约 4-5GB（识别模型 ~3GB + 标点模型 ~1.5GB）。若内存不足 8GB，建议改用云端百炼 ASR。

**耗时预估公式**：`转录耗时 ≈ 音频时长 × 实时率倍数 + 约 30 秒模型加载`。

实测参考（i5-1240P / 8 线程 / int8）：5 分钟节目约 15-18 分钟，1 小时节目约 3-3.5 小时。标点恢复很快（约 150 字/秒），占比可忽略。

> 提示：`cpu_threads`（默认 8）可按 CPU 物理核心数调整；`beam_size` 默认 1（中文场景质量与 beam=5 几乎无差、速度快 35%）。详见 `config/config.example.json` 的 `asr` 节注释。

### 3. 百炼 ASR（可选）

如果本地 CPU 太慢或你希望更快完成转录，可切换到百炼（DashScope）Paraformer：

```bash
cp config/config.example.json config/config.json
```

编辑 `config.json`：

```json
{
  "asr": {
    "backend": "bailian"
  },
  "bailian": {
    "api_key": "你的百炼 API Key",
    "model": "paraformer-v2"
  }
}
```

`api_key` 也支持从环境变量 `DASHSCOPE_API_KEY` 读取，避免写入配置文件。

### 本地转录 vs 云端转录：怎么选

两种后端输出格式完全一致（句级分段 + 时间戳 + Markdown/PDF），可随时切换。对照你的机器情况选择：

| 维度 | 本地（faster-whisper + ct-punc） | 云端（百炼 Paraformer） |
|------|----------------------------------|------------------------|
| 速度 | 约音频时长的 2-8 倍（取决于 CPU） | 约音频时长的 0.1-0.2 倍，1 小时节目几分钟 |
| 费用 | 免费 | 按调用量计费（有免费额度） |
| 隐私 | 音频不离开本机 | 音频上传云端 |
| 网络 | 仅首次需下载模型 | 全程需要网络 |
| 门槛 | 需 8GB+ 内存的 CPU | 需申请百炼 API Key |
| 质量 | large-v3 + 标点恢复，句级分段 | Paraformer-v2，句级分段 |

**按机器配置的推荐**：

- **内存 ≥ 16GB、近 5 年的 8 核+ CPU** → 本地转录。质量与云端相当，免费且私密；短节目（<20 分钟）等待完全可接受
- **内存 8-16GB、4-8 核 CPU** → 本地可用但偏慢。短节目用本地，1 小时以上的长节目建议云端，或本地挂机过夜
- **内存 < 8GB / 老旧 CPU / 需要立即拿到结果** → 云端百炼 ASR
- **内容敏感、不能上传** → 本地（无其他选项）；若机器太弱，考虑换台更好的机器跑

> 混合用法很常见：日常短节目本地跑，攒下来的长节目批量切到 `backend: bailian` 快速处理。

### 4. 网络代理（可选）

如果你的网络环境无法直连某些站点（如 YouTube），可配置 HTTP 代理。工具会在抓取元数据和下载音频时使用该代理；代理不可用时下载会自动回退为直连。

编辑 `config.json`：

```json
{
  "network": {
    "proxy": "http://127.0.0.1:7897"
  }
}
```

也支持环境变量 `HTTPS_PROXY` / `HTTP_PROXY`（配置文件优先）。

### 5. 转录播客

```bash
# 从播客网页自动抓取
python main.py https://example.com/podcast/episode-42

# 直接使用音频 URL（需手动提供标题）
python main.py https://cdn.example.com/audio/ep42.mp3 --title "AI 的未来"

# YouTube 视频（需已安装 yt-dlp；受限网络请配置 network.proxy）
python main.py https://www.youtube.com/watch?v=VIDEO_ID

# 仅抓取信息，不转录（预览用）
python main.py --dry https://example.com/podcast/episode-42
```

输出保存在 `output/` 目录，每集一个文件夹，包含 Markdown 和 PDF。

## 输出示例

一集 50 分钟的播客，输出结构如下：

```
# 标题
> 节目 | 来源 | 发布日期 | 时长
🔗 [收听原播客](url)

---

## 📝 转录全文（12,345 字）

[00:00] 大家好，欢迎收听本期节目...
[00:15] 今天我们要聊的主题是...
[01:02] 第一个关键点是...
...

---
_本文档由播客转录工具自动生成 · 仅供个人学习使用_
```

拿到转录文本后，你可以（或让你的 AI 助手）：
- 生成结构化摘要（核心观点 + 方法论 + 金句）
- 按话题切割索引（方便跳转收听）
- 提取行动项或延伸阅读
- 批量对比多集内容

## 工作原理

```text
URL 输入
  │
  ├─ podfetch.py      → 网页抓取：JSON-LD / OpenGraph / HTML5 audio；YouTube 链接走 yt-dlp
  ├─ podtranscribe.py → 下载音频（可选代理）→ faster-whisper 识别 + ct-punc 标点恢复 → 句级时间戳分段
  ├─ podsummarize.py  → 组装 Markdown 文档
  └─ md2pdf.py        → Markdown → PDF（中英文混排）
  │
  └─ output/<日期_播客名_单集标题>/  ← MD + PDF
```

## 项目结构

```
├── main.py               # CLI 入口
├── podfetch.py           # 通用元数据抓取
├── podtranscribe.py      # 音频下载 + 本地 faster-whisper 识别 + ct-punc 标点恢复
├── podsummarize.py       # 转录文档构建 + 输出管理
├── podauth.py            # 本地配置加载
├── md2pdf.py             # Markdown → PDF
├── SKILL.md              # AI Agent 技能描述（供智能体调用）
├── config/
│   └── config.example.json
└── output/               # 转录产物（gitignore）
```

## 支持的播客来源

不绑定任何特定平台。只要网页中包含以下任一元素即可自动识别：

- JSON-LD（`PodcastEpisode` / `AudioObject`）
- OpenGraph（`og:audio`）
- HTML5 `<audio>` 标签
- 直接音频 URL（.mp3 / .m4a / .wav / .ogg / .aac / .flac / .opus）
- YouTube 视频链接（经 yt-dlp 解析音频流，可选）

已验证：小宇宙、Apple Podcasts、Spotify（网页版）、TWiT、Libsyn 托管播客、YouTube 等。

## 作为 AI Agent 技能使用

本项目附带 `SKILL.md`，可直接作为智能体技能安装。智能体调用本工具完成转录后，利用自身模型能力生成摘要、话题分段、延伸阅读等——无需任何外部 API 配置。

## 常见问题

**Q: 转录一集需要多久？**
A: 下载 1-3 分钟；本地转录耗时约为音频时长的 2-8 倍（取决于 CPU，8 核机型约 3-3.5 倍），1 小时播客约 3-4 小时；切到百炼云端则只需几分钟。详见上文「本地转录的机器要求与耗时预估」。

**Q: 支持哪些语言？**
A: Whisper large-v3 支持中文、英文、日文等多语种，ct-punc 标点恢复支持中英混合。建议为中文播客设置 `"language": "zh"`。

**Q: 能转录 YouTube 上的播客吗？**
A: 可以，直接传 YouTube 链接即可（需先 `pip install yt-dlp`）。若提示无法连接 YouTube，请在 `network.proxy` 中配置可用代理（如 Clash 的 `http://127.0.0.1:7897`）。

**Q: PDF 中文乱码？**
A: 需要 CJK 字体。Windows/macOS 通常自带；Linux 请安装 `fonts-wqy-zenhei`。

**Q: 可以批量处理吗？**
A: 可以。写个简单的 shell 循环即可：
```bash
for url in $(cat urls.txt); do python main.py "$url"; done
```

## 数据流向

1. **元数据抓取**：从播客网页公开 HTML 提取标题、音频 URL（本地处理）
2. **音频下载**：从播客 CDN 下载到本地 `audio_cache/`
3. **语音转录**：faster-whisper 识别 + ct-punc 标点恢复，全部在本地运行，音频不离开本机（选择百炼后端时音频会上传云端）
4. **本地输出**：Markdown + PDF 保存在 `output/`

## 免责声明

本工具仅供个人学习与研究用途：

- 播客音频及转录文本版权归原作者/制作方所有，不得用于商业目的或公开再分发
- 请遵守播客平台的用户协议，如平台禁止程序化下载则勿使用本工具
- 转录全文仅用于个人回顾和学习笔记，引用应注明出处
- 因不当使用产生的一切法律后果由使用者自行承担

## License

MIT
