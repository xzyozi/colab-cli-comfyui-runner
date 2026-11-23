---
title: "運用手順書"
document_type: "operation_procedure"
document_number: "CCR-OP-001"
document_role: "procedure"
ssot_scope: "operation"
status: "draft"
version: "1.2"
created_at: "2026-11-22"
updated_at: "2026-11-23"
author: "xzyozi"
owner: "xzyozi"
purpose: "フェーズ1（GUIベンチマーク・テンプレート確定）、フェーズ2（定常バッチ運用・月次CU管理・モデル追加）、および障害復旧ランブックの操作手順を定義するため"
related_documents:
  - "ドキュメントガバナンス（相対パス: ../README.md）"
  - "用語集（相対パス: ../../CONTEXT.md）"
  - "CCR-ENV-001 環境・開発基盤仕様書（相対パス: ../setup/CCR-ENV-001_環境・開発基盤仕様書.md）"
  - "CCR-SETUP-001 環境構築手順書（相対パス: ../setup/CCR-SETUP-001_環境構築手順書.md）"
  - "CCR-BD-001 基本設計書（相対パス: ../design/CCR-BD-001_基本設計書.md）"
  - "CCR-DD-001 詳細設計書（相対パス: ../design/CCR-DD-001_詳細設計書.md）"
  - "CCR-DS-001 データ構造仕様書（相対パス: ../design/CCR-DS-001_データ構造仕様書.md）"
  - "CCR-TEST-001 テスト計画・結果報告書（相対パス: ../test/CCR-TEST-001_テスト計画・結果報告書.md）"
---

# 運用手順書
**フェーズ1 実機ベンチマーク・フェーズ2 定常バッチ運用・障害復旧ランブック**

---

## 1. 目的・対象範囲

- **目的**: Google Colab スポットVM（A100 40GB / High-RAM）を用いた動画生成パイプラインについて、実機ベンチマーク計測から定常無人バッチ運用、月次CU管理、モデル追加、および障害時の緊急復旧までの標準操作手順を定める。
- **対象**:
  - フェーズ1：GUI中心の実機ベンチマークおよび API テンプレート JSON 抽出
  - フェーズ2：CLI バッチ実行（5〜10本単位）、月間 200 CU 枠管理、新モデル追加
  - 障害復旧ランブック（`ERR-STOP-FAILURE`、`stale lock`、レジューム、OOM / クールダウン）
- **対象外**:
  - 初回OAuth認証・SSH鍵生成・約 42.5 GB モデルの事前配置（[CCR-SETUP-001 環境構築手順書](../setup/CCR-SETUP-001_環境構築手順書.md) を参照）
  - エラーコード・状態遷移定義の正本（[CCR-DD-001 詳細設計書](../design/CCR-DD-001_詳細設計書.md) を参照）
  - JSON フィールド制約・原子書込仕様の正本（[CCR-DS-001 データ構造仕様書](../design/CCR-DS-001_データ構造仕様書.md) を参照）
- **実施タイミング**: 初回立上げ時（フェーズ1）、バッチ生成時・月末（フェーズ2）、異常検知時（ランブック）。

## 2. 前提条件・注意事項

- **実行者・必要権限**: ローカル操作者（xzyozi）。[CCR-SETUP-001](../setup/CCR-SETUP-001_環境構築手順書.md) のフェーズ0検収を完了していること。
- **事前確認**: 実行前に `colab sessions`（既存VM不在）および `colab usage`（CU残高十分）を必ず確認すること。
- **停止・中断条件**:
  - 残高CUがバッチ所要見込を下回る場合はVMを起動しない。
  - 異常発生時・操作中断時は、いかなる場合でも最終的に `colab stop` と `colab sessions` 空確認を完了させること。
- **安全上の注意**:
  - A100 High-RAM VM を起動したまま放置しない（アイドル切断前の 60〜90 分も課金対象になるため）。
  - 実行ログ・証跡に OAuth トークン、SSH 秘密鍵、ローカル固有の絶対パスを残さない。

---

## 3. 手順

### 3.1 フェーズ1：実機ベンチマーク・テンプレート確定手順（GUI中心）

