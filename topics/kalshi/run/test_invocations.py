"""``topics.kalshi.run`` — that every documented command still exists.

Renaming a package moves the code and leaves the strings behind. When
``agents/`` became configs, eleven ``python -m topics.kalshi.agents...`` lines
stayed pointing at a package that no longer existed — including the two in
``workflow.yml``, which is the file the scheduled run actually executes. Nothing
failed until a run fired, and a scheduled run that dies in three seconds tells
nobody.

So the invocations are checked against the import system rather than by reading.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SOURCES = [HERE / "README.md", HERE / "workflow.yml", HERE / "__main__.py",
           HERE / "supervisor.py", HERE / "slate.py",
           HERE.parent / "README.md", HERE.parent / "agents" / "README.md",
           HERE.parent / "tools" / "README.md"]

#: ``python -m some.module``, however it is wrapped — a timeout, a backslash
#: continuation, a code fence.
INVOCATION = re.compile(r"python -m ([\w.]+)")


def documented_modules() -> list[tuple[str, str]]:
    found = []
    for path in SOURCES:
        if not path.exists():
            continue
        for module in INVOCATION.findall(path.read_text()):
            found.append((path.name, module))
    return sorted(set(found))


def test_there_are_invocations_to_check() -> None:
    """A regex that silently matches nothing would pass every test below."""
    assert len(documented_modules()) >= 4


@pytest.mark.parametrize("source,module", documented_modules(),
                         ids=lambda v: v.replace(".", "-"))
def test_a_documented_command_names_a_real_module(source: str, module: str) -> None:
    assert importlib.util.find_spec(module) is not None, (
        f"{source} runs `python -m {module}`, which does not import")
