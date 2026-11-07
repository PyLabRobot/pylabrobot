# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2024-2025 HSE AG <opensource@hseag.com>
"""USB serial control of the eviFluor Duo's 470 nm measurement channel.

Protocol reference: https://github.com/hseag/evifluor
The manufacturer's license is included in this package's LICENSE file.
"""

import asyncio
import binascii
import logging
import math
import shlex
import time
from dataclasses import dataclass
from typing import Literal, Optional

from pylabrobot.io.serial import Serial

logger = logging.getLogger(__name__)

_ERRORS = {
  0: "OK",
  1: "Unknown command",
  2: "Invalid parameter",
  3: "Timeout",
  4: "SREC Flash write error",
  5: "SREC Unsupported type",
  6: "SREC Invalid crc",
  7: "SREC Invalid string",
  11: "No more logging",
  100: "Communication error",
}
_STATUS_COLORS = {"off": 0, "red": 1, "green": 2, "blue": 3, "white": 4}


class EviFluorError(RuntimeError):
  """A device error, preserving the numeric firmware code and failed command."""

  def __init__(self, code: int, command: str):
    """Build an error from an ``E <code>`` response."""
    self.code = code
    self.command = command
    super().__init__(f"eviFluor {command!r}: {_ERRORS.get(code, 'Unknown error')} ({code})")


class EviFluorProtocolError(RuntimeError):
  """An invalid, corrupt, or unexpected device response."""


def _validate_integer(value: int, minimum: int, maximum: int, name: str) -> None:
  """Validate a bounded integer without accepting booleans or truncating floats."""
  if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
    raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")


def _parse_integer(value: str, minimum: int, maximum: int) -> int:
  """Parse a bounded integer from a response."""
  try:
    result = int(value)
    _validate_integer(result, minimum, maximum, "Response value")
    return result
  except ValueError as exc:
    raise EviFluorProtocolError(f"Invalid integer in response: {value!r}") from exc


@dataclass(frozen=True)
class EviFluorMeasurement:
  """One 470 nm reading: dark and illuminated values in mV, LED power in 0..255.

  ``reserved_values`` preserves the trailing triplet in the C protocol. Its meaning is
  not assigned here: the manufacturer APIs only expose the first measurement channel.
  """

  dark: float
  value: float
  led_power: int
  reserved_values: tuple[str, ...] = ()

  def __post_init__(self) -> None:
    """Reject nonfinite readings and invalid LED powers."""
    if not math.isfinite(self.dark) or not math.isfinite(self.value):
      raise ValueError("Measurement values must be finite")
    _validate_integer(self.led_power, 0, 255, "led_power")

  @property
  def signal(self) -> float:
    """Dark-corrected signal in mV."""
    return self.value - self.dark


@dataclass(frozen=True)
class EviFluorAirCalibration:
  """Empty-cuvette readings at the minimum and maximum 470 nm LED powers."""

  minimum: EviFluorMeasurement
  maximum: EviFluorMeasurement

  def interpolate(self, led_power: int) -> EviFluorMeasurement:
    """Interpolate the empty-cuvette reading at a power within the measured range."""
    _validate_integer(led_power, self.minimum.led_power, self.maximum.led_power, "led_power")
    if self.minimum.value >= 2499 or self.maximum.value >= 2499:
      raise ValueError("Cannot interpolate saturated empty-cuvette readings")
    span = self.maximum.led_power - self.minimum.led_power
    if span == 0:
      return self.minimum
    fraction = (led_power - self.minimum.led_power) / span
    return EviFluorMeasurement(
      dark=self.minimum.dark + (self.maximum.dark - self.minimum.dark) * fraction,
      value=self.minimum.value + (self.maximum.value - self.minimum.value) * fraction,
      led_power=led_power,
    )


@dataclass(frozen=True)
class EviFluorAutoGain:
  """Whether the requested signal was reached and the selected 470 nm LED power."""

  found: bool
  led_power: int


def _corrected_signal(air: EviFluorMeasurement, sample: EviFluorMeasurement) -> float:
  """Subtract the empty cuvette, rejecting mismatched powers and saturated signals."""
  if air.led_power != sample.led_power:
    raise ValueError("Air and sample readings must use the same LED power")
  if air.value >= 2499 or sample.value >= 2499:
    raise ValueError("Saturated eviFluor reading (at least 2499 mV)")
  return sample.signal - air.signal


