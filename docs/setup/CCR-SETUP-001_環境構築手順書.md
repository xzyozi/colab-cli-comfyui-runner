---
title: "環境構築手順書"
document_type: "environment_setup_procedure"
document_number: "CCR-SETUP-001"
document_role: "procedure"
ssot_scope: "environment_setup"
status: "draft"
version: "1.3"
created_at: "2026-11-22"
updated_at: "2026-11-23"
author: "xzyozi"
owner: "xzyozi"
purpose: "フェーズ0（ローカル開発・認証・SSH鍵準備および約42.5GBモデルのGoogle Drive事前配置）を構築・検収する標準手順を定義するため"
related_documents:
  - "ドキュメントガバナンス（相対パス: ../README.md）"
  - "用語集（相対パス: ../../CONTEXT.md）"
  - "CCR-ENV-001 環境・開発基盤仕様書（相対パス: ./CCR-ENV-001_環境・開発基盤仕様書.md）"
  - "Python プロジェクト環境構築手順書 (uv版)（相対パス: ./python_setup.md）"
  - "CCR-BD-001 基本設計書（相対パス: ../design/CCR-BD-001_基本設計書.md）"
  - "CCR-DS-001 データ構造仕様書（相対パス: ../design/CCR-DS-001_データ構造仕様書.md）"
  - "CCR-OP-001 運用手順書（相対パス: ../how-to/CCR-OP-001_運用手順書.md）"
---

# 環境構築手順書
**フェーズ0：ローカル準備・対話型OAuth認証・SSH設定・モデル資産のGoogle Drive事前配置**

---

## 1. 目的・対象範囲

- **目的**: 高価な A100 GPU（約 5.5 CU/時目安）上で約 42.5 GB のモデルダウンロード待ちを行わないよう、ローカル制御基盤・認証・SSH鍵・Google Drive 永続領域を事前に構築する。
- **対象環境**: ローカル開発・制御端末（Windows / OpenSSH / Python `uv`）および Google Drive（5TB 契約領域）。
- **対象者**: リポジトリ操作者（xzyozi）。
- **対象外**:
  - Python 仮想環境および `uv sync --group dev` の基本構築手順（[Python プロジェクト環境構築手順書 (uv版)](./python_setup.md) を正本として委譲）
  - 品質ゲートのコマンド・合格基準（[CCR-ENV-001 環境・開発基盤仕様書](./CCR-ENV-001_環境・開発基盤仕様書.md) 4.4 を正本として委譲）
  - フェーズ1 実機ベンチマークおよびフェーズ2 バッチ運用手順（[CCR-OP-001 運用手順書](../how-to/CCR-OP-001_運用手順書.md) を正本として委譲）

## 2. 前提条件・注意事項

- **参照する環境仕様**: [CCR-ENV-001 環境・開発基盤仕様書](./CCR-ENV-001_環境・開発基盤仕様書.md)
- **必要なアカウント・権限**: Google AI Pro 契約済み Google アカウント（Google Colab および Google Drive 5TB 利用権限）。
- **必要な資材・アクセス**: Python 3.10 以上（推奨 3.12）、`uv`、OpenSSH クライアント、`google-colab-cli`、Hugging Face モデル配布元へのアクセス。
- **安全上の注意**:
  - Google OAuth 初回認証は**必ず対話型ターミナル**から直接実行すること。非対話シェル（自動化エージェントやパイプ経由スクリプト）で実行すると `"Malformed auth code"` で失敗する。
  - SSH 秘密鍵・OAuth トークン・ローカル端末固有の絶対パスを Git 管理ファイルやログへ記載・コミットしないこと。
  - MiniMax H3 Community License（日本国内利用可・非商用自由・年商 2,000 万米ドル未満の商用利用時は帰属表示必須）を遵守すること。
- **中断条件**:
  - `colab usage` で契約または残高が認識できない場合は後続の VM 起動手順へ進まず認証状態を再確認する。
  - Google Drive の空き容量が 100 GB 未満（モデル約 42.5 GB＋生成 MP4 領域不足）の場合は不要ファイルを整理してからモデル配置を行う。

## 3. 構築手順

### 3.1 ローカル Python・CLI・認証・SSH 準備

