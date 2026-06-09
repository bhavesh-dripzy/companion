"""Tool-provider CRUD routes: list / custom / zapier / delete.

Extracted verbatim from main.py (#29).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from companion_api.auth import get_current_user
from companion_api.models import CustomMcpProviderRequest, ZapierMcpProviderRequest
from companion_api.tool_runtime import (
    delete_custom_tool_provider,
    get_tool_provider,
    list_tool_providers,
    save_custom_tool_provider,
)

router = APIRouter()


@router.get("/api/tools/providers/")
def tool_providers(user: str = Depends(get_current_user)):
    return {"providers": list_tool_providers(user_id=user)}


@router.post("/api/tools/providers/custom/")
def create_custom_tool_provider(
    payload: CustomMcpProviderRequest,
    user: str = Depends(get_current_user),
):
    try:
        provider = save_custom_tool_provider(
            label=payload.label,
            mcp_url=payload.mcp_url,
            tools=payload.tools,
            user_id=user,
        )
        return {"status": "success", "provider": provider}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/tools/providers/zapier/")
def save_zapier_tool_provider(
    payload: ZapierMcpProviderRequest,
    user: str = Depends(get_current_user),
):
    try:
        provider = save_custom_tool_provider(
            label="Zapier MCP",
            mcp_url=payload.mcp_url,
            tools=payload.tools,
            auth_mode="url",
            provider_id="zapier_mcp",
            user_id=user,
        )
        return {"status": "success", "provider": provider}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/api/tools/providers/custom/{provider_id}")
def remove_custom_tool_provider(
    provider_id: str,
    user: str = Depends(get_current_user),
):
    provider = get_tool_provider(provider_id, user_id=user)
    if not provider or not provider.custom:
        raise HTTPException(status_code=404, detail="Custom MCP provider not found.")
    deleted = delete_custom_tool_provider(provider_id, user_id=user)
    return {"status": "success" if deleted else "missing", "provider_id": provider_id}
