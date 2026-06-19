# Pipeline Seams — four cross-provider contracts

Source: Instamart prod audit (2026-06-20). The 8 observed bugs are symptoms of **4
missing pipeline-level contracts**, not Instamart bugs. Each fix is a seam every
provider (Zomato, Zepto, Swiggy Food, Kite, District, and future plugins) inherits.
Design principle: fix the layer that owns the concern; never patch a symptom per provider.

---

## 0. The unifying spine (read this first)

The four seams share three primitives. Two already exist in the contracts and are **dead
(producers, no consumers)** — activating them is most of the work.

| Primitive | Where it lives now | Status | Used by seam |
|---|---|---|---|
| `Tool.mutating: bool` | `core/contracts/tool.py:49` (set in `seed.py:150`) | **exists, ZERO consumers** | S1 (narrate gate), S3 (write disambiguation) |
| read/write turn signal | — (only regex `message_is_transactional`, `orchestration.py:131`) | missing/incomplete | S1, S3 |
| `capability_class` on provider | hardcoded tuples `orchestration.py:104-112` + prose `narrate.py:38-41` | **not declared; 3 duplicate copies** | S3 (grouping), S4 (recall), de-dups S1 prose |
| committed selection | `SessionState.selected_address_id` (`session.py:42`), address-only, doesn't round-trip; 5 hand-rolled per-provider keys | partial, duplicated 5× | S2 |
| `ctx.blocked` precedent | `list[ToolCall]` (`context.py:54`), populated `execute.py:149`, surfaced `narrate.py:230` | working pattern to copy | S1, S3 |

The convergence: **`Tool.mutating` is the single read/write discriminator** for both the
post-execution narrate gate (S1) and the pre-execution routing gate (S3). Declaring
`capability_class` once (S3) deletes three duplicated copies of the grocery pairing.
The committed-selection map (S2) is what tells S3 *which* same-class provider wins a write.
Build the spine, the seams snap together.

---

## S1 — Execution-grounding: write-effect attestation  (kills B1, B5; surfaces B6)

**Concern:** the narrator may only assert a state change (cart updated, order placed,
address switched, reminder created) that the tool results actually prove. Today it infers
state from prose + history, so on a 0-tool turn it fabricates `"added 2 × X, ₹154, proceed
to checkout?"`, and it narrates a `get_cart` **error** as `"your cart is empty"`.

**Today (grounded):**
- `ToolResult` (`tool_result.py:31-46`) has `ok / error / error_detail / data` — **no field
  saying a mutation applied.** `ok` conflates "read worked" and "write applied".
- `narrate._results_summary` (`narrate.py:209-236`) is the only per-tool signal the narrator
  sees; it reads only `ok` → `"ok"` / `"error:<code>"`. A 0-tool turn yields `"(no tools ran)"`
  yet nothing structurally forbids a success claim.
- The lone guard is **prose, per tool**: the `make_phone_call` rule (`narrate.py:51-56`). This
  is the exact anti-pattern to generalize — it would need re-writing for every mutating tool.
- Precedent to copy: `ctx.blocked` — typed, single producer (`confirmation.partition` →
  `execute.py:149`), surfaced as a dedicated narrate line (`narrate.py:230-231`).

**Contract change:**
1. Add to `ToolResult` a normalized attestation field, modeled on how `error` is set:
   `effect: Effect | None` where `Effect ∈ {APPLIED, NOT_APPLIED}` and `None` = read-only
   (no claim). Rule: `effect` is non-None **iff** `tool.mutating` (finally giving `mutating`
   a consumer). `APPLIED` only when a mutating call returned a committed envelope;
   `NOT_APPLIED` on any mutating failure/timeout/block.
2. Set it at the three construction sites: `mcp_base.execute_one` (`mcp_base.py:270-283`, it
   already knows `error` and the tool), the HTTP providers (`twilio/provider.py:81-88` already
   has `status`/`ok`), and the exception path (`errors.py:94-101`, defaults mutating → `NOT_APPLIED`).
