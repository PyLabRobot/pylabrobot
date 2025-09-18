# For direct control of the Opentrons Thermocycler to any USB port.
# Does not require an Opentrons liquid handler to use.

import asyncio
from typing import List, Optional

from pylabrobot.thermocycling.backend import ThermocyclerBackend
from pylabrobot.thermocycling.standard import (
  BlockStatus,
  LidStatus,
)
from pylabrobot.thermocycling.standard import (
  Protocol as ThermocyclingProtocol,
)

try:
  import serial.tools.list_ports
  from opentrons.drivers.thermocycler import ThermocyclerDriverFactory
  from opentrons.drivers.thermocycler.abstract import AbstractThermocyclerDriver
  from opentrons.drivers.types import PlateTemperature, Temperature, ThermocyclerLidStatus

  USE_OPENTRONS_DRIVER = True
except ImportError:
  USE_OPENTRONS_DRIVER = False


async def set_temperature_no_pause(
  driver,
  temperature: float,
  hold_time_seconds: Optional[float],
  hold_time_minutes: Optional[float],
  ramp_rate: Optional[float],
  volume: Optional[float],
) -> None:
  """Set temperature without waiting for completion (similar to thermocycler.py)."""
  seconds = hold_time_seconds if hold_time_seconds is not None else 0
  minutes = hold_time_minutes if hold_time_minutes is not None else 0
  total_seconds = seconds + (minutes * 60)
  hold_time = total_seconds if total_seconds > 0 else 0

  if ramp_rate is not None:
    await driver.set_ramp_rate(ramp_rate=ramp_rate)

  await driver.set_plate_temperature(temp=temperature, hold_time=hold_time, volume=volume)


