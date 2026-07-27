"""MovieGlu provider (priority 2).

Stubbed but structurally complete: if MovieGlu credentials are present this is
where the filmsNowShowing / filmShowTimes calls slot in. MovieGlu also does not
provide seat maps, so results are "check manually" for seats.

Docs: https://developer.movieglu.com/
"""
from __future__ import annotations

import logging

from ..config import get_settings
from .base import ProviderQuery, ProviderShowtime, ShowtimeProvider

logger = logging.getLogger("showtime_finder.movieglu")


class MovieGluProvider(ShowtimeProvider):
    name = "movieglu"

    def available(self) -> bool:
        return get_settings().has_movieglu

    async def fetch(self, query: ProviderQuery) -> list[ProviderShowtime]:
        # Intentionally not implemented for v1 beyond the interface. Wire the
        # MovieGlu REST calls here (they require the client/authorization/
        # territory headers from config) and map their response into
        # ProviderShowtime records with the same shape as SerpApi.
        logger.info("MovieGlu configured but the v1 fetch is a stub; returning no rows.")
        return []
