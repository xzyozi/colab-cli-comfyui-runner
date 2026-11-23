"""Colab VM・CUライフサイクル制御および動的SSH/SCPトランスポート（CCR-DD-001 C-001）。"""

from __future__ import annotations

from collections.abc import Sequence
import json
import logging
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from typing import Protocol

from colab_cli_comfyui_runner.errors import (
    PreflightCuError,
    SetupComfyError,
    SetupDriveMissingError,
    VmProvisionError,
)

logger = logging.getLogger(__name__)

_CU_LABEL_RE = re.compile(r"(?i)(?:remaining|balance|available|compute\s*units?|cu)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)")
_NUMBER_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
_NO_SESSION_MARKERS = ("no active", "no running", "no sessions", "none", "0 sessions")


def parse_cu_usage(output: str) -> float:
    """`colab usage` の標準出力から残高CU数値を抽出する。

    Args:
        output: `colab usage` の標準出力文字列。

    Returns:
        残高CU数値（0.0以上）。

    Raises:
        PreflightCuError: 出力からCU残高を解釈できない場合。
    """
    stripped = output.strip()
    if not stripped:
        raise PreflightCuError("colab usage の出力が空です。")

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        for key in ("remaining_cu", "remaining", "balance", "available_cu", "compute_units", "cu"):
            val = parsed.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool) and float(val) >= 0.0:
                return float(val)

    match = _CU_LABEL_RE.search(stripped)
    if match:
        return float(match.group(1))

    numbers = _NUMBER_RE.findall(stripped)
    if len(numbers) == 1:
        return float(numbers[0])

    raise PreflightCuError("colab usage の出力からCU残高を解析できませんでした。")


def parse_sessions(output: str) -> set[str]:
    """`colab sessions` の標準出力からアクティブセッション名集合を抽出する。

    Args:
        output: `colab sessions` の標準出力文字列。

    Returns:
        稼働中セッション名の集合。
    """
    stripped = output.strip()
    if not stripped:
        return set()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        names: set[str] = set()
        for entry in parsed:
            if isinstance(entry, str) and entry.strip():
                names.add(entry.strip())
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("session_name") or entry.get("id")
                if isinstance(name, str) and name.strip():
                    names.add(name.strip())
        return names

    sessions: set[str] = set()
    for line in stripped.splitlines():
        clean = line.strip()
        if not clean:
            continue
        lower = clean.lower()
        if any(marker in lower for marker in _NO_SESSION_MARKERS):
            continue
        if clean.startswith(("-", "=")) or lower.startswith(("name ", "session", "id ")):
            continue
        token = clean.split()[0]
        if token:
            sessions.add(token)
    return sessions


def build_ssh_options(session_name: str, ssh_key_path: Path) -> list[str]:
    """動的 `session_name` と秘密鍵絶対パスから OpenSSH オプション配列を生成する。

    Args:
        session_name: Colab セッション名。
        ssh_key_path: `ed25519` 秘密鍵のパス（ログ出力禁止）。

    Returns:
        `ssh` / `scp` に渡す `-o ...` オプションのリスト。
    """
    abs_key = str(ssh_key_path.expanduser().resolve())
    null_hosts = "NUL" if sys.platform == "win32" else "/dev/null"
    proxy_cmd = f'colab ssh --proxy-mode -s {session_name} -i "{abs_key}"'
    return [
        "-i",
        abs_key,
        "-o",
        f"ProxyCommand={proxy_cmd}",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        f"UserKnownHostsFile={null_hosts}",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "BatchMode=yes",
    ]


class TunnelHandle(Protocol):
    """確立済みSSHポートフォワーディングプロセスの制御ハンドル。"""

    def close(self) -> None:
        """SSHトンネルプロセスを終了する。"""


