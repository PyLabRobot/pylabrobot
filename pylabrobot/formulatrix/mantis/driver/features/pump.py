"""The pump: the pressure board (board 10), its air pump and its three pressure controllers.

Each controller regulates one line, bottle, chip or vacuum, from its feedback sensor. Public
methods read, prime the chip on the head (a volume, or until the drop sensor sees drops) and
recover its reagent, on a v4 and, the prime only, on a v3; the pneumatic clamp can privately
start and restore the air pump. Setup tares the bottle and chip sensors; stop releases the
pressure if the pump runs and writes their calibration back.
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
  get_args,
)

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
from .arm import ARM_MOTORS, CHIP_CHANGER_ROUTE, MOVE_TIMEOUT, V3_CHIP_CHANGER_ROUTE, Z_MOTOR

if TYPE_CHECKING:
  from ..master import MantisDriver
  from .arm import Arm

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

  async def _start_pump(self) -> None:
    """Start the pump for an operation, as captured: on a v4 every valve queued closed first, the
    first time since setup (a v3 never does); the pump on; each line enabled, with the target last
    set on it, if any; then ``START_PUMP_DELAY``. The sequencer must run.
    """
    if not self._valves_closed and self._driver.configuration.generation == "v4":
      await self._driver._queue_ppi_write(
        duration=VALVES_CLOSED_DURATION,
        start_address=VALVE_BANK_PPI_ADDRESS,
        values=[VALVES_CLOSED],
      )
      self._valves_closed = True
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
    self, *, arm: "Arm", queue: Callable[[], Awaitable[None]]
  ) -> None:
    """Clear and start the sequencer, start the pump if it is off and run ``queue``, then release
    what the operation took: the whole pressure if the pump was off, also after a failed start,
    else only the chip and vacuum lines, so a pump already running for the clamp stays on. If
    ``queue`` fails or is cancelled, the sequencer is aborted and every motor stopped first, as
    ``Arm._play_queued_moves`` does, and a failed release is only logged.
    """
    pump_was_on = True  # until read: a pump that may run for the clamp is not turned off

    async def release() -> None:
      if not pump_was_on:
        await self._release_pressure()
        return
      for controller in ("chip", "vacuum"):
        await self._unchecked_fw_set_controller_enabled(controller=controller, enabled=False)

    try:
      await self._driver._unchecked_fw_clear_sequencer()
      await self._driver._unchecked_fw_start_sequencer()
      pump_was_on = await self.request_pump_on()
      if not pump_was_on:
        await self._start_pump()
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
