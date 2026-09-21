"""The X-arm: the gantry the pipetting channels ride."""

from __future__ import annotations

import enum
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Optional, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

from .. import prep_commands as PrepCmd

if TYPE_CHECKING:
  from ..master import PrepDriver

logger = logging.getLogger(__name__)


@dataclass
class XArmConfiguration:
  """How the arm is modelled.

  Measured on the arm rather than read off the device: the Prep reports no size for it. X and Z are
  the model's own, which is the measured part; the back shuttle reaches left of that width and below
  that height, as it does on the arm.
  """

  size_x: float = 66.5
  """How wide the arm is, in mm: from the body's left face to the right face of its cable duct."""
  size_y: float = 532.0
  """How deep the arm is, in mm. Its back is flush with the back of the device."""
  size_z: float = 70.0
  """How tall the arm is, in mm, from the underside of its body to its top."""
  ride_height: float = 248.5
  """How high the arm rides, in mm on the deck, measured to the underside of its body. Measured on
  the arm: what the channels report is how far they travel, not where the arm sits."""
  reference_point_from_left: float = -80.0
  """Where the gantry's x refers to, in mm from the arm's left edge. The channels hang to the arm's left,
  so their axis - which is what the Prep reports - sits about 80 mm left of that edge."""
  model: str = "hamilton_prep_x_arm"
  """Which 3D model draws it. The model is measured off the arm, and stands where the arm does: its
  right edge on this box's right edge, its front edge and the body's underside on its own origin.
  The back shuttle reaches left of the box and below it, as the part does."""
  appearance: Dict[str, Any] = field(
    default_factory=lambda: {"color": 0xC0C4C8, "metalness": 0.6, "roughness": 0.35}
  )
  """How the viewer draws the arm: silver, and metallic."""
  speed_per_scale_percent: float = 6.0
  """How fast the gantry may drive X per percent of MLPrep's X speed scale, in mm/s. Measured: the
  X axis speed is this times the scale, up to the top of `speed_range`."""
  speed_range: Tuple[float, float] = (0.0, 400.0)
  """X speed window in mm/s: above the first, up to the second, the fastest the axis drives."""

  def speed_to_scale_percent(self, speed: float) -> int:
    """The X speed scale that drives the gantry at `speed`, rounded to the nearest percent.

    Args:
      speed: in mm/s, from `speed_per_scale_percent` to the top of `speed_range`.

    Raises:
      ValueError: If `speed` is outside that range.
    """
    high = self.speed_range[1]
    if not self.speed_per_scale_percent <= speed <= high:
      raise ValueError(
        f"x speed must be between {self.speed_per_scale_percent} and {high} mm/s, is {speed}"
      )
    return max(1, min(100, round(speed / self.speed_per_scale_percent)))


