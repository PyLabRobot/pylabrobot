"""The STAR master module, responsible for
- carrying the transport,
- firmware protocol
- orchestrating higher level tasks.
"""

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple, cast

from pylabrobot.events import emit_event
from pylabrobot.hamilton.protocol.text.framing import (
  assemble_channel_command,
  parse_firmware_version_date,
  parse_fw_string,
)
from pylabrobot.hamilton.protocol.text.router import ReplyRouter
from pylabrobot.hamilton.star.driver.configuration import DeviceConfiguration
from pylabrobot.hamilton.star.driver.errors import (
  STAR_MODULE_ID_LENGTH,
  check_fw_string_error,
)
from pylabrobot.hamilton.star.driver.features.autoload import Autoload
from pylabrobot.hamilton.star.driver.features.cover import FrontCover
from pylabrobot.hamilton.star.driver.features.head96 import Head96
from pylabrobot.hamilton.star.driver.features.head384 import Head384
from pylabrobot.hamilton.star.driver.features.iswap import iSWAP
from pylabrobot.hamilton.star.driver.features.pipettes import CHANNEL_X_REFERENCE_ANCHOR, Pipettes
from pylabrobot.hamilton.star.driver.features.x_arm import XArm, XArmConfiguration
from pylabrobot.hamilton.star.driver.lock import _FirmwareLock
from pylabrobot.hamilton.star.resource_model import NChannelPipette
from pylabrobot.hamilton.star.resource_model import head96 as head96_pipette
from pylabrobot.hamilton.star.resource_model import head384 as head384_pipette
from pylabrobot.io.io import IOBase
from pylabrobot.io.usb import USB
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.hamilton.hamilton_decks import HamiltonDeck
from pylabrobot.resources.hamilton.tip_creators import HamiltonTip, TipPickupMethod, TipSize
from pylabrobot.resources.resource import Resource

logger = logging.getLogger(__name__)

ID_VENDOR = 0x08AF
ID_PRODUCT = 0x8000


def _range(values: Optional[Tuple[float, float]]) -> str:
  """A `(low, high)` range in mm, or a note that it was not resolved."""
  return "unresolved" if values is None else f"{values[0]} to {values[1]} mm"


# The instrument initialization procedure homes every drive, which takes minutes.
PRE_INITIALIZE_READ_TIMEOUT = 300


