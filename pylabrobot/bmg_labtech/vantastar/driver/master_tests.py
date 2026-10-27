"""Tests for the VANTAstar driver: its commands against the bytes Voyager sent to the reader, and
its command flows against the reader's captured replies, through a fake USB link."""

import unittest
from collections import deque
from dataclasses import replace
from typing import Callable, Deque, List, Optional

from pylabrobot.bmg_labtech.vantastar.driver.configuration_tests import HARDWARE_INFO
from pylabrobot.bmg_labtech.vantastar.driver.errors import CommandRefusedError
from pylabrobot.bmg_labtech.vantastar.driver.framing import (
  CONTINUATION,
  FIRST,
  HOST_ACKNOWLEDGEMENT,
  SINGLE,
  wrap_payload,
)
from pylabrobot.bmg_labtech.vantastar.driver.master import (
  LUMINESCENCE_NO_FILTER,
  STANDARD_96,
  MonochromatorSettings,
  VANTAstarDriver,
  compute_monochromator_settings,
  encode_plate_geometry,
  encode_well_mask,
)
from pylabrobot.bmg_labtech.vantastar.driver.responses_tests import (
  ABSORBANCE_FIRST_PAGE,
  ABSORBANCE_LAST_PAGE,
  GAIN_DATA,
  STATUS_HEATING,
  STATUS_INIT_RUNNING,
  STATUS_NOT_INITIALIZED,
)
from pylabrobot.io.usb import USB

# Commands Voyager sent: an absorbance read (96 wells, 600 nm, 22 flashes, 0.5 s settling) and
# fluorescence reads (excitation 365/8, emission 400/10, dichroic 382.0 nm, 20 flashes, 0.5 s
# settling) at gain 500 and 5 mm, and with enhanced dynamic range at 4 mm.
RUN_ABSORBANCE = bytes.fromhex(
  "0431e82164059e04642c4a1d000c0802920000ffffffffffffffffffffffff0000000000000000000000000000000000"
  "000000000000000000000000000000000000000242000000000000000000000000000000000000000000000000000027"
  "0f270f00190117700064232826ca000000640000000000000000000001000000010016000000"
)
RUN_FLUORESCENCE = bytes.fromhex(
  "0431e82164059e04642c4a1d000c0802920000ffffffffffffffffffffffff0000000000000000000000000000000000"
  "000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000027"
  "0f270f001901f40000010000070000000001f40e570e2d0eec0fb60f8a00040003000000000000000000000100000001"
  "0014000000"
)
RUN_FLUORESCENCE_EDR = bytes.fromhex(
  "0431e82164059e04642c4a1d000c0802920000ffffffffffffffffffffffff0000000000000000000000000000000000"
  "000000000000000000000000000000000000000200400000000000000000000000000000000000000000000000000027"
  "0f270f001901900000010000070000000000000e570e2d0eec0fb60f8a00040003000000000000000000000100000001"
  "0014000000"
)
# A gain and focus adjustment on A1-H1 for the fluorescence read.
GAIN_PLATE = bytes.fromhex(
  "0c31e82164059e04642c4a1d000c08029200800800800800800800800800000000000000000000000000000000000000"
  "00000000000000000000000000000000000020400301f406400014006422600000000c070e570e2d0eec0fb60f8a0004"
  "0003"
)
# A luminescence RUN as Voyager's command serializer builds it with its defaults (gain 3600, the
# other fields 0, a 6.96 mm well); no luminescence read was captured (the reader has none).
RUN_LUMINESCENCE_SERIALIZER = bytes.fromhex(
  "0431e82164059e04642c4a1d000c0802b80000ffffffffffffffffffffffff0000000000000000000000000000000000"
  "000000000000000000000000000000000000000201000000000000000000000000000000000000000000000000000027"
  "0f270f000500000000010000070e10000000000000000000000000000000000000000000000000000001000000010000"
  "000100"
)
INIT = bytes.fromhex("0100001001")
PLATE_OUT = bytes.fromhex("030100000000")
PLATE_IN = bytes.fromhex("030000000000")
# Double orbital shaking, 300 rpm, 10 s.
SHAKE_DOUBLE_ORBITAL_300_10 = bytes.fromhex("270702000a00000000")
INCUBATOR_ON_30 = bytes.fromhex("0601012c07")
INCUBATOR_OFF = bytes.fromhex("0602000007")
MW_BLOCK_STATUS = bytes.fromhex("2d000000ffff")
VALUES_CURRENT_BLOCK = bytes.fromhex("0502ffffffffffff")
VALUES_NEXT_NEW_BLOCK = bytes.fromhex("0502000000000000")
VALUES_WELL_LIST = bytes.fromhex("051d000000000000")
VALUES_TIME_DATA = bytes.fromhex("0503000000000000")
VALUES_GAIN_DATA = bytes.fromhex("0505000000000000")