class ColabCliRunner(Protocol):
    """`google-colab-cli` コマンド実行Seam（L1テスト差し替え境界）。"""

    def get_usage(self, *, timeout_sec: int = 30) -> float:
        """現在のCU残高を取得する。"""

    def list_sessions(self, *, timeout_sec: int = 30) -> set[str]:
        """現在のアクティブセッション名一覧を取得する。"""

    def new_session(
        self,
        session_name: str,
        *,
        gpu: str = "A100",
        high_mem: bool = True,
        timeout_sec: int = 300,
    ) -> None:
        """新規Colabセッションを起動する。"""

    def drivemount(self, session_name: str, *, timeout_sec: int = 600) -> None:
        """Colab VMへGoogle Driveをマウントする。"""

    def stop_session(self, session_name: str, *, timeout_sec: int = 60) -> None:
        """指定セッションを停止する。"""


class SshTransport(Protocol):
    """OpenSSH / SCP トランスポートSeam（L1テスト差し替え境界）。"""

    def start_tunnel(
        self,
        session_name: str,
        ssh_key_path: Path,
        *,
        local_port: int = 8188,
        remote_port: int = 8188,
        timeout_sec: int = 60,
    ) -> TunnelHandle:
        """SSHポートフォワーディングを開始し疎通確認後にハンドルを返す。"""

    def deliver_files(
        self,
        session_name: str,
        ssh_key_path: Path,
        files: Sequence[Path],
        *,
        remote_dir: str = "/tmp/ccr_setup",
        timeout_sec: int = 60,
    ) -> None:
        """`setup.sh` およびモデル依存定義をVM一時領域へ配送する。"""

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
        """VM上で `/tmp/ccr_setup/setup.sh` を実行する。"""


class SubprocessColabCliRunner:
    """サブプロセス経由で `google-colab-cli` (`colab`) を実行する実装。"""

    def __init__(self, executable: str = "colab") -> None:
        """ランナーを初期化する。

        Args:
            executable: `colab` CLI実行コマンド名。
        """
        self._executable = executable

    def _run(
        self,
        args: list[str],
        *,
        timeout_sec: int,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [self._executable, *args]
        try:
            return subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VmProvisionError(f"colab CLI 実行に失敗しました ({' '.join(args)}): {exc}") from exc

    def get_usage(self, *, timeout_sec: int = 30) -> float:
        """`colab usage` を実行して残高CUを返す。

        Args:
            timeout_sec: コマンドタイムアウト（秒）。

        Returns:
            残高CU値。

        Raises:
            PreflightCuError: CLIが非ゼロ終了または出力解析不可の場合。
        """
        try:
            proc = self._run(["usage"], timeout_sec=timeout_sec)
        except VmProvisionError as exc:
            raise PreflightCuError(str(exc)) from exc
        if proc.returncode != 0:
            raise PreflightCuError(f"colab usage が失敗しました (code={proc.returncode}): {proc.stderr.strip()}")
        return parse_cu_usage(proc.stdout)

    def list_sessions(self, *, timeout_sec: int = 30) -> set[str]:
        """`colab sessions` を実行してセッション集合を返す。

        Args:
            timeout_sec: コマンドタイムアウト（秒）。

        Returns:
            アクティブセッション名の集合。

        Raises:
            VmProvisionError: CLI実行失敗時。
        """
        proc = self._run(["sessions"], timeout_sec=timeout_sec)
        if proc.returncode != 0:
            raise VmProvisionError(f"colab sessions が失敗しました (code={proc.returncode}): {proc.stderr.strip()}")
        return parse_sessions(proc.stdout)

    def new_session(
        self,
        session_name: str,
        *,
        gpu: str = "A100",
        high_mem: bool = True,
        timeout_sec: int = 300,
    ) -> None:
        """`colab new` でスポットVMを起動する。

        Args:
            session_name: セッション識別名。
            gpu: GPU種別（既定: `A100`）。
            high_mem: High-RAM 必須指定。
            timeout_sec: タイムアウト（秒）。

        Raises:
            VmProvisionError: 起動に失敗した場合。
        """
        args = ["new", "-s", session_name, "--gpu", gpu]
        if high_mem:
            args.append("--high-mem")
        proc = self._run(args, timeout_sec=timeout_sec)
        if proc.returncode != 0:
            raise VmProvisionError(
                f"colab new が失敗しました (session={session_name}, code={proc.returncode}): {proc.stderr.strip()}"
            )

    def drivemount(self, session_name: str, *, timeout_sec: int = 600) -> None:
        """`colab drivemount` で Google Drive をマウントする。

        初回または Drive 権限再承認が必要な場合、`colab drivemount` が対話端末に
        認可 URL と Enter 待ちプロンプトを出すため標準入出力を捕捉せず継承する。

        Args:
            session_name: 対象セッション名。
            timeout_sec: タイムアウト（秒、既定: `colab_cli` 準拠の 600 秒）。

        Raises:
            VmProvisionError: マウントに失敗した場合。
        """
        proc = self._run(["drivemount", "-s", session_name], timeout_sec=timeout_sec, capture_output=False)
        if proc.returncode != 0:
            stderr_msg = (proc.stderr or "").strip()
            raise VmProvisionError(
                f"colab drivemount が失敗しました (session={session_name}, code={proc.returncode}): {stderr_msg}"
            )

    def stop_session(self, session_name: str, *, timeout_sec: int = 60) -> None:
        """`colab stop` でセッションを停止する。

        Args:
            session_name: 停止対象セッション名。
            timeout_sec: タイムアウト（秒）。

        Raises:
            VmProvisionError: 停止コマンドが失敗した場合。
        """
        proc = self._run(["stop", "-s", session_name], timeout_sec=timeout_sec)
        if proc.returncode != 0:
            raise VmProvisionError(
                f"colab stop が失敗しました (session={session_name}, code={proc.returncode}): {proc.stderr.strip()}"
            )


class SubprocessTunnelHandle:
    """`ssh -N -L` バックグラウンドプロセスのハンドル。"""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        """プロセスハンドルを初期化する。

        Args:
            process: 起動済みSSH `Popen` オブジェクト。
        """
        self._process = process

    def close(self) -> None:
        """SSHトンネルプロセスを安全に停止する。"""
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)


