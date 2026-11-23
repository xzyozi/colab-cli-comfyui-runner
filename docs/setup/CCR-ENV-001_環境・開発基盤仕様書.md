---
title: "環境・開発基盤仕様書"
document_type: "environment_specification"
document_number: "CCR-ENV-001"
document_role: "normative"
ssot_scope: "environment"
status: "draft"
version: "1.4"
created_at: "2026-11-22"
updated_at: "2026-11-23"
author: "xzyozi"
owner: "xzyozi"
purpose: "colab-cli-comfyui-runner の開発・検証・実行環境（ローカル制御端末、Google Colab A100、Google Drive、CI/品質ゲート）について、固定条件と必要構成を定義するため"
related_documents:
  - "ドキュメントガバナンス（相対パス: ../README.md）"
  - "用語集（相対パス: ../../CONTEXT.md）"
  - "CCR-BD-001 基本設計書（相対パス: ../design/CCR-BD-001_基本設計書.md）"
  - "CCR-SETUP-001 環境構築手順書（相対パス: ./CCR-SETUP-001_環境構築手順書.md）"
  - "CCR-OP-001 運用手順書（相対パス: ../how-to/CCR-OP-001_運用手順書.md）"
  - "CCR-TEST-001 テスト計画・結果報告書（相対パス: ../test/CCR-TEST-001_テスト計画・結果報告書.md）"
  - "Python プロジェクト環境構築手順書 (uv版)（相対パス: ./python_setup.md）"
  - "Git Flow 運用規約 ＆ ブランチ自動管理ガイド（相対パス: ./git_flow_and_branch_policy.md）"
  - "ドキュメント検証スクリプト運用（相対パス: ./doc_validation.md）"
  - "Mermaid CI 検証運用（相対パス: ./mermaid_ci_validation.md）"
  - "ミューテーションテスト導入・運用ガイド（相対パス: ./mutation_testing_setup.md）"
---

# 環境・開発基盤仕様書
**Google Colab CLI × ComfyUI（MiniMax H3）動画自動生成パイプライン（colab-cli-comfyui-runner）**

---

## 1. 目的・対象範囲

- **目的**: ローカルPCから `google-colab-cli`（SSH）経由で Google Colab 上の ComfyUI および MiniMax H3 を制御し、スポット実行型で動画を連続生成するための開発・検証・実行環境の制約と基準を定める。
- **対象環境**:
  - ローカル開発・制御環境（Python / `uv` / OpenSSH クライアント）
  - Google Colab GPU 実行環境（A100 40GB / High-RAM スポットVM）
  - Google Drive 永続ストレージ領域（モデル資産・出力動画）
  - GitHub Actions CI/CD 検証環境
- **対象範囲**: プラットフォーム要件、契約・ライセンス制約、言語・ランタイム、ツールチェーン、モデル配置構成、品質ゲート（`QG-001`〜`QG-007`）、秘密情報・再現性方針。
- **対象外**:
  - 24時間365日の常駐Webサーバ・常時待ち受け推論APIとしてのホスティング
  - マルチGPUによるフル精度（BF16）モデルの分散推論
  - MiniMaxホスト型API専用機能（`H3-Regenerate-2K`、`H3-Context-IR`）
  - 個別の環境構築操作手順（手順の正本は `./python_setup.md` 等を参照）

## 2. ハード要件・制約

- **必須プラットフォーム・ハードウェア**:
  - **推論GPU**: NVIDIA A100（VRAM 40GB）単一構成
  - **システムRAM**: 64GB以上（通常は `--high-mem` なしの標準 A100 でも 83.5GB 割り当てられるため標準 A100 を第一候補とし、混雑時は `G4` / `--high-mem` へフォールバック。L4以下は速度不足のため非推奨）
  - **永続ストレージ**: Google Drive 5TB（モデル資産約42.5GBおよび生成動画の保存先）
  - **ローカル制御端末**: Python 3（3.10以上、推奨3.12）、OpenSSH、`ed25519` SSH鍵。Windows環境では `~/.ssh/config` の `ProxyCommand` 内 `-i` に鍵の絶対パスを指定し、`Host *` より上位に記述すること。
- **互換性・契約・法務制約**:
  - **利用契約**: Google AI Pro（月額2,900円 / 月間200 CU / Google Drive 5TB）。月間200 CUは契約更新日にリセットされ、未使用分の翌月繰り越しは不可。
  - **モデルライセンス**: MiniMax H3 Community License を遵守すること（日本国内利用可、非商用利用自由、年商2,000万米ドル未満の商用利用時は帰属表示必須）。
  - **Colab利用規約制約**: 常駐サーバ化は不可。必要時のみ起動し、処理完了または異常終了と同時に停止するスポット実行型であること。
