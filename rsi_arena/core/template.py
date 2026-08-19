"""Templating and condition evaluation for plans.

Both exist because plans are *data*: a step's prompt and a loop's stopping
condition arrive as strings, from a JSON file or — the case this project is
actually built for — from an LLM that just rewrote a harness.

**Templating** uses ``{{name}}`` rather than ``{name}``. Single braces collide
with JSON, and prompts are full of JSON: a step that shows the model an
example object would explode on ``str.format``. Dotted paths (``{{search.
results}}``) walk dicts, objects and lists.

**Conditions** are evaluated by walking the AST and refusing anything not on a
whitelist, rather than by ``eval``. With a normal config file ``eval`` would be
a defensible shortcut. Here the optimizer writes the conditions, so the string
being evaluated is model output — and model output evaluated as Python is a
remote code execution bug with extra steps.
"""

from __future__ import annotations

import ast
import json
import operator
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")


def resolve(path: str, state: dict[str, Any]) -> Any:
    """Walk a dotted path through dicts, objects and list indices."""
    parts = path.split(".")
    value: Any = state
    for part in parts:
        if isinstance(value, dict):
            if part not in value:
                raise KeyError(path)
            value = value[part]
        elif isinstance(value, (list, tuple)) and part.isdigit():
            value = value[int(part)]
        elif hasattr(value, part):
            value = getattr(value, part)
        else:
            raise KeyError(path)
    return value


def stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def render(template: str, state: dict[str, Any], *, strict: bool = True) -> str:
    """Substitute ``{{name}}`` from ``state``.

    ``strict`` decides what an unknown name means. It defaults to raising,
    because a silently empty placeholder produces a prompt that looks fine and
    is missing its evidence — the most expensive kind of bug to notice here.
    """

    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        try:
            return stringify(resolve(path, state))
        except (KeyError, IndexError, TypeError):
            if strict:
                available = ", ".join(sorted(k for k in state if not k.startswith("_")))
                raise KeyError(
                    f"template refers to {{{{{path}}}}} which is not in state (have: {available})"
                ) from None
            return ""

    return _PLACEHOLDER.sub(replace, template)


def placeholders(template: str) -> list[str]:
    return sorted({m.group(1) for m in _PLACEHOLDER.finditer(template)})


# --- conditions -------------------------------------------------------------

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}
_CMP_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b, ast.Is: operator.is_, ast.IsNot: operator.is_not,
}
_FUNCS: dict[str, Any] = {
    "len": len, "str": str, "int": int, "float": float, "bool": bool, "abs": abs,
    "min": min, "max": max, "sum": sum, "any": any, "all": all, "sorted": sorted,
    "round": round, "lower": lambda s: str(s).lower(),
}


class ConditionError(ValueError):
    """The condition is not expressible in the allowed subset."""


def evaluate(expression: str, state: dict[str, Any]) -> Any:
    """Evaluate a restricted Python expression against ``state``.

    Allowed: literals, names from ``state``, attribute and index access,
    comparisons, boolean and arithmetic operators, and the functions in
    ``_FUNCS``. Everything else — imports, calls to arbitrary objects,
    comprehensions with side effects, dunder access — raises.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ConditionError(f"cannot parse condition {expression!r}: {exc}") from None
    return _eval(tree.body, state)


def _eval(node: ast.AST, state: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in state:
            return state[node.id]
        if node.id in _FUNCS:
            return _FUNCS[node.id]
        raise ConditionError(f"unknown name {node.id!r} in condition")
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ConditionError(f"attribute {node.attr!r} is not allowed")
        value = _eval(node.value, state)
        if isinstance(value, dict):
            return value.get(node.attr)
        return getattr(value, node.attr)
    if isinstance(node, ast.Subscript):
        return _eval(node.value, state)[_eval(node.slice, state)]
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, state) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, state)
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.USub):
            return -value
        return value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ConditionError(f"operator {type(node.op).__name__} is not allowed")
        return op(_eval(node.left, state), _eval(node.right, state))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, state)
        for operation, comparator in zip(node.ops, node.comparators):
            op = _CMP_OPS.get(type(operation))
            if op is None:
                raise ConditionError(f"comparison {type(operation).__name__} is not allowed")
            right = _eval(comparator, state)
            if not op(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ConditionError("only these functions are allowed: " + ", ".join(sorted(_FUNCS)))
        return _FUNCS[node.func.id](*[_eval(a, state) for a in node.args])
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, state) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {
            _eval(k, state): _eval(v, state)
            for k, v in zip(node.keys, node.values)
            if k is not None
        }
    raise ConditionError(f"{type(node).__name__} is not allowed in a condition")