def _is_local_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _resolve_openssh_binary(binary_name: str) -> str:
    found = shutil.which(binary_name)
    if found:
        return found
    if sys.platform == "win32":
        system32_candidate = Path(r"C:\Windows\System32\OpenSSH") / f"{binary_name}.exe"
        if system32_candidate.is_file():
            return str(system32_candidate)
    return binary_name


class OpenSshTransport:
    """OpenSSH / SCP を用いて動的 `ProxyCommand` 経由でVMと通信する実装。"""

    def __init__(self, ssh_bin: str = "ssh", scp_bin: str = "scp", remote_host: str = "root@colab") -> None:
        """トランスポートを初期化する。

        Args:
            ssh_bin: `ssh` 実行ファイル名。
            scp_bin: `scp` 実行ファイル名。
            remote_host: SSHターゲットホスト表記。
        """
        self._ssh_bin = _resolve_openssh_binary(ssh_bin)
        self._scp_bin = _resolve_openssh_binary(scp_bin)
        self._remote_host = remote_host

    def start_tunnel(
        self,
        session_name: str,
        ssh_key_path: Path,
        *,
        local_port: int = 8188,
        remote_port: int = 8188,
        timeout_sec: int = 60,
    ) -> TunnelHandle:
        """SSHポート転送バックグラウンドプロセスを起動し、待機する。

        Args:
            session_name: Colab セッション名。
            ssh_key_path: `ed25519` 秘密鍵パス。
            local_port: ローカル転送先ポート。
            remote_port: VM側ポート。
            timeout_sec: トンネル開通待機タイムアウト（秒）。

        Returns:
            開通済み `TunnelHandle`。

        Raises:
            VmProvisionError: プロセス終了またはタイムアウト時。
        """
        opts = build_ssh_options(session_name, ssh_key_path)
        cmd = [
            self._ssh_bin,
            "-N",
            "-o",
            "ExitOnForwardFailure=yes",
            "-L",
            f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
            *opts,
            self._remote_host,
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise VmProvisionError(f"SSHトンネル起動に失敗しました: {exc}") from exc

        handle = SubprocessTunnelHandle(proc)
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stderr_text = proc.stderr.read() if proc.stderr is not None else ""
                raise VmProvisionError(f"SSHトンネルが異常終了しました (code={proc.returncode}): {stderr_text.strip()}")
            if _is_local_port_open(local_port):
                return handle
            time.sleep(0.5)

        handle.close()
        raise VmProvisionError(f"SSHポート転送 (127.0.0.1:{local_port}) が {timeout_sec}秒以内に疎通しませんでした。")

    def deliver_files(
        self,
        session_name: str,
        ssh_key_path: Path,
        files: Sequence[Path],
        *,
        remote_dir: str = "/tmp/ccr_setup",
        timeout_sec: int = 60,
    ) -> None:
        """動的 `ProxyCommand` 経由で `/tmp/ccr_setup` へファイルを配送する。

        Args:
            session_name: Colab セッション名。
            ssh_key_path: `ed25519` 秘密鍵パス。
            files: 配送するローカルファイル一覧。
            remote_dir: VM側一時配置ディレクトリ。
            timeout_sec: タイムアウト（秒）。

        Raises:
            SetupComfyError: ディレクトリ作成またはSCP転送に失敗した場合。
        """
        if not files:
            return
        opts = build_ssh_options(session_name, ssh_key_path)
        mkdir_cmd = [self._ssh_bin, *opts, self._remote_host, f"mkdir -p {remote_dir}"]
        try:
            mkdir_proc = subprocess.run(
                mkdir_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SetupComfyError(f"VM配送先ディレクトリ作成に失敗しました: {exc}") from exc
        if mkdir_proc.returncode != 0:
            raise SetupComfyError(f"VM配送先ディレクトリ作成エラー: {mkdir_proc.stderr.strip()}")

        scp_cmd = [self._scp_bin, *opts, *[str(f) for f in files], f"{self._remote_host}:{remote_dir}/"]
        try:
            scp_proc = subprocess.run(
                scp_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SetupComfyError(f"SCP配送に失敗しました: {exc}") from exc
        if scp_proc.returncode != 0:
            raise SetupComfyError(f"SCP配送エラー (code={scp_proc.returncode}): {scp_proc.stderr.strip()}")

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
        """VM上で `/tmp/ccr_setup/setup.sh` を実行し、終了コードに応じた例外を送出する。

        Args:
            session_name: Colab セッション名。
            ssh_key_path: `ed25519` 秘密鍵パス。
            model_name: モデル名。
            drive_root: Driveルートフォルダ名。
            required_drive_assets: 必須モデル相対パス一覧。
            remote_dir: `setup.sh` 配置ディレクトリ。
            timeout_sec: 実行タイムアウト（秒）。

        Raises:
            SetupDriveMissingError: Drive上の必須モデルが不在（終了コード 2）の場合。
            SetupComfyError: その他セットアップ失敗・タイムアウトの場合。
        """
        opts = build_ssh_options(session_name, ssh_key_path)
        assets_payload = "\n".join(required_drive_assets) + "\n"
        remote_cmd = (
            f"chmod +x {remote_dir}/setup.sh && "
            f"cat > {remote_dir}/required_assets.txt && "
            f"bash {remote_dir}/setup.sh "
            f"--model-name {model_name} "
            f"--drive-root {drive_root} "
            f"--setup-dir {remote_dir} "
            f"--assets-file {remote_dir}/required_assets.txt"
        )
        cmd = [self._ssh_bin, *opts, self._remote_host, remote_cmd]
        try:
            proc = subprocess.run(
                cmd,
                input=assets_payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SetupComfyError(f"setup.sh リモート実行が失敗・タイムアウトしました: {exc}") from exc

        if proc.returncode == 2:
            raise SetupDriveMissingError(f"Drive必須モデル資産が不足しています: {proc.stderr.strip()}")
        if proc.returncode != 0:
            raise SetupComfyError(f"setup.sh が失敗しました (code={proc.returncode}): {proc.stderr.strip()}")


__all__ = [
    "ColabCliRunner",
    "OpenSshTransport",
    "SshTransport",
    "SubprocessColabCliRunner",
    "SubprocessTunnelHandle",
    "TunnelHandle",
    "build_ssh_options",
    "parse_cu_usage",
    "parse_sessions",
]
