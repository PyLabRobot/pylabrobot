"""STAR Head96 backend: translates Head96 operations into STAR firmware commands."""

from __future__ import annotations

import datetime
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Literal, Optional, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.liquid_handling.head96_backend import Head96Backend
from pylabrobot.capabilities.liquid_handling.standard import (
  DropTipRack,
  MultiHeadAspirationContainer,
  MultiHeadAspirationPlate,
  MultiHeadDispenseContainer,
  MultiHeadDispensePlate,
  PickupTipRack,
)
from pylabrobot.resources import Coordinate, Resource
from pylabrobot.resources.hamilton import HamiltonTip, TipSize

from .pip_backend import _dispensing_mode_for_op  # noqa: F401

if TYPE_CHECKING:
  from .driver import STARDriver

logger = logging.getLogger(__name__)

# Conversion factors for 96-Head (mm per increment / uL per increment)
_Z_DRIVE_MM_PER_INCREMENT = 0.005
_Y_DRIVE_MM_PER_INCREMENT = 0.015625
_DISPENSING_DRIVE_MM_PER_INCREMENT = 0.001025641026
_DISPENSING_DRIVE_UL_PER_INCREMENT = 0.019340933
_SQUEEZER_DRIVE_MM_PER_INCREMENT = 0.0002086672009


def _channel_pattern_to_hex(pattern: List[bool]) -> str:
  """Convert a list of 96 booleans to the hex string expected by firmware."""
  if len(pattern) != 96:
    raise ValueError("channel_pattern must be a list of 96 boolean values")
  channel_pattern_bin_str = reversed(["1" if x else "0" for x in pattern])
  return hex(int("".join(channel_pattern_bin_str), 2)).upper()[2:]


