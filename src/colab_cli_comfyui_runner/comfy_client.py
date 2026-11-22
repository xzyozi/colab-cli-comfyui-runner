"""ComfyUI 標準 HTTP/WS API クライアントアダプタ（CCR-DD-001 C-004）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import time
from typing import Any, Protocol

import httpx
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect as ws_connect

from colab_cli_comfyui_runner.errors import (
    JobApiTransientError,
    JobExecutionError,
    JobValidationError,
    SetupComfyError,
)

logger = logging.getLogger(__name__)


class ComfyApiClient(Protocol):
    """ComfyUI API 制御 Seam（L1テストおよび将来 `ComfyScript` 差し替え境界）。"""

    def wait_ready(self, *, timeout_sec: int = 180, poll_interval_sec: float = 5.0) -> None:
        """`/system_stats` が 200 OK を返すまで待機する。"""

    def upload_image(self, image_path: Path) -> str:
        """ローカル画像を `/upload/image` へアップロードし、ComfyUI 参照名を返す。"""

    def submit_prompt(self, workflow: dict[str, Any], *, client_id: str) -> str:
        """`/prompt` へワークフローを投入し `prompt_id` を返す。"""

    def wait_for_completion(
        self,
        prompt_id: str,
        *,
        client_id: str,
        timeout_sec: int,
    ) -> list[str]:
        """`/ws` で完了監視し、切断時は `/history/{prompt_id}` フォールバックで出力一覧を返す。"""

    def interrupt(self) -> None:
        """実行中ジョブへ `/interrupt` を送信する。"""


def extract_output_relpaths(history_entry: dict[str, Any]) -> list[str]:
    """`/history/{prompt_id}` 応答エントリから生成ファイル相対パス一覧を抽出する。

    Args:
        history_entry: `/history/{prompt_id}` の対象プロンプトオブジェクト。

    Returns:
        `subfolder/filename` 形式の出力パスリスト。
    """
    outputs = history_entry.get("outputs")
    if not isinstance(outputs, dict):
        return []
    paths: list[str] = []
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for media_key in ("videos", "gifs", "images", "audio"):
            items = node_output.get(media_key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename")
                if not isinstance(filename, str) or not filename:
                    continue
                subfolder = item.get("subfolder")
                if isinstance(subfolder, str) and subfolder.strip("/"):
                    paths.append(f"{subfolder.strip('/')}/{filename}")
                else:
                    paths.append(filename)
    return paths


class HttpWsComfyApiClient:
    """ComfyUI 標準 HTTP/WS API を利用するクライアント実装。"""

    def __init__(self, *, base_url: str = "http://127.0.0.1:8188", request_timeout_sec: float = 30.0) -> None:
        """クライアントを初期化する。

        Args:
            base_url: SSHトンネル越しに待ち受ける ComfyUI HTTP ベースURL。
            request_timeout_sec: 単発 HTTP リクエストのタイムアウト（秒）。
        """
        self.base_url = base_url.rstrip("/")
        self._ws_base_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        self._timeout = request_timeout_sec

    def wait_ready(self, *, timeout_sec: int = 180, poll_interval_sec: float = 5.0) -> None:
        """`/system_stats` ポーリングにより ComfyUI 待ち受け開始を確認する。

        Args:
            timeout_sec: 最大待機時間（秒）。
            poll_interval_sec: ポーリング間隔（秒）。

        Raises:
            SetupComfyError: タイムアウトまでに 200 OK が返らない場合。
        """
        deadline = time.monotonic() + timeout_sec
        last_error = ""
        with httpx.Client(base_url=self.base_url, timeout=5.0) as client:
            while time.monotonic() < deadline:
                try:
                    resp = client.get("/system_stats")
                    if resp.status_code == 200:
                        return
                    last_error = f"HTTP {resp.status_code}"
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                time.sleep(poll_interval_sec)
        raise SetupComfyError(f"ComfyUI (/system_stats) が {timeout_sec}秒以内に起動しませんでした: {last_error}")

    def upload_image(self, image_path: Path) -> str:
        """ローカル入力素材を `POST /upload/image` で送信する。

        Args:
            image_path: 送信するローカル画像・素材ファイルパス。

        Returns:
            ComfyUI ワークフロー内で参照可能なファイル名（`subfolder/name` または `name`）。

        Raises:
            JobValidationError: ファイル不存在・クライアント側4xxエラー時。
            JobApiTransientError: 一時的な通信エラー・5xxエラー時。
        """
        if not image_path.is_file():
            raise JobValidationError(f"アップロード対象のローカル素材が存在しません: {image_path}")
        try:
            with image_path.open("rb") as handle, httpx.Client(base_url=self.base_url, timeout=self._timeout) as client:
                resp = client.post(
                    "/upload/image",
                    files={"image": (image_path.name, handle)},
                    data={"overwrite": "true"},
                )
        except httpx.HTTPError as exc:
            raise JobApiTransientError(f"/upload/image 通信エラー: {exc}") from exc

        if resp.status_code >= 500:
            raise JobApiTransientError(f"/upload/image サーバエラー: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise JobValidationError(f"/upload/image リクエスト不正: HTTP {resp.status_code} {resp.text}")

        data = resp.json()
        name = data.get("name")
        if not isinstance(name, str) or not name:
            raise JobApiTransientError("/upload/image 応答に 'name' が含まれていません。")
        subfolder = data.get("subfolder")
        if isinstance(subfolder, str) and subfolder.strip("/"):
            return f"{subfolder.strip('/')}/{name}"
        return name

    def submit_prompt(self, workflow: dict[str, Any], *, client_id: str) -> str:
        """`POST /prompt` でワークフローを投入し `prompt_id` を返す。

        Args:
            workflow: パラメータ注入済みの API 用ワークフロー辞書。
            client_id: WebSocket 監視用のクライアント相関ID。

        Returns:
            発行された `prompt_id`。

        Raises:
            JobValidationError: 400 系（ワークフロー構文・ノードバリデーション不正）。
            JobApiTransientError: 5xx または一時的な通信障害。
        """
        try:
            with httpx.Client(base_url=self.base_url, timeout=self._timeout) as client:
                resp = client.post("/prompt", json={"prompt": workflow, "client_id": client_id})
        except httpx.HTTPError as exc:
            raise JobApiTransientError(f"/prompt 通信エラー: {exc}") from exc

        if resp.status_code >= 500:
            raise JobApiTransientError(f"/prompt サーバエラー: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise JobValidationError(f"/prompt バリデーションエラー: HTTP {resp.status_code} {resp.text}")

        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise JobApiTransientError(f"/prompt 応答に 'prompt_id' がありません: {data!r}")
        return prompt_id

    def wait_for_completion(
        self,
        prompt_id: str,
        *,
        client_id: str,
        timeout_sec: int,
    ) -> list[str]:
        """WebSocket `/ws` で完了を監視し、切断時は `/history/{prompt_id}` へフォールバックする。

        Args:
            prompt_id: 監視対象の `prompt_id`。
            client_id: 投入時と同一の `client_id`。
            timeout_sec: 単一ジョブ上限時間（秒）。

        Returns:
            生成された出力ファイル相対パス一覧。

        Raises:
            JobExecutionError: ComfyUI 実行エラーまたはタイムアウト時。
        """
        deadline = time.monotonic() + timeout_sec
        ws_url = f"{self._ws_base_url}/ws?clientId={client_id}"
        try:
            with ws_connect(ws_url, open_timeout=10.0) as ws:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._best_effort_interrupt()
                        raise JobExecutionError(
                            f"ジョブ (prompt_id={prompt_id}) が {timeout_sec}秒でタイムアウトしました。"
                        )
                    try:
                        raw = ws.recv(timeout=min(remaining, 10.0))
                    except TimeoutError:
                        continue
                    if not isinstance(raw, str):
                        continue
                    event = json.loads(raw)
                    ev_type = event.get("type")
                    ev_data = event.get("data")
                    if not isinstance(ev_data, dict) or ev_data.get("prompt_id") != prompt_id:
                        continue
                    if ev_type == "execution_error":
                        exc_msg = ev_data.get("exception_message") or "ComfyUI execution_error"
                        raise JobExecutionError(f"ComfyUI 実行エラー (prompt_id={prompt_id}): {exc_msg}")
                    if ev_type == "executing" and ev_data.get("node") is None:
                        return self._fetch_history_outputs(prompt_id, deadline=deadline)
        except (WebSocketException, OSError) as exc:
            logger.warning("WS接続が切断されたため /history/%s ポーリングへフォールバックします: %s", prompt_id, exc)
            return self._poll_history_until_done(prompt_id, deadline=deadline, timeout_sec=timeout_sec)

    def _fetch_history_outputs(self, prompt_id: str, *, deadline: float) -> list[str]:
        entry = self._get_history_entry(prompt_id)
        if entry is not None:
            self._raise_if_history_error(prompt_id, entry)
            return extract_output_relpaths(entry)
        remaining_sec = int(max(1.0, deadline - time.monotonic()))
        return self._poll_history_until_done(prompt_id, deadline=deadline, timeout_sec=remaining_sec)

    def _poll_history_until_done(self, prompt_id: str, *, deadline: float, timeout_sec: int) -> list[str]:
        while time.monotonic() < deadline:
            entry = self._get_history_entry(prompt_id)
            if entry is not None:
                self._raise_if_history_error(prompt_id, entry)
                status = entry.get("status")
                if isinstance(status, dict) and status.get("completed") is True:
                    return extract_output_relpaths(entry)
                if "outputs" in entry:
                    return extract_output_relpaths(entry)
            time.sleep(2.0)
        self._best_effort_interrupt()
        raise JobExecutionError(f"ジョブ (prompt_id={prompt_id}) が {timeout_sec}秒でタイムアウトしました。")

    def _get_history_entry(self, prompt_id: str) -> dict[str, Any] | None:
        try:
            with httpx.Client(base_url=self.base_url, timeout=self._timeout) as client:
                resp = client.get(f"/history/{prompt_id}")
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        payload = resp.json()
        entry = payload.get(prompt_id)
        return entry if isinstance(entry, dict) else None

    @staticmethod
    def _raise_if_history_error(prompt_id: str, entry: dict[str, Any]) -> None:
        status = entry.get("status")
        if isinstance(status, dict) and status.get("status_str") == "error":
            raise JobExecutionError(f"ComfyUI 履歴にエラーが記録されました (prompt_id={prompt_id}): {status}")

    def _best_effort_interrupt(self) -> None:
        try:
            self.interrupt()
        except Exception:
            logger.warning("/interrupt の送信に失敗しました。", exc_info=True)

    def interrupt(self) -> None:
        """`POST /interrupt` を送信して実行中プロンプトを中断する。"""
        with httpx.Client(base_url=self.base_url, timeout=5.0) as client:
            client.post("/interrupt")


__all__ = ["ComfyApiClient", "HttpWsComfyApiClient", "extract_output_relpaths"]
