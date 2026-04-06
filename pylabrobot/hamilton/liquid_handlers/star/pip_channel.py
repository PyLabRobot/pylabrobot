"""PIPChannel: represents a single PIP channel on the STAR."""

from __future__ import annotations

import enum
import re
from typing import TYPE_CHECKING, Dict, Literal, Optional, Tuple

from .errors import STARFirmwareError

if TYPE_CHECKING:
  from .driver import STARDriver
  from .pip_backend import STARPIPBackend


# ---------------------------------------------------------------------------
# Drive-unit conversion helpers (mirrored from legacy STARBackend)
# ---------------------------------------------------------------------------

_Z_DRIVE_MM_PER_INCREMENT = 0.01072765
_DISPENSING_DRIVE_VOL_PER_INCREMENT = 0.046876  # uL / increment
_DISPENSING_DRIVE_MM_PER_INCREMENT = 0.002734375  # mm / increment
_Y_DRIVE_MM_PER_INCREMENT = 0.046302082

MAXIMUM_CHANNEL_Z_POSITION = 334.7  # mm (= z-drive increment 31_200)
MINIMUM_CHANNEL_Z_POSITION = 99.98  # mm (= z-drive increment 9_320)
DEFAULT_TIP_FITTING_DEPTH = 8  # mm, for 10, 50, 300, 1000 uL Hamilton tips


def _mm_to_z_inc(mm: float) -> int:
  return round(mm / _Z_DRIVE_MM_PER_INCREMENT)


def _z_inc_to_mm(inc: int) -> float:
  return round(inc * _Z_DRIVE_MM_PER_INCREMENT, 2)


def _vol_to_disp_inc(vol: float) -> int:
  return round(vol / _DISPENSING_DRIVE_VOL_PER_INCREMENT)


def _disp_inc_to_vol(inc: int) -> float:
  return round(inc * _DISPENSING_DRIVE_VOL_PER_INCREMENT, 1)


def _mm_to_disp_inc(mm: float) -> int:
  return round(mm / _DISPENSING_DRIVE_MM_PER_INCREMENT)


def _disp_inc_to_mm(inc: int) -> float:
  return round(inc * _DISPENSING_DRIVE_MM_PER_INCREMENT, 3)


def _mm_to_y_inc(mm: float) -> int:
  return round(mm / _Y_DRIVE_MM_PER_INCREMENT)


def _y_inc_to_mm(inc: int) -> float:
  return round(inc * _Y_DRIVE_MM_PER_INCREMENT, 2)


# ---------------------------------------------------------------------------
# PressureLLDMode (was nested in legacy STARBackend)
# ---------------------------------------------------------------------------


class PressureLLDMode(enum.Enum):
  """Pressure liquid level detection mode."""

  LIQUID = 0
  FOAM = 1


# ---------------------------------------------------------------------------
# PIPChannel
# ---------------------------------------------------------------------------

DISPENSING_DRIVE_VOL_LIMIT_BOTTOM = -45  # uL  # TODO: confirm with others
DISPENSING_DRIVE_VOL_LIMIT_TOP = 1_250  # uL


