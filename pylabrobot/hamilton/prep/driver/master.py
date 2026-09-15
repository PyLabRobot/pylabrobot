"""Prep driver: orchestrates transport, the device's configuration, and peer construction."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, TypeVar, Union

from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.error_tables import HC_RESULT_PROTOCOL
from pylabrobot.hamilton.transport.tcp.hoi_error import parse_hamilton_error_entries
from pylabrobot.hamilton.transport.tcp.introspection import FirmwareTreeNode, MethodInfo
from pylabrobot.hamilton.transport.tcp.messages import (
  CommandMessage,
  CommandResponse,
  HoiParamsParser,
)
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.hamilton.transport.tcp.protocol import Hoi2Action
from pylabrobot.hamilton.transport.tcp.session import TCPSession
from pylabrobot.hamilton.transport.tcp.tcp import HamiltonTCPClient
from pylabrobot.hamilton.transport.tcp.wire_types import HamiltonDataType, HcResultEntry
from pylabrobot.io.socket import Socket
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.hamilton.core_grippers import HamiltonCoreGrippers
from pylabrobot.resources.hamilton.prep_decks import PrepDeck
from pylabrobot.resources.resource import Resource

from . import prep_commands as PrepCmd
from .configuration import DeviceConfiguration, read_configuration, to_jsonable
from .errors import PREP_ERROR_CODES, PrepMethodNotFoundError
from .features.calibration import Calibration
from .features.core_grippers import CoreGripperArm, CoreGrippers
from .features.head8 import Head8
from .features.method import MethodLifecycle
from .features.pipettes import Pipettes
from .features.x_arm import XArm
from .prep_commands import (
  _UNRESOLVED,
  DECK_CONFIGURATION_OBJECT_PATH,
  MLPREP_CPU_OBJECT_PATH,
  MLPREP_OBJECT_PATH,
  MLPREP_SERVICE_OBJECT_PATH,
  MODULE_INFORMATION_OBJECT_PATH,
  PREP_ROOT_NAME,
  PrepCommand,
)

logger = logging.getLogger(__name__)

# What a declaration and a device have to agree on for the one to stand for the other: what is
# fitted. Identity and set-up are the device's own.
_DECLARATION_MUST_MATCH = ("num_channels", "head8_installed", "has_enclosure")

# The PrepDeck waste position for each channel a waste site names.
_WASTE_SITE_NAMES = {
  int(PrepCmd.ChannelIndex.FrontChannel): "waste_front",
  int(PrepCmd.ChannelIndex.RearChannel): "waste_rear",
  int(PrepCmd.ChannelIndex.MPHChannel): "waste_mph",
}


ResultT = TypeVar("ResultT")


def _range(values: Optional[Tuple[float, float]]) -> str:
  """A `(low, high)` range in mm, or a note that it was not resolved."""
  return "unresolved" if values is None else f"{values[0]:.2f} to {values[1]:.2f} mm"


def _fragment_values(data: bytes) -> List[Any]:
  """Each value in an HOI payload, with structures opened into their own values."""
  values: List[Any] = []
  for type_id, value in HoiParamsParser(data).parse_all():
    if type_id == HamiltonDataType.STRUCTURE:
      value = _fragment_values(value)
    elif type_id == HamiltonDataType.STRUCTURE_ARRAY:
      value = [_fragment_values(v) for v in value]
    values.append(value)
  return values


class _PrepTCPClient(HamiltonTCPClient):
  """The TCP link to a Prep, describing firmware errors from the Prep's own error table."""

  _ERROR_CODES = PREP_ERROR_CODES

  def _create_session(self) -> TCPSession:
    return _PrepTCPSession(
      Socket(
        human_readable_device_name="Hamilton Prep",
        host=self._host,
        port=self._port,
        read_timeout=self._read_timeout,
        write_timeout=self._write_timeout,
      ),
      read_timeout=self._read_timeout,
      error_codes=self._ERROR_CODES,
    )


@dataclass(frozen=True)
class ChannelDriveMap:
  """Cached channel-drive topology discovered from the firmware tree.

  One entry per discovered channel for the sleeve sensor (``Squeeze.SDrive``),
  the Z drive (``ZAxis.ZDrive``), and the per-node ``NodeInformation`` object
  (used for firmware-string queries). Lists are parallel and sorted by tree
  traversal order (same order the firmware returns Channel Root instances).
  The Y axis (``YAxis``) and its drive (``YAxis.YDrive``) are listed for the
  channels that have one; the 8-channel head's channels do not. So are the
  channel's ``Calibration`` object (which starts and stops continuous cLLD
  detection), its ``CLld`` object (which reports it), and its Z axis (``ZAxis``).
  """

  sleeve_sensor_addrs: List[Address]
  zdrive_addrs: List[Address]
  node_info_addrs: List[Address]
  yaxis_addrs: List[Address] = field(default_factory=list)
  ydrive_addrs: List[Address] = field(default_factory=list)
  calibration_addrs: List[Address] = field(default_factory=list)
  clld_addrs: List[Address] = field(default_factory=list)
  zaxis_addrs: List[Address] = field(default_factory=list)

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
      "yaxis_addrs": list(self.yaxis_addrs),
      "ydrive_addrs": list(self.ydrive_addrs),
      "calibration_addrs": list(self.calibration_addrs),
      "clld_addrs": list(self.clld_addrs),
      "zaxis_addrs": list(self.zaxis_addrs),
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


