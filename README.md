# Podcast Transcriber

**[English](README_EN.md) | [日本語](README_JA.md)**

把播客变成可检索、可速读的知识库。

## 为什么做这个工具

我订阅了几十档高质量播客——AI 前沿、商业洞察、技术深谈、投资方法论。每集 1-3 小时，每周新增十几集。听完是不可能的，但错过又可惜。

后来我找到了一个适合自己的节奏：**先转录，再速读，最后选听。**

1. **批量转录**：把攒下来的单集链接丢给工具，自动完成下载、语音转文字、AI 摘要
2. **速读筛选**：花 2 分钟读一篇结构化摘要（核心观点 + 金句 + 话题索引），判断这集值不值得花 1 小时去听
3. **深度收听**：对真正感兴趣的内容，带着话题索引去听，直接跳到最相关的段落

这本质上是一种 **"先广后深"的学习方法**——用 AI 把 1 小时音频压缩成 2 分钟阅读，让你的注意力只花在真正高价值的信息上。播客不再是"听完就忘"的流水账，而是可检索、可回顾、可引用的个人知识资产。

这个工具就是这套方法的自动化实现。

## 功能

- **一条命令完成全部**：URL → 元数据抓取 → 音频下载 → 语音转文字 → AI 摘要 + 话题分段 → Markdown + PDF
- **多源识别**：JSON-LD / OpenGraph / HTML5 audio / 直接音频 URL，不绑定特定平台
- **AI 深度摘要**：不是简单总结，而是观点提炼 + 方法论框架 + 金句（带时间戳）+ 延伸阅读推荐
- **话题分段索引**：按内容语义自动切割，每段标注时间范围和内容概要，方便跳转收听
- **`--no-fulltext` 模式**：只输出摘要和索引，不保存逐字全文——适合"速读筛选"场景

## 快速开始

### 1. 安装依赖

```bash
pip install requests reportlab markdown openai
```

### 2. 配置 API Key

```bash
cp config/config.example.json config/config.json
# 编辑 config.json 填入百炼 API Key，或设置环境变量
export DASHSCOPE_API_KEY=sk-your-key-here
```

API Key 获取：[阿里云百炼控制台](https://dashscope.console.aliyun.com/apiKey)

### 3. 转录播客

```bash
# 从播客网页自动抓取
python main.py https://example.com/podcast/episode-42

# 直接使用音频 URL（需手动提供标题）
python main.py https://cdn.example.com/audio/ep42.mp3 --title "AI 的未来"

# 仅抓取信息，不转录（预览用）
python main.py --dry https://example.com/podcast/episode-42

# 仅输出摘要和话题索引，不含逐字全文（速读筛选模式）
python main.py --no-fulltext https://example.com/podcast/episode-42
```

输出保存在 `output/` 目录，每集一个文件夹，包含 Markdown 和 PDF。

## 输出示例

一集 50 分钟的播客，输出结构如下：

```
# 标题
> 节目 | 来源 | 发布日期 | 时长

## 📋 AI 摘要
### 一句话总结
### 核心内容提炼（关键观点 / 方法论 / 重要数据 / 精彩金句）
### 延伸阅读（节目中提及的 + 主题相关推荐）
### 关键词

## 📑 全文（按话题分段）        ← --no-fulltext 时跳过此部分
### 📍 00:00 - 12:35 | 话题标题
> 内容概要
[00:00] 逐字转录文本...
```

## 工作原理

```text
URL 输入
  │
  ├─ podfetch.py      → 网页抓取：JSON-LD / OpenGraph / HTML5 audio
  ├─ podtranscribe.py → 下载音频 → 百炼异步转录 → 时间戳分段
  ├─ podsummarize.py  → LLM 摘要 + 话题分段 + 延伸阅读
  └─ md2pdf.py        → Markdown → PDF（中英文混排）
  │
  └─ output/<日期_播客名_单集标题>/  ← MD + PDF
```

## 项目结构

```
├── main.py               # CLI 入口
├── podfetch.py           # 通用元数据抓取
├── podtranscribe.py      # 音频下载 + 阿里云百炼转录
├── podsummarize.py       # AI 摘要 + 话题分段 + 文档生成
├── podauth.py            # API Key 配置加载
├── md2pdf.py             # Markdown → PDF
├── SKILL.md              # AI Agent 技能描述（可供智能体直接调用）
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

已验证：小宇宙、Apple Podcasts、Spotify（网页版）、TWiT、Libsyn 托管播客等。

## 常见问题

**Q: 转录一集需要多久？**
A: 下载 1-3 分钟 + 异步转录 5-15 分钟（取决于音频时长和服务负载）。1 小时播客总计约 10-20 分钟。

**Q: 支持哪些语言？**
A: 阿里云百炼 ASR 支持中文、英文、日文等多语种。摘要默认中文输出。

**Q: PDF 中文乱码？**
A: 需要 CJK 字体。Windows/macOS 通常自带；Linux 请安装 `fonts-wqy-zenhei`。

**Q: 可以批量处理吗？**
A: 可以。写个简单的 shell 循环即可：
```bash
for url in $(cat urls.txt); do python main.py --no-fulltext "$url"; done
```

## 数据流向

1. **元数据抓取**：从播客网页公开 HTML 提取标题、音频 URL（本地处理）
2. **音频下载**：从播客 CDN 下载到本地 `audio_cache/`
3. **语音转录**：音频上传至**阿里云百炼（DashScope）**进行异步识别
4. **AI 摘要**：转录文本发送至**百炼 LLM API**（默认 qwen-plus）生成摘要
5. **本地输出**：Markdown + PDF 保存在 `output/`

步骤 3、4 会将数据传输到阿里云服务器。请确认您有权处理相关内容。

## 免责声明

本工具仅供个人学习与研究用途：

- 播客音频及转录文本版权归原作者/制作方所有，不得用于商业目的或公开再分发
- 请遵守播客平台的用户协议，如平台禁止程序化下载则勿使用本工具
- 转录全文仅用于个人回顾和学习笔记，引用应注明出处
- 因不当使用产生的一切法律后果由使用者自行承担

## License

MIT
