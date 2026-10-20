"""Fluorescence tests over a fake io: what the driver sends is pinned to vendor captures (F-A01 to
F-J01), but for the band edges, which this driver sends at their nominal values."""

import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

from pylabrobot.bmg_labtech.clariostar.driver.configuration_tests import decoded
from pylabrobot.bmg_labtech.clariostar.driver.features import fluorescence
from pylabrobot.bmg_labtech.clariostar.driver.features.absorbance_tests import (
  COLUMN_1_DATA_REPLY,
  RUN_REPLY,
  STATUS_MEASURED,
  STATUS_MEASURING,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.drawer_tests import make_plate_holder
from pylabrobot.bmg_labtech.clariostar.driver.features.fluorescence import (
  Fluorescence,
  _compute_band_edges,
  _decode_data_reply,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.measurement import (
  MeasurementSettings,
  ShakeSettings,
)
from pylabrobot.bmg_labtech.clariostar.driver.framing import ftdi_extract_payload
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  STATUS_IDLE,
  FakeFTDI,
  load_session_replies,
  make_driver,
)
from pylabrobot.lib.optics.filters import DichroicFilter, OpticalFilter
from pylabrobot.resources import Plate, Well, cor_96_wellplate_360uL_Fb, create_ordered_items_2d

# F-A01
BASELINE_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a00010000141b0d"
)

# F-B01
WAVELENGTHS_540_590_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8156114cf1606176d16ab00040003000000000000000000000100"
  "000001000a0001000014940d"
)

# F-B03
WAVELENGTHS_620_680_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8188117ef19581af01a3000040003000000000000000000000100"
  "000001000a00010000153e0d"
)

# F-C02
EMISSION_BANDWIDTH_100_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a9145c1961157f00040003000000000000000000000100"
  "000001000a0001000014610d"
)

# F-D01
GAIN_500_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c01f4133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a0001000014250d"
)

# F-E01
FOCAL_HEIGHT_4_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05019000000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a0001000014570d"
)

# F-G01
ORBITAL_RUN_FRAME = bytes.fromhex(
  "0200a10c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a300000000000000000000000000000000000000000000000000000"
  "00000000270f270f0303027b0005035200000100000000000c03e8133b12a913bc1501143f0004000300000000000000"
  "000000010000000100070001000014d00d"
)

# F-H01
SHAKE_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000020000000000020005000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a0001000014240d"
)

# F-I01
SETTLING_05_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f19035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a00010000142f0d"
)

# F-J01
ROW_BY_ROW_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "000000000000000000000000000000000000000002000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a0001000014130d"
)

# F-K01, at 37 C
TEMPERATURE_37_DATA_REPLY = bytes.fromhex(
  "0201aa0c020506260000a1006000600003f7a0010001006001000100000001010000000001720000008d0000008f0000"
  "00a50000009d00000093000000980000009600000093000000950000009400000ac5000000950000009a000000970000"
  "009d0000009b0000009100000093000000a30000009b00000718000000ae0000084d0000009500000aaf000007d90000"
  "00b50000086f0000078800000734000006f1000006c100000707000000bf000008550000009900000ad2000007f10000"
  "06ea00000640000005e500000611000006bb0000068d000006cf000000d20000082e000000a0000000eb000000fd0000"
  "00b7000000ce000000de000000f1000000f3000000f30000071f000000df000008020000009c000000ef000000f20000"
  "00f1000001040000010300000105000000f0000000f30000076a000000f6000000eb00000094000000900000009f0000"
  "009b0000009a000000970000009f000000990000009a000000970000009f0000009f0000009b0000008a000000950000"
  "008e000000940000008f0000008a000000960000008c00000090000000930000008e00000089003fd70d"
)

# F-K01: the vendor software's export of the same read, row by row.
TEMPERATURE_37_EXPORT = [
  [141, 143, 165, 157, 147, 152, 150, 147, 149, 148, 2757, 149],
  [154, 151, 157, 155, 145, 147, 163, 155, 1816, 174, 2125, 149],
  [2735, 2009, 181, 2159, 1928, 1844, 1777, 1729, 1799, 191, 2133, 153],
  [2770, 2033, 1770, 1600, 1509, 1553, 1723, 1677, 1743, 210, 2094, 160],
  [235, 253, 183, 206, 222, 241, 243, 243, 1823, 223, 2050, 156],
  [239, 242, 241, 260, 259, 261, 240, 243, 1898, 246, 235, 148],
  [144, 159, 155, 154, 151, 159, 153, 154, 151, 159, 159, 155],
  [138, 149, 142, 148, 143, 138, 150, 140, 144, 147, 142, 137],
]

