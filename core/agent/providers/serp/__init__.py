"""SerpApi-backed read-only data providers (Google News, and future engines).

A shared HTTP client (`client.search`) with a TTL cache + daily budget guard, plus per-engine
pure `params`/`extract` helpers. Each consumer domain (e.g. `news_agent`) forms a query, calls
`search`, and renders the lean extracted result — no per-user auth, no booking, just lookups.
"""