- **ネットワーク・アクセス制約**:
  - ローカルPCとColab VM間の通信は `google-colab-cli` のSSHプロキシおよびSSHポートフォワーディング（例: `localhost:8188`）経由で行う。
  - ComfyUI Web UI・APIは外部公開せずローカルループバック経由でのみ操作する。
- **その他の制約**:
  - Colabセッションには最大24時間連続上限およびアイドル切断（60〜90分）の制約があるため、1回のバッチ実行は2〜4時間以内で完結させる。
  - 月間200 CU枠でのA100稼働目安は約30〜37時間/月（標準設定で月間250〜450本目安）であり、フェーズ1の実測CU消費レートに基づき月次上限を管理する。

## 3. 環境概要

| 環境 | 用途 | プラットフォーム / 実行方式 | 所有者 | 更新・廃止方針 |
| :--- | :--- | :--- | :--- | :--- |
| **ローカル開発・制御** | CLI/APIバッチ投入・SSH接続・開発検証 | ローカルPC（Windows / OpenSSH / Python `uv`） | xzyozi | Git管理および `uv.lock` で同期 |
| **Google Colab 実行** | ComfyUI・MiniMax H3 推論実行 | Google Colab スポットVM（A100 40GB / High-RAM） | xzyozi | 実行都度プロビジョニングし完了後即破棄 |
| **Google Drive 永続領域** | モデル重み（約42.5GB）と生成MP4保管 | Google Drive（5TB契約領域・VMへマウント） | xzyozi | モデル追加・入替時のみ手動/半自動更新 |
| **CI/CD 検証** | 静的解析・型検査・テスト・文書検証 | GitHub Actions（`ubuntu-latest`） | xzyozi | PR/Push時に自動実行（`.github/workflows/`） |

## 4. 適用する環境要素

### 4.1 言語・ランタイム・実行基盤

- **ローカル制御スクリプト**: Python 3.10以上（`pyproject.toml` の `requires-python = ">=3.10"`、`.python-version` は `3.12` を標準指定）。
- **Colab VM実行基盤**: Google Colab Linux環境（A100 40GB / High-RAM）、ComfyUI（`Comfy-Org/ComfyUI`）および `Comfy-Org/comfy-cli`。
- **推論高速化**: `thu-ml/SageAttention` および TeaCache を利用してアテンション計算・ステップ間キャッシュを高速化する。

### 4.2 ツールチェーン・依存関係

- **ローカル開発・依存管理**: `uv` を正本とし、`pyproject.toml` と `uv.lock` で依存を管理する。開発用依存は `[dependency-groups] dev`（`pytest`, `pytest-cov`, `ruff`, `mypy`）を `uv sync --group dev` で同期する。
- **VM・SSH制御**: `google-colab-cli`（Google公式、ローカル端末へ `uv tool install google-colab-cli` 等で導入する外部CLI）を使用し、VM確保（`colab new --gpu A100 --high-mem`）、SSHプロキシ、Driveマウント（`colab drivemount`）、CU・セッション確認（`colab usage` / `colab sessions`）、停止（`colab stop` / `--rm`）を行う。
- **Colab側環境構築**: `comfy-cli`（`uv` 連携）でComfyUI環境を高速構築・起動し、Google Driveへのシンボリックリンクによりモデル再ダウンロードを回避する（VM起動から待ち受け完了まで3分以内を目標）。
- **動画生成モデルスタック（MiniMax H3・合計約42.5GB・Drive永続化対象）**:

