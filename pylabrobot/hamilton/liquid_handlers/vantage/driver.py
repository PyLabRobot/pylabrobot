"""VantageDriver: inherits HamiltonLiquidHandler, adds Vantage-specific config and error handling."""

import asyncio
import random

from typing import TYPE_CHECKING, Any, List, Literal, Optional, Union

from pylabrobot.capabilities.liquid_handling.head96_backend import Head96Backend
from pylabrobot.capabilities.liquid_handling.pip_backend import PIPBackend
from pylabrobot.hamilton.liquid_handlers.base import HamiltonLiquidHandler
from pylabrobot.resources.hamilton import TipPickupMethod, TipSize

from .errors import vantage_response_string_to_error
from .fw_parsing import parse_vantage_fw_string

if TYPE_CHECKING:
  from .ipg import IPGBackend
  from .loading_cover import VantageLoadingCover
  from .x_arm import VantageXArm


class VantageDriver(HamiltonLiquidHandler):
  """Driver for Hamilton Vantage liquid handlers.

  Inherits USB I/O, command assembly, and background reading from
  :class:`~pylabrobot.hamilton.liquid_handlers.base.HamiltonLiquidHandler`.
  Adds Vantage-specific firmware parsing, error handling, and subsystem management.

  The Vantage uses USB product ID ``0x8003`` and a 4-character module ID length in
  its firmware protocol (compared to 2 characters on the STAR).

  **Setup flow** (see :meth:`setup`):

  1. Open USB connection (inherited from HamiltonLiquidHandler).
  2. Discover channel count by querying tip presence.
  3. Pre-initialize the arm module (A1AM).
  4. Create and initialize PIP, X-arm, and loading cover subsystems.
  5. Optionally initialize the Core 96-head (A1HM) and IPG (A1RM) if present.

  After setup completes, subsystem backends are available as ``self.pip``,
  ``self.head96``, ``self.ipg``, ``self.x_arm``, and ``self.loading_cover``.
  """

  def __init__(
    self,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 60,
    write_timeout: int = 30,
  ):
    """Initialize the VantageDriver.

    Args:
      device_address: USB device address. If None, auto-detected.
      serial_number: USB serial number filter. If None, connects to the first
        matching device.
      packet_read_timeout: Timeout in seconds for reading individual USB packets.
      read_timeout: Timeout in seconds for reading a complete firmware response.
      write_timeout: Timeout in seconds for writing a firmware command.
    """
    super().__init__(
      id_product=0x8003,
      device_address=device_address,
      serial_number=serial_number,
      packet_read_timeout=packet_read_timeout,
      read_timeout=read_timeout,
      write_timeout=write_timeout,
    )

    self._num_channels: Optional[int] = None
    self._traversal_height: float = 245.0

    # Populated during setup().
    self.pip: Optional[PIPBackend] = None  # set in setup()
    self.head96: Optional[Head96Backend] = None  # set in setup() if installed
    self.ipg: Optional["IPGBackend"] = None  # set in setup() if installed
    self.x_arm: Optional["VantageXArm"] = None  # set in setup()
    self.loading_cover: Optional["VantageLoadingCover"] = None  # set in setup()

  # -- HamiltonLiquidHandler abstract methods --------------------------------

  @property
  def module_id_length(self) -> int:
    """Length of the module identifier prefix in firmware messages.

    The Vantage uses 4-character module IDs (e.g. ``A1PM``, ``A1HM``, ``A1RM``),
    compared to the STAR's 2-character IDs (e.g. ``C0``, ``R0``).
    """
    return 4

  @property
  def num_channels(self) -> int:
    """Number of PIP channels discovered during setup.

    Raises:
      RuntimeError: If the driver has not been set up yet.
    """
    if self._num_channels is None:
      raise RuntimeError("Driver not set up - call setup() first.")
    return self._num_channels

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    """Extract the command ID from a Vantage firmware response string.

    Args:
      resp: Raw firmware response string.

    Returns:
      The integer command ID, or None if the response does not contain one.
    """
    parsed = parse_vantage_fw_string(resp, {"id": "int"})
    if "id" in parsed and parsed["id"] is not None:
      return int(parsed["id"])
    return None

  def check_fw_string_error(self, resp: str) -> None:
    """Check a firmware response string for errors and raise if found.

    Args:
      resp: Raw firmware response string.

    Raises:
      VantageFirmwareError: If the response contains a non-zero error code.
    """
    # FIXME: "er0" substring check also suppresses er01-er09 (error codes 1-9).
    # Pre-existing bug from legacy. Needs proper regex-based error detection.
    if "er" in resp and "er0" not in resp:
      raise vantage_response_string_to_error(resp)

  def _parse_response(self, resp: str, fmt: Any) -> dict:
    """Parse a Vantage firmware response string using the given format specification.

    Args:
      resp: Raw firmware response string.
      fmt: Format dictionary mapping parameter names to type strings
        (e.g. ``{"qw": "int"}``).

    Returns:
      Dictionary of parsed key-value pairs.
    """
    return parse_vantage_fw_string(resp, fmt)

  async def define_tip_needle(
    self,
    tip_type_table_index: int,
    has_filter: bool,
    tip_length: int,
    maximum_tip_volume: int,
    tip_size: TipSize,
    pickup_method: TipPickupMethod,
  ) -> None:
    """Define a tip/needle type in the firmware tip table (A1AM:TT).

    Values set here are temporary and apply only until power OFF or RESET.

    Args:
      tip_type_table_index: Index in the tip table (0-99).
      has_filter: Whether the tip has a filter.
      tip_length: Tip length [0.1mm] (1-1999).
      maximum_tip_volume: Maximum volume of tip [0.1ul] (1-56000). Automatically limited to
        max channel capacity.
      tip_size: Type of tip collar (tip type identification).
      pickup_method: Tip pick-up method.
    """
    if not 0 <= tip_type_table_index <= 99:
      raise ValueError("tip_type_table_index must be between 0 and 99")
    if not 1 <= tip_length <= 1999:
      raise ValueError("tip_length must be between 1 and 1999")
    if not 1 <= maximum_tip_volume <= 56000:
      raise ValueError("maximum_tip_volume must be between 1 and 56000")

    await self.send_command(
      module="A1AM",
      command="TT",
      ti=f"{tip_type_table_index:02}",
      tf=has_filter,
      tl=f"{tip_length:04}",
      tv=f"{maximum_tip_volume:05}",
      tg=tip_size.value,
      tu=pickup_method.value,
    )

  # -- traversal height ------------------------------------------------------

  def set_minimum_traversal_height(self, traversal_height: float) -> None:
    """Set the minimum traversal height (mm). Used as default for z-safety parameters."""
    assert 0 < traversal_height < 285, "Traversal height must be between 0 and 285 mm"
    self._traversal_height = traversal_height

  @property
  def traversal_height(self) -> float:
    """Current minimum traversal height in mm.

    This value is used as the default Z-safety height for all subsystem backends
    (PIP, Head96, IPG) when their ``BackendParams`` leave the traverse height as None.
    Default is 245.0mm. Can be changed via :meth:`set_minimum_traversal_height`.
    """
    return self._traversal_height

  # -- lifecycle -------------------------------------------------------------

  async def setup(
    self,
    skip_loading_cover: bool = False,
    skip_core96: bool = False,
    skip_ipg: bool = False,
  ):
    """Initialize the Vantage hardware and all subsystem backends.

    This method opens the USB connection, discovers the channel count, and
    initializes subsystems (PIP, loading cover, Core 96-head, IPG, X-arm).
    Subsystems can be skipped with the ``skip_*`` flags.

    Args:
      skip_loading_cover: If True, skip loading cover initialization.
      skip_core96: If True, skip Core 96-head initialization.
      skip_ipg: If True, skip IPG (Integrated Plate Gripper) initialization.
    """
    await super().setup()
    self.id_ = 0

    # Import here to avoid circular imports.
    from .head96_backend import VantageHead96Backend
    from .ipg import IPGBackend
    from .loading_cover import VantageLoadingCover
    from .pip_backend import VantagePIPBackend
    from .x_arm import VantageXArm

    # Discover channel count.
    tip_presences = await self.query_tip_presence()
    self._num_channels = len(tip_presences)

    # Arm pre-initialization (device-level, not subsystem-specific).
    arm_initialized = await self.arm_request_instrument_initialization_status()
    if not arm_initialized:
      await self.arm_pre_initialize()

    # Create subsystem instances.
    self.pip = VantagePIPBackend(self)
    self.x_arm = VantageXArm(driver=self)
    self.loading_cover = VantageLoadingCover(driver=self) if not skip_loading_cover else None
    self.head96 = VantageHead96Backend(self) if not skip_core96 else None
    self.ipg = IPGBackend(driver=self) if not skip_ipg else None

    # Each subsystem's _on_setup() handles its own initialization check.
    for sub in self._subsystems:
      await sub._on_setup()

  @property
  def _subsystems(self) -> List[Any]:
    """Non-capability subsystems owned directly by the driver.

    ``pip``, ``head96``, and ``ipg`` are intentionally excluded: their lifecycle
    (``_on_setup`` / ``_on_stop``) is driven by the capability frontends on the
    :class:`Vantage` device. Including them here would initialize each backend
    twice — once from :meth:`setup` and once from ``Vantage.setup()``.
    """
    subs: List[Any] = []
    if self.x_arm is not None:
      subs.append(self.x_arm)
    if self.loading_cover is not None:
      subs.append(self.loading_cover)
    return subs

  async def stop(self):
    # Stop subsystems first (they may need to send firmware commands).
    for sub in reversed(self._subsystems):
      await sub._on_stop()
    await super().stop()
    self._num_channels = None
    self.pip = None
    self.head96 = None
    self.ipg = None
    self.x_arm = None
    self.loading_cover = None

  # -- arm commands (A1AM) ---------------------------------------------------

  async def arm_request_instrument_initialization_status(self) -> bool:
    """Check if the arm module is initialized (A1AM:QW)."""
    resp = await self.send_command(module="A1AM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def arm_pre_initialize(self) -> None:
    """Pre-initialize the arm module (A1AM:MI)."""
    await self.send_command(module="A1AM", command="MI")

  # -- pip module commands (A1PM) used during setup --------------------------

  async def pip_request_initialization_status(self) -> bool:
    """Check if PIP channels are initialized (A1PM:QW)."""
    resp = await self.send_command(module="A1PM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def pip_initialize(
    self,
    x_position: List[float],
    y_position: List[float],
    begin_z_deposit_position: Optional[List[float]] = None,
    end_z_deposit_position: Optional[List[float]] = None,
    minimal_height_at_command_end: Optional[List[float]] = None,
    tip_pattern: Optional[List[bool]] = None,
    tip_type: Optional[List[int]] = None,
    TODO_DI_2: int = 0,
  ) -> None:
    """Initialize PIP channels (A1PM:DI).

    Args:
      x_position: X position [mm].
      y_position: Y position [mm].
      begin_z_deposit_position: Begin of tip deposit process (Z-discard range) [mm].
      end_z_deposit_position: Z deposit position [mm] (collar bearing position).
      minimal_height_at_command_end: Minimal height at command end [mm].
      tip_pattern: Tip pattern (channels involved). False = not involved, True = involved.
      tip_type: Tip type (see command TT / define_tip_needle).
      TODO_DI_2: Unknown firmware parameter (maps to firmware key ``ts``).
    """

    if begin_z_deposit_position is None:
      begin_z_deposit_position = [0.0] * self.num_channels
    if end_z_deposit_position is None:
      end_z_deposit_position = [0.0] * self.num_channels
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [360.0] * self.num_channels
    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    if tip_type is None:
      tip_type = [4] * self.num_channels

    await self.send_command(
      module="A1PM",
      command="DI",
      xp=[round(v * 10) for v in x_position],
      yp=[round(v * 10) for v in y_position],
      tp=[round(v * 10) for v in begin_z_deposit_position],
      tz=[round(v * 10) for v in end_z_deposit_position],
      te=[round(v * 10) for v in minimal_height_at_command_end],
      tm=tip_pattern,
      tt=tip_type,
      ts=TODO_DI_2,
    )

  async def query_tip_presence(self) -> List[bool]:
    """Query tip presence on all channels (A1PM:QA)."""
    resp = await self.send_command(module="A1PM", command="QA", fmt={"rt": "[int]"})
    if resp is None:
      return [False] * (self._num_channels or 8)
    presences_int: List[int] = resp["rt"]
    return [bool(p) for p in presences_int]

  # -- core 96 commands used during setup (A1HM) -----------------------------

  async def core96_request_initialization_status(self) -> bool:
    """Check if Core96 head is initialized (A1HM:QW)."""
    resp = await self.send_command(module="A1HM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def core96_initialize(
    self,
    x_position: float = 734.7,
    y_position: float = 268.4,
    z_position: float = 0.0,
    minimal_traverse_height_at_begin_of_command: float = 245.0,
    minimal_height_at_command_end: float = 245.0,
    end_z_deposit_position: float = 242.0,
    tip_type: int = 4,
  ) -> None:
    """Initialize the Core 96 head (A1HM:DI).

    Args:
      x_position: X position [mm].
      y_position: Y position [mm].
      z_position: Z position [mm].
      minimal_traverse_height_at_begin_of_command: Minimal traverse height at begin of
        command [mm].
      minimal_height_at_command_end: Minimal height at command end [mm].
      end_z_deposit_position: Z deposit position [mm] (collar bearing position).
      tip_type: Tip type (see command TT / define_tip_needle).
    """
    await self.send_command(
      module="A1HM",
      command="DI",
      xp=round(x_position * 10),
      yp=round(y_position * 10),
      zp=round(z_position * 10),
      th=round(minimal_traverse_height_at_begin_of_command * 10),
      te=round(minimal_height_at_command_end * 10),
      tz=round(end_z_deposit_position * 10),
      tt=tip_type,
    )

  # -- LED (C0AM) ------------------------------------------------------------

  async def set_led_color(
    self,
    mode: Union[Literal["on"], Literal["off"], Literal["blink"]],
    intensity: int,
    white: int,
    red: int,
    green: int,
    blue: int,
    uv: int,
    blink_interval: Optional[int] = None,
  ) -> None:
    """Set the instrument LED color (C0AM:LI).

    Args:
      mode: LED mode. One of "on", "off", or "blink".
      intensity: LED intensity (0-100).
      white: White LED value (0-100).
      red: Red LED value (0-100).
      green: Green LED value (0-100).
      blue: Blue LED value (0-100).
      uv: UV LED value (0-100).
      blink_interval: Blink interval in ms. Only used when mode is "blink".
    """
    if blink_interval is not None and mode != "blink":
      raise ValueError("blink_interval is only used when mode is 'blink'.")

    await self.send_command(
      module="C0AM",
      command="LI",
      li={"on": 1, "off": 0, "blink": 2}[mode],
      os=intensity,
      ok=blink_interval if blink_interval is not None else 750,
      ol=f"{white} {red} {green} {blue} {uv}",
    )

  async def disco_mode(self):
    """Easter egg."""
    for _ in range(69):
      r, g, b = random.randint(30, 100), random.randint(30, 100), random.randint(30, 100)
      await self.set_led_color("on", intensity=100, white=0, red=r, green=g, blue=b, uv=0)
      await asyncio.sleep(0.1)

  async def russian_roulette(self):
    """Dangerous easter egg."""
    sure = input(
      "Are you sure you want to play Russian Roulette? This will turn on the uv-light "
      "with a probability of 1/6. (yes/no) "
    )
    if sure.lower() != "yes":
      print("boring")
      return

    if random.randint(1, 6) == 6:
      await self.set_led_color("on", intensity=100, white=100, red=100, green=0, blue=0, uv=100)
      print("You lost.")
    else:
      await self.set_led_color("on", intensity=100, white=100, red=0, green=100, blue=0, uv=0)
      print("You won.")

    await asyncio.sleep(5)
    await self.set_led_color("on", intensity=100, white=100, red=100, green=100, blue=100, uv=0)
