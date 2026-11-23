"""パイプライン制御（F-001〜F-006）および安全回路のL1テスト（TC-L1-001〜010）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from colab_cli_comfyui_runner.cli import main as cli_main
from colab_cli_comfyui_runner.dao import LockManager, read_json_file
from colab_cli_comfyui_runner.errors import PreflightCuError, StopFailureError
from colab_cli_comfyui_runner.models import BatchRunRequestDto
from colab_cli_comfyui_runner.pipeline import run_batch_pipeline
from tests.conftest import FakeColabCliRunner, FakeComfyApiClient, FakeSshTransport


def _make_request(workspace: Path, *, dry_run: bool = False) -> BatchRunRequestDto:
    return BatchRunRequestDto(
        batch_spec_path=Path("batches/b1.json"),
        dry_run=dry_run,
        ssh_key_path=workspace / "id_ed25519",
        base_dir=workspace,
    )


def test_tc_l1_001_preflight_rejects_low_cu_and_active_session(sample_workspace: Path) -> None:
    """TC-L1-001: CU不足および既存セッション残存時にVMを起動せず拒否しロックを解放すること。"""
    colab_low = FakeColabCliRunner(cu_balances=[5.0])
    ssh = FakeSshTransport()
    with pytest.raises(PreflightCuError):
        run_batch_pipeline(_make_request(sample_workspace), colab=colab_low, ssh=ssh)
    assert not any(c[0] == "new" for c in colab_low.calls)
    assert not (sample_workspace / "state" / "active_batch.lock").exists()

    colab_busy = FakeColabCliRunner(cu_balances=[150.0], active_sessions={"existing-vm"})
    with pytest.raises(PreflightCuError):
        run_batch_pipeline(_make_request(sample_workspace), colab=colab_busy, ssh=ssh)
    assert not any(c[0] == "new" for c in colab_busy.calls)
    assert not (sample_workspace / "state" / "active_batch.lock").exists()


def test_tc_l1_002_and_003_order_session_recording_and_delivery(sample_workspace: Path) -> None:
    """TC-L1-002 / TC-L1-003: 起動順序、lock内session_name事前記録、配送対象、Drive不在即停止を検証。"""
    lock_path = sample_workspace / "state" / "active_batch.lock"
    observed_lock_sessions: list[str | None] = []

    class InspectingColab(FakeColabCliRunner):
        def new_session(
            self, session_name: str, *, gpu: str = "A100", high_mem: bool = True, timeout_sec: int = 300
        ) -> None:
            observed_lock_sessions.append(read_json_file(lock_path).get("session_name"))
            super().new_session(session_name, gpu=gpu, high_mem=high_mem, timeout_sec=timeout_sec)

    colab = InspectingColab(cu_balances=[100.0, 98.0])
    ssh = FakeSshTransport()
    api = FakeComfyApiClient()
    summary = run_batch_pipeline(
        _make_request(sample_workspace),
        colab=colab,
        ssh=ssh,
        api_client_factory=lambda _port: api,
    )

    assert summary.final_status == "completed"
    assert observed_lock_sessions and observed_lock_sessions[0] is not None
    assert [c[0] for c in colab.calls[:4]] == ["sessions", "usage", "new", "drivemount"]
    assert [f.name for f in ssh.delivered_files] == ["setup.sh", "requirements.txt", "nodes.txt"]

    # Driveモデル欠落時は ERR-SETUP-DRIVE-MISSING かつ必ず colab stop が呼ばれること
    colab2 = FakeColabCliRunner(cu_balances=[100.0, 99.5])
    ssh_missing = FakeSshTransport(fail_drive_missing=True)
    summary2 = run_batch_pipeline(
        _make_request(sample_workspace),
        colab=colab2,
        ssh=ssh_missing,
        api_client_factory=lambda _port: api,
    )
    assert summary2.final_status == "aborted"
    assert summary2.error_code == "ERR-SETUP-DRIVE-MISSING"
    assert summary2.vm_stopped_verified is True
    assert any(c[0] == "stop" for c in colab2.calls)


def test_tc_l1_004_and_006_template_non_pollution_and_input_assets(sample_workspace: Path) -> None:
    """TC-L1-004 / TC-L1-006: テンプレート非汚染・output_prefix・input_assets解決を検証。"""
    local_img = sample_workspace / "start.png"
    local_img.write_bytes(b"PNG")
    batch_data = {
        "schema_version": "1.0",
        "batch_id": "b1",
        "model_name": "dummy_model",
        "max_batch_duration_sec": 3600,
        "min_required_cu": 10.0,
        "jobs": [
            {
                "job_id": "job_1",
                "prompt": "first prompt",
                "width": 1344,
                "height": 768,
                "duration_sec": 10,
                "input_assets": {
                    "start_image": {"source": "local", "path": "start.png"},
                    "ref_image": {"source": "drive", "path": "refs/hero.png"},
                },
            },
            {
                "job_id": "job_2",
                "prompt": "second prompt",
            },
        ],
    }
    (sample_workspace / "batches" / "b1.json").write_text(json.dumps(batch_data), encoding="utf-8")

    colab = FakeColabCliRunner(cu_balances=[100.0, 97.0])
    ssh = FakeSshTransport()
    api = FakeComfyApiClient(transient_submit_failures=1)
    summary = run_batch_pipeline(
        _make_request(sample_workspace),
        colab=colab,
        ssh=ssh,
        api_client_factory=lambda _port: api,
    )

    assert summary.final_status == "completed"
    assert summary.succeeded_count == 2
    assert len(api.uploaded_paths) == 1
    wf1, wf2 = api.submitted_workflows
    assert wf1["3"]["inputs"]["width"] == 1344
    assert wf1["5"]["inputs"]["filename_prefix"] == "dummy_model/b1/job_1"
    assert wf1["6"]["inputs"]["image"] == "uploaded_start.png"
    assert wf1["7"]["inputs"]["image"] == "drive_assets/refs/hero.png"
    # ジョブ2は既定値を使い、ジョブ1の値やinput_assetsが漏れないこと
    assert wf2["3"]["inputs"]["width"] == 960
    assert wf2["5"]["inputs"]["filename_prefix"] == "dummy_model/b1/job_2"
    assert wf2["6"]["inputs"]["image"] == "TEMPLATE_IMG"


def test_tc_l1_008_budget_and_consecutive_failures_skip_remaining(sample_workspace: Path) -> None:
    """TC-L1-008: 時間予算超過および連続2件失敗で残ジョブを skipped にして安全停止すること。"""
    batch_data = {
        "schema_version": "1.0",
        "batch_id": "b1",
        "model_name": "dummy_model",
        "max_batch_duration_sec": 600,
        "min_required_cu": 10.0,
        "jobs": [
            {"job_id": "j1", "prompt": "p1"},
            {"job_id": "j2", "prompt": "p2"},
            {"job_id": "j3", "prompt": "p3"},
        ],
    }
    (sample_workspace / "batches" / "b1.json").write_text(json.dumps(batch_data), encoding="utf-8")

    # 1) 時間予算超過
    ticks = iter([0.0, 10.0, 10.0, 20.0, 650.0, 660.0])
    colab = FakeColabCliRunner(cu_balances=[50.0, 49.0])
    summary = run_batch_pipeline(
        _make_request(sample_workspace),
        colab=colab,
        ssh=FakeSshTransport(),
        api_client_factory=lambda _port: FakeComfyApiClient(),
        clock=lambda: next(ticks, 700.0),
    )
    assert summary.succeeded_count == 1
    assert summary.skipped_count == 2
    assert summary.vm_stopped_verified is True

    # 2) 連続2件失敗
    colab2 = FakeColabCliRunner(cu_balances=[50.0, 49.0])
    summary2 = run_batch_pipeline(
        _make_request(sample_workspace),
        colab=colab2,
        ssh=FakeSshTransport(),
        api_client_factory=lambda _port: FakeComfyApiClient(fail_on_job_indices={0, 1}),
    )
    assert summary2.final_status == "aborted"
    assert summary2.failed_count == 2
    assert summary2.skipped_count == 1
    assert summary2.vm_stopped_verified is True


def test_tc_l1_009_always_stops_vm_on_drivemount_failure_and_sigint(sample_workspace: Path) -> None:
    """TC-L1-009: drivemount失敗および推論中SIGINT割り込みで必ず colab stop と sessions空確認が走ること。"""
    # drivemount 失敗
    colab_dm = FakeColabCliRunner(cu_balances=[100.0, 99.9], fail_drivemount=True)
    ssh_dm = FakeSshTransport()
    summary_dm = run_batch_pipeline(
        _make_request(sample_workspace),
        colab=colab_dm,
        ssh=ssh_dm,
        api_client_factory=lambda _port: FakeComfyApiClient(),
    )
    assert summary_dm.final_status == "aborted"
    assert summary_dm.vm_stopped_verified is True
    assert any(c[0] == "stop" for c in colab_dm.calls)
    assert not (sample_workspace / "state" / "active_batch.lock").exists()

    # 2本目実行中の割り込み (KeyboardInterrupt)
    colab_sig = FakeColabCliRunner(cu_balances=[100.0, 98.5])
    ssh_sig = FakeSshTransport()

    def _interrupt_on_second(idx: int, _prompt_id: str) -> None:
        if idx == 1:
            raise KeyboardInterrupt

    api_sig = FakeComfyApiClient(on_wait_callback=_interrupt_on_second)
    summary_sig = run_batch_pipeline(
        _make_request(sample_workspace),
        colab=colab_sig,
        ssh=ssh_sig,
        api_client_factory=lambda _port: api_sig,
    )
    assert summary_sig.final_status == "aborted"
    assert summary_sig.succeeded_count == 1
    assert summary_sig.skipped_count == 0
    assert summary_sig.vm_stopped_verified is True
    assert ssh_sig.tunnel_handle.closed is True
    assert not (sample_workspace / "state" / "active_batch.lock").exists()


def test_tc_l1_010_colab_stop_retry_and_failure_alert(sample_workspace: Path) -> None:
    """TC-L1-010: colab stop が2回失敗後3回目で成功、および3回失敗で StopFailureError を送出すること。"""
    colab_ok = FakeColabCliRunner(cu_balances=[100.0, 99.0], stop_failures_remaining=2)
    summary_ok = run_batch_pipeline(
        _make_request(sample_workspace),
        colab=colab_ok,
        ssh=FakeSshTransport(),
        api_client_factory=lambda _port: FakeComfyApiClient(),
    )
    assert summary_ok.final_status == "completed"
    assert summary_ok.vm_stopped_verified is True
    assert sum(1 for c in colab_ok.calls if c[0] == "stop") == 3

    colab_fail = FakeColabCliRunner(
        cu_balances=[100.0, 99.0], stop_failures_remaining=5, keep_session_after_stop=True
    )
    with pytest.raises(StopFailureError) as exc_info:
        run_batch_pipeline(
            _make_request(sample_workspace),
            colab=colab_fail,
            ssh=FakeSshTransport(),
            api_client_factory=lambda _port: FakeComfyApiClient(),
        )
    assert exc_info.value.error_code == "ERR-STOP-FAILURE"
    assert exc_info.value.manual_stop_required is True
    assert sum(1 for c in colab_fail.calls if c[0] == "stop") == 3
    # ゾンビVM残存時は多重起動防止のためロックを残置する
    assert (sample_workspace / "state" / "active_batch.lock").exists()


def test_dry_run_and_cli_stdout_json(
    sample_workspace: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run 実行時にVM起動せず終了コード0でJSONサマリをstdoutへ出力すること。"""
    monkeypatch.setattr(
        "colab_cli_comfyui_runner.pipeline.SubprocessColabCliRunner",
        lambda: FakeColabCliRunner(cu_balances=[120.0]),
    )
    exit_code = cli_main(
        [
            "--batch",
            "batches/b1.json",
            "--dry-run",
            "--base-dir",
            str(sample_workspace),
        ]
    )
    assert exit_code == 0
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert payload["final_status"] == "completed"
    assert payload["cu_consumed"] == 0.0
    assert payload["skipped_count"] == 2


