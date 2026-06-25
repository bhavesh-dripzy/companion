"""Shape tests for the IGM 2.0 and RSF 2.0 message builders."""

from __future__ import annotations

from companion_api.ondc_igm import build_issue, build_issue_close
from companion_api.ondc_rsf import _rsf_context, on_recon_message, recon_message

_ORDER_STATE = {
    "order_id": "O1", "provider_id": "P1", "item_ids": ["I1"],
    "bpp_id": "pramaan.ondc.org/beta/preprod/mock/seller", "fulfillment_id": "F1",
}


def test_igm_open_issue_invariants():
    issue = build_issue(_ORDER_STATE, "ORDER-TXN-1")["issue"]
    assert issue["status"] == "OPEN" and issue["level"] == "ISSUE"
    assert issue["created_at"] == issue["updated_at"]
    assert issue["source_id"] == "CONSUMER-1"
    assert issue["complainant_id"] == "INP-1"
    assert issue["last_action_id"] == issue["actions"][-1]["id"]
    ref_types = {r["ref_type"] for r in issue["refs"]}
    # no TRANSACTION ref — the Pramaan bench rejects it as invalid for an IGM 2.0 issue
    assert {"ORDER", "PROVIDER", "FULFILLMENT", "ITEM"} <= ref_types
    actor_types = {a["type"] for a in issue["actors"]}
    assert {"CONSUMER", "INTERFACING_NP", "COUNTERPARTY_NP"} <= actor_types


def test_igm_escalation_and_close():
    esc = build_issue(_ORDER_STATE, "T1", level="GRIEVANCE", action_code="ESCALATED")["issue"]
    assert esc["level"] == "GRIEVANCE"
    assert esc["actions"][-1]["descriptor"]["code"] == "ESCALATED"

    close = build_issue_close(_ORDER_STATE, "T1", issue_id="ISSUE-1")["issue"]
    assert close["status"] == "CLOSED"
    # action code is CLOSE (not CLOSED) — only CLOSE triggers the bench's mandatory-tags rule
    assert close["actions"][-1]["descriptor"]["code"] == "CLOSE"
    assert close["actions"][-1]["tags"]


def test_rsf_recon_settlement_required_fields():
    settlement = recon_message("ORDER-1")["orders"][0]["settlements"][0]
    required = {"id", "payment_id", "status", "amount", "commission",
                "withholding_amount", "tcs", "tds", "updated_at"}
    assert required <= set(settlement)
    assert settlement["amount"]["currency"] == "INR"


def test_rsf_on_recon_adds_accord():
    inbound = {"message": recon_message("ORDER-1"), "context": {}}
    out = on_recon_message(inbound)
    assert out["orders"][0]["recon_accord"] is True


def test_rsf_context_domain_and_location():
    ctx = _rsf_context("recon", bap_id="b", bap_uri="u", bpp_id="p", bpp_uri="pu")
    assert ctx["domain"] == "ONDC:NTS10" and ctx["version"] == "2.0.0"
    assert ctx["location"]["country"]["code"] == "IND"
    on_ctx = _rsf_context("on_recon", bap_id="b", bap_uri="u", bpp_id="p", bpp_uri="pu")
    assert on_ctx["location"]["city"]["code"] == "*"
