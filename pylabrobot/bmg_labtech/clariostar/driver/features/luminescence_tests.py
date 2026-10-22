"""Luminescence tests over a fake io: what the driver sends is pinned to the ground truth, but for
the plate's geometry (a PLR plate; the plate block is tested with the other modes) and the emission
band's edges, which this driver sends at their nominal values; the counts are the ground truth's.

What every mode shares (plate block, scan and settling bytes, shaking, the run and its stop) is
tested with absorbance and fluorescence; only luminescence's own fields and reply are here.
"""

import unittest
from typing import Any, Dict, List

from pylabrobot.bmg_labtech.clariostar.driver.configuration_tests import decoded
from pylabrobot.bmg_labtech.clariostar.driver.features.absorbance_tests import (
  RUN_REPLY,
  STATUS_MEASURED,
  STATUS_MEASURING,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.drawer_tests import make_plate_holder
from pylabrobot.bmg_labtech.clariostar.driver.features.fluorescence_tests import (
  TEMPERATURE_37_DATA_REPLY,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.luminescence import Luminescence
from pylabrobot.bmg_labtech.clariostar.driver.features.measurement import (
  Measurement,
  MeasurementSettings,
)
from pylabrobot.bmg_labtech.clariostar.driver.framing import ftdi_extract_payload
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  STATUS_IDLE,
  FakeFTDI,
  make_driver,
)
from pylabrobot.resources import Plate, cor_96_wellplate_360uL_Fb

# The ground truth: every well, 580-80 nm, gain 3500, focal height 8.5 mm, settling 0.0 s,
# normalization 1.00 s, measurement interval 0.80 s.
LUMINESCENCE_RUN_FRAME = bytes.fromhex(
  "02009a0c0431e82164059604602c521d040c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a014000000000000000000000000000000000000000000000000000"
  "00000000270f270f01035200000100000dac0001000100011834151c0005000000640000000000000000000001000000"
  "0100500001000012f30d"
)

# Its data reply: layout 25, 96 values, ceiling 210,000,000, no temperature.
LUMINESCENCE_DATA_REPLY = bytes.fromhex(
  "0201aa0c02050626000025006000600c845880010001006001000100000001010000000000000000001c0000001c0000"
  "00150000001200000011000000210000002100000010000000170000001400000008000000280000001e000000120000"
  "001f00000012000000120000002300000015000000110000001f0000001a00000010000000160000000b000000100000"
  "001b0000000d0000001f0000000b000000190000002300000029000000170000000f0000001900000020000000210000"
  "0010000000100000000c000000110000001f0000001b00000016000000100000001f0000001400000017000000100000"
  "001500000012000000150000001e000000190000001f0000001200000025000000120000001b000000150000001a0000"
  "00100000003300000024000000230000001900000016000000150000001200000011000000150000001b000000160000"
  "00370000001e000000190000000f000000160000001a0000001b0000001a000000140000001000000019000000200000"
  "0012000000100000001700000016000000140000001200000015000000120000001100000011000c790d"
)

# The ground truth's counts, row by row.
LUMINESCENCE_GROUND_TRUTH = [
  [28, 28, 21, 18, 17, 33, 33, 16, 23, 20, 8, 40],
  [30, 18, 31, 18, 18, 35, 21, 17, 31, 26, 16, 22],
  [11, 16, 27, 13, 31, 11, 25, 35, 41, 23, 15, 25],
  [32, 33, 16, 16, 12, 17, 31, 27, 22, 16, 31, 20],
  [23, 16, 21, 18, 21, 30, 25, 31, 18, 37, 18, 27],
  [21, 26, 16, 51, 36, 35, 25, 22, 21, 18, 17, 21],
  [27, 22, 55, 30, 25, 15, 22, 26, 27, 26, 20, 16],
  [25, 32, 18, 16, 23, 22, 20, 18, 21, 18, 17, 17],
]


def plate() -> Plate:
  """The plate the tests read: a Corning 96 360 uL flat-bottom."""
  return cor_96_wellplate_360uL_Fb("plate")


def make_luminescence(io: FakeFTDI, plate: Plate) -> Luminescence:
  """A luminescence feature whose drawer holds `plate` in its plate holder."""
  driver = make_driver(io)
  driver.configuration = decoded()
  driver.drawer.plate_holder = make_plate_holder()
  driver.drawer.plate_holder.assign_child_resource(plate)
  return Luminescence(driver)


def with_fields(ground_truth: bytes, fields: Dict[int, int]) -> bytes:
  """`ground_truth`'s payload on `plate()`: its plate geometry, and each u16 in `fields` replaced.

  `fields` maps payload offsets to values.
  """
  payload = bytearray(ftdi_extract_payload(ground_truth))
  payload[1:13] = Measurement._encode_plate_geometry(plate())[:12]
  for offset, value in fields.items():
    payload[offset : offset + 2] = value.to_bytes(2, "big")
  return bytes(payload)


# Payload offsets of the luminescence fields, after the plate block, scan byte, pre-separator,
# `27 0f 27 0f` and the settling byte.
FOCAL_HEIGHT = 101
GAIN = 108
EMISSION_EDGES = 116
NORMALIZATION_TIME = 124
MEASUREMENT_INTERVAL = 141

# mm, the ground truth's.
GROUND_TRUTH_FOCAL_HEIGHT = 8.5

# The ground truth as this driver sends it: the emission band at its nominal edges, 620.0 /
# 540.0 nm; the ground truth has 619.6 / 540.4.
NOMINAL_EDGES = {EMISSION_EDGES: 6200, EMISSION_EDGES + 2: 5400}


async def read(io: FakeFTDI, **arguments):
  """A read of the whole plate answered by the ground truth's reply; the result."""
  io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED, LUMINESCENCE_DATA_REPLY]
  return await make_luminescence(io, plate()).measure(
    **{"focal_height": GROUND_TRUTH_FOCAL_HEIGHT, **arguments}
  )