class EviFluor:
  """Plain Python driver for an HSE eviFluor Duo, using :class:`pylabrobot.io.serial.Serial`.

  Args:
    port: Serial port, e.g. ``COM3`` or ``/dev/ttyACM0``. If omitted, PLR finds the
      single device with USB VID/PID 0x1CBE/0x0003.
    timeout: Maximum response time in seconds. Measurements and autogain are blocking
      firmware commands; the default matches the manufacturer's 30 second timeout.
    use_checksum: Send CRC-16/CCITT frames instead of the default plain ASCII frames.

  This driver has not been tested on hardware. Only the 470 nm measurement and LED
  registers are supported. Calibration is held on this instance and cleared on disconnect.
  """

  def __init__(self, port: Optional[str] = None, timeout: float = 30.0, use_checksum: bool = False):
    """Configure the transport without connecting to hardware."""
    if not math.isfinite(timeout) or timeout <= 0:
      raise ValueError("timeout must be finite and positive")
    self.io = Serial(
      human_readable_device_name="HSE eviFluor Duo",
      port=port,
      vid=0x1CBE,
      pid=0x0003,
      baudrate=115200,
      bytesize=8,
      parity="N",
      stopbits=1,
      timeout=min(timeout, 0.1),
      write_timeout=timeout,
    )
    self.timeout = timeout
    self.use_checksum = use_checksum
    self._lock = asyncio.Lock()
    self._connected = False
    self._faulted = False
    self._calibration: Optional[tuple[float, float, float, float, int]] = None

  async def _read_frame(self) -> list[str]:
    """Read one bounded CR/LF-terminated frame and verify its optional CRC."""
    deadline = time.monotonic() + self.timeout
    frame = bytearray()
    while time.monotonic() < deadline:
      char = await self.io.read(1)
      if not char:
        await asyncio.sleep(0)
        continue
      if not frame:
        if char in (b":", b";"):
          frame.extend(char)
        continue
      if char in (b"\r", b"\n"):
        payload = bytes(frame[1:])
        if frame[0] == ord(";"):
          payload, separator, checksum = payload.rpartition(b"@")
          if not separator or not checksum.isdigit():
            raise EviFluorProtocolError("Missing or invalid response checksum")
          if int(checksum) != binascii.crc_hqx(payload, 0x1D0F):
            raise EviFluorProtocolError("Response CRC mismatch")
        try:
          lexer = shlex.shlex(payload.decode("ascii"), posix=True)
          lexer.whitespace_split = True
          lexer.commenters = ""
          lexer.escape = ""
          tokens = list(lexer)
        except (UnicodeDecodeError, ValueError) as exc:
          raise EviFluorProtocolError("Malformed response payload") from exc
        if not tokens:
          raise EviFluorProtocolError("Empty response payload")
        return tokens
      frame.extend(char)
      if len(frame) >= 255:
        raise EviFluorProtocolError("Response exceeds the 255-byte protocol buffer")
    raise TimeoutError("Timed out waiting for an eviFluor response")

  async def _exchange(self, command: str, counts: tuple[int, ...]) -> list[str]:
    """Exchange a command with the lock held; counts exclude the echoed command token."""
    if not self._connected:
      raise RuntimeError("Call setup() before using eviFluor")
    if self._faulted:
      raise RuntimeError("eviFluor connection lost synchronization; call stop(), then setup()")
    payload = command.encode("ascii")
    if self.use_checksum:
      frame = b";" + payload + b"@" + str(binascii.crc_hqx(payload, 0x1D0F)).encode() + b"\n"
    else:
      frame = b":" + payload + b"\n"
    try:
      await self.io.write(frame)
      tokens = await self._read_frame()
      if tokens[0] == "E":
        if len(tokens) != 2:
          raise EviFluorProtocolError(f"Malformed error response: {tokens!r}")
        raise EviFluorError(_parse_integer(tokens[1], 0, 65535), command)
      if tokens[0] != command.split()[0] or len(tokens) - 1 not in counts:
        raise EviFluorProtocolError(f"Unexpected response to {command!r}: {tokens!r}")
      return tokens[1:]
    except EviFluorError as exc:
      logger.warning("eviFluor device error: %s", exc)
      raise
    except BaseException:
      # Without transaction IDs, a late reply cannot be paired with another command safely.
      self._faulted = True
      logger.warning("eviFluor exchange interrupted; reconnect before issuing another command")
      raise

  async def _command(self, command: str, counts: tuple[int, ...]) -> list[str]:
    """Serialize a complete request/response exchange."""
    async with self._lock:
      return await self._exchange(command, counts)

  async def setup(self) -> None:
    """Open the port and check communication with a firmware-version query."""
    async with self._lock:
      if self._connected:
        if self._faulted:
          raise RuntimeError("Call stop() before reconnecting eviFluor")
        return
      logger.warning(
        "EviFluor has not been tested on hardware. Please report successful hardware "
        "validation to PyLabRobot and update this warning once verified."
      )
      try:
        await self.io.setup()
        await self.io.reset_input_buffer()
        self._connected = True
        self._faulted = False
        version = (await self._exchange("V 0", (1,)))[0]
      except BaseException:
        self._connected = False
        await self.io.stop()
        raise
      logger.info("Connected to eviFluor firmware %s", version)

  async def stop(self) -> None:
    """Close the serial connection and discard the local concentration calibration."""
    async with self._lock:
      try:
        await self.io.stop()
      finally:
        self._connected = False
        self._faulted = False
        self._calibration = None
      logger.info("Disconnected eviFluor")

  async def get_firmware_version(self) -> str:
    """Read the firmware version string."""
    return (await self._command("V 0", (1,)))[0]

  async def get_serial_number(self) -> str:
    """Read the device serial number."""
    return (await self._command("V 1", (1,)))[0]

  async def get_production_number(self) -> str:
    """Read the production number."""
    return (await self._command("V 3", (1,)))[0]

  async def run_self_test(self) -> int:
    """Return the self-test bitmask: zero means success, bit 0 is a communication fault."""
    result = _parse_integer((await self._command("Y", (1,)))[0], 0, 0xFFFFFFFF)
    logger.info("eviFluor self-test result: 0x%08x", result)
    return result

  async def is_cuvette_holder_empty(self) -> bool:
    """Return whether the cuvette holder is empty; ignore optional diagnostic fields."""
    values = await self._command("X", tuple(range(1, 20)))
    return bool(_parse_integer(values[0], 0, 1))

  async def clear_measurements(self) -> None:
    """Clear the firmware's measurement history (G); this does not measure a baseline."""
    await self._command("G", (0,))
    logger.info("Cleared eviFluor measurement history")

  async def get_measurement_count(self) -> int:
    """Return the number of readings in the firmware's ten-reading history."""
    return _parse_integer((await self._command("V 10", (1,)))[0], 0, 10)

  async def get_led_power(self) -> int:
    """Read the current 470 nm LED power (0..255)."""
    return _parse_integer((await self._command("V 15", (1,)))[0], 0, 255)

  async def _get_led_power_limits(self) -> tuple[int, int]:
    """Read and validate the 470 nm power limits with the operation lock held."""
    minimum = _parse_integer((await self._exchange("V 16", (1,)))[0], 0, 255)
    maximum = _parse_integer((await self._exchange("V 17", (1,)))[0], minimum, 255)
    return minimum, maximum

  async def get_led_power_limits(self) -> tuple[int, int]:
    """Read the device's minimum and maximum 470 nm LED powers."""
    async with self._lock:
      return await self._get_led_power_limits()

  async def set_led_power(self, led_power: int) -> None:
    """Set the 470 nm LED power (0..255); firmware enforces device-specific limits."""
    _validate_integer(led_power, 0, 255, "led_power")
    await self._command(f"V 15 {led_power}", (0,))
    logger.info("Set eviFluor 470 nm LED power to %d", led_power)

  async def _measure(self, command: str = "M") -> EviFluorMeasurement:
    """Parse the 470 nm triplet, preserving any reserved trailing triplet."""
    values = await self._exchange(command, (3, 6))
    try:
      return EviFluorMeasurement(
        dark=float(values[0]),
        value=float(values[1]),
        led_power=_parse_integer(values[2], 0, 255),
        reserved_values=tuple(values[3:]),
      )
    except ValueError as exc:
      raise EviFluorProtocolError(f"Invalid measurement: {values!r}") from exc

  async def measure(self, led_power: Optional[int] = None) -> EviFluorMeasurement:
    """Measure at the current power, or atomically set a power and take a reading.

    Insert the cuvette before calling. Measure it empty, dispense the sample, then
    measure again at the same LED power to obtain an air/sample pair.
    """
    if led_power is not None:
      _validate_integer(led_power, 0, 255, "led_power")
    async with self._lock:
      if led_power is not None:
        await self._exchange(f"V 15 {led_power}", (0,))
      result = await self._measure()
    logger.info("eviFluor measured %.3f mV at LED power %d", result.signal, result.led_power)
    return result

  async def get_stored_measurement(self, index: int = 0) -> EviFluorMeasurement:
    """Read history without measuring: index 0 is newest, up to 9 is oldest."""
    _validate_integer(index, 0, 9, "index")
    async with self._lock:
      return await self._measure(f"M {index}")

  async def _autogain(self, target: int) -> EviFluorAutoGain:
    """Ask the firmware to select LED power for a target signal, with the lock held."""
    values = await self._exchange(f"C {target}", (2,))
    result = EviFluorAutoGain(
      found=bool(_parse_integer(values[0], 0, 1)),
      led_power=_parse_integer(values[1], 0, 255),
    )
    logger.info("eviFluor autogain found=%s LED power=%d", result.found, result.led_power)
    return result

  async def autogain(self, target: int = 2000) -> EviFluorAutoGain:
    """Select 470 nm power for a target signal in mV (0..2500).

    Use the high standard. Subsequent standards and samples must retain this power.
    Check ``found`` before using the result for calibration.
    """
    _validate_integer(target, 0, 2500, "target")
    async with self._lock:
      return await self._autogain(target)

  async def measure_first_air(self) -> EviFluorAirCalibration:
    """Measure an empty cuvette at both power limits, leaving the LED at maximum power.

    Use this before dispensing the high standard. After autogain, interpolate these
    readings to the selected power. Keep the same cuvette in place between readings.
    """
    async with self._lock:
      minimum, maximum = await self._get_led_power_limits()
      await self._exchange(f"V 15 {minimum}", (0,))
      low = await self._measure()
      await self._exchange(f"V 15 {maximum}", (0,))
      high = await self._measure()
      if low.led_power != minimum or high.led_power != maximum:
        raise EviFluorProtocolError("Air measurement did not use the requested LED power")
      return EviFluorAirCalibration(low, high)

  async def measure_first_sample(self, target: int = 2000) -> EviFluorMeasurement:
    """Select power using the high standard and measure; raise if autogain fails."""
    _validate_integer(target, 0, 2500, "target")
    async with self._lock:
      gain = await self._autogain(target)
      if not gain.found:
        raise RuntimeError(f"eviFluor autogain failed at LED power {gain.led_power}")
      result = await self._measure()
      if result.led_power != gain.led_power:
        raise EviFluorProtocolError("Sample measurement did not use the autogain LED power")
      return result

  async def set_status_led(self, color: Literal["off", "red", "green", "blue", "white"]) -> None:
    """Set the status indicator color."""
    if color not in _STATUS_COLORS:
      raise ValueError(f"Unsupported status LED color: {color!r}")
    await self._command(f"Z {_STATUS_COLORS[color]}", (0,))

  def calibrate(
    self,
    low_air: EviFluorMeasurement,
    low_sample: EviFluorMeasurement,
    high_air: EviFluorMeasurement,
    high_sample: EviFluorMeasurement,
    low_concentration: float,
    high_concentration: float,
  ) -> None:
    """Store a linear calibration from two air/sample standard pairs, without I/O.

    Concentrations use the same units as the assay standards; subsequent results use
    those units. All readings must use the same LED power. For the first high standard,
    supply the interpolated air reading from :meth:`measure_first_air`.
    """
    if not (
      math.isfinite(low_concentration)
      and math.isfinite(high_concentration)
      and 0 <= low_concentration < high_concentration
    ):
      raise ValueError("Standard concentrations must be finite and satisfy 0 <= low < high")
    low = _corrected_signal(low_air, low_sample)
    high = _corrected_signal(high_air, high_sample)
    if low_sample.led_power != high_sample.led_power:
      raise ValueError("Both standards must use the same LED power")
    if high <= low:
      raise ValueError("High standard signal must exceed low standard signal")
    self._calibration = (low, high, low_concentration, high_concentration, low_sample.led_power)
    logger.info("Calibrated eviFluor at LED power %d", low_sample.led_power)

  def calculate_concentration(self, air: EviFluorMeasurement, sample: EviFluorMeasurement) -> float:
    """Calculate concentration from a cuvette pair using the stored calibration.

    Results use the standards' concentration units. Dilution correction is the caller's
    responsibility. Values outside the calibration range are linearly extrapolated.
    """
    if self._calibration is None:
      raise RuntimeError("Call calibrate() with low and high standards first")
    low, high, low_concentration, high_concentration, led_power = self._calibration
    value = _corrected_signal(air, sample)
    if sample.led_power != led_power:
      raise ValueError("Sample LED power differs from the calibration")
    return low_concentration + (value - low) * (high_concentration - low_concentration) / (
      high - low
    )
