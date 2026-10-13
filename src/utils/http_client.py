"""
HTTP Client with Connection Pooling for InkyPi

Provides a shared requests.Session() instance for all plugins to use.
Benefits:
- Connection reuse (20-30% faster requests)
- Reduced TCP handshake overhead
- Automatic keep-alive handling
- Consistent headers across all requests

Usage:
    from utils.http_client import get_http_session

    session = get_http_session()
    response = session.get(url)
"""

import requests
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Global session instance (singleton)
_HTTP_SESSION: Optional[requests.Session] = None

# Statuses that mean "try again shortly": timeouts, rate limits, and server errors (529 is overloaded).
RETRY_STATUSES = {408, 429, 500, 502, 503, 504, 529}
MAX_RETRY_WAIT_SECONDS = 60


def get_http_session() -> requests.Session:
    """
    Get the shared HTTP session instance.
    Creates it on first call (lazy initialization).

    Returns:
        requests.Session: Shared session with connection pooling
    """
    global _HTTP_SESSION

    if _HTTP_SESSION is None:
        logger.debug("Initializing shared HTTP session with connection pooling")
        _HTTP_SESSION = requests.Session()

        # Set common headers for all InkyPi requests
        _HTTP_SESSION.headers.update({
            'User-Agent': 'InkyPi/1.0 (https://github.com/fatihak/InkyPi/)'
        })

        # Configure connection pool
        # Max 10 connections per host (reasonable for e-ink device)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=3,
            pool_block=False
        )
        _HTTP_SESSION.mount('http://', adapter)
        _HTTP_SESSION.mount('https://', adapter)

        logger.debug("HTTP session initialized successfully")

    return _HTTP_SESSION


def post_with_retries(url, retries=2, is_retryable=None, **kwargs):
    """POSTs with the shared session, retrying temporary failures with exponential backoff.

    Retries the statuses in RETRY_STATUSES, waiting as long as a Retry-After header asks (up to
    MAX_RETRY_WAIT_SECONDS). is_retryable(response) can rule out a response, such as a 429 for an
    exhausted quota. The session's adapter already retries failed connections.
    """
    session = get_http_session()
    for attempt in range(retries + 1):
        response = session.post(url, **kwargs)
        if (response.status_code not in RETRY_STATUSES or attempt == retries
                or (is_retryable and not is_retryable(response))):
            return response
        wait = min(_retry_after_seconds(response) or 2 ** attempt, MAX_RETRY_WAIT_SECONDS)
        logger.warning(f"{url} returned {response.status_code}, retrying in {wait:g} seconds.")
        time.sleep(wait)
    return response


def _retry_after_seconds(response):
    try:
        return float(response.headers.get("retry-after", ""))
    except ValueError:
        return None


def close_http_session():
    """
    Close the shared HTTP session.
    Should be called on application shutdown.
    """
    global _HTTP_SESSION

    if _HTTP_SESSION is not None:
        logger.debug("Closing shared HTTP session")
        _HTTP_SESSION.close()
        _HTTP_SESSION = None

