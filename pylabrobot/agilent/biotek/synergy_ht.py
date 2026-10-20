"""Endpoint absorbance and tray/temperature control for the BioTek Synergy HT."""

import asyncio
import logging
import math
import re
from datetime import datetime
from typing import List, Literal, Optional, Sequence, Tuple

from pylabrobot.agilent.biotek._reader import _BioTekReader
from pylabrobot.agilent.biotek.results import AbsorbanceResult

logger = logging.getLogger(__name__)


class SynergyHT(_BioTekReader):
  """BioTek Synergy HT using its legacy serial protocol.

  This driver has offline coverage for basecode 7090202, firmware 2.24. Other
  basecodes and devices lacking the required protocol capabilities are rejected.
  Hardware verification is pending. Shaking is supported before an absorbance read.
  Fluorescence, luminescence, standalone shaking, kinetics, and custom plate
  geometries are not implemented.

  Args:
    device_id: USB FTDI serial number, required when multiple adapters are connected.
    timeout: Command timeout in seconds.
    read_timeout: Time allowed for a complete absorbance measurement, in seconds.
  """

  def __init__(
    self,
    device_id: Optional[str] = None,
    timeout: float = 20,
    read_timeout: float = 180,
  ) -> None:
    if not all(math.isfinite(t) and t > 0 for t in (timeout, read_timeout)):
      raise ValueError("Timeouts must be finite and positive")
    super().__init__(timeout, device_id, "BioTek Synergy HT")
    self.read_timeout = read_timeout
    self._temperature_range: Optional[Tuple[int, int]] = None

  @property
  def temperature_range(self) -> Optional[Tuple[int, int]]:
    """Reported minimum and maximum temperature setpoints, in degrees Celsius."""
    return self._temperature_range

  def _reset_state(self) -> None:
    """Clear the reported incubation limits along with the shared reader identity."""
    super()._reset_state()
    self._temperature_range = None

  async def _configure_serial(self) -> None:
    """Apply the legacy reader's DTR and initial buffer settings."""
    await super()._configure_serial()
    await self.io.set_dtr(False)
    await self.io.usb_purge_rx_buffer()
    await self.io.usb_purge_tx_buffer()
    await self._drain_stale_output()

  async def _drain_stale_output(self) -> None:
    """Discard replies the reader queued for an earlier connection.

    RTS/CTS flow control holds the reader's unsent output while the port is closed, so
    it arrives after the purge once RTS is asserted again. Read until the line has been
    quiet for about half a second, or give up after ten seconds.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    dropped = bytearray()
    quiet = 0
    while quiet < 10 and loop.time() < deadline:
      chunk = await self.io.read(64)
      if chunk:
        dropped.extend(chunk)
        quiet = 0
      else:
        quiet += 1
        await asyncio.sleep(0.05)
    if quiet < 10:
      raise TimeoutError("Synergy HT kept sending data; wait until it is idle")
    if dropped:
      logger.warning(
        "Discarded %s stale bytes from an earlier Synergy HT session: %r",
        len(dropped),
        bytes(dropped[:64]),
      )
    self._receive_buffer.clear()

  @staticmethod
  def _check_status(status: bytes) -> None:
    """Accept the power-on flag that replaces the first status digit.

    Gen5 captures show statuses of 0x1E followed by the error digits from power-on until
    the host sends n 1, which setup() does. The remaining digits are checked as usual.
    """
    if status[:1] == b"\x1e":
      status = b"0" + status[1:]
    _BioTekReader._check_status(status)

  async def _initialize_reader(self) -> None:
    """Verify the legacy protocol profile and enable asynchronous result status."""
    logger.warning(
      "Synergy HT has not been verified on hardware. Please report successful hardware "
      "verification so this warning can be removed."
    )
    await super()._initialize_reader()
    if self._basecode != "7090202":
      raise NotImplementedError(f"Unsupported Synergy HT basecode: {self._basecode!r}")
    await self.request_serial_number()
    if (await self.query_capability(2000))[:1] != b"0":
      raise NotImplementedError("This reader reports RDOP22 instead of the legacy protocol")
    for number in (1013, 1006, 1008, 1009, 1012, 1020):
      if (await self.query_capability(number))[:1] != b"1":
        raise NotImplementedError(f"Required Synergy HT capability {number} is unavailable")
    limits = await self.query_capability(1031)
    fields = limits[1:].split(b",")
    if limits[:1] != b"1" or len(fields) < 3 or not all(f.isdigit() for f in fields[:2]):
      raise ValueError(f"Malformed Synergy HT temperature range: {limits!r}")
    lower, upper = int(fields[0]), int(fields[1])
    if not 0 < lower <= upper <= 99:
      raise ValueError(f"Invalid Synergy HT temperature range: {limits!r}")
    await self._exchange(b"n", b"1")
    self._temperature_range = (lower, upper)
    if self.version != "2.24":
      logger.warning(
        "Synergy HT firmware %s has no capture-based regression coverage", self.version
      )

  async def set_temperature(self, temperature: int) -> None:
    """Enable incubation at an integer Celsius setpoint in the reported range.

    Use stop_heating() to turn incubation off. This returns after the setpoint is
    accepted; use get_temperature() to monitor warming.
    """
    limits = self._temperature_range
    if limits is None:
      raise RuntimeError("Call setup() to obtain the instrument's temperature range")
    if not limits[0] <= temperature <= limits[1]:
      raise ValueError(f"Temperature must be between {limits[0]} and {limits[1]} degrees Celsius")
    logger.info("Setting Synergy HT incubation to %s degrees Celsius", temperature)
    await self._exchange(b"g", f"{temperature:2d}".encode("ascii"))

  async def stop_heating(self) -> None:
    """Disable incubation."""
    logger.info("Disabling Synergy HT incubation")
    await self._exchange(b"g", b" 0")

  @staticmethod
  def _absorbance_setup(
    wavelength: int,
    shake_duration: int = 0,
    shake_intensity: Literal["slow", "medium", "fast", "variable"] = "slow",
  ) -> bytes:
    """Encode the fixed 170-byte endpoint setup and its raw one-byte checksum."""
    if not 1 <= wavelength <= 999:
      raise ValueError("Wavelength must be between 1 and 999 nm")
    if type(shake_duration) is not int or not 0 <= shake_duration <= 65535:
      raise ValueError("Shake duration must be an integer between 0 and 65535 seconds")
    intensity_codes = {"slow": 0, "medium": 1, "fast": 2, "variable": 3}
    if not isinstance(shake_intensity, str) or shake_intensity not in intensity_codes:
      raise ValueError("Shake intensity must be 'slow', 'medium', 'fast', or 'variable'")
    packet = bytearray(170)
    packet[8] = ord("0")
    packet[29] = 0x10
    packet[49:52] = b"000"
    packet[66:84] = f"{wavelength:03d}".encode("ascii") + b"000" * 5
    if shake_duration:
      packet[30] = 0x10
      packet[162:164] = shake_duration.to_bytes(2, "little")
      packet[164] = intensity_codes[shake_intensity]
    packet[0] = sum(packet[1:]) & 0xFF
    return bytes(packet)

  @staticmethod
  def _selection(
    plate_type: Literal["24-well", "96-well"], wells: Optional[Sequence[str]]
  ) -> Tuple[int, int, bytes, List[Tuple[int, int]]]:
    """Resolve a built-in plate preset and validate selected well identifiers."""
    if plate_type == "24-well":
      rows, columns, preset = 4, 6, b"\x00"
    elif plate_type == "96-well":
      rows, columns, preset = 8, 12, b"\x02"
    else:
      raise ValueError("Supported plate presets are '24-well' and '96-well'")
    if wells is None:
      return rows, columns, preset, [(r, c) for r in range(rows) for c in range(columns)]
    if not wells:
      raise ValueError("wells must be a nonempty sequence of well names, or None for all wells")
    selected = []
    for well in wells:
      match = re.fullmatch(r"([A-H])([1-9][0-9]?)", well)
      if match is None:
        raise ValueError(f"Invalid well identifier: {well!r}")
      row, column = ord(match[1]) - ord("A"), int(match[2]) - 1
      if row >= rows or column >= columns:
        raise ValueError(f"Well {well} is outside the {plate_type} plate")
      selected.append((row, column))
    if len(set(selected)) != len(selected):
      raise ValueError("Duplicate wells are not allowed")
    return rows, columns, preset, selected

  @staticmethod
  def _parse_absorbance(
    frame: bytes, rows: int, columns: int
  ) -> Tuple[List[List[float]], Optional[float]]:
    """Validate an endpoint result and return its OD matrix and temperature in Celsius."""
    expected = 23 + rows * (6 * columns + 2)
    if len(frame) != expected or frame[:1] != b"\r" or frame[-8:-7] != b"\x1a":
      raise ValueError("Malformed Synergy HT absorbance frame")
    if frame[-6:-5] != b"\x1a":
      raise ValueError("Missing Synergy HT result status separator")
    SynergyHT._check_status(frame[-5:])
    if sum(frame[1:-7]) & 0xFF != frame[-7]:
      raise ValueError("Synergy HT absorbance checksum mismatch")
    if re.fullmatch(rb"\d{3}:[0-5]\d:[0-5]\d\.\d", frame[1:12]) is None:
      raise ValueError("Malformed Synergy HT measurement timestamp")
    if not frame[-11:-8].isdigit():
      raise ValueError("Malformed Synergy HT measurement temperature")
    temperature = int(frame[-11:-8])
    data = []
    for row in range(rows):
      offset = 12 + row * (6 * columns + 2)
      row_data = frame[offset : offset + 6 * columns + 2]
      if row_data[-2:] != b"\r\n":
        raise ValueError("Missing Synergy HT row terminator")
      values = []
      for column in range(columns):
        field = row_data[6 * column : 6 * (column + 1)]
        if re.fullmatch(rb",[+-]\d{4}", field):
          values.append(int(field[1:]) / 1000)
        elif re.fullmatch(rb",\*[^,\r\n]{4}", field):
          values.append(float("nan"))
        else:
          raise ValueError(f"Malformed Synergy HT absorbance value: {field!r}")
      data.append(values)
    return data, None if temperature in (111, 999) else temperature / 10

  async def _read_absorbance_frame(
    self, rows: int, columns: int
  ) -> Tuple[List[List[float]], Optional[float]]:
    """Consume the asynchronous result, including short error/cancellation frames."""
    first = await self._read_exactly(1)
    if first in (b"\x10", b"\x03"):
      status = await self._read_exactly(5)
      self._check_status(status)
      raise RuntimeError(f"Synergy HT measurement interrupted: {first.hex()}")
    if first != b"\r":
      raise ValueError(f"Unexpected Synergy HT result start: {first!r}")
    frame = first + await self._read_exactly(22 + rows * (6 * columns + 2))
    return self._parse_absorbance(frame, rows, columns)

  async def read_absorbance(
    self,
    wavelength: int,
    plate_type: Literal["24-well", "96-well"],
    wells: Optional[Sequence[str]] = None,
    *,
    shake_duration: int = 0,
    shake_intensity: Literal["slow", "medium", "fast", "variable"] = "slow",
  ) -> List[AbsorbanceResult]:
    """Read endpoint absorbance in optical density (OD).

    Args:
      wavelength: Integer wavelength in nm; the reader checks its optical range.
      plate_type: The reader's built-in 24- or 96-well geometry. Load a compatible,
        unlidded plate with A1 at the tray's A1 position. Custom geometry is unsupported.
      wells: Well names such as ["A1", "B2"], or None for the whole plate. The reader
        measures the bounding rectangle; only requested wells are returned.
      shake_duration: Shake the plate before measuring, in whole seconds (0-65535).
        Zero disables shaking. This duration is added to read_timeout.
      shake_intensity: "slow" (default), "medium", "fast", or "variable", used when
        shake_duration is positive. These are device settings, not frequencies in Hz or RPM.

    Returns:
      A one-element list of AbsorbanceResult, matching the other BioTek readers.
      Its data is a full plate matrix indexed [row][column], with None for
      unselected wells and NaN for readings the instrument marks as overflow.
      It includes the wavelength, the temperature reported in the measurement
      frame (or None if unavailable), and the host's local completion timestamp.
      No blank subtraction or pathlength correction is applied. The tray must
      be closed before calling this method.
    """
    packet = self._absorbance_setup(wavelength, shake_duration, shake_intensity)
    rows, columns, preset, selected = self._selection(plate_type, wells)
    r0, r1 = min(r for r, _ in selected), max(r for r, _ in selected)
    c0, c1 = min(c for _, c in selected), max(c for _, c in selected)
    async with self._operation():
      logger.info("Reading Synergy HT absorbance at %s nm on a %s plate", wavelength, plate_type)
      if shake_duration:
        logger.info(
          "Shaking Synergy HT plate for %s seconds at intensity %s before reading",
          shake_duration,
          shake_intensity,
        )
      await self._exchange(b"V", packet)
      await self._exchange(b"{", preset)
      await self._exchange(b"i", b"1", ack_only=True)
      await self._exchange(b"r", bytes((r0 + 1, r1 + 1)))
      await self._exchange(b"%", bytes((c0 + 1, c1 + 1)))
      await self._exchange(b"k", b"0")
      await self._exchange(b"Q", b"1", ack_only=True)
      await self._exchange(b"t", b"40000\x03")
      await self._exchange(b"S")
      data, temperature = await asyncio.wait_for(
        self._read_absorbance_frame(r1 - r0 + 1, c1 - c0 + 1),
        self.read_timeout + shake_duration,
      )
      timestamp = datetime.now()
    result: List[List[Optional[float]]] = [[None] * columns for _ in range(rows)]
    for row, column in selected:
      result[row][column] = data[row - r0][column - c0]
    return [
      AbsorbanceResult(
        data=result, wavelength=wavelength, temperature=temperature, timestamp=timestamp
      )
    ]
