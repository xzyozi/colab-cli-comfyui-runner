"""DAO・原子書き込み・排他ロック・入力バリデーションの単体テスト（TC-L1-101〜105等）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from colab_cli_comfyui_runner.colab_cli import build_ssh_options, parse_cu_usage, parse_sessions
from colab_cli_comfyui_runner.dao import (
    LockManager,
    append_cu_usage_history,
    atomic_write_json,
    default_is_pid_alive,
    read_json_file,
)
from colab_cli_comfyui_runner.errors import JobValidationError, PreflightCuError, PreflightLockError
from colab_cli_comfyui_runner.models import (
    BatchRunRequestDto,
    BatchSpec,
    CuUsageRecord,
    ExecutionLock,
    ModelManifest,
)


def test_tc_l1_101_atomic_write_fault_injection_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-L1-101: os.replace失敗時に既存ファイルが無傷で、.tmpファイルが清掃されること。"""
    target = tmp_path / "logs" / "batch.json"
    atomic_write_json(target, {"status": "original"})
    orphan_tmp = target.parent / "batch.json.tmp.999999"
    orphan_tmp.write_text("stale", encoding="utf-8")

    def _boom(_src: Path, _dst: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_json(target, {"status": "corrupted"})

    assert read_json_file(target) == {"status": "original"}
    own_tmp = target.parent / f"{target.name}.tmp.{os.getpid()}"
    assert not own_tmp.exists()
    assert orphan_tmp.exists()


def test_tc_l1_102_exclusive_lock_rejects_second_acquire(tmp_path: Path) -> None:
    """TC-L1-102: 生存中プロセスのロックがある場合は即時 ERR-PREFLIGHT-LOCK で拒否すること。"""
    lock_path = tmp_path / "state" / "active_batch.lock"
    mgr = LockManager(lock_path, is_pid_alive=lambda _pid: True, hostname_provider=lambda: "host-a")
    first = mgr.acquire("b1", has_active_session=lambda _s: False)
    assert first.batch_id == "b1"

    with pytest.raises(PreflightLockError) as exc_info:
        mgr.acquire("b2", has_active_session=lambda _s: False)
    assert exc_info.value.error_code == "ERR-PREFLIGHT-LOCK"

    mgr.release(first)
    assert not lock_path.exists()


@pytest.mark.parametrize(
    ("pid_alive", "session_name", "session_exists", "should_succeed"),
    [
        (True, None, False, False),
        (False, None, False, True),
        (False, "ccr-old", False, True),
        (False, "ccr-old", True, False),
    ],
)
def test_tc_l1_103_stale_lock_four_quadrants(
    tmp_path: Path,
    pid_alive: bool,
    session_name: str | None,
    session_exists: bool,
    should_succeed: bool,
) -> None:
    """TC-L1-103: stale lock 4象限マトリクス検証。"""
    lock_path = tmp_path / "state" / "active_batch.lock"
    old_time = (datetime.now(timezone.utc).astimezone() - timedelta(minutes=5)).isoformat(timespec="seconds")
    atomic_write_json(
        lock_path,
        ExecutionLock(
            lock_id="old-lock",
            batch_id="old-batch",
            pid=12345,
            hostname="host-a",
            acquired_at=old_time,
            session_name=session_name,
        ).to_dict(),
    )
    mgr = LockManager(lock_path, is_pid_alive=lambda _pid: pid_alive, hostname_provider=lambda: "host-a")

    if should_succeed:
        new_lock = mgr.acquire("new-batch", has_active_session=lambda _s: session_exists)
        assert new_lock.batch_id == "new-batch"
    else:
        with pytest.raises(PreflightLockError):
            mgr.acquire("new-batch", has_active_session=lambda _s: session_exists)


@pytest.mark.parametrize(
    "override",
    [
        {"duration_sec": 3},
        {"duration_sec": 16},
        {"width": 961},
        {"width": 0},
        {"height": -2},
        {"seed": -1},
        {"input_assets": {"img": {"source": "drive", "path": "../secret.png"}}},
    ],
)
def test_tc_l1_104_batch_validation_boundaries(override: dict[str, object]) -> None:
    """TC-L1-104: 尺・解像度・パストラバーサルの境界値違反を拒否すること。"""
    job: dict[str, object] = {"job_id": "j1", "prompt": "hello"}
    job.update(override)
    raw = {
        "schema_version": "1.0",
        "batch_id": "b1",
        "model_name": "dummy_model",
        "max_batch_duration_sec": 3600,
        "min_required_cu": 10.0,
        "jobs": [job],
    }
    with pytest.raises(JobValidationError):
        BatchSpec.from_dict(raw)


def test_tc_l1_104_unknown_schema_version_rejected() -> None:
    """TC-L1-104: 未知の schema_version を即座にエラー終了すること。"""
    with pytest.raises(JobValidationError):
        BatchSpec.from_dict(
            {
                "schema_version": "9.9",
                "batch_id": "b1",
                "model_name": "dummy_model",
                "jobs": [{"job_id": "j1", "prompt": "hi"}],
            }
        )
    with pytest.raises(JobValidationError):
        ModelManifest.from_dict({"schema_version": "2.0", "model_name": "dummy_model"})


def test_tc_l1_104_request_dto_rejects_traversal_drive_root(tmp_path: Path) -> None:
    """drive_root のパストラバーサル指定を拒否すること。"""
    with pytest.raises(JobValidationError):
        BatchRunRequestDto(batch_spec_path=tmp_path / "b.json", drive_root="../escape")


def test_tc_l1_105_append_cu_usage_history_jsonl(tmp_path: Path) -> None:
    """TC-L1-105: cu_usage_history.jsonl へ2行独立追記されること。"""
    history_path = tmp_path / "logs" / "cu_usage_history.jsonl"
    for idx in range(2):
        append_cu_usage_history(
            history_path,
            CuUsageRecord(
                execution_id=f"exec-{idx}",
                batch_id="b1",
                model_name="dummy_model",
                session_name=f"ccr-{idx}",
                cu_before=100.0,
                cu_after=98.0,
                cu_consumed=2.0,
                duration_sec=120.0,
                succeeded_count=2,
                failed_count=0,
                recorded_at="2026-11-22T23:00:00+09:00",
            ),
        )
    lines = history_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["execution_id"] == "exec-0"
    assert json.loads(lines[1])["execution_id"] == "exec-1"


def test_cli_parsers_and_dynamic_ssh_options(tmp_path: Path) -> None:
    """colab usage / sessions パーサと動的 ProxyCommand オプションの検証。"""
    assert parse_cu_usage('{"remaining_cu": 185.5}') == 185.5
    assert parse_cu_usage("Remaining compute units: 142.25 CU") == 142.25
    assert (
        parse_cu_usage("Current balance: 600.00 compute units\nUsage rate: 0.00/hr\nActive assignments: 0\n")
        == 600.0
    )
    with pytest.raises(PreflightCuError):
        parse_cu_usage("")

    assert parse_sessions("No active sessions.") == set()
    assert parse_sessions("[colab] No active sessions found on server.\n") == set()
    assert parse_sessions('[{"name": "ccr-1"}, {"name": "ccr-2"}]') == {"ccr-1", "ccr-2"}
    assert parse_sessions("NAME   GPU\nccr-1 A100\n") == {"ccr-1"}

    key = tmp_path / "my key" / "id_ed25519"
    opts = build_ssh_options("ccr-20261122-001", key)
    joined = " ".join(opts)
    assert "-i " in joined
    assert "colab ssh --proxy-mode -s ccr-20261122-001 -i" in joined
    assert "StrictHostKeyChecking=no" in joined
    assert "LogLevel=ERROR" in joined


def test_default_is_pid_alive_and_lock_edge_cases(tmp_path: Path) -> None:
    """OSのPID生存確認および破損ロック・別ホスト・lock_id不一致解放を検証する。"""
    assert default_is_pid_alive(os.getpid()) is True
    assert default_is_pid_alive(-1) is False
    assert default_is_pid_alive(99999999) is False

    lock_path = tmp_path / "state" / "active_batch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("{broken-json", encoding="utf-8")
    mgr = LockManager(lock_path, is_pid_alive=lambda _p: True, hostname_provider=lambda: "host-a")
    with pytest.raises(PreflightLockError, match="破損"):
        mgr.acquire("b1", has_active_session=lambda _s: False)

    # 別ホスト所有ロックは回収しない
    atomic_write_json(
        lock_path,
        ExecutionLock(
            lock_id="other-id",
            batch_id="b0",
            pid=999,
            hostname="other-host",
            acquired_at="2026-11-22T23:00:00+09:00",
        ).to_dict(),
    )
    with pytest.raises(PreflightLockError, match="別ホスト"):
        mgr.acquire("b1", has_active_session=lambda _s: False)

    # lock_id 不一致時は他人のロックを削除しない
    mgr.release(
        ExecutionLock(
            lock_id="my-id",
            batch_id="b1",
            pid=1,
            hostname="host-a",
            acquired_at="2026-11-22T23:00:00+09:00",
        )
    )
    assert lock_path.exists()