# Replies of the reader.
RUN_REPLY = bytes.fromhex(
  "03250406000001000011c10000018c0111300000005f0001010000000000000003000000580001000000030000e00041"
)
STATUS_IDLE = bytes.fromhex("010500240000050000000000000901010102010400004f0000")
STATUS_DRAWER_OPEN = bytes.fromhex("010500210000050000000000000901020103010400004f0000")
STATUS_MOTORS_OFF = bytes.fromhex("010516060000050000000000000901050106010700004f0000")
# The headers of data block status replies: busy; busy with a data page ready; and the last page
# announced as the read finishes (NewMeasDataReady and MeasDataReady, no page-ready flag).
MW_BLOCK_BUSY = bytes.fromhex("2a2504260000")
MW_BLOCK_PAGE_READY = bytes.fromhex("2a25042e0000")
MW_BLOCK_LAST_PAGE = bytes.fromhex("2a2517260000")

# The fluorescence read's monochromator, as Voyager set it.
VOYAGER_MONOCHROMATOR = MonochromatorSettings(
  excitation_lower=362.9,
  excitation_upper=367.1,
  dichroic=382.0,
  emission_lower=397.8,
  emission_upper=402.2,
)


class FakeUSB(USB):
  """A VANTAstar on a fake link: answers each command payload with ``respond(payload)``.

  Speaks the packet framing: acknowledges the first packet of a long command, and sends a long
  reply's continuations after the host's acknowledgement.  Subclasses ``USB`` so the driver takes
  it as one, without opening a device.
  """

  # No super().__init__(): USB.__init__ looks for the device.
  def __init__(self, respond: Callable[[bytes], bytes]) -> None:
    self.respond = respond
    self.commands: List[bytes] = []
    self._to_host: Deque[bytes] = deque()
    self._continuations: List[bytes] = []
    self._receiving: Optional[bytearray] = None
    self._receiving_length = 0

  async def setup(self, empty_buffer: bool = True) -> None: ...

  async def stop(self) -> None: ...

  async def drain(self, *args, **kwargs) -> None: ...

  def ctrl_transfer(self, *args, **kwargs) -> None:  # type: ignore[override]
    pass

  def serialize(self) -> dict:
    return {"serial_number": "0421-000446"}

  async def write(self, data: bytes, timeout: Optional[float] = None) -> None:  # type: ignore[override]
    if data == HOST_ACKNOWLEDGEMENT:
      self._to_host.extend(self._continuations)
      self._continuations = []
      return
    packet_type, value, chunk = data[2], int.from_bytes(data[3:7], "big"), data[7:]
    if packet_type == SINGLE:
      self._receive(chunk[:value])
    elif packet_type == FIRST:
      self._receiving, self._receiving_length = bytearray(chunk), value
      self._to_host.append(bytes.fromhex("00160401"))
    elif packet_type == CONTINUATION:
      assert self._receiving is not None
      self._receiving.extend(chunk)
      if len(self._receiving) >= self._receiving_length:
        payload = bytes(self._receiving[: self._receiving_length])
        self._receiving = None
        self._receive(payload)

  def _receive(self, payload: bytes) -> None:
    self.commands.append(payload)
    packets = wrap_payload(self.respond(payload))
    self._to_host.append(packets[0])
    self._continuations = packets[1:]

  async def read(self, timeout: Optional[float] = None, size: Optional[int] = None) -> bytes:  # type: ignore[override]
    return self._to_host.popleft()


def make_driver(respond: Callable[[bytes], bytes]) -> VANTAstarDriver:
  driver = VANTAstarDriver(io=FakeUSB(respond))
  driver.default_poll_interval = 0
  driver.measurement_poll_interval = 0
  return driver


def commands(driver: VANTAstarDriver) -> List[bytes]:
  assert isinstance(driver.io, FakeUSB)
  return driver.io.commands


