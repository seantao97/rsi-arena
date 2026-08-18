"""Built-in API specs.

Importing this package registers everything in it. Add a new API by dropping
a module here that calls :func:`rsi_arena.api.register_api`, then importing it
below — or, for an API defined outside this repo, just call ``register_api``
anywhere before the agent runs. Nothing here is privileged.
"""

from __future__ import annotations

from .searchapi import SEARCHAPI

__all__ = ["SEARCHAPI"]
