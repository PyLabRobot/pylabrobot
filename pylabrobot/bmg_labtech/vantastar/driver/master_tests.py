"""Tests for the VANTAstar driver: its commands against the bytes Voyager sent to the reader, and
its command flows against the reader's captured replies, through a fake USB link."""

import unittest
from collections import deque
from dataclasses import replace
from typing import Any, Callable, Deque, Dict, List, Optional

from pylabrobot.bmg_labtech.vantastar.driver.configuration_tests import HARDWARE_INFO
from pylabrobot.bmg_labtech.protocol.errors import FrameError
from pylabrobot.bmg_labtech.vantastar.driver.errors import CommandRefusedError, DeviceError
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
  WellScan,
  compute_monochromator_settings,
  compute_scan_monochromators,
  encode_plate_geometry,
  encode_well_mask,
)
from pylabrobot.bmg_labtech.vantastar.driver.responses_tests import (
  ABSORBANCE_FIRST_PAGE,
  ABSORBANCE_LAST_PAGE,
  GAIN_DATA,
  STATUS_AFTER_INCUBATOR_COMMAND,
  STATUS_HEATING,
  STATUS_INIT_RUNNING,
  STATUS_NOT_INITIALIZED,
  load_flows,
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
# The 96-well absorbance read's well list, and its data block status: busy; busy with a data page
# ready; and the block finished (NewMeasDataReady and MeasDataReady, no page-ready flag).
WELL_LIST_96 = bytes.fromhex(
  "1d25042600000c08000000002c2a000001b3005f00600101020103010401050106010701080109010a010b010c010c02"
  "0b020a020902080207020602050204020302020201020103020303030403050306030703080309030a030b030c030c04"
  "0b040a040904080407040604050404040304020401040105020503050405050506050705080509050a050b050c050c06"
  "0b060a060906080607060606050604060306020601060107020703070407050706070707080709070a070b070c070c08"
  "0b080a08090808080708060805080408030802080108"
)
MW_BLOCK_BUSY = bytes.fromhex(
  "2a2504260000000000010000018802050500000120040000006003000000010100000001000000000105050000006004"
  "00000060030000000101000000010000000001000800000000000100000000"
)
MW_BLOCK_PAGE_READY = bytes.fromhex(
  "2a25042e0000000000010000018802050500000120040000006003000000010100000001000000000105050000006004"
  "00000060030000000101000000010000000001000800000000000100000004"
)
MW_BLOCK_LAST_PAGE = bytes.fromhex(
  "2a2517260000000000010000018802050500000120040000006003000000010100000001000000000105050000006004"
  "00000060030000000101000000010000000001000800010000000000000000"
)

# Reads of a plate with an Ir complex in A1, A6, A12 and a Ru complex in H1, H6, H12: an absorbance
# spectrum of the six wells (220-1000 nm, 1 nm steps, 22 flashes, 0.5 s settling), alone and with
# orbital averaging (3 mm, 16 flashes, 0.1 s); excitation scans (emission 475/12, excitation
# 320-425 nm in 1 nm steps at 8 nm bandwidth, gain 600 at 6.4 mm, 20 flashes, the six wells; and
# 380-425 nm with spiral averaging, gain 667 at 6.2 mm, 10 flashes, 0.2 s, A1, A6, A12); emission
# scans (excitation 450/16, H1, H6, H12, 5 flashes, 0.1 s: emission 480-700 nm in 2 nm steps at
# 10 nm, gain 1365 at 6.8 mm; and 500-700 nm in 1 nm steps at 30 nm with the dichroic at 471.5 nm,
# gain 800 at 6.6 mm); absorbance at 300, 450 and 600 nm, every well; kinetic absorbance at 450 nm,
# 6 cycles 10 s apart, A1, A6, A12; and fluorescence 380/8, 475/10 with Voyager's band edges, gain
# 600 at 4.5 mm, 10 flashes, 0.2 s, A1, A6, A12.  The scans carry Voyager's band edges.
RUN_ABSORBANCE_SPECTRUM = bytes.fromhex(
  "0431e82164059e04642c4a1d000c08029200008410000000000000000008410000000000000000000000000000000000"
  "000000000000000000000000000000000000000242000000000000000000000000000000000000000000000000000027"
  "0f270f00190008982710000a232826ca000000640000000000000000000001000000010016000000"
)
RUN_ABSORBANCE_SPECTRUM_ORBITAL = bytes.fromhex(
  "0431e82164059e04642c4a1d000c08029200008410000000000000000008410000000000000000000000000000000000"
  "000000000000000000000000000000000000000242000000000000000000000000000000000000000000000000000027"
  "0f270f030003050008982710000a232826ca000000640000000000000000000001000000010010000000"
)
RUN_EXCITATION_SCAN = bytes.fromhex(
  "0431e82164059e04642c4a1d000c08029200008410000000000000000008410000000000000000000000000000000000"
  "000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000027"
  "0f270f0019028082006a0000070000000002580c9d0c630f7d12c812540003000200000000025810b7107d118a12c812"
  "54000300020000000000000000000001000000010014000000"
)
RUN_EXCITATION_SCAN_SPIRAL = bytes.fromhex(
  "0431e82164059e04642c4a1d000c08029200008410000000000000000000000000000000000000000000000000000000"
  "000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000027"
  "0f270f0400030a026c82002e00000700000000029b0ef70eb910a912c812540003000200000000029b10b7107d118a12"
  "c8125400030002000000000000000000000100000001000a000000"
)
RUN_EMISSION_SCAN = bytes.fromhex(
  "0431e82164059e04642c4a1d000c08029200000000000000000000000008410000000000000000000000000000000000"
  "000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000027"
  "0f270f000502a882006f00000700000000055511e11147123912f012900002000300000000055511e1114716851b871b"
  "29000200030000000000000000000001000000010005000000"
)
RUN_EMISSION_SCAN_DICHROIC = bytes.fromhex(
  "0431e82164059e04642c4a1d000c08029200000000000000000000000008410000000000000000000000000000000000"
  "000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000027"
  "0f270f000502948200c900000700000000032011e11147126b141c12f40002000300000000032011e11147126b1beb1a"
  "c5000200030000000000000000000001000000010005000000"
)
RUN_ABSORBANCE_THREE_WAVELENGTHS = bytes.fromhex(
  "0431e82164059e04642c4a1d000c0802920000ffffffffffffffffffffffff0000000000000000000000000000000000"
  "000000000000000000000000000000000000000242000000000000000000000000000000000000000000000000000027"
  "0f270f0019030bb8119417700064232826ca000000640000000000000000000001000000010016000000"
)
RUN_ABSORBANCE_KINETIC = bytes.fromhex(
  "0431e82164059e04642c4a1d000c08029200008410000000000000000000000000000000000000000000000000000000"
  "000000000000000000000000000000000000000242000000000000000000000000000000000000000000000000000027"
  "0f270f00190111940064232826ca000000640000000000000000000001000000060016000a00"
)
RUN_FLUORESCENCE_VOYAGER_EDGES = bytes.fromhex(
  "0431e82164059e04642c4a1d000c08029200008410000000000000000000000000000000000000000000000000000000"
  "000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000027"
  "0f270f000a01c20000010000070000000002580eec0ec410ae12a0127c00040003000000000000000000000100000001"
  "000a000000"
)
# Gain adjustments for the first excitation scan: gain and focus on A1 at a 10 % target; then the
# focus alone on the six wells; the focus alone with spiral averaging for the second scan; and the
# gain alone on A1 at a 90 % target and 4.5 mm.
GAIN_PLATE_GAIN_AND_FOCUS = bytes.fromhex(
  "0c31e82164059e04642c4a1d000c08029200800000000000000000000000000000000000000000000000000000000000"
  "00000000000000000000000000000000000020400301f406400014006403e80000000c070f4f0f2910db12c712550004"
  "0003"
)
GAIN_PLATE_FOCUS = bytes.fromhex(
  "0c31e82164059e04642c4a1d000c08029200841000000000000000000841000000000000000000000000000000000000"
  "00000000000000000000000000000000000020400301f406400014006400000000000c070f4f0f2910db12c712550004"
  "0003"
)
GAIN_PLATE_FOCUS_SPIRAL = bytes.fromhex(
  "0c31e82164059e04642c4a1d000c08029200841000000000000000000000000000000000000000000000000000000000"
  "00000000000000000000000000000000000020440301f406400014006400000000000c0710150ff3113f12c712550004"
  "00030003000a"
)
GAIN_PLATE_GAIN = bytes.fromhex(
  "0c31e82164059e04642c4a1d000c08029200800000000000000000000000000000000000000000000000000000000000"
  "00000000000000000000000000000000000000000301c205dc0014006423280000000c070f190e9710b312d1124b0004"
  "0003"
)
STOP = bytes.fromhex("0b00")

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


class ScanKineticAndGainCommandTests(unittest.TestCase):
  """The commands of reads with spectra, well scans, kinetics and gain adjustments, as Voyager sent
  them (``RUN_ABSORBANCE_SPECTRUM`` and the constants after it)."""

  def setUp(self):
    self.driver = make_driver(lambda p: b"")
    self.six = encode_well_mask(12, 8, [(0, 0), (0, 5), (0, 11), (7, 0), (7, 5), (7, 11)])
    self.ir = encode_well_mask(12, 8, [(0, 0), (0, 5), (0, 11)])
    self.ru = encode_well_mask(12, 8, [(7, 0), (7, 5), (7, 11)])

  def test_absorbance_spectrum(self):
    run = self.driver.build_absorbance_spectrum_run(
      220, 1000, 1, flashes=22, settling_time=0.5, well_mask=self.six
    )
    self.assertEqual(bytes([0x04]) + run, RUN_ABSORBANCE_SPECTRUM)

  def test_absorbance_spectrum_orbital(self):
    run = self.driver.build_absorbance_spectrum_run(
      220,
      1000,
      1,
      flashes=16,
      settling_time=0.1,
      well_mask=self.six,
      well_scan=WellScan("orbital", 3),
    )
    self.assertEqual(bytes([0x04]) + run, RUN_ABSORBANCE_SPECTRUM_ORBITAL)

  def test_excitation_scans(self):
    last = MonochromatorSettings(422.1, 427.9, 449.0, 469.2, 480.8, 2, 3)
    run = self.driver.build_fluorescence_scan_run(
      MonochromatorSettings(317.1, 322.9, 396.5, 469.2, 480.8, 2, 3),
      last,
      106,
      gain=600,
      focal_height=6.4,
      flashes=20,
      settling_time=0.5,
      well_mask=self.six,
    )
    self.assertEqual(bytes([0x04]) + run, RUN_EXCITATION_SCAN)
    run = self.driver.build_fluorescence_scan_run(
      MonochromatorSettings(376.9, 383.1, 426.5, 469.2, 480.8, 2, 3),
      last,
      46,
      gain=667,
      focal_height=6.2,
      flashes=10,
      settling_time=0.2,
      well_mask=self.ir,
      well_scan=WellScan("spiral", 3),
    )
    self.assertEqual(bytes([0x04]) + run, RUN_EXCITATION_SCAN_SPIRAL)

  def test_emission_scans(self):
    run = self.driver.build_fluorescence_scan_run(
      MonochromatorSettings(442.3, 457.7, 466.5, 475.2, 484.8, 3, 2),
      MonochromatorSettings(442.3, 457.7, 576.5, 695.3, 704.7, 3, 2),
      111,
      gain=1365,
      focal_height=6.8,
      flashes=5,
      settling_time=0.1,
      well_mask=self.ru,
    )
    self.assertEqual(bytes([0x04]) + run, RUN_EMISSION_SCAN)
    run = self.driver.build_fluorescence_scan_run(
      MonochromatorSettings(442.3, 457.7, 471.5, 485.2, 514.8, 3, 2),
      MonochromatorSettings(442.3, 457.7, 471.5, 685.3, 714.7, 3, 2),
      201,
      gain=800,
      focal_height=6.6,
      flashes=5,
      settling_time=0.1,
      well_mask=self.ru,
    )
    self.assertEqual(bytes([0x04]) + run, RUN_EMISSION_SCAN_DICHROIC)

  def test_scan_monochromators(self):
    # Nominal edges, the dichroic Voyager chose at both ends, and its scan slits.
    first, last = compute_scan_monochromators(
      "excitation", 320, 425, 475, excitation_bandwidth=8, emission_bandwidth=12
    )
    self.assertEqual(first, MonochromatorSettings(316, 324, 396.5, 469, 481, 2, 3))
    self.assertEqual(last, MonochromatorSettings(421, 429, 449.0, 469, 481, 2, 3))
    first, last = compute_scan_monochromators(
      "emission", 500, 700, 450, excitation_bandwidth=16, emission_bandwidth=30, dichroic=471.5
    )
    self.assertEqual(first, MonochromatorSettings(442, 458, 471.5, 485, 515, 3, 2))
    self.assertEqual(last, MonochromatorSettings(442, 458, 471.5, 685, 715, 3, 2))

  def test_absorbance_three_wavelengths(self):
    run = self.driver.build_absorbance_run([300, 450, 600], flashes=22, settling_time=0.5)
    self.assertEqual(bytes([0x04]) + run, RUN_ABSORBANCE_THREE_WAVELENGTHS)

  def test_absorbance_kinetic(self):
    run = self.driver.build_absorbance_run(
      [450], flashes=22, settling_time=0.5, well_mask=self.ir, cycles=6, cycle_time=10
    )
    self.assertEqual(bytes([0x04]) + run, RUN_ABSORBANCE_KINETIC)

  def test_fluorescence_with_voyager_edges(self):
    run = self.driver.build_fluorescence_run(
      [MonochromatorSettings(378.0, 382.0, 427.0, 473.2, 476.8)],
      gains=[600],
      focal_height=4.5,
      flashes=10,
      settling_time=0.2,
      well_mask=self.ir,
    )
    self.assertEqual(bytes([0x04]) + run, RUN_FLUORESCENCE_VOYAGER_EDGES)

  def test_gain_adjustments(self):
    monochromator = MonochromatorSettings(388.1, 391.9, 431.5, 469.3, 480.7)
    a1 = encode_well_mask(12, 8, [(0, 0)])
    build = self.driver.build_gain_focus
    gain_and_focus = build(
      monochromator, focal_height=5.0, target_percent=10, adjust_focus=True, well_mask=a1
    )
    self.assertEqual(bytes([0x0C]) + gain_and_focus, GAIN_PLATE_GAIN_AND_FOCUS)
    focus = build(
      monochromator, focal_height=5.0, target_percent=0, adjust_focus=True, well_mask=self.six
    )
    self.assertEqual(bytes([0x0C]) + focus, GAIN_PLATE_FOCUS)
    spiral = build(
      MonochromatorSettings(408.3, 411.7, 441.5, 469.3, 480.7),
      focal_height=5.0,
      target_percent=0,
      adjust_focus=True,
      well_mask=self.ir,
      well_scan=WellScan("spiral", 3),
      flashes=10,
    )
    self.assertEqual(bytes([0x0C]) + spiral, GAIN_PLATE_FOCUS_SPIRAL)
    gain = build(
      MonochromatorSettings(373.5, 386.5, 427.5, 468.3, 481.7),
      focal_height=4.5,
      target_percent=90,
      adjust_focus=False,
      well_mask=a1,
    )
    self.assertEqual(bytes([0x0C]) + gain, GAIN_PLATE_GAIN)

  def test_kinetic_cycles_out_of_range(self):
    with self.assertRaises(ValueError):
      self.driver.build_absorbance_run([450], flashes=22, settling_time=0.5, cycles=0)

  def test_well_scan_diameter_out_of_range(self):
    with self.assertRaises(ValueError):
      self.driver.build_absorbance_run(
        [450], flashes=22, settling_time=0.5, well_scan=WellScan("orbital", 0)
      )


class RecordedReader:
  """Answers as the reader did in a recorded read (``load_flows``): STATUS (and STOP) and RUN with
  their recorded replies, each data block status poll with the next block status reply, and each
  other request (data requests, the block release) with the next reply recorded for it; the last
  again once they run out."""

  def __init__(self, flow: Dict[str, Any]) -> None:
    self.status = bytes.fromhex(flow["status"])
    self.run_reply = bytes.fromhex(flow["run_reply"])
    self.block_status: Deque[bytes] = deque(bytes.fromhex(h) for h in flow["block_status"])
    self.replies: Dict[bytes, Deque[bytes]] = {}
    for request, reply in flow["values"]:
      self.replies.setdefault(bytes.fromhex(request), deque()).append(bytes.fromhex(reply))

  @staticmethod
  def _next(replies: Deque[bytes]) -> bytes:
    return replies.popleft() if len(replies) > 1 else replies[0]

  def __call__(self, payload: bytes) -> bytes:
    if payload[0] in (0x80, 0x0B):
      return self.status
    if payload[0] == 0x04:
      return self.run_reply
    if payload == MW_BLOCK_STATUS:
      return self._next(self.block_status)
    return self._next(self.replies[payload])


class RecordedFlowTests(unittest.IsolatedAsyncioTestCase):
  """Reads through the recorded replies (``load_flows``): the driver requests the data as Voyager
  did."""

  flows: Dict[str, Any]

  @classmethod
  def setUpClass(cls):
    cls.flows = load_flows()

  def make(self, name: str):
    flow = self.flows[name]
    return make_driver(RecordedReader(flow)), bytes.fromhex(flow["run"])[1:]

  async def test_absorbance_spectrum_requests_each_well_in_reading_order(self):
    driver, run = self.make("absorbance_spectrum")
    blocks = await driver.run_absorbance_spectrum(run, timeout=10)
    self.assertEqual([b.well for b in blocks], [(1, 1), (6, 1), (12, 1), (12, 8), (6, 8), (1, 8)])
    requests = [c.hex() for c in commands(driver) if c[:2] == bytes([0x05, 0x02])]
    self.assertEqual(
      requests,
      [
        "0502010100010001",  # A1 with the calibration values
        "0502060100018001",
        "05020c0100018001",
        "05020c0800018001",
        "0502060800018001",
        "0502010800018001",
      ],
    )
    # Fetched part by part, the block is released after the last well.
    sent = commands(driver)
    release = sent.index(bytes.fromhex("2d0100010000"))
    self.assertGreater(release, sent.index(bytes.fromhex("0502010800018001")))

  async def test_kinetic_read_returns_every_cycle(self):
    driver, run = self.make("absorbance_kinetic")
    blocks = await driver.run_blocks(run, timeout=10)
    self.assertEqual([b.cycle for b in blocks], [1, 2, 3, 4, 5, 6])
    self.assertEqual([b.start_time for b in blocks], [0, 10, 20, 30, 40, 50])
    self.assertTrue(all(b.complete for b in blocks))

  async def test_emission_spectrum_is_one_block(self):
    driver, run = self.make("emission_spectrum")
    data = await driver.run_measurement(run, timeout=10)
    self.assertEqual((data.chromatics, data.wells), ((201,), 3))
    self.assertTrue(data.complete)
    requests = [c for c in commands(driver) if c[:2] == bytes([0x05, 0x02])]
    self.assertEqual(requests[-1], VALUES_NEXT_NEW_BLOCK)

  async def test_timeout_stops_the_run(self):
    driver, run = self.make("absorbance_kinetic")
    with self.assertRaises(TimeoutError):
      await driver.run_blocks(run, timeout=0)
    self.assertIn(STOP, commands(driver))


class RobustnessTests(unittest.IsolatedAsyncioTestCase):
  async def test_continuation_out_of_sequence_raises(self):
    driver = make_driver(lambda p: ABSORBANCE_LAST_PAGE)
    io = driver.io
    assert isinstance(io, FakeUSB)
    receive = io._receive

    def renumber(payload: bytes) -> None:
      receive(payload)
      packet = io._continuations[1]
      io._continuations[1] = packet[:3] + (3).to_bytes(4, "big") + packet[7:]

    io._receive = renumber  # type: ignore[method-assign]
    with self.assertRaises(FrameError):
      await driver.exchange(bytes([0x80]))

  async def test_status_with_the_error_bit_raises(self):
    error = bytes([STATUS_IDLE[0], STATUS_IDLE[1] | 0x40]) + STATUS_IDLE[2:]
    driver = make_driver(lambda p: error)
    with self.assertRaises(DeviceError):
      await driver.request_status()

  async def test_status_of_a_reader_not_initialized_is_read(self):
    driver = make_driver(lambda p: STATUS_NOT_INITIALIZED)
    status = await driver.request_status()
    self.assertFalse(status.busy)

  async def test_stop_closes_the_connection_after_a_device_error(self):
    error = bytes([STATUS_IDLE[0], STATUS_IDLE[1] | 0x40]) + STATUS_IDLE[2:]
    driver = make_driver(lambda p: error)
    closed = []

    async def close() -> None:
      closed.append(True)

    driver._usb_close = close  # type: ignore[method-assign]
    with self.assertRaises(DeviceError):
      await driver.stop()
    self.assertEqual(closed, [True])

  def test_a_warning_that_clears_is_logged_again(self):
    driver = make_driver(lambda p: b"")
    warning = bytes([STATUS_IDLE[0], STATUS_IDLE[1] | 0x80]) + STATUS_IDLE[2:]
    with self.assertLogs("pylabrobot.bmg_labtech.vantastar.driver.master", "WARNING") as logs:
      for reply in (warning, warning, STATUS_IDLE, warning):
        driver._check_reply(bytes([0x80]), reply)
    self.assertEqual(len(logs.records), 2)

  def test_well_mask_holds_up_to_384_wells(self):
    self.assertEqual(len(encode_well_mask(24, 16)), 48)
    with self.assertRaises(ValueError):
      encode_well_mask(48, 32)


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

  async def test_temperature_commands_return_once_temperatures_are_reported(self):
    replies = deque([STATUS_IDLE] + [STATUS_AFTER_INCUBATOR_COMMAND] * 3)
    driver = make_driver(lambda p: replies.popleft() if replies else STATUS_HEATING)
    await driver.set_temperature(30)
    self.assertEqual(len(replies), 0)
    self.assertEqual(await driver.get_temperatures(), (30.1, 30.6, 30.5))

  async def test_temperature_command_times_out_without_temperatures(self):
    driver = make_driver(lambda p: STATUS_AFTER_INCUBATOR_COMMAND)
    driver.incubator_settle_timeout = 0
    with self.assertRaises(TimeoutError):
      await driver.set_temperature(30)

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
      if payload == VALUES_WELL_LIST:
        return WELL_LIST_96
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
