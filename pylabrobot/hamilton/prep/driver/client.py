"""Prep connection and immutable request binding to discovered firmware objects."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, TypeVar, Union

from pylabrobot.hamilton.prep.driver.errors import PREP_ERROR_CODES, PrepMethodNotFoundError
from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.messages import (
  CommandMessage,
  CommandResponse,
  HoiParamsParser,
)
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.tcp import HamiltonTCPClient
from pylabrobot.hamilton.transport.tcp.wire_types import HcResultEntry

from . import prep_commands as PrepCmd
from .prep_commands import _UNRESOLVED, PrepCommand

_EXPECTED_ROOT = "MLPrepRoot"
MLPREP_OBJECT_PATH = "MLPrepRoot.MLPrep"
PIPETTOR_OBJECT_PATH = "MLPrepRoot.PipettorRoot.Pipettor"
MPH_OBJECT_PATH = "MLPrepRoot.MphRoot.MPH"
MLPREP_SERVICE_OBJECT_PATH = "MLPrepRoot.MLPrepService"
DECK_CONFIGURATION_OBJECT_PATH = "MLPrepRoot.MLPrepCalibration.DeckConfiguration"
MLPREP_CPU_OBJECT_PATH = "MLPrepRoot.MLPrepCpu"
MODULE_INFORMATION_OBJECT_PATH = "MLPrepRoot.PipettorRoot.ModuleInformation"
# Everything `PrepDriver.discover` reads beyond MLPrep itself.
DISCOVERY_OBJECT_PATHS = (
  MLPREP_SERVICE_OBJECT_PATH,
  DECK_CONFIGURATION_OBJECT_PATH,
  MLPREP_CPU_OBJECT_PATH,
  MODULE_INFORMATION_OBJECT_PATH,
)
ResultT = TypeVar("ResultT")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelDriveMap:
  """Cached channel-drive topology discovered from the firmware tree.

  One entry per discovered channel for the sleeve sensor (``Squeeze.SDrive``),
  the Z drive (``ZAxis.ZDrive``), and the per-node ``NodeInformation`` object
  (used for firmware-string queries). Lists are parallel and sorted by tree
  traversal order (same order the firmware returns Channel Root instances).
  """

  sleeve_sensor_addrs: List[Address]
  zdrive_addrs: List[Address]
  node_info_addrs: List[Address]

  @property
  def num_channels_discovered(self) -> int:
    return len(self.sleeve_sensor_addrs)

  def to_dict(self) -> dict:
    """Serialize for logs / notebooks that prefer plain dicts."""
    return {
      "num_channels_discovered": self.num_channels_discovered,
      "sleeve_sensor_addrs": list(self.sleeve_sensor_addrs),
      "zdrive_addrs": list(self.zdrive_addrs),
      "node_info_addrs": list(self.node_info_addrs),
    }


@dataclass(frozen=True)
class _ResolvedPrepCommand(TCPCommand[bytes]):
  """Bind a reusable Prep request to one connection's discovered destination."""

  request: PrepCommand[object]

  def build(self, src: Address, seq: int, response_required: bool = True) -> bytes:
    """Encode the original request at its resolved destination."""
    request = self.request
    if request.interface_id is None or request.command_id is None:
      raise ValueError(f"{type(request).__name__} must define interface_id and command_id")
    return CommandMessage(
      dest=self.dest,
      interface_id=request.interface_id,
      method_id=request.command_id,
      params=request.build_parameters(),
      action_code=request.action_code,
      harp_protocol=request.harp_protocol,
      ip_protocol=request.ip_protocol,
    ).build(src, seq, harp_response_required=response_required)

  @property
  def uses_physical_channels(self) -> bool:  # type: ignore[override]
    """Preserve the device request's error attribution."""
    return self.request.uses_physical_channels

  def _channel_index_for_entry(self, entry_index: int, entry: HcResultEntry) -> Optional[int]:
    """Map result ordinals through the original channel selection."""
    return self.request._channel_index_for_entry(entry_index, entry)

  @classmethod
  def parse_response_parameters(cls, data: bytes) -> bytes:
    """Preserve the checked payload for the original request to decode."""
    return data


