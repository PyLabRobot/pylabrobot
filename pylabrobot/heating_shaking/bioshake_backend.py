import asyncio
from typing import Optional
from pylabrobot.heating_shaking.backend import HeaterShakerBackend
from pylabrobot.io.serial import Serial

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

  async def _send_command(self, cmd: str, delay: float = 0.5):
    try:
        # Send the command
        await self.io.write((cmd + '\r').encode('ascii'))
        await asyncio.sleep(delay)

        # Read and decode the response
        response = await self.io.readline()
        decoded = response.decode('ascii', errors='ignore').strip()

        # Parsing the response from the BioShake

        # No response at all
        if not decoded:
          raise RuntimeError(f"No response for '{cmd}'")

        # Device-specific errors
        if decoded.startswith("e"):
          raise RuntimeError(f"Device returned error for '{cmd}': '{decoded}'")

        if decoded.startswith("u ->"):
          raise NotImplementedError (f"'{cmd}' not supported: '{decoded}'")

        # Standard OK
        if decoded.lower().startswith("ok"):
            return None

        # All other valid responses (e.g. temperature and remaining time)
        return decoded

    except Exception as e:
        raise RuntimeError(
            f"Unexpected error while sending '{cmd}': {type(e).__name__}: {e}"
        ) from e

  async def setup(self):
    await super().setup()
    await self.io.setup()

  async def stop(self):
    await super().stop()
    await self.io.stop()

  async def reset(self):
    # Reset the BioShake if stuck in "e" state
    await self._send_command(cmd="resetDevice", delay=30)

  async def home(self):
    # Initialize the BioShake into home position
    await self._send_command(cmd="shakeGoHome", delay=5)

  async def shake(self, speed: float, duration: Optional[float] = None):
    # Set the speed of the shaker
    set_speed_cmd = f"setShakeTargetSpeed{speed}"
    await self._send_command(cmd=set_speed_cmd, delay=0.2)

    # Send the command to start shaking, either with or without duration
    if duration is None:
      await self._send_command(cmd="shakeOn", delay=0.2)
    else:
      set_duration_cmd = f"shakeOnWithRuntime{duration}"
      await self._send_command(cmd=set_duration_cmd, delay=0.2)

  async def stop_shaking(self):
    await self._send_command(cmd="shakeOff", delay=0.2)

  async def get_remaining_time(self) -> float:
    response = await self._send_command(cmd="getShakeRemainingTime", delay=0.2)
    return float(response) # Return the remaining time in seconds if duration was set

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
    # Set the temperature of the shaker
    temperature = temperature * 10
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