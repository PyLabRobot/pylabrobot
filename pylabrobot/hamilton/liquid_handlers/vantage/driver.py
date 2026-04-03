"""VantageDriver: inherits HamiltonLiquidHandler, adds Vantage-specific config and error handling."""

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

  Inherits USB I/O, command assembly, and background reading from HamiltonLiquidHandler.
  Adds Vantage-specific firmware parsing, error handling, and subsystem management.
  """

  def __init__(
    self,
    device_address: Optional[int] = None,
    serial_number: Optional[str] = None,
    packet_read_timeout: int = 3,
    read_timeout: int = 60,
    write_timeout: int = 30,
  ):
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
    return 4

  @property
  def num_channels(self) -> int:
    if self._num_channels is None:
      raise RuntimeError("Driver not set up - call setup() first.")
    return self._num_channels

  def get_id_from_fw_response(self, resp: str) -> Optional[int]:
    parsed = parse_vantage_fw_string(resp, {"id": "int"})
    if "id" in parsed and parsed["id"] is not None:
      return int(parsed["id"])
    return None

  def check_fw_string_error(self, resp: str) -> None:
    if "er" in resp and "er0" not in resp:
      raise vantage_response_string_to_error(resp)

  def _parse_response(self, resp: str, fmt: Any) -> dict:
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
    return self._traversal_height

  # -- lifecycle -------------------------------------------------------------

  async def setup(
    self,
    skip_loading_cover: bool = False,
    skip_core96: bool = False,
    skip_ipg: bool = False,
  ):
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

    # Arm pre-initialization.
    arm_initialized = await self.arm_request_instrument_initialization_status()
    if not arm_initialized:
      await self.arm_pre_initialize()

    # Create backends.
    self.pip = VantagePIPBackend(self)
    self.x_arm = VantageXArm(driver=self)
    self.loading_cover = VantageLoadingCover(driver=self)

    # Initialize PIP channels.
    pip_channels_initialized = await self.pip_request_initialization_status()
    if not pip_channels_initialized or any(tip_presences):
      await self.pip_initialize(
        x_position=[7095] * self.num_channels,
        y_position=[3891, 3623, 3355, 3087, 2819, 2551, 2283, 2016],
        begin_z_deposit_position=[int(self._traversal_height * 10)] * self.num_channels,
        end_z_deposit_position=[1235] * self.num_channels,
        minimal_height_at_command_end=[int(self._traversal_height * 10)] * self.num_channels,
        tip_pattern=[True] * self.num_channels,
        tip_type=[1] * self.num_channels,
        ts=70,
      )

    # Loading cover.
    if not skip_loading_cover:
      loading_cover_initialized = await self.loading_cover.request_initialization_status()
      if not loading_cover_initialized:
        await self.loading_cover.initialize()

    # Core 96 head.
    core96_initialized = await self.core96_request_initialization_status()
    if not core96_initialized and not skip_core96:
      self.head96 = VantageHead96Backend(self)
      await self.core96_initialize(
        x_position=7347,
        y_position=2684,
        minimal_traverse_height_at_begin_of_command=int(self._traversal_height * 10),
        minimal_height_at_command_end=int(self._traversal_height * 10),
        end_z_deposit_position=2420,
      )
    else:
      # Even if already initialized, create the backend.
      self.head96 = VantageHead96Backend(self) if not skip_core96 else None

    # IPG.
    if not skip_ipg:
      self.ipg = IPGBackend(driver=self)
      ipg_initialized = await self.ipg.request_initialization_status()
      if not ipg_initialized:
        await self.ipg.initialize()
      if not await self.ipg.get_parking_status():
        await self.ipg.park()
    else:
      self.ipg = None

    # Initialize subsystems.
    for sub in self._subsystems:
      await sub._on_setup()

  @property
  def _subsystems(self) -> List[Any]:
    """All active subsystems, for lifecycle management."""
    subs: List[Any] = []
    if self.pip is not None:
      subs.append(self.pip)
    if self.head96 is not None:
      subs.append(self.head96)
    if self.ipg is not None:
      subs.append(self.ipg)
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
    x_position: List[int],
    y_position: List[int],
    begin_z_deposit_position: Optional[List[int]] = None,
    end_z_deposit_position: Optional[List[int]] = None,
    minimal_height_at_command_end: Optional[List[int]] = None,
    tip_pattern: Optional[List[bool]] = None,
    tip_type: Optional[List[int]] = None,
    ts: int = 0,
  ) -> None:
    """Initialize PIP channels (A1PM:DI)."""

    if begin_z_deposit_position is None:
      begin_z_deposit_position = [0] * self.num_channels
    if end_z_deposit_position is None:
      end_z_deposit_position = [0] * self.num_channels
    if minimal_height_at_command_end is None:
      minimal_height_at_command_end = [3600] * self.num_channels
    if tip_pattern is None:
      tip_pattern = [False] * self.num_channels
    if tip_type is None:
      tip_type = [4] * self.num_channels

    await self.send_command(
      module="A1PM",
      command="DI",
      xp=x_position,
      yp=y_position,
      tp=begin_z_deposit_position,
      tz=end_z_deposit_position,
      te=minimal_height_at_command_end,
      tm=tip_pattern,
      tt=tip_type,
      ts=ts,
    )

  async def query_tip_presence(self) -> List[bool]:
    """Query tip presence on all channels (A1PM:QA)."""
    resp = await self.send_command(module="A1PM", command="QA", fmt={"rt": "[int]"})
    presences_int: List[int] = resp["rt"]
    return [bool(p) for p in presences_int]

  # -- core 96 commands used during setup (A1HM) -----------------------------

  async def core96_request_initialization_status(self) -> bool:
    """Check if Core96 head is initialized (A1HM:QW)."""
    resp = await self.send_command(module="A1HM", command="QW", fmt={"qw": "int"})
    return resp is not None and resp["qw"] == 1

  async def core96_initialize(
    self,
    x_position: int = 7347,
    y_position: int = 2684,
    z_position: int = 0,
    minimal_traverse_height_at_begin_of_command: int = 2450,
    minimal_height_at_command_end: int = 2450,
    end_z_deposit_position: int = 2420,
    tip_type: int = 4,
  ) -> None:
    """Initialize Core 96 head (A1HM:DI)."""
    await self.send_command(
      module="A1HM",
      command="DI",
      xp=x_position,
      yp=y_position,
      zp=z_position,
      th=minimal_traverse_height_at_begin_of_command,
      te=minimal_height_at_command_end,
      tz=end_z_deposit_position,
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
    """Set the instrument LED color (C0AM:LI)."""
    if blink_interval is not None and mode != "blink":
      raise ValueError("blink_interval is only used when mode is 'blink'.")

    await self.send_command(
      module="C0AM",
      command="LI",
      li={"on": 1, "off": 0, "blink": 2}[mode],
      os=intensity,
      ok=blink_interval or 750,
      ol=f"{white} {red} {green} {blue} {uv}",
    )
