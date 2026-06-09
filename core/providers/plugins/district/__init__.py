"""District plugin (clean McpProvider) — the sole District path.

The read-only discovery surfaces — dining, movies, theatres, shows, events,
venues, artists — are validated against real District data (connection, execution,
typed card-building, per-entity session LRUs; tests/core/test_district_provider.py
+ live read-only searches on admin_bhavesh tokens). Mutating reservation bookings
execute through the same generic `McpProvider.run`; a richer reservation card is a
future enhancement.
"""

from core.providers.plugins.district.provider import DistrictProvider

__all__ = ["DistrictProvider"]
