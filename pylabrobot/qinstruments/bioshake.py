import asyncio
from typing import Optional, Union

from pylabrobot.capabilities.shaking import ShakerBackend, ShakingCapability
from pylabrobot.capabilities.temperature_controlling import (
  TemperatureControlCapability,
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


class BioShakeDriver(Driver):
  """Serial driver for QInstruments BioShake devices.

  Owns the serial connection, command protocol, and device-level operations
  (reset, home) that don't belong to any capability.
  """

  def __init__(self, port: str, timeout: int = 60):
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

  async def stop(self):
    await self.io.stop()

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
    self._driver = driver

  async def start_shaking(self, speed: float, acceleration: Union[int, float] = 0):
    if isinstance(speed, float):
      if not speed.is_integer():
        raise ValueError(f"Speed must be a whole number, not {speed}")
      speed = int(speed)
    if not isinstance(speed, int):
      raise TypeError(
        f"Speed must be an integer or a whole number float, not {type(speed).__name__}"
      )

    min_speed = int(float(await self._driver.send_command(cmd="getShakeMinRpm", delay=0.2)))
    max_speed = int(float(await self._driver.send_command(cmd="getShakeMaxRpm", delay=0.2)))

    assert min_speed <= speed <= max_speed, (
      f"Speed {speed} RPM is out of range. Allowed range is {min_speed}-{max_speed} RPM"
    )

    await self._driver.send_command(cmd=f"setShakeTargetSpeed{speed}")

    if isinstance(acceleration, float):
      if not acceleration.is_integer():
        raise ValueError(f"Acceleration must be a whole number, not {acceleration}")
      acceleration = int(acceleration)
    if not isinstance(acceleration, int):
      raise TypeError(
        f"Acceleration must be an integer or a whole number float, not {type(acceleration).__name__}"
      )

    min_accel = int(
      float(await self._driver.send_command(cmd="getShakeAccelerationMin", delay=0.2))
    )
    max_accel = int(
      float(await self._driver.send_command(cmd="getShakeAccelerationMax", delay=0.2))
    )

    assert min_accel <= acceleration <= max_accel, (
      f"Acceleration {acceleration} seconds is out of range. "
      f"Allowed range is {min_accel}-{max_accel} seconds"
    )

    await self._driver.send_command(cmd=f"setShakeAcceleration{acceleration}", delay=0.2)
    await self._driver.send_command(cmd="shakeOn", delay=0.2)

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

    min_decel = int(
      float(await self._driver.send_command(cmd="getShakeAccelerationMin", delay=0.2))
    )
    max_decel = int(
      float(await self._driver.send_command(cmd="getShakeAccelerationMax", delay=0.2))
    )

    assert min_decel <= deceleration <= max_decel, (
      f"Deceleration {deceleration} seconds is out of range. "
      f"Allowed range is {min_decel}-{max_decel} seconds"
    )

    await self._driver.send_command(cmd=f"setShakeAcceleration{deceleration}", delay=0.2)
    await self._driver.send_command(cmd="shakeOff", delay=0.2)

    # The firmware needs the motor to fully decelerate before ELM can operate.
    await asyncio.sleep(3)

  @property
  def supports_locking(self) -> bool:
    return True

  async def lock_plate(self):
    await self._driver.send_command(cmd="setElmLockPos", delay=0.3)

  async def unlock_plate(self):
    await self._driver.send_command(cmd="setElmUnlockPos", delay=0.3)


class BioShakeTemperatureBackend(TemperatureControllerBackend):
  """Translates TemperatureControllerBackend calls into BioShake serial commands."""

  def __init__(self, driver: BioShakeDriver, supports_active_cooling: bool = False):
    self._driver = driver
    self._supports_active_cooling = supports_active_cooling

  @property
  def supports_active_cooling(self) -> bool:
    return self._supports_active_cooling

  async def set_temperature(self, temperature: float):
    min_temp = int(float(await self._driver.send_command(cmd="getTempMin", delay=0.2)))
    max_temp = int(float(await self._driver.send_command(cmd="getTempMax", delay=0.2)))

    assert min_temp <= temperature <= max_temp, (
      f"Temperature {temperature} C is out of range. Allowed range is {min_temp}-{max_temp} C."
    )

    temperature_tenths = temperature * 10

    if isinstance(temperature_tenths, float):
      if not temperature_tenths.is_integer():
        raise ValueError(f"Temperature must be a whole number in 1/10 C, not {temperature_tenths}")
      temperature_tenths = int(temperature_tenths)

    await self._driver.send_command(cmd=f"setTempTarget{temperature_tenths}", delay=0.2)
    await self._driver.send_command(cmd="tempOn", delay=0.2)

  async def get_current_temperature(self) -> float:
    response = await self._driver.send_command(cmd="getTempActual", delay=0.2)
    return float(response)

  async def deactivate(self):
    await self._driver.send_command(cmd="tempOff", delay=0.2)


class BioShake(PlateHolder, Device):
  """QInstruments BioShake base class.

  Use a subclass for your specific model. Capabilities (``tc``, ``shaker``)
  are only present on subclasses whose hardware supports them.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    driver: BioShakeDriver,
    child_location: Coordinate,
    pedestal_size_z: float,
    category: str = "bioshake",
    model: Optional[str] = None,
  ):
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
    self._driver: BioShakeDriver = driver

  def serialize(self) -> dict:
    return {
      **Device.serialize(self),
      **PlateHolder.serialize(self),
    }


# -- Per-model classes --
# Shaking only


class BioShake3000(BioShake):
  """BioShake 3000 - shaking 200-3000 rpm, no ELM, no heating."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("BioShake3000 is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.shaker]