| No. | 操作・設定 | 期待結果 | 異常時の対応 |
| :---: | :--- | :--- | :--- |
| 1 | [Python プロジェクト環境構築手順書 (uv版)](./python_setup.md) に従い、`uv sync --group dev` でローカル仮想環境を構築する。 | エラーなく `.venv` が作成され、ローカル Python 依存が同期される。 | Python バージョン（`.python-version`: `3.12`）と `uv` 導入状態を確認する。 |
| 2 | 外部 CLI ツールとして `uv tool install google-colab-cli`（または `pip install google-colab-cli`）を実行し、`colab --help` が表示されることを確認する。 | `colab` コマンドが PATH から実行可能になる。 | PATH 未反映時は `uv tool update-shell` を実行し、ターミナルを再起動する。 |
| 3 | **対話型ターミナル**（PowerShell 等）上で `colab usage` を実行する。表示された認証 URL をブラウザで開き、Google AI Pro 契約アカウントでログイン・許可して、表示された認証コードをターミナルへ貼り付ける。（※ `colab auth` は起動済みVM内へGCP権限を渡す別コマンドであり、CLI初回ログインには `colab usage` を使用する） | 認証トークンが OS 標準キーリング（Windows 資格情報マネージャ：`google-colab-cli`）へ保存され、`colab usage` が CU 残高を応答する。 | `"Malformed auth code"` 発生時は、非対話シェルを避け、前後に空白・改行が入らないよう再実行して新しい認証コードを取り直す。 |
| 4 | OpenSSH クライアント（`ssh` / `scp`）が利用可能か確認し、`ed25519` 鍵が未作成の場合は `ssh-keygen -t ed25519` で `~/.ssh/` に鍵を生成する。 | `ssh -V` が応答し、秘密鍵と公開鍵（`.pub`）が生成される。 | Windows で `ssh`/`scp` 未導入時は「Windows のオプション機能」または Git for Windows 等で OpenSSH クライアントを導入し PATH を通す。 |
| 5 | （フェーズ1手動GUI操作向け）`~/.ssh/config` に Colab 用設定を記述する場合、Windows 環境では `ProxyCommand` 内の `-i` に鍵の絶対パスを指定し、**必ず `Host *` ブロックより上位**に配置する。 | 手動 `ssh` 実行時に `Host *` のデフォルト設定へ吸われず鍵が解決される。 | フェーズ2（`run_batch.py`）は動的 `ProxyCommand` をCLI引数で渡すため、環境変数 `CCR_SSH_KEY_PATH` または既定鍵パスを確認する。 |
| 6 | 無課金確認として `colab sessions` および `colab usage` を実行する。 | エラーなくセッション一覧と CU 残高が返る（2回目以降はブラウザ認証不要）。 | OAuth 期限切れ時は手順3を再実施する。 |

```powershell
# フェーズ0 ローカル導入・OAuth認証コマンド例（ご自身の対話型 PowerShell で実行）
uv sync --group dev
uv tool install google-colab-cli
colab --help

# 初回 OAuth 認証（表示された URL をブラウザで開き、認証コードを貼り付ける）
colab usage
colab sessions

# OpenSSH 確認と ed25519 鍵生成（未作成の場合）
ssh -V
ssh-keygen -t ed25519
```

### 3.2 約 42.5 GB モデルの Google Drive 事前配置手順

A100 ランタイム上でモデルをダウンロードすると CU を浪費するため、以下のいずれかの省コスト経路で事前に Google Drive 上へ配置する。

- **経路A（推奨・Colab CPU ランタイム利用）**: GPU を指定しない通常 CPU ランタイムで VM を起動して Drive をマウントし、`huggingface-cli`（`hf download`）で `<drive_root>/models/` 配下へ直接ダウンロードする。
- **経路B（ローカル経由・Google Drive デスクトップ同期等）**: ローカル回線でダウンロードし、Google Drive 同期クライアントまたは Web UI 経由で `<drive_root>/models/` 配下へアップロードする。

具体的な配置先ディレクトリ構成（`<drive_root>` 既定値は `ComfyUI`）と格納先は [CCR-DS-001 データ構造仕様書](../design/CCR-DS-001_データ構造仕様書.md) 1.3 および [CCR-ENV-001 環境・開発基盤仕様書](./CCR-ENV-001_環境・開発基盤仕様書.md) 4.2 を正本とする。

