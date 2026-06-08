"""Profile / leads / admin-graph routes.

Extracted from main.py (#29). The graph-service token fetch and the admin
allowlist — previously copy-pasted across three handlers — are shared helpers here.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
import requests
from fastapi import APIRouter, Depends, HTTPException

from companion_api import settings
from companion_api.auth import get_current_user

router = APIRouter()

_ADMINS = {"admin", "admin_bhavesh", "admin_kshitij", "local_admin"}


def _graph_token() -> str:
    """Bearer token for the EC2 graph service (Secrets Manager, env fallback)."""
    token = None
    try:
        sm = boto3.client("secretsmanager", region_name=settings.AWS_REGION)
        res = sm.get_secret_value(SecretId="companion/graph-service-token")
        token = json.loads(res["SecretString"]).get("token")
    except Exception:
        token = os.getenv("GRAPH_SERVICE_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="Graph service token is not configured.")
    return token


def _require_admin(user: str) -> None:
    if user not in _ADMINS:
        raise HTTPException(status_code=403, detail="Forbidden: Admin access only.")


@router.get("/api/profile/me/")
def get_my_profile(user: str = Depends(get_current_user)) -> dict[str, Any]:
    """The digital profile (facts, interests, active addresses) for the logged-in user."""
    from companion_api.profile_cache import ProfileCacheRepository

    profile = ProfileCacheRepository().get(user)
    if profile:
        return profile
    return {
        "user_id": user,
        "name": None,
        "age": None,
        "city": None,
        "language": None,
        "addresses": {},
        "interests": [],
        "open_leads": [],
        "consent_status": "implicit",
    }


@router.get("/api/leads/")
def get_leads_api(
    category: str | None = None,
    status: str = "open",
    user: str = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Query generated leads from the Graph Service on EC2."""
    headers = {"Authorization": f"Bearer {_graph_token()}"}
    params = {"status": status}
    if category:
        params["category"] = category
    try:
        resp = requests.get(
            f"{settings.GRAPH_SERVICE_URL}/leads", headers=headers, params=params, timeout=10
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to query leads from graph service: {e}"
        ) from e


@router.get("/api/admin/users/")
def list_admin_users(user: str = Depends(get_current_user)) -> list[dict[str, Any]]:
    """List all user profiles in the knowledge graph (admin only)."""
    _require_admin(user)
    headers = {"Authorization": f"Bearer {_graph_token()}"}
    cypher = "MATCH (u:User) RETURN u.id as id, u.name as name, u.city as city"
    try:
        resp = requests.post(
            f"{settings.GRAPH_SERVICE_URL}/query",
            headers=headers,
            json={"cypher": cypher},
            timeout=10,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json().get("results", [])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to query users from graph service: {e}"
        ) from e


@router.get("/api/admin/profile-graph/{user_id}/")
def get_admin_profile_graph(user_id: str, user: str = Depends(get_current_user)) -> dict[str, Any]:
    """Full knowledge-graph nodes + edges for a user (admin only)."""
    _require_admin(user)
    headers = {"Authorization": f"Bearer {_graph_token()}"}
    cypher = """
    MATCH (u:User {id: $user_id})
    OPTIONAL MATCH (u)-[r1]->(first)
    OPTIONAL MATCH (first)-[r2]->(second)
    WHERE r2 IS NULL OR type(r2) IN ['HAS_INTEREST', 'IN_CATEGORY']
    RETURN
      u,
      labels(u) AS user_labels,
      collect(DISTINCT {
        source: u, source_labels: labels(u), rel_type: type(r1),
        rel: properties(r1), target: first, target_labels: labels(first)
      }) AS first_hop,
      collect(DISTINCT {
        source: first, source_labels: labels(first), rel_type: type(r2),
        rel: properties(r2), target: second, target_labels: labels(second)
      }) AS second_hop
    """
    try:
        resp = requests.post(
            f"{settings.GRAPH_SERVICE_URL}/query",
            headers=headers,
            json={"cypher": cypher, "parameters": {"user_id": user_id}},
            timeout=10,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        results = resp.json().get("results", [])
        return _shape_graph(user_id, results)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to query graph data from graph service: {e}"
        ) from e


def _shape_graph(user_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[str, Any] = {}
    links: list[dict[str, Any]] = []

    def add_node(node: dict[str, Any] | None, labels: list[str] | None) -> str | None:
        if not node:
            return None
        node_type = labels[0] if labels else "Unknown"
        node_id = node.get("id") or node.get("name") or str(hash(json.dumps(node, sort_keys=True)))
        label_key = {"Place": "address_line", "Lead": "product", "Interest": "detail"}.get(
            node_type, "name"
        )
        node_label = node.get(label_key) or node.get("name") or node_id
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id,
                "label": node_label,
                "type": node_type,
                "properties": {k: v for k, v in node.items() if k not in ["id", label_key]},
            }
        return node_id

    def add_link(source_id, target_id, rel_type, rel):
        if not source_id or not target_id or not rel_type:
            return
        if any(
            link["source"] == source_id and link["target"] == target_id and link["type"] == rel_type
            for link in links
        ):
            return
        links.append(
            {"source": source_id, "target": target_id, "type": rel_type, "properties": rel or {}}
        )

    if results:
        u_node = results[0].get("u")
        if u_node:
            nodes[user_id] = {
                "id": user_id,
                "label": u_node.get("name") or user_id,
                "type": "User",
                "properties": {k: v for k, v in u_node.items() if k != "id"},
            }
        for row in results:
            for hop in [*(row.get("first_hop") or []), *(row.get("second_hop") or [])]:
                source_id = add_node(hop.get("source"), hop.get("source_labels") or [])
                target_id = add_node(hop.get("target"), hop.get("target_labels") or [])
                add_link(source_id, target_id, hop.get("rel_type"), hop.get("rel"))

    return {"user_id": user_id, "nodes": list(nodes.values()), "links": links}
