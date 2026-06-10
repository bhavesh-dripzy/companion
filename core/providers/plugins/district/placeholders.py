"""District placeholder resolver (port of the legacy executor's alias/issuer maps).

District chains discovery ids across steps and turns (__MOVIE_ID__ from
search_movies into search_shows, __RES_ID__ into slots/booking, …). The shared
resolver only knows the food-commerce placeholders, so without this map nothing
is ever learned and literal "__MOVIE_ID__" strings reach the server.
"""

from __future__ import annotations

from core.providers.mcp.placeholders import DEFAULT_ALIASES, PlaceholderResolver

_ALIASES: dict[str, tuple[str, ...]] = {
    **DEFAULT_ALIASES,
    "__RES_ID__": ("res_id", "restaurant_id", "restaurantId"),
    "__BOOKING_ID__": ("booking_id", "order_id", "orderId"),
    "__MOVIE_ID__": ("movie_id", "movieId", "content_id", "contentId"),
    "__THEATRE_ID__": ("theatre_id", "theater_id", "theatreId", "cinema_id"),
    "__SHOW_ID__": ("show_id", "showId"),
    "__EVENT_ID__": ("event_id", "eventId"),
    "__VENUE_ID__": ("venue_id", "venueId"),
    "__ARTIST_ID__": ("artist_id", "artistId"),
}

# Each discovery search mints a fresh id; carried values are stale on re-run.
_ISSUERS: dict[str, tuple[str, ...]] = {
    "search_dining_restaurants": ("__RES_ID__",),
    "search_movies": ("__MOVIE_ID__",),
    "search_theatres": ("__THEATRE_ID__",),
    "search_shows": ("__SHOW_ID__",),
    "search_events": ("__EVENT_ID__",),
    "search_event_venues": ("__VENUE_ID__",),
    "search_artists": ("__ARTIST_ID__",),
    "create_table_reservation_booking": ("__BOOKING_ID__",),
}


class DistrictResolver(PlaceholderResolver):
    """Shared resolver armed with District's entity-id aliases and issuers."""

    def __init__(self) -> None:
        super().__init__(aliases=_ALIASES, issuers=_ISSUERS)