```bash
# 例: Colab CPU ランタイム等で Drive マウント後に実行する配置確認・DLイメージ
# (実際の格納ディレクトリ名: /content/drive/MyDrive/ComfyUI/models/)
mkdir -p /content/drive/MyDrive/ComfyUI/models/diffusion_models
mkdir -p /content/drive/MyDrive/ComfyUI/models/text_encoders
mkdir -p /content/drive/MyDrive/ComfyUI/models/vae
mkdir -p /content/drive/MyDrive/ComfyUI/models/loras
mkdir -p /content/drive/MyDrive/ComfyUI/input
mkdir -p /content/drive/MyDrive/ComfyUI/output
```

| No. | 操作・設定 | 期待結果 | 異常時の対応 |
| :---: | :--- | :--- | :--- |
| 1 | 経路A（CPUランタイム）で**ご自身の対話型ターミナル**から `colab new -s ccr-stage` → `colab drivemount -s ccr-stage` を実行し（Drive 未認可時は端末に認可 URL と `Press Enter` 待ちが表示されるため、ブラウザで許可後に Enter を押す）、その後 `scripts/stage_minimax_h3.sh` を転送・実行（または経路Bでローカル経由）して `Comfy-Org/MiniMax-H3` から必要モデル群を上記サブディレクトリへ配置する。 | 必須5ファイル（Ref2VA併用時は6ファイル）が所定ディレクトリに保存される。 | 転送中断時は再実行（`curl -C -` によるレジューム対応）し、0バイトや `.part` ファイルを残さない。非対話環境で `drivemount` が待機状態になった場合は `colab stop -s ccr-stage` 後に対話型ターミナルでやり直す。 |
| 2 | 経路Aで CPU ランタイムを起動した場合は、配置完了直後に必ず `colab stop` および `colab sessions` で停止を確認する。 | アクティブセッションが空になる。 | 残存時は即座に `colab stop` を再実行する。 |
| 3 | 配置した全ファイルについて、次章「4. 構築後の確認」のパス・サイズ検収リストと照合する。 | 全ファイルのパスと容量目安が一致する。 | 不一致ファイルは再取得して差し替える。 |

## 4. 構築後の確認（フェーズ0 完了検収）

| 確認項目 | 確認方法 | 合格条件 | 証跡 |
| :--- | :--- | :--- | :--- |
| ローカル品質ゲート環境 | [CCR-ENV-001](./CCR-ENV-001_環境・開発基盤仕様書.md) 4.4 の `QG-001`〜`QG-005` を実行 | エラー0件で終了すること | CIログまたはローカル実行ログ |
| OAuth / CLI 正常性 | `colab usage` および `colab sessions` を実行 | エラーなく残高と空セッション一覧が返ること | コマンド出力確認 |
| SSH 鍵準備 | 鍵ファイル実在と `ProxyCommand` 用 `-i` パス確認 | `ed25519` 鍵が存在し読み取り可能であること | ローカル確認メモ（鍵パス非公開） |
| 拡散モデル本体（T2V/FL2V） | `<drive_root>/models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 存在し、容量が約 21.0 GB であること | `ls -lh` 出力 |
| 拡散モデル本体（Ref2VA・利用時） | `<drive_root>/models/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 利用する場合に存在し、約 21.0 GB であること | `ls -lh` 出力 |
| エンコーダ | `<drive_root>/models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 存在し、容量が約 15.7 GB であること | `ls -lh` 出力 |
| Video VAE | `<drive_root>/models/vae/minimax_h3_video_vae_int8_convrot.safetensors`（または `fp16` 版） | 存在し、容量が約 2.81 GB（`fp16` 版は約 5.21 GB）であること | `ls -lh` 出力 |
| Audio VAE | `<drive_root>/models/vae/minimax_h3_audio_vae_fp32.safetensors` | 存在し、容量が約 0.61 GB であること | `ls -lh` 出力 |
| 高速化LoRA | `<drive_root>/models/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | 存在し、容量が約 1.96 GB であること | `ls -lh` 出力 |

## 5. 更新・再構築・削除

- **更新**: モデル差し替えや量子化版更新時は、`workflows/<model_name>/model_manifest.json` の `required_drive_assets` を更新した上で Drive 側へ新規重みを配置する。
- **削除・撤去**: 旧モデルは新モデルの実機検収完了後に Drive 側から削除する。`output/` の生成動画はローカル取得またはバックアップ後に整理する。

