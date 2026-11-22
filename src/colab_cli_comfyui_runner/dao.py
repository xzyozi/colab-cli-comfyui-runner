"""永続化DAO（原子書き込み契約）・排他ロック管理（CCR-DS-001 第3章準拠）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import socket
import sys
from typing import Any
import uuid

from colab_cli_comfyui_runner.errors import JobValidationError, PreflightLockError
from colab_cli_comfyui_runner.models import CuUsageRecord, ExecutionLock

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """現在時刻をタイムゾーン付きISO 8601文字列（秒精度）で返す。

    Returns:
        ISO 8601 形式の現在時刻文字列。
    """
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_is_pid_alive(pid: int) -> bool:
    """ローカルOS上で指定PIDのプロセスが生存しているかを判定する。

    Args:
        pid: 確認対象のプロセスID。

    Returns:
        プロセスが生存していれば True、存在しなければ False。
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query_limited_info = 0x1000
        still_active = 259
        kernel32 = getattr(ctypes, "windll").kernel32
        handle = kernel32.OpenProcess(process_query_limited_info, False, pid)
        if not handle:
            last_err = ctypes.get_last_error()
            # ERROR_ACCESS_DENIED (5) means process exists but access is restricted.
            return last_err == 5
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == still_active
            return True
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_json_file(path: Path) -> dict[str, Any]:
    """UTF-8 JSONファイルを辞書として読み込む。

    Args:
        path: 読み込み対象ファイルパス。

    Returns:
        パースされたJSON辞書。

    Raises:
        JobValidationError: ファイル不存在・JSON構文不正・ルートが辞書でない場合。
    """
    if not path.is_file():
        raise JobValidationError(f"JSONファイルが見つかりません: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise JobValidationError(f"JSONの読み込みに失敗しました ({path}): {exc}") from exc
    if not isinstance(data, dict):
        raise JobValidationError(f"JSONルートはオブジェクトである必要があります: {path}")
    return data


def atomic_write_json(target: Path, data: dict[str, Any]) -> None:
    """原子的書き込み契約（一時ファイル → fsync → os.replace）に従いJSONを書き込む。

    Args:
        target: 保存先の正本ファイルパス。
        data: 保存するJSON辞書。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.parent / f"{target.name}.tmp.{os.getpid()}"
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.warning("一時ファイルの削除に失敗しました: %s", tmp_path)


def append_cu_usage_history(path: Path, record: CuUsageRecord) -> None:
    """月次CU履歴ログ（JSONL）へ1行をUTF-8・LFで安全に追記する。

    Args:
        path: `cu_usage_history.jsonl` のパス。
        record: 追記するレコード。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


class LockManager:
    """ローカル排他ロックファイル（`state/active_batch.lock`）を管理するクラス。"""

    def __init__(
        self,
        lock_path: Path,
        *,
        is_pid_alive: Callable[[int], bool] = default_is_pid_alive,
        hostname_provider: Callable[[], str] = socket.gethostname,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        """ロックマネージャを初期化する。

        Args:
            lock_path: ロックファイルの保存パス。
            is_pid_alive: PID生存確認関数（テスト用差し替え可）。
            hostname_provider: ホスト名取得関数。
            now_provider: 現在時刻取得関数（テスト用差し替え可）。
        """
        self.lock_path = lock_path
        self._is_pid_alive = is_pid_alive
        self._hostname_provider = hostname_provider
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc).astimezone())

    def acquire(
        self,
        batch_id: str,
        *,
        has_active_session: Callable[[str], bool],
        ttl_sec: int = 18000,
    ) -> ExecutionLock:
        """排他ロック（`O_CREAT | O_EXCL`）を取得する。既存ロックは stale 4象限で判定する。

        Args:
            batch_id: 実行対象のバッチID。
            has_active_session: 指定 `session_name` が `colab sessions` に残存するか判定する関数。
            ttl_sec: 生存中プロセスハング警告の閾値（秒）。

        Returns:
            取得した `ExecutionLock`。

        Raises:
            PreflightLockError: 有効な別ロックが存在する、または残存VM疑いがある場合。
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            lock = ExecutionLock(
                lock_id=str(uuid.uuid4()),
                batch_id=batch_id,
                pid=os.getpid(),
                hostname=self._hostname_provider(),
                acquired_at=self._now_provider().isoformat(timespec="seconds"),
                session_name=None,
                ttl_sec=ttl_sec,
            )
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if attempt == 0:
                    self._handle_existing_lock(has_active_session=has_active_session)
                    continue
                raise PreflightLockError(f"排他ロックの取得が競合しました: {self.lock_path}")
            else:
                try:
                    payload = json.dumps(lock.to_dict(), ensure_ascii=False, indent=2) + "\n"
                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    self.lock_path.unlink(missing_ok=True)
                    raise
                return lock
        raise PreflightLockError(f"排他ロックの取得に失敗しました: {self.lock_path}")

    def _handle_existing_lock(self, *, has_active_session: Callable[[str], bool]) -> None:
        try:
            existing_raw = read_json_file(self.lock_path)
            existing = ExecutionLock.from_dict(existing_raw)
        except JobValidationError as exc:
            raise PreflightLockError(
                f"既存ロックファイルが破損しているため安全のため中断します ({self.lock_path}): {exc}"
            ) from exc

        current_host = self._hostname_provider()
        if existing.hostname != current_host:
            raise PreflightLockError(
                f"別ホスト ({existing.hostname}) がロックを保持中です: "
                f"batch_id={existing.batch_id}, acquired_at={existing.acquired_at}"
            )

        if self._is_pid_alive(existing.pid):
            elapsed = (self._now_provider() - datetime.fromisoformat(existing.acquired_at)).total_seconds()
            if elapsed > existing.ttl_sec:
                logger.warning(
                    "実行中プロセスが ttl_sec (%ds) を超過してハングしている可能性があります: "
                    "batch_id=%s, pid=%d, session=%s",
                    existing.ttl_sec,
                    existing.batch_id,
                    existing.pid,
                    existing.session_name,
                )
            raise PreflightLockError(
                f"別バッチが実行中です: batch_id={existing.batch_id}, pid={existing.pid}, "
                f"acquired_at={existing.acquired_at}"
            )

        # PID が不在の場合: session_name の有無と colab sessions 照合で判定する。
        if existing.session_name is not None:
            try:
                still_running = has_active_session(existing.session_name)
            except Exception as exc:
                raise PreflightLockError(
                    f"stale lock 判定中の colab sessions 確認に失敗しました (session={existing.session_name}): {exc}"
                ) from exc
            if still_running:
                raise PreflightLockError(
                    f"ローカルプロセス (pid={existing.pid}) は終了していますが、"
                    f"Colab セッション '{existing.session_name}' が残存しています。"
                    f"`colab stop -s {existing.session_name}` および `colab sessions` で停止を確認してください。"
                )

        logger.info(
            "stale lock を自動回収します: batch_id=%s, pid=%d, session=%s",
            existing.batch_id,
            existing.pid,
            existing.session_name,
        )
        self.lock_path.unlink(missing_ok=True)

    def update_session_name(self, lock: ExecutionLock, session_name: str) -> ExecutionLock:
        """`colab new` 呼び出し直前に `session_name` を原子置換でロックへ記録する。

        Args:
            lock: 保持中のロック情報。
            session_name: 採番された Colab セッション名。

        Returns:
            `session_name` 更新後の `ExecutionLock`。
        """
        updated = ExecutionLock(
            lock_id=lock.lock_id,
            batch_id=lock.batch_id,
            pid=lock.pid,
            hostname=lock.hostname,
            acquired_at=lock.acquired_at,
            session_name=session_name,
            ttl_sec=lock.ttl_sec,
        )
        atomic_write_json(self.lock_path, updated.to_dict())
        return updated

    def release(self, lock: ExecutionLock) -> None:
        """保持中の `lock_id` と一致することを確認した上でロックファイルを解放する。

        Args:
            lock: 自プロセスが取得した `ExecutionLock`。
        """
        if not self.lock_path.exists():
            return
        try:
            current = ExecutionLock.from_dict(read_json_file(self.lock_path))
        except JobValidationError:
            logger.warning("ロック解放時に内容を読み取れませんでした: %s", self.lock_path)
            return
        if current.lock_id != lock.lock_id:
            logger.warning(
                "別プロセスのロック (lock_id=%s) のため解放をスキップしました。",
                current.lock_id,
            )
            return
        self.lock_path.unlink(missing_ok=True)