class BioShake3000Elm(BioShake):
  """BioShake 3000 elm - shaking 200-3000 rpm, ELM, no heating."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("BioShake3000Elm is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.shaker]


class BioShake3000ElmDWP(BioShake):
  """BioShake 3000 elm DWP - shaking 200-3000 rpm, ELM, no heating."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("BioShake3000ElmDWP is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.shaker]


class BioShakeD30Elm(BioShake):
  """BioShake D30 elm - shaking 200-2000 rpm, ELM, no heating."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("BioShakeD30Elm is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.shaker]


class BioShake5000Elm(BioShake):
  """BioShake 5000 elm - shaking 200-5000 rpm, ELM, no heating."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("BioShake5000Elm is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.shaker]


# Shaking + heating (no active cooling)


class BioShake3000T(BioShake):
  """BioShake 3000-T - shaking 200-3000 rpm, no ELM, heating."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("BioShake3000T is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.tc = TemperatureControlCapability(backend=BioShakeTemperatureBackend(driver))
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.tc, self.shaker]


class BioShake3000TElm(BioShake):
  """BioShake 3000-T elm - shaking 200-3000 rpm, ELM, heating."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("BioShake3000TElm is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.tc = TemperatureControlCapability(backend=BioShakeTemperatureBackend(driver))
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.tc, self.shaker]


class BioShakeD30TElm(BioShake):
  """BioShake D30-T elm - shaking 200-2000 rpm, ELM, heating."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("BioShakeD30TElm is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.tc = TemperatureControlCapability(backend=BioShakeTemperatureBackend(driver))
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.tc, self.shaker]


# Shaking + heating + active cooling


class BioShakeQ1(BioShake):
  """BioShake Q1 - shaking 200-3000 rpm, ELM, heating, active cooling."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("BioShakeQ1 is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.tc = TemperatureControlCapability(
      backend=BioShakeTemperatureBackend(driver, supports_active_cooling=True)
    )
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.tc, self.shaker]


class BioShakeQ2(BioShake):
  """BioShake Q2 - shaking 200-3000 rpm, ELM, heating, active cooling."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("BioShakeQ2 is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.tc = TemperatureControlCapability(
      backend=BioShakeTemperatureBackend(driver, supports_active_cooling=True)
    )
    self.shaker = ShakingCapability(backend=BioShakeShakerBackend(driver))
    self._capabilities = [self.tc, self.shaker]


# Temperature only


class Heatplate(BioShake):
  """Heatplate - no shaking, heating only."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("Heatplate is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.tc = TemperatureControlCapability(backend=BioShakeTemperatureBackend(driver))
    self._capabilities = [self.tc]


class ColdPlate(BioShake):
  """ColdPlate - no shaking, heating, active cooling."""

  def __init__(self, name: str, port: str):
    raise NotImplementedError("ColdPlate is missing resource definition.")
    driver = BioShakeDriver(port=port)
    super().__init__(
      name=name,
      driver=driver,
      size_x=0,
      size_y=0,
      size_z=0,  # TODO
      child_location=Coordinate(0, 0, 0),  # TODO
      pedestal_size_z=0,  # TODO
    )
    self.tc = TemperatureControlCapability(
      backend=BioShakeTemperatureBackend(driver, supports_active_cooling=True)
    )
    self._capabilities = [self.tc]
