"""Talking to everything that is not a model.

Defining a new API is meant to be one declaration and nothing else:

.. code-block:: python

    NWS = register_api(APISpec(
        name="nws",
        base_url="https://api.weather.gov",
        auth=NoAuth(),
        rate_limit=RateLimit(per_second=5),
        endpoints=[
            Endpoint("forecast", "/gridpoints/{office}/{grid_x},{grid_y}/forecast",
                     params=(Param("office", required=True),)),
        ],
    ))

After that, ``await api.call("nws", "forecast", office="OKX", ...)`` works, it
is retried, rate limited, cached and costed like everything else, and
``api_tool(NWS, "forecast")`` hands the same endpoint to a model as a callable
tool with a generated JSON Schema.

The split is deliberate: :class:`APISpec` is *declarative data* — no HTTP, no
state, serialisable, safe to define in a user's own module — while
:class:`APIClient` owns the connection pool, the limiters and the cache. That
is what makes third-party API definitions cheap: they are data, not plumbing.

========================  ======================================================
:mod:`~rsi_arena.api.spec`      :class:`Param`, :class:`Endpoint`, :class:`APISpec`
:mod:`~rsi_arena.api.auth`      none / bearer / header / query credentials
:mod:`~rsi_arena.api.registry`  name → spec, global and instantiable
:mod:`~rsi_arena.api.client`    :class:`APIClient`, :class:`APIResponse`
:mod:`~rsi_arena.api.apis`      the specs that ship with the runtime
========================  ======================================================
"""

from __future__ import annotations

from .auth import Auth, BearerAuth, HeaderAuth, NoAuth, QueryAuth
from .client import APIClient, APIResponse
from .errors import APIError, MissingCredential
from .registry import Registry, default_registry, get_api, register_api, registry
from .spec import APISpec, Endpoint, Param

__all__ = [
    "APIClient", "APIResponse", "APIError", "MissingCredential",
    "APISpec", "Endpoint", "Param",
    "Auth", "NoAuth", "BearerAuth", "HeaderAuth", "QueryAuth",
    "Registry", "registry", "default_registry", "register_api", "get_api",
]
