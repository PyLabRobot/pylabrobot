"""The pump: the pressure board (board 10), its air pump and its three pressure controllers.

Each controller regulates one line, bottle, chip or vacuum, from its feedback sensor. Public
methods read, prime the chip on the head (a volume, or until the drop sensor sees drops), dispense
from it into wells and recover its reagent, on a v4 and, the prime and the dispense, on a v3; the
pneumatic clamp can privately start and restore the air pump. Setup tares the bottle and chip
sensors; stop releases the pressure if the pump runs and writes their calibration back.
"""

import asyncio
import functools
import logging
import math
import struct
import time
from typing import (
  TYPE_CHECKING,
  Awaitable,
  Callable,
  Dict,
  List,
  Literal,
  Optional,
  Tuple,
  Union,
  get_args,
)

from pylabrobot.resources import Coordinate, Plate, Well
from pylabrobot.resources.formulatrix.mantis_chips import MantisChip, MantisChipInput

from ...liquid_classes import MantisLiquidClass, mantis_mapping
from ..errors import (
  MantisConfigurationError,
  MantisPressureError,
  MantisPrimeError,
)
from ..framing import PRESSURE_BOARD
from ..oem_config import WASTE_APPROACH_PATH
from ..valve_sequences import (
  LIQUID_CLASS_DELAYS,
  SEQUENCE_VOLUMES,
  VALVE_BANK_PPI_ADDRESS,
  VALVE_SEQUENCES,
  _queue_valve_sequence,
)
from .arm import (
  ARM_MOTORS,
  CHIP_CHANGER_ROUTE,
  MOVE_TIMEOUT,
  PITCH_HEIGHT,
  V3_CHIP_CHANGER_ROUTE,
  Z_MOTOR,
)

if TYPE_CHECKING:
  from ..master import MantisDriver
  from .arm import Arm
  from .plate_clamp import PlateClamp

logger = logging.getLogger(__name__)

PressureController = Literal["bottle", "chip", "vacuum"]
# The pressure controllers by name, with their number on the wire.
PRESSURE_CONTROLLERS: Dict[PressureController, int] = {"bottle": 0, "chip": 1, "vacuum": 2}
# A controller's status, as it reads it.
PRESSURE_CONTROLLER_STATUS_DESCRIPTIONS: Dict[int, str] = {
  0: "off",
  1: "settled",
  2: "unsettled",
}
# The sensors setup tares against atmosphere. No recorded startup tares the vacuum sensor.
TARED_CONTROLLERS: Tuple[PressureController, ...] = ("bottle", "chip")
# The solenoid valve value that vents a line while its sensor is tared, as in every recorded
# startup; also the bottle's while it is let off, and the chip line's at stop.
TARE_SOLENOID_VALUE = 10000
# The tare reads until two readings TUNE_DELAY apart agree within TUNE_TOLERANCE (psi), for at most
# TUNE_TIMEOUT (s): PressureRegulator.config's TuneDelayMs, TuneTolerance, TuneTimeoutMs.
TUNE_DELAY = 0.3
TUNE_TOLERANCE = 0.1
TUNE_TIMEOUT = 5.0
# After the pump stops, a v4 opens the bottle's solenoid and proportional valves to 20000 for 2 s
# to release the reservoir (PressureRegulator.config's ...ReleaseReservoir keys). A v3 does not.
RELEASE_RESERVOIR_CONTROLLER: PressureController = "bottle"
RELEASE_RESERVOIR_VALUE = 20000
RELEASE_RESERVOIR_DELAY = 2.0
# At stop a v4's chip line is vented for RELEASE_RESERVOIR_DELAY; a line that then reads further
# from 0 than this, in psi, is warned about. Both are chosen bounds, not a device's.
VENT_TOLERANCE = 2.0
# Starting the pump holds it this long before anything else: StartAirPump.seq.txt, "ON 2".
START_PUMP_DELAY = 2.0
# Setting a chip's pressures, as the chips' PreparingPressure.seq.txt: each line settles within
# 2000 ms; the bottle is let off ("Bottle 0500 OFF") for 500 ms.
SETTLE_TIMEOUT = 2.0
BOTTLE_EXHAUST_DELAY = 0.5
# A v4 sets the vacuum to 0, then to its target 0.23 to 0.27 s later in every capture that sets it:
# a 0.2 s wait and the link's round trip. A v3 never sends the 0.
RELEASE_VACUUM_DELAY = 0.2
# Before the first pump start after a power-up a v4 queues every valve closed for 10 ms: else the
# pressure pushes air back into the input.
VALVES_CLOSED = 31
VALVES_CLOSED_DURATION = 10
# A count of drops or plays is rounded after adding this, as in every captured prime and recovery.
PUMP_CYCLE_ROUNDING = 0.0005
# A drop-detection prime plays the prime pump at most this often, by chip type: Dispense.config's
# MaximumAutoPrimeCycleforLV. Only a low volume Hi-Fi chip's drop-detection prime is captured.
MAXIMUM_AUTO_PRIME_CYCLES: Dict[str, int] = {"low_volume_hi_fi": 400}
# The drops in a row a drop-detection prime ends on: Default.config's AutoPrime.Value, 5 for every
# chip type; the drop-detection prime in 260811_startup_dispense_0.5_2.5uL_shutdown stopped after 5.
AUTO_PRIME_CONSECUTIVE_DROPS = 5
# The chip types each generation's captures prime: a v4's Hi-Fi chips (260724_prime_slot1_LVHF,
# 270628_HVHF_prime_fixed_300uL), a v3's high volume chip (260601_homethenprime).
PRIMED_CHIP_TYPES: Dict[str, Tuple[str, ...]] = {
  "v4": ("low_volume_hi_fi", "high_volume_hi_fi"),
  "v3": ("high_volume",),
}
# The chip types each generation's captures recover (260728_simple_recover_command,
# 270628_HVHF_recovery); no v3 recovery is captured.
RECOVERED_CHIP_TYPES: Dict[str, Tuple[str, ...]] = {
  "v4": ("low_volume_hi_fi", "high_volume_hi_fi"),
}
# The chip types each generation's captures dispense from: a v4's Hi-Fi chips, a v3's high volume
# chip.
DISPENSED_CHIP_TYPES: Dict[str, Tuple[str, ...]] = {
  "v4": ("low_volume_hi_fi", "high_volume_hi_fi"),
  "v3": ("high_volume",),
}
# What a dispense plays at the waste before the first well, uL, by chip type: the chips'
# Chip.config "Tube.PreDispense.Volume"; as captured on a v4 and a v3.
PRE_DISPENSE_VOLUMES: Dict[str, float] = {
  "low_volume_hi_fi": 0.6,
  "high_volume_hi_fi": 6.0,
  "high_volume": 6.0,
}
# A precise dispense's pre-dispense, uL, by chip type: 1 large and 3 small drops in both captured
# precise dispenses, from a low volume Hi-Fi chip; no other chip type is captured.
PRECISE_PRE_DISPENSE_VOLUMES: Dict[str, float] = {"low_volume_hi_fi": 0.8}
# The large drops a precise dispense plays at the waste before its round of large drops, by chip
# type, as in both captured precise dispenses.
PRECISE_PRE_DISPENSE_LARGE_DROPS: Dict[str, int] = {"low_volume_hi_fi": 2}


def _round_half_away_from_zero(*, value: float) -> int:
  """``value`` rounded to a whole number, a half away from zero."""
  fraction, whole = math.modf(value)
  if abs(fraction) >= 0.5:
    whole += math.copysign(1.0, fraction)
  return int(whole)


def _get_pump_cycle(*, volume: float, large_volume: float, small_volume: float) -> Tuple[int, int]:
  """The large and small drops for ``volume``, as a v4 splits it: the large drops rounded down
  and up, each finished with small drops, the closer total kept (down on a tie). A volume
  that gives no drop gives one small drop. Checked against every captured prime.

  Args:
    volume: uL.
    large_volume: one large drop's volume, uL.
    small_volume: one small drop's volume, uL.
  """

  def split(round_large: Callable[[float], int]) -> Tuple[int, int]:
    large = int(round_large(volume / large_volume + PUMP_CYCLE_ROUNDING))
    remainder = volume - large * large_volume
    if remainder < small_volume / 2:
      return large, 0
    if large_volume - remainder < small_volume / 2:
      return large + 1, 0
    return large, _round_half_away_from_zero(value=remainder / small_volume + PUMP_CYCLE_ROUNDING)

  large, small = min(
    (split(round_large=math.floor), split(round_large=math.ceil)),
    key=lambda drops: abs(volume - drops[0] * large_volume - drops[1] * small_volume),
  )
  if large + small == 0:
    small = 1
  return large, small


