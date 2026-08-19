"""How a spec proves who it is.

Four shapes cover essentially every vendor: none, a bearer token, a custom
header, or a query parameter. Each reads its key from the environment at call
time rather than at import time, so a spec can be declared in a module that
loads before the key is exported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .errors import MissingCredential

# --- auth -------------------------------------------------------------------


@dataclass(frozen=True)
class Auth:
    """Base auth. Subclasses mutate the outgoing headers/params in place."""

    env_var: str | None = None
    required: bool = True

    def key(self, api: str) -> str:
        if not self.env_var:
            return ""
        value = os.environ.get(self.env_var, "")
        if not value and self.required:
            raise MissingCredential(f"{api} needs {self.env_var} in the environment")
        return value

    def apply(self, api: str, headers: dict[str, str], params: dict[str, Any]) -> None:
        return None


@dataclass(frozen=True)
class NoAuth(Auth):
    required: bool = False


@dataclass(frozen=True)
class BearerAuth(Auth):
    def apply(self, api: str, headers: dict[str, str], params: dict[str, Any]) -> None:
        headers["Authorization"] = f"Bearer {self.key(api)}"


@dataclass(frozen=True)
class HeaderAuth(Auth):
    header: str = "X-API-Key"
    prefix: str = ""

    def apply(self, api: str, headers: dict[str, str], params: dict[str, Any]) -> None:
        headers[self.header] = f"{self.prefix}{self.key(api)}"


@dataclass(frozen=True)
class QueryAuth(Auth):
    param: str = "api_key"

    def apply(self, api: str, headers: dict[str, str], params: dict[str, Any]) -> None:
        params[self.param] = self.key(api)
