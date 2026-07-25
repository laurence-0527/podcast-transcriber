"""
podtranscribe.py — 音频下载 + 转录模块
使用阿里云百炼异步文件转录接口（统一走异步，确保返回时间戳分段）

流程：下载音频 → [优先URL直连/回退curl上传] → 百炼异步转录 → 轮询结果 → 解析带时间戳的转录文本
"""

import time
import hashlib
import json
import subprocess
import requests
from typing import Optional
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pathlib import Path

AUDIO_CACHE = Path(__file__).parent / "audio_cache"
AUDIO_CACHE.mkdir(exist_ok=True)

# 转录结果缓存目录（独立于输出目录）
TRANSCRIPT_CACHE = Path(__file__).parent / "transcript_cache"
TRANSCRIPT_CACHE.mkdir(exist_ok=True)

# 百炼异步转录
DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/api/v1"
FILETRANS_MODEL = "fun-asr"

# 热词管理
HOTWORD_MODEL = "speech-biasing"
HOTWORD_PREFIX = "pod"  # 热词表前缀（全账号共享，最多 10 个）


def _clear_proxy_env():
    """清除代理环境变量，返回被清除的变量以便恢复"""
    import os
    old_env = {}
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        if key in os.environ:
            old_env[key] = os.environ.pop(key)
    return old_env


def _restore_proxy_env(old_env: dict):
    """恢复代理环境变量"""
    import os
    os.environ.update(old_env)


