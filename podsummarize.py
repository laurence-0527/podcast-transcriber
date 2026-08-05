"""
podsummarize.py — 转录文档构建 + 输出管理

职责：将转录结果组装为结构化 Markdown 文档并保存。
AI 摘要由调用方智能体自行完成，本模块不调用任何 LLM API。

输出文档结构：元数据 → 带时间戳的转录全文
"""

import re
import shutil
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


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


# ─────────────────────────────────────────
# Markdown 文档构建
# ─────────────────────────────────────────

def build_markdown_doc(episode: dict) -> str:
    """
    构建 Markdown 文档。

    文档结构：
    1. 头部元数据（标题、节目、时长等）
    2. 带时间戳的转录全文
    3. 页脚
    """
    title = episode.get("title") or "未知标题"
    podcast_name = episode.get("podcast_name") or "未知节目"
    source = episode.get("source", "")
    published_at = (episode.get("published_at", "") or "")[:10]
    episode_url = episode.get("episode_url", "")
    cover_url = episode.get("cover_url", "")
    duration_seconds = episode.get("audio_duration", episode.get("duration_seconds", 0))
    if duration_seconds > 3600:
        duration_str = f"{int(duration_seconds)//3600}h{(int(duration_seconds)%3600)//60}m"
    else:
        duration_str = f"{int(duration_seconds)//60}min"

    segments = episode.get("transcript_segments", [])
    transcript = episode.get("transcript", "")

    # 构建转录全文
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

> **节目**：{podcast_name} ｜ **来源**：{source} ｜ **发布日期**：{published_at} ｜ **时长**：{duration_str}"""
    if cover_url:
        header += f"\n\n![封面]({cover_url})"
    if episode_url:
        header += f"\n\n🔗 [收听原播客]({episode_url})"
    doc_parts.append(header)

    # 转录全文
    char_count = len(full_transcript)
    doc_parts.append(f"---\n\n## 📝 转录全文（{char_count:,} 字）\n\n{full_transcript}")

    # 页脚
    doc_parts.append(f"\n---\n\n_本文档由播客转录工具自动生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')} · 仅供个人学习使用_")

    doc = "\n\n".join(doc_parts)

    return doc


# ─────────────────────────────────────────
# 输出管理
# ─────────────────────────────────────────

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
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
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
    return f"{safe_podcast}_{safe_title}"


def recycle_existing(episode: dict, output_dir: Path) -> bool:
    """
    判重 + 归档：如果目标目录已存在，将其整体移动到 recycled/ 目录下。
    返回 True 表示执行了归档，False 表示无需归档。
    """
    dir_name = _build_dir_name(episode)
    episode_dir = output_dir / dir_name

    if not episode_dir.exists():
        return False

    earliest_time = None
    for f in episode_dir.iterdir():
        if f.is_file():
            mt = datetime.fromtimestamp(f.stat().st_mtime)
            if earliest_time is None or mt < earliest_time:
                earliest_time = mt

    if earliest_time is None:
        earliest_time = datetime.now()

    ts_str = earliest_time.strftime("%Y%m%d%H%M%S")
    archive_name = f"{ts_str}_{dir_name}"

    recycled_dir = output_dir.parent / "recycled"
    recycled_dir.mkdir(parents=True, exist_ok=True)

    archive_path = recycled_dir / archive_name
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
    """保存 Markdown 文档，返回文件路径。"""
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

def process_episode(episode: dict, config: dict) -> Path:
    """
    将转录结果构建为 Markdown 文档并保存。
    AI 摘要由调用方智能体自行完成。
    返回保存路径。
    """
    output_dir = Path(__file__).parent / config.get("output", {}).get("dir", "output")

    # 构建文档（元数据 + 转录全文）
    doc_content = build_markdown_doc(episode)

    # 判重：如果目录已存在，将旧版本归档到 recycled/
    recycle_existing(episode, output_dir)

    file_path = save_document(episode, doc_content, output_dir)

    return file_path
