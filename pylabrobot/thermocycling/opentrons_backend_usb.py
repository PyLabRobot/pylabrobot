# For direct control of the Opentrons Thermocycler to any USB port.
# Does not require an Opentrons liquid handler to use.

import asyncio
from typing import List, Optional

from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import (
  BlockStatus,
  LidStatus,
  Protocol,
)

try:
  import serial.tools.list_ports
  from opentrons.drivers.thermocycler import ThermocyclerDriverFactory
  from opentrons.drivers.thermocycler.abstract import AbstractThermocyclerDriver
  from opentrons.drivers.types import PlateTemperature, Temperature, ThermocyclerLidStatus

  USE_OPENTRONS_DRIVER = True
  _import_error = None
except ImportError as e:
  USE_OPENTRONS_DRIVER = False
  _import_error = e


async def set_temperature_no_pause(
  driver,
  temperature: float,
  hold_time_seconds: Optional[float],
  hold_time_minutes: Optional[float],
  ramp_rate: Optional[float],
  volume: Optional[float],
) -> None:
  """Set temperature without waiting for completion."""
  seconds = hold_time_seconds if hold_time_seconds is not None else 0
  minutes = hold_time_minutes if hold_time_minutes is not None else 0
  total_seconds = seconds + (minutes * 60)
  hold_time = total_seconds if total_seconds > 0 else 0

  if ramp_rate is not None:
    await driver.set_ramp_rate(ramp_rate=ramp_rate)

  await driver.set_plate_temperature(temp=temperature, hold_time=hold_time, volume=volume)


async def wait_for_block_target(driver) -> None:
  """Wait for block temperature to reach target."""
  max_attempts = 300  # 5 minutes max wait (300 * 1 second)
  attempt = 0

  while attempt < max_attempts:
    try:
      plate_temp = await driver.get_plate_temperature()
      if plate_temp.target is not None and abs(plate_temp.current - plate_temp.target) < 1.0:
        break
    except Exception as e:
      if "invalid thermistor" in str(e).lower() or "error" in str(e).lower():
        raise RuntimeError(f"Thermocycler hardware error: {e}")
      print(f"Temperature check failed (attempt {attempt + 1}), retrying: {e}")
    attempt += 1
    await asyncio.sleep(1.0)
  else:
    raise TimeoutError(f"Temperature did not reach target within {max_attempts} seconds")


async def execute_cycle_step(
  driver,
  temperature: float,
  hold_time_seconds: float,
  ramp_rate: Optional[float] = None,
  volume: Optional[float] = None,
) -> None:
  """Execute a single thermocycler step."""
  await set_temperature_no_pause(
    driver=driver,
    temperature=temperature,
    hold_time_seconds=hold_time_seconds,
    hold_time_minutes=None,
    ramp_rate=ramp_rate,
    volume=volume,
  )
  await wait_for_block_target(driver)


async def execute_cycles(
  driver,
  steps: List[tuple],  # (temperature, hold_time, ramp_rate)
  repetitions: int,
  volume: Optional[float],
) -> None:
  """Execute cycles of temperature steps."""
  for rep in range(repetitions):
    for temperature, hold_time, ramp_rate in steps:
      await execute_cycle_step(
        driver=driver,
        temperature=temperature,
        hold_time_seconds=hold_time,
        ramp_rate=ramp_rate,
        volume=volume,
      )


