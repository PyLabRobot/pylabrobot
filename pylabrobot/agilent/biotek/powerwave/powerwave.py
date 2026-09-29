"""Driver for the Agilent BioTek PowerWave / PowerWaveX microplate spectrophotometer.

Page references are to the *PowerWaveX Operator's Manual* (Bio-Tek p/n 7261014
Rev D2), Appendix B "Computer Control".

This is an RS-232 instrument from the early 2000s, so it predates the USB/FTDI
BioTek readers in :mod:`pylabrobot.agilent.biotek.plate_reader_base` and speaks a
different command language. The plate carrier commands ``'J'`` and ``'A'`` are the
only ones the two generations share.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import List, Literal, Optional, Sequence, cast

from pylabrobot.agilent.biotek.plate_reader_base import AbsorbanceResult
from pylabrobot.io.serial import Serial
from pylabrobot.resources import Coordinate, Plate, PlateHolder, Well

from ._protocol import (
  ACK,
  DLE,
  ETX,
  MAX_WAVELENGTH,
  MIN_WAVELENGTH,
  NAK,
  STATUS_LENGTH,
  SUB,
  Command,
  PowerWaveError,
  PowerWaveNakError,
  PowerWaveParseError,
  build_assay_definition,
  configuration_has_incubator,
  count_overrange,
  encode_temperature_setpoint,
  parse_device_configuration,
  parse_plate,
  parse_status,
  parse_temperature,
  parse_uint16,
  parse_wavelength_table,
  verify_plate_checksum,
)

try:
  import serial as _serial  # noqa: F401

  HAS_SERIAL = True
  _SERIAL_IMPORT_ERROR: Optional[ImportError] = None
except ImportError as e:  # pragma: no cover - depends on the environment
  HAS_SERIAL = False
  _SERIAL_IMPORT_ERROR = e

logger = logging.getLogger(__name__)

CarrierPosition = Literal["in", "out"]


class PowerWaveX:
  """Agilent BioTek PowerWaveX microplate spectrophotometer.

  Absorbance reader with an optional incubator. Wavelengths are selected by a
  monochromator anywhere in 200-999 nm at 1 nm increments, and up to six can be
  read in a single pass of the plate. Driven over RS-232 at 9600 baud, 8 data
  bits, no parity, two stop bits.

  Example:
    >>> reader = PowerWaveX(name="reader", port="/dev/ttyUSB0")
    >>> await reader.setup()
    >>> await reader.open()
    ... # load a plate
    >>> await reader.close()
    >>> results = await reader.read_absorbance(plate, plate.get_all_items(), 630)
    >>> results[0].data[0][0]  # A1, in OD
  """

  #: Serial line settings fixed by the instrument (page B-5).
  BAUDRATE = 9600
  BYTESIZE = 8
  PARITY = "N"
  STOPBITS = 2

  #: Seconds to settle either side of flushing the stray NAK that follows an
  #: accepted assay definition table.
  _ASSAY_SETTLE_SECONDS = 1.0

  def __init__(
    self,
    name: str,
    port: Optional[str] = None,
    vid: Optional[int] = None,
    pid: Optional[int] = None,
    timeout: float = 70,
  ) -> None:
    """
    Args:
      name: Machine name, also used to name the plate holder resource.
      port: Serial port, for example ``"/dev/ttyUSB0"`` or ``"COM3"``. Either this
        or both ``vid`` and ``pid`` must be given.
      vid: USB vendor id of the serial adapter, as an alternative to ``port``.
      pid: USB product id of the serial adapter, as an alternative to ``port``.
      timeout: Default seconds to wait for a complete response. Plate reads are
        slow, so this is generous.
    """
    if not HAS_SERIAL:
      raise RuntimeError(
        "pyserial is not installed. Install with: pip install pylabrobot[serial]. "
        f"Import error: {_SERIAL_IMPORT_ERROR}"
      )

    self.timeout = timeout
    self._connected = False
    self._model: Optional[str] = None
    self._wavelength_table: Optional[List[int]] = None
    self._carrier_position: Optional[CarrierPosition] = None
    self._temperature_setpoint: Optional[int] = None
    self._assay_defined = False
    self._assay_wavelengths: List[int] = []

    self.io = Serial(
      human_readable_device_name="Agilent BioTek PowerWaveX",
      port=port,
      vid=vid,
      pid=pid,
      baudrate=self.BAUDRATE,
      bytesize=self.BYTESIZE,
      parity=self.PARITY,
      stopbits=self.STOPBITS,
      # Reads poll in one-second slices so that the operation deadlines below stay
      # responsive; per-operation waits are handled by _read_until.
      timeout=1,
      write_timeout=2,
    )

    self.plate_holder = PlateHolder(
      name=name + "_plate_holder",
      size_x=127.76,
      size_y=85.48,
      size_z=0,  # TODO: measure
      pedestal_size_z=0,
      child_location=Coordinate.zero(),  # TODO: measure
    )

  # ------------------------------------------------------------------------------------
  # Lifecycle
  # ------------------------------------------------------------------------------------

  async def setup(self) -> None:
    """Open the serial connection and identify the instrument.

    Reads the instrument configuration (page B-19) and the calibrated wavelength
    table (page B-11), both of which are then available without further traffic
    as :attr:`model` and :attr:`wavelength_table`.
    """
    logger.warning(
      "This PowerWaveX driver is only partly verified against hardware. Identification, "
      "the wavelength table, temperature, the minimum kinetic interval and carrier "
      "movement have been confirmed on a PowerWaveX w/ Incubator (firmware R06.20). "
      "Absorbance reading has NOT been confirmed on an instrument, only against "
      "captured traffic. Please report results at https://discuss.pylabrobot.org."
    )

    await self.io.setup()
    self._connected = True

    # The instrument may have left bytes queued from a previous session.
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()

    self._model = await self.request_model()
    self._wavelength_table = await self.request_wavelength_table()

    logger.info(
      "[PowerWaveX %s] connected: model=%s, calibrated wavelengths=%s",
      self.port,
      self._model,
      self._wavelength_table,
    )

  async def stop(self) -> None:
    """Close the serial connection."""
    await self.io.stop()
    self._connected = False
    self._carrier_position = None
    logger.info("[PowerWaveX %s] disconnected", self.port)

  # ------------------------------------------------------------------------------------
  # Properties
  # ------------------------------------------------------------------------------------

  @property
  def port(self) -> str:
    """The configured or detected serial port."""
    return cast(str, self.io.port)

  @property
  def connected(self) -> bool:
    """Whether :meth:`setup` has completed without a subsequent :meth:`stop`."""
    return self._connected

  @property
  def model(self) -> str:
    """The model name reported by the instrument during :meth:`setup`."""
    if self._model is None:
      raise RuntimeError("Model unknown. Did you call setup()?")
    return self._model

  @property
  def wavelength_table(self) -> List[int]:
    """The calibrated wavelengths in nm, read at :meth:`setup` (page B-11).

    A calibration set, not a constraint: the monochromator reads any wavelength in
    :attr:`abs_wavelength_range`, though gains are calibrated only for these.
    """
    if self._wavelength_table is None:
      raise RuntimeError("Wavelength table unknown. Did you call setup()?")
    return list(self._wavelength_table)

  @property
  def supports_heating(self) -> bool:
    """Whether the identified model includes the incubator."""
    return configuration_has_incubator(self.model)

  @property
  def supports_cooling(self) -> bool:
    """The PowerWave incubator heats only; it cannot cool below ambient."""
    return False

  @property
  def abs_wavelength_range(self) -> tuple[int, int]:
    """Readable absorbance wavelengths in nm (page 1-1).

    340 models cannot go below 340 nm; this driver does not detect that, since the
    instrument configuration word does not distinguish it for every variant.
    """
    return (MIN_WAVELENGTH, MAX_WAVELENGTH)

  @property
  def temperature_range(self) -> tuple[float, float]:
    """Supported incubator setpoint range in degrees C (page B-17)."""
    return (22.0, 50.0)

  @property
  def carrier_position(self) -> Optional[CarrierPosition]:
    """Where the plate carrier is, or ``None`` if it has not been moved yet."""
    return self._carrier_position

  # ------------------------------------------------------------------------------------
  # Identification
  # ------------------------------------------------------------------------------------

  async def request_model(self) -> str:
    """Read the instrument configuration and return the model name (page B-19).

    This command is answered with the configuration word and, unusually, no
    status string.
    """
    await self._send_command(Command.GET_INSTRUMENT_CONFIGURATION)
    payload = await self._read_exactly(2)
    model = parse_device_configuration(payload)
    logger.debug("[PowerWaveX %s] identified as %s", self.port, model)
    return model

  async def request_wavelength_table(self) -> List[int]:
    """Read the installed filter wavelengths in nm (page B-11).

    Unpopulated filter positions report ``000`` and are omitted.
    """
    await self._send_command(Command.GET_WAVELENGTH_TABLE)
    payload = await self._read_exactly(24)
    table = parse_wavelength_table(payload)
    await self._read_status()
    return table

  async def request_minimum_kinetic_interval(self) -> int:
    """Read the shortest usable kinetic interval in seconds (page B-13).

    Determined by the loaded assay definition, the strip range and the baud rate,
    so the value changes once an assay has been pushed.
    """
    await self._send_command(Command.GET_MINIMUM_KINETIC_INTERVAL)
    payload = await self._read_exactly(2)
    interval = parse_uint16(payload)
    await self._read_status()
    return interval

  # ------------------------------------------------------------------------------------
  # Plate carrier
  # ------------------------------------------------------------------------------------

  async def open(self) -> None:
    """Present the plate carrier outside the instrument so a plate can be loaded.

    PRESENT PLATE CARRIER (``'J'``), page B-7. Does nothing if the carrier is
    already known to be out.
    """
    if self._carrier_position == "out":
      logger.debug("[PowerWaveX %s] carrier already out", self.port)
      return

    await self._send_command(Command.PRESENT_PLATE_CARRIER)
    await self._read_status()
    self._carrier_position = "out"
    logger.info("[PowerWaveX %s] carrier out", self.port)

  async def close(self) -> None:
    """Store the plate carrier inside the instrument.

    STORE PLATE CARRIER (``'A'``), page B-7. Does nothing if the carrier is
    already known to be in.

    Note:
      Unlike the newer USB BioTek readers, this instrument takes no plate
      geometry with the store command. Geometry is a separate SET PLATE GEOMETRY
      (``'{'``) request, which this driver does not yet implement, so tall or
      lidded labware has not been exercised.
    """
    if self._carrier_position == "in":
      logger.debug("[PowerWaveX %s] carrier already in", self.port)
      return

    await self._send_command(Command.STORE_PLATE_CARRIER)
    await self._read_status()
    self._carrier_position = "in"
    logger.info("[PowerWaveX %s] carrier in", self.port)

  async def halt(self) -> None:
    """Abort a read or scan in progress (``'X'``, page B-12).

    All axes return to their home positions and the carrier is presented
    outside. This command is framed differently from every other: the instrument
    sends neither ACK nor a status string, only a single ``DLE`` once the abort
    has completed.
    """
    await self.io.write(bytes(Command.HALT.value))
    logger.debug("[PowerWaveX %s] sent HALT", self.port)

    response = await self._read_until(DLE)
    if not response.endswith(DLE):
      raise PowerWaveError(f"HALT was not acknowledged with DLE, got {response!r}")

    # The abort leaves the carrier presented outside (page B-12).
    self._carrier_position = "out"
    self._assay_defined = False
    logger.info("[PowerWaveX %s] halted, carrier out", self.port)

  # ------------------------------------------------------------------------------------
  # Absorbance reads
  # ------------------------------------------------------------------------------------

  async def read_absorbance(
    self,
    plate: Plate,
    wells: List[Well],
    wavelength: int,
  ) -> List[AbsorbanceResult]:
    """Read absorbance at one wavelength.

    Args:
      plate: The plate loaded in the carrier. Must be 8x12; see the note below.
      wells: The wells to read. Must currently be every well of ``plate``.
      wavelength: Wavelength in nm, 200 to 999. The monochromator selects it at
        1 nm increments (page 1-1), so any value in range is readable regardless
        of the stored wavelength table.

    Returns:
      A single-element list, for consistency with the other BioTek readers.

    Raises:
      NotImplementedError: If ``wells`` is a subset of the plate, or the plate is
        not 8x12. Both need commands this driver does not yet send: SET STRIP
        RANGE (``'%'``, page B-13) and SET PLATE GEOMETRY (``'{'``, page B-12).

    Note:
      The carrier is not moved. Call :meth:`close` first if the plate is still
      presented outside.
    """
    return await self.read_absorbances(plate, wells, [wavelength])

  async def read_absorbances(
    self,
    plate: Plate,
    wells: List[Well],
    wavelengths: Sequence[int],
  ) -> List[AbsorbanceResult]:
    """Read absorbance at up to six wavelengths in a single pass of the plate.

    The instrument's assay definition table holds up to six wavelengths and
    returns one data block per wavelength from one READ PLATE command (page B-9),
    so this is substantially faster than calling :meth:`read_absorbance` in a
    loop.

    Args:
      plate: The plate loaded in the carrier.
      wells: The wells to read. Must currently be every well of ``plate``.
      wavelengths: One to six wavelengths in nm, 200 to 999.

    Returns:
      One :class:`AbsorbanceResult` per requested wavelength, in the order asked.
    """
    self._require_full_plate(plate, wells)

    low, high = self.abs_wavelength_range
    for wavelength in wavelengths:
      if not low <= wavelength <= high:
        raise ValueError(f"Wavelength must be within {low}-{high} nm, got {wavelength}")

    uncalibrated = [w for w in wavelengths if w not in (self._wavelength_table or [])]
    if len(uncalibrated) > 0:
      logger.warning(
        "[PowerWaveX %s] %s nm not in the calibrated wavelength table %s. The read will "
        "still work, but gains are calibrated only for the stored table (page B-8).",
        self.port,
        uncalibrated,
        self._wavelength_table,
      )

    logger.info(
      "[PowerWaveX %s] read_absorbance: plate=%s, wavelengths=%s nm, wells=%d",
      self.port,
      plate.name,
      list(wavelengths),
      len(wells),
    )

    await self.push_assay_definition(wavelengths, read_type="endpoint")
    blocks = await self._read_plate(len(wavelengths), plate)

    temperature = await self._try_read_temperature()
    timestamp = datetime.now()

    results = []
    for wavelength, block in zip(wavelengths, blocks):
      data: List[List[Optional[float]]] = [[value for value in row] for row in block]
      overrange = count_overrange(block)
      if overrange > 0:
        logger.warning(
          "[PowerWaveX %s] %d of %d wells overrange at %d nm; readings above 3.000 OD are "
          "outside the instrument's specified range (page 1-1)",
          self.port,
          overrange,
          plate.num_items,
          wavelength,
        )
      results.append(
        AbsorbanceResult(
          data=data,
          wavelength=wavelength,
          temperature=temperature,
          timestamp=timestamp,
        )
      )

    return results

  async def push_assay_definition(
    self,
    wavelengths: Sequence[int],
    read_type: str = "endpoint",
    **kwargs,
  ) -> None:
    """Download a 170-byte assay definition table (``'V'``, page B-10).

    Args:
      wavelengths: One to six wavelengths in nm.
      read_type: ``"endpoint"``, ``"kinetic"`` or ``"scan"``.
      kwargs: Remaining assay fields, passed to
        :func:`._protocol.build_assay_definition`.
    """
    table = build_assay_definition(wavelengths, read_type=read_type, **kwargs)

    await self._send_command(Command.SET_ASSAY_DEFINITION)
    await self.io.write(table)
    await self._read_status()

    self._assay_wavelengths = list(wavelengths)
    self._assay_defined = True

    # The instrument emits a stray NAK shortly after accepting the table, with no
    # command outstanding. Observed repeatedly on a PowerWaveX w/ Incubator in 2021.
    # Settle, then discard it so it cannot be mistaken for the next reply.
    await asyncio.sleep(self._ASSAY_SETTLE_SECONDS)
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()
    await asyncio.sleep(self._ASSAY_SETTLE_SECONDS)

    logger.debug(
      "[PowerWaveX %s] assay defined: %s, %s nm", self.port, read_type, list(wavelengths)
    )

  async def _read_plate(self, num_blocks: int, plate: Plate) -> List[List[List[float]]]:
    """Run READ PLATE and collect one parsed data block per wavelength (page B-9).

    The response is an ACK, a status string, then for each wavelength a ``CR``
    start code, the rows, a ``^Z`` terminator and a raw checksum byte. A trailing
    status string is sent only if the read failed, so none is consumed here.
    """
    if not self._assay_defined:
      raise PowerWaveError(
        "No assay definition has been pushed. Call push_assay_definition() first, or use "
        "read_absorbance() which does it for you."
      )

    await self._send_command(Command.READ_PLATE)
    await self._read_status()

    blocks = []
    for index in range(num_blocks):
      frame = await self._read_until(SUB)
      checksum = await self._read_exactly(1)
      verify_plate_checksum(frame, checksum[0])
      blocks.append(parse_plate(frame, plate.num_items_y, plate.num_items_x))
      logger.debug(
        "[PowerWaveX %s] block %d/%d parsed, checksum %d verified",
        self.port,
        index + 1,
        num_blocks,
        checksum[0],
      )

    return blocks

  def _require_full_plate(self, plate: Plate, wells: List[Well]) -> None:
    """Reject the cases that need commands this driver does not yet send."""
    if (plate.num_items_y, plate.num_items_x) != (8, 12):
      raise NotImplementedError(
        f"{plate.name} is {plate.num_items_y}x{plate.num_items_x}. Only 8x12 plates are "
        "supported until SET PLATE GEOMETRY ('{', page B-12) is implemented."
      )

    parents = set(well.parent for well in wells)
    if len(parents) != 1 or parents.pop() is not plate:
      raise ValueError("All wells must belong to the specified plate")

    if len(wells) != plate.num_items:
      raise NotImplementedError(
        f"Reading a subset of wells ({len(wells)} of {plate.num_items}) needs SET STRIP "
        "RANGE ('%', page B-13), which is not implemented yet. Pass plate.get_all_items()."
      )

  async def _try_read_temperature(self) -> Optional[float]:
    """Read the chamber temperature for a result, returning None if unavailable."""
    if not self.supports_heating:
      return None
    try:
      return await self.request_current_temperature()
    except (PowerWaveError, TimeoutError) as e:
      logger.debug("[PowerWaveX %s] temperature unavailable for result: %s", self.port, e)
      return None

  # ------------------------------------------------------------------------------------
  # Incubator
  # ------------------------------------------------------------------------------------

  async def request_current_temperature(self) -> float:
    """Read the incubation chamber temperature in degrees C (page B-18).

    Averaged over four thermal zones. Accepted at any time, including during a
    read cycle, though the answer is deferred until any plate data transmission
    has finished.

    Raises:
      PowerWaveError: If the instrument has no working incubator.
    """
    await self._send_command(Command.GET_TEMPERATURE)
    payload = await self._read_exactly(2)
    try:
      temperature = parse_temperature(payload)
    except PowerWaveError:
      # The instrument flags an incubator error in the status string that follows.
      await self._read_status()
      raise
    await self._read_status()
    logger.info("[PowerWaveX %s] temperature: %.1f degrees C", self.port, temperature)
    return temperature

  async def set_temperature(self, temperature: int) -> None:
    """Set the incubator setpoint in whole degrees C (page B-17).

    Args:
      temperature: 22 to 50 degrees C, or 0 to switch the heaters off. The
        instrument accepts whole degrees only.

    Raises:
      NotImplementedError: If the identified model has no incubator.

    Note:
      The manual lists the setpoint as data bytes "1 - 2", but this instrument
      accepts a *single* raw byte holding the integer degrees. Confirmed on a
      PowerWaveX w/ Incubator (firmware R06.20), which returned status "000" to a
      one-byte payload. Sending two bytes has not been tried.
    """
    if not self.supports_heating:
      raise NotImplementedError(f"{self.model} has no incubator.")

    if self._temperature_setpoint == temperature:
      logger.debug("[PowerWaveX %s] setpoint already %d", self.port, temperature)
      return

    payload = encode_temperature_setpoint(temperature)

    await self._send_command(Command.SET_TEMPERATURE)
    await self.io.write(payload)
    await self._read_status()

    self._temperature_setpoint = temperature
    if temperature == 0:
      logger.info("[PowerWaveX %s] incubator off", self.port)
    else:
      logger.info("[PowerWaveX %s] incubator setpoint: %d degrees C", self.port, temperature)

  async def stop_heating(self) -> None:
    """Switch the incubator heaters off, by setting the setpoint to zero (page B-17)."""
    await self.set_temperature(0)

  @property
  def temperature_setpoint(self) -> Optional[int]:
    """The setpoint this driver last wrote, or ``None`` if it has written none.

    The instrument offers no way to read its setpoint back, so a value left in
    non-volatile memory by a previous session is not visible here.
    """
    return self._temperature_setpoint

  # ------------------------------------------------------------------------------------
  # Transport
  # ------------------------------------------------------------------------------------

  async def _send_command(self, command: Command) -> None:
    """Write a command byte and consume the ACK.

    Raises:
      PowerWaveNakError: If the instrument rejected the command.
      PowerWaveError: If the reply was neither ACK nor NAK.
    """
    await self.io.write(bytes(command.value))
    logger.debug("[PowerWaveX %s] sent %s", self.port, command.name)

    response = await self.io.read(1)
    if response == ACK:
      return
    if response == NAK:
      raise PowerWaveNakError(f"Instrument rejected {command.name} with NAK")
    if len(response) == 0:
      raise TimeoutError(f"No reply to {command.name} within {self.io.get_read_timeout()}s")
    raise PowerWaveError(f"Expected ACK or NAK after {command.name}, got {response!r}")

  async def _read_exactly(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    """Read exactly ``num_bytes``, or raise TimeoutError."""
    deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
    buffer = bytearray()

    while len(buffer) < num_bytes:
      if time.monotonic() > deadline:
        raise TimeoutError(
          f"Timed out reading {num_bytes} bytes, got {len(buffer)}: {bytes(buffer)!r}"
        )
      chunk = await self.io.read(num_bytes - len(buffer))
      buffer.extend(chunk)

    return bytes(buffer)

  async def _read_until(self, terminator: bytes, timeout: Optional[float] = None) -> bytes:
    """Read until ``terminator`` is seen, inclusive, or raise TimeoutError."""
    deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
    buffer = bytearray()

    while True:
      if buffer.endswith(terminator):
        return bytes(buffer)
      if time.monotonic() > deadline:
        raise TimeoutError(
          f"Timed out waiting for {terminator!r}, partial response: {bytes(buffer)!r}"
        )
      buffer.extend(await self.io.read(1))

  async def _read_status(self, timeout: Optional[float] = None) -> str:
    """Read and validate a status string (page B-21).

    Returns:
      The three-character status code, always ``"000"``, since anything else
      raises.

    Raises:
      PowerWaveStatusError: If the instrument reported an error.
      PowerWaveParseError: If the status string was malformed.
    """
    frame = await self._read_until(ETX, timeout=timeout)

    if len(frame) > STATUS_LENGTH:
      # Stale bytes ahead of the status string, most often a plate data checksum
      # left unread by an earlier command.
      logger.warning(
        "[PowerWaveX %s] discarding %d unexpected bytes before status string: %r",
        self.port,
        len(frame) - STATUS_LENGTH,
        frame[:-STATUS_LENGTH],
      )
      frame = frame[-STATUS_LENGTH:]
    elif len(frame) < STATUS_LENGTH:
      raise PowerWaveParseError(f"Truncated status string: {frame!r}")

    return parse_status(frame)

  def __str__(self) -> str:
    model = self._model or "PowerWaveX (not yet identified)"
    return f"{model} on {self.io.port if self._connected else '<not connected>'}"
