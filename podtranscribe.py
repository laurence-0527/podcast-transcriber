"""
podtranscribe.py — 音频下载 + ASR 转录模块
默认本地管线：faster-whisper（Whisper large-v3，CPU int8）识别 + FunASR ct-punc
标点恢复 + 句级合并，输出与百炼 Paraformer 相当的句级分段，无需 API Key；
可选百炼/DashScope ASR；faster-whisper 不可用时回退 openai-whisper。

流程：下载音频 → 按配置选择 ASR 后端 → 解析带时间戳的转录文本
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from datetime import datetime

# Windows + Anaconda 环境下 ctranslate2 与 MKL 各带一份 OpenMP 运行时，
# 不设此变量会在推理时 abort（libiomp5md.dll already initialized）
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

AUDIO_CACHE = Path(__file__).parent / "audio_cache"
AUDIO_CACHE.mkdir(exist_ok=True)

# 转录结果缓存目录（独立于输出目录）
TRANSCRIPT_CACHE = Path(__file__).parent / "transcript_cache"
TRANSCRIPT_CACHE.mkdir(exist_ok=True)


# ─────────────────────────────────────────
# 音频下载
# ─────────────────────────────────────────

def get_cache_path(episode_id: str, url: str) -> Path:
    """根据 episode_id 确定本地缓存路径"""
    ext = ".mp3"
    for candidate in [".m4a", ".aac", ".ogg", ".opus", ".mp3", ".wav", ".flac"]:
        if candidate in url:
            ext = candidate
            break
    return AUDIO_CACHE / f"{episode_id}{ext}"


def _get_session() -> requests.Session:
    """创建带自动重试的 requests Session"""
    session = requests.Session()
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


def download_audio(episode: dict, force: bool = False, proxy: str = "") -> Path:
    """
    下载播客音频，返回本地文件路径。
    已缓存则跳过，支持断点续传。proxy 非空时经代理下载（代理失败自动回退直连）。
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

    proxies = {"http": proxy, "https": proxy} if proxy else None

    last_error = None
    for attempt in range(1, 4):
        try:
            resp = requests.get(audio_url, headers=headers, stream=True,
                                timeout=(30, 1800), proxies=proxies)
            resp.raise_for_status()
            break
        except requests.exceptions.ProxyError as e:
            if proxies:
                print(f"\n  ⚠️  代理连接失败，回退为直连重试...")
                proxies = None
                existing_size = 0
                headers.pop("Range", None)
                continue
            raise
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
    expected_total = total + existing_size
    with open(cache_path, mode) as f:
        for chunk in resp.iter_content(chunk_size=1024 * 512):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / (total + existing_size) * 100
                print(f"\r  下载中... {pct:.0f}% ({downloaded/1024/1024:.1f}MB)", end="")

    final_size = cache_path.stat().st_size
    if total > 0 and final_size < expected_total * 0.95:
        print(f"\n  ⚠️  下载不完整！预期 {expected_total/1024/1024:.1f}MB，实际 {final_size/1024/1024:.1f}MB")
        cache_path.unlink()
        raise IOError(f"下载不完整: {final_size} / {expected_total} 字节，已删除缓存，请重试")

    print(f"\n  ✅ 下载完成: {cache_path.name} ({cache_path.stat().st_size/1024/1024:.1f}MB)")
    return cache_path


# ─────────────────────────────────────────
# ASR 路由：本地 Whisper / 百炼 DashScope
# ─────────────────────────────────────────

def _get_bailian_api_key(bailian_cfg: dict) -> str:
    """读取百炼 API Key：配置 > 环境变量"""
    key = (bailian_cfg.get("api_key") or "").strip()
    if not key:
        key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not key:
        raise ValueError(
            "asr.backend='bailian' 需要提供百炼 API Key："
            "在 config.json 的 bailian.api_key 中填写，或设置环境变量 DASHSCOPE_API_KEY"
        )
    return key


