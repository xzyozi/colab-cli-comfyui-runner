#!/usr/bin/env python3
"""`uv run python scripts/run_batch.py` 用の薄いCLIエントリラッパー（CCR-OP-001準拠）。"""

from __future__ import annotations

import sys

from colab_cli_comfyui_runner.cli import main

if __name__ == "__main__":
    sys.exit(main())