class STARHead96Backend(Head96Backend):
  """Translates Head96 operations into STAR firmware commands via the driver."""

  def __init__(self, driver: STARDriver, traversal_height: float = 245.0, deck=None):
    self.driver = driver
    self.traversal_height = traversal_height
    self.deck = deck

  @contextmanager
  def use_traversal_height(self, height: float):
    """Temporarily override the traversal height for all Head96 operations."""
    original = self.traversal_height
    self.traversal_height = height
    try:
      yield
    finally:
      self.traversal_height = original

  # ---------------------------------------------------------------------------
  # Lifecycle
  # ---------------------------------------------------------------------------

  async def _on_setup(self, backend_params: Optional[BackendParams] = None):
    """Initialize the 96-head if not already initialized, and cache firmware info.

    Mirrors the legacy initialization flow:
      1. Check if already initialized (H0:QW).
      2. If not, send the initialize command (C0:EI).
      3. Cache firmware version and configuration for version-specific behavior.
    """
    already_initialized = await self.request_initialization_status()
    if not already_initialized:
      if self.deck is not None:
        trash96 = self.deck.get_trash_area96()
        loc = self._position_96_head_in_resource(trash96)
        await self.initialize(x=loc.x, y=loc.y, z=loc.z)
      else:
        await self.initialize()

    # Cache firmware version and configuration for version-specific behavior
    self.fw_version = await self.request_firmware_version()

  async def _on_stop(self):
    """Move to Z safety and park the 96-head on shutdown."""
    try:
      await self.move_to_z_safety()
    except Exception:
      logger.warning("Failed to move 96-head to Z safety during stop", exc_info=True)
    try:
      await self.park()
    except Exception:
      logger.warning("Failed to park 96-head during stop", exc_info=True)

  # ---------------------------------------------------------------------------
  # Initialization & status
  # ---------------------------------------------------------------------------

  async def request_firmware_version(self) -> datetime.date:
    """Request 96-head firmware version (H0:RF)."""
    from pylabrobot.hamilton.liquid_handlers.star.fw_parsing import (
      parse_star_firmware_version_date,
    )

    resp = await self.driver.send_command(module="H0", command="RF")
    if resp is None:
      # Chatterbox / simulation: return a sensible default
      return datetime.date(2024, 1, 1)
    return parse_star_firmware_version_date(str(resp))

  async def request_initialization_status(self) -> bool:
    """Request 96-head initialization status (H0:QW).

    Returns:
      True if the 96-head is initialized, False otherwise.
    """
    response = await self.driver.send_command(module="H0", command="QW", fmt="qw#")
    if response is None:
      return False
    return bool(response.get("qw", 0) == 1)

  async def initialize(
    self,
    x: float = 0,
    y: float = 0,
    z: float = 0,
    minimum_height_command_end: Optional[float] = None,
  ):
    """Initialize the CoRe 96 Head (C0:EI).

    This sends tips to the specified position (typically the trash area) and
    initializes all axes.

    Args:
      x: X position in mm for A1 channel of the 96-head during initialization.
      y: Y position in mm for A1 channel of the 96-head during initialization.
      z: Z position in mm. Default 0.
      minimum_height_command_end: Minimum Z height in mm at command end.
        If None, uses the backend's ``traversal_height``.
    """
    ze = (
      minimum_height_command_end
      if minimum_height_command_end is not None
      else self.traversal_height
    )

    await self.driver.send_command(
      module="C0",
      command="EI",
      read_timeout=60,
      xs=f"{abs(round(x * 10)):05}",
      xd=0 if x >= 0 else 1,
      yh=f"{abs(round(y * 10)):04}",
      za=f"{round(z * 10):04}",
      ze=f"{round(ze * 10):04}",
    )

  async def initialize_dispensing_drive_and_squeezer(
    self,
    squeezer_speed: float = 15.0,
    squeezer_acceleration: float = 62.0,
    squeezer_current_limit: int = 15,
    dispensing_drive_current_limit: int = 7,
  ):
    """Initialize 96-head's dispensing drive AND squeezer drive (H0:PI).

    This command:
      - Drops any tips that might be on the channels (in place, without moving to trash).
      - Moves the dispense drive to volume position 215.92 uL
        (after tip pickup it will be at 218.19 uL).

    Args:
      squeezer_speed: Speed of the movement in mm/sec. Must be between 0.01 and 16.69.
      squeezer_acceleration: Acceleration of the movement in mm/sec**2. Must be between
        1.04 and 62.6.
      squeezer_current_limit: Current limit for the squeezer drive (1-15).
      dispensing_drive_current_limit: Current limit for the dispensing drive (1-15).
    """
    if not (0.01 <= squeezer_speed <= 16.69):
      raise ValueError(
        f"squeezer_speed must be between 0.01 and 16.69 mm/sec, got {squeezer_speed}"
      )
    if not (1.04 <= squeezer_acceleration <= 62.6):
      raise ValueError(
        f"squeezer_acceleration must be between 1.04 and 62.6 mm/sec**2, got {squeezer_acceleration}"
      )
    if not (1 <= squeezer_current_limit <= 15):
      raise ValueError(
        f"squeezer_current_limit must be between 1 and 15, got {squeezer_current_limit}"
      )
    if not (1 <= dispensing_drive_current_limit <= 15):
      raise ValueError(
        f"dispensing_drive_current_limit must be between 1 and 15, got {dispensing_drive_current_limit}"
      )

    squeezer_speed_inc = round(squeezer_speed / _SQUEEZER_DRIVE_MM_PER_INCREMENT)
    squeezer_accel_inc = round(squeezer_acceleration / _SQUEEZER_DRIVE_MM_PER_INCREMENT)

    await self.driver.send_command(
      module="H0",
      command="PI",
      sv=f"{squeezer_speed_inc:05}",
      sr=f"{squeezer_accel_inc:06}",
      sw=f"{squeezer_current_limit:02}",
      dw=f"{dispensing_drive_current_limit:02}",
    )

  # ---------------------------------------------------------------------------
  # Movement commands
  # ---------------------------------------------------------------------------

  async def move_to_z_safety(self):
    """Move 96-Head to Z safety coordinate, i.e. z=342.5 mm (C0:EV)."""
    await self.driver.send_command(module="C0", command="EV")

  async def park(self):
    """Park the 96-head (H0:MO).

    Uses firmware default speeds and accelerations.
    """
    await self.driver.send_command(module="H0", command="MO")

  async def move_to_coordinate(
    self,
    coordinate: Coordinate,
    minimum_height_at_beginning_of_a_command: float = 342.5,
  ):
    """Move 96-Head to a defined coordinate (C0:EM).

    Args:
      coordinate: Coordinate of A1 in mm. If tips are present, refers to tip bottom;
        if not present, refers to channel bottom.
      minimum_height_at_beginning_of_a_command: Minimum Z height in mm before lateral
        movement begins. Must be between 0 and 342.5.
    """
    if not (0 <= minimum_height_at_beginning_of_a_command <= 342.5):
      raise ValueError("minimum_height_at_beginning_of_a_command must be between 0 and 342.5")

    await self.driver.send_command(
      module="C0",
      command="EM",
      xs=f"{abs(round(coordinate.x * 10)):05}",
      xd=0 if coordinate.x >= 0 else 1,
      yh=f"{round(coordinate.y * 10):04}",
      za=f"{round(coordinate.z * 10):04}",
      zh=f"{round(minimum_height_at_beginning_of_a_command * 10):04}",
    )

  async def move_y(
    self,
    y: float,
    speed: float = 300.0,
    acceleration: float = 300.0,
    current_protection_limiter: int = 15,
  ):
    """Move the 96-head to a specified Y-axis coordinate (H0:YA).

    Args:
      y: Target Y coordinate in mm. Valid range: [93.75, 562.5].
      speed: Movement speed in mm/sec. Valid range: [0.78125, 625.0].
      acceleration: Movement acceleration in mm/sec**2. Valid range: [78.125, 781.25].
      current_protection_limiter: Motor current limit (0-15, hardware units).
    """
    if not (93.75 <= y <= 562.5):
      raise ValueError("y must be between 93.75 and 562.5 mm")
    if not (0.78125 <= speed <= 625.0):
      raise ValueError("speed must be between 0.78125 and 625.0 mm/sec")
    if not (78.125 <= acceleration <= 781.25):
      raise ValueError("acceleration must be between 78.125 and 781.25 mm/sec**2")
    if not (isinstance(current_protection_limiter, int) and 0 <= current_protection_limiter <= 15):
      raise ValueError("current_protection_limiter must be an integer between 0 and 15")

    y_inc = round(y / _Y_DRIVE_MM_PER_INCREMENT)
    speed_inc = round(speed / _Y_DRIVE_MM_PER_INCREMENT)
    accel_inc = round(acceleration / _Y_DRIVE_MM_PER_INCREMENT)

    await self.driver.send_command(
      module="H0",
      command="YA",
      ya=f"{y_inc:05}",
      yv=f"{speed_inc:05}",
      yr=f"{accel_inc:05}",
      yw=f"{current_protection_limiter:02}",
    )

  async def move_z(
    self,
    z: float,
    speed: float = 80.0,
    acceleration: float = 300.0,
    current_protection_limiter: int = 15,
  ):
    """Move the 96-head to a specified Z-axis coordinate (H0:ZA).

    Args:
      z: Target Z coordinate in mm. Valid range: [180.5, 342.5].
      speed: Movement speed in mm/sec. Valid range: [0.25, 100.0].
      acceleration: Movement acceleration in mm/sec**2. Valid range: [25.0, 500.0].
      current_protection_limiter: Motor current limit (0-15, hardware units).
    """
    if not (180.5 <= z <= 342.5):
      raise ValueError("z must be between 180.5 and 342.5 mm")
    if not (0.25 <= speed <= 100.0):
      raise ValueError("speed must be between 0.25 and 100.0 mm/sec")
    if not (25.0 <= acceleration <= 500.0):
      raise ValueError("acceleration must be between 25.0 and 500.0 mm/sec**2")
    if not (isinstance(current_protection_limiter, int) and 0 <= current_protection_limiter <= 15):
      raise ValueError("current_protection_limiter must be an integer between 0 and 15")

    z_inc = round(z / _Z_DRIVE_MM_PER_INCREMENT)
    speed_inc = round(speed / _Z_DRIVE_MM_PER_INCREMENT)
    accel_inc = round(acceleration / _Z_DRIVE_MM_PER_INCREMENT)

    await self.driver.send_command(
      module="H0",
      command="ZA",
      za=f"{z_inc:05}",
      zv=f"{speed_inc:05}",
      zr=f"{accel_inc:06}",
      zw=f"{current_protection_limiter:02}",
    )

  async def dispensing_drive_move_to_position(
    self,
    position: float,
    speed: float = 261.1,
    stop_speed: float = 0,
    acceleration: float = 17406.84,
    current_protection_limiter: int = 15,
  ):
    """Move dispensing drive to absolute position in uL (H0:DQ).

    Args:
      position: Position in uL. Must be between 0 and 1244.59.
      speed: Speed in uL/s. Must be between 0.1 and 1063.75.
      stop_speed: Stop speed in uL/s. Must be between 0 and 1063.75.
      acceleration: Acceleration in uL/s**2. Must be between 96.7 and 17406.84.
      current_protection_limiter: Current protection limiter (0-15).
    """
    if not (0 <= position <= 1244.59):
      raise ValueError("position must be between 0 and 1244.59")
    if not (0.1 <= speed <= 1063.75):
      raise ValueError("speed must be between 0.1 and 1063.75")
    if not (0 <= stop_speed <= 1063.75):
      raise ValueError("stop_speed must be between 0 and 1063.75")
    if not (96.7 <= acceleration <= 17406.84):
      raise ValueError("acceleration must be between 96.7 and 17406.84")
    if not (0 <= current_protection_limiter <= 15):
      raise ValueError("current_protection_limiter must be between 0 and 15")

    pos_inc = round(position / _DISPENSING_DRIVE_UL_PER_INCREMENT)
    speed_inc = round(speed / _DISPENSING_DRIVE_UL_PER_INCREMENT)
    stop_inc = round(stop_speed / _DISPENSING_DRIVE_UL_PER_INCREMENT)
    accel_inc = round(acceleration / _DISPENSING_DRIVE_UL_PER_INCREMENT)

    await self.driver.send_command(
      module="H0",
      command="DQ",
      dq=f"{pos_inc:05}",
      dv=f"{speed_inc:05}",
      du=f"{stop_inc:05}",
      dr=f"{accel_inc:06}",
      dw=f"{current_protection_limiter:02}",
    )

  async def dispensing_drive_move_to_home_volume(self):
    """Move the 96-head dispensing drive into its home position, vol=0.0 uL (H0:DL).

    .. warning::
      This firmware command is known to be broken: the 96-head dispensing drive
      cannot reach vol=0.0 uL, which typically raises a position-out-of-permitted-area
      error.
    """
    logger.warning(
      "dispensing_drive_move_to_home_volume is a known broken firmware command: "
      "the 96-head dispensing drive cannot reach vol=0.0 uL."
    )
    await self.driver.send_command(module="H0", command="DL")

  # ---------------------------------------------------------------------------
  # Query commands
  # ---------------------------------------------------------------------------

  async def request_position(self) -> Coordinate:
    """Request position of the CoRe 96 Head (C0:QI).

    Returns:
      Coordinate: x, y, z in mm. The position of A1, considering tip length
        if tips are mounted.
    """
    resp = await self.driver.send_command(module="C0", command="QI", fmt="xs#####xd#yh####za####")
    if resp is None:
      return Coordinate(x=0, y=0, z=0)

    x = resp["xs"] / 10
    y = resp["yh"] / 10
    z = resp["za"] / 10
    x = x if resp["xd"] == 0 else -x

    return Coordinate(x=x, y=y, z=z)

  async def request_tip_presence(self) -> int:
    """Request tip presence on the 96-Head (C0:QH).

    Note: This queries the firmware's internal memory. It does not directly
    sense whether tips are physically present.

    Returns:
      0 = no tips, 1 = firmware believes tips are on the 96-head.
    """
    resp = await self.driver.send_command(module="C0", command="QH", fmt="qh#")
    if resp is None:
      return 0
    return int(resp["qh"])

  async def request_tadm_status(self) -> int:
    """Request CoRe 96 Head channel TADM status (C0:VC).

    Returns:
      0 = off, 1 = on.
    """
    resp = await self.driver.send_command(module="C0", command="VC", fmt="qx#")
    if resp is None:
      return 0
    return int(resp["qx"])

  async def request_tadm_error_status(self) -> dict:
    """Request CoRe 96 Head channel TADM error status (C0:VB).

    Returns:
      Dictionary with error pattern (0 = no error).
    """
    resp = await self.driver.send_command(module="C0", command="VB", fmt="vb" + "&" * 24)
    if resp is None:
      return {}
    return dict(resp)

  async def dispensing_drive_request_position_mm(self) -> float:
    """Request 96-head dispensing drive position in mm (H0:RD)."""
    resp = await self.driver.send_command(module="H0", command="RD", fmt="rd######")
    if resp is None:
      return 0.0
    return float(round(resp["rd"] * _DISPENSING_DRIVE_MM_PER_INCREMENT, 2))

  async def dispensing_drive_request_position_uL(self) -> float:
    """Request 96-head dispensing drive position in uL."""
    position_mm = await self.dispensing_drive_request_position_mm()
    increment = round(position_mm / _DISPENSING_DRIVE_MM_PER_INCREMENT)
    return round(increment * _DISPENSING_DRIVE_UL_PER_INCREMENT, 2)

  # ---------------------------------------------------------------------------
  # Pick up tips
  # ---------------------------------------------------------------------------

  @dataclass
  class PickUpTips96Params(BackendParams):
    """STAR-specific parameters for 96-head tip pickup.

    Args:
      tip_pickup_method: Tip pickup strategy.
        - ``"from_rack"``: standard pickup from a tip rack; moves the plunger down
          before mounting tips.
        - ``"from_waste"``: moves plunger up, mounts tips, retracts ~10 mm, moves
          plunger down, then moves to traversal height.
        - ``"full_blowout"``: moves plunger up, mounts tips, then moves to traversal
          height.
      minimum_height_command_end: Minimal Z height in mm at command end. If None, uses
        the backend's ``traversal_height``. Must be between 0 and 342.5.
      minimum_traverse_height_at_beginning_of_a_command: Minimum Z clearance in mm
        before lateral movement begins. If None, uses the backend's
        ``traversal_height``. Must be between 0 and 342.5.
      alignment_tipspot_identifier: The tip spot identifier (e.g. ``"A1"``) used to
        align the 96-head's A1 channel. Allowed range is ``"A1"`` to ``"H12"``.
    """

    tip_pickup_method: Literal["from_rack", "from_waste", "full_blowout"] = "from_rack"
    minimum_height_command_end: Optional[float] = None
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    alignment_tipspot_identifier: str = "A1"

  async def pick_up_tips96(
    self, pickup: PickupTipRack, backend_params: Optional[BackendParams] = None
  ):
    """Pick up tips using the 96 head.

    Firmware command: C0 EP
    """
    await self.driver.ensure_iswap_parked()
    logger.info("[STAR 96] pick_up_tips: resource=%s", pickup.resource.name)
    if not isinstance(backend_params, STARHead96Backend.PickUpTips96Params):
      backend_params = STARHead96Backend.PickUpTips96Params()

    tip_pickup_method = backend_params.tip_pickup_method
    if tip_pickup_method not in {"from_rack", "from_waste", "full_blowout"}:
      raise ValueError(f"Invalid tip_pickup_method: '{tip_pickup_method}'.")

    prototypical_tip = next((tip for tip in pickup.tips if tip is not None), None)
    if prototypical_tip is None:
      raise ValueError("No tips found in the tip rack.")
    if not isinstance(prototypical_tip, HamiltonTip):
      raise TypeError("Tip type must be HamiltonTip.")

    ttti = await self.driver.request_or_assign_tip_type_index(prototypical_tip)

    tip_length = prototypical_tip.total_tip_length
    fitting_depth = prototypical_tip.fitting_depth
    tip_engage_height_from_tipspot = tip_length - fitting_depth

    # Adjust tip engage height based on tip size
    if prototypical_tip.tip_size == TipSize.LOW_VOLUME:
      tip_engage_height_from_tipspot += 2
    elif prototypical_tip.tip_size != TipSize.STANDARD_VOLUME:
      tip_engage_height_from_tipspot -= 2

    # Compute pickup position using absolute coordinates (deck is at origin)
    alignment_tipspot = pickup.resource.get_item(backend_params.alignment_tipspot_identifier)
    tip_spot_z = alignment_tipspot.get_absolute_location().z + pickup.offset.z
    z_pickup_position = tip_spot_z + tip_engage_height_from_tipspot

    pickup_position = alignment_tipspot.get_absolute_location(x="c", y="c") + pickup.offset
    pickup_position.z = round(z_pickup_position, 2)

    traversal = self.traversal_height

    if tip_pickup_method == "from_rack":
      # Move the dispensing drive down before pickup.
      # The STAR will not automatically move the dispensing drive down if it is still up.
      # See https://github.com/PyLabRobot/pylabrobot/pull/835
      #
      # Pre-computed increment values (uL / 0.019340933):
      #   position=218.19uL -> 11281, speed=261.1uL/s -> 13500,
      #   stop_speed=0 -> 0, acceleration=17406.84uL/s^2 -> 900000
      await self.driver.send_command(
        module="H0",
        command="DQ",
        dq="11281",
        dv="13500",
        du="00000",
        dr="900000",
        dw="15",
      )

    await self.driver.send_command(
      module="C0",
      command="EP",
      xs=f"{abs(round(pickup_position.x * 10)):05}",
      xd=0 if pickup_position.x >= 0 else 1,
      yh=f"{round(pickup_position.y * 10):04}",
      tt=f"{ttti:02}",
      wu={"from_rack": 0, "from_waste": 1, "full_blowout": 2}[tip_pickup_method],
      za=f"{round(pickup_position.z * 10):04}",
      zh=f"{round((backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10):04}",
      ze=f"{round((backend_params.minimum_height_command_end or traversal) * 10):04}",
    )

  # ---------------------------------------------------------------------------
  # Drop tips
  # ---------------------------------------------------------------------------

  @dataclass
  class DropTips96Params(BackendParams):
    """STAR-specific parameters for 96-head tip drop.

    Args:
      minimum_height_command_end: Minimal Z height in mm at command end. If None, uses
        the backend's ``traversal_height``. Must be between 0 and 342.5.
      minimum_traverse_height_at_beginning_of_a_command: Minimum Z clearance in mm
        before lateral movement begins. If None, uses the backend's
        ``traversal_height``. Must be between 0 and 342.5.
      alignment_tipspot_identifier: The tip spot identifier (e.g. ``"A1"``) used to
        align the 96-head's A1 channel. Allowed range is ``"A1"`` to ``"H12"``.
    """

    minimum_height_command_end: Optional[float] = None
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    alignment_tipspot_identifier: str = "A1"

  async def drop_tips96(self, drop: DropTipRack, backend_params: Optional[BackendParams] = None):
    """Drop tips from the 96 head.

    Firmware command: C0 ER
    """
    await self.driver.ensure_iswap_parked()
    logger.info("[STAR 96] drop_tips: resource=%s", drop.resource.name)
    if not isinstance(backend_params, STARHead96Backend.DropTips96Params):
      backend_params = STARHead96Backend.DropTips96Params()

    from pylabrobot.resources import TipRack

    if isinstance(drop.resource, TipRack):
      tip_spot_a1 = drop.resource.get_item(backend_params.alignment_tipspot_identifier)
      position = tip_spot_a1.get_absolute_location(x="c", y="c") + drop.offset
      tip_rack = tip_spot_a1.parent
      if tip_rack is None:
        raise ValueError("Tip spot parent (tip rack) must not be None")
      position.z = tip_rack.get_absolute_location().z + 1.45
    else:
      # Drop into trash or other resource: center the head in the resource.
      position = self._position_96_head_in_resource(drop.resource) + drop.offset

    traversal = self.traversal_height

    await self.driver.send_command(
      module="C0",
      command="ER",
      xs=f"{abs(round(position.x * 10)):05}",
      xd=0 if position.x >= 0 else 1,
      yh=f"{round(position.y * 10):04}",
      za=f"{round(position.z * 10):04}",
      zh=f"{round((backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10):04}",
      ze=f"{round((backend_params.minimum_height_command_end or traversal) * 10):04}",
    )

  # ---------------------------------------------------------------------------
  # Aspirate
  # ---------------------------------------------------------------------------

  @dataclass
  class Aspirate96Params(BackendParams):
    """STAR-specific parameters for 96-head aspiration.

    Args:
      use_lld: If True, use gamma liquid level detection. If False, use the
        liquid height from the aspiration operation.
      aspiration_type: Type of aspiration (0 = simple, 1 = sequence, 2 = cup emptied).
      minimum_traverse_height_at_beginning_of_a_command: Minimum Z clearance in mm
        before lateral movement. If None, uses the backend's ``traversal_height``.
      min_z_endpos: Minimum Z position in mm at end of command. If None, uses the
        backend's ``traversal_height``.
      lld_search_height: LLD search height in mm. Default 199.9.
      minimum_height: Minimum height (maximum immersion depth) in mm. If None, uses
        the container bottom Z.
      second_section_height: Tube 2nd section height measured from minimum_height in mm.
        Default 3.2.
      second_section_ratio: Tube 2nd section ratio: (bottom diameter * 10000) / top
        diameter. Default 618.0.
      immersion_depth: Immersion depth in mm. Positive = go deeper into liquid,
        negative = go up out of liquid. Default 0.
      surface_following_distance: Surface following distance during aspiration in mm.
        Default 0.
      transport_air_volume: Transport air volume in uL. Default 5.0.
      pre_wetting_volume: Pre-wetting volume in uL. Default 5.0.
      gamma_lld_sensitivity: Gamma LLD sensitivity (1 = high, 4 = low). Default 1.
      swap_speed: Swap speed (on leaving liquid) in mm/s. Must be between 0.3 and
        160.0. Default 2.0.
      settling_time: Settling time in seconds. Default 1.0.
      mix_position_from_liquid_surface: Mix position in Z direction from liquid surface
        in mm. Default 0.
      mix_surface_following_distance: Surface following distance during mix in mm.
        Default 0.
      limit_curve_index: Limit curve index for TADM. Must be between 0 and 999.
        Default 0.
      pull_out_distance_transport_air: Distance in mm to pull out for transport air.
        Default 10.
      tadm_algorithm: Whether to use the TADM algorithm. Default False.
      recording_mode: Recording mode (0 = no recording, 1 = TADM errors only,
        2 = all TADM measurements). Default 0.
    """

    use_lld: bool = False
    aspiration_type: int = 0
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    min_z_endpos: Optional[float] = None
    lld_search_height: float = 199.9
    minimum_height: Optional[float] = None
    second_section_height: float = 3.2
    second_section_ratio: float = 618.0
    immersion_depth: float = 0
    surface_following_distance: float = 0
    transport_air_volume: float = 5.0
    pre_wetting_volume: float = 5.0
    gamma_lld_sensitivity: int = 1
    swap_speed: float = 2.0
    settling_time: float = 1.0
    mix_position_from_liquid_surface: float = 0
    mix_surface_following_distance: float = 0
    limit_curve_index: int = 0
    pull_out_distance_transport_air: float = 10
    tadm_algorithm: bool = False
    recording_mode: int = 0

  async def aspirate96(
    self,
    aspiration: Union[MultiHeadAspirationPlate, MultiHeadAspirationContainer],
    backend_params: Optional[BackendParams] = None,
  ):
    """Aspirate using the Core96 head.

    Firmware command: C0 EA
    """
    await self.driver.ensure_iswap_parked()
    if not isinstance(backend_params, STARHead96Backend.Aspirate96Params):
      backend_params = STARHead96Backend.Aspirate96Params()

    # Compute position
    if isinstance(aspiration, MultiHeadAspirationPlate):
      plate = aspiration.wells[0].parent
      if plate is None:
        raise ValueError("MultiHeadAspirationPlate well parent must not be None")
      rot = plate.get_absolute_rotation()
      if rot.x % 360 != 0 or rot.y % 360 != 0:
        raise ValueError("Plate rotation around x or y is not supported for 96 head operations")
      if rot.z % 360 == 180:
        ref_well = aspiration.wells[-1]
      elif rot.z % 360 == 0:
        ref_well = aspiration.wells[0]
      else:
        raise ValueError("96 head only supports plate rotations of 0 or 180 degrees around z")

      position = (
        ref_well.get_absolute_location(x="c", y="c")
        + Coordinate(z=ref_well.material_z_thickness)
        + aspiration.offset
      )
    else:
      # Container (trough): center the head
      x_width = (12 - 1) * 9  # 12 tips in a row, 9 mm between them
      y_width = (8 - 1) * 9  # 8 tips in a column, 9 mm between them
      x_position = (aspiration.container.get_absolute_size_x() - x_width) / 2
      y_position = (aspiration.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        aspiration.container.get_absolute_location(z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + aspiration.offset
      )

    liquid_height = position.z + (aspiration.liquid_height or 0)

    volume = aspiration.volume
    flow_rate = aspiration.flow_rate or 250
    blow_out_air_volume = aspiration.blow_out_air_volume or 0

    if isinstance(aspiration, MultiHeadAspirationPlate):
      if aspiration.wells[0].parent is None:
        raise ValueError("Well has no parent resource")
      resource_name = aspiration.wells[0].parent.name
    else:
      resource_name = aspiration.container.name
    logger.info(
      "[STAR 96] aspirate: resource=%s volume=%.2f flow_rate=%.2f", resource_name, volume, flow_rate
    )

    traversal = self.traversal_height

    immersion_depth = backend_params.immersion_depth
    immersion_depth_direction = 0 if immersion_depth >= 0 else 1

    await self.driver.send_command(
      module="C0",
      command="EA",
      aa=backend_params.aspiration_type,
      xs=f"{abs(round(position.x * 10)):05}",
      xd=0 if position.x >= 0 else 1,
      yh=f"{round(position.y * 10):04}",
      zh=f"{round((backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10):04}",
      ze=f"{round((backend_params.min_z_endpos or traversal) * 10):04}",
      lz=f"{round(backend_params.lld_search_height * 10):04}",
      zt=f"{round(liquid_height * 10):04}",
      pp=f"{round(backend_params.pull_out_distance_transport_air * 10):04}",
      zm=f"{round((backend_params.minimum_height or position.z) * 10):04}",
      zv=f"{round(backend_params.second_section_height * 10):04}",
      zq=f"{round(backend_params.second_section_ratio * 10):05}",
      iw=f"{round(abs(immersion_depth) * 10):03}",
      ix=immersion_depth_direction,
      fh=f"{round(backend_params.surface_following_distance * 10):03}",
      af=f"{round(volume * 10):05}",
      ag=f"{round(flow_rate * 10):04}",
      vt=f"{round(backend_params.transport_air_volume * 10):03}",
      bv=f"{round(blow_out_air_volume * 10):05}",
      wv=f"{round(backend_params.pre_wetting_volume * 10):05}",
      cm=int(backend_params.use_lld),
      cs=backend_params.gamma_lld_sensitivity,
      bs=f"{round(backend_params.swap_speed * 10):04}",
      wh=f"{round(backend_params.settling_time * 10):02}",
      hv=f"{round(aspiration.mix.volume * 10):05}" if aspiration.mix is not None else "00000",
      hc=f"{aspiration.mix.repetitions:02}" if aspiration.mix is not None else "00",
      hp=f"{round(backend_params.mix_position_from_liquid_surface * 10):03}",
      mj=f"{round(backend_params.mix_surface_following_distance * 10):03}",
      hs=f"{round(aspiration.mix.flow_rate * 10):04}" if aspiration.mix is not None else "1200",
      cw=_channel_pattern_to_hex([True] * 96),
      cr=f"{backend_params.limit_curve_index:03}",
      cj=backend_params.tadm_algorithm,
      cx=backend_params.recording_mode,
    )

  # ---------------------------------------------------------------------------
  # Dispense
  # ---------------------------------------------------------------------------

  @dataclass
  class Dispense96Params(BackendParams):
    """STAR-specific parameters for 96-head dispense.

    Args:
      jet: Whether to use jet dispensing mode.
      empty: Whether to use empty tip mode.
      blow_out: Whether to blow out after dispensing.
      use_lld: If True, use gamma liquid level detection. If False, use the
        liquid height from the dispense operation.
      minimum_traverse_height_at_beginning_of_a_command: Minimum Z clearance in mm
        before lateral movement. If None, uses the backend's ``traversal_height``.
      min_z_endpos: Minimum Z position in mm at end of command. If None, uses the
        backend's ``traversal_height``.
      lld_search_height: LLD search height in mm. Default 199.9.
      minimum_height: Minimum height (maximum immersion depth) in mm. If None, uses
        the container bottom Z.
      second_section_height: Tube 2nd section height measured from minimum_height in mm.
        Default 3.2.
      second_section_ratio: Tube 2nd section ratio: (bottom diameter * 10000) / top
        diameter. Default 618.0.
      immersion_depth: Immersion depth in mm. Positive = go deeper into liquid,
        negative = go up out of liquid. Default 0.
      surface_following_distance: Surface following distance during dispensing in mm.
        Default 0.
      transport_air_volume: Transport air volume in uL. Default 5.0.
      gamma_lld_sensitivity: Gamma LLD sensitivity (1 = high, 4 = low). Default 1.
      swap_speed: Swap speed (on leaving liquid) in mm/s. Must be between 0.3 and
        160.0. Default 2.0.
      settling_time: Settling time in seconds. Default 5.0.
      mix_position_from_liquid_surface: Mix position in Z direction from liquid surface
        in mm. Default 0.
      mix_surface_following_distance: Surface following distance during mix in mm.
        Default 0.
      limit_curve_index: Limit curve index for TADM. Must be between 0 and 999.
        Default 0.
      cut_off_speed: Cut-off speed in uL/s. Default 5.0.
      stop_back_volume: Stop back volume in uL. Default 0.
      pull_out_distance_transport_air: Distance in mm to pull out for transport air.
        Default 10.
      side_touch_off_distance: Side touch off distance in 0.1 mm units (0 = OFF).
        Default 0.
      tadm_algorithm: Whether to use the TADM algorithm. Default False.
      recording_mode: Recording mode (0 = no recording, 1 = TADM errors only,
        2 = all TADM measurements). Default 0.
    """

    jet: bool = False
    empty: bool = False
    blow_out: bool = False
    use_lld: bool = False
    minimum_traverse_height_at_beginning_of_a_command: Optional[float] = None
    min_z_endpos: Optional[float] = None
    lld_search_height: float = 199.9
    minimum_height: Optional[float] = None
    second_section_height: float = 3.2
    second_section_ratio: float = 618.0
    immersion_depth: float = 0
    surface_following_distance: float = 0
    transport_air_volume: float = 5.0
    gamma_lld_sensitivity: int = 1
    swap_speed: float = 2.0
    settling_time: float = 5.0
    mix_position_from_liquid_surface: float = 0
    mix_surface_following_distance: float = 0
    limit_curve_index: int = 0
    cut_off_speed: float = 5.0
    stop_back_volume: float = 0
    pull_out_distance_transport_air: float = 10
    side_touch_off_distance: int = 0
    tadm_algorithm: bool = False
    recording_mode: int = 0

  async def dispense96(
    self,
    dispense: Union[MultiHeadDispensePlate, MultiHeadDispenseContainer],
    backend_params: Optional[BackendParams] = None,
  ):
    """Dispense using the Core96 head.

    Firmware command: C0 ED
    """
    await self.driver.ensure_iswap_parked()
    if not isinstance(backend_params, STARHead96Backend.Dispense96Params):
      backend_params = STARHead96Backend.Dispense96Params()

    # Compute position
    if isinstance(dispense, MultiHeadDispensePlate):
      plate = dispense.wells[0].parent
      if plate is None:
        raise ValueError("MultiHeadDispensePlate well parent must not be None")
      rot = plate.get_absolute_rotation()
      if rot.x % 360 != 0 or rot.y % 360 != 0:
        raise ValueError("Plate rotation around x or y is not supported for 96 head operations")
      if rot.z % 360 == 180:
        ref_well = dispense.wells[-1]
      elif rot.z % 360 == 0:
        ref_well = dispense.wells[0]
      else:
        raise ValueError("96 head only supports plate rotations of 0 or 180 degrees around z")

      position = (
        ref_well.get_absolute_location(x="c", y="c")
        + Coordinate(z=ref_well.material_z_thickness)
        + dispense.offset
      )
    else:
      # Container (trough): center the head
      x_width = (12 - 1) * 9
      y_width = (8 - 1) * 9
      x_position = (dispense.container.get_absolute_size_x() - x_width) / 2
      y_position = (dispense.container.get_absolute_size_y() - y_width) / 2 + y_width
      position = (
        dispense.container.get_absolute_location(z="cavity_bottom")
        + Coordinate(x=x_position, y=y_position)
        + dispense.offset
      )

    liquid_height = position.z + (dispense.liquid_height or 0)

    volume = dispense.volume
    flow_rate = dispense.flow_rate or 120
    blow_out_air_volume = dispense.blow_out_air_volume or 0

    if isinstance(dispense, MultiHeadDispensePlate):
      if dispense.wells[0].parent is None:
        raise ValueError("Well has no parent resource")
      resource_name = dispense.wells[0].parent.name
    else:
      resource_name = dispense.container.name
    logger.info(
      "[STAR 96] dispense: resource=%s volume=%.2f flow_rate=%.2f", resource_name, volume, flow_rate
    )

    dispense_mode = _dispensing_mode_for_op(
      empty=backend_params.empty,
      jet=backend_params.jet,
      blow_out=backend_params.blow_out,
    )

    traversal = self.traversal_height

    immersion_depth = backend_params.immersion_depth
    immersion_depth_direction = 0 if immersion_depth >= 0 else 1

    await self.driver.send_command(
      module="C0",
      command="ED",
      da=dispense_mode,
      xs=f"{abs(round(position.x * 10)):05}",
      xd=0 if position.x >= 0 else 1,
      yh=f"{round(position.y * 10):04}",
      zm=f"{round((backend_params.minimum_height or position.z) * 10):04}",
      zv=f"{round(backend_params.second_section_height * 10):04}",
      zq=f"{round(backend_params.second_section_ratio * 10):05}",
      lz=f"{round(backend_params.lld_search_height * 10):04}",
      zt=f"{round(liquid_height * 10):04}",
      pp=f"{round(backend_params.pull_out_distance_transport_air * 10):04}",
      iw=f"{round(abs(immersion_depth) * 10):03}",
      ix=immersion_depth_direction,
      fh=f"{round(backend_params.surface_following_distance * 10):03}",
      zh=f"{round((backend_params.minimum_traverse_height_at_beginning_of_a_command or traversal) * 10):04}",
      ze=f"{round((backend_params.min_z_endpos or traversal) * 10):04}",
      df=f"{round(volume * 10):05}",
      dg=f"{round(flow_rate * 10):04}",
      es=f"{round(backend_params.cut_off_speed * 10):04}",
      ev=f"{round(backend_params.stop_back_volume * 10):03}",
      vt=f"{round(backend_params.transport_air_volume * 10):03}",
      bv=f"{round(blow_out_air_volume * 10):05}",
      cm=int(backend_params.use_lld),
      cs=backend_params.gamma_lld_sensitivity,
      ej=f"{backend_params.side_touch_off_distance:02}",
      bs=f"{round(backend_params.swap_speed * 10):04}",
      wh=f"{round(backend_params.settling_time * 10):02}",
      hv=f"{round(dispense.mix.volume * 10):05}" if dispense.mix is not None else "00000",
      hc=f"{dispense.mix.repetitions:02}" if dispense.mix is not None else "00",
      hp=f"{round(backend_params.mix_position_from_liquid_surface * 10):03}",
      mj=f"{round(backend_params.mix_surface_following_distance * 10):03}",
      hs=f"{round(dispense.mix.flow_rate * 10):04}" if dispense.mix is not None else "1200",
      cw=_channel_pattern_to_hex([True] * 96),
      cr=f"{backend_params.limit_curve_index:03}",
      cj=backend_params.tadm_algorithm,
      cx=backend_params.recording_mode,
    )

  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  @staticmethod
  def _position_96_head_in_resource(resource: Resource) -> Coordinate:
    """Compute the A1 position for centering the 96-head in a resource."""
    head_size_x = 9 * 11  # 12 channels, 9mm spacing
    head_size_y = 9 * 7  # 8 channels, 9mm spacing
    channel_size = 9
    loc = resource.get_absolute_location()
    loc.x += (resource.get_size_x() - head_size_x) / 2 + channel_size / 2
    loc.y += (resource.get_size_y() - head_size_y) / 2 + channel_size / 2
    return loc
