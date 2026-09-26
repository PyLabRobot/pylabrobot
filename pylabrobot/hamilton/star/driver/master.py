"""The STAR master module, responsible for
- carrying the connection & transport,
- firmware protocol generation and parsing,
- orchestrating higher level tasks.
"""

import asyncio
import dataclasses
import datetime
import json
import logging
from typing import Any, Dict, FrozenSet, List, Literal, Optional, Tuple, Union, cast, overload

from pylabrobot.events import emit_event
from pylabrobot.hamilton.protocol.text.framing import (
  assemble_channel_command,
  parse_firmware_version_date,
  parse_fw_string,
)
from pylabrobot.hamilton.protocol.text.router import ReplyRouter
from pylabrobot.hamilton.star.driver.configuration import (
  DeviceConfiguration,
  read_configuration,
)
from pylabrobot.hamilton.star.driver.errors import (
  STAR_MODULE_ID_LENGTH,
  check_fw_string_error,
)
from pylabrobot.hamilton.star.driver.features.autoload import Autoload
from pylabrobot.hamilton.star.driver.features.cover import FrontCover
from pylabrobot.hamilton.star.driver.features.head96 import Head96
from pylabrobot.hamilton.star.driver.features.head384 import Head384
from pylabrobot.hamilton.star.driver.features.iswap import iSWAP, iSWAPConfiguration
from pylabrobot.hamilton.star.driver.features.pipettes import Pipettes
from pylabrobot.hamilton.star.driver.features.x_arm import XArm, XArmConfiguration
from pylabrobot.hamilton.star.driver.lock import _FirmwareLock
from pylabrobot.hamilton.star.resource_model import (
  ELBOW_DRIVE_COLUMN_ABOVE_REPORTED_Z,
  iswap_gripper,
  iswap_head,
  iswap_link_1,
  iSWAPHead,
)
from pylabrobot.hamilton.star.resource_model import head96 as head96_pipette
from pylabrobot.hamilton.star.resource_model import head384 as head384_pipette
from pylabrobot.io.io import IOBase
from pylabrobot.io.usb import USB
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.end_effector import MechanicalGripper
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonDeck
from pylabrobot.resources.hamilton.star_decks import HamiltonSTARDeck
from pylabrobot.resources.hamilton.tip_creators import HamiltonTip, TipPickupMethod, TipSize
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.n_channel_pipettes import NChannelPipette
from pylabrobot.resources.resource import Resource
from pylabrobot.serializer import serialize

logger = logging.getLogger(__name__)

# What the firmware's tip type table calls the CO-RE grip tool (cat. 186100).
CORE_GRIPPER_TIP_TYPE_INDEX = 14

# What a declaration and a device have to agree on for the one to stand for the other: what is
# fitted and how much of it. Everything else is either identity, which is the device's own, or
# geometry, which follows from what is fitted.
_DECLARATION_MUST_MATCH = frozenset(
  {
    "num_pip_channels",
    "instrument_size_slots",
    "autoload_installed",
    "kb_iswap_installed",
    "head96_installed",
    "head384_installed",
    "main_front_cover_monitoring_installed",
  }
)

ID_VENDOR = 0x08AF
ID_PRODUCT = 0x8000


def _range(values: Optional[Tuple[float, float]]) -> str:
  """A `(low, high)` range in mm, or a note that it was not resolved."""
  return "unresolved" if values is None else f"{values[0]} to {values[1]} mm"


