import asyncio
import logging
from typing import Optional, Union

from pylabrobot.capabilities.shaking import Shaker, ShakerBackend
from pylabrobot.capabilities.temperature_controlling import (
  TemperatureController,
  TemperatureControllerBackend,
)
from pylabrobot.device import Device, Driver
from pylabrobot.io.serial import Serial
from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import PlateHolder

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

logger = logging.getLogger(__name__)


class BioShakeDriver(Driver):
  """Serial driver for QInstruments BioShake devices.

  Owns the serial connection, command protocol, and device-level operations
  (reset, home) that don't belong to any capability.
  """

  def __init__(self, port: str, timeout: int = 60):
    super().__init__()
    if not HAS_SERIAL:
      raise RuntimeError(f"pyserial is required for BioShake. Import error: {_SERIAL_IMPORT_ERROR}")

    self.port = port
    self.timeout = timeout
    self.io = Serial(
      human_readable_device_name="QInstruments BioShake",
      port=self.port,
      baudrate=9600,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=10,
      timeout=self.timeout,
    )

  async def send_command(self, cmd: str, delay: float = 0.5, timeout: float = 2):
    try:
      await self.io.reset_input_buffer()
      await self.io.reset_output_buffer()

      await self.io.write((cmd + "\r").encode("ascii"))
      await asyncio.sleep(delay)

      try:
        response = await asyncio.wait_for(self.io.readline(), timeout=timeout)
      except asyncio.TimeoutError:
        raise RuntimeError(f"Timed out waiting for response to '{cmd}'")

      decoded = response.decode("ascii", errors="ignore").strip()

      if not decoded:
        raise RuntimeError(f"No response for '{cmd}'")

      if decoded.startswith("e"):
        logger.error("[BioShake %s] error for '%s': '%s'", self.port, cmd, decoded)
        raise RuntimeError(f"Device returned error for '{cmd}': '{decoded}'")

      if decoded.startswith("u ->"):
        raise NotImplementedError(f"'{cmd}' not supported: '{decoded}'")

      if decoded.lower().startswith("ok"):
        return None

      return decoded

    except Exception as e:
      raise RuntimeError(f"Unexpected error while sending '{cmd}': {type(e).__name__}: {e}") from e

  async def setup(self, skip_home: bool = False):
    await self.io.setup()
    if not skip_home:
      await self.reset()
      await asyncio.sleep(4)
      await self.home()
    logger.info("[BioShake %s] connected", self.port)

  async def stop(self):
    await self.io.stop()
    logger.info("[BioShake %s] disconnected", self.port)

  async def reset(self):
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()

    await self.io.write(("resetDevice\r").encode("ascii"))

    start = asyncio.get_event_loop().time()
    max_seconds = 30

    while True:
      if asyncio.get_event_loop().time() - start > max_seconds:
        raise TimeoutError("Reset did not complete in time")

      try:
        response = await asyncio.wait_for(self.io.readline(), timeout=2)
        decoded = response.decode("ascii", errors="ignore").strip()
        await asyncio.sleep(0.1)

        if len(decoded) > 0:
          if "Initialization complete" in decoded:
            break

      except asyncio.TimeoutError:
        continue

  async def home(self):
    await self.send_command(cmd="shakeGoHome", delay=5)


