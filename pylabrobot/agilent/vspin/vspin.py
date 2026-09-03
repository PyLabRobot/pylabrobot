import asyncio
import json
import logging
import os
import warnings
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from pylabrobot.agilent.vspin import _nmc
from pylabrobot.events import device_reference, evented_operation, resource_reference
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import Coordinate, ResourceHolder

logger = logging.getLogger(__name__)


_vspin_bucket_calibrations_path = os.path.join(
  os.path.expanduser("~"),
  ".pylabrobot",
  "vspin_bucket_calibrations.json",
)


def _load_vspin_calibrations(device_id: str) -> Optional[int]:
  if not os.path.exists(_vspin_bucket_calibrations_path):
    warnings.warn(
      f"No calibration found for VSpin with device id {device_id}. "
      "Please set the bucket 1 position using `set_bucket_1_position_to_current` method after setup.",
      UserWarning,
    )
    return None
  with open(_vspin_bucket_calibrations_path, "r") as f:
    remainder = json.load(f).get(device_id)
  if remainder is None:
    return None
  return int(remainder) % _nmc.COUNTS_PER_REVOLUTION


def _save_vspin_calibrations(device_id: str, remainder: int):
  if os.path.exists(_vspin_bucket_calibrations_path):
    with open(_vspin_bucket_calibrations_path, "r") as f:
      data = json.load(f)
  else:
    data = {}
  data[device_id] = remainder
  os.makedirs(os.path.dirname(_vspin_bucket_calibrations_path), exist_ok=True)
  with open(_vspin_bucket_calibrations_path, "w") as f:
    json.dump(data, f)


FULL_ROTATION: int = _nmc.COUNTS_PER_REVOLUTION

_POSITION_GAINS = _nmc.ServoGains(
  proportional=200,
  derivative=1200,
  integral=150,
  integration_limit=15,
  output_limit=75,
  current_limit=0,
  position_error_limit=4000,
  servo_rate=5,
  deadband=0,
)

_VELOCITY_GAINS = _nmc.ServoGains(
  proportional=5,
  derivative=100,
  integral=0,
  integration_limit=0,
  output_limit=253,
  current_limit=0,
  position_error_limit=16000,
  servo_rate=1,
  deadband=0,
)

_HOMING_GAINS = _nmc.ServoGains(
  proportional=5,
  derivative=100,
  integral=0,
  integration_limit=0,
  output_limit=50,
  current_limit=0,
  position_error_limit=1000,
  servo_rate=1,
  deadband=0,
)

_POSITION_TRAJECTORY_MODE = (
  _nmc.LOAD_POSITION
  | _nmc.LOAD_VELOCITY
  | _nmc.LOAD_ACCELERATION
  | _nmc.ENABLE_SERVO
  | _nmc.START_NOW
)

_VELOCITY_TRAJECTORY_MODE = (
  _nmc.LOAD_VELOCITY
  | _nmc.LOAD_ACCELERATION
  | _nmc.ENABLE_SERVO
  | _nmc.VELOCITY_MODE
  | _nmc.START_NOW
)

_STATUS_POLL_INTERVAL = 0.1
_MOTION_TIMEOUT = 15.0
_SPIN_TIMEOUT_MARGIN = 5.0
_TARGET_SPEED_FRACTION = 0.95
_IO_TRANSITION_TIMEOUT = 5.0
_SERVO_TRANSITION_SETTLE_TIME = 0.1
_TACHOMETER_TO_RPM = -14.69320388
_NETWORK_PROBE_TIMEOUT = 0.2
_NETWORK_INPUT_QUIET_TIME = 0.1
_NETWORK_INPUT_DRAIN_TIMEOUT = 1.0
_INITIAL_BAUD_RATES = (19200, 115200, 57600, 9600)
_NMC_RESET_SETTLE_TIME = 0.1
_NMC_BAUD_SETTLE_TIME = 0.1
_BUCKET_POSITION_TOLERANCE = 10
_BUCKET_PRESENT_RETRIES = 1

bucket_1_not_set_error = RuntimeError(
  "Bucket 1 position not set. "
  "Please rotate the bucket to bucket 1 using go_to_position and "
  "then calling set_bucket_1_position_to_current."
)


class _PositionAlignmentError(RuntimeError):
  """Raised when a completed rotor move settles outside its target tolerance."""


def _vspin_event_context(
  self: "VSpin",
  g: float = 500,
  duration: float = 60,
  acceleration: float = 0.8,
  deceleration: float = 0.8,
) -> dict:
  """Describe the currently loaded buckets and requested centrifuge run."""
  bucket_resources = [
    {
      "holder": resource_reference(bucket),
      "resource": resource_reference(bucket.resource),
    }
    for bucket in (self.bucket1, self.bucket2)
    if bucket.resource is not None
  ]
  return {
    "device": device_reference(self, name=self.name),
    "resources": [bucket["resource"] for bucket in bucket_resources],
    "bucket_resources": bucket_resources,
    "relative_centrifugal_force": g,
    "duration": duration,
    "acceleration_fraction": acceleration,
    "deceleration_fraction": deceleration,
  }


# ---------------------------------------------------------------------------
# VSpin Driver — FTDI I/O and hardware queries
# ---------------------------------------------------------------------------


