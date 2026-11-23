"""DTO（CCR-DD-001）および永続データモデル（CCR-DS-001）の型定義・検証ロジック。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
import re
from typing import Any, Literal

from colab_cli_comfyui_runner.errors import JobValidationError

SUPPORTED_SCHEMA_VERSION = "1.0"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MODEL_NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
_SESSION_PREFIX_PATTERN = re.compile(r"^[a-z0-9-]{1,16}$")

BatchStatus = Literal[
    "initialized",
    "preflight_checked",
    "vm_provisioning",
    "env_restoring",
    "running",
    "stopping",
    "completed",
    "aborted",
]
JobStatus = Literal["pending", "submitted", "succeeded", "failed", "skipped"]
AssetSource = Literal["local", "drive"]


def validate_safe_relpath(raw_path: str, *, field_name: str) -> str:
    """相対パス文字列にパストラバーサルや絶対パス指定が含まれないことを検証する。

    Args:
        raw_path: 検証対象の相対パス文字列。
        field_name: エラーメッセージ表示用のフィールド名。

    Returns:
        正規化されたPOSIX相対パス文字列。

    Raises:
        JobValidationError: 空文字、絶対パス、ドライブレター、または `..` を含む場合。
    """
    if not raw_path or not raw_path.strip():
        raise JobValidationError(f"{field_name} は空文字にできません。")
    normalized = raw_path.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized:
        raise JobValidationError(f"{field_name} に絶対パスやドライブ指定は使用できません: {raw_path}")
    posix = PurePosixPath(normalized)
    if posix.is_absolute() or ".." in posix.parts:
        raise JobValidationError(f"{field_name} に親ディレクトリ参照 '..' は使用できません: {raw_path}")
    return posix.as_posix()


def validate_schema_version(data: dict[str, Any], *, source_name: str) -> str:
    """JSONデータの `schema_version` を検証する。

    Args:
        data: 読み込んだJSON辞書。
        source_name: エラー表示用のファイル種別名。

    Returns:
        検証済み `schema_version` 文字列。

    Raises:
        JobValidationError: 未指定またはサポート外のバージョンの場合。
    """
    version = data.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise JobValidationError(
            f"{source_name} の schema_version が不正または未対応です: {version!r} (対応: {SUPPORTED_SCHEMA_VERSION!r})"
        )
    return str(version)


def _require_str(data: dict[str, Any], key: str, *, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JobValidationError(f"{context}.{key} は非空の文字列で指定してください。")
    return value


def _require_positive_even_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value % 2 != 0:
        raise JobValidationError(f"{field_name} は正の偶数で指定してください: {value!r}")
    return value


@dataclass(frozen=True)
class InputAssetRef:
    """ジョブ入力素材参照（CCR-DS-001 2.2 B）。"""

    source: AssetSource
    path: str

    @classmethod
    def from_dict(cls, raw: Any, *, field_name: str) -> InputAssetRef:
        """辞書から `InputAssetRef` を生成・検証する。

        Args:
            raw: 入力JSON内の素材エントリ。
            field_name: エラー表示用フィールド名。

        Returns:
            検証済み `InputAssetRef`。

        Raises:
            JobValidationError: 形式不正・ソース種別不正・Driveパス不正の場合。
        """
        if not isinstance(raw, dict):
            raise JobValidationError(f"{field_name} はオブジェクトで指定してください。")
        source = raw.get("source")
        path = raw.get("path")
        if source not in ("local", "drive"):
            raise JobValidationError(f"{field_name}.source は 'local' または 'drive' にしてください: {source!r}")
        if not isinstance(path, str) or not path.strip():
            raise JobValidationError(f"{field_name}.path は非空の文字列で指定してください。")
        if source == "drive":
            path = validate_safe_relpath(path, field_name=f"{field_name}.path")
        return cls(source=source, path=path)


@dataclass(frozen=True)
class NodeTargetMapping:
    """テンプレートJSON内のノードIDとフィールド名のマッピング。"""

    node_id: str
    input_key: str

    @classmethod
    def from_dict(cls, raw: Any, *, mapping_key: str) -> NodeTargetMapping:
        """辞書からノードマッピングを生成・検証する。

        Args:
            raw: `node_mappings[mapping_key]` の値。
            mapping_key: 論理パラメータ名。

        Returns:
            検証済み `NodeTargetMapping`。

        Raises:
            JobValidationError: `node_id` または `input_key` が欠落している場合。
        """
        if not isinstance(raw, dict):
            raise JobValidationError(f"node_mappings.{mapping_key} はオブジェクトで指定してください。")
        node_id = _require_str(raw, "node_id", context=f"node_mappings.{mapping_key}")
        input_key = _require_str(raw, "input_key", context=f"node_mappings.{mapping_key}")
        return cls(node_id=node_id, input_key=input_key)


@dataclass(frozen=True)
class ModelManifest:
    """モデル別定義（`workflows/<model_name>/model_manifest.json`）。"""

    schema_version: str
    model_name: str
    workflow_template_path: str
    required_drive_assets: tuple[str, ...]
    node_mappings: dict[str, NodeTargetMapping]
    default_params: dict[str, Any]
    requirements_path: str | None = None
    nodes_path: str | None = None
    default_job_timeout_sec: int = 900
    duration_bounds: tuple[int, int] = (4, 15)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, expected_model_name: str | None = None) -> ModelManifest:
        """JSON辞書から `ModelManifest` を生成・検証する。

        Args:
            data: `model_manifest.json` の内容。
            expected_model_name: ディレクトリ名との一致を検証する場合の期待モデル名。

        Returns:
            検証済み `ModelManifest`。

        Raises:
            JobValidationError: 必須項目・型・制約違反がある場合。
        """
        schema_version = validate_schema_version(data, source_name="model_manifest.json")
        model_name = _require_str(data, "model_name", context="model_manifest")
        if not _MODEL_NAME_PATTERN.match(model_name):
            raise JobValidationError(f"model_name が命名規約外です: {model_name!r}")
        if expected_model_name is not None and model_name != expected_model_name:
            raise JobValidationError(
                f"model_name ({model_name}) がディレクトリ名 ({expected_model_name}) と一致しません。"
            )

        raw_tpl = data.get("workflow_template_path", "workflow_api.json")
        if not isinstance(raw_tpl, str):
            raise JobValidationError("workflow_template_path は文字列で指定してください。")
        workflow_template_path = validate_safe_relpath(raw_tpl, field_name="workflow_template_path")

        raw_assets = data.get("required_drive_assets")
        if not isinstance(raw_assets, list) or not raw_assets:
            raise JobValidationError("required_drive_assets は1件以上の配列で指定してください。")
        assets: list[str] = []
        for idx, asset in enumerate(raw_assets):
            if not isinstance(asset, str):
                raise JobValidationError(f"required_drive_assets[{idx}] は文字列で指定してください。")
            assets.append(validate_safe_relpath(asset, field_name=f"required_drive_assets[{idx}]"))

        raw_mappings = data.get("node_mappings")
        if not isinstance(raw_mappings, dict) or not raw_mappings:
            raise JobValidationError("node_mappings は1件以上のオブジェクトで指定してください。")
        mappings = {str(k): NodeTargetMapping.from_dict(v, mapping_key=str(k)) for k, v in raw_mappings.items()}

        default_params = data.get("default_params")
        if not isinstance(default_params, dict):
            raise JobValidationError("default_params はオブジェクトで指定してください。")

        req_path_raw = data.get("requirements_path")
        requirements_path = (
            validate_safe_relpath(req_path_raw, field_name="requirements_path")
            if isinstance(req_path_raw, str) and req_path_raw.strip()
            else None
        )
        if req_path_raw is not None and not isinstance(req_path_raw, str):
            raise JobValidationError("requirements_path は文字列または null で指定してください。")

        nodes_path_raw = data.get("nodes_path")
        nodes_path = (
            validate_safe_relpath(nodes_path_raw, field_name="nodes_path")
            if isinstance(nodes_path_raw, str) and nodes_path_raw.strip()
            else None
        )
        if nodes_path_raw is not None and not isinstance(nodes_path_raw, str):
            raise JobValidationError("nodes_path は文字列または null で指定してください。")

        timeout = data.get("default_job_timeout_sec", 900)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise JobValidationError(f"default_job_timeout_sec は正の整数で指定してください: {timeout!r}")

        min_dur = data.get("min_duration_sec", 4)
        max_dur = data.get("max_duration_sec", 15)
        if (
            isinstance(min_dur, bool)
            or isinstance(max_dur, bool)
            or not isinstance(min_dur, int)
            or not isinstance(max_dur, int)
            or min_dur <= 0
            or max_dur < min_dur
        ):
            raise JobValidationError("min_duration_sec / max_duration_sec の範囲指定が不正です。")

        return cls(
            schema_version=schema_version,
            model_name=model_name,
            workflow_template_path=workflow_template_path,
            required_drive_assets=tuple(assets),
            node_mappings=mappings,
            default_params=dict(default_params),
            requirements_path=requirements_path,
            nodes_path=nodes_path,
            default_job_timeout_sec=timeout,
            duration_bounds=(min_dur, max_dur),
        )


@dataclass(frozen=True)
class JobItem:
    """個別動画生成ジョブ定義（`BATCH_SPEC.jobs[*]`）。"""

    job_id: str
    prompt: str
    negative_prompt: str = ""
    width: int | None = None
    height: int | None = None
    duration_sec: int | None = None
    seed: int | None = None
    input_assets: dict[str, InputAssetRef] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, index: int, duration_bounds: tuple[int, int] = (4, 15)) -> JobItem:
        """辞書から `JobItem` を生成・検証する。

        Args:
            data: ジョブオブジェクト辞書。
            index: 配列内インデックス（エラー表示用）。
            duration_bounds: 許容する尺 `(min, max)` 秒。

        Returns:
            検証済み `JobItem`。

        Raises:
            JobValidationError: 必須項目・範囲・型制約に違反する場合。
        """
        ctx = f"jobs[{index}]"
        job_id = _require_str(data, "job_id", context=ctx)
        if not _ID_PATTERN.match(job_id):
            raise JobValidationError(f"{ctx}.job_id が不正です（1〜64文字の英数字・_-）: {job_id!r}")
        prompt = _require_str(data, "prompt", context=ctx)

        neg = data.get("negative_prompt", "")
        if neg is None:
            neg = ""
        if not isinstance(neg, str):
            raise JobValidationError(f"{ctx}.negative_prompt は文字列で指定してください。")

        width = (
            _require_positive_even_int(data["width"], field_name=f"{ctx}.width")
            if data.get("width") is not None
            else None
        )
        height = (
            _require_positive_even_int(data["height"], field_name=f"{ctx}.height")
            if data.get("height") is not None
            else None
        )

        duration = data.get("duration_sec")
        if duration is not None:
            min_d, max_d = duration_bounds
            if isinstance(duration, bool) or not isinstance(duration, int) or not (min_d <= duration <= max_d):
                raise JobValidationError(
                    f"{ctx}.duration_sec は {min_d}〜{max_d} 秒の整数で指定してください: {duration!r}"
                )

        seed = data.get("seed")
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
                raise JobValidationError(f"{ctx}.seed は0以上の整数で指定してください: {seed!r}")

        raw_assets = data.get("input_assets") or {}
        if not isinstance(raw_assets, dict):
            raise JobValidationError(f"{ctx}.input_assets はオブジェクトで指定してください。")
        assets = {
            str(k): InputAssetRef.from_dict(v, field_name=f"{ctx}.input_assets.{k}") for k, v in raw_assets.items()
        }

        return cls(
            job_id=job_id,
            prompt=prompt,
            negative_prompt=neg,
            width=width,
            height=height,
            duration_sec=duration,
            seed=seed,
            input_assets=assets,
        )


@dataclass(frozen=True)
class BatchSpec:
    """バッチ入力定義（`batches/<batch_id>.json`）。"""

    schema_version: str
    batch_id: str
    model_name: str
    max_batch_duration_sec: int
    min_required_cu: float
    jobs: tuple[JobItem, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, duration_bounds: tuple[int, int] = (4, 15)) -> BatchSpec:
        """辞書から `BatchSpec` を生成・検証する。

        Args:
            data: バッチ入力JSON辞書。
            duration_bounds: 対象モデルの許容尺 `(min, max)` 秒。

        Returns:
            検証済み `BatchSpec`。

        Raises:
            JobValidationError: 必須項目・範囲・`job_id` 重複などの制約違反がある場合。
        """
        schema_version = validate_schema_version(data, source_name="batch_spec")
        batch_id = _require_str(data, "batch_id", context="batch_spec")
        if not _ID_PATTERN.match(batch_id):
            raise JobValidationError(f"batch_id が不正です（1〜64文字の英数字・_-）: {batch_id!r}")

        model_name = _require_str(data, "model_name", context="batch_spec")
        if not _MODEL_NAME_PATTERN.match(model_name):
            raise JobValidationError(f"model_name が命名規約外です: {model_name!r}")

        max_dur = data.get("max_batch_duration_sec", 14400)
        if isinstance(max_dur, bool) or not isinstance(max_dur, int) or not (600 <= max_dur <= 14400):
            raise JobValidationError(f"max_batch_duration_sec は 600〜14400 秒で指定してください: {max_dur!r}")

        min_cu = data.get("min_required_cu", 10.0)
        if isinstance(min_cu, bool) or not isinstance(min_cu, (int, float)) or float(min_cu) <= 0.0:
            raise JobValidationError(f"min_required_cu は 0.0 より大きい数値で指定してください: {min_cu!r}")

        raw_jobs = data.get("jobs")
        if not isinstance(raw_jobs, list) or not raw_jobs:
            raise JobValidationError("jobs は1件以上の配列で指定してください。")

        jobs: list[JobItem] = []
        seen_ids: set[str] = set()
        for idx, raw_job in enumerate(raw_jobs):
            if not isinstance(raw_job, dict):
                raise JobValidationError(f"jobs[{idx}] はオブジェクトで指定してください。")
            item = JobItem.from_dict(raw_job, index=idx, duration_bounds=duration_bounds)
            if item.job_id in seen_ids:
                raise JobValidationError(f"jobs[{idx}].job_id '{item.job_id}' が重複しています。")
            seen_ids.add(item.job_id)
            jobs.append(item)

        return cls(
            schema_version=schema_version,
            batch_id=batch_id,
            model_name=model_name,
            max_batch_duration_sec=max_dur,
            min_required_cu=float(min_cu),
            jobs=tuple(jobs),
        )


@dataclass
class JobExecutionRecord:
    """個別ジョブの永続化実行レコード（CCR-DS-001 2.2 C）。"""

    job_id: str
    status: JobStatus = "pending"
    prompt_id: str | None = None
    output_relpath: str | None = None
    elapsed_sec: float | None = None
    error_code: str | None = None
    seed: int | None = None


@dataclass
class BatchExecutionLog:
    """バッチ実行状態・監査ログ（`logs/batch_<batch_id>_<execution_id>.json`）。"""

    execution_id: str
    batch_id: str
    session_name: str
    started_at: str
    status: BatchStatus = "initialized"
    schema_version: str = SUPPORTED_SCHEMA_VERSION
    cu_before: float | None = None
    cu_after: float | None = None
    ended_at: str | None = None
    jobs: list[JobExecutionRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON永続化用の辞書へ変換する。

        Returns:
            永続化辞書表現。
        """
        return asdict(self)


