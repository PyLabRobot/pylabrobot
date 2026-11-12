"""A simulated Mantis: the driver with no device behind it, answering from its configuration."""

import logging
from typing import Dict, List, Optional, Tuple

from pylabrobot.io.ftdi import FTDI

from .configuration import (
  RECORDING_MANTIS_V3,
  RECORDING_MANTIS_V4,
  DeviceConfiguration,
  compute_generation,
  configuration_from_json,
  read_configuration,
)
from .errors import MantisConfigurationError, MantisTimeoutError
from .features.led_bar import LedBar
from .features.plate_clamp import CLAMPING_DONE, UNCLAMPING_DONE, PlateClamp
from .features.pump import PressureController, Pump
from .features.rfid import Rfid
from .framing import ABSOLUTE_POSITION_SENSOR_BOARD, LC3_BOARD, MOTION_BOARD, PRESSURE_BOARD
from .master import ABSOLUTE_ENCODER_MOTORS, MOTORS, SEQUENCER_QUEUE_SIZE, MantisDriver

logger = logging.getLogger(__name__)


class MantisSimulationPlateClamp(PlateClamp):
  """A plate clamp with no device behind it: each command sets the state it ends in, at once."""

  def __init__(
    self, *, driver: "MantisSimulationDriver", levels: Optional[Dict[int, Tuple[float, int]]]
  ):
    """
    Args:
      driver: the simulated driver.
      levels: the declared lock levels, as for ``PlateClamp``.
    """
    super().__init__(driver=driver, levels=levels)
    self._state = 0  # idle, as after power-on

  async def _unchecked_fw_do_plate_unclamping(self) -> None:
    """Set the state to unclamping done."""
    self._state = UNCLAMPING_DONE

  async def _unchecked_fw_do_plate_clamping(self, *, pwm: float, timeout_ms: int) -> None:
    """Set the state to clamping done."""
    self._state = CLAMPING_DONE

  async def request_state(self, *, attempts: int = 3) -> int:
    """The simulated state."""
    return self._state


class MantisSimulationPump(Pump):
  """A pump with no device behind it, answering from the declared configuration.

  Off, its controllers off and disabled, every line at 0, with the recorded sensor calibration.
  """

  async def measure_pressure(self, *, controller: PressureController) -> float:
    """0: no line holds pressure."""
    self._get_controller_index(controller=controller)
    return 0.0

  async def request_feedback_sensor_parameters(
    self, *, controller: PressureController
  ) -> Tuple[float, float]:
    """The recorded calibration, as setup read it.

    Raises:
      MantisConfigurationError: if the generation's recording holds none for ``controller``.
    """
    self._get_controller_index(controller=controller)
    parameters = self._driver.configuration.feedback_sensor_parameters.get(controller)
    if parameters is None:
      raise MantisConfigurationError(
        f"{self._driver.name}: no feedback_sensor_parameters recorded for the {controller} "
        "controller"
      )
    return parameters

  async def request_controller_status(self, *, controller: PressureController) -> int:
    """0: off."""
    self._get_controller_index(controller=controller)
    return 0

  async def request_controller_enabled(self, *, controller: PressureController) -> bool:
    """False: no controller regulates."""
    self._get_controller_index(controller=controller)
    return False

  async def request_pump_on(self) -> bool:
    """False: the pump is off."""
    return False

  async def _request_calibration(self) -> Dict[PressureController, Tuple[float, float]]:
    """The generation's recording's calibration; none recorded is none."""
    generation = self._driver.configuration.generation
    path = RECORDING_MANTIS_V4 if generation == "v4" else RECORDING_MANTIS_V3
    recorded = read_configuration(path=path)["device"].get("feedback_sensor_parameters", {})
    return {
      controller: (float(scale), float(offset)) for controller, (scale, offset) in recorded.items()
    }


class MantisSimulationRfid(Rfid):
  """An RFID reader with no device behind it: every slot reads empty."""

  async def _unchecked_fw_write_ppi(self, *, start_address: int, values: List[int]) -> None:
    """Select nothing: there is no multiplexer."""

  async def _read_tag(self) -> Optional[bytes]:
    """No tag: every slot is empty."""
    return None


class MantisSimulationLedBar(LedBar):
  """An LED bar with no device behind it: every command is taken, and no hold is waited."""

  async def _unchecked_fw_stop_animator(self) -> None:
    """Send nothing."""

  async def _unchecked_fw_start_animator(self, *, lowest_index: int, highest_index: int) -> None:
    """Send nothing."""

  async def _unchecked_fw_set_rgb_led(self, *, colors: List[int]) -> None:
    """Send nothing."""

  async def _sleep(self, *, seconds: float) -> None:
    """Return at once: a simulated bar has nothing to show."""


