"""A repeatable benchmark for in-play forecasting harnesses.

Live collection answers "how is it doing tonight". This answers "is version two
better than version one", which needs the same questions asked twice — and a
live market never asks the same question twice.

:mod:`.replay` puts an agent back at a chosen instant of a finished match.
:mod:`.scorer` turns what it says there into a score the arena can rank on.
"""

from .replay import HORIZON_MINUTES, Timeline, replay_tools, timeline
from .scorer import WindowScore, horizon_skill, score_window

__all__ = ["HORIZON_MINUTES", "Timeline", "WindowScore", "horizon_skill",
           "replay_tools", "score_window", "timeline"]
