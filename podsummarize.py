"""
podsummarize.py — AI 摘要生成 + 全文话题分段 + Markdown 文档输出

两阶段处理：
  1. 摘要提取：重点提取关键信息、观点、方法论等体系化内容
  2. 全文分段：AI 按话题自动分段，标注每段时间范围和内容概要

输出文档结构：元数据 → 摘要 → 话题分段全文 → 纯文本附录
"""

import re
import time
import json
import shutil
from pathlib import Path
from datetime import datetime
from openai import OpenAI

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────
# LLM 纠错（ASR 后处理）
# ─────────────────────────────────────────

CORRECTION_PROMPT = """你是一位专业的语音转录文本校对员。以下是一段播客的 ASR（自动语音识别）转录文本，其中存在同音字/近音字导致的专有名词错误。

## 播客信息
- 节目名称：{podcast_name}
- 单集标题：{title}
- 节目简介：{description}

## 上下文线索
{context}

## 待校对文本
{transcript}

---

## 任务
请修正转录文本中的**专有名词错误**（人名、公司名、产品名、术语、地名等），保持其他内容完全不变。

规则：
1. **只修正专有名词的同音/近音错误**，不改动其他内容
2. **参考上下文线索**：从标题、节目名、简介中提取人名和术语作为依据
3. **保留时间戳格式**：每行开头的 `[HH:MM:SS]` 或 `[MM:SS]` 必须原样保留
4. **不要删减或增加内容**，只做字符替换
5. 如果不确定某个词是否为错误，保持原文不变
6. 输出完整的校对后文本，不要输出解释说明

## 校对后文本
"""


def _build_context_hints(episode: dict) -> str:
    """从元数据中提取上下文线索，帮助 LLM 识别正确的人名和术语"""
    hints = []

    title = episode.get("title", "")
    description = episode.get("description", "")
    podcast_name = episode.get("podcast_name", "")

    # 从标题提取可能的人名（常见模式："和XXX聊"、"XXX对话"、"嘉宾XXX"）
    import re
    # 匹配 "和/与/对话/聊/专访 X名" 模式
    patterns = [
        r'[与和]([^，。、｜\s聊对话探讨谈论]{2,6})(?:聊|对话|探讨|谈|论)',
        r'对话\s*[:：]?\s*([^，。、｜\s]{2,6})',
        r'嘉宾\s*[:：]?\s*([^，。、｜\s]{2,6})',
        r'专访\s*([^，。、｜\s]{2,6})',
    ]
    names_from_title = set()
    for pat in patterns:
        matches = re.findall(pat, title)
        names_from_title.update(matches)

    if names_from_title:
        hints.append(f"从标题识别到的人物：{'、'.join(names_from_title)}")

    # 节目名本身也可能是人名节目
    hints.append(f"节目名称：{podcast_name}")

    # 从描述中提取可能的上下文（截取前 500 字避免过长）
    if description:
        hints.append(f"节目简介（参考）：{description[:500]}")

    return "\n".join(hints) if hints else "（无额外上下文线索）"


