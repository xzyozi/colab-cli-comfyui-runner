---
title: "テスト計画・結果報告書"
document_type: "test_plan_report"
document_number: "CCR-TEST-001"
document_role: "evidence"
ssot_scope: "test_evidence"
status: "draft"
version: "1.1"
created_at: "2026-11-22"
updated_at: "2026-11-23"
author: "xzyozi"
owner: "xzyozi"
purpose: "colab-cli-comfyui-runner のCU消費分離テスト階層（L1/L2/L3）、機能テスト要件（TR-001〜TR-008）・データ境界・非機能受入テストの具象ケース、合格基準および証跡記録形式を定義するため"
related_documents:
  - "ドキュメントガバナンス（相対パス: ../README.md）"
  - "用語集（相対パス: ../../CONTEXT.md）"
  - "CCR-BD-001 基本設計書（相対パス: ../design/CCR-BD-001_基本設計書.md）"
  - "CCR-DD-001 詳細設計書（相対パス: ../design/CCR-DD-001_詳細設計書.md）"
  - "CCR-DS-001 データ構造仕様書（相対パス: ../design/CCR-DS-001_データ構造仕様書.md）"
  - "CCR-ENV-001 環境・開発基盤仕様書（相対パス: ../setup/CCR-ENV-001_環境・開発基盤仕様書.md）"
  - "CCR-SETUP-001 環境構築手順書（相対パス: ../setup/CCR-SETUP-001_環境構築手順書.md）"
  - "CCR-OP-001 運用手順書（相対パス: ../how-to/CCR-OP-001_運用手順書.md）"
---

# テスト計画・結果報告書
**Google Colab CLI × ComfyUI（MiniMax H3）動画自動生成パイプライン（colab-cli-comfyui-runner）**

---

## 1. 目的・対象範囲

- **目的**: 月間 200 CU の制約下で課金暴走やデータ破損を防ぐため、「CUを消費せずに安全回路・永続化・API冪等性を証明するテスト（L1/L2）」と「実CUを消費する実機受入テスト（L3）」を厳格に分離したテスト計画および結果記録枠組みを定める。
- **テスト対象**:
  - `scripts/` 配下のバッチ制御・VMライフサイクル・動的SSH/SCP・ComfyUI HTTP/WS APIアダプタ・DAO/排他ロック実装（`F-001`〜`F-006`）
  - `workflows/<model_name>/` のマニフェスト・テンプレートJSON整合
  - Colab A100 High-RAM 実機・Google Drive 連携（フェーズ1・少数E2E・カオス受入）
- **対象外**:
  - `google-colab-cli`・`ComfyUI`・`MiniMax H3` 本体OSSの内部アルゴリズム検証
  - 長時間（24時間連続等）の耐久試験（1バッチ2〜4時間制限のため）
- **関連する設計・基準**:
  - アーキテクチャ・非機能要件（`NFR-001`〜`NFR-007`）: [CCR-BD-001 基本設計書](../design/CCR-BD-001_基本設計書.md)
  - 機能テスト要件（`TR-001`〜`TR-008`）・エラー契約: [CCR-DD-001 詳細設計書](../design/CCR-DD-001_詳細設計書.md)
  - 永続化・原子書込・ロック仕様: [CCR-DS-001 データ構造仕様書](../design/CCR-DS-001_データ構造仕様書.md)

## 2. テスト戦略・合格基準

### 2.1 テスト階層と CU コスト分離戦略

