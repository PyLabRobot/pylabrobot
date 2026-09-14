"""The X-arm: the gantry the pipetting channels ride."""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

from .. import prep_commands as PrepCmd

if TYPE_CHECKING:
  from ..master import PrepDriver

logger = logging.getLogger(__name__)


@dataclass
class XArmConfiguration:
  """How the arm is modelled.

  Measured on the arm rather than read off the device: the Prep reports no size for it.
  """

  size_x: float = 66.0
  """How wide the arm is, in mm."""
  size_y: float = 532.0
  """How deep the arm is, in mm. Its back is flush with the back of the device."""
  size_z: float = 69.0
  """How tall the arm is, in mm."""
  reference_point_from_left: float = -80.0
  """Where the gantry's x refers to, in mm from the arm's left edge. The channels hang to the arm's left,
  so their axis - which is what the Prep reports - sits about 80 mm left of that edge."""
  model: str = "hamilton_prep_x_arm"
  """Which 3D model draws it. None ships yet, so the viewer draws a box of this size."""
  appearance: Dict[str, Any] = field(
    default_factory=lambda: {"color": 0xC0C4C8, "metalness": 0.6, "roughness": 0.35}
  )
  """How the viewer draws the arm: silver, and metallic."""
  speed_per_scale_percent: float = 6.0
  """How fast the gantry may drive X per percent of MLPrep's X speed scale, in mm/s. Measured on
  PRPAA1087 (V1.2.2): the X axis profile velocity is this times the scale, up to `max_speed`."""
  max_speed: float = 400.0
  """The fastest the gantry drives X whatever the scale, in mm/s. Reached from 67 percent up."""

  def speed_to_scale_percent(self, speed: float) -> int:
    """The X speed scale that drives the gantry at `speed`, rounded to the nearest percent.

    Args:
      speed: in mm/s, from `speed_per_scale_percent` to `max_speed`.

    Raises:
      ValueError: If `speed` is outside that range.
    """
    if not self.speed_per_scale_percent <= speed <= self.max_speed:
      raise ValueError(
        f"x speed must be between {self.speed_per_scale_percent} and {self.max_speed} mm/s, is {speed}"
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

  async def move_to_x_position(
    self, x: float, speed: Optional[float] = None, acceleration: Optional[float] = None
  ) -> None:
    """Move the arm along X with the X axis's own move, at a given speed and acceleration.

    `Pipettes.move_to_location` moves through the channel coordinator, whose move overwrites the X
    axis's velocity and acceleration. This sends `XAxis.MoveAbsolute`, which on PRPAA1087 (V1.2.2) moved
    at the velocity and acceleration set just before it. Those are read before the move, set for it,
    and put back afterwards. The coordinator takes no part, so every channel has to be at the traverse
    height already.

    The axis counts in its own frame, which on PRPAA1087 sat 0.193 mm from the X `GetPositions`
    reports. `x` is in the `GetPositions` frame; the difference is read before the move.

    Args:
      x: where to send the gantry's reference point, in mm on the deck.
      speed: how fast, in mm/s. Defaults to `default_speed`.
      acceleration: how hard, in mm/s2. Defaults to `default_acceleration`.

    Raises:
      ValueError: If `x` is outside the channels' X range, `speed` is not above 0 or above
        `configuration.max_speed`, or `acceleration` is not above 0.
      RuntimeError: If there are no pipettes to read, the channels report no position, or a channel
        is below the traverse height.
    """
    speed = self.default_speed if speed is None else speed
    acceleration = self.default_acceleration if acceleration is None else acceleration
    if not 0 < speed <= self.configuration.max_speed:
      raise ValueError(
        f"speed must be above 0 and at most {self.configuration.max_speed} mm/s, is {speed}"
      )
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
    positions = await pipettes.request_locations()
    if not positions:
      raise RuntimeError("the channels reported no positions")
    # The positions a traverse leaves the channels at read a few hundredths of a millimetre under it.
    traverse = pipettes._resolve_traverse_height()
    low = [channel for channel, position in enumerate(positions) if position.z < traverse - 0.1]
    if low:
      raise RuntimeError(
        f"channels {low} are below the traverse height ({traverse} mm); an X axis move does not raise them"
      )

    commanded = (await self._driver.send_command(PrepCmd.PrepXAxisGetCommandedPosition())).value
    axis_x = x - (positions[0].x - commanded)
    velocity_before = (await self._driver.send_command(PrepCmd.PrepXAxisGetVelocity())).value
    acceleration_before = (
      await self._driver.send_command(PrepCmd.PrepXAxisGetAcceleration())
    ).value
    try:
      await self._driver.send_command(PrepCmd.PrepXAxisSetVelocity(value=speed))
      await self._driver.send_command(PrepCmd.PrepXAxisSetAcceleration(value=acceleration))
      await self._unchecked_fw_move_absolute(axis_x)
      # What was asked, recorded as soon as the command answers; the read below replaces it with
      # where the arm actually stopped.
      self.update_location_by_reference_point(x)
    finally:
      try:
        await self._driver.send_command(PrepCmd.PrepXAxisSetVelocity(value=velocity_before))
        await self._driver.send_command(PrepCmd.PrepXAxisSetAcceleration(value=acceleration_before))
      finally:
        try:
          await self.request_position()
        except Exception:
          logger.warning("could not read where the arm stopped; its model is stale")

  async def probe_home_flag(
    self,
    distance: float,
    trip_sense: "XArm.TripSense" = TripSense.SENSOR_0,
    speed: Optional[float] = None,
    travel_limits_enable: bool = True,
  ) -> float:
    """Move the arm along X until its home flag sensor trips, and return where it did.

    Sends `XAxis.SeekToHomeFlag` at `speed`, set as the X axis velocity for the seek and put back
    afterwards. On PRPAA1087 (V1.2.2), seeking 280 mm left from the parked position at 100 mm/s with
    SENSOR_0 tripped at 274.329 mm (axis frame), within 0.2 mm of `GetHomePosition`, and the arm came
    to rest about 6 mm further on. The coordinator takes no part, so every channel has to be at the
    traverse height already.

    Args:
      distance: how far to seek at most, in mm, relative to where the arm is; negative is left.
      trip_sense: when to stop. Defaults to `TripSense.SENSOR_0`.
      speed: how fast to seek, in mm/s. Defaults to `default_probe_speed`.
      travel_limits_enable: whether the axis keeps to its travel limits while seeking.

    Returns:
      Where the sensor tripped, in mm on the deck (the `GetPositions` frame).

    Raises:
      ValueError: If `distance` is 0, the seek could end outside the channels' X range, or `speed`
        is not above 0 or above `configuration.max_speed`.
      RuntimeError: If there are no pipettes to read, the channels report no position, or a channel
        is below the traverse height.
    """
    speed = self.default_probe_speed if speed is None else speed
    if distance == 0:
      raise ValueError("distance must not be 0")
    if not 0 < speed <= self.configuration.max_speed:
      raise ValueError(
        f"speed must be above 0 and at most {self.configuration.max_speed} mm/s, is {speed}"
      )
    pipettes = self._driver.pipettes
    if pipettes is None:
      raise RuntimeError("no pipettes to read the channels from; have you called `prep.setup()`?")
    positions = await pipettes.request_locations()
    if not positions:
      raise RuntimeError("the channels reported no positions")
    end = positions[0].x + distance
    for channel in pipettes.configuration.channels:
      if channel.x_range is not None and not channel.x_range[0] <= end <= channel.x_range[1]:
        raise ValueError(
          f"the seek could end at x={end}, outside the channels' range "
          f"[{channel.x_range[0]:.1f}, {channel.x_range[1]:.1f}]"
        )
    # The positions a traverse leaves the channels at read a few hundredths of a millimetre under it.
    traverse = pipettes._resolve_traverse_height()
    low = [channel for channel, position in enumerate(positions) if position.z < traverse - 0.1]
    if low:
      raise RuntimeError(
        f"channels {low} are below the traverse height ({traverse} mm); an X axis seek does not raise them"
      )

    commanded = (await self._driver.send_command(PrepCmd.PrepXAxisGetCommandedPosition())).value
    offset = positions[0].x - commanded
    velocity_before = (await self._driver.send_command(PrepCmd.PrepXAxisGetVelocity())).value
    try:
      await self._driver.send_command(PrepCmd.PrepXAxisSetVelocity(value=speed))
      tripped = await self._unchecked_fw_seek_to_home_flag(
        distance, travel_limits_enable, trip_sense
      )
    finally:
      try:
        await self._driver.send_command(PrepCmd.PrepXAxisSetVelocity(value=velocity_before))
      finally:
        try:
          await self.request_position()
        except Exception:
          logger.warning("could not read where the arm stopped; its model is stale")
    return tripped + offset

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

  async def _unchecked_fw_move_absolute(self, position: float) -> None:
    """Send `XAxis.MoveAbsolute` (cmd=3). Nothing is guarded and nothing is recorded.

    Args:
      position: where to send the axis, in mm in the axis's own frame.
    """
    await self._driver.send_command(PrepCmd.PrepXAxisMoveAbsolute(position=position))

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