def _get_session() -> requests.Session:
    """创建带自动重试的 requests Session（无代理）"""
    session = requests.Session()
    session.trust_env = False  # 忽略环境变量中的代理
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["POST", "GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ─────────────────────────────────────────
# 音频下载
# ─────────────────────────────────────────

def get_cache_path(episode_id: str, url: str) -> Path:
    """根据 episode_id 确定本地缓存路径"""
    ext = ".mp3"
    for candidate in [".m4a", ".aac", ".ogg", ".opus", ".mp3"]:
        if candidate in url:
            ext = candidate
            break
    return AUDIO_CACHE / f"{episode_id}{ext}"


def download_audio(episode: dict, force: bool = False) -> Path:
    """
    下载播客音频，返回本地文件路径。
    已缓存则跳过，支持断点续传。
    """
    audio_url = episode.get("audio_url", "")
    episode_id = episode.get("id") or hashlib.md5(audio_url.encode()).hexdigest()[:8]

    if not audio_url:
        raise ValueError(f"单集 {episode.get('title')} 没有可用的音频 URL")

    cache_path = get_cache_path(episode_id, audio_url)

    if cache_path.exists() and not force:
        size = cache_path.stat().st_size
        if size > 0:
            print(f"  ✅ 使用缓存音频: {cache_path.name} ({size/1024/1024:.1f}MB)")
            return cache_path
        else:
            print(f"  ⚠️  缓存文件为空({size}字节)，删除并重新下载...")
            cache_path.unlink()
            existing_size = 0

    print(f"  ⬇️  下载音频: {episode.get('title', '')[:40]}...")
    headers = {
        "User-Agent": "PodcastTranscriber/1.0 (personal-use audio downloader)",
    }

    existing_size = cache_path.stat().st_size if cache_path.exists() else 0
    if existing_size > 0:
        headers["Range"] = f"bytes={existing_size}-"

    # 下载连接重试（DNS/瞬时网络错误）
    last_error = None
    for attempt in range(1, 4):
        try:
            resp = requests.get(audio_url, headers=headers, stream=True, timeout=(30, 1800))
            resp.raise_for_status()
            break
        except Exception as e:
            last_error = e
            err_msg = str(e)
            retryable = any(kw in err_msg.lower() for kw in (
                "connection", "timeout", "dns", "getaddrinfo",
                "name or service not known", "reset by peer",
            ))
            if retryable and attempt < 3:
                delay = [5, 15, 30][attempt - 1]
                print(f"\n  ⚠️  下载连接失败（{err_msg[:60]}），{delay}s 后重试 ({attempt}/3)...")
                time.sleep(delay)
            else:
                raise

    total = int(resp.headers.get("Content-Length", 0))
    mode = "ab" if existing_size > 0 else "wb"

    downloaded = existing_size
    expected_total = total + existing_size  # 断点续传时预期总大小
    with open(cache_path, mode) as f:
        for chunk in resp.iter_content(chunk_size=1024 * 512):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / (total + existing_size) * 100
                print(f"\r  下载中... {pct:.0f}% ({downloaded/1024/1024:.1f}MB)", end="")

    # 验证下载完整性
    final_size = cache_path.stat().st_size
    if total > 0 and final_size < expected_total * 0.95:
        print(f"\n  ⚠️  下载不完整！预期 {expected_total/1024/1024:.1f}MB，实际 {final_size/1024/1024:.1f}MB")
        cache_path.unlink()
        raise IOError(f"下载不完整: {final_size} / {expected_total} 字节，已删除缓存，请重试")

    print(f"\n  ✅ 下载完成: {cache_path.name} ({cache_path.stat().st_size/1024/1024:.1f}MB)")
    return cache_path


# ─────────────────────────────────────────
# 热词管理（Fun-ASR 热词增强）
# ─────────────────────────────────────────

def _build_hotwords(episode: dict) -> list:
    """
    从播客元数据中提取热词列表（人名、公司名、术语等）。
    返回 [{"text": "xxx", "weight": 4, "lang": "zh"}, ...]
    """
    import re
    hotwords = []

    title = episode.get("title", "")
    description = episode.get("description", "")
    podcast_name = episode.get("podcast_name", "")

    # 1. 从标题提取人名（常见模式）
    name_patterns = [
        r'[与和]([^，。、｜\s聊对话探讨谈论]{2,6})(?:聊|对话|探讨|谈|论)',
        r'对话\s*[:：]?\s*([^，。、｜\s]{2,6})',
        r'嘉宾\s*[:：]?\s*([^，。、｜\s]{2,6})',
        r'专访\s*([^，。、｜\s]{2,6})',
    ]
    for pat in name_patterns:
        for name in re.findall(pat, title):
            if 2 <= len(name) <= 15 and name not in hotwords:
                hotwords.append(name)

    # 2. 从节目简介中提取可能的人名/术语
    if description:
        # 匹配「嘉宾：XXX」「本期嘉宾XXX」等
        desc_patterns = [
            r'(?:嘉宾|主持人|主讲人)[：:\s]+([^，。、\n]{2,10})',
        ]
        for pat in desc_patterns:
            for name in re.findall(pat, description):
                name = name.strip()
                if 2 <= len(name) <= 15 and name not in hotwords:
                    hotwords.append(name)

    # 3. 节目名本身（如果看起来像人名）
    # 纯中文 2-6 字可能是人名节目，排除含"的""播客""电台"等后缀的
    if re.fullmatch(r'[\u4e00-\u9fff]{2,6}', podcast_name):
        suffix_blacklist = ("的", "播客", "电台", "节目", "频道", "时间", "空间")
        if not any(podcast_name.endswith(s) for s in suffix_blacklist):
            hotwords.insert(0, podcast_name)

    # 去重，构建热词列表
    seen = set()
    result = []
    for word in hotwords:
        clean = word.strip()
        if clean and clean not in seen and len(clean) <= 15:
            seen.add(clean)
            result.append({"text": clean, "weight": 4, "lang": "zh"})

    return result


def _create_vocabulary(api_key: str, hotwords: list) -> str:
    """
    创建热词表，返回 vocabulary_id。
    热词免费，每账号最多 10 个（使用后会删除）。
    """
    if not hotwords:
        return ""

    customize_url = f"{DASHSCOPE_BASE}/services/audio/asr/customization"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": HOTWORD_MODEL,
        "input": {
            "action": "create_vocabulary",
            "target_model": FILETRANS_MODEL,
            "prefix": HOTWORD_PREFIX,
            "vocabulary": hotwords,
        },
    }

    session = _get_session()
    resp = session.post(customize_url, headers=headers, json=payload, timeout=30)

    if resp.status_code != 200:
        raise RuntimeError(f"创建热词表失败: {resp.status_code} {resp.text[:300]}")

    data = resp.json()
    vocab_id = data.get("output", {}).get("vocabulary_id", "")
    if not vocab_id:
        raise RuntimeError(f"热词表响应缺少 vocabulary_id: {resp.text[:300]}")

    print(f"  🔤 热词表已创建（{len(hotwords)} 个词）: {vocab_id[:20]}...")
    return vocab_id


