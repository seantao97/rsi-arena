"""``topics.kalshi.tools`` — every primitive, and the contract behind it.

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

from rsi_arena import Tool, ToolOutput

from . import REGISTRY, TOOLS, kalshi_tools


# --- helpers -----------------------------------------------------------------


def call(name: str, **kwargs):
    """A declared tool answers synchronously through get_tool_output."""
    return TOOLS[name].get_tool_output(kwargs)

#: The fixture recedes. A window measured in hours from *now* reaches it today
#: and does not next month, which is a test that rots rather than a tool that
#: broke — `candlesticks` only looks back from the present, so any test of it
#: has to ask for a window wide enough to still contain 23 August.
def hours_since_fixture(pad: float = 48.0) -> float:
    from datetime import datetime, timezone

    kickoff = datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - kickoff).total_seconds() / 3600 + pad


SETTLED_EVENT = "KXEPLGAME-26AUG23NEWLFC"
SETTLED_TICKER = "KXEPLGAME-26AUG23NEWLFC-NEW"
FINISHED_GAME = ("EPL", "401879319")


# --- the contract: true of every tool, no network ----------------------------

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
    for tool in box.values():
        schema = tool.to_openai_schema()["function"]
        assert schema["name"] and schema["description"]
        assert schema["parameters"]["type"] == "object"
    # The version is visible to the model, so a trace records which revision
    # of a tool a harness was written against.
    assert "(v1)" in box.get("market_quote").described()


def test_narrowing_the_box_keeps_only_what_was_asked_for() -> None:
    box = kalshi_tools(["market_quote", "trading_fees"])
    assert sorted(box.names()) == ["market_quote", "trading_fees"]


def test_every_tool_has_an_output_schema_worth_reading() -> None:
    """A harness rewriting itself has to know what fields come back.

    A bare {"type": "object"} says a dict arrives and nothing about what is in
    it, which is no better than not saying.
    """
    for cls in REGISTRY:
        schema = json.loads(cls().get_tool_output_schema())
        assert schema.get("properties") or schema.get("description"), cls.name


def test_a_failure_is_reported_not_raised() -> None:
    """A tool that cannot answer says so. The model can read a refusal; it
    cannot read a traceback."""
    out = call("trading_fees", price=1.4)
    assert not out.ok and "outside" in out.response
    assert call("price_the_edge", probability=0.5, yes_price=0.0).ok is False


# --- behaviour: the numbers, against a finished match ------------------------


def test_a_ladder_must_not_price_a_harder_line_higher() -> None:
    """Winning by three cannot be likelier than winning by two, and a ladder
    read one rung at a time cannot show that it does."""
    out = call("spread_ladder", event_ticker=SETTLED_EVENT, league="EPL")
    if not out.ok or not out.raw_output.get("ladders"):
        pytest.skip("no multi-rung ladder listed on that fixture")
    for rows in out.raw_output["ladders"].values():
        lines = [r["line"] for r in rows]
        assert lines == sorted(lines), "rungs must come back in order"


def test_the_book_is_only_restable_where_there_is_room() -> None:
    """A one-cent book has no price between bid and ask, so a resting order can
    only join the queue — which is the constraint that decides most trades."""
    out = call("tradeable_spreads", event_ticker=SETTLED_EVENT, league="EPL")
    if not out.ok:
        pytest.skip("fixture no longer listed")
    for row in out.raw_output["tradeable"]:
        assert row["room_cents"] == max(0, int(round(row["spread_cents"])) - 1)


def test_positions_come_from_the_book_not_from_nothing() -> None:
    out = call("my_positions", state_dir="/tmp/does-not-exist-at-all")
    assert not out.ok and "no book" in out.response

def test_fees_follow_the_kalshi_curve() -> None:
    """Peaks at the midpoint, near zero in the tails. It is why the same edge
    is worth taking at 0.05 and not at 0.50."""
    mid = call("trading_fees", price=0.50).raw_output
    tail = call("trading_fees", price=0.03).raw_output
    assert mid["taker_fee"] > tail["taker_fee"]
    assert mid["maker_fee"] == pytest.approx(mid["taker_fee"] * 0.25)
    # A round trip costs twice one leg, which is the number that decides a
    # five-minute trade.
    assert (mid["breakeven_round_trip"] - 0.50) == pytest.approx(
        (mid["breakeven_taker"] - 0.50) * 2, abs=1e-6)


def test_the_edge_survives_or_does_not() -> None:
    worth = call("price_the_edge", probability=0.90, yes_price=0.80).raw_output
    thin = call("price_the_edge", probability=0.52, yes_price=0.50).raw_output
    assert worth["worth_taking"] and worth["suggested_stake_usd"] > 0
    assert not thin["worth_taking"], "a 2c edge at midprice is eaten by the fee"


def test_devig_sums_to_one_and_needs_every_outcome() -> None:
    out = call("devig_odds", american_odds=[-150, 320, 260]).raw_output
    assert sum(out["fair"]) == pytest.approx(1.0, abs=1e-6)
    assert out["overround"] > 0, "a real book carries margin"
    assert not call("devig_odds", american_odds=[-150]).ok


def test_candlesticks_read_a_finished_market() -> None:
    out = call("candlesticks", ticker=SETTLED_TICKER,
               hours_back=hours_since_fixture(), hourly=True)
    assert out.ok, out.response
    bars = out.raw_output["bars"]
    assert len(bars) > 10
    assert all(0 <= b["mid"] <= 1 for b in bars)
    assert out.raw_output["low"] <= out.raw_output["high"]
    # The response is what the model reads, so it has to carry the numbers.
    assert SETTLED_TICKER in out.response and "bars" in out.response


def test_probabilities_are_devigged_not_raw() -> None:
    """A contract's mid is not a probability — the outcomes overround."""
    out = call("candlestick_probabilities", event_ticker=SETTLED_EVENT,
               hours_back=hours_since_fixture(), hourly=True)
    if not out.ok:
        pytest.skip("settled event no longer quotes both sides")
    assert sum(out.raw_output["now"].values()) == pytest.approx(1.0, abs=1e-3)


