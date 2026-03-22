import asyncio

from pylabrobot.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.io.serial import Serial
from pylabrobot.machines.backend import MachineBackend

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e


class BioShake(HeaterShakerBackend):
  def __init__(self, port: str, timeout: int = 60):
    if not HAS_SERIAL:
      raise RuntimeError(
        f"pyserial is required for the BioShake module backend. Import error: {_SERIAL_IMPORT_ERROR}"
      )

    self.setup_finished = False
    self.port = port
    self.timeout = timeout
    self.io = Serial(
      port=self.port,
      baudrate=9600,
      bytesize=serial.EIGHTBITS,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
      write_timeout=10,
      timeout=self.timeout,
    )

  async def _send_command(self, cmd: str, delay: float = 0.5, timeout: float = 2):
    try:
      # Flush serial buffers for a clean start
      await self.io.reset_input_buffer()
      await self.io.reset_output_buffer()

      # Send the command
      await self.io.write((cmd + "\r").encode("ascii"))
      await asyncio.sleep(delay)

      # Read and decode the response with a timeout
      try:
        response = await asyncio.wait_for(self.io.readline(), timeout=timeout)

      except asyncio.TimeoutError:
        raise RuntimeError(f"Timed out waiting for response to '{cmd}'")

      decoded = response.decode("ascii", errors="ignore").strip()

      # Parsing the response from the BioShake

      # No response at all
      if not decoded:
        raise RuntimeError(f"No response for '{cmd}'")

      # Device-specific errors
      if decoded.startswith("e"):
        raise RuntimeError(f"Device returned error for '{cmd}': '{decoded}'")

      if decoded.startswith("u ->"):
        raise NotImplementedError(f"'{cmd}' not supported: '{decoded}'")

      # Standard OK
      if decoded.lower().startswith("ok"):
        return None

      # All other valid responses (e.g. temperature and remaining time)
      return decoded

    except Exception as e:
      raise RuntimeError(f"Unexpected error while sending '{cmd}': {type(e).__name__}: {e}") from e

  async def setup(self, skip_home: bool = False):
    await MachineBackend.setup(self)
    await self.io.setup()
    if not skip_home:
      # Reset first before homing it to ensure the device is ready for run
      await self.reset()
      # Additional seconds until next command can be send after reset
      await asyncio.sleep(4)
      # Now home the device
      await self.home()

  async def stop(self):
    await MachineBackend.stop(self)
    await self.io.stop()

  async def reset(self):
    # Reset the BioShake if stuck in "e" state
    # Flush serial buffers for a clean start
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()

    # Send the command
    await self.io.write(("resetDevice\r").encode("ascii"))

    start = asyncio.get_event_loop().time()
    max_seconds = 30  # How long a reset typically last

    while True:
      # Break the loop if process takes longer than 30 seconds
      if asyncio.get_event_loop().time() - start > max_seconds:
        raise TimeoutError("Reset did not complete in time")

      try:
        # Wait for each line with a timeout
        response = await asyncio.wait_for(self.io.readline(), timeout=2)
        decoded = response.decode("ascii", errors="ignore").strip()
        await asyncio.sleep(0.1)

        if len(decoded) > 0:
          # Stop when the final message arrives
          if "Initialization complete" in decoded:
            break

      except asyncio.TimeoutError:
        # Keep polling if nothing arrives within timeout
        continue

  async def home(self):
    # Initialize the BioShake into home position
    await self._send_command(cmd="shakeGoHome", delay=5)

  async def shake(self, speed: float, acceleration: int = 0):
    # Check if speed is an integer
    if isinstance(speed, float):
      if not speed.is_integer():
        raise ValueError(f"Speed must be a whole number, not {speed}")
      speed = int(speed)
    if not isinstance(speed, int):
      raise TypeError(
        f"Speed must be an integer or a whole number float, not {type(speed).__name__}"
      )

    # Get the min and max speed of the device to assert speed
    min_speed = int(float(await self._send_command(cmd="getShakeMinRpm", delay=0.2)))
    max_speed = int(float(await self._send_command(cmd="getShakeMaxRpm", delay=0.2)))

    assert min_speed <= speed <= max_speed, (
      f"Speed {speed} RPM is out of range. Allowed range is {min_speed}{max_speed} RPM"
    )

    # Set the speed of the shaker
    set_speed_cmd = f"setShakeTargetSpeed{speed}"
    await self._send_command(cmd=set_speed_cmd)

    # Check if accel is an integer
    if isinstance(acceleration, float):
      if not acceleration.is_integer():  # type: ignore[attr-defined] # mypy is retarded
        raise ValueError(f"Acceleration must be a whole number, not {acceleration}")
      acceleration = int(acceleration)
    if not isinstance(acceleration, int):
      raise TypeError(
        f"Acceleration must be an integer or a whole number float, not {type(acceleration).__name__}"
      )

    # Get the min and max acceleration of the device to check bounds
    min_accel = int(float(await self._send_command(cmd="getShakeAccelerationMin", delay=0.2)))
    max_accel = int(float(await self._send_command(cmd="getShakeAccelerationMax", delay=0.2)))

    assert min_accel <= acceleration <= max_accel, (
      f"Acceleration {acceleration} seconds is out of range. Allowed range is {min_accel}-{max_accel} seconds"
    )

    # Set the acceleration of the shaker
    set_accel_cmd = f"setShakeAcceleration{acceleration}"
    await self._send_command(cmd=set_accel_cmd, delay=0.2)

    # Send the command to start shaking, either with or without duration

    await self._send_command(cmd="shakeOn", delay=0.2)

  async def stop_shaking(self, deceleration: int = 0):
    # Check if decel is an integer
    if isinstance(deceleration, float):
      if not deceleration.is_integer():  # type: ignore[attr-defined] # mypy is retarded
        raise ValueError(f"Deceleration must be a whole number, not {deceleration}")
      deceleration = int(deceleration)
    if not isinstance(deceleration, int):
      raise TypeError(
        f"Deceleration must be an integer or a whole number float, not {type(deceleration).__name__}"
      )

    # Get the min and max decel of the device to asset decel
    min_decel = int(float(await self._send_command(cmd="getShakeAccelerationMin", delay=0.2)))
    max_decel = int(float(await self._send_command(cmd="getShakeAccelerationMax", delay=0.2)))

    assert min_decel <= deceleration <= max_decel, (
      f"Deceleration {deceleration} seconds is out of range. Allowed range is {min_decel}-{max_decel} seconds"
    )

    # Set the deceleration of the shaker
    set_decel_cmd = f"setShakeAcceleration{deceleration}"
    await self._send_command(cmd=set_decel_cmd, delay=0.2)

    # stop shaking
    await self._send_command(cmd="shakeOff", delay=0.2)

  @property
  def supports_locking(self) -> bool:
    return True

  async def lock_plate(self):
    await self._send_command(cmd="setElmLockPos", delay=0.3)

  async def unlock_plate(self):
    await self._send_command(cmd="setElmUnlockPos", delay=0.3)

  @property
  def supports_active_cooling(self) -> bool:
    return True

  async def set_temperature(self, temperature: float):
    # Get the min and max set points of the device to assert temperature
    min_temp = int(float(await self._send_command(cmd="getTempMin", delay=0.2)))
    max_temp = int(float(await self._send_command(cmd="getTempMax", delay=0.2)))

    assert min_temp <= temperature <= max_temp, (
      f"Temperature {temperature} C is out of range. Allowed range is {min_temp}–{max_temp} C."
    )

    temperature = temperature * 10

    # Check if temperature is an integer
    if isinstance(temperature, float):
      if not temperature.is_integer():
        raise ValueError(f"Temperature must be a whole number, not {temperature} (1/10 C)")
      temperature = int(temperature)
    if not isinstance(temperature, int):
      raise TypeError(
        f"Temperature must be an integer or a whole number float, not {type(temperature).__name__} (1/10 C)"
      )

    set_temp_cmd = f"setTempTarget{temperature}"
    await self._send_command(cmd=set_temp_cmd, delay=0.2)

    # Start temperature control
    await self._send_command(cmd="tempOn", delay=0.2)

  async def get_current_temperature(self) -> float:
    response = await self._send_command(cmd="getTempActual", delay=0.2)
    return float(response)

  async def deactivate(self):
    # Stop temperature control
    await self._send_command(cmd="tempOff", delay=0.2)
