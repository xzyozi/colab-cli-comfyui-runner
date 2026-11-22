# colab-cli-comfyui-runner

Google Colab 上で ComfyUI を実行するための CLI ランナープロジェクトです。

## 構成

- `src/colab_cli_comfyui_runner/`: VM/SSH制御・ComfyUI API制御・DAO/排他ロック・パイプライン統括
- `scripts/`: CLIラッパー（`run_batch.py`）および Colab VM側復元スクリプト（`setup.sh`）
- `tests/`: モックを用いた L1 単体・結合テスト（0 CU）
- `pyproject.toml`: Pythonプロジェクトと開発ツールの基本設定
- `docs/`: 要求、設計、手順、検証結果などのドキュメント
- `.github/workflows/`: ドキュメントとMermaid図・Python CIの定義
- `.github/scripts/`: CIから呼び出す検証スクリプト

## 開発・テスト実行

```powershell
uv sync --group dev
uv run --no-sync pytest --cov=colab_cli_comfyui_runner --cov-report=term-missing
uv run --no-sync python scripts/run_batch.py --help
```

## ドキュメント検証

ローカルでは次のコマンドで、`docs/` とルートREADMEを検証できます。

```powershell
python .github/scripts/validate_docs.py docs README.md
```

Mermaid図を検証する場合は、Node.jsが必要です。

```powershell
node .github/scripts/validateMermaidMarkdown.mjs --all
```
