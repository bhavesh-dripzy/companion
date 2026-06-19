"""A provider's natural-language-selectable entity (delivery address, store, …).

Declarative seam: a provider lists its `Selectable`s and the generic selection
resolver (core/middleware/selection.py) lets the user switch the committed entity
by name ("deliver to Work") without per-provider parsing. The resolved id is
written to `committed_key` in the session — which the provider's before_run anchor
already reads — and round-trips to the client.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Selectable:
    kind: str
    saved_key: str
    committed_key: str
    id_field: str = "address_id"
    label_fields: tuple[str, ...] = ("addressTag", "label")
