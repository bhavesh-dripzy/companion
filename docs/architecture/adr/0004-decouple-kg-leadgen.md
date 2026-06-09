# ADR-0004: Decouple the KG / leadgen subsystem behind two protocols

- Status: accepted
- Date: 2026-06-08

## Context

The knowledge-graph / leadgen path is wired into the request pipeline (a per-turn SQS enqueue in
`main.py`/`voice_routes.py` and a profile-cache read in `pipeline.py`). It is already loosely coupled
(fire-and-forget enqueue, fail-open read) but there is no formal boundary, so the two subsystems
cannot evolve independently (issue #28). There is also confirmed infra drift: the live Lambda reads
the CDK-managed `companion-graph-ProfileCacheTable…` / `…LeadCacheTable…`, while bare
`companion-profile-cache` / `companion-lead-cache` tables are orphans.

## Decision

Define two protocols in `core/boundaries/`: `TurnEventPublisher.publish(event)` and
`ProfileReadService.get(user_id) -> Profile | None` (fail-open). `Personalize` and `Persist` depend
only on these; the pipeline imports neither `leadgen_queue`, `profile_cache`, nor `graph_client`.
Concrete impls (SQS publisher, cached-profile reader) adapt the existing repos unchanged; tests
inject no-op doubles. Retire the orphan cache tables after confirming they hold no unique data;
make canonical table names explicit per environment.

## Consequences

- The EC2/Neo4j worker is an optional, independently-deployable consumer behind the queue.
- The pipeline runs fine with KG absent (no personalization, no crash).
- New KG *features* (e.g. issue #11) are out of scope for the decouple itself.
