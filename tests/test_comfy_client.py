"""ComfyUI HTTP/WS クライアント（WS切断時フォールバック・タイムアウト中断）のテスト。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from websockets.exceptions import ConnectionClosedError

from colab_cli_comfyui_runner.comfy_client import HttpWsComfyApiClient, extract_output_relpaths
from colab_cli_comfyui_runner.errors import JobApiTransientError, JobExecutionError


def test_tc_l1_005_ws_disconnect_falls_back_to_history_without_resubmit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-L1-005: WS切断時に /prompt を再投入せず /history/{prompt_id} から出力取得できること。"""
    client = HttpWsComfyApiClient(base_url="http://127.0.0.1:8188")

    def _broken_ws(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionClosedError(None, None)

    history_calls = 0

    def _fake_history(prompt_id: str) -> dict[str, Any] | None:
        nonlocal history_calls
        history_calls += 1
        if history_calls == 1:
            return None
        return {
            "status": {"status_str": "success", "completed": True},
            "outputs": {
                "5": {"videos": [{"filename": "out_0001.mp4", "subfolder": "minimax_h3/b1"}]}
            },
        }

    monkeypatch.setattr("colab_cli_comfyui_runner.comfy_client.ws_connect", _broken_ws)
    monkeypatch.setattr("colab_cli_comfyui_runner.comfy_client.time.sleep", lambda _s: None)
    monkeypatch.setattr(client, "_get_history_entry", _fake_history)

    outputs = client.wait_for_completion("prompt-123", client_id="cid-1", timeout_sec=30)
    assert outputs == ["minimax_h3/b1/out_0001.mp4"]
    assert history_calls == 2


def test_tc_l1_007_timeout_triggers_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-L1-007: タイムアウト時に POST /interrupt を試行して JobExecutionError を送出すること。"""
    client = HttpWsComfyApiClient(base_url="http://127.0.0.1:8188")
    interrupted = False

    def _broken_ws(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionClosedError(None, None)

    ticks = iter([0.0, 100.0])

    def _fake_interrupt() -> None:
        nonlocal interrupted
        interrupted = True

    monkeypatch.setattr("colab_cli_comfyui_runner.comfy_client.ws_connect", _broken_ws)
    monkeypatch.setattr("colab_cli_comfyui_runner.comfy_client.time.monotonic", lambda: next(ticks, 200.0))
    monkeypatch.setattr(client, "_get_history_entry", lambda _pid: None)
    monkeypatch.setattr(client, "interrupt", _fake_interrupt)

    with pytest.raises(JobExecutionError):
        client.wait_for_completion("prompt-timeout", client_id="cid-2", timeout_sec=10)
    assert interrupted is True


def test_upload_image_and_submit_prompt_http_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MockTransport を用いた /upload/image と /prompt のHTTP処理検証。"""
    img = tmp_path / "ref.png"
    img.write_bytes(b"PNGDATA")

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/upload/image":
            return httpx.Response(200, json={"name": "ref.png", "subfolder": "sub"})
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "pid-999"})
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    real_client = httpx.Client

    def _patched_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("colab_cli_comfyui_runner.comfy_client.httpx.Client", _patched_client)
    api = HttpWsComfyApiClient(base_url="http://127.0.0.1:8188")

    assert api.upload_image(img) == "sub/ref.png"
    assert api.submit_prompt({"1": {"inputs": {}}}, client_id="c1") == "pid-999"


def test_submit_prompt_500_raises_transient_and_extract_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 503 で JobApiTransientError を送出し、extract_output_relpaths が複数メディアを抽出すること。"""
    transport = httpx.MockTransport(lambda _req: httpx.Response(503, text="busy"))
    real_client = httpx.Client
    monkeypatch.setattr(
        "colab_cli_comfyui_runner.comfy_client.httpx.Client",
        lambda *args, **kwargs: real_client(*args, transport=transport, **kwargs),
    )
    api = HttpWsComfyApiClient(base_url="http://127.0.0.1:8188")
    with pytest.raises(JobApiTransientError):
        api.submit_prompt({}, client_id="c1")

    extracted = extract_output_relpaths(
        {"outputs": {"9": {"gifs": [{"filename": "clip.mp4", "subfolder": ""}]}}}
    )
    assert extracted == ["clip.mp4"]
