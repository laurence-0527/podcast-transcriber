# 変更履歴

本プロジェクトの重要な変更はすべてこのファイルに記録されます。

## 1.0.2 - 2026-08-09

### 新機能

- デフォルトでローカル ASR 構成：faster-whisper（Whisper large-v3、CPU int8）認識 + FunASR ct-punc 句読点復元 + 文字ストリーム単位の文分割により、百煉 Paraformer と同等の文単位・句読点付きセグメントを出力——完全オフライン、API Key 不要
- YouTube リンク対応：動画URLを直接渡すと、yt-dlp で音声ストリームを解決して文字起こし（オプション依存）
- HTTP プロキシ対応：設定の `network.proxy` または環境変数 `HTTPS_PROXY` / `HTTP_PROXY` をメタデータ取得と音声ダウンロードに使用；プロキシ不可時は自動で直接接続にフォールバック

### 修正

- 対話プロンプトで [r]（再文字起こし）を選択した際、内部フラグ `_force_retranscribe` によりキャッシュされた文字起こし結果を確実に破棄するように修正

### ドキュメント

- README（中国語/英語/日本語）に追加：ローカル文字起こしのモデルダウンロード一覧、ハードウェア要件のランク別表、所要時間の目安、および「ローカル vs クラウド：選び方」のマシン構成別のおすすめ
- SKILL.md を v3.5.0 に更新し、新しいローカル構成の内容を反映

### 依存関係

- faster-whisper、funasr、modelscope、torchaudio を追加；ctranslate2==4.6.0 に固定（4.8.0 はセグメンテーションフォールトが発生）
- openai-whisper は不要に（requirements.txt にはコメントとして残置）
- config.example.json を再構成し、新しい `asr.*` キーを追加（backend、model、compute_type、cpu_threads、beam_size、vad_filter、sentence_merge、punc_restore、punc_model など）

## 1.0.1

- 以前のリリース（git 履歴を参照）
