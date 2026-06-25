"""Product reference resolution for the grocery domain (kept separate for the file-size budget).

The LLM Resolver does the matching; this module owns the *scoping* and the *safety nets* around it:
- `resolve_refs`: pick the candidate set (cart-only for set_qty/remove, else the whole provider
  catalog), call the LLM, apply a deterministic fallback when it punts, and re-search once on a miss.
- `name_candidates`/`fallback`: token-overlap safety net so a punting LLM still resolves/asks.
- `disambig_options`: shape ambiguous matches into the pending-action options the FE renders.
All provider-agnostic — they operate on the normalized catalog projection + Resolver contract.
"""

from __future__ import annotations

from typing import Any

from core.agent.contracts import fmt_minor
from core.agent.debug import log, short
from core.agent.llm import resolve

_STOP = {
    "wala",
    "wali",
    "ka",
    "ki",
    "ke",
    "the",
    "add",
    "kar",
    "do",
    "mujhe",
    "chahiye",
    "ek",
    "bhi",
    "se",
    "hata",
    "remove",
}
# Fallback only — the Router's per-item `pick` is the source of truth. These catch a superlative the
# Router left in the phrase (incl. Hindi gender forms: sasti/mehngi), so word-matching is never relied on.
_CHEAP = ("sasta", "saste", "sasti", "cheapest", "cheap", "lowest")
_COSTLY = (
    "mehnga",
    "mehenga",
    "mahanga",
    "mehngi",
    "mehengi",
    "costliest",
    "premium",
    "expensive",
    "highest",
)


def _pick_from_phrase(phrase: str) -> str | None:
    low = phrase.lower()
    if any(w in low for w in _CHEAP):
        return "cheapest"
    return "costliest" if any(w in low for w in _COSTLY) else None


def superlative_ref(
    compact: list[dict[str, Any]], phrase: str, pick: str | None = None
) -> str | None:
    """The cheapest/costliest match, picked DETERMINISTICALLY. `pick` is the Router's explicit per-item
    indicator ("cheapest"/"costliest") — the source of truth; when absent we derive it from the phrase
    as a fallback (handles "sasta"/"sasti"/"mehnga"/…). Scopes to the name-matched items, else all."""
    kind = pick if pick in ("cheapest", "costliest") else _pick_from_phrase(phrase)
    if not kind:
        return None
    cands = set(name_candidates(compact, phrase)) or {c["ref"] for c in compact}
    priced = [(c["price"], c["ref"]) for c in compact if c["ref"] in cands and c.get("price")]
    if not priced:
        return None
    return (min if kind == "cheapest" else max)(priced)[1]


async def resolve_super(
    compact: list[dict[str, Any]], requests: list[dict[str, Any]]
) -> dict[str, Any]:
    """LLM resolve, but pick price superlatives deterministically (they bypass the LLM)."""
    sup, normal = [], []
    for r in requests:
        ref = superlative_ref(compact, r.get("phrase", ""), r.get("pick"))
        (sup if ref else normal).append({"ref": ref, "qty": r.get("qty", 1)} if ref else r)
    refs = (
        await resolve(compact, normal)
        if normal
        else {"resolved": [], "ambiguous": [], "unresolved": []}
    )
    refs["resolved"] = (refs.get("resolved") or []) + sup
    return refs


def _net(refs: dict[str, Any], compact: list[dict[str, Any]], requests: list[dict[str, Any]]):
    """Deterministic safety net over the LLM result. Re-classifies ONLY the phrases the LLM left
    `unresolved` (and a fully-empty reply) via name overlap against the SAME candidate set: an
    in-context item the LLM missed becomes resolved/ambiguous; a phrase with NO catalog match stays
    `unresolved` (a trustworthy 'not in context' signal). Pure: no I/O."""
    if not (refs["resolved"] or refs["ambiguous"] or refs["unresolved"]):
        return fallback(compact, requests)
    if not refs.get("unresolved"):
        return refs
    pending = [r for r in requests if r.get("phrase") in set(refs["unresolved"])]
    fb = fallback(compact, pending)
    return {
        "resolved": refs["resolved"] + fb["resolved"],
        "ambiguous": refs["ambiguous"] + fb["ambiguous"],
        "unresolved": fb["unresolved"],
    }