class STARDriver:
  """Interface for the Hamilton STARDriver."""

  def __init__(
    self,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    deck: Optional[HamiltonDeck] = None,
    packet_read_timeout: int = 3,
    write_timeout: int = 30,
    read_timeout: int = 60,
    left_side_panel_installed: bool = False,
    io: Optional[IOBase] = None,
  ):
    """Create a new STAR interface.

    Args:
      device_address: the USB device address of the Hamilton STAR. Only useful if using more than
        one Hamilton machine over USB.
      serial_number: the serial number of the Hamilton STAR. Only useful if using more than one
        Hamilton machine over USB.
      packet_read_timeout: timeout in seconds for reading a single packet.
      read_timeout: timeout in seconds for reading a full response.
      write_timeout: timeout in seconds for writing a command.
      left_side_panel_installed: whether the machine has its left side panel on. Declared rather
        than read: it comes off in seconds, so the machine's own travel report does not follow it.
        With one fitted, an arm carrying a head stops while the head is still clear of it.
      io: an already-built USB handle to use instead of opening one from the arguments above.
      deck: the deck to reflect the machine into. Optional: without one the driver still drives the
        machine, and nothing about where things are is modelled.
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

    self._num_channels: Optional[int] = None

    # Whether the link is open. Commands gate on it: they flow during setup, long before setup
    # is done.
    self._connected = False

    self.left_side_panel_installed = left_side_panel_installed

    # The deck to reflect the machine into, or None to drive it without a resource model. With one,
    # setup builds a resource per capability as a child of it; without, nothing is modelled.
    self.deck = deck

    self.configuration: Optional[DeviceConfiguration] = None

    # What each capability reported at discovery, keyed as `confirmed_firmware_versions` keys it.
    self.firmware: Dict[str, str] = {}
    # Which table index each tip type was written to. The table is volatile, so this is
    # rebuilt per session as tips are first used.
    self._tip_type_indices: Dict[int, int] = {}

    # Subsystems. Each reads what it needs off `configuration`, so they are usable once setup has
    # run and raise a clear error before that. Each arm appears only if setup finds one installed.
    self.front_cover: Optional[FrontCover] = None
    self.left_x_arm: Optional[XArm] = None
    self.right_x_arm: Optional[XArm] = None
    self.autoload: Optional[Autoload] = None

  # ----------------------------------------
  # Connection and lifecycle
  # ----------------------------------------

  async def setup(self):
    """Connect to the machine, find out what it is, and bring it up.

    This moves the instrument: everything that can be initialized is. `discover` on its own is
    the read-only half, for connecting and inspecting without anything moving.

    Safe to call again: discovery re-reads the machine and initialization is a no-op on a machine
    that is already up. A setup that fails part way closes the link rather than leaving a claimed
    device and a reader behind.
    """
    logger.debug("Setting up STAR on %s ...", self._describe_link())
    await self._open()
    self._connected = True

    try:
      # 1. What is on the other end, and what does it carry?
      logger.debug("[PHASE 1] Discovery")
      await self.discover()

      # 2. Bring the instrument to a known state.
      logger.debug("[PHASE 2] Instrument initialization")
      already_initialized = await self.initialize()

      # 3. Each capability brings itself up. They sit on different modules, so they run together;
      #    the autoload, iSWAP and 96-head join this gather as they land. The channels only need
      #    it when the instrument procedure did not just run, or when something is still mounted.
      logger.debug("[PHASE 3] Capability initialization")
      initializing = [self._initialize_arm(arm, already_initialized) for arm in self.arms]
      if self.autoload is not None:
        initializing.append(self.autoload.initialize())
      await asyncio.gather(*initializing)

      # 4. What was found, as resources on the deck - when the driver was given one to reflect
      #    into. Each is a child of the deck, so a machine with a deck carries one tree.
      if self.deck is not None:
        logger.debug("[PHASE 4] Capability resources")
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

  async def stop(self):
    """Close the link. The machine keeps its state; only this driver lets go of it."""
    self._connected = False
    await self._close()

  @property
  def connected(self) -> bool:
    """Whether the link is open, so commands can be sent."""
    return self._connected

  def _describe_link(self) -> str:
    """How this machine is reached, in whatever terms its transport is addressed by."""
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

  async def send_command(
    self,
    module: str,
    command: str,
    auto_id=True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait=True,
    fmt: Optional[Any] = None,
    **kwargs,
  ):
    """Assemble a firmware command, send it, and parse the reply if a format is given.

    Modules share one link, so this serializes what has to be serialized: one command at a time
    per module, and a C0 master command alone, after the modules have drained. Commands that only
    read - every `R` and `Q` command - take no lock and run alongside anything.

    Raises:
      RuntimeError: If the link is not open.
    """
    kwargs_ = dict(
      auto_id=auto_id,
      tip_pattern=tip_pattern,
      write_timeout=write_timeout,
      read_timeout=read_timeout,
      wait=wait,
      fmt=fmt,
      **kwargs,
    )
    if command[0] in ("R", "Q"):
      return await self._send(module, command, **kwargs_)
    if module == "C0":
      async with self._lock.c0():
        return await self._send(module, command, **kwargs_)
    async with self._lock.subsystem(module):
      return await self._send(module, command, **kwargs_)

  async def _send(
    self,
    module: str,
    command: str,
    auto_id=True,
    tip_pattern: Optional[List[bool]] = None,
    write_timeout: Optional[int] = None,
    read_timeout: Optional[int] = None,
    wait=True,
    fmt: Optional[Any] = None,
    **kwargs,
  ):
    """Assemble, send and parse, without coordinating against anything else in flight."""
    self._require_connection()
    id_ = self._replies.next_id() if auto_id else None
    # Always the channel-aware assembler: a list parameter has to be terminated against the
    # machine's channel count whether or not the caller named which channels are involved, and
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
      resp = await self._replies.send(
        cmd=cmd,
        id_=id_,
        write_timeout=write_timeout,
        read_timeout=read_timeout,
        wait=wait,
      )
      result = self._parse_response(resp, fmt) if resp is not None and fmt is not None else resp
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
    """Send a raw command to the machine.

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
    """Raise unless the link is open, so a command cannot be sent into nothing."""
    if not self._connected:
      raise RuntimeError("not connected to a machine; call `setup` first")

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """Get the id from a firmware response."""
    parsed = parse_fw_string(resp, "id####")
    if "id" in parsed and parsed["id"] is not None:
      return int(parsed["id"])
    return None

  def _parse_response(self, resp: str, fmt: Any) -> Dict[str, Any]:
    """Parse a response from the machine."""
    return parse_fw_string(resp, fmt)

  # ----------------------------------------
  # What the arms carry
  # ----------------------------------------

  @property
  def arms(self) -> List[XArm]:
    """The arms this machine has, left first."""
    return [arm for arm in (self.left_x_arm, self.right_x_arm) if arm is not None]

  def _require_one_arm(self, reaching_for: str) -> Optional[XArm]:
    """The machine's arm, for accessors that only make sense when it has one.

    A machine with two carries two of everything, so which is meant is the caller's to say. One
    with none carries nothing, which is an answer rather than a refusal.

    Args:
      reaching_for: what the caller was after. Used to word the refusal, nothing else.

    Returns:
      The machine's one arm, or None when it has none.

    Raises:
      ValueError: If the machine has more than one arm.
    """
    arms = self.arms
    if len(arms) > 1:
      raise ValueError(
        f"this machine has two X-arms, so `{reaching_for}` is ambiguous - reach it through "
        f"`left_x_arm.{reaching_for}` or `right_x_arm.{reaching_for}`."
      )
    return arms[0] if arms else None

  @property
  def pipettes(self) -> Optional[Pipettes]:
    """The pipetting channels, on a machine with one arm."""
    arm = self._require_one_arm("pipettes")
    return arm.pipettes if arm is not None else None

  @property
  def head96(self) -> Optional[Head96]:
    """The 96-head, on a machine with one arm."""
    arm = self._require_one_arm("head96")
    return arm.head96 if arm is not None else None

  @property
  def head384(self) -> Optional[Head384]:
    """The 384-head, on a machine with one arm."""
    arm = self._require_one_arm("head384")
    return arm.head384 if arm is not None else None

  @property
  def iswap(self) -> Optional[iSWAP]:
    """The iSWAP, on a machine with one arm."""
    arm = self._require_one_arm("iswap")
    return arm.iswap if arm is not None else None

  @property
  def x_arm(self) -> XArm:
    """The machine's X-arm, on a machine that has only one.

    Most STARs carry a single arm, and naming a side there is noise. A machine with two has no
    single X-arm, so this refuses rather than picking one.

    Raises:
      RuntimeError: If setup has not run, so it is not yet known which arms are installed.
      ValueError: If the machine has no arm, or more than one.
    """
    if self.configuration is None:
      raise RuntimeError("no configuration read; have you called `star.setup()`?")
    installed = {
      name: arm
      for name, arm in (("left_x_arm", self.left_x_arm), ("right_x_arm", self.right_x_arm))
      if arm is not None
    }
    if not installed:
      raise ValueError("this machine reports no X-arm installed.")
    if len(installed) > 1:
      raise ValueError(
        f"this machine has {len(installed)} X-arms ({', '.join(installed)}), so `x_arm` is "
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

  async def request_firmware_version(self) -> Tuple[str, datetime.date]:
    """Request the master's firmware version and build date.

    Returns:
      The version string and its build date, e.g. `("7.6S", date(2021, 11, 5))`.
    """
    resp = await self.send_command(module="C0", command="RF")
    return resp.split("rf")[-1], parse_firmware_version_date(resp)

  async def request_tip_presence(self) -> List[bool]:
    """Measure tip presence on all single channels using their sleeve sensors.

    Returns:
      A list of length `num_channels`, `True` where a tip is mounted.
    """
    resp = await self.send_command(module="C0", command="RT", fmt="rt# (n)")
    return [bool(v) for v in cast(List[int], resp.get("rt"))]

  async def request_cover_input_status(self) -> Tuple[bool, bool, bool]:
    """Request the three inputs on the cover connector.

    On the master rather than on `front_cover`, because it is the one cover read that stays
    reachable on a machine whose configuration says the cover monitoring is not installed - and
    that machine is exactly the one worth asking.

    Returns:
      Whether each is set: the cover input, the second input - a reserve or the additional cover
      control, depending on the board - and a second reserve. What a set cover input means is not
      stated; `front_cover.request_position` is the one that says open or shut.

    Raises:
      ValueError: If the machine answered with fewer than three inputs.
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
    resp = await self.send_command(module="C0", command="UA")
    values = [int(v) / 10 for v in resp.split("ua")[-1].strip().split()]
    left_wrap, right_wrap, left_min, left_max, right_min, right_max = values
    return {
      "left": (left_wrap, (left_min, left_max)),
      "right": (right_wrap, (right_min, right_max)),
    }

  async def request_device_configuration(self) -> DeviceConfiguration:
    """Request the instrument's installed hardware and geometry.

    Combines the machine configuration (RM) and the extended configuration (QM). Each installed
    X-drive's geometry is resolved from the X-drive range (RU) and working-envelope (UA) queries;
    `right_arm` is None when no second arm is installed.
    """
    machine = await self.send_command(module="C0", command="RM", fmt="kb**kp##")
    extended = await self.send_command(
      module="C0",
      command="QM",
      fmt="ka******ke********xt##xa##xw#####xl**xn**xr**xo**xm#####xx#####xu####xv####kc#kr#"
      + "ys###kl###km###ym####yu####yx####",
    )

    ranges = await self.request_maximal_ranges_of_x_drives()
    wraps = await self.request_working_envelopes_per_arm()

    def _resolve_arm(
      byte1: int, byte2: int, side: Literal["left", "right"], width: float
    ) -> Optional[XArmConfiguration]:
      wrap, workspace_range = wraps[side]
      if wrap == 0:  # arm not installed
        return None
      return XArmConfiguration(
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
        x_range=ranges[side],
        workspace_range=workspace_range,
        wrap_size=wrap,
      )

    kb = machine["kb"]
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
      num_pip_channels=machine["kp"],
      left_x_drive_large=bool(ka & (1 << 0)),
      ka_head96_installed=bool(ka & (1 << 1)),
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
      dispensing_head_384_installed=bool(ka & (1 << 12)),
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
      left_arm=_resolve_arm(extended["xl"], extended["xn"], "left", extended["xu"] / 10),
      right_arm=_resolve_arm(extended["xr"], extended["xo"], "right", extended["xv"] / 10),
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

    Every module answers the same query, so this covers the master and each subsystem.

    Args:
      module: the module to ask. Defaults to the master, which reports for the instrument.

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
    """Write one entry of the instrument's tip type table.

    The table is volatile: it is written from scratch after every power on, so what a run defines
    lasts only as long as the machine stays up.

    Args:
      tip_type_table_index: which entry to write, 1 to 99.
      has_filter: whether the tip has a filter.
      tip_length: how far the tip stands proud of what holds it, in mm - its total length past its
        fitting depth.
      maximum_tip_volume: what the tip holds, in uL. The firmware caps it at the channel's own
        capacity.
      tip_size: which collar the tip has, which is how the instrument identifies it.
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
      tt=f"{tip_type_table_index:02}",
      tf=has_filter,
      tl=f"{length_increments:04}",
      tv=f"{volume_increments:05}",
      tg=tip_size.value,
      tu=pickup_method.value,
    )

  async def get_or_assign_tip_type_index(self, tip: HamiltonTip) -> int:
    """The table index this tip is defined at, defining it if it is new to this session.

    Every command that mounts tips names one of these indices rather than the tip itself, so a tip
    the machine has not been told about has to be written into the table first.

    Args:
      tip: the tip to look up.

    Returns:
      Its index in the instrument's tip type table.

    Raises:
      ValueError: If the table is full.
    """
    tip_hash = hash(tip)
    if tip_hash not in self._tip_type_indices:
      index = len(self._tip_type_indices) + 1
      if index > 99:
        raise ValueError("the tip type table is full: 99 tip types have already been defined.")
      await self.define_tip_needle(
        tip_type_table_index=index,
        has_filter=tip.has_filter,
        tip_length=tip.total_tip_length - tip.fitting_depth,
        # Floored at 1.0 uL so a teaching or probe needle with no capacity registers the way the
        # firmware's own non-pipetting tools do. It does not affect pickup, which goes by length
        # and collar.
        maximum_tip_volume=max(tip.maximal_volume, 1.0),
        tip_size=tip.tip_size,
        pickup_method=tip.pickup_method,
      )
      self._tip_type_indices[tip_hash] = index
    return self._tip_type_indices[tip_hash]

  # ----------------------------------------
  # Discovery and initialization
  # ----------------------------------------

  async def discover(self):
    """Read what machine is on the other end, and build the subsystems it turns out to have.

    Read-only: nothing moves. Call `initialize` to bring the machine up.
    """
    self.configuration = await self.request_device_configuration()
    self._num_channels = len(await self.request_tip_presence())

    # Built for what the machine turns out to have, and only if not already there: a caller can
    # hand a capability its configuration before setup, and re-running setup keeps it.
    if self.configuration.left_arm is not None and self.left_x_arm is None:
      self.left_x_arm = XArm(self, side="left")
    if self.configuration.right_arm is not None and self.right_x_arm is None:
      self.right_x_arm = XArm(self, side="right")
    # What an arm carries is what its own configuration bits claim. The firmware requires the two
    # drives' bits to be disjoint, so no capability can be on both.
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

    # Each capability reads its own modules, and they are different modules, so they read at
    # once. Both arms run off the same X-drive board, so only one of them asks it.
    arms = [arm for arm in (self.left_x_arm, self.right_x_arm) if arm is not None]
    # Through the arms, not through the accessors above: those refuse on a machine with two, and
    # setup has to reach every capability the machine has whichever arm holds it.
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

    master_version, _ = await self.request_firmware_version()
    reported = {
      "master": master_version,
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

  async def initialize(self, force: bool = False) -> bool:
    """Bring the instrument itself to a known state.

    This moves it. An uninitialized machine runs its initialization procedure, which homes every
    drive and leaves the channels at Z safety. A machine that is already initialized is left where
    it is, apart from raising the channels to Z safety, which the procedure would otherwise have
    guaranteed - nothing may move laterally while a channel is low.

    This is the instrument-level step only. `setup` is what initializes the capabilities after it.

    Args:
      force: run the initialization procedure even if the machine reports itself initialized.

    Returns:
      Whether the machine reported itself already initialized before this ran.
    """
    already_initialized = await self.request_initialization_status()

    if force or not already_initialized:
      logger.debug(
        "machine reports %s - running the initialization procedure (up to %d s)",
        "initialized, but the run was forced" if already_initialized else "not initialized",
        PRE_INITIALIZE_READ_TIMEOUT,
      )
      await self.pre_initialize()
    else:
      logger.debug("machine reports initialized - raising the channels to Z safety only")
      for arm in self.arms:
        if arm.pipettes is not None:
          await arm.pipettes.move_to_safe_z()
        # A head is retracted whatever its own status says: the retract is what keeps it clear
        # of the iSWAP, which shares the arm's X drive and moves while capabilities initialize.
        for head in (arm.head96, arm.head384):
          if head is not None:
            await head.probe_z_max()

    return already_initialized

  async def pre_initialize(self):
    """Run the instrument's initialization procedure.

    Homes every drive and leaves the channels at Z safety. It takes minutes, hence the long read
    timeout.
    """
    return await self.send_command(
      module="C0", command="VI", read_timeout=PRE_INITIALIZE_READ_TIMEOUT
    )

  async def _initialize_arm(self, arm: XArm, already_initialized: bool):
    """Initialize everything one arm carries, one after another.

    The channels, the iSWAP and the 96-head share the arm's X drive, so initializing one while
    another is moving is refused by the machine. They go in the order the legacy routine uses.
    Two arms have two drives, so a machine with both initializes them alongside each other.

    Args:
      arm: the arm whose capabilities to initialize.
      already_initialized: whether the instrument reported itself up before this setup ran.
    """
    if arm.pipettes is None:
      logger.debug("channels: none installed - skipped")
    else:
      tips = await self.request_tip_presence()
      if not already_initialized or any(tips):
        logger.debug(
          "channels: %d of %d carrying tips, instrument %s - initializing",
          sum(tips),
          len(tips),
          "was already up" if already_initialized else "has just been homed",
        )
        await arm.pipettes.initialize()
      else:
        logger.debug("channels: already up and nothing mounted - skipped")

    if arm.iswap is not None:
      if not await self.request_initialization_status("R0"):
        logger.debug("iSWAP reports itself uninitialized - initializing")
        await arm.iswap.initialize()
      await arm.iswap.park()

    for head, name in ((arm.head96, "head96"), (arm.head384, "head384")):
      if head is None:
        continue
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
      # runs on every setup rather than only the first.
      await head.probe_z_max()

  def format_setup_summary(self) -> str:
    """One block describing the machine that was found: how it is reached, what firmware every
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
      f"  Configuration: {', '.join(fitted)}",
      f"  Autoload: {autoload}",
      f"  Arms: {len(arms)}",
    ]
    for arm in arms:
      a = arm.configuration
      # Read through the capability, not the arm's own bit, so the summary cannot report channels
      # the driver did not build. The two disagree only on a machine whose configuration says both.
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
        f"travel {_range(a.x_range)}, workspace {_range(a.workspace_range)}"
      )
      lines.append(f"      channels: {channels} | {' | '.join(heads)} | iSWAP: {iswap}")
    if sum(arm.configuration.pip_installed for arm in arms) > 1:
      lines.append("      (the machine reports one channel count for the instrument, not per arm)")
    return "\n".join(lines)

  # ----------------------------------------
  # Resource model
  # ----------------------------------------

  async def _create_capability_resources(self) -> None:
    """Put what the machine carries on the deck, where it is.

    Read once, at setup: each capability reports where it came to rest and its resource is placed
    there. One already on the deck is reused rather than replaced, so repeated setups do not
    duplicate it.
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
        name=f"{arm.side}_x_arm",
        x=await arm.request_position(),
        width=a.width,
        model=a.model,
        reference_anchor=arm.reference_anchor,
      )
    await self._create_pipette_resources()
    await self._create_autoload_resource()
    await self._create_head_resources()

  async def _create_pipette_resources(self) -> None:
    """Put a resource on the arm for each pipetting channel, where it is.

    One per channel rather than one for the block: they share the arm's X, which the resource tree
    carries for free, but each has its own Y and Z. Children of the arm's resource for the same
    reason the 96-head is. Ones already on the arm are reused, so repeated setups do not duplicate
    them.

    Each carries a `TipMountingShaft` at its lower end, as the channels of a 96-head do: the channel
    is the body that travels, the shaft is the part a tip goes onto, and a collected tip becomes a
    child of that shaft rather than of the channel.
    """
    if self.deck is None:
      return
    arm = next((a for a in self.arms if a.pipettes is not None), None)
    if arm is None or arm.pipettes is None or arm.resource is None:
      return

    # One per channel the machine reported at discovery, not a count assumed here.
    c = arm.pipettes.configuration
    arm.pipettes.resources = []
    for channel in range(len(c.channels)):
      name = f"pipette_channel_{channel}"
      resource = next((r for r in arm.resource.children if r.name == name), None)
      if resource is None:
        width = c.channels[channel].width
        if width is None:
          logger.warning("channel %d reported no width, so it is not modelled", channel)
          continue
        resource = Resource(
          name=name,
          size_x=width,
          size_y=width,
          size_z=c.channel_size_z,
          category="pipette_channel",
          model="hamilton_star_pipette_channel",
        )
        # Along X a channel sits at the arm's own reference point, so its centre lands there.
        anchor = resource.get_anchor(x=CHANNEL_X_REFERENCE_ANCHOR)
        arm.resource.assign_child_resource(
          resource,
          location=Coordinate(arm.resource.get_absolute_size_x() / 2 - anchor.x, 0.0, 0.0),
        )
      arm.pipettes.add_tip_mounting_shaft(resource)
      arm.pipettes.resources.append(resource)

    # Asking where they are records them, as the arm's and the head's reads do.
    await arm.pipettes.request_y_positions()
    for channel in range(len(arm.pipettes.resources)):
      await arm.pipettes.request_stop_disk_z(channel)

  async def _create_head_resources(self) -> None:
    """Put each head on the arm it rides, where it is along Y.

    A child of the arm's resource rather than of the deck, so it follows the arm in X without
    anything having to keep the two in step. One already on the arm is reused rather than replaced,
    so repeated setups do not duplicate it.

    Raises:
      RuntimeError: If a head's X offset was not read, so where it sits across the arm is unknown.
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
        c = head.configuration
        # Where the head is, read before it has a resource to read from: the drive answers on a
        # machine, and a simulated one falls back to where it rests rather than reporting back the
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
          # Channel A1 sits `x_offset` left of the carriage centre, and the arm is located by its
          # own left edge, so A1 lands that far left of the arm's centre. Y is set from the drive
          # below.
          arm.resource.assign_child_resource(
            resource,
            location=Coordinate(arm.resource.get_absolute_size_x() / 2 - c.x_offset, 0.0, 0.0),
          )
        head.resource = resource
        head.update_location_by_reference_point(y=y, z=z)

  async def _create_autoload_resource(self) -> None:
    """Put the autoload's sled on the deck, where it is, and the tray it draws carriers from.

    The tray is placed from the deck's own features rather than read off the machine: it is bolted
    to the instrument and has no drive to report where it is.
    """
    if self.autoload is None or self.deck is None:
      return
    x = await self.autoload.request_x_position()
    self.autoload.resource = self.deck.get_or_create_autoload_sled(
      name="autoload_sled",
      x=x,
      reference_point_from_left=self.autoload.configuration.reference_point_from_sled_left_edge,
    )
    self.autoload.update_location_by_reference_point(x)
    self.deck.get_or_create_autoload_loading_tray(name="autoload_loading_tray")
