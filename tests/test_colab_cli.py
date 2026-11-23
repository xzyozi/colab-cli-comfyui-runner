"""SubprocessColabCliRunner および OpenSshTransport の単体テスト。"""

from __future__ import annotations

import io
from pathlib import Path
import subprocess
from typing import Any

import pytest

from colab_cli_comfyui_runner.colab_cli import OpenSshTransport, SubprocessColabCliRunner, _is_local_port_open
from colab_cli_comfyui_runner.errors import (
    PreflightCuError,
    SetupComfyError,
    SetupDriveMissingError,
    VmProvisionError,
)


def _completed(args: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def test_subprocess_colab_cli_runner_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """SubprocessColabCliRunner の全CLIサブコマンド成功・異常系を検証する。"""
    recorded: list[tuple[list[str], dict[str, Any]]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded.append((cmd, kwargs))
        if cmd[1] == "usage":
            return _completed(cmd, stdout="Remaining: 123.5 CU")
        if cmd[1] == "sessions":
            return _completed(cmd, stdout='["ccr-1"]')
        return _completed(cmd)

    monkeypatch.setattr("colab_cli_comfyui_runner.colab_cli.subprocess.run", _fake_run)
    runner = SubprocessColabCliRunner()

    assert runner.get_usage() == 123.5
    assert runner.list_sessions() == {"ccr-1"}
    runner.new_session("ccr-1", gpu="A100", high_mem=True)
    runner.drivemount("ccr-1")
    runner.stop_session("ccr-1")

    assert recorded[2][0] == ["colab", "new", "-s", "ccr-1", "--gpu", "A100", "--high-mem"]
    assert recorded[2][1]["capture_output"] is True
    assert recorded[3][0] == ["colab", "drivemount", "-s", "ccr-1"]
    assert recorded[3][1]["capture_output"] is False
    assert recorded[3][1]["timeout"] == 600
    assert recorded[4][0] == ["colab", "stop", "-s", "ccr-1"]

    # 非ゼロ終了コード時の例外種別確認（capture_output=False時のstderr=None含む）
    monkeypatch.setattr(
        "colab_cli_comfyui_runner.colab_cli.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout=None if kw.get("capture_output") is False else "",
            stderr=None if kw.get("capture_output") is False else "boom",
        ),
    )
    with pytest.raises(PreflightCuError):
        runner.get_usage()
    with pytest.raises(VmProvisionError):
        runner.list_sessions()
    with pytest.raises(VmProvisionError):
        runner.new_session("ccr-1")
    with pytest.raises(VmProvisionError):
        runner.drivemount("ccr-1")
    with pytest.raises(VmProvisionError):
        runner.stop_session("ccr-1")

    # TimeoutExpired 時の例外変換確認
    def _timeout(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, timeout=5)

    monkeypatch.setattr("colab_cli_comfyui_runner.colab_cli.subprocess.run", _timeout)
    with pytest.raises(PreflightCuError):
        runner.get_usage()
    with pytest.raises(VmProvisionError):
        runner.new_session("ccr-1")


class _FakePopen:
    def __init__(self, *, exit_code: int | None = None, wait_timeout_once: bool = False) -> None:
        self.returncode = exit_code
        self._wait_timeout_once = wait_timeout_once
        self.terminated = False
        self.killed = False
        self.stderr = io.StringIO("tunnel failed")

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: int | None = None) -> int:
        if self._wait_timeout_once:
            self._wait_timeout_once = False
            raise subprocess.TimeoutExpired(["ssh"], timeout=timeout or 5)
        self.returncode = 0
        return 0