# F-D02, gain 4000: every well at the detector's ceiling
SATURATED_DATA_REPLY = bytes.fromhex(
  "0201aa0c020506260000a1006000600003f7a0010001006001000100000001010000000000000003f7a00003f7a00003"
  "f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003"
  "f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003"
  "f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003"
  "f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003"
  "f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003"
  "f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003"
  "f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003"
  "f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a00003f7a0009e0d0d"
)

# F-Lf01
FILTERS_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e80002000100020001000200010001000000000000000000000100"
  "000001000a0001000011dd0d"
)

# F-Lf02
EMISSION_FILTER_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e80fe70f5900020001000200010003000000000000000000000100"
  "000001000a00010000133a0d"
)

# F-Lf03
EXCITATION_FILTER_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e80002000100021501143f00040001000000000000000000000100"
  "000001000a0001000012460d"
)

# F-L01
DICHROIC_SPLIT_510_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913ec1501143f00040003000000000000000000000100"
  "000001000a00010000144b0d"
)


def fluorescence_plate() -> Plate:
  """A plate of the F-series geometry: 127.80 x 85.50 mm, A1 centre 14.30 / 11.20 mm, H12 centre
  113.50 / 74.30 mm, 6.35 mm wells (F-G01)."""
  return Plate(
    "fluorescence",
    size_x=127.80,
    size_y=85.50,
    size_z=14.0,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.30 - 6.35 / 2,
      dy=11.20 - 6.35 / 2,
      dz=1.0,
      item_dx=99.2 / 11,
      item_dy=63.1 / 7,
      size_x=6.35,
      size_y=6.35,
      size_z=10.0,
    ),
  )


def make_fluorescence(io: FakeFTDI, plate: Optional[Plate] = None) -> Fluorescence:
  """A fluorescence feature whose drawer holds `plate` in its plate holder."""
  driver = make_driver(io)
  driver.configuration = decoded()
  driver.drawer.plate_holder = make_plate_holder()
  if plate is not None:
    driver.drawer.plate_holder.assign_child_resource(plate)
  return Fluorescence(driver)


# The filters of the captured reader, as its filter detection reports them.
EX_TR = OpticalFilter(1, "Ex TR", passbands=((282.2, 371.8),))
LP_TR = DichroicFilter(1, "LP TR", transmission_bands=((421.8, None),))
EM_615_18 = OpticalFilter(1, "615-18", passbands=((604.9, 623.2),))

# The captured reads' arguments, as F-A01 but for what each capture varies.
BASELINE: Dict[str, Any] = dict(
  excitation_wavelength=485, emission_wavelength=528, gain=1000, focal_height=8.5
)
CAPTURED_READS: Dict[str, Tuple[bytes, Dict[str, Any], Dict[str, Any], Optional[ShakeSettings]]] = {
  "F-A01": (BASELINE_RUN_FRAME, {}, {}, None),
  "F-B01": (
    WAVELENGTHS_540_590_RUN_FRAME,
    dict(excitation_wavelength=540, emission_wavelength=590),
    {},
    None,
  ),
  "F-B03": (
    WAVELENGTHS_620_680_RUN_FRAME,
    dict(excitation_wavelength=620, emission_wavelength=680),
    {},
    None,
  ),
  "F-C02": (
    EMISSION_BANDWIDTH_100_RUN_FRAME,
    dict(emission_wavelength=600, emission_bandwidth=100),
    {},
    None,
  ),
  "F-D01": (GAIN_500_RUN_FRAME, dict(gain=500), {}, None),
  "F-E01": (FOCAL_HEIGHT_4_RUN_FRAME, dict(focal_height=4.0), {}, None),
  "F-G01": (ORBITAL_RUN_FRAME, {}, dict(well_scan="orbital", scan_diameter=3, flashes=7), None),
  "F-H01": (SHAKE_RUN_FRAME, {}, {}, ShakeSettings("orbital", 300, 5)),
  "F-I01": (SETTLING_05_RUN_FRAME, {}, dict(settling_time=0.5), None),
  "F-J01": (ROW_BY_ROW_RUN_FRAME, {}, dict(vertical=False), None),
  "F-L01": (DICHROIC_SPLIT_510_RUN_FRAME, dict(dichroic_split=510.0), {}, None),
  "F-Lf01": (
    FILTERS_RUN_FRAME,
    dict(
      excitation_wavelength=None,
      emission_wavelength=None,
      excitation_filter=EX_TR,
      emission_filter=EM_615_18,
      dichroic_filter=LP_TR,
    ),
    {},
    None,
  ),
  "F-Lf02": (
    EMISSION_FILTER_RUN_FRAME,
    dict(
      excitation_wavelength=400,
      emission_wavelength=None,
      emission_filter=EM_615_18,
      dichroic_filter=LP_TR,
    ),
    {},
    None,
  ),
  "F-Lf03": (
    EXCITATION_FILTER_RUN_FRAME,
    dict(excitation_wavelength=None, excitation_filter=EX_TR, dichroic_filter=LP_TR),
    {},
    None,
  ),
}