@dataclass(frozen=True)
class ExecutionLock:
    """ローカル排他ロックデータ（`state/active_batch.lock`）。"""

    lock_id: str
    batch_id: str
    pid: int
    hostname: str
    acquired_at: str
    session_name: str | None = None
    ttl_sec: int = 18000

    def to_dict(self) -> dict[str, Any]:
        """JSON永続化用の辞書へ変換する。

        Returns:
            ロック辞書表現。
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionLock:
        """辞書から `ExecutionLock` を復元する。

        Args:
            data: ロックファイルから読み込んだJSON辞書。

        Returns:
            復元した `ExecutionLock`。

        Raises:
            JobValidationError: 必須フィールドが不足または不正な場合。
        """
        lock_id = _require_str(data, "lock_id", context="active_batch.lock")
        batch_id = _require_str(data, "batch_id", context="active_batch.lock")
        hostname = _require_str(data, "hostname", context="active_batch.lock")
        acquired_at = _require_str(data, "acquired_at", context="active_batch.lock")
        pid = data.get("pid")
        ttl_sec = data.get("ttl_sec", 18000)
        session_name = data.get("session_name")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise JobValidationError(f"active_batch.lock.pid が不正です: {pid!r}")
        if isinstance(ttl_sec, bool) or not isinstance(ttl_sec, int) or ttl_sec <= 0:
            raise JobValidationError(f"active_batch.lock.ttl_sec が不正です: {ttl_sec!r}")
        if session_name is not None and not isinstance(session_name, str):
            raise JobValidationError("active_batch.lock.session_name が文字列ではありません。")
        # Validate ISO 8601 format
        try:
            datetime.fromisoformat(acquired_at)
        except ValueError as exc:
            raise JobValidationError(f"active_batch.lock.acquired_at が不正な日時です: {acquired_at!r}") from exc
        return cls(
            lock_id=lock_id,
            batch_id=batch_id,
            pid=pid,
            hostname=hostname,
            acquired_at=acquired_at,
            session_name=session_name,
            ttl_sec=ttl_sec,
        )


@dataclass(frozen=True)
class CuUsageRecord:
    """月次CU履歴追記ログの1行レコード（`logs/cu_usage_history.jsonl`）。"""

    execution_id: str
    batch_id: str
    model_name: str
    session_name: str | None
    cu_before: float | None
    cu_after: float | None
    cu_consumed: float | None
    duration_sec: float
    succeeded_count: int
    failed_count: int
    recorded_at: str
    schema_version: str = SUPPORTED_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """JSONL出力用辞書へ変換する。

        Returns:
            レコード辞書。
        """
        return asdict(self)


# --- DTO (CCR-DD-001 2.3 / 2.4) ---

# L4以下（L4/T4/CPU）へは落とさず、L4より上位のGPU環境のみを優先順でフォールバックする。
HIGH_TIER_VM_PROFILES: tuple[tuple[str, bool], ...] = (
    ("A100", True),
    ("A100", False),
    ("G4", False),
    ("H100", False),
)


@dataclass(frozen=True)
class BatchRunRequestDto:
    """`F-005` CLI起動入力 DTO。"""

    batch_spec_path: Path
    dry_run: bool = False
    local_port: int = 8188
    session_prefix: str = "ccr"
    drive_root: str = "ComfyUI"
    ssh_key_path: Path | None = None
    base_dir: Path = field(default_factory=Path.cwd)
    vm_profiles: tuple[tuple[str, bool], ...] = HIGH_TIER_VM_PROFILES

    def __post_init__(self) -> None:
        """入力パラメータの基本制約を検証する。

        Raises:
            JobValidationError: ポート番号・接頭辞・`drive_root`・`vm_profiles` が不正な場合。
        """
        if not (1024 <= self.local_port <= 65535):
            raise JobValidationError(f"local_port は 1024〜65535 の範囲で指定してください: {self.local_port}")
        if not _SESSION_PREFIX_PATTERN.match(self.session_prefix):
            raise JobValidationError(
                f"session_prefix が規約外です（英小文字・数字・- 1〜16文字）: {self.session_prefix!r}"
            )
        validate_safe_relpath(self.drive_root, field_name="drive_root")
        if not self.vm_profiles:
            raise JobValidationError("vm_profiles には1つ以上の (gpu, high_mem) 候補を指定してください。")


@dataclass(frozen=True)
class PreflightResultDto:
    """`F-001` 事前検証結果 DTO。"""

    execution_id: str
    batch_spec: BatchSpec
    model_manifest: ModelManifest
    workflow_template: dict[str, Any]
    lock: ExecutionLock
    cu_before: float
    ssh_key_path: Path
    drive_root: str


@dataclass(frozen=True)
class VmSessionDto:
    """`F-002` VM確保・SSHトンネル確立結果 DTO。"""

    session_name: str
    local_port: int
    ssh_key_path: Path
    tunnel_handle: Any


@dataclass(frozen=True)
class EnvDeliveryPayloadDto:
    """`F-003` VM配送入力 DTO。"""

    session_name: str
    ssh_key_path: Path
    model_name: str
    setup_script_path: Path
    dependency_files: tuple[Path, ...]
    required_drive_assets: tuple[str, ...]
    drive_root: str


@dataclass(frozen=True)
class JobSubmitDto:
    """`F-004` ジョブ投入入力 DTO。"""

    execution_id: str
    job: JobItem
    output_prefix: str
    workflow_template: dict[str, Any]
    manifest: ModelManifest
    batch_id: str
    resolved_params: dict[str, Any]
    timeout_sec: int


@dataclass(frozen=True)
class JobResultDto:
    """`F-004` ジョブ完了出力 DTO。"""

    job_id: str
    prompt_id: str | None
    status: JobStatus
    output_relpath: str | None
    elapsed_sec: float
    seed: int | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class BatchSummaryDto:
    """`F-005` バッチ実行最終サマリ DTO。"""

    execution_id: str
    batch_id: str
    final_status: Literal["completed", "aborted"]
    succeeded_count: int
    failed_count: int
    skipped_count: int
    cu_consumed: float | None
    vm_stopped_verified: bool
    error_code: str | None = None