class PrepClient(HamiltonTCPClient):
  """Hamilton TCP client with Prep discovery and firmware-path request binding."""

  _ERROR_CODES = PREP_ERROR_CODES

  def __init__(
    self,
    host: str,
    port: int = 2000,
    read_timeout: float = 300.0,
    write_timeout: float = 30.0,
    connection_timeout: int = 600,
  ):
    super().__init__(
      host=host,
      port=port,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
      connection_timeout=connection_timeout,
    )
    self._mlprep_address: Optional[Address] = None

  async def setup(self) -> None:
    """Connect, verify the instrument identity, and resolve the MLPrep object."""
    await super().setup()
    self._mlprep_address = None
    try:
      root = await self.request_root_name()
      if root != _EXPECTED_ROOT:
        raise RuntimeError(
          f"Expected root '{_EXPECTED_ROOT}' (Prep), but discovered '{root}'. Wrong instrument?"
        )
      self._mlprep_address = await self.resolve_path(MLPREP_OBJECT_PATH)
    except BaseException:
      await self.stop()
      raise

  async def stop(self) -> None:
    """Close the connection and discard its bootstrap address."""
    try:
      await super().stop()
    finally:
      self._mlprep_address = None

  def describe_link(self) -> str:
    """How this device is reached."""
    return f"TCP {self._host}:{self._port}"

  @property
  def mlprep_address(self) -> Address:
    """Address of MLPrep in the current connection."""
    if self._mlprep_address is None:
      raise RuntimeError("MLPrep address not resolved. Call setup() first.")
    return self._mlprep_address

  async def _resolve_command(
    self, command: TCPCommand[ResultT]
  ) -> Union[TCPCommand[ResultT], _ResolvedPrepCommand]:
    """Resolve a fixed firmware path without modifying the caller's request."""
    self._session.require_active()
    if not isinstance(command, PrepCommand) or command.dest != _UNRESOLVED:
      return command
    path = command.firmware_path
    if path is None:
      raise RuntimeError(
        f"{type(command).__name__} has no firmware_path declared and no explicit dest= supplied."
      )
    try:
      address = await self.resolve_path(path)
    except KeyError as exc:
      raise RuntimeError(
        f"Cannot send {type(command).__name__}: firmware path {path!r} did not resolve ({exc})."
      ) from exc
    return _ResolvedPrepCommand(dest=address, request=command)

  async def execute(
    self, command: TCPCommand[ResultT], *, read_timeout: Optional[float] = None
  ) -> ResultT:
    """Resolve the request target, execute once, and decode its typed response."""
    session = self._session
    resolved = await self._resolve_command(command)
    if isinstance(resolved, _ResolvedPrepCommand):
      data = await session.execute(resolved, read_timeout=read_timeout)
      return command.parse_response_parameters(data)
    return await session.execute(command, read_timeout=read_timeout)

  async def exchange(
    self, command: TCPCommand[object], *, read_timeout: Optional[float] = None
  ) -> CommandResponse:
    """Resolve the target and return the full terminal frame for protocol inspection."""
    session = self._session
    return await session.exchange(await self._resolve_command(command), read_timeout=read_timeout)

  async def request_root_name(self) -> str:
    """Read the discovered firmware root's name."""
    roots = self.get_root_object_addresses()
    if not roots:
      raise RuntimeError("No root objects discovered. Call setup() first.")
    return (await self.introspection.get_object(roots[0])).name

  async def request_by_name(self, dest: Union[Address, str], name: str) -> bytes:
    """Send a status request to the method called `name`, at the ids this firmware declares for it.

    Prep firmware versions do not keep a method at the same ids, and older ones lack some methods
    altogether, so a method is found by name in the object's method table rather than by number.

    Args:
      dest: the object, by address or firmware path.
      name: the method's exact firmware name.

    Returns:
      The reply's parameters, for the caller to decode.

    Raises:
      PrepMethodNotFoundError: If the object has no method of that name.
      RuntimeError: If the object has the name on more than one interface, so the name alone does
        not say which to send.
    """
    address = dest if isinstance(dest, Address) else await self.resolve_path(dest)
    where = dest if isinstance(dest, str) else (self.registry.path(address) or str(address))
    matches = [m for m in await self.introspection.ensure_method_table(address) if m.name == name]
    if not matches:
      raise PrepMethodNotFoundError(
        f"this Prep's firmware has no {name!r} method on {where}; which methods exist, and at "
        "which ids, differs between firmware versions"
      )
    if len(matches) > 1:
      interfaces = sorted(m.interface_id for m in matches)
      raise RuntimeError(
        f"{name!r} is on more than one interface of {where} ({interfaces}), so the name does not "
        "say which to send"
      )
    method = matches[0]
    return await self.execute(
      PrepCmd.PrepProbeRequest(
        dest=address, command_id=method.method_id, interface_id=method.interface_id
      )
    )

  async def _find_children_by_name(self, parent_addr: Address, *names: str) -> dict:
    """Enumerate ``parent_addr``'s subobjects; return ``{name: Address}`` for matches.

    Bounded by ``subobject_count`` on the parent. Returns early once every
    requested name has been found. Children that raise on ``get_object`` (e.g.
    unknown firmware types) are skipped with a debug log.
    """
    intro = self.introspection
    parent = await intro.get_object(parent_addr)
    wanted = set(names)
    found: dict = {}
    for i in range(parent.subobject_count):
      try:
        sub_addr = await intro.get_subobject_address(parent_addr, i)
        sub = await intro.get_object(sub_addr)
      except Exception as e:
        logger.debug("subobject[%d] of %s failed: %s", i, parent_addr, e)
        continue
      if sub.name in wanted:
        found[sub.name] = sub_addr
        if len(found) == len(wanted):
          break
    return found

  async def request_channel_drives(self, root_name: str = "Channel Root") -> ChannelDriveMap:
    """Discover per-channel drive addresses via bounded subobject enumeration.

    MLPrepRoot exposes one ``<root_name>`` child per physical channel (siblings
    with identical names, distinguished by the ``node`` component of their
    :class:`Address`). For each one we walk:

    - ``<root>.Channel.Squeeze.SDrive``     → sleeve sensor
    - ``<root>.Channel.ZAxis.ZDrive``       → Z drive
    - ``<root>.NodeInformation``            → per-channel firmware strings

    Uses ``get_subobject_address`` / ``get_object`` along the known path shape —
    no full-tree traversal. Pass ``root_name="MPH Channel Root"`` for the 8MPH
    head. For a full firmware-tree dump use
    :meth:`PrepDriver.request_firmware_tree`.
    """
    intro = self.introspection
    try:
      mlprep_root = await self.resolve_path("MLPrepRoot")
      root_info = await intro.get_object(mlprep_root)
    except (KeyError, RuntimeError) as e:
      logger.debug("MLPrepRoot unavailable (%s); skipping channel discovery", e)
      return ChannelDriveMap(sleeve_sensor_addrs=[], zdrive_addrs=[], node_info_addrs=[])

    channel_root_addrs: List[Address] = []
    for i in range(root_info.subobject_count):
      try:
        sub_addr = await intro.get_subobject_address(mlprep_root, i)
        sub = await intro.get_object(sub_addr)
      except Exception as e:
        logger.debug("MLPrepRoot subobject[%d] failed: %s", i, e)
        continue
      if sub.name == root_name:
        channel_root_addrs.append(sub_addr)

    sleeve: List[Address] = []
    zdrive: List[Address] = []
    node_info: List[Address] = []

    for ch_root in channel_root_addrs:
      top = await self._find_children_by_name(ch_root, "Channel", "NodeInformation")
      if "NodeInformation" in top:
        node_info.append(top["NodeInformation"])

      channel_addr = top.get("Channel")
      if channel_addr is None:
        logger.warning("%s @ %s has no 'Channel' child", root_name, ch_root)
        continue

      axes = await self._find_children_by_name(channel_addr, "Squeeze", "ZAxis")
      if (sq_parent := axes.get("Squeeze")) is not None:
        sq = await self._find_children_by_name(sq_parent, "SDrive")
        if "SDrive" in sq:
          sleeve.append(sq["SDrive"])
      if (zx_parent := axes.get("ZAxis")) is not None:
        zx = await self._find_children_by_name(zx_parent, "ZDrive")
        if "ZDrive" in zx:
          zdrive.append(zx["ZDrive"])

    logger.debug("Discovered %d %s channel drive pair(s)", len(channel_root_addrs), root_name)
    return ChannelDriveMap(
      sleeve_sensor_addrs=sleeve,
      zdrive_addrs=zdrive,
      node_info_addrs=node_info,
    )

  async def _query_firmware_string(
    self, addr: Address, cmd_id: int, iface_id: int = 3
  ) -> Optional[str]:
    """Execute a status query and decode its string fragment."""
    data = await self.execute(
      PrepCmd.PrepProbeRequest(dest=addr, command_id=cmd_id, interface_id=iface_id)
    )
    for _, value in HoiParamsParser(data).parse_all():
      if isinstance(value, str):
        return value.rstrip("\x00")
    return None
