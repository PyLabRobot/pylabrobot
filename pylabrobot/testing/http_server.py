"""Answer a device driver's HTTP calls from a handler instead of the network.

Test support for drivers built on :class:`~pylabrobot.io.http.HTTP`: wrap the
part of a test that talks to the device, and every request reaches `handler`
rather than a socket.

    async def robot(request):
      return httpx.Response(200, json={"api_version": "8.8.0"})

    with serving(robot):
      await io.setup()
      await io.get("/health")
"""

from typing import Any, Callable, Union
from unittest import mock

import httpx

Handler = Callable[[httpx.Request], Union[httpx.Response, Any]]

# Bound before any patch: the patch target is the httpx module itself, so
# calling httpx.AsyncClient inside the factory would re-enter it.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def serving(handler: Handler):
  """Patch the HTTP io's client to answer from `handler`. Sync or async handler."""

  def make_client(**kwargs: Any) -> httpx.AsyncClient:
    return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

  return mock.patch("pylabrobot.io.http.httpx.AsyncClient", make_client)