async def resolve_refs(state, adapter, s, ctx, pid, requests, cart_only=False) -> dict[str, Any]:
    """Classify each request against the CURRENT session catalog (cart items only for set_qty/remove).
    Pure resolution — no re-search: `unresolved` means 'not in context', which the add handler turns
    into a discovery search (FIND), while set_qty/remove treat it as 'not in your cart'."""
    compact = (
        state.catalog.compact_cart(pid, state.cart(pid))
        if cart_only
        else state.catalog.compact(pid, state.cart(pid))
    )
    refs = _net(await resolve_super(compact, requests), compact, requests)
    log.debug(
        "RESOLVER %s %s n=%d req=%s → resolved=%s ambiguous=%s unresolved=%s",
        pid,
        "cart-only" if cart_only else "catalog",
        len(compact),
        short([r.get("phrase") for r in requests]),
        short(refs.get("resolved")),
        short(refs.get("ambiguous")),
        short(refs.get("unresolved")),
    )
    return refs


def plan_intents(intent: dict[str, Any], ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Deterministic provider fan-out → {provider_id: [item_refs]}, the turn's execution plan.

    Each item runs on its OWN app(s), so multi-app requests fall out for free: an item tagged with
    `provider` goes to THAT app; an UNTAGGED item goes to EVERY app (no app named = both stores). A
    whole-message `provider_hint` of one app scopes untagged items to it; "both"/null → all apps.
    Item-less actions (clear/show_cart) plan the same base apps with empty lists. Order preserved."""
    hint = intent.get("provider_hint")
    base = [hint] if hint in ids else list(ids)  # single named app, else both ("both"/null)
    refs = intent.get("item_refs", [])
    if not refs:
        return {pid: [] for pid in base}
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in refs:
        tag = r.get("provider")
        for pid in [tag] if tag in ids else base:
            buckets.setdefault(pid, []).append(r)
    return buckets


def name_candidates(compact: list[dict[str, Any]], phrase: str) -> list[str]:
    toks = [t for t in phrase.lower().split() if t not in _STOP and len(t) > 2]
    if not toks:
        return []
    # rank by how MANY query tokens match; keep only the best-scoring (so "amul bread"
    # prefers the amul *bread*, not every amul *milk*).
    scored: list[tuple[int, str]] = []
    for p in compact:
        hay = (p.get("name", "") + " " + (p.get("brand") or "")).lower()
        hits = sum(1 for t in toks if t in hay)
        if hits:
            scored.append((hits, p["ref"]))
    if not scored:
        return []
    best = max(h for h, _ in scored)
    return [r for h, r in scored if h == best][:8]


def fallback(compact: list[dict[str, Any]], requests: list[dict[str, Any]]) -> dict[str, Any]:
    res: dict[str, Any] = {"resolved": [], "ambiguous": [], "unresolved": []}
    for r in requests:
        cands = name_candidates(compact, r.get("phrase", ""))
        if len(cands) == 1:
            res["resolved"].append({"ref": cands[0], "qty": r.get("qty", 1)})
        elif cands:
            res["ambiguous"].append({"phrase": r.get("phrase", ""), "refs": cands[:5]})
        else:
            res["unresolved"].append(r.get("phrase", ""))
    return res


def disambig_options(state, pid: str, refs: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape ambiguous matches into pending-action options the FE renders as product cards
    (same look as search results): each choice carries image + size + price so the user picks
    the exact variant in one tap, no re-typing."""
    out = []
    for a in refs.get("ambiguous", []):
        choices = []
        for r in a.get("refs", []):
            p = state.catalog.by_ref(r)
            choices.append(
                {
                    "ref": r,
                    "provider": pid,
                    "name": p.name if p else r,
                    "price": fmt_minor(p.price_minor) if p else "",
                    "image": p.image_url if p else "",
                    "size": p.variation if p else "",
                }
            )
        out.append({"phrase": a.get("phrase", ""), "choices": choices})
    return out
