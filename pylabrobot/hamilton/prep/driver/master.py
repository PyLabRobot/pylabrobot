"""Prep driver: orchestrates transport, the device's configuration, and peer construction."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import (
  Any,
  AsyncIterator,
  Dict,
  List,
  Optional,
  Tuple,
  TypeVar,
  Union,
)

from pylabrobot.hamilton.transport.tcp.commands import TCPCommand
from pylabrobot.hamilton.transport.tcp.error_tables import HC_RESULT_PROTOCOL
from pylabrobot.hamilton.transport.tcp.hoi_error import HoiError, parse_hamilton_error_entries
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
from pylabrobot.resources.hamilton.tip_creators import (
  HamiltonTip,
  TipPickupMethod,
  TipSize,
  hamilton_tip_10uL,
  hamilton_tip_10uL_filter,
  hamilton_tip_50uL,
  hamilton_tip_50uL_filter,
  hamilton_tip_300uL,
  hamilton_tip_300uL_filter,
  hamilton_tip_1000uL,
  hamilton_tip_1000uL_filter,
)
from pylabrobot.resources.resource import Resource

from . import prep_commands as PrepCmd
from .configuration import DeviceConfiguration, read_configuration, to_jsonable
from .errors import PREP_ERROR_CODES, PrepMethodNotFoundError
from .features.calibration import Calibration
from .features.core_grippers import CoreGrippers
from .features.head8 import Head8
from .features.lights import Lights
from .features.method import MethodLifecycle
from .features.pipettes import TIP_FITTING_DEPTH, Pipettes, channels_named
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


# How far each channel moves out to open the grippers before initializing, in mm.
_OPEN_GRIPPERS_BY = 5.0


def _confirm(question: str) -> bool:
  """Ask the person at the device; only y or yes is a yes. No one to ask is a no."""
  try:
    return input(question).strip().lower() in ("y", "yes")
  except EOFError:
    return False


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


def _hamilton_tip_for_reach(reach: float, has_filter: bool, name: str) -> Optional[HamiltonTip]:
  """The Hamilton tip whose reach below the stop disc is `reach`, or None when none is within 0.3 mm.

  A drop needs the tip's collar height, which only a known tip has. The filter flag breaks a tie
  between the plain and filtered tip of one length, which share every other dimension.
  """
  candidates = (
    hamilton_tip_10uL,
    hamilton_tip_10uL_filter,
    hamilton_tip_50uL,
    hamilton_tip_50uL_filter,
    hamilton_tip_300uL,
    hamilton_tip_300uL_filter,
    hamilton_tip_1000uL,
    hamilton_tip_1000uL_filter,
  )
  matching = [
    tip
    for tip in (make(name) for make in candidates)
    if abs(tip.get_size_z() - tip.fitting_depth - reach) <= 0.3
  ]
  if not matching:
    return None
  return next((tip for tip in matching if tip.has_filter == has_filter), matching[0])


def _refused_with(error: BaseException, code: int) -> bool:
  """Whether a device refusal carries `code` in any of its entries."""
  entries = getattr(error, "entries", None) or getattr(error, "hoi_entries", None) or []
  return any(getattr(entry, "result", None) == code for entry in entries)


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
    use_two_sessions: bool = True,
    second_io: Optional[HamiltonTCPClient] = None,
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
      use_two_sessions: whether to open a second connection to `host`, so each channel node can be
        sent a command beside the other's. Only with `host`: a given `io` has no second link but
        `second_io`.
      second_io: the second link, instead of a second TCP connection to `host`.

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
      if use_two_sessions and second_io is None:
        second_io = _PrepTCPClient(host=host, port=port)
    self.io: HamiltonTCPClient = io
    self.second_io: Optional[HamiltonTCPClient] = second_io
    self._mlprep_address: Optional[Address] = None
    self.deck = deck
    # What the device reports about itself, read by `discover`. None until setup has run.
    self.configuration: Optional[DeviceConfiguration] = None
    # Where each tool the channels are carrying came from, so it goes back there when it is
    # dropped. A tool is a resource: while the channels hold it, it is theirs.
    self.pipettes: Optional[Pipettes] = None
    self.head8: Optional[Head8] = None
    self.core_grippers: Optional[CoreGrippers] = None
    self.lights: Optional[Lights] = None
    # How long to wait for a command's answer, in seconds. A command that takes longer than any
    # this device performs is one it is not going to answer, and a caller waiting on it cannot halt
    # the device or say so. Initializing names its own.
    self.default_read_timeout: float = 60.0
    self.method: Optional[MethodLifecycle] = None
    self.calibration: Optional[Calibration] = None
    # The gantry the channels ride. Built at setup, and kept across setups like the configuration.
    self.x_arm: Optional[XArm] = None
    self._setup_finished: bool = False

  def get_component_name(self, name: str) -> str:
    """Resolve a built-in resource name using the Prep deck's naming prefix."""
    if isinstance(self.deck, PrepDeck):
      return self.deck.get_component_name(name)
    return name

  # ----------------------------------------
  # Connection and lifecycle
  # ----------------------------------------

  async def setup(
    self,
    *,
    smart: bool = True,
    force_initialize: bool = False,
    skip_device_initialization: bool = False,
    default_minimum_traverse_height: Optional[float] = None,
    use_v1_aspirate_dispense: bool = False,
  ):
    """Connect, discover the device, initialize MLPrep, construct peers.

    A device that reports itself initialized is left as it was found, so what the channels hold is read and
    logged, and they are raised to Z safety, before anything can move laterally.

    Args:
      skip_device_initialization: do not run the device's own initialization procedure on a device that reports
        itself down. Its moves are then whatever the caller sends, and a device that has not initialized may
        refuse them.
      default_minimum_traverse_height: the height the pipettes and the 8-channel head travel at when a command
        names none, in mm. Replaces what the device reports.

    Raises:
      RuntimeError: If the device records a plate gripped and has to initialize, and the person at it
        does not confirm the grippers may open.
    """
    logger.debug("Setting up Prep on %s ...", self.describe_link())
    try:
      await self._open()

      # 1. What is on the other end, and what does it carry?
      logger.debug("[PHASE 1] Discovery")
      await self.discover()

      # The light as early as the device's method table allows, so the deck says the device is
      # working for all of the initializing and bringing up that is left. A colour, not an
      # animation: the host drives every frame down this same connection, and setup keeps it busy.
      if self.lights is None and await self.request_deck_light_installed():
        self.lights = Lights(self)
      if self.lights is not None:
        await self.lights.set_color("turquoise")

      # 2. Bring the device to a known state.
      logger.debug("[PHASE 2] Device initialization")
      # Kept across a power cycle: while it is set, the device refuses to initialize (0x0F0A) and to
      # take the tools home (0x0F04).
      plate_held = await self._request_plate_held()
      if skip_device_initialization:
        logger.warning("skipping the device initialization procedure, as asked")
      else:
        if plate_held and (force_initialize or not await self.request_initialization_status()):
          await self._release_held_plate_by_hand()
          plate_held = False
          # Known to need it, just asked: not asked again.
          await self._initialize_instrument(smart=smart, force_initialize=True)
        else:
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
      if use_v1_aspirate_dispense:
        self.pipettes.configuration.use_v1_aspirate_dispense = True
      await self.pipettes._on_setup()
      if default_minimum_traverse_height is not None:
        self.pipettes.default_minimum_traverse_height = default_minimum_traverse_height

      if self.pipettes.head8_installed:
        if self.head8 is None:
          self.head8 = Head8(self, use_v1_aspirate_dispense=use_v1_aspirate_dispense)
        await self.head8._on_setup()
        # One height governs the arm: the head rides the channels' gantry, so it travels at what
        # they travel at rather than at a second number that happens to match.
        self.head8.default_minimum_traverse_height = self.pipettes.default_minimum_traverse_height
        if default_minimum_traverse_height is not None:
          self.head8.default_minimum_traverse_height = default_minimum_traverse_height

      # What the device was left holding, and where it was left standing: read before anything moves laterally,
      # then raise what can be raised. The 8-channel head is not raised: no move of its Z alone is known.
      tips = await self.pipettes.sense_tip_presence()
      logger.info(
        "tips held at setup: %s",
        ", ".join(f"channel {i}" for i, held in enumerate(tips) if held) or "none",
      )
      try:
        await self.pipettes.move_to_safe_z()
      except Exception:
        # A device that has not initialized refuses to move; setup still has to finish so the caller can look.
        logger.warning("could not raise the channels to Z safety at setup", exc_info=True)
      # Asked, not assumed, as `stop` asks: a device that reported itself initialized was left as someone
      # else left it, and a raise that answers has not said where it arrived.
      low = await self.features_below_safe_z()
      if low:
        logger.warning("not everything is at Z safety after setup: %s", "; ".join(low))
      if self.head8 is not None:
        # TODO: the head is left where it stands, and every lateral move travels it there. No move
        # of its Z alone is known, and the device reports neither its position nor its bounds, so
        # nothing here can tell that it is low or lift it. `MoveZUpToSafe` takes ChannelIndex values
        # and the MPH has one (3): try it on a device with a head fitted, and if it answers, raise
        # the head in `Pipettes.move_to_xy_positions` and `XArm.move_to_x_position` as the channels
        # are raised.
        logger.warning("the 8-channel head is not raised at setup: no move of its Z alone is known")

      if self.core_grippers is None:
        self.core_grippers = CoreGrippers(self)
      if self.x_arm is None:
        self.x_arm = XArm(self)
      # What was found, as resources on the deck - when the driver was given a Prep deck to reflect into.
      if self.deck is not None:
        logger.debug("[PHASE 4] Feature resources")
        self._place_reported_sites()
        await self._create_capability_resources()

      if any(tips) and plate_held:
        attached = await self.pipettes.request_attached_tip_information(tips.index(True))
        if attached is not None and attached.is_tool and self.core_grippers is not None:
          await self.core_grippers._adopt_mounted_tools()
        logger.warning(
          "the device records a plate gripped, so the tools stay on: lower it onto a free spot with "
          "`pipettes.move_tool_bottom_to_z_positions` (both channels together), let go with "
          "`core_grippers.release_plate()`, then return the tools"
        )
      elif any(tips):
        try:
          await self._return_or_discard_attached(tips)
        except Exception:
          # Setup still has to finish: the caller needs a driver to look at the device with.
          logger.warning("could not clear what was attached at setup", exc_info=True)
        # The device reports each channel's window for whatever is attached to it, so the windows
        # recorded at discovery are that thing's. Read them again now the channels are clear.
        still = await self.pipettes.sense_tip_presence()
        carrying = [channel for channel, held in enumerate(still) if held]
        if carrying:
          logger.warning(
            "%s still carries something, so the windows recorded are its, not an empty channel's",
            channels_named(carrying),
          )
        else:
          await self.pipettes._record_channel_bounds()
      self._setup_finished = True
    except Exception:
      # The deck said the device was working; it is not, and the link is about to go.
      if self.lights is not None:
        self.lights.stop_animation()
      await self._close()
      raise

    logger.info("%s", self.format_setup_summary())

    # Setup got all the way here, so say so, and leave the deck as setup found it: dark. Setup
    # stands at the green, so that a caller which stops or exits straight after still sees it.
    if self.lights is not None:
      await self.lights.hold("green", duration=self.lights.default_ready_seconds)

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
      if self.second_io is not None:
        await self.second_io.setup()
    except BaseException:
      await self._close()
      raise

  async def _close(self) -> None:
    """Close the links and discard what was resolved on them."""
    try:
      if self.second_io is not None:
        await self.second_io.stop()
    finally:
      try:
        await self.io.stop()
      finally:
        self._mlprep_address = None

  async def features_below_safe_z(self, tolerance: float = 0.5) -> List[str]:
    """Which channels report below where they are safe.

    Read back rather than taken on trust: a retract that answered without arriving leaves the device looking
    safe while a lateral move would drive whatever is still low into whatever is in the way. Each channel's stop
    disc is held to the pipettes' `default_minimum_traverse_height`, so a mounted tip does not count as low. The 8-channel
    head is not judged: no read of its height is known.

    Args:
      tolerance: how far below the traverse height still counts as up, in mm.

    Returns:
      One entry per channel that is low, naming it and where it says it is. Empty when everything is up, or when
      there are no pipettes.
    """
    low: List[str] = []
    pipettes = self.pipettes
    if pipettes is None:
      return low
    safe = pipettes.default_minimum_traverse_height
    for channel in range(pipettes.num_channels):
      try:
        z = await pipettes.request_stop_disc_z_position(channel)
      except Exception:
        low.append(f"channel {channel} (where it is could not be read)")
      else:
        if z < safe - tolerance:
          low.append(f"channel {channel} at {z:.1f} mm, safe is {safe:.1f} mm")
    return low

  async def stop(self, skip_raise_to_z_safety: bool = False):
    """Close the link, leaving the device safe to move laterally.

    The device keeps its state, but not the deck light: it is darkened, since a colour stands on
    the device with nobody holding it. Only this driver lets go. Every pipetting channel is moved up to Z safety
    first, and where the channels stopped is read back: a driver that let go with a channel low would leave the
    next lateral move to crash it. The 8-channel head is not raised: no move of its Z alone is known.

    The link closes whether or not that succeeds. Repeatable: a driver that is not set up is left alone.

    Args:
      skip_raise_to_z_safety: leave the channels where they stand. The next lateral move of the arm or of a
        channel will crash a channel that is low, so only for holding a position between sessions.
    """
    if not self._setup_finished:
      return
    try:
      # A plate in the jaws goes back first: parking spreads the channels and would tear it out.
      holding = await self._return_held_resource()
      # As at setup: a tool goes back in its holder, anything else into the waste.
      if self.pipettes is not None:
        try:
          tips = await self.pipettes.sense_tip_presence()
          if any(tips) and not holding:
            await self._return_or_discard_attached(tips)
        except Exception:
          # The link closes either way.
          logger.warning("could not clear what was attached before stopping", exc_info=True)
      if skip_raise_to_z_safety:
        low = await self.features_below_safe_z()
        logger.warning(
          "leaving the device without raising the channels: %s",
          "; ".join(low) if low else "nothing is low",
        )
      elif self.pipettes is not None:
        try:
          await self.pipettes.move_to_safe_z()
        except Exception:
          logger.warning("could not move the channels to Z safety", exc_info=True)
        # Asked, not assumed: a move that answers has not said where it stopped.
        low = await self.features_below_safe_z()
        if low:
          logger.warning("not everything is at Z safety: %s", "; ".join(low))
      if holding:
        logger.error(
          "not parking: the device records a plate gripped, and parking spreads the channels. "
          "They are left where they stand; put the plate down before moving them apart."
        )
      else:
        await self.park_device()
    except Exception:
      logger.warning(
        "could not bring the device to a safe state; closing the link anyway", exc_info=True
      )
    finally:
      if self.pipettes is not None:
        await self.pipettes._on_stop()
      if self.head8 is not None:
        await self.head8._on_stop()
      if self.lights is not None:
        # A colour stands on the device without a host to hold it, so a driver that let go mid-hold
        # would leave the deck lit for good.
        try:
          await self.lights.turn_off()
        except Exception:
          logger.warning("could not darken the deck light", exc_info=True)
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
      read_timeout: how long to wait for the answer, in seconds. `default_read_timeout` when None.

    Returns:
      The command's decoded response.

    Raises:
      RuntimeError: If the command's firmware path does not resolve on this device.
    """
    read_timeout = self.default_read_timeout if read_timeout is None else read_timeout
    session = self.io._session
    resolved = await self._resolve_command(command)
    if isinstance(resolved, _ResolvedPrepCommand):
      data = await session.execute(resolved, read_timeout=read_timeout)
      return command.parse_response_parameters(data)
    return await session.execute(command, read_timeout=read_timeout)

  async def send_command_on_second_session(
    self, command: TCPCommand[ResultT], *, read_timeout: Optional[float] = None
  ) -> ResultT:
    """Send a command on the second session, beside whatever the first has in flight.

    Args:
      command: the command, with its `dest` given, as each channel node's commands have it.
      read_timeout: how long to wait for the answer, in seconds. `default_read_timeout` when None.

    Returns:
      The command's decoded response.

    Raises:
      RuntimeError: If there is no second session, or the command names a firmware path, not a
        `dest`.
    """
    if self.second_io is None:
      raise RuntimeError("no second session: built with use_two_sessions=False, or without a host")
    if isinstance(command, PrepCommand) and command.dest == _UNRESOLVED:
      raise RuntimeError(f"{type(command).__name__} needs a dest= on the second session")
    read_timeout = self.default_read_timeout if read_timeout is None else read_timeout
    return await self.second_io._session.execute(command, read_timeout=read_timeout)

  async def exchange(
    self, command: TCPCommand[object], *, read_timeout: Optional[float] = None
  ) -> CommandResponse:
    """Send a command and return the device's full terminal frame, firmware errors included.

    Args:
      command: the command to send.
      read_timeout: how long to wait for the answer, in seconds. `default_read_timeout` when None.
    """
    read_timeout = self.default_read_timeout if read_timeout is None else read_timeout
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
    has_enclosure = bool(enc_resp.value) if enc_resp else False
    safe_speeds_enabled = bool(safe_resp.value) if safe_resp else False

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
      deck_bounds=deck_bounds,
      deck_sites=deck_sites,
      waste_sites=waste_sites,
    )

  async def _release_held_plate_by_hand(self) -> None:
    """Open the grippers with a person there to take the plate, so the device can initialize.

    Before initializing only each channel's own relative Y move runs: the two move apart by
    `_OPEN_GRIPPERS_BY`, then PrepReleasePlate clears the record.

    Raises:
      RuntimeError: If the person does not confirm, or the record is still set after.
    """
    print(
      "\nThe Prep has a plate gripped written into its memory, which a power cycle does not clear. "
      "It refuses to initialize (0x0F0A) until the grippers open, and until it initializes nothing "
      "else can move."
    )
    if not _confirm(
      "Take the plate out of the grippers, or have your hand under it to catch it as they open. "
      "Ready for the grippers to open? [y/n] "
    ):
      raise RuntimeError(
        "setup stopped: the device records a plate gripped and refuses to initialize until the "
        "grippers open. Run setup again when someone can take the plate."
      )
    for channel, distance in ((1, -_OPEN_GRIPPERS_BY), (0, _OPEN_GRIPPERS_BY)):
      try:
        await self._move_relative_in_y(channel, distance)
      except Exception:
        logger.warning("channel %d did not move %+.1f mm", channel, distance, exc_info=True)
    await self.send_command(PrepCmd.PrepReleasePlate())
    if not _confirm("Have you moved your hands out of the device? [y/n] "):
      raise RuntimeError(
        "setup stopped before initializing: hands may be in the device. The grippers are open; run "
        "setup again to initialize."
      )
    if await self._request_plate_held():
      raise RuntimeError("the device still records a plate gripped after the grippers opened")

  async def _return_held_resource(self) -> bool:
    """Put back what the grippers hold, if this session knows where from. Whether one is still held.

    A record that cannot be read counts as held.
    """
    try:
      if not await self._request_plate_held():
        return False
      grippers = self.core_grippers
      if grippers is not None and grippers._taken_from is not None:
        held = grippers._held_resource
        logger.warning(
          "the grippers hold %s: putting it back before stopping",
          "a resource" if held is None else held.name,
        )
        try:
          await grippers.return_resource()
        except Exception:
          logger.error("could not put back what the grippers hold", exc_info=True)
      return await self._request_plate_held()
    except Exception:
      logger.error("could not read whether a plate is held; treating it as held", exc_info=True)
      return True

  async def _move_relative_in_y(self, channel: int, distance: float) -> None:
    """Move a channel along Y by `distance` mm with its own Y axis; runs before initializing.

    Args:
      channel: which channel, 0-indexed from the back.
      distance: how far, in mm; positive is towards the back.
    """
    yaxes = (await self.request_channel_drives()).yaxis_addrs
    await self.send_command(PrepCmd.PrepYAxisMoveRelative(dest=yaxes[channel], distance=distance))

  async def _request_plate_held(self) -> bool:
    """Whether the device records a plate gripped (PrepGetPlateHeld): its record, not a sensor."""
    return bool((await self.send_command(PrepCmd.PrepGetPlateHeld())).value)

  async def request_initialization_status(self) -> bool:
    """Whether MLPrep reports as initialized (GetIsInitialized, cmd=2)."""
    result = await self.send_command(PrepCmd.PrepGetIsInitialized(dest=self.mlprep_address))
    if result is None:
      return False
    return bool(result.value)

  async def request_default_traverse_height(self) -> Optional[float]:
    """The height MLPrep travels at when a command names none (GetDefaultTraverseHeight), in mm.

    Returns:
      The height, or None if the device does not answer it.
    """
    result = await self.send_command(PrepCmd.PrepGetDefaultTraverseHeight(dest=self.mlprep_address))
    return None if result is None else float(result.value)

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

  async def _release_attached_over_waste(self, carrying: List[int]) -> None:
    """Take the channels over their waste sites and let go with the squeeze drive alone.

    A last resort, for what the Pipettor will not drop: after a power cycle it forgets what it
    holds and refuses `DropTips` and its own initialization; a refused `PickUpTool` leaves a stale
    definition behind. The X axis, each channel's Y axis and `ReleaseTips` all run regardless.

    Args:
      carrying: the channels sensing something, 0-indexed from the back.
    """
    if self.pipettes is None or self.x_arm is None:
      raise RuntimeError("the pipettes and the arm are needed to release over the waste")
    if not isinstance(self.deck, PrepDeck):
      raise RuntimeError("the waste sites are placed on a PrepDeck; this driver has another deck")
    names = ["waste_rear", "waste_front", "waste_mph"]
    targets = {
      ch: self.deck.waste_positions[names[ch]].get_location_wrt(self.deck, "c", "c", "t")
      for ch in carrying
    }
    await self.x_arm.move_to_x_position(next(iter(targets.values())).x)
    at = await self.pipettes.request_locations()
    # The smaller target first, so a channel never has to pass the one ahead of it.
    for ch in sorted(carrying, key=lambda c: targets[c].y):
      await self._move_relative_in_y(ch, targets[ch].y - at[ch].y)
    await self.pipettes.release_tips(carrying)

  async def _initialize_instrument(
    self, *, smart: bool, force_initialize: bool, read_timeout: float = 300.0
  ) -> None:
    """Send ``MLPrep.Initialize`` when needed.

    Args:
      smart: whether the device initializes only what it judges it has to.
      force_initialize: run the procedure without asking whether it has already run.
      read_timeout: how long to wait for the procedure, in seconds. A wide margin over the 17 s it
        has been measured to take at its longest, rather than `default_read_timeout`.
    """
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
    initialize = PrepCmd.PrepInitialize(
      smart=smart,
      tip_drop_params=PrepCmd.InitTipDropParameters(
        default_values=True,
        x_position=287.0,
        rolloff_distance=3,
        channel_parameters=[],
      ),
    )
    try:
      await self.send_command(initialize, read_timeout=read_timeout)
    except HoiError as e:
      # Tips on after a power cycle: the device has no definition for them, so its own drop inside
      # the procedure computes a Z beyond travel (0x0F06), whatever drop parameters it is given.
      if not _refused_with(e, 0x0F06):
        raise
      if self.pipettes is None:
        self.pipettes = Pipettes(self)
        await self.pipettes._on_setup()
      if self.x_arm is None:
        self.x_arm = XArm(self)
      carrying = [ch for ch, on in enumerate(await self.pipettes.sense_tip_presence()) if on]
      if not carrying:
        raise
      logger.warning(
        "the device refused to initialize with something on %s it has no definition for: "
        "releasing it over the waste, then initializing again",
        channels_named(carrying),
      )
      await self._release_attached_over_waste(carrying)
      await self.send_command(initialize, read_timeout=read_timeout)
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

    height = None if self.pipettes is None else self.pipettes.default_minimum_traverse_height
    traverse = "unknown" if height is None else f"{height} mm"
    lines = [
      f"[Hamilton Prep] Connected on {self.describe_link()}\n",
      f"  Serial: {c.serial_number or 'unknown'}\n",
      f"  Firmware: {c.firmware_version or 'unknown'}\n",
      f"  Configuration: \n - enclosure {'installed' if c.has_enclosure else 'none'}, "
      f" - safe speeds {'on' if c.safe_speeds_enabled else 'off'},\n - traverse height {traverse}",
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

  async def _return_or_discard_attached(self, tips: List[bool]) -> None:
    """Put back or discard whatever the device was already holding when this session connected.

    A tool goes back in its holder; anything else goes into the waste. What it was is read from the
    firmware, which keeps it across a restart while the model knows nothing.

    Args:
      tips: whether each pipette senses something on it.
    """
    if self.pipettes is None:
      return
    carrying = [channel for channel, held in enumerate(tips) if held]
    attached = await self.pipettes.request_attached_tip_information(carrying[0])
    if attached is None:
      return
    logger.warning(
      "something is still attached to %s: the firmware holds it as "
      "%r, id %d, %.1f mm below the stop disc, %.1f uL, needle=%s, tool=%s",
      channels_named(carrying),
      attached.label,
      attached.id,
      attached.length,
      attached.volume,
      attached.is_needle,
      attached.is_tool,
    )
    if attached.is_tool and self.core_grippers is not None:
      logger.warning("it is a tool, so it goes back in its holder rather than into the waste")
      try:
        # Nothing parked to put back: only a tool on a channel to let go of.
        await self.core_grippers.drop_tools()
        return
      except HoiError as e:
        # A refused `PickUpTool` leaves its definition behind while the tips stay on: the device
        # then says no tool is held, and what is on is treated as tips.
        if not _refused_with(e, 0x0F07):
          raise
        logger.warning(
          "the device holds no tool: the definition is stale, so it is treated as tips"
        )
    elif attached.is_tool:
      logger.warning("it is a tool, but there is nothing here to put it back with")
      return
    waste = self.deck.waste_block if isinstance(self.deck, PrepDeck) else None
    if waste is None:
      logger.warning("this deck has no waste, so it stays on: take it off before running anything")
      return
    logger.warning("discarding it into the waste")
    # The model holds nothing at setup and a drop needs it to, so it adopts what the firmware
    # describes. The definition gives how far the tip reaches below the stop disc, not its fitting
    # depth, which is 8 mm for every Hamilton tip but the 5 mL family's 10.
    traverse = self.pipettes.default_minimum_traverse_height
    for channel in carrying:
      # A model that already knows keeps what it has; a fresh one adopts what the firmware describes.
      # One tip per channel: a resource has one parent, so a shared one would leave all but the last.
      if self.pipettes.get_mounted_tip(channel) is None:
        # The Z window the device reports is shifted by what is physically on; the definition can
        # be another thing's. The window wins where the two disagree.
        length = attached.length
        windows = self.pipettes.configuration.channels
        window = windows[channel].z_range if channel < len(windows) else None
        if window is not None:
          physical = traverse - window[1]
          if physical > 0.5 and abs(physical - length) > 0.5:
            logger.warning(
              "channel %d's window says %.1f mm below the stop disc, its definition %.1f: the window",
              channel,
              physical,
              length,
            )
            length = physical
        name = f"attached at setup ({attached.label}) on channel {channel}"
        found = _hamilton_tip_for_reach(length, attached.has_filter, name) or HamiltonTip(
          name=name,
          has_filter=attached.has_filter,
          size_z=length + TIP_FITTING_DEPTH,
          maximal_volume=attached.volume,
          nominal_volume=attached.volume,
          tip_size=TipSize.STANDARD_VOLUME,
          pickup_method=TipPickupMethod.OUT_OF_RACK,
        )
        self.pipettes._mount_tip(channel, found)
    try:
      await self.pipettes.drop_tips([waste] * len(carrying), use_channels=carrying)
    except (HoiError, ValueError) as e:
      # Last resort: the Pipettor forgets what it holds across a power cycle and refuses to drop it
      # (0x0F03), computes its Z from a stale definition (0x0F06), or what is on matches no tip
      # whose collar height a drop can be planned from. The squeeze drive lets go regardless.
      logger.warning("the device refused to drop it (%s): releasing it over the waste instead", e)
      await self._release_attached_over_waste(carrying)

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
    spot = self.deck.teaching_needle_spot
    if spot is not None:
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
      waste = self.deck.waste_positions.get(name) if name is not None else None
      if waste is None:
        continue
      waste.location = Coordinate(
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
    # The arm rides at the height it was measured at, not at the top of the channels' travel: what
    # the channels report is how far they travel, not where the arm sits.
    z = c.ride_height
    # The Y the channels reach between them, which the arm's reference line spans.
    y_ranges = [c.y_range for c in pipettes.channels if c.y_range is not None]
    reach = (min(r[0] for r in y_ranges), max(r[1] for r in y_ranges)) if y_ranges else None
    arm.resource = self.deck.get_or_create_x_arm(
      name=self.get_component_name("x_arm"),
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
      name = self.get_component_name(f"pipette_channel_{channel}")
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
  def core_gripper_holder(self) -> Optional[HamiltonCoreGrippers]:
    """The holder this Prep's deck parks its CO-RE grip tools in, by type, or None."""
    if self.deck is None:
      return None
    return next(
      (r for r in self.deck.get_all_children() if isinstance(r, HamiltonCoreGrippers)), None
    )

  @property
  def core_grippers_mounted(self) -> bool:
    """Whether the CoRe gripper tools are on the channels."""
    return self.core_grippers is not None and self.core_grippers.tools_mounted

  # ----------------------------------------
  # Park and spread
  # ----------------------------------------

  async def park_device(self) -> None:
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

  async def request_deck_light_installed(self) -> bool:
    """Request whether this device has a deck light, by whether its firmware declares one.

    What the light stands at, and how to set it, is `lights`, built at setup if this answers True.
    """
    try:
      await self.request_method_by_name(MLPREP_OBJECT_PATH, "SetDeckLight")
    except (RuntimeError, PrepMethodNotFoundError):
      return False
    return True

  # ----------------------------------------
  # Error lighting
  # ----------------------------------------

  async def signal_error(self, duration: Optional[float] = 8.0, wait: bool = False) -> None:
    """Pulse the deck red, if this device has a light to say it with.

    It says that something went wrong, and is not part of what went wrong: it never raises, and a
    device with no light does nothing at all.

    Args:
      duration: how many seconds to pulse for, or None to pulse until the light is turned off or
        set to something else, leaving the deck lit after the error.
      wait: hold until the pulse is over and the deck is dark. The pulse otherwise runs in the
        background, which is what a notebook or a running protocol wants; a script that exits the
        moment it raises takes its event loop with it, and needs this to show anything.

    Raises:
      ValueError: If `wait` is asked of a pulse with no duration, which would never return.
    """
    if wait and duration is None:
      raise ValueError("a pulse with no duration never ends, so it cannot be waited on")
    if self.lights is None:
      return
    try:
      await self.lights.animate_error_pulse(duration=duration)
      if wait:
        await self.lights.wait_for_animation()
    except Exception:
      logger.debug("could not signal an error on the deck light", exc_info=True)

  @asynccontextmanager
  async def error_lighting(
    self, duration: Optional[float] = 8.0, wait: bool = False
  ) -> AsyncIterator[None]:
    """Pulse the deck red when something inside raises, and let it raise on.

    Nothing is caught or hidden: the error carries on to whoever was going to handle it.

    Args:
      duration: how many seconds to pulse for, or None to leave the deck pulsing after the error,
        until the light is turned off or set to something else.
      wait: as `signal_error`.
    """
    try:
      yield
    except Exception:
      await self.signal_error(duration=duration, wait=wait)
      raise

  # ----------------------------------------
  # Speed scales
  # ----------------------------------------

  async def request_x_speed_scale(self) -> int:
    """Request how fast MLPrep drives X, as a percentage of its full speed."""
    return int((await self.send_command(PrepCmd.PrepGetXSpeedScale())).value)

  async def request_z_speed_scale(self) -> int:
    """Request how fast MLPrep drives Z, as a percentage of its full speed."""
    return int((await self.send_command(PrepCmd.PrepGetZSpeedScale())).value)

  async def _set_safe_speeds_enabled(self, enabled: bool) -> None:
    """Switch MLPrep's safe speeds on or off. Stays set until changed.

    On, the device holds the Z drives to a low acceleration and raises the X jerk; `discover` reads
    the state into the configuration. The command's id is read from the method table by name.

    Args:
      enabled: True for safe speeds.
    """
    mlprep = self.mlprep_address
    method = await self.request_method_by_name(mlprep, "SetSafeSpeedsEnabled")
    await self.send_command(
      PrepCmd.PrepSetSafeSpeedsEnabled(
        dest=mlprep, command_id=method.method_id, interface_id=method.interface_id, value=enabled
      )
    )

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