def with_nominal_edges(captured: bytes, arguments: Dict[str, Any]) -> bytes:
  """`captured`'s payload with the band edges of each side read through its monochromator set to
  their nominal values: the vendor software sends each band narrower (F-A01: 14.6 nm of 15,
  19.4 nm of 20)."""
  payload = bytearray(ftdi_extract_payload(captured))
  pair = len(payload) - 36  # gain, then the excitation edges, the dichroic and the emission edges
  for side, offset, default in (("excitation", 2, 15), ("emission", 8, 20)):
    wavelength = arguments.get(f"{side}_wavelength")
    if wavelength is None:
      continue
    edges = _compute_band_edges(wavelength, arguments.get(f"{side}_bandwidth") or default)
    for index, value in enumerate(edges):
      start = pair + offset + 2 * index
      payload[start : start + 2] = value.to_bytes(2, "big")
  return bytes(payload)


async def read(io: FakeFTDI, reply: bytes, **arguments):
  """A read of the whole fluorescence plate answered by `reply`; the feature and the result."""
  io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED, reply]
  feature = make_fluorescence(io, fluorescence_plate())
  return feature, await feature.measure(**{**BASELINE, **arguments})


# The driver's asyncio.Lock needs an event loop on Python 3.9: build drivers inside one.
class RunParametersTests(unittest.IsolatedAsyncioTestCase):
  async def test_captured_reads_but_their_band_edges(self):
    for name, (captured, arguments, settings, shake) in CAPTURED_READS.items():
      with self.subTest(name):
        io = FakeFTDI()
        _, result = await read(
          io,
          TEMPERATURE_37_DATA_REPLY,
          measurement_settings=MeasurementSettings(**{"flashes": 10, **settings}),
          shake_settings=shake,
          **arguments,
        )
        given = {**BASELINE, **arguments}
        self.assertEqual(ftdi_extract_payload(io.written[1]), with_nominal_edges(captured, given))
        # The result says what the read used: a filter's passband, or the monochromator's band.
        excitation_filter, emission_filter = (
          given.get("excitation_filter"),
          given.get("emission_filter"),
        )
        self.assertEqual(
          result.excitation_bandwidth,
          excitation_filter.bandwidth
          if excitation_filter is not None
          else given.get("excitation_bandwidth") or 15,
        )
        self.assertEqual(
          result.emission_bandwidth,
          emission_filter.bandwidth
          if emission_filter is not None
          else given.get("emission_bandwidth") or 20,
        )
        self.assertEqual(result.gain, given["gain"])


class FilterPositionTests(unittest.IsolatedAsyncioTestCase):
  async def test_every_slot_moves_its_slides(self):
    """Excitation (slide 1, slide 2), emission (slide 2, slide 1), dichroic slot + 1; 1 is a
    slide's home, 2 its first filter (the filter detection capture). Only slot 1 and dichroic A
    are a reader's (F-Lf01); the others are predictions no reader has answered."""
    excitation = {1: (2, 1), 2: (3, 1), 3: (1, 2), 4: (1, 3)}
    emission = {1: (1, 2), 2: (1, 3), 3: (2, 1), 4: (3, 1)}
    for slot in range(1, 5):
      for dichroic in range(1, 4):
        with self.subTest(slot=slot, dichroic=dichroic):
          io = FakeFTDI()
          await read(
            io,
            TEMPERATURE_37_DATA_REPLY,
            excitation_wavelength=None,
            emission_wavelength=None,
            excitation_filter=OpticalFilter(slot),
            emission_filter=OpticalFilter(slot),
            dichroic_filter=DichroicFilter(dichroic),
          )
          payload = ftdi_extract_payload(io.written[1])
          pair = len(payload) - 36
          fields = [
            int.from_bytes(payload[pair + i : pair + i + 2], "big") for i in (2, 4, 6, 8, 10)
          ]
          self.assertEqual(fields, [*excitation[slot], dichroic + 1, *emission[slot]])