class CommandTests(unittest.TestCase):
  def test_absorbance_run_matches_voyager(self):
    driver = make_driver(lambda p: b"")
    run = driver.build_absorbance_run([600], flashes=22, settling_time=0.5)
    self.assertEqual(bytes([0x04]) + run, RUN_ABSORBANCE)

  def test_fluorescence_run_matches_voyager(self):
    driver = make_driver(lambda p: b"")
    run = driver.build_fluorescence_run(
      [VOYAGER_MONOCHROMATOR], gains=[500], focal_height=5.0, flashes=20, settling_time=0.5
    )
    self.assertEqual(bytes([0x04]) + run, RUN_FLUORESCENCE)

  def test_enhanced_dynamic_range_run_matches_voyager(self):
    driver = make_driver(lambda p: b"")
    run = driver.build_fluorescence_run(
      [VOYAGER_MONOCHROMATOR],
      gains=[500],
      focal_height=4.03,
      flashes=20,
      settling_time=0.5,
      enhanced_dynamic_range=True,
    )
    self.assertEqual(bytes([0x04]) + run, RUN_FLUORESCENCE_EDR)

  def test_luminescence_run_matches_the_serializer_layout(self):
    driver = make_driver(lambda p: b"")
    closed = MonochromatorSettings(0, 0, 0, 0, 0, excitation_slit=0, emission_slit=0)
    run = driver.build_luminescence_run(
      [closed],
      gains=[3600],
      integration_time=0,
      settling_time=0.1,
      aperture_distance=0,
      geometry=replace(STANDARD_96, well_size=6.96),
    )
    # The serializer's default kinetic delay is 1; Voyager's reads send 0 (absorbance, fluorescence).
    expected = bytearray(RUN_LUMINESCENCE_SERIALIZER)
    expected[-2] = 0
    self.assertEqual(bytes([0x04]) + run, bytes(expected))

  def test_luminescence_automatic_gain(self):
    driver = make_driver(lambda p: b"")
    run = driver.build_luminescence_run(
      [LUMINESCENCE_NO_FILTER], gains=None, integration_time=0.8, settling_time=0.02
    )
    self.assertEqual(run[66:69], bytes([0x02, 0x01, 0x40]))  # reading mode, luminescence, auto gain
    self.assertEqual(run[99:102], bytes([1, 0x00, 50]))  # settling, aperture 0.5 mm
    self.assertEqual(run[-5:-3], (80).to_bytes(2, "big"))  # 0.8 s

  def test_gain_focus_matches_voyager(self):
    driver = make_driver(lambda p: b"")
    reference_wells = encode_well_mask(12, 8, [(row, 0) for row in range(8)])
    parameters = driver.build_gain_focus(
      VOYAGER_MONOCHROMATOR,
      focal_height=5.0,
      target_percent=88.0,
      adjust_focus=True,
      well_mask=reference_wells,
    )
    self.assertEqual(bytes([0x0C]) + parameters, GAIN_PLATE)

  def test_shake_matches_voyager(self):
    body = VANTAstarDriver.build_shake("double_orbital", 300, 10)
    self.assertEqual(bytes([0x27]) + body, SHAKE_DOUBLE_ORBITAL_300_10)

  def test_long_shake_pauses(self):
    body = VANTAstarDriver.build_shake("orbital", 500, 600)
    self.assertEqual(body.hex(), "05040258" + "0001" + "003b")

  def test_shake_speed_must_be_a_multiple_of_100(self):
    with self.assertRaises(ValueError):
      VANTAstarDriver.build_shake("orbital", 250, 10)

  def test_plate_geometry(self):
    self.assertEqual(encode_plate_geometry(STANDARD_96), RUN_ABSORBANCE[1:18])

  def test_automatic_dichroic(self):
    self.assertEqual(compute_monochromator_settings(365, 8, 400, 10).dichroic, 382.0)
    self.assertEqual(compute_monochromator_settings(483, 14, 530, 30).dichroic, 502.5)


