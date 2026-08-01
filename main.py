"""
main.py — 播客转录工具

用法：
  python main.py <播客单集URL>           # 从网页抓取元数据并转录
  python main.py <音频URL> --title "xxx"  # 直接传入音频 URL
  python main.py --dry <URL>              # 仅抓取信息，不转录

流程：抓取元数据 → 下载音频 → 语音转录 → Markdown + PDF 归档
AI 摘要由调用方智能体自行完成。
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from podfetch import fetch_episode
from podtranscribe import download_and_transcribe, load_transcript_cache
from podsummarize import process_episode, _build_dir_name
from md2pdf import convert as md_to_pdf

LOG_DIR = Path(__file__).parent / "state"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "run_log.jsonl"


def log_run(result: dict):
    """追加运行日志"""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def _check_cache(episode: dict, config: dict) -> bool:
    """检查是否有本地转录缓存，有则询问是否复用。返回 True 表示跳过。"""
    cached = load_transcript_cache(episode)
    has_cache = cached is not None

    output_dir = Path(config.get("output", {}).get("dir", "output"))
    if not output_dir.is_absolute():
        output_dir = Path(__file__).parent / output_dir
    dir_name = _build_dir_name(episode)
    episode_dir = output_dir / dir_name
    md_path = episode_dir / f"{dir_name}.md"
    has_doc = episode_dir.exists() and md_path.exists()

    if not has_cache and not has_doc:
        return False

    print("\n  📋 发现本地已有处理记录：")
    if has_cache:
        chars = len(cached.get("transcript", ""))
        print(f"     🔹 转录缓存：{chars:,} 字")
    if has_doc:
        print(f"     🔹 已输出文档：{episode_dir.name[:60]}")

    print("\n  请选择：[r] 重新转录  [s] 跳过  [q] 退出")
    while True:
        choice = input("  输入 [r/s/q]，默认 s：").strip().lower()
        if choice in ("", "s"):
            print("\n  ▶ 复用缓存...")
            if cached:
                episode["transcript"] = cached.get("transcript", "")
                episode["transcript_segments"] = cached.get("transcript_segments", [])
                episode["audio_duration"] = cached.get("audio_duration", 0)
                episode["audio_local_path"] = cached.get("audio_local_path", "")
                episode["_from_cache"] = True
            return True
        elif choice == "r":
            return False
        elif choice == "q":
            episode["_skip_quit"] = True
            return True


def process_url(url: str, config: dict, title: str = None, dry_run: bool = False) -> dict:
    """处理单个播客链接。"""
    print(f"\n{'─'*60}")
    print(f"  🎙️  播客转录")
    print(f"  🔗 {url[:80]}")
    print(f"{'─'*60}")

    # Step 1: 获取元数据
    print("\n📡 抓取单集信息...")
    try:
        episode = fetch_episode(url)
    except Exception as e:
        print(f"  ❌ 获取信息失败: {e}")
        return {"url": url, "status": "fetch_failed", "error": str(e)}

    # 允许用户覆盖标题
    if title:
        episode["title"] = title

    ep_title = episode.get("title", "未知标题")
    podcast = episode.get("podcast_name", "未知播客")
    duration_min = episode.get("duration_seconds", 0) // 60
    print(f"  📋 {podcast} — {ep_title} ({duration_min} 分钟)")

    if not episode.get("audio_url"):
        print(f"  ⚠️  无法获取音频 URL。如果是直接音频链接，请使用 --title 参数。")
        return {"url": url, "title": ep_title, "status": "no_audio_url"}

    if dry_run:
        print(f"  [DRY RUN] 仅抓取信息模式")
        return {"url": url, "title": ep_title, "podcast": podcast,
                "duration_min": duration_min, "status": "dry_run_ok"}

    # Step 2: 检查缓存
    skip = _check_cache(episode, config)
    if skip:
        if episode.get("_skip_quit"):
            return {"url": url, "title": ep_title, "status": "skipped"}

    # Step 3: 下载 + 转录
    if not episode.get("_from_cache"):
        print("\n⬇️  下载音频并转录...")
        try:
            episode = download_and_transcribe(episode, config)
            if episode.get("error"):
                return {"url": url, "title": ep_title, "status": "transcribe_failed",
                        "error": episode["error"]}
        except Exception as e:
            return {"url": url, "title": ep_title, "status": "transcribe_failed",
                    "error": str(e)}
    else:
        print("\n  📋 使用缓存转录数据")

    # Step 4: 构建文档并归档
    print("\n📄 构建文档...")
    saved_path = process_episode(episode, config)

    print(f"\n  🎉 转录完成！")
    print(f"  📄 Markdown: {saved_path}")

    # Step 5: PDF
    print("\n📝 转换为 PDF...")
    try:
        pdf_path = md_to_pdf(str(saved_path))
        print(f"  📑 PDF: {pdf_path}")
    except Exception as e:
        print(f"  ⚠️  PDF 转换失败: {e}")
        pdf_path = None

    result = {
        "url": url, "title": ep_title, "podcast": podcast,
        "duration": f"{duration_min} 分钟",
        "md_path": str(saved_path),
        "pdf_path": str(pdf_path) if pdf_path else None,
        "status": "success",
    }
    log_run({"time": datetime.now().isoformat(), **result})
    return result


def main():
    parser = argparse.ArgumentParser(
        description="播客转录工具 — 从播客链接到转录文档的全自动流水线",
        epilog="示例：python main.py https://example.com/podcast/episode-42"
    )
    parser.add_argument("url", nargs="?", help="播客单集 URL 或音频 URL")
    parser.add_argument("--title", "-t", help="手动指定标题（用于纯音频 URL）")
    parser.add_argument("--dry", action="store_true", help="仅抓取信息，不转录")
    args = parser.parse_args()

    if not args.url:
        parser.print_help()
        return

    # 加载配置（dry-run 模式下可缺省）
    from podauth import load_config
    try:
        cfg = load_config()
        print("📖 配置加载成功")
    except (FileNotFoundError, ValueError) as e:
        if args.dry:
            cfg = {}
            print("📖 [DRY RUN] 跳过配置加载（无需 API Key）")
        else:
            print(f"❌ {e}")
            return

    result = process_url(args.url, cfg, title=args.title, dry_run=args.dry)

    # 汇总
    status = result.get("status", "unknown")
    if status in ("success", "dry_run_ok", "skipped"):
        print(f"\n✅ 完成 — {result.get('pdf_path') or result.get('md_path') or result.get('title', '')}")
    else:
        print(f"\n❌ {status}: {result.get('error', '')}")


if __name__ == "__main__":
    main()