| テスト層 | 実行環境・対象 | CU消費 | 検証目的 | 実行タイミング |
| :--- | :--- | :---: | :--- | :--- |
| **L1: 単体・モジュール結合** | ローカル（`google-colab-cli`・SSH/SCP・ComfyUI API を完全モック） | **0 CU** | `TR-001`〜`TR-008`、状態遷移、原子書込、ロック制御、安全回路 | コード変更ごと（CI・品質ゲート対象） |
| **L2: ローカル結合・ドライラン** | ローカル実ファイル＋実 `colab usage` / `sessions` 読取のみ | **0 CU** | CLI出力パース互換性、実マニフェスト検証、`--dry-run` 正常性 | 実機投入前・CLI更新時 |
| **L3: 実機受入・ベンチマーク** | Google Colab A100 High-RAM 実機（フェーズ1 + 少数E2E） | **約 2〜4 CU** | `NFR-001`〜`NFR-006` 実測確認、Driveリンク実動作、動画音声品質確認 | 初回構築時・モデル/ComfyUI刷新時 |

### 2.2 採用テスト方式・合格基準・品質ゲート

- [x] TDD（L1 単体・安全回路実装時）
- [x] 回帰テスト（L1/L2 自動実行）
- [x] 結合テスト（L1 モジュール結合・L2 実ファイル結合）
- [x] E2Eテスト（L3 実機少数バッチ・カオス受入）
- [x] 手動確認（L3 動画再生・`ffprobe` 検収）

- **選択理由**: 実機実行にコスト（CU/時間）がかかるため、ロジック・安全回路・例外処理を L1（0 CU）で網羅的に固定し、L3 は実機固有の境界（Driveリンク・VRAM/RAM・動画音声品質・CU単価）の受入に集中させる。
- **合格基準（Exit Criteria）**:
  - **L1 / L2 完了条件**: 適用品質ゲートの全パス、および `TR-001`〜`TR-008`・`CCR-DS-001` 異常系ケースの 100% 合格。
  - **L3 完了条件**: フェーズ1ベンチマーク計測確定、少数E2E（2本）およびカオス受入テスト合格、終了後の `colab sessions` 空確認証跡の取得。
- **適用する品質ゲート**: [CCR-ENV-001 環境・開発基盤仕様書](../setup/CCR-ENV-001_環境・開発基盤仕様書.md) 4.4 に定める `QG-001`、`QG-002`、`QG-003`、`QG-004`、`QG-005`、`QG-006`（Linux CI で `[tool.mutmut]` 有効化時は `QG-007`）。実行コマンドおよび合格基準の詳細は正本を参照し、本書へ再掲しない。
- **使用する証跡**: L1/L2 は `pytest` 出力・CI 実行リンク、L3 は `logs/batch_<batch_id>_<execution_id>.json`、`logs/cu_usage_history.jsonl`、`colab sessions` 出力、`ffprobe` 出力（いずれも認証情報・個人絶対パスをマスク済み）。

### 2.3 テスト対象の Seam（L1 モック境界）

| Seam / 公開インターフェース | 確認する振る舞い | テスト対象外の内部実装 |
| :--- | :--- | :--- |
| `ColabCliRunner`（`C-001` CLI実行境界） | `colab usage` / `sessions` / `new` / `drivemount` / `stop` の発行順序・引数・リトライ・出力パース | `google-colab-cli` 内部通信・Google認証通信 |
| `SshTransport`（`C-001` SSH/SCP境界） | 動的 `ProxyCommand` 引数構築、`/tmp/ccr_setup/` への配送対象ファイル選別、トンネル起動・終了 | OpenSSH クライアントバイナリ内部処理 |
| `ComfyApiClient`（`C-004` HTTP/WS境界） | `/system_stats` ポーリング、`/upload/image` 送信、`/prompt` 投入、`/ws` 監視と `/history` フォールバック、`/interrupt` | ComfyUI サーバ内部キュー・PyTorch推論処理 |
| `FileDao` / `LockManager`（`CCR-DS-001` 境界） | `O_CREAT \| O_EXCL` 排他取得、stale lock 4象限判定、`os.fsync`＋`os.replace` 原子書込、`.tmp` 清掃 | OS ファイルシステム・カーネルのファイルロック実装 |
| `Clock` / `Signal` Seam | 時間予算（`max_batch_duration_sec`）超過判定、実行中 `SIGINT` / `SIGTERM` 受信時の安全停止遷移 | OS シグナル配送機構そのもの |

