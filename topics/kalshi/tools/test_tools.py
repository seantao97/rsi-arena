"""Checks every tool keeps the contract the agent relies on.

Two kinds here, and the split matters. The contract tests are structural — they
hold for every tool, need no network, and would have caught a badly declared
tool before an agent ever called it. The behaviour tests hit the live exchange
on a settled fixture, because a tool that returns the wrong *shape* is a bug
the schema catches and a tool that returns the wrong *numbers* is not.

The live half uses Newcastle 2-2 Liverpool, 23 August 2026. A finished match
is the only fixture that gives the same answer twice.
"""

from __future__ import annotations

import json

import pytest

from rsi_arena.agent.tool import Tool, ToolOutput

from . import REGISTRY, TOOLS, kalshi_tools

SETTLED_EVENT = "KXEPLGAME-26AUG23NEWLFC"
SETTLED_TICKER = "KXEPLGAME-26AUG23NEWLFC-NEW"
FINISHED_GAME = ("EPL", "401879319")


# ---------- contract: true of every tool, no network ----------

@pytest.mark.parametrize("cls", REGISTRY, ids=lambda c: c.name)
def test_every_tool_declares_itself(cls: type[Tool]) -> None:
    """A model picks a tool from its name and description and nothing else."""
    tool = cls()
    assert tool.name and tool.name.islower() and " " not in tool.name
    # Long enough to say what it answers and when not to reach for it; short
    # enough that twenty of them still fit in a prompt.
    assert 120 <= len(tool.description) <= 1200, len(tool.description)
    assert tool.version >= 1


@pytest.mark.parametrize("cls", REGISTRY, ids=lambda c: c.name)
def test_schemas_are_valid_json_schema_objects(cls: type[Tool]) -> None:
    tool = cls()
    for raw in (tool.get_tool_input_schema(), tool.get_tool_output_schema()):
        schema = json.loads(raw)
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties", {}), dict)
    required = json.loads(tool.get_tool_input_schema()).get("required", [])
    props = json.loads(tool.get_tool_input_schema()).get("properties", {})
    # A required argument the schema never describes cannot be supplied.
    assert set(required) <= set(props), tool.name


def test_names_are_unique() -> None:
    names = [cls.name for cls in REGISTRY]
    assert len(names) == len(set(names))
    assert set(names) == set(TOOLS)


def test_the_registry_becomes_a_toolbox() -> None:
    """The bridge to the runtime, which is what makes these usable at all."""
    box = kalshi_tools()
    assert len(box) == len(REGISTRY)
    for tool in box:
        schema = tool.to_openai_schema()["function"]
        assert schema["name"] and schema["description"]
        assert schema["parameters"]["type"] == "object"
    # The version is visible to the model, so a trace records which revision
    # of a tool a harness was written against.
    assert "(v1)" in box.get("market_quote").description


def test_narrowing_the_box_keeps_only_what_was_asked_for() -> None:
    box = kalshi_tools(["market_quote", "trading_fees"])
    assert sorted(box.names()) == ["market_quote", "trading_fees"]


def test_a_failure_is_reported_not_raised() -> None:
    """A tool that cannot answer says so. The model can read a refusal; it
    cannot read a traceback."""
    out = TOOLS["trading_fees"](price=1.4)
    assert not out.ok and "outside" in out.response
    assert TOOLS["price_the_edge"](probability=0.5, yes_price=0.0).ok is False


# ---------- behaviour: the numbers, against a finished match ----------

def test_fees_follow_the_kalshi_curve() -> None:
    """Peaks at the midpoint, near zero in the tails. It is why the same edge
    is worth taking at 0.05 and not at 0.50."""
    mid = TOOLS["trading_fees"](price=0.50).raw_output
    tail = TOOLS["trading_fees"](price=0.03).raw_output
    assert mid["taker_fee"] > tail["taker_fee"]
    assert mid["maker_fee"] == pytest.approx(mid["taker_fee"] * 0.25)
    # A round trip costs twice one leg, which is the number that decides a
    # five-minute trade.
    assert (mid["breakeven_round_trip"] - 0.50) == pytest.approx(
        (mid["breakeven_taker"] - 0.50) * 2, abs=1e-6)


