"""Offline tests for the M1000 Pro absorbance operations."""

import inspect
import math
import tempfile
import unittest
from pathlib import Path
from struct import pack
from unittest.mock import AsyncMock, patch

from pylabrobot.resources.biorad.plates import (
  biorad_96_wellplate_200uL_Vb,
  biorad_384_wellplate_50uL_Vb,
)
from pylabrobot.tecan.infinite_m1000_pro import (
  KNOWN_PLATES,
  OVER,
  InfiniteM1000Pro,
  PlateGeometry,
  _absorbance_od,
  _decode_absorbance_data_4channel,
  _decode_absorbance_prepare_4channel,
  _multi_read_runs,
  _plan_passes,
)
from pylabrobot.tecan.infinite_m1000_pro_tests import reader_frame

CAL = bytes.fromhex(
  "0001 1770 00000000 0000 0001 0001 0001 0001 0064 0064 0064 0064 076c 076c 076c 076c 0001 0064 07d0"
)
PACKET = bytes.fromhex("0001 1770 00000000 0000 03e8 03e8 03e8 03e8 07d0")
HALF = 0.3010299956639812  # OD of PACKET against CAL


def binary_block(data, sequence=0):
  checksum = 0
  for byte in data:
    checksum ^= byte
  return (
    reader_frame(f"{len(data)},BIN:", sequence)
    + data
    + bytes([sequence])
    + len(data).to_bytes(2, "big")
    + bytes([checksum])
  )


def burst(kind, blocks, sequence=0):
  header = pack(
    ">HHBBIIHI", 256, 0, 20, 0, sum(map(len, blocks)), max(map(len, blocks)), kind, 25000
  )
  return (
    binary_block(header, sequence)
    + b"".join(binary_block(b, sequence) for b in blocks)
    + reader_frame("ST", sequence)
  )


def with_wavelength(block, raw):
  return block[:2] + raw.to_bytes(2, "big") + block[4:]


def reply(command, sequence):
  """The reader's reply to a non-measurement command, as seen in captures."""
  if command == "TT":
    return reader_frame("TT#T60000", sequence) + reader_frame("ST", sequence)
  if command == "QQ":
    return reader_frame("ST", sequence)
  if command == "?ABSOLUTE MTP,POS":
    return reader_frame("IN", sequence)
  if command == "?TEMPERATURE PLATE,CURRENT":
    return reader_frame("250", sequence)
  if command == "?CONFIG CHECK,FLASHCOUNT":
    return reader_frame("1000", sequence)
  if command.startswith("#"):
    return reader_frame("5", sequence)
  if command.startswith(("ABSOLUTE MTP,", "CONFIG CHECK,FLASHCOUNT=")) or command in (
    "MODE ABS",
    "TERMINATE",
  ):
    return reader_frame("BY#T5000", sequence) + reader_frame("ST", sequence)
  if (command.startswith("POSITION ") and command != "POSITION CLEAR") or command.startswith(
    ("FLASH ", "BEAM ", "MOVEMENT ", "SIBUS ")
  ):
    return reader_frame("ST", sequence)
  return reader_frame("+", sequence)


class FakeReader:
  """Synthetic transport: bursts from queues, other commands from `reply`."""

  def __init__(self, prepare, scans, chunk=13):
    self.prepare = list(prepare)
    self.scans = list(scans)
    self.chunk = chunk
    self.pending = bytearray()
    self.commands = []

  async def write(self, data):
    command = data[1 : data.index(b"\x03")].decode("ascii")
    sequence = data[-5]
    self.commands.append(command)
    if command == "PREPARE REF":
      self.pending.extend(burst(1024, self.prepare, sequence))
    elif command.startswith("SCANX "):
      self.pending.extend(burst(1040, self.scans.pop(0), sequence))
    else:
      self.pending.extend(reply(command, sequence))

  async def read(self, timeout):
    del timeout
    if not self.pending:
      raise TimeoutError("test transport has no response")
    chunk = bytes(self.pending[: self.chunk])
    del self.pending[: self.chunk]
    return chunk

  def device(self, usb_class):
    usb = usb_class.return_value
    usb.write = AsyncMock(side_effect=self.write)
    usb.read = AsyncMock(side_effect=self.read)
    usb.drain = AsyncMock()
    device = InfiniteM1000Pro()
    device._ready = True
    device._initialized = True
    return device


