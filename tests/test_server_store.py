"""``server.store`` — the SQLite record of battles and votes.

Append-only, and deliberately not a rating system: it records what happened so
that when ratings are built they have something to compute over.
"""

from __future__ import annotations

import pytest

from server.store import Store


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(str(tmp_path / "arena.db"))


def test_a_battle_round_trips(store: Store):
    battle_id = store.open_battle("Did the ECB cut rates?", "pipeline", "freeform",
                                  "test/model", blind=True)
    row = store.battle(battle_id)
    assert row["question"] == "Did the ECB cut rates?"
    assert row["agent_a"] == "pipeline" and row["agent_b"] == "freeform"
    assert row["blind"] == 1 and row["result_json"] is None


def test_an_unknown_battle_is_none(store: Store):
    assert store.battle("nope") is None


def test_closing_a_battle_records_its_results(store: Store):
    battle_id = store.open_battle("q", "a", "b", "m", blind=False)
    store.close_battle(battle_id, {"a": {"total_usd": 0.1}, "b": {"total_usd": 0.2}})
    assert '"total_usd": 0.1' in store.battle(battle_id)["result_json"]


def test_a_vote_is_recorded_and_returns_its_id(store: Store):
    battle_id = store.open_battle("q", "a", "b", "m", blind=True)
    vote_id = store.record_vote(battle_id, "a", "better sourced")
    assert vote_id and len(vote_id) == 12


def test_the_tally_counts_wins_losses_and_ties(store: Store):
    for winner in ("a", "a", "b", "tie"):
        battle_id = store.open_battle("q", "pipeline", "freeform", "m", blind=True)
        store.record_vote(battle_id, winner)

    by_agent = {row["agent"]: row for row in store.tally()}
    assert by_agent["pipeline"] == {"agent": "pipeline", "wins": 2, "losses": 1, "ties": 1,
                                    "battles": 4}
    assert by_agent["freeform"] == {"agent": "freeform", "wins": 1, "losses": 2, "ties": 1,
                                    "battles": 4}
    assert store.tally()[0]["agent"] == "pipeline", "sorted by wins"


def test_both_bad_counts_as_a_battle_but_not_a_win(store: Store):
    battle_id = store.open_battle("q", "a", "b", "m", blind=True)
    store.record_vote(battle_id, "both_bad")
    row = store.tally()[0]
    assert row["battles"] == 1 and row["wins"] == 0 and row["ties"] == 0


def test_an_empty_store_tallies_to_nothing(store: Store):
    assert store.tally() == []


def test_several_votes_on_one_battle_all_count(store: Store):
    battle_id = store.open_battle("q", "a", "b", "m", blind=True)
    store.record_vote(battle_id, "a")
    store.record_vote(battle_id, "b")
    assert {row["agent"]: row["battles"] for row in store.tally()} == {"a": 2, "b": 2}


def test_the_schema_survives_reopening_the_same_file(tmp_path):
    path = str(tmp_path / "arena.db")
    battle_id = Store(path).open_battle("q", "a", "b", "m", blind=True)
    assert Store(path).battle(battle_id) is not None, "opening again must not wipe it"
