from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from neo4j import GraphDatabase

from leadgen.schema import Interest, Lead, ProfileFact, Vendor


class GraphClient:
    def __init__(
        self,
        uri: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ):
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.username = username or os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "")
        self.driver = None
        if self.password:
            self.driver = GraphDatabase.driver(
                self.uri, auth=(self.username, self.password)
            )

    def close(self):
        if self.driver:
            self.driver.close()

    def execute_query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not self.driver:
            # Return empty or dummy if password not set (e.g. during local tests without a live graph db)
            return []
        with self.driver.session() as session:
            result = session.run(cypher, parameters)
            return [record.data() for record in result]

    def bootstrap(self):
        """Initializes database constraints and indexes."""
        queries = [
            "CREATE CONSTRAINT user_id     IF NOT EXISTS FOR (u:User)     REQUIRE u.id IS UNIQUE",
            "CREATE CONSTRAINT place_key   IF NOT EXISTS FOR (p:Place)    REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT category_nm IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE",
            "CREATE CONSTRAINT interest_id IF NOT EXISTS FOR (i:Interest) REQUIRE i.id IS UNIQUE",
            "CREATE CONSTRAINT lead_id     IF NOT EXISTS FOR (l:Lead)     REQUIRE l.id IS UNIQUE",
            "CREATE CONSTRAINT vendor_id   IF NOT EXISTS FOR (v:Vendor)  REQUIRE v.id IS UNIQUE",
            "CREATE INDEX place_norm       IF NOT EXISTS FOR (p:Place)    ON (p.normalized_key)",
            "CREATE INDEX lead_cat_status  IF NOT EXISTS FOR (l:Lead)     ON (l.category, l.status)",
            "CREATE INDEX vendor_cat_status IF NOT EXISTS FOR (v:Vendor)  ON (v.category, v.status)",
        ]
        for q in queries:
            try:
                self.execute_query(q)
            except Exception as e:
                # Log warning but continue; some drivers or DB versions throw transient warnings
                print(f"Schema boostrap warning for query: {q}. Error: {e}")

    def upsert_user(self, user_id: str, properties: dict[str, Any]):
        """Creates or updates a user node with basic profile details."""
        cypher = """
        MERGE (u:User {id: $user_id})
        ON CREATE SET u.created_at = timestamp()
        SET u += $properties, u.updated_at = timestamp()
        RETURN u
        """
        # Ensure ID isn't duplicated inside properties dictionary
        clean_props = {k: v for k, v in properties.items() if k != "id" and v is not None}
        self.execute_query(cypher, {"user_id": user_id, "properties": clean_props})

    def upsert_profile_fact(self, user_id: str, fact: ProfileFact, source: str = "llm"):
        """Upserts a fact. Direct attributes are stored on User; addresses form Place nodes with active edges."""
        fact_type = fact.type.strip().lower()

        # 1. Address facts: create Place node and handle active links
        if fact_type in ("work_address", "home_address"):
            address_str = fact.value.strip()
            if not address_str:
                return

            # Normalize key to dedup similar strings
            normalized_key = "".join(c for c in address_str.lower() if c.isalnum())
            place_id = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()

            kind = "office" if fact_type == "work_address" else "home"
            rel_type = "WORKS_AT" if fact_type == "work_address" else "LIVES_AT"

            # Step A: Merge the Place node
            place_cypher = """
            MERGE (p:Place {id: $place_id})
            SET p.normalized_key = $normalized_key, p.address_line = $address, p.kind = $kind, p.updated_at = timestamp()
            """
            self.execute_query(place_cypher, {
                "place_id": place_id,
                "normalized_key": normalized_key,
                "address": address_str,
                "kind": kind
            })

            # Step B: Retire old relationships of the same type to different places
            retire_cypher = f"""
            MATCH (u:User {{id: $user_id}})-[r:{rel_type}]->(old_p:Place)
            WHERE old_p.id <> $place_id AND r.valid_to IS NULL
            SET r.valid_to = timestamp()
            """
            self.execute_query(retire_cypher, {"user_id": user_id, "place_id": place_id})

            # Step C: Merge new relationship
            rel_cypher = f"""
            MATCH (u:User {{id: $user_id}}), (p:Place {{id: $place_id}})
            MERGE (u)-[r:{rel_type}]->(p)
            ON CREATE SET r.valid_from = timestamp(), r.confidence = $confidence, r.source = $source
            ON MATCH SET r.confidence = $confidence, r.source = $source, r.valid_to = null
            """
            self.execute_query(rel_cypher, {
                "user_id": user_id,
                "place_id": place_id,
                "confidence": fact.confidence,
                "source": source
            })

        else:
            # 2. Regular attributes (age, name, city, language, etc.) on User node
            # Translate fact_type to safe property name
            prop_map = {
                "age": "age",
                "name": "name",
                "city": "city",
                "language": "language",
                "email": "email",
                "phone": "phone",
                "occupation": "occupation",
                "family_size": "family_size",
                "family_composition": "family_composition",
            }
            prop_name = prop_map.get(fact_type)
            if prop_name:
                cypher = f"""
                MERGE (u:User {{id: $user_id}})
                SET u.{prop_name} = $value, u.updated_at = timestamp()
                """
                self.execute_query(cypher, {"user_id": user_id, "value": fact.value})

    def upsert_interest(self, user_id: str, interest: Interest):
        """Upserts user interest and ties it to Category and fine-grained Interest nodes."""
        cat_name = interest.category.strip().lower()
        detail_name = interest.detail.strip().lower()
        if not cat_name or not detail_name:
            return

        # Unique ID for fine grained interest (e.g. food:ramen)
        interest_id = hashlib.sha256(f"{cat_name}:{detail_name}".encode()).hexdigest()

        # Step A: Merge Category and Interest nodes
        cypher_nodes = """
        MERGE (c:Category {name: $cat_name})
        MERGE (i:Interest {id: $interest_id})
        SET i.category = $cat_name, i.detail = $detail, i.updated_at = timestamp()
        """
        self.execute_query(cypher_nodes, {
            "cat_name": cat_name,
            "interest_id": interest_id,
            "detail": interest.detail
        })

        # Step B: Link User to Category (INTERESTED_IN)
        cypher_cat_rel = """
        MATCH (u:User {id: $user_id}), (c:Category {name: $cat_name})
        MERGE (u)-[r:INTERESTED_IN]->(c)
        SET r.weight = $weight, r.updated_at = timestamp()
        """
        self.execute_query(cypher_cat_rel, {
            "user_id": user_id,
            "cat_name": cat_name,
            "weight": interest.weight
        })

        # Step C: Link User to Interest node (LIKES)
        cypher_likes_rel = """
        MATCH (u:User {id: $user_id}), (i:Interest {id: $interest_id})
        MERGE (u)-[r:LIKES]->(i)
        SET r.weight = $weight, r.updated_at = timestamp()
        """
        self.execute_query(cypher_likes_rel, {
            "user_id": user_id,
            "interest_id": interest_id,
            "weight": interest.weight
        })

        # Step D: Link the category to its leaf interests so consumers can render
        # User -> Category -> Interest instead of a flat cluster around the user.
        cypher_hierarchy_rel = """
        MATCH (c:Category {name: $cat_name}), (i:Interest {id: $interest_id})
        MERGE (c)-[r:HAS_INTEREST]->(i)
        SET r.updated_at = timestamp()
        """
        self.execute_query(cypher_hierarchy_rel, {
            "cat_name": cat_name,
            "interest_id": interest_id,
        })

    def upsert_lead(self, user_id: str, lead: Lead, source: str = "llm") -> dict[str, Any] | None:
        """Upserts a commercial lead and connects it to the generating user."""
        cat_name = lead.category.strip().lower()

        # Generate a fingerprint/id for the lead based on user + product
        normalized_product = "".join(c for c in lead.product.lower() if c.isalnum())
        lead_id = hashlib.sha256(f"{user_id}:{cat_name}:{normalized_product}".encode()).hexdigest()

        attrs_json = json.dumps(lead.attrs)

        cypher = """
        MERGE (c:Category {name: $category_key})
        MERGE (l:Lead {id: $lead_id})
        ON CREATE SET l.created_at = timestamp(), l.status = 'open'
        SET l.category = $category,
            l.product = $product,
            l.attrs = $attrs,
            l.budget_signal = $budget_signal,
            l.intent_score = $intent_score,
            l.confidence = $confidence,
            l.last_seen_at = timestamp(),
            l.consent_status = coalesce(l.consent_status, 'implicit')

        WITH l
        MATCH (u:User {id: $user_id})
        MERGE (u)-[r:GENERATED]->(l)
        SET r.updated_at = timestamp()
        WITH l
        MATCH (c:Category {name: $category_key})
        MERGE (l)-[cr:IN_CATEGORY]->(c)
        SET cr.updated_at = timestamp()
        RETURN l.id AS lead_id,
               l.status AS status,
               l.consent_status AS consent_status,
               l.category AS category,
               l.product AS product,
               l.attrs AS attrs,
               l.budget_signal AS budget_signal,
               l.intent_score AS intent_score,
               l.confidence AS confidence,
               l.last_seen_at AS last_seen_at,
               l.created_at AS created_at
        """
        results = self.execute_query(cypher, {
            "lead_id": lead_id,
            "user_id": user_id,
            "category_key": cat_name,
            "category": lead.category,
            "product": lead.product,
            "attrs": attrs_json,
            "budget_signal": lead.budget_signal,
            "intent_score": lead.intent_score,
            "confidence": lead.confidence
        })
        if results:
            res = results[0]
            if isinstance(res.get("attrs"), str):
                try:
                    res["attrs"] = json.loads(res["attrs"])
                except Exception:
                    pass
            res["user_id"] = user_id
            return res
        return None

    def upsert_vendor(self, vendor: Vendor) -> dict[str, Any] | None:
        """Creates or updates a Vendor node and links it to the category it serves."""
        cat_name = vendor.category.strip().lower()
        if not vendor.id or not cat_name:
            return None
        service_area = [s.strip().lower() for s in vendor.service_area if s and s.strip()]
        metadata_json = json.dumps(vendor.metadata)

        cypher = """
        MERGE (c:Category {name: $category_key})
        MERGE (v:Vendor {id: $vendor_id})
        ON CREATE SET v.created_at = timestamp()
        SET v.name = $name,
            v.category = $category_key,
            v.location = $location,
            v.service_area = $service_area,
            v.budget_range = $budget_range,
            v.contact_phone = $contact_phone,
            v.contact_email = $contact_email,
            v.rating = $rating,
            v.commission_rate = $commission_rate,
            v.status = $status,
            v.joined_at = $joined_at,
            v.metadata = $metadata,
            v.updated_at = timestamp()
        WITH v
        MATCH (c:Category {name: $category_key})
        MERGE (v)-[r:SERVES]->(c)
        SET r.updated_at = timestamp()
        RETURN v.id AS vendor_id, v.name AS vendor_name, v.category AS category, v.status AS status
        """
        results = self.execute_query(cypher, {
            "vendor_id": vendor.id,
            "category_key": cat_name,
            "name": vendor.name,
            "location": vendor.location,
            "service_area": service_area,
            "budget_range": vendor.budget_range,
            "contact_phone": vendor.contact_phone,
            "contact_email": vendor.contact_email,
            "rating": vendor.rating,
            "commission_rate": vendor.commission_rate,
            "status": vendor.status,
            "joined_at": vendor.joined_at,
            "metadata": metadata_json,
        })
        return results[0] if results else None

    def match_leads(
        self,
        *,
        category: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Match open, un-vendored leads to an eligible active vendor.

        Eligibility: same category, vendor active, the lead owner's city is in the
        vendor's service_area (or the vendor serves anywhere), and the vendor's
        budget_range matches the lead's budget_signal (or either is unset). The
        single best vendor (highest rating) wins. On a match the Lead flips to
        'matched', records the vendor, and gains a (:Lead)-[:MATCHED_TO]->(:Vendor)
        edge. Returns one promotion-ready row per newly matched lead.
        """
        clauses = ["l.status = 'open'", "l.vendor_id IS NULL"]
        params: dict[str, Any] = {"limit": limit}
        if category:
            # Vendor categories are stored lowercased; lead categories keep their
            # original case, so always compare on a normalized basis.
            clauses.append("toLower(l.category) = $category")
            params["category"] = category.strip().lower()
        if user_id:
            clauses.append("u.id = $user_id")
            params["user_id"] = user_id
        where = " AND ".join(clauses)

        cypher = f"""
        MATCH (u:User)-[:GENERATED]->(l:Lead)
        WHERE {where}
        CALL {{
            WITH l, u
            MATCH (v:Vendor {{status: 'active'}})
            WHERE v.category = toLower(l.category)
              AND (size(v.service_area) = 0
                   OR u.city IS NULL
                   OR toLower(u.city) IN v.service_area)
              AND (v.budget_range IS NULL
                   OR l.budget_signal IS NULL
                   OR v.budget_range = l.budget_signal)
            RETURN v
            ORDER BY coalesce(v.rating, 0) DESC
            LIMIT 1
        }}
        SET l.status = 'matched',
            l.vendor_id = v.id,
            l.vendor_name = v.name,
            l.matched_at = timestamp()
        MERGE (l)-[m:MATCHED_TO]->(v)
        SET m.matched_at = l.matched_at
        RETURN u.id AS user_id,
               l.id AS lead_id,
               l.category AS category,
               l.product AS product,
               l.budget_signal AS budget_signal,
               l.intent_score AS intent_score,
               l.attrs AS attrs,
               l.matched_at AS matched_at,
               v.id AS vendor_id,
               v.name AS vendor_name,
               v.contact_phone AS vendor_phone,
               v.contact_email AS vendor_email
        LIMIT $limit
        """
        results = self.execute_query(cypher, params)
        for res in results:
            if isinstance(res.get("attrs"), str):
                try:
                    res["attrs"] = json.loads(res["attrs"])
                except Exception:
                    pass
        return results

    def get_user_profile_projection(self, user_id: str) -> dict[str, Any]:
        """Returns the denormalized profile view for the DynamoDB cache."""
        profile: dict[str, Any] = {
            "user_id": user_id,
            "name": None,
            "age": None,
            "city": None,
            "language": None,
            "phone": None,
            "email": None,
            "occupation": None,
            "family_size": None,
            "family_composition": None,
            "addresses": {},
            "interests": [],
            "open_leads": [],
            "matched_leads": [],
            "leads_by_category": {},
            "consent_status": "implicit",
        }

        # A. Query basic user properties
        u_records = self.execute_query("MATCH (u:User {id: $user_id}) RETURN u", {"user_id": user_id})
        if not u_records:
            return profile

        u_node = u_records[0].get("u", {})
        for col in ("name", "age", "city", "language", "phone", "email",
                    "occupation", "family_size", "family_composition"):
            profile[col] = u_node.get(col)

        # B. Query active addresses
        addr_cypher = """
        MATCH (u:User {id: $user_id})-[r:WORKS_AT|LIVES_AT]->(p:Place)
        WHERE r.valid_to IS NULL
        RETURN type(r) as type, p
        """
        addr_records = self.execute_query(addr_cypher, {"user_id": user_id})
        for record in addr_records:
            rel_type = record.get("type")
            p_node = record.get("p", {})
            addr_data = {
                "address_line": p_node.get("address_line"),
                "label": p_node.get("kind", "other"),
                "provider_address_id": p_node.get("provider_address_id"),
                "lat": p_node.get("lat"),
                "lng": p_node.get("lng"),
            }
            if rel_type == "WORKS_AT":
                profile["addresses"]["office"] = addr_data
            elif rel_type == "LIVES_AT":
                profile["addresses"]["home"] = addr_data

        # C. Query top-N interests
        interests_cypher = """
        MATCH (u:User {id: $user_id})-[r:INTERESTED_IN]->(c:Category)
        OPTIONAL MATCH (u)-[:LIKES]->(i:Interest {category: c.name})
        RETURN c.name as category, r.weight as weight, collect(i.detail) as details
        ORDER BY weight DESC
        LIMIT 10
        """
        interests_records = self.execute_query(interests_cypher, {"user_id": user_id})
        for record in interests_records:
            profile["interests"].append({
                "category": record.get("category"),
                "weight": record.get("weight"),
                "details": [d for d in record.get("details", []) if d]
            })

        # D. Query active leads (open + matched). Matched leads carry the vendor
        # so the agent can acknowledge a connection instead of re-pitching.
        leads_cypher = """
        MATCH (u:User {id: $user_id})-[:GENERATED]->(l:Lead)
        WHERE l.status IN ['open', 'matched']
        RETURN l
        ORDER BY l.last_seen_at DESC
        LIMIT 25
        """
        leads_records = self.execute_query(leads_cypher, {"user_id": user_id})
        for record in leads_records:
            l_node = record.get("l", {})

            # Parse attributes
            attrs = {}
            if l_node.get("attrs"):
                try:
                    attrs = json.loads(l_node.get("attrs"))
                except Exception:
                    pass

            lead_view = {
                "lead_id": l_node.get("id"),
                "category": l_node.get("category"),
                "product": l_node.get("product"),
                "status": l_node.get("status"),
                "last_seen_at": l_node.get("last_seen_at"),
                "budget_signal": l_node.get("budget_signal"),
                "intent_score": l_node.get("intent_score"),
                "vendor_id": l_node.get("vendor_id"),
                "vendor_name": l_node.get("vendor_name"),
                "matched_at": l_node.get("matched_at"),
                "attrs": attrs,
            }

            if lead_view["status"] == "matched":
                profile["matched_leads"].append(lead_view)
            else:
                profile["open_leads"].append(lead_view)

            # Per-category bucket for O(1) "does this user have a <category> lead?"
            cat = lead_view["category"] or "other"
            profile["leads_by_category"].setdefault(cat, []).append(lead_view)

            # Use most recent lead consent status or fallback
            if l_node.get("consent_status"):
                profile["consent_status"] = l_node.get("consent_status")

        return profile