REFERENCE_ABSORBANCE_COMMANDS = [
  "?ABSOLUTE MTP,POS",
  "?TEMPERATURE PLATE,CURRENT",
  "MODE ABS",
  "?CONFIG CHECK,FLASHCOUNT",
  "EXCITATION CLEAR",
  "#EXCITATION MAXINDEX",
  "TIME CLEAR",
  "GAIN CLEAR",
  "READS CLEAR",
  "POSITION CLEAR",
  "FLASH ENERGY=LOW",
  "EXCITATION 0,ABS,6000,50,0",
  "TIME 0,READDELAY=0",
  "READS 0,NUMBER=25",
  "READS 0,SPEED=2500",
  "#POSITION ABS,Z",
  "POSITION 0,Z=17514",
  "SCAN DIRECTION=ALTUP",
  "RATIO LABELS=1",
  "BEAM DIAMETER=800",
  "EXCITATION 1,ABS,6000,50,0",
  "TIME 1,READDELAY=0",
  "READS 1,NUMBER=25",
  "READS 1,SPEED=2500",
  "POSITION 1,Z=17514",
  "PREPARE REF",
  "#POSITION MTP Y",
  "ABSOLUTE MTP,Y=11240",
  "ABSOLUTE MTP,X=14380,Y=11240",
  "SCANX 14380,113380,12",
  "ABSOLUTE MTP,Y=47240",
  "ABSOLUTE MTP,X=113380,Y=47240",
  "SCANX 113380,14380,12",
  "TERMINATE",
  "CONFIG CHECK,FLASHCOUNT=1002",
  "SIBUS TCAN SSL 0",
]


