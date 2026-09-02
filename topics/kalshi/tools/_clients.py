"""The shared handles every tool reads through.

One client, one discovery, one history — built once and reused. Discovery
caches its series sweep, so a second tool asking about the same league in the
same run pays nothing; giving each tool its own would throw that away and
re-sweep hundreds of markets per call.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..client import KalshiClient
from ..coherence import Coherence
from ..discovery import Discovery
from ..history import History
from ..quotes import Quotes
from ..timeline import Timeline

CLIENT = KalshiClient()
DISCOVERY = Discovery(CLIENT)
QUOTES = Quotes(CLIENT)
HISTORY = History(CLIENT)
COHERENCE = Coherence(CLIENT)
TIMELINE = Timeline(CLIENT)


def hours_ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def now() -> datetime:
    return datetime.now(timezone.utc)