class BioShakeShakerBackend(ShakerBackend):
  """Translates ShakerBackend calls into BioShake serial commands."""

  def __init__(self, driver: BioShakeDriver):
    self.driver = driver

  async def start_shaking(self, speed: float, acceleration: Union[int, float] = 0):
    if isinstance(speed, float):
      if not speed.is_integer():
        raise ValueError(f"Speed must be a whole number, not {speed}")
      speed = int(speed)
    if not isinstance(speed, int):
      raise TypeError(
        f"Speed must be an integer or a whole number float, not {type(speed).__name__}"
      )

    min_speed = int(float(await self.driver.send_command(cmd="getShakeMinRpm", delay=0.2)))
    max_speed = int(float(await self.driver.send_command(cmd="getShakeMaxRpm", delay=0.2)))

    if not (min_speed <= speed <= max_speed):
      raise ValueError(
        f"Speed {speed} RPM is out of range. Allowed range is {min_speed}-{max_speed} RPM"
      )

    await self.driver.send_command(cmd=f"setShakeTargetSpeed{speed}")

    if isinstance(acceleration, float):
      if not acceleration.is_integer():
        raise ValueError(f"Acceleration must be a whole number, not {acceleration}")
      acceleration = int(acceleration)
    if not isinstance(acceleration, int):
      raise TypeError(
        f"Acceleration must be an integer or a whole number float, not {type(acceleration).__name__}"
      )

    min_accel = int(float(await self.driver.send_command(cmd="getShakeAccelerationMin", delay=0.2)))
    max_accel = int(float(await self.driver.send_command(cmd="getShakeAccelerationMax", delay=0.2)))

    if not (min_accel <= acceleration <= max_accel):
      raise ValueError(
        f"Acceleration {acceleration} seconds is out of range. "
        f"Allowed range is {min_accel}-{max_accel} seconds"
      )

    await self.driver.send_command(cmd=f"setShakeAcceleration{acceleration}", delay=0.2)
    logger.info("[BioShake %s] start shaking: speed=%d, accel=%d", self.driver.port, speed, acceleration)
    await self.driver.send_command(cmd="shakeOn", delay=0.2)

  async def stop_shaking(self, deceleration: Union[int, float] = 0):
    if isinstance(deceleration, float):
      if not deceleration.is_integer():
        raise ValueError(f"Deceleration must be a whole number, not {deceleration}")
      deceleration = int(deceleration)
    if not isinstance(deceleration, int):
      raise TypeError(
        f"Deceleration must be an integer or a whole number float, "
        f"not {type(deceleration).__name__}"
      )

    min_decel = int(float(await self.driver.send_command(cmd="getShakeAccelerationMin", delay=0.2)))
    max_decel = int(float(await self.driver.send_command(cmd="getShakeAccelerationMax", delay=0.2)))

    if not (min_decel <= deceleration <= max_decel):
      raise ValueError(
        f"Deceleration {deceleration} seconds is out of range. "
        f"Allowed range is {min_decel}-{max_decel} seconds"
      )

    await self.driver.send_command(cmd=f"setShakeAcceleration{deceleration}", delay=0.2)
    logger.info("[BioShake %s] stop shaking (decel=%d)", self.driver.port, deceleration)
    await self.driver.send_command(cmd="shakeOff", delay=0.2)

    # The firmware needs the motor to fully decelerate before ELM can operate.
    await asyncio.sleep(3)

  @property
  def supports_locking(self) -> bool:
    return True

  async def lock_plate(self):
    logger.info("[BioShake %s] lock plate", self.driver.port)
    await self.driver.send_command(cmd="setElmLockPos", delay=0.3)

  async def unlock_plate(self):
    logger.info("[BioShake %s] unlock plate", self.driver.port)
    await self.driver.send_command(cmd="setElmUnlockPos", delay=0.3)


class BioShakeTemperatureBackend(TemperatureControllerBackend):
  """Translates TemperatureControllerBackend calls into BioShake serial commands."""

  def __init__(self, driver: BioShakeDriver, supports_active_cooling: bool = False):
    self.driver = driver
    self._supports_active_cooling = supports_active_cooling

  @property
  def supports_active_cooling(self) -> bool:
    return self._supports_active_cooling

  async def set_temperature(self, temperature: float):
    min_temp = int(float(await self.driver.send_command(cmd="getTempMin", delay=0.2)))
    max_temp = int(float(await self.driver.send_command(cmd="getTempMax", delay=0.2)))

    if not (min_temp <= temperature <= max_temp):
      raise ValueError(
        f"Temperature {temperature} C is out of range. Allowed range is {min_temp}-{max_temp} C."
      )

    temperature_tenths = temperature * 10

    if isinstance(temperature_tenths, float):
      if not temperature_tenths.is_integer():
        raise ValueError(f"Temperature must be a whole number in 1/10 C, not {temperature_tenths}")
      temperature_tenths = int(temperature_tenths)

    logger.info("[BioShake %s] setting temperature to %.1f C", self.driver.port, temperature)
    await self.driver.send_command(cmd=f"setTempTarget{temperature_tenths}", delay=0.2)
    await self.driver.send_command(cmd="tempOn", delay=0.2)

  async def request_current_temperature(self) -> float:
    response = await self.driver.send_command(cmd="getTempActual", delay=0.2)
    temp = float(response)
    logger.info("[BioShake %s] read temperature: actual=%.1f C", self.driver.port, temp)
    return temp

  async def deactivate(self):
    logger.info("[BioShake %s] deactivating temperature", self.driver.port)
    await self.driver.send_command(cmd="tempOff", delay=0.2)