3. `narrate._results_summary` emits a hard token per mutating call —
   `[swiggy.update_cart MUTATION_APPLIED]` / `MUTATION_NOT_APPLIED` — and **one** generalized
   prompt rule replaces the `make_phone_call` lines: *"You may assert a state change ONLY if
   its result is tagged MUTATION_APPLIED. If no MUTATION_APPLIED entry exists, never claim
   anything was added/ordered/switched/booked — report what the tools returned or ask the
   needed clarifying question."* This is ledger-grounded; it needs no intent inference, so it
   also covers the **0-tool** case (no APPLIED entry ⇒ cannot claim) — which is what makes the
   B6 planner-deferral surface as "which size?" instead of a fake success.
4. (Hardening, optional) For the highest-risk tier (`ConfirmPolicy.TWO_STEP` / checkout /
   place_order), add a deterministic post-narrate assertion check in the repair/validator
   layer: if the reply asserts a mutation with no APPLIED entry, re-narrate. Belt-and-suspenders
   for real-money/irreversible actions.

**Inherited by:** every mutating tool on every provider — cart writes, checkout, Kite orders,
calendar/reminders, WhatsApp/Twilio — all get the same structural truth gate for free.

---

## S2 — Generic committed-selection contract  (kills B3, B4)

**Concern:** "switch my delivery address to Work" / "deliver to my Gurgaon address" must set
the provider's committed entity from natural language, uniformly for any selectable thing
(address, store, payment method, trading account).

**Today (grounded):**
- `SessionState.selected_address_id` (`session.py:42`) is the **only** first-class selection
  field — address-only, and it **doesn't round-trip** (`_to_envelope` emits only `placeholders`
  + `legacy`, `core_shadow.py:153-154`), so it's recomputed from `request.address_id` each turn.
- Five providers hand-roll the same concept in `merge` as a side effect of a *mutating* result:
  Instamart `selected_instamart_address_id` (written **only from a non-empty cart**,
  `provider.py:168-175`), Zepto store binding, Zomato `session_cart`, Kite
  `session_kite_order_ticket`, District `session_reservation`.
- NL → selection exists only as 4 hardcoded home/office keywords (`personalize.py:22-32`).
- Concretely why the switch reverts: UI sends the switch as a **chat message**
  (`App.tsx:1485` "Switch my delivery address to: {label}.") → planner puts the id in a
  `search_products` arg → `merge` ignores search addresses → `_anchor_address`
  (`provider.py:188-204`) re-pins the OLD value next turn.

**Contract change:**
1. Generalize `selected_address_id` → a scoped `selections: dict[str, dict[str, str]]` on
   `SessionState` (provider → entity-kind → id), reusing the **existing** placeholder scoping
   pattern (`provider_view`/`fold_learned`, `placeholders.py:26-44`) so providers stay isolated.
   Make it round-trip (persist + `_to_envelope`).
2. Add a generic **NL-resolution step** beside `personalize._resolve_address` (`personalize.py:16`):
   given the message + each provider's known saved entities (`session_instamart_addresses`,
   future saved stores/cards), resolve "switch to Work" → `(provider, kind, id)` and write it to
   `selections` **before Plan**. This is the missing read-the-message path.
3. Plumb `selections` through `ExecCtx` (`base.py:28-51`, populated in `execute._exec_ctx_for`,
   `execute.py:117-130`) and have the **base** `before_run` (`mcp_base.py:143`) apply it as the
   provider's placeholder — **superseding** each plugin's hand-rolled `_anchor_address`.

**Inherited by:** any provider with a selectable entity. Collapses 5 bespoke implementations
into one path; the cart-only write in Instamart `provider.py:168-175` is deleted.

---

## S3 — Same-class write disambiguation  (kills B2)

**Concern:** a WRITE that matches N providers of the same capability class (grocery:
`swiggy_instamart` + `zepto`) must not fan the mutation to all of them. Reads *should* still
fan out (compare groceries); only writes collapse.

**Today (grounded):**
- The grocery pairing is hardcoded in `expand_agnostic`: `_AGNOSTIC_GROUPS` /
  `_ALWAYS_FANOUT` (`orchestration.py:104-112`), with the stickiness guard deliberately skipped
  for grocery — so **both** fan out every non-named turn. Duplicated again as prose in
  `narrate.py:38-41`. **No `capability_class` is declared on any provider.**
- Write-intent is invisible: `message_is_transactional` regex (`orchestration.py:131-138`)
  misses "add 2 to cart" / "make it 3", so the write reaches both planners; `plan.py:100` loops
  every provider, `Dispatcher.dispatch` fans all calls out (`dispatcher.py:36-43`); the
  confirmation gate (`confirmation.py:30`) has no concept of "two same-class providers both mutating".