class PIPChannel:
  """Represents a single PIP channel on the STAR.

  Instances are created by :class:`STARPIPBackend` — one per physical channel.
  """

  def __init__(self, driver: STARDriver, index: int, backend: STARPIPBackend):
    self.driver = driver
    self.index = index
    self.backend = backend

  async def send_command(self, *args, **kwargs):
    """Send a firmware command. C0 commands are serialized; Px commands go direct."""
    return await self.backend.send_command(*args, **kwargs)

  @property
  def module_id(self) -> str:
    """Firmware module identifier for this channel (e.g. ``"P1"``)."""
    return "P" + "123456789ABCDEFG"[self.index]

  # -- Px:RF  firmware version ------------------------------------------------

  async def request_firmware_version(self) -> str:
    """Query the firmware version of this channel (Px:RF)."""
    resp = await self.send_command(
      module=self.module_id,
      command="RF",
      fmt="rf" + "&" * 17,
    )
    return str(resp["rf"])

  # -- Px:RV  cycle counts ----------------------------------------------------

  async def request_cycle_counts(self) -> Dict[str, int]:
    """Request cycle counters for a single channel.

    Returns the number of tip pick-up, tip discard, aspiration, and dispensing cycles
    performed by the channel.

    Returns:
      A dict with keys ``tip_pick_up_cycles``, ``tip_discard_cycles``,
      ``aspiration_cycles``, and ``dispensing_cycles``.
    """

    resp = await self.send_command(
      module=self.module_id,
      command="RV",
      fmt="na##########nb##########nc##########nd##########",
    )
    return {
      "tip_pick_up_cycles": resp["na"],
      "tip_discard_cycles": resp["nb"],
      "aspiration_cycles": resp["nc"],
      "dispensing_cycles": resp["nd"],
    }

  # -- Px:RD  dispensing drive position ---------------------------------------

  async def request_dispensing_drive_position(self) -> float:
    """Request the current position of the channel's dispensing drive"""

    resp = await self.send_command(
      module=self.module_id,
      command="RD",
      fmt="rd##### #####",
    )
    return _disp_inc_to_vol(resp["rd"])

  # -- Px:DS  move dispensing drive -------------------------------------------

  async def move_dispensing_drive_to_position(
    self,
    vol: float,
    flow_rate: float = 200.0,  # uL/sec
    acceleration: float = 3000.0,  # uL/sec**2
    current_limit: int = 5,
  ):
    """Move channel's dispensing drive to specified volume position

    Args:
      vol: Target volume position to move the dispensing drive piston to (uL).
      flow_rate: Speed of the movement (uL/sec). Default is 200.0 uL/sec.
      acceleration: Acceleration of the movement (uL/sec**2). Default is 3000.0 uL/sec**2.
      current_limit: Current limit for the drive (1-7). Default is 5.
    """

    if not (DISPENSING_DRIVE_VOL_LIMIT_BOTTOM <= vol <= DISPENSING_DRIVE_VOL_LIMIT_TOP):
      raise ValueError(
        f"Target dispensing Drive vol must be between {DISPENSING_DRIVE_VOL_LIMIT_BOTTOM}"
        f" and {DISPENSING_DRIVE_VOL_LIMIT_TOP}, is {vol}"
      )
    if not (0.9 <= flow_rate <= 632.8):
      raise ValueError(
        f"Dispensing drive speed must be between 0.9 and 632.8 uL/sec, is {flow_rate}"
      )
    if not (234.4 <= acceleration <= 28125.6):
      raise ValueError(
        f"Dispensing drive acceleration must be between 234.4 and 28125.6 uL/sec**2, is {acceleration}"
      )
    if not (1 <= current_limit <= 7):
      raise ValueError(
        f"Dispensing drive current limit must be between 1 and 7, is {current_limit}"
      )

    current_position = await self.request_dispensing_drive_position()
    relative_vol_movement = round(vol - current_position, 1)
    relative_vol_movement_increment = _vol_to_disp_inc(abs(relative_vol_movement))
    speed_increment = _vol_to_disp_inc(flow_rate)
    acceleration_increment = _vol_to_disp_inc(acceleration)
    acceleration_increment_thousands = round(acceleration_increment * 0.001)

    await self.send_command(
      module=self.module_id,
      command="DS",
      ds=f"{relative_vol_movement_increment:05}",
      dt="0" if relative_vol_movement >= 0 else "1",
      dv=f"{speed_increment:05}",
      dr=f"{acceleration_increment_thousands:03}",
      dw=f"{current_limit}",
    )

  # -- empty_tip (convenience over Px:DS) ------------------------------------

  async def empty_tip(
    self,
    vol: Optional[float] = None,
    flow_rate: float = 200.0,  # vol/sec
    acceleration: float = 3000.0,  # vol/sec**2
    current_limit: int = 5,
    reset_dispensing_drive_after: bool = True,
  ):
    """Empty tip by moving to `vol` (default bottom limit), optionally returning plunger position to 0.

    Args:
      vol: Target volume position to move the dispensing drive piston to (uL). If None, defaults to bottom limit.
      flow_rate: Speed of the movement (uL/sec). Default is 200.0 uL/sec.
      acceleration: Acceleration of the movement (uL/sec**2). Default is 3000.0 uL/sec**2.
      current_limit: Current limit for the drive (1-7). Default is 5.
      reset_dispensing_drive_after: Whether to return the dispensing drive to 0 after emptying. Default is True
    """

    if vol is None:
      vol = DISPENSING_DRIVE_VOL_LIMIT_BOTTOM

    # Empty tip
    await self.move_dispensing_drive_to_position(
      vol=vol,
      flow_rate=flow_rate,
      acceleration=acceleration,
      current_limit=current_limit,
    )

    if reset_dispensing_drive_after:
      # Reset only channel used back to vol=0.0 position
      await self.move_dispensing_drive_to_position(
        vol=0,
        flow_rate=flow_rate,
        acceleration=acceleration,
        current_limit=current_limit,
      )

  # -- Px:ZA  move Z-drive (stop disk reference) ------------------------------

  async def move_stop_disk_z(
    self,
    z: float,
    speed: float = 125.0,
    acceleration: float = 800.0,
    current_limit: int = 3,
  ):
    """Move this channel's Z-drive to an absolute stop disk position.

    Communicates directly with the channel (Px:ZA) rather than through the
    master module (C0:KZ). This bypasses the firmware's tip-picked-up flag,
    enabling Z moves with configurable speed/acceleration.

    Args:
      z: Target Z position in mm (stop disk).
      speed: Max Z-drive speed in mm/sec. Default 125.0 mm/s.
      acceleration: Acceleration in mm/sec². Default 800.0. Valid range: ~53.6 to 1609.
      current_limit: Current limit (0-7). Default 3.
    """

    z_inc = _mm_to_z_inc(z)
    speed_inc = _mm_to_z_inc(speed)
    accel_inc = _mm_to_z_inc(acceleration / 1000)

    if not (9320 <= z_inc <= 31200):
      raise ValueError(
        f"z must be between {_z_inc_to_mm(9320)} and {_z_inc_to_mm(31200)} mm, got {z} mm"
      )
    if not (20 <= speed_inc <= 15000):
      raise ValueError(
        f"speed must be between {_z_inc_to_mm(20)} and {_z_inc_to_mm(15000)} mm/s, got {speed} mm/s"
      )
    if not (5 <= accel_inc <= 150):
      raise ValueError(
        f"acceleration must be between ~53.6 and ~1609 mm/s², got {acceleration} mm/s²"
      )
    if not (0 <= current_limit <= 7):
      raise ValueError(f"current_limit must be between 0 and 7, got {current_limit}")

    return await self.send_command(
      module=self.module_id,
      command="ZA",
      za=f"{z_inc:05}",
      zv=f"{speed_inc:05}",
      zr=f"{accel_inc:03}",
      zw=f"{current_limit:01}",
    )

  async def move_to_z_safety(self):
    """Move this channel to the maximum Z position (safe height) using Px:ZA."""
    await self.move_stop_disk_z(self.MAXIMUM_CHANNEL_Z_POSITION)

  # -- move tool Z (tip/tool end reference) ------------------------------------

  MAXIMUM_CHANNEL_Z_POSITION = 334.7  # mm
  MINIMUM_CHANNEL_Z_POSITION = 99.98  # mm
  DEFAULT_TIP_FITTING_DEPTH = 8  # mm

  async def move_tool_z(self, z: float):
    """Move this channel in the Z direction, referenced to the tip/tool end (mm).

    Requires a tip or tool to be attached. Use :meth:`move_stop_disk_z` for moves
    without a tip.

    Args:
      z: Target Z position in mm (tip/tool end).
    """
    tip_presence = await self.backend.request_tip_presence()
    if not tip_presence[self.index]:
      raise ValueError(
        f"Channel {self.index} does not have a tip or tool attached. "
        "Use move_stop_disk_z() for Z moves without a tip attached."
      )

    tip_len = await self.request_tip_length()

    max_tip_z = self.MAXIMUM_CHANNEL_Z_POSITION - tip_len + self.DEFAULT_TIP_FITTING_DEPTH
    min_tip_z = self.MINIMUM_CHANNEL_Z_POSITION - tip_len + self.DEFAULT_TIP_FITTING_DEPTH

    if not (min_tip_z <= z <= max_tip_z):
      raise ValueError(
        f"z={z} mm out of safe range [{min_tip_z}, {max_tip_z}] mm "
        f"for tip length {tip_len} mm on channel {self.index}"
      )

    await self.send_command(
      module="C0",
      command="KZ",
      pn=f"{self.index + 1:02}",
      zj=f"{round(z * 10):04}",
    )

  # -- C0:RA  request X position ------------------------------------------------

  async def request_x_pos(self) -> float:
    """Request current X-position of this channel (mm).

    All PIP channels share the same X arm, so this returns the arm position.
    """
    resp = await self.driver.send_command(
      module="C0",
      command="RA",
      fmt="ra#####",
      pn=f"{self.index + 1:02}",
    )
    return float(resp["ra"] / 10)

  # -- C0:RB  request Y position ------------------------------------------------

  async def request_y_pos(self) -> float:
    """Request current Y-position of this channel (mm)."""
    resp = await self.driver.send_command(
      module="C0",
      command="RB",
      fmt="rb####",
      pn=f"{self.index + 1:02}",
    )
    return float(resp["rb"] / 10)

  # -- C0:KY  move channel Y ---------------------------------------------------

  async def move_y(self, y: float):
    """Move this channel safely in the Y direction (mm), with anti-crash checks.

    Enforces minimum spacing between neighboring channels (queried from firmware
    during setup) so that two channels can never be commanded closer than the
    hardware allows.

    Args:
      y: Target Y position in mm.
    """
    assert self.driver.extended_conf is not None
    if self.index > 0:
      adj_y = await self.backend.channels[self.index - 1].request_y_pos()
      spacing = self.driver._min_spacing_between(self.index, self.index - 1)
      max_y_pos = adj_y - spacing
      if y > max_y_pos:
        raise ValueError(
          f"channel {self.index} y-target {y} mm too close to channel {self.index - 1} "
          f"at {round(adj_y, 2)} mm (minimum spacing {spacing} mm, "
          f"max allowed {round(max_y_pos, 2)} mm)"
        )
    else:
      max_y_pos = self.driver.extended_conf.pip_maximal_y_position
      if y > max_y_pos:
        raise ValueError(f"channel {self.index} y-target must be <= {max_y_pos} mm")

    if self.index < (self.backend.num_channels - 1):
      adj_y = await self.backend.channels[self.index + 1].request_y_pos()
      spacing = self.driver._min_spacing_between(self.index, self.index + 1)
      min_y_pos = adj_y + spacing
      if y < min_y_pos:
        raise ValueError(
          f"channel {self.index} y-target {y} mm too close to channel {self.index + 1} "
          f"at {round(adj_y, 2)} mm (minimum spacing {spacing} mm, "
          f"min allowed {round(min_y_pos, 2)} mm)"
        )
    else:
      if y < self.driver.extended_conf.left_arm_min_y_position:
        raise ValueError(
          f"channel {self.index} y-target must be >= "
          f"{self.driver.extended_conf.left_arm_min_y_position} mm"
        )

    y_position = round(y * 10)
    if not 0 <= y_position <= 6500:
      raise ValueError("y_position must be between 0 and 650 mm")
    await self.send_command(
      module="C0",
      command="KY",
      pn=f"{self.index + 1:02}",
      yj=f"{y_position:04}",
    )

  # -- Px:RZ  probe Z position -----------------------------------------------

  async def request_probe_z_position(self) -> float:
    """Request the z-position of the channel probe (EXCLUDING the tip)"""
    resp = await self.send_command(module=self.module_id, command="RZ", fmt="rz######")
    increments = resp["rz"]
    return _z_inc_to_mm(increments)

  # -- C0:RD  tip bottom Z position -------------------------------------------

  async def request_tip_bottom_z_position(self) -> float:
    """Request Z-position of the tip bottom on this channel (mm) (C0:RD).

    Raises RuntimeError if no tip is mounted.
    """
    if not (await self.backend.request_tip_presence())[self.index]:
      raise RuntimeError(f"No tip mounted on channel {self.index}")
    resp = await self.driver.send_command(
      module="C0",
      command="RD",
      fmt="rd####",
      pn=f"{self.index + 1:02}",
    )
    return float(resp["rd"] / 10)

  # -- tip length measurement --------------------------------------------------

  async def request_tip_length(self) -> float:
    """Measure the length of the tip on this channel (mm).

    Raises RuntimeError if no tip is present.
    """
    tip_presence = await self.backend.request_tip_presence()
    if not tip_presence[self.index]:
      raise RuntimeError(f"No tip present on channel {self.index}")

    probe_z = await self.request_probe_z_position()
    tip_bottom_z = await self.request_tip_bottom_z_position()

    fitting_depth = 8  # mm
    return round(probe_z - (tip_bottom_z - fitting_depth), 1)

  # -- Px:QC  volume in tip ---------------------------------------------------

  async def request_volume_in_tip(self) -> float:
    resp = await self.send_command(self.module_id, "QC", fmt="qc##### (n)")
    _, current_volume = resp["qc"]  # first is max volume
    return float(current_volume) / 10

  # -- C0:QS  TADM enabled -----------------------------------------------------

  async def request_tadm_enabled(self) -> bool:
    """Whether TADM (Total Aspiration and Dispensing Monitoring) is enabled on this channel."""
    resp = await self.driver.send_command(
      module="C0",
      command="QS",
      fmt="qs# (n)",
    )
    return bool(resp["qs"][self.index])

  # -- Px:ZL  cLLD Z search (low-level, head-space) --------------------------

  async def search_z_using_clld(
    self,
    lowest_immers_pos: float = 99.98,  # mm
    start_pos_search: float = 334.7,  # mm
    channel_speed: float = 10.0,  # mm
    channel_acceleration: float = 800.0,  # mm/sec**2
    detection_edge: int = 10,
    detection_drop: int = 2,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_dist: float = 2.0,  # mm
  ):
    """Move the tip on a channel to the liquid surface using capacitive LLD (cLLD).

    Runs a downward capacitive liquid-level detection (cLLD) search on the specified
    0-indexed channel. The search will not go below lowest_immers_pos. After detection,
    the channel performs the configured post-detection move (by default retracting 2.0 mm).

    This is a low level method that takes parameters in "head space", not using the tip length.

    Args:
      lowest_immers_pos: Lowest allowed search position in mm (hard stop). Defaults to 99.98.
      start_pos_search: Search start position in mm. Defaults to 334.7.
      channel_speed: Search speed in mm/s. Defaults to 10.0.
      channel_acceleration: Search acceleration in mm/s^2. Defaults to 800.0.
      detection_edge: Edge steepness threshold for cLLD detection (0-1023). Defaults to 10.
      detection_drop: Offset applied after cLLD edge detection (0-1023). Defaults to 2.
      post_detection_trajectory: Instrument post-detection move mode (0 or 1). Defaults to 1.
      post_detection_dist: Distance in mm to move after detection (interpreted per trajectory).
        Defaults to 2.0.

    Raises:
      ValueError: If any parameter is outside the instrument-supported range.
    """

    # Conversions & machine-compatibility check of parameters
    lowest_immers_pos_increments = _mm_to_z_inc(lowest_immers_pos)
    start_pos_search_increments = _mm_to_z_inc(start_pos_search)
    channel_speed_increments = _mm_to_z_inc(channel_speed)
    channel_acceleration_thousand_increments = _mm_to_z_inc(channel_acceleration / 1000)
    post_detection_dist_increments = _mm_to_z_inc(post_detection_dist)

    if not (9_320 <= lowest_immers_pos_increments <= 31_200):
      raise ValueError(
        f"Lowest immersion position must be between \n{_z_inc_to_mm(9_320)}"
        + f" and {_z_inc_to_mm(31_200)} mm, is {lowest_immers_pos} mm"
      )
    if not (9_320 <= start_pos_search_increments <= 31_200):
      raise ValueError(
        f"Start position of LLD search must be between \n{_z_inc_to_mm(9_320)}"
        + f" and {_z_inc_to_mm(31_200)} mm, is {start_pos_search} mm"
      )
    if not (20 <= channel_speed_increments <= 15_000):
      raise ValueError(
        f"LLD search speed must be between \n{_z_inc_to_mm(20)}"
        + f"and {_z_inc_to_mm(15_000)} mm/sec, is {channel_speed} mm/sec"
      )
    if not (5 <= channel_acceleration_thousand_increments <= 150):
      raise ValueError(
        f"Channel acceleration must be between \n{_z_inc_to_mm(5 * 1_000)} "
        + f" and {_z_inc_to_mm(150 * 1_000)} mm/sec**2, is {channel_acceleration} mm/sec**2"
      )
    if not (0 <= detection_edge <= 1_023):
      raise ValueError("Edge steepness at capacitive LLD detection must be between 0 and 1023")
    if not (0 <= detection_drop <= 1_023):
      raise ValueError("Offset after capacitive LLD edge detection must be between 0 and 1023")
    if not (0 <= post_detection_dist_increments <= 9_999):
      raise ValueError(
        "Post cLLD-detection movement distance must be between \n0"
        + f" and {_z_inc_to_mm(9_999)} mm, is {post_detection_dist} mm"
      )

    await self.send_command(
      module=self.module_id,
      command="ZL",
      zh=f"{lowest_immers_pos_increments:05}",  # Lowest immersion position [increment]
      zc=f"{start_pos_search_increments:05}",  # Start position of LLD search [increment]
      zl=f"{channel_speed_increments:05}",  # Speed of channel movement
      zr=f"{channel_acceleration_thousand_increments:03}",  # Acceleration [1000 increment/second^2]
      gt=f"{detection_edge:04}",  # Edge steepness at capacitive LLD detection
      gl=f"{detection_drop:04}",  # Offset after capacitive LLD edge detection
      zj=post_detection_trajectory,  # Movement of the channel after contacting surface
      zi=f"{post_detection_dist_increments:04}",  # Distance to move up after detection [increment]
    )

  # -- Px:ZE  pLLD Z search (low-level, head-space) --------------------------

  async def search_z_using_plld(
    self,
    lowest_immers_pos: float = 99.98,  # mm of the head_probe!
    start_pos_search: float = 334.7,  # mm of the head_probe!
    channel_speed_above_start_pos_search: float = 120.0,  # mm/sec
    channel_speed: float = 10.0,  # mm
    channel_acceleration: float = 800.0,  # mm/sec**2
    z_drive_current_limit: int = 3,
    tip_has_filter: bool = False,
    dispense_drive_speed: float = 5.0,  # mm/sec
    dispense_drive_acceleration: float = 0.2,  # mm/sec**2
    dispense_drive_max_speed: float = 14.5,  # mm/sec
    dispense_drive_current_limit: int = 3,
    plld_detection_edge: int = 30,
    plld_detection_drop: int = 10,
    clld_verification: bool = False,  # cLLD Verification feature
    clld_detection_edge: int = 10,  # cLLD Verification feature
    clld_detection_drop: int = 2,  # cLLD Verification feature
    max_delta_plld_clld: float = 5.0,  # cLLD Verification feature; mm
    plld_mode: Optional[PressureLLDMode] = None,  # Foam feature
    plld_foam_detection_drop: int = 30,  # Foam feature
    plld_foam_detection_edge_tolerance: int = 30,  # Foam feature
    plld_foam_ad_values: int = 30,  # Foam feature; unknown unit
    plld_foam_search_speed: float = 10.0,  # Foam feature; mm/sec
    dispense_back_plld_volume: Optional[float] = None,  # uL
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_dist: float = 2.0,  # mm
  ) -> Tuple[float, float]:
    """Search a surface using pressured-based liquid level detection (pLLD)
    (1) with or (2) without additional cLLD verification, and (a) with foam detection sub-mode or
    (b) without foam detection sub-mode.

    Notes:
    - This command is implemented  via the PX command module, i.e. it IS parallelisable
    - lowest_immers_pos & start_pos_search refer to the head_probe z-coordinate (not the tip)
    - The return values represent head_probe z-positions (not the tip) in mm

    Args:
      lowest_immers_pos: Lowest allowed Z during the search (mm). Default 99.98.
      start_pos_search: Z position where the search begins (mm). Default 334.7.
      channel_speed_above_start_pos_search: Z speed above the start position (mm/s). Default 120.0.
      channel_speed: Z search speed (mm/s). Default 10.0.
      channel_acceleration: Z acceleration (mm/s**2). Default 800.0.
      z_drive_current_limit: Z drive current limit (instrument units). Default 3.
      tip_has_filter: Whether a filter tip is mounted. Default False.
      dispense_drive_speed: Dispense drive speed (mm/s). Default 5.0.
      dispense_drive_acceleration: Dispense drive acceleration (mm/s**2). Default 0.2.
      dispense_drive_max_speed: Dispense drive max speed (mm/s). Default 14.5.
      dispense_drive_current_limit: Dispense drive current limit (instrument units). Default 3.
      plld_detection_edge: Pressure detection edge threshold. Default 30.
      plld_detection_drop: Pressure detection drop threshold. Default 10.
      clld_verification: Activates cLLD sensing concurrently with the pressure probing. Note: cLLD
        measurement itself cannot be retrieved. Instead it can be used for other applications, including
        (1) verification of the surface level detected by pLLD based on max_delta_plld_clld,
        (2) detection of foam (more easily triggers cLLD), if desired, causing an error.
        This activates all cLLD-specific arguments. Default False.
      max_delta_plld_clld: Max allowed delta between pressure/capacitive detections (mm). Default 5.0.
      clld_detection_edge: Capacitive detection edge threshold. Default 10.
      clld_detection_drop: Capacitive detection drop threshold. Default 2.
      plld_mode: Pressure-detection sub-mode (instrument-defined). Default None.
      plld_foam_detection_drop: Foam detection drop threshold. Default 30.
      plld_foam_detection_edge_tolerance: Foam detection edge tolerance. Default 30.
      plld_foam_ad_values: Foam AD values (instrument units). Default 30.
      plld_foam_search_speed: Foam search speed (mm/s). Default 10.0.
      dispense_back_plld_volume: Optional dispense-back volume after detection (uL). Default None.
      post_detection_trajectory: Post-detection movement pattern selector. Default 1.
      post_detection_dist: Post-detection movement distance (mm). Default 2.0.

    Returns:
      Two z-coordinates (mm), head_probe, meaning depends on the selected pressure sub-mode:
      - Single-detection modes/PressureLLDMode.LIQUID: (liquid_level_pos, 0)
      - Two-detection modes/PressureLLDMode.FOAM: (first_detection_pos, liquid_level_pos)
    """

    if plld_mode is None:
      plld_mode = PressureLLDMode.LIQUID

    if dispense_back_plld_volume is None:
      dispense_back_plld_volume_mode = 0
      dispense_back_plld_volume_increments = 0
    else:
      dispense_back_plld_volume_mode = 1
      dispense_back_plld_volume_increments = _vol_to_disp_inc(dispense_back_plld_volume)

    # Conversions to machine units
    lowest_immers_pos_increments = _mm_to_z_inc(lowest_immers_pos)
    start_pos_search_increments = _mm_to_z_inc(start_pos_search)

    channel_speed_above_start_pos_search_increments = _mm_to_z_inc(
      channel_speed_above_start_pos_search
    )
    channel_speed_increments = _mm_to_z_inc(channel_speed)
    channel_acceleration_thousand_increments = _mm_to_z_inc(channel_acceleration / 1000)

    dispense_drive_speed_increments = _mm_to_disp_inc(dispense_drive_speed)
    dispense_drive_acceleration_increments = _mm_to_disp_inc(dispense_drive_acceleration)
    dispense_drive_max_speed_increments = _mm_to_disp_inc(dispense_drive_max_speed)

    post_detection_dist_increments = _mm_to_z_inc(post_detection_dist)
    max_delta_plld_clld_increments = _mm_to_z_inc(max_delta_plld_clld)

    plld_foam_search_speed_increments = _mm_to_z_inc(plld_foam_search_speed)

    # Machine-compatibility parameter checks
    if not (9_320 <= lowest_immers_pos_increments <= 31_200):
      raise ValueError(
        f"Lowest immersion position must be between \n{_z_inc_to_mm(9_320)}"
        + f" and {_z_inc_to_mm(31_200)} mm, is {lowest_immers_pos} mm"
      )
    if not (9_320 <= start_pos_search_increments <= 31_200):
      raise ValueError(
        f"Start position of LLD search must be between \n{_z_inc_to_mm(9_320)}"
        + f" and {_z_inc_to_mm(31_200)} mm, is {start_pos_search} mm"
      )

    if tip_has_filter not in [True, False]:
      raise TypeError("tip_has_filter must be a boolean")

    if not isinstance(clld_verification, bool):
      raise TypeError(f"clld_verification must be a boolean, is {clld_verification}")

    if plld_mode not in [PressureLLDMode.LIQUID, PressureLLDMode.FOAM]:
      raise ValueError(
        f"plld_mode must be either PressureLLDMode.LIQUID ({PressureLLDMode.LIQUID}) or "
        + f"PressureLLDMode.FOAM ({PressureLLDMode.FOAM}), is {plld_mode}"
      )

    if not (20 <= channel_speed_above_start_pos_search_increments <= 15_000):
      raise ValueError(
        "Speed above start position of LLD search must be between \n"
        + f"{_z_inc_to_mm(20)} and "
        + f"{_z_inc_to_mm(15_000)} mm/sec, is "
        + f"{channel_speed_above_start_pos_search} mm/sec"
      )
    if not (20 <= channel_speed_increments <= 15_000):
      raise ValueError(
        f"LLD search speed must be between \n{_z_inc_to_mm(20)}"
        + f"and {_z_inc_to_mm(15_000)} mm/sec, is {channel_speed} mm/sec"
      )
    if not (5 <= channel_acceleration_thousand_increments <= 150):
      raise ValueError(
        f"Channel acceleration must be between \n{_z_inc_to_mm(5 * 1_000)} "
        + f" and {_z_inc_to_mm(150 * 1_000)} mm/sec**2, is {channel_acceleration} mm/sec**2"
      )
    if not (0 <= z_drive_current_limit <= 7):
      raise ValueError(f"Z-drive current limit must be between 0 and 7, is {z_drive_current_limit}")

    if not (20 <= dispense_drive_speed_increments <= 13_500):
      raise ValueError(
        "Dispensing drive speed must be between \n"
        + f"{_disp_inc_to_mm(20)} and "
        + f"{_disp_inc_to_mm(13_500)} mm/sec, is {dispense_drive_speed} mm/sec"
      )
    if not (1 <= dispense_drive_acceleration_increments <= 100):
      raise ValueError(
        "Dispensing drive acceleration must be between \n"
        + f"{_disp_inc_to_mm(1)} and "
        + f"{_disp_inc_to_mm(100)} mm/sec**2, is {dispense_drive_acceleration} mm/sec**2"
      )
    if not (20 <= dispense_drive_max_speed_increments <= 13_500):
      raise ValueError(
        "Dispensing drive max speed must be between \n"
        + f"{_disp_inc_to_mm(20)} and "
        + f"{_disp_inc_to_mm(13_500)} mm/sec, is {dispense_drive_max_speed} mm/sec"
      )
    if not (0 <= dispense_drive_current_limit <= 7):
      raise ValueError(
        f"Dispensing drive current limit must be between 0 and 7, is {dispense_drive_current_limit}"
      )

    if not (0 <= clld_detection_edge <= 1_023):
      raise ValueError("Edge steepness at capacitive LLD detection must be between 0 and 1023")
    if not (0 <= clld_detection_drop <= 1_023):
      raise ValueError("Offset after capacitive LLD edge detection must be between 0 and 1023")
    if not (0 <= plld_detection_edge <= 1_023):
      raise ValueError("Edge steepness at pressure LLD detection must be between 0 and 1023")
    if not (0 <= plld_detection_drop <= 1_023):
      raise ValueError("Offset after pressure LLD edge detection must be between 0 and 1023")

    if not (0 <= max_delta_plld_clld_increments <= 9_999):
      raise ValueError(
        "Maximum allowed difference between pressure LLD and capacitive LLD detection z-positions "
        + f"must be between 0 and {_z_inc_to_mm(9_999)} mm,"
        + f" is {max_delta_plld_clld} mm"
      )

    if not (0 <= plld_foam_detection_drop <= 1_023):
      raise ValueError(
        f"Pressure LLD foam detection drop must be between 0 and 1023, is {plld_foam_detection_drop}"
      )
    if not (0 <= plld_foam_detection_edge_tolerance <= 1_023):
      raise ValueError(
        "Pressure LLD foam detection edge tolerance must be between 0 and 1023, "
        + f"is {plld_foam_detection_edge_tolerance}"
      )
    if not (0 <= plld_foam_ad_values <= 4_999):
      raise ValueError(
        f"Pressure LLD foam AD values must be between 0 and 4999, is {plld_foam_ad_values}"
      )
    if not (20 <= plld_foam_search_speed_increments <= 13_500):
      raise ValueError(
        "Pressure LLD foam search speed must be between \n"
        + f"{_z_inc_to_mm(20)} and "
        + f"{_z_inc_to_mm(13_500)} mm/sec, is {plld_foam_search_speed} mm/sec"
      )

    if dispense_back_plld_volume_mode not in [0, 1]:
      raise ValueError(
        "dispense_back_plld_volume_mode must be either 0 ('normal') or 1 "
        + "('dispense back dispense_back_plld_volume'), "
        + f"is {dispense_back_plld_volume_mode}"
      )

    if not (0 <= dispense_back_plld_volume_increments <= 26_666):
      raise ValueError(
        "Dispense back pressure LLD volume must be between \n0"
        + f" and {_disp_inc_to_vol(26_666)} uL, is {dispense_back_plld_volume} uL"
      )

    if not (0 <= post_detection_dist_increments <= 9_999):
      raise ValueError(
        "Post cLLD-detection movement distance must be between \n0"
        + f" and {_z_inc_to_mm(9_999)} mm, is {post_detection_dist} mm"
      )

    resp_raw = await self.send_command(
      module=self.module_id,
      command="ZE",
      zh=f"{lowest_immers_pos_increments:05}",
      zc=f"{start_pos_search_increments:05}",
      zi=f"{post_detection_dist_increments:04}",
      zj=f"{post_detection_trajectory:01}",
      gf=str(int(tip_has_filter)),
      gt=f"{clld_detection_edge:04}",
      gl=f"{clld_detection_drop:04}",
      gu=f"{plld_detection_edge:04}",
      gn=f"{plld_detection_drop:04}",
      gm=str(int(clld_verification)),
      gz=f"{max_delta_plld_clld_increments:04}",
      cj=str(plld_mode.value),
      co=f"{plld_foam_detection_drop:04}",
      cp=f"{plld_foam_detection_edge_tolerance:04}",
      cq=f"{plld_foam_ad_values:04}",
      cl=f"{plld_foam_search_speed_increments:05}",
      cc=str(dispense_back_plld_volume_mode),
      cd=f"{dispense_back_plld_volume_increments:05}",
      zv=f"{channel_speed_above_start_pos_search_increments:05}",
      zl=f"{channel_speed_increments:05}",
      zr=f"{channel_acceleration_thousand_increments:03}",
      zw=f"{z_drive_current_limit}",
      dl=f"{dispense_drive_speed_increments:05}",
      dr=f"{dispense_drive_acceleration_increments:03}",
      dv=f"{dispense_drive_max_speed_increments:05}",
      dw=f"{dispense_drive_current_limit}",
      read_timeout=max(self.driver.read_timeout, 120),  # it can take long (>30s)
    )
    if resp_raw is None:
      raise RuntimeError("No response received from pLLD search command")

    resp_probe_mm = [
      _z_inc_to_mm(int(return_val)) for return_val in resp_raw.split("if")[-1].split()
    ]

    # return depending on mode
    return (
      (resp_probe_mm[0], 0)
      if plld_mode == PressureLLDMode.LIQUID
      else (resp_probe_mm[0], resp_probe_mm[1])
    )

  # -- Px:SI  violently shoot down tip ----------------------------------------

  async def violently_shoot_down_tip(self):
    """Shoot down the tip on the specified channel by releasing the drive that holds the spring. The
    tips will shoot down in place at an acceleration bigger than g. This is done by initializing
    the squeezer drive wihile a tip is mounted.

    Safe to do when above a tip rack, for example directly after a tip pickup.

    .. warning::

      Consider this method an easter egg. Not for serious use.
    """
    await self.send_command(module=self.module_id, command="SI")

  # ---------------------------------------------------------------------------
  # Probe / query methods — delegate to backend C0 helpers as needed.
  # ---------------------------------------------------------------------------

  async def clld_probe_z_height(
    self,
    lowest_immers_pos: float = 99.98,
    start_pos_search: Optional[float] = None,
    channel_speed: float = 10.0,
    channel_acceleration: float = 800.0,
    detection_edge: int = 10,
    detection_drop: int = 2,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_dist: float = 2.0,
    move_channels_to_safe_pos_after: bool = False,
  ) -> float:
    """Probe the liquid surface Z-height using this channel's capacitive LLD (cLLD).

    Ensures a tip is mounted, reads the tip length, converts tip-referenced positions
    to head-space coordinates, then delegates to :meth:`search_z_using_clld`.

    Args:
      lowest_immers_pos: Lowest allowed search position in mm (tip-referenced).
      start_pos_search: Start position for the cLLD search in mm (tip-referenced).
        If None, the highest safe position is used based on tip length.
      channel_speed: Search speed in mm/s.
      channel_acceleration: Search acceleration in mm/s^2.
      detection_edge: Edge steepness threshold for cLLD detection (0-1023).
      detection_drop: Offset applied after cLLD edge detection (0-1023).
      post_detection_trajectory: Firmware post-detection move mode (0 or 1).
      post_detection_dist: Distance in mm to move after detection.
      move_channels_to_safe_pos_after: If True, moves all channels to Z-safe after.

    Returns:
      The detected liquid surface Z-height in mm.

    Raises:
      RuntimeError: If no tip is mounted on this channel.
    """
    assert self.backend is not None, "backend reference required for clld_probe_z_height"

    tip_presence = await self.backend.request_tip_presence()
    if not tip_presence[self.index]:
      raise RuntimeError(f"No tip mounted on channel {self.index}")

    tip_len = await self.request_tip_length()
    safe_tip_top_z_pos = MAXIMUM_CHANNEL_Z_POSITION - tip_len + DEFAULT_TIP_FITTING_DEPTH

    if start_pos_search is None:
      start_pos_search = safe_tip_top_z_pos

    if lowest_immers_pos < MINIMUM_CHANNEL_Z_POSITION:
      raise ValueError(
        f"lowest_immers_pos must be at least {MINIMUM_CHANNEL_Z_POSITION} mm "
        f"but is {lowest_immers_pos} mm"
      )

    # Convert tip-space to head-space
    lowest_immers_pos_head_space = lowest_immers_pos + tip_len - DEFAULT_TIP_FITTING_DEPTH
    channel_head_start_pos = round(start_pos_search + tip_len - DEFAULT_TIP_FITTING_DEPTH, 2)

    if not (lowest_immers_pos <= start_pos_search <= safe_tip_top_z_pos):
      raise ValueError(
        f"Start position of LLD search must be between "
        f"{lowest_immers_pos} and {safe_tip_top_z_pos} mm, is {start_pos_search} mm"
      )

    try:
      await self.search_z_using_clld(
        lowest_immers_pos=lowest_immers_pos_head_space,
        start_pos_search=channel_head_start_pos,
        channel_speed=channel_speed,
        channel_acceleration=channel_acceleration,
        detection_edge=detection_edge,
        detection_drop=detection_drop,
        post_detection_trajectory=post_detection_trajectory,
        post_detection_dist=post_detection_dist,
      )
    except STARFirmwareError:
      await self.move_to_z_safety()
      raise

    if move_channels_to_safe_pos_after:
      await self.move_to_z_safety()

    heights = await self.backend.request_pip_height_last_lld()
    return heights[self.index]

  async def plld_probe_z_height(
    self,
    lowest_immers_pos: float = 99.98,
    start_pos_search: Optional[float] = None,
    channel_speed_above_start_pos_search: float = 120.0,
    channel_speed: float = 10.0,
    channel_acceleration: float = 800.0,
    z_drive_current_limit: int = 3,
    tip_has_filter: bool = False,
    dispense_drive_speed: float = 5.0,
    dispense_drive_acceleration: float = 0.2,
    dispense_drive_max_speed: float = 14.5,
    dispense_drive_current_limit: int = 3,
    plld_detection_edge: int = 30,
    plld_detection_drop: int = 10,
    clld_verification: bool = False,
    clld_detection_edge: int = 10,
    clld_detection_drop: int = 2,
    max_delta_plld_clld: float = 5.0,
    plld_mode: Optional[PressureLLDMode] = None,
    plld_foam_detection_drop: int = 30,
    plld_foam_detection_edge_tolerance: int = 30,
    plld_foam_ad_values: int = 30,
    plld_foam_search_speed: float = 10.0,
    dispense_back_plld_volume: Optional[float] = None,
    post_detection_trajectory: Literal[0, 1] = 1,
    post_detection_dist: float = 2.0,
    move_channels_to_safe_pos_after: bool = False,
  ) -> Tuple[float, float]:
    """Probe the liquid surface Z-height using pressure-based LLD (pLLD).

    Ensures a tip is mounted, reads the tip length, converts tip-referenced positions
    to head-space coordinates, then delegates to :meth:`search_z_using_plld`.

    All positions in args are in the *tip-referenced* coordinate system. Returned
    positions are also in tip-space.

    Args:
      lowest_immers_pos: Lowest allowed search position in mm (tip-referenced).
      start_pos_search: Start position in mm (tip-referenced). If None, highest safe.
      channel_speed_above_start_pos_search: Z speed above the start position (mm/s).
      channel_speed: Z search speed (mm/s).
      channel_acceleration: Z acceleration (mm/s^2).
      z_drive_current_limit: Z drive current limit.
      tip_has_filter: Whether a filter tip is mounted.
      dispense_drive_speed: Dispense drive speed (mm/s).
      dispense_drive_acceleration: Dispense drive acceleration (mm/s^2).
      dispense_drive_max_speed: Dispense drive max speed (mm/s).
      dispense_drive_current_limit: Dispense drive current limit.
      plld_detection_edge: Pressure detection edge threshold.
      plld_detection_drop: Pressure detection drop threshold.
      clld_verification: Activate cLLD sensing concurrently.
      clld_detection_edge: Capacitive detection edge threshold.
      clld_detection_drop: Capacitive detection drop threshold.
      max_delta_plld_clld: Max delta between pLLD and cLLD detections (mm).
      plld_mode: Pressure-detection sub-mode.
      plld_foam_detection_drop: Foam detection drop threshold.
      plld_foam_detection_edge_tolerance: Foam detection edge tolerance.
      plld_foam_ad_values: Foam AD values.
      plld_foam_search_speed: Foam search speed (mm/s).
      dispense_back_plld_volume: Dispense-back volume after detection (uL).
      post_detection_trajectory: Post-detection movement pattern selector.
      post_detection_dist: Post-detection movement distance (mm).
      move_channels_to_safe_pos_after: Move all channels to Z-safe after probing.

    Returns:
      Two z-coordinates (mm) in tip-space:
      - PressureLLDMode.LIQUID: ``(liquid_level_pos, 0.0)``
      - PressureLLDMode.FOAM: ``(first_detection_pos, liquid_level_pos)``
    """
    assert self.backend is not None, "backend reference required for plld_probe_z_height"

    if plld_mode is None:
      plld_mode = PressureLLDMode.LIQUID

    tip_presence = await self.backend.request_tip_presence()
    if not tip_presence[self.index]:
      raise RuntimeError(f"No tip mounted on channel {self.index}")

    tip_len = await self.request_tip_length()
    safe_tip_top_z_pos = MAXIMUM_CHANNEL_Z_POSITION - tip_len + DEFAULT_TIP_FITTING_DEPTH

    if start_pos_search is None:
      start_pos_search = safe_tip_top_z_pos

    if lowest_immers_pos < MINIMUM_CHANNEL_Z_POSITION:
      raise ValueError(
        f"lowest_immers_pos must be at least {MINIMUM_CHANNEL_Z_POSITION} mm "
        f"but is {lowest_immers_pos} mm"
      )

    # Convert tip-space to head-space
    lowest_immers_pos_head_space = lowest_immers_pos + tip_len - DEFAULT_TIP_FITTING_DEPTH
    channel_head_start_pos = round(start_pos_search + tip_len - DEFAULT_TIP_FITTING_DEPTH, 2)

    if not (lowest_immers_pos <= start_pos_search <= safe_tip_top_z_pos):
      raise ValueError(
        f"Start position of LLD search must be between "
        f"{lowest_immers_pos} and {safe_tip_top_z_pos} mm, is {start_pos_search} mm"
      )

    try:
      resp_probe_mm = await self.search_z_using_plld(
        lowest_immers_pos=lowest_immers_pos_head_space,
        start_pos_search=channel_head_start_pos,
        channel_speed_above_start_pos_search=channel_speed_above_start_pos_search,
        channel_speed=channel_speed,
        channel_acceleration=channel_acceleration,
        z_drive_current_limit=z_drive_current_limit,
        tip_has_filter=tip_has_filter,
        dispense_drive_speed=dispense_drive_speed,
        dispense_drive_acceleration=dispense_drive_acceleration,
        dispense_drive_max_speed=dispense_drive_max_speed,
        dispense_drive_current_limit=dispense_drive_current_limit,
        plld_detection_edge=plld_detection_edge,
        plld_detection_drop=plld_detection_drop,
        clld_verification=clld_verification,
        clld_detection_edge=clld_detection_edge,
        clld_detection_drop=clld_detection_drop,
        max_delta_plld_clld=max_delta_plld_clld,
        plld_mode=plld_mode,
        plld_foam_detection_drop=plld_foam_detection_drop,
        plld_foam_detection_edge_tolerance=plld_foam_detection_edge_tolerance,
        plld_foam_ad_values=plld_foam_ad_values,
        plld_foam_search_speed=plld_foam_search_speed,
        dispense_back_plld_volume=dispense_back_plld_volume,
        post_detection_trajectory=post_detection_trajectory,
        post_detection_dist=post_detection_dist,
      )
    except STARFirmwareError:
      await self.move_to_z_safety()
      raise

    # Convert head-space response to tip-space
    if plld_mode == PressureLLDMode.FOAM:
      resp_tip_mm = (
        round(resp_probe_mm[0] - tip_len + DEFAULT_TIP_FITTING_DEPTH, 2),
        round(resp_probe_mm[1] - tip_len + DEFAULT_TIP_FITTING_DEPTH, 2),
      )
    else:
      resp_tip_mm = (
        round(resp_probe_mm[0] - tip_len + DEFAULT_TIP_FITTING_DEPTH, 2),
        0.0,
      )

    if move_channels_to_safe_pos_after:
      await self.move_to_z_safety()

    return resp_tip_mm

  async def ztouch_probe_z_height(
    self,
    tip_len: Optional[float] = None,
    lowest_immers_pos: float = 99.98,
    start_pos_search: Optional[float] = None,
    channel_speed: float = 10.0,
    channel_acceleration: float = 800.0,
    channel_speed_upwards: float = 125.0,
    detection_limiter_in_PWM: int = 1,
    push_down_force_in_PWM: int = 0,
    post_detection_dist: float = 2.0,
    move_channels_to_safe_pos_after: bool = False,
  ) -> float:
    """Probe the Z-height using z-touch-off (controlled z-drive crash detection).

    Args:
      tip_len: Override the tip length (mm). If None, measured automatically.
      lowest_immers_pos: Lowest allowed search position in mm (tip-referenced).
      start_pos_search: Start position in mm (tip-referenced). If None, highest safe.
      channel_speed: Search speed downward in mm/s.
      channel_acceleration: Acceleration in mm/s^2.
      channel_speed_upwards: Retraction speed in mm/s.
      detection_limiter_in_PWM: Offset PWM limiter value for searching (0-125).
      push_down_force_in_PWM: Offset PWM value for push down force (0-125).
      post_detection_dist: Distance to retract after detection in mm.
      move_channels_to_safe_pos_after: Move all channels to Z-safe after probing.

    Returns:
      The detected Z-height in mm (tip-referenced).

    Raises:
      ValueError: If the channel firmware predates 2022 (z-touch not supported).
    """
    assert self.backend is not None, "backend reference required for ztouch_probe_z_height"

    version = await self.request_firmware_version()
    year_matches = re.search(r"\b\d{4}\b", version)
    if year_matches is not None:
      year = int(year_matches.group())
      if year < 2022:
        raise ValueError(
          "Z-touch probing is not supported for PIP versions predating 2022, "
          f"found version '{version}'"
        )

    if tip_len is None:
      tip_len = await self.request_tip_length()

    if start_pos_search is None:
      start_pos_search = MAXIMUM_CHANNEL_Z_POSITION - tip_len + DEFAULT_TIP_FITTING_DEPTH

    tip_len_used_in_increments = _mm_to_z_inc(tip_len - DEFAULT_TIP_FITTING_DEPTH)
    channel_head_start_pos = start_pos_search + tip_len - DEFAULT_TIP_FITTING_DEPTH
    lowest_immers_pos_head_space = lowest_immers_pos + tip_len - DEFAULT_TIP_FITTING_DEPTH
    safe_head_bottom_z_pos = MINIMUM_CHANNEL_Z_POSITION + tip_len - DEFAULT_TIP_FITTING_DEPTH
    safe_head_top_z_pos = MAXIMUM_CHANNEL_Z_POSITION

    lowest_immers_pos_increments = _mm_to_z_inc(lowest_immers_pos_head_space)
    start_pos_search_increments = _mm_to_z_inc(channel_head_start_pos)
    channel_speed_increments = _mm_to_z_inc(channel_speed)
    channel_acceleration_thousand_increments = _mm_to_z_inc(channel_acceleration / 1000)
    channel_speed_upwards_increments = _mm_to_z_inc(channel_speed_upwards)

    if not (0 <= self.index <= 15):
      raise ValueError(f"channel index must be between 0 and 15, is {self.index}")
    if not (20 <= tip_len <= 120):
      raise ValueError(f"Total tip length must be between 20 and 120, is {tip_len}")
    if not (9320 <= lowest_immers_pos_increments <= 31200):
      raise ValueError(
        f"Lowest immersion position must be between 99.98 and 334.7 mm, is {lowest_immers_pos} mm"
      )
    if not (safe_head_bottom_z_pos <= channel_head_start_pos <= safe_head_top_z_pos):
      raise ValueError(
        f"Start position of search must be between "
        f"{safe_head_bottom_z_pos} and {safe_head_top_z_pos} mm, "
        f"is {channel_head_start_pos} mm"
      )
    if not (20 <= channel_speed_increments <= 15000):
      raise ValueError(
        f"Z-touch search speed must be between "
        f"{_z_inc_to_mm(20)} and {_z_inc_to_mm(15000)} mm/s, is {channel_speed} mm/s"
      )
    if not (5 <= channel_acceleration_thousand_increments <= 150):
      raise ValueError(
        f"Channel acceleration must be between "
        f"{_z_inc_to_mm(5000)} and {_z_inc_to_mm(150000)} mm/s^2, "
        f"is {channel_acceleration} mm/s^2"
      )
    if not (20 <= channel_speed_upwards_increments <= 15000):
      raise ValueError(
        f"Channel retraction speed must be between "
        f"{_z_inc_to_mm(20)} and {_z_inc_to_mm(15000)} mm/s, "
        f"is {channel_speed_upwards} mm/s"
      )
    if not (0 <= detection_limiter_in_PWM <= 125):
      raise ValueError("Detection limiter value must be between 0 and 125 PWM.")
    if not (0 <= push_down_force_in_PWM <= 125):
      raise ValueError("Push down force must be between 0 and 125 PWM values")
    if not (0 <= post_detection_dist <= 245):
      raise ValueError(
        f"Post detection distance must be between 0 and 245 mm, is {post_detection_dist}"
      )

    ztouch_probed_z_height = await self.send_command(
      module=self.module_id,
      command="ZH",
      zb=f"{start_pos_search_increments:05}",
      za=f"{lowest_immers_pos_increments:05}",
      zv=f"{channel_speed_upwards_increments:05}",
      zr=f"{channel_acceleration_thousand_increments:03}",
      zu=f"{channel_speed_increments:05}",
      cg=f"{detection_limiter_in_PWM:03}",
      cf=f"{push_down_force_in_PWM:03}",
      fmt="rz#####",
    )

    result_in_mm = _z_inc_to_mm(ztouch_probed_z_height["rz"] - tip_len_used_in_increments)

    if post_detection_dist != 0:
      await self.move_tool_z(result_in_mm + post_detection_dist)

    if move_channels_to_safe_pos_after:
      await self.move_to_z_safety()

    return float(result_in_mm)

  async def clld_probe_y_position(
    self,
    probing_direction: Literal["forward", "backward"],
    start_pos_search: Optional[float] = None,
    end_pos_search: Optional[float] = None,
    channel_speed: float = 10.0,
    channel_acceleration_int: Literal[1, 2, 3, 4] = 4,
    detection_edge: int = 10,
    current_limit_int: Literal[1, 2, 3, 4, 5, 6, 7] = 7,
    post_detection_dist: float = 2.0,
    tip_bottom_diameter: float = 1.2,
  ) -> float:
    """Probe the y-position of a conductive material using cLLD.

    Moves this channel along the y-axis to detect a conductive surface. Safety checks
    prevent collisions with adjacent channels. After detection, the channel is retracted
    by ``post_detection_dist``.

    Args:
      probing_direction: ``"forward"`` (decreasing y) or ``"backward"`` (increasing y).
      start_pos_search: Initial y-position for the search (mm). If None, uses current.
      end_pos_search: Final y-position for the search (mm). If None, uses safe limit.
      channel_speed: Movement speed during probing (mm/s).
      channel_acceleration_int: Acceleration ramp [1-4] (* 5000 steps/s^2).
      detection_edge: Edge steepness for capacitive detection (0-1023).
      current_limit_int: Current limit [1-7].
      post_detection_dist: Retraction distance after detection (mm).
      tip_bottom_diameter: Effective tip bottom diameter (mm).

    Returns:
      Corrected y-position of the detected material boundary in mm.
    """
    assert self.backend is not None, "backend reference required for clld_probe_y_position"

    if probing_direction not in ("forward", "backward"):
      raise ValueError(
        f"Probing direction must be either 'forward' or 'backward', is {probing_direction}."
      )

    # Anti-channel-crash: determine safe y bounds
    assert self.driver.extended_conf is not None
    if self.index > 0:
      adj_upper_y = await self.backend.channels[self.index - 1].request_y_pos()
      max_safe_upper_y_pos = adj_upper_y - self.driver._min_spacing_between(
        self.index, self.index - 1
      )
    else:
      max_safe_upper_y_pos = self.driver.extended_conf.pip_maximal_y_position

    if self.index < (self.backend.num_channels - 1):
      adj_lower_y = await self.backend.channels[self.index + 1].request_y_pos()
      max_safe_lower_y_pos = adj_lower_y + self.driver._min_spacing_between(
        self.index, self.index + 1
      )
    else:
      max_safe_lower_y_pos = self.driver.extended_conf.left_arm_min_y_position

    # Validate and optionally move to start position
    if start_pos_search is not None:
      if not (max_safe_lower_y_pos <= start_pos_search <= max_safe_upper_y_pos):
        raise ValueError(
          f"Start position for y search must be between "
          f"{max_safe_lower_y_pos} and {max_safe_upper_y_pos} mm, "
          f"is {start_pos_search} mm. Otherwise channel will crash."
        )
      await self.move_y(start_pos_search)

    if end_pos_search is not None:
      if not (max_safe_lower_y_pos <= end_pos_search <= max_safe_upper_y_pos):
        raise ValueError(
          f"End position for y search must be between "
          f"{max_safe_lower_y_pos} and {max_safe_upper_y_pos} mm, "
          f"is {end_pos_search} mm. Otherwise channel will crash."
        )

    # Set safe search end based on direction
    current_channel_y_pos = await self.request_y_pos()
    if probing_direction == "backward":
      max_y_search_pos = end_pos_search or max_safe_upper_y_pos
      if max_y_search_pos < current_channel_y_pos:
        raise ValueError(
          f"Channel {self.index} cannot move backward: "
          f"End position = {max_y_search_pos} < current position = {current_channel_y_pos}"
          f"\nDid you mean to move forward?"
        )
    else:  # forward
      max_y_search_pos = end_pos_search or max_safe_lower_y_pos
      if max_y_search_pos > current_channel_y_pos:
        raise ValueError(
          f"Channel {self.index} cannot move forward: "
          f"End position = {max_y_search_pos} > current position = {current_channel_y_pos}"
          f"\nDid you mean to move backward?"
        )

    # Convert to increments
    max_y_search_pos_increments = _mm_to_y_inc(max_y_search_pos)
    channel_speed_increments = _mm_to_y_inc(channel_speed)

    if not (0 <= max_y_search_pos_increments <= 13714):
      raise ValueError(
        f"Maximum y search position must be between 0 and "
        f"{_y_inc_to_mm(13714)} mm, is {max_y_search_pos} mm"
      )
    if not (20 <= channel_speed_increments <= 8000):
      raise ValueError(
        f"LLD search speed must be between {_y_inc_to_mm(20)} and "
        f"{_y_inc_to_mm(8000)} mm/s, is {channel_speed} mm/s"
      )
    if channel_acceleration_int not in (1, 2, 3, 4):
      raise ValueError(
        f"Channel acceleration must be in [1, 2, 3, 4], is {channel_acceleration_int}"
      )
    if not (0 <= detection_edge <= 1023):
      raise ValueError("Edge steepness must be between 0 and 1023")
    if not (0 <= current_limit_int <= 7):
      raise ValueError(f"Current limit must be between 0 and 7, is {current_limit_int}")

    # Send Px:YL command
    await self.send_command(
      module=self.module_id,
      command="YL",
      ya=f"{max_y_search_pos_increments:05}",
      gt=f"{detection_edge:04}",
      gl=f"{0:04}",  # always 0 to measure y-pos
      yv=f"{channel_speed_increments:04}",
      yr=f"{channel_acceleration_int}",
      yw=f"{current_limit_int}",
      read_timeout=120,
    )

    detected_material_y_pos = await self.request_y_pos()

    # Post-detection retraction with anti-crash
    if probing_direction == "backward":
      if self.index < self.backend.num_channels - 1:
        min_y = await self.backend.channels[
          self.index + 1
        ].request_y_pos() + self.driver._min_spacing_between(self.index, self.index + 1)
      else:
        min_y = self.driver.extended_conf.left_arm_min_y_position

      max_safe_dist = detected_material_y_pos - min_y
      move_target = detected_material_y_pos - min(post_detection_dist, max_safe_dist)
    else:  # forward
      if self.index > 0:
        max_y = await self.backend.channels[
          self.index - 1
        ].request_y_pos() - self.driver._min_spacing_between(self.index, self.index - 1)
      else:
        max_y = self.driver.extended_conf.pip_maximal_y_position

      max_safe_dist = max_y - detected_material_y_pos
      move_target = detected_material_y_pos + min(post_detection_dist, max_safe_dist)

    await self.move_y(move_target)

    # Correct for tip geometry
    if probing_direction == "backward":
      material_y_pos = detected_material_y_pos + tip_bottom_diameter / 2
    else:
      material_y_pos = detected_material_y_pos - tip_bottom_diameter / 2

    return round(material_y_pos, 1)
