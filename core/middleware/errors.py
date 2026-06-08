"""Error taxonomy for tool execution.

Provider executors raise these; the dispatcher catches them and converts to typed
`ToolResult`s so one tool's failure never aborts the others (ADR-0003).
"""

from __future__ import annotations

from typing import Any

from core.contracts.tool_result import ErrorCode, ToolResult


class ProviderError(Exception):
    """Base for provider execution failures. Carries an `ErrorCode`."""

    code: ErrorCode = ErrorCode.UNKNOWN

    def __init__(self, message: str = "", *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail or message


class AuthRequiredError(ProviderError):
    code = ErrorCode.AUTH_REQUIRED


class ProviderUnavailableError(ProviderError):
    code = ErrorCode.PROVIDER_UNAVAILABLE


class InvalidArgsError(ProviderError):
    code = ErrorCode.INVALID_ARGS


class UnresolvedPlaceholderError(ProviderError):
    code = ErrorCode.UNRESOLVED_PLACEHOLDER


class ToolTimeoutError(ProviderError):
    code = ErrorCode.TOOL_TIMEOUT


class UpstreamError(ProviderError):
    code = ErrorCode.UPSTREAM_ERROR


class ConfirmationRequiredError(ProviderError):
    code = ErrorCode.CONFIRMATION_REQUIRED


def result_from_exception(
    tool: str,
    provider: str,
    exc: BaseException,
    *,
    arguments: dict[str, Any] | None = None,
) -> ToolResult:
    """Convert any exception into a failed `ToolResult` with a typed error code."""
    code = exc.code if isinstance(exc, ProviderError) else ErrorCode.UNKNOWN
    detail = getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__
    return ToolResult(
        tool=tool,
        provider=provider,
        ok=False,
        arguments=arguments or {},
        error=code,
        error_detail=detail,
    )
