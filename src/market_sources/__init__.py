"""market_sources — marketplace adapters plus the polite-HTTP layer under them.

* :mod:`market_sources.base` — the ``ProductSource`` Protocol and the
  ``RawProduct`` / ``RawOffer`` / ``RawReview`` models every adapter returns.
* :mod:`market_sources.polite_http` — ``PoliteClient``: semaphore + jittered
  delay + browser headers + optional proxy + retry/backoff.
* :mod:`market_sources.playwright_session` — ``BrowserSession``: lazy persistent
  headless Chromium with serialized navigation and relaunch-retry (needs the
  ``browser`` extra to *run*, never to import).
* :mod:`market_sources.kaspi` / ``.olx`` (JSON APIs over ``PoliteClient``) and
  :mod:`market_sources.ozon` (composer-api over ``BrowserSession``).
* :mod:`market_sources.kimovil` — phone specs by marketplace title (``kimovil`` extra).
"""

from .base import ProductSource, RawOffer, RawProduct, RawReview, RawReviewStats
from .kaspi.source import KaspiSource
from .kimovil.source import KimovilSource
from .olx.source import OlxSource
from .ozon.source import OzonSource
from .playwright_session import BrowserSession, BrowserSessionError
from .polite_http import PoliteClient, parse_retry_after

__all__ = [
    "BrowserSession",
    "BrowserSessionError",
    "KaspiSource",
    "KimovilSource",
    "OlxSource",
    "OzonSource",
    "PoliteClient",
    "ProductSource",
    "RawOffer",
    "RawProduct",
    "RawReview",
    "RawReviewStats",
    "parse_retry_after",
]