class MantisSimulationDriver(MantisDriver):
  """A Mantis driver with no device behind it.

  Nothing reaches a wire. The configuration comes from ``declared_configuration_json``, and the
  reads answer from it; a board it leaves ``None`` does not answer. The plate clamp's state changes
  at once, every RFID slot reads empty, and the LED bar takes every command and holds nothing. The
  motors read idle at 0, as after power-on. The device name and the motor reads the configuration
  does not hold are not simulated.

  The sequencer holds ``SEQUENCER_QUEUE_SIZE`` items, stopped and empty after setup; started, it
  plays each item at once. Stop and abort keep what is queued, as on a v4; only a clear drops it.
  """

  def __init__(
    self,
    declared_configuration_json: str,
    name: str = "Mantis",
    *,
    oem_config_directory: Optional[str] = None,
    show_actions: bool = True,
  ):
    """
    Args:
      declared_configuration_json: a file ``save_configuration`` wrote; the simulated device's
        configuration.
      name: the device name, heading its log messages.
      oem_config_directory: a unit's vendor ``Data/Device/Configs`` folder, as for a device.
      show_actions: whether the LED bar shows each action, as for a device.
    """
    super().__init__(
      name=name,
      declared_configuration_json=declared_configuration_json,
      oem_config_directory=oem_config_directory,
      show_actions=show_actions,
    )
    self._queued_items: List[int] = []
    self._sequencer_started = False

  def _build_io(self, *, device_id: Optional[str], ftdi_library: Optional[str]) -> FTDI:
    """Build no link."""
    return None  # type: ignore[return-value]  # no link: nothing reaches a wire

  def _build_rfid_io(
    self, *, device_id: Optional[str], ftdi_library: Optional[str]
  ) -> Optional[FTDI]:
    """Build no link."""
    return None

  async def _send_command(
    self,
    *,
    board: int,
    command: int,
    payload: bytes = b"",
    timeout: Optional[float] = None,
    attempts: int = 1,
  ) -> bytes:
    """Raise: a simulated Mantis sends nothing."""
    raise NotImplementedError(f"{self.name}: a simulated Mantis sends nothing")

  async def request_firmware_version(self, *, board: int = MOTION_BOARD) -> str:
    """The declared firmware version of a board.

    Raises:
      MantisTimeoutError: if the configuration leaves the board ``None``.
    """
    self._check_index(
      name="board",
      value=board,
      allowed=(MOTION_BOARD, PRESSURE_BOARD, ABSOLUTE_POSITION_SENSOR_BOARD, LC3_BOARD),
    )
    version = self.configuration.firmware_versions.get(board)
    if version is None:
      raise MantisTimeoutError(f"{self.name}: board {board} does not answer")
    return version

  async def request_name(self) -> str:
    """Raise: the name is not part of the configuration."""
    raise NotImplementedError(f"{self.name}: the device name is not simulated")

  async def request_motor_limits(self, *, motor: int) -> Tuple[float, float]:
    """A motor's declared (lower, upper) limit."""
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    return self.configuration.motor_limits[motor]

  async def request_motor_currents(self, *, motor: int) -> Tuple[float, float, float]:
    """Raise: the currents are not part of the configuration."""
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    raise NotImplementedError(f"{self.name}: the motor currents are not simulated")

  async def request_motor_configuration(
    self, *, motor: int
  ) -> Tuple[int, float, float, float, int, int]:
    """Raise: a motor's configuration is not part of the device configuration."""
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    raise NotImplementedError(f"{self.name}: the motor configuration is not simulated")

  async def request_motor_status(self, *, motor: int) -> int:
    """0: idle, no fault, as after power-on."""
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    return 0

  async def request_motor_position(self, *, motor: int) -> float:
    """0, as after power-on."""
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    return 0.0

  async def request_following_error_configuration(self, *, motor: int) -> Tuple[bool, float]:
    """Raise: the following-error check is not part of the configuration."""
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    raise NotImplementedError(f"{self.name}: the following-error check is not simulated")

  async def request_encoder_position(self, *, motor: int) -> float:
    """0, as after power-on."""
    self._check_index(name="motor", value=motor, allowed=MOTORS)
    return 0.0

  async def request_absolute_encoder_position(self, *, motor: int) -> float:
    """Raise: a raw reading means nothing without the unit's encoder correction."""
    self._check_index(name="motor", value=motor, allowed=ABSOLUTE_ENCODER_MOTORS)
    raise NotImplementedError(f"{self.name}: the absolute encoders are not simulated")

  def _play_queued_items(self) -> None:
    """Play every queued item at once, if the sequencer is started."""
    if self._sequencer_started and self._queued_items:
      self._sequence_progress = self._last_drained_item = self._queued_items[-1]
      self._queued_items = []

  async def _unchecked_fw_clear_sequencer(self) -> int:
    """Empty the queue."""
    self._queued_items = []
    return 0

  async def _unchecked_fw_start_sequencer(self) -> int:
    """Start the sequencer; it plays what is queued."""
    self._sequencer_started = True
    self._play_queued_items()
    return 0

  async def _unchecked_fw_stop_sequencer(self) -> int:
    """Stop the sequencer; what is queued stays."""
    self._sequencer_started = False
    return 0

  async def _unchecked_fw_abort_sequencer(self) -> int:
    """Stop the sequencer; what is queued stays."""
    self._sequencer_started = False
    return 0

  async def _unchecked_fw_queue_write_ppi(
    self, *, item: int, duration: int, start_address: int, values: List[int]
  ) -> Tuple[int, int]:
    """Queue the item unless the queue is full; started, play it. Writes nothing."""
    if len(self._queued_items) >= SEQUENCER_QUEUE_SIZE:
      return -1, 0
    self._queued_items.append(item)
    free = SEQUENCER_QUEUE_SIZE - len(self._queued_items)
    self._play_queued_items()
    return free, 0

  async def _wait_for_queue_item(self, *, item: int, timeout: float) -> None:
    """Return if ``item`` was the last played, with none left queued.

    Raises:
      MantisTimeoutError: at once otherwise: the sequencer is stopped or played a later item.
    """
    if self._last_drained_item != item:
      raise MantisTimeoutError(f"{self.name}: queue item {item} is not reported played last")

  async def _request_configuration(self) -> DeviceConfiguration:
    """The declared configuration's ``device`` block, a field it lacks filled as for the unit
    blocks; the generation from its boards, as a device's is."""
    declared = configuration_from_json(
      fields=read_configuration(path=self.declared_configuration_json or "")["device"]
    )
    generation = compute_generation(firmware_versions=declared.firmware_versions)
    values, _ = self._load_unit_values(generation=generation)
    configuration = configuration_from_json(fields=values["device"])
    configuration.generation = generation
    return configuration

  def _discover_features(self) -> None:
    """Build the features as a device would, simulated, and the RFID reader.

    Raises:
      MantisConfigurationError: if a declared block is malformed.
    """
    super()._discover_features()
    if self.plate_clamp is not None and not isinstance(
      self.plate_clamp, MantisSimulationPlateClamp
    ):
      self.plate_clamp = MantisSimulationPlateClamp(driver=self, levels=self.plate_clamp.levels)
    if self.pump is not None and not isinstance(self.pump, MantisSimulationPump):
      self.pump = MantisSimulationPump(driver=self)
    if self.led_bar is not None and not isinstance(self.led_bar, MantisSimulationLedBar):
      self.led_bar = MantisSimulationLedBar(
        driver=self,
        number_of_leds=self.led_bar.number_of_leds,
        show_actions=self.led_bar.show_actions,
      )
    if self.rfid is None:
      rfid_block = self._unit_blocks.get("rfid")
      slot_to_antenna: Optional[Dict[int, int]] = None
      multiplexer_delay_raw = 0.0  # unused while slot_to_antenna is None: every read refuses
      if rfid_block is not None:
        try:
          slot_to_antenna = {
            int(slot): int(antenna) for slot, antenna in rfid_block["slot_to_antenna"].items()
          }
          multiplexer_delay_raw = float(rfid_block["multiplexer_delay_raw"])
        except (KeyError, TypeError, ValueError) as error:
          raise MantisConfigurationError(f"malformed rfid block: {error!r}") from error
      self.rfid = MantisSimulationRfid(
        driver=self, slot_to_antenna=slot_to_antenna, multiplexer_delay_raw=multiplexer_delay_raw
      )

  async def _open(self) -> None:
    """Open nothing: there is no link. The sequencer is stopped and empty."""
    logger.info("[%s] simulated, from %s", self.name, self.declared_configuration_json)
    self._next_item, self._last_drained_item, self._sequence_progress = 1, None, None
    self._queued_items, self._sequencer_started = [], False

  async def _close(self) -> None:
    """Close nothing: there is no link."""
