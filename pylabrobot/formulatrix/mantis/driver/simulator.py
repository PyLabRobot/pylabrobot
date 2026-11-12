"""A simulated Mantis: the driver with no device behind it, answering from its configuration."""

import logging
from typing import Dict, List, Optional, Tuple

from pylabrobot.io.ftdi import FTDI

from .configuration import (
  DeviceConfiguration,
  compute_generation,
  configuration_from_json,
  read_configuration,
)
from .errors import MantisConfigurationError, MantisTimeoutError
from .features.plate_clamp import CLAMPING_DONE, UNCLAMPING_DONE, PlateClamp
from .features.rfid import Rfid
from .framing import ABSOLUTE_POSITION_SENSOR_BOARD, LC3_BOARD, MOTION_BOARD, PRESSURE_BOARD
from .master import MOTORS, MantisDriver

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


class MantisSimulationRfid(Rfid):
  """An RFID reader with no device behind it: every slot reads empty."""

  async def _unchecked_fw_write_ppi(self, *, start_address: int, values: List[int]) -> None:
    """Select nothing: there is no multiplexer."""

  async def _read_tag(self) -> Optional[bytes]:
    """No tag: every slot is empty."""
    return None


class MantisSimulationDriver(MantisDriver):
  """A Mantis driver with no device behind it.

  Nothing reaches a wire. The configuration comes from ``declared_configuration_json``, and the
  reads answer from it; a board it leaves ``None`` does not answer. The plate clamp's state changes
  at once, and every RFID slot reads empty. The device name is not simulated.
  """

  def __init__(
    self,
    declared_configuration_json: str,
    name: str = "Mantis",
    *,
    oem_config_directory: Optional[str] = None,
  ):
    """
    Args:
      declared_configuration_json: a file ``save_configuration`` wrote; the simulated device's
        configuration.
      name: the device name, heading its log messages.
      oem_config_directory: a unit's vendor ``Data/Device/Configs`` folder, as for a device.
    """
    super().__init__(
      name=name,
      declared_configuration_json=declared_configuration_json,
      oem_config_directory=oem_config_directory,
    )

  def _build_io(self, *, device_id: Optional[str]) -> FTDI:
    """Build no link."""
    return None  # type: ignore[return-value]  # no link: nothing reaches a wire

  def _build_rfid_io(self, *, device_id: Optional[str]) -> Optional[FTDI]:
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

  async def _request_configuration(self) -> DeviceConfiguration:
    """The declared configuration's ``device`` block; the generation from its boards, as a
    device's is."""
    configuration = configuration_from_json(
      fields=read_configuration(path=self.declared_configuration_json or "")["device"]
    )
    configuration.generation = compute_generation(firmware_versions=configuration.firmware_versions)
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
    """Open nothing: there is no link."""
    logger.info("[%s] simulated, from %s", self.name, self.declared_configuration_json)

  async def _close(self) -> None:
    """Close nothing: there is no link."""
