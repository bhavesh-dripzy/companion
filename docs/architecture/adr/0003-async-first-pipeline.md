# ADR-0003: Async-first pipeline with provider fan-out

- Status: accepted
- Date: 2026-06-08

## Context

Every provider executor wraps its async MCP session in `asyncio.run`, and `execute_tool_plan_sync`
runs providers sequentially. This forces voice endpoints to stay synchronous (a comment in
`voice_routes.py` warns that calling `run_agent_turn` from async would fail because it calls
`asyncio.run` internally) and makes parallel provider fan-out impossible.

## Decision

Make the entire pipeline `async/await`. `PipelineRunner.run` is `async def`; FastAPI routes are
`async def`; every `asyncio.run` is removed (one event loop per request — Mangum drives it on
Lambda). The `Execute` stage fans out over providers with `asyncio.gather`, tolerant of partial
failure (one provider's error becomes a typed `ToolResult`, never aborting the others). Side-channel
work in `Persist` (leadgen, persistence) does not block the response.

## Consequences

- Voice and chat share the identical pipeline (`await runner.run(...)`).
- "Compare Zomato vs Swiggy" / "order from whichever" flows run concurrently.
- The MCP SDK is natively async, so the shared MCP base is simpler than the sync wrappers it replaces.
- Care is needed that fire-and-forget side-channels still complete before Lambda freeze (await them
  after building the envelope, or enqueue synchronously as today — SQS send is <10ms).
