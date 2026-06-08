"""District plugin (clean McpProvider).

Status (migration): the read-only discovery surfaces — dining, movies, theatres,
shows, events, venues, artists — are ported clean (connection, execution, typed
card-building, per-entity session LRUs) and validated against real District data
(tests/core/test_district_provider.py + live read-only searches on admin_bhavesh
tokens). The reservation card (mutating booking) stays on the proven legacy
executor via the LegacyMcpAdapter; the swap is gated on porting it + session parity.
"""

from core.providers.plugins.district.provider import DistrictProvider

__all__ = ["DistrictProvider"]