def _upload_file_to_bailian(audio_path: Path, api_key: str) -> tuple[str, str]:
    """把本地音频上传到百炼文件服务，返回 (file_id, public_url)"""
    base_url = "https://dashscope.aliyuncs.com/api/v1/files"
    with open(audio_path, "rb") as f:
        files = {"files": (audio_path.name, f, "audio/mpeg")}
        data = {"purpose": "file-extract"}
        resp = requests.post(
            base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
            data=data,
            timeout=(30, 300),
        )
    resp.raise_for_status()
    payload = resp.json()
    uploaded = payload.get("data", {}).get("uploaded_files", [])
    if not uploaded:
        failed = payload.get("data", {}).get("failed_uploads", [])
        msg = failed[0].get("message", "unknown") if failed else "unknown"
        raise RuntimeError(f"百炼文件上传失败: {msg}")

    file_id = uploaded[0]["file_id"]
    # 获取可公开访问的临时下载链接，供 Paraformer 识别使用
    detail_resp = requests.get(
        f"{base_url}/{file_id}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=(30, 60),
    )
    detail_resp.raise_for_status()
    file_url = detail_resp.json().get("data", {}).get("url", "")
    if not file_url:
        raise RuntimeError("百炼文件上传成功，但未能获取到可识别的下载链接")
    return file_id, file_url


def _transcribe_bailian(audio_path: Path, config: dict, episode: dict = None) -> dict:
    """使用百炼/DashScope Paraformer 转录音频"""
    from http import HTTPStatus
    from dashscope.audio.asr import Transcription
    import dashscope

    bailian_cfg = config.get("bailian", {})
    api_key = _get_bailian_api_key(bailian_cfg)
    dashscope.api_key = api_key
    if bailian_cfg.get("base_url"):
        dashscope.base_http_api_url = bailian_cfg["base_url"]

    model = bailian_cfg.get("model", "paraformer-v2")
    language_hints = bailian_cfg.get("language_hints", ["zh", "en"])
    disfluency = bailian_cfg.get("disfluency_removal_enabled", False)
    timestamp_align = bailian_cfg.get("timestamp_alignment_enabled", True)

    file_size = audio_path.stat().st_size
    print(f"  🎙️  开始百炼转录（{model}）: {audio_path.name} ({file_size/1024/1024:.1f}MB)")
    start_time = time.time()

    # 上传本地文件到百炼文件服务，并拿到公网可访问的临时 URL
    print("  ⬆️  上传音频到百炼...")
    file_id, file_url = _upload_file_to_bailian(audio_path, api_key)
    print(f"  ✅ 上传完成，file_id: {file_id[:16]}...")

    # 提交识别任务
    print("  ⏳ 提交识别任务...")
    task_response = Transcription.async_call(
        model=model,
        file_urls=[file_url],
        language_hints=language_hints,
        disfluency_removal_enabled=disfluency,
        timestamp_alignment_enabled=timestamp_align,
    )

    # 等待任务完成
    print("  ⏳ 等待识别结果（耗时取决于音频长度）...")
    transcribe_response = Transcription.wait(task=task_response.output.task_id)

    if transcribe_response.status_code != HTTPStatus.OK:
        raise RuntimeError(f"百炼识别任务失败: {transcribe_response.message}")

    task_status = transcribe_response.output.get("task_status")
    results = transcribe_response.output.get("results") or []
    if task_status != "SUCCEEDED" or not results:
        detail = results[0] if results else {}
        raise RuntimeError(
            f"百炼识别任务未成功: task_status={task_status}, "
            f"code={detail.get('code', 'N/A')}, message={detail.get('message', 'N/A')}"
        )

    subtask_status = results[0].get("subtask_status")
    if subtask_status != "SUCCEEDED":
        raise RuntimeError(
            f"百炼识别子任务未成功: subtask_status={subtask_status}, "
            f"code={results[0].get('code', 'N/A')}, message={results[0].get('message', 'N/A')}"
        )

    # 下载识别结果
    result_url = results[0].get("transcription_url")
    if not result_url:
        raise RuntimeError("百炼识别结果中没有 transcription_url")

    result_resp = requests.get(result_url, timeout=(30, 300))
    result_resp.raise_for_status()
    result_payload = result_resp.json()

    # 解析为统一格式
    transcripts = result_payload.get("transcripts", [])
    segments = []
    text_parts = []
    for transcript in transcripts:
        for sentence in transcript.get("sentences", []):
            text = sentence.get("text", "").strip()
            if not text:
                continue
            begin_ms = int(sentence.get("begin_time", 0))
            end_ms = int(sentence.get("end_time", 0))
            segments.append({
                "start": begin_ms / 1000.0,
                "end": end_ms / 1000.0,
                "text": text,
            })
            text_parts.append(text)

    full_text = "\n".join(text_parts)
    duration = segments[-1]["end"] if segments else (episode.get("duration_seconds", 0) if episode else 0)

    elapsed = time.time() - start_time
    print(f"  ✅ 转录完成，耗时 {elapsed:.0f}秒，共 {len(full_text)} 字，{len(segments)} 个分段")

    return {"text": full_text, "segments": segments, "duration": duration}