def test_tc_l1_011_vm_profile_fallback_above_l4_only(sample_workspace: Path) -> None:
    """L4より上位GPUのみフォールバック試行し、L4/T4/CPUへは落とさず中断すること。"""
    # A100 Standard が 503 で失敗 -> G4 へフォールバック成功
    colab_fb = FakeColabCliRunner(
        cu_balances=[100.0, 99.0],
        failing_new_profiles={("A100", False)},
    )
    summary = run_batch_pipeline(
        _make_request(sample_workspace),
        colab=colab_fb,
        ssh=FakeSshTransport(),
        api_client_factory=lambda _port: FakeComfyApiClient(),
    )
    assert summary.final_status == "completed"
    new_calls = [(c[2], c[3]) for c in colab_fb.calls if c[0] == "new"]
    assert new_calls == [("A100", False), ("G4", False)]

    # 全上位プロファイル失敗時は L4 等へ落とさず ERR-VM-PROVISION で安全中断
    colab_all_fail = FakeColabCliRunner(
        cu_balances=[100.0, 100.0],
        failing_new_profiles={("A100", False), ("G4", False), ("A100", True)},
    )
    summary_fail = run_batch_pipeline(
        _make_request(sample_workspace),
        colab=colab_all_fail,
        ssh=FakeSshTransport(),
        api_client_factory=lambda _port: FakeComfyApiClient(),
    )
    assert summary_fail.final_status == "aborted"
    assert summary_fail.error_code == "ERR-VM-PROVISION"
    attempted = [(c[2], c[3]) for c in colab_all_fail.calls if c[0] == "new"]
    assert attempted == [("A100", False), ("G4", False), ("A100", True)]
    assert all(gpu not in ("L4", "T4", "CPU") for gpu, _ in attempted)
    assert not (sample_workspace / "state" / "active_batch.lock").exists()
