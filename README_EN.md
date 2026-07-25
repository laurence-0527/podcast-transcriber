# Podcast Transcriber

**[中文](README.md) | [日本語](README_JA.md)**

Turn podcasts into a searchable, skimmable knowledge base.

## Why This Tool Exists

I subscribe to dozens of high-quality podcasts — AI research, business insights, deep tech interviews, investment frameworks. Each episode runs 1-3 hours, with a dozen new ones dropping every week. Listening to all of them is impossible, but missing them feels wasteful.

So I developed a workflow that works for me: **transcribe first, skim the summary, then selectively listen.**

1. **Batch transcribe**: Feed episode URLs to the tool — it handles downloading, speech-to-text, and AI summarization automatically
2. **Skim & filter**: Spend 2 minutes reading a structured summary (key insights + quotes + topic index) to decide whether an episode deserves a full hour of your time
3. **Deep listen**: For episodes that pass the filter, use the topic index to jump straight to the most relevant segments

This is essentially a **"breadth-first, then depth" learning method** — AI compresses 1 hour of audio into 2 minutes of reading, so your attention goes only to truly high-value information. Podcasts stop being ephemeral streams you forget after listening; they become searchable, reviewable, quotable personal knowledge assets.

This tool is the automation layer for that method.

## Features

- **One command, end to end**: URL → metadata extraction → audio download → ASR → AI summary + topic segmentation → Markdown + PDF
- **Platform-agnostic**: JSON-LD / OpenGraph / HTML5 audio / direct audio URLs — no lock-in to any specific platform
- **Deep AI summaries**: Not just a recap — extracts key arguments, methodologies, memorable quotes (with timestamps), and recommended further reading
- **Topic-segmented index**: Automatically splits content by semantic topic boundaries, each segment timestamped and summarized for easy navigation
- **`--no-fulltext` mode**: Output only the summary and topic index, skip the verbatim transcript — ideal for the "skim & filter" workflow

## Quick Start

### 1. Install dependencies

```bash
pip install requests reportlab markdown openai
```

### 2. Configure API Key

```bash
cp config/config.example.json config/config.json
# Edit config.json with your DashScope API key, or set an environment variable
export DASHSCOPE_API_KEY=sk-your-key-here
```

Get your key: [Alibaba Cloud DashScope Console](https://dashscope.console.aliyun.com/apiKey)

### 3. Transcribe

```bash
# From a podcast webpage
python main.py https://example.com/podcast/episode-42

# Direct audio URL (provide title manually)
python main.py https://cdn.example.com/audio/ep42.mp3 --title "The Future of AI"

# Metadata only (preview, no API key needed)
python main.py --dry https://example.com/podcast/episode-42

# Summary + topic index only, no verbatim transcript (skim mode)
python main.py --no-fulltext https://example.com/podcast/episode-42
```

Output is saved to `output/` — one folder per episode, containing Markdown and PDF.

## Output Structure

For a 50-minute episode:

```
# Episode Title
> Show | Source | Published | Duration

## 📋 AI Summary
### One-line takeaway
### Core insights (key arguments / frameworks / data / quotes)
### Further reading (mentioned in episode + thematic recommendations)
### Keywords

## 📑 Full Transcript (by topic)    ← skipped with --no-fulltext
### 📍 00:00 - 12:35 | Topic Title
> Segment summary
[00:00] Verbatim transcript...
```

## How It Works

```text
URL input
  │
  ├─ podfetch.py      → Web scraping: JSON-LD / OpenGraph / HTML5 audio
  ├─ podtranscribe.py → Audio download → DashScope async ASR → timestamped segments
  ├─ podsummarize.py  → LLM summary + topic segmentation + further reading
  └─ md2pdf.py        → Markdown → PDF (CJK-aware)
  │
  └─ output/<date_show_title>/  ← MD + PDF
```

## Supported Sources

Not tied to any platform. Works with any page containing:

- JSON-LD (`PodcastEpisode` / `AudioObject`)
- OpenGraph (`og:audio`)
- HTML5 `<audio>` tags
- Direct audio URLs (.mp3 / .m4a / .wav / .ogg / .aac / .flac / .opus)

Verified: Xiaoyuzhou (小宇宙), Apple Podcasts, Spotify (web), TWiT, Libsyn-hosted podcasts, and more.

## FAQ

**Q: How long does transcription take?**
A: Download 1-3 min + async ASR 5-15 min (depends on audio length and service load). A 1-hour episode takes ~10-20 min total.

**Q: What languages are supported?**
A: DashScope ASR supports Chinese, English, Japanese, and more. Summaries default to Chinese output.

**Q: Can I batch process?**
A: Yes. A simple shell loop works:
```bash
for url in $(cat urls.txt); do python main.py --no-fulltext "$url"; done
```

## Data Flow

1. **Metadata extraction**: Parse public HTML for title, audio URL (local)
2. **Audio download**: From podcast CDN to local `audio_cache/`
3. **Speech-to-text**: Audio uploaded to **Alibaba Cloud DashScope** for async ASR
4. **AI summary**: Transcript sent to **DashScope LLM API** (default: qwen-plus)
5. **Local output**: Markdown + PDF saved to `output/`

Steps 3-4 transmit data to Alibaba Cloud servers. Ensure you have the right to process the content.

## Disclaimer

This tool is for personal learning and research only:

- Podcast audio and transcripts remain the property of their creators; no commercial use or redistribution
- Respect platform terms of service; do not use this tool where programmatic downloading is prohibited
- Full transcripts are for personal review and study notes; cite sources when quoting
- Users bear full responsibility for any misuse

## License

MIT