class _PrepTCPSession(TCPSession):
  """A session that logs each request and what the device answered, as the simulator's session does.

  At DEBUG level, above the socket's raw bytes at IO level: the request as it was sent and the object it went to, then the
  decoded answer, or the firmware's error with its description.
  """

  async def exchange(
    self, command: TCPCommand[object], *, read_timeout: Optional[float] = None
  ) -> CommandResponse:
    request = command.request if isinstance(command, _ResolvedPrepCommand) else command
    link = f"[{getattr(self._io, '_host', '?')}:{getattr(self._io, '_port', '?')}]"
    if logger.isEnabledFor(logging.DEBUG):
      logger.debug("%s write: %s to %s", link, request, command.dest)
    response = await super().exchange(command, read_timeout=read_timeout)
    if logger.isEnabledFor(logging.DEBUG):
      logger.debug("%s read: %s", link, self._describe(command, request, response))
    return response

  def _describe(
    self, command: TCPCommand[object], request: TCPCommand[object], response: CommandResponse
  ) -> str:
    """The answer decoded as the request decodes it, or the firmware's error; hex when neither decodes."""
    params = response.hoi.params
    if response.hoi.action_code in (
      Hoi2Action.STATUS_EXCEPTION,
      Hoi2Action.COMMAND_EXCEPTION,
      Hoi2Action.INVALID_ACTION_RESPONSE,
    ):
      described = []
      for e in parse_hamilton_error_entries(params):
        key = (e.module_id, e.node_id, e.object_id, e.interface_id, e.result)
        fallback = (e.module_id, e.node_id, e.object_id, e.action_id, e.result)
        text = (
          self._error_codes.get(key)
          or self._error_codes.get(fallback)
          or HC_RESULT_PROTOCOL.get(e.result)
        )
        described.append(f"0x{e.result:04X}" + (f" {text}" if text else ""))
      return "error: " + ("; ".join(described) or params.hex())
    try:
      payload, _ = command._strip_warning_prefix(response)
      decoded = request.parse_response_parameters(payload)
      # A request that does not decode its answer (a probe by ids) hands back the payload: show its values.
      return repr(_fragment_values(decoded) if isinstance(decoded, bytes) else decoded)
    except Exception:
      return params.hex()