class STARDriver:
  """Interface for the Hamilton STARDriver."""

  def _deck_component_name(self, name: str) -> str:
    """Resolve a built-in deck resource without changing its existing name."""
    if isinstance(self.deck, HamiltonSTARDeck):
      return self.deck.get_component_name(name)
    return name

  def _component_name(self, name: str) -> str:
    """Name discovered components after the device carrying the deck, or the standalone deck."""
    if self.deck is not None and self.deck.parent is not None:
      device = self.deck.parent
      if device.category == "device":
        return f"{device.name}_{name}"
    return self._deck_component_name(name)

  def __init__(
    self,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    deck: Optional[HamiltonDeck] = None,
    declared_configuration_json: Optional[str] = None,
    packet_read_timeout: int = 3,
    write_timeout: int = 30,
    read_timeout: int = 120,
    left_side_panel_installed: bool = False,
    io: Optional[IOBase] = None,
  ):
    """Create a new STAR interface.

    Args:
      device_address: the USB device address of the Hamilton STAR. Only useful if using more than
        one Hamilton device over USB.
      serial_number: the serial number of the Hamilton STAR. Only useful if using more than one
        Hamilton device over USB.
      deck: the deck to reflect the device into. Optional: without one the driver still drives the
        device, and nothing about where things are is modelled.
      declared_configuration_json: path to a JSON file holding the declared configuration for the
        device, as `save_configuration` writes one. The only way a configuration is read from a
        file. If given, (1) against a physical device, discovery cross-checks the declaration
        against what the device answers, (2) in simulation, the device answers as the declaration
        says instead of from the simulation default.
      packet_read_timeout: timeout in seconds for reading a single packet.
      read_timeout: timeout in seconds for reading a full response.
      write_timeout: timeout in seconds for writing a command.
      left_side_panel_installed: whether the device has its left side panel on. Declared, not
        read: it comes off in seconds, and the reported travel range does not follow it. With one
        fitted, an arm carrying a head stops while the head is still clear of it.
      io: an already-built USB handle to use instead of opening one from the arguments above.
    """

    self.io: IOBase = io or USB(
      human_readable_device_name=f"Hamilton {'STAR'}",
      id_vendor=ID_VENDOR,
      id_product=ID_PRODUCT,
      device_address=device_address,
      write_timeout=write_timeout,
      serial_number=serial_number,
    )

    # Coordinates commands on the shared link: one at a time per module, and a C0 master
    # command alone. Read-only requests are exempt, see `send_command`.
    self._lock = _FirmwareLock()
    self._replies = ReplyRouter(
      io=self.io,
      module_id_length=STAR_MODULE_ID_LENGTH,
      parse_id=self.get_id_from_fw_response,
      raise_for_error=check_fw_string_error,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
    )

    # What was declared this device is, read once here: the one place a configuration comes off a
    # file. Empty when nothing was declared.
    self.declared_configuration_json = declared_configuration_json
    self.declared: Dict[str, Any] = (
      {} if declared_configuration_json is None else read_configuration(declared_configuration_json)
    )

    self._num_channels: Optional[int] = None

    self._connected = False

    self.left_side_panel_installed = left_side_panel_installed

    # The deck to reflect the device into, or None to drive it without a resource model. With one,
    # setup builds a resource per feature as a child of it; without, nothing is modelled and
    # driver functionality is limited due to lack of information available.
    self.deck = deck

    self.configuration: Optional[DeviceConfiguration] = None

    self.firmware: Dict[str, str] = {}
    # Which table index each tip type was written to. The table is volatile, so this is
    # rebuilt per session as tips are first used.
    self._tip_type_indices: Dict[str, int] = {}

    # Subsystems. Each reads what it needs off `configuration`, so they are usable once setup has
    # run and raise a clear error before that. Each arm appears only if setup finds one installed.
    self.front_cover: Optional[FrontCover] = None
    self.left_x_arm: Optional[XArm] = None
    self.right_x_arm: Optional[XArm] = None
    self.autoload: Optional[Autoload] = None

  # ----------------------------------------
  # Connection and lifecycle
  # ----------------------------------------

  async def setup(
    self,
    skip_device_initialization: bool = False,
    skip_pipettes: bool = False,
    skip_iswap: bool = False,
    skip_head96: bool = False,
    skip_head384: bool = False,
    skip_autoload: bool = False,
  ):
    """Connect to the device, find out what it is, and bring it up.

    This moves the device: everything that can be initialized is. `discover` is the read-only
    part; it reads the device without moving it, and needs the link already open.

    Repeatable. Discovery re-reads the device, and initialization does nothing on a device that
    is already up. A setup that fails part way closes the link.

    Every argument only ever does less. Each leaves that feature exactly as the device had it -
    which for one that reports itself down means its drives keep no reference, and it will refuse
    to move until something initializes it. Discovery still reads it either way, so what is
    skipped is the moving, not the finding out.

    Args:
      skip_device_initialization: do not run the device's own initialization procedure on a device
        that reports itself down. A device that reports itself up is still raised to Z safety,
        which is not motion this can decline: nothing may travel laterally while a channel is low.
      skip_pipettes: do not initialize the channels.
      skip_iswap: do not initialize or park the iSWAP.
      skip_head96: do not initialize the 96-head.
      skip_head384: do not initialize the 384-head.
      skip_autoload: do not initialize or park the autoload.
    """
    skipped = frozenset(
      name
      for name, skip in (
        ("pipettes", skip_pipettes),
        ("iswap", skip_iswap),
        ("head96", skip_head96),
        ("head384", skip_head384),
      )
      if skip
    )
    logger.debug("Setting up STAR on %s ...", self._describe_link())
    # A repeated setup goes over the link the first one opened: opening it again would start a
    # second thread reading the same replies.
    if not self._connected:
      await self._open()
      self._connected = True

    try:
      # 1. What is on the other end, and what does it carry?
      logger.debug("[PHASE 1] Discovery")
      await self.discover()

      # 2. Bring the device to a known state. The autoload homes alongside it: it is its own
      #    unit, and the device procedure holds every drive but its. Phase 3 reads its state
      #    again, so a device that does home it there loses nothing but the overlap.
      #    It runs alone. The autoload used to be brought up alongside it, to save the time the
      #    procedure takes, and that put a C0 command and an I0 command in flight together: on
      #    2026-09-04 the autoload answered one with the id of its own previous command, and the
      #    caller waited for a reply that had already arrived. Legacy runs the procedure by itself
      #    and gathers the modules afterwards; so does this.
      logger.debug("[PHASE 2] Device initialization")
      autoload = self.autoload if not skip_autoload else None
      already_initialized = await self.request_initialization_status()
      if skip_device_initialization and not already_initialized:
        logger.debug("device reports not initialized, and initializing it was skipped")
      else:
        already_initialized = await self.initialize()

      # 3. Each feature brings itself up. They sit on different modules, so they run together;
      #    the autoload, iSWAP and 96-head join this gather as they land. The channels only need
      #    it when the device procedure did not just run, or when something is still mounted.
      logger.debug("[PHASE 3] Feature initialization")
      initializing = [self._initialize_arm(arm, already_initialized, skipped) for arm in self.arms]
      if autoload is not None:
        initializing.append(autoload.initialize())
      await asyncio.gather(*initializing)

      # A command that answered is not a module that came up, so each is asked again. The channels
      # report no initialization of their own.
      down = [
        type(feature).__name__
        for feature in self.features
        if not isinstance(feature, Pipettes)
        and not await self.request_initialization_status(feature.configuration.module)
      ]
      if not await self.request_initialization_status():
        down.insert(0, "device")
      if down:
        logger.warning("setup finished with these not initialized: %s", ", ".join(down))

      # 4. What was found, as resources on the deck - when the driver was given one to reflect
      #    into. Each is a child of the deck, so a device with a deck carries one tree.
      if self.deck is not None:
        logger.debug("[PHASE 4] Feature resources")
        await self._create_capability_resources()

    except BaseException:
      await self.stop()
      raise

    logger.info("%s", self.format_setup_summary())

  async def _open(self):
    """Open the link and start reading replies."""
    await self.io.setup()
    self._replies.start()

  async def _close(self):
    """Stop reading replies and close the link."""
    self._replies.stop()
    await self.io.stop()

  async def features_below_safe_z(self, tolerance: float = 0.5) -> List[str]:
    """Which channels, heads, iSWAP and the autoload wheel report below where they are safe.

    Read back rather than taken on trust. A retract that answered without arriving leaves the
    device looking safe while a lateral move would drive whatever is still low into whatever is in
    the way, and the answer to the move command does not say where the drive stopped.

    A feature with no window probed is not judged: there is no height to hold it to.

    Args:
      tolerance: how far below the top of the window still counts as up, in mm.

    Returns:
      One entry per channel, head, arm or wheel that is low, naming it and where it says it is.
      Empty
      when everything is up, which is the only state anything may travel laterally in.
    """
    low: List[str] = []
    for arm in self.arms:
      pipettes = arm.pipettes
      if pipettes is not None and pipettes.configuration.z_range is not None:
        safe = pipettes.configuration.z_range[1]
        try:
          positions = await pipettes.request_stop_disc_z_positions()
        except Exception:
          low.append(f"{arm.side} channels (where they are could not be read)")
        else:
          low += [
            f"{arm.side} channel {channel} at {z:.1f} mm, safe is {safe:.1f} mm"
            for channel, z in enumerate(positions)
            if z < safe - tolerance
          ]
      for head, name in ((arm.head96, "head96"), (arm.head384, "head384")):
        if head is None:
          continue
        safe = head.configuration.z_range[1]
        try:
          z = await head.request_z_position()
        except Exception:
          low.append(f"{arm.side} {name} (where it is could not be read)")
        else:
          if z < safe - tolerance:
            low.append(f"{arm.side} {name} at {z:.1f} mm, safe is {safe:.1f} mm")
      iswap = arm.iswap
      if iswap is not None:
        safe = iswap.configuration.elbow_z_range[1]
        try:
          z = await iswap.elbow_request_z_position()
        except Exception:
          low.append(f"{arm.side} iSWAP (where it is could not be read)")
        else:
          if z < safe - tolerance:
            low.append(f"{arm.side} iSWAP at {z:.1f} mm, safe is {safe:.1f} mm")

    autoload = self.autoload
    if autoload is not None:
      try:
        at_safe_z = await autoload.wheel_is_at_safe_z(tolerance=tolerance)
      except Exception:
        low.append("autoload wheel (where it is could not be read)")
      else:
        if not at_safe_z:
          low.append("autoload wheel below its safe Z")
    return low

  async def stop(self):
    """Close the link, leaving the device safe to move laterally.

    The device keeps its state; only this driver lets go of it. Every channel, head and the
    autoload wheel are moved up to Z safety first: a driver that let go with any of them low
    would leave the next lateral move from anything else to crash it.

    An iSWAP parks after them, which retracts it, but only with empty fingers: one still holding a
    plate would carry it home and leave it wherever the fingers next let go.

    The link closes whether or not that succeeds. This also runs when setup failed part way,
    where there may be nothing up to move yet and the failure that matters is the one about to
    propagate.

    Repeatable. A device already let go of is left alone: retracting through a closed link
    fails on every subsystem, and the warnings that produces read exactly like a device that
    would not come up.
    """
    if not self._connected:
      logger.debug("the link is already closed; nothing to put down")
      return

    try:
      # The channels, each head and the autoload wheel take different firmware locks and drive
      # different Z axes, so they go up together rather than one after another. The wheel is on
      # its own unit and travels its own rail, but it is the same hazard: left down, it is what
      # the sled carries into whatever the sled next passes.
      safe_z_moves: List[Any] = []
      for arm in self.arms:
        if arm.pipettes is not None:
          safe_z_moves.append(arm.pipettes.move_to_safe_z())
        for head in (arm.head96, arm.head384):
          if head is not None:
            safe_z_moves.append(head.move_to_safe_z())
        # The arm is the same hazard as the rest: parking retracts it, and an arm left low is
        # driven through whatever it is over on the way home.
        if arm.iswap is not None:
          safe_z_moves.append(arm.iswap.elbow_move_to_safe_z_height())
      if self.autoload is not None:
        safe_z_moves.append(self.autoload.wheel_move_to_safe_z())

      # Every subsystem gets its chance to reach safe Z, so one that cannot does not leave the
      # rest of them low.
      results = await asyncio.gather(*safe_z_moves, return_exceptions=True)
      failed = [result for result in results if isinstance(result, BaseException)]
      for failure in failed:
        logger.warning("could not move a subsystem to Z safety", exc_info=failure)

      # Asked, not assumed: a move that answers has not said where it stopped, and this is the
      # one moment the answer decides whether anything may travel laterally.
      low = await self.features_below_safe_z()
      if low:
        logger.warning(
          "not everything is at Z safety, so the iSWAP stays where it is: %s", "; ".join(low)
        )

      # The iSWAP parks instead: parking retracts it, which is lateral motion, so it waits until
      # everything sharing its arm is up. If anything is still low, it stays where it is.
      if not failed and not low:
        parks = []
        for arm in self.arms:
          if arm.iswap is None:
            continue
          # Asked, not assumed, and asked of the arm rather than the model: parking closes the
          # gripper and retracts it, so an arm still holding a plate carries it home and leaves it
          # wherever the fingers next let go. An arm that cannot say counts as holding something.
          try:
            holding = await arm.iswap.request_plate_gripped()
          except Exception:
            logger.warning(
              "could not read whether the %s iSWAP is holding a plate, so it stays where it is",
              arm.side,
              exc_info=True,
            )
            continue
          if holding:
            logger.warning("the %s iSWAP is holding a plate, so it stays where it is", arm.side)
            continue
          parks.append(arm.iswap.park())
        for failure in await asyncio.gather(*parks, return_exceptions=True):
          if isinstance(failure, BaseException):
            logger.warning("could not park the iSWAP", exc_info=failure)

      # And the deck goes dark. An indicator left lit outlives the session that lit it - it says a
      # carrier is being handled by software that is no longer there. On its own path, and its own
      # failure: a deck that will not go dark is not a reason to hold the link open.
      if self.autoload is not None:
        try:
          await self.autoload.clear_loading_indicators()
        except Exception:
          logger.warning("could not put the loading indicators out", exc_info=True)
    except Exception:
      logger.warning(
        "could not bring the device to a safe state; closing the link anyway", exc_info=True
      )
    finally:
      self._connected = False
      # The device's tip type table is volatile, so what this session wrote to it does not
      # survive the device going down.
      self._tip_type_indices.clear()
      await self._close()

  @property
  def connected(self) -> bool:
    """Whether the link is open. Commands can be sent only while it is."""
    return self._connected

  def _describe_link(self) -> str:
    """How this device is reached, in whatever terms its transport is addressed by."""
    fields = self.io.serialize()
    link = type(self.io).__name__
    vendor, product = fields.get("id_vendor"), fields.get("id_product")
    if vendor is not None and product is not None:
      link += f" {vendor:#06x}:{product:#06x}"
    named = [
      f"{label} {fields[key]}"
      for label, key in (
        ("address", "device_address"),
        ("serial", "serial_number"),
        ("port", "port"),
      )
      if fields.get(key)
    ]
    return link + (f" ({', '.join(named)})" if named else "")

  # ----------------------------------------
  # Low-level I/O
  # ----------------------------------------

  @overload
  async def send_command(
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    *,
    fmt: Any,
    subsystem: Optional[str] = None,
    **kwargs: Any,
  ) -> Dict[str, Any]: ...

  @overload
  async def send_command(
    self,
    module: str,
    command: str,
    auto_id: bool = True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    fmt: None = None,
    subsystem: Optional[str] = None,
    **kwargs: Any,
  ) -> str: ...

  async def send_command(
    self,
    module: str,
    command: str,
    auto_id=True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    fmt: Optional[Any] = None,
    subsystem: Optional[str] = None,
    **kwargs,
  ):
    """Assemble a firmware command, send it, and parse the reply if a format is given.

    Modules share one physical link between control PC and the device but due to the STAR's 
    architecture containing multiple control boards, for different drives, we can parallelise
    certain subsystems.
    To prevent collisions, we use a lock system inside this method which ensures that only 
    compatible commands are sent in parallel.
    
    A command addressed to C0 can drive a subsystem of its own, e.g. `C0 DI` the channels and 
    `C0 II` the autoload, enabling parallelisation of known orthogonal C0 commands.\
    A C0 command naming no subsystem explicitly is assumed to not allow parallelisation and
    runs alone.
    Read-only `R` and `Q` commands take no lock.

    Args:
      module: the module to address the command to.
      command: the two-letter command code.
      subsystem: which subsystem the command drives, when that is not the module it is addressed
        to. Only a C0 command needs it.

    Returns:
      The parsed reply when a format was given, and the raw reply otherwise.

    Raises:
      RuntimeError: If the link is not open.
    """
    kwargs_ = dict(
      auto_id=auto_id,
      tip_pattern=tip_pattern,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      fmt=fmt,
      **kwargs,
    )
    if command[0] in ("R", "Q"):
      return await self._send(module, command, **kwargs_)
    key = subsystem or (_FirmwareLock.EVERY_SUBSYSTEM if module == "C0" else module)
    async with self._lock.subsystem(key):
      return await self._send(module, command, **kwargs_)

  async def _send(
    self,
    module: str,
    command: str,
    auto_id=True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    fmt: Optional[Any] = None,
    **kwargs,
  ):
    """Assemble, send and parse, without coordinating against anything else in flight."""
    self._require_connection()
    id_ = self._replies.next_id() if auto_id else None
    # Always the channel-aware assembler: a list parameter has to be terminated against the
    # device's channel count whether or not the caller named which channels are involved, and
    # `tip_pattern=None` means each list already holds one value per channel it names.
    #
    # The count is only read when there is a list to terminate. Discovery has to send commands
    # before it knows the count, and asking for it there would refuse the reads that establish it.
    carries_a_list = any(isinstance(value, list) for value in kwargs.values())
    cmd = assemble_channel_command(
      module=module,
      command=command,
      id_=id_,
      tip_pattern=tip_pattern,
      num_channels=self.num_channels if carries_a_list else 0,
      **kwargs,
    )
    event_data = {
      "transport": "hamilton_usb",
      "driver": type(self).__name__,
      "module": module,
      "command": command,
      "command_id": id_,
      "raw_command": cmd,
    }
    emit_event("firmware.command.started", **event_data)
    try:
      resp = cast(
        str,
        await self._replies.send(
          cmd=cmd,
          id_=id_,
          write_timeout=write_timeout,
          read_timeout=read_timeout,
        ),
      )
      result = self._parse_response(resp, fmt) if fmt is not None else resp
    except BaseException as error:
      emit_event(
        "firmware.command.failed",
        **event_data,
        error_type=type(error).__name__,
        error_message=str(error),
      )
      raise
    emit_event("firmware.command.completed", **event_data, response=resp)
    return result

  async def send_raw_command(
    self,
    command: str,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait: bool = True,
  ) -> Optional[str]:
    """Send a raw command to the device.

    Returns:
      Whatever the device answered, or None when nothing came back.

    Raises:
      RuntimeError: If the link is not open.
    """
    self._require_connection()
    return await self._replies.send_raw(
      command=command,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      wait=wait,
    )

  def _require_connection(self) -> None:
    """Raise unless the link is open. A command may not be sent into a closed link."""
    if not self._connected:
      raise RuntimeError("not connected to a device; call `setup` first")

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """Get the id from a firmware response."""
    parsed = parse_fw_string(resp, "id####")
    if "id" in parsed and parsed["id"] is not None:
      return int(parsed["id"])
    return None

  def _parse_response(self, resp: str, fmt: Any) -> Dict[str, Any]:
    """Parse a response from the device."""
    return parse_fw_string(resp, fmt)

  # ----------------------------------------
  # What the arms carry
  # ----------------------------------------

  @property
  def arms(self) -> List[XArm]:
    """The arms this device has, left first."""
    return [arm for arm in (self.left_x_arm, self.right_x_arm) if arm is not None]

  @property
  def features(self) -> List[Union[Pipettes, Head96, Head384, iSWAP, Autoload]]:
    """Every feature this device is fitted with: each arm's channels, heads and iSWAP, then the
    autoload."""
    features: List[Union[Pipettes, Head96, Head384, iSWAP, Autoload]] = []
    for arm in self.arms:
      for feature in (arm.pipettes, arm.head96, arm.head384, arm.iswap):
        if feature is not None:
          features.append(feature)
    if self.autoload is not None:
      features.append(self.autoload)
    return features

  def _require_one_arm(self, reaching_for: str) -> Optional[XArm]:
    """Check to enable simple accessors which are only unambiguous when there is only one Xarm.

    e.g.:
      `star.head96` is ambiguous on a device with two arms.
      -> requires explicit declaration of the arm that has the head:
      `star.left_x_arm.head96` or `star.right_x_arm.head96`.

    Args:
      reaching_for: what the caller was after. Used to word the refusal.

    Returns:
      The device's one arm, or None when it has none.

    Raises:
      ValueError: If the device has more than one arm.
    """
    arms = self.arms
    if len(arms) > 1:
      raise ValueError(
        f"this device has two X-arms, so `{reaching_for}` is ambiguous - reach it through "
        f"`left_x_arm.{reaching_for}` or `right_x_arm.{reaching_for}`."
      )
    return arms[0] if arms else None

  @property
  def pipettes(self) -> Optional[Pipettes]:
    """The pipetting channels, on a device with one arm."""
    arm = self._require_one_arm("pipettes")
    return arm.pipettes if arm is not None else None

  @property
  def head96(self) -> Optional[Head96]:
    """The 96-head, on a device with one arm."""
    arm = self._require_one_arm("head96")
    return arm.head96 if arm is not None else None

  @property
  def head384(self) -> Optional[Head384]:
    """The 384-head, on a device with one arm."""
    arm = self._require_one_arm("head384")
    return arm.head384 if arm is not None else None

  @property
  def iswap(self) -> Optional[iSWAP]:
    """The iSWAP, on a device with one arm."""
    arm = self._require_one_arm("iswap")
    return arm.iswap if arm is not None else None

  @property
  def x_arm(self) -> XArm:
    """The device's X-arm, on a device that has only one.

    Most STARs carry a single arm, where explicit naming can be cumbersome.
    A device with two has no single X-arm, and this refuses instead of picking one.

    Returns:
      The device's one arm.

    Raises:
      RuntimeError: If setup has not run, so it is not yet known which arms are installed.
      ValueError: If the device has no arm, or more than one.
    """
    if self.configuration is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")
    installed = {
      name: arm
      for name, arm in (("left_x_arm", self.left_x_arm), ("right_x_arm", self.right_x_arm))
      if arm is not None
    }
    if not installed:
      raise ValueError("this device reports no X-arm installed.")
    if len(installed) > 1:
      raise ValueError(
        f"this device has {len(installed)} X-arms ({', '.join(installed)}), so `x_arm` is "
        f"ambiguous. Use the one you mean by name."
      )
    return next(iter(installed.values()))

  @property
  def num_channels(self) -> int:
    """The number of pipette channels present on the robot."""
    if self._num_channels is None:
      raise RuntimeError("channel count not read; have you called `star.setup()`?")
    return self._num_channels

  # ----------------------------------------
  # Device queries
  # ----------------------------------------

  async def request_device_serial_number(self) -> str:
    """Request what the device calls itself.

    Not the USB serial this driver may have picked the device off the bus with: that identifies a
    handle, this identifies the device answering on it.

    Returns:
      The serial number the device answers.
    """
    # One `sn` field, not the two the older driver named: a repeated name parses to the first
    # match, so the second was inert there and would be here. Whether the device answers a serial
    # in two four-character halves is unsettled - if it does, this reads the first half only, and a
    # reading off a device is what will say.
    resp = await self.send_command(module="C0", command="RI", fmt="si####sn&&&&")
    return cast(str, resp["sn"])

  async def request_firmware_version(self) -> Tuple[str, datetime.date]:
    """Request the master's firmware version and build date.

    Returns:
      The version string and its build date, e.g. `("7.6S", date(2021, 11, 5))`.
    """
    resp = await self.send_command(module="C0", command="RF")
    return resp.split("rf")[-1], parse_firmware_version_date(resp)

  async def request_cover_input_status(self) -> Tuple[bool, bool, bool]:
    """Read the three inputs on the cover connector.

    An device-level query about the cover feature. `front_cover.request_position` reports the
    cover's own position.

    TODO: establish what each input carries. Every reading so far is 000, except during a run
    where the operator closed the cover between two reads and it went 000 -> 100.

    Returns:
      Whether each of the three inputs is set.

    Raises:
      ValueError: If the response contains fewer than three inputs.
    """
    resp = await self.send_command(module="C0", command="RW")
    read = resp.split("rw", 1)[-1].strip().strip("'")
    if len(read) < 3:
      raise ValueError(f"expected three inputs in the reply: {resp!r}")
    return read[0] == "1", read[1] == "1", read[2] == "1"

  async def request_maximal_ranges_of_x_drives(self) -> Dict[str, Tuple[float, float]]:
    """Request the maximal travel range of each X drive.

    Returns:
      The `(minimum, maximum)` X position in mm each drive can reach, keyed by side:
      `{"left": (min, max), "right": (min, max)}`.
    """
    resp = await self.send_command(module="C0", command="RU")
    values = [int(v) / 10 for v in resp.split("ru")[-1].strip().split()]
    left_min, left_max, right_min, right_max = values
    return {"left": (left_min, left_max), "right": (right_min, right_max)}

  async def request_working_envelopes_per_arm(
    self,
  ) -> Dict[str, Tuple[float, Tuple[float, float]]]:
    """Request the working envelope of each installed arm.

    Returns:
      Per side, `(wrap_size, (workspace_min, workspace_max))` in mm, keyed by side. A
      `wrap_size` of 0 means that arm is not installed.
    """
    resp = await self.send_command(module="C0", command="UA", subsystem="C0")
    values = [int(v) / 10 for v in resp.split("ua")[-1].strip().split()]
    left_wrap, right_wrap, left_min, left_max, right_min, right_max = values
    return {
      "left": (left_wrap, (left_min, left_max)),
      "right": (right_wrap, (right_min, right_max)),
    }

  async def request_device_configuration(self) -> DeviceConfiguration:
    """Request the device's installed hardware and geometry.

    Combines the device configuration (RM) and the extended configuration (QM). Each installed
    X-drive's geometry is resolved from the X-drive range (RU) and working-envelope (UA) queries;
    `right_arm` is None when no second arm is installed.

    Returns:
      What the device reports it carries.
    """
    device = await self.send_command(module="C0", command="RM", fmt="kb**kp##")
    extended = await self.send_command(
      module="C0",
      command="QM",
      fmt="ka******ke********xt##xa##xw#####xl**xn**xr**xo**xm#####xx#####xu####xv####kc#kr#"
      + "ys###kl###km###ym####yu####yx####",
    )

    ranges = await self.request_maximal_ranges_of_x_drives()
    wraps = await self.request_working_envelopes_per_arm()

    def _resolve_arm(
      byte1: int, byte2: int, side: Literal["left", "right"], width: float, large: bool
    ) -> Optional[XArmConfiguration]:
      wrap, workspace_x_range = wraps[side]
      if wrap == 0:  # arm not installed
        return None
      answered = XArmConfiguration(
        pip_installed=bool(byte1 & (1 << 0)),
        iswap_installed=bool(byte1 & (1 << 1)),
        head96_installed=bool(byte1 & (1 << 2)),
        nano_pipettor_installed=bool(byte1 & (1 << 3)),
        head384_installed=bool(byte1 & (1 << 4)),
        xl_channels_installed=bool(byte1 & (1 << 5)),
        tube_gripper_installed=bool(byte1 & (1 << 6)),
        imaging_channel_installed=bool(byte1 & (1 << 7)),
        robotic_channel_installed=bool(byte2 & (1 << 0)),
        gel_card_gripper_installed=bool(byte2 & (1 << 1)),
        puncher_handler_installed=bool(byte2 & (1 << 2)),
        width=width,
        large=large,
        x_range=ranges[side],
        workspace_x_range=workspace_x_range,
        wrap_size=wrap,
      )
      # Only the fields above come off the device. The rest are device facts, which nothing
      # reports and a caller may have corrected for an arm this driver was not recorded against,
      # so they are carried over rather than reset to what this generation documents. Every other
      # feature keeps its configuration across a re-read because the object survives; an arm's is
      # rebuilt here, so what was set on it is carried by hand.
      if self.configuration is None:
        return answered
      carried = self.configuration.left_arm if side == "left" else self.configuration.right_arm
      return answered if carried is None else answered.with_device_facts_of(carried)

    kb = device["kb"]
    ka = extended["ka"]
    return DeviceConfiguration(
      pip_type_1000ul=bool(kb & (1 << 0)),
      kb_iswap_installed=bool(kb & (1 << 1)),
      main_front_cover_monitoring_installed=bool(kb & (1 << 2)),
      autoload_installed=bool(kb & (1 << 3)),
      wash_station_1_installed=bool(kb & (1 << 4)),
      wash_station_2_installed=bool(kb & (1 << 5)),
      temp_controlled_carrier_1_installed=bool(kb & (1 << 6)),
      temp_controlled_carrier_2_installed=bool(kb & (1 << 7)),
      num_pip_channels=device["kp"],
      left_x_drive_large=bool(ka & (1 << 0)),
      head96_installed=bool(ka & (1 << 1)),
      right_x_drive_large=bool(ka & (1 << 2)),
      pump_station_1_installed=bool(ka & (1 << 3)),
      pump_station_2_installed=bool(ka & (1 << 4)),
      wash_station_1_type_cr=bool(ka & (1 << 5)),
      wash_station_2_type_cr=bool(ka & (1 << 6)),
      left_cover_installed=bool(ka & (1 << 7)),
      right_cover_installed=bool(ka & (1 << 8)),
      additional_front_cover_monitoring_installed=bool(ka & (1 << 9)),
      pump_station_3_installed=bool(ka & (1 << 10)),
      multi_channel_nano_pipettor_installed=bool(ka & (1 << 11)),
      head384_installed=bool(ka & (1 << 12)),
      xl_channels_installed=bool(ka & (1 << 13)),
      tube_gripper_installed=bool(ka & (1 << 14)),
      waste_direction_left=bool(ka & (1 << 15)),
      iswap_gripper_wide=bool(ka & (1 << 16)),
      additional_channel_nano_pipettor_installed=bool(ka & (1 << 17)),
      imaging_channel_installed=bool(ka & (1 << 18)),
      robotic_channel_installed=bool(ka & (1 << 19)),
      channel_order_ox_first=bool(ka & (1 << 20)),
      x0_interface_ham_can=bool(ka & (1 << 21)),
      park_heads_with_iswap_off=bool(ka & (1 << 22)),
      configuration_data_3=extended["ke"],
      instrument_size_slots=extended["xt"],
      autoload_size_slots=extended["xa"],
      tip_waste_x_position=extended["xw"] / 10,
      left_arm=_resolve_arm(
        extended["xl"], extended["xn"], "left", extended["xu"] / 10, bool(ka & (1 << 0))
      ),
      right_arm=_resolve_arm(
        extended["xr"], extended["xo"], "right", extended["xv"] / 10, bool(ka & (1 << 2))
      ),
      min_iswap_collision_free_position=extended["xm"] / 10,
      max_iswap_collision_free_position=extended["xx"] / 10,
      left_x_arm_width=extended["xu"] / 10,
      right_x_arm_width=extended["xv"] / 10,
      num_xl_channels=extended["kc"],
      num_robotic_channels=extended["kr"],
      min_raster_pitch_pip_channels=extended["ys"] / 10,
      min_raster_pitch_xl_channels=extended["kl"] / 10,
      min_raster_pitch_robotic_channels=extended["km"] / 10,
      pip_maximal_y_position=extended["ym"] / 10,
      left_arm_min_y_position=extended["yu"] / 10,
      right_arm_min_y_position=extended["yx"] / 10,
    )

  async def request_initialization_status(self, module: str = "C0") -> bool:
    """Whether a module reports itself initialized.

    Every module answers the same query: the master and each subsystem.

    Args:
      module: the module to ask. Defaults to the master, which reports for the device.

    Returns:
      True if the module is initialized.
    """
    resp = await self.send_command(module=module, command="QW", fmt="qw#")
    return cast(int, resp["qw"]) == 1

  # ----------------------------------------
  # Tip types
  # ----------------------------------------

  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    has_filter: bool,
    tip_length: float,
    maximum_tip_volume: float,
    tip_size: TipSize,
    pickup_method: TipPickupMethod,
  ):
    """Write one entry of the device's tip type table.

    The table is volatile. It is written from scratch after every power on, and what a run defines
    lasts only while the device stays up.

    Args:
      tip_type_table_index: which entry to write, 1 to 99.
      has_filter: whether the tip has a filter.
      tip_length: how far the tip stands proud of what holds it, in mm. Its total length past its
        fitting depth.
      maximum_tip_volume: what the tip holds, in uL. The firmware caps it at the channel's
        capacity.
      tip_size: which collar the tip has, which is how the device identifies it.
      pickup_method: whether it is collected from a rack or out of wash liquid.
    Raises:
      ValueError: If an argument is outside what the command accepts.
    """
    length_increments = round(tip_length * 10)
    volume_increments = round(maximum_tip_volume * 10)
    if not 0 <= tip_type_table_index <= 99:
      raise ValueError(f"tip_type_table_index must be between 0 and 99, is {tip_type_table_index}")
    if not 1 <= length_increments <= 1999:
      raise ValueError(f"tip_length must be between 0.1 and 199.9 mm, is {tip_length}")
    if not 1 <= volume_increments <= 56000:
      raise ValueError(
        f"maximum_tip_volume must be between 0.1 and 5600.0 uL, is {maximum_tip_volume}"
      )

    return await self.send_command(
      module="C0",
      command="TT",
      subsystem="C0",
      tt=f"{tip_type_table_index:02}",
      tf=has_filter,
      tl=f"{length_increments:04}",
      tv=f"{volume_increments:05}",
      tg=tip_size.value,
      tu=pickup_method.value,
    )

  async def get_or_assign_tip_type_index(self, tip: HamiltonTip) -> int:
    """The table index this tip is defined at, defining it if it is new to this session.

    Every command that mounts tips names one of these indices, not the tip itself. A tip that has
    not been written into the table has no index to name.

    Args:
      tip: the tip to look up.

    Returns:
      Its index in the device's tip type table.

    Raises:
      ValueError: If the tip model is undefined or the table is full.
    """
    model = tip.model
    if model is None:
      raise ValueError("Tip model must be defined to assign a tip type index.")
    if model not in self._tip_type_indices:
      # The first free entry, skipping the grip tool's: a tip written there would overwrite it.
      taken = set(self._tip_type_indices.values()) | {CORE_GRIPPER_TIP_TYPE_INDEX}
      index = next((i for i in range(1, 100) if i not in taken), None)
      if index is None:
        raise ValueError(
          f"the tip type table is full: {len(self._tip_type_indices)} tip types have already "
          "been defined."
        )
      await self.define_tip_needle(
        tip_type_table_index=index,
        has_filter=tip.has_filter,
        tip_length=tip.get_size_z() - tip.fitting_depth,
        # Floored at 1.0 uL so a teaching or probe needle with no capacity registers the way the
        # firmware's own non-pipetting tools do. It does not affect pickup, which goes by length
        # and collar.
        maximum_tip_volume=max(tip.maximal_volume, 1.0),
        tip_size=tip.tip_size,
        pickup_method=tip.pickup_method,
      )
      self._tip_type_indices[model] = index
    return self._tip_type_indices[model]

  # ----------------------------------------
  # Discovery and initialization
  # ----------------------------------------

  def _check_declared_against(self, discovered: DeviceConfiguration) -> None:
    """Raise if what was declared cannot stand for what the device answered.

    Only what decides whether the two are the same kind of device: which features are fitted, how
    many channels, and what each arm carries. Identity is left out, since a declaration taken off
    one device describes another of the same build and its serial and firmware are its own. So is
    geometry, which follows from what is fitted.

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
    for side in ("left_arm", "right_arm"):
      declared_arm, discovered_arm = getattr(declared, side), getattr(discovered, side)
      if (declared_arm is None) != (discovered_arm is None):
        differences.append(
          f"{side}: declared {'an arm' if declared_arm else 'none'}, "
          f"device answers {'an arm' if discovered_arm else 'none'}"
        )
      elif declared_arm is not None and discovered_arm is not None:
        # What the arm carries, not how big it is: geometry is the frame's, and a declaration off
        # another frame of the same build is still a fair description of what is bolted on.
        differences += [
          f"{side}.{field.name}: declared {getattr(declared_arm, field.name)!r}, "
          f"device answers {getattr(discovered_arm, field.name)!r}"
          for field in dataclasses.fields(declared_arm)
          if field.name.endswith("_installed")
          and getattr(declared_arm, field.name) != getattr(discovered_arm, field.name)
        ]
    if differences:
      raise ValueError(
        "the declared configuration does not describe this device:\n  " + "\n  ".join(differences)
      )

  async def discover(self):
    """Read what device is on the other end, and build the subsystems it turns out to have.

    Read-only: nothing moves. Call `initialize` to bring the device up.
    """
    self.configuration = await self.request_device_configuration()
    # Which device answered, and what it is running. Read into the same configuration the rest of
    # discovery fills, so a saved one says where it came from. A device that will not answer keeps
    # nothing rather than failing setup: the identity is for telling recordings apart, and nothing
    # the driver does depends on it.
    try:
      self.configuration.serial_number = await self.request_device_serial_number()
    except Exception:
      logger.warning("the device did not say what it calls itself; leaving its serial unrecorded")
    try:
      (
        self.configuration.firmware_version,
        self.configuration.firmware_date,
      ) = await self.request_firmware_version()
    except Exception:
      logger.warning("the device did not report its firmware; leaving its version unrecorded")
    self._check_declared_against(self.configuration)
    self._num_channels = self.configuration.num_pip_channels

    # Built for what the device turns out to have, and only if not already there: a caller can
    # hand a feature its configuration before setup, and re-running setup keeps it.
    if self.configuration.left_arm is not None and self.left_x_arm is None:
      self.left_x_arm = XArm(self, side="left")
    if self.configuration.right_arm is not None and self.right_x_arm is None:
      self.right_x_arm = XArm(self, side="right")
    # What an arm carries is what its own configuration bits claim. The firmware requires the two
    # drives' bits to be disjoint, so no feature can be on both.
    for arm in (self.left_x_arm, self.right_x_arm):
      if arm is None:
        continue
      a = arm.configuration
      if a.pip_installed and self.configuration.num_pip_channels > 0 and arm.pipettes is None:
        arm.pipettes = Pipettes(self)
      if a.head96_installed and arm.head96 is None:
        arm.head96 = Head96(self)
      if a.head384_installed and arm.head384 is None:
        arm.head384 = Head384(self)
      if a.iswap_installed and arm.iswap is None:
        arm.iswap = iSWAP(self)
    if self.configuration.autoload_installed and self.autoload is None:
      self.autoload = Autoload(self)
    if self.configuration.main_front_cover_monitoring_installed and self.front_cover is None:
      self.front_cover = FrontCover(self)

    # Each feature reads its own modules, and they are different modules, so they read at
    # once. Both arms run off the same X-drive board, so only one of them asks it.
    arms = [arm for arm in (self.left_x_arm, self.right_x_arm) if arm is not None]
    # Through the arms, not through the accessors above: those refuse on a device with two, and
    # setup has to reach every feature the device has whichever arm holds it.
    reading = []
    for arm in arms:
      reading.append(arm.discover())
      if arm.pipettes is not None:
        reading.append(arm.pipettes.discover())
      if arm.head96 is not None:
        reading.append(arm.head96.discover())
      if arm.head384 is not None:
        reading.append(arm.head384.discover())
      if arm.iswap is not None:
        reading.append(arm.iswap.discover())
    if self.autoload is not None:
      reading.append(self.autoload.discover())
    await asyncio.gather(*reading)
    # Once the head has said where it sits, an arm can take a declared side panel out of its own
    # travel: the offset is what decides how much the panel costs.
    for arm in arms:
      arm.narrow_travel_for_left_side_panel()

    # Read once, at discovery, and recorded there: a feature that would not say, or reported
    # nothing, is left out.
    reported = {
      "master": self.configuration.firmware_version,
      "pipettes": next(
        (a.pipettes.configuration.channels[0].firmware_version for a in arms if a.pipettes), None
      ),
      "x_arm": None if not arms else arms[0].configuration.firmware_version,
      "head96": next((a.head96.configuration.firmware_version for a in arms if a.head96), None),
      "head384": next((a.head384.configuration.firmware_version for a in arms if a.head384), None),
      "iswap": next((a.iswap.configuration.firmware_version for a in arms if a.iswap), None),
      "autoload": None if self.autoload is None else self.autoload.configuration.firmware_version,
    }
    self.firmware = {name: v for name, v in reported.items() if v is not None}

  async def initialize(self, read_timeout: int = 300) -> bool:
    """Bring the device itself to a known state.

    This moves it. A device that reports itself uninitialized runs the initialization procedure.
    One that reports itself initialized is left where it is, except that the channels are raised
    to Z safety. Nothing may move laterally while a channel is low.

    The procedure runs only on a device that is down, and `setup` initializes the features in the
    same call. There is deliberately no way to run it on a device that is up, and the procedure
    itself is private for the same reason: it takes the features' drives out of reference while
    the device goes on reporting itself initialized, and nothing that ran afterwards could tell.

    This is the device-level step only. `setup` is what initializes the features after it.

    Args:
      read_timeout: how long to wait for the procedure, in seconds.

    Returns:
      Whether the device reported itself already initialized before this ran.
    """
    already_initialized = await self.request_initialization_status()

    if not already_initialized:
      logger.debug(
        "device reports not initialized - running the initialization procedure (up to %d s)",
        read_timeout,
      )
      await self._pre_initialize(read_timeout=read_timeout)
    else:
      logger.debug("device reports initialized - raising the channels to Z safety only")
      for arm in self.arms:
        if arm.pipettes is not None:
          # Probing how high the channels reach raises them, so it doubles as that raise, as the
          # head's does.
          reached = await arm.pipettes.probe_z_max()
          c = arm.pipettes.configuration
          c.z_range = (c.z_range[0], min(reached))
        # A head is retracted whatever its own status says: the retract is what keeps it clear
        # of the iSWAP, which shares the arm's X drive and moves while features initialize.
        for head in (arm.head96, arm.head384):
          if head is not None:
            head_z = await head.probe_z_max()
            head.configuration.z_range = (head.configuration.z_range[0], head_z)

    return already_initialized

  async def _pre_initialize(self, read_timeout: int = 300):
    """Run the device's initialization procedure.

    Leaves the channels at Z safety and their Y drive without a reference: afterwards they report
    Y positions outside the range they reach, and the firmware refuses to move them, answering
    that the Y drive is not initialized. C0 goes on reporting the device as initialized, so no
    status read tells the difference. `setup` brings the features back up; a caller reaching for
    this on its own has to initialize them itself.

    The default read timeout is a wide margin over what the command has been measured to take.

    The autoload is a separate unit with an initialize of its own. It is left out of what this
    holds, and can be brought up alongside.

    Args:
      read_timeout: how long to wait for the procedure, in seconds.
    """
    resp = await self.send_command(
      module="C0",
      command="VI",
      subsystem=_FirmwareLock.EVERY_SUBSYSTEM_BUT_THE_AUTOLOAD,
      read_timeout=read_timeout,
    )
    logger.debug(
      "the device initialization procedure has run; the features are not initialized and their "
      "drives have no reference until they are"
    )
    return resp

  async def _channels_keep_no_reference(self, arm: XArm) -> bool:
    """Whether the channels report a Y position none of them can reach.

    A device whose initialization procedure ran without its features being initialized answers
    that it is initialized while its channels' Y drive holds no reference. The status read cannot
    tell the difference, and a firmware that refuses the next move says so only once something
    tries; where the channels say they are does tell, so that is what is asked.

    Warns and returns. Putting the reference back means initializing the channels, which discards
    whatever is mounted and moves them, so it is the caller's to do: `pipettes.initialize()`.

    Args:
      arm: the arm whose channels to ask.

    Returns:
      True if any channel reports outside the Y range that arm reaches. False when nothing could
      be read, or no geometry was discovered to judge against: a reading that cannot be taken is
      not evidence of a fault.
    """
    if arm.pipettes is None or self.configuration is None:
      return False
    low = (
      self.configuration.left_arm_min_y_position
      if arm.side == "left"
      else self.configuration.right_arm_min_y_position
    )
    high = self.configuration.pip_maximal_y_position
    try:
      positions = await arm.pipettes.request_y_positions()
    except Exception:
      logger.warning("could not read where the channels are; taking their reference on trust")
      return False
    unreachable = [y for y in positions if not low <= y <= high]
    if unreachable:
      logger.warning(
        "%d of %d channels report outside the %.1f to %.1f mm they reach: their Y drive keeps no "
        "reference, whatever the device answers about being initialized. `pipettes.initialize()` "
        "puts it back, and discards whatever is mounted doing so",
        len(unreachable),
        len(positions),
        low,
        high,
      )
    return bool(unreachable)

  async def _initialize_arm(
    self, arm: XArm, already_initialized: bool, skipped: FrozenSet[str] = frozenset()
  ):
    """Initialize everything one arm carries, one after another.

    The channels, the iSWAP and the 96-head share the arm's X drive. Initializing one while
    another is moving is refused, so they go in the order the legacy routine uses. Two arms have
    two drives, and a device with both initializes them alongside each other.

    Args:
      arm: the arm whose features to initialize.
      already_initialized: whether the device reported itself up before this setup ran.
      skipped: which of `pipettes`, `iswap`, `head96` and `head384` to leave as the device has
        them. A skipped feature is not raised to safety either, so nothing else may travel
        laterally on this arm until something brings it up.
    """
    if arm.pipettes is None:
      logger.debug("channels: none installed - skipped")
    elif "pipettes" in skipped:
      logger.debug("channels: initializing them was skipped")
    else:
      tips = await arm.pipettes.sense_tip_presence()
      if already_initialized:
        # Said, not acted on: initializing discards whatever is mounted and moves the channels,
        # which is not something to do off a reading. The caller decides.
        await self._channels_keep_no_reference(arm)
      if not already_initialized or any(tips):
        logger.debug(
          "channels: %d of %d carrying tips, device %s - initializing",
          sum(tips),
          len(tips),
          "was already up" if already_initialized else "has just been homed",
        )
        await arm.pipettes.initialize()
      else:
        logger.debug("channels: already up and nothing mounted - skipped")
      # Probing how high the channels reach raises them, so it doubles as the safety raise and
      # runs on every setup rather than only the first.
      reached = await arm.pipettes.probe_z_max()
      # One ceiling for all of them, since one window is what `_check_reachable` holds every
      # channel to. The floor is left as it stands: nothing here measures how low they go.
      c = arm.pipettes.configuration
      c.z_range = (c.z_range[0], min(reached))
      # The drives keep what an earlier session wrote until a power cycle; every run starts here.
      await arm.pipettes._set_default_drive_parameters()

    if arm.iswap is not None and "iswap" in skipped:
      logger.debug("iSWAP: initializing it was skipped")
    elif arm.iswap is not None:
      if not await self.request_initialization_status("R0"):
        logger.debug("iSWAP reports itself uninitialized - initializing")
        await arm.iswap.initialize()
      await arm.iswap.park()

    for head, name in ((arm.head96, "head96"), (arm.head384, "head384")):
      if head is None:
        continue
      if name in skipped:
        logger.debug("%s: initializing it was skipped", name)
        continue
      # A STAR deck carries a trash for the 96-head. A head told nowhere else to eject ejects there,
      # centred over it, as legacy does.
      if (
        name == "head96"
        and head.configuration.tip_discard_location is None
        and self.deck is not None
        and self.deck.has_resource(self._deck_component_name("trash_core96"))
      ):
        head.configuration.tip_discard_location = cast(Head96, head)._position_centred_in(
          self.deck.get_resource(self._deck_component_name("trash_core96"))
        )
      if not await self.request_initialization_status(head.configuration.module):
        if head.configuration.tip_discard_location is None:
          logger.warning(
            "the %s reports itself uninitialized, and there is nowhere configured to eject at. "
            "Set %s.configuration.tip_discard_location, or pass it to %s.initialize().",
            name,
            name,
            name,
          )
        else:
          logger.debug("%s reports itself uninitialized - initializing", name)
          await head.initialize()
      # Probing how far a head reaches retracts it, so it doubles as the safety retract and
      # runs on every setup rather than only the first. The floor is what the drive documents.
      retracted = await head.probe_z_max()
      head.configuration.z_range = (head.configuration.z_range[0], retracted)

  def format_setup_summary(self) -> str:
    """One block describing the device that was found: how it is reached, what firmware every
    module runs, whether an autoload is fitted, how many arms there are, and per arm its
    dimensions, how many channels it carries and whether it carries a 96-head, a 384-head and an
    iSWAP.

    Returns:
      A multi-line summary, or a note that setup has not run.
    """
    c = self.configuration
    if c is None:
      return "[Hamilton STAR] not discovered yet"

    firmware = (
      ", ".join(f"{name} {version}" for name, version in self.firmware.items()) or "unknown"
    )

    fitted = [f"{c.instrument_size_slots} slots"]
    for number, installed in ((1, c.wash_station_1_installed), (2, c.wash_station_2_installed)):
      if installed:
        fitted.append(f"wash station {number}")

    autoload = "none"
    if c.autoload_installed:
      autoload = "installed"
      if self.autoload is not None and self.autoload.configuration.autoload_type is not None:
        autoload = self.autoload.configuration.autoload_type

    arms = [arm for arm in (self.left_x_arm, self.right_x_arm) if arm is not None]
    lines = [
      f"[Hamilton STAR] Connected on {self._describe_link()}",
      f"  Firmware: {firmware}",
      f"  Arms: {len(arms)}",
      f"  Configuration: {', '.join(fitted)}",
      f"  Autoload: {autoload}",
    ]
    for arm in arms:
      a = arm.configuration
      # Read through the feature, not the arm's own bit, so the summary cannot report channels
      # the driver did not build. The two disagree only on a device whose configuration says both.
      channels = "none"
      if arm.pipettes is not None and a.pip_installed:
        channels = f"{c.num_pip_channels} ({'1000uL' if c.pip_type_1000ul else '300uL'})"
      elif arm.pipettes is None and a.pip_installed:
        channels = "none, but this arm reports the module installed"
      heads = []
      for head, installed, label in (
        (arm.head96, a.head96_installed, "96-head"),
        (arm.head384, a.head384_installed, "384-head"),
      ):
        described = "none"
        if installed:
          described = "installed"
          if head is not None and head.configuration.head_type is not None:
            described = head.configuration.head_type
        heads.append(f"{label}: {described}")
      iswap = "none"
      if a.iswap_installed:
        iswap = f"{'wide' if c.iswap_gripper_wide else 'small'} gripper"
      lines.append(
        f"    {arm.side}: {a.model}, {a.width} mm wide, "
        f"travel {_range(a.x_range)}, workspace {_range(a.workspace_x_range)}"
      )
      lines.append(f"      channels: {channels} | {' | '.join(heads)} | iSWAP: {iswap}")
    if sum(arm.configuration.pip_installed for arm in arms) > 1:
      lines.append("      (the device reports one channel count for the device, not per arm)")
    return "\n".join(lines)

  # ----------------------------------------
  # Configuration system
  # ----------------------------------------

  def _saved_configuration(self) -> Dict[str, Any]:
    """Every configuration this device holds, shaped as the device is.

    Walked rather than listed: the device's own configuration, then whatever each arm turns out to
    carry under the side carrying it, then whatever is fitted to the device itself. Nothing here
    fixes how many of anything a device may have, so one that grows a second head is saved without
    this having to change.

    Returns:
      What `save_configuration` writes.

    Raises:
      RuntimeError: If nothing has been read off the device yet.
    """
    if self.configuration is None:
      raise RuntimeError("nothing has been read off this device; call `setup` first")

    saved: Dict[str, Any] = {
      "device": serialize(dataclasses.asdict(self.configuration)),
      "arms": {},
    }
    for arm in self.arms:
      carried = {
        name: serialize(dataclasses.asdict(feature.configuration))
        for name, feature in (
          ("pipettes", arm.pipettes),
          ("head96", arm.head96),
          ("head384", arm.head384),
          ("iswap", arm.iswap),
        )
        if feature is not None
      }
      if carried:
        saved["arms"][arm.side] = carried
    if self.autoload is not None:
      saved["autoload"] = serialize(dataclasses.asdict(self.autoload.configuration))
    return saved

  def save_configuration(self, path: str, indent: Optional[int] = 2) -> None:
    """Write what this device reported to a file, to be simulated from later.

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

  async def _create_capability_resources(self) -> None:
    """Put what the device carries on the deck, where it is.

    Read once, at setup: each feature's resource is placed at the position read back from it.
    A resource already on the deck is reused, and repeated setups do not duplicate it.
    """
    if self.deck is None:
      return
    for arm in (self.left_x_arm, self.right_x_arm):
      if arm is None:
        continue
      a = arm.configuration
      if a.width is None:
        logger.warning("the %s X-arm reported no width, so it is not modelled", arm.side)
        continue
      arm.resource = self.deck.get_or_create_x_arm(
        name=self._component_name(f"{arm.side}_x_arm"),
        x=await arm.request_position(),
        size_x=a.size_x,
        reference_point_from_left=a.reference_point_from_left,
        model=a.model,
      )
    await self._create_pipette_resources()
    await self._create_autoload_resource()
    await self._create_head_resources()
    await self._create_iswap_resource()

  async def _create_pipette_resources(self) -> None:
    """Put a resource on the arm for each pipetting channel, where it is.

    One resource per channel, not one for the block: the channels share the arm's X but each has
    its own Y and Z. They are children of the arm's resource, as the 96-head is. Channels already
    on the arm are reused, and repeated setups do not duplicate them.

    Each carries a `TipMountingShaft` at its lower end. A collected tip becomes a child of the
    shaft, not of the channel.

    Raises:
      RuntimeError: If a channel to be modelled has no width read.
    """
    if self.deck is None:
      return
    arm = next((a for a in self.arms if a.pipettes is not None), None)
    if arm is None or arm.pipettes is None or arm.resource is None:
      return

    # One per channel the device reported at discovery, not a count assumed here.
    c = arm.pipettes.configuration
    arm.pipettes.resources = []

    # Read before the resources exist, as the arm's own creation reads before it makes one. The
    # reads record nothing while there is nothing to record on, and a simulated device answers
    # where it powered up rather than from a resource that has not been placed yet.
    ys = await arm.pipettes.request_y_positions()
    zs = await arm.pipettes._unchecked_fw_request_lowest_z_positions()

    for channel in range(len(c.channels)):
      name = self._component_name(f"pipette_channel_{channel}")
      resource = next((r for r in arm.resource.children if r.name == name), None)
      if resource is None:
        width = c.channels[channel].width
        if width is None:
          # Skipping it would leave the list one short and every later channel's resource one out
          # of step with its channel.
          raise RuntimeError(f"channel {channel} has no width read yet; run discovery first")
        resource = Resource(
          name=name,
          size_x=width,
          size_y=width,
          size_z=c.channel_size_z,
          category="pipette_channel",
          model="hamilton_star_pipette_channel",
        )
        # Along X a channel sits at the arm's own reference point, so its centre lands there. The
        # arm's reference point is not the middle of it: the part is wider than the width the drive
        # reports, and reaches further right than the point it counts from.
        anchor = resource.get_anchor(x=arm.pipettes.configuration.x_reference_anchor)
        arm.resource.assign_child_resource(
          resource,
          location=Coordinate(arm.configuration.reference_point_from_left - anchor.x, 0.0, 0.0),
        )
      arm.pipettes.add_tip_mounting_shaft(resource)
      arm.pipettes.resources.append(resource)

    # Seat each one where the reads above found it. Recorded here rather than by the reads
    # themselves, because the resources they would have recorded on did not exist yet.
    for channel in range(len(arm.pipettes.resources)):
      arm.pipettes.update_location_by_reference_point(channel, y=ys[channel], z=zs[channel])

  async def _create_head_resources(self) -> None:
    """Put each head on the arm it rides, where it is along Y.

    A child of the arm's resource, not of the deck. It then follows the arm in X with nothing
    keeping the two in step. A head already on the arm is reused, and repeated setups do not
    duplicate it.

    Raises:
      RuntimeError: If a head's X offset was not read, leaving its position across the arm
        unknown.
    """
    if self.deck is None:
      return
    for arm in self.arms:
      if arm.resource is None:
        continue
      for head, name, label, build in (
        (arm.head96, "head96", "96-head", head96_pipette),
        (arm.head384, "head384", "384-head", head384_pipette),
      ):
        if head is None:
          continue
        name = self._component_name(name)
        c = head.configuration
        # Where the head is, read before it has a resource to read from: the drive answers on a
        # device, and a simulated one falls back to where it rests rather than reporting back the
        # placeholder position it is about to be given.
        y, z = await head.request_y_position(), await head.request_z_position()
        existing = next((child for child in arm.resource.children if child.name == name), None)
        resource = existing if isinstance(existing, NChannelPipette) else None
        if resource is None:
          if c.x_offset is None:
            raise RuntimeError(
              f"the {label}'s X offset was not read; have you called `star.setup()`?"
            )
          # The definition, not a bare resource: it carries a mounting shaft per channel, which
          # is what a collected tip becomes a child of.
          resource = build(name=name, size_z=c.body_size_z)
          # Channel A1 sits `x_offset` left of the point the drive tracks the arm by, and the arm
          # is located by its own left edge, so A1 lands that far left of the reference point. What
          # is placed is the head, whose own A1 stands inside it, so that inset comes off too - the
          # head is measured from A1 the way a channel is measured from its axis. Y is set from the
          # drive below.
          arm.resource.assign_child_resource(
            resource,
            location=Coordinate(
              arm.configuration.reference_point_from_left - c.x_offset - resource.reference_point.x,
              0.0,
              0.0,
            ),
          )
        head.resource = resource
        head.update_location_by_reference_point(y=y, z=z)

  async def _create_iswap_resource(self) -> None:
    """Put each iSWAP's elbow on the arm it rides, where it is.

    A child of the arm's resource, not of the deck, so it follows the arm in X with nothing keeping
    the two in step. One already on the arm is reused, and repeated setups do not duplicate it.

    The drive is modelled, not the gripper: where the gripper is follows from the joint state
    rather than from where the drive sits, and `request_pose` is what resolves that.

    Raises:
      RuntimeError: If the drive's X offset was not read, leaving its position across the arm
        unknown.
    """
    if self.deck is None:
      return
    for arm in self.arms:
      if arm.iswap is None or arm.resource is None:
        continue
      iswap = arm.iswap
      c = iswap.configuration
      # Read before it has a resource to read into, as the head's are.
      y = await iswap.elbow_request_y_position()
      z = await iswap.elbow_request_z_position()
      angle = await iswap.elbow_drive_request_angle()
      name = self._component_name("iswap_head")
      existing = next((child for child in arm.resource.children if child.name == name), None)
      resource = existing if isinstance(existing, iSWAPHead) else None
      if resource is None:
        if c.elbow_x_offset is None:
          raise RuntimeError(
            "the iSWAP elbow's X offset was not read; have you called `star.setup()`?"
          )
        # How tall to model the column: nothing reports it, and what the device shows is its top
        # standing level with the tops of the channel bodies when the drive is fully retracted.
        # Both heights are taken on the deck, so neither needs the arm taking out.
        tops = [
          ch.get_location_wrt(self.deck).z + ch.get_size_z()
          for ch in (arm.pipettes.resources if arm.pipettes is not None else [])
          if ch.location is not None
        ]
        retracted_base = (
          c.z_increments_to_mm(c.z_range_increments[1])
          + c.elbow_z_offset_above_finger
          + ELBOW_DRIVE_COLUMN_ABOVE_REPORTED_Z
        )
        resource = iswap_head(
          name=name,
          diameter=c.elbow_drive_diameter,
          size_z=round(max(tops) - retracted_base, 1) if tops else c.elbow_drive_size_z,
        )
        # The drive sits `elbow_x_offset` left of the point the drive tracks the arm by,
        # and the arm is located by its own left edge, so it lands that far left of the reference
        # point.
        anchor = resource.reference_point
        arm.resource.assign_child_resource(
          resource,
          location=Coordinate(
            arm.configuration.reference_point_from_left - c.elbow_x_offset - anchor.x,
            0.0,
            0.0,
          ),
        )
      iswap.resource = resource
      iswap.link_1, iswap.gripper = self._create_iswap_arm(resource, c)
      iswap.update_location_by_reference_point(y=y, z=z)
      iswap.elbow_drive_update_angle(angle)
      iswap.wrist_drive_update_angle(await iswap.wrist_drive_request_angle())
      # And how far the jaws stand open, which the read records, so the model starts in step with
      # the arm rather than at whatever width the gripper was built holding.
      await iswap.gripper_request_width()

  def _create_iswap_arm(
    self, resource: iSWAPHead, c: iSWAPConfiguration
  ) -> Tuple[Optional[LinkBody], Optional[MechanicalGripper]]:
    """Hang the arm off the carriage: one link, and the gripper it carries.

    Link 1 turns on the elbow drive; the gripper turns on the wrist that link 1 carries, so it
    is a child of link 1 and its angle is measured from it. Where each points is written by
    `elbow_drive_update_angle` and `wrist_drive_update_angle`. What is already there is reused.

    Args:
      resource: the carriage they hang from.
      c: the arm's configuration, which carries both lengths.

    Returns:
      The link and the gripper, or `(None, None)` if the arm did not report its lengths.
    """
    if c.link_1_length is None or c.tool_length is None:
      logger.warning("the iSWAP reported no link lengths, so its arm is not modelled")
      return None, None
    link_1 = next((child for child in resource.children if isinstance(child, LinkBody)), None)
    if link_1 is None:
      link_1 = iswap_link_1(name=self._component_name("iswap_link_1"), length=c.link_1_length)
      # A member's origin is a corner, so it is placed by where its joint has to land: the joint
      # goes on the drive's reference point, and the corner falls wherever that puts it.
      resource.assign_child_resource(
        link_1, location=resource.reference_point - link_1.proximal_joint
      )
    gripper = next(
      (child for child in link_1.children if isinstance(child, MechanicalGripper)), None
    )
    if gripper is None:
      # How far the jaws travel is the gripper drive's own window, converted, rather than a
      # measurement of the fingers: what the drive accepts is what the jaws do.
      gripper = iswap_gripper(
        name=self._component_name("iswap_gripper"),
        # The Z drive is calibrated to the finger plane and reports its own bottom, so the grip
        # centre is that far below the wrist the gripper hangs from.
        tool_center_point=Coordinate(c.tool_length, 0.0, -c.elbow_z_offset_above_finger),
        jaw_range=(
          c.gripper_increments_to_mm(c.gripper_range_increments[0]),
          c.gripper_increments_to_mm(c.gripper_range_increments[1]),
        ),
        # And standing where an initialized gripper stands, so the model does not start out
        # claiming a width nothing has read. Setup reads the jaws straight after and records it.
        jaw_width=(
          c.gripper_increments_to_mm(c.gripper_drive_predefined_increments.home)
          if c.gripper_drive_predefined_increments
          else None
        ),
      )
      # Likewise: the gripper's own joint lands on the wrist, which is link 1's far joint.
      link_1.assign_child_resource(
        gripper, location=cast(Coordinate, link_1.distal_joint) - gripper.proximal_joint
      )
    return link_1, gripper

  async def _create_autoload_resource(self) -> None:
    """Put the autoload's sled on the deck, where it is, and the tray it draws carriers from.

    The tray is placed from the deck's features, not read off the device. It is bolted to the
    device and has no drive to report its position.
    """
    if self.autoload is None or self.deck is None:
      return
    x = await self.autoload.request_x_position()
    self.autoload.resource = self.deck.get_or_create_autoload_sled(
      name=self._component_name("autoload_sled"),
      x=x,
      reference_point_from_left=self.autoload.configuration.reference_point_from_sled_left_edge,
    )
    self.autoload.update_location_by_reference_point(x)
    self.deck.get_or_create_autoload_loading_tray(
      name=self._component_name("autoload_loading_tray")
    )
