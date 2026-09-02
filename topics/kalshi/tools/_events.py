"""Timestamped match events, where a feed publishes them mid-match.

Soccer publishes no play-by-play while a match runs — that shaped the whole
in-play design. It does publish ``keyEvents``: goals and cards with a clock
value on each. Whether they appear *during* a match or only afterwards varies
by competition, so everything here treats an empty list as a fact about
coverage and says so, rather than failing.

The fallback is a baseline held in this process: the first look records what it
saw, and a later one reports the difference. That answers a narrower question —
"what changed since I last looked" rather than "when did it happen" — and the
tools say which of the two they are answering.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .. import gamestate as gs
from ..taxonomy import resolve_league

#: Every event type that puts a goal on the board. A converted penalty is filed
#: as its own type, and counting only "goal" loses it.
SCORING = frozenset({"goal", "penalty---scored", "own-goal", "penalty-goal",
                     "goal-penalty"})

#: Things that change a price without changing the score.
NOTABLE = frozenset({"red-card", "yellow-red-card", "penalty---missed",
                     "penalty---saved", "substitution"})


@dataclass(frozen=True)
class MatchEvent:
    seconds: float
    kind: str
    team: str
    text: str

    @property
    def minute(self) -> int:
        return int(self.seconds // 60)

    @property
    def scored(self) -> bool:
        return self.kind in SCORING


def key_events(league: str, game_id: str) -> list[MatchEvent]:
    """Timestamped events, newest last. Empty when the feed publishes none."""
    path = gs.ESPN_PATHS.get(resolve_league(league) or league.upper())
    if not path:
        return []
    sport, competition = path
    try:
        data = gs._get(f"{gs.ESPN_API}/{sport}/{competition}"
                       f"/summary?event={game_id}", throttle=True)
    except Exception:
        return []
    out = []
    for raw in data.get("keyEvents") or []:
        clock = (raw.get("clock") or {}).get("value")
        kind = ((raw.get("type") or {}).get("type") or "").lower()
        if clock is None or not kind:
            continue
        out.append(MatchEvent(seconds=float(clock), kind=kind,
                              team=((raw.get("team") or {}).get("displayName") or ""),
                              text=(raw.get("text") or "")))
    return sorted(out, key=lambda e: e.seconds)


# --- the fallback: what this process saw last -------------------------------

_LOCK = threading.Lock()
_SEEN: dict[str, dict[str, Any]] = {}


def remember(key: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Store a look and hand back the one before it.

    Keyed per caller, guarded because tools run concurrently across leagues.
    """
    stamped = {**snapshot, "at": datetime.now(timezone.utc).isoformat()}
    with _LOCK:
        previous = _SEEN.get(key)
        _SEEN[key] = stamped
    return previous


def seconds_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds()