| No. | 操作・確認内容 | 期待結果 | 異常時の対応 |
| :---: | :--- | :--- | :--- |
| 1 | 実行前確認：`colab sessions` と `colab usage` を実行し、空セッションと開始前 CU（`cu_before`）および現在時刻を記録する。 | 残存セッションなし、`cu_before` が記録される。 | 残存セッションがあれば `colab stop` で停止してからやり直す。 |
| 2 | VM起動・Driveマウント：`colab new -s h3-bench --gpu A100`（`--high-mem` なしが第一候補・VRAM 40GB / RAM 83.5GB）を実行し、続けて `colab drivemount` を実行する。 | A100 VM が起動し、`/content/drive/MyDrive/` がマウントされる（`nvidia-smi` / `free -h` で確認）。 | 起動失敗・タイムアウト時は即 `colab stop -s h3-bench` を実行し、連続リトライを避ける。 |
| 3 | 環境復元とポート転送：`scripts/setup.sh` および `workflows/minimax_h3/` 依存定義をVMへ配送・実行し、SSHポートフォワーディング（`localhost:8188`）を張る。 | 3分以内を目安に ComfyUI が起動し、ブラウザで `http://127.0.0.1:8188` が開く。 | Drive モデル不在エラー時は配置を見直し、即座に VM を停止する。 |
| 4 | 2本連続生成ベンチマーク：軽量テスト設定（`960x544` / 5秒 / Turbo 8step）で **1本目（VRAMロード込み）** と **2本目（純粋推論）** を連続実行し、各所要時間と `output/` 書込を確認する。 | 2本目が 1.5〜2.5 分目安で完了し、音声付きMP4が Drive `output/` へ保存される。 | OOM 発生時は `--high-mem` 有無と解像度/秒数を確認する。 |
| 5 | 本番設定（任意確認：`1344x768` / 10秒 / 8step + SageAttn）の所要時間を確認後、ComfyUI 設定で **Dev Mode** を有効化し、`Save (API Format)` から `workflows/minimax_h3/workflow_api.json` を保存する。 | API形式のJSONがローカルリポジトリへ保存される。 | フォーマット誤り時は `Save` ではなく `Save (API Format)` を再実行する。 |
| 6 | マニフェスト反映：`workflow_api.json` 内の実ノードIDを確認し、`workflows/minimax_h3/model_manifest.json` の `node_mappings`（`prompt`, `width`, `height`, `duration_sec`, `seed`, `output_prefix`, `input_assets`）へ反映する。 | 論理キーと実ノードIDが1対1で対応する。 | ノード構成変更時は再度 `Save (API Format)` から取り直す。 |
| 7 | 終了検収：`colab stop -s h3-bench` → `colab sessions`（空確認）→ `colab usage`（`cu_after` 計測）を実施し、実消費CU・実測消費レート（CU/時）・月間稼働可能本数を確定する。 | アクティブセッションが空になり、実測 CU/時 が記録される。 | 停止失敗時は 3.3 ランブック「`ERR-STOP-FAILURE`」へ即移行する。 |

```powershell
# フェーズ1 実行例（セッション名は用途に合わせて変更）
colab sessions
colab usage
colab new -s h3-bench --gpu A100
colab drivemount -s h3-bench
# ポート転送例（別ターミナルまたはバックグラウンド）
ssh -N -L 8188:127.0.0.1:8188 -o ProxyCommand="colab ssh --proxy-mode -s h3-bench -i $env:USERPROFILE\.ssh\id_ed25519" -i $env:USERPROFILE\.ssh\id_ed25519 root@colab
# 終了時
colab stop -s h3-bench
colab sessions
colab usage
```

### 3.2 フェーズ2：定常バッチ運用・月次管理手順

#### A. バッチ実行手順（`--dry-run` 必須）

1バッチあたり 5〜10 本・総実行時間 2〜4 時間以内（`max_batch_duration_sec` 上限 14,400 秒）を目安に運用する。

