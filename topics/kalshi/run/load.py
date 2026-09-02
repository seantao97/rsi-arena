"""Build an agent from its JSON config.

``agents/`` holds configs and nothing else: name, context, model settings, the
tool names the plan may call, and the plan itself. Everything that is Python —
the loop that keeps agents forecasting, the arithmetic that turns a forecast
into an action — lives here in ``run/``.

The split is what makes a harness comparable. A config is data the arena can
diff, version and mutate; if the same file also held the code, "the harness
changed" would stop meaning anything specific.

Binding is by tool *name*, which is the useful part: the same config loads
against the live exchange or against ``eval.replay``'s frozen tools, because
both boxes answer to ``market_quote``, ``candlesticks`` and ``previous_trades``.
That is how a benchmark replays a harness without the harness knowing.
"""

from __future__ import annotations

import json
from pathlib import Path

from rsi_arena import Agent, AgentConfig, Toolbox

from ..tools import kalshi_tools

AGENTS = Path(__file__).resolve().parent.parent / "agents"


def available() -> list[str]:
    """Every config name, which is the file name without ``.json``."""
    return sorted(p.stem for p in AGENTS.glob("*.json"))


def short_names() -> list[str]:
    """The labels the CLI takes — ``inplay`` rather than ``kalshi-sports-inplay``.

    A config is named for the agent inside it, which is right in a file name and
    tedious on a command line.
    """
    out = []
    for full in available():
        label = full
        for prefix in ("kalshi-", "sports-"):
            if label.startswith(prefix):
                label = label[len(prefix):]
        out.append(label)
    return out


def resolve(name: str) -> str:
    """Full config name from a short one — ``inplay`` finds ``kalshi-sports-inplay``.

    The CLI has always taken the short label, and a config file is named for the
    agent inside it. Rather than keep a second mapping in sync, a short name is
    matched as a substring and has to be unambiguous.
    """
    names = available()
    if name in names:
        return name
    hits = [n for n in names if name in n]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise FileNotFoundError(
            f"no agent config {name!r}; have {', '.join(names)}")
    raise ValueError(f"{name!r} matches several configs: {', '.join(hits)}")


def load_spec(name: str) -> dict:
    return json.loads((AGENTS / f"{resolve(name)}.json").read_text())


def default_config(max_usd: float = 2.00) -> AgentConfig:
    """The arena's shared ceiling. Every agent runs under the same one."""
    return AgentConfig(default_model="anthropic/claude-sonnet-4.5", max_usd=max_usd)


def load_agent(name: str, *, tools: Toolbox | None = None,
               config: AgentConfig | None = None) -> Agent:
    """Load ``agents/<name>.json``.

    ``tools`` overrides which box the names bind against — pass
    ``eval.replay.replay_tools(...)`` to run the same config at a past instant.
    ``config`` overrides the model and budget without editing the file, which is
    what a sweep over models needs.
    """
    agent = Agent.from_dict(load_spec(name), tools=tools or kalshi_tools())
    if config is not None:
        agent.config = config
    return agent
