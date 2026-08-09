"""
podfetch.py — 通用播客单集信息抓取模块

从任意播客单集网页提取元数据和音频URL：
  1. JSON-LD 结构化数据（schema.org PodcastEpisode / AudioObject）
  2. OpenGraph / Twitter Card meta 标签
  3. HTML5 <audio> 标签和常见 RSS 链接

输入：任意播客单集网页 URL
输出：标准化 episode 字典
"""
import re
import json
import requests
from html import unescape
from typing import Optional
from urllib.parse import urljoin

HEADERS = {
    "User-Agent": "PodcastTranscriber/1.0 (personal-use podcast metadata fetcher)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _parse_jsonld(html: str, base_url: str) -> Optional[dict]:
    """Extract episode metadata from JSON-LD structured data."""
    pattern = r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>'
    for match in re.finditer(pattern, html, re.DOTALL):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

        # Normalize: JSON-LD can be a single object or array
        items = data if isinstance(data, list) else [data]

        for item in items:
            if not isinstance(item, dict):
                continue
            gtype = item.get("@type", "")
            graph = item.get("@graph", [])
            if graph:
                items.extend(graph if isinstance(graph, list) else [graph])
                continue

            # PodcastEpisode
            if gtype in ("PodcastEpisode", "MusicRecording", "AudioObject"):
                audio = _extract_audio_from_jsonld(item)
                return {
                    "id": item.get("episodeNumber", "") or item.get("@id", ""),
                    "title": item.get("name", ""),
                    "podcast_name": _get_podcast_name(item),
                    "description": item.get("description", ""),
                    "duration_seconds": _parse_duration(item.get("duration", "")),
                    "audio_url": audio,
                    "cover_url": _get_image(item),
                    "published_at": item.get("datePublished", ""),
                    "episode_url": item.get("url", "") or base_url,
                    "source": item.get("partOfSeries", {}).get("name", "") or gtype,
                }

        # Also check for AudioObject not explicitly typed as episode
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "AudioObject":
                audio_url = item.get("contentUrl", "") or item.get("url", "")
                if audio_url:
                    return {
                        "id": "",
                        "title": item.get("name", ""),
                        "podcast_name": "",
                        "description": item.get("description", ""),
                        "duration_seconds": _parse_duration(item.get("duration", "")),
                        "audio_url": audio_url,
                        "cover_url": "",
                        "published_at": "",
                        "episode_url": base_url,
                        "source": "JSON-LD",
                    }
    return None


def _extract_audio_from_jsonld(item: dict) -> str:
    """Extract audio URL from JSON-LD item."""
    # Direct associatedMedia
    media = item.get("associatedMedia", {}) or {}
    if isinstance(media, list) and media:
        media = media[0]
    audio = media.get("contentUrl", "") or media.get("url", "")

    # encoding
    if not audio:
        encodings = item.get("encoding", []) or []
        if isinstance(encodings, dict):
            encodings = [encodings]
        for enc in encodings:
            audio = enc.get("contentUrl", "") or enc.get("url", "")
            if audio:
                break

    # Direct contentUrl
    if not audio:
        audio = item.get("contentUrl", "")

    return audio


def _get_podcast_name(item: dict) -> str:
    """Extract podcast/series name from JSON-LD."""
    series = item.get("partOfSeries", {}) or {}
    if isinstance(series, dict):
        return series.get("name", "")
    return ""


def _get_image(item: dict) -> str:
    """Extract cover image from JSON-LD."""
    # Direct image
    img = item.get("image", "")
    if isinstance(img, dict):
        img = img.get("url", "")
    if isinstance(img, list) and img:
        img = img[0].get("url", "") if isinstance(img[0], dict) else img[0]
    if img:
        return img

    # thumbnailUrl
    thumbnails = item.get("thumbnailUrl", []) or []
    if isinstance(thumbnails, list) and thumbnails:
        t = thumbnails[0]
        return t.get("url", "") if isinstance(t, dict) else t
    return ""


def _parse_duration(dur) -> int:
    """Parse ISO 8601 duration (PT1H23M45S) or raw seconds to seconds."""
    if not dur:
        return 0
    if isinstance(dur, (int, float)):
        return int(dur)
    dur = str(dur).strip()
    # ISO 8601: PT1H23M45S
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', dur)
    if m:
        h, mm, s = m.groups()
        return int(h or 0) * 3600 + int(mm or 0) * 60 + int(s or 0)
    # Raw seconds string
    try:
        return int(float(dur))
    except ValueError:
        return 0


def _parse_meta_tags(html: str, base_url: str) -> Optional[dict]:
    """Extract metadata from OpenGraph / Twitter Card meta tags."""
    def meta(prop):
        # property / name attribute
        for attr in ("property", "name"):
            p = re.search(
                fr'<meta\s+{attr}="{re.escape(prop)}"\s+content="([^"]*)"',
                html, re.IGNORECASE
            )
            if p:
                return unescape(p.group(1))
        return ""

    audio = meta("og:audio") or meta("twitter:player:stream")
    if not audio:
        return None

    title = meta("og:title") or meta("twitter:title")
    desc = meta("og:description") or meta("twitter:description")
    image = meta("og:image") or meta("twitter:image")

    # Try to split "Episode Title - Podcast Name" pattern
    podcast_name = ""
    ep_title = title
    for sep in (" | ", " — ", " - "):
        if sep in title:
            parts = title.rsplit(sep, 1)
            ep_title = parts[0].strip()
            podcast_name = parts[1].strip()
            break

    return {
        "id": "",
        "title": ep_title or "未知标题",
        "podcast_name": podcast_name,
        "description": desc,
        "duration_seconds": 0,
        "audio_url": audio,
        "cover_url": image,
        "published_at": "",
        "episode_url": base_url,
        "source": "meta-tags",
    }


def _parse_html5_audio(html: str, base_url: str) -> Optional[dict]:
    """Fallback: extract <audio> tag sources."""
    m = re.search(r'<audio[^>]*src="([^"]*)"', html, re.IGNORECASE)
    src = m.group(1) if m else ""
    if not src:
        m = re.search(r'<source\s+src="([^"]*)"', html, re.IGNORECASE)
        src = m.group(1) if m else ""
    if not src:
        return None

    # Verify it's actually audio
    if not re.search(r'\.(mp3|m4a|wav|ogg|aac|flac|opus)(\?|$)', src, re.IGNORECASE):
        return None

    # Try to get title from <h1> or <title>
    title_m = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
    title = unescape(title_m.group(1).strip()) if title_m else ""

    return {
        "id": "",
        "title": title or "未知标题",
        "podcast_name": "",
        "description": "",
        "duration_seconds": 0,
        "audio_url": urljoin(base_url, src),
        "cover_url": "",
        "published_at": "",
        "episode_url": base_url,
        "source": "html5-audio",
    }


def _parse_rss_enclosure(html: str, base_url: str) -> Optional[dict]:
    """Fallback: look for RSS enclosure links in page (type='audio/mpeg')."""
    enclosure_patterns = [
        r'<enclosure\s+url="([^"]*)"[^>]*type="audio[^"]*"',
        r'<link[^>]*type="audio[^"]*"[^>]*href="([^"]*)"',
    ]
    for pat in enclosure_patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            src = urljoin(base_url, m.group(1))
            return _parse_html5_audio(html, base_url) or {
                "id": "", "title": "", "podcast_name": "",
                "description": "", "duration_seconds": 0,
                "audio_url": src, "cover_url": "",
                "published_at": "", "episode_url": base_url,
                "source": "rss-enclosure",
            }
    return None


# ── YouTube 支持（依赖 yt-dlp）──

YOUTUBE_RE = re.compile(
    r'(youtube\.com/(watch\?|shorts/|live/|embed/)|youtu\.be/)', re.IGNORECASE
)


def _is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_RE.search(url))


