"""PrepClient: Hamilton TCP client for Hamilton Prep liquid handlers (Nimbus-style layout).

Transport-only: opens TCP, discovers the firmware root, and resolves one bootstrap
handle — :attr:`PrepClient.mlprep_address` (``MLPrepRoot.MLPrep``). Everything
else uses :meth:`HamiltonTCPClient.resolve_path`, which consults the introspection
registry (cache-hot after the first hit).

**JIT command targets.** Concrete :class:`~pylabrobot.hamilton.prep.prep_commands.PrepCommand`
subclasses declare ``firmware_path``; :meth:`PrepClient._send_raw` resolves
that path when ``dest`` is the unresolved sentinel. No parallel path tables on
backends.

**Bootstrap info.** :class:`~pylabrobot.hamilton.prep.info.PrepInstrumentInfo`
resolves a small set of diagnostic paths (see ``PrepInstrumentInfo._paths``)
during setup via the same ``resolve_path`` cache.

**Channel topology** (per-channel drive addresses) is discovered in
:mod:`~pylabrobot.hamilton.prep.channels` by walking the tree
from ``MLPrepRoot``, not via a separate registry.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.error_tables import PREP_ERROR_CODES
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.tcp import HamiltonTCPClient

from . import prep_commands as PrepCmd
from .prep_commands import _UNRESOLVED, PrepCommand

logger = logging.getLogger(__name__)

_EXPECTED_ROOT = "MLPrepRoot"

# Canonical firmware path strings (single source for client, chatterbox, probes).
MLPREP_OBJECT_PATH = "MLPrepRoot.MLPrep"
PIPETTOR_OBJECT_PATH = "MLPrepRoot.PipettorRoot.Pipettor"
MPH_OBJECT_PATH = "MLPrepRoot.MphRoot.MPH"


class PrepClient(HamiltonTCPClient):
  """Hamilton TCP client for Prep: connection, MLPrep bootstrap, firmware string decode.

  Instrument-wide motion, power, and deck-light entry points live on
  :class:`~pylabrobot.hamilton.prep.prep.Prep` and
  :class:`~pylabrobot.hamilton.prep.method.PrepMethodLifecycle`.
  Pipettor, calibration, and MPH traffic goes through :class:`PrepCommand` plus
  :meth:`send_command` / :meth:`resolve_path`, or through peers that build those
  commands.
  """

  _ERROR_CODES = PREP_ERROR_CODES

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
    self._mlprep_address: Optional[Address] = None

  # ---------------------------------------------------------------------------
  # Lifecycle
  # ---------------------------------------------------------------------------

  async def setup(self):
    await super().setup()

    root = await self.discovered_root_name()
    if root != _EXPECTED_ROOT:
      raise RuntimeError(
        f"Expected root '{_EXPECTED_ROOT}' (Prep), but discovered '{root}'. Wrong instrument?"
      )

    self._mlprep_address = await self.resolve_path(MLPREP_OBJECT_PATH)

  async def stop(self) -> None:
    await super().stop()
    self._mlprep_address = None

  # ---------------------------------------------------------------------------
  # MLPrep root handle (resolved in :meth:`setup`)
  # ---------------------------------------------------------------------------

  @property
  def mlprep_address(self) -> Address:
    """Address of ``MLPrepRoot.MLPrep``. Raises if :meth:`setup` has not run."""
    if self._mlprep_address is None:
      raise RuntimeError("MLPrep address not resolved. Call setup() first.")
    return self._mlprep_address

  # ---------------------------------------------------------------------------
  # JIT firmware-path resolution for PrepCommand.dest
  # ---------------------------------------------------------------------------

  async def _send_raw(
    self,
    command: TCPCommand,
    *,
    ensure_connection: bool,
    return_raw: bool,
    raise_on_error: bool,
    read_timeout: Optional[float] = None,
  ) -> Any:
    if isinstance(command, PrepCommand) and command.dest == _UNRESOLVED:
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
          f"Cannot send {type(command).__name__}: firmware path "
          f"{path!r} did not resolve on this instrument ({exc})."
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

  # ---------------------------------------------------------------------------
  # Discovery
  # ---------------------------------------------------------------------------

  async def discovered_root_name(self) -> str:
    roots = self.get_root_object_addresses()
    if not roots:
      raise RuntimeError("No root objects discovered. Call setup() first.")
    info = await self.introspection.get_object(roots[0])
    name = info.name
    if not isinstance(name, str):
      raise RuntimeError(f"Unexpected root name type: {type(name).__name__}")
    return name

  # ---------------------------------------------------------------------------
  # Firmware string queries (transport: raw HOI decode + status query)
  # ---------------------------------------------------------------------------

  @staticmethod
  def _decode_firmware_string(raw: Optional[tuple]) -> Optional[str]:
    """Decode a string from a raw HOI response (Hamilton string wire format)."""
    if raw is None:
      return None
    data: bytes = raw[0]
    i = 0
    while i < len(data) - 3:
      if data[i] == 0x0F and data[i + 1] in (0x00, 0x01):
        slen = int.from_bytes(data[i + 2 : i + 4], "little")
        if slen > 0 and i + 4 + slen <= len(data):
          return data[i + 4 : i + 4 + slen].decode("utf-8", errors="replace").rstrip("\x00")
      i += 1
    return None

  async def _query_firmware_string(
    self, addr: Address, cmd_id: int, iface_id: int = 3
  ) -> Optional[str]:
    """Send a status query and decode the string response."""
    ns: dict[str, Any] = {
      "command_id": cmd_id,
      "interface_id": iface_id,
      "__annotations__": {"dest": Address},
    }
    Cmd = type("_FWQuery", (PrepCmd.PrepStatusRequest,), ns)
    raw_resp: object = await self.send_query(Cmd(dest=addr))
    if raw_resp is None:
      return self._decode_firmware_string(None)
    if not isinstance(raw_resp, tuple):
      return None
    return self._decode_firmware_string(raw_resp)