| No. | 操作・確認内容 | 期待結果 | 異常時の対応 |
| :---: | :--- | :--- | :--- |
| 1 | バッチ入力ファイル（`batches/<batch_id>.json`）を作成し、モデル名・プロンプト・パラメータ・入力素材を記述する（形式は `CCR-DS-001` 2.2 B 準拠）。 | バッチ定義JSONが準備できる。 | 必須キー漏れがないか見直す。 |
| 2 | **無課金事前検証（必須）**：`--dry-run` 付きで `run_batch.py` を実行し、JSON整合・パラメータ境界値・ローカル `input_assets` 実在・ロック競合・残高確認を検証する。 | エラー0件・終了コード0で終了する（VMは起動されない）。 | 指摘された入力不備または残存ロックを解消してから次へ進む。 |
| 3 | 本番バッチ実行：`--dry-run` を外して `run_batch.py` を起動する。完了または中断まで待機し、終了サマリ（`succeeded_count` / `cu_consumed` / `vm_stopped_verified`）を確認する。 | 全ジョブ（または予算内ジョブ）が生成され、`vm_stopped_verified == true` で終了する。 | 異常終了時は出力されたエラーコード（`CCR-DD-001` 4.1）に従い、必要に応じ 3.3 ランブックを実行する。 |
| 4 | 成果物検収：Google Drive の `<drive_root>/output/<model_name>/<batch_id>/` を確認する。ローカル取得が必要な場合のみ `colab download` / `scp` / `rsync` で引き取る。 | 全成功ジョブの MP4 が再生可能な状態で存在する。 | 途中失敗分のみ残った場合は 3.3 のレジューム手順を実行する。 |

```powershell
# 事前検証（0 CU）
uv run python scripts/run_batch.py --batch batches/h3_batch_001.json --dry-run

# 本番バッチ実行
uv run python scripts/run_batch.py --batch batches/h3_batch_001.json
```

#### B. 月次 CU 消化管理ルーチン

- **日次 / バッチ後**: `logs/cu_usage_history.jsonl` の最新行および `colab usage` を確認し、月間 200 CU 枠に対する残量と実測消費レート（CU/時）を把握する。
- **月末調整（契約更新日前）**: 未使用CUは翌月へ繰り越せないため、更新日 3〜5 日前に残CUを確認し、端数消化用の小規模バッチ（例: 2〜3本）を実行するか計画的に使い切る。更新日を跨ぐ長尺バッチは投入しない。

#### C. モデル追加手順（`FR-08` モデル差し替え）

1. `workflows/<new_model_name>/` ディレクトリを作成する（`scripts/` 側は原則変更しない）。
2. 新モデルのモデル重みを Google Drive `<drive_root>/models/` 配下へ事前配置する（[CCR-SETUP-001](../setup/CCR-SETUP-001_環境構築手順書.md) 3.2 準拠）。
3. フェーズ1の手順（3.1）で GUI 動作確認・`workflow_api.json` 抽出を行う。
4. `model_manifest.json`（`required_drive_assets`, `node_mappings`, `default_params`, `default_job_timeout_sec`）を作成する。
5. 追加 Python パッケージやカスタムノードが必要な場合は、同ディレクトリに `requirements.txt` / `nodes.txt` を配置する。
6. `--dry-run` および 1〜2 本の小規模テストバッチで疎通・出力確認を行う。

---

### 3.3 障害復旧ランブック（Runbook）

#### ランブック1：`ERR-STOP-FAILURE`（自動停止失敗・課金暴走防止）

1. **最優先で CLI 強制停止を試行**: `colab stop -s <session_name>` を実行し、`colab sessions` で一覧が空になるか確認する。
2. **CLI で消えない場合（Web UI 強制終了）**:
   - ブラウザで Google Colab（サインイン済み同一アカウント）を開く。
   - メニュー **「ランタイム」→「ランタイムを管理」** を開く。
   - アクティブなセッションを選択し、**「セッションを終了」** をクリックして強制破棄する。
3. **終了確認**: 再度ローカルから `colab sessions` を実行し空であることを確認した上で、`logs/batch_<batch_id>_<execution_id>.json` の状態と `state/active_batch.lock` 残留を確認する。

#### ランブック2：`stale lock` 残留時の安全確認と回収

`state/active_batch.lock` が残存し起動が拒否された場合は、**ロックファイルを手動削除する前に**必ず以下の順序で確認する。

1. ロックファイル内の `pid`、`session_name`、`acquired_at` を確認する。
2. ローカル OS 上で該当 `pid` が稼働中か確認する（Windows: `Get-Process -Id <pid>`）。生存中なら進行中のため待機し削除しない。
3. `pid` が不在の場合、`colab sessions` を実行して `session_name` の Colab VM が残存していないか確認する。
4. **VM が残存している場合**: 先に `colab stop -s <session_name>`（不可時はランブック1）で VM を停止し、`colab sessions` が空になったことを確認してから `state/active_batch.lock` を削除する。
5. **VM が残存していない場合**: 安全に `state/active_batch.lock` を削除（または `run_batch.py` の自動 stale 回収に委ねる）し、再実行へ進む。

