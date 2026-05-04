"""NimbusDriver: TCP-based transport driver for Hamilton Nimbus liquid handlers.

Transport-only: opens TCP, discovers the firmware root, and resolves one
bootstrap handle — :attr:`NimbusDriver.nimbus_core_address` (``NimbusCORE``).
Everything else uses :meth:`HamiltonTCPClient.resolve_path`, which consults the
introspection registry (cache-hot after the first hit).

**JIT command targets.** Concrete :class:`NimbusCommand` subclasses declare
``firmware_path``; :meth:`NimbusDriver._send_raw` resolves that path when
``dest`` is the unresolved sentinel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.hamilton.tcp.client import HamiltonTCPClient
from pylabrobot.hamilton.tcp.commands import TCPCommand
from pylabrobot.hamilton.tcp.error_tables import NIMBUS_ERROR_CODES
from pylabrobot.hamilton.tcp.packets import Address
from pylabrobot.resources.hamilton.nimbus_decks import NimbusDeck

from .commands import NimbusCommand, _UNRESOLVED

logger = logging.getLogger(__name__)

_EXPECTED_ROOT = "NimbusCORE"


@dataclass
class NimbusSetupParams(BackendParams):
  deck: Optional[NimbusDeck] = None
  require_door_lock: bool = False
  force_initialize: bool = False


class NimbusDriver(HamiltonTCPClient):
  """Driver for Hamilton Nimbus liquid handlers.

  Handles TCP communication and hardware root discovery. All orchestration
  (backend construction, peer creation, initialization) lives in :class:`Nimbus`.
  """

  _ERROR_CODES = NIMBUS_ERROR_CODES

  def __init__(
    self,
    host: str,
    port: int = 2000,
    read_timeout: float = 300.0,
    write_timeout: float = 30.0,
    auto_reconnect: bool = True,
    max_reconnect_attempts: int = 3,
    connection_timeout: int = 600,
  ):
    super().__init__(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      auto_reconnect=auto_reconnect,
      max_reconnect_attempts=max_reconnect_attempts,
      connection_timeout=connection_timeout,
    )
    self._nimbus_core_address: Optional[Address] = None

  @property
  def nimbus_core_address(self) -> Address:
    if self._nimbus_core_address is None:
      raise RuntimeError("Nimbus root address not discovered. Call setup() first.")
    return self._nimbus_core_address

  async def setup(self, backend_params: Optional[BackendParams] = None):
    """Open TCP connection, verify firmware root is NimbusCORE, resolve bootstrap handle."""
    if backend_params is None:
      params = NimbusSetupParams()
    elif isinstance(backend_params, NimbusSetupParams):
      params = backend_params
    else:
      raise TypeError(
        "NimbusDriver.setup expected NimbusSetupParams | None for backend_params, "
        f"got {type(backend_params).__name__}"
      )
    del params  # consumed by Nimbus / peers, not the transport

    await super().setup()

    root = await self._discovered_root_name()
    if root != _EXPECTED_ROOT:
      raise RuntimeError(
        f"Expected root '{_EXPECTED_ROOT}' (Nimbus), but discovered '{root}'. Wrong instrument?"
      )

    self._nimbus_core_address = await self.resolve_path("NimbusCORE")

  async def stop(self) -> None:
    """Close connection and clear cached addresses."""
    await super().stop()
    self._nimbus_core_address = None

  async def _discovered_root_name(self) -> str:
    roots = self.get_root_object_addresses()
    if not roots:
      raise RuntimeError("No root objects discovered. Call setup() first.")
    info = await self.introspection.get_object(roots[0])
    return info.name

  async def _send_raw(
    self,
    command: TCPCommand,
    *,
    ensure_connection: bool,
    return_raw: bool,
    raise_on_error: bool,
    read_timeout: Optional[float] = None,
  ) -> Any:
    if isinstance(command, NimbusCommand) and command.dest == _UNRESOLVED:
      path = type(command).firmware_path
      if path is None:
        raise RuntimeError(
          f"{type(command).__name__} has no firmware_path declared and no "
          "explicit dest= supplied at construction. Polymorphic-dest commands "
          "must pass dest= to send_query or send_command."
        )
      try:
        addr = await self.resolve_path(path)
      except KeyError as exc:
        raise RuntimeError(
          f"Cannot send {type(command).__name__}: firmware path {path!r} did not resolve "
          f"on this instrument ({exc})."
        ) from exc
      command.dest = addr
      command.dest_address = addr
    return await super()._send_raw(
      command,
      ensure_connection=ensure_connection,
      return_raw=return_raw,
      raise_on_error=raise_on_error,
      read_timeout=read_timeout,
    )
