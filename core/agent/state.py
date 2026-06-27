"""ConversationState + SessionCatalog + StateStore (the authoritative server state).

SessionCatalog is **provider-keyed** (resolution never mixes providers); every shown product
gets a short global `ref` printed on its card and used by the Resolver. Carts coexist per
provider. `StateStore` is in-memory for now (swap to DynamoDB `pk=user, sk=chat#id` later —
the shape already maps to one item per (user, chat)).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.agent.contracts import Cart, Product


class SessionCatalog:
    """Append-on-show index of products, keyed by provider, addressable by `ref`.

    Two views over the SAME products:
      • flat  — `entries` (ref -> Product), used by taps + the Resolver (unchanged);
      • notepad — `_buckets` (provider, item-category) -> price-ranked refs, the structured
        "what's on the shelf" the grounded selection step reads. Built from the search query;
        cart-enrichment appends (no query) only touch the flat view.
    """

    def __init__(self) -> None:
        self.entries: dict[str, Product] = {}  # ref -> Product
        self._by_key: dict[tuple[str, str], str] = {}  # (provider,item_id) -> ref
        self._n = 0
        self._buckets: dict[tuple[str, str], list[str]] = {}  # (provider, category) -> [refs]

    @staticmethod
    def _cat(s: str) -> str:
        """Normalize a search query into a stable category key ('Brown Bread' == 'brown bread')."""
        return " ".join((s or "").lower().split())

    def append(self, products: list[Product], query: str = "") -> list[str]:
        refs: list[str] = []
        for p in products:
            key = (p.provider, p.item_id)
            ref = self._by_key.get(key)
            if ref is None:
                self._n += 1
                ref = f"p{self._n}"
                self._by_key[key] = ref
            p.extra["ref"] = ref  # stamp the ref so cards can reference it
            self.entries[ref] = p
            refs.append(ref)
        # A search for `query` defines this provider's current shelf for that item (replace, so the
        # notepad reflects the latest results). Cart-enrichment appends pass no query → flat-only.
        cat = self._cat(query)
        if cat and products:
            self._buckets[(products[0].provider, cat)] = refs
        return refs

    def by_ref(self, ref: str) -> Product | None:
        return self.entries.get(ref)

    def ref_of(self, provider: str, item_id: str) -> str | None:
        """The ref for an already-seen product by (provider, item_id) — addresses live cart lines."""
        return self._by_key.get((provider, item_id))

    def bucket(self, provider: str, category: str) -> list[tuple[str, Product]]:
        """The shown products for (provider, item-category), cheapest first."""
        refs = self._buckets.get((provider, self._cat(category)), [])
        pairs = [(r, self.entries[r]) for r in refs if r in self.entries]
        return sorted(pairs, key=lambda rp: rp[1].price_minor)

    def cheapest(self, provider: str, category: str) -> tuple[str, Product] | None:
        b = self.bucket(provider, category)
        return b[0] if b else None

    def categories(self, provider: str) -> list[str]:
        return [cat for (prov, cat) in self._buckets if prov == provider]

    def structured(self, provider: str | None = None) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Per-app → per-item → price-ranked projection — the 'notepad' the grounded step reads."""
        out: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for prov, cat in self._buckets:
            if provider and prov != provider:
                continue
            items = [
                {
                    "ref": r,
                    "name": p.name,
                    "brand": p.brand,
                    "variation": p.variation,
                    "price": p.price_minor / 100,
                }
                for r, p in self.bucket(prov, cat)
            ]
            out.setdefault(prov, {})[cat] = items
        return out

    def slice(self, provider: str) -> list[tuple[str, Product]]:
        return [(r, p) for r, p in self.entries.items() if p.provider == provider]

    def compact(self, provider: str, cart: Cart | None = None) -> list[dict[str, Any]]:
        """Compact projection for the Resolver prompt (this provider only)."""
        in_cart = {i.item_id for i in (cart.items if cart else [])}
        return [
            {
                "ref": r,
                "name": p.name,
                "brand": p.brand,
                "variation": p.variation,
                "price": p.price_minor / 100,
                "in_cart": p.item_id in in_cart,
            }
            for r, p in self.slice(provider)
        ]

    def compact_cart(self, provider: str, cart: Cart) -> list[dict[str, Any]]:
        """Only the items currently IN the cart — the candidate set for set_qty/remove
        (so 'doodh'/'coke' resolve to the cart item, not the whole catalog)."""
        ids = {i.item_id for i in cart.items}
        return [
            {
                "ref": r,
                "name": p.name,
                "brand": p.brand,
                "variation": p.variation,
                "price": p.price_minor / 100,
                "in_cart": True,
            }
            for r, p in self.slice(provider)
            if p.item_id in ids
        ]


@dataclass
class ConversationState:
    user_id: str
    chat_id: str
    active_domain: str | None = None
    active_provider: str | None = None
    carts: dict[str, Cart] = field(default_factory=dict)  # provider -> Cart
    selected_context: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # domain -> {type, address_id|lat/lng,...}
    provider_session: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # provider -> {device_id, store_id,...}
    history: list[dict[str, str]] = field(default_factory=list)
    catalog: SessionCatalog = field(default_factory=SessionCatalog)
    pending_action: dict[str, Any] | None = None
    # provider -> item_ids added DURING this session, so the grounded brain can tell freshly-added
    # items from ones that were already in the cart ("purane hata do" removes only the latter).
    session_added: dict[str, set[str]] = field(default_factory=dict)
    # providers for which we've ALREADY told the user "ye items pehle se the" — so we mention old
    # leftover items only ONCE per session, not on every add.
    old_items_flagged: set[str] = field(default_factory=set)
    # Direct chat-screen cart actions (UI taps) since the user last spoke — given to the brain so it
    # has full context of what changed in the UI. Consumed (cleared) once the brain has seen them.
    recent_ui_actions: list[str] = field(default_factory=list)

    def cart(self, provider: str) -> Cart:
        return self.carts.setdefault(provider, Cart(provider=provider))

    def recent_history(self, n: int = 8) -> list[dict[str, str]]:
        return self.history[-n:]

    def add_history(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})


class StateStore:
    """In-memory store keyed by (user, chat). Replace with DynamoDB without touching callers."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], ConversationState] = {}

    def load(self, user_id: str, chat_id: str) -> ConversationState:
        key = (user_id, chat_id)
        st = self._store.get(key)
        if st is None:
            st = ConversationState(user_id=user_id, chat_id=chat_id)
            self._store[key] = st
        return st

    def save(self, state: ConversationState) -> None:
        self._store[(state.user_id, state.chat_id)] = state


STORE = StateStore()