def correct_transcript(episode: dict, config: dict) -> str:
    """
    用 LLM 对转录文本做专有名词纠错（ASR 后处理）。
    保留时间戳格式，仅修正文本中的同音字错误。
    返回纠错后的完整转录文本（带时间戳）。
    """
    segments = episode.get("transcript_segments", [])
    transcript_raw = episode.get("transcript", "")

    # 构建带时间戳的转录文本
    if segments:
        transcript_with_ts = format_transcript_with_timestamps(segments)
    else:
        transcript_with_ts = transcript_raw

    if not transcript_with_ts.strip():
        print("  ⚠️  转录文本为空，跳过纠错")
        return transcript_with_ts

    client, model = get_summary_client(config)
    context = _build_context_hints(episode)

    # 分批处理（每批约 4 万字，避免超限）
    BATCH_SIZE = 40000
    text = transcript_with_ts

    if len(text) <= BATCH_SIZE:
        batches = [text]
    else:
        # 按行分割，避免截断时间戳行
        lines = text.split("\n")
        batches = []
        current_batch = []
        current_len = 0

        for line in lines:
            if current_len + len(line) > BATCH_SIZE and current_batch:
                batches.append("\n".join(current_batch))
                current_batch = [line]
                current_len = len(line)
            else:
                current_batch.append(line)
                current_len += len(line)

        if current_batch:
            batches.append("\n".join(current_batch))

    print(f"  🔤 LLM 纠错（模型: {model}，转录文本 {len(text)} 字，分 {len(batches)} 批）...")

    corrected_parts = []
    for i, batch in enumerate(batches, 1):
        prompt = CORRECTION_PROMPT.format(
            podcast_name=episode.get("podcast_name", "未知节目"),
            title=episode.get("title", "未知标题"),
            description=episode.get("description", ""),
            context=context,
            transcript=batch,
        )

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,  # 低温度，尽量保持原文不变
            max_tokens=max(len(batch) + 500, 4000),  # 输出长度略大于输入
        )

        corrected = response.choices[0].message.content.strip()
        corrected_parts.append(corrected)
        print(f"    纠错第 {i}/{len(batches)} 批完成")

    result = "\n".join(corrected_parts)
    print(f"  ✅ 纠错完成（{len(result)} 字）")

    # 同步更新 episode 中的 transcript
    episode["transcript"] = result

    # 如果有 segments，也同步更新 segment 中的 text
    # 从纠错后文本解析修正后的句子
    if segments:
        corrected_lines = result.split("\n")
        seg_idx = 0
        for line in corrected_lines:
            line = line.strip()
            if not line:
                continue
            # 匹配时间戳行 [HH:MM:SS] text 或 [MM:SS] text
            import re
            ts_match = re.match(r'^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+)$', line)
            if ts_match and seg_idx < len(segments):
                segments[seg_idx]["text"] = ts_match.group(2)
                seg_idx += 1

        episode["transcript_segments"] = segments

    return result


# ─────────────────────────────────────────
# Prompt 模板
# ─────────────────────────────────────────

SUMMARY_PROMPT = """你是一位专业的播客内容分析师。请基于以下带时间戳的播客转录文本，完成**两个任务**。

## 播客信息
- 节目名称：{podcast_name}
- 单集标题：{title}
- 时长：{duration_min} 分钟

## 转录文本（带时间戳）
{transcript}

---

## 任务一：结构化摘要（重点输出）

请深度分析内容，**重点提取博主/嘉宾讲到的关键信息、核心观点、方法论、实操经验等成体系的内容**，按以下格式输出：

### 一句话总结
（20字以内，概括本集最核心的价值点）

### 核心内容提炼

#### 关键观点
（3-7 条。每条包括：观点内容 + 为什么重要/有什么启发。这是最重要的部分，要深入提取）

#### 方法论/框架
（如果博主分享了具体的方法、框架、流程、模型等，在此详细列出。没有则写"本集未涉及明确的方法论框架"）

#### 重要信息/数据
（关键数字、事实、引用来源等值得记录的信息）

#### 精彩金句
（3-5 句最值得反复回味的原话，附上时间戳。保持原话不改写）

### 延伸阅读

#### 播客中提及的阅读材料
（列出主播/嘉宾在节目中明确提到的书籍、文章、论文、报告、数据来源、网站等。每项格式：`- **书名/篇名** | 作者（如提及）：简要说明在节目中的上下文——为何被提及、核心论点和本集主题的关联`）

#### 主题相关推荐
（基于本期播客的核心主题，推荐 3-5 本/篇相关的延伸阅读材料。每项格式：`- **书名/篇名** | 作者：推荐理由（紧扣播客主题，说明为何值得延伸阅读）`。宁缺毋滥——如果没有合适的高质量推荐，写"暂无特别推荐"）

### 关键词
（5-10 个，逗号分隔）

---

## 任务二：话题分段索引

将整集内容按**话题自然切换点**进行分段（不是固定时间切分），每段包含：
- 时间范围（从转录文本的时间戳中提取）
- 话题标题（简明扼要）
- 内容概要（2-3 句话说明这段聊了什么、有什么价值）

格式要求：
```
### 📍 00:00 - 12:35 | 话题标题
内容概要...
```

请确保：
1. 分段覆盖完整集内容，不要遗漏
2. 话题切换点要自然，根据内容语义判断
3. 时间范围准确
4. 概要要有信息量，不要泛泛而谈

{transcript_note}

---

**注意**：保持客观，忠实于原内容。摘要部分要有深度，不要停留在表面描述。
"""


# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────

def _fmt_ts(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS 或 MM:SS"""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_transcript_with_timestamps(segments: list) -> str:
    """将分段转录格式化为带时间戳的文本"""
    lines = []
    for seg in segments:
        ts = _fmt_ts(seg.get("start", 0))
        lines.append(f"[{ts}] {seg.get('text', '')}")
    return "\n".join(lines)


def _estimate_chars_per_minute() -> int:
    """中文播客语速约 250-300 字/分钟，取保守值"""
    return 280


# ─────────────────────────────────────────
# 摘要生成
# ─────────────────────────────────────────

def get_summary_client(config: dict) -> tuple:
    """创建 LLM 客户端，返回 (client, model)"""
    s_cfg = config.get("summary", {})
    api_key = s_cfg.get("api_key", "")
    base_url = s_cfg.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    model = s_cfg.get("model", "qwen-plus")

    if not api_key or api_key == "sk-xxxx":
        raise ValueError("请在 config/config.json 的 summary.api_key 中填入有效的 API Key")

    return OpenAI(api_key=api_key, base_url=base_url), model


def _call_llm_with_retry(client, model: str, messages: list, *,
                         temperature: float = 0.3, max_tokens: int = 8000,
                         timeout: int = 180, max_retries: int = 3) -> str:
    """
    调用 LLM API，带自动重试（网络错误/DNS/连接断开）。
    重试间隔递增：5s → 15s → 30s
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            error_msg = str(e)
            # 判断是否为可重试错误（网络/连接/DNS）
            retryable = any(kw in error_msg.lower() for kw in (
                "connection", "timeout", "dns", "getaddrinfo", "name or service not known",
                "incompleteread", "broken pipe", "reset by peer", "ssl", "proxy",
            ))
            if retryable and attempt < max_retries:
                delay = [5, 15, 30][attempt - 1]
                print(f"  ⚠️  LLM 调用失败（{error_msg[:60]}），{delay}s 后重试 ({attempt}/{max_retries})...")
                time.sleep(delay)
            else:
                print(f"  ❌ LLM 调用失败（{error_msg[:80]}），已达最大重试次数")
                raise

    raise last_error


def generate_summary(episode: dict, config: dict) -> str:
    """
    调用 LLM 生成结构化摘要 + 话题分段索引。
    qwen-plus 支持 128K+ 上下文（约 40 万字），普通播客不会超限。
    对于超长文本（>3 小时），分批处理。
    """
    segments = episode.get("transcript_segments", [])
    transcript_raw = episode.get("transcript", "")

    # 构建带时间戳的转录文本
    if segments:
        transcript_with_ts = format_transcript_with_timestamps(segments)
    else:
        transcript_with_ts = transcript_raw

    duration_seconds = episode.get("audio_duration", episode.get("duration_seconds", 0))
    duration_min = int(duration_seconds) // 60

    # 判断是否需要分批
    # qwen-plus 上下文 128K tokens ≈ 40 万中文字符，安全线 30 万字符
    MAX_CHARS = 280000
    transcript_note = ""

    if len(transcript_with_ts) > MAX_CHARS:
        # 超长内容：截取摘要用全文，在 prompt 中说明
        transcript_note = "\n**注意：转录文本过长，以下为截取版本。完整全文将在话题分段中单独处理。**"
        transcript_for_summary = transcript_with_ts[:MAX_CHARS] + "\n\n[...转录文本过长，已截断...]"
    else:
        transcript_for_summary = transcript_with_ts

    prompt = SUMMARY_PROMPT.format(
        podcast_name=episode.get("podcast_name", "未知节目"),
        title=episode.get("title", "未知标题"),
        duration_min=duration_min,
        transcript=transcript_for_summary,
        transcript_note=transcript_note,
    )

    client, model = get_summary_client(config)
    print(f"  🤖 正在生成摘要 + 话题分段（模型: {model}，转录文本 {len(transcript_with_ts)} 字）...")

    # qwen-plus 支持 128K 输出 tokens，摘要 + 分段索引不需要那么长
    # 设 8000 足够（约 2 万中文字）
    summary = _call_llm_with_retry(
        client, model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=8000, timeout=180,
    )

    print(f"  ✅ 摘要生成完成（{len(summary)} 字）")
    return summary


# ─────────────────────────────────────────
# 全文话题分段
# ─────────────────────────────────────────

TOPIC_SEGMENT_PROMPT = """你是一位专业的内容编辑。请将以下带时间戳的播客转录文本，按照**话题自然切换点**进行分段，并为每段生成一个简短的标题。

## 转录文本（第 {part} 部分 / 共 {total} 部分）
{transcript}

## 格式要求

按以下 JSON 数组格式输出，不要输出其他内容：

```json
[
  {{
    "start_time": "00:00",
    "end_time": "12:35",
    "title": "话题标题（5-15字）",
    "summary": "2-3 句话概要，说明这段聊了什么核心内容"
  }},
  ...
]
```

注意：
1. 严格根据内容语义判断话题切换点，不要按固定时长切割
2. 每段内容应围绕一个主题
3. 时间范围从转录文本的时间戳中提取，要准确
4. 标题要具体，不要泛泛的"讨论环节""嘉宾分享"这种
5. 概要要有信息量
"""


def generate_topic_segments(segments: list, config: dict) -> list:
    """
    将转录分段按话题切割，返回话题分段列表。
    对于普通长度（<2 小时），一次处理；超长内容分批。
    """
    if not segments:
        return []

    client, model = get_summary_client(config)
    transcript_with_ts = format_transcript_with_timestamps(segments)

    # 按 15 万字符分批（每批约 8-9 小时语音，实际不会超过）
    BATCH_SIZE = 150000
    batches = []

    if len(transcript_with_ts) <= BATCH_SIZE:
        batches.append(transcript_with_ts)
    else:
        # 按行分割，避免截断时间戳行
        lines = transcript_with_ts.split("\n")
        current_batch = []
        current_len = 0

        for line in lines:
            if current_len + len(line) > BATCH_SIZE and current_batch:
                batches.append("\n".join(current_batch))
                current_batch = [line]
                current_len = len(line)
            else:
                current_batch.append(line)
                current_len += len(line)

        if current_batch:
            batches.append("\n".join(current_batch))

    print(f"  📑 全文分 {len(batches)} 批进行话题分段...")

    all_topics = []
    for i, batch in enumerate(batches, 1):
        print(f"    处理第 {i}/{len(batches)} 批...")

        prompt = TOPIC_SEGMENT_PROMPT.format(
            part=i,
            total=len(batches),
            transcript=batch,
        )

        content = _call_llm_with_retry(
            client, model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=16000, timeout=120,
        ).strip()

        # 提取 JSON（可能被 markdown code block 包裹）
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            try:
                topics = json.loads(json_match.group())
                if isinstance(topics, list):
                    all_topics.extend(topics)
            except json.JSONDecodeError:
                print(f"    ⚠️ 第 {i} 批 JSON 解析失败，跳过")
        else:
            print(f"    ⚠️ 第 {i} 批未找到 JSON 数组，跳过")

    print(f"  ✅ 话题分段完成，共 {len(all_topics)} 个话题")
    return all_topics


def build_topic_transcript(segments: list, topic_segments: list) -> str:
    """
    根据话题分段，将转录文本按话题组织成 Markdown 格式。
    每个话题下放对应时间范围内的转录文本。
    """
    if not segments:
        return ""

    # 将 topic_segments 的 start_time / end_time 转换为秒数用于匹配
    def ts_to_seconds(ts: str) -> float:
        """将 'HH:MM:SS' 或 'MM:SS' 转为秒数"""
        parts = ts.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return 0

    # 计算最后一个转录分段的结束时间，用于兜底
    last_seg_end = max((s.get("end", 0) for s in segments), default=0)

    # 为每个话题构建内容块
    sections = []
    is_last = False
    for i, topic in enumerate(topic_segments, 1):
        is_last = (i == len(topic_segments))
        start_sec = ts_to_seconds(topic.get("start_time", "00:00"))
        end_sec = ts_to_seconds(topic.get("end_time", "99:59:59"))
        # 兜底: 最后一个话题扩展到覆盖所有剩余转录分段
        if is_last and last_seg_end > end_sec:
            end_sec = last_seg_end
        title = topic.get("title", f"话题 {i}")
        summary = topic.get("summary", "")

        # 筛选该时间范围内的转录分段
        seg_texts = []
        for seg in segments:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", seg_start)
            # 转录分段与话题时间段有交集即包含
            if seg_start < end_sec and seg_end > start_sec:
                ts = _fmt_ts(seg_start)
                seg_texts.append(f"[{ts}] {seg.get('text', '')}")

        section = f"### 📍 {topic.get('start_time', '??')} - {_fmt_ts(end_sec) if is_last and end_sec > ts_to_seconds(topic.get('end_time', '00:00')) else topic.get('end_time', '??')} | {title}\n\n"
        if summary:
            section += f"> {summary}\n>\n"
        section += "\n".join(seg_texts) if seg_texts else "_（该段落转录内容为空）_"
        sections.append(section)

    return "\n\n---\n\n".join(sections)


# ─────────────────────────────────────────
# Markdown 文档构建
# ─────────────────────────────────────────

def build_markdown_doc(episode: dict, summary: str, topic_segments: list = None,
                       no_fulltext: bool = False) -> str:
    """
    构建完整的 Markdown 文档。

    文档结构：
    1. 头部元数据（标题、节目、时长等）
    2. AI 摘要（核心观点 + 方法论 + 金句）
    3. 话题分段全文（按话题组织的转录文本）—— no_fulltext 时跳过
    4. 纯文本全文（可折叠，作为备份）—— no_fulltext 时跳过
    """
    title = episode.get("title", "未知标题")
    podcast_name = episode.get("podcast_name", "未知节目")
    source = episode.get("source", "")
    published_at = (episode.get("published_at", "") or "")[:10]
    episode_url = episode.get("episode_url", "")
    cover_url = episode.get("cover_url", "")
    description = episode.get("description", "")
    duration_seconds = episode.get("audio_duration", episode.get("duration_seconds", 0))
    if duration_seconds > 3600:
        duration_str = f"{int(duration_seconds)//3600}h{(int(duration_seconds)%3600)//60}m"
    else:
        duration_str = f"{int(duration_seconds)//60}min"

    segments = episode.get("transcript_segments", [])
    transcript = episode.get("transcript", "")
    error = episode.get("error", "")

    # ---- 话题分段全文 ----
    topic_transcript = ""
    if topic_segments and segments:
        topic_transcript = build_topic_transcript(segments, topic_segments)

    # ---- 纯文本全文（可折叠备份）----
    if segments:
        full_transcript = format_transcript_with_timestamps(segments)
    elif transcript:
        full_transcript = transcript
    else:
        full_transcript = "_（转录失败或无内容）_"

    # ---- 组装文档 ----
    doc_parts = []

    # 头部
    header = f"""# {title}

> **节目**：{podcast_name}　｜　**来源**：{source}　｜　**发布日期**：{published_at}　｜　**时长**：{duration_str}"""
    if cover_url:
        header += f"\n\n![封面]({cover_url})"
    if episode_url:
        header += f"\n\n🔗 [收听原播客]({episode_url})"
    doc_parts.append(header)

    # 摘要
    doc_parts.append(f"---\n\n## 📋 AI 摘要\n\n{summary}")

    # 话题分段全文（no_fulltext 模式下跳过逐字全文，仅保留摘要中的话题索引）
    if not no_fulltext:
        if topic_transcript:
            doc_parts.append(f"---\n\n## 📑 全文（按话题分段）\n\n{topic_transcript}")
        elif transcript:
            # 没有话题分段时，降级为纯文本
            doc_parts.append(f"---\n\n## 📝 全文转录\n\n<details>\n<summary>点击展开完整转录文本（{len(transcript)} 字）</summary>\n\n{full_transcript}\n\n</details>")

    # 页脚
    doc_parts.append(f"\n---\n\n_本文档由播客转录工具自动生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')} · 仅供个人学习使用_")

    doc = "\n\n".join(doc_parts)

    if error:
        doc = f"> ⚠️ **处理时遇到错误**：{error}\n\n" + doc

    return doc


def _extract_publish_date(episode: dict) -> str:
    """从 episode 提取发布日期，返回 YYYYMMDD 字符串；无法解析则返回空串。"""
    raw = (
        episode.get("published_at", "")
        or episode.get("pubDate", "")
        or episode.get("publishedDateStr", "")
    )
    if not raw:
        return ""
    s = str(raw).strip()
    # 兼容 ISO 含 T 的情况：2024-03-15T08:30:00.000Z
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    # 纯数字 20240315
    m = re.match(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    return ""


def _build_dir_name(episode: dict) -> str:
    """构建目录名：发布日期_栏目名_单集标题（单层目录）"""
    title = episode.get("title", "untitled")
    podcast_name = episode.get("podcast_name", "未知节目")
    safe_podcast = re.sub(r'[\\/*?:"<>|]', "_", podcast_name)[:40]
    safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)[:60]

    date_part = _extract_publish_date(episode)
    if date_part:
        return f"{date_part}_{safe_podcast}_{safe_title}"
    # 无发布日期时回退到旧格式，保证可用性
    return f"{safe_podcast}_{safe_title}"


def recycle_existing(episode: dict, output_dir: Path) -> bool:
    """
    判重 + 归档：如果目标目录已存在，将其整体移动到 recycled/ 目录下。
    归档子目录命名格式：年月日时分秒_栏目名_单集名
    返回 True 表示执行了归档，False 表示无需归档。
    """
    dir_name = _build_dir_name(episode)
    episode_dir = output_dir / dir_name

    if not episode_dir.exists():
        return False

    # 查找目录内最早文件的修改时间作为生成时间
    earliest_time = None
    for f in episode_dir.iterdir():
        if f.is_file():
            mt = datetime.fromtimestamp(f.stat().st_mtime)
            if earliest_time is None or mt < earliest_time:
                earliest_time = mt

    if earliest_time is None:
        earliest_time = datetime.now()

    # 构建归档目录名：年月日时分秒_栏目名_单集名
    ts_str = earliest_time.strftime("%Y%m%d%H%M%S")
    archive_name = f"{ts_str}_{dir_name}"

    recycled_dir = output_dir.parent / "recycled"
    recycled_dir.mkdir(parents=True, exist_ok=True)

    archive_path = recycled_dir / archive_name
    # 如果归档目录名冲突，加序号
    counter = 1
    while archive_path.exists():
        archive_path = recycled_dir / f"{archive_name}_{counter}"
        counter += 1

    try:
        shutil.move(str(episode_dir), str(archive_path))
        print(f"  ♻️  旧版本已归档: recycled/{archive_path.name}")
        return True
    except Exception as e:
        print(f"  ⚠️  归档失败: {e}，旧文件可能被覆盖")
        return False


def save_document(episode: dict, content: str, output_dir: Path = None) -> Path:
    """保存 Markdown 文档，返回文件路径。
    目录：栏目名_单集标题（单层目录）
    文件：栏目名_单集标题.md（与目录同名）
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR

    dir_name = _build_dir_name(episode)
    episode_dir = output_dir / dir_name
    episode_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{dir_name}.md"
    file_path = episode_dir / filename

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"  💾 文档已保存: {file_path}")
    return file_path


# ─────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────

def process_episode(episode: dict, config: dict, no_fulltext: bool = False) -> Path:
    """
    对单集生成摘要 + 话题分段 + 完整文档。
    返回保存路径。
    """
    output_dir = Path(__file__).parent / config.get("output", {}).get("dir", "output")

    # Step 1: 生成摘要 + 话题分段索引
    summary = generate_summary(episode, config)

    # Step 2: 全文话题分段（用 AI 对全部转录文本做话题切割）
    segments = episode.get("transcript_segments", [])
    topic_segments = []
    if segments:
        topic_segments = generate_topic_segments(segments, config)

    # Step 3: 构建完整文档并保存
    doc_content = build_markdown_doc(episode, summary, topic_segments,
                                     no_fulltext=no_fulltext)

    # 判重：如果目录已存在，将旧版本归档到 recycled/
    recycle_existing(episode, output_dir)

    file_path = save_document(episode, doc_content, output_dir)

    return file_path