def _episode_from_youtube(url: str, proxy: str = "") -> dict:
    """用 yt-dlp 解析 YouTube 视频，提取最佳音频流的直链"""
    import shutil
    import subprocess

    if not shutil.which("yt-dlp"):
        raise RuntimeError(
            "解析 YouTube 链接需要 yt-dlp，请先安装：pip install yt-dlp"
        )

    print(f"  🎬 检测到 YouTube 链接，使用 yt-dlp 解析...")
    cmd = [
        "yt-dlp", "--dump-single-json", "--no-playlist",
        "--no-warnings", "-f", "bestaudio/best",
    ]
    if proxy:
        cmd += ["--proxy", proxy]
        print(f"  🌐 使用代理: {proxy}")
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        last = stderr[-1] if stderr else "unknown"
        hint = ""
        low = " ".join(stderr).lower()
        if any(kw in low for kw in ("timed out", "timeout", "10054", "reset", "unreachable", "refused")):
            hint = "（当前网络可能无法访问 YouTube，可设置 HTTPS_PROXY 环境变量或开启代理后重试）"
        raise RuntimeError(f"yt-dlp 解析 YouTube 失败: {last[:200]} {hint}")

    info = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    audio_url = info.get("url", "")
    if not audio_url:
        raise RuntimeError("yt-dlp 未能获取 YouTube 音频直链")

    description = (info.get("description") or "")[:300]

    # 标题去掉 yt-dlp 追加的 "| 频道名" / "- 频道名" 尾巴
    title = info.get("title", "")
    channel = info.get("channel") or info.get("uploader") or ""
    if channel:
        for sep in (" | ", " - ", " _ "):
            suffix = sep + channel
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
                break

    # 发布日期 YYYYMMDD -> YYYY-MM-DD
    upload_date = info.get("upload_date", "") or ""
    if len(upload_date) == 8 and upload_date.isdigit():
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    return {
        "id": info.get("id", ""),
        "title": title,
        "podcast_name": channel,
        "description": description,
        "duration_seconds": int(info.get("duration") or 0),
        "audio_url": audio_url,
        "cover_url": info.get("thumbnail", ""),
        "published_at": upload_date,
        "episode_url": info.get("webpage_url") or url,
        "source": "youtube",
    }


