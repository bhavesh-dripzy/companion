"""Tests for the RET10 message builders and the flow-driver state machine."""

from __future__ import annotations

import companion_api.ondc_flow as flow
from companion_api.ondc_retail import (
    cancel_message,
    confirm_message,
    init_message,
    status_message,
)


def test_init_message_shape():
    msg = init_message(
        "P1", ["I1"], created_at="2026-01-01T00:00:00.000Z", location_ids=["L1"], fulfillment_id="F1"
    )
    order = msg["order"]
    assert order["provider"] == {"id": "P1", "locations": [{"id": "L1"}]}
    assert order["items"][0] == {"id": "I1", "quantity": {"count": 1}, "fulfillment_id": "F1"}
    assert order["billing"]["created_at"] == order["billing"]["updated_at"] == "2026-01-01T00:00:00.000Z"
    assert order["fulfillments"][0]["type"] == "Delivery"
    assert "quote" not in order and "payment" not in order


def test_confirm_echoes_on_init_and_accepts_terms():
    on_init_order = {
        "id": "O1",
        "quote": {"price": {"value": "150.00", "currency": "INR"}},
        "payment": {"@ondc/org/settlement_basis": "delivery"},
        "tags": [{"code": "bpp_terms", "list": [{"code": "tax_number", "value": "X"}]}],
    }
    order = confirm_message(on_init_order, created_at="2026-01-01T00:00:00.000Z")["order"]
    assert order["id"] == "O1" and order["state"] == "Created"
    assert order["payment"]["status"] == "PAID"
    assert order["payment"]["params"]["amount"] == "150.00"
    assert order["payment"]["@ondc/org/settlement_basis"] == "delivery"  # echoed from on_init
    codes = {t["code"] for t in order["tags"]}
    assert codes == {"bpp_terms", "bap_terms"}
    bap = next(t for t in order["tags"] if t["code"] == "bap_terms")
    tax = next(i for i in bap["list"] if i["code"] == "tax_number")
    assert len(tax["value"]) == 15  # valid GSTIN length


def test_status_and_cancel():
    assert status_message("O1") == {"order_id": "O1"}
    assert cancel_message("O1", "003") == {"order_id": "O1", "cancellation_reason_id": "003"}


def test_flow_chains_search_through_status(monkeypatch):
    sent: list[dict] = []
    store: dict[str, dict] = {}

    def fake_send(action, message, **kw):
        txn = kw.get("transaction_id") or "T1"
        sent.append({"action": action, "message": message, **kw})
        return {"context": {"transaction_id": txn}, "status_code": 200, "ack": {}}

    monkeypatch.setattr(flow, "send_action", fake_send)
    monkeypatch.setattr(flow, "save_transaction", lambda txn, **st: store.__setitem__(txn, st))
    monkeypatch.setattr(flow, "get_transaction", lambda txn: store.get(txn, {}))

    out = flow.start_order_flow("milk", gps="12.9,77.5", area_code="560001")
    assert out["transaction_id"] == "T1" and sent[0]["action"] == "search"

    ctx = {
        "transaction_id": "T1",
        "bpp_id": "pramaan.ondc.org/beta/preprod/mock/seller",
        "bpp_uri": "https://pramaan.ondc.org/beta/preprod/mock/seller",
    }
    on_search = {"context": ctx, "message": {"catalog": {"bpp/providers": [
        {"id": "PR1", "descriptor": {"name": "Store"}, "items": [
            {"id": "IT1", "descriptor": {"name": "Milk"}, "price": {"value": "55", "currency": "INR"},
             "fulfillment_id": "F1", "location_id": "L1"},
        ]},
    ]}}}
    flow.advance("on_search", on_search)
    assert sent[1]["action"] == "select" and sent[1]["bpp_uri"].endswith("/mock/seller")
    assert store["T1"]["provider_id"] == "PR1" and store["T1"]["item_ids"] == ["IT1"]

    on_select = {"context": ctx, "message": {"order": {"quote": {"price": {"value": "55", "currency": "INR"}}}}}
    flow.advance("on_select", on_select)
    assert sent[2]["action"] == "init" and store["T1"]["billing_created_at"]

    on_init = {"context": ctx, "message": {"order": {
        "id": "ORD1", "quote": {"price": {"value": "60", "currency": "INR"}}, "payment": {}}}}
    flow.advance("on_init", on_init)
    assert sent[3]["action"] == "confirm"
    assert sent[3]["message"]["order"]["id"] == "ORD1"
    assert sent[3]["message"]["order"]["payment"]["status"] == "PAID"
    # billing created_at is byte-identical to the init leg
    assert sent[3]["message"]["order"]["created_at"] == store["T1"]["billing_created_at"]

    on_confirm = {"context": ctx, "message": {"order": {"id": "ORD1", "state": "Accepted"}}}
    flow.advance("on_confirm", on_confirm)
    assert sent[4]["action"] == "status" and sent[4]["message"] == {"order_id": "ORD1"}


def test_advance_ignores_unknown_or_txnless(monkeypatch):
    monkeypatch.setattr(flow, "get_transaction", lambda txn: {})
    assert flow.advance("on_status", {"context": {"transaction_id": "T1"}}) is None  # terminal, no handler
    assert flow.advance("on_search", {"context": {}}) is None  # no txn id
