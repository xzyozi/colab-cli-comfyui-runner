"""L1テスト用の共通フィクスチャとフェイクSeam定義。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import pytest

from colab_cli_comfyui_runner.errors import (
    JobApiTransientError,
    JobExecutionError,
    SetupDriveMissingError,
    VmProvisionError,
)


@dataclass
class FakeTunnelHandle:
    """SSHトンネルハンドルのフェイク実装。"""

    closed: bool = False

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeColabCliRunner:
    """`ColabCliRunner` Seamのメモリ上フェイク実装。"""

    cu_balances: list[float] = field(default_factory=lambda: [180.0, 178.5])
    active_sessions: set[str] = field(default_factory=set)
    calls: list[tuple[Any, ...]] = field(default_factory=list)
    fail_drivemount: bool = False
    failing_new_profiles: set[tuple[str, bool]] = field(default_factory=set)
    stop_failures_remaining: int = 0
    keep_session_after_stop: bool = False

    def get_usage(self, *, timeout_sec: int = 30) -> float:
        self.calls.append(("usage", timeout_sec))
        if len(self.cu_balances) > 1:
            return self.cu_balances.pop(0)
        return self.cu_balances[0]

    def list_sessions(self, *, timeout_sec: int = 30) -> set[str]:
        self.calls.append(("sessions", set(self.active_sessions)))
        return set(self.active_sessions)

    def new_session(
        self,
        session_name: str,
        *,
        gpu: str = "A100",
        high_mem: bool = True,
        timeout_sec: int = 300,
    ) -> None:
        self.calls.append(("new", session_name, gpu, high_mem))
        if (gpu, high_mem) in self.failing_new_profiles:
            raise VmProvisionError(f"Service Unavailable: {gpu} high_mem={high_mem}")
        self.active_sessions.add(session_name)

    def drivemount(self, session_name: str, *, timeout_sec: int = 180) -> None:
        self.calls.append(("drivemount", session_name))
        if self.fail_drivemount:
            raise RuntimeError("drivemount failed")

    def stop_session(self, session_name: str, *, timeout_sec: int = 60) -> None:
        self.calls.append(("stop", session_name))
        if self.stop_failures_remaining > 0:
            self.stop_failures_remaining -= 1
            raise RuntimeError("stop failed")
        if not self.keep_session_after_stop:
            self.active_sessions.discard(session_name)


@dataclass
class FakeSshTransport:
    """`SshTransport` Seamのフェイク実装。"""

    calls: list[tuple[Any, ...]] = field(default_factory=list)
    delivered_files: list[Path] = field(default_factory=list)
    fail_drive_missing: bool = False
    tunnel_handle: FakeTunnelHandle = field(default_factory=FakeTunnelHandle)

    def start_tunnel(
        self,
        session_name: str,
        ssh_key_path: Path,
        *,
        local_port: int = 8188,
        remote_port: int = 8188,
        timeout_sec: int = 60,
    ) -> FakeTunnelHandle:
        self.calls.append(("tunnel", session_name, local_port))
        return self.tunnel_handle

    def deliver_files(
        self,
        session_name: str,
        ssh_key_path: Path,
        files: Sequence[Path],
        *,
        remote_dir: str = "/tmp/ccr_setup",
        timeout_sec: int = 60,
    ) -> None:
        self.delivered_files = list(files)
        self.calls.append(("deliver", [f.name for f in files], remote_dir))

    def run_remote_setup(
        self,
        session_name: str,
        ssh_key_path: Path,
        *,
        model_name: str,
        drive_root: str,
        required_drive_assets: Sequence[str],
        remote_dir: str = "/tmp/ccr_setup",
        timeout_sec: int = 600,
    ) -> None:
        self.calls.append(("setup", model_name, drive_root, list(required_drive_assets)))
        if self.fail_drive_missing:
            raise SetupDriveMissingError("missing model asset on Drive")


@dataclass
class FakeComfyApiClient:
    """`ComfyApiClient` Seamのフェイク実装。"""

    submitted_workflows: list[dict[str, Any]] = field(default_factory=list)
    uploaded_paths: list[Path] = field(default_factory=list)
    interrupt_called: int = 0
    fail_on_job_indices: set[int] = field(default_factory=set)
    transient_submit_failures: int = 0
    on_wait_callback: Any = None

    def wait_ready(self, *, timeout_sec: int = 180, poll_interval_sec: float = 5.0) -> None:
        return None

    def upload_image(self, image_path: Path) -> str:
        self.uploaded_paths.append(image_path)
        return f"uploaded_{image_path.name}"

    def submit_prompt(self, workflow: dict[str, Any], *, client_id: str) -> str:
        if self.transient_submit_failures > 0:
            self.transient_submit_failures -= 1
            raise JobApiTransientError("temporary 503")
        self.submitted_workflows.append(workflow)
        return f"prompt-{len(self.submitted_workflows)}"

    def wait_for_completion(self, prompt_id: str, *, client_id: str, timeout_sec: int) -> list[str]:
        idx = len(self.submitted_workflows) - 1
        if self.on_wait_callback is not None:
            self.on_wait_callback(idx, prompt_id)
        if idx in self.fail_on_job_indices:
            raise JobExecutionError(f"execution failed on job index {idx}")
        return [f"dummy_model/b1/job_{idx + 1}_00001.mp4"]

    def interrupt(self) -> None:
        self.interrupt_called += 1


@pytest.fixture
def sample_workspace(tmp_path: Path) -> Path:
    """モデル非依存構成の最小ワークスペースを `tmp_path` に作成する。"""
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts" / "setup.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    ssh_key = tmp_path / "id_ed25519"
    ssh_key.write_text("DUMMY_KEY_FOR_TEST", encoding="utf-8")

    model_dir = tmp_path / "workflows" / "dummy_model"
    model_dir.mkdir(parents=True)
    (model_dir / "requirements.txt").write_text("einops\n", encoding="utf-8")
    (model_dir / "nodes.txt").write_text("https://example.com/custom-node.git\n", encoding="utf-8")

    manifest = {
        "schema_version": "1.0",
        "model_name": "dummy_model",
        "workflow_template_path": "workflow_api.json",
        "required_drive_assets": ["models/diffusion_models/dummy.safetensors"],
        "node_mappings": {
            "prompt": {"node_id": "1", "input_key": "text"},
            "negative_prompt": {"node_id": "2", "input_key": "text"},
            "width": {"node_id": "3", "input_key": "width"},
            "height": {"node_id": "3", "input_key": "height"},
            "duration_sec": {"node_id": "3", "input_key": "duration"},
            "seed": {"node_id": "4", "input_key": "seed"},
            "output_prefix": {"node_id": "5", "input_key": "filename_prefix"},
            "start_image": {"node_id": "6", "input_key": "image"},
            "ref_image": {"node_id": "7", "input_key": "image"},
        },
        "default_params": {
            "width": 960,
            "height": 544,
            "duration_sec": 5,
            "seed": 42,
        },
        "requirements_path": "requirements.txt",
        "nodes_path": "nodes.txt",
        "default_job_timeout_sec": 900,
    }
    (model_dir / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    workflow_tpl = {
        "1": {"inputs": {"text": "TEMPLATE_PROMPT"}},
        "2": {"inputs": {"text": ""}},
        "3": {"inputs": {"width": 960, "height": 544, "duration": 5}},
        "4": {"inputs": {"seed": 42}},
        "5": {"inputs": {"filename_prefix": "TEMPLATE"}},
        "6": {"inputs": {"image": "TEMPLATE_IMG"}},
        "7": {"inputs": {"image": "TEMPLATE_REF"}},
    }
    (model_dir / "workflow_api.json").write_text(json.dumps(workflow_tpl), encoding="utf-8")

    (tmp_path / "batches").mkdir()
    batch_spec = {
        "schema_version": "1.0",
        "batch_id": "b1",
        "model_name": "dummy_model",
        "max_batch_duration_sec": 3600,
        "min_required_cu": 10.0,
        "jobs": [
            {"job_id": "job_1", "prompt": "cat running", "width": 1344, "height": 768, "duration_sec": 10},
            {"job_id": "job_2", "prompt": "dog jumping"},
        ],
    }
    (tmp_path / "batches" / "b1.json").write_text(json.dumps(batch_spec), encoding="utf-8")
    return tmp_path
