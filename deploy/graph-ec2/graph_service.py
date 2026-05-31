from __future__ import annotations
import os
import sys
import json
import logging
from contextlib import asynccontextmanager
import boto3
from fastapi import FastAPI, Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

# Add repo root to sys.path
sys.path.append("/var/www/companion")

from app.services.leadgen.graph_client import GraphClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("graph_service")


def load_secrets():
    project_name = os.getenv("PROJECT_NAME", "companion")
    region = os.getenv("AWS_REGION", "ap-south-1")
    client = boto3.client("secretsmanager", region_name=region)
    try:
        # Load app secrets
        res = client.get_secret_value(SecretId=f"{project_name}/app")
        secrets = json.loads(res["SecretString"])
        for k, v in secrets.items():
            os.environ[k] = str(v)

        # Load neo4j secrets
        res_neo4j = client.get_secret_value(SecretId=f"{project_name}/neo4j-key")
        neo4j_secrets = json.loads(res_neo4j["SecretString"])
        os.environ["NEO4J_PASSWORD"] = neo4j_secrets["password"]
        os.environ["NEO4J_USERNAME"] = neo4j_secrets.get("username", "neo4j")
        
        # Load graph service bearer token
        res_token = client.get_secret_value(SecretId=f"{project_name}/graph-service-token")
        token_secrets = json.loads(res_token["SecretString"])
        os.environ["GRAPH_SERVICE_TOKEN"] = token_secrets["token"]
        
        logger.info("Successfully loaded Secrets Manager credentials for FastAPI service")
    except Exception as e:
        logger.error(f"Error loading secrets in FastAPI service: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_secrets()
    yield


app = FastAPI(title="Companion Knowledge Graph Service", lifespan=lifespan)
security_bearer = HTTPBearer()


def get_bearer_token(
    credentials: HTTPAuthorizationCredentials = Security(security_bearer),
) -> str:
    token = credentials.credentials
    expected_token = os.getenv("GRAPH_SERVICE_TOKEN")
    
    if not expected_token:
        # Reload secrets just in case
        load_secrets()
        expected_token = os.getenv("GRAPH_SERVICE_TOKEN")

    if not expected_token or token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authorization token",
        )
    return token


class QueryRequest(BaseModel):
    cypher: str
    parameters: dict[str, Any] | None = None


@app.get("/health")
def health_check():
    """Unauthenticated health check endpoint."""
    client = GraphClient()
    neo4j_ok = False
    try:
        client.execute_query("RETURN 1")
        neo4j_ok = True
    except Exception as e:
        logger.error(f"Neo4j health check failed: {e}")

    return {
        "status": "healthy" if neo4j_ok else "degraded",
        "neo4j_connected": neo4j_ok,
    }


@app.get("/profile/{user_id}")
def get_user_profile(user_id: str, token: str = Depends(get_bearer_token)):
    """Retrieves projected user digital profile facts and interests."""
    client = GraphClient()
    try:
        profile = client.get_user_profile_projection(user_id)
        return profile
    except Exception as e:
        logger.exception(f"Error projecting profile for user={user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/leads")
def get_leads(
    category: str | None = None,
    status: str = "open",
    token: str = Depends(get_bearer_token),
):
    """Retrieves commercial leads filtered by status and category."""
    query = """
    MATCH (u:User)-[:GENERATED]->(l:Lead)
    WHERE l.status = $status
    """
    params = {"status": status}
    if category:
        query += " AND l.category = $category"
        params["category"] = category
    query += " RETURN u.id as user_id, l as lead ORDER BY l.last_seen_at DESC LIMIT 100"

    client = GraphClient()
    try:
        records = client.execute_query(query, params)
        leads = []
        for r in records:
            lead_node = r["lead"]
            attrs = {}
            if lead_node.get("attrs"):
                try:
                    attrs = json.loads(lead_node.get("attrs"))
                except Exception:
                    pass

            leads.append(
                {
                    "user_id": r["user_id"],
                    "lead_id": lead_node.get("id"),
                    "category": lead_node.get("category"),
                    "product": lead_node.get("product"),
                    "attrs": attrs,
                    "budget_signal": lead_node.get("budget_signal"),
                    "intent_score": lead_node.get("intent_score"),
                    "confidence": lead_node.get("confidence"),
                    "last_seen_at": lead_node.get("last_seen_at"),
                    "created_at": lead_node.get("created_at"),
                    "consent_status": lead_node.get("consent_status"),
                }
            )
        return leads
    except Exception as e:
        logger.exception(f"Error querying leads: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query")
def run_custom_query(req: QueryRequest, token: str = Depends(get_bearer_token)):
    """Runs a raw Cypher query against the graph database."""
    client = GraphClient()
    try:
        results = client.execute_query(req.cypher, req.parameters)
        return {"status": "success", "results": results}
    except Exception as e:
        logger.exception(f"Error executing custom Cypher query: {e}")
        raise HTTPException(status_code=400, detail=str(e))
