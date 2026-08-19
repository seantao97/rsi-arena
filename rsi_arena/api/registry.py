"""Name → :class:`~rsi_arena.api.spec.APISpec`.

There is one global registry so ``api_tool("searchapi", "search")`` can name an
API by string, and :class:`Registry` is instantiable so a test can have its own
without touching it.
"""

from __future__ import annotations

from typing import Any

from .spec import APISpec

# --- registry ---------------------------------------------------------------


class Registry:
    """Name → :class:`APISpec`. Global by default; instantiate for tests."""

    def __init__(self) -> None:
        self._specs: dict[str, APISpec] = {}

    def register(self, spec: APISpec, *, replace: bool = False) -> APISpec:
        if spec.name in self._specs and not replace:
            raise ValueError(f"api {spec.name!r} already registered; pass replace=True to override")
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> APISpec:
        try:
            return self._specs[name]
        except KeyError:
            known = ", ".join(sorted(self._specs)) or "none"
            raise KeyError(f"unknown api {name!r} (registered: {known})") from None

    def names(self) -> list[str]:
        return sorted(self._specs)

    def to_dict(self) -> dict[str, Any]:
        return {name: spec.to_dict() for name, spec in sorted(self._specs.items())}


registry = Registry()


def default_registry() -> Registry:
    return registry


def register_api(spec: APISpec, *, replace: bool = False) -> APISpec:
    """Add a spec to the global registry and hand it back, so this works:

    ``MY_API = register_api(APISpec(...))``
    """
    return registry.register(spec, replace=replace)


def get_api(name: str) -> APISpec:
    return registry.get(name)
