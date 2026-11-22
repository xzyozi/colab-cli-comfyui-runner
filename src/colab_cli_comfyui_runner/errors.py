"""ドメイン固有の例外クラスとエラーコード定義（CCR-DD-001 4.1準拠）。"""

from __future__ import annotations


class CcrBaseError(Exception):
    """本パイプラインの全例外の基底クラス。

    Attributes:
        error_code: CCR-DD-001 4.1 に定義されたエラーコード。
        retryable: 同一操作を再試行しても安全かを示すフラグ。
        manual_stop_required: 運用者による手動VM停止確認が必要かを示すフラグ。
    """

    error_code: str = "ERR-UNKNOWN"

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        retryable: bool = False,
        manual_stop_required: bool = False,
    ) -> None:
        """例外を初期化する。

        Args:
            message: 人間可読なエラー詳細メッセージ。
            error_code: 明示指定するエラーコード（省略時はクラス定義値）。
            retryable: 再試行可能かどうか。
            manual_stop_required: 手動停止アラートが必要かどうか。
        """
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code
        self.retryable = retryable
        self.manual_stop_required = manual_stop_required


class PreflightLockError(CcrBaseError):
    """排他ロック競合または残存VM疑いにより起動拒否された場合の例外。"""

    error_code = "ERR-PREFLIGHT-LOCK"


class PreflightCuError(CcrBaseError):
    """CU残高不足または既存セッション残存による事前検証エラー。"""

    error_code = "ERR-PREFLIGHT-CU"


class VmProvisionError(CcrBaseError):
    """Colab VM起動・Driveマウント・SSHトンネル確立の失敗。"""

    error_code = "ERR-VM-PROVISION"


class SetupDriveMissingError(CcrBaseError):
    """Google Drive上の必須モデル資産不在による環境復元中断。"""

    error_code = "ERR-SETUP-DRIVE-MISSING"


class SetupComfyError(CcrBaseError):
    """setup.sh実行失敗またはComfyUI起動待機タイムアウト。"""

    error_code = "ERR-SETUP-COMFY"


class JobValidationError(CcrBaseError):
    """入力JSON・マニフェスト・パラメータ範囲・スキーマ検証エラー。"""

    error_code = "ERR-JOB-VALIDATION"


class JobApiTransientError(CcrBaseError):
    """ComfyUI HTTP/WS通信の一時的障害（安全条件付き再試行対象）。"""

    error_code = "ERR-JOB-API-TRANSIENT"

    def __init__(self, message: str) -> None:
        """再試行可能フラグを有効にして初期化する。

        Args:
            message: 通信エラーの詳細メッセージ。
        """
        super().__init__(message, retryable=True)


class JobExecutionError(CcrBaseError):
    """ComfyUI実行エラー・CUDA OOM・単一ジョブタイムアウト。"""

    error_code = "ERR-JOB-EXECUTION"


class BatchBudgetError(CcrBaseError):
    """バッチ許容時間予算（max_batch_duration_sec）超過。"""

    error_code = "ERR-BATCH-BUDGET"


class StopFailureError(CcrBaseError):
    """colab stop のリトライ後もセッションが残存した場合の緊急例外。"""

    error_code = "ERR-STOP-FAILURE"

    def __init__(self, message: str) -> None:
        """手動停止必須フラグを有効にして初期化する。

        Args:
            message: 停止失敗の詳細メッセージ。
        """
        super().__init__(message, manual_stop_required=True)