## 6. トラブル・例外

| ID | 対象手順 | 症状・差異 | 原因・制約 | 対応 | 再発防止・解除条件 |
| :---: | :--- | :--- | :--- | :--- | :--- |
| `EX-SETUP-001` | 3.1 手順3 | `"Malformed auth code"` で認証失敗 | 非対話シェル実行、コード期限切れ、または空白・改行混入 | 対話型ターミナルから `colab usage` を再実行し新しいコードを取得（壊れたトークン残留時は OS キーリングの `google-colab-cli` または `~/.config/colab-cli/token.json` を削除して再認証） | 初回認証成功・OSキーリング保存で解消 |
| `EX-SETUP-002` | 3.1 手順5 | Windows で `Permission denied (publickey)` | `~/.ssh/config` の `Host *` より下に Colab 設定がある、相対パス指定、または SSH 接続ユーザー名に `root`（`root@colab` / `User root`）が未指定でローカルOSユーザー名になってしまう | Colab ブロックを `Host *` より上位に移動し `User root` と絶対パスを指定（フェーズ2および手動接続でも `root@colab` と `-i` を指定） | `root` ユーザー指定と設定見直し完了で解消 |
| `EX-SETUP-003` | 3.2 手順1 | `colab drivemount` がタイムアウト・ハング、または `ValueError: mount failed` でも終了コード `0` で抜ける | `google-colab-cli` が Drive 認可 URL 表示後に `Press Enter` を待機するため非対話では進めない。また VM 側 `drive.mount` の 120 秒タイムアウト等で失敗しても `colab` CLI 自体は終了コード `0` を返す場合がある | 対話型 PowerShell で実行し、ブラウザ認可後は速やかに（120秒以内目安で）Enter を押す。必ず `Mounted at /content/drive` 表示または `/content/drive/MyDrive` の存在を確認し、失敗時は同一セッションで `colab drivemount -s <session>` を再実行する | 対話端末での認可完了と `/content/drive/MyDrive` 実在確認で解消 |

## 7. 関連文書

- **環境仕様**: [CCR-ENV-001 環境・開発基盤仕様書](./CCR-ENV-001_環境・開発基盤仕様書.md)、[Python プロジェクト環境構築手順書 (uv版)](./python_setup.md)
- **設計書**: [CCR-BD-001 基本設計書](../design/CCR-BD-001_基本設計書.md)、[CCR-DS-001 データ構造仕様書](../design/CCR-DS-001_データ構造仕様書.md)
- **運用・テスト**: [CCR-OP-001 運用手順書](../how-to/CCR-OP-001_運用手順書.md)、[CCR-TEST-001 テスト計画・結果報告書](../test/CCR-TEST-001_テスト計画・結果報告書.md)

## 8. 作成完了チェック

- [x] `document_role`、`ssot_scope`、`status`、`owner`をFrontmatterへ設定した。
- [x] 参照する環境仕様、目的、対象環境、前提条件を記載した。
- [x] 各手順に操作内容、期待結果、異常時の対応を記載した。
- [x] 構築後の確認方法と合格条件を記載した。
- [x] 更新・再構築・削除は必要な場合だけ記載した。
- [x] 標準から外れる場合は、原因・対応・解除条件を記録した。
- [x] 秘密情報や利用者固有の認証情報を記載していない。

## 9. 改訂履歴 (Change Log)

| 版数 | 改訂日 | 変更者 | 変更内容・変更理由 (Why) |
| :--- | :--- | :--- | :--- |
| Rev.1.0 | 2026-11-22 | xzyozi | フェーズ0のローカル準備・OAuth/SSH・Drive事前配置手順として初版作成 |
| Rev.1.1 | 2026-11-23 | xzyozi | `uv tool install google-colab-cli` の導入手順、対話型 OAuth 認証手順、OpenSSH 確認手順を追記 |
| Rev.1.2 | 2026-11-23 | xzyozi | 公式 `Comfy-Org/MiniMax-H3` 実ファイル名および `scripts/stage_minimax_h3.sh` によるCPU配置手順を反映 |
| Rev.1.3 | 2026-11-23 | xzyozi | `colab drivemount` 初回認可時の対話型 URL 承認・`Press Enter` 待機および `EX-SETUP-003` を追記 |