def test_open_ssh_transport_tunnel_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenSshTransport.start_tunnel の開通成功・タイムアウト・異常終了を検証する。"""
    key = tmp_path / "id_ed25519"
    key.write_text("KEY", encoding="utf-8")
    transport = OpenSshTransport()

    # 正常開通 + terminate タイムアウト時の kill フォールバック
    popen_ok = _FakePopen(exit_code=None, wait_timeout_once=True)
    monkeypatch.setattr("colab_cli_comfyui_runner.colab_cli.subprocess.Popen", lambda *_a, **_kw: popen_ok)
    monkeypatch.setattr("colab_cli_comfyui_runner.colab_cli._is_local_port_open", lambda _p: True)

    handle = transport.start_tunnel("ccr-1", key, local_port=18188, timeout_sec=5)
    handle.close()
    assert popen_ok.terminated is True
    assert popen_ok.killed is True

    # プロセス早期終了時
    popen_dead = _FakePopen(exit_code=255)
    monkeypatch.setattr("colab_cli_comfyui_runner.colab_cli.subprocess.Popen", lambda *_a, **_kw: popen_dead)
    with pytest.raises(VmProvisionError, match="異常終了"):
        transport.start_tunnel("ccr-1", key, timeout_sec=5)

    # ポート開通タイムアウト時
    popen_hang = _FakePopen(exit_code=None)
    ticks = iter([0.0, 0.1, 10.0])
    monkeypatch.setattr("colab_cli_comfyui_runner.colab_cli.subprocess.Popen", lambda *_a, **_kw: popen_hang)
    monkeypatch.setattr("colab_cli_comfyui_runner.colab_cli._is_local_port_open", lambda _p: False)
    monkeypatch.setattr("colab_cli_comfyui_runner.colab_cli.time.monotonic", lambda: next(ticks, 10.0))
    monkeypatch.setattr("colab_cli_comfyui_runner.colab_cli.time.sleep", lambda _s: None)
    with pytest.raises(VmProvisionError, match="疎通しませんでした"):
        transport.start_tunnel("ccr-1", key, timeout_sec=5)
    assert popen_hang.terminated is True


def test_open_ssh_transport_deliver_and_remote_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """deliver_files と run_remote_setup の成功・exit code 2 (Drive欠落)・その他エラーを検証する。"""
    key = tmp_path / "id_ed25519"
    key.write_text("KEY", encoding="utf-8")
    setup_sh = tmp_path / "setup.sh"
    setup_sh.write_text("#!/bin/bash\n", encoding="utf-8")
    transport = OpenSshTransport()

    # 空ファイル配列は即リターン
    transport.deliver_files("ccr-1", key, [])

    recorded: list[list[str]] = []
    recorded_kwargs: list[dict[str, Any]] = []

    def _run_ok(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        recorded.append(cmd)
        recorded_kwargs.append(kwargs)
        return _completed(cmd, returncode=0)

    monkeypatch.setattr("colab_cli_comfyui_runner.colab_cli.subprocess.run", _run_ok)
    transport.deliver_files("ccr-1", key, [setup_sh])
    assert Path(recorded[0][0]).stem == "ssh"
    assert recorded[0][-2] == "root@colab"
    assert Path(recorded[1][0]).stem == "scp"
    assert any(part.startswith("root@colab:") for part in recorded[1])
    assert any("ProxyCommand=colab ssh --proxy-mode -s ccr-1" in part for part in recorded[1])

    transport.run_remote_setup(
        "ccr-1",
        key,
        model_name="minimax_h3",
        drive_root="ComfyUI",
        required_drive_assets=["models/diffusion_models/a.safetensors"],
    )
    setup_input = recorded_kwargs[2]["input"]
    assert isinstance(setup_input, bytes)
    assert b"\r" not in setup_input
    assert setup_input == b"models/diffusion_models/a.safetensors\n"

    # exit code 2 -> SetupDriveMissingError (bytes stderr でも変換できること)
    monkeypatch.setattr(
        "colab_cli_comfyui_runner.colab_cli.subprocess.run",
        lambda cmd, **_kw: subprocess.CompletedProcess(args=cmd, returncode=2, stdout=b"", stderr=b"missing asset"),
    )
    with pytest.raises(SetupDriveMissingError):
        transport.run_remote_setup(
            "ccr-1",
            key,
            model_name="minimax_h3",
            drive_root="ComfyUI",
            required_drive_assets=["models/a.safetensors"],
        )

    # exit code 1 -> SetupComfyError
    monkeypatch.setattr(
        "colab_cli_comfyui_runner.colab_cli.subprocess.run",
        lambda cmd, **_kw: _completed(cmd, returncode=1, stderr="pip failed"),
    )
    with pytest.raises(SetupComfyError):
        transport.run_remote_setup(
            "ccr-1",
            key,
            model_name="minimax_h3",
            drive_root="ComfyUI",
            required_drive_assets=["models/a.safetensors"],
        )
    with pytest.raises(SetupComfyError):
        transport.deliver_files("ccr-1", key, [setup_sh])


def test_is_local_port_open_closed_port() -> None:
    """未開放ポートに対して False を返すこと。"""
    assert _is_local_port_open(59999) is False
