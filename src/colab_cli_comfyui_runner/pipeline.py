"""バッチ実行オーケストレータおよび機能契約 F-001〜F-006（CCR-DD-001 準拠）。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
from pathlib import Path
import signal
import threading
import time
from typing import Any, Literal
import uuid

from colab_cli_comfyui_runner.colab_cli import (
    ColabCliRunner,
    OpenSshTransport,
    SshTransport,
    SubprocessColabCliRunner,
    TunnelHandle,
)
from colab_cli_comfyui_runner.comfy_client import ComfyApiClient, HttpWsComfyApiClient
from colab_cli_comfyui_runner.dao import (
    LockManager,
    append_cu_usage_history,
    atomic_write_json,
    read_json_file,
    utc_now_iso,
)
from colab_cli_comfyui_runner.errors import (
    CcrBaseError,
    JobApiTransientError,
    JobExecutionError,
    JobValidationError,
    PreflightCuError,
    StopFailureError,
    VmProvisionError,
)
from colab_cli_comfyui_runner.models import (
    HIGH_TIER_VM_PROFILES,
    BatchExecutionLog,
    BatchRunRequestDto,
    BatchSpec,
    BatchSummaryDto,
    CuUsageRecord,
    EnvDeliveryPayloadDto,
    ExecutionLock,
    JobExecutionRecord,
    JobResultDto,
    JobSubmitDto,
    ModelManifest,
    PreflightResultDto,
    VmSessionDto,
)
from colab_cli_comfyui_runner.workflow import inject_workflow_params, resolve_job_parameters

logger = logging.getLogger(__name__)

_CONSECUTIVE_FAILURE_LIMIT = 2
_MAX_STOP_RETRIES = 3
_MAX_API_TRANSIENT_RETRIES = 3


def _resolve_ssh_key_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser()
    return Path.home() / ".ssh" / "id_ed25519"


def _resolve_input_local_path(raw_path: str, base_dir: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    return candidate if candidate.is_absolute() else (base_dir / candidate)


def preflight_and_acquire_lock(
    request: BatchRunRequestDto,
    *,
    colab: ColabCliRunner,
    lock_manager: LockManager,
) -> PreflightResultDto:
    """`F-001`: バッチ定義・マニフェスト・ローカル素材を検証し、ロックとCU確認を行う。

    Args:
        request: CLI起動入力DTO。
        colab: `google-colab-cli` 実行Seam。
        lock_manager: ローカル排他ロック管理。

    Returns:
        事前検証結果DTO。

    Raises:
        JobValidationError: 定義ファイルや入力パラメータ、ローカル素材が不正な場合。
        PreflightLockError: 有効な別ロックが存在する場合。
        PreflightCuError: 既存セッション残存またはCU残高不足の場合。
    """
    base_dir = request.base_dir
    spec_path = (
        request.batch_spec_path if request.batch_spec_path.is_absolute() else (base_dir / request.batch_spec_path)
    )
    raw_spec = read_json_file(spec_path)

    model_name = raw_spec.get("model_name")
    if not isinstance(model_name, str) or not model_name.strip():
        raise JobValidationError("batch_spec.model_name は非空の文字列で指定してください。")

    model_dir = base_dir / "workflows" / model_name
    manifest_path = model_dir / "model_manifest.json"
    manifest = ModelManifest.from_dict(read_json_file(manifest_path), expected_model_name=model_name)

    template_path = model_dir / manifest.workflow_template_path
    workflow_template = read_json_file(template_path)

    batch_spec = BatchSpec.from_dict(raw_spec, duration_bounds=manifest.duration_bounds)

    for job in batch_spec.jobs:
        # 既定値込みの必須パラメータ解決を事前検証する
        resolve_job_parameters(job, manifest, seed_generator=lambda: 0)
        for asset_key, asset_ref in job.input_assets.items():
            if asset_key not in manifest.node_mappings:
                raise JobValidationError(
                    f"ジョブ '{job.job_id}' の input_assets.{asset_key} が node_mappings に未定義です。"
                )
            if asset_ref.source == "local":
                local_file = _resolve_input_local_path(asset_ref.path, base_dir)
                if not local_file.is_file():
                    raise JobValidationError(f"ジョブ '{job.job_id}' のローカル素材が見つかりません: {asset_ref.path}")

    setup_script_path = base_dir / "scripts" / "setup.sh"
    if not request.dry_run and not setup_script_path.is_file():
        raise JobValidationError(f"共通セットアップスクリプトが見つかりません: {setup_script_path}")

    ssh_key_path = _resolve_ssh_key_path(request.ssh_key_path)
    if not request.dry_run and not ssh_key_path.is_file():
        raise JobValidationError("指定されたSSH秘密鍵ファイルが存在しません。")

    lock = lock_manager.acquire(
        batch_spec.batch_id,
        has_active_session=lambda session: session in colab.list_sessions(),
    )
    try:
        active_sessions = colab.list_sessions()
        if active_sessions:
            raise PreflightCuError(
                f"既存のアクティブな Colab セッションが残存しています: {', '.join(sorted(active_sessions))}"
            )
        cu_before = colab.get_usage()
        if cu_before < batch_spec.min_required_cu:
            raise PreflightCuError(
                f"CU残高が不足しています: 残高={cu_before:.2f} < 最低必要={batch_spec.min_required_cu:.2f}"
            )
    except Exception:
        lock_manager.release(lock)
        raise

    return PreflightResultDto(
        execution_id=str(uuid.uuid4()),
        batch_spec=batch_spec,
        model_manifest=manifest,
        workflow_template=workflow_template,
        lock=lock,
        cu_before=cu_before,
        ssh_key_path=ssh_key_path,
        drive_root=request.drive_root,
    )


def provision_vm_and_tunnel(
    *,
    session_name: str,
    local_port: int,
    ssh_key_path: Path,
    lock: ExecutionLock,
    lock_manager: LockManager,
    colab: ColabCliRunner,
    ssh: SshTransport,
    vm_profiles: Sequence[tuple[str, bool]] = HIGH_TIER_VM_PROFILES,
) -> tuple[VmSessionDto, ExecutionLock]:
    """`F-002`: `colab new` 直前にロックへ `session_name` を記録し、VM起動・Driveマウント・SSH転送を行う。

    Args:
        session_name: 採番された一意セッション名。
        local_port: ローカル転送ポート。
        ssh_key_path: SSH秘密鍵パス。
        lock: 保持中の排他ロック。
        lock_manager: ロックマネージャ。
        colab: Colab CLI Seam。
        ssh: SSHトランスポート Seam。
        vm_profiles: フォールバック試行する `(gpu, high_mem)` 候補一覧（L4より上位のみ）。

    Returns:
        `(VmSessionDto, 更新後ExecutionLock)` のタプル。

    Raises:
        VmProvisionError: すべての候補プロファイルでVM確保に失敗した場合。
    """
    if not vm_profiles:
        raise VmProvisionError("試行可能な GPU プロファイル候補が空です。")

    updated_lock = lock_manager.update_session_name(lock, session_name)
    last_exc: VmProvisionError | None = None
    for gpu, high_mem in vm_profiles:
        try:
            colab.new_session(session_name, gpu=gpu, high_mem=high_mem)
            logger.info("Colab VM 起動完了: session=%s, gpu=%s, high_mem=%s", session_name, gpu, high_mem)
            last_exc = None
            break
        except VmProvisionError as exc:
            last_exc = exc
            logger.warning(
                "Colab VM 割当失敗 (gpu=%s, high_mem=%s)、次の候補へフォールバックします: %s",
                gpu,
                high_mem,
                exc,
            )
            try:
                if session_name in colab.list_sessions():
                    colab.stop_session(session_name)
            except Exception as stop_exc:
                logger.warning("失敗後のセッション掃除に失敗しました (%s): %s", session_name, stop_exc)
    if last_exc is not None:
        raise last_exc

    colab.drivemount(session_name)
    tunnel = ssh.start_tunnel(session_name, ssh_key_path, local_port=local_port, remote_port=8188)
    return (
        VmSessionDto(
            session_name=session_name,
            local_port=local_port,
            ssh_key_path=ssh_key_path,
            tunnel_handle=tunnel,
        ),
        updated_lock,
    )


def deliver_and_restore_env(
    payload: EnvDeliveryPayloadDto,
    *,
    ssh: SshTransport,
    api_client: ComfyApiClient,
) -> None:
    """`F-003`: `setup.sh` とモデル固有依存定義をVMへ配送・実行し、ComfyUI起動を待機する。

    Args:
        payload: VM配送入力DTO。
        ssh: SSHトランスポート Seam。
        api_client: ComfyUI APIクライアント Seam。
    """
    files_to_send = [payload.setup_script_path, *payload.dependency_files]
    ssh.deliver_files(payload.session_name, payload.ssh_key_path, files_to_send)
    ssh.run_remote_setup(
        payload.session_name,
        payload.ssh_key_path,
        model_name=payload.model_name,
        drive_root=payload.drive_root,
        required_drive_assets=payload.required_drive_assets,
    )
    api_client.wait_ready()


def _upload_with_retry(api_client: ComfyApiClient, local_path: Path) -> str:
    last_exc: Exception | None = None
    for attempt in range(_MAX_API_TRANSIENT_RETRIES + 1):
        try:
            return api_client.upload_image(local_path)
        except JobApiTransientError as exc:
            last_exc = exc
            if attempt >= _MAX_API_TRANSIENT_RETRIES:
                raise
    raise JobApiTransientError(str(last_exc))


def _submit_with_retry(api_client: ComfyApiClient, workflow: dict[str, Any], *, client_id: str) -> str:
    last_exc: Exception | None = None
    for attempt in range(_MAX_API_TRANSIENT_RETRIES + 1):
        try:
            return api_client.submit_prompt(workflow, client_id=client_id)
        except JobApiTransientError as exc:
            last_exc = exc
            if attempt >= _MAX_API_TRANSIENT_RETRIES:
                raise
    raise JobApiTransientError(str(last_exc))


def submit_and_monitor_job(
    submit_dto: JobSubmitDto,
    *,
    base_dir: Path,
    api_client: ComfyApiClient,
    clock: Callable[[], float] = time.monotonic,
) -> JobResultDto:
    """`F-004`: ローカル素材送信・パラメータ注入・`/prompt` 投入・完了監視を実行する。

    Args:
        submit_dto: ジョブ投入入力DTO。
        base_dir: ローカル基準ディレクトリ。
        api_client: ComfyUI APIクライアント Seam。
        clock: 経過秒数計測関数。

    Returns:
        `JobResultDto`。
    """
    started = clock()
    uploaded_local_assets: dict[str, str] = {}
    for asset_key, asset_ref in submit_dto.job.input_assets.items():
        if asset_ref.source == "local":
            local_path = _resolve_input_local_path(asset_ref.path, base_dir)
            uploaded_local_assets[asset_key] = _upload_with_retry(api_client, local_path)

    workflow = inject_workflow_params(
        submit_dto.workflow_template,
        submit_dto.manifest,
        submit_dto.job,
        batch_id=submit_dto.batch_id,
        resolved_params=submit_dto.resolved_params,
        uploaded_local_assets=uploaded_local_assets,
    )
    client_id = f"{submit_dto.execution_id}-{submit_dto.job.job_id}"
    prompt_id = _submit_with_retry(api_client, workflow, client_id=client_id)
    outputs = api_client.wait_for_completion(prompt_id, client_id=client_id, timeout_sec=submit_dto.timeout_sec)
    elapsed = max(0.0, clock() - started)
    output_relpath = outputs[0] if outputs else submit_dto.output_prefix
    return JobResultDto(
        job_id=submit_dto.job.job_id,
        prompt_id=prompt_id,
        status="succeeded",
        output_relpath=output_relpath,
        elapsed_sec=round(elapsed, 3),
        seed=int(submit_dto.resolved_params["seed"]),
    )


def teardown_and_audit(
    *,
    session_name: str | None,
    vm_started: bool,
    tunnel_handle: TunnelHandle | None,
    lock: ExecutionLock,
    lock_manager: LockManager,
    colab: ColabCliRunner,
    exec_log: BatchExecutionLog,
    log_path: Path,
    cu_history_path: Path,
    model_name: str,
    started_monotonic: float,
    had_error: bool,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[BatchSummaryDto, bool]:
    """`F-006`: SSH終了・`colab stop`（最大3回）・`colab sessions` 空確認・CU差分監査・ロック解放を行う。

    Args:
        session_name: 停止対象セッション名。
        vm_started: `colab new` が試行されたかどうか。
        tunnel_handle: 確立済みSSHトンネルハンドル。
        lock: 保持中のロック情報。
        lock_manager: ロックマネージャ。
        colab: Colab CLI Seam。
        exec_log: 実行状態ログ。
        log_path: 個別バッチログ保存先パス。
        cu_history_path: 月次CU履歴JSONLパス。
        model_name: 対象モデル名。
        started_monotonic: バッチ開始時刻（monotonic秒）。
        had_error: 異常中断要因が発生したか。
        clock: 経過秒数計測関数。

    Returns:
        `(BatchSummaryDto, stop_failed)` のタプル。
    """
    exec_log.status = "stopping"
    atomic_write_json(log_path, exec_log.to_dict())

    if tunnel_handle is not None:
        try:
            tunnel_handle.close()
        except Exception:
            logger.warning("SSHトンネル終了中にエラーが発生しました。", exc_info=True)

    vm_stopped_verified = True
    stop_failed = False
    if vm_started and session_name is not None:
        vm_stopped_verified = False
        for attempt in range(_MAX_STOP_RETRIES):
            try:
                colab.stop_session(session_name)
            except Exception as exc:
                logger.warning("colab stop 試行 %d/%d が失敗しました: %s", attempt + 1, _MAX_STOP_RETRIES, exc)
            try:
                remaining = colab.list_sessions()
                if session_name not in remaining:
                    vm_stopped_verified = True
                    break
            except Exception as exc:
                logger.warning("colab sessions 確認が失敗しました: %s", exc)

        if not vm_stopped_verified:
            stop_failed = True
            logger.error(
                "CRITICAL [ERR-STOP-FAILURE]: セッション '%s' の自動停止を確認できませんでした。"
                "ただちに `colab stop -s %s` または Web UI の「ランタイムを管理」から手動停止してください。",
                session_name,
                session_name,
            )

    cu_after: float | None = None
    try:
        cu_after = colab.get_usage()
    except Exception as exc:
        logger.warning("終了後 colab usage の取得に失敗しました: %s", exc)

    exec_log.cu_after = cu_after
    exec_log.ended_at = utc_now_iso()
    final_status: Literal["completed", "aborted"] = "aborted" if (had_error or stop_failed) else "completed"
    exec_log.status = final_status
    atomic_write_json(log_path, exec_log.to_dict())

    cu_consumed = (
        round(exec_log.cu_before - cu_after, 4) if (exec_log.cu_before is not None and cu_after is not None) else None
    )
    succeeded_count = sum(1 for j in exec_log.jobs if j.status == "succeeded")
    failed_count = sum(1 for j in exec_log.jobs if j.status == "failed")
    skipped_count = sum(1 for j in exec_log.jobs if j.status == "skipped")

    append_cu_usage_history(
        cu_history_path,
        CuUsageRecord(
            execution_id=exec_log.execution_id,
            batch_id=exec_log.batch_id,
            model_name=model_name,
            session_name=session_name if vm_started else None,
            cu_before=exec_log.cu_before,
            cu_after=cu_after,
            cu_consumed=cu_consumed,
            duration_sec=round(max(0.0, clock() - started_monotonic), 3),
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            recorded_at=exec_log.ended_at,
        ),
    )

    if not stop_failed:
        lock_manager.release(lock)

    error_code = StopFailureError.error_code if stop_failed else None
    return (
        BatchSummaryDto(
            execution_id=exec_log.execution_id,
            batch_id=exec_log.batch_id,
            final_status=final_status,
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            cu_consumed=cu_consumed,
            vm_stopped_verified=vm_stopped_verified,
            error_code=error_code,
        ),
        stop_failed,
    )


def _collect_dependency_files(model_dir: Path, manifest: ModelManifest) -> tuple[Path, ...]:
    files: list[Path] = []
    for rel in (manifest.requirements_path, manifest.nodes_path):
        if rel is not None:
            candidate = model_dir / rel
            if candidate.is_file():
                files.append(candidate)
    return tuple(files)


def _mark_remaining_skipped(exec_log: BatchExecutionLog, log_path: Path) -> None:
    changed = False
    for rec in exec_log.jobs:
        if rec.status == "pending":
            rec.status = "skipped"
            changed = True
    if changed:
        atomic_write_json(log_path, exec_log.to_dict())


def run_batch_pipeline(
    request: BatchRunRequestDto,
    *,
    colab: ColabCliRunner | None = None,
    ssh: SshTransport | None = None,
    api_client_factory: Callable[[int], ComfyApiClient] | None = None,
    lock_manager: LockManager | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> BatchSummaryDto:
    """`F-005`: 事前検証〜VM起動〜環境復元〜連続ジョブ投入〜安全停止を統括する。

    Args:
        request: CLI起動入力DTO。
        colab: Colab CLI Seam（省略時は実プロセス実装）。
        ssh: SSHトランスポート Seam（省略時は OpenSSH 実装）。
        api_client_factory: ローカルポートを受け取り `ComfyApiClient` を返すファクトリ。
        lock_manager: 排他ロック管理（省略時は `base_dir/state/active_batch.lock`）。
        clock: monotonic 秒取得関数（テスト用差し替え可）。

    Returns:
        最終実行サマリ `BatchSummaryDto`。
    """
    colab_runner: ColabCliRunner = colab or SubprocessColabCliRunner()
    ssh_transport: SshTransport = ssh or OpenSshTransport()
    client_factory: Callable[[int], ComfyApiClient] = api_client_factory or (
        lambda port: HttpWsComfyApiClient(base_url=f"http://127.0.0.1:{port}")
    )
    lock_mgr = lock_manager or LockManager(request.base_dir / "state" / "active_batch.lock")

    preflight = preflight_and_acquire_lock(request, colab=colab_runner, lock_manager=lock_mgr)
    if request.dry_run:
        lock_mgr.release(preflight.lock)
        return BatchSummaryDto(
            execution_id=preflight.execution_id,
            batch_id=preflight.batch_spec.batch_id,
            final_status="completed",
            succeeded_count=0,
            failed_count=0,
            skipped_count=len(preflight.batch_spec.jobs),
            cu_consumed=0.0,
            vm_stopped_verified=True,
        )

    batch_spec = preflight.batch_spec
    manifest = preflight.model_manifest
    session_name = f"{request.session_prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{preflight.execution_id[:4]}"
    log_path = request.base_dir / "logs" / f"batch_{batch_spec.batch_id}_{preflight.execution_id}.json"
    cu_history_path = request.base_dir / "logs" / "cu_usage_history.jsonl"

    exec_log = BatchExecutionLog(
        execution_id=preflight.execution_id,
        batch_id=batch_spec.batch_id,
        session_name=session_name,
        started_at=utc_now_iso(),
        status="preflight_checked",
        cu_before=preflight.cu_before,
        jobs=[JobExecutionRecord(job_id=j.job_id) for j in batch_spec.jobs],
    )
    atomic_write_json(log_path, exec_log.to_dict())

    started_monotonic = clock()
    active_lock = preflight.lock
    tunnel_handle: TunnelHandle | None = None
    vm_started = False
    had_error = False
    last_error_code: str | None = None
    cancel_requested = threading.Event()
    previous_sigint: Any = None
    previous_sigterm: Any = None
    is_main_thread = threading.current_thread() is threading.main_thread()

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.warning("割り込みシグナル (%d) を受信したため安全停止へ移行します。", signum)
        cancel_requested.set()
        raise KeyboardInterrupt

    if is_main_thread:
        previous_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _handle_signal)
        if hasattr(signal, "SIGTERM"):
            previous_sigterm = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, _handle_signal)

    try:
        exec_log.status = "vm_provisioning"
        atomic_write_json(log_path, exec_log.to_dict())
        vm_started = True
        vm_session, active_lock = provision_vm_and_tunnel(
            session_name=session_name,
            local_port=request.local_port,
            ssh_key_path=preflight.ssh_key_path,
            lock=active_lock,
            lock_manager=lock_mgr,
            colab=colab_runner,
            ssh=ssh_transport,
            vm_profiles=request.vm_profiles,
        )
        tunnel_handle = vm_session.tunnel_handle

        exec_log.status = "env_restoring"
        atomic_write_json(log_path, exec_log.to_dict())
        api_client = client_factory(request.local_port)
        model_dir = request.base_dir / "workflows" / batch_spec.model_name
        deliver_and_restore_env(
            EnvDeliveryPayloadDto(
                session_name=session_name,
                ssh_key_path=preflight.ssh_key_path,
                model_name=batch_spec.model_name,
                setup_script_path=request.base_dir / "scripts" / "setup.sh",
                dependency_files=_collect_dependency_files(model_dir, manifest),
                required_drive_assets=manifest.required_drive_assets,
                drive_root=preflight.drive_root,
            ),
            ssh=ssh_transport,
            api_client=api_client,
        )

        exec_log.status = "running"
        atomic_write_json(log_path, exec_log.to_dict())

        consecutive_failures = 0
        for idx, job in enumerate(batch_spec.jobs):
            if cancel_requested.is_set():
                had_error = True
                break
            elapsed_total = clock() - started_monotonic
            if elapsed_total >= batch_spec.max_batch_duration_sec:
                logger.warning(
                    "バッチ予算時間 (%ds) に達したため残りのジョブをスキップして停止します。",
                    batch_spec.max_batch_duration_sec,
                )
                last_error_code = "ERR-BATCH-BUDGET"
                break

            record = exec_log.jobs[idx]
            resolved_params = resolve_job_parameters(job, manifest)
            record.seed = int(resolved_params["seed"])
            record.status = "submitted"
            atomic_write_json(log_path, exec_log.to_dict())

            try:
                result = submit_and_monitor_job(
                    JobSubmitDto(
                        execution_id=preflight.execution_id,
                        job=job,
                        output_prefix=f"{batch_spec.model_name}/{batch_spec.batch_id}/{job.job_id}",
                        workflow_template=preflight.workflow_template,
                        manifest=manifest,
                        batch_id=batch_spec.batch_id,
                        resolved_params=resolved_params,
                        timeout_sec=manifest.default_job_timeout_sec,
                    ),
                    base_dir=request.base_dir,
                    api_client=api_client,
                    clock=clock,
                )
            except CcrBaseError as exc:
                record.status = "failed"
                record.error_code = exc.error_code
                last_error_code = exc.error_code
                atomic_write_json(log_path, exec_log.to_dict())
                consecutive_failures += 1
                if not isinstance(exc, JobExecutionError) or consecutive_failures >= _CONSECUTIVE_FAILURE_LIMIT:
                    had_error = True
                    break
            else:
                record.status = "succeeded"
                record.prompt_id = result.prompt_id
                record.output_relpath = result.output_relpath
                record.elapsed_sec = result.elapsed_sec
                atomic_write_json(log_path, exec_log.to_dict())
                consecutive_failures = 0

        _mark_remaining_skipped(exec_log, log_path)
    except KeyboardInterrupt:
        had_error = True
        last_error_code = last_error_code or "ERR-INTERRUPTED"
        _mark_remaining_skipped(exec_log, log_path)
    except CcrBaseError as exc:
        had_error = True
        last_error_code = exc.error_code
        _mark_remaining_skipped(exec_log, log_path)
        logger.error("パイプライン実行エラー [%s]: %s", exc.error_code, exc)
    except Exception as exc:
        had_error = True
        last_error_code = "ERR-UNEXPECTED"
        _mark_remaining_skipped(exec_log, log_path)
        logger.exception("予期しないエラーにより中断します: %s", exc)
    finally:
        if is_main_thread:
            if previous_sigint is not None:
                signal.signal(signal.SIGINT, previous_sigint)
            if hasattr(signal, "SIGTERM") and previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)

        summary, stop_failed = teardown_and_audit(
            session_name=session_name,
            vm_started=vm_started,
            tunnel_handle=tunnel_handle,
            lock=active_lock,
            lock_manager=lock_mgr,
            colab=colab_runner,
            exec_log=exec_log,
            log_path=log_path,
            cu_history_path=cu_history_path,
            model_name=batch_spec.model_name,
            started_monotonic=started_monotonic,
            had_error=had_error,
            clock=clock,
        )

    if stop_failed:
        raise StopFailureError(f"セッション '{session_name}' の停止確認に失敗しました。手動停止を行ってください。")
    if summary.error_code is None and last_error_code is not None:
        return BatchSummaryDto(
            execution_id=summary.execution_id,
            batch_id=summary.batch_id,
            final_status=summary.final_status,
            succeeded_count=summary.succeeded_count,
            failed_count=summary.failed_count,
            skipped_count=summary.skipped_count,
            cu_consumed=summary.cu_consumed,
            vm_stopped_verified=summary.vm_stopped_verified,
            error_code=last_error_code,
        )
    return summary