# ─────────────────────────────────────────
# 本地转录（faster-whisper 优先，openai-whisper 兜底）
# ─────────────────────────────────────────

def _build_prompt(episode: dict) -> str:
    """
    用播客元数据构造 initial prompt，帮助模型识别节目名、嘉宾、专业术语。
    """
    if not episode:
        return ""

    podcast_name = episode.get("podcast_name", "").strip()
    title = episode.get("title", "").strip()
    description = episode.get("description", "").strip()

    parts = []
    if podcast_name:
        parts.append(f"播客：《{podcast_name}》")
    if title:
        parts.append(f"单集标题：《{title}》")
    if description:
        # 简介可能很长，截断到 300 字
        desc = re.sub(r"\s+", " ", description)[:300]
        parts.append(f"简介：{desc}")

    if not parts:
        return ""

    return "\n".join(parts)


SENTENCE_END_PUNCT = "。！？!?…～；;"


def merge_segments_to_sentences(segments: list, min_len: int = 20, max_len: int = 150) -> list:
    """
    将 ASR 细粒度分段合并为句级分段（对标百炼 Paraformer 的按句输出）。
    把所有文本拼成字符序列（记录每字符所属分段），在满足 min_len 的
    首个句末标点处切句；无标点的超长片段按 max_len 强制切分。
    句子时间戳取首/尾字符所属分段的起止时间。
    """
    chars = []
    seg_ids = []
    for idx, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        for ch in text:
            chars.append(ch)
            seg_ids.append(idx)
    if not chars:
        return []

    full = "".join(chars)
    n = len(full)
    merged = []
    lo = 0
    while lo < n:
        cut = -1
        limit = min(lo + max_len, n)
        for i in range(lo + min_len - 1, limit):
            if full[i] in SENTENCE_END_PUNCT:
                cut = i + 1
                break
        if cut < 0:
            cut = limit  # 无标点长片段：强制截断

        sentence = full[lo:cut].strip()
        if sentence:
            s0 = segments[seg_ids[lo]].get("start", 0)
            e0 = segments[seg_ids[cut - 1]].get("end", s0)
            merged.append({"start": s0, "end": e0, "text": sentence})
        lo = cut
    return merged


# ── 标点恢复（ct-punc）──────────────────
# faster-whisper 中文输出不含任何标点，导致无法按句断句。
# 用 FunASR 的 ct-punc（CT-Transformer）对标点做恢复，再按字符对齐写回分段。

_PUNC_MODEL = None
_PUNC_FAILED = False


def _clean_restored_text(text: str) -> str:
    """修复 ct-punc 输出的常见残留：重复标点、英文首字母重复、中文语境的 ASCII 标点。"""
    # 1) 连续重复标点合并为一个（如 "，：" → "："，取最后一个）
    text = re.sub(r"[，,。.、！!？?；;：:…～~]{2,}", lambda m: m.group(0)[-1], text)
    # 2) 模型偶发的英文首字母重复："S SpaceX" → "SpaceX"
    text = re.sub(r"(?<![A-Za-z])([A-Za-z]) (?=\1)", "", text)
    # 3) 中文语境下 ASCII 标点转全角（先保护数字小数点 3.5）
    _MAP = {",": "，", ".": "。", "!": "！", "?": "？", ";": "；", ":": "："}
    text = re.sub(r"(\d)\.", lambda m: m.group(1) + "\u0001", text)
    text = re.sub(r"[,.!?;:]\s*(?=[\u4e00-\u9fff])", lambda m: _MAP[m.group(0)[0]], text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])[,!?;:]", lambda m: _MAP[m.group(0)], text)
    text = text.replace("\u0001", ".")
    return text