async def wait_for_block_target_simple(driver) -> None:
  """Wait for block temperature to reach target (simplified version)."""
  max_attempts = 300  # 5 minutes max wait (300 * 1 second)
  attempt = 0

  while attempt < max_attempts:
    try:
      plate_temp = await driver.get_plate_temperature()
      if plate_temp.target is not None and abs(plate_temp.current - plate_temp.target) < 1.0:
        break
    except Exception as e:
      # Re-raise hardware errors immediately
      if "invalid thermistor" in str(e).lower() or "error" in str(e).lower():
        raise RuntimeError(f"Thermocycler hardware error: {e}")
      # For other errors, continue trying
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
  await wait_for_block_target_simple(driver)


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

  def __init__(self, port: Optional[str] = None):
    """Create a new USB backend bound to a specific port.

    If port is None, auto-detects the thermocycler using supported VID:PID pairs.
    If multiple devices are found, raises an error with available ports.

    If port is specified, use it directly.
    """
    super().__init__()
    if not USE_OPENTRONS_DRIVER:
      raise RuntimeError(
        "Opentrons drivers are not installed. Please install the opentrons package."
      )

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
        self.port = opentrons_ports[0].device
    else:
      self.port = port

    self.driver: Optional[AbstractThermocyclerDriver] = None
    self._current_protocol: Optional[ThermocyclingProtocol] = None
    self._loop: Optional[asyncio.AbstractEventLoop] = None

    # Cycle tracking variables (similar to thermocycler.py)
    self._total_cycle_count: Optional[int] = None
    self._current_cycle_index: Optional[int] = None
    self._total_step_count: Optional[int] = None
    self._current_step_index: Optional[int] = None

  def _clear_cycle_counters(self) -> None:
    """Clear the cycle counters."""
    self._total_cycle_count = None
    self._current_cycle_index = None
    self._total_step_count = None
    self._current_step_index = None

  async def _read_lid_temperature(self) -> Temperature:
    """Read the current lid temperature."""
    assert self.driver is not None
    return await self.driver.get_lid_temperature()

  async def _read_block_temperature(self) -> PlateTemperature:
    """Read the current block temperature."""
    assert self.driver is not None
    return await self.driver.get_plate_temperature()

  async def _read_lid_status(self) -> ThermocyclerLidStatus:
    """Read the current lid status."""
    assert self.driver is not None
    return await self.driver.get_lid_status()

  async def _set_temperature_no_pause(
    self,
    temperature: float,
    hold_time_seconds: Optional[float],
    hold_time_minutes: Optional[float],
    ramp_rate: Optional[float],
    volume: Optional[float],
  ) -> None:
    """Set temperature without waiting for completion (uses shared utility)."""
    assert self.driver is not None
    await set_temperature_no_pause(
      driver=self.driver,
      temperature=temperature,
      hold_time_seconds=hold_time_seconds,
      hold_time_minutes=hold_time_minutes,
      ramp_rate=ramp_rate,
      volume=volume,
    )
    await wait_for_block_target_simple(self.driver)

  async def _wait_for_block_target(self) -> None:
    """Wait for block temperature to reach target (uses shared utility)."""
    assert self.driver is not None
    await wait_for_block_target_simple(self.driver)

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
    steps: List[tuple],  # (temperature, hold_time, ramp_rate)
    repetitions: int,
    volume: Optional[float],
  ) -> None:
    """Execute cycles of temperature steps (uses shared utility with cycle tracking)."""
    assert self.driver is not None
    for rep in range(repetitions):
      self._current_cycle_index = rep + 1  # science starts at 1
      for step_idx, (temperature, hold_time, ramp_rate) in enumerate(steps):
        self._current_step_index = step_idx + 1  # science starts at 1
        await execute_cycle_step(
          driver=self.driver,
          temperature=temperature,
          hold_time_seconds=hold_time,
          ramp_rate=ramp_rate,
          volume=volume,
        )

  def _convert_protocol_to_steps(
    self, protocol: ThermocyclingProtocol, block_max_volume: float
  ) -> List[tuple]:
    """Convert pylabrobot Protocol to list of (temperature, hold_time, ramp_rate) tuples."""
    steps = []
    for stage in protocol.stages:
      for _ in range(stage.repeats):  # Repeat the entire stage
        for step in stage.steps:
          if len(set(step.temperature)) != 1:
            raise ValueError(
              f"Opentrons thermocycler only supports a single unique temperature per step, got {set(step.temperature)}"
            )
          temperature = step.temperature[0]
          hold_time = step.hold_seconds
          ramp_rate = step.rate if step.rate is not None else None
          steps.append((temperature, hold_time, ramp_rate))
    return steps

  async def run_protocol(self, protocol: ThermocyclingProtocol, block_max_volume: float):
    """Execute thermocycler protocol using sophisticated execution logic from thermocycler.py.

    This implementation supports:
    - Multiple stages with repeats
    - Individual step tracking
    - Cycle counting
    - Ramp rate control
    - Proper temperature waiting
    """
    # Convert protocol to execution format
    steps = self._convert_protocol_to_steps(protocol, block_max_volume)

    # Initialize cycle tracking
    self._total_cycle_count = 1  # Single execution of the entire protocol
    self._total_step_count = len(steps)
    self._current_cycle_index = 1
    self._current_step_index = 0

    try:
      # Execute all steps as one cycle
      await self._execute_cycles(
        steps=steps,
        repetitions=1,  # Protocol is executed once
        volume=block_max_volume,
      )
    finally:
      self._clear_cycle_counters()

    self._current_protocol = protocol

  async def setup(self):
    """Setup the USB connection to the thermocycler."""
    if self._loop is None:
      self._loop = asyncio.get_event_loop()
    self.driver = await ThermocyclerDriverFactory.create(self.port, self._loop)
    assert self.driver is not None

  async def stop(self):
    """Gracefully deactivate both heaters and close connection."""
    if self.driver is not None:
      await self.deactivate_block()
      await self.deactivate_lid()
      await self.driver.disconnect()

  def serialize(self) -> dict:
    """Include the USB port in serialized state."""
    return {**super().serialize(), "port": self.port}

  async def open_lid(self):
    """Open the thermocycler lid."""
    assert self.driver is not None
    await self.driver.open_lid()

  async def close_lid(self):
    """Close the thermocycler lid."""
    assert self.driver is not None
    await self.driver.close_lid()

  async def lift_plate(self):
    """Lift the thermocycler plate for labware access."""
    assert self.driver is not None
    await self.driver.lift_plate()

  async def jog_lid(self, angle: float):
    """Jog the lid to a specific angle position."""
    assert self.driver is not None
    await self.driver.jog_lid(angle)

  async def set_block_temperature(self, temperature: List[float]):
    """Set block temperature in °C. Only single unique temperature supported."""
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
    """Get thermocycler device information."""
    assert self.driver is not None
    return await self.driver.get_device_info()

  async def get_block_current_temperature(self) -> List[float]:
    """Get the current block temperature."""
    assert self.driver is not None
    plate_temp = await self.driver.get_plate_temperature()
    return [plate_temp.current]

  async def get_block_target_temperature(self) -> List[float]:
    """Get the block target temperature."""
    assert self.driver is not None
    plate_temp = await self.driver.get_plate_temperature()
    if plate_temp.target is not None:
      return [plate_temp.target]
    raise RuntimeError("Block target temperature is not set.")

  async def get_lid_current_temperature(self) -> List[float]:
    """Get the current lid temperature."""
    assert self.driver is not None
    lid_temp = await self.driver.get_lid_temperature()
    return [lid_temp.current]

  async def get_lid_target_temperature(self) -> List[float]:
    """Get the lid target temperature."""
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
    """Get the lid temperature status."""
    assert self.driver is not None
    lid_temp = await self.driver.get_lid_temperature()
    if lid_temp.target is not None and abs(lid_temp.current - lid_temp.target) < 1.0:
      return LidStatus.HOLDING_AT_TARGET
    return LidStatus.IDLE

  async def get_block_status(self) -> BlockStatus:
    """Get the block temperature status."""
    assert self.driver is not None
    plate_temp = await self.driver.get_plate_temperature()
    if plate_temp.target is not None and abs(plate_temp.current - plate_temp.target) < 1.0:
      return BlockStatus.HOLDING_AT_TARGET
    return BlockStatus.IDLE

  async def get_hold_time(self) -> float:
    """Get remaining hold time in seconds."""
    # USB driver doesn't provide hold time information
    return 0.0

  async def get_current_cycle_index(self) -> int:
    """Get the zero-based index of the current cycle."""
    if self._current_cycle_index is not None:
      return self._current_cycle_index
    raise RuntimeError("No cycle is currently running")

  async def get_total_cycle_count(self) -> int:
    """Get the total cycle count."""
    if self._total_cycle_count is not None:
      return self._total_cycle_count
    raise RuntimeError("No protocol has been run")

  async def get_current_step_index(self) -> int:
    """Get the zero-based index of the current step within the cycle."""
    if self._current_step_index is not None:
      return self._current_step_index
    raise RuntimeError("No step is currently running")

  async def get_total_step_count(self) -> int:
    """Get the total number of steps in the current cycle."""
    if self._total_step_count is not None:
      return self._total_step_count
    raise RuntimeError("No protocol has been run")

  @property
  def total_cycle_count(self) -> Optional[int]:
    """Get the total cycle count."""
    return self._total_cycle_count

  @property
  def current_cycle_index(self) -> Optional[int]:
    """Get the current cycle index."""
    return self._current_cycle_index

  @property
  def total_step_count(self) -> Optional[int]:
    """Get the total step count."""
    return self._total_step_count

  @property
  def current_step_index(self) -> Optional[int]:
    """Get the current step index."""
    return self._current_step_index