| 役割 | ファイル名 | 容量目安 | Drive配下の格納先 |
| :--- | :--- | :--- | :--- |
| 拡散モデル本体（T2V/FL2V用） | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 約21.0 GB | `models/diffusion_models/` |
| 拡散モデル本体（複数参照用） | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 約21.0 GB | `models/diffusion_models/` |
| エンコーダ | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 約15.7 GB | `models/text_encoders/` |
| Video VAE | `minimax_h3_video_vae_int8_convrot.safetensors`（または `fp16` 版） | 約2.81 GB（`fp16`: 約5.21 GB） | `models/vae/` |
| Audio VAE | `minimax_h3_audio_vae_fp32.safetensors` | 約0.61 GB | `models/vae/` |
| 高速化LoRA | `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | 約1.96 GB | `models/loras/` |

### 4.3 ビルド・パッケージング・配布

- **ビルド定義**: `pyproject.toml`（`setuptools.build_meta`）でパッケージ `colab-cli-comfyui-runner` を定義する。
- **配布方針**: 個人管理リポジトリ（`colab-cli-comfyui-runner`）からローカル端末へクローンして実行する形態とし、外部PyPI公開や常駐コンテナレジストリ配布は行わない。

### 4.4 CI/CD・品質ゲート

本プロジェクトにおける品質ゲートの正本を下表に定義する。設計書・テスト文書からはゲートIDで参照し、実行コマンドや合格基準を再掲しない。

| ゲートID | 検証種別 | 実行コマンドまたはワークフロー | 適用条件 | 合格基準 |
| :--- | :--- | :--- | :--- | :--- |
| `QG-001` | 静的解析（Lint） | `uv run --no-sync ruff check .`（`.github/workflows/ci-python.yml`） | `main`/`develop` へのPushおよびPR | 指摘0件で終了すること |
| `QG-002` | フォーマット検査 | `uv run --no-sync ruff format --check .`（`.github/workflows/ci-python.yml`） | `main`/`develop` へのPushおよびPR | フォーマット差分なしで終了すること |
| `QG-003` | 型検査 | `uv run --no-sync mypy .`（`.github/workflows/ci-python.yml`） | `main`/`develop` へのPushおよびPR | 型エラー0件で終了すること |
| `QG-004` | 単体テスト | `uv run --no-sync pytest`（`.github/workflows/ci-python.yml`） | `main`/`develop` へのPushおよびPR | 全件パス（またはテスト未収集終了コード5の許容条件） |
| `QG-005` | ドキュメント規約検証 | `python .github/scripts/validate_docs.py docs README.md`（`.github/workflows/validate-docs.yml`） | `docs/**/*.md` または `README.md` 変更時のPush/PR | エラー0件（終了コード0）であること |
| `QG-006` | Mermaid構文・描画検証 | `node .github/scripts/validateMermaidMarkdown.mjs`（`.github/workflows/validate-mermaid.yml`） | `docs/**/*.md` または `README.md` 変更時のPush/PR | 全MermaidブロックのSVGレンダリング成功 |
| `QG-007` | ミューテーションテスト | `uv run mutmut run`（`.github/workflows/mutation-pr.yml` / `mutation-nightly.yml`） | `pyproject.toml` に `[tool.mutmut]` を有効化した場合（Linux限定） | 初期は可視化のみ（レポート出力完了・ハードゲート対象外） |

### 4.5 設定・秘密情報

- **Google OAuth認証情報**: 初回認証は必ず対話型ターミナルで実施し、認証情報はローカル端末のユーザー領域に保持する。リポジトリへのコミットは禁止する。
- **SSH鍵**: `ed25519` 鍵ペアをローカル端末に保持し、秘密鍵・個人環境固有パスをコードやドキュメントに埋め込まない。
- **環境変数・設定ファイル**: `.env` / `.env.*` / `*.env` は `.gitignore` で除外し、機密値を標準出力・ログ・例外メッセージへ出力しない。

### 4.6 再現性・サプライチェーン

- **依存固定**: ローカルPython環境は `uv.lock` および `.python-version`（`3.12`）によりバージョンを固定する。
- **モデル資産の一貫性**: モデルファイルはGoogle Drive上の定位置に永続化し、Colab VM起動時にシンボリックリンクを張ることで実行ごとのバージョン揺れと再ダウンロードを排除する。
- **ワークフロー定義の固定**: ComfyUI GUIで動作確認後に `Save (API Format)` で出力したテンプレートJSON（`workflows/<model_name>/workflow_api.json`）をGit管理する。

## 5. 検証方法

| 確認項目 | 確認方法 | 合格条件 | 証跡 |
| :--- | :--- | :--- | :--- |
| ローカルPython・品質ツール動作 | `uv sync --group dev` 後に `QG-001`〜`QG-004` を実行 | 全ゲートがエラー0件で終了すること | CIワークフロー実行履歴・ローカル実行結果 |
| ドキュメント・Mermaid整合 | `QG-005` および `QG-006` のローカル実行またはPR CI実行 | エラー0件で終了すること | GitHub Actions `Validate docs` / `Validate Mermaid` ログ |
| Colab接続・SSH設定 | `colab usage`・`colab sessions` および `colab new --gpu A100 --high-mem`、`colab drivemount` 接続確認 | SSH接続・Driveマウント・ポート転送 `localhost:8188` が疎通すること | フェーズ1実測ログ（`docs/logs/` 等へ記録予定） |
| CU残高・実測レート把握 | 実行前後の `colab usage` 差分計測（フェーズ1） | 実測CU/時と起動〜推論所要時間が記録されること | フェーズ1ベンチマーク記録 |

## 6. 例外・適用差異

| ID | 対象 | 標準との差異 | 理由・制約 | 影響 | 代替対応 | 解除条件 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `EX-001` | ローカルWindows環境での `mutmut` 実行 | Windowsローカルで `QG-007` を直接実行しない | `mutmut` v3 は `os.fork` に依存するためLinux必須 | ローカル単体ではミューテーション計測不可 | GitHub Actions（`ubuntu-latest`）またはWSL2で実行する | `mutmut` のWindowsネイティブ対応または完全CI移行時 |
| `EX-002` | Colab環境での依存解決 | `uv.lock` 完全同一環境ではなく Colab 側 `comfy-cli`（`uv`連携）を使用 | Colab VMはGPUドライバ・プリインストールCUDAに依存するため | ローカルとColabでPythonパッケージ集合が異なる | Colab側セットアップスクリプト（`setup.sh`）で構築手順を固定化する | コンテナ固定イメージ実行へ移行した場合 |

## 7. 関連文書

- **構築手順**: [CCR-SETUP-001 環境構築手順書](./CCR-SETUP-001_環境構築手順書.md)、[Python プロジェクト環境構築手順書 (uv版)](./python_setup.md)
- **設計書**: [CCR-BD-001 基本設計書](../design/CCR-BD-001_基本設計書.md)、[CCR-DD-001 詳細設計書](../design/CCR-DD-001_詳細設計書.md)、[CCR-DS-001 データ構造仕様書](../design/CCR-DS-001_データ構造仕様書.md)
- **運用・テスト**: [CCR-OP-001 運用手順書](../how-to/CCR-OP-001_運用手順書.md)、[CCR-TEST-001 テスト計画・結果報告書](../test/CCR-TEST-001_テスト計画・結果報告書.md)
- **ガバナンス**: [ドキュメントガバナンス](../README.md)、[用語集](../../CONTEXT.md)
- **運用・検証ガイド**: [Git Flow 運用規約](./git_flow_and_branch_policy.md)、[ドキュメント検証スクリプト運用](./doc_validation.md)、[Mermaid CI 検証運用](./mermaid_ci_validation.md)、[ミューテーションテスト導入・運用ガイド](./mutation_testing_setup.md)

## 8. 作成完了チェック

- [x] `document_role`、`ssot_scope`、`status`、`owner`をFrontmatterへ設定した。
- [x] 目的、対象範囲、対象外、ハード要件・制約を記載した。
- [x] 必要な環境要素だけを選択し、不要な項目を無理に追加していない。
- [x] 品質ゲートを採用する場合は、ゲートID・実行方法・適用条件・合格基準を4.4に記載した。
- [x] 検証方法と合格条件を記載した。
- [x] 標準から外れる場合は、理由・影響・代替対応・解除条件を記録した。
- [x] セットアップ手順書、設計書、運用・テスト文書への参照を設定した。
- [x] 秘密情報や利用者固有の認証情報を記載していない。

## 9. 改訂履歴 (Change Log)

| 版数 | 改訂日 | 変更者 | 変更内容・変更理由 (Why) |
| :--- | :--- | :--- | :--- |
| Rev.1.0 | 2026-11-22 | xzyozi | 要件定義書 Ver 2.0 およびリポジトリ実態に基づき初版作成 |
| Rev.1.1 | 2026-11-22 | xzyozi | `google-colab-cli` コマンド表記（`colab new`・`colab drivemount`・`colab sessions`）を実態に整合 |
| Rev.1.2 | 2026-11-23 | xzyozi | `google-colab-cli` を `uv tool install` 等で導入する外部CLIである旨を明記 |
| Rev.1.3 | 2026-11-23 | xzyozi | 公式 `Comfy-Org/MiniMax-H3` の実測モデルファイル名・容量表記へ整合 |
| Rev.1.4 | 2026-11-23 | xzyozi | A100 標準形状（40GB / RAM 83.5GB）を第一候補とし `--high-mem` 枠枯渇を回避する方針へ整合 |