def _delete_vocabulary(api_key: str, vocabulary_id: str):
    """删除热词表，释放配额"""
    if not vocabulary_id:
        return

    customize_url = f"{DASHSCOPE_BASE}/services/audio/asr/customization"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": HOTWORD_MODEL,
        "input": {
            "action": "delete_vocabulary",
            "vocabulary_id": vocabulary_id,
        },
    }

    try:
        session = _get_session()
        resp = session.post(customize_url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            print(f"  🗑️  热词表已删除: {vocabulary_id[:20]}...")
        else:
            print(f"  ⚠️  删除热词表失败: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"  ⚠️  删除热词表异常: {e}")


def _list_vocabulary_ids(api_key: str) -> list:
    """列出当前所有热词表 ID（用于清理残留）"""
    customize_url = f"{DASHSCOPE_BASE}/services/audio/asr/customization"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": HOTWORD_MODEL,
        "input": {
            "action": "list_vocabulary",
            "prefix": HOTWORD_PREFIX,
            "page_index": 0,
            "page_size": 10,
        },
    }

    try:
        session = _get_session()
        resp = session.post(customize_url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            vocabularies = data.get("output", {}).get("vocabularies", [])
            return [v.get("vocabulary_id", "") for v in vocabularies if v.get("vocabulary_id")]
    except Exception as e:
        print(f"  ⚠️  查询热词表列表失败: {e}")

    return []


def _cleanup_stale_vocabularies(api_key: str):
    """清理残留的热词表（避免占满 10 个配额）"""
    vocab_ids = _list_vocabulary_ids(api_key)
    if not vocab_ids:
        return

    print(f"  🧹 发现 {len(vocab_ids)} 个残留热词表，正在清理...")
    for vid in vocab_ids:
        _delete_vocabulary(api_key, vid)


# ─────────────────────────────────────────
# 百炼异步文件转录
# ─────────────────────────────────────────

def _upload_file_curl(audio_path: Path, api_key: str) -> str:
    """使用 curl 上传音频文件到百炼（绕过 Python SSL 代理问题）
    正确端点: POST /api/v1/files，字段名: files（非 file）
    """
    upload_url = "https://dashscope.aliyuncs.com/api/v1/files"

    result = subprocess.run(
        [
            "curl", "--noproxy", "*",
            "-s", "-w", "\nHTTP_CODE:%{http_code}",
            "-X", "POST", upload_url,
            "-H", f"Authorization: Bearer {api_key}",
            "-F", f"files=@{audio_path};type=audio/mp4",
            "-F", "purpose=file-extract",
            "--max-time", "600",
        ],
        capture_output=True, timeout=900,
    )

    output = result.stdout.decode("utf-8", errors="replace")
    lines = output.strip().split("\n")

    http_code = ""
    json_body = ""
    for line in lines:
        if line.startswith("HTTP_CODE:"):
            http_code = line.split(":")[1]
        else:
            json_body += line

    if not http_code or http_code != "200":
        raise RuntimeError(f"curl 上传失败，HTTP {http_code}: {json_body[:300]}")

    data = json.loads(json_body)
    uploaded_files = data.get("data", {}).get("uploaded_files", [])
    if not uploaded_files:
        raise RuntimeError(f"上传响应中无成功文件: {json_body[:300]}")

    file_id = uploaded_files[0].get("file_id", "")
    if not file_id:
        raise RuntimeError(f"上传响应中缺少 file_id: {json_body[:300]}")

    print(f"  ✅ 上传成功（curl），file_id: {file_id[:16]}...")

    # 获取文件 URL
    print(f"  📥 获取文件 URL...")
    detail_url = f"https://dashscope.aliyuncs.com/api/v1/files/{file_id}"
    detail_result = subprocess.run(
        ["curl", "--noproxy", "*", "-s", "-w", "\nHTTP_CODE:%{http_code}",
         detail_url, "-H", f"Authorization: Bearer {api_key}"],
        capture_output=True, timeout=30,
    )
    detail_output = detail_result.stdout.decode("utf-8", errors="replace")
    detail_lines = detail_output.strip().split("\n")
    detail_json = ""
    for line in detail_lines:
        if not line.startswith("HTTP_CODE:"):
            detail_json += line

    detail_data = json.loads(detail_json)
    file_url = detail_data.get("data", {}).get("url", "")
    if not file_url:
        raise RuntimeError(f"文件详情中缺少 url: {detail_json[:300]}")

    print(f"  ✅ 获取文件 URL 成功: {file_url[:60]}...")
    return file_url


def _upload_file_requests(audio_path: Path, api_key: str) -> str:
    """使用 Python requests 上传音频文件（备选方案）"""
    upload_url = f"{DASHSCOPE_BASE}/files"
    upload_headers = {"Authorization": f"Bearer {api_key}"}

    session = _get_session()
    with open(audio_path, "rb") as f:
        upload_resp = session.post(
            upload_url,
            headers=upload_headers,
            files={"files": (audio_path.name, f)},
            data={"purpose": "file-extract"},
            timeout=(30, 1800),
        )

    if upload_resp.status_code != 200:
        raise RuntimeError(f"文件上传失败: {upload_resp.status_code} {upload_resp.text[:300]}")

    upload_data = upload_resp.json()
    uploaded_files = upload_data.get("data", {}).get("uploaded_files", [])
    if not uploaded_files:
        raise RuntimeError(f"上传响应中无成功文件: {upload_resp.text[:300]}")

    file_id = uploaded_files[0].get("file_id", "")
    if not file_id:
        raise RuntimeError(f"上传响应中缺少 file_id: {upload_resp.text[:300]}")

    print(f"  ✅ 上传成功（requests），file_id: {file_id[:16]}...")

    # 获取文件 URL
    print(f"  📥 获取文件 URL...")
    detail_resp = session.get(
        f"{DASHSCOPE_BASE}/files/{file_id}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )

    if detail_resp.status_code != 200:
        raise RuntimeError(f"获取文件详情失败: {detail_resp.status_code} {detail_resp.text[:300]}")

    detail_data = detail_resp.json()
    file_url = detail_data.get("data", {}).get("url", "")
    if not file_url:
        raise RuntimeError(f"文件详情中缺少 url: {detail_resp.text[:300]}")

    print(f"  ✅ 获取文件 URL 成功: {file_url[:60]}...")
    return file_url


def _upload_file(audio_path: Path, api_key: str) -> str:
    """上传音频文件到百炼 OSS（优先 curl，回退 requests）"""
    # 优先用 curl（已验证能绕过代理 SSL 问题）
    try:
        return _upload_file_curl(audio_path, api_key)
    except FileNotFoundError:
        print(f"  ⚠️  curl 不可用，回退到 Python requests...")
    except Exception as e:
        print(f"  ⚠️  curl 上传失败: {e}，回退到 Python requests...")

    return _upload_file_requests(audio_path, api_key)


def _submit_task(file_url: str, api_key: str, vocabulary_id: str = None) -> str:
    """提交异步转录任务（Fun-ASR），返回 task_id"""
    task_url = f"{DASHSCOPE_BASE}/services/audio/asr/transcription"
    task_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }

    task_input = {"file_urls": [file_url]}
    if vocabulary_id:
        task_input["vocabulary_id"] = vocabulary_id

    task_payload = {
        "model": FILETRANS_MODEL,
        "input": task_input,
        "parameters": {"channel_id": [0], "language_hints": ["zh"]},
    }

    session = _get_session()
    task_resp = session.post(task_url, headers=task_headers, json=task_payload, timeout=30)

    if task_resp.status_code != 200:
        raise RuntimeError(f"提交转录任务失败: {task_resp.status_code} {task_resp.text[:300]}")

    task_data = task_resp.json()
    task_id = task_data.get("output", {}).get("task_id", "")
    if not task_id:
        raise RuntimeError(f"提交响应中缺少 task_id: {task_resp.text[:300]}")

    print(f"  ✅ 转录任务已提交，task_id: {task_id[:16]}...")
    return task_id


def _poll_result(task_id: str, api_key: str, max_wait: int = 1800) -> dict:
    """轮询转录任务状态，返回转录结果 JSON"""
    poll_interval = 5
    elapsed = 0
    session = _get_session()

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        status_url = f"{DASHSCOPE_BASE}/tasks/{task_id}"
        status_resp = session.get(
            status_url,
            headers={"Authorization": f"Bearer {api_key}", "X-DashScope-Async": "enable"},
            timeout=30,
        )

        status_data = status_resp.json()
        task_status = status_data.get("output", {}).get("task_status", "")

        if task_status == "SUCCEEDED":
            print(f"  ✅ 转录完成（总耗时 {elapsed}秒）")
            return status_data

        elif task_status == "FAILED":
            error_msg = status_data.get("output", {}).get("message", "未知错误")
            code = status_data.get("output", {}).get("code", "")
            raise RuntimeError(f"转录任务失败 [{code}]: {error_msg}")

        else:
            # PENDING / RUNNING
            print(f"\r  ⏳ 转录中... ({elapsed}秒, 状态: {task_status})", end="")

    raise RuntimeError(f"转录超时（{max_wait}秒）")


def _parse_transcription_result(result_data: dict) -> dict:
    """解析转录结果，返回 {text, segments, duration}"""
    import os
    # 确保无代理
    old_env = _clear_proxy_env()

    try:
        output = result_data.get("output", {})

        # 策略1: 从 transcription_url 下载详细结果（带时间戳分段）
        # 兼容两种响应格式：
        #   - Fun-ASR:      output.results[].transcription_url（复数数组）
        #   - 旧模型(qwen):  output.result.transcription_url（单数 dict）
        transcription_url = ""

        # 先尝试 Fun-ASR 格式：output.results（复数数组）
        results_list = output.get("results")
        if isinstance(results_list, list) and results_list:
            for r in results_list:
                if r.get("subtask_status") == "SUCCEEDED" and r.get("transcription_url"):
                    transcription_url = r["transcription_url"]
                    break

        # 再尝试旧模型格式：output.result（单数 dict）
        if not transcription_url:
            result = output.get("result", {})
            transcription_url = result.get("transcription_url", "")

        if transcription_url:
            print(f"  📥 下载转录详情...")
            session = _get_session()
            tr_resp = session.get(transcription_url, timeout=120)
            tr_data = tr_resp.json()

            text_parts = []
            segments = []

            # 解析嵌套结构：tr_data["transcripts"][0]["sentences"]
            if isinstance(tr_data, dict):
                # 处理嵌套 transcripts 结构
                nested_transcripts = tr_data.get("transcripts", [])
                if isinstance(nested_transcripts, list) and nested_transcripts:
                    # 取第一个频道（通常是 channel_id=0）
                    first_channel = nested_transcripts[0]
                    if isinstance(first_channel, dict):
                        # 优先使用 sentences 字段
                        sentences = first_channel.get("sentences", [])
                        if sentences:
                            for sentence in sentences:
                                text_parts.append(sentence.get("text", ""))
                                segments.append({
                                    "start": sentence.get("begin_time", 0) / 1000.0,
                                    "end": sentence.get("end_time", 0) / 1000.0,
                                    "text": sentence.get("text", ""),
                                })
                        # 如果 sentences 为空但 channel 有 text，作为纯文本回退
                        elif first_channel.get("text"):
                            text_parts.append(first_channel["text"])

                # 如果嵌套 transcripts 没有产出 segments，尝试顶层字段
                if not segments:
                    sentences = (tr_data.get("sentences")
                                 or tr_data.get("results")
                                 or tr_data.get("transcription_results", []))
                    if not sentences and "transcriptions" in tr_data:
                        val = tr_data["transcriptions"]
                        if isinstance(val, list) and val and isinstance(val[0], dict) and "sentences" in val[0]:
                            sentences = val[0]["sentences"]
                        elif isinstance(val, list):
                            sentences = val
                    if not sentences:
                        for v in tr_data.values():
                            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                                if "begin_time" in v[0] or "start" in v[0]:
                                    sentences = v
                                    break

                    for sentence in sentences:
                        text_parts.append(sentence.get("text", ""))
                        segments.append({
                            "start": sentence.get("begin_time", 0) / 1000.0,
                            "end": sentence.get("end_time", 0) / 1000.0,
                            "text": sentence.get("text", ""),
                        })

            elif isinstance(tr_data, list):
                for item in tr_data:
                    text_parts.append(item.get("text", ""))
                    segments.append({
                        "start": item.get("begin_time", 0) / 1000.0,
                        "end": item.get("end_time", 0) / 1000.0,
                        "text": item.get("text", ""),
                    })

            if segments:
                full_text = "\n".join(text_parts) if text_parts else "\n".join(s["text"] for s in segments)
                duration = segments[-1]["end"] if segments else 0
                print(f"  ✅ 解析到 {len(segments)} 个时间戳分段，时长 {duration:.0f}s")
                return {"text": full_text, "segments": segments, "duration": duration}

            if text_parts:
                full_text = "\n".join(text_parts)
                print(f"  ⚠️  仅有纯文本，无时间戳分段（{len(full_text)} 字）")
                return {"text": full_text, "segments": [], "duration": 0}

            # transcription_url 有内容但无法解析
            print(f"  ⚠️  transcription_url 数据无法解析，结构: {type(tr_data).__name__}")
            if isinstance(tr_data, dict):
                print(f"  ⚠️  keys: {list(tr_data.keys())[:10]}")
            print(f"  ⚠️  内容预览: {str(tr_data)[:300]}")

        # 策略2: 直接从 choices 字段提取文本
        choices = output.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if content:
                return {"text": content, "segments": [], "duration": 0}

        # 策略3: 检查 result 中的 text 字段
        if result.get("text"):
            return {"text": result["text"], "segments": [], "duration": 0}

        # 策略4: 打印完整 output 帮助调试
        print(f"  ⚠️  无法解析转录结果，output 结构: {list(output.keys())}")
        print(f"  ⚠️  output 内容预览: {str(output)[:500]}")
        return {"text": "", "segments": [], "duration": 0}
    finally:
        _restore_proxy_env(old_env)


def transcribe_audio(audio_path: Path, config: dict, episode: dict = None) -> dict:
    """
    百炼异步文件转录（Fun-ASR + 热词增强）。
    流程：清理残留热词 → 构建热词 → 上传 → 提交任务（含热词） → 轮询 → 解析结果 → 删除热词
    """
    t_cfg = config.get("transcription", {})
    api_key = t_cfg.get("openai_api_key", "")

    if not api_key or api_key == "sk-xxxx":
        raise ValueError("请在 config.json 的 transcription.openai_api_key 中填入百炼 API Key")

    file_size = audio_path.stat().st_size
    print(f"  🎙️  开始转录（Fun-ASR + 热词增强）: {audio_path.name} ({file_size/1024/1024:.1f}MB)")
    start_time = time.time()

    # Step 0: 清理残留热词表（防配额耗尽）
    _cleanup_stale_vocabularies(api_key)

    # Step 1: 构建并创建热词表
    vocabulary_id = ""
    hotwords = _build_hotwords(episode or {})
    if hotwords:
        try:
            vocabulary_id = _create_vocabulary(api_key, hotwords)
        except Exception as e:
            print(f"  ⚠️  热词表创建失败，继续无热词转录: {e}")

    try:
        # Step 2: 上传（curl 优先）
        print(f"  📤 上传音频文件到百炼...")
        file_url = _upload_file(audio_path, api_key)

        # Step 3: 提交任务（含热词）
        print(f"  📋 提交异步转录任务...")
        task_id = _submit_task(file_url, api_key, vocabulary_id)

        # Step 4: 轮询
        result_data = _poll_result(task_id, api_key, max_wait=1800)

        # Step 5: 解析结果
        transcript = _parse_transcription_result(result_data)

        elapsed = time.time() - start_time
        seg_count = len(transcript["segments"])
        hotword_info = f"，热词 {len(hotwords)} 个" if hotwords else ""
        print(f"  ✅ 转录完成，耗时 {elapsed:.0f}秒，共 {len(transcript['text'])} 字，{seg_count} 个分段{hotword_info}")

        return transcript
    finally:
        # Step 6: 删除热词表（释放配额）
        if vocabulary_id:
            _delete_vocabulary(api_key, vocabulary_id)


# ─────────────────────────────────────────
# 转录缓存读写（独立 JSON 文件）
# ─────────────────────────────────────────

def _get_transcript_cache_path(episode_id: str) -> Path:
    """根据 episode_id 确定转录缓存文件路径"""
    return TRANSCRIPT_CACHE / f"{episode_id}.json"


def load_transcript_cache(episode: dict) -> Optional[dict]:
    """
    尝试读取本地转录缓存。
    返回缓存 dict（含 transcript / transcript_segments / audio_duration），
    若缓存不存在或无效则返回 None。
    """
    audio_url = episode.get("audio_url", "")
    episode_id = episode.get("id") or (hashlib.md5(audio_url.encode()).hexdigest()[:8] if audio_url else "")
    if not episode_id:
        return None

    cache_path = _get_transcript_cache_path(episode_id)
    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        transcript_text = data.get("transcript", "")
        segments = data.get("transcript_segments", [])

        # 有效缓存：转录文本非空，或有足够多的分段
        if transcript_text or len(segments) > 5:
            print(f"  📋 找到本地转录缓存（{len(transcript_text)} 字，{len(segments)} 分段）")
            return data
        else:
            print(f"  ⚠️  本地缓存无效（内容为空），将重新转录")
            return None
    except (json.JSONDecodeError, IOError) as e:
        print(f"  ⚠️  本地缓存读取失败，将重新转录: {e}")
        return None


def save_transcript_cache(episode: dict):
    """
    将转录结果写入本地缓存文件（JSON）。
    在 transcribe_audio 成功返回后调用。
    """
    audio_url = episode.get("audio_url", "")
    episode_id = episode.get("id") or (hashlib.md5(audio_url.encode()).hexdigest()[:8] if audio_url else "")
    if not episode_id:
        return

    cache_path = _get_transcript_cache_path(episode_id)
    cache_data = {
        "episode_id": episode_id,
        "title": episode.get("title", ""),
        "podcast_name": episode.get("podcast_name", ""),
        "transcript": episode.get("transcript", ""),
        "transcript_segments": episode.get("transcript_segments", []),
        "audio_duration": episode.get("audio_duration", 0),
        "audio_local_path": episode.get("audio_local_path", ""),
        "cached_at": datetime.now().isoformat(),
    }

    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"  💾 转录缓存已保存: {cache_path.name}")
    except IOError as e:
        print(f"  ⚠️  转录缓存写入失败: {e}")


def _cleanup_audio_cache(episode: dict):
    """转录完成后删除音频缓存，释放磁盘空间（失败不阻塞主流程）。"""
    episode_id = episode.get("id", "")
    if not episode_id:
        return
    for f in AUDIO_CACHE.glob(f"{episode_id}.*"):
        try:
            size_mb = f.stat().st_size / 1024 / 1024
            f.unlink()
            print(f"  🧹 音频缓存已清理: {f.name} ({size_mb:.1f}MB)")
        except Exception as e:
            print(f"  ⚠️ 清理音频缓存失败: {f.name} - {e}")


def download_and_transcribe(episode: dict, config: dict) -> dict:
    """
    完整流程：下载 → 上传 → 转录 → 返回结果
    支持从本地缓存恢复（已含完整 transcript + segments）
    转录成功（含缓存命中）后自动删除音频文件释放磁盘空间。
    """
    try:
        # Step 1: 下载音频
        audio_path = download_audio(episode)
        episode["audio_local_path"] = str(audio_path)

        # Step 2: 检查本地转录缓存
        cached = load_transcript_cache(episode)
        if cached:
            episode["transcript"] = cached.get("transcript", "")
            episode["transcript_segments"] = cached.get("transcript_segments", [])
            episode["audio_duration"] = cached.get("audio_duration", 0)
            # audio_local_path 已在上面设置
            episode["_from_cache"] = True  # 标记来源，供调用方判断
            _cleanup_audio_cache(episode)  # 缓存命中，清理音频
            return episode

        # Step 3: 上传并转录（传入 episode 以构建热词）
        transcript = transcribe_audio(audio_path, config, episode)

        episode["transcript"] = transcript["text"]
        episode["transcript_segments"] = transcript["segments"]
        episode["audio_duration"] = transcript["duration"] or episode.get("duration_seconds", 0)

        # Step 4: 保存转录缓存（供下次快速恢复，跳过重新转录）
        save_transcript_cache(episode)

        # Step 5: 清理音频缓存，释放磁盘空间
        _cleanup_audio_cache(episode)

        return episode
    except Exception as e:
        print(f"  ❌ 处理失败 [{episode.get('title', '')[:30]}]: {e}")
        episode["transcript"] = ""
        episode["transcript_segments"] = []
        episode["error"] = str(e)
        # 异常时不清理音频（保留供重试）
        return episode
