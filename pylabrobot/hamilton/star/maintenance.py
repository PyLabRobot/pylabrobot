"""Daily tightness and cLLD checks for STAR 1000 µL pipetting channels."""

import math
from asyncio import sleep
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Dict, List, Literal, Optional, Sequence

from pylabrobot.hamilton.star.driver.features.pipettes import PipetteChannelParameters
from pylabrobot.hamilton.star.driver.master import STARDriver
from pylabrobot.resources import Coordinate, Resource, TipSpot
from pylabrobot.resources.hamilton.star_decks import HamiltonSTARDeck


def get_default_verification_pad_position(deck: Optional[Resource]) -> Coordinate:
  """Return the VENUS rear verification-pad surface for a STAR or STARlet deck, in mm."""
  if not isinstance(deck, HamiltonSTARDeck) or deck.num_tracks not in (30, 54):
    raise ValueError("Supply verification_position for a deck without a STAR or STARlet default")
  return Coordinate(1338.9 if deck.num_tracks == 54 else 798.9, 429.6, 220)


@dataclass(frozen=True)
class PressureCheck:
  """Pressure magnitudes in Pa, measured 20 seconds apart after 2 seconds settling."""

  channel: int
  polarity: Literal["positive", "negative"]
  initial_pa: int
  final_pa: int

  @property
  def passed(self) -> bool:
    """Require at least 3500 Pa initially and no more than 400 Pa pressure loss."""
    return self.initial_pa >= 3500 and self.initial_pa - self.final_pa <= 400


@dataclass(frozen=True)
class CLLDCheck:
  """A channel must detect the verification surface and must not detect in air."""

  channel: int
  detected_in_air: bool
  detected_on_pad: bool

  @property
  def passed(self) -> bool:
    """Whether both capacitive detection conditions were met."""
    return not self.detected_in_air and self.detected_on_pad


@dataclass
class DailyMaintenanceReport:
  """Measurements, saved settings, and errors for the selected zero-based channels."""

  state: Literal["not_started", "running", "passed", "failed", "aborted"] = "not_started"
  channels: List[int] = field(default_factory=list)
  pressure: List[PressureCheck] = field(default_factory=list)
  clld: List[CLLDCheck] = field(default_factory=list)
  original_parameters: Dict[int, PipetteChannelParameters] = field(default_factory=dict)
  cleanup_errors: List[str] = field(default_factory=list)
  error: Optional[str] = None


