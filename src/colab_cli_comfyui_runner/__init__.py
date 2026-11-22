"""Google Colab CLI × ComfyUI 動画自動生成パイプラインの共通基盤パッケージ。"""

from colab_cli_comfyui_runner.errors import CcrBaseError
from colab_cli_comfyui_runner.pipeline import run_batch_pipeline

__all__ = ["CcrBaseError", "run_batch_pipeline"]