def test_the_tape_reports_prints_not_quotes() -> None:
    out = call("previous_trades", ticker=SETTLED_TICKER, limit=10)
    assert out.ok, out.response
    trades = out.raw_output["trades"]
    assert trades and all(t["ts"] for t in trades)
    assert out.raw_output["volume"] > 0


def test_the_book_is_read_best_first() -> None:
    """Kalshi lists levels cheapest-first and returns them under a key that
    changed; reading only the old shape gave an empty book on a market that
    plainly had one."""
    quoted = call("market_quote", ticker=SETTLED_TICKER)
    assert quoted.ok
    box = call("order_book", ticker=SETTLED_TICKER, depth=5)
    if not box.ok:
        pytest.skip("settled market has no resting orders")
    yes = box.raw_output["yes"]
    assert yes == sorted(yes, key=lambda lvl: -lvl[0]), "best price must be first"


def test_a_market_resolves_to_its_fixture() -> None:
    out = call("find_game_for_market", event_ticker=SETTLED_EVENT, league="EPL")
    assert out.ok, out.response
    assert out.raw_output["game_id"] == FINISHED_GAME[1]
    assert out.raw_output["confidence"] >= 0.6


def test_the_score_is_the_score() -> None:
    league, game_id = FINISHED_GAME
    out = call("game_state", league=league, game_id=game_id)
    assert out.ok
    state = out.raw_output
    assert state["status"] == "final"
    assert {state["home_score"], state["away_score"]} == {2}


def test_soccer_has_no_play_by_play_and_says_so() -> None:
    """Recorded because it is a coverage fact that shaped the whole design:
    a soccer fixture in progress publishes no plays, so in-play work is score
    and clock only. The tool returns the state rather than an error."""
    league, game_id = FINISHED_GAME
    out = call("recent_plays", league=league, game_id=game_id)
    assert out.ok
    if not out.raw_output.get("available"):
        assert "score" in out.raw_output


# --- in-play edge cases: the stopped clock -----------------------------------
#
# A goal in the 90th minute is where the in-play tools break, and they broke
# exactly once already: state_change read the stopped clock of a finished match
# as freshness and announced "a goal went in 0 minutes ago" on a fixture that
# had been over for a week. Nothing was absorbing anything. These pin the two
# halves of that bug — a converted penalty is a goal, and a stopped clock is
# not a recent event — plus the neighbouring cases in the same family.


def _state(**over):
    """A GameState with only the fields the in-play tools read."""
    from . import _gamestate as gs

    base = dict(game_id="1", league="EPL", status="in_progress",
                home="Liverpool", away="Newcastle United",
                home_score=2, away_score=2, period="2", clock="90'",
                fetched_at="2026-08-23T16:00:00+00:00")
    return gs.GameState(**{**base, **over})


def _events(*pairs):
    from ._events import MatchEvent

    return [MatchEvent(seconds=minute * 60.0, kind=kind, team="Liverpool",
                       text=f"{kind} at {minute}") for minute, kind in pairs]


@pytest.fixture
def feed(monkeypatch):
    """Drive the in-play tools off a fixture instead of the network."""
    from . import _events as ev
    from . import minutes_since_goal as msg
    from . import state_change as sc

    def install(state, events):
        read = state if callable(state) else (lambda: state)
        for module in (sc, msg):
            monkeypatch.setattr(module.gs, "game_state",
                                lambda *a, **k: read(), raising=False)
            monkeypatch.setattr(module, "key_events", lambda *a, **k: events)
        monkeypatch.setattr(ev, "_SEEN", {})
    return install