class DailyMaintenance:
  """Run pressure and cLLD checks with closed, 51.9 mm-overhang maintenance needles.

  Args:
    driver: A connected STAR driver with an initialized instrument, exclusively owned here.
    needles: One to eight occupied teaching-needle spots, in rear-to-front order. Needles are
      reused in batches when the instrument has more channels than needles.
    verification_position: Conducting surface position in deck coordinates, in mm. Defaults
      to the VENUS rear verification-pad position for the driver's STAR or STARlet deck.
    on_update: Optional synchronous callback to persist the report before parameter changes
      and after each measurement. It must not operate the instrument.
    traverse_height: Minimum needle-tip clearance in mm; must be at least 245 mm.
    channels: Ascending zero-based channel numbers; all installed channels when omitted.

  On a command error, needles are not automatically returned. Parameters are restored on a
  best-effort basis and any restoration failure aborts the procedure. Resolve the fault before
  returning mounted needles or reinitializing the instrument.
  """

  _needle_overhang = 51.9

  def __init__(
    self,
    driver: STARDriver,
    needles: Sequence[TipSpot],
    verification_position: Optional[Coordinate] = None,
    on_update: Optional[Callable[[DailyMaintenanceReport], None]] = None,
    traverse_height: float = 245.0,
    channels: Optional[Sequence[int]] = None,
  ) -> None:
    """Bind the initialized driver, selected channels, calibrated tools, and reporting callback."""
    if driver.pipettes is None:
      raise RuntimeError("Set up a STAR driver with pipetting channels before maintenance")
    self.driver = driver
    self.pipettes = driver.pipettes
    self.needles = list(needles)
    self.verification_position = (
      get_default_verification_pad_position(driver.deck)
      if verification_position is None
      else verification_position
    )
    self.report = DailyMaintenanceReport()
    self.on_update = on_update
    self._traverse_height = traverse_height
    self.channels = list(range(driver.num_channels)) if channels is None else list(channels)

  def _notify_report_updated(self) -> None:
    """Pass the current report to the caller's progress callback, when supplied."""
    if self.on_update is not None:
      self.on_update(self.report)

  def _validate_configuration(self) -> None:
    """Reject unsupported devices or invalid tools before changing any parameters."""
    config = self.driver.configuration
    if config is None or self.driver.deck is None:
      raise RuntimeError("Connect the driver with a deck before running maintenance")
    if (
      not config.pip_type_1000ul
      or config.xl_channels_installed
      or not 1 <= self.driver.num_channels <= 16
    ):
      raise ValueError("These checks require 1 to 16 standard 1000 uL pipetting channels")
    if (
      not self.channels
      or any(type(channel) is not int for channel in self.channels)
      or any(not 0 <= channel < self.driver.num_channels for channel in self.channels)
      or self.channels != sorted(set(self.channels))
    ):
      raise ValueError("Select distinct installed channels in ascending zero-based order")
    if config.instrument_size_slots not in (30, 54) or config.right_arm is not None:
      raise ValueError("These checks support single-arm STAR and STARlet instruments")
    if (
      isinstance(self.driver.deck, HamiltonSTARDeck)
      and self.driver.deck.num_tracks != config.instrument_size_slots
    ):
      raise ValueError("The selected deck size does not match the instrument")
    if not 1 <= len(self.needles) <= 8 or len({id(s) for s in self.needles}) != len(self.needles):
      raise ValueError("Supply one to eight distinct maintenance needle spots")
    for spot in self.needles:
      if not spot.is_in_subtree_of(self.driver.deck):
        raise ValueError("Maintenance needle spots must belong to the driver's deck")
      if not spot.tracks_tips:
        raise ValueError("Enable tip tracking for the maintenance needle rack")
      tip = spot.tip_for_pickup()
      if tip.model != "hamilton_teaching_needle_300uL":
        raise ValueError("Use closed Hamilton teaching needles, not disposable tips")
    if not math.isfinite(self._traverse_height) or self._traverse_height < 245:
      raise ValueError("Minimum needle-tip traverse height must be at least 245 mm")
    x, y, z = self.verification_position
    if not all(math.isfinite(v) for v in (x, y, z)):
      raise ValueError("Verification coordinates must be finite")
    x_range = self.pipettes.arm.configuration.x_range
    if x_range is None:
      raise RuntimeError("The arm's X travel range is unknown; call driver.setup() first")
    for name, value, (low, high) in (
      ("X", x, x_range),
      ("Y", y, (config.left_arm_min_y_position, config.pip_maximal_y_position)),
      (
        "Traverse stop-disc Z",
        self._traverse_height + self._needle_overhang + 0.5,
        self.pipettes.configuration.z_range,
      ),
    ):
      if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high} mm, is {value}")
    if not 100 <= z < self._traverse_height - 1:
      raise ValueError("Verification surface must be below the selected traverse height")

  async def check_readiness(self) -> None:
    """Read readiness without motion; reject uninitialized, occupied, or low hardware."""
    self._validate_configuration()
    if not await self.driver.request_initialization_status():
      raise RuntimeError("Instrument is not initialized; call driver.setup() before maintenance")
    if any(await self.pipettes.sense_tip_presence()):
      raise RuntimeError("All channels must be bare")
    if any(z < self._traverse_height for z in await self.pipettes.request_stop_disc_z_positions()):
      raise RuntimeError("Raise all channels before maintenance")
    for head in (self.driver.head96, self.driver.head384):
      if head is not None and await head.request_z_position() < head.configuration.z_range[1] - 0.5:
        raise RuntimeError(f"Raise {head.configuration.module} before maintenance")
    if self.driver.autoload is not None and not await self.driver.autoload.wheel_is_at_safe_z():
      raise RuntimeError("Autoload wheel is below safe Z")
    if self.driver.front_cover is not None:
      if await self.driver.front_cover.request_state() != "closed":
        raise RuntimeError("Close the front cover")

  @asynccontextmanager
  async def _preserve_channel_parameters(self, channels: List[int]) -> AsyncIterator[None]:
    """Snapshot all modified drive parameters before changing any; restore each channel."""
    pipettes = self.pipettes
    saved = {channel: await pipettes.request_channel_parameters(channel) for channel in channels}
    self.report.original_parameters.update(saved)
    self._notify_report_updated()
    try:
      yield
    except BaseException as error:
      self.report.error = f"{type(error).__name__}: {error}"
      raise
    finally:
      errors = []
      for channel, parameters in saved.items():
        try:
          await pipettes.set_lld_measurement(channel, enabled=False)
        except Exception as error:
          errors.append(f"Channel {channel} disabling LLD: {error}")
        try:
          await pipettes.restore_channel_parameters(channel, parameters)
        except Exception as error:
          errors.append(f"Channel {channel} restoring parameters: {error}")
        try:
          if await pipettes.request_channel_parameters(channel) != parameters:
            raise RuntimeError("Channel parameters did not restore")
        except Exception as error:
          errors.append(f"Channel {channel} readback: {error}")
      if errors:
        self.report.cleanup_errors.extend(errors)
        raise RuntimeError("Could not restore maintenance parameters: " + "; ".join(errors))

  async def _check_pressure_retention(self, channels: List[int], positive: bool) -> None:
    """Build pressure, then measure its loss over a 20 second interval."""
    pipettes = self.pipettes
    for channel in channels:
      await pipettes.adjust_pressure_sensor_for_measurement(
        channel, "positive" if positive else "negative"
      )
      speed = await pipettes.request_dispensing_drive_speed_increments(channel)
      acceleration = await pipettes.request_dispensing_drive_acceleration_increments(channel)
      current_limit = await pipettes.request_dispensing_drive_current_limit(channel)
      await pipettes.dispensing_drive_move(
        channel,
        distance=-1250 if positive else 800,
        speed=speed,
        acceleration=acceleration,
        current_limit=current_limit,
      )
      for _ in range(30):
        if abs(await pipettes.request_channel_pressure(channel)) >= 4000:
          break
        await pipettes.dispensing_drive_move(
          channel,
          distance=-50 if positive else 20,
          speed=speed,
          acceleration=acceleration,
          current_limit=current_limit,
        )
    await sleep(2)
    initial = [abs(await pipettes.request_channel_pressure(channel)) for channel in channels]
    await sleep(20)
    final = [abs(await pipettes.request_channel_pressure(channel)) for channel in channels]
    self.report.pressure.extend(
      PressureCheck(channel, "positive" if positive else "negative", start, end)
      for channel, start, end in zip(channels, initial, final)
    )
    self._notify_report_updated()

  async def _check_channel_tightness(self, channels: List[int]) -> None:
    """Check positive and negative pressure retention, reloading needles between polarities."""
    async with self._preserve_channel_parameters(channels):
      for positive, piston_steps in ((True, 20000), (False, 5760)):
        for channel in channels:
          await self.pipettes.set_tip_pickup_piston_position(
            channel, piston_steps * self.pipettes.configuration.dispensing_drive_uL_per_increment
          )
        await self.pipettes.ensure_traverse_height(self._traverse_height, channels)
        await self.pipettes.pick_up_tips(
          self.needles[: len(channels)],
          use_channels=channels,
          minimum_traverse_height_start=self._traverse_height,
        )
        await self.pipettes.ensure_traverse_height(self._traverse_height, channels)
        await self._check_pressure_retention(channels, positive)
        await self.pipettes.ensure_traverse_height(self._traverse_height, channels)
        await self.pipettes.return_tips(
          use_channels=channels,
          minimum_traverse_height_start=self._traverse_height,
          minimum_traverse_height_end=self._traverse_height,
        )
      await self.pipettes.ensure_traverse_height(self._traverse_height, channels)

  async def _probe_capacitive_contact(self, channel: int, surface: bool) -> bool:
    """Check the detection latch in air or across the verification pad's surface."""
    c = self.pipettes.configuration
    center = self.verification_position.z if surface else self._traverse_height
    search_half_width = 1.0 if surface else 0.0
    search_speed = (
      100 * c.z_drive_mm_per_increment
      if surface
      else await self.pipettes.request_lld_search_speed(channel)
    )
    acceleration = await self.pipettes.request_z_acceleration(channel)
    detection_edge = await self.pipettes.request_clld_detection_edge(channel)
    detection_drop = await self.pipettes.request_clld_detection_drop(channel)
    await self.pipettes.probe_z_using_clld(
      channel,
      search_start_position=center + search_half_width,
      search_end_position=center - search_half_width,
      approach_speed=4500 * c.z_drive_mm_per_increment if surface else None,
      search_speed=search_speed,
      acceleration=acceleration,
      detection_edge=detection_edge,
      detection_drop=detection_drop,
      post_detection_trajectory=1,
      post_detection_distance=20 * c.z_drive_mm_per_increment if surface else 0,
      move_to_safe_z_position_after=False,
    )
    return await self.pipettes.request_clld_detection(channel)

  async def _check_capacitive_detection(self, channels: List[int]) -> None:
    """Check each channel above and on the conducting verification surface."""
    pipettes = self.pipettes
    async with self._preserve_channel_parameters(channels):
      await pipettes.ensure_traverse_height(self._traverse_height, channels)
      await pipettes.pick_up_tips(
        self.needles[: len(channels)],
        use_channels=channels,
        minimum_traverse_height_start=self._traverse_height,
      )
      for channel in channels:
        await pipettes.set_lld_measurement(channel, enabled=True, mode="capacitive")
      for channel in channels:
        await pipettes.ensure_traverse_height(self._traverse_height, channels)
        await pipettes.move_to_xy_positions(
          self.verification_position.x,
          {channel: self.verification_position.y},
          make_space=True,
          minimum_traverse_height_start=self._traverse_height,
        )
        air = await self._probe_capacitive_contact(channel, surface=False)
        if air:
          raise RuntimeError(f"Channel {channel + 1} detected in air; surface search aborted")
        pad = await self._probe_capacitive_contact(channel, surface=True)
        self.report.clld.append(CLLDCheck(channel, air, pad))
        self._notify_report_updated()
      for channel in channels:
        await pipettes.set_lld_measurement(channel, enabled=False)
      await pipettes.ensure_traverse_height(self._traverse_height, channels)
      await pipettes.return_tips(
        use_channels=channels,
        minimum_traverse_height_start=self._traverse_height,
        minimum_traverse_height_end=self._traverse_height,
      )
      await pipettes.ensure_traverse_height(self._traverse_height, channels)

  async def _initialize_empty_dispensing_drives(self) -> None:
    """Home the selected channels' empty dispensing drives."""
    for channel in self.channels:
      await self.pipettes.initialize_dispensing_drive(channel)

  async def run(self) -> DailyMaintenanceReport:
    """Run selected independent channels; raise on operational errors and retain partial results.

    The operator must first inspect the deck, empty waste, load the maintenance needles and
    close the cover. A failed measurement returns a failed report; communication, motion and
    cleanup errors raise and leave the report aborted. The firmware maintenance record is
    never written. Do not run any other commands concurrently with this method.
    """
    if self.report.state != "not_started":
      raise RuntimeError("Create a new DailyMaintenance instance for each run")
    self.report.channels = self.channels.copy()
    try:
      await self.check_readiness()
      self.report.state = "running"
      self._notify_report_updated()
      if self.driver.front_cover is not None:
        await self.driver.front_cover.enable_control()
      channels = self.channels
      batches = [
        channels[i : i + len(self.needles)] for i in range(0, len(channels), len(self.needles))
      ]
      for batch in batches:
        await self._check_channel_tightness(batch)
      await self._initialize_empty_dispensing_drives()
      for batch in batches:
        await self._check_capacitive_detection(batch)
      await self._initialize_empty_dispensing_drives()
      self.report.state = (
        "passed"
        if all(result.passed for result in self.report.pressure)
        and all(result.passed for result in self.report.clld)
        else "failed"
      )
    except BaseException as error:
      self.report.state = "aborted"
      detail = f"{type(error).__name__}: {error}"
      if self.report.error is None:
        self.report.error = detail
      elif self.report.error != detail:
        self.report.error += "; " + detail
      raise
    finally:
      self._notify_report_updated()
    return self.report
