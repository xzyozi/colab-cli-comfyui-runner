"""ワークフローテンプレートへのパラメータ注入・非破壊構築ロジック（CCR-DD-001 F-004）。"""

from __future__ import annotations

import copy
import secrets
from typing import Any

from colab_cli_comfyui_runner.errors import JobValidationError
from colab_cli_comfyui_runner.models import JobItem, ModelManifest, validate_safe_relpath


def _set_mapped_value(
    workflow: dict[str, Any],
    manifest: ModelManifest,
    mapping_key: str,
    value: Any,
    *,
    required: bool = True,
) -> None:
    target = manifest.node_mappings.get(mapping_key)
    if target is None:
        if required:
            raise JobValidationError(f"model_manifest.json の node_mappings に '{mapping_key}' が未定義です。")
        return
    node = workflow.get(target.node_id)
    if not isinstance(node, dict):
        raise JobValidationError(f"workflow_api.json にノードID '{target.node_id}' が存在しません。")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise JobValidationError(f"ノード '{target.node_id}' に 'inputs' オブジェクトが存在しません。")
    inputs[target.input_key] = value


def resolve_job_parameters(
    job: JobItem,
    manifest: ModelManifest,
    *,
    seed_generator: Any = None,
) -> dict[str, Any]:
    """ジョブ個別指定とモデル既定値をマージし、確定パラメータ辞書を作成する。

    Args:
        job: 投入対象のジョブ定義。
        manifest: モデルマニフェスト。
        seed_generator: 乱数シード生成コールバック（省略時は `secrets.randbelow`）。

    Returns:
        補完・確定済みパラメータ辞書。

    Raises:
        JobValidationError: 必須パラメータがマニフェスト既定値にもジョブにも存在しない場合。
    """
    defaults = manifest.default_params
    width = job.width if job.width is not None else defaults.get("width")
    height = job.height if job.height is not None else defaults.get("height")
    duration_sec = job.duration_sec if job.duration_sec is not None else defaults.get("duration_sec")

    if width is None or height is None or duration_sec is None:
        raise JobValidationError(f"ジョブ '{job.job_id}' の width / height / duration_sec が既定値を含めて未設定です。")

    if job.seed is not None:
        seed = job.seed
    elif defaults.get("seed") is not None:
        seed = int(defaults["seed"])
    else:
        seed = int(seed_generator()) if seed_generator is not None else secrets.randbelow(2**31)

    return {
        "prompt": job.prompt,
        "negative_prompt": job.negative_prompt,
        "width": int(width),
        "height": int(height),
        "duration_sec": int(duration_sec),
        "seed": seed,
    }


def build_output_prefix(model_name: str, batch_id: str, job_id: str) -> str:
    """出力動画保存用の `filename_prefix`（`<model_name>/<batch_id>/<job_id>`）を生成する。

    Args:
        model_name: モデル名。
        batch_id: バッチID。
        job_id: ジョブID。

    Returns:
        検証済みの `output_prefix` 相対パス文字列。
    """
    raw = f"{model_name}/{batch_id}/{job_id}"
    return validate_safe_relpath(raw, field_name="output_prefix")


def inject_workflow_params(
    template: dict[str, Any],
    manifest: ModelManifest,
    job: JobItem,
    *,
    batch_id: str,
    resolved_params: dict[str, Any],
    uploaded_local_assets: dict[str, str],
) -> dict[str, Any]:
    """テンプレートJSONをディープコピーし、ジョブパラメータ・出力接頭辞・入力素材を注入する。

    Args:
        template: `workflow_api.json` の辞書（元の辞書は一切変更しない）。
        manifest: モデルマニフェスト。
        job: 投入対象ジョブ。
        batch_id: バッチID。
        resolved_params: `resolve_job_parameters` で確定済みのパラメータ辞書。
        uploaded_local_assets: ローカル素材キーから `/upload/image` 返却パスへのマップ。

    Returns:
        パラメータ注入済みの新規ワークフロー辞書。

    Raises:
        JobValidationError: テンプレートノードやマッピングが不足している場合。
    """
    workflow: dict[str, Any] = copy.deepcopy(template)

    _set_mapped_value(workflow, manifest, "prompt", resolved_params["prompt"], required=True)
    _set_mapped_value(workflow, manifest, "negative_prompt", resolved_params["negative_prompt"], required=False)
    _set_mapped_value(workflow, manifest, "width", resolved_params["width"], required=False)
    _set_mapped_value(workflow, manifest, "height", resolved_params["height"], required=False)
    _set_mapped_value(workflow, manifest, "duration_sec", resolved_params["duration_sec"], required=False)
    _set_mapped_value(workflow, manifest, "seed", resolved_params["seed"], required=False)

    output_prefix = build_output_prefix(manifest.model_name, batch_id, job.job_id)
    _set_mapped_value(workflow, manifest, "output_prefix", output_prefix, required=True)

    for asset_key, asset_ref in job.input_assets.items():
        if asset_ref.source == "local":
            if asset_key not in uploaded_local_assets:
                raise JobValidationError(f"ローカル入力素材 '{asset_key}' のアップロード結果が見つかりません。")
            resolved_asset_value = uploaded_local_assets[asset_key]
        else:
            resolved_asset_value = f"drive_assets/{asset_ref.path}"
        _set_mapped_value(workflow, manifest, asset_key, resolved_asset_value, required=True)

    return workflow
