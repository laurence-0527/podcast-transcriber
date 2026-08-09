# Changelog

All notable changes to this project will be documented in this file.

## 1.0.2 - 2026-08-09

### Features

- Local ASR pipeline by default: faster-whisper (Whisper large-v3, CPU int8) recognition + FunASR ct-punc punctuation restoration + character-stream sentence merging, producing sentence-level punctuated segments on par with Bailian Paraformer — fully offline, no API key required
- YouTube support: pass a YouTube video URL directly; the audio stream is resolved via yt-dlp (optional dependency)
- HTTP proxy support: `network.proxy` in config or `HTTPS_PROXY` / `HTTP_PROXY` env vars, used for metadata fetching and audio download with automatic fallback to direct connection

### Fixes

- The `[r]` (re-transcribe) prompt choice now truly discards the cached transcript via an internal `_force_retranscribe` flag

### Documentation

- README (zh/en/ja): added local transcription model downloads, hardware tiers, time estimates, and a "Local vs Cloud: How to Choose" guide with per-machine recommendations
- SKILL.md updated to v3.5.0 to reflect the new local pipeline

### Dependencies

- Added faster-whisper, funasr, modelscope, torchaudio; pinned ctranslate2==4.6.0 (4.8.0 segfaults)
- openai-whisper no longer required (kept commented in requirements.txt)
- config.example.json reworked with the new `asr.*` keys (backend, model, compute_type, cpu_threads, beam_size, vad_filter, sentence_merge, punc_restore, punc_model, ...)

## 1.0.1

- Previous release (see git history)