# ── 主入口 ──

AUDIO_EXTENSIONS = re.compile(r'\.(mp3|m4a|wav|ogg|aac|flac|opus)(\?.*)?$', re.IGNORECASE)


def _is_direct_audio_url(url: str) -> bool:
    """判断 URL 是否为直接音频文件链接"""
    return bool(AUDIO_EXTENSIONS.search(url.split('#')[0]))


def _episode_from_audio_url(url: str) -> dict:
    """从直接音频 URL 构造 episode 字典，标题从文件名推导"""
    from urllib.parse import urlparse, unquote
    path = urlparse(url).path
    filename = unquote(path.rsplit('/', 1)[-1]) if '/' in path else unquote(path)
    # 去掉扩展名作为标题
    title = re.sub(r'\.(mp3|m4a|wav|ogg|aac|flac|opus)$', '', filename, flags=re.IGNORECASE)
    title = title.replace('_', ' ').replace('-', ' ').strip() or "未命名音频"

    return {
        "id": "",
        "title": title,
        "podcast_name": "",
        "description": "",
        "duration_seconds": 0,
        "audio_url": url,
        "cover_url": "",
        "published_at": "",
        "episode_url": url,
        "source": "direct-audio",
    }


def fetch_episode(url: str, proxy: str = "") -> dict:
    """
    从播客单集页面提取元数据。
    若为直接音频 URL（.mp3/.m4a 等），直接构造 episode 跳过网页解析。
    网页解析优先级：JSON-LD > OpenGraph meta > HTML5 audio > RSS enclosure
    proxy: 可选 HTTP 代理（访问 YouTube 等受限站点时需要）
    """
    # 直接音频 URL：跳过网页解析
    if _is_direct_audio_url(url):
        print(f"  🎵 检测到直接音频链接，跳过网页解析")
        return _episode_from_audio_url(url)

    # YouTube 链接：用 yt-dlp 提取音频直链
    if _is_youtube_url(url):
        return _episode_from_youtube(url, proxy=proxy)

    print(f"  🔗 抓取页面: {url[:80]}...")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20, proxies=proxies)
    except requests.exceptions.ProxyError:
        if proxies:
            print(f"  ⚠️  代理不可用，回退为直连...")
            resp = requests.get(url, headers=HEADERS, timeout=20)
        else:
            raise
    resp.raise_for_status()
    html = resp.text

    # Try each parser in priority order
    for name, parser in [
        ("JSON-LD", _parse_jsonld),
        ("OpenGraph", _parse_meta_tags),
        ("HTML5 audio", _parse_html5_audio),
        ("RSS enclosure", _parse_rss_enclosure),
    ]:
        print(f"  📡 尝试 {name} 解析...")
        episode = parser(html, url)
        if episode and episode.get("audio_url"):
            print(f"  ✅ {name} 解析成功")
            return episode
        elif episode:
            print(f"  ⚠️  {name} 找到元数据但无音频 URL，继续尝试...")

    # Last resort: just return what we have from meta tags
    episode = _parse_meta_tags(html, url)
    if episode and episode.get("audio_url"):
        return episode

    raise RuntimeError(
        f"无法从页面提取播客信息和音频 URL。\n"
        f"  URL: {url}\n"
        f"  请确认该页面包含播客音频（og:audio 标签、JSON-LD 或 <audio> 元素）。\n"
        f"  也支持直接传入音频文件 URL（需同时提供 --title 参数）。"
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python podfetch.py <播客单集URL>")
        sys.exit(1)

    ep = fetch_episode(sys.argv[1])
    print(f"\n{'='*60}")
    print(f"  标题：{ep['title']}")
    print(f"  节目：{ep['podcast_name']}")
    print(f"  时长：{ep['duration_seconds']//60} 分钟" if ep['duration_seconds'] else "")
    print(f"  音频：{'✅ 已获取' if ep['audio_url'] else '❌ 无'}")
    print(f"  来源：{ep['source']}")
    print(f"{'='*60}")
