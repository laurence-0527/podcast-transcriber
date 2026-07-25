# Podcast Transcriber

**[中文](README.md) | [English](README_EN.md)**

ポッドキャストを、検索可能で速読可能なナレッジベースに変える。

## なぜこのツールを作ったか

私は数十の高品質ポッドキャストを購読しています——AI最前線、ビジネスインサイト、技術深掘りインタビュー、投資方法論。各エピソードは1〜3時間、毎週十数本が更新される。全部聴くのは不可能ですが、見逃すのは惜しい。

そこで自分に合ったリズムを見つけました：**まず文字起こし、次に要約を速読、最後に選んで聴く。**

1. **一括文字起こし**：溜まったエピソードのURLをツールに渡すだけで、ダウンロード・音声認識・AI要約が全自動
2. **速読スクリーニング**：構造化された要約（核心观点＋名言＋トピック索引）を2分で読み、1時間かけて聴く価値があるか判断
3. **深聴**：本当に興味のあるコンテンツは、トピック索引を使って最も関連するセグメントに直接ジャンプ

これは本質的に **「まず広く、次に深く」という学習法**です——AIが1時間の音声を2分の読書に圧縮し、あなたの注意力を本当に価値の高い情報だけに集中させます。ポッドキャストは「聴いたら忘れる」一過性のストリームではなく、検索可能・復習可能・引用可能な個人の知的資産になります。

このツールは、その方法論の自動化実装です。

## 機能

- **ワンコマンドで全工程**：URL → メタデータ取得 → 音声ダウンロード → 音声認識 → AI要約＋トピック分割 → Markdown + PDF
- **プラットフォーム非依存**：JSON-LD / OpenGraph / HTML5 audio / 直接音声URL — 特定プラットフォームに縛られない
- **深いAI要約**：単純なまとめではなく、論点抽出＋方法論フレームワーク＋名言（タイムスタンプ付き）＋推薦図書
- **トピック分割索引**：意味的な話題の切り替わりで自動分割、各セグメントに時間範囲と概要を付与
- **`--no-fulltext` モード**：要約と索引のみ出力、逐字テキストを省略——「速読スクリーニング」に最適

## クイックスタート

### 1. 依存関係のインストール

```bash
pip install requests reportlab markdown openai
```

### 2. API Key の設定

```bash
cp config/config.example.json config/config.json
# config.json を編集して DashScope API Key を入力、または環境変数を設定
export DASHSCOPE_API_KEY=sk-your-key-here
```

API Key の取得：[Alibaba Cloud DashScope コンソール](https://dashscope.console.aliyun.com/apiKey)

### 3. 文字起こし

```bash
# ポッドキャストのWebページから自動取得
python main.py https://example.com/podcast/episode-42

# 直接音声URL（タイトルを手動指定）
python main.py https://cdn.example.com/audio/ep42.mp3 --title "AIの未来"

# メタデータのみ取得（プレビュー用、API Key不要）
python main.py --dry https://example.com/podcast/episode-42

# 要約とトピック索引のみ、逐字テキストなし（速読モード）
python main.py --no-fulltext https://example.com/podcast/episode-42
```

出力は `output/` ディレクトリに保存されます。エピソードごとにフォルダが作成され、MarkdownとPDFが含まれます。

## 出力構造

50分のエピソードの場合：

```
# エピソードタイトル
> 番組名 | ソース | 公開日 | 再生時間

## 📋 AI要約
### 一言まとめ
### コアコンテンツ（主要論点 / 方法論 / 重要データ / 名言）
### 推薦図書（番組内で言及 + テーマ別推薦）
### キーワード

## 📑 全文（トピック別）        ← --no-fulltext 時は省略
### 📍 00:00 - 12:35 | トピックタイトル
> セグメント概要
[00:00] 逐字テキスト...
```

## 仕組み

```text
URL入力
  │
  ├─ podfetch.py      → Webスクレイピング：JSON-LD / OpenGraph / HTML5 audio
  ├─ podtranscribe.py → 音声ダウンロード → DashScope非同期ASR → タイムスタンプ分割
  ├─ podsummarize.py  → LLM要約 + トピック分割 + 推薦図書
  └─ md2pdf.py        → Markdown → PDF（CJK対応）
  │
  └─ output/<日付_番組名_タイトル>/  ← MD + PDF
```

## 対応ソース

特定のプラットフォームに依存しません。以下のいずれかが含まれるページなら自動認識：

- JSON-LD（`PodcastEpisode` / `AudioObject`）
- OpenGraph（`og:audio`）
- HTML5 `<audio>` タグ
- 直接音声URL（.mp3 / .m4a / .wav / .ogg / .aac / .flac / .opus）

動作確認済み：小宇宙（Xiaoyuzhou）、Apple Podcasts、Spotify（Web版）、TWiT、Libsynホスティング等。

## よくある質問

**Q: 1エピソードの処理時間は？**
A: ダウンロード1〜3分 + 非同期ASR 5〜15分（音声の長さとサーバー負荷による）。1時間のポッドキャストで合計約10〜20分。

**Q: 対応言語は？**
A: DashScope ASRは中国語・英語・日本語等多言語対応。要約はデフォルトで中国語出力。

**Q: 一括処理できる？**
A: 可能。シンプルなシェルループで：
```bash
for url in $(cat urls.txt); do python main.py --no-fulltext "$url"; done
```

## データフロー

1. **メタデータ取得**：ポッドキャストWebページの公開HTMLからタイトル・音声URLを抽出（ローカル処理）
2. **音声ダウンロード**：ポッドキャストCDNからローカル `audio_cache/` に保存
3. **音声認識**：音声を **Alibaba Cloud DashScope** にアップロードし非同期認識
4. **AI要約**：テキストを **DashScope LLM API**（デフォルト：qwen-plus）に送信
5. **ローカル出力**：Markdown + PDF を `output/` に保存

ステップ3・4でデータがAlibaba Cloudサーバーに送信されます。コンテンツを処理する権限があることをご確認ください。

## 免責事項

本ツールは個人の学習・研究目的専用です：

- ポッドキャスト音声及び文字起こしテキストの著作権は原作者/制作元に帰属します。商業利用・再配布は禁止
- プラットフォームの利用規約を遵守してください。プログラム的ダウンロードが禁止されている場合は使用しないでください
- 全文テキストは個人の復習・学習メモ用です。引用時は出典を明記してください
- 不適切な使用により生じた一切の法的責任は使用者が負います

## License

MIT
