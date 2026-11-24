"""The pump: the pressure board (board 10), its air pump and its three pressure controllers.

Each controller regulates one line, bottle, chip or vacuum, from its feedback sensor. Public
methods read; the pneumatic clamp can privately start and restore the air pump. Setup tares the
bottle and chip sensors; stop releases the pressure if the pump runs and writes their calibration
back.
"""

import asyncio
import functools
import logging
import struct
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Literal, Tuple

from ..errors import MantisPressureError
from ..framing import PRESSURE_BOARD

if TYPE_CHECKING:
  from ..master import MantisDriver

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
# startup.
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

  async def _stop(self) -> None:
    """Release the pressure if the pump runs, then write back each tared sensor's calibration as
    read before its tare. Both are tried; the first failure is raised after them.

    Raises:
      MantisPressureError: if the pump still runs after the release.
    """
    failure = None
    try:
      if await self.request_pump_on():
        await self._release_pressure()
    except Exception as error:
      failure = error
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