# The driver's asyncio.Lock needs an event loop on Python 3.9: build drivers inside one.
class LuminescenceTests(unittest.IsolatedAsyncioTestCase):
  async def test_the_defaults_are_the_ground_truth(self):
    io = FakeFTDI()
    await read(io)
    self.assertEqual(
      ftdi_extract_payload(io.written[1]), with_fields(LUMINESCENCE_RUN_FRAME, NOMINAL_EDGES)
    )

  async def test_each_field_is_sent_where_the_ground_truth_has_it(self):
    """Every luminescence field changed at once, to values a CLARIOstarPlus (firmware 1.35)
    accepted."""
    io = FakeFTDI()
    await read(
      io,
      focal_height=4.0,
      gain=2000,
      emission_wavelength=560,
      emission_bandwidth=40,
      normalization_time=2.0,
      measurement_interval=0.2,
    )
    fields = {
      FOCAL_HEIGHT: 400,
      GAIN: 2000,
      EMISSION_EDGES: 5800,
      EMISSION_EDGES + 2: 5400,
      NORMALIZATION_TIME: 200,
      MEASUREMENT_INTERVAL: 20,
    }
    self.assertEqual(
      ftdi_extract_payload(io.written[1]), with_fields(LUMINESCENCE_RUN_FRAME, fields)
    )

  async def test_none_reads_a_quarter_up_the_well(self):
    # Corning 96 360 uL: the well at 3.03 mm, its floor 0.50 mm, its depth 10.67 mm: 6.2 mm.
    runs = []
    for focal_height in (None, 6.2):
      io = FakeFTDI()
      io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED]
      io.replies.append(LUMINESCENCE_DATA_REPLY)
      feature = make_luminescence(io, cor_96_wellplate_360uL_Fb("plate"))
      await feature.measure(focal_height=focal_height)
      runs.append(io.written[1])
    self.assertEqual(runs[0], runs[1])
    with self.assertRaises(ValueError):
      feature.default_focal_height_percent_of_well_depth = 101

  async def test_counts_are_the_ground_truth(self):
    result = await read(FakeFTDI())
    expected = {
      f"{'ABCDEFGH'[row]}{column + 1}": float(value)
      for row, values in enumerate(LUMINESCENCE_GROUND_TRUTH)
      for column, value in enumerate(values)
    }
    self.assertEqual(result.data, expected)
    self.assertIsNone(result.temperature)
    with self.assertRaises(ValueError):  # fluorescence's layout
      make_luminescence(FakeFTDI(), plate())._decode_data_reply(
        ftdi_extract_payload(TEMPERATURE_37_DATA_REPLY), 96
      )

  async def test_what_luminescence_cannot_read_sends_nothing(self):
    io = FakeFTDI()
    feature = make_luminescence(io, plate())
    refused: List[Dict[str, Any]] = [
      dict(measurement_settings=MeasurementSettings(well_scan="orbital")),
      dict(measurement_settings=MeasurementSettings(pause_before_reading=5)),
      dict(measurement_interval=0.805),  # not in 0.01 s steps
    ]
    for arguments in refused:
      with self.assertRaises(ValueError):
        await feature.measure(**arguments)
    self.assertEqual(io.written, [])

  def test_the_wavelength_limits_follow_the_photomultipliers(self):
    # (extended red, dual, crossed over): the highest emission wavelength.
    for photomultipliers, highest in (
      ((True, True, False), 740),  # the reader of the hardware reads
      ((True, True, True), 840),
      ((True, False, False), 840),
      ((False, False, False), 740),
    ):
      with self.subTest(photomultipliers=photomultipliers):
        driver = make_driver(FakeFTDI())
        driver.configuration = decoded()
        driver.configuration.luminescence_channels = 1
        (
          driver.configuration.extended_red_photomultiplier_tube,
          driver.configuration.dual_photomultiplier_tubes,
          driver.configuration.photomultiplier_tube_crossover,
        ) = photomultipliers
        driver._discover_features()
        assert driver.luminescence is not None
        self.assertEqual(driver.luminescence.configuration.wavelength_range[1], highest)

  def test_luminescence_is_built_only_when_reported(self):
    for channels, built in ((1, True), (0, False)):
      driver = make_driver(FakeFTDI())
      driver.configuration = decoded()
      driver.configuration.luminescence_channels = channels
      driver._discover_features()
      self.assertEqual(isinstance(driver.luminescence, Luminescence), built)