class XArm:
  """The X-arm the pipetting channels ride.

  Reached as `driver.x_arm`. The channels share its X: the firmware reports the gantry's position in
  each channel's `GetPositions` entry, not for the arm itself.
  """

  class TripSense(enum.IntEnum):
    """When `probe_home_flag` stops: the firmware's TripSense for `XAxis.SeekToHomeFlag`.

    On PRPAA1087 (V1.2.2) the home flag sensor read 1 with the gantry parked at the right end of its
    travel. Seeking left, SENSOR_0 tripped where the sensor changed to 0 (274.329 mm, axis frame) and
    SENSOR_1 tripped at once where it started. SENSOR_TOGGLE has not been run.
    """

    SENSOR_0 = 0
    SENSOR_1 = 1
    SENSOR_TOGGLE = 2

  def __init__(self, driver: "PrepDriver", configuration: Optional[XArmConfiguration] = None):
    """
    Args:
      driver: the driver to send commands through.
      configuration: how the arm is modelled. Defaults to `XArmConfiguration()`.
    """
    self._driver = driver
    self.configuration = configuration or XArmConfiguration()
    # The arm on the deck, when the driver was given one. Setup puts it there; reads and moves keep it
    # in step. Without a deck it stays None and nothing is modelled.
    self.resource: Optional[Resource] = None
    # Speed (mm/s) and acceleration (mm/s2) `move_to_x_position` uses when none is given: PyLabRobot's
    # defaults are 80 percent of the X axis profile read on PRPAA1087 (V1.2.2), 400 mm/s and 2250 mm/s2.
    self.default_speed: float = 320.0
    self.default_acceleration: float = 1800.0
    # Speed (mm/s) `probe_home_flag` seeks at when none is given: what it was run with on PRPAA1087.
    self.default_probe_speed: float = 100.0

  # ----------------------------------------
  # Movement
  # ----------------------------------------

  def update_location_by_reference_point(self, x: float) -> None:
    """Record where the arm is on the resource that models it.

    The gantry's x refers to the channels' axis, which sits a fixed distance from the arm's left edge,
    while a resource is located by its left front bottom corner, so the two differ by that distance.
    Does nothing when the driver was given no deck to model into.

    Args:
      x: where the reference point is now, in mm on the deck.
    """
    if self.resource is None or self.resource.location is None:
      return
    self.resource.location = Coordinate(
      x - self.configuration.reference_point_from_left,
      self.resource.location.y,
      self.resource.location.z,
    )

  async def request_axis_offset(self) -> float:
    """Request how far the reported X is from the X axis's own position.

    Returns:
      Reported x less the axis position, in mm.

    Raises:
      RuntimeError: If the channels report no position.
    """
    x = await self.request_position()
    if x is None:
      raise RuntimeError("the channels reported no positions")
    return x - await self.request_commanded_position()

  # -- requests --------------------------------------------------------------

  async def request_position(self) -> Optional[float]:
    """Request where along X the arm is.

    Read from `GetPositions`, whose entries all carry the gantry's X, and recorded on the resource that
    models the arm.

    Returns:
      The position in mm, or None when no channel reported one.
    """
    response = await self._driver.send_command(PrepCmd.PrepGetPositions())
    if not response or not response.positions:
      return None
    x = float(response.positions[0].position_x)
    self.update_location_by_reference_point(x)
    return x

  async def _record_where_it_stopped(self) -> None:
    """Read where the arm and the channels on it came to rest, and record it.

    For a move's `finally`. A move that failed part way left the arm somewhere no target describes.
    Its own failure is logged and swallowed: it must not replace the move's exception, which is the
    one that says what went wrong.
    """
    try:
      pipettes = self._driver.pipettes
      if pipettes is not None:
        await pipettes.request_locations()  # the arm's X and each channel's Y and Z, in one read
      else:
        await self.request_position()
    except Exception:
      logger.warning("could not read where the X-arm stopped; its model is stale")

  async def request_commanded_position(self) -> float:
    """Request the X axis position in its own frame.

    Returns:
      The commanded position in mm.
    """
    response = await self._driver.send_command(PrepCmd.PrepXAxisGetCommandedPosition())
    return float(response.value)

  async def request_speed(self) -> float:
    """Request the speed the X axis drives at (`XAxis.GetVelocity`).

    Returns:
      The speed in mm/s.
    """
    return float((await self._driver.send_command(PrepCmd.PrepXAxisGetVelocity())).value)

  # manage speed and acceleration -------------------------------------------------

  async def request_acceleration(self) -> float:
    """Request the X axis acceleration.

    Returns:
      The acceleration in mm/s2.
    """
    return float((await self._driver.send_command(PrepCmd.PrepXAxisGetAcceleration())).value)

  async def _unchecked_fw_set_speed(self, speed: float) -> None:
    """Send `XAxis.SetVelocity` without checks.

    Args:
      speed: speed in mm/s.
    """
    await self._driver.send_command(PrepCmd.PrepXAxisSetVelocity(value=speed))

  async def _unchecked_fw_set_acceleration(self, acceleration: float) -> None:
    """Send `XAxis.SetAcceleration` without checks.

    Args:
      acceleration: acceleration in mm/s2.
    """
    await self._driver.send_command(PrepCmd.PrepXAxisSetAcceleration(value=acceleration))

  @asynccontextmanager
  async def _temporary_x_axis_profile(
    self, speed: Optional[float] = None, acceleration: Optional[float] = None
  ) -> AsyncIterator[None]:
    """Set the X axis speed and acceleration for the enclosed block, then restore each.

    A value that cannot be restored is logged, not raised.

    Args:
      speed: speed in mm/s, or None to leave it.
      acceleration: acceleration in mm/s2, or None to leave it.
    """
    speed_before = None if speed is None else await self.request_speed()
    acceleration_before = None if acceleration is None else await self.request_acceleration()
    try:
      if speed is not None:
        await self._unchecked_fw_set_speed(speed)
      if acceleration is not None:
        await self._unchecked_fw_set_acceleration(acceleration)
      yield
    finally:
      if speed_before is not None:
        try:
          await self._unchecked_fw_set_speed(speed_before)
        except Exception:
          logger.warning("could not restore the X axis speed to %s", speed_before)
      if acceleration_before is not None:
        try:
          await self._unchecked_fw_set_acceleration(acceleration_before)
        except Exception:
          logger.warning("could not restore the X axis acceleration to %s", acceleration_before)

  # -- x motion --------------------------------------------------------------

  async def _unchecked_fw_move_absolute(self, position: float) -> None:
    """Send `XAxis.MoveAbsolute` (cmd=3). Nothing is guarded and nothing is recorded.

    Args:
      position: where to send the axis, in mm in the axis's own frame.
    """
    await self._driver.send_command(PrepCmd.PrepXAxisMoveAbsolute(position=position))

  async def move_to_x_position(
    self,
    x: float,
    speed: Optional[float] = None,
    acceleration: Optional[float] = None,
    minimum_traverse_height_start: Optional[float] = None,
    z_speed: Optional[float] = None,
    z_acceleration: Optional[float] = None,
  ) -> None:
    """Move the arm along X with `XAxis.MoveAbsolute`.

    Args:
      x: target x in mm.
      speed: speed in mm/s. Defaults to `default_speed`.
      acceleration: acceleration in mm/s2. Defaults to `default_acceleration`.
      minimum_traverse_height_start: raise every channel standing below this height, in mm, before
        the arm travels. The pipettes' `default_minimum_traverse_height` when None; 0 raises nothing,
        so the channels travel at the height they stand at.

    Raises:
      ValueError: If `x`, `speed` or `acceleration` is out of range.
      RuntimeError: If there are no pipettes, or the channels report no position.
    """
    speed = self.default_speed if speed is None else speed
    acceleration = self.default_acceleration if acceleration is None else acceleration
    low, high = self.configuration.speed_range
    if not low < speed <= high:
      raise ValueError(f"speed must be above {low} and at most {high} mm/s, is {speed}")
    if acceleration <= 0:
      raise ValueError(f"acceleration must be above 0 mm/s2, is {acceleration}")
    pipettes = self._driver.pipettes
    if pipettes is None:
      raise RuntimeError("no pipettes to read the channels from; have you called `prep.setup()`?")
    for channel in pipettes.configuration.channels:
      if channel.x_range is not None and not channel.x_range[0] <= x <= channel.x_range[1]:
        raise ValueError(
          f"x={x} outside the channels' range [{channel.x_range[0]:.1f}, {channel.x_range[1]:.1f}]"
        )
    traverse = (
      pipettes.default_minimum_traverse_height
      if minimum_traverse_height_start is None
      else minimum_traverse_height_start
    )
    below = {}
    for index, at in enumerate(await pipettes.request_locations()):
      window = (
        pipettes.configuration.channels[index].z_range
        if index < len(pipettes.configuration.channels)
        else None
      )
      # The device reports each channel's Z window for whatever is attached to it, so a channel
      # carrying something travels as high as it goes rather than to the traverse height.
      ceiling = traverse if window is None else min(traverse, window[1])
      if at.z < ceiling:
        below[index] = ceiling
    if below:
      await pipettes.move_tool_bottom_to_z_positions(
        below, speed=z_speed, acceleration=z_acceleration
      )
    offset = await self.request_axis_offset()
    try:
      async with self._temporary_x_axis_profile(speed=speed, acceleration=acceleration):
        await self._unchecked_fw_move_absolute(x - offset)
        self.update_location_by_reference_point(x)
    finally:
      await self._record_where_it_stopped()

  async def _unchecked_fw_seek_to_home_flag(
    self, distance: float, travel_limits_enable: bool, trip_sense: int
  ) -> float:
    """Send `XAxis.SeekToHomeFlag` (cmd=5). Nothing is guarded and nothing is recorded.

    Args:
      distance: how far to seek at most, in mm, relative to where the axis is.
      travel_limits_enable: whether the axis keeps to its travel limits.
      trip_sense: the firmware's TripSense value.

    Returns:
      Where the sensor tripped, in mm in the axis's own frame.
    """
    response = await self._driver.send_command(
      PrepCmd.PrepXAxisSeekToHomeFlag(
        distance=distance, travel_limits_enable=travel_limits_enable, trip_sense=int(trip_sense)
      )
    )
    return float(response.value)

  async def probe_home_flag(
    self,
    distance: float,
    trip_sense: "XArm.TripSense" = TripSense.SENSOR_0,
    speed: Optional[float] = None,
    travel_limits_enable: bool = True,
  ) -> float:
    """Move the arm along X until its home flag sensor trips.

    Args:
      distance: maximum distance in mm, relative to the arm; negative is left.
      trip_sense: sensor state to stop at.
      speed: speed in mm/s. Defaults to `default_probe_speed`.
      travel_limits_enable: whether the axis keeps to its travel limits.

    Returns:
      Trip position in mm.

    Raises:
      ValueError: If `distance` is 0, the seek could leave the X range, or `speed` is out of range.
      RuntimeError: If there are no pipettes, or the channels report no position.
    """
    speed = self.default_probe_speed if speed is None else speed
    if distance == 0:
      raise ValueError("distance must not be 0")
    low, high = self.configuration.speed_range
    if not low < speed <= high:
      raise ValueError(f"speed must be above {low} and at most {high} mm/s, is {speed}")
    pipettes = self._driver.pipettes
    if pipettes is None:
      raise RuntimeError("no pipettes to read the channels from; have you called `prep.setup()`?")
    here = await self.request_position()
    if here is None:
      raise RuntimeError("the channels reported no positions")
    end = here + distance
    for channel in pipettes.configuration.channels:
      if channel.x_range is not None and not channel.x_range[0] <= end <= channel.x_range[1]:
        raise ValueError(
          f"the seek could end at x={end}, outside the channels' range "
          f"[{channel.x_range[0]:.1f}, {channel.x_range[1]:.1f}]"
        )
    offset = await self.request_axis_offset()
    try:
      async with self._temporary_x_axis_profile(speed=speed):
        tripped = await self._unchecked_fw_seek_to_home_flag(
          distance, travel_limits_enable, trip_sense
        )
    finally:
      await self._record_where_it_stopped()
    return tripped + offset