def _get_dispense_pump_cycle(
  *, volume: float, large_volume: float, small_volume: float, minimum_small_drops: int
) -> Tuple[int, int]:
  """The large and small drops for a well's ``volume``: none under half a small drop; else up to
  ``minimum_small_drops`` small drops first, the whole ones the volume holds, then the rest as
  ``_get_pump_cycle``, none for a rest under half a small drop. Half a small drop or more rounds up
  to one, as ``_get_pump_cycle`` rounds. Checked against every captured dispense.

  Args:
    volume: uL.
    large_volume: one large drop's volume, uL.
    small_volume: one small drop's volume, uL.
    minimum_small_drops: the small drops taken first, at most.
  """
  if volume / small_volume + PUMP_CYCLE_ROUNDING < 0.5:
    return 0, 0
  first_small = min(minimum_small_drops, math.floor(volume / small_volume + PUMP_CYCLE_ROUNDING))
  rest = volume - first_small * small_volume
  if rest / small_volume + PUMP_CYCLE_ROUNDING < 0.5:
    return 0, first_small
  large, small = _get_pump_cycle(volume=rest, large_volume=large_volume, small_volume=small_volume)
  return large, first_small + small


def _get_drop_sequences(*, chip_type: str) -> Tuple[Tuple[float, str], Tuple[float, str]]:
  """A chip type's small and large drop sequences, each as (volume, name), from
  ``SEQUENCE_VOLUMES``.

  Args:
    chip_type: a key of ``SEQUENCE_VOLUMES``.
  """
  (small_volume, small), (large_volume, large) = sorted(
    (volume, name) for name, volume in SEQUENCE_VOLUMES[chip_type].items() if name != "reverse_pump"
  )
  return (small_volume, small), (large_volume, large)


def _check_cycle_count(*, name: str, value: int) -> None:
  """Raise ``ValueError`` unless ``value`` (the argument ``name``) is a whole number, at least 1."""
  if isinstance(value, bool) or not isinstance(value, int):
    raise ValueError(f"{name} must be a whole number, got {value!r}")
  if value < 1:
    raise ValueError(f"{name} must be at least 1, got {value}")


def _get_reverse_pump_cycle(*, volume: float, reverse_pump_volume: float) -> int:
  """How often the reverse pump plays for ``volume``, as a v4 rounds it, at least once.
  Checked against every captured recovery.

  Args:
    volume: uL.
    reverse_pump_volume: the volume one play moves, uL.
  """
  return max(
    1, _round_half_away_from_zero(value=volume / reverse_pump_volume + PUMP_CYCLE_ROUNDING)
  )