class BioShake(PlateHolder, Device):
  """QInstruments BioShake device.

  Use a model-specific factory function (e.g. ``BioShake3000``) to create instances.
  ``shaker`` and ``tc`` are ``None`` when the hardware doesn't support the capability.
  """

  def __init__(
    self,
    name: str,
    port: str,
    size_x: float,
    size_y: float,
    size_z: float,
    child_location: Coordinate,
    pedestal_size_z: float,
    has_shaking: bool = False,
    has_temperature: bool = False,
    supports_active_cooling: bool = False,
    category: str = "bioshake",
    model: Optional[str] = None,
  ):
    driver = BioShakeDriver(port=port)
    PlateHolder.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      child_location=child_location,
      pedestal_size_z=pedestal_size_z,
      category=category,
      model=model,
    )
    Device.__init__(self, driver=driver)
    self.driver: BioShakeDriver = driver

    self.shaker: Optional[Shaker] = None
    self.tc: Optional[TemperatureController] = None
    self._capabilities = []

    if has_shaking:
      self.shaker = Shaker(backend=BioShakeShakerBackend(driver))
      self._capabilities.append(self.shaker)
    if has_temperature:
      self.tc = TemperatureController(
        backend=BioShakeTemperatureBackend(driver, supports_active_cooling=supports_active_cooling)
      )
      self._capabilities.append(self.tc)

  def serialize(self) -> dict:
    return {
      **Device.serialize(self),
      **PlateHolder.serialize(self),
    }


# -- Factory functions for specific models --


def BioShake3000(name: str, port: str) -> BioShake:
  """BioShake 3000 - shaking 200-3000 rpm, no ELM, no heating."""
  return BioShake(
    name=name,
    port=port,
    size_x=142,  # from spec
    size_y=99,  # from spec
    size_z=60.67,  # from spec
    child_location=Coordinate(7.12, 6.76, 51.75),  # from spec
    pedestal_size_z=0,
    has_shaking=True,
    model=BioShake3000.__name__,
  )


def BioShake3000Elm(name: str, port: str) -> BioShake:
  """BioShake 3000 elm - shaking 200-3000 rpm, ELM, no heating."""
  return BioShake(
    name=name,
    port=port,
    size_x=142,  # from spec
    size_y=99,  # from spec
    size_z=55.35,  # from spec
    child_location=Coordinate(7.12, 6.76, 48.20),  # from spec
    pedestal_size_z=0,
    has_shaking=True,
    model=BioShake3000Elm.__name__,
  )


def BioShake3000ElmDWP(name: str, port: str) -> BioShake:
  """BioShake 3000 elm DWP - shaking 200-3000 rpm, ELM, no heating."""
  return BioShake(
    name=name,
    port=port,
    size_x=142,  # from spec
    size_y=99,  # from spec
    size_z=55.35,  # from spec
    child_location=Coordinate(7.12, 6.76, 48.20),  # from spec
    pedestal_size_z=0,
    has_shaking=True,
    model=BioShake3000ElmDWP.__name__,
  )


def BioShakeD30Elm(name: str, port: str) -> BioShake:
  """BioShake D30 elm - shaking 200-2000 rpm, ELM, no heating."""
  raise NotImplementedError("BioShakeD30Elm is missing resource definition.")


def BioShake5000Elm(name: str, port: str) -> BioShake:
  """BioShake 5000 elm - shaking 200-5000 rpm, ELM, no heating."""
  raise NotImplementedError("BioShake5000Elm is missing resource definition.")


def BioShake3000T(name: str, port: str) -> BioShake:
  """BioShake 3000-T - shaking 200-3000 rpm, no ELM, heating."""
  raise NotImplementedError("BioShake3000T is missing resource definition.")


def BioShake3000TElm(name: str, port: str) -> BioShake:
  """BioShake 3000-T elm - shaking 200-3000 rpm, ELM, heating."""
  raise NotImplementedError("BioShake3000TElm is missing resource definition.")


def BioShakeD30TElm(name: str, port: str) -> BioShake:
  """BioShake D30-T elm - shaking 200-2000 rpm, ELM, heating."""
  raise NotImplementedError("BioShakeD30TElm is missing resource definition.")


def BioShakeQ1(name: str, port: str) -> BioShake:
  """BioShake Q1 - shaking 200-3000 rpm, ELM, heating, active cooling.

  Dimensions defined with microplate adapter #2016-1024 (flat bottom standard).
  """
  return BioShake(
    name=name,
    port=port,
    size_x=142,  # from spec
    size_y=99,  # from spec
    size_z=97.30,  # from spec
    child_location=Coordinate(7.12, 6.76, 90.50),  # from spec
    pedestal_size_z=0,
    has_shaking=True,
    has_temperature=True,
    supports_active_cooling=True,
    model=BioShakeQ1.__name__,
  )


def BioShakeQ2(name: str, port: str) -> BioShake:
  """BioShake Q2 - shaking 200-3000 rpm, ELM, heating, active cooling."""
  raise NotImplementedError("BioShakeQ2 is missing resource definition.")


def Heatplate(name: str, port: str) -> BioShake:
  """Heatplate - no shaking, heating only."""
  raise NotImplementedError("Heatplate is missing resource definition.")


def ColdPlate(name: str, port: str) -> BioShake:
  """ColdPlate - no shaking, heating, active cooling."""
  raise NotImplementedError("ColdPlate is missing resource definition.")