#### ランブック3：バッチ途中中断からの再開（レジューム）

1. 中断した実行の監査ログ `logs/batch_<batch_id>_<execution_id>.json` を開く。
2. `jobs` 配列から `status == "succeeded"` の `job_id` と Drive 上の出力ファイルを確認する。
3. 元の `batches/<batch_id>.json` をコピーして新しい `batch_id`（例: `<batch_id>_retry1`）を付与し、`status` が `failed` / `skipped` / `pending` のジョブのみを残した再実行用バッチJSONを作成する。
4. `--dry-run` 実行後、本番実行を行う（出力先ディレクトリが分離されるため既存成果物を上書きしない）。

#### ランブック4：CUDA OOM / Google 側クールダウン発生時

- **CUDA OOM / システムRAM不足**:
  - まず `colab new` 時に `--high-mem` が確実に付与されているか確認する。
  - 解像度・尺・参照枚数を段階的に引き下げる（例: 本番 `1344x768`・10秒 → `960x544`・5〜8秒へフォールバック）。
- **Google Colab 側リソース割当制限 / クールダウン**:
  - A100 の確保に失敗し続ける場合は、まず `--high-mem` を外して標準 A100（40GB / RAM 83.5GB）で試行する（`--high-mem` は希少な 80GB/167GB 枠限定となり失敗しやすい）。
  - 短時間の連続リトライは Colab 側の動的クールダウンを誘発するため即停止し、15〜30分（状況により数時間）間隔を空ける。
  - 実行時間帯は日本時間 20:00〜翌 2:00 頃の混雑帯を避け、午前中〜昼過ぎへのシフトを推奨する。

---

## 4. 補足・例外

| ID | 対象手順 | 標準との差異 | 理由・制約 | 影響 | 代替対応 | 解除条件 |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| `EX-OP-001` | 3.1 手順3 | フェーズ1は手動SSH転送、フェーズ2は動的 `ProxyCommand` | フェーズ1はブラウザ検証のため対話維持が必要 | フェーズ1では操作者自身の停止忘れリスクあり | 手順7の `colab sessions` 空確認をチェックリスト化 | フェーズ2移行後は自動停止に移行 |

## 5. 関連文書

- **環境・構築仕様**: [CCR-ENV-001 環境・開発基盤仕様書](../setup/CCR-ENV-001_環境・開発基盤仕様書.md)、[CCR-SETUP-001 環境構築手順書](../setup/CCR-SETUP-001_環境構築手順書.md)
- **設計書**: [CCR-BD-001 基本設計書](../design/CCR-BD-001_基本設計書.md)、[CCR-DD-001 詳細設計書](../design/CCR-DD-001_詳細設計書.md)、[CCR-DS-001 データ構造仕様書](../design/CCR-DS-001_データ構造仕様書.md)
- **テスト**: [CCR-TEST-001 テスト計画・結果報告書](../test/CCR-TEST-001_テスト計画・結果報告書.md)

## 6. 作成完了チェック

- [x] `document_role`、`ssot_scope`、`status`、`owner`をFrontmatterへ設定した。
- [x] 目的、対象範囲、対象外、実施タイミングを記入した。
- [x] 実行者、必要権限、事前確認、中断条件を記入した。
- [x] 各手順に操作内容、期待結果、異常時の対応を記入した。
- [x] 不要な監視・バックアップ・エスカレーション項目を無理に追加していない。
- [x] 標準手順から外れる場合は、理由・影響・代替対応・解除条件を記録した。
- [x] 関連する正本文書への相対リンクを設定した。
- [x] 秘密情報そのもの、固定パス、利用者固有の認証情報を記載していない。

## 7. 改訂履歴 (Change Log)

| 版数 | 改訂日 | 変更者 | 変更内容・変更理由 (Why) |
| :--- | :--- | :--- | :--- |
| Rev.1.0 | 2026-11-22 | xzyozi | フェーズ1ベンチマーク・フェーズ2定常運用・障害復旧ランブックとして初版作成 |
| Rev.1.1 | 2026-11-23 | xzyozi | 手動SSHポート転送例の接続先へ `root@colab` および秘密鍵 `-i` を明記 |
| Rev.1.2 | 2026-11-23 | xzyozi | 標準 A100（`--high-mem` なし）第一候補化、15〜30分クールダウンおよび混雑時間帯（JST 20:00〜2:00）回避指針を追記 |
