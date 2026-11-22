"""CLIエントリポイント（標準出力はJSONサマリ専用、ログはすべて標準エラー出力）。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import sys

from colab_cli_comfyui_runner.errors import CcrBaseError
from colab_cli_comfyui_runner.models import BatchRunRequestDto
from colab_cli_comfyui_runner.pipeline import run_batch_pipeline


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


def build_parser() -> argparse.ArgumentParser:
    """CLI引数パーサを構築する。

    Returns:
        設定済み `ArgumentParser`。
    """
    parser = argparse.ArgumentParser(
        prog="ccr",
        description="Google Colab CLI × ComfyUI スポットバッチ実行ランナー",
    )
    parser.add_argument(
        "--batch",
        required=True,
        type=Path,
        help="バッチ入力定義JSONファイルパス (例: batches/h3_batch_001.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="VMを起動せず、JSON・ローカル素材・ロック・CU残高の事前検証のみ実行する",
    )
    parser.add_argument(
        "--local-port",
        type=int,
        default=8188,
        help="SSHポート転送のローカル側ポート番号 (既定: 8188)",
    )
    parser.add_argument(
        "--session-prefix",
        default="ccr",
        help="Colabセッション名の接頭辞 (既定: ccr)",
    )
    parser.add_argument(
        "--drive-root",
        default=os.environ.get("CCR_DRIVE_ROOT", "ComfyUI"),
        help="Google Drive MyDrive 配下のルートフォルダ名 (環境変数 CCR_DRIVE_ROOT 優先、既定: ComfyUI)",
    )
    parser.add_argument(
        "--ssh-key",
        type=Path,
        default=Path(os.environ["CCR_SSH_KEY_PATH"]) if os.environ.get("CCR_SSH_KEY_PATH") else None,
        help="ed25519 SSH秘密鍵のパス (環境変数 CCR_SSH_KEY_PATH 優先)",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path.cwd(),
        help="プロジェクトルートディレクトリ (既定: カレントディレクトリ)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLIを実行し、結果サマリJSONを標準出力へ、ログを標準エラー出力へ出力する。

    Args:
        argv: 引数リスト（テスト用）。

    Returns:
        終了コード（成功 0、失敗 1）。
    """
    _configure_logging()
    args = build_parser().parse_args(argv)
    try:
        request = BatchRunRequestDto(
            batch_spec_path=args.batch,
            dry_run=args.dry_run,
            local_port=args.local_port,
            session_prefix=args.session_prefix,
            drive_root=args.drive_root,
            ssh_key_path=args.ssh_key,
            base_dir=args.base_dir.resolve(),
        )
        summary = run_batch_pipeline(request)
    except CcrBaseError as exc:
        logging.getLogger(__name__).error("[%s] %s", exc.error_code, exc)
        return 1

    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    return 0 if summary.final_status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