class OpentronsThermocyclerUSBBackend(ThermocyclerBackend):
  """USB backend for the Opentrons GEN-1/GEN-2 Thermocycler."""

  SUPPORTED_USB_IDS = {
    (0x04D8, 0xED8C),  # legacy Microchip bridge
    (0x0483, 0xED8D),  # STMicroelectronics bridge seen in newer units
  }

  def __init__(self):
    """Create a new USB backend."""
    super().__init__()
    if not USE_OPENTRONS_DRIVER:
      raise _import_error

    self.driver: Optional[AbstractThermocyclerDriver] = None
    self._current_protocol: Optional[Protocol] = None
    self._loop: Optional[asyncio.AbstractEventLoop] = None

    self._total_cycle_count: Optional[int] = None
    self._current_cycle_index: Optional[int] = None
    self._total_step_count: Optional[int] = None
    self._current_step_index: Optional[int] = None

  def _clear_cycle_counters(self) -> None:
    self._total_cycle_count = None
    self._current_cycle_index = None
    self._total_step_count = None
    self._current_step_index = None

  async def _execute_cycle_step(
    self,
    temperature: float,
    hold_time_seconds: float,
    ramp_rate: Optional[float] = None,
    volume: Optional[float] = None,
  ) -> None:
    """Execute a single thermocycler step (uses shared utility)."""
    assert self.driver is not None
    await execute_cycle_step(
      driver=self.driver,
      temperature=temperature,
      hold_time_seconds=hold_time_seconds,
      ramp_rate=ramp_rate,
      volume=volume,
    )

  async def _execute_cycles(
    self,
    protocol: Protocol,
    repetitions: int,
    volume: Optional[float],
  ) -> None:
    """Execute cycles of temperature steps directly from protocol (with cycle tracking)."""
    assert self.driver is not None
    self._total_cycle_count = repetitions
    total_steps = 0
    for stage in protocol.stages:
      for _ in range(stage.repeats):
        for step in stage.steps:
          total_steps += 1
    self._total_step_count = total_steps

    step_index = 0
    for rep in range(repetitions):
      self._current_cycle_index = rep + 1
      for stage in protocol.stages:
        for _ in range(stage.repeats):
          for step in stage.steps:
            if len(set(step.temperature)) != 1:
              raise ValueError(
                f"Opentrons thermocycler only supports a single unique temperature per step, got {set(step.temperature)}"
              )
            temperature = step.temperature[0]
            hold_time = step.hold_seconds
            ramp_rate = step.rate if step.rate is not None else None
            step_index += 1
            self._current_step_index = step_index
            await execute_cycle_step(
              driver=self.driver,
              temperature=temperature,
              hold_time_seconds=hold_time,
              ramp_rate=ramp_rate,
              volume=volume,
            )

  async def run_protocol(self, protocol: Protocol, block_max_volume: float):
    """Execute thermocycler protocol using similar execution logic from thermocycler.py.

    Implements specific to opentrons thermocycler:
    - Multiple stages with repeats
    - Individual step tracking
    - Cycle counting
    - Ramp rate control
    - Temperature waiting
    """
    try:
      # Execute all steps as one cycle
      await self._execute_cycles(
        protocol=protocol,
        repetitions=1,  # Protocol is executed once
        volume=block_max_volume,
      )
    finally:
      self._clear_cycle_counters()

    self._current_protocol = protocol

  async def setup(self, port: Optional[str] = None):
    """Setup the USB connection to the thermocycler."""
    if self._loop is None:
      self._loop = asyncio.get_event_loop()

    if port is None:
      ports = serial.tools.list_ports.comports()
      opentrons_ports = [p for p in ports if (p.vid, p.pid) in self.SUPPORTED_USB_IDS]
      if len(opentrons_ports) == 0:
        raise RuntimeError(
          f"No Opentrons Thermocycler found with supported VID:PID pairs: {self.SUPPORTED_USB_IDS}"
        )
      elif len(opentrons_ports) > 1:
        available_ports = [p.device for p in opentrons_ports]
        raise RuntimeError(
          f"Multiple Opentrons Thermocyclers found: {available_ports}. Please specify the port explicitly."
        )
      else:
        port = opentrons_ports[0].device

    self.driver = await ThermocyclerDriverFactory.create(port, self._loop)
    assert self.driver is not None

  async def stop(self):
    if self.driver is not None:
      await self.deactivate_block()
      await self.deactivate_lid()
      await self.driver.disconnect()

  async def open_lid(self):
    assert self.driver is not None
    await self.driver.open_lid()

  async def close_lid(self):
    assert self.driver is not None
    await self.driver.close_lid()

  async def lift_plate(self):
    """Lift the thermocycler plate to un-stick and robustly pick up with robot arm."""
    assert self.driver is not None
    await self.driver.lift_plate()

  async def jog_lid(self, angle: float):
    """Jog the lid to a specific angle position."""
    assert self.driver is not None
    await self.driver.jog_lid(angle)

  async def set_block_temperature(self, temperature: List[float]):
    """Set block temperature in °C. Only single unique temperature supported.
    use set_ramp_rate inependently to control ramp rate to determined temperature
    """
    if len(set(temperature)) != 1:
      raise ValueError(
        f"Opentrons thermocycler only supports a single unique block temperature, got {set(temperature)}"
      )
    temp_value = temperature[0]
    assert self.driver is not None
    await self.driver.set_plate_temperature(temp_value)

  async def set_lid_temperature(self, temperature: List[float]):
    """Set lid temperature in °C. Only single unique temperature supported."""
    if len(set(temperature)) != 1:
      raise ValueError(
        f"Opentrons thermocycler only supports a single unique lid temperature, got {set(temperature)}"
      )
    temp_value = temperature[0]
    assert self.driver is not None
    await self.driver.set_lid_temperature(temp_value)

  async def set_ramp_rate(self, ramp_rate: float):
    """Set the temperature ramp rate in °C/minute."""
    assert self.driver is not None
    await self.driver.set_ramp_rate(ramp_rate)

  async def deactivate_block(self):
    """Deactivate the block heater."""
    assert self.driver is not None
    await self.driver.deactivate_block()

  async def deactivate_lid(self):
    """Deactivate the lid heater."""
    assert self.driver is not None
    await self.driver.deactivate_lid()

  async def get_device_info(self) -> dict:
    assert self.driver is not None
    return await self.driver.get_device_info()

  async def get_block_current_temperature(self) -> List[float]:
    assert self.driver is not None
    plate_temp = await self.driver.get_plate_temperature()
    return [plate_temp.current]

  async def get_block_target_temperature(self) -> List[float]:
    assert self.driver is not None
    plate_temp = await self.driver.get_plate_temperature()
    if plate_temp.target is not None:
      return [plate_temp.target]
    raise RuntimeError("Block target temperature is not set.")

  async def get_lid_current_temperature(self) -> List[float]:
    assert self.driver is not None
    lid_temp = await self.driver.get_lid_temperature()
    return [lid_temp.current]

  async def get_lid_target_temperature(self) -> List[float]:
    assert self.driver is not None
    lid_temp = await self.driver.get_lid_temperature()
    if lid_temp.target is not None:
      return [lid_temp.target]
    raise RuntimeError("Lid target temperature is not set.")

  async def get_lid_open(self) -> bool:
    """Return True if the lid is open."""
    assert self.driver is not None
    lid_status = await self.driver.get_lid_status()
    return lid_status == ThermocyclerLidStatus.OPEN

  async def get_lid_status(self) -> LidStatus:
    assert self.driver is not None
    lid_temp = await self.driver.get_lid_temperature()
    if lid_temp.target is not None and abs(lid_temp.current - lid_temp.target) < 1.0:
      return LidStatus.HOLDING_AT_TARGET
    return LidStatus.IDLE

  async def get_block_status(self) -> BlockStatus:
    assert self.driver is not None
    plate_temp = await self.driver.get_plate_temperature()
    if plate_temp.target is not None and abs(plate_temp.current - plate_temp.target) < 1.0:
      return BlockStatus.HOLDING_AT_TARGET
    return BlockStatus.IDLE

  async def get_hold_time(self) -> float:
    raise NotImplementedError("USB driver doesn't provide hold time information")

  async def get_current_cycle_index(self) -> int:
    return self._current_cycle_index if self._current_cycle_index is not None else 0

  async def get_total_cycle_count(self) -> int:
    return self._total_cycle_count if self._total_cycle_count is not None else 0

  async def get_current_step_index(self) -> int:
    return self._current_step_index if self._current_step_index is not None else 0

  async def get_total_step_count(self) -> int:
    return self._total_step_count if self._total_step_count is not None else 0
