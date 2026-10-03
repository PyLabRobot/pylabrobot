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
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal, Optional, Sequence, Tuple, cast

from pylabrobot.agilent.biotek.plate_reader_base import AbsorbanceResult
from pylabrobot.io.serial import Serial
from pylabrobot.resources import Coordinate, Plate, PlateHolder, Well

from ._protocol import (
  ACK,
  CR,
  DLE,
  ETX,
  KINETIC_FLAGS,
  MAX_WAVELENGTH,
  MIN_WAVELENGTH,
  NAK,
  SCAN_SECONDS_PER_WAVELENGTH,
  SCAN_WHOLE_STRIP,
  STATUS_LENGTH,
  SUB,
  Command,
  PowerWaveError,
  PowerWaveNakError,
  PowerWaveFatalError,
  PowerWaveHaltedError,
  PowerWaveParseError,
  PowerWaveReadAbortedError,
  PowerWaveStatusError,
  PowerWaveSelfTestError,
  build_assay_definition,
  configuration_has_incubator,
  count_overrange,
  encode_plate_geometry,
  encode_scan_request,
  encode_strip_range,
  encode_temperature_setpoint,
  parse_device_configuration,
  parse_plate,
  parse_scan_data,
  parse_scan_wavelength_list,
  parse_self_test_report,
  parse_status,
  parse_temperature,
  parse_uint16,
  parse_wavelength_table,
  scan_wavelengths,
  verify_plate_checksum,
  verify_scan_checksum,
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
ScanSeries = Literal["single", "first", "next", "last"]


@dataclass
class SpectralScanResult:
  """One spectral scan of a well or a strip (page B-14).

  Attributes:
    wavelengths: The wavelengths visited, in nm and in scan order.
    data: Absorbance indexed ``[wavelength][well]``, in optical density units.
      A single-well scan has one entry per row; a strip scan has one per plate
      row. Overrange wells are :data:`~._protocol.OVERRANGE`.
    column: The 1-based plate column scanned.
    row: The 1-based plate row, or ``None`` when the whole strip was scanned.
    calibrated: Wavelengths the instrument calibrated before scanning.
    temperature: Chamber temperature in degrees C, or ``None``.
    timestamp: When the scan completed.
  """

  wavelengths: List[int]
  data: List[List[float]]
  column: int
  row: Optional[int]
  calibrated: List[int]
  temperature: Optional[float]
  timestamp: datetime

  def spectrum(self, well_index: int = 0) -> List[Tuple[int, float]]:
    """The (wavelength, absorbance) pairs for one well of this scan."""
    return [(w, self.data[i][well_index]) for i, w in enumerate(self.wavelengths)]


class PowerWaveX:
  """Agilent BioTek PowerWaveX microplate spectrophotometer.

  Absorbance reader with an optional incubator. Wavelengths are selected by a
  monochromator anywhere in 200-999 nm at 1 nm increments, and up to six can be
  read in a single pass of the plate. Driven over RS-232 at 9600 baud, 8 data
  bits, no parity, two stop bits.

  Only 96-well plates are usable on a 96-well instrument. The geometry command
  accepts 8x12 and 16x24, but a PowerWaveX w/ Incubator refuses the 384-well
  request with status ``0A0``, so :class:`PowerWaveStatusError` on
  :meth:`set_plate_geometry` means the instrument is not a 384-well model rather
  than that anything went wrong. The instrument recovers immediately and reads
  96-well plates as usual afterwards.

  Shaking is honoured: a scan asked to shake for 10 or 20 seconds took exactly
  that much longer than the same scan without it, to within 0.2 s. Kinetic
  intervals hold without drifting, 12 cycles at 15 s running to a mean gap of
  15.03 s and +0.38 s of total drift. The incubator tracks a new setpoint rather
  than merely accepting one, reaching 37.0 C from 30.0 C; it has no active
  cooling, so a lower setpoint only stops heating and the chamber then drifts
  down on its own.

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

  #: How long to wait for the status string that explains an abandoned read.
  _ABORT_STATUS_SECONDS = 10.0

  #: How long :meth:`halt` waits for a read in progress to let go of the serial
  #: port. A read notices within one io slice, so this only has to cover that.
  _HALT_HANDOVER_SECONDS = 5.0

  #: Seconds allowed per scanned wavelength, on top of :attr:`timeout`. An
  #: instance attribute so a test can collapse the wait.
  _SCAN_SECONDS_PER_WAVELENGTH = SCAN_SECONDS_PER_WAVELENGTH

  #: How long the line must stay quiet for :meth:`_drain_input` to call it clear,
  #: and the total time it will spend draining.
  _DRAIN_QUIET_SECONDS = 1.5
  _DRAIN_LIMIT_SECONDS = 20.0

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
    self._strip_range: Optional[tuple[int, int]] = None
    self._plate_geometry: Optional[tuple[int, int]] = None

    # Only one task may read the serial port at a time, and halt() has to be able
    # to take it away from a read in progress rather than queue behind it.
    self._halt_requested = asyncio.Event()
    self._reader_idle = asyncio.Event()
    self._reader_idle.set()

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
      "This PowerWaveX driver is verified against one instrument: a PowerWaveX w/ "
      "Incubator (firmware R06.20) passing its self-test. Identification, the "
      "wavelength table, temperature, the minimum kinetic interval, carrier movement, "
      "the assay definition, plate geometry, strip range, endpoint reads, kinetic "
      "reads and spectral scans have all been exercised on it end to end, including "
      "absorbing samples that read from 0.004 to 4.05 OD and saturated wells that "
      "come back as overrange. What has NOT been checked is absolute photometric "
      "accuracy: no reading has been compared against a known standard, only against "
      "an empty light path reading 0.000 and the expectation that a given sample "
      "absorbs less as the wavelength rises. "
      "Please report results at https://discuss.pylabrobot.org."
    )

    await self.io.setup()
    self._connected = True

    # A previous session may have died mid-operation, leaving the instrument part
    # way through a response. Flushing once is not enough: bytes already on the
    # wire arrive after the flush and would be read as the reply to the first
    # command. Drain until the line has been quiet for a moment.
    await self._drain_input()

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

    Warning:
      Reading near the bottom of this range can abandon the read and latch
      ``0F11`` or ``0F21`` ("signal out of range" on detector channel 1 or 2,
      whichever reads worst on the run), and often stops the instrument responding
      at all until it is power cycled. Observed on a PowerWaveX w/ Incubator
      (firmware R06.20), reading a full 96-well plate::

        200 nm, never calibrated   faulted 2 times out of 2
        205 nm, never calibrated   faulted 3 times out of 3
        208 nm, never calibrated   faulted
        209 nm, never calibrated   faulted
        210 nm, never calibrated   read normally
        212 nm, never calibrated   read normally
        214 nm, never calibrated   read normally
        215 nm, never calibrated   read normally
        395 nm, never calibrated   read normally
        200 nm, after a scan       read normally, twice, one from a cold start
        205 nm, after a scan       read normally

      The wavelength alone does not predict it and neither does the calibration
      state alone, but the pair does: READ PLATE sets its own gain for a
      wavelength it has not calibrated, and that attempt fails below 210 nm,
      where too little light reaches the detector. The threshold is that sharp:
      210 nm reads, 209 nm does not. The 208 and 209 nm faults both happened
      with 210 nm already calibrated immediately above them, so a calibrated
      neighbour does not help; each wavelength stands alone.
      :meth:`scan_plate` calibrates every wavelength it sweeps and manages it
      even at 200 nm, after which reads there succeed - 205 nm failed three times
      running uncalibrated and then read normally once a 200-210 nm scan in 5 nm
      steps had calibrated it.

      So to read below 210 nm, scan a band covering the wavelength first.
      A scan's :attr:`SpectralScanResult.calibrated` lists what it had to
      calibrate, which is a direct read-out of what was missing: that 5 nm scan
      reported ``[205]`` alone, 200 and 210 being already done.

      200 nm is a real floor and the firmware enforces it: a scan request for
      190-200 nm, legal in every other field, was answered ACK and status
      ``090`` without moving anything. So the bottom of this range cannot be
      crossed, which is the one far-UV probe that carries no risk at all.

      The order-sorting filter wheel is not the cause: 200, 205 and 215 nm share a
      single band, so it sits in the same position whether the read faults or
      not. See :data:`._protocol.ORDER_SORTING_FILTER_BANDS`.

      One scan per power-on is enough. The per-wavelength calibration survives
      both HALT and a full self-test - each was tried, and a following scan
      reported nothing left to calibrate - so it is only lost when the
      instrument is powered off. There is no command that clears it either, so
      a wavelength cannot be returned to its uncalibrated state remotely.

      The fault is never reported over the serial port, and the instrument stops
      answering it entirely until an operator acknowledges the fault at the front
      panel. It then sends a single ``DLE``, the same byte that completes a HALT,
      and resumes. Verified by listening across a deliberate fault: status
      ``000`` at 11 s as the read began, then 99 s of silence, then ``DLE`` at the
      instant the panel key was pressed.

      This is worth knowing because it looks like several different failures. The
      ``DLE`` seems to arrive immediately, or late, or never, purely according to
      whether someone pressed the key while anything was listening. Recovery is
      always the same and never needs a power cycle: press the panel key, which
      restores the serial port, then call :meth:`self_test` to clear the ``080``
      that stays latched until it runs.
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
    so the value changes once an assay has been pushed. Asked before the right
    assay is loaded it answers for the wrong one, which is why
    :meth:`read_absorbance_kinetic` queries it itself rather than taking the
    caller's word for the interval.

    The floor scales with the wavelength count, since the carriage makes a pass
    per wavelength. Measured on a PowerWaveX w/ Incubator (firmware R06.20) at
    9600 baud over a full 96-well plate, asked immediately after the assay was
    pushed: 10 s for one wavelength, 19 s for two, 38 s for four. It reports that
    floor whatever interval the assay asked for, so a shorter request does not
    hide it.
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

    Safe to call while a read or scan is running, which is the point of it: the
    read in progress is asked to stop, and raises
    :class:`PowerWaveHaltedError` in whichever task was awaiting it, before this
    takes the serial port and sends the command. That ordering matters because
    both would otherwise be polling the same bytes, and the read would swallow
    the ``DLE`` this waits for.

    Verified against a PowerWaveX w/ Incubator (firmware R06.20) by interrupting
    each kind of run in flight: an endpoint read 6 s in, a 31-wavelength scan 8 s
    in, and a ten-cycle kinetic run 25 s in. Each returned here with the ``DLE``
    in hand after 0.4 to 3.6 s, left the carrier outside, and raised
    :class:`PowerWaveHaltedError` in the interrupted call. Unlike a fault, a halt
    latches nothing: the next read worked with no :meth:`self_test` in between.

    Raises:
      PowerWaveError: If a read in progress does not let go of the serial port,
        or the instrument does not acknowledge with ``DLE``.
    """
    if not self._reader_idle.is_set():
      logger.info("[PowerWaveX %s] halting a read in progress", self.port)
      self._halt_requested.set()
      try:
        await asyncio.wait_for(self._reader_idle.wait(), timeout=self._HALT_HANDOVER_SECONDS)
      except (asyncio.TimeoutError, TimeoutError):
        raise PowerWaveError(
          f"A read in progress did not release the serial port within "
          f"{self._HALT_HANDOVER_SECONDS:.0f}s, so HALT was not sent. The instrument is "
          f"still reading."
        ) from None
      finally:
        self._halt_requested.clear()

    await self.io.write(bytes(Command.HALT.value))
    logger.debug("[PowerWaveX %s] sent HALT", self.port)

    # Whatever the abandoned read left on the wire arrives before the DLE, and is
    # discarded with it.
    response = await self._read_until(DLE)
    if not response.endswith(DLE):
      raise PowerWaveError(f"HALT was not acknowledged with DLE, got {response!r}")
    if len(response) > 1:
      logger.debug(
        "[PowerWaveX %s] discarded %d bytes of abandoned read before the DLE",
        self.port,
        len(response) - 1,
      )

    # The abort leaves the carrier presented outside (page B-12).
    self._carrier_position = "out"
    self._assay_defined = False
    self._strip_range = None
    logger.info("[PowerWaveX %s] halted, carrier out", self.port)

  # ------------------------------------------------------------------------------------
  # Absorbance reads
  # ------------------------------------------------------------------------------------

  def _check_read_wavelengths(self, wavelengths: Sequence[int]) -> None:
    """Range-check wavelengths for a read, and warn about uncalibrated ones.

    Shared by every read path so that the long-running ones are not the quiet
    ones. Deliberately a warning rather than a refusal: whether a wavelength is
    safe depends on calibration this driver cannot see, since a scan in an
    earlier session leaves the instrument calibrated and tracking that would mean
    holding state the instrument itself will not confirm. See
    :attr:`abs_wavelength_range` for what the risk actually is.

    Raises:
      ValueError: If a wavelength is outside the instrument's range.
    """
    low, high = self.abs_wavelength_range
    for wavelength in wavelengths:
      if not low <= wavelength <= high:
        raise ValueError(f"Wavelength must be within {low}-{high} nm, got {wavelength}")

    uncalibrated = [w for w in wavelengths if w not in (self._wavelength_table or [])]
    if len(uncalibrated) > 0:
      logger.warning(
        "[PowerWaveX %s] %s nm not in the calibrated wavelength table %s. The read sets "
        "its own gain, which is less accurate than the stored table (page B-8) and can "
        "hang the instrument outright where little light reaches the detector: below "
        "210 nm it abandons the read and waits for its front panel. scan_plate() over a "
        "band containing the wavelength calibrates it properly first; see "
        "abs_wavelength_range.",
        self.port,
        uncalibrated,
        self._wavelength_table,
      )

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
    first_strip, last_strip = self._strip_range_for(plate, wells)

    self._check_read_wavelengths(wavelengths)

    num_strips = last_strip - first_strip + 1
    logger.info(
      "[PowerWaveX %s] read_absorbance: plate=%s, wavelengths=%s nm, wells=%d, strips %d-%d",
      self.port,
      plate.name,
      list(wavelengths),
      len(wells),
      first_strip,
      last_strip,
    )

    # Held across the setup commands as well as the data, because a second
    # operation starting here would interleave its assay definition with this
    # one's data stream.
    async with self._reading():
      # Order matters (pages B-12, B-13): the assay definition resets the strip
      # range, and so does the geometry command, so both must follow it in this
      # order.
      await self.push_assay_definition(wavelengths, read_type="endpoint")
      await self.set_plate_geometry(plate.num_items_y, plate.num_items_x)
      if num_strips != plate.num_items_x:
        await self.set_strip_range(first_strip, last_strip)

      blocks = await self._read_plate(len(wavelengths), plate.num_items_y, num_strips)

    temperature = await self._try_read_temperature()
    timestamp = datetime.now()

    results = []
    for wavelength, block in zip(wavelengths, blocks):
      data = self._place_strips(block, plate, first_strip)
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

  async def read_absorbance_kinetic(
    self,
    plate: Plate,
    wells: List[Well],
    wavelengths: Sequence[int],
    interval: int,
    count: int,
    shake_speed: int = 1,
    total_read_time: Optional[int] = None,
  ) -> List[AbsorbanceResult]:
    """Read the plate repeatedly at a fixed interval (page B-10).

    The instrument runs the whole series itself once READ PLATE is sent, emitting
    one data block per wavelength per cycle. This call therefore blocks for
    roughly ``interval * count`` seconds.

    Args:
      plate: The plate loaded in the carrier.
      wells: The wells to read; widened to whole columns as for
        :meth:`read_absorbances`.
      wavelengths: One to six wavelengths in nm.
      interval: Seconds between reads. Must be at least
        :meth:`request_minimum_kinetic_interval`, which depends on the assay and
        the baud rate, and so is only known once the assay has been loaded. A
        shorter interval raises :class:`ValueError`, because the instrument
        rejects the read outright rather than running it late.
      count: Number of reads, 2 to 9999.
      shake_speed: 0 slow, 1 medium, 2 fast, 3 variable. Shaking runs between
        reads.
      total_read_time: Not supported; pass ``None``. Running a kinetic series by
        total time means the instrument ignores ``count``, so the driver cannot
        know how many blocks to expect. See :data:`._protocol.KINETIC_FLAGS`.

    Returns:
      One :class:`AbsorbanceResult` per wavelength per cycle, ordered by cycle
      then wavelength. Each is timestamped when its cycle's data finished
      arriving, not when the instrument measured it: the instrument reports no
      time of its own, and a read takes roughly ten seconds to transfer, so a
      cycle was measured somewhat before its timestamp. The spacing between
      timestamps is sound - twelve cycles at a 15 s interval held a mean gap of
      15.03 s with 0.38 s of total drift - so rates computed from differences are
      unaffected, while absolute times carry that offset.

    Note:
      Use :meth:`halt` to abort a run in progress.
    """
    first_strip, last_strip = self._strip_range_for(plate, wells)
    self._check_read_wavelengths(wavelengths)

    num_strips = last_strip - first_strip + 1

    if total_read_time is not None:
      raise NotImplementedError(
        "total_read_time is not supported: setting the total kinetic time makes the "
        "instrument ignore count and keep cycling, so the driver cannot tell when the "
        "run ends. Use count and interval instead."
      )

    # Held across the setup commands too, so a second operation cannot interleave
    # its assay definition with this run's cycles.
    async with self._reading():
      return await self._kinetic_exchange(
        plate,
        wells,
        wavelengths,
        interval,
        count,
        shake_speed,
        first_strip,
        last_strip,
        num_strips,
      )

  async def _kinetic_exchange(
    self,
    plate: Plate,
    wells: List[Well],
    wavelengths: Sequence[int],
    interval: int,
    count: int,
    shake_speed: int,
    first_strip: int,
    last_strip: int,
    num_strips: int,
  ) -> List[AbsorbanceResult]:
    """Load a kinetic assay and collect every cycle (pages B-10, B-13).

    Split out so :meth:`read_absorbance_kinetic` can hold the serial port across
    the whole run, which is what lets :meth:`halt` stop it.
    """
    await self.push_assay_definition(
      wavelengths,
      read_type="kinetic",
      flags=KINETIC_FLAGS,
      kinetic_interval=interval,
      kinetic_read_count=count,
      shake_speed=shake_speed,
    )
    await self.set_plate_geometry(plate.num_items_y, plate.num_items_x)
    if num_strips != plate.num_items_x:
      await self.set_strip_range(first_strip, last_strip)

    # Only meaningful once the assay is loaded: it depends on the assay, the strip
    # range and the baud rate (page B-13).
    minimum = await self.request_minimum_kinetic_interval()
    if interval < minimum:
      # The instrument does not run such a series late, it refuses it: a
      # PowerWaveX w/ Incubator asked for two wavelengths every 15 s against a
      # 19 s minimum answered READ PLATE with status 090. Raise here instead,
      # where the number it wants can be reported.
      raise ValueError(
        f"interval {interval}s is below the instrument's minimum of {minimum}s for this "
        f"assay. The minimum depends on the wavelength count, the strip range and the "
        f"baud rate, so it is only known once the assay is loaded; reading at {interval}s "
        f"would be rejected with status 090."
      )

    logger.info(
      "[PowerWaveX %s] kinetic read: %s nm, %d cycles every %ds, about %d minutes",
      self.port,
      list(wavelengths),
      count,
      interval,
      round(interval * count / 60),
    )

    # Each cycle arrives an interval apart, so the per-block deadline has to cover
    # the wait as well as the transfer.
    block_timeout = interval + self.timeout

    results: List[AbsorbanceResult] = []
    await self._start_read()

    for cycle in range(count):
      blocks = await self._collect_blocks(
        len(wavelengths), plate.num_items_y, num_strips, timeout=block_timeout
      )
      # Stamped once the cycle's data is in hand. Taking it before the wait
      # named the moment this loop started waiting, which for every cycle after
      # the first is when the previous cycle's data finished arriving - well
      # before the cycle it was labelling.
      timestamp = datetime.now()
      for wavelength, block in zip(wavelengths, blocks):
        results.append(
          AbsorbanceResult(
            data=self._place_strips(block, plate, first_strip),
            wavelength=wavelength,
            temperature=None,
            timestamp=timestamp,
          )
        )
      logger.info("[PowerWaveX %s] kinetic cycle %d/%d", self.port, cycle + 1, count)

    # One marker closes the whole run, however many cycles it produced.
    await self._consume_end_of_data()

    return results

  async def set_plate_geometry(self, num_rows: int, num_columns: int) -> None:
    """Select the microplate geometry (``'{'``, page B-12).

    Must be sent *after* the assay definition table, which is why
    :meth:`read_absorbances` sequences it that way. Sending it resets the strip
    range to the full plate, so any strip range must follow it.

    The manual recommends issuing this even for 96-well plates, which are the
    default.

    Args:
      num_rows: 8 for a 96-well plate, 16 for a 384-well plate.
      num_columns: 12 for a 96-well plate, 24 for a 384-well plate.

    Raises:
      NotImplementedError: For any layout the instrument has no code for.
      PowerWaveStatusError: If the instrument rejects the geometry. A 96-well
        PowerWaveX answers ``0A0`` to a 384-well request.

    Note:
      The manual documents only an ACK for this command, with no status string.
      That is wrong: a PowerWaveX w/ Incubator (firmware R06.20) sends a full
      status string after the geometry byte, verified directly. Not consuming it
      leaves five bytes queued and desynchronises every later command.
    """
    payload = encode_plate_geometry(num_rows, num_columns)

    await self._send_command(Command.SET_PLATE_GEOMETRY)
    await self.io.write(payload)
    await self._read_status()

    # Kept so that a later strip range can be checked against the geometry that
    # is actually selected, rather than against the widest the family allows.
    self._plate_geometry = (num_rows, num_columns)

    self._strip_range = (1, num_columns)
    logger.debug("[PowerWaveX %s] plate geometry set to %dx%d", self.port, num_rows, num_columns)

  async def set_strip_range(self, first_strip: int, last_strip: int) -> None:
    """Restrict the read to a contiguous range of plate columns (``'%'``, page B-13).

    Strips are numbered plate columns, from 1. The instrument cannot skip strips
    and cannot select rows, so every row of the chosen columns is always read.

    The range reverts to the full plate whenever a new assay definition table is
    loaded, and whenever SET PLATE GEOMETRY is sent, so this must be the last of
    the three.

    Args:
      first_strip: First column to read, from 1.
      last_strip: Last column to read, inclusive.

    Raises:
      ValueError: If the range falls outside 1 to :data:`._protocol.MAX_STRIP`, or
        outside the width of the geometry currently selected.

    Note:
      The instrument does not range-check these itself; it answered ``000`` to a
      first strip of 0 when tested, and accepted columns 13 to 24 against a
      96-well geometry. The bounds are enforced here instead, against the
      selected geometry where :meth:`set_plate_geometry` has established one.
    """
    if self._plate_geometry is not None:
      _, num_columns = self._plate_geometry
      if last_strip > num_columns:
        raise ValueError(
          f"last_strip {last_strip} is outside the {num_columns}-column geometry "
          f"currently selected. The instrument accepts the request and reads columns "
          f"that do not exist."
        )

    payload = encode_strip_range(first_strip, last_strip)

    await self._send_command(Command.SET_STRIP_RANGE)
    await self.io.write(payload)
    await self._read_status()

    self._strip_range = (first_strip, last_strip)
    logger.debug("[PowerWaveX %s] strip range set to %d-%d", self.port, first_strip, last_strip)

  async def self_test(self, timeout: float = 180, raise_on_failure: bool = True) -> str:
    """Run the instrument self-test and return its report (``'*'``, page B-16).

    This is the documented remedy for status ``080``, and the only way to learn
    *why* an instrument is refusing to read. It recalibrates gains and should be
    run after any change to the wavelength table.

    Args:
      timeout: Seconds to allow. The test moves every axis and is slow.
      raise_on_failure: Whether to raise when the report declares a failure.

    Returns:
      The full ASCII report, including the per-wavelength Air and Dark counts and
      the incubator zone results.

    Raises:
      PowerWaveSelfTestError: If the report declares a failure and
        ``raise_on_failure`` is set.

    Note:
      The status string that closes this response reports whether the *command*
      ran, not whether the test passed. A failing instrument still answers
      ``"000"``, so the verdict is taken from the report body instead.
    """
    await self._send_command(Command.SELF_TEST)

    frame = await self._read_until(ETX, timeout=timeout)
    if len(frame) < STATUS_LENGTH:
      raise PowerWaveParseError(f"Truncated self-test response: {frame!r}")

    report = frame[:-STATUS_LENGTH].decode("latin-1")
    parse_status(frame[-STATUS_LENGTH:])

    code = parse_self_test_report(report)
    if code is None:
      logger.info("[PowerWaveX %s] self-test passed", self.port)
      return report

    error = PowerWaveSelfTestError(code, report)
    logger.error("[PowerWaveX %s] %s", self.port, error)
    if raise_on_failure:
      raise error
    return report

  # ------------------------------------------------------------------------------------
  # Documented but not implemented
  #
  # These four Appendix B commands are deliberately unimplemented. They are declared
  # here so the gap is discoverable from the API rather than by reading the manual,
  # and so a caller gets a specific explanation instead of AttributeError.
  # ------------------------------------------------------------------------------------

  async def configure_wavelengths(self, wavelengths: Sequence[int]) -> None:
    """Not implemented: write the calibration wavelength table (``'M'``, page B-8).

    This rewrites the table in the instrument's **non-volatile memory**, and the
    manual requires a self-test afterwards to recalibrate gains for the new set.
    It is only correct after physically changing the optics, so it is left out
    rather than offered as a routine call.

    The encoder exists and is tested, so wiring this up is small:
    :func:`._protocol.encode_wavelength_table` builds the 24-byte payload, which
    is sent after the command byte and followed by ``ETX``, then
    :meth:`self_test`.

    Raises:
      NotImplementedError: Always.
    """
    raise NotImplementedError(
      "CONFIGURE WAVELENGTHS ('M', page B-8) is not implemented. It rewrites the "
      "calibration table in non-volatile memory and requires a self-test afterwards, "
      "so it is only appropriate after the optics have been changed. See "
      "encode_wavelength_table() if you need to send it yourself."
    )

  async def scan_plate(
    self,
    plate: Plate,
    start_wavelength: int,
    stop_wavelength: int,
    step: int,
    well: Optional[Well] = None,
    column: Optional[int] = None,
    calibrate: bool = False,
    shake_time: int = 0,
    shake_speed: int = 0,
    series: ScanSeries = "single",
  ) -> SpectralScanResult:
    """Spectrally scan one well, or one whole strip (``'&'``, page B-14).

    The monochromator sweeps from ``start_wavelength`` to ``stop_wavelength`` in
    ``step`` increments, reading the selected well or column at each. This is a
    different operation from :meth:`read_absorbance`, which reads the whole plate
    at up to six fixed wavelengths.

    Args:
      plate: The plate loaded in the carrier.
      start_wavelength: First wavelength in nm, 200 to 999.
      stop_wavelength: Last wavelength in nm, above the start.
      step: Increment in nm. At least two wavelengths must be scanned, so this
        cannot exceed the span.
      well: The single well to scan. Give exactly one of ``well`` or ``column``.
      column: The 1-based column to scan in full, all rows at once.
      calibrate: Calibrate every wavelength first. Without it the instrument
        still calibrates any wavelength not scanned since power-up, and any with
        a previous error. Recommended when absolute accuracy matters.
      shake_time: Seconds to shake before scanning, 0 to 99.
      shake_speed: 0 slow, 1 medium, 2 fast, 3 cycle.
      series: Use ``"first"``, ``"next"`` and ``"last"`` to scan adjacent strips
        efficiently. They must be contiguous and go left to right, because the
        first pass for the next strip is collected during the current one.

    Returns:
      A :class:`SpectralScanResult` whose ``data`` is indexed
      ``[wavelength][well]``.

    Raises:
      ValueError: If neither or both of ``well`` and ``column`` are given, or any
        argument is out of range.

        These checks are the only thing standing between a caller and a hung
        instrument, so do not relax them. The instrument validates a scan
        request only partly, and not the fields that matter most. A PowerWaveX
        w/ Incubator (firmware R06.20) sent row 99, column 99, 999 to 200 nm
        with a step of 0 answered ACK and status ``000``, accepting every
        impossible field, and then stopped responding - neither HALT nor any
        other command brought it back, and it had to be recovered at the front
        panel. A step of 0 in particular gives the firmware no way to finish
        sweeping. It does check the wavelength bounds, refusing 190 nm with
        status ``090``, so a NAK or an error status proves nothing about the
        other fields.
      NotImplementedError: For a whole-strip scan of a plate that is not 8 rows;
        the manual warns against row "00" on 384-well plates.

    Note:
      Scanning a whole strip takes two passes, because even-row detectors sit one
      column to the left. The driver handles the first pass transparently.

      The geometry command must precede a scan on any plate that is not the
      standard 96-well, which :meth:`set_plate_geometry` does here.

      A scan and a plate read agree. Scanning three wells of a part-covered
      carrier spanning most of the usable range, then comparing each against the
      620 nm plate read of the same wells, gave 0.114 against 0.098, 1.884
      against 1.873, and 2.230 against 2.209 - within 0.021 OD, from two
      different instrument operations parsed by two different code paths. Each
      well's spectrum also fell monotonically from about 580 nm upwards, and the
      wells saturated in the order their plate readings predicted: none, three
      and nine wavelengths overrange respectively.
    """
    if (well is None) == (column is None):
      raise ValueError("Give exactly one of well or column")

    if well is not None:
      if well.parent is not plate:
        raise ValueError("The well must belong to the specified plate")
      row_index, column_index = well.get_row(), well.get_column()
      row, target_column = row_index + 1, column_index + 1
      num_wells = 1
    else:
      assert column is not None
      if not 1 <= column <= plate.num_items_x:
        raise ValueError(
          f"column must be 1-{plate.num_items_x} for this {plate.num_items_y}x"
          f"{plate.num_items_x} plate, got {column}. The instrument does not check "
          f"this: it accepted row 99 and column 99 and then stopped responding."
        )
      if plate.num_items_y != 8:
        raise NotImplementedError(
          "Scanning a whole strip is only supported on 8-row plates. The manual warns "
          'against row "00" on 384-well plates (page B-14); scan wells individually.'
        )
      row, target_column = SCAN_WHOLE_STRIP, column
      num_wells = plate.num_items_y

    request = encode_scan_request(
      row=row,
      column=target_column,
      start_wavelength=start_wavelength,
      stop_wavelength=stop_wavelength,
      step=step,
      series=series,
      calibrate=calibrate,
      shake_time=shake_time,
      shake_speed=shake_speed,
    )
    expected = scan_wavelengths(start_wavelength, stop_wavelength, step)

    logger.info(
      "[PowerWaveX %s] spectral scan: %s, %d-%d nm in %d nm steps, %d wavelengths",
      self.port,
      f"well row {row} column {target_column}" if row else f"strip {target_column}",
      start_wavelength,
      stop_wavelength,
      step,
      len(expected),
    )

    async with self._reading():
      await self.set_plate_geometry(plate.num_items_y, plate.num_items_x)
      return await self._scan_exchange(
        plate, request, expected, num_wells, row, target_column, shake_time
      )

  async def _scan_exchange(
    self,
    plate: Plate,
    request: bytes,
    expected: List[int],
    num_wells: int,
    row: int,
    target_column: int,
    shake_time: int,
  ) -> SpectralScanResult:
    """Send a prepared scan request and collect its response (page B-14).

    Split out so :meth:`scan_plate` can hold the serial port across the whole
    exchange, which is what lets :meth:`halt` interrupt a scan.
    """
    # One budget for every phase of the scan, because the slow parts come before
    # the data: the instrument shakes first, then calibrates whatever wavelengths
    # it must, and only then sweeps. Timing any one phase against the plain
    # response timeout fails a scan that is merely slow - a 31-wavelength
    # calibrated scan, or any shake approaching its 99 second maximum, outlasts it
    # before the first wavelength list arrives.
    timeout = self.timeout + shake_time + self._SCAN_SECONDS_PER_WAVELENGTH * len(expected)

    await self._send_command(Command.SCAN_PLATE)
    await self.io.write(request + ETX)

    # The request is acknowledged a second time, after ETX (page B-14). Poll for
    # it rather than reading one io slice, as _send_command does: the instrument
    # is free to take its time here.
    acknowledgement = await self._read_exactly(1, timeout=timeout)
    if acknowledgement == NAK:
      raise PowerWaveNakError("Instrument rejected the scan request: invalid arguments")
    if acknowledgement != ACK:
      raise PowerWaveError(f"Expected ACK or NAK after the scan request, got {acknowledgement!r}")

    await self._read_status(timeout=timeout)

    # Two wavelength lists precede the data. Both terminators are always sent,
    # even when the list is empty.
    calibrated = parse_scan_wavelength_list(await self._read_until(SUB, timeout=timeout))
    first_pass = parse_scan_wavelength_list(await self._read_until(SUB, timeout=timeout))
    logger.debug(
      "[PowerWaveX %s] scan calibrated %s, first pass %s", self.port, calibrated, first_pass
    )

    # A single CR marks the start of the data, then one line per wavelength, then
    # one ^Z and one checksum byte for the whole series.
    try:
      start_code = await self._read_exactly(1, timeout=timeout)
    except TimeoutError as silence:
      raise PowerWaveReadAbortedError(
        f"No scan data arrived: the instrument sent nothing for {timeout:.0f}s after "
        f"acknowledging the scan. It has halted and is waiting for the fault to be "
        f"acknowledged at its front panel. Press the panel key, note the code it shows, "
        f"then call self_test() to clear the latched 080."
      ) from silence
    if start_code == DLE:
      await self._raise_read_aborted(0, len(expected))
    if start_code != CR:
      raise PowerWaveParseError(f"Expected CR data start code, got {start_code!r}")

    section = await self._read_until(SUB, timeout=timeout)
    checksum = (await self._read_exactly(1, timeout=timeout))[0]
    verify_scan_checksum(section, checksum)

    wavelengths, data = parse_scan_data(section, num_wells)

    if wavelengths != expected:
      logger.warning(
        "[PowerWaveX %s] scan returned wavelengths %s, expected %s",
        self.port,
        wavelengths,
        expected,
      )

    return SpectralScanResult(
      wavelengths=wavelengths,
      data=data,
      column=target_column,
      row=row if row != SCAN_WHOLE_STRIP else None,
      calibrated=calibrated,
      temperature=await self._try_read_temperature(),
      timestamp=datetime.now(),
    )

  async def request_plate_barcode(self) -> str:
    """Not implemented: read the microplate barcode (``'s'``, page B-19).

    Requires the barcode scanner option. The manual notes the instrument returns
    a null string if no barcode is found after two passes of the carrier.

    Raises:
      NotImplementedError: Always.
    """
    raise NotImplementedError(
      "GET PLATE BARCODE ('s', page B-19) is not implemented. It requires the barcode "
      "scanner option, which was not available to develop against."
    )

  async def close_plate_access_door(self) -> None:
    """Not implemented: close the robotic access door (``')'``, page B-20).

    Only meaningful on the robotic-access-door models, whose configuration words
    are 0x00B0 to 0x00BA, 0x00F2 and 0x00FA. A model without the door NAKs the
    command, so :attr:`model` tells you whether it could ever apply.

    Raises:
      NotImplementedError: Always.
    """
    raise NotImplementedError(
      "CLOSE PLATE ACCESS DOOR (')', page B-20) is not implemented. It applies only to "
      f"robotic-access-door models; this one reports {self._model or 'an unknown model'}."
    )

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

  async def _read_plate(
    self, num_blocks: int, num_rows: int, num_columns: int
  ) -> List[List[List[float]]]:
    """Run READ PLATE and collect one parsed data block per wavelength (page B-9).

    The response is an ACK, a status string, then for each wavelength a ``CR``
    start code, the rows, a ``^Z`` terminator and a raw checksum byte. A trailing
    status string is sent only if the read failed, so none is consumed here.

    Args:
      num_blocks: One per wavelength in the loaded assay definition.
      num_rows: Rows in the loaded plate geometry.
      num_columns: Columns actually read, which is the strip range and not
        necessarily the plate width.
    """
    await self._start_read()
    blocks = await self._collect_blocks(num_blocks, num_rows, num_columns)
    await self._consume_end_of_data()
    return blocks

  async def _start_read(self) -> None:
    """Send READ PLATE and consume the status string that precedes the data."""
    if not self._assay_defined:
      raise PowerWaveError(
        "No assay definition has been pushed. Call push_assay_definition() first, or use "
        "read_absorbance() which does it for you."
      )

    await self._send_command(Command.READ_PLATE)
    await self._read_status()

  async def _collect_blocks(
    self,
    num_blocks: int,
    num_rows: int,
    num_columns: int,
    timeout: Optional[float] = None,
  ) -> List[List[List[float]]]:
    """Read and verify ``num_blocks`` consecutive plate data blocks.

    Each block is a ``CR`` start code, the rows, a ``^Z`` terminator and a raw
    checksum byte. The checksum byte must be consumed here: leaving it in the
    stream makes it the first byte of the next block, which is how the original
    2021 kinetic loop ended up slicing frames by hand.
    """
    blocks = []
    for index in range(num_blocks):
      try:
        start = await self._read_exactly(1, timeout=timeout)
      except TimeoutError as silence:
        # Nothing at all arrived. A fault can abandon the read without the DLE
        # below, and can take the serial interface down with it.
        raise PowerWaveReadAbortedError(
          f"No data block arrived after {index} of {num_blocks} "
          f"block{'' if num_blocks == 1 else 's'}: the instrument sent nothing for "
          f"{timeout if timeout is not None else self.timeout:.0f}s. It has halted and is "
          f"waiting for the fault to be acknowledged at its front panel, and will not "
          f"answer the serial port until then. Press the panel key, note the code it "
          f"shows, then call self_test() to clear the latched 080. No power cycle is "
          f"needed."
        ) from silence
      if start == DLE:
        await self._raise_read_aborted(index, num_blocks)
      frame = start + await self._read_until(SUB, timeout=timeout)
      checksum = await self._read_exactly(1, timeout=timeout)
      verify_plate_checksum(frame, checksum[0])
      blocks.append(parse_plate(frame, num_rows, num_columns))
      logger.debug(
        "[PowerWaveX %s] block %d/%d parsed, checksum %d verified",
        self.port,
        index + 1,
        num_blocks,
        checksum[0],
      )

    return blocks

  def _strip_range_for(self, plate: Plate, wells: List[Well]) -> tuple[int, int]:
    """Work out the strip range covering ``wells``, validating the selection.

    The instrument reads whole columns only, so a selection that does not fill
    every row of its columns is widened to the enclosing strips. Those extra
    wells are measured and reported rather than discarded.

    Returns:
      The inclusive, 1-based ``(first_strip, last_strip)`` to read.
    """
    if len(wells) == 0:
      raise ValueError("No wells to read")

    parents = set(well.parent for well in wells)
    if len(parents) != 1 or parents.pop() is not plate:
      raise ValueError("All wells must belong to the specified plate")

    # Validates the geometry early, before anything is sent.
    encode_plate_geometry(plate.num_items_y, plate.num_items_x)

    columns = [well.get_column() for well in wells]
    first_strip, last_strip = min(columns) + 1, max(columns) + 1

    covered = (last_strip - first_strip + 1) * plate.num_items_y
    if covered != len(wells):
      logger.info(
        "[PowerWaveX %s] the selection of %d wells is not a whole block of columns; "
        "reading strips %d-%d (%d wells) and reporting all of them, since the instrument "
        "cannot select rows (page B-13)",
        self.port,
        len(wells),
        first_strip,
        last_strip,
        covered,
      )

    return first_strip, last_strip

  def _place_strips(
    self, block: List[List[float]], plate: Plate, first_strip: int
  ) -> List[List[Optional[float]]]:
    """Position a strip-range data block within a full-plate grid.

    Columns outside the strip range were never measured and stay ``None``.
    """
    data: List[List[Optional[float]]] = [
      [None] * plate.num_items_x for _ in range(plate.num_items_y)
    ]
    for row_index, row in enumerate(block):
      for offset, value in enumerate(row):
        data[row_index][first_strip - 1 + offset] = value
    return data

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

    # The io layer reads in one-second slices, so poll until this driver's own
    # deadline. An instrument that is still finishing a read or a carriage move
    # takes several seconds to acknowledge the next command; waiting only one
    # slice made a second read in the same session fail intermittently.
    deadline = time.monotonic() + self.timeout
    response = b""
    while len(response) == 0:
      self._check_halted()
      if time.monotonic() > deadline:
        raise TimeoutError(f"No reply to {command.name} within {self.timeout}s")
      response = await self.io.read(1)

    if response == ACK:
      return
    if response == NAK:
      raise PowerWaveNakError(f"Instrument rejected {command.name} with NAK")
    raise PowerWaveError(f"Expected ACK or NAK after {command.name}, got {response!r}")

  @contextlib.asynccontextmanager
  async def _reading(self):
    """Hold the serial port for one long operation, so :meth:`halt` can take it.

    A read or scan occupies the port for seconds to minutes, and the instrument
    offers no way to ask what it is doing, so the only way to abort one is to
    stop reading and send HALT. Without an owner, HALT issued from another task -
    a GUI's stop button, say - races the read for the same bytes: whichever polls
    first swallows the other's reply, which is how HALT came to consume a data
    block and then time out waiting for a ``DLE`` the read had already eaten.

    While this is held, :meth:`halt` sets a flag that the read loops notice and
    raise :class:`PowerWaveHaltedError` on, so the port is handed over rather
    than contended for.
    """
    if not self._reader_idle.is_set():
      raise PowerWaveError(
        "Another read or scan is already using the serial port. Wait for it to "
        "finish, or call halt() to abort it."
      )

    self._halt_requested.clear()
    self._reader_idle.clear()
    try:
      yield
    finally:
      self._reader_idle.set()

  def _check_halted(self) -> None:
    """Abandon a read that :meth:`halt` has asked to stop."""
    if self._halt_requested.is_set():
      raise PowerWaveHaltedError(
        "The read was interrupted by halt(); the serial port has been handed over."
      )

  async def _drain_input(self) -> None:
    """Discard anything the instrument is still sending, until the line goes quiet.

    A session that was interrupted mid-response - a crashed script, an abandoned
    plate read - leaves the rest of that response in flight. Those bytes would
    otherwise be read as the reply to the next command, which surfaces as an
    unexpected byte in place of an ACK.
    """
    await self.io.reset_input_buffer()
    await self.io.reset_output_buffer()

    # A non-positive quiet window means "flush only", which is how the tests opt
    # out: their scripted transport answers a read before any command is sent,
    # whereas a real instrument stays silent until it is asked something.
    if self._DRAIN_QUIET_SECONDS <= 0:
      return

    discarded = bytearray()
    deadline = time.monotonic() + self._DRAIN_LIMIT_SECONDS
    quiet_until = time.monotonic() + self._DRAIN_QUIET_SECONDS

    while time.monotonic() < quiet_until and time.monotonic() < deadline:
      chunk = await self.io.read(64)
      if len(chunk) > 0:
        discarded.extend(chunk)
        quiet_until = time.monotonic() + self._DRAIN_QUIET_SECONDS

    if len(discarded) > 0:
      logger.warning(
        "[PowerWaveX %s] discarded %d stale bytes left by a previous session: %r",
        self.port,
        len(discarded),
        bytes(discarded[:64]),
      )

  async def _raise_read_aborted(self, done: int, total: int) -> None:
    """Explain a read or scan the instrument abandoned, then raise.

    A read that faults part way through is not reported inside the data stream.
    The instrument halts, shows the fault on its front panel, and stops answering
    the serial port until an operator acknowledges it there; it then sends
    ``DLE`` - the byte that completes a HALT - where the next block's ``CR`` start
    code belongs. So this arrives only when someone is at the instrument while a
    read is outstanding, which is why the same fault can look immediate, late, or
    silent. Anything that follows is decoded by
    :func:`._protocol.parse_status`, including the four-character ELx codes that
    carry a real description.

    Verified on a PowerWaveX w/ Incubator (firmware R06.20) reading an
    uncalibrated 205 nm: status ``000``, 99 s of silence, then ``DLE`` at the
    moment the panel key was pressed, with ``0F11`` ("signal out of range") on
    the display and ``080`` latched until :meth:`self_test` ran. See
    :attr:`abs_wavelength_range`.

    Args:
      done: Data blocks received before the abort.
      total: Blocks the command should have produced.

    Raises:
      PowerWaveReadAbortedError: Always, carrying the reported code when the
        instrument sent one.
    """
    where = f"after {done} of {total} data block{'' if total == 1 else 's'}"
    try:
      status = await self._read_exactly(
        STATUS_LENGTH, timeout=min(self._ABORT_STATUS_SECONDS, self.timeout)
      )
    except TimeoutError:
      status = b""

    if len(status) > 0:
      try:
        code = parse_status(status)
      except (PowerWaveStatusError, PowerWaveFatalError) as reported:
        raise PowerWaveReadAbortedError(
          f"The instrument abandoned the read {where}: {reported}. The fault is shown on "
          f"its front panel and stays latched there until cleared; readings will keep "
          f"failing with 080 until it is.",
          code=getattr(reported, "code", None),
        ) from reported
      raise PowerWaveReadAbortedError(
        f"The instrument abandoned the read {where}, then reported status {code}.", code=code
      )

    raise PowerWaveReadAbortedError(
      f"The instrument abandoned the read {where} and reported no status. Check its front "
      f"panel for the fault code and run self_test()."
    )

  async def _consume_end_of_data(self) -> None:
    """Consume the ``^Z`` that closes a whole READ PLATE response.

    The manual documents a ``^Z`` terminator and a checksum per data block and
    stops there. A PowerWaveX w/ Incubator (firmware R06.20) also sends one extra
    ``^Z`` after the last block's checksum - once per READ PLATE command, however
    many blocks or kinetic cycles it produced. Verified directly: a one-wavelength
    endpoint read ends ``^Z r ^Z``, and a three-cycle kinetic read sends three
    blocks and a single trailing ``^Z``.

    It does not follow the checksum promptly: the instrument returns the carriage
    home first, which measured 7.3 s after a one-wavelength read and 7.7 s after a
    two-wavelength one. So it waits out the driver's full response timeout rather
    than a short window. Firmware that does not send it is tolerated: this only
    warns, at the cost of one such wait per read.

    Leaving it queued makes it the reply to the next command, which is how a
    second read in one session failed with an unexpected ``^Z`` in place of an
    ACK.
    """
    try:
      trailer = await self._read_exactly(1)
    except TimeoutError:
      logger.warning(
        "[PowerWaveX %s] no ^Z end-of-data marker after the last data block; "
        "this firmware may not send one",
        self.port,
      )
      return

    if trailer != SUB:
      logger.warning(
        "[PowerWaveX %s] expected a ^Z end-of-data marker after the last data block, got %r",
        self.port,
        trailer,
      )

  async def _read_exactly(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    """Read exactly ``num_bytes``, or raise TimeoutError."""
    deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
    buffer = bytearray()

    while len(buffer) < num_bytes:
      self._check_halted()
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
      self._check_halted()
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
