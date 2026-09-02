"""Registering the search API, once, wherever a search tool needs it.

The spec is defined in the runtime but only registered by whoever imports it,
so a tool that assumes the registry is populated fails with "unknown api" —
which reads like a bug rather than a missing key.
"""

from __future__ import annotations

from rsi_arena.api import get_api, registry
from rsi_arena.api.apis.searchapi import SEARCHAPI


def search_api():
    """The registered spec, registering it if nobody has yet."""
    try:
        return get_api("searchapi")
    except KeyError:
        registry.register(SEARCHAPI, replace=True)
        return get_api("searchapi")