---

## 3. テスト環境・前提条件

- **環境**:
  - L1/L2: ローカル端末または GitHub Actions（`ubuntu-latest`）、Python 3.10+（推奨 3.12）、`uv` 管理環境。
  - L3: Google Colab A100 40GB / High-RAM、Google Drive 5TB（[CCR-SETUP-001](../setup/CCR-SETUP-001_環境構築手順書.md) 完了済み）。
- **テストデータ**: L1 では `tmp_path` 上にダミー `model_manifest.json`・`workflow_api.json`・ダミーPNG素材を生成する。実認証トークン・秘密鍵は使用しない。
- **実行方法**: L1 は `QG-004`（[CCR-ENV-001](../setup/CCR-ENV-001_環境・開発基盤仕様書.md) 4.4 参照）、L2 は [CCR-OP-001](../how-to/CCR-OP-001_運用手順書.md) 3.2 A の `--dry-run`、L3 は [CCR-OP-001](../how-to/CCR-OP-001_運用手順書.md) 3.1〜3.2 の手順に従う。

---

## 4. テストケース

### 4.1 L1：機能テスト要件・安全回路・API制御（0 CU）

| ケースID | 対象・条件 | 入力・操作 | 期待結果 | 期待結果の根拠 | 実績結果 | 判定 | 証跡 |
| :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| `TC-L1-001` | `F-001` CU不足・残存セッション | `cu_before < min_required_cu`、または `colab sessions` に残存ありのモック応答で `F-001` 実行 | `ERR-PREFLIGHT-CU` で即 `Aborted`、VM起動未実行、取得ロック解放 | `TR-001` (`CCR-DD-001` 4.1) | 期待どおり例外・ロック解放確認 | Pass | `RUN-001` (`tests/test_pipeline.py`) |
| `TC-L1-002` | `F-002` 起動順序・`session_name` 事前記録 | 正常モックおよび `colab drivemount` 失敗モックで `F-002` 実行 | `colab new` 呼び出し直前にロックへ `session_name` が原子書込され、`new` → `drivemount` → 動的 `ProxyCommand` SSH の順で実行。途中失敗時も `F-006` へ `session_name` が渡る | `TR-002` (`CCR-DD-001` 2.2/3.2) | 呼び出し順序・事前記録・`ProxyCommand` 確認 | Pass | `RUN-001` (`tests/test_pipeline.py`, `tests/test_colab_cli.py`) |
| `TC-L1-003` | `F-003` 配送対象分離・Drive不在 | `setup.sh` と `workflows/<model>/` 依存定義（`requirements.txt`, `nodes.txt`）を配送。Driveモデル不在エラー応答でテスト | 動的 `ProxyCommand` で `/tmp/ccr_setup/` へ配送され、Driveモデル欠落時は `ERR-SETUP-DRIVE-MISSING` で即停止へ遷移 | `TR-003` (`CCR-DD-001` 2.2) | 配送対象・終了コード2判別・即停止確認 | Pass | `RUN-001` (`tests/test_pipeline.py`, `tests/test_colab_cli.py`) |
| `TC-L1-004` | `F-004` テンプレート非破壊・2ジョブ連続 | 1バッチ2ジョブ（ジョブ1のみ任意解像度/尺指定、ジョブ2は既定値）を投入 | テンプレート辞書が汚染されず、ジョブ2へジョブ1の値（および `output_prefix`）が漏れない | `TR-004` (`CCR-DD-001` 2.2/2.3) | ジョブ2の既定値維持・非汚染確認 | Pass | `RUN-001` (`tests/test_pipeline.py`) |
| `TC-L1-005` | `F-004` WS切断時 `/history` フォールバック | `POST /prompt` 成功後、`/ws` を強制切断し `/history/{prompt_id}` が完了応答を返すモック | **重複 `POST /prompt` を発行せず** `/history/{prompt_id}` から完了・出力パスを取得する | `TR-004` (`CCR-DD-001` 2.2/4.1) | フォールバックによる出力パス取得確認 | Pass | `RUN-001` (`tests/test_comfy_client.py`) |
| `TC-L1-006` | `F-004` `input_assets` 解決・送信 | `source: "local"` 素材と `source: "drive"` 素材を含むジョブを投入 | `local` は `/upload/image` で送信した返却名、`drive` は `drive_assets/<relpath>` が `node_mappings` 経由で注入される | `TR-004` (`CCR-DD-001` 2.2) | 両ソースの送信・注入値確認 | Pass | `RUN-001` (`tests/test_pipeline.py`) |
| `TC-L1-007` | `F-004` 単一ジョブタイムアウト | `/ws` 応答が `timeout_sec` を超過するモック | `POST /interrupt` を試行した上で `ERR-JOB-EXECUTION` として記録する | `TR-005` (`CCR-DD-001` 2.2) | `/interrupt` 発行と `JobExecutionError` 送出確認 | Pass | `RUN-001` (`tests/test_comfy_client.py`) |
| `TC-L1-008` | `F-005` 時間予算・連続失敗遮断 | 3ジョブ中、1本目完了後に `max_batch_duration_sec` 超過、および連続2件失敗の2パターンを実行 | 残りジョブが `skipped` と記録され、`Stopping` へ遷移する | `TR-006` (`CCR-DD-001` 3.2) | 両パターンで残ジョブ `skipped`・安全停止確認 | Pass | `RUN-001` (`tests/test_pipeline.py`) |
| `TC-L1-009` | `F-005`/`F-006` 課金暴走防止（全異常・割込） | `F-002`失敗・`F-003`失敗・`F-004`失敗・実行中 `SIGINT`/`SIGTERM` 送出の4パターンを実行 | 全パターンで `finally` 経由の `colab stop` と `colab sessions` 空確認・ロック解放が必ず実行される | `TR-007` / `NFR-006` (`CCR-DD-001`, `CCR-BD-001`) | 全パターンで停止・トンネル終了・ロック解放確認 | Pass | `RUN-001` (`tests/test_pipeline.py`) |
| `TC-L1-010` | `F-006` `colab stop` リトライと最終失敗 | `colab stop` が「1〜2回目失敗・3回目成功」と「3回連続失敗」の2パターンを実行 | 前者は `vm_stopped_verified == true` で終了。後者は最大3回リトライ後に `ERR-STOP-FAILURE` および手動停止警告を出力 | `TR-008` (`CCR-DD-001` 4.1) | 3回リトライ成功・最終失敗時の例外/ロック残置確認 | Pass | `RUN-001` (`tests/test_pipeline.py`) |