class ReadTests(unittest.IsolatedAsyncioTestCase):
  async def test_counts_are_the_vendors_export(self):
    _, result = await read(FakeFTDI(), TEMPERATURE_37_DATA_REPLY)
    exported = {
      f"{'ABCDEFGH'[row]}{column + 1}": float(value)
      for row, values in enumerate(TEMPERATURE_37_EXPORT)
      for column, value in enumerate(values)
    }
    self.assertEqual(result.data, exported)
    self.assertEqual((result.excitation_wavelength, result.emission_wavelength), (485, 528))
    self.assertEqual(result.temperature, 37.0)

  async def test_saturated_wells_warn(self):
    with patch.object(fluorescence.logger, "warning") as warning:
      _, result = await read(FakeFTDI(), SATURATED_DATA_REPLY, gain=4000)
    self.assertEqual(set(result.data.values()), {260000.0})
    self.assertIn(96, warning.call_args.args)

  def test_another_modes_reply_is_refused(self):
    with self.assertRaises(ValueError):
      _decode_data_reply(ftdi_extract_payload(COLUMN_1_DATA_REPLY), 8)

  async def test_out_of_range_reads_send_nothing(self):
    io = FakeFTDI()
    feature = make_fluorescence(io, fluorescence_plate())
    refused: List[Dict[str, Any]] = [
      dict(excitation_wavelength=319),
      dict(emission_wavelength=841),
      dict(excitation_bandwidth=7),
      dict(emission_bandwidth=101),
      dict(excitation_wavelength=520, emission_wavelength=528),  # bands overlap
      dict(excitation_wavelength=330, emission_wavelength=345),  # split below 340 nm
      dict(gain=4096),
      dict(gain=1000.0),
      dict(focal_height=25.1),
      dict(focal_height=8.55),
      dict(measurement_settings=MeasurementSettings(pause_before_reading=5)),
      dict(timeout=0),
      dict(excitation_wavelength=None),  # neither a wavelength nor a filter
      dict(excitation_filter=EX_TR),  # both
      dict(excitation_wavelength=None, excitation_filter=EX_TR),  # no dichroic filter
      dict(excitation_wavelength=None, excitation_filter=OpticalFilter(5), dichroic_filter=LP_TR),
      dict(emission_wavelength=None, emission_filter=OpticalFilter(0), dichroic_filter=LP_TR),
      dict(dichroic_filter=DichroicFilter(4)),
      dict(dichroic_filter=LP_TR, dichroic_split=510.0),
      dict(dichroic_split=480.0),  # not between the wavelengths
      dict(dichroic_split=510.05),
    ]
    for arguments in refused:
      with self.subTest(arguments), self.assertRaises(ValueError):
        await feature.measure(**{**BASELINE, **arguments})
    self.assertEqual(io.written, [])


class SessionTests(unittest.IsolatedAsyncioTestCase):
  async def test_column_1_as_the_reader_read_it(self):
    """Column 1 of an empty plate, 485/15 to 528/20 nm, gain 1000, as the reader answered: the RUN
    it ran, and its 8-well reply."""
    session = load_session_replies()
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED]
    io.replies.append(bytes.fromhex(session["step_16_reply"]))
    plate = cor_96_wellplate_360uL_Fb("plate")
    result = await make_fluorescence(io, plate).measure(
      wells=plate.get_items("A1:H1"), excitation_wavelength=485, emission_wavelength=528, gain=1000
    )
    self.assertEqual(io.written[1], bytes.fromhex(session["step_16_run"]))
    self.assertEqual(list(result.data.values()), [68.0, 62.0, 72.0, 64.0, 67.0, 69.0, 67.0, 68.0])


class GatingTests(unittest.IsolatedAsyncioTestCase):
  def build_with(self, fluorescence_channels: int):
    driver = make_driver(FakeFTDI())
    driver.configuration = decoded()
    driver.configuration.fluorescence_channels = fluorescence_channels
    driver._discover_features()
    return driver

  def test_fluorescence_is_built_when_reported(self):
    self.assertIsInstance(self.build_with(1).fluorescence, Fluorescence)

  def test_no_fluorescence_is_not_built(self):
    self.assertIsNone(self.build_with(0).fluorescence)


if __name__ == "__main__":
  unittest.main()
