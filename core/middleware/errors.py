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


class NeedsLocationError(ProviderError):
    code = ErrorCode.NEEDS_LOCATION


class ToolTimeoutError(ProviderError):
    code = ErrorCode.TOOL_TIMEOUT


class UpstreamError(ProviderError):
    code = ErrorCode.UPSTREAM_ERROR


class ConfirmationRequiredError(ProviderError):
    code = ErrorCode.CONFIRMATION_REQUIRED


def _leaf_exceptions(exc: BaseException) -> list[BaseException]:
    """Flatten (possibly nested) ExceptionGroups into their leaf exceptions."""
    if isinstance(exc, BaseExceptionGroup):
        leaves: list[BaseException] = []
        for sub in exc.exceptions:
            leaves.extend(_leaf_exceptions(sub))
        return leaves or [exc]
    return [exc]


def result_from_exception(
    tool: str,
    provider: str,
    exc: BaseException,
    *,
    arguments: dict[str, Any] | None = None,
) -> ToolResult:
    """Convert any exception into a failed `ToolResult` with a typed error code.

    ExceptionGroups (anyio task groups around MCP sessions) are flattened to
    their most informative leaf — "unhandled errors in a TaskGroup" tells
    nobody anything."""
    leaves = _leaf_exceptions(exc)
    primary = next((leaf for leaf in leaves if isinstance(leaf, ProviderError)), None)
    if primary is None:
        primary = next((leaf for leaf in leaves if not isinstance(leaf, TimeoutError)), leaves[0])
    if isinstance(primary, ProviderError):
        code = primary.code
    elif isinstance(primary, TimeoutError):
        code = ErrorCode.TOOL_TIMEOUT
    else:
        code = ErrorCode.UNKNOWN
    detail = getattr(primary, "detail", None) or str(primary) or primary.__class__.__name__
    if isinstance(primary, TimeoutError) and detail == "TimeoutError":
        detail = (
            "timed out opening the provider session — its saved login may have "
            "expired; reconnect it from Settings"
        )
    return ToolResult(
        tool=tool,
        provider=provider,
        ok=False,
        arguments=arguments or {},
        error=code,
        error_detail=detail,
    )