### 4.2 L1：データ永続化・排他ロック・境界値（0 CU）

| ケースID | 対象・条件 | 入力・操作 | 期待結果 | 期待結果の根拠 | 実績結果 | 判定 | 証跡 |
| :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| `TC-L1-101` | 原子書き込み破壊テスト | 既存ログがある状態で `os.replace` 直前に例外を注入し、別テストで孤立 `.tmp.<pid>` を配置 | 既存ログが破損せず無傷で残り、自プロセス `.tmp.<pid>` が清掃され、孤立 `.tmp.*` は無視される | `CCR-DS-001` 3.1 | 既存ファイル保全・`.tmp` 清掃確認 | Pass | `RUN-001` (`tests/test_dao_and_models.py`) |
| `TC-L1-102` | 排他ロック `O_CREAT \| O_EXCL` | 同時2スレッド/プロセスからロック取得を試行 | 1つのみ成功し、他方は `ERR-PREFLIGHT-LOCK` で即時拒否される | `CCR-DS-001` 3.2 | 二重取得の即時拒否確認 | Pass | `RUN-001` (`tests/test_dao_and_models.py`) |
| `TC-L1-103` | `stale lock` 4象限マトリクス | ①PID生存 ②PID不在＋`session_name: null` ③PID不在＋`sessions`空 ④PID不在＋`sessions`残存 | ①は競合拒否、②③は自動回収、④は自動回収せず残存VM停止を促して中断 | `CCR-DS-001` 3.2 | 4象限すべて期待通り判定 | Pass | `RUN-001` (`tests/test_dao_and_models.py`) |
| `TC-L1-104` | 入力バリデーション境界値 | 尺（3/4/15/16秒）、解像度（奇数/0/負数）、`..` 含む相対パス、未知 `schema_version` | 4〜15秒・正の偶数のみ許可。3/16秒・奇数・0・負数・`..`・未知バージョンはすべて起動前にエラー終了 | `CCR-DS-001` 2.2/4.1 | 全境界値・未知バージョン拒否確認 | Pass | `RUN-001` (`tests/test_dao_and_models.py`) |
| `TC-L1-105` | ログ一意化と `cu_usage_history.jsonl` | 同一 `batch_id` を2回実行 | `logs/batch_<batch_id>_<execution_id>.json` が2ファイル独立生成され上書きされず、`cu_usage_history.jsonl` に2行追記される | `CCR-DS-001` 1.3/2.2 E | 独立追記および JSONL 2行生成確認 | Pass | `RUN-001` (`tests/test_dao_and_models.py`) |