class VSpin:
  """FTDI driver for the Agilent VSpin Centrifuge.

  Owns the USB connection, low-level command protocol, and hardware status queries.
  """

  def __init__(self, name: str, device_id: Optional[str] = None):
    """
    Args:
      device_id: The libftdi id for the centrifuge. Find using
        `python -m pylibftdi.examples.list_devices`
    """
    super().__init__()
    self.name = name
    self.io = FTDI(human_readable_device_name="Agilent VSpin Centrifuge", device_id=device_id)
    self.device_id = device_id
    self._servo_status_mask = 0
    self._io_status_mask = 0
    self._io_output_word = 0
    self._nmc_lock = asyncio.Lock()
    self._command_lock = asyncio.Lock()
    self._spin_active = False
    self._spin_cancel_requested = False
    self._bucket_1_remainder: Optional[int] = None
    self._home_position: Optional[int] = None
    if device_id is not None:
      self._bucket_1_remainder = _load_vspin_calibrations(device_id)

    self.bucket1 = ResourceHolder(
      name=f"{name}_bucket1",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      child_location=Coordinate.zero(),
    )
    self.bucket2 = ResourceHolder(
      name=f"{name}_bucket2",
      size_x=127.76,
      size_y=85.48,
      size_z=0,
      child_location=Coordinate.zero(),
    )

    # Door and rotor state, tracked from the commands we issue: the controller has no query for
    # which bucket is parked at the load position.
    self._door_open = False
    self._at_bucket: Optional[ResourceHolder] = None

  @property
  def door_open(self) -> bool:
    """Whether the door was left open by the last door command."""
    return self._door_open

  @property
  def at_bucket(self) -> Optional[ResourceHolder]:
    """The bucket parked at the load position, or None if the rotor is elsewhere."""
    return self._at_bucket

  @asynccontextmanager
  async def _command_scope(self, name: str) -> AsyncIterator[None]:
    """Prevent state-changing VSpin workflows from interleaving."""
    if self._command_lock.locked():
      raise RuntimeError(f"Cannot {name} while another VSpin command is active")
    async with self._command_lock:
      yield

  async def setup(self) -> None:
    async with self._command_scope("set up VSpin"):
      await self._setup()

  async def _setup(self) -> None:
    logger.info("[vSpin %s] connected", self.device_id)
    await self.io.setup()
    await self._configure_ftdi()
    await self._initialize_nmc_network()
    await self.io.set_rts(True)
    await self.io.set_dtr(True)

    servo_status_mask = (
      _nmc.SEND_POSITION
      | _nmc.SEND_ANALOG
      | _nmc.SEND_VELOCITY
      | _nmc.SEND_AUXILIARY
      | _nmc.SEND_HOME
    )
    await self._send_nmc(
      _nmc.build_define_status(_nmc.PIC_SERVO_ADDRESS, servo_status_mask),
      response_data_length=_nmc.servo_status_data_length(servo_status_mask),
    )
    self._servo_status_mask = servo_status_mask
    for _ in range(8):
      await self._send_nmc(_nmc.build_set_io_direction(_nmc.PIC_IO_ADDRESS, 0x0FFF))
    await self._send_nmc(_nmc.build_set_io_direction(_nmc.PIC_IO_ADDRESS, 0x0FDF))
    await self._send_nmc(_nmc.build_set_io_direction(_nmc.PIC_IO_ADDRESS, 0x0EDF))
    await self._send_nmc(_nmc.build_set_io_direction(_nmc.PIC_IO_ADDRESS, 0x0CDF))
    await self._send_nmc(_nmc.build_set_io_direction(_nmc.PIC_IO_ADDRESS, 0x08DF))
    for _ in range(4):
      await self._write_io_output(0x0000)
    io_status_mask = _nmc.SEND_INPUTS | _nmc.SEND_ANALOG_1
    await self._send_nmc(
      _nmc.build_define_status(_nmc.PIC_IO_ADDRESS, io_status_mask),
      response_data_length=_nmc.io_status_data_length(io_status_mask),
    )
    self._io_status_mask = io_status_mask
    for _ in range(5):
      await self._write_io_output(1 << _nmc.OUTPUT_VERSION_TOGGLE)
      await self._write_io_output(0x0000)
    await self._lock_door()

    await self._write_io_output(0x0000)
    await self._wait_for_io_bit(
      _nmc.INPUT_BUCKET_UNLOCKED,
      True,
      active_low=True,
      name="bucket-unlock sensor",
    )

    await self._enable_amplifier_and_reset_servo_status()
    await self._send_nmc(_nmc.build_reset_position(_nmc.PIC_SERVO_ADDRESS))
    await self._send_nmc(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, _HOMING_GAINS))
    await self._send_nmc(
      _nmc.build_load_trajectory(
        _nmc.PIC_SERVO_ADDRESS,
        _VELOCITY_TRAJECTORY_MODE,
        velocity=0x8312,
        acceleration=0x0112,
      )
    )
    await self._send_nmc(_nmc.build_set_homing(_nmc.PIC_SERVO_ADDRESS, 0x28))

    loop = asyncio.get_running_loop()
    homing_deadline = loop.time() + _MOTION_TIMEOUT
    homing_status = await self.request_positions_and_tachometer()
    while homing_status.status & _nmc.STATUS_HOMING_IN_PROGRESS:
      self._raise_on_servo_fault(homing_status, operation="homing")
      if loop.time() >= homing_deadline:
        raise TimeoutError(
          f"VSpin homing did not finish within {_MOTION_TIMEOUT} seconds; "
          f"last status was 0x{homing_status.status:02x}"
        )
      await asyncio.sleep(_STATUS_POLL_INTERVAL)
      homing_status = await self.request_positions_and_tachometer()
    self._raise_on_servo_fault(homing_status, operation="homing")
    if homing_status.home_position is None:
      raise RuntimeError("VSpin homing response did not include the home position")
    self._home_position = homing_status.home_position % FULL_ROTATION

    # --- almost the same as go to position ---
    await self._enable_amplifier_and_reset_servo_status()
    await self._send_nmc(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, _POSITION_GAINS))
    await self._send_nmc(
      _nmc.build_load_trajectory(
        _nmc.PIC_SERVO_ADDRESS,
        _POSITION_TRAJECTORY_MODE,
        position=0,
        velocity=0x28F5C3,
        acceleration=0x1AD7,
      )
    )
    # -----------------------------------------

    move_deadline = loop.time() + _MOTION_TIMEOUT
    move_status = await self.request_positions_and_tachometer()
    while not (
      move_status.status & _nmc.STATUS_MOVE_DONE
      and move_status.position is not None
      and abs(move_status.position) <= _BUCKET_POSITION_TOLERANCE
    ):
      self._raise_on_servo_fault(move_status, operation="setup positioning")
      if loop.time() >= move_deadline:
        raise TimeoutError(
          f"VSpin setup motion did not finish within {_MOTION_TIMEOUT} seconds; "
          f"last status was 0x{move_status.status:02x}, "
          f"last position was {move_status.position}"
        )
      await asyncio.sleep(_STATUS_POLL_INTERVAL)
      move_status = await self.request_positions_and_tachometer()
    self._raise_on_servo_fault(move_status, operation="setup positioning")

    await self._disable_servo_after_motion()

    await self._lock_door()

  async def stop(self) -> None:
    async with self._command_scope("stop VSpin"):
      await self._stop()

  async def _stop(self) -> None:
    logger.info("[vSpin %s] disconnected", self.device_id)
    await self.io.stop()

  async def _enable_amplifier_and_reset_servo_status(self) -> None:
    """Apply the vendor transition delays before clearing status for motion."""
    await asyncio.sleep(_SERVO_TRANSITION_SETTLE_TIME)
    await self._send_nmc(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF))
    await self._send_nmc(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, _POSITION_GAINS))
    await self._send_nmc(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.STOP_ABRUPT))
    await self._send_nmc(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.AMPLIFIER_ENABLE))
    await asyncio.sleep(_SERVO_TRANSITION_SETTLE_TIME)
    await self._send_nmc(_nmc.build_clear_bits(_nmc.PIC_SERVO_ADDRESS))

  async def _disable_servo_after_motion(self) -> None:
    """Allow the completed move to settle around the vendor motor-off transition."""
    await asyncio.sleep(_SERVO_TRANSITION_SETTLE_TIME)
    await self._send_nmc(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF))
    await asyncio.sleep(_SERVO_TRANSITION_SETTLE_TIME)

  # -- low-level protocol --

  async def _read_exact_response(self, length: int, timeout: float) -> bytes:
    """Read one fixed-length NMC response.

    NMC responses do not have a delimiter. Their length is determined by the
    status mask configured for the addressed module.
    """
    if length < 1:
      raise ValueError("NMC response length must be positive")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    response = bytearray()
    while len(response) < length:
      chunk = await self.io.read(length - len(response))
      if chunk:
        response.extend(chunk)
        continue
      if loop.time() >= deadline:
        raise TimeoutError(
          f"VSpin sent {len(response)} of {length} expected response bytes "
          f"within {timeout} seconds: {bytes(response).hex()}"
        )
      await asyncio.sleep(0)
    data = bytes(response)
    logger.debug("Read %s", data.hex())
    return data

  async def send_command(
    self,
    cmd: bytes,
    expected_response_length: int,
    read_timeout: float = 0.2,
  ) -> bytes:
    """Send a VSpin command and read its fixed-length response."""
    async with self._nmc_lock:
      written = await self.io.write(bytes(cmd))
      if written != len(cmd):
        raise RuntimeError(f"VSpin wrote {written} of {len(cmd)} bytes for NMC command {cmd.hex()}")
      return await self._read_exact_response(expected_response_length, timeout=read_timeout)

  async def _send_nmc(
    self,
    command: bytes,
    *,
    response_data_length: Optional[int] = None,
    expect_response: bool = True,
    timeout: float = 0.2,
  ) -> _nmc.NMCResponse:
    """Send a framed NMC command and consume its complete response."""
    if len(command) < 4 or command[0] != _nmc.SYNC_BYTE:
      raise ValueError(f"Invalid NMC command: {command.hex()}")
    if not expect_response:
      async with self._nmc_lock:
        written = await self.io.write(command)
        if written != len(command):
          raise RuntimeError(
            f"VSpin wrote {written} of {len(command)} bytes for NMC command {command.hex()}"
          )
      return _nmc.NMCResponse(status=0, data=b"")

    if response_data_length is None:
      address = command[1]
      if address == _nmc.PIC_SERVO_ADDRESS:
        response_data_length = _nmc.servo_status_data_length(self._servo_status_mask)
      elif address == _nmc.PIC_IO_ADDRESS:
        response_data_length = _nmc.io_status_data_length(self._io_status_mask)
      else:
        response_data_length = 0

    try:
      response = await self.send_command(
        command,
        expected_response_length=response_data_length + 2,
        read_timeout=timeout,
      )
    except TimeoutError as error:
      raise TimeoutError(f"VSpin NMC command {command.hex()} timed out: {error}") from error
    try:
      return _nmc.parse_response(response, response_data_length)
    except _nmc.NMCProtocolError as error:
      raise _nmc.NMCProtocolError(
        f"VSpin NMC command {command.hex()} failed for response {response.hex()}: {error}"
      ) from error

  async def _initialize_nmc_network(self) -> None:
    """Find the controller baud, reset the bus, and assign the two known modules."""
    last_error: Exception | None = None
    for initial_baudrate in _INITIAL_BAUD_RATES:
      await self.io.set_baudrate(initial_baudrate)
      await self._reset_nmc_network()
      await self._reopen_ftdi(19200)
      await self._reset_nmc_network()
      # Closing the transport is intentional. Some reset/NOP replies have already crossed the
      # USB boundary when the FTDI receive buffer is purged, and can otherwise become the reply
      # to SET_ADDRESS. Reopening discards those host-side bytes as well.
      await self._reopen_ftdi(19200)
      await self.io.usb_purge_rx_buffer()
      await self._drain_nmc_input()

      try:
        await self._send_nmc(
          _nmc.build_set_address(_nmc.PIC_SERVO_ADDRESS),
          timeout=_NETWORK_PROBE_TIMEOUT,
        )
      except (TimeoutError, _nmc.NMCProtocolError) as error:
        last_error = error
        await self.io.usb_purge_rx_buffer()
        continue

      modules: dict[int, tuple[int, int]] = {}
      servo_id_response = await self._send_nmc(
        _nmc.build_read_status(_nmc.PIC_SERVO_ADDRESS, _nmc.SEND_MODULE_ID),
        response_data_length=2,
      )
      modules[_nmc.PIC_SERVO_ADDRESS] = (
        servo_id_response.data[0],
        servo_id_response.data[1],
      )

      try:
        await self._send_nmc(
          _nmc.build_set_address(_nmc.PIC_IO_ADDRESS),
          timeout=_NETWORK_PROBE_TIMEOUT,
        )
      except (TimeoutError, _nmc.NMCProtocolError) as error:
        raise RuntimeError(
          "VSpin found only one NMC module; expected one PIC-SERVO and one PIC-IO"
        ) from error
      io_id_response = await self._send_nmc(
        _nmc.build_read_status(_nmc.PIC_IO_ADDRESS, _nmc.SEND_MODULE_ID),
        response_data_length=2,
      )
      modules[_nmc.PIC_IO_ADDRESS] = (
        io_id_response.data[0],
        io_id_response.data[1],
      )

      try:
        await self._send_nmc(
          _nmc.build_set_address(3),
          timeout=_NETWORK_PROBE_TIMEOUT,
        )
      except TimeoutError:
        await self.io.usb_purge_rx_buffer()
      else:
        extra_id_response = await self._send_nmc(
          _nmc.build_read_status(3, _nmc.SEND_MODULE_ID),
          response_data_length=2,
        )
        raise RuntimeError(
          "VSpin found an unexpected third NMC module: "
          f"type {extra_id_response.data[0]}, version {extra_id_response.data[1]}"
        )

      if modules[_nmc.PIC_SERVO_ADDRESS][0] != _nmc.PIC_SERVO_MODULE_TYPE:
        raise RuntimeError(
          "VSpin expected a PIC-SERVO at address 1, found module type "
          f"{modules[_nmc.PIC_SERVO_ADDRESS][0]}"
        )
      if modules[_nmc.PIC_IO_ADDRESS][0] != _nmc.PIC_IO_MODULE_TYPE:
        raise RuntimeError(
          "VSpin expected a PIC-IO at address 2, found module type "
          f"{modules[_nmc.PIC_IO_ADDRESS][0]}"
        )

      await self._send_nmc(_nmc.build_set_baud(57600), expect_response=False)
      await asyncio.sleep(_NMC_BAUD_SETTLE_TIME)
      await self._reopen_ftdi(57600)
      await asyncio.sleep(_NMC_BAUD_SETTLE_TIME)
      await self.io.usb_purge_rx_buffer()
      await self._drain_nmc_input()
      return

    context = "" if last_error is None else f": {last_error}"
    raise RuntimeError(
      f"VSpin NMC initialization found no modules at supported baud rates{context}"
    )

  async def _configure_ftdi(self, baudrate: int = 19200) -> None:
    """Configure the FTDI UART before probing the NMC network."""
    await self.io.set_latency_timer(16)
    await self.io.set_line_property(bits=8, stopbits=1, parity=0)
    await self.io.set_flowctrl(0)
    await self.io.set_baudrate(baudrate)

  async def _reopen_ftdi(self, baudrate: int) -> None:
    """Reopen the FTDI transport and restore all UART settings."""
    await self.io.stop()
    await self.io.setup()
    await self._configure_ftdi(baudrate)

  async def _reset_nmc_network(self) -> None:
    """Send the vendor hard-reset sequence at the currently selected baud rate."""
    self._servo_status_mask = 0
    self._io_status_mask = 0
    self._io_output_word = 0
    await self.io.write(b"\x00" * 20)
    for address in range(33):
      await self.io.write(_nmc.build_no_op(address) + b"\x00" * 8)
    await self._send_nmc(_nmc.build_hard_reset(), expect_response=False)
    await asyncio.sleep(_NMC_RESET_SETTLE_TIME)

  async def _drain_nmc_input(self) -> None:
    """Discard reset-time replies until the FTDI receive path remains quiet."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _NETWORK_INPUT_DRAIN_TIMEOUT
    quiet_since: float | None = None
    discarded = bytearray()

    while True:
      chunk = await self.io.read(64)
      now = loop.time()
      if chunk:
        discarded.extend(chunk)
        quiet_since = None
      elif quiet_since is None:
        quiet_since = now
      elif now - quiet_since >= _NETWORK_INPUT_QUIET_TIME:
        break

      if now >= deadline:
        raise TimeoutError(
          "VSpin NMC receive path did not become quiet after reset; "
          f"discarded {len(discarded)} bytes: {bytes(discarded).hex()}"
        )
      await asyncio.sleep(0.01)

    if discarded:
      logger.debug("Discarded reset-time NMC input: %s", bytes(discarded).hex())

  # -- hardware status queries --

  async def request_positions_and_tachometer(self) -> _nmc.ServoStatus:
    status_mask = (
      _nmc.SEND_POSITION
      | _nmc.SEND_ANALOG
      | _nmc.SEND_VELOCITY
      | _nmc.SEND_AUXILIARY
      | _nmc.SEND_HOME
    )
    response = await self._send_nmc(
      _nmc.build_no_op(_nmc.PIC_SERVO_ADDRESS),
      response_data_length=_nmc.servo_status_data_length(status_mask),
    )
    return _nmc.decode_servo_status(response, status_mask)

  async def request_position(self) -> int:
    position = (await self.request_positions_and_tachometer()).position
    if position is None:
      raise RuntimeError("VSpin position was absent from the configured servo status")
    return position

  async def request_tachometer(self) -> float:
    """Current speed in rpm."""
    velocity = (await self.request_positions_and_tachometer()).velocity
    if velocity is None:
      raise RuntimeError("VSpin velocity was absent from the configured servo status")
    return velocity * _TACHOMETER_TO_RPM

  @staticmethod
  def _raise_on_servo_fault(status: _nmc.ServoStatus, *, operation: str) -> None:
    if status.status & _nmc.STATUS_OVERCURRENT:
      raise RuntimeError(
        f"VSpin servo overcurrent detected during {operation} (status 0x{status.status:02x})"
      )
    if status.status & _nmc.STATUS_POSITION_ERROR:
      raise RuntimeError(
        f"VSpin servo position error detected during {operation} (status 0x{status.status:02x})"
      )

  async def _wait_for_target_speed(self, rpm: float, acceleration: float) -> None:
    """Wait until measured speed reaches the requested spin speed."""
    timeout = _nmc.predicted_ramp_time(rpm, acceleration) + _SPIN_TIMEOUT_MARGIN
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    await self._raise_for_spin_faults()
    measured_rpm = await self.request_tachometer()
    while measured_rpm < rpm * _TARGET_SPEED_FRACTION:
      await self._raise_for_spin_faults()
      if self._spin_cancel_requested:
        return
      if loop.time() >= deadline:
        raise TimeoutError(
          f"VSpin reached only {measured_rpm:.1f} RPM of the requested {rpm:.1f} RPM "
          f"within {timeout:.1f} seconds"
        )
      await asyncio.sleep(_STATUS_POLL_INTERVAL)
      measured_rpm = await self.request_tachometer()

  async def _wait_for_position(
    self,
    position: int,
    timeout: float,
    operation: str,
    *,
    cancel_on_spin_abort: bool = False,
  ) -> int:
    """Wait for the encoder to reach ``position`` and return its final value."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    if cancel_on_spin_abort:
      await self._raise_for_spin_faults()
    current_position = await self.request_position()
    while current_position < position:
      if cancel_on_spin_abort:
        await self._raise_for_spin_faults()
        if self._spin_cancel_requested:
          return current_position
      if loop.time() >= deadline:
        raise TimeoutError(
          f"VSpin {operation} did not reach encoder position {position} within "
          f"{timeout:.1f} seconds; last position was {current_position}"
        )
      await asyncio.sleep(_STATUS_POLL_INTERVAL)
      current_position = await self.request_position()
    return current_position

  async def _raise_for_spin_faults(self) -> None:
    """Raise when a wired safety input makes continued rotor motion unsafe."""
    inputs = await self._request_input_flags()
    if inputs & (1 << _nmc.INPUT_AMPLIFIER_FAULT):
      raise RuntimeError("VSpin amplifier fault detected during spin")
    if inputs & (1 << _nmc.INPUT_IMBALANCE):
      raise RuntimeError("VSpin imbalance detected during spin")
    if inputs & (1 << _nmc.INPUT_DOOR_OPEN):
      raise RuntimeError("VSpin door-open sensor became active during spin")
    if inputs & (1 << _nmc.INPUT_DOOR_LOCKED):
      raise RuntimeError("VSpin door-lock sensor became inactive during spin")
    if inputs & (1 << _nmc.INPUT_BUCKET_UNLOCKED):
      raise RuntimeError("VSpin bucket-unlock sensor became inactive during spin")

  async def _command_deceleration(self, deceleration: float) -> None:
    """Command a velocity-mode ramp to zero RPM."""
    await self._send_nmc(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, _VELOCITY_GAINS))
    await self._send_nmc(
      _nmc.build_load_trajectory(
        _nmc.PIC_SERVO_ADDRESS,
        _VELOCITY_TRAJECTORY_MODE,
        velocity=0,
        acceleration=_nmc.acceleration_to_nmc(deceleration),
      )
    )

  async def _wait_until_stopped(self, initial_rpm: float, deceleration: float) -> None:
    """Wait until the controller reports motion complete and zero measured velocity."""
    timeout = _nmc.predicted_ramp_time(initial_rpm, deceleration) + _SPIN_TIMEOUT_MARGIN
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    await self._raise_for_spin_faults()
    status = await self.request_positions_and_tachometer()
    while True:
      self._raise_on_servo_fault(status, operation="deceleration")
      if status.velocity is None:
        raise RuntimeError("VSpin velocity was absent while confirming deceleration")
      measured_rpm = abs(status.velocity * _TACHOMETER_TO_RPM)
      motion_complete = bool(status.status & _nmc.STATUS_MOVE_DONE)
      if motion_complete and status.velocity == 0:
        return
      await self._raise_for_spin_faults()
      if loop.time() >= deadline:
        raise TimeoutError(
          f"VSpin did not finish deceleration within {timeout:.1f} seconds; "
          f"last status was 0x{status.status:02x} at {measured_rpm:.1f} RPM"
        )
      await asyncio.sleep(_STATUS_POLL_INTERVAL)
      status = await self.request_positions_and_tachometer()

  async def stop_spin(self, deceleration: float = 0.8) -> None:
    """Safely abort an active spin and wait for measured speed to reach zero."""
    if deceleration <= 0 or deceleration > 1:
      raise ValueError("Deceleration must be within 0-1.")
    if not self._spin_active:
      return
    self._spin_cancel_requested = True
    measured_rpm = abs(await self.request_tachometer())
    await self._command_deceleration(deceleration)
    await self._wait_until_stopped(measured_rpm, deceleration)

  async def request_home_position(self) -> int:
    """Changes during a run, but the bucket 1 position relative to it does not."""
    home_position = (await self.request_positions_and_tachometer()).home_position
    if home_position is None:
      raise RuntimeError("VSpin home position was absent from the configured servo status")
    return home_position

  async def _request_status(self) -> _nmc.IOStatus:
    status_mask = _nmc.SEND_INPUTS | _nmc.SEND_ANALOG_1
    response = await self._send_nmc(
      _nmc.build_no_op(_nmc.PIC_IO_ADDRESS),
      response_data_length=_nmc.io_status_data_length(status_mask),
    )
    return _nmc.decode_io_status(response, status_mask)

  async def _request_input_flags(self) -> int:
    inputs = (await self._request_status()).inputs
    if inputs is None:
      raise RuntimeError("VSpin inputs were absent from the configured IO status")
    return inputs

  async def _write_io_output(self, output_word: int) -> None:
    await self._send_nmc(_nmc.build_set_output(_nmc.PIC_IO_ADDRESS, output_word))
    self._io_output_word = output_word

  async def _set_io_output_bit(self, bit: int, value: bool) -> None:
    if value:
      output_word = self._io_output_word | (1 << bit)
    else:
      output_word = self._io_output_word & ~(1 << bit)
    await self._write_io_output(output_word)

  async def _request_io_bit(self, bit: int, *, active_low: bool = False) -> bool:
    value = bool(await self._request_input_flags() & (1 << bit))
    return not value if active_low else value

  async def _wait_for_io_bit(
    self,
    bit: int,
    value: bool,
    *,
    active_low: bool = False,
    name: str,
  ) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _IO_TRANSITION_TIMEOUT
    last_value = await self._request_io_bit(bit, active_low=active_low)
    while last_value != value:
      if loop.time() >= deadline:
        raise TimeoutError(
          f"VSpin {name} did not become {value} within {_IO_TRANSITION_TIMEOUT} seconds; "
          f"last value was {last_value}"
        )
      await asyncio.sleep(_STATUS_POLL_INTERVAL)
      last_value = await self._request_io_bit(bit, active_low=active_low)

  async def request_bucket_locked(self) -> bool:
    return await self._request_io_bit(_nmc.INPUT_BUCKET_LOCKED, active_low=True)

  async def request_bucket_unlocked(self) -> bool:
    return await self._request_io_bit(_nmc.INPUT_BUCKET_UNLOCKED, active_low=True)

  async def request_door_open(self) -> bool:
    return await self._request_io_bit(_nmc.INPUT_DOOR_OPEN)

  async def request_door_locked(self) -> bool:
    return await self._request_io_bit(_nmc.INPUT_DOOR_LOCKED, active_low=True)

  async def request_amplifier_fault(self) -> bool:
    return await self._request_io_bit(_nmc.INPUT_AMPLIFIER_FAULT)

  async def request_imbalance(self) -> bool:
    return await self._request_io_bit(_nmc.INPUT_IMBALANCE)

  async def request_spinning(self) -> bool:
    return await self._request_io_bit(_nmc.INPUT_SPINNING)

  # -- bucket calibration --

  @property
  def bucket_1_remainder(self) -> int:
    if self._bucket_1_remainder is None:
      raise bucket_1_not_set_error
    return self._bucket_1_remainder

  async def set_bucket_1_position_to_current(self) -> None:
    """Set the current position as bucket 1 position and save calibration."""
    async with self._command_scope("set the bucket 1 position"):
      await self._set_bucket_1_position_to_current()

  async def _set_bucket_1_position_to_current(self) -> None:
    current_position = await self.request_position()
    device_id = await self.io.request_serial()
    home_position = await self.request_home_position()
    self._home_position = home_position % FULL_ROTATION
    remainder = (home_position - current_position) % FULL_ROTATION
    self._bucket_1_remainder = remainder
    _save_vspin_calibrations(device_id, remainder)

  async def request_bucket_1_position(self) -> int:
    """Get the bucket 1 position based on calibration."""
    return await self._request_bucket_position(offset=0)

  async def request_bucket_2_position(self) -> int:
    """Get the bucket 2 position based on calibration."""
    return await self._request_bucket_position(offset=FULL_ROTATION // 2)

  async def _request_bucket_position(self, offset: int) -> int:
    if self._bucket_1_remainder is None:
      raise bucket_1_not_set_error
    home_position = self._home_position
    if home_position is None:
      home_position = await self.request_home_position()
    target_remainder = home_position - self.bucket_1_remainder + offset
    current_position = await self.request_position()
    return _nmc.nearest_encoder_position(
      current_position,
      target_remainder,
      counts_per_revolution=FULL_ROTATION,
    )

  # -- CentrifugeBackend interface --

  async def open_door(self) -> None:
    async with self._command_scope("open the door"):
      await self._open_door()

  async def _open_door(self) -> None:
    if await self.request_door_open():
      self._door_open = True
      return
    logger.info("[vSpin %s] open door", self.device_id)
    await self._set_io_output_bit(_nmc.OUTPUT_DOOR_CYLINDER, True)
    await self._wait_for_io_bit(
      _nmc.INPUT_DOOR_OPEN,
      True,
      name="door-open sensor",
    )
    self._door_open = True

  async def close_door(self) -> None:
    async with self._command_scope("close the door"):
      await self._close_door()

  async def _close_door(self) -> None:
    if not (await self.request_door_open()):
      self._door_open = False
      return
    logger.info("[vSpin %s] close door", self.device_id)
    await self._set_io_output_bit(_nmc.OUTPUT_DOOR_CYLINDER, False)
    await self._wait_for_io_bit(
      _nmc.INPUT_DOOR_OPEN,
      False,
      name="door-open sensor",
    )
    self._door_open = False

  async def lock_door(self) -> None:
    async with self._command_scope("lock the door"):
      await self._lock_door()

  async def _lock_door(self) -> None:
    if await self.request_door_open():
      raise RuntimeError("Cannot lock door while it is open.")
    if await self.request_door_locked():
      return
    logger.info("[vSpin %s] lock door", self.device_id)
    await self._set_io_output_bit(_nmc.OUTPUT_DOOR_LOCK_CYLINDER, False)
    await self._wait_for_io_bit(
      _nmc.INPUT_DOOR_LOCKED,
      True,
      active_low=True,
      name="door-lock sensor",
    )

  async def unlock_door(self) -> None:
    async with self._command_scope("unlock the door"):
      await self._unlock_door()

  async def _unlock_door(self) -> None:
    if not await self.request_door_locked():
      return
    await self._set_io_output_bit(_nmc.OUTPUT_DOOR_LOCK_CYLINDER, True)
    await self._wait_for_io_bit(
      _nmc.INPUT_DOOR_LOCKED,
      False,
      active_low=True,
      name="door-lock sensor",
    )

  async def lock_bucket(self) -> None:
    async with self._command_scope("lock the bucket"):
      await self._lock_bucket()

  async def _lock_bucket(self) -> None:
    if await self.request_bucket_locked():
      return
    await self._set_io_output_bit(_nmc.OUTPUT_BUCKET_LOCK_CYLINDER, True)
    await self._wait_for_io_bit(
      _nmc.INPUT_BUCKET_LOCKED,
      True,
      active_low=True,
      name="bucket-lock sensor",
    )

  async def unlock_bucket(self) -> None:
    async with self._command_scope("unlock the bucket"):
      await self._unlock_bucket()

  async def _unlock_bucket(self) -> None:
    if await self.request_bucket_unlocked():
      return
    await self._set_io_output_bit(_nmc.OUTPUT_BUCKET_LOCK_CYLINDER, False)
    await self._wait_for_io_bit(
      _nmc.INPUT_BUCKET_UNLOCKED,
      True,
      active_low=True,
      name="bucket-unlock sensor",
    )

  async def go_to_bucket1(self) -> None:
    async with self._command_scope("move to bucket 1"):
      await self._go_to_bucket(self.bucket1, await self.request_bucket_1_position())

  async def go_to_bucket2(self) -> None:
    async with self._command_scope("move to bucket 2"):
      await self._go_to_bucket(self.bucket2, await self.request_bucket_2_position())

  async def _go_to_bucket(self, bucket: ResourceHolder, position: int) -> None:
    for attempt in range(_BUCKET_PRESENT_RETRIES + 1):
      try:
        await self._go_to_position(position)
      except _PositionAlignmentError:
        if attempt >= _BUCKET_PRESENT_RETRIES:
          raise
        position += FULL_ROTATION
      else:
        self._at_bucket = bucket
        return

  async def go_to_position(self, position: int) -> None:
    async with self._command_scope("move to a position"):
      await self._go_to_position(position)

  async def _go_to_position(self, position: int) -> None:
    logger.info("[vSpin %s] go_to_position: position=%d", self.device_id, position)
    await self._close_door()
    await self._lock_door()
    await self._unlock_bucket()

    await self._enable_amplifier_and_reset_servo_status()
    await self._send_nmc(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, _POSITION_GAINS))
    try:
      trajectory_response = await self._send_nmc(
        _nmc.build_load_trajectory(
          _nmc.PIC_SERVO_ADDRESS,
          _POSITION_TRAJECTORY_MODE,
          position=position,
          velocity=0x28F5C3,
          acceleration=0x1AD7,
        )
      )
      motion_started = not bool(trajectory_response.status & _nmc.STATUS_MOVE_DONE)

      loop = asyncio.get_running_loop()
      deadline = loop.time() + _MOTION_TIMEOUT
      motion_status = await self.request_positions_and_tachometer()
      while not motion_status.status & _nmc.STATUS_MOVE_DONE:
        motion_started = True
        self._raise_on_servo_fault(motion_status, operation=f"move to position {position}")
        if loop.time() >= deadline:
          raise TimeoutError(
            f"VSpin did not complete motion to encoder position {position} within "
            f"{_MOTION_TIMEOUT} seconds; last status was 0x{motion_status.status:02x}, "
            f"last position was {motion_status.position}"
          )
        await asyncio.sleep(_STATUS_POLL_INTERVAL)
        motion_status = await self.request_positions_and_tachometer()
      self._raise_on_servo_fault(motion_status, operation=f"move to position {position}")
    except BaseException:
      try:
        await asyncio.shield(
          self._send_nmc(_nmc.build_stop_motor(_nmc.PIC_SERVO_ADDRESS, _nmc.MOTOR_OFF))
        )
      except Exception:
        logger.exception("[vSpin %s] failed to turn off motor after position error", self.device_id)
      raise

    await self._disable_servo_after_motion()
    if motion_status.position is None:
      raise RuntimeError("VSpin completed motion without returning an encoder position")
    if abs(motion_status.position - position) > _BUCKET_POSITION_TOLERANCE:
      transition = "after motion started" if motion_started else "without reporting motion start"
      raise _PositionAlignmentError(
        f"VSpin completed move to encoder position {position} {transition}, but settled at "
        f"{motion_status.position} (tolerance {_BUCKET_POSITION_TOLERANCE})"
      )
    await self._lock_bucket()
    await self._unlock_door()
    await self._open_door()

  @staticmethod
  def g_to_rpm(g: float) -> int:
    return int(_nmc.rcf_to_rpm(g))

  @evented_operation("centrifuge.spin", _vspin_event_context)
  async def spin(
    self,
    g: float = 500,
    duration: float = 60,
    acceleration: float = 0.8,
    deceleration: float = 0.8,
  ) -> None:
    """Start a spin cycle.

    Args:
      g: relative centrifugal force, also known as g-force
      duration: time in seconds spent at speed (g)
      acceleration: Acceleration rate as a fraction of maximum (0 to 1, exclusive of 0).
      deceleration: Deceleration rate as a fraction of maximum (0 to 1, exclusive of 0).
    """
    if acceleration <= 0 or acceleration > 1:
      raise ValueError("Acceleration must be within 0-1.")
    if deceleration <= 0 or deceleration > 1:
      raise ValueError("Deceleration must be within 0-1.")
    if g < 1 or g > 1000:
      raise ValueError("G-force must be within 1-1000")
    if duration < 1:
      raise ValueError("Spin time must be at least 1 second")

    async with self._command_scope("start a spin"):
      await self._run_spin_cycle(g, duration, acceleration, deceleration)

  async def _run_spin_cycle(
    self,
    g: float,
    duration: float,
    acceleration: float,
    deceleration: float,
  ) -> None:
    if await self.request_door_open():
      await self._close_door()
    if not await self.request_door_locked():
      await self._lock_door()
    if await self.request_bucket_locked():
      await self._unlock_bucket()

    rpm = VSpin.g_to_rpm(g)
    logger.info(
      "[vSpin %s] spin: g=%.1f rpm=%d duration=%.1fs acceleration=%.2f deceleration=%.2f",
      self.device_id,
      g,
      rpm,
      duration,
      acceleration,
      deceleration,
    )

    ticks_per_second = rpm / 60 * _nmc.COUNTS_PER_REVOLUTION
    distance_at_speed = ticks_per_second * duration

    current_position = await self.request_position()
    final_position = current_position + _nmc.spin_target_distance(
      rpm=rpm,
      duration=duration,
      acceleration=acceleration,
    )

    if not -(2**31) <= final_position <= 2**31 - 1:
      raise NotImplementedError(
        "The VSpin spin target does not fit in the controller's signed 32-bit position. "
        "Please report this issue on discuss.pylabrobot.org."
      )

    spin_trajectory = _nmc.build_load_trajectory(
      _nmc.PIC_SERVO_ADDRESS,
      _POSITION_TRAJECTORY_MODE,
      position=final_position,
      velocity=_nmc.rpm_to_nmc_velocity(rpm),
      acceleration=_nmc.acceleration_to_nmc(acceleration),
    )

    await self._enable_amplifier_and_reset_servo_status()
    await self._send_nmc(_nmc.build_set_gain(_nmc.PIC_SERVO_ADDRESS, _VELOCITY_GAINS))

    trajectory_started = False
    self._spin_active = True
    self._spin_cancel_requested = False
    try:
      await self._raise_for_spin_faults()
      await self._send_nmc(spin_trajectory)
      trajectory_started = True

      await self._wait_for_target_speed(rpm, acceleration)
      if not self._spin_cancel_requested:
        cruise_start_position = await self.request_position()
        decel_start_position = int(cruise_start_position + distance_at_speed)
        cruise_timeout = duration / _TARGET_SPEED_FRACTION + _SPIN_TIMEOUT_MARGIN
        await self._wait_for_position(
          decel_start_position,
          timeout=cruise_timeout,
          operation="at-speed interval",
          cancel_on_spin_abort=True,
        )

      if not self._spin_cancel_requested:
        await self._command_deceleration(deceleration)
      await self._wait_until_stopped(rpm, deceleration)
      trajectory_started = False
    except BaseException:
      if trajectory_started:
        try:
          await asyncio.shield(self._command_deceleration(deceleration))
          await asyncio.shield(self._wait_until_stopped(rpm, deceleration))
        except Exception:
          logger.exception("[vSpin %s] emergency deceleration failed", self.device_id)
      raise
    finally:
      self._spin_active = False

    # The rotor has moved off whichever bucket was parked at the load position.
    self._at_bucket = None