class PrepDriver:
  """Hamilton Prep liquid handler.

  Setup constructs peers (``channels``, ``head8``, ``method``, ``calibration``,
  gripper factory) directly. Firmware paths live on each :class:`PrepCommand`
  subclass and are resolved JIT by :meth:`PrepDriver.send_command`.
  """

  def __init__(
    self,
    deck: Deck,
    host: Optional[str] = None,
    port: int = 2000,
    declared_configuration_json: Optional[str] = None,
    io: Optional[HamiltonTCPClient] = None,
  ):
    """
    Args:
      deck: the deck positions are measured from.
      host: the address the Prep answers on. Required unless `io` is given.
      port: the port it answers on.
      declared_configuration_json: path to a JSON file holding a declared configuration, as
        `save_configuration` writes one. The only way a configuration is read from a file. Against a
        physical device, discovery cross-checks it against what the device answers; against a
        simulated one, the device answers as it says.
      io: the link to drive the device through, instead of a TCP connection to `host`.

    Raises:
      ValueError: If neither `host` nor `io` is given.
    """
    # What was declared this device is, read once here. Empty when nothing was declared.
    self.declared_configuration_json = declared_configuration_json
    self.declared: Dict[str, Any] = (
      {} if declared_configuration_json is None else read_configuration(declared_configuration_json)
    )
    if io is None:
      if not host:
        raise ValueError("host must be provided to reach a Prep over TCP")
      io = _PrepTCPClient(host=host, port=port)
    self.io: HamiltonTCPClient = io
    self._mlprep_address: Optional[Address] = None
    self.deck = deck
    # What the device reports about itself, read by `discover`. None until setup has run.
    self.configuration: Optional[DeviceConfiguration] = None
    self._core_gripper_arm: Optional[CoreGripperArm] = None
    self.pipettes: Optional[Pipettes] = None
    self.head8: Optional[Head8] = None
    self.core_grippers: Optional[CoreGrippers] = None
    self.method: Optional[MethodLifecycle] = None
    self.calibration: Optional[Calibration] = None
    # The gantry the channels ride. Built at setup, and kept across setups like the configuration.
    self.x_arm: Optional[XArm] = None
    self._setup_finished: bool = False

  # ----------------------------------------
  # Connection and lifecycle
  # ----------------------------------------

  async def setup(
    self,
    *,
    smart: bool = True,
    force_initialize: bool = False,
    default_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
  ):
    """Connect, discover the device, initialize MLPrep, construct peers."""
    logger.debug("Setting up Prep on %s ...", self.describe_link())
    try:
      await self._open()

      # 1. What is on the other end, and what does it carry?
      logger.debug("[PHASE 1] Discovery")
      await self.discover()

      # 2. Bring the device to a known state.
      logger.debug("[PHASE 2] Device initialization")
      await self._initialize_instrument(smart=smart, force_initialize=force_initialize)

      # 3. Each feature brings itself up.
      logger.debug("[PHASE 3] Feature initialization")

      # Built only if not already there: a caller can hand a feature its configuration before setup,
      # and re-running setup keeps it.
      if self.method is None:
        self.method = MethodLifecycle(self)
      if self.calibration is None:
        self.calibration = Calibration(self)
      if self.pipettes is None:
        self.pipettes = Pipettes(self)
      if default_traverse_height is not None:
        self.pipettes.default_minimum_traverse_height = default_traverse_height
      if use_v1_aspirate_dispense:
        self.pipettes.configuration.use_v1_aspirate_dispense = True
      await self.pipettes._on_setup()

      if self.pipettes.head8_installed:
        if self.head8 is None:
          self.head8 = Head8(
            self,
            default_traverse_height=default_traverse_height,
            use_v1_aspirate_dispense=use_v1_aspirate_dispense,
          )
        await self.head8._on_setup()

      if self.core_grippers is None:
        self.core_grippers = CoreGrippers(self)
      if self.x_arm is None:
        self.x_arm = XArm(self)
      # What was found, as resources on the deck - when the driver was given a Prep deck to reflect into.
      if self.deck is not None:
        logger.debug("[PHASE 4] Feature resources")
        self._place_reported_sites()
        await self._create_capability_resources()
      self._setup_finished = True
    except Exception:
      await self._close()
      raise

    logger.info("%s", self.format_setup_summary())

  async def _open(self) -> None:
    """Open the link and check that a Prep answers on it.

    Raises:
      RuntimeError: If the device's firmware root is not a Prep's.
    """
    await self.io.setup()
    self._mlprep_address = None
    try:
      root = await self.request_root_name()
      if root != PREP_ROOT_NAME:
        raise RuntimeError(
          f"Expected root '{PREP_ROOT_NAME}' (Prep), but discovered '{root}'. Wrong instrument?"
        )
      self._mlprep_address = await self.resolve_path(MLPREP_OBJECT_PATH)
    except BaseException:
      await self._close()
      raise

  async def _close(self) -> None:
    """Close the link and discard what was resolved on it."""
    try:
      await self.io.stop()
    finally:
      self._mlprep_address = None

  async def features_below_safe_z(self, tolerance: float = 0.5) -> List[str]:
    """Which channels report below where they are safe.

    Read back rather than taken on trust: a retract that answered without arriving leaves the device looking
    safe while a lateral move would drive whatever is still low into whatever is in the way. Each channel's stop
    disc is held to the traverse height the device reports, so a mounted tip does not count as low. The 8-channel
    head is not judged: no read of its height is known.

    Args:
      tolerance: how far below the traverse height still counts as up, in mm.

    Returns:
      One entry per channel that is low, naming it and where it says it is. Empty when everything is up, or when
      the device reported no traverse height to hold the channels to.
    """
    low: List[str] = []
    pipettes = self.pipettes
    safe = None if self.configuration is None else self.configuration.default_traverse_height
    if pipettes is None or safe is None:
      return low
    for channel in range(pipettes.num_channels):
      try:
        z = await pipettes.request_stop_disc_z_position(channel)
      except Exception:
        low.append(f"channel {channel} (where it is could not be read)")
      else:
        if z < safe - tolerance:
          low.append(f"channel {channel} at {z:.1f} mm, safe is {safe:.1f} mm")
    return low

  async def stop(self):
    """Close the link, leaving the device safe to move laterally.

    The device keeps its state; only this driver lets go of it. Every pipetting channel is moved up to Z safety
    first, and where the channels stopped is read back: a driver that let go with a channel low would leave the
    next lateral move to crash it. The 8-channel head is not raised: no move of its Z alone is known.

    The link closes whether or not that succeeds. Repeatable: a driver that is not set up is left alone.
    """
    if not self._setup_finished:
      return
    try:
      if self._core_gripper_arm is not None:
        logger.warning(
          "PrepDriver.stop() called with CoRe grippers still mounted. stop() raises the channels to Z "
          "safety but does not return the tools. Call `await prep.return_core_grippers()` first if you "
          "want them returned."
        )
        self._core_gripper_arm = None
      if self.pipettes is not None:
        try:
          await self.pipettes.move_to_safe_z()
        except Exception:
          logger.warning("could not move the channels to Z safety", exc_info=True)
        # Asked, not assumed: a move that answers has not said where it stopped.
        low = await self.features_below_safe_z()
        if low:
          logger.warning("not everything is at Z safety: %s", "; ".join(low))
    except Exception:
      logger.warning(
        "could not bring the device to a safe state; closing the link anyway", exc_info=True
      )
    finally:
      if self.pipettes is not None:
        await self.pipettes._on_stop()
      if self.head8 is not None:
        await self.head8._on_stop()
      await self._close()
      self._setup_finished = False

  # ----------------------------------------
  # Low-level I/O
  # ----------------------------------------

  def describe_link(self) -> str:
    """How this device is reached."""
    return f"TCP {self.io._host}:{self.io._port}"

  @property
  def mlprep_address(self) -> Address:
    """Address of MLPrep in the current connection."""
    if self._mlprep_address is None:
      raise RuntimeError("MLPrep address not resolved. Call setup() first.")
    return self._mlprep_address

  async def resolve_path(self, path: str) -> Address:
    """The address of the firmware object at `path` on the current connection.

    Raises:
      KeyError: If the device has no object at that path.
    """
    return await self.io.resolve_path(path)

  async def _resolve_optional(self, path: str) -> Optional[Address]:
    """The address of the object at `path`, or None when this instrument has no such object."""
    try:
      return await self.resolve_path(path)
    except (KeyError, RuntimeError, TypeError):
      return None

  async def _resolve_command(
    self, command: TCPCommand[ResultT]
  ) -> Union[TCPCommand[ResultT], _ResolvedPrepCommand]:
    """Resolve a fixed firmware path without modifying the caller's request."""
    self.io._session.require_active()
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

  async def send_command(
    self, command: TCPCommand[ResultT], *, read_timeout: Optional[float] = None
  ) -> ResultT:
    """Send a command to the object it names, and decode what the device answers.

    Args:
      command: the command. One declaring a `firmware_path` is sent to that object's address on this
        connection; one given an explicit `dest` is sent there.
      read_timeout: how long to wait for the answer, in seconds. Defaults to the link's.

    Returns:
      The command's decoded response.

    Raises:
      RuntimeError: If the command's firmware path does not resolve on this device.
    """
    session = self.io._session
    resolved = await self._resolve_command(command)
    if isinstance(resolved, _ResolvedPrepCommand):
      data = await session.execute(resolved, read_timeout=read_timeout)
      return command.parse_response_parameters(data)
    return await session.execute(command, read_timeout=read_timeout)

  async def exchange(
    self, command: TCPCommand[object], *, read_timeout: Optional[float] = None
  ) -> CommandResponse:
    """Send a command and return the device's full terminal frame, firmware errors included."""
    session = self.io._session
    return await session.exchange(await self._resolve_command(command), read_timeout=read_timeout)

  async def request_root_name(self) -> str:
    """Request the name of the device's firmware root object."""
    roots = self.io.get_root_object_addresses()
    if not roots:
      raise RuntimeError("No root objects discovered. Call setup() first.")
    return (await self.io.introspection.get_object(roots[0])).name

  async def request_method_by_name(self, address: Union[Address, str], name: str) -> MethodInfo:
    """Request the method called `name` on an object, from its method table.

    Raises:
      RuntimeError: If the name is absent, or on more than one interface.
    """
    return await self.io.introspection.get_method_by_name(address, name)

  async def request_interface_methods(
    self, address: Union[Address, str], interface_id: int
  ) -> List[MethodInfo]:
    """Request the methods an object declares on one interface."""
    return await self.io.introspection.methods_for_interface(address, interface_id)

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
    where = dest if isinstance(dest, str) else (self.io.registry.path(address) or str(address))
    table = await self.io.introspection.ensure_method_table(address)
    matches = [m for m in table if m.name == name]
    if not matches:
      message = (
        f"this Prep's firmware has no {name!r} method on {where}; which methods exist, and at "
        "which ids, differs between firmware versions"
      )
      version = None if self.configuration is None else self.configuration.firmware_version
      if version is not None:
        message += f" (this Prep runs {version})"
      raise PrepMethodNotFoundError(message)
    if len(matches) > 1:
      interfaces = sorted(m.interface_id for m in matches)
      raise RuntimeError(
        f"{name!r} is on more than one interface of {where} ({interfaces}), so the name does not "
        "say which to send"
      )
    method = matches[0]
    return await self.send_command(
      PrepCmd.PrepProbeRequest(
        dest=address, command_id=method.method_id, interface_id=method.interface_id
      )
    )

  async def request_firmware_string(
    self, address: Address, method_id: int, interface_id: int = 3
  ) -> Optional[str]:
    """Send a status request and decode the string it answers, or None when it answers none."""
    data = await self.send_command(
      PrepCmd.PrepProbeRequest(dest=address, command_id=method_id, interface_id=interface_id)
    )
    for _, value in HoiParamsParser(data).parse_all():
      if isinstance(value, str):
        return value.rstrip("\x00")
    return None

  async def request_channel_drives(self, root_name: str = "Channel Root") -> ChannelDriveMap:
    """Discover per-channel drive addresses via bounded subobject enumeration.

    MLPrepRoot exposes one ``<root_name>`` child per physical channel (siblings
    with identical names, distinguished by the ``node`` component of their
    :class:`Address`). For each one we walk:

    - ``<root>.Channel.Squeeze.SDrive``     → sleeve sensor
    - ``<root>.Channel.ZAxis`` / ``.ZDrive`` → Z axis and Z drive
    - ``<root>.Channel.YAxis`` / ``.YDrive`` → Y axis and Y drive, where the channel has one
    - ``<root>.Channel.Calibration`` / ``.CLld`` → continuous cLLD detection and its status
    - ``<root>.NodeInformation``            → per-channel firmware strings

    Uses ``get_subobject_address`` / ``get_object`` along the known path shape —
    no full-tree traversal. Pass ``root_name="MPH Channel Root"`` for the 8MPH
    head. For a full firmware-tree dump use
    :meth:`PrepDriver.request_firmware_tree`.
    """
    intro = self.io.introspection
    try:
      mlprep_root = await self.resolve_path(PREP_ROOT_NAME)
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
    yaxis: List[Address] = []
    ydrive: List[Address] = []
    calibration: List[Address] = []
    clld: List[Address] = []
    zaxis: List[Address] = []

    for ch_root in channel_root_addrs:
      top = await intro.find_children_by_name(ch_root, "Channel", "NodeInformation")
      if "NodeInformation" in top:
        node_info.append(top["NodeInformation"])

      channel_addr = top.get("Channel")
      if channel_addr is None:
        logger.warning("%s @ %s has no 'Channel' child", root_name, ch_root)
        continue

      axes = await intro.find_children_by_name(
        channel_addr, "Squeeze", "ZAxis", "YAxis", "Calibration", "CLld"
      )
      if (sq_parent := axes.get("Squeeze")) is not None:
        sq = await intro.find_children_by_name(sq_parent, "SDrive")
        if "SDrive" in sq:
          sleeve.append(sq["SDrive"])
      if (zx_parent := axes.get("ZAxis")) is not None:
        zaxis.append(zx_parent)
        zx = await intro.find_children_by_name(zx_parent, "ZDrive")
        if "ZDrive" in zx:
          zdrive.append(zx["ZDrive"])
      if (yx := axes.get("YAxis")) is not None:
        yaxis.append(yx)
        yd = await intro.find_children_by_name(yx, "YDrive")
        if "YDrive" in yd:
          ydrive.append(yd["YDrive"])
      if (cal := axes.get("Calibration")) is not None:
        calibration.append(cal)
      if (cl := axes.get("CLld")) is not None:
        clld.append(cl)

    logger.debug("Discovered %d %s channel drive pair(s)", len(channel_root_addrs), root_name)
    return ChannelDriveMap(
      sleeve_sensor_addrs=sleeve,
      zdrive_addrs=zdrive,
      node_info_addrs=node_info,
      yaxis_addrs=yaxis,
      ydrive_addrs=ydrive,
      calibration_addrs=calibration,
      clld_addrs=clld,
      zaxis_addrs=zaxis,
    )

  # ----------------------------------------
  # What the device carries
  # ----------------------------------------

  @property
  def num_channels(self) -> int:
    """The number of independent pipetting channels present on the device."""
    if self.configuration is None or self.configuration.num_channels is None:
      raise RuntimeError("channel count not read; have you called `prep.setup()`?")
    return self.configuration.num_channels

  @property
  def head8_installed(self) -> bool:
    """Whether the 8-channel head is fitted."""
    if self.configuration is None or self.configuration.head8_installed is None:
      raise RuntimeError("head8 presence not read; have you called `prep.setup()`?")
    return self.configuration.head8_installed

  # ----------------------------------------
  # Device queries
  # ----------------------------------------

  async def request_device_serial_number(self) -> Optional[str]:
    """Request what the device calls itself, or None when this instrument has no such object."""
    addr = await self._resolve_optional(MLPREP_CPU_OBJECT_PATH)
    if addr is None:
      return None
    return await self.request_firmware_string(addr, method_id=9)

  async def request_firmware_version(self) -> Optional[str]:
    """Request what MLPrepCpu runs, or None when this instrument has no such object."""
    addr = await self._resolve_optional(MLPREP_CPU_OBJECT_PATH)
    if addr is None:
      return None
    return await self.request_firmware_string(addr, method_id=8)

  async def request_bootloader_version(self) -> Optional[str]:
    """Request MLPrepCpu's bootloader version, or None when this instrument has no such object."""
    addr = await self._resolve_optional(MLPREP_CPU_OBJECT_PATH)
    if addr is None:
      return None
    return await self.request_firmware_string(addr, method_id=2, interface_id=2)

  async def request_module_part_number(self) -> Optional[str]:
    """Request the pipettor module's part number, or None when it has no such object."""
    addr = await self._resolve_optional(MODULE_INFORMATION_OBJECT_PATH)
    if addr is None:
      return None
    return await self.request_firmware_string(addr, method_id=5)

  async def request_present_channels(self) -> Optional[Tuple[PrepCmd.ChannelIndex, ...]]:
    """Request which channels are present (GetPresentChannels on MLPrepService)."""
    service_addr = await self._resolve_optional(MLPREP_SERVICE_OBJECT_PATH)
    if service_addr is None:
      return None
    try:
      resp = await self.send_command(PrepCmd.PrepGetPresentChannels(dest=service_addr))
      if resp is None or not resp.channels:
        return None
      return tuple(
        PrepCmd.ChannelIndex(v) if v in (0, 1, 2, 3) else PrepCmd.ChannelIndex.InvalidIndex
        for v in resp.channels
      )
    except (
      TimeoutError,
      ConnectionError,
      ConnectionResetError,
      ConnectionAbortedError,
      BrokenPipeError,
      OSError,
    ):
      raise
    except Exception as e:
      logger.warning("Failed to query present channels: %s", e)
      return None

  async def request_device_configuration(self) -> DeviceConfiguration:
    """Request what the device carries and how it is set up.

    Aggregates MLPrep, the deck configuration and MLPrepService. Which device it is - its serial and
    firmware - is read separately, by `discover`.

    Raises:
      RuntimeError: If the deck configuration object does not resolve.
    """
    mlprep = self.mlprep_address
    enc_resp = await self.send_command(PrepCmd.PrepGetIsEnclosurePresent(dest=mlprep))
    safe_resp = await self.send_command(PrepCmd.PrepGetSafeSpeedsEnabled(dest=mlprep))
    height_resp = await self.send_command(PrepCmd.PrepGetDefaultTraverseHeight(dest=mlprep))
    has_enclosure = bool(enc_resp.value) if enc_resp else False
    safe_speeds_enabled = bool(safe_resp.value) if safe_resp else False
    default_traverse_height = float(height_resp.value) if height_resp else None

    deck_bounds: Optional[PrepCmd.DeckBounds] = None
    deck_sites: Tuple[PrepCmd.DeckSiteInfo, ...] = ()
    waste_sites: Tuple[PrepCmd.WasteSiteInfo, ...] = ()
    deck_addr = await self._resolve_optional(DECK_CONFIGURATION_OBJECT_PATH)
    if deck_addr is None:
      raise RuntimeError("DeckConfiguration path did not resolve — cannot load instrument config")

    bounds_resp = await self.send_command(PrepCmd.PrepGetDeckBounds(dest=deck_addr))
    if bounds_resp:
      deck_bounds = PrepCmd.DeckBounds(
        min_x=bounds_resp.min_x,
        max_x=bounds_resp.max_x,
        min_y=bounds_resp.min_y,
        max_y=bounds_resp.max_y,
        min_z=bounds_resp.min_z,
        max_z=bounds_resp.max_z,
      )

    sites_resp = await self.send_command(PrepCmd.PrepGetDeckSiteDefinitions(dest=deck_addr))
    if sites_resp and sites_resp.sites:
      deck_sites = tuple(
        PrepCmd.DeckSiteInfo(
          id=int(s.id),
          left_bottom_front_x=float(s.left_bottom_front_x),
          left_bottom_front_y=float(s.left_bottom_front_y),
          left_bottom_front_z=float(s.left_bottom_front_z),
          length=float(s.length),
          width=float(s.width),
          height=float(s.height),
        )
        for s in sites_resp.sites
      )
      logger.debug("Discovered %d deck sites", len(deck_sites))

    waste_resp = await self.send_command(PrepCmd.PrepGetWasteSiteDefinitions(dest=deck_addr))
    if waste_resp and waste_resp.sites:
      waste_sites = tuple(
        PrepCmd.WasteSiteInfo(
          index=int(s.index),
          x_position=float(s.x_position),
          y_position=float(s.y_position),
          z_position=float(s.z_position),
          z_seek=float(s.z_seek),
        )
        for s in waste_resp.sites
      )
      logger.debug("Discovered %d waste sites: %s", len(waste_sites), waste_sites)

    present = await self.request_present_channels()
    if present is not None:
      pipetting = [
        c
        for c in present
        if c not in (PrepCmd.ChannelIndex.InvalidIndex, PrepCmd.ChannelIndex.MPHChannel)
      ]
      num_channels = len(pipetting)
      head8_installed = PrepCmd.ChannelIndex.MPHChannel in present
    else:
      # A device that does not say which channels it has is taken for a legacy Prep, with its two.
      num_channels = len(PrepCmd.channel_order_legacy_prep)
      head8_installed = False

    return DeviceConfiguration(
      num_channels=num_channels,
      head8_installed=head8_installed,
      has_enclosure=has_enclosure,
      safe_speeds_enabled=safe_speeds_enabled,
      default_traverse_height=default_traverse_height,
      deck_bounds=deck_bounds,
      deck_sites=deck_sites,
      waste_sites=waste_sites,
    )

  async def request_initialization_status(self) -> bool:
    """Whether MLPrep reports as initialized (GetIsInitialized, cmd=2)."""
    result = await self.send_command(PrepCmd.PrepGetIsInitialized(dest=self.mlprep_address))
    if result is None:
      return False
    return bool(result.value)

  async def request_firmware_tree(self, refresh: bool = False) -> FirmwareTreeNode:
    """Firmware object tree. ``print(await prep.request_firmware_tree())`` for a diagnostic dump."""
    return await self.io.introspection.get_firmware_tree(refresh=refresh)

  # ----------------------------------------
  # Tip types
  # ----------------------------------------

  async def request_tip_and_needle_definitions(self) -> Tuple[PrepCmd.TipDefinition, ...]:
    """Tip/needle definitions (GetTipAndNeedleDefinitions, cmd=11)."""
    result = await self.send_command(
      PrepCmd.PrepGetTipAndNeedleDefinitions(dest=self.mlprep_address)
    )
    if result is None or not result.definitions:
      return ()
    return tuple(result.definitions)

  # ----------------------------------------
  # Discovery and initialization
  # ----------------------------------------

  def _check_declared_against(self, discovered: DeviceConfiguration) -> None:
    """Raise if what was declared cannot stand for what the device answered.

    Only what decides whether the two are the same kind of device: what is fitted. Identity and
    set-up are left out, since a declaration taken off one device describes another of the same build.

    Args:
      discovered: what the device answered.

    Raises:
      ValueError: If any of those disagree, naming each.
    """
    declared = self.declared.get("device")
    if declared is None:
      return
    differences = [
      f"{name}: declared {getattr(declared, name)!r}, device answers {getattr(discovered, name)!r}"
      for name in _DECLARATION_MUST_MATCH
      if getattr(declared, name) != getattr(discovered, name)
    ]
    if differences:
      raise ValueError(
        "the declared configuration does not describe this device:\n  " + "\n  ".join(differences)
      )

  async def discover(self) -> DeviceConfiguration:
    """Read what device is on the other end: what it carries, how it is set up, and which it is.

    Read-only: nothing moves.

    Returns:
      The configuration, which is also kept as `self.configuration`.

    Raises:
      ValueError: If a declared configuration does not describe this device.
    """
    configuration = await self.request_device_configuration()
    # Which device answered, and what it is running. A device that will not say keeps nothing rather
    # than failing setup: nothing the driver does depends on it.
    try:
      configuration.serial_number = await self.request_device_serial_number()
    except Exception:
      logger.warning("the device did not say what it calls itself; leaving its serial unrecorded")
    try:
      configuration.firmware_version = await self.request_firmware_version()
    except Exception:
      logger.warning("the device did not report its firmware; leaving its version unrecorded")
    self._check_declared_against(configuration)
    self.configuration = configuration
    return configuration

  async def _initialize_instrument(self, *, smart: bool, force_initialize: bool) -> None:
    """Send ``MLPrep.Initialize`` when needed."""
    if not force_initialize:
      try:
        already = await self.request_initialization_status()
      except Exception as e:
        logger.error("GetIsInitialized failed; cannot decide whether to init: %s", e)
        raise
      if already:
        logger.debug("device reports initialized - skipping the initialization procedure")
        return

      logger.debug("device reports not initialized - running the initialization procedure")
    await self.send_command(
      PrepCmd.PrepInitialize(
        smart=smart,
        tip_drop_params=PrepCmd.InitTipDropParameters(
          default_values=True,
          x_position=287.0,
          rolloff_distance=3,
          channel_parameters=[],
        ),
      )
    )
    logger.debug("the device initialization procedure has run")

  def format_setup_summary(self) -> str:
    """One block describing the device that was found: how it is reached, what it calls itself and
    runs, how it is set up, its deck, and per channel its firmware and reach, and whether it carries
    an 8-channel head.

    Returns:
      A multi-line summary, or a note that setup has not run.
    """
    c = self.configuration
    if c is None:
      return "[Hamilton Prep] not discovered yet"

    traverse = "unknown" if c.default_traverse_height is None else f"{c.default_traverse_height} mm"
    lines = [
      f"[Hamilton Prep] Connected on {self.describe_link()}",
      f"  Serial: {c.serial_number or 'unknown'}",
      f"  Firmware: {c.firmware_version or 'unknown'}",
      f"  Configuration: enclosure {'installed' if c.has_enclosure else 'none'}, "
      f"safe speeds {'on' if c.safe_speeds_enabled else 'off'}, traverse height {traverse}",
    ]
    deck = f"{len(c.deck_sites)} sites, {len(c.waste_sites)} waste sites"
    if c.deck_bounds is not None:
      b = c.deck_bounds
      deck = (
        f"x {_range((b.min_x, b.max_x))}, y {_range((b.min_y, b.max_y))}, "
        f"z {_range((b.min_z, b.max_z))}; {deck}"
      )
    lines.append(f"  Deck: {deck}")

    if self.pipettes is None:
      lines.append("  Pipettes: none")
    else:
      p = self.pipettes.configuration
      pipetting = "aspirate/dispense unprobed"
      if p.supports_v2_pipetting is not None:
        pipetting = f"{'v2' if p.supports_v2_pipetting else 'v1'} aspirate/dispense"
      lines.append(f"  Pipettes: {len(p.channels)}, {pipetting}")
      for channel, entry in enumerate(p.channels):
        order = self.pipettes.channel_order
        enum = int(order[channel]) if channel < len(order) else None
        side = (
          {
            int(PrepCmd.ChannelIndex.RearChannel): "rear",
            int(PrepCmd.ChannelIndex.FrontChannel): "front",
          }.get(enum, str(channel))
          if enum is not None
          else str(channel)
        )
        firmware = f"firmware {entry.firmware_version}, " if entry.firmware_version else ""
        lines.append(
          f"    channel {channel} ({side}): {firmware}x {_range(entry.x_range)}, "
          f"y {_range(entry.y_range)}, z {_range(entry.z_range)}"
        )

    head8 = "none"
    if c.head8_installed:
      head8 = "installed" if self.head8 is not None else "none, but the device reports it installed"
    lines.append(f"  8-channel head: {head8}")
    return "\n".join(lines)

  # ----------------------------------------
  # Configuration system
  # ----------------------------------------

  def _saved_configuration(self) -> Dict[str, Any]:
    """What `save_configuration` writes.

    Raises:
      RuntimeError: If nothing has been read off the device yet.
    """
    if self.configuration is None:
      raise RuntimeError("nothing has been read off this device; call `setup` first")
    saved: Dict[str, Any] = {"device": to_jsonable(self.configuration)}
    if self.pipettes is not None:
      saved["pipettes"] = to_jsonable(self.pipettes.configuration)
    return saved

  def save_configuration(self, path: str, indent: Optional[int] = 2) -> None:
    """Write what this device reported to a file, to be declared or simulated from later.

    Args:
      path: where to write it.
      indent: how far to indent the JSON, or None to write it on one line.

    Raises:
      RuntimeError: If nothing has been read off the device yet.
    """
    with open(path, "w", encoding="utf-8") as f:
      json.dump(self._saved_configuration(), f, indent=indent)

  # ----------------------------------------
  # Resource model
  # ----------------------------------------

  def _place_reported_sites(self) -> None:
    """Move the teaching needle and the waste positions to where the device reports them.

    Only on a `PrepDeck`. The teaching needle goes to the deck site whose footprint matches the deck's
    teaching spot, keeping the spot's height: a site's height is not where a needle is picked up. Each waste
    position goes to the waste site for its channel. Whatever the device does not report keeps the deck's
    default.
    """
    if not isinstance(self.deck, PrepDeck) or self.configuration is None:
      return
    c = self.configuration
    if self.deck.has_resource("teaching_tip"):
      spot = self.deck.get_resource("teaching_tip")
      footprint = (spot.get_absolute_size_x(), spot.get_absolute_size_y())
      site = next((s for s in c.deck_sites if (s.length, s.width) == footprint), None)
      if site is not None and spot.location is not None and spot.parent is not None:
        parent = spot.parent.get_location_wrt(self.deck)
        spot.location = Coordinate(
          site.left_bottom_front_x - parent.x, site.left_bottom_front_y - parent.y, spot.location.z
        )
        logger.debug("teaching needle at deck site %d", site.id)
    for waste_site in c.waste_sites:
      name = _WASTE_SITE_NAMES.get(waste_site.index)
      if name is None or not self.deck.has_resource(name):
        continue
      self.deck.get_resource(name).location = Coordinate(
        waste_site.x_position, waste_site.y_position, waste_site.z_position
      )
      logger.debug("%s at waste site %d", name, waste_site.index)

  async def _create_capability_resources(self) -> None:
    """Put the X-arm on the deck where it is, and hang a resource for each pipetting channel from it.

    As the STAR driver does. Only on a `PrepDeck`, which is what knows where a Prep's arm goes. What is
    already on the deck is reused, and repeated setups do not duplicate it.
    """
    if not isinstance(self.deck, PrepDeck) or self.x_arm is None or self.pipettes is None:
      return
    positions = await self.pipettes.request_locations()
    if not positions:
      logger.warning("the channels reported no positions, so the arm and channels are not modelled")
      return
    arm, c = self.x_arm, self.x_arm.configuration
    pipettes = self.pipettes.configuration
    # The arm rides at the top of the channels' travel: what their bounds say, or the traverse height
    # when no bounds were read.
    tops = [c.z_range[1] for c in self.pipettes.configuration.channels if c.z_range is not None]
    if tops:
      z = max(tops)
    elif self.configuration is not None and self.configuration.default_traverse_height is not None:
      z = self.configuration.default_traverse_height
    else:
      z = self.deck.get_absolute_size_z()
    # The Y the channels reach between them, which the arm's reference line spans.
    y_ranges = [c.y_range for c in pipettes.channels if c.y_range is not None]
    reach = (min(r[0] for r in y_ranges), max(r[1] for r in y_ranges)) if y_ranges else None
    arm.resource = self.deck.get_or_create_x_arm(
      name="x_arm",
      x=positions[0].x,
      z=z,
      size_x=c.size_x,
      size_y=c.size_y,
      size_z=c.size_z,
      reference_point_from_left=c.reference_point_from_left,
      model=c.model,
      appearance=c.appearance,
      reference_y_range=reach,
    )

    # One resource per channel, a child of the arm's as on the STAR: the channels share the arm's X,
    # and each has its own Y and Z.
    self.pipettes.resources = []
    for channel in range(len(positions)):
      name = f"pipette_channel_{channel}"
      resource = next((child for child in arm.resource.children if child.name == name), None)
      if resource is None:
        resource = Resource(
          name=name,
          size_x=pipettes.channel_width,
          size_y=pipettes.channel_width,
          size_z=pipettes.channel_size_z,
          category="pipette_channel",
          model=pipettes.channel_model,
        )
        anchor = resource.get_anchor(x=pipettes.x_reference_anchor)
        arm.resource.assign_child_resource(
          resource, location=Coordinate(c.reference_point_from_left - anchor.x, 0.0, 0.0)
        )
      self.pipettes.add_tip_mounting_shaft(resource)
      self.pipettes.resources.append(resource)
    # Seat each where it was read, now that there is something to record it on.
    self.pipettes._record_positions(positions)

  # ----------------------------------------
  # CoRe grippers
  # ----------------------------------------

  @property
  def core_gripper_arm(self) -> CoreGripperArm:
    """The mounted CoRe gripper arm. Raises if grippers are not currently picked up."""
    if self._core_gripper_arm is None:
      raise RuntimeError(
        "CoRe grippers not mounted. Call `await prep.pick_up_core_grippers()` first, "
        "or use `async with prep.mounted_core_grippers() as arm:`."
      )
    return self._core_gripper_arm

  @property
  def core_grippers_mounted(self) -> bool:
    return self._core_gripper_arm is not None

  async def pick_up_core_grippers(self) -> CoreGripperArm:
    """Pick up the CoRe gripper tools and return the mounted arm."""
    if self._core_gripper_arm is not None:
      raise RuntimeError("CoRe grippers already mounted")
    if self.pipettes is None or self.core_grippers is None:
      raise RuntimeError("PrepDriver.setup() has not run.")

    mount = self.deck.get_resource("core_grippers")
    if not isinstance(mount, HamiltonCoreGrippers):
      raise TypeError(
        "deck must have a resource named 'core_grippers' of type HamiltonCoreGrippers"
      )

    loc = mount.get_location_wrt(self.deck)
    await self.core_grippers.pick_up_tool(
      tool_position_x=loc.x,
      tool_position_z=loc.z,
      front_channel_position_y=loc.y + mount.front_channel_y_center,
      rear_channel_position_y=loc.y + mount.back_channel_y_center,
      tool_seek=loc.z + 10.0,
    )

    self._core_gripper_arm = CoreGripperArm(
      backend=self.core_grippers, reference_resource=self.deck, grip_axis="y"
    )
    return self._core_gripper_arm

  async def return_core_grippers(self) -> None:
    if self._core_gripper_arm is None:
      return
    try:
      await self._core_gripper_arm.backend.drop_tool()
    finally:
      self._core_gripper_arm = None

  @asynccontextmanager
  async def mounted_core_grippers(self) -> AsyncIterator[CoreGripperArm]:
    arm = await self.pick_up_core_grippers()
    try:
      yield arm
    finally:
      await self.return_core_grippers()

  # ----------------------------------------
  # Park and spread
  # ----------------------------------------

  async def park(self) -> None:
    await self.send_command(PrepCmd.PrepPark())

  async def spread(self) -> None:
    await self.send_command(PrepCmd.PrepSpread())

  async def is_parked(self) -> bool:
    """Whether MLPrep reports itself parked.

    Looked up by name: the method's id is not the same on every firmware version, and older
    firmware does not have it.

    Raises:
      PrepMethodNotFoundError: If this firmware has no IsParked.
    """
    data = await self.request_by_name(MLPREP_OBJECT_PATH, "IsParked")
    return bool(PrepCmd.PrepIsParked.parse_response_parameters(data).value)

  async def is_spread(self) -> bool:
    """Whether MLPrep reports its channels spread.

    Looked up by name, as `is_parked` is.

    Raises:
      PrepMethodNotFoundError: If this firmware has no IsSpread.
    """
    data = await self.request_by_name(MLPREP_OBJECT_PATH, "IsSpread")
    return bool(PrepCmd.PrepIsSpread.parse_response_parameters(data).value)

  # ----------------------------------------
  # Power
  # ----------------------------------------

  async def power_down_request(self) -> None:
    await self.send_command(PrepCmd.PrepPowerDownRequest())

  async def confirm_power_down(self) -> None:
    await self.send_command(PrepCmd.PrepConfirmPowerDown())

  async def cancel_power_down(self) -> None:
    await self.send_command(PrepCmd.PrepCancelPowerDown())

  # ----------------------------------------
  # Deck light
  # ----------------------------------------

  async def request_deck_light(self) -> Tuple[int, int, int, int]:
    result = await self.send_command(PrepCmd.PrepGetDeckLight())
    if result is None:
      raise ValueError("No response from GetDeckLight.")
    return (result.white, result.red, result.green, result.blue)

  async def set_deck_light(self, white: int, red: int, green: int, blue: int) -> None:
    await self.send_command(PrepCmd.PrepSetDeckLight(white=white, red=red, green=green, blue=blue))

  async def disco_mode(self) -> None:
    """Easter egg: cycle deck lights then restore previous state."""
    white, red, green, blue = await self.request_deck_light()
    try:
      for _ in range(69):
        await self.set_deck_light(
          white=random.randint(1, 255),
          red=random.randint(1, 255),
          green=random.randint(1, 255),
          blue=random.randint(1, 255),
        )
        await asyncio.sleep(0.1)
    finally:
      await self.set_deck_light(white=white, red=red, green=green, blue=blue)

  # ----------------------------------------
  # Speed scales
  # ----------------------------------------

  async def request_x_speed_scale(self) -> int:
    """Request how fast MLPrep drives X, as a percentage of its full speed."""
    return int((await self.send_command(PrepCmd.PrepGetXSpeedScale())).value)

  async def request_z_speed_scale(self) -> int:
    """Request how fast MLPrep drives Z, as a percentage of its full speed."""
    return int((await self.send_command(PrepCmd.PrepGetZSpeedScale())).value)

  async def set_x_speed_scale(self, percent: int) -> None:
    """Set how fast MLPrep drives X, as a percentage of its full speed. Stays set until changed.

    Args:
      percent: 1 to 100.

    Raises:
      ValueError: If `percent` is outside 1 to 100.
    """
    if not 1 <= percent <= 100:
      raise ValueError(f"x speed scale must be between 1 and 100 percent, is {percent}")
    await self.send_command(PrepCmd.PrepSetXSpeedScale(value=percent))

  async def set_z_speed_scale(self, percent: int) -> None:
    """Set how fast MLPrep drives Z, as a percentage of its full speed. Stays set until changed.

    Args:
      percent: 1 to 100.

    Raises:
      ValueError: If `percent` is outside 1 to 100.
    """
    if not 1 <= percent <= 100:
      raise ValueError(f"z speed scale must be between 1 and 100 percent, is {percent}")
    await self.send_command(PrepCmd.PrepSetZSpeedScale(value=percent))