def test_the_edge_survives_or_does_not() -> None:
    worth = TOOLS["price_the_edge"](probability=0.90, yes_price=0.80).raw_output
    thin = TOOLS["price_the_edge"](probability=0.52, yes_price=0.50).raw_output
    assert worth["worth_taking"] and worth["suggested_stake_usd"] > 0
    assert not thin["worth_taking"], "a 2c edge at midprice is eaten by the fee"


def test_devig_sums_to_one_and_needs_every_outcome() -> None:
    out = TOOLS["devig_odds"](american_odds=[-150, 320, 260]).raw_output
    assert sum(out["fair"]) == pytest.approx(1.0, abs=1e-6)
    assert out["overround"] > 0, "a real book carries margin"
    assert not TOOLS["devig_odds"](american_odds=[-150]).ok


def test_candlesticks_read_a_finished_market() -> None:
    out = TOOLS["candlesticks"](ticker=SETTLED_TICKER, hours_back=300, hourly=True)
    assert out.ok, out.response
    bars = out.raw_output["bars"]
    assert len(bars) > 10
    assert all(0 <= b["mid"] <= 1 for b in bars)
    assert out.raw_output["low"] <= out.raw_output["high"]
    # The response is what the model reads, so it has to carry the numbers.
    assert SETTLED_TICKER in out.response and "bars" in out.response


def test_probabilities_are_devigged_not_raw() -> None:
    """A contract's mid is not a probability — the outcomes overround."""
    out = TOOLS["candlestick_probabilities"](event_ticker=SETTLED_EVENT,
                                             hours_back=300, hourly=True)
    if not out.ok:
        pytest.skip("settled event no longer quotes both sides")
    assert sum(out.raw_output["now"].values()) == pytest.approx(1.0, abs=1e-3)


def test_the_tape_reports_prints_not_quotes() -> None:
    out = TOOLS["previous_trades"](ticker=SETTLED_TICKER, limit=10)
    assert out.ok, out.response
    trades = out.raw_output["trades"]
    assert trades and all(t["ts"] for t in trades)
    assert out.raw_output["volume"] > 0


def test_the_book_is_read_best_first() -> None:
    """Kalshi lists levels cheapest-first and returns them under a key that
    changed; reading only the old shape gave an empty book on a market that
    plainly had one."""
    quoted = TOOLS["market_quote"](ticker=SETTLED_TICKER)
    assert quoted.ok
    box = TOOLS["order_book"](ticker=SETTLED_TICKER, depth=5)
    if not box.ok:
        pytest.skip("settled market has no resting orders")
    yes = box.raw_output["yes"]
    assert yes == sorted(yes, key=lambda lvl: -lvl[0]), "best price must be first"


def test_a_market_resolves_to_its_fixture() -> None:
    out = TOOLS["find_game_for_market"](event_ticker=SETTLED_EVENT, league="EPL")
    assert out.ok, out.response
    assert out.raw_output["game_id"] == FINISHED_GAME[1]
    assert out.raw_output["confidence"] >= 0.6


def test_the_score_is_the_score() -> None:
    league, game_id = FINISHED_GAME
    out = TOOLS["game_state"](league=league, game_id=game_id)
    assert out.ok
    state = out.raw_output
    assert state["status"] == "final"
    assert {state["home_score"], state["away_score"]} == {2}


def test_soccer_has_no_play_by_play_and_says_so() -> None:
    """Recorded because it is a coverage fact that shaped the whole design:
    a soccer fixture in progress publishes no plays, so in-play work is score
    and clock only. The tool returns the state rather than an error."""
    league, game_id = FINISHED_GAME
    out = TOOLS["recent_plays"](league=league, game_id=game_id)
    assert out.ok
    if not out.raw_output.get("available"):
        assert "score" in out.raw_output
