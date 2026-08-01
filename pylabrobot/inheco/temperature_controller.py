import logging

from pylabrobot.inheco.control_box import InhecoTECControlBox

logger = logging.getLogger(__name__)


class InhecoTemperatureController:
  def __init__(self, index: int, interface: InhecoTECControlBox):
    self.index = index
    self.interface = interface
    if not (1 <= index <= 6):
      raise ValueError("Index must be between 1 and 6 (inclusive)")

  @property
  def supports_active_cooling(self) -> bool:
    return True

  # -- temperature control

  async def set_temperature(self, temperature: float):
    logger.info("[Inheco idx=%d] setting temperature to %.1f C", self.index, temperature)
    await self._set_target_temperature(temperature)
    await self._start_temperature_control()

  async def request_current_temperature(self) -> float:
    response = await self.interface.send_command(f"{self.index}RAT0")
    temp = float(response) / 10
    logger.info("[Inheco idx=%d] read temperature: actual=%.1f C", self.index, temp)
    return temp

  async def stop_temperature_control(self):
    """Stop the temperature control"""
    logger.info("[Inheco idx=%d] stopping temperature control", self.index)
    return await self.interface.send_command(f"{self.index}ATE0")

  # --- firmware temp

  async def _set_target_temperature(self, temperature: float):
    temperature = int(temperature * 10)
    await self.interface.send_command(f"{self.index}STT{temperature}")

  async def _start_temperature_control(self):
    return await self.interface.send_command(f"{self.index}ATE1")

  # --- firmware misc

  async def request_device_info(self, info_type: int):
    """Get device information

    - 0 Bootstrap Version
    - 1 Application Version
    - 2 Serial number
    - 3 Current hardware version
    - 4 INHECO copyright
    """

    if info_type not in range(5):
      raise ValueError("Info type must be in the range 0 to 4")
    return await self.interface.send_command(f"{self.index}RFV{info_type}")
