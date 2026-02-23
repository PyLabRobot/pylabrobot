import asyncio
import warnings

from pylabrobot.io.serial import Serial
from pylabrobot.machines.backend import MachineBackend
from pylabrobot.shaking.backend import ShakerBackend

try:
  import serial

  HAS_SERIAL = True
except ImportError as e:
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e


class BioShake(ShakerBackend):
  """Backend for BioShake devices.

  This backend models BioShake as a pure shaker with plate locking support.
  """

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

  def serialize(self) -> dict:
    return {
      **super().serialize(),
      "port": self.port,
      "timeout": self.timeout,
    }

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

      return decoded

    except Exception as e:
      raise RuntimeError(f"Unexpected error while sending '{cmd}': {type(e).__name__}: {e}") from e

  async def setup(self, skip_home: bool = False):
    await MachineBackend.setup(self)
    await self.io.setup()
    if not skip_home:
      # Reset first before homing to ensure the device is ready for use.
      await self.reset()
      # Additional time until next command can be sent after reset.
      await asyncio.sleep(4)
      await self.home()

  async def stop(self):
    await MachineBackend.stop(self)
    await self.io.stop()

  async def reset(self):
    # Reset the BioShake if stuck in "e" state.
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()
    await self.io.write(("resetDevice\r").encode("ascii"))

    start = asyncio.get_event_loop().time()
    max_seconds = 30  # Typical reset duration.

    while True:
      if asyncio.get_event_loop().time() - start > max_seconds:
        raise TimeoutError("Reset did not complete in time")

      try:
        response = await asyncio.wait_for(self.io.readline(), timeout=2)
        decoded = response.decode("ascii", errors="ignore").strip()
        await asyncio.sleep(0.1)
        if decoded and "Initialization complete" in decoded:
          break
      except asyncio.TimeoutError:
        continue

  async def home(self):
    # Initialize BioShake into home position.
    await self._send_command(cmd="shakeGoHome", delay=5)

  async def start_shaking(self, speed: float, acceleration: int = 0):
    # Check speed value type.
    if isinstance(speed, float):
      if not speed.is_integer():
        raise ValueError(f"Speed must be a whole number, not {speed}")
      speed = int(speed)
    if not isinstance(speed, int):
      raise TypeError(
        f"Speed must be an integer or a whole number float, not {type(speed).__name__}"
      )

    min_speed = int(float(await self._send_command(cmd="getShakeMinRpm", delay=0.2)))
    max_speed = int(float(await self._send_command(cmd="getShakeMaxRpm", delay=0.2)))
    assert (
      min_speed <= speed <= max_speed
    ), f"Speed {speed} RPM is out of range. Allowed range is {min_speed}{max_speed} RPM"

    await self._send_command(cmd=f"setShakeTargetSpeed{speed}")

    if isinstance(acceleration, float):
      if not acceleration.is_integer():  # type: ignore[attr-defined]
        raise ValueError(f"Acceleration must be a whole number, not {acceleration}")
      acceleration = int(acceleration)
    if not isinstance(acceleration, int):
      raise TypeError(
        "Acceleration must be an integer or a whole number float, not "
        f"{type(acceleration).__name__}"
      )

    min_accel = int(float(await self._send_command(cmd="getShakeAccelerationMin", delay=0.2)))
    max_accel = int(float(await self._send_command(cmd="getShakeAccelerationMax", delay=0.2)))
    assert (
      min_accel <= acceleration <= max_accel
    ), f"Acceleration {acceleration} seconds is out of range. Allowed range is {min_accel}-{max_accel} seconds"

    await self._send_command(cmd=f"setShakeAcceleration{acceleration}", delay=0.2)
    await self._send_command(cmd="shakeOn", delay=0.2)

  async def shake(self, speed: float, acceleration: int = 0):
    """Deprecated alias for ``start_shaking``."""
    warnings.warn(
      "BioShake.shake() is deprecated. Use start_shaking() instead.",
      DeprecationWarning,
      stacklevel=2,
    )
    await self.start_shaking(speed=speed, acceleration=acceleration)

  async def stop_shaking(self, deceleration: int = 0):
    if isinstance(deceleration, float):
      if not deceleration.is_integer():  # type: ignore[attr-defined]
        raise ValueError(f"Deceleration must be a whole number, not {deceleration}")
      deceleration = int(deceleration)
    if not isinstance(deceleration, int):
      raise TypeError(
        "Deceleration must be an integer or a whole number float, not "
        f"{type(deceleration).__name__}"
      )

    min_decel = int(float(await self._send_command(cmd="getShakeAccelerationMin", delay=0.2)))
    max_decel = int(float(await self._send_command(cmd="getShakeAccelerationMax", delay=0.2)))
    assert (
      min_decel <= deceleration <= max_decel
    ), f"Deceleration {deceleration} seconds is out of range. Allowed range is {min_decel}-{max_decel} seconds"

    await self._send_command(cmd=f"setShakeAcceleration{deceleration}", delay=0.2)
    await self._send_command(cmd="shakeOff", delay=0.2)

  @property
  def supports_locking(self) -> bool:
    return True

  async def lock_plate(self):
    await self._send_command(cmd="setElmLockPos", delay=0.3)

  async def unlock_plate(self):
    await self._send_command(cmd="setElmUnlockPos", delay=0.3)