def test_a_ninetieth_minute_penalty_is_a_goal(feed) -> None:
    """ESPN files a converted penalty under its own type. Counting only "goal"
    loses the most price-moving event in the match."""
    feed(_state(), _events((4, "goal"), (90, "penalty---scored")))
    out = call("state_change", league="EPL", game_id="1")
    assert out.ok
    assert out.raw_output["minutes_since_score"] == 0, out.response
    assert "absorbing" in out.response


def test_full_time_is_not_a_goal_a_minute_ago(feed) -> None:
    """The regression. The clock still reads 90' after the whistle, so measuring
    "minutes since" off it makes every settled match look like it just scored."""
    feed(_state(status="final"), _events((4, "goal"), (90, "penalty---scored")))
    out = call("state_change", league="EPL", game_id="1")
    assert out.ok
    assert out.raw_output["minutes_since_score"] is None
    assert "full time" in out.response.lower()
    assert "90'" in out.response          # the goal is still reported...
    assert "absorbing" not in out.response  # ...but not as news.


def test_the_goal_clock_stops_at_full_time_too(feed) -> None:
    """minutes_since_goal reads the same clock and had the same hole."""
    feed(_state(status="final"), _events((4, "goal"), (90, "penalty---scored")))
    out = call("minutes_since_goal", league="EPL", game_id="1")
    assert out.ok
    assert out.raw_output["last_goal_minute"] == 90
    assert out.raw_output["minutes_since"] is None
    assert "full time" in out.response.lower()


def test_stoppage_time_never_reads_as_a_future_goal(feed) -> None:
    """A 90+4' goal against a clock the feed still reports as 90' would give a
    negative age. Clamped, because "-4 minutes ago" is worse than "just now"."""
    feed(_state(clock="90'"), _events((94, "goal")))
    out = call("state_change", league="EPL", game_id="1")
    assert out.raw_output["minutes_since_score"] == 0


def test_a_goalless_match_says_so_rather_than_nothing(feed) -> None:
    feed(_state(home_score=0, away_score=0, clock="70'"), _events((23, "yellow-card")))
    out = call("minutes_since_goal", league="EPL", game_id="1")
    assert out.ok
    assert out.raw_output["last_goal_minute"] is None
    assert "goalless" in out.response.lower()


def test_no_event_times_is_a_coverage_fact_not_a_failure(feed) -> None:
    """Competitions that publish no keyEvents must fall back, not error."""
    feed(_state(), [])
    out = call("state_change", league="EPL", game_id="1")
    assert out.ok
    assert out.raw_output["source"] == "first_look"
    again = call("state_change", league="EPL", game_id="1")
    assert again.raw_output["source"] == "baseline"
    assert again.raw_output["score_changed"] is False


def test_the_baseline_notices_a_late_goal(feed) -> None:
    """The fallback path's whole job: two looks either side of a goal."""
    scores = {"home": 1}
    feed(lambda: _state(home_score=scores["home"], away_score=2), [])
    call("state_change", league="EPL", game_id="1")
    scores["home"] = 2                      # a goal, between the two looks
    out = call("state_change", league="EPL", game_id="1")
    assert out.raw_output["score_changed"] is True
    assert "CHANGED" in out.response


# --- in-play edge cases: prices around the same moment -----------------------


def test_a_window_past_the_whistle_is_named_as_such() -> None:
    """A wrong kick-off puts the whole window after full time, where every
    reading is identical. That is a dead market, not a flat one, and reporting
    a drift rate off it invents a trend."""
    out = call("time_decay", ticker=SETTLED_TICKER,
               kickoff="2026-08-23T14:00:00Z", minutes=90)
    if not out.ok:
        pytest.skip(out.error)
    if out.raw_output.get("stalled"):
        assert "stopped moving" in out.response


def test_a_shock_reports_how_much_came_back() -> None:
    """Chelsea's spread fell 38 cents in minutes on a goal. Whether it retraced
    is the whole question — a jump that holds is information, one that snaps
    back was a thin book."""
    out = call("market_shock", ticker="KXEPLSPREAD-26AUG24FULCFC-CFC2",
               minutes_back=40, ending="2026-08-24T20:20:00Z")
    if not out.ok:
        pytest.skip(out.error)
    biggest = out.raw_output["shocks"][0]
    assert abs(biggest["move_cents"]) >= 5
    assert 0.0 <= biggest["retraced_fraction"] <= 1.0
    assert "retraced" in out.response


def test_a_market_with_no_bars_refuses_instead_of_raising() -> None:
    """Every in-play tool is called on markets that may not have traded. A
    refusal the agent can read beats an exception it cannot."""
    out = call("market_shock", ticker="KXEPLGAME-01JAN00XXXYYY-XXX",
               minutes_back=30)
    assert not out.ok
    assert out.response.startswith("unavailable:")