class Pump:
  """The pump and the pressure controllers, built when the pressure board answers."""

  # The unit's PitchHeight (StageTuning.config), where every captured dispense stood.
  _default_dispense_height_above_well: float = PITCH_HEIGHT

  def __init__(self, *, driver: "MantisDriver"):
    """
    Args:
      driver: the driver it sends through.
    """
    self._driver = driver
    # Each tared sensor's calibration as read before its tare, written back at stop.
    self._calibration_before_tare: Dict[PressureController, Tuple[float, float]] = {}
    # The target last set on each line, set again on it each time the pump starts.
    self._target_pressures: Dict[PressureController, float] = {}
    # Whether every valve was queued closed since setup, before the first pump start; the tare at
    # setup clears it and the targets.
    self._valves_closed = False
    # The chip slot whose chip was primed last, while that chip stays on the head.
    self._primed_chip: Optional[int] = None

  @property
  def is_primed(self) -> bool:
    """Whether the chip on the head was primed since it was picked up."""
    arm = self._driver.arm
    current = None if arm is None else arm.current_chip
    return current is not None and self._primed_chip == current

  @property
  def default_dispense_height_above_well(self) -> float:
    """The nozzle's height above the wellplate's highest point when a dispense names none, mm."""
    return self._default_dispense_height_above_well

  @default_dispense_height_above_well.setter
  def default_dispense_height_above_well(self, value: float) -> None:
    if value < 0:
      raise ValueError(f"default_dispense_height_above_well must be at least 0, got {value}")
    self._default_dispense_height_above_well = value

  def _get_controller_index(self, *, controller: PressureController) -> int:
    """The controller's number on the wire.

    Raises:
      ValueError: if ``controller`` is not in ``PRESSURE_CONTROLLERS``.
    """
    if controller not in PRESSURE_CONTROLLERS:
      raise ValueError(
        f"controller must be one of {tuple(PRESSURE_CONTROLLERS)}, got {controller!r}"
      )
    return PRESSURE_CONTROLLERS[controller]

  async def _send_controller_read(self, *, command: int, controller: PressureController) -> bytes:
    """Send a read of one controller and return its reply; the name is checked before sending."""
    index = self._get_controller_index(controller=controller)
    return await self._driver._send_command(
      board=PRESSURE_BOARD, command=command, payload=struct.pack("<h", index), attempts=3
    )

  async def measure_pressure(self, *, controller: PressureController) -> float:
    """The pressure a controller's feedback sensor reads: psi, inHg on the vacuum.

    Args:
      controller: "bottle", "chip" or "vacuum".
    """
    reply = await self._send_controller_read(command=30, controller=controller)
    (pressure,) = struct.unpack_from("<d", reply)
    return float(pressure)

  async def request_feedback_sensor_parameters(
    self, *, controller: PressureController
  ) -> Tuple[float, float]:
    """A controller's feedback sensor calibration, (scale, offset), as the pressure board holds it.

    Args:
      controller: "bottle", "chip" or "vacuum".
    """
    reply = await self._send_controller_read(command=20, controller=controller)
    scale, offset = struct.unpack_from("<dd", reply)
    return float(scale), float(offset)

  async def request_controller_status(self, *, controller: PressureController) -> int:
    """A controller's status; ``PRESSURE_CONTROLLER_STATUS_DESCRIPTIONS`` says what each means.

    Args:
      controller: "bottle", "chip" or "vacuum".
    """
    reply = await self._send_controller_read(command=16, controller=controller)
    (status,) = struct.unpack_from("<H", reply)
    return int(status)

  async def request_controller_enabled(self, *, controller: PressureController) -> bool:
    """Whether a controller regulates its line.

    Args:
      controller: "bottle", "chip" or "vacuum".
    """
    reply = await self._send_controller_read(command=12, controller=controller)
    (enabled,) = struct.unpack_from("<H", reply)
    return bool(enabled)

  async def request_pump_on(self) -> bool:
    """Whether the air pump runs."""
    reply = await self._driver._send_command(board=PRESSURE_BOARD, command=14, attempts=3)
    (on,) = struct.unpack_from("<H", reply)
    return bool(on)

  async def _unchecked_fw_set_pump_on(self, *, on: bool) -> None:
    """Set the air pump (board 10, command 15), sent once, without changing controllers."""
    await self._driver._send_command(
      board=PRESSURE_BOARD, command=15, payload=struct.pack("<H", on), attempts=1
    )

  async def _set_pump_on(self, *, on: bool) -> None:
    """Set the pump only if needed and confirm its control-state readback."""
    if await self.request_pump_on() == on:
      return
    await self._unchecked_fw_set_pump_on(on=on)
    if await self.request_pump_on() != on:
      raise MantisPressureError(f"{self._driver.name}: air pump did not become {on}")

  async def _unchecked_fw_set_controller_enabled(
    self, *, controller: PressureController, enabled: bool
  ) -> None:
    """Enable or disable a controller (board 10, command 13), sent once."""
    index = self._get_controller_index(controller=controller)
    await self._driver._send_command(
      board=PRESSURE_BOARD, command=13, payload=struct.pack("<hH", index, enabled), attempts=1
    )

  async def _unchecked_fw_set_target_pressure(
    self, *, controller: PressureController, pressure: float
  ) -> None:
    """Set a controller's target (board 10, command 11), sent once: psi, inHg on the vacuum."""
    index = self._get_controller_index(controller=controller)
    await self._driver._send_command(
      board=PRESSURE_BOARD, command=11, payload=struct.pack("<hd", index, pressure), attempts=1
    )

  async def _unchecked_fw_set_proportional_valve(
    self, *, controller: PressureController, value: int
  ) -> None:
    """Set a controller's proportional valve (board 10, command 32), sent once."""
    index = self._get_controller_index(controller=controller)
    await self._driver._send_command(
      board=PRESSURE_BOARD, command=32, payload=struct.pack("<hH", index, value), attempts=1
    )

  async def _unchecked_fw_set_solenoid_valve(
    self, *, controller: PressureController, value: int
  ) -> None:
    """Set a controller's solenoid valve (board 10, command 34), sent once."""
    index = self._get_controller_index(controller=controller)
    await self._driver._send_command(
      board=PRESSURE_BOARD, command=34, payload=struct.pack("<hH", index, value), attempts=1
    )

  async def _unchecked_fw_set_feedback_sensor_parameters(
    self, *, controller: PressureController, scale: float, offset: float
  ) -> None:
    """Set a controller's feedback sensor calibration (board 10, command 21), sent once; the
    board keeps it until power-off."""
    index = self._get_controller_index(controller=controller)
    await self._driver._send_command(
      board=PRESSURE_BOARD,
      command=21,
      payload=struct.pack("<hdd", index, scale, offset),
      attempts=1,
    )

  async def _sleep(self, *, seconds: float) -> None:
    """Wait; the simulated pump waits for nothing."""
    await asyncio.sleep(seconds)

  async def _tare_pressure_sensors(self) -> None:
    """Zero the bottle and chip sensors against atmosphere, as every recorded startup does.

    For each sensor: disable its controller, vent its line through the solenoid valve, read until
    two readings agree (``TUNE_*``), lower the offset by the last reading, close the solenoid valve,
    and enable the controller again if it was enabled. ``_stop`` writes back the calibration read
    before the tare.
    """
    # Each setup starts over: the valves close again before the next pump start, with no target.
    self._valves_closed = False
    self._target_pressures = {}
    self._primed_chip = None
    for controller in TARED_CONTROLLERS:
      scale, offset = await self.request_feedback_sensor_parameters(controller=controller)
      enabled = await self.request_controller_enabled(controller=controller)
      await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=False)
      await self._unchecked_fw_set_proportional_valve(controller=controller, value=0)
      await self._unchecked_fw_set_solenoid_valve(controller=controller, value=TARE_SOLENOID_VALUE)
      previous = None
      for _ in range(int(TUNE_TIMEOUT / TUNE_DELAY)):
        reading = await self.measure_pressure(controller=controller)
        await self._sleep(seconds=TUNE_DELAY)
        if previous is not None and abs(reading - previous) <= TUNE_TOLERANCE:
          break
        previous = reading
      else:
        logger.warning(
          "[%s] the %s sensor did not settle during the tare; used the last reading, %.3f psi",
          self._driver.name,
          controller,
          reading,
        )
      self._calibration_before_tare[controller] = (scale, offset)
      await self._unchecked_fw_set_feedback_sensor_parameters(
        controller=controller, scale=scale, offset=offset - reading
      )
      logger.info(
        "[%s] tared the %s sensor: it read %.3f psi vented; offset changed from %.4f to %.4f",
        self._driver.name,
        controller,
        reading,
        offset,
        offset - reading,
      )
      await self._unchecked_fw_set_solenoid_valve(controller=controller, value=0)
      if enabled:
        await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=True)

  async def _release_reservoir(self) -> None:
    """Open a v4's reservoir valves for ``RELEASE_RESERVOIR_DELAY``, then close them; a v3 has
    none."""
    if self._driver.configuration.generation != "v4":
      return
    controller = RELEASE_RESERVOIR_CONTROLLER
    await self._unchecked_fw_set_solenoid_valve(
      controller=controller, value=RELEASE_RESERVOIR_VALUE
    )
    await self._unchecked_fw_set_proportional_valve(
      controller=controller, value=RELEASE_RESERVOIR_VALUE
    )
    await self._sleep(seconds=RELEASE_RESERVOIR_DELAY)
    await self._unchecked_fw_set_solenoid_valve(controller=controller, value=0)
    await self._unchecked_fw_set_proportional_valve(controller=controller, value=0)

  async def _release_pressure(self) -> None:
    """End as every recorded operation does: disable each controller, turn the pump off and release
    the reservoir. Every step is tried; the first failure is raised after all of them.

    Raises:
      MantisPressureError: if the pump still runs after these steps.
    """
    failure = None
    steps: List[Callable[[], Awaitable[None]]] = [
      *(
        functools.partial(
          self._unchecked_fw_set_controller_enabled, controller=controller, enabled=False
        )
        for controller in PRESSURE_CONTROLLERS
      ),
      functools.partial(self._unchecked_fw_set_pump_on, on=False),
      self._release_reservoir,
    ]
    for step in steps:
      try:
        await step()
      except Exception as error:
        logger.warning("[%s] a pressure release step failed", self._driver.name, exc_info=True)
        failure = failure or error
    if failure is not None:
      raise failure
    if await self.request_pump_on():
      raise MantisPressureError(f"{self._driver.name}: the air pump still runs after stop")

  async def _vent_chip_line(self) -> None:
    """Vent a v4's chip line, its pressure kept after a prime or a recovery, as the tare vents it:
    its controller off, then ``TARE_SOLENOID_VALUE`` for ``RELEASE_RESERVOIR_DELAY``. Then warn if
    the chip or the bottle line reads further from 0 than ``VENT_TOLERANCE``. A v3 is left as is.
    """
    if self._driver.configuration.generation != "v4":
      return
    await self._unchecked_fw_set_controller_enabled(controller="chip", enabled=False)
    await self._unchecked_fw_set_proportional_valve(controller="chip", value=0)
    await self._unchecked_fw_set_solenoid_valve(controller="chip", value=TARE_SOLENOID_VALUE)
    await self._sleep(seconds=RELEASE_RESERVOIR_DELAY)
    await self._unchecked_fw_set_solenoid_valve(controller="chip", value=0)
    for controller in TARED_CONTROLLERS:
      pressure = await self.measure_pressure(controller=controller)
      if abs(pressure) > VENT_TOLERANCE:
        logger.warning(
          "[%s] the %s line still reads %.2f psi after the vent",
          self._driver.name,
          controller,
          pressure,
        )

  async def _stop(self) -> None:
    """Release the pressure if the pump runs, vent the chip line after the bottle unless a chip is
    on the head (its valves stay held shut), then write back each tared sensor's calibration as
    read before its tare. Each is tried; the first failure is raised after them.

    Raises:
      MantisPressureError: if the pump still runs after the release.
    """
    failure = None
    try:
      if await self.request_pump_on():
        await self._release_pressure()
    except Exception as error:
      failure = error
    arm = self._driver.arm
    try:
      if arm is not None and arm.current_chip is not None:
        logger.warning("[%s] a chip is on the head: the chip line is not vented", self._driver.name)
      else:
        await self._vent_chip_line()
    except Exception as error:
      logger.warning("[%s] the chip line was not vented", self._driver.name, exc_info=True)
      failure = failure or error
    for controller, (scale, offset) in list(self._calibration_before_tare.items()):
      try:
        await self._unchecked_fw_set_feedback_sensor_parameters(
          controller=controller, scale=scale, offset=offset
        )
      except Exception as error:
        logger.warning(
          "[%s] the %s sensor's calibration was not written back", self._driver.name, controller
        )
        failure = failure or error
        continue
      del self._calibration_before_tare[controller]
      logger.info(
        "[%s] wrote back the %s sensor's offset %.4f", self._driver.name, controller, offset
      )
    if failure is not None:
      raise failure

  async def _request_calibration(self) -> Dict[PressureController, Tuple[float, float]]:
    """Every controller's feedback sensor calibration, by controller, read at setup."""
    return {
      controller: await self.request_feedback_sensor_parameters(controller=controller)
      for controller in PRESSURE_CONTROLLERS
    }

  async def _start_pump(self, *, pump_on: bool = False) -> None:
    """Start the pump for an operation, as captured: on a v4 every valve queued closed first, the
    first time since setup (a v3 never does); the pump on; each line enabled, with the target last
    set on it, if any; then ``START_PUMP_DELAY``. The sequencer must run.

    Args:
      pump_on: the pump runs already, switched on by a pneumatic clamp's lock as a v3's dispense
        does: it is not switched on again.
    """
    if not self._valves_closed and self._driver.configuration.generation == "v4":
      await self._driver._queue_ppi_write(
        duration=VALVES_CLOSED_DURATION,
        start_address=VALVE_BANK_PPI_ADDRESS,
        values=[VALVES_CLOSED],
      )
      self._valves_closed = True
    if not pump_on:
      await self._unchecked_fw_set_pump_on(on=True)
    for controller in PRESSURE_CONTROLLERS:
      await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=True)
      if controller in self._target_pressures:
        await self._unchecked_fw_set_target_pressure(
          controller=controller, pressure=self._target_pressures[controller]
        )
    await self._sleep(seconds=START_PUMP_DELAY)

  async def _set_pressures(self, *, chip_pressure: float, vacuum: float) -> None:
    """Set a chip's pressures, as the chips' PreparingPressure.seq.txt: the vacuum, through 0 on a
    v4, settled; the bottle let off; the chip line, settled.

    Args:
      chip_pressure: psi.
      vacuum: inHg.

    Raises:
      MantisPressureError: if a line turns off, or does not settle within ``SETTLE_TIMEOUT``.
    """

    async def settle(controller: PressureController, target: float) -> None:
      deadline = time.monotonic() + SETTLE_TIMEOUT
      while True:
        status = await self.request_controller_status(controller=controller)
        pressure = await self.measure_pressure(controller=controller)
        description = PRESSURE_CONTROLLER_STATUS_DESCRIPTIONS.get(status)
        if description == "settled":
          return
        if description == "off" or time.monotonic() >= deadline:
          raise MantisPressureError(
            f"{self._driver.name}: the {controller} line did not settle at {target} within "
            f"{SETTLE_TIMEOUT} s: it reads {pressure:.2f}, {description}"
          )

    await self._unchecked_fw_set_controller_enabled(controller="vacuum", enabled=True)
    if self._driver.configuration.generation == "v4":
      await self._unchecked_fw_set_target_pressure(controller="vacuum", pressure=0.0)
      await self._sleep(seconds=RELEASE_VACUUM_DELAY)
    await self._unchecked_fw_set_target_pressure(controller="vacuum", pressure=vacuum)
    self._target_pressures["vacuum"] = vacuum
    await settle(controller="vacuum", target=vacuum)
    await self._unchecked_fw_set_controller_enabled(controller="bottle", enabled=False)
    await self._unchecked_fw_set_proportional_valve(controller="bottle", value=0)
    await self._unchecked_fw_set_solenoid_valve(controller="bottle", value=TARE_SOLENOID_VALUE)
    await self._sleep(seconds=BOTTLE_EXHAUST_DELAY)
    await self._unchecked_fw_set_solenoid_valve(controller="bottle", value=0)
    await self._unchecked_fw_set_controller_enabled(controller="chip", enabled=True)
    await self._unchecked_fw_set_target_pressure(controller="chip", pressure=chip_pressure)
    self._target_pressures["chip"] = chip_pressure
    await settle(controller="chip", target=chip_pressure)

  def _check_chip_type(
    self, *, chip: Optional[MantisChip], operation: Literal["prime", "recovery"]
  ) -> MantisChip:
    """Check this generation's captures show ``operation`` for ``chip``'s type, and return it.

    Raises:
      NotImplementedError: on a generation whose captures show no such operation (a v3 recovery).
      MantisConfigurationError: for no chip, or a chip type with no captured operation.
    """
    generation = self._driver.configuration.generation
    chip_types = (PRIMED_CHIP_TYPES if operation == "prime" else RECOVERED_CHIP_TYPES).get(
      str(generation)
    )
    if chip_types is None:
      raise NotImplementedError(
        f"{self._driver.name}: no {operation} has been captured on a {generation}; nothing is sent"
      )
    chip_type = None if chip is None else chip.chip_type
    if chip is None or chip_type not in chip_types:
      raise MantisConfigurationError(
        f"{self._driver.name}: the chip is a {chip_type} chip; a {generation}'s {operation} is "
        f"captured for {list(chip_types)}; nothing is sent"
      )
    return chip

  def _check_attached_chip(self, *, chip_number: int) -> MantisChip:
    """Check a recovery of the chip from slot ``chip_number`` can run, and return it: a recovery
    does not pick up, so that chip must be on the head.

    Raises:
      NotImplementedError, MantisConfigurationError: as ``_check_chip_type``; also without an arm,
        or if the chip on the head is not from slot ``chip_number``.
      MantisMotionError: if the arm is not brought up.
    """
    generation = self._driver.configuration.generation
    if str(generation) not in RECOVERED_CHIP_TYPES:
      self._check_chip_type(chip=None, operation="recovery")
    arm = self._driver.arm
    if arm is None:
      raise MantisConfigurationError(f"{self._driver.name}: no arm carries a chip")
    arm._check_initialized(run_on_v3=True)
    if chip_number != arm.current_chip:
      raise MantisConfigurationError(
        f"{self._driver.name}: a recovery needs the chip from slot {chip_number} on the head; "
        f"the head holds the chip from slot {arm.current_chip}; nothing is sent"
      )
    return self._check_chip_type(chip=arm.attached_chip, operation="recovery")

  def _get_default_volume(
    self,
    *,
    chip: MantisChip,
    chip_number: int,
    input_source: MantisChipInput,
    field: Literal["prime_volume", "reagent_recovery_volume"],
  ) -> float:
    """The default volume, uL: input slot ``chip_number``'s in the unit's ``inputs`` block, for
    ``input_source`` and the chip's type; a prime's without it the chip's ``default_prime_volume``.

    Raises:
      MantisConfigurationError: without a default, or with one not more than 0; nothing is sent.
    """
    entry = (self._driver._unit_blocks.get("inputs") or {}).get(str(chip_number))
    try:
      volume = None if entry is None else entry[field].get(input_source, {}).get(chip.chip_type)
    except (AttributeError, KeyError, TypeError) as error:
      raise MantisConfigurationError(f"malformed inputs block: {error!r}") from error
    origin = f"input slot {chip_number}"
    if volume is None and field == "prime_volume":
      volume = chip.default_prime_volume.get(input_source)
      origin = f"the {chip.chip_type} chip"
    if volume is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no default {field} for a {chip.chip_type} chip from a "
        f"{input_source} in input slot {chip_number}; give a volume, or the unit's configuration "
        "folder (oem_config_directory)"
      )
    if not volume > 0:
      raise MantisConfigurationError(
        f"{self._driver.name}: {origin}'s default {field} is {volume} uL"
      )
    logger.info(
      "[%s] %s's default %s from a %s: %s uL",
      self._driver.name,
      origin,
      field,
      input_source,
      volume,
    )
    return float(volume)

  async def _play_queued_operation(
    self,
    *,
    arm: "Arm",
    queue: Callable[[], Awaitable[None]],
    plate_clamp: Optional["PlateClamp"] = None,
  ) -> None:
    """Clear and start the sequencer, lock ``plate_clamp`` if given, start the pump if it was off
    and run ``queue``, then release what the operation took: the clamp, then the whole pressure if
    the pump was off, also after a failed start, else only the chip and vacuum lines, so a pump
    already running for the clamp stays on. Each release is tried; the first failure is raised. If
    ``queue`` fails or is cancelled, the sequencer is aborted and every motor stopped first, as
    ``Arm._play_queued_moves`` does, and a failed release is only logged.

    Args:
      arm: the arm whose motors are stopped on a failure.
      queue: queues the operation and waits for it.
      plate_clamp: locked after the sequencer starts, as a dispense does; None for none. A
        pneumatic clamp's lock switches the pump on, so the pump start only enables the lines.
    """
    pump_was_on = True  # until read: a pump that may run for the clamp is not turned off
    locked = False

    async def release() -> None:
      failure: Optional[BaseException] = None
      if locked:
        assert plate_clamp is not None
        try:
          await plate_clamp.unlock()
        except BaseException as error:  # a cancel too: the pressure is released all the same
          logger.warning("[%s] the clamp was not released", self._driver.name, exc_info=True)
          failure = error
      try:
        if not pump_was_on:
          await self._release_pressure()
          if plate_clamp is not None:
            plate_clamp._started_pump = False  # the pump is off: the clamp no longer owns it
        else:
          for controller in ("chip", "vacuum"):
            await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=False)
      except Exception as error:
        failure = failure or error
      if failure is not None:
        raise failure

    try:
      await self._driver._unchecked_fw_clear_sequencer()
      await self._driver._unchecked_fw_start_sequencer()
      pump_was_on = await self.request_pump_on()
      if plate_clamp is not None:
        await plate_clamp.lock()
        locked = True
      if not pump_was_on:
        await self._start_pump(pump_on=plate_clamp is not None and await self.request_pump_on())
      await queue()
    except BaseException:
      try:
        await self._driver._unchecked_fw_abort_sequencer()
      except Exception:
        logger.warning("[%s] could not abort the sequencer", self._driver.name, exc_info=True)
      await arm._stop_motors(motors=(*ARM_MOTORS, Z_MOTOR))
      await arm._log_where_it_stopped()
      try:
        await release()
      except Exception:
        logger.warning("[%s] the pressure was not released", self._driver.name, exc_info=True)
      raise
    await release()

  async def _queue_plays(
    self, *, chip_type: str, name: str, count: int, durations: Dict[int, int]
  ) -> Tuple[Optional[int], float]:
    """Queue a valve sequence ``count`` times; return its last item and the plays' length, s."""
    item = None
    for _ in range(count):
      item = await _queue_valve_sequence(
        driver=self._driver, chip_type=chip_type, name=name, durations=durations
      )
    steps = VALVE_SEQUENCES[chip_type][name]
    milliseconds = sum(durations.get(index, step[1]) for index, step in enumerate(steps))
    return item, count * milliseconds / 1000

  async def _prime_until_drops(
    self,
    *,
    chip_type: str,
    liquid_class: MantisLiquidClass,
    consecutive_drops: int,
    max_cycles: int,
  ) -> Tuple[bool, int]:
    """Play the prime pump one cycle at a time until ``consecutive_drops`` cycles in a row each
    saw a drop, at most ``max_cycles``; return whether they did, and the cycles played.

    The next cycle is queued once a dry cycle has played; after a cycle that dropped, the liquid
    class's ``auto_prime_detect_liquid_delay`` and ``auto_prime_liquid_counted_delay`` first, as
    captured. The sensor's ``consecutive_run`` follows the run; the head is in the waste.
    """
    sensor = self._driver.drop_sensor
    assert sensor is not None
    delays = LIQUID_CLASS_DELAYS[chip_type]["prime_pump"]
    durations = {index: delay(liquid_class) for index, delay in delays.items()}
    for cycle in range(1, max_cycles + 1):
      drops_before = sensor._get_drop_count()
      item, seconds = await self._queue_plays(
        chip_type=chip_type, name="prime_pump", count=1, durations=durations
      )
      assert item is not None
      # A chosen bound, not a device's: twice what the cycle takes, and a move's.
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT + 2 * seconds)
      if sensor._get_drop_count() == drops_before:
        sensor._consecutive_run = 0
        continue
      sensor._consecutive_run += 1
      await self._sleep(seconds=liquid_class.auto_prime_detect_liquid_delay / 1000)
      await self._sleep(seconds=liquid_class.auto_prime_liquid_counted_delay / 1000)
      if sensor._consecutive_run >= consecutive_drops:
        return True, cycle
    return False, max_cycles

  async def prime_chip(
    self,
    chip_number: int,
    *,
    volume: Optional[float] = None,
    liquid_class: Optional[MantisLiquidClass] = None,
    input_source: MantisChipInput = "tube",
    verify_sensor: bool = True,
    drop_detection: Optional[bool] = None,
    consecutive_drops: int = AUTO_PRIME_CONSECUTIVE_DROPS,
    max_cycles: Optional[int] = None,
  ) -> None:
    """Pick up the chip in slot ``chip_number`` and prime it at the waste, as captured: the pump
    started, the prime pressures set, the head into the waste, the drops, the safe state, the head
    out of the waste to (15, 30) at the safe height, the pressure released. The chip stays on the
    head. After a failure or a cancellation the head stays where it stopped, maybe in the waste;
    ``Arm.recover_from_fault`` brings it back.

    The drops are ``volume`` as small then large drops; with ``drop_detection`` (a v4 only), the
    prime pump one cycle at a time until the drop sensor saw a drop in ``consecutive_drops``
    cycles in a row. A v3 primes from idle only, whole large drops along a waste path at Z's flag,
    as captured (``V3_CHIP_CHANGER_ROUTE`` back). Every check runs before anything moves.

    Args:
      chip_number: the chip slot; its chip is picked up first (``Arm.pick_up_chip``), a no-op when
        it is on the head, a swap when another is.
      volume: uL, more than 0, as whole small and large drops (``_get_pump_cycle``); None the
        default (``_get_default_volume``). None with ``drop_detection``.
      liquid_class: its prime pressures, drop delays and drop-detection waits; None the chip
        type's 1 cP class.
      input_source: "tube" or "pipette_tip"; selects the default volume. Not the slot's own
        ``InputSourceType``: "tube" unless given.
      verify_sensor: with a drop sensor, a fixed prime raises if it saw no drop.
      drop_detection: prime until drops; None: with a drop sensor, no ``volume`` and a chip type
        whose drop-detection prime is captured (``MAXIMUM_AUTO_PRIME_CYCLES``).
      consecutive_drops: with ``drop_detection``, the drops in a row it ends on.
      max_cycles: with ``drop_detection``, the cycle cap; None ``MAXIMUM_AUTO_PRIME_CYCLES``.

    Raises:
      NotImplementedError: on a v3 away from idle, with a waste path that moves Z or a volume
        that needs small drops; nothing moves.
      ValueError: for a ``volume`` not more than 0 or given with ``drop_detection``, an
        ``input_source`` not known, or a cycle count not 1 or more (``consecutive_drops`` at most
        the cap); nothing moves.
      MantisConfigurationError: without the arm, the waste's path or a default volume, for a chip
        type with no captured prime, or with ``drop_detection`` without a drop sensor or for a
        chip type with no captured drop-detection prime; as ``Arm.pick_up_chip``. Nothing moves.
      MantisMotionError: if the arm is not brought up, as ``Arm.pick_up_chip``, or if a motor
        faults.
      MantisPressureError: if a line does not settle. The pressure is released on every path.
      MantisPrimeError: after the prime has ended as above, if the cycle cap is reached first, or
        with ``verify_sensor`` if the drop sensor saw no drop.
    """
    if input_source not in get_args(MantisChipInput):
      raise ValueError(
        f"input_source must be one of {get_args(MantisChipInput)}, got {input_source!r}"
      )
    if volume is not None and not volume > 0:
      raise ValueError(f"volume must be more than 0 uL, got {volume}")
    arm = self._driver.arm
    if arm is None:
      raise MantisConfigurationError(f"{self._driver.name}: no arm carries a chip")
    arm._check_initialized(run_on_v3=True)
    sensor = self._driver.drop_sensor
    if drop_detection and volume is not None:
      raise ValueError("a drop-detection prime takes no volume; it primes until drops")
    if drop_detection and sensor is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no drop sensor; a v4 whose board 12 answers has one"
      )
    is_v3 = self._driver.configuration.generation == "v3"
    # Every check runs before anything moves: the chip from its slot's tag unless on the head.
    if arm.current_chip == chip_number:
      chip = self._check_chip_type(chip=arm.attached_chip, operation="prime")
      if is_v3 and not await arm.request_is_parked():
        raise NotImplementedError(
          f"{self._driver.name}: a v3 primes only from idle, as captured; after a prime the head "
          "stands at (15, 30): place the chip back first; nothing moves"
        )
    else:
      arm._get_chip_slot_path(slot=chip_number)
      tag = await arm._check_chip_slot_tag(slot=chip_number, action="pick_up_chip")
      chip = self._check_chip_type(
        chip=arm._get_chip(chip_number=chip_number, tag=tag), operation="prime"
      )
    chip_type = chip.chip_type
    if drop_detection is None:
      drop_detection = (
        volume is None and sensor is not None and chip_type in MAXIMUM_AUTO_PRIME_CYCLES
      )
    cap = 0
    if drop_detection:
      if chip_type not in MAXIMUM_AUTO_PRIME_CYCLES:
        raise MantisConfigurationError(
          f"{self._driver.name}: the chip is a {chip_type} chip; only "
          f"{sorted(MAXIMUM_AUTO_PRIME_CYCLES)} are primed until drops"
        )
      cap = MAXIMUM_AUTO_PRIME_CYCLES[chip_type] if max_cycles is None else max_cycles
      _check_cycle_count(name="max_cycles", value=cap)
      _check_cycle_count(name="consecutive_drops", value=consecutive_drops)
      if consecutive_drops > cap:
        raise ValueError(
          f"consecutive_drops must be at most {cap}, the cycle cap, got {consecutive_drops}"
        )
    elif volume is None:
      volume = self._get_default_volume(
        chip=chip, chip_number=chip_number, input_source=input_source, field="prime_volume"
      )
    if liquid_class is None:
      liquid_class = mantis_mapping[("1 cP", chip_type)]
    volumes = SEQUENCE_VOLUMES[chip_type]
    (small_volume, small), (large_volume, large) = sorted(
      (sequence_volume, name) for name, sequence_volume in volumes.items() if name != "reverse_pump"
    )
    large_count = small_count = 0
    if volume is not None:
      large_count, small_count = _get_pump_cycle(
        volume=volume, large_volume=large_volume, small_volume=small_volume
      )
    waste_path = arm._get_approach_path(name=WASTE_APPROACH_PATH)
    if is_v3 and any(line.z not in (None, 0.0) for line in waste_path):
      raise NotImplementedError(
        f"{self._driver.name}: a v3 primes only along a waste path at Z's flag, as the one a v3 "
        "played (MoveToWasteStation.seq.txt); this unit's moves Z; nothing moves"
      )
    if is_v3 and small_count > 0:
      raise NotImplementedError(
        f"{self._driver.name}: {volume} uL needs {small_count} small drops, which no v3 prime "
        f"has played; give whole large drops of {large_volume} uL; nothing moves"
      )
    into_waste = arm._get_approach_path_moves(name=WASTE_APPROACH_PATH, reverse=False)
    out_of_waste = arm._get_approach_path_moves(name=WASTE_APPROACH_PATH, reverse=True)
    to_idle = arm._get_route_moves(route=V3_CHIP_CHANGER_ROUTE if is_v3 else CHIP_CHANGER_ROUTE)
    delays = LIQUID_CLASS_DELAYS[chip_type]
    self._primed_chip = None
    await arm.pick_up_chip(chip_number=chip_number)  # nothing moves when the chip is on the head
    if not await arm.request_is_parked():
      await arm.park()
    if sensor is not None:
      sensor.reset()
    drops_seen, cycles = False, 0

    async def queue() -> None:
      nonlocal drops_seen, cycles
      await self._set_pressures(
        chip_pressure=liquid_class.prime_pressure, vacuum=liquid_class.prime_vacuum
      )
      item = await arm._queue_moves(moves=into_waste)
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT * len(into_waste))
      if drop_detection:
        drops_seen, cycles = await self._prime_until_drops(
          chip_type=chip_type,
          liquid_class=liquid_class,
          consecutive_drops=consecutive_drops,
          max_cycles=cap,
        )
      else:
        seconds = 0.0
        for name, count in ((small, small_count), (large, large_count)):
          durations = {index: delay(liquid_class) for index, delay in delays[name].items()}
          last, played = await self._queue_plays(
            chip_type=chip_type, name=name, count=count, durations=durations
          )
          item, seconds = last or item, seconds + played
        # A chosen bound, not a device's: twice what the drops take, and a move's.
        await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT + 2 * seconds)
      # The safe state holds for the large drop's fill delay, as in every captured fixed prime;
      # no drop-detection prime's end is captured on its own, so it ends the same.
      await self._queue_plays(
        chip_type=chip_type,
        name="safe_state",
        count=1,
        durations={0: liquid_class.large_fill_delay},
      )
      item = await arm._queue_moves(moves=out_of_waste + to_idle)
      moves = len(out_of_waste) + len(to_idle)
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT * moves)

    await self._play_queued_operation(arm=arm, queue=queue)
    if drop_detection:
      logger.info(
        "[%s] primed chip %d until drops: %d prime pump cycles, %s",
        self._driver.name,
        chip_number,
        cycles,
        f"the last {consecutive_drops} each with a drop" if drops_seen else "the cap reached",
      )
    else:
      logger.info(
        "[%s] primed chip %d with %s uL: %d small and %d large drops",
        self._driver.name,
        chip_number,
        volume,
        small_count,
        large_count,
      )
    await arm._report_pose()
    if drop_detection and not drops_seen:
      raise MantisPrimeError(
        f"{self._driver.name}: the drop sensor did not see a drop in {consecutive_drops} prime "
        f"pump cycles in a row within {cycles} cycles"
      )
    if not drop_detection and verify_sensor and sensor is not None and not sensor.drop_detected:
      raise MantisPrimeError(
        f"{self._driver.name}: chip {chip_number} was primed with {small_count + large_count} "
        "drops, but the drop sensor saw none"
      )
    self._primed_chip = chip_number

  def _get_dispense_drops(
    self,
    *,
    wells: List[Well],
    volumes: List[float],
    chip_type: str,
    minimum_small_diaphragm_pumps: int,
  ) -> List[Tuple[int, int, int]]:
    """Each well's (index, small drops, large drops) for its volume (``_get_dispense_pump_cycle``),
    a well without drops left out, with a warning if its volume is not 0; a warning too where the
    drops miss the volume by half a small drop or more."""
    (small_volume, _), (large_volume, _) = _get_drop_sequences(chip_type=chip_type)
    drops = []
    for index, (well, well_volume) in enumerate(zip(wells, volumes)):
      large_count, small_count = _get_dispense_pump_cycle(
        volume=well_volume,
        large_volume=large_volume,
        small_volume=small_volume,
        minimum_small_drops=minimum_small_diaphragm_pumps,
      )
      if large_count + small_count == 0:
        if well_volume > 0:
          logger.warning(
            "[%s] %s uL into %s is under half a small drop; skipped",
            self._driver.name,
            well_volume,
            well.name,
          )
        continue
      dispensed = large_count * large_volume + small_count * small_volume
      miss = abs(dispensed - well_volume)
      if miss > small_volume / 2 or math.isclose(miss, small_volume / 2):
        logger.warning(
          "[%s] %s uL into %s is dispensed as %s uL: %d large and %d small drops",
          self._driver.name,
          well_volume,
          well.name,
          dispensed,
          large_count,
          small_count,
        )
      drops.append((index, small_count, large_count))
    return drops

  def _check_dispense_traverse_heights(
    self,
    *,
    drops: List[Tuple[int, int, int]],
    heights: List[Tuple[float, float]],
    precise: bool,
    start: Optional[float],
    end: Optional[float],
  ) -> List[List[Tuple[int, int, int]]]:
    """A dispense's rounds of (well index, small drops, large drops): one, or with ``precise`` the
    small drops' and the large drops'; a round without drops left out of its visits. Every captured
    dispense keeps the nozzle at each well's dispense height from the first well to the last, so a
    traverse height that would add a move is refused.

    Args:
      drops: from ``_get_dispense_drops``.
      heights: each well's dispense height and traverse height between wells, mm above its
        wellplate's base.
      precise: two rounds, each of which must have drops.
      start: ``minimum_traverse_height_start``.
      end: ``minimum_traverse_height_end``.

    Raises:
      NotImplementedError: for ``start`` above a round's first well's dispense height, a traverse
        height above a later well's, ``end`` above the last well's, or a precise round without
        drops; nothing is sent.
    """
    rounds = (
      [
        [(index, small_count, 0) for index, small_count, _ in drops if small_count > 0],
        [(index, 0, large_count) for index, _, large_count in drops if large_count > 0],
      ]
      if precise
      else [drops]
    )
    if precise and drops and not all(rounds):
      raise NotImplementedError(
        "a precise dispense whose wells take no small or no large drops has not been captured; "
        "nothing is sent"
      )
    visited = [round_drops for round_drops in rounds if round_drops]
    for round_drops in visited:
      first = round_drops[0][0]
      if start is not None and start > heights[first][0]:
        raise NotImplementedError(
          f"minimum_traverse_height_start {start} mm is above the first well's dispense height, "
          f"{heights[first][0]:.2f} mm: no capture shows that move; nothing is sent"
        )
      for index, _, _ in round_drops[1:]:
        if heights[index][0] < heights[index][1]:
          raise NotImplementedError(
            f"the traverse height between wells, {heights[index][1]:.2f} mm, is above a well's "
            f"dispense height, {heights[index][0]:.2f} mm: no capture shows the nozzle rising "
            "between wells; lower minimum_traverse_height_during or nozzle_clearance_above_plate; "
            "nothing is sent"
          )
    if visited and end is not None and end > heights[visited[-1][-1][0]][0]:
      raise NotImplementedError(
        f"minimum_traverse_height_end {end} mm is above the last well's dispense height: no "
        "capture shows that move; nothing is sent"
      )
    return rounds

  async def dispense(
    self,
    resource: Union[Plate, List[Well]],
    volume: Union[float, List[float]],
    offset: Optional[Coordinate] = None,
    *,
    chip: Optional[int] = None,
    liquid_class: Optional[MantisLiquidClass] = None,
    dispense_height_above_well: Optional[float] = None,
    prime_volume: Optional[float] = None,
    pre_dispense_volume: Optional[float] = None,
    minimum_small_diaphragm_pumps: int = 0,
    precise: bool = False,
    wait_for_each_drop: bool = False,
    place_chip_after: bool = False,
    minimum_traverse_height_start: Optional[float] = None,
    minimum_traverse_height_during: Optional[float] = None,
    minimum_traverse_height_end: Optional[float] = None,
    nozzle_clearance_above_plate: float = PITCH_HEIGHT,
  ) -> None:
    """Dispense into wells from the chip in slot ``chip``, priming it first if it is not primed
    (``prime_chip``, which picks it up), as captured: the clamp locked, the pump started, the
    dispense pressures set; at the waste the pre-dispense, large drops first, and back to (15, 30)
    at the safe height; from idle down onto the first well, then well to well in one move each,
    each well's small then large drops; the safe state; the arms back to (15, 30) at the last well's
    height, then Z up there to the safe height, twice; the clamp and the pressure released. The chip
    stays on the head unless ``place_chip_after``. It starts from idle, or from (15, 30) at the safe
    height where a prime leaves the head; a v4 elsewhere parks first, a v3 refuses. All volumes 0
    send nothing.

    A precise dispense takes two rounds: every well's small drops; then to (15, 30), at the waste
    ``PRECISE_PRE_DISPENSE_LARGE_DROPS``, back to (15, 30) and to the safe height, and every well's
    large drops. The safe state follows the last round only. Below the safe height the head moves
    unguarded, as ``Arm.move_to_well`` does.

    Args:
      resource: a wellplate (all its wells) or its wells, in the order dispensed.
      volume: uL per well, one for all or one per well, as whole small and large drops
        (``_get_dispense_pump_cycle``); a well whose volume gives no drop is skipped with a warning.
      offset: added to each nozzle location, mm.
      chip: the chip slot; None the chip on the head.
      liquid_class: its dispense pressures and drop delays, and the prime's; None the chip type's
        1 cP class.
      dispense_height_above_well: the nozzle's height above the wellplate's highest point (its
        frame or its wells' tops), mm; None ``default_dispense_height_above_well``.
      prime_volume: uL for the prime before the dispense, if one runs; None its default.
      pre_dispense_volume: uL played at the waste before the first well; None the chip type's
        ``PRE_DISPENSE_VOLUMES``, or ``PRECISE_PRE_DISPENSE_VOLUMES`` if ``precise``; 0, which
        no capture shows, is refused.
      minimum_small_diaphragm_pumps: small drops each well takes first, as far as its volume holds
        them; the unit's wellplate definitions name it ``minsd``.
      precise: every well's small drops first, then the large ones; the unit's Precise dispense
        mode. A round with no drops is left out.
      wait_for_each_drop: queue each drop in a well once the one before it has played; slower, the
        same frames.
      place_chip_after: place the chip back into its slot after the clamp and pressure are released
        (``Arm.place_chip``).
      minimum_traverse_height_start: the nozzle's height above the wellplate base before it
        travels to the first well of a round, mm; None or one at most that well's dispense height.
      minimum_traverse_height_during: the nozzle's height above the wellplate base between wells,
        mm; None ``nozzle_clearance_above_plate`` above the wellplate's highest point. It must be
        at most each well's dispense height.
      minimum_traverse_height_end: the nozzle's height above the wellplate base after the last
        well, mm; None or one at most that well's dispense height.
      nozzle_clearance_above_plate: with ``minimum_traverse_height_during`` None, how far the
        nozzle clears the wellplate's highest point between wells, mm.

    Raises:
      NotImplementedError: for a traverse height that would add a move no capture shows, a
        ``pre_dispense_volume`` of 0, a precise dispense with no small or no large drops, a
        pneumatic clamp on a v4, or a v3 away from where it starts; nothing is sent.
      ValueError: if the wells and volumes differ in number, a volume, ``pre_dispense_volume`` or
        ``minimum_small_diaphragm_pumps`` is negative, a well cannot be reached as
        ``Arm.move_to_well`` refuses it, or the clamp's ``default_lock_level`` is not one of its
        levels; nothing is sent.
      MantisConfigurationError: without the arm, the stage, the waste's path or the clamp's levels;
        without a chip given or on the head; for a chip type with no captured dispense, or no
        captured precise dispense if ``precise``; as ``prime_chip``. Nothing is sent, or after the
        prime only.
      MantisMotionError: if the arm is not brought up, or a motor faults.
      MantisPressureError: if a line does not settle. The clamp and the pressure are released on
        every path.
      MantisPrimeError: as ``prime_chip``; nothing is dispensed.
    """
    wells = list(resource.get_all_items()) if isinstance(resource, Plate) else list(resource)
    if not wells:
      return
    volumes = list(volume) if isinstance(volume, (list, tuple)) else [volume] * len(wells)
    if len(wells) != len(volumes):
      raise ValueError(f"{len(wells)} wells but {len(volumes)} volumes; nothing is sent")
    if any(well_volume < 0 for well_volume in volumes):
      raise ValueError(f"volumes must not be negative, got {volumes}; nothing is sent")
    if (
      isinstance(minimum_small_diaphragm_pumps, bool)
      or not isinstance(minimum_small_diaphragm_pumps, int)
      or minimum_small_diaphragm_pumps < 0
    ):
      raise ValueError(
        "minimum_small_diaphragm_pumps must be a whole number of 0 or more, got "
        f"{minimum_small_diaphragm_pumps!r}"
      )
    if pre_dispense_volume is not None and pre_dispense_volume < 0:
      raise ValueError(f"pre_dispense_volume must not be negative, got {pre_dispense_volume}")
    if dispense_height_above_well is None:
      dispense_height_above_well = self.default_dispense_height_above_well
    if offset is None:
      offset = Coordinate.zero()
    generation = str(self._driver.configuration.generation)
    arm = self._driver.arm
    if arm is None:
      raise MantisConfigurationError(f"{self._driver.name}: no arm carries a chip")
    arm._check_initialized(run_on_v3=True)
    arm._check_stage()
    chip_number = arm.current_chip if chip is None else chip
    if chip_number is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no chip given and none on the head; give chip or pick one up"
      )
    plate_clamp = self._driver.plate_clamp
    if plate_clamp is not None and plate_clamp.auxiliary_output is not None and generation == "v4":
      raise NotImplementedError(
        f"{self._driver.name}: a v4 dispense with a pneumatic clamp has not been captured; "
        "nothing is sent"
      )
    if plate_clamp is not None and plate_clamp.auxiliary_output is None:
      if plate_clamp.levels is None:
        raise MantisConfigurationError(
          f"{self._driver.name}: no clamp levels for this unit; give its vendor folder "
          "(oem_config_directory)"
        )
      self._driver._check_index(
        name="default_lock_level",
        value=plate_clamp.default_lock_level,
        allowed=tuple(plate_clamp.levels),
      )
    # Each well's dispense height above its wellplate's base and the height between wells there.
    targets: List[Dict[int, float]] = []
    heights: List[Tuple[float, float]] = []
    for well in wells:
      targets.append(
        arm._get_route_line_targets(
          route_line=arm._get_well_arm_frame_location(
            well=well, offset=offset, height=dispense_height_above_well
          )
        )
      )
      top = arm._get_wellplate_top(well=well)
      during = (
        top + nozzle_clearance_above_plate
        if minimum_traverse_height_during is None
        else minimum_traverse_height_during
      )
      heights.append((top + dispense_height_above_well + offset.z, during))
    arm._get_approach_path(name=WASTE_APPROACH_PATH)
    if pre_dispense_volume == 0:
      raise NotImplementedError(
        "a dispense without a pre-dispense has not been captured; nothing is sent"
      )
    if all(well_volume == 0 for well_volume in volumes):
      return

    async def request_is_at_start() -> bool:
      # Idle, or (15, 30) at the safe height, where a prime leaves the head: the waste's path
      # starts there, as captured.
      return await arm.request_is_parked() or await arm._request_is_at(
        location=arm._get_chip_changer_route()[-1], tolerance=0.5
      )

    if generation == "v3" and not await request_is_at_start():
      raise NotImplementedError(
        f"{self._driver.name}: a v3 dispenses only from idle or where its prime left the head, "
        "as captured; nothing moves"
      )

    def get_chip() -> MantisChip:
      # The chip on the head once primed: its type decides the drops.
      attached = arm.attached_chip
      chip_types = DISPENSED_CHIP_TYPES[generation]
      if attached is None or attached.chip_type not in chip_types:
        chip_type = None if attached is None else attached.chip_type
        raise MantisConfigurationError(
          f"{self._driver.name}: the chip on the head is a {chip_type} chip; a {generation}'s "
          f"dispense is captured for {list(chip_types)}"
        )
      if precise and attached.chip_type not in PRECISE_PRE_DISPENSE_LARGE_DROPS:
        raise MantisConfigurationError(
          f"{self._driver.name}: the chip on the head is a {attached.chip_type} chip; only "
          f"{sorted(PRECISE_PRE_DISPENSE_LARGE_DROPS)} dispense precisely; nothing is sent"
        )
      return attached

    if arm.current_chip == chip_number:
      chip_type = get_chip().chip_type
      self._check_dispense_traverse_heights(
        drops=self._get_dispense_drops(
          wells=wells,
          volumes=volumes,
          chip_type=chip_type,
          minimum_small_diaphragm_pumps=minimum_small_diaphragm_pumps,
        ),
        heights=heights,
        precise=precise,
        start=minimum_traverse_height_start,
        end=minimum_traverse_height_end,
      )
    if not (arm.current_chip == chip_number and self.is_primed):
      # As the vendor's dispense: a chip not primed is primed first.
      await self.prime_chip(chip_number=chip_number, volume=prime_volume, liquid_class=liquid_class)
    chip_type = get_chip().chip_type
    drops = self._get_dispense_drops(
      wells=wells,
      volumes=volumes,
      chip_type=chip_type,
      minimum_small_diaphragm_pumps=minimum_small_diaphragm_pumps,
    )
    rounds = self._check_dispense_traverse_heights(
      drops=drops,
      heights=heights,
      precise=precise,
      start=minimum_traverse_height_start,
      end=minimum_traverse_height_end,
    )
    if not drops:
      return
    if pre_dispense_volume is None:
      pre_dispense_volume = (PRECISE_PRE_DISPENSE_VOLUMES if precise else PRE_DISPENSE_VOLUMES)[
        chip_type
      ]
    if liquid_class is None:
      liquid_class = mantis_mapping[("1 cP", chip_type)]
    (small_volume, small), (large_volume, large) = _get_drop_sequences(chip_type=chip_type)
    profiles = arm._get_route_profiles()
    # Each round's visits: its first well entered from idle, each next one in one move.
    visits_by_round: List[List[Tuple[List[List[Tuple[float, float, float]]], int, int]]] = []
    for round_drops in rounds:
      visits: List[Tuple[List[List[Tuple[float, float, float]]], int, int]] = []
      for index, small_count, large_count in round_drops:
        moves = (
          [arm._get_queued_move(targets=targets[index], profiles=profiles)]
          if visits
          else arm._get_well_entry_moves(targets=targets[index])
        )
        visits.append((moves, small_count, large_count))
      visits_by_round.append(visits)
    large_round = visits_by_round[1] if precise else []
    into_waste = arm._get_approach_path_moves(name=WASTE_APPROACH_PATH, reverse=False)
    out_of_waste = arm._get_approach_path_moves(name=WASTE_APPROACH_PATH, reverse=True)
    chip_changer_route = arm._get_chip_changer_route()
    to_idle = arm._get_route_moves(route=chip_changer_route)
    # The safe height alone, as the unit's MoveToSafeHeight sequence: the route's third line.
    to_safe_height = arm._get_route_moves(route=chip_changer_route[2:3])
    delays = LIQUID_CLASS_DELAYS[chip_type]
    durations = {
      name: {index: delay(liquid_class) for index, delay in delays[name].items()}
      for name in (small, large)
    }
    if not await request_is_at_start():
      if generation == "v3":
        raise NotImplementedError(
          f"{self._driver.name}: a v3 dispenses only from idle or where its prime left the head, "
          "as captured; nothing moves"
        )
      await arm.park()

    async def queue_drops(
      *, order: Tuple[Tuple[str, int], ...], wait: bool, item: Optional[int]
    ) -> Tuple[Optional[int], float]:
      seconds = 0.0
      for name, count in order:
        for _ in range(count):
          if wait and item is not None:
            # A chosen bound, not a device's: a move's.
            await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT)
          last, played = await self._queue_plays(
            chip_type=chip_type, name=name, count=1, durations=durations[name]
          )
          item, seconds = last or item, seconds + played
      return item, seconds

    async def queue_pre_dispense(
      *,
      into: List[List[Tuple[float, float, float]]],
      order: Tuple[Tuple[str, int], ...],
      out: List[List[Tuple[float, float, float]]],
    ) -> None:
      item = await arm._queue_moves(moves=into)
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT * len(into))
      _, seconds = await queue_drops(order=order, wait=False, item=None)
      item = await arm._queue_moves(moves=out)
      # A chosen bound, not a device's: twice what the drops take, and a move's each.
      await self._driver._wait_for_queue_item(
        item=item, timeout=MOVE_TIMEOUT * len(out) + 2 * seconds
      )

    async def queue_visits(
      *, visits: List[Tuple[List[List[Tuple[float, float, float]]], int, int]]
    ) -> None:
      item: Optional[int] = None
      seconds, count = 0.0, 0
      for moves, small_count, large_count in visits:
        item = await arm._queue_moves(moves=moves)
        item, played = await queue_drops(
          order=((small, small_count), (large, large_count)), wait=wait_for_each_drop, item=item
        )
        seconds, count = seconds + played, count + len(moves)
      assert item is not None
      # A chosen bound, not a device's: twice what the drops take, and a move's each.
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT * count + 2 * seconds)

    async def queue() -> None:
      await self._set_pressures(
        chip_pressure=liquid_class.chip_pressure, vacuum=liquid_class.vacuum
      )
      if pre_dispense_volume > 0:
        large_count, small_count = _get_pump_cycle(
          volume=pre_dispense_volume, large_volume=large_volume, small_volume=small_volume
        )
        await queue_pre_dispense(
          into=into_waste,
          order=((large, large_count), (small, small_count)),
          out=out_of_waste + to_idle + to_safe_height + to_idle,
        )
      if visits_by_round[0]:
        await queue_visits(visits=visits_by_round[0])
      if large_round:
        # As both captured precise dispenses: to (15, 30) at the safe height, into the waste, and
        # out as after the pre-dispense but without its last way to (15, 30).
        await queue_pre_dispense(
          into=to_idle + into_waste,
          order=((large, PRECISE_PRE_DISPENSE_LARGE_DROPS[chip_type]),),
          out=out_of_waste + to_idle + to_safe_height,
        )
        await queue_visits(visits=large_round)
      # The safe state holds for the large drop's fill delay, as captured after the last well.
      await self._queue_plays(
        chip_type=chip_type,
        name="safe_state",
        count=1,
        durations={0: liquid_class.large_fill_delay},
      )
      item = await arm._queue_moves(moves=to_idle)
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT * len(to_idle))
      # The way to (15, 30) again, as every captured dispense sends it before the clamp's release.
      item = await arm._queue_moves(moves=to_idle)
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT * len(to_idle))

    await self._play_queued_operation(arm=arm, queue=queue, plate_clamp=plate_clamp)
    logger.info(
      "[%s] dispensed %s uL into %d wells from chip %d",
      self._driver.name,
      sum(volumes),
      len(drops),
      chip_number,
    )
    await arm._report_pose()
    if place_chip_after:
      await arm.place_chip(chip_number=chip_number)

  async def recover_reagent(
    self,
    chip_number: int,
    *,
    volume: Optional[float] = None,
    input_source: MantisChipInput = "tube",
    liquid_class: Optional[MantisLiquidClass] = None,
  ) -> None:
    """Draw ``volume`` of the chip on the head's reagent back into its input, as captured: the
    pump started, the head to (15, 30) at the safe height, the recovery pressures set, the reverse
    pump played, the safe state, the pressure released. The chip stays on the head.

    Args:
      chip_number: the chip slot the chip on the head came from.
      volume: uL, more than 0, as whole plays of the reverse pump (``_get_reverse_pump_cycle``);
        None input slot ``chip_number``'s default (``_get_default_volume``).
      input_source: "tube" or "pipette_tip"; selects the default volume.
      liquid_class: its recovery pressures and delays; None the chip type's 1 cP class.

    Raises:
      NotImplementedError: on a v3, whose recovery is not captured; nothing is sent.
      ValueError: for a ``volume`` not more than 0 or an ``input_source`` not known; nothing is
        sent.
      MantisConfigurationError: without the arm or a default volume, if the chip on the head is not
        from ``chip_number`` (a recovery does not pick up), or for a chip type with no captured
        recovery; nothing is sent.
      MantisMotionError: if the arm is not brought up, nothing is sent; or if a motor faults.
      MantisPressureError: if a line does not settle. The pressure is released on every path.
    """
    if input_source not in get_args(MantisChipInput):
      raise ValueError(
        f"input_source must be one of {get_args(MantisChipInput)}, got {input_source!r}"
      )
    if volume is not None and not volume > 0:
      raise ValueError(f"volume must be more than 0 uL, got {volume}")
    chip = self._check_attached_chip(chip_number=chip_number)
    chip_type = chip.chip_type
    if volume is None:
      volume = self._get_default_volume(
        chip=chip,
        chip_number=chip_number,
        input_source=input_source,
        field="reagent_recovery_volume",
      )
    arm = self._driver.arm
    assert arm is not None
    self._primed_chip = None
    if liquid_class is None:
      liquid_class = mantis_mapping[("1 cP", chip_type)]
    count = _get_reverse_pump_cycle(
      volume=volume, reverse_pump_volume=SEQUENCE_VOLUMES[chip_type]["reverse_pump"]
    )
    to_idle = arm._get_route_moves(route=CHIP_CHANGER_ROUTE)
    delays = LIQUID_CLASS_DELAYS[chip_type]["reverse_pump"]
    durations = {index: delay(liquid_class) for index, delay in delays.items()}
    if not await arm.request_is_parked():
      await arm.park()

    async def queue() -> None:
      await arm._queue_moves(moves=to_idle)
      await self._set_pressures(
        chip_pressure=liquid_class.recovery_pressure, vacuum=liquid_class.recovery_vacuum
      )
      item, seconds = await self._queue_plays(
        chip_type=chip_type, name="reverse_pump", count=count, durations=durations
      )
      assert item is not None
      # A chosen bound, not a device's: twice what the plays take, and a move's.
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT + 2 * seconds)
      # The safe state holds for the recovery's fill delay, as in every captured recovery.
      await self._queue_plays(
        chip_type=chip_type,
        name="safe_state",
        count=1,
        durations={0: liquid_class.recovery_fill_delay},
      )
      item = await arm._queue_moves(moves=to_idle)
      await self._driver._wait_for_queue_item(item=item, timeout=MOVE_TIMEOUT * len(to_idle))

    await self._play_queued_operation(arm=arm, queue=queue)
    logger.info(
      "[%s] recovered %s uL from chip %d: %d reverse pump plays",
      self._driver.name,
      volume,
      chip_number,
      count,
    )
    await arm._report_pose()