def _get_punc_model(model_name: str = "ct-punc"):
    """懒加载全局标点恢复模型（只加载一次）"""
    global _PUNC_MODEL
    if _PUNC_MODEL is None:
        from funasr import AutoModel
        print(f"  ⏳ 加载标点恢复模型（{model_name}）...")
        _PUNC_MODEL = AutoModel(model=model_name)
        print(f"  ✅ 标点模型加载完成")
    return _PUNC_MODEL


def _align_punct_to_segments(raw: str, out: str, seg_count: int, bounds: list) -> list:
    """
    将标点恢复结果 out 对齐回各分段（difflib 求编辑对齐）。
    bounds: 每个分段在 raw 中的 [start, end) 字符区间。
    - equal: 逐字符归属对应分段
    - insert: 插入的标点归属前一个 raw 字符所在分段；纯空白插入直接丢弃
      （ct-punc 会在英文单词内插空格，如 "Jeff" → "J eff"）
    - replace: 归属被替换 raw 范围起始的分段
    - delete: 保留 raw 原文，防止丢字
    """
    import difflib

    aligned = [""] * seg_count

    def seg_of(ri: int) -> int:
        if ri < 0:
            return 0
        if ri >= bounds[-1][1]:
            return seg_count - 1
        lo, hi = 0, seg_count - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if ri >= bounds[mid][1]:
                lo = mid + 1
            else:
                hi = mid
        return lo

    sm = difflib.SequenceMatcher(a=raw, b=out, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                aligned[seg_of(i1 + k)] += out[j1 + k]
        elif tag == "insert":
            added = out[j1:j2]
            if added.strip() == "":
                continue  # 丢弃 ct-punc 插入的空白
            aligned[seg_of(i1 - 1)] += added
        elif tag == "replace":
            aligned[seg_of(i1)] += out[j1:j2]
        else:  # delete
            for k in range(i1, i2):
                aligned[seg_of(k)] += raw[k]
    return aligned


def restore_punctuation(segments: list, config: dict) -> list:
    """
    对 ASR 细粒度分段做标点恢复（ct-punc），返回带标点的新分段列表。
    文本分块送模型（避免超长输入），标点按字符对齐写回原分段时间戳。
    """
    global _PUNC_FAILED
    if _PUNC_FAILED or not segments:
        return segments

    asr_cfg = config.get("asr", {})
    model_name = asr_cfg.get("punc_model", "ct-punc")
    chunk_size = int(asr_cfg.get("punc_chunk_size", 800) or 800)

    try:
        punc = _get_punc_model(model_name)
    except Exception as e:
        print(f"  ⚠️  标点模型加载失败，跳过标点恢复: {e}")
        _PUNC_FAILED = True
        return segments

    result = []
    i = 0
    t0 = time.time()
    n = len(segments)
    while i < n:
        # 累积若干分段为一个 chunk（首个分段必收，之后累计超过 chunk_size 即止）
        chunk = []
        total = 0
        while i < n and (total == 0 or total < chunk_size):
            chunk.append(segments[i])
            total += len(segments[i]["text"])
            i += 1

        raw = "".join(s["text"] for s in chunk)
        res = punc.generate(input=raw)
        out = (res[0].get("text", "") if res else "") or raw

        bounds, pos = [], 0
        for s in chunk:
            bounds.append((pos, pos + len(s["text"])))
            pos += len(s["text"])

        aligned = _align_punct_to_segments(raw, out, len(chunk), bounds)
        for s, text in zip(chunk, aligned):
            text = _clean_restored_text(text).strip()
            if text:
                result.append({"start": s["start"], "end": s["end"], "text": text})

    print(f"  ✍️  标点恢复完成，耗时 {time.time()-t0:.0f}s（{model_name}）")
    return result


def _transcribe_faster_whisper(audio_path: Path, config: dict, episode: dict = None) -> dict:
    """使用 faster-whisper（CTranslate2）本地转录：CPU int8 量化，速度快、精度高。"""
    from faster_whisper import WhisperModel

    asr_cfg = config.get("asr", {})
    model_name = asr_cfg.get("model", "large-v3")
    compute_type = asr_cfg.get("compute_type", "int8")
    cpu_threads = int(asr_cfg.get("cpu_threads", 8) or 8)
    language = asr_cfg.get("language", "zh")
    vad_filter = bool(asr_cfg.get("vad_filter", True))
    condition_on_previous_text = asr_cfg.get("condition_on_previous_text", True)
    beam_size = int(asr_cfg.get("beam_size", 5) or 5)

    file_size = audio_path.stat().st_size
    print(f"  🎙️  开始本地转录（faster-whisper {model_name} / cpu-{compute_type}）: "
          f"{audio_path.name} ({file_size/1024/1024:.1f}MB)")
    start_time = time.time()

    print(f"  ⏳ 加载模型 {model_name}...")
    model = WhisperModel(model_name, device="cpu", compute_type=compute_type,
                         cpu_threads=cpu_threads)

    kwargs = {
        "language": language,
        "beam_size": beam_size,
        "vad_filter": vad_filter,
        "condition_on_previous_text": condition_on_previous_text,
    }
    prompt = _build_prompt(episode or {})
    if prompt:
        kwargs["initial_prompt"] = prompt
        print(f"  📝 使用元数据 prompt 提升专有名词识别")

    seg_gen, info = model.transcribe(str(audio_path), **kwargs)
    print(f"  🌐 语言: {info.language} (p={info.language_probability:.2f})，"
          f"音频时长 {info.duration:.0f}s")

    segments = []
    for seg in seg_gen:
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append({"start": float(seg.start), "end": float(seg.end), "text": text})

    # faster-whisper 中文输出无标点，先用 ct-punc 恢复标点再做句级合并
    if asr_cfg.get("punc_restore", True):
        try:
            segments = restore_punctuation(segments, config)
        except Exception as e:
            print(f"  ⚠️  标点恢复失败，继续无标点流程: {e}")

    if asr_cfg.get("sentence_merge", True):
        before = len(segments)
        segments = merge_segments_to_sentences(segments)
        print(f"  🔗 句级合并: {before} 个细粒度分段 → {len(segments)} 个句级分段")

    full_text = "\n".join(s["text"] for s in segments)
    duration = segments[-1]["end"] if segments else (episode.get("duration_seconds", 0) if episode else 0)

    elapsed = time.time() - start_time
    print(f"  ✅ 转录完成，耗时 {elapsed:.0f}秒，共 {len(full_text)} 字，{len(segments)} 个分段")

    return {"text": full_text, "segments": segments, "duration": duration}


def _resolve_device(device_pref: str) -> str:
    """根据配置和实际环境确定 whisper 运行设备"""
    if device_pref and device_pref != "auto":
        return device_pref
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _transcribe_openai_whisper(audio_path: Path, config: dict, episode: dict = None) -> dict:
    """旧版 openai-whisper 本地转录（faster-whisper 不可用时的兜底路径）。"""
    import whisper

    asr_cfg = config.get("asr", {})
    model_name = asr_cfg.get("model", "small")
    device = _resolve_device(asr_cfg.get("device", "auto"))
    language = asr_cfg.get("language", "zh")
    condition_on_previous_text = asr_cfg.get("condition_on_previous_text", True)
    fp16 = asr_cfg.get("fp16", False) if device == "cpu" else asr_cfg.get("fp16", True)

    file_size = audio_path.stat().st_size
    print(f"  🎙️  开始本地转录（Whisper {model_name} / {device}）: {audio_path.name} ({file_size/1024/1024:.1f}MB)")
    start_time = time.time()

    print(f"  ⏳ 加载 Whisper 模型 {model_name}...")
    model = whisper.load_model(model_name).to(device)

    prompt = _build_prompt(episode or {})
    if prompt:
        print(f"  📝 使用元数据 prompt 提升专有名词识别")

    kwargs = {
        "language": language,
        "condition_on_previous_text": condition_on_previous_text,
        "fp16": fp16,
        "verbose": False,
    }
    if prompt:
        kwargs["initial_prompt"] = prompt

    result = model.transcribe(str(audio_path), **kwargs)

    segments = []
    text_parts = []
    for seg in result.get("segments", []):
        text = seg.get("text", "").strip()
        if not text:
            continue
        segments.append({
            "start": float(seg.get("start", 0)),
            "end": float(seg.get("end", 0)),
            "text": text,
        })
        text_parts.append(text)

    if asr_cfg.get("sentence_merge", True):
        segments = merge_segments_to_sentences(segments)

    full_text = "\n".join(s["text"] for s in segments)
    duration = segments[-1]["end"] if segments else (episode.get("duration_seconds", 0) if episode else 0)

    elapsed = time.time() - start_time
    print(f"  ✅ 转录完成，耗时 {elapsed:.0f}秒，共 {len(full_text)} 字，{len(segments)} 个分段")

    return {"text": full_text, "segments": segments, "duration": duration}


def transcribe_audio(audio_path: Path | str, config: dict, episode: dict = None) -> dict:
    """
    根据配置选择 ASR 后端转录音频。
    返回 {"text": str, "segments": list, "duration": float}
    """
    audio_path = Path(audio_path)
    backend = config.get("asr", {}).get("backend", "local").lower()
    if backend == "bailian":
        return _transcribe_bailian(audio_path, config, episode)

    try:
        import faster_whisper  # noqa: F401
        return _transcribe_faster_whisper(audio_path, config, episode)
    except ImportError:
        print("  ⚠️  未安装 faster-whisper，回退到 openai-whisper")
        return _transcribe_openai_whisper(audio_path, config, episode)


# ─────────────────────────────────────────
# 转录缓存读写（独立 JSON 文件）
# ─────────────────────────────────────────

def _get_transcript_cache_path(episode_id: str) -> Path:
    """根据 episode_id 确定转录缓存文件路径"""
    return TRANSCRIPT_CACHE / f"{episode_id}.json"


def load_transcript_cache(episode: dict) -> dict | None:
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
    完整流程：下载 → 本地转录 → 返回结果
    支持从本地缓存恢复（已含完整 transcript + segments）
    转录成功（含缓存命中）后自动删除音频文件释放磁盘空间。
    """
    try:
        # Step 1: 下载音频（YouTube 等受限站点可能需要代理）
        from podauth import get_proxy
        proxy = get_proxy(config) if config else ""
        audio_path = download_audio(episode, proxy=proxy)
        episode["audio_local_path"] = str(audio_path)

        # Step 2: 检查本地转录缓存（用户选择强制重转时跳过）
        cached = None if episode.get("_force_retranscribe") else load_transcript_cache(episode)
        if cached:
            episode["transcript"] = cached.get("transcript", "")
            episode["transcript_segments"] = cached.get("transcript_segments", [])
            episode["audio_duration"] = cached.get("audio_duration", 0)
            episode["_from_cache"] = True
            _cleanup_audio_cache(episode)
            return episode

        # Step 3: 本地转录
        transcript = transcribe_audio(audio_path, config, episode)

        episode["transcript"] = transcript["text"]
        episode["transcript_segments"] = transcript["segments"]
        episode["audio_duration"] = transcript["duration"] or episode.get("duration_seconds", 0)

        # Step 4: 保存转录缓存
        save_transcript_cache(episode)

        # Step 5: 清理音频缓存
        _cleanup_audio_cache(episode)

        return episode
    except Exception as e:
        print(f"  ❌ 处理失败 [{episode.get('title', '')[:30]}]: {e}")
        episode["transcript"] = ""
        episode["transcript_segments"] = []
        episode["error"] = str(e)
        return episode
