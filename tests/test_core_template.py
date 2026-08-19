"""``rsi_arena.core.template`` — rendering, and the restricted evaluator.

The evaluator tests matter more than they look. The optimizer writes these
condition strings, so the string being evaluated is model output; anything the
whitelist misses is a remote code execution bug with extra steps.
"""

from __future__ import annotations

import pytest

from rsi_arena.core.template import ConditionError, evaluate, placeholders, render, resolve


# --- rendering --------------------------------------------------------------


def test_renders_named_values():
    assert render("Answer {{q}} now", {"q": "why"}) == "Answer why now"


def test_double_braces_leave_json_alone():
    template = 'Return {"answer": "..."} for {{q}}'
    assert render(template, {"q": "x"}) == 'Return {"answer": "..."} for x'


def test_dotted_paths_walk_dicts_objects_and_lists():
    state = {"hits": {"results": [{"title": "first"}, {"title": "second"}]}}
    assert render("{{hits.results.1.title}}", state) == "second"


def test_non_strings_render_as_json():
    out = render("{{data}}", {"data": {"b": 1, "a": 2}})
    assert '"b": 1' in out and out.startswith("{")


def test_none_renders_empty():
    assert render("[{{v}}]", {"v": None}) == "[]"


def test_unknown_placeholder_raises_and_names_what_is_available():
    with pytest.raises(KeyError) as exc:
        render("{{missing}}", {"question": "q", "hits": []})
    assert "missing" in str(exc.value) and "question" in str(exc.value)


def test_unknown_placeholder_is_empty_when_not_strict():
    assert render("[{{missing}}]", {}, strict=False) == "[]"


def test_placeholders_lists_names():
    assert placeholders("{{a}} and {{b.c}} and {{a}}") == ["a", "b.c"]


def test_resolve_raises_for_a_missing_key():
    with pytest.raises(KeyError):
        resolve("a.b", {"a": {}})


# --- conditions -------------------------------------------------------------


@pytest.mark.parametrize(
    "expression,state,expected",
    [
        ("len(hits) >= 2", {"hits": [1, 2]}, True),
        ("len(hits) >= 3", {"hits": [1, 2]}, False),
        ("sufficient", {"sufficient": True}, True),
        ("not sufficient", {"sufficient": False}, True),
        ("a and b", {"a": True, "b": False}, False),
        ("a or b", {"a": True, "b": False}, True),
        ("1 < n < 5", {"n": 3}, True),
        ("notes.sufficient", {"notes": {"sufficient": True}}, True),
        ("'x' in tags", {"tags": ["x", "y"]}, True),
        ("count * 2 == 4", {"count": 2}, True),
        ("max(scores) > 0.8", {"scores": [0.4, 0.9]}, True),
        ("lower(answer) == 'yes'", {"answer": "YES"}, True),
    ],
)
def test_allowed_expressions(expression, state, expected):
    assert evaluate(expression, state) is expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo pwned')",
        "open('/etc/passwd').read()",
        "(lambda: 1)()",
        "state.__class__",
        "[x for x in range(3)]",
        "exec('x=1')",
        "hits.__dict__",
    ],
)
def test_refuses_anything_not_whitelisted(expression):
    with pytest.raises(ConditionError):
        evaluate(expression, {"hits": [], "state": {}})


def test_unknown_name_raises_rather_than_defaulting_to_false():
    # Silently false would make a loop condition that never fires look like a
    # loop condition that is simply not met yet.
    with pytest.raises(ConditionError):
        evaluate("nope > 1", {})


def test_syntax_error_is_a_condition_error():
    with pytest.raises(ConditionError):
        evaluate("len(hits >= ", {"hits": []})
