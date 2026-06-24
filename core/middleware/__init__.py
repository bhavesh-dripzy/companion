"""Shared middleware retained for the MCP/OAuth layer.

The tool-calling brains (planner, dispatcher, validator, selection, errors,
tracing, …) were removed with the legacy orchestrator. Only `repair` remains —
the MCP connection layer imports its backoff helper directly as a submodule.
"""
