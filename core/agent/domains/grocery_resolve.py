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
_CHEAP = ("sasta", "saste", "cheapest", "cheap", "lowest")
_COSTLY = ("mehnga", "mehenga", "mahanga", "costliest", "premium", "expensive", "highest")


def superlative_ref(compact: list[dict[str, Any]], phrase: str) -> str | None:
    """'sabse sasta X' / 'cheapest X' → lowest-priced; 'sabse mehnga' → highest. Deterministic
    (the LLM is unreliable on superlatives). Scopes to the name-matched items, else all shown."""
    low = phrase.lower()
    kind = (
        "low"
        if any(w in low for w in _CHEAP)
        else ("high" if any(w in low for w in _COSTLY) else None)
    )
    if not kind:
        return None
    cands = set(name_candidates(compact, phrase)) or {c["ref"] for c in compact}
    priced = [(c["price"], c["ref"]) for c in compact if c["ref"] in cands and c.get("price")]
    if not priced:
        return None
    return (min if kind == "low" else max)(priced)[1]


async def resolve_super(
    compact: list[dict[str, Any]], requests: list[dict[str, Any]]
) -> dict[str, Any]:
    """LLM resolve, but pick price superlatives deterministically (they bypass the LLM)."""
    sup, normal = [], []
    for r in requests:
        ref = superlative_ref(compact, r.get("phrase", ""))
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
    `unresolved` (and a fully-empty LLM reply) via name overlap against the SAME candidate set, so:
    an in-context item the LLM missed still becomes resolved/ambiguous, and a phrase with NO catalog
    match stays `unresolved` — a trustworthy 'not in context' signal (the handler turns add-misses
    into a fresh search). Pure: it never does I/O."""
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


async def resolve_combined(state, providers, requests, cart_only) -> dict[str, Any]:
    """Resolve over the COMBINED catalog/carts of several providers. Refs are global, so the
    resolved product's provider can be read back from the catalog (§6b provider inference)."""
    compact: list[dict[str, Any]] = []
    for pid in providers:
        compact += (
            state.catalog.compact_cart(pid, state.cart(pid))
            if cart_only
            else state.catalog.compact(pid, state.cart(pid))
        )
    refs = await resolve_super(compact, requests)
    if not (refs["resolved"] or refs["ambiguous"] or refs["unresolved"]):
        refs = fallback(compact, requests)
    return refs


async def infer_provider(state, providers, intent) -> str | None:
    """Pick the provider that owns the referenced item, by resolving across all catalogs.
    Returns a single provider id, or None when the item resolves to 0 / >1 providers (→ ask)."""
    cart_only = intent["action"] in ("set_qty", "remove")
    refs = await resolve_combined(state, providers, intent.get("item_refs", []), cart_only)
    provs = {
        p.provider
        for r in refs.get("resolved", [])
        if (p := state.catalog.by_ref(r.get("ref"))) is not None
    }
    log.debug(
        "INFER provider for %s → candidates=%s resolved_providers=%s",
        short([r.get("phrase") for r in intent.get("item_refs", [])]),
        providers,
        provs,
    )
    return provs.pop() if len(provs) == 1 else None


async def choose_providers(state, intent, providers) -> tuple[list[str], dict[str, Any] | None]:
    """§6b provider selection → (chosen_ids, ask_result|None).
    Priority: explicit hint > fan-out(search) > sticky active > single provider >
    resolved-item provider (cart ops) > ask. active_provider := the single app, or "both" on a
    fan-out — and "both" isn't in `ids`, so it skips sticky and lets the next cart op infer."""
    ids = {p.id for p in providers}
    action, hint = intent["action"], intent.get("provider_hint")
    chosen: list[str] | None = None
    if hint == "both":
        chosen = [p.id for p in providers]
    elif hint in ids:
        chosen = [hint]
    elif action == "search":
        chosen = [p.id for p in providers]  # fan-out
    elif state.active_provider in ids:
        chosen = [state.active_provider]  # sticky
    elif len(providers) == 1:
        chosen = [providers[0].id]
    elif action in ("add", "set_qty", "remove"):
        pid = await infer_provider(state, sorted(ids), intent)  # infer from the referenced item
        chosen = [pid] if pid else None
    if not chosen:
        return [], {
            "domain": "grocery",
            "status": "need_provider",
            "error": None,
            "cards": [],
            "pending_action": {"type": "pick_provider", "options": sorted(ids)},
            "note": f"Kaunse app mein — {' ya '.join(p.capitalize() for p in sorted(ids))}?",
        }
    # A single chosen app becomes sticky; a fan-out (both apps) marks the context "both" — that's
    # not a single app, so the next cart op skips sticky and infers the item's app from the catalog.
    state.active_provider = chosen[0] if len(chosen) == 1 else "both"
    return chosen, None


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
