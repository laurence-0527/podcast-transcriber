# 更新日志

本项目的所有重要变更都会记录在此文件中。

## 1.0.2 - 2026-08-09

### 新功能

- 默认本地 ASR 管线：faster-whisper（Whisper large-v3，CPU int8）识别 + FunASR ct-punc 标点恢复 + 字符流句级合并，输出与百炼 Paraformer 相当的句级带标点分段——全程离线，无需任何 API Key
- 支持 YouTube 链接：直接传入视频 URL，经 yt-dlp 解析音频流并转录（可选依赖）
- HTTP 代理支持：`network.proxy` 配置项或 `HTTPS_PROXY` / `HTTP_PROXY` 环境变量，用于元数据抓取与音频下载，代理失效时自动回退直连

### 修复

- 交互提示选择 [r]（重新转录）时，现在会通过内部 `_force_retranscribe` 标志真正丢弃缓存的转录结果

### 文档

- README（中/英/日）新增：本地转录的模型下载清单、硬件分档要求、耗时预估，以及「本地转录 vs 云端转录：怎么选」的按机器配置推荐
- SKILL.md 更新至 v3.5.0，同步本地管线说明

### 依赖

- 新增 faster-whisper、funasr、modelscope、torchaudio；固定 ctranslate2==4.6.0（4.8.0 存在段错误）
- 不再需要 openai-whisper（requirements.txt 中保留注释说明）
- config.example.json 重写，新增 `asr.*` 配置项（backend、model、compute_type、cpu_threads、beam_size、vad_filter、sentence_merge、punc_restore、punc_model 等）

## 1.0.1

- 此前版本（详见 git 历史）