**Contract change:**
1. Declare `capability_class: str` on the provider (`base.py` Protocol + `mcp_base.py:55-65`),
   surfaced via the registry. `swiggy_instamart` and `zepto` → `"grocery"`; `zomato` /
   `swiggy_food` → `"food"`. Deletes the three duplicated hardcoded copies.
2. Put the gate **between Plan and Execute** (a small stage, or the head of `Execute` before
   `partition`/dispatch). Using the **already-emitted** calls + `Tool.mutating`: if ≥2 providers
   of the same `capability_class` each have ≥1 mutating call, **collapse to one** — choose by
   (a) provider the user named, else (b) provider with an active committed cart/selection (this
   is **S2's `selections`** — the seams compose), else (c) ask the user. Reads from all providers
   proceed untouched. This uses real emitted tools, not a regex guess.

**Inherited by:** any future same-class provider pair (two ride-hailing, two food). The
disambiguation is declarative (`capability_class`) + structural (`mutating`), not per-pair code.

---

## S4 — Capability-aware routing recall  (kills B7; B8 is the related display-cap)

**Concern:** an intent that a provider can serve must reach that provider. "reorder my go-to
items", "show my saved addresses" must engage `swiggy_instamart` (it has `your_go_to_items`,
`get_addresses`) instead of dropping to `normal_query → providers=[]`.

**Today (grounded):**
- The orchestrator prompt is **product-noun-based** (`orchestrator.system.md` Step-1 table keys
  on milk/atta/eggs/app names). Account/recall verbs ("reorder", "my usual / go-to", "my saved
  addresses", "my orders") have no entry → catch-all `normal_query` →
  `parse_routing` returns `providers=[]` (`orchestration.py:202-203`). (Irony: "reorder" is in
  the transactional regex, but only to *suppress fan-out*, never to *route*.)

**Contract change:**
- Near-term: add the account/recall verb cues to `orchestrator.system.md` so they land in the
  grocery branch (`orchestration.py:185-191`) instead of `normal_query`.
- Long-term (the generic fix): make routing **tool-capability-aware** — derive the orchestrator's
  routable surface from each provider's declared tools (`seed.py`) + `capability_class` (S3),
  so any provider that declares a recall tool (`your_go_to_items`, `get_addresses`, `get_orders`)
  is reachable by its capability, not by a hand-maintained noun list. New providers become
  routable by declaring tools, with no prompt edit.
- B8 (15 saved addresses shown as "8") is the same family: a display cap (`planner_context`
  `[:8]`) leaking into narration — fold into a single capped-list contract that states the cap.

---

## Rollout order (dependency-aware, incremental, safe)

1. **Declare the spine** (no behavior change): `capability_class` on providers; make
   `Tool.mutating` correct/complete in `seed.py`. Pure declaration — ship first, risk ~0.
2. **S1 attestation**: add `ToolResult.effect`, set at the 3 sites, add the narrate token +
   one generalized rule, delete the `make_phone_call` prose. Highest leverage, makes every other
   silent failure *visible* instead of confabulated. Behind a flag; compare narrate output.
3. **S3 disambiguation gate**: between Plan and Execute, keyed on `capability_class` + `mutating`.
   Reads unaffected; only collapses same-class writes.
4. **S2 selection contract**: generalize `selected_address_id` → `selections`, add the NL-resolution
   step, base `before_run` applies it; then S3's tie-break (b) uses it.
5. **S4 recall**: prompt cues now; tool-capability-aware routing as the durable follow-up.

## Traceability — what each seam kills

| Bug | Symptom | Seam |
|---|---|---|
| B1 | narrator claims cart writes that never ran | **S1** |
| B5 | get_cart error narrated as "cart empty" | **S1** |
| B6 | multi-variant add over-defers, hidden by fake success | **S1** (surfaces the ask) + planner prompt |
| B3 | verbal address switch never persists | **S2** |
| B4 | per-query address ignored + misreported | **S2** |
| B2 | grocery write leaks to Zepto | **S3** (tie-break uses S2) |
| B7 | "go-to items" / "saved addresses" → providers=[] | **S4** |
| B8 | 15 addresses shown as "8" | **S4** (display-cap contract) |

All 8 symptoms close from 4 contracts, two of which are activations of fields that already exist.
