"""newsdata.io news provider — richer than SerpApi google_news: every article carries a
`description` (a real summary), so we can show fuller cards AND explain a story without a
second lookup. Shared HTTP client (cache + budget + key) + a pure params/extract pair.
"""