### 4.3 L2：ローカル結合・ドライラン（0 CU）

| ケースID | 対象・条件 | 入力・操作 | 期待結果 | 期待結果の根拠 | 実績結果 | 判定 | 証跡 |
| :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| `TC-L2-001` | `colab usage`/`sessions` 実出力パース | ローカル環境から実 `google-colab-cli` の `usage` と `sessions` 出力を取得しパーサへ入力 | 例外なく残高数値とセッション集合を抽出できる（0 CU） | `F-001` / `F-006` (`CCR-DD-001`) | 未実施 | 未実施 | — |
| `TC-L2-002` | 実マニフェスト＋`--dry-run` | 実 `workflows/minimax_h3/` とテスト用バッチJSONで `--dry-run` を実行 | 終了コード 0 で検証完了し、VMを起動しない | `CCR-OP-001` 3.2 A | 未実施 | 未実施 | — |

### 4.4 L3：実機受入・カオス受入テスト（約 2〜4 CU）

| ケースID | 対象・条件 | 入力・操作 | 期待結果 | 期待結果の根拠 | 実績結果 | 判定 | 証跡 |
| :--- | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| `TC-L3-001` | フェーズ1 環境復元・ベンチマーク | A100 High-RAM 起動〜Driveリンク〜`setup.sh`〜軽量設定2本連続生成〜停止 | 復元 3 分以内、2本目 1.5〜2.5 分目安、実測 CU/時 確定、終了後 `colab sessions` 空 | `NFR-001`, `NFR-002`, `NFR-003` (`CCR-BD-001` 1.1) | 未実施 | 未実施 | — |
| `TC-L3-002` | 出力動画仕様検収 | L3 で生成された Drive `output/` 上の MP4 を `ffprobe` 等で検査 | 映像 24fps・音声 32kHz ステレオを含む再生可能な MP4 であること | `FR-04` (`CCR-BD-001` 1.1) | 未実施 | 未実施 | — |
| `TC-L3-003` | カオス受入（途中 `SIGINT` 中断） | 2本連続バッチを実行し、1本目完了後の2本目推論中に意図的に `SIGINT` を送出 | `finally` で `colab stop` が完了し `colab sessions` が空となり、かつ1本目の MP4 が Drive 上に再生可能な状態で残存する | `NFR-005`, `NFR-006` (`CCR-BD-001` 1.1/3.2) | 未実施 | 未実施 | — |

---

## 5. 実施結果・証跡フォーマット

実施時に本表へ追記する（認証情報、SSH鍵、ローカル固有の絶対パスは必ずマスクすること）。