class AbsorbanceOperationTests(unittest.IsolatedAsyncioTestCase):
  async def test_full_plate_matches_icontrol_sequence(self):
    """Fragmented transport, i-control command order, 96 mapped results, run bookkeeping."""
    fake = FakeReader([CAL], [[PACKET] * 12, [PACKET] * 12])
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      plate = biorad_96_wellplate_200uL_Vb("plate")
      result = await device.read_absorbance(plate, plate_definition="GRE96ft")
    self.assertEqual(fake.commands, REFERENCE_ABSORBANCE_COMMANDS)
    self.assertEqual(len(result.data), 96)
    self.assertAlmostEqual(result.data["A1"], HALF)
    self.assertAlmostEqual(result.data["H12"], HALF)
    self.assertEqual(result.temperature, 25.0)
    self.assertFalse(fake.pending)

  async def test_parameters_reach_the_reader(self):
    cal = with_wavelength(CAL, 2300)
    packet = with_wavelength(PACKET, 2300)
    fake = FakeReader([cal], [[packet] * 12, [packet] * 12])
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      plate = biorad_96_wellplate_200uL_Vb("plate")
      await device.read_absorbance(
        plate, wavelength=230, flashes=50, settle_time=5, z=18000, plate_definition="GRE96ft"
      )
    for command in (
      "EXCITATION 0,ABS,2300,25,0",
      "TIME 1,READDELAY=5000",
      "READS 0,NUMBER=50",
      "POSITION 1,Z=18000",
    ):
      self.assertIn(command, fake.commands)

  async def test_well_selection_scans_only_needed_passes_and_columns(self):
    fake = FakeReader([CAL], [[PACKET] * 2])
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      plate = biorad_96_wellplate_200uL_Vb("plate")
      result = await device.read_absorbance(
        plate, plate.get_items(["B3", "C4"]), plate_definition="GRE96ft"
      )
    self.assertEqual(set(result.data), {"B3", "C4"})
    self.assertEqual([c for c in fake.commands if c.startswith("SCANX")], ["SCANX 32380,41380,2"])
    self.assertIn("CONFIG CHECK,FLASHCOUNT=1001", fake.commands)

  async def test_384_subset_matches_captured_geometry(self):
    fake = FakeReader([CAL], [[PACKET] * 2, [PACKET] * 2])
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      plate = biorad_384_wellplate_50uL_Vb("plate")
      result = await device.read_absorbance(
        plate, plate.get_items("A1:H2"), plate_definition="GRE384ft"
      )
    self.assertEqual(len(result.data), 16)
    self.assertIn("POSITION 0,Z=17136", fake.commands)
    scan_moves = [c for c in fake.commands if c.startswith(("SCANX", "ABSOLUTE MTP,X"))]
    self.assertEqual(
      scan_moves,
      [
        "ABSOLUTE MTP,X=12130,Y=8990",
        "SCANX 12130,16630,2",
        "ABSOLUTE MTP,X=16630,Y=13490",
        "SCANX 16630,12130,2",
      ],
    )

  async def test_multiple_reads_per_well(self):
    """A 2 x 2 grid: one SCANX per grid line, mean/stdev and i-control grid labels."""
    low = bytes.fromhex("0001 1770 00000000 0000 03e8 03e8 03e8 03e8 07d0")
    high = bytes.fromhex("0001 1770 00000000 0000 01f4 01f4 01f4 01f4 07d0")
    fake = FakeReader([CAL], [[low, high], [low, high]])
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      plate = biorad_96_wellplate_200uL_Vb("plate")
      result = await device.read_absorbance(
        plate,
        [plate.get_item("A1")],
        reads_per_well=2,
        settle_time=10,
        plate_definition="GRE96ft",
      )
    moves = fake.commands[fake.commands.index("#POSITION MTP Y") + 1 : -3]
    self.assertEqual(
      moves,
      [
        "ABSOLUTE MTP,X=14380,Y=11240",
        "MOVEMENT MTP,ACCELERATION=LOW",
        "ABSOLUTE MTP,X=12758,Y=9618",
        "SCANX 12758,16003,2",
        "ABSOLUTE MTP,X=16003,Y=12863",
        "SCANX 16003,12758,2",
        "MOVEMENT MTP,ACCELERATION=NORMAL",
      ],
    )
    reads = result.reads["A1"]
    self.assertEqual(set(reads), {"0;1", "1;1", "1;0", "0;0"})
    self.assertAlmostEqual(reads["0;1"], HALF)
    self.assertAlmostEqual(reads["0;0"], reads["1;1"])
    self.assertAlmostEqual(result.data["A1"], sum(reads.values()) / 4)
    self.assertGreater(result.standard_deviation["A1"], 0)
    self.assertIn("CONFIG CHECK,FLASHCOUNT=1002", fake.commands)

  async def test_square_pattern_scans_the_border_clockwise(self):
    fake = FakeReader([CAL], [[PACKET] * 3, [PACKET], [PACKET] * 3, [PACKET]])
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      plate = biorad_96_wellplate_200uL_Vb("plate")
      result = await device.read_absorbance(
        plate,
        [plate.get_item("A1")],
        reads_per_well=3,
        multi_read_pattern="square",
        settle_time=10,
        plate_definition="GRE96ft",
      )
    self.assertEqual(
      [c for c in fake.commands if c.startswith("SCANX")],
      [
        "SCANX 12758,16002,3",
        "SCANX 16002,16002,1",
        "SCANX 16002,12758,3",
        "SCANX 12758,12758,1",
      ],
    )
    self.assertEqual(
      list(result.reads["A1"]), ["0;2", "1;2", "2;2", "2;1", "2;0", "1;0", "0;0", "0;1"]
    )

  async def test_circle_patterns_are_rejected_before_io(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = FakeReader([], []).device(usb_class)
      plate = biorad_96_wellplate_200uL_Vb("plate")
      with self.assertRaisesRegex(ValueError, "circle"):
        await device.read_absorbance(
          plate, reads_per_well=3, multi_read_pattern="circle", plate_definition="GRE96ft"
        )
      usb_class.return_value.write.assert_not_awaited()

  async def test_spectrum(self):
    wavelengths = (2300, 2320, 2340)
    prepare = [with_wavelength(CAL, w) for w in wavelengths]
    scan = [with_wavelength(PACKET, w) for w in wavelengths]
    fake = FakeReader(prepare, [scan])
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      plate = biorad_96_wellplate_200uL_Vb("plate")
      result = await device.read_absorbance_spectrum(
        plate,
        [plate.get_item("A1")],
        start_wavelength=230,
        end_wavelength=234,
        step_size=2,
        plate_definition="GRE96ft",
      )
    self.assertIn("EXCITATION 0,ABS,2300~2340:20,25,0", fake.commands)
    self.assertIn("SCAN DIRECTION=UP", fake.commands)
    self.assertNotIn("BEAM DIAMETER=800", fake.commands)
    self.assertEqual(
      [c for c in fake.commands if c.startswith(("SCANX", "ABSOLUTE MTP,X"))],
      ["ABSOLUTE MTP,X=14380,Y=11240", "ABSOLUTE MTP,X=14380,Y=11240", "SCANX 14380,14380,1"],
    )
    self.assertEqual(list(result.data["A1"]), [230, 232, 234])
    self.assertAlmostEqual(result.data["A1"][232], HALF)

  async def test_wrong_scan_point_count_terminates(self):
    fake = FakeReader([CAL], [[PACKET] * 13, [PACKET] * 11])
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      plate = biorad_96_wellplate_200uL_Vb("plate")
      with self.assertRaisesRegex(ValueError, "Expected 12 scan blocks"):
        await device.read_absorbance(plate, plate_definition="GRE96ft")
    self.assertEqual(sum(c.startswith("SCANX ") for c in fake.commands), 1)
    # i-control's Stop: TT, QQ, flash counter, TCAN; no TERMINATE
    self.assertEqual(
      fake.commands[-4:], ["TT", "QQ", "CONFIG CHECK,FLASHCOUNT=1001", "SIBUS TCAN SSL 0"]
    )
    self.assertNotIn("TERMINATE", fake.commands)
    self.assertTrue(device._ready)

  async def test_bad_command_response_raises_without_further_commands(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[reader_frame("IN"), reader_frame("250", 1), reader_frame("ER", 2)]
      )
      device = InfiniteM1000Pro()
      device._ready = True
      plate = biorad_96_wellplate_200uL_Vb("plate")
      with self.assertRaisesRegex(RuntimeError, "Unexpected response"):
        await device.read_absorbance(plate, wavelength=600, plate_definition="GRE96ft")
      self.assertTrue(device._ready)
      self.assertEqual(usb.write.await_count, 3)

  async def test_corrupt_binary_burst_is_rejected(self):
    data = bytearray(burst(1024, [bytes(40)]))
    data[-10] ^= 1
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=bytes(data))
      device = InfiniteM1000Pro()
      with self.assertRaises(ValueError):
        await device._absorbance_burst("PREPARE REF", 1024)

  async def test_invalid_requests_do_not_write(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.write = AsyncMock()
      device = InfiniteM1000Pro()
      device._ready = True
      plate = biorad_96_wellplate_200uL_Vb("plate")
      with self.assertRaisesRegex(ValueError, "No wells"):
        await device.read_absorbance(plate, [], plate_definition="GRE96ft")
      with self.assertRaisesRegex(ValueError, "Unknown plate definition"):
        await device.read_absorbance(plate, plate_definition="no_such_plate")
      with self.assertRaisesRegex(ValueError, "16x24"):
        await device.read_absorbance(plate, plate_definition="GRE384ft")
      with self.assertRaisesRegex(ValueError, "height with cover"):
        await device.read_absorbance(
          plate,
          plate_lid=True,
          plate_definition=PlateGeometry("X", 8, 12, 14380, 11240, 113380, 74240),
        )
      usb.write.assert_not_awaited()


class GeometryTests(unittest.TestCase):
  def test_384_passes_cover_rows_two_apart(self):
    geometry = KNOWN_PLATES["GRE384ft"]
    cells = {(row, column) for row in range(16) for column in range(24)}
    passes = _plan_passes(geometry, cells, channels=4)
    self.assertEqual([p.y for p in passes], [8990, 13490, 44990, 49490])
    self.assertEqual(passes[1].rows, (1, 3, 5, 7))
    self.assertEqual(passes[0].columns[0], 0)
    self.assertEqual(passes[1].columns[0], 23)

  def test_pdfx_matches_known_geometry(self):
    pdfx = """<?xml version="1.0" encoding="UTF-8"?>
<TecanFile xmlns="tecan.at.schema.documents"><TecanPlateDefinition name="GRE96ft">
<DefinitionRectanglePlateBaseDimensions firstColumnDistanceX="14380" firstColumnDistanceY="11240"
 lastColumnDistanceX="113380" lastColumnDistanceY="74240" nrOfRows="8" nrOfColumns="12"
 heightTolerance="200" height="14600" heightWithCover="17606" />
<DefinitionWellBaseDimensions dimensionX="6960" dimensionBottomX="6390" dimensionY="6960"
 dimensionBottomY="6390" dimensionZ="10900" />
</TecanPlateDefinition></TecanFile>"""
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "GRE96ft.pdfx"
      path.write_text(pdfx, encoding="utf8")
      geometry = PlateGeometry.from_pdfx(path)
    self.assertEqual(geometry, KNOWN_PLATES["GRE96ft"])
    self.assertEqual(geometry.x(11), 113380)
    self.assertEqual(geometry.y(4), 47240)

  def test_absorbance_z_matches_icontrol(self):
    """Z values i-control sent for captured plate definitions, with and without lid."""
    cos96ft = PlateGeometry("COS96ft", 8, 12, 14370, 11240, 113370, 74240, 14220, None, 250, 10670)
    self.assertEqual(KNOWN_PLATES["GRE96ft"].absorbance_z(), 17514)
    self.assertEqual(KNOWN_PLATES["GRE96ft"].absorbance_z(lid=True), 20496)
    self.assertEqual(KNOWN_PLATES["GRE384ft"].absorbance_z(), 17136)
    self.assertEqual(cos96ft.absorbance_z(), 17262)
    with self.assertRaises(ValueError):
      cos96ft.absorbance_z(lid=True)

  def test_multiple_reads_spacing_matches_icontrol(self):
    """Spacing i-control used in captured multiple-reads methods."""
    gre96, gre384 = KNOWN_PLATES["GRE96ft"], KNOWN_PLATES["GRE384ft"]
    half_area = PlateGeometry(
      "GRE96ft_half area",
      8,
      12,
      14380,
      11240,
      113380,
      74240,
      14400,
      17406,
      200,
      11500,
      5000,
      5000,
      4380,
      4380,
    )
    for geometry, pattern, size, border, step in (
      (gre96, "filled_square", 4, 500, 1081),
      (gre96, "filled_square", 3, 1000, 1269),
      (gre384, "filled_square", 2, 500, 1060),
      (half_area, "filled_square", 4, 500, 608),
      (gre96, "square", 4, 500, 1081),
      (gre96, "xy_lines", 5, 500, 1147),
    ):
      _, step_x, step_y = _multi_read_runs(geometry, pattern, size, border, 800)
      self.assertEqual((step_x, step_y), (step, step), (pattern, size, border))
    runs, _, _ = _multi_read_runs(gre96, "xy_lines", 5, 500, 800)
    self.assertEqual([len(run) for run in runs], [1, 1, 5, 1, 1])


class AbsorbanceCalculationTests(unittest.TestCase):
  def test_no_light_above_dark_is_over(self):
    """i-control prints OVER when the count does not exceed dark (spectrum workbook)."""
    cal = _decode_absorbance_prepare_4channel(CAL).items[0]
    item = _decode_absorbance_data_4channel(
      bytes.fromhex("0001 1770 00000000 0000 0064 0063 03e8 03e8 07d0")
    ).items[0]
    self.assertEqual(_absorbance_od(cal, item, 0), OVER)
    self.assertEqual(_absorbance_od(cal, item, 1), OVER)
    self.assertAlmostEqual(_absorbance_od(cal, item, 2), HALF)

  def test_low_reference_is_nan(self):
    cal = _decode_absorbance_prepare_4channel(CAL).items[0]
    item = _decode_absorbance_data_4channel(
      bytes.fromhex("0001 1770 00000000 0000 03e8 03e8 03e8 03e8 0100")
    ).items[0]
    self.assertTrue(math.isnan(_absorbance_od(cal, item, 0)))


class AbsorbancePlateLidTests(unittest.IsolatedAsyncioTestCase):
  async def _read_reference(self, **kwargs):
    fake = FakeReader([CAL], [[PACKET] * 12, [PACKET] * 12])
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      device = fake.device(usb_class)
      plate = biorad_96_wellplate_200uL_Vb("plate")
      result = await device.read_absorbance(plate, plate_definition="GRE96ft", **kwargs)
    self.assertFalse(fake.pending)
    return fake.commands, result

  def test_plate_lid_is_keyword_only_and_defaults_to_false(self):
    parameter = inspect.signature(InfiniteM1000Pro.read_absorbance).parameters.get("plate_lid")
    self.assertIsNotNone(parameter)
    assert parameter is not None
    self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
    self.assertIs(parameter.default, False)

  async def test_plate_lid_raises_z_to_height_with_cover(self):
    commands, result = await self._read_reference(plate_lid=True)
    self.assertIn("POSITION 0,Z=20496", commands)
    self.assertEqual(len(result.data), 96)