class StatusTests(unittest.TestCase):
  def test_drawer_open(self):
    status = VANTAstarDriver._parse_status(STATUS_DRAWER_OPEN)
    self.assertTrue(status.drawer_open)
    self.assertTrue(status.motors_enabled)
    self.assertFalse(status.busy)

  def test_busy_command(self):
    status = VANTAstarDriver._parse_status(STATUS_INIT_RUNNING)
    self.assertTrue(status.busy)
    self.assertEqual(status.busy_command, "INIT")

  def test_temperatures(self):
    self.assertEqual(VANTAstarDriver._parse_status(STATUS_HEATING).temperatures, (30.1, 30.6, 30.5))

  def test_refused_reply_raises_with_quit_codes(self):
    driver = make_driver(lambda p: b"")
    with self.assertRaises(CommandRefusedError) as cm:
      driver._check_reply(bytes([0x80]), STATUS_NOT_INITIALIZED)
    self.assertEqual(cm.exception.quit_codes, (201, 0, 0, 0, 0))


class FlowTests(unittest.IsolatedAsyncioTestCase):
  async def test_initialize(self):
    replies = deque([STATUS_IDLE, HARDWARE_INFO, STATUS_INIT_RUNNING, STATUS_IDLE])
    driver = make_driver(lambda p: replies.popleft())
    await driver.initialize()
    self.assertEqual(commands(driver)[:2], [bytes([0x80]), INIT])
    self.assertEqual(driver.discover().serial_number, "0421-000446")

  async def test_drawer(self):
    replies = deque(
      [STATUS_IDLE, STATUS_IDLE, STATUS_DRAWER_OPEN, STATUS_DRAWER_OPEN, STATUS_IDLE, STATUS_IDLE]
    )
    driver = make_driver(lambda p: replies.popleft())
    await driver.open_drawer()
    await driver.close_drawer()
    sent = [c for c in commands(driver) if c[0] == 0x03]
    self.assertEqual(sent, [PLATE_OUT, PLATE_IN])

  async def test_shake_moves_the_plate_in_when_the_motors_are_off(self):
    replies = deque([STATUS_MOTORS_OFF] + [STATUS_IDLE] * 6)
    driver = make_driver(lambda p: replies.popleft())
    await driver.shake("double_orbital", 300, 10)
    sent = [c for c in commands(driver) if c[0] != 0x80]
    self.assertEqual(sent, [PLATE_IN, SHAKE_DOUBLE_ORBITAL_300_10])

  async def test_temperature(self):
    driver = make_driver(lambda p: STATUS_IDLE)
    await driver.set_temperature(30)
    await driver.stop_temperature_control()
    sent = [c for c in commands(driver) if c[0] == 0x06]
    self.assertEqual(sent, [INCUBATOR_ON_30, INCUBATOR_OFF])

  async def test_measurement(self):
    block_status = deque([MW_BLOCK_BUSY, MW_BLOCK_PAGE_READY, MW_BLOCK_LAST_PAGE])
    pages = deque([ABSORBANCE_FIRST_PAGE, ABSORBANCE_LAST_PAGE])

    def respond(payload: bytes) -> bytes:
      if payload[0] == 0x80:
        return STATUS_IDLE
      if payload[0] == 0x04:
        return RUN_REPLY
      if payload == MW_BLOCK_STATUS:
        return block_status.popleft()
      if payload in (VALUES_CURRENT_BLOCK, VALUES_NEXT_NEW_BLOCK):
        return pages.popleft()
      return RUN_REPLY

    driver = make_driver(respond)
    data = await driver.run_measurement(RUN_ABSORBANCE[1:], timeout=10)
    self.assertTrue(data.complete)
    self.assertEqual(data.wells, 96)
    sent = [c for c in commands(driver) if c[0] != 0x80]
    self.assertEqual(
      sent,
      [
        RUN_ABSORBANCE,
        VALUES_WELL_LIST,
        VALUES_TIME_DATA,
        MW_BLOCK_STATUS,
        MW_BLOCK_STATUS,
        VALUES_CURRENT_BLOCK,
        MW_BLOCK_STATUS,
        VALUES_NEXT_NEW_BLOCK,
      ],
    )

  async def test_gain_and_focus(self):
    def respond(payload: bytes) -> bytes:
      return GAIN_DATA if payload == VALUES_GAIN_DATA else STATUS_IDLE

    driver = make_driver(respond)
    result = await driver.adjust_gain_and_focus(GAIN_PLATE[1:])
    self.assertEqual((result.gain, result.focal_height), (2904, 3.0))


if __name__ == "__main__":
  unittest.main()