| 実行ID | 実施日 | Gitコミットハッシュ | テスト層 | 実施者・環境 | 結果概要 | 実測時間 | 消費CU | 特記事項・証跡リンク |
| :--- | :--- | :--- | :---: | :--- | :--- | :---: | :---: | :--- |
| `RUN-001` | 2026-11-23 | `3f12316` | L1 | xzyozi（Windows / Python 3.12） | 36件全パス、全体カバレッジ 88%（1243文/143未到達）、品質ゲート `QG-001`〜`QG-004` パス | 1.86秒 | 0.0 | `feat/core-infra` 上でモック単体・結合テスト実施 |

- **総合判定**: 条件付き合格（L1 完了・L2/L3 はワークフロー配置後に実施予定）
- **判定理由**: L1（0 CU）の安全回路・永続化・CLI/SSH/APIモック検証および品質ゲート（`QG-001`〜`QG-004`）はすべて合格した。モデル固有ワークフロー（`workflows/minimax_h3/`）選定後に L2（`--dry-run`）および L3（実機受入）を実施し最終確定する。

## 6. 未実施・対象外・テスト差異

| ID | 対象項目 | 状態 | 理由・標準との差異 | 影響・リスク | 代替確認・対応 | 承認者・期限 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `EX-TEST-001` | L2 / L3（`TC-L2-001`〜`002`, `TC-L3-001`〜`003`） | 未実施 | 共通インフラ基盤先行実装フェーズであり、実モデルワークフロー未配置・実機未検証のため | 実機環境固有の差異（実出力形式・推論速度・CU単価）は未確認 | ワークフロー取り込み後に L2 → L3 の順に実施し、5章へ追記する | xzyozi（フェーズ1実施時） |

## 7. 関連文書

- **基本・詳細・データ構造設計**: [CCR-BD-001 基本設計書](../design/CCR-BD-001_基本設計書.md)、[CCR-DD-001 詳細設計書](../design/CCR-DD-001_詳細設計書.md)、[CCR-DS-001 データ構造仕様書](../design/CCR-DS-001_データ構造仕様書.md)
- **環境・構築・運用**: [CCR-ENV-001 環境・開発基盤仕様書](../setup/CCR-ENV-001_環境・開発基盤仕様書.md)、[CCR-SETUP-001 環境構築手順書](../setup/CCR-SETUP-001_環境構築手順書.md)、[CCR-OP-001 運用手順書](../how-to/CCR-OP-001_運用手順書.md)

## 8. 作成完了チェック

- [x] `document_role`、`ssot_scope`、`status`、`owner`をFrontmatterへ設定した。
- [x] 目的、対象範囲、対象外、関連する設計書を記入した。
- [x] 採用したテスト戦略（L1/L2/L3分離）と、プロジェクト固有の合格基準を記入した。
- [x] TDD・モック対象Seamの表を追加した。
- [x] テストケースに期待結果、根拠、実績、判定、証跡枠を記録した。
- [x] 詳細設計書の機能テスト要件（`TR-001`〜`TR-008`）を検証するケースの根拠に要件IDを記載した。
- [x] 品質ゲートは環境・開発基盤仕様書を参照し、実行コマンドや合格基準を再掲していない。
- [x] 未実施・対象外の理由と対応を記録した。
- [x] テスト文書が要求・設計の正本を上書きしていないことを確認した。
- [x] 秘密情報そのものや利用者固有の認証情報を記載していない。

## 9. 改訂履歴 (Change Log)

| 版数 | 改訂日 | 変更者 | 変更内容・変更理由 (Why) |
| :--- | :--- | :--- | :--- |
| Rev.1.0 | 2026-11-22 | xzyozi | CUコスト分離戦略（L1/L2/L3）および TR-001〜TR-008 / NFR-001〜NFR-007 に対応する初版作成 |
| Rev.1.1 | 2026-11-23 | xzyozi | 共通基盤実装に伴い L1 テスト実施結果（`RUN-001`: 36件パス・カバレッジ 88%）を反映 |
