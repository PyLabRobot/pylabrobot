"""Fluorescence tests over a fake io: what the driver sends is pinned to the vendor's frames.

Except for the band edges, which this driver sends at their nominal values, and the scan byte's
bit 4, which it leaves clear in flying mode, enhanced dynamic range and a matrix.
"""

import functools
import gzip
import json
import os
import unittest
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from unittest.mock import patch

from pylabrobot.bmg_labtech.clariostar.driver.configuration_tests import decoded
from pylabrobot.bmg_labtech.clariostar.driver.features import fluorescence
from pylabrobot.bmg_labtech.clariostar.driver.features import measurement as measurement_module
from pylabrobot.bmg_labtech.clariostar.driver.features.absorbance_tests import (
  COLUMN_1_DATA_REPLY,
  DATA_FRAME,
  RUN_REPLY,
  STATUS_MEASURED,
  STATUS_MEASURING,
  STOP_FRAME,
  STOP_REPLY,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.drawer_tests import make_plate_holder
from pylabrobot.bmg_labtech.clariostar.driver.features.fluorescence import (
  CLARIOstarFluorescenceSpectrumResult,
  Fluorescence,
  _compute_band_edges,
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
from pylabrobot.lib.fluorescence.channels import FluorescenceChannel
from pylabrobot.lib.fluorescence.results import FluorescenceSpectrumResult
from pylabrobot.lib.optics.filters import DichroicFilter, OpticalFilter
from pylabrobot.lib.optics.holders import (
  DichroicFilterSlide,
  EmissionFilterSlide,
  ExcitationFilterSlide,
)
from pylabrobot.resources import Plate, Well, cor_96_wellplate_360uL_Fb, create_ordered_items_2d

# The vendor's default read: 485/15 to 528/20 nm, gain 1000, 8.5 mm, 10 flashes.
BASELINE_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a00010000141b0d"
)

# 540 to 590 nm.
WAVELENGTHS_540_590_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8156114cf1606176d16ab00040003000000000000000000000100"
  "000001000a0001000014940d"
)

# 620 to 680 nm.
WAVELENGTHS_620_680_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8188117ef19581af01a3000040003000000000000000000000100"
  "000001000a00010000153e0d"
)

# Emission 600 nm, 100 nm wide.
EMISSION_BANDWIDTH_100_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a9145c1961157f00040003000000000000000000000100"
  "000001000a0001000014610d"
)

# Gain 500.
GAIN_500_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c01f4133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a0001000014250d"
)

# Focal height 4.0 mm.
FOCAL_HEIGHT_4_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05019000000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a0001000014570d"
)

# Orbital averaging, 3 mm, 7 flashes.
ORBITAL_RUN_FRAME = bytes.fromhex(
  "0200a10c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a300000000000000000000000000000000000000000000000000000"
  "00000000270f270f0303027b0005035200000100000000000c03e8133b12a913bc1501143f0004000300000000000000"
  "000000010000000100070001000014d00d"
)

# Shaken orbital at 300 rpm for 5 s first.
SHAKE_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000020000000000020005000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a0001000014240d"
)

# Settling 0.5 s.
SETTLING_05_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f19035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a00010000142f0d"
)

# Row by row.
ROW_BY_ROW_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "000000000000000000000000000000000000000002000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a0001000014130d"
)

# The default read's reply at 37 C.
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

# The same read as the vendor's software reports it, row by row.
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

# Gain 4000: every well at the detector's ceiling
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

# Excitation, dichroic and emission through filters.
FILTERS_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e80002000100020001000200010001000000000000000000000100"
  "000001000a0001000011dd0d"
)

# Excitation 400 nm through its monochromator, dichroic and emission through filters.
EMISSION_FILTER_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e80fe70f5900020001000200010003000000000000000000000100"
  "000001000a00010000133a0d"
)

# Excitation and dichroic through filters, emission 528 nm through its monochromator.
EXCITATION_FILTER_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e80002000100021501143f00040001000000000000000000000100"
  "000001000a0001000012460d"
)

# A dichroic split at 510 nm.
DICHROIC_SPLIT_510_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913ec1501143f00040003000000000000000000000100"
  "000001000a00010000144b0d"
)


# The bottom optic: pre-separator byte 0 `40`
BOTTOM_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a400000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a00010000145b0d"
)

# Two fluorescence channels: 485/15 to 528/20 nm, then 610/30 to 675/50 nm
DUAL_RUN_FRAME = bytes.fromhex(
  "0200b00c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000200000000000c03e8133b12a913bc1501143f000400030000000c03e81868174018e7"
  "1b54196800040003000000000000000000000100000001000a0001000017f40d"
)

# Flying mode: scan byte `1e`, settling `01`, 1 flash
FLYING_MODE_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000001e000000000000000000000000000000000000000000000000000000"
  "00000000270f270f01035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "00000100010001000014220d"
)

# Enhanced dynamic range: scan byte `1a`, pre-separator byte 1 `40`
ENHANCED_DYNAMIC_RANGE_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000001a004000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a00010000146b0d"
)

# A 3 x 3 matrix of 3 mm on A1, 10 flashes: scan byte `1a`, pre-separator `10`,
# well-scan field `03 03 02 7b 00`
MATRIX_RUN_FRAME = bytes.fromhex(
  "0200a10c0431ec2166059604602c561d060c080080000000000000000000000000000000000000000000000000000000"
  "00000000000000000000000000000000000000001a100000000000000000000000000000000000000000000000000000"
  "00000000270f270f0303027b0005035200000100000000000c03e8133b12a913bc1501143f0004000300000000000000"
  "0000000100000001000a00010000094f0d"
)

# The matrix read's reply: A1's 9 points
MATRIX_DATA_REPLY = bytes.fromhex(
  "02004e0c020506260000a1000900090003f7a00100010001030009000000010100000000000000000091000000930000"
  "008e000000880000009000000090000000890000008e0000008b0007e90d"
)

# A 384-well plate at 5.0 mm
PLATE_384_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec216604bc03832d301de3181000ffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
  "ffffffffffffffffffffffffffffffffffffffff0a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f0501f400000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a0001000039aa0d"
)

# A1 alone
SINGLE_WELL_RUN_FRAME = bytes.fromhex(
  "02009c0c0431ec2166059604602c561d060c080080000000000000000000000000000000000000000000000000000000"
  "00000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035200000100000000000c03e8133b12a913bc1501143f00040003000000000000000000000100"
  "000001000a0001000008a70d"
)

# An excitation spectrum: 400 to 620 nm 10 nm wide, emission 800/20 nm, on A1, B1, C2, D2
EXCITATION_SPECTRUM_RUN_FRAME = bytes.fromhex(
  "0200b00c0431ec2166059604602c561d060c080080080040040000000000000000000000000000000000000000000000"
  "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
  "00000000270f270f0503520200dd00000000000c03e80fce0f7217571fa01ee0000300020000000c03e8186818081ba3"
  "1fa01ee0000300020000000000000000000001000000010005000100000f330d"
)

# An emission spectrum: 500 to 600 nm 10 nm wide, excitation 400/10 nm, on A1, B1, C2, D2
EMISSION_SPECTRUM_RUN_FRAME = bytes.fromhex(
  "0200b00c0431ec2166059604602c561d060c080080080040040000000000000000000000000000000000000000000000"
  "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
  "00000000270f270f05035202006500000000000c03e80fce0f72119413b81358000200030000000c03e80fce0f721388"
  "17a01740000200030000000000000000000001000000010005000100000e570d"
)

# A1 alone, the replies after the RUN in order: its data comes while the reader is still busy.
SINGLE_WELL_REPLIES = [
  bytes.fromhex(
    "0200350c0325040600000001770000000005010000000d00000001010000000000000001000000010001000000"
    "0100000700010d0d"
  ),
  bytes.fromhex("0200180c012504260000040100000400000000e000015f0d"),
  bytes.fromhex("0200180c012507260000040100000300000000e00001610d"),
  bytes.fromhex(
    "02002e0c022506260000a1000100010003f7a001000100010100010000000101000000000000000000910003640d"
  ),
  bytes.fromhex("0200180c010506260000000000000000000000e00001380d"),
]


def fluorescence_plate() -> Plate:
  """The fluorescence plate's geometry: 127.80 x 85.50 mm, A1 centre 14.30 / 11.20 mm, H12 centre
  113.50 / 74.30 mm, 6.35 mm wells."""
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


# The default read's fluorescence channel; the reads below vary it or the read-wide settings.
BASELINE = FluorescenceChannel(excitation_wavelength=485, emission_wavelength=528)
TEN_FLASHES = MeasurementSettings(flashes=10)
CAPTURED_FOCAL_HEIGHT = 8.5  # mm, the vendor's default, every capture's
CAPTURED_READS: Dict[str, Tuple[bytes, FluorescenceChannel, Dict[str, Any]]] = {
  "default": (BASELINE_RUN_FRAME, BASELINE, {}),
  "540_590_nm": (
    WAVELENGTHS_540_590_RUN_FRAME,
    replace(BASELINE, excitation_wavelength=540, emission_wavelength=590),
    {},
  ),
  "620_680_nm": (
    WAVELENGTHS_620_680_RUN_FRAME,
    replace(BASELINE, excitation_wavelength=620, emission_wavelength=680),
    {},
  ),
  "emission_100_nm_wide": (
    EMISSION_BANDWIDTH_100_RUN_FRAME,
    replace(BASELINE, emission_wavelength=600, emission_bandwidth=100),
    {},
  ),
  "gain_500": (GAIN_500_RUN_FRAME, BASELINE, dict(gain=500)),
  "focal_height_4_mm": (FOCAL_HEIGHT_4_RUN_FRAME, BASELINE, dict(focal_height=4.0)),
  "orbital": (
    ORBITAL_RUN_FRAME,
    BASELINE,
    dict(measurement_settings=MeasurementSettings(well_scan="orbital", scan_diameter=3, flashes=7)),
  ),
  "shaken": (SHAKE_RUN_FRAME, BASELINE, dict(shake_settings=ShakeSettings("orbital", 300, 5))),
  "settling_0_5_s": (
    SETTLING_05_RUN_FRAME,
    BASELINE,
    dict(measurement_settings=MeasurementSettings(settling_time=0.5, flashes=10)),
  ),
  "row_by_row": (
    ROW_BY_ROW_RUN_FRAME,
    BASELINE,
    dict(measurement_settings=MeasurementSettings(reading_direction="row_major", flashes=10)),
  ),
  "dichroic_split_wavelength_510_nm": (
    DICHROIC_SPLIT_510_RUN_FRAME,
    replace(BASELINE, dichroic_split_wavelength=510.0),
    {},
  ),
  "three_filters": (
    FILTERS_RUN_FRAME,
    FluorescenceChannel(excitation_filter=EX_TR, dichroic_filter=LP_TR, emission_filter=EM_615_18),
    {},
  ),
  "emission_filter": (
    EMISSION_FILTER_RUN_FRAME,
    FluorescenceChannel(
      excitation_wavelength=400, dichroic_filter=LP_TR, emission_filter=EM_615_18
    ),
    {},
  ),
  "excitation_filter": (
    EXCITATION_FILTER_RUN_FRAME,
    FluorescenceChannel(excitation_filter=EX_TR, dichroic_filter=LP_TR, emission_wavelength=528),
    {},
  ),
}


def with_nominal_edges(
  captured: bytes, fluorescence_channels: Sequence[FluorescenceChannel]
) -> bytes:
  """`captured`'s payload with each monochromator's band edges at their nominal values.

  The vendor software sends each band narrower (14.6 nm of 15, 19.4 nm of 20).
  """
  payload = bytearray(ftdi_extract_payload(captured))
  # Each fluorescence channel is gain, the excitation edges, the dichroic, the emission edges, the
  # wavelength selectors and `00 00 0c`; the last is followed by 19 bytes.
  first = len(payload) - 36 - 20 * (len(fluorescence_channels) - 1)
  for number, fluorescence_channel in enumerate(fluorescence_channels):
    start_of_fluorescence_channel = first + 20 * number
    for wavelength, bandwidth, offset in (
      (
        fluorescence_channel.excitation_wavelength,
        fluorescence_channel.excitation_bandwidth or 15,
        2,
      ),
      (fluorescence_channel.emission_wavelength, fluorescence_channel.emission_bandwidth or 20, 8),
    ):
      if wavelength is None:
        continue
      for index, value in enumerate(_compute_band_edges(wavelength, bandwidth)):
        start = start_of_fluorescence_channel + offset + 2 * index
        payload[start : start + 2] = value.to_bytes(2, "big")
  return bytes(payload)


def without_equidistant_cycles(payload: bytes) -> bytes:
  """`payload` with scan byte bit 4 clear: the vendor's equidistant kinetic cycles, which this
  driver does not send (flying mode `1e`, enhanced dynamic range and a matrix `1a`)."""
  patched = bytearray(payload)
  patched[64] &= ~0x10  # the family byte, the 63-byte plate block, then the scan byte
  return bytes(patched)


async def read(
  io: FakeFTDI, reply: bytes, fluorescence_channel: FluorescenceChannel = BASELINE, **arguments
):
  """A read of the whole fluorescence plate through `fluorescence_channel` answered by `reply`; the
  feature and the result."""
  io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED, reply]
  feature = make_fluorescence(io, fluorescence_plate())
  return feature, await feature.measure(
    fluorescence_channel, **{"gain": 1000, "focal_height": CAPTURED_FOCAL_HEIGHT, **arguments}
  )


# The driver's asyncio.Lock needs an event loop on Python 3.9: build drivers inside one.
class RunParametersTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_set_default_bandwidth_is_sent_and_checked(self):
    io = FakeFTDI()
    io.replies = [
      STATUS_IDLE,
      RUN_REPLY,
      STATUS_MEASURING,
      STATUS_MEASURED,
      TEMPERATURE_37_DATA_REPLY,
    ]
    feature = make_fluorescence(io, fluorescence_plate())
    with self.assertRaises(ValueError):  # below `bandwidth_range`
      feature.default_emission_bandwidth = 7
    feature.default_emission_bandwidth = 100
    await feature.measure(
      replace(BASELINE, emission_wavelength=600),
      measurement_settings=TEN_FLASHES,
      gain=1000,
      focal_height=CAPTURED_FOCAL_HEIGHT,
    )
    given = replace(BASELINE, emission_wavelength=600, emission_bandwidth=100)
    self.assertEqual(  # emission 600 nm, 100 nm wide
      ftdi_extract_payload(io.written[1]),
      with_nominal_edges(EMISSION_BANDWIDTH_100_RUN_FRAME, [given]),
    )

  async def test_captured_reads_are_sent_with_nominal_band_edges(self):
    for name, (captured, fluorescence_channel, arguments) in CAPTURED_READS.items():
      with self.subTest(name):
        io = FakeFTDI()
        _, result = await read(
          io,
          TEMPERATURE_37_DATA_REPLY,
          fluorescence_channel,
          **{"measurement_settings": TEN_FLASHES, **arguments},
        )
        self.assertEqual(
          ftdi_extract_payload(io.written[1]), with_nominal_edges(captured, [fluorescence_channel])
        )
        # The result says what the read used: a filter's passband, or the monochromator's band.
        excitation_filter, emission_filter = (
          fluorescence_channel.excitation_filter,
          fluorescence_channel.emission_filter,
        )
        self.assertEqual(
          result.excitation_bandwidth,
          excitation_filter.bandwidth
          if isinstance(excitation_filter, OpticalFilter)
          else fluorescence_channel.excitation_bandwidth or 15,
        )
        self.assertEqual(
          result.emission_bandwidth,
          emission_filter.bandwidth
          if isinstance(emission_filter, OpticalFilter)
          else fluorescence_channel.emission_bandwidth or 20,
        )
        self.assertEqual(result.gain, arguments.get("gain", 1000))


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
    with patch.object(measurement_module.logger, "warning") as warning:
      _, result = await read(FakeFTDI(), SATURATED_DATA_REPLY, gain=4000)
    self.assertEqual(set(result.data.values()), {260000.0})
    self.assertIn(96, warning.call_args.args)

  def test_a_reply_not_of_this_read_is_refused(self):
    reply = ftdi_extract_payload(TEMPERATURE_37_DATA_REPLY)
    for wrong, fluorescence_channels, wells in (
      (ftdi_extract_payload(COLUMN_1_DATA_REPLY), 1, 8),  # absorbance's layout
      (reply, 2, 96),
      (reply, 1, 95),
      (reply[:-4], 1, 96),  # Not a reader's reply: the last count cut off.
    ):
      with self.subTest(fluorescence_channels=fluorescence_channels, wells=wells):
        with self.assertRaises(ValueError):
          make_fluorescence(FakeFTDI())._decode_data_reply(wrong, fluorescence_channels, wells)

  async def test_out_of_range_reads_send_nothing(self):
    io = FakeFTDI()
    feature = make_fluorescence(io, fluorescence_plate())
    fractional_gain: Any = 1000.0
    # `_check_fluorescence_channel`'s refusals, then the read-wide settings'.
    refused: List[Tuple[FluorescenceChannel, Dict[str, Any]]] = [
      (replace(BASELINE, excitation_wavelength=None), {}),  # no wavelength, no filter
      (replace(BASELINE, emission_wavelength=528.0), {}),
      (replace(BASELINE, emission_bandwidth=20.5), {}),
      (replace(BASELINE, dichroic_split_wavelength=510.05), {}),
      (BASELINE, dict(gain=fractional_gain)),
      (replace(BASELINE, excitation_wavelength=319), {}),
      (replace(BASELINE, emission_wavelength=841), {}),
      (replace(BASELINE, excitation_bandwidth=7), {}),
      (replace(BASELINE, emission_bandwidth=101), {}),
      (replace(BASELINE, excitation_wavelength=520), {}),  # the bands overlap
      (replace(BASELINE, excitation_wavelength=320, emission_wavelength=345), {}),
      (BASELINE, dict(gain=4096)),
      (  # a filter without a dichroic filter
        replace(BASELINE, excitation_wavelength=None, excitation_filter=EX_TR),
        {},
      ),
      (
        FluorescenceChannel(
          excitation_filter=OpticalFilter(5), dichroic_filter=LP_TR, emission_wavelength=528
        ),
        {},
      ),
      (
        FluorescenceChannel(
          excitation_wavelength=485, dichroic_filter=LP_TR, emission_filter=OpticalFilter(0)
        ),
        {},
      ),
      (replace(BASELINE, dichroic_filter=DichroicFilter(4)), {}),
      (replace(BASELINE, dichroic_split_wavelength=480.0), {}),  # not between the wavelengths
      (BASELINE, dict(focal_height=25.1)),
      (BASELINE, dict(focal_height=8.55)),
      (BASELINE, dict(measurement_settings=MeasurementSettings(pause_before_reading=5))),
      (BASELINE, dict(timeout=0)),
    ]
    for fluorescence_channel, arguments in refused:
      with (
        self.subTest(fluorescence_channel=fluorescence_channel, arguments=arguments),
        self.assertRaises(ValueError),
      ):
        await feature.measure(
          fluorescence_channel, **{"gain": 1000, "focal_height": CAPTURED_FOCAL_HEIGHT, **arguments}
        )
    with self.assertRaises(ValueError) as raised:
      await feature.measure(replace(BASELINE, emission_wavelength=528.0), gain=fractional_gain)
    self.assertIn("emission_wavelength", str(raised.exception))
    self.assertIn("gain", str(raised.exception))
    self.assertEqual(io.written, [])


class ExcitationAboveEmissionTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_read_is_sent_with_the_split_between_the_bands_and_warns(self):
    """Not captured: excitation 600/15 nm above emission 500/20 nm; the split lies midway between
    the bands' facing edges, 510.0 and 592.5 nm: 551.2 nm."""
    io = FakeFTDI()
    with patch.object(measurement_module.logger, "warning") as warning:
      await read(
        io,
        TEMPERATURE_37_DATA_REPLY,
        replace(BASELINE, excitation_wavelength=600, emission_wavelength=500),
      )
    payload = ftdi_extract_payload(io.written[1])
    fluorescence_channel = len(payload) - 36  # the last fluorescence channel starts with its gain
    fields = [
      int.from_bytes(payload[fluorescence_channel + i : fluorescence_channel + i + 2], "big")
      for i in (2, 4, 6, 8, 10)
    ]
    self.assertEqual(fields, [6075, 5925, 5512, 5100, 4900])
    self.assertIn("excitation above emission", warning.call_args.args[1])


class SessionTests(unittest.IsolatedAsyncioTestCase):
  async def test_column_1_as_the_reader_read_it(self):
    """Column 1 of an empty plate, 485/15 to 528/20 nm, gain 1000, as the reader answered: the RUN
    it ran, and its 8-well reply."""
    session = load_session_replies()
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED]
    io.replies.append(bytes.fromhex(session["fluorescence_column_1_reply"]))
    plate = cor_96_wellplate_360uL_Fb("plate")
    result = await make_fluorescence(io, plate).measure(
      BASELINE, wells=plate.get_items("A1:H1"), gain=1000, focal_height=CAPTURED_FOCAL_HEIGHT
    )
    self.assertEqual(io.written[1], bytes.fromhex(session["fluorescence_column_1_run"]))
    self.assertEqual(list(result.data.values()), [68.0, 62.0, 72.0, 64.0, 67.0, 69.0, 67.0, 68.0])


class DefaultFocalHeightTests(unittest.IsolatedAsyncioTestCase):
  async def read_column_1(self, plate: Plate, percent: Optional[float], **arguments) -> bytes:
    session = load_session_replies()
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED]
    io.replies.append(bytes.fromhex(session["fluorescence_column_1_reply"]))
    feature = make_fluorescence(io, plate)
    if percent is not None:
      feature.default_focal_height_percent_of_well_depth = percent
    await feature.measure(BASELINE, wells=plate.get_items("A1:H1"), gain=1000, **arguments)
    return io.written[1]

  async def test_none_reads_at_the_percent_of_the_well_depth_above_its_floor(self):
    # Corning 96 360 uL: the well at 3.03 mm, its floor 0.50 mm, its depth 10.67 mm.
    for percent, focal_height in ((None, 6.2), (50, 8.9), (0, 3.5), (100, 14.2)):
      with self.subTest(percent=percent):
        plate = cor_96_wellplate_360uL_Fb("plate")
        self.assertEqual(
          await self.read_column_1(plate, percent),
          await self.read_column_1(
            cor_96_wellplate_360uL_Fb("plate"), None, focal_height=focal_height
          ),
        )

  async def test_the_percent_is_checked_when_set(self):
    feature = make_fluorescence(FakeFTDI(), cor_96_wellplate_360uL_Fb("plate"))
    self.assertEqual(feature.default_focal_height_percent_of_well_depth, 25)
    for refused in (-1, 100.5, "25", True):
      with self.subTest(refused=refused), self.assertRaises(ValueError):
        feature.default_focal_height_percent_of_well_depth = refused  # type: ignore[assignment]
    self.assertEqual(feature.default_focal_height_percent_of_well_depth, 25)

  async def test_a_well_without_a_floor_thickness_sends_nothing(self):
    io = FakeFTDI()
    feature = make_fluorescence(io, fluorescence_plate())
    with self.assertRaisesRegex(ValueError, "floor thickness; give focal_height"):
      await feature.measure(BASELINE, gain=1000)
    self.assertEqual(io.written, [])


@functools.lru_cache(maxsize=None)
def load_captures() -> Dict[str, Any]:
  """Larger frames, as hex: replies with the same read as the vendor's software reports it.

  Row by row, one grid per fluorescence channel; each spectrum's replies in order.
  """
  path = os.path.join(os.path.dirname(__file__), "test_data", "fluorescence_captures.json.gz")
  with gzip.open(path, "rt") as f:
    return dict(json.load(f))


def keyed_by_well(export: List[List[int]]) -> Dict[str, float]:
  """An export grid, row by row, keyed by well identifier."""
  return {
    f"{'ABCDEFGHIJKLMNOP'[row]}{column + 1}": float(value)
    for row, values in enumerate(export)
    for column, value in enumerate(values)
  }


async def read_capture(
  reply: bytes,
  fluorescence_channels: Union[FluorescenceChannel, Sequence[FluorescenceChannel]] = BASELINE,
  plate: Optional[Plate] = None,
  bottom_measurement_available=None,
  **arguments,
):
  """A read through `fluorescence_channels` answered by `reply`; what was written and the result."""
  io = FakeFTDI(chunk=4096)
  io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED, reply]
  feature = make_fluorescence(io, fluorescence_plate() if plate is None else plate)
  feature._driver.configuration.bottom_measurement_available = bottom_measurement_available
  return io, await feature.measure(
    fluorescence_channels, **{"gain": 1000, "focal_height": CAPTURED_FOCAL_HEIGHT, **arguments}
  )


class BottomOpticTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_bottom_read_is_the_vendors(self):
    capture = load_captures()["bottom_optic"]
    io, result = await read_capture(
      bytes.fromhex(capture["reply"]),
      bottom_measurement_available=True,
      optic_position="bottom",
      measurement_settings=TEN_FLASHES,
    )
    self.assertEqual(
      ftdi_extract_payload(io.written[1]), with_nominal_edges(BOTTOM_RUN_FRAME, [BASELINE])
    )
    self.assertEqual(result.data, keyed_by_well(capture["export"]))

  async def test_the_default_optic_position_is_read_with_and_checked(self):
    capture = load_captures()["bottom_optic"]
    io = FakeFTDI(chunk=4096)
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED]
    io.replies.append(bytes.fromhex(capture["reply"]))
    feature = make_fluorescence(io, fluorescence_plate())
    feature._driver.configuration.bottom_measurement_available = True
    self.assertEqual(feature.default_optic_position, "top")
    feature.default_optic_position = "bottom"
    await feature.measure(
      BASELINE, measurement_settings=TEN_FLASHES, gain=1000, focal_height=CAPTURED_FOCAL_HEIGHT
    )
    self.assertEqual(
      ftdi_extract_payload(io.written[1]), with_nominal_edges(BOTTOM_RUN_FRAME, [BASELINE])
    )
    feature._driver.configuration.bottom_measurement_available = False
    for refused in ("side", "bottom"):
      with self.subTest(refused), self.assertRaises(ValueError):
        feature.default_optic_position = refused  # type: ignore[assignment]

  async def test_no_bottom_optic_or_an_unknown_one_sends_nothing(self):
    for available, position in ((False, "bottom"), (True, "side")):
      io = FakeFTDI()
      feature = make_fluorescence(io, fluorescence_plate())
      feature._driver.configuration.bottom_measurement_available = available
      with self.subTest(position), self.assertRaises(ValueError):
        await feature.measure(  # type: ignore[call-overload]
          BASELINE, optic_position=position, gain=1000, focal_height=CAPTURED_FOCAL_HEIGHT
        )
      self.assertEqual(io.written, [])


DUAL_FLUORESCENCE_CHANNELS = [
  BASELINE,
  FluorescenceChannel(
    excitation_wavelength=610,
    excitation_bandwidth=30,
    emission_wavelength=675,
    emission_bandwidth=50,
  ),
]


class FluorescenceChannelsTests(unittest.IsolatedAsyncioTestCase):
  async def test_two_fluorescence_channels_are_the_vendors_read(self):
    capture = load_captures()["two_fluorescence_channels"]
    io, results = await read_capture(
      bytes.fromhex(capture["reply"]), DUAL_FLUORESCENCE_CHANNELS, measurement_settings=TEN_FLASHES
    )
    expected = with_nominal_edges(DUAL_RUN_FRAME, DUAL_FLUORESCENCE_CHANNELS)
    self.assertEqual(ftdi_extract_payload(io.written[1]), expected)
    self.assertEqual(
      [result.data for result in results], [keyed_by_well(grid) for grid in capture["export"]]
    )
    self.assertEqual([result.excitation_wavelength for result in results], [485, 610])
    self.assertEqual([result.emission_bandwidth for result in results], [20, 50])

  async def test_each_fluorescence_channel_is_sent_with_its_own_gain(self):
    capture = load_captures()["two_fluorescence_channels"]
    io, results = await read_capture(
      bytes.fromhex(capture["reply"]),
      DUAL_FLUORESCENCE_CHANNELS,
      gain=[1000, 1500],
      measurement_settings=TEN_FLASHES,
    )
    # Not a reader's frame: the two fluorescence channels' read with the second one's gain 1500,
    # at its start.
    expected = bytearray(with_nominal_edges(DUAL_RUN_FRAME, DUAL_FLUORESCENCE_CHANNELS))
    second = len(expected) - 36
    expected[second : second + 2] = (1500).to_bytes(2, "big")
    self.assertEqual(ftdi_extract_payload(io.written[1]), bytes(expected))
    self.assertEqual([result.gain for result in results], [1000, 1500])

  async def test_out_of_range_fluorescence_channels_send_nothing(self):
    io = FakeFTDI()
    feature = make_fluorescence(io, fluorescence_plate())
    refused: List[Tuple[List[FluorescenceChannel], Any]] = [
      ([], 1000),
      ([BASELINE] * 6, 1000),
      ([BASELINE, replace(BASELINE, emission_wavelength=841)], 1000),
      (DUAL_FLUORESCENCE_CHANNELS, [1000]),  # not one gain per fluorescence channel
      (DUAL_FLUORESCENCE_CHANNELS, [1000, 4096]),
    ]
    for fluorescence_channels, gain in refused:
      with (
        self.subTest(fluorescence_channels=fluorescence_channels, gain=gain),
        self.assertRaises(ValueError),
      ):
        await feature.measure(fluorescence_channels, gain=gain)
    self.assertEqual(io.written, [])


class FlyingModeTests(unittest.IsolatedAsyncioTestCase):
  async def test_flying_mode_is_the_vendors_read(self):
    capture = load_captures()["flying_mode"]
    io, result = await read_capture(
      bytes.fromhex(capture["reply"]),
      flying_mode=True,
      measurement_settings=MeasurementSettings(settling_time=0, flashes=1),
    )
    expected = with_nominal_edges(FLYING_MODE_RUN_FRAME, [BASELINE])
    self.assertEqual(ftdi_extract_payload(io.written[1]), without_equidistant_cycles(expected))
    self.assertEqual(result.data, keyed_by_well(capture["export"]))

  async def test_what_flying_mode_cannot_read_sends_nothing(self):
    io = FakeFTDI()
    feature = make_fluorescence(io, fluorescence_plate())
    for settings, enhanced in (
      (MeasurementSettings(settling_time=0, flashes=1, well_scan="orbital"), False),
      (MeasurementSettings(settling_time=0, flashes=5), False),
      (MeasurementSettings(settling_time=0.1, flashes=1), False),
      (MeasurementSettings(settling_time=0, flashes=1), True),
    ):
      with self.subTest(settings=settings, enhanced=enhanced), self.assertRaises(ValueError):
        await feature.measure(
          BASELINE,
          flying_mode=True,
          enhanced_dynamic_range=enhanced,
          measurement_settings=settings,
          gain=1000,
          focal_height=CAPTURED_FOCAL_HEIGHT,
        )
    self.assertEqual(io.written, [])


class EnhancedDynamicRangeTests(unittest.IsolatedAsyncioTestCase):
  async def test_enhanced_dynamic_range_is_the_vendors_read(self):
    capture = load_captures()["enhanced_dynamic_range"]
    reply = bytes.fromhex(capture["reply"])
    with patch.object(measurement_module.logger, "warning") as warning:
      io, result = await read_capture(
        reply, enhanced_dynamic_range=True, measurement_settings=TEN_FLASHES
      )
    expected = with_nominal_edges(ENHANCED_DYNAMIC_RANGE_RUN_FRAME, [BASELINE])
    self.assertEqual(ftdi_extract_payload(io.written[1]), without_equidistant_cycles(expected))
    self.assertEqual(result.data, keyed_by_well(capture["export"]))
    decode = make_fluorescence(FakeFTDI())._decode_data_reply
    _, ceiling, _ = decode(ftdi_extract_payload(reply), 1, 96)
    self.assertEqual(ceiling, 700_000_000)
    warning.assert_not_called()  # 127,000 counts are far below its ceiling

  async def test_with_orbital_or_spiral_averaging_sends_nothing(self):
    io = FakeFTDI()
    feature = make_fluorescence(io, fluorescence_plate())
    for well_scan in ("orbital", "spiral"):
      with self.subTest(well_scan), self.assertRaises(ValueError):
        await feature.measure(
          BASELINE,
          enhanced_dynamic_range=True,
          measurement_settings=MeasurementSettings(well_scan=well_scan),  # type: ignore[arg-type]
          gain=1000,
          focal_height=CAPTURED_FOCAL_HEIGHT,
        )
    self.assertEqual(io.written, [])


class MatrixTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_matrix_returns_every_point(self):
    plate = fluorescence_plate()
    io, result = await read_capture(
      MATRIX_DATA_REPLY,
      plate=plate,
      wells=[plate.get_well("A1")],
      measurement_settings=MeasurementSettings(
        well_scan="matrix", matrix_size=3, scan_diameter=3, flashes=10
      ),
    )
    expected = with_nominal_edges(MATRIX_RUN_FRAME, [BASELINE])
    self.assertEqual(ftdi_extract_payload(io.written[1]), without_equidistant_cycles(expected))
    points = [145.0, 147.0, 142.0, 136.0, 144.0, 144.0, 137.0, 142.0, 139.0]
    self.assertEqual(result.matrix_points, {"A1": points})
    # The plain mean: the driver's choice, not the vendor's (its export reads 141).
    self.assertEqual(result.data, {"A1": sum(points) / 9})

  async def test_a_matrix_of_several_wells_sends_nothing(self):
    io = FakeFTDI()
    plate = fluorescence_plate()
    feature = make_fluorescence(io, plate)
    settings = MeasurementSettings(well_scan="matrix", matrix_size=3)
    with self.assertRaises(ValueError):  # no fluorescence matrix of several wells is captured
      await feature.measure(
        BASELINE,
        wells=plate.get_items("A1:B1"),
        measurement_settings=settings,
        gain=1000,
        focal_height=CAPTURED_FOCAL_HEIGHT,
      )
    self.assertEqual(io.written, [])


def plate_384() -> Plate:
  """The 384-well plate's geometry: 127.80 x 85.50 mm, A1 centre 12.12 / 8.99 mm from the back,
  P24 centre 115.68 / 76.51 mm; its pitch is 103.56 / 23 and 67.52 / 15 mm, not 4.5."""
  return Plate(
    "plate_384",
    size_x=127.80,
    size_y=85.50,
    size_z=14.0,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=24,
      num_items_y=16,
      dx=12.12 - 1.8,
      dy=8.99 - 1.8,
      dz=1.0,
      item_dx=103.56 / 23,
      item_dy=67.52 / 15,
      size_x=3.6,
      size_y=3.6,
      size_z=10.0,
    ),
  )


class PlateTests(unittest.IsolatedAsyncioTestCase):
  async def test_384_wells_are_the_vendors_read(self):
    capture = load_captures()["plate_384"]
    io, result = await read_capture(
      bytes.fromhex(capture["reply"]),
      plate=plate_384(),
      focal_height=5.0,
      measurement_settings=TEN_FLASHES,
    )
    self.assertEqual(
      ftdi_extract_payload(io.written[1]), with_nominal_edges(PLATE_384_RUN_FRAME, [BASELINE])
    )
    self.assertEqual(result.data, keyed_by_well(capture["export"]))

  async def test_a_single_well_as_the_reader_answered(self):
    io = FakeFTDI(chunk=4096)
    io.replies = [STATUS_IDLE, *SINGLE_WELL_REPLIES]
    plate = fluorescence_plate()
    result = await make_fluorescence(io, plate).measure(
      BASELINE,
      wells=[plate.get_well("A1")],
      measurement_settings=TEN_FLASHES,
      gain=1000,
      focal_height=CAPTURED_FOCAL_HEIGHT,
    )
    expected = with_nominal_edges(SINGLE_WELL_RUN_FRAME, [BASELINE])
    self.assertEqual(ftdi_extract_payload(io.written[1]), expected)
    self.assertEqual(result.data, {"A1": 145.0})


# The spectra's fixed excitation or emission and their scans, as the captures read them.
SPECTRUM_WELLS = ["A1", "B1", "C2", "D2"]
EXCITATION_SPECTRUM: Dict[str, Any] = dict(
  scan="excitation",
  start_wavelength=400,
  end_wavelength=620,
  excitation_bandwidth=10,
  emission_wavelength=800,
  emission_bandwidth=20,
)
EMISSION_SPECTRUM: Dict[str, Any] = dict(
  scan="emission",
  start_wavelength=500,
  end_wavelength=600,
  excitation_wavelength=400,
  excitation_bandwidth=10,
  emission_bandwidth=10,
)


def spectrum_replies(name: str) -> List[bytes]:
  """The reader's replies to capture `name`'s RUN, status polls and data requests, in order: each
  page after a status with data ready, the last status idle."""
  return [bytes.fromhex(frame) for frame in load_captures()[name]["replies"]]


def spectrum_pages(name: str) -> List[bytes]:
  """The data pages of capture `name`, in the order received."""
  replies = [ftdi_extract_payload(reply) for reply in spectrum_replies(name)]
  return [reply for reply in replies if reply[0] == 0x02]


def counts_at(result: FluorescenceSpectrumResult, wavelength: int) -> Dict[str, float]:
  """Each well's count at `wavelength`, in the result's well order."""
  return {well: spectrum[wavelength] for well, spectrum in result.data.items()}


def count_data_requests(io: FakeFTDI) -> int:
  """The data requests the driver sent."""
  return sum(frame == DATA_FRAME for frame in io.written)


class SpectrumTests(unittest.IsolatedAsyncioTestCase):
  async def measure(self, replies: List[bytes], arguments: Dict[str, Any]):
    """A spectrum of `SPECTRUM_WELLS` with `arguments` answered by `replies`; the io and the
    result."""
    io = FakeFTDI(chunk=4096)
    io.replies = list(replies)
    plate = fluorescence_plate()
    feature = make_fluorescence(io, plate)
    result = await feature.measure_spectrum(
      wells=[plate.get_well(well) for well in SPECTRUM_WELLS],
      **{"gain": 1000, "focal_height": CAPTURED_FOCAL_HEIGHT, **arguments},
    )
    return io, result

  async def test_an_excitation_spectrum_is_the_vendors_read(self):
    io, result = await self.measure(spectrum_replies("excitation_spectrum"), EXCITATION_SPECTRUM)
    fluorescence_channels = [
      FluorescenceChannel(
        excitation_wavelength=wavelength,
        excitation_bandwidth=10,
        emission_wavelength=800,
        emission_bandwidth=20,
      )
      for wavelength in (400, 620)
    ]
    expected = with_nominal_edges(EXCITATION_SPECTRUM_RUN_FRAME, fluorescence_channels)
    self.assertEqual(ftdi_extract_payload(io.written[1]), expected)
    self.assertEqual(count_data_requests(io), 4)  # the page repeated after the last is not
    self.assertEqual(list(result.data), SPECTRUM_WELLS)
    self.assertEqual(list(result.data["A1"]), list(range(400, 621)))
    self.assertEqual((result.excitation_wavelength, result.excitation_bandwidth), (None, 10))
    self.assertEqual((result.emission_wavelength, result.emission_bandwidth), (800, 20))
    self.assertEqual(result.gain, 1000)
    # The raw counts: the vendor's export is spectrally corrected.
    self.assertEqual(counts_at(result, 400), {"A1": 41.0, "B1": 31.0, "C2": 22.0, "D2": 25.0})
    self.assertEqual(list(counts_at(result, 620).values()), [46.0, 14.0, 17.0, 22.0])

  async def test_an_emission_spectrum_is_the_vendors_read(self):
    io, result = await self.measure(spectrum_replies("emission_spectrum"), EMISSION_SPECTRUM)
    fluorescence_channels = [
      FluorescenceChannel(
        excitation_wavelength=400,
        excitation_bandwidth=10,
        emission_wavelength=wavelength,
        emission_bandwidth=10,
      )
      for wavelength in (500, 600)
    ]
    expected = with_nominal_edges(EMISSION_SPECTRUM_RUN_FRAME, fluorescence_channels)
    self.assertEqual(ftdi_extract_payload(io.written[1]), expected)
    self.assertEqual(count_data_requests(io), 4)
    self.assertEqual(list(result.data["A1"]), list(range(500, 601)))
    self.assertEqual((result.excitation_wavelength, result.emission_wavelength), (400, None))
    assert isinstance(result, CLARIOstarFluorescenceSpectrumResult)
    self.assertEqual(result.gain, 1000)
    self.assertEqual(counts_at(result, 500), {"A1": 73.0, "B1": 49.0, "C2": 32.0, "D2": 30.0})
    self.assertEqual(list(counts_at(result, 501).values()), [70.0, 40.0, 29.0, 24.0])
    self.assertEqual(list(counts_at(result, 600).values()), [20.0, 31.0, 71.0, 62.0])

  def test_each_page_is_its_wells_whatever_its_place(self):
    plate = fluorescence_plate()
    pages = spectrum_pages("emission_spectrum")
    decode = make_fluorescence(FakeFTDI())._decode_spectrum_pages
    in_order = decode(pages, plate, SPECTRUM_WELLS, 101)
    # Not a reader's reply: the pages reversed, one missing, one repeated.
    self.assertEqual(decode(pages[::-1], plate, SPECTRUM_WELLS, 101), in_order)
    for wrong in (pages[:3], [*pages, pages[3]]):
      with self.assertRaises(ValueError):
        decode(wrong, plate, SPECTRUM_WELLS, 101)

  def test_a_page_not_of_this_read_is_refused(self):
    plate = fluorescence_plate()
    pages = spectrum_pages("emission_spectrum")
    off_plate = bytearray(pages[0])
    off_plate[23:25] = (13).to_bytes(2, "big")  # Not a reader's reply: column 13 of 12.
    for wrong, wavelengths in (
      (pages, 100),
      (pages[:-1] + [pages[-1][:-4]], 101),  # Not a reader's reply: the last count cut off.
      ([bytes(off_plate), *pages[1:]], 101),
    ):
      with self.subTest(wavelengths=wavelengths), self.assertRaises(ValueError):
        make_fluorescence(FakeFTDI())._decode_spectrum_pages(
          wrong, plate, SPECTRUM_WELLS, wavelengths
        )

  async def test_a_short_page_stops_the_run_and_the_plate_is_read_again(self):
    replies = spectrum_replies("emission_spectrum")
    first_page = next(index for index, reply in enumerate(replies) if reply[4] == 0x02)
    # Not a reader's reply: the first page cut short, as an absorbance page arrived
    # (ShortPageTests); no short fluorescence page is captured.
    short = replies[:first_page] + [replies[first_page][:200], STOP_REPLY]
    io, result = await self.measure(short + replies, EMISSION_SPECTRUM)
    runs = [frame for frame in io.written if ftdi_extract_payload(frame)[0] == 0x04]
    self.assertEqual(len(runs), 2)
    self.assertIn(STOP_FRAME, io.written)
    self.assertEqual(count_data_requests(io), 5)
    self.assertEqual(list(counts_at(result, 500).values()), [73.0, 49.0, 32.0, 30.0])

  async def test_a_run_ending_before_its_last_page_stops(self):
    replies = spectrum_replies("emission_spectrum")
    # Not a reader's reply: the run ending idle after three of its four pages.
    ended = replies[:13] + [replies[-1], STOP_REPLY]
    with self.assertRaisesRegex(RuntimeError, "3 of 4 pages"):
      await self.measure(ended, EMISSION_SPECTRUM)

  async def test_out_of_range_spectra_send_nothing(self):
    io = FakeFTDI()
    feature = make_fluorescence(io, fluorescence_plate())
    refused: List[Dict[str, Any]] = [
      dict(EMISSION_SPECTRUM, scan="both"),
      dict(EMISSION_SPECTRUM, step_size=2),
      dict(EMISSION_SPECTRUM, end_wavelength=500),
      dict(EMISSION_SPECTRUM, end_wavelength=841),
      dict(EMISSION_SPECTRUM, start_wavelength=319),
      dict(EMISSION_SPECTRUM, emission_wavelength=550),  # the scanned emission's
      dict(EMISSION_SPECTRUM, excitation_wavelength=None),  # no fixed excitation
      dict(EXCITATION_SPECTRUM, end_wavelength=800),  # overlap
      dict(EMISSION_SPECTRUM, gain=4096),
      dict(EMISSION_SPECTRUM, focal_height=25.1),  # `_check_read`
      dict(
        EMISSION_SPECTRUM,
        measurement_settings=MeasurementSettings(well_scan="matrix", matrix_size=3),
      ),
      dict(
        EMISSION_SPECTRUM, measurement_settings=MeasurementSettings(reading_direction="row_major")
      ),
      dict(EMISSION_SPECTRUM, measurement_settings=MeasurementSettings(corner="top_right")),
      dict(EMISSION_SPECTRUM, measurement_settings=MeasurementSettings(bidirectional=False)),
    ]
    for arguments in refused:
      with self.subTest(arguments=arguments), self.assertRaises(ValueError):
        await feature.measure_spectrum(
          **{"gain": 1000, "focal_height": CAPTURED_FOCAL_HEIGHT, **arguments}
        )
    self.assertEqual(io.written, [])


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

  def test_the_wavelength_limits_follow_the_photomultipliers(self):
    # (extended red, dual, crossed over): the highest wavelength and dichroic split.
    for photomultipliers, highest in (
      ((True, True, False), (840, 760.0)),  # the captured reader
      ((True, True, True), (740, 740.0)),
      ((True, False, False), (840, 760.0)),
      ((False, False, False), (740, 740.0)),
    ):
      with self.subTest(photomultipliers=photomultipliers):
        driver = make_driver(FakeFTDI())
        driver.configuration = decoded()
        driver.configuration.fluorescence_channels = 1
        (
          driver.configuration.extended_red_photomultiplier_tube,
          driver.configuration.dual_photomultiplier_tubes,
          driver.configuration.photomultiplier_tube_crossover,
        ) = photomultipliers
        driver._discover_features()
        assert driver.fluorescence is not None
        configuration = driver.fluorescence.configuration
        self.assertEqual(
          (configuration.wavelength_range[1], configuration.dichroic_range[1]), highest
        )


def load_filter_detection() -> List[Dict[str, Any]]:
  """The reader's filter detection, its 11 positions in order.

  Each holds its `24` frame and the replies the driver reads, as hex: the status before the `24`,
  the reply to it, to `80` until data ready, to `05 11`, to `05 1b`, then the next status.
  """
  path = os.path.join(os.path.dirname(__file__), "test_data", "filter_detection_captures.json.gz")
  with gzip.open(path, "rt") as f:
    return list(json.load(f)["positions"])


# The detection's positions, in its order (excitation 1-4, dichroic A-C, emission 5-8).
DETECTION_POSITIONS = [
  *(("excitation", slot) for slot in range(1, 5)),
  *(("dichroic", slot) for slot in range(1, 4)),
  *(("emission", slot) for slot in range(1, 5)),
]


def detection_result(slide: str, slot: int) -> bytes:
  """The payload of the FILTER_RESULT the reader sent for a position, the reply to `05 1b`."""
  position = load_filter_detection()[DETECTION_POSITIONS.index((slide, slot))]
  return ftdi_extract_payload(bytes.fromhex(position["replies"][-2]))


def decode_filter_result(reply: bytes, slide: str) -> Tuple[int, int, int, int, int]:
  """`Fluorescence._decode_filter_result` of a feature without a plate."""
  return make_fluorescence(FakeFTDI())._decode_filter_result(reply, slide)


def make_detecting_fluorescence(io: FakeFTDI) -> Fluorescence:
  """A fluorescence feature of the captured reader, answered by the whole detection capture."""
  io.replies = [
    bytes.fromhex(reply) for position in load_filter_detection() for reply in position["replies"]
  ]
  return make_fluorescence(io)


class FilterDetectionTests(unittest.IsolatedAsyncioTestCase):
  async def test_detection_as_the_reader_answered(self):
    """The reader's filters: excitation 1, dichroic A and emission 5, every other position empty."""
    io = FakeFTDI(chunk=4096)
    feature = make_detecting_fluorescence(io)
    feature.excitation_filter_slide = ExcitationFilterSlide(4)  # a slide the detection replaces
    feature.excitation_filter_slide.register(OpticalFilter(3, "old"))
    excitation, dichroic, emission = await feature.detect_all_filters()
    self.assertIs(feature.excitation_filter_slide, excitation)
    self.assertIs(feature.dichroic_filter_slide, dichroic)
    self.assertIs(feature.emission_filter_slide, emission)
    self.assertEqual(excitation[1], OpticalFilter(1, passbands=((282.2, 371.8),)))
    self.assertEqual(dichroic[1], DichroicFilter(1, transmission_bands=((421.8, None),)))
    self.assertEqual(emission[1], OpticalFilter(1, passbands=((604.9, 623.2),)))
    self.assertEqual(repr(excitation), "ExcitationFilterSlide(4 slots, {1: (unnamed)})")
    self.assertEqual(repr(dichroic), "DichroicFilterSlide(3 slots, {1: (unnamed)})")
    self.assertEqual(repr(emission), "EmissionFilterSlide(4 slots, {1: (unnamed)})")
    # Each position: status, `24`, status until data ready, `05 11` and `05 1b` without a status
    # before them (the reader still busy), status until idle.
    expected: List[bytes] = []
    for position in load_filter_detection():
      polls = len(position["replies"]) - 5
      expected += [b"\x80", ftdi_extract_payload(bytes.fromhex(position["request"]))]
      expected += [b"\x80"] * polls + [bytes.fromhex("05110000000000")]
      expected += [bytes.fromhex("051b0000000000"), b"\x80"]
    self.assertEqual([ftdi_extract_payload(frame) for frame in io.written], expected)
    self.assertEqual(io.replies, [])

  def test_long_pass_and_short_pass_filters_are_decoded(self):
    """No long-pass or short-pass filter is captured: the replies are real results edited."""
    # Not a reader's reply: excitation 1's without its bandwidth, then also without its first edge.
    long_pass = bytearray(detection_result("excitation", 1))
    long_pass[9:11] = bytes(2)
    result = decode_filter_result(bytes(long_pass), "excitation")
    self.assertEqual(
      Fluorescence._compute_optical_filter(1, result), OpticalFilter(1, passbands=((282.2, None),))
    )
    long_pass[11:13] = bytes(2)
    result = decode_filter_result(bytes(long_pass), "excitation")
    with patch.object(fluorescence.logger, "warning") as warning:
      self.assertEqual(Fluorescence._compute_optical_filter(1, result), OpticalFilter(1))
    warning.assert_called_once()  # a short-pass, its band not stated
    # Not a reader's reply: dichroic A's with its edge moved to the second.
    short_pass = bytearray(detection_result("dichroic", 1))
    short_pass[13:15], short_pass[11:13] = short_pass[11:13], bytes(2)
    result = decode_filter_result(bytes(short_pass), "dichroic")
    with patch.object(fluorescence.logger, "warning") as warning:
      self.assertEqual(Fluorescence._compute_dichroic_filter(1, result), DichroicFilter(1))
    warning.assert_called_once()

  def test_a_reply_not_of_its_position_is_refused(self):
    reply = detection_result("excitation", 1)
    for wrong, slide in (
      (reply, "emission"),
      (reply[:-9], "excitation"),  # Not a reader's reply: cut short.
    ):
      with self.subTest(slide=slide, length=len(wrong)), self.assertRaises(ValueError):
        decode_filter_result(wrong, slide)

  async def test_a_scan_ending_without_data_stops(self):
    """Not a reader's sequence: the idle status before the first `24`, again after it."""
    first = load_filter_detection()[0]["replies"]
    io = FakeFTDI(chunk=4096)
    io.replies = [bytes.fromhex(first[0]), bytes.fromhex(first[1]), bytes.fromhex(first[0])]
    io.replies.append(STOP_REPLY)
    feature = make_fluorescence(io)
    with self.assertRaises(RuntimeError):
      await feature.detect_all_filters()
    self.assertEqual(io.written[-1], STOP_FRAME)
    self.assertIsNone(feature.excitation_filter_slide)

  async def test_no_spectrometer_or_extended_red_sends_nothing(self):
    for field in ("spectrometer", "absorbance_available", "extended_red_photomultiplier_tube"):
      io = FakeFTDI()
      feature = make_fluorescence(io)
      configuration = feature._driver.configuration
      if field == "spectrometer":
        configuration.spectrometer = False
      elif field == "absorbance_available":
        configuration.absorbance_available = False
      else:
        configuration.extended_red_photomultiplier_tube = False
      with self.subTest(field), self.assertRaises(RuntimeError):
        await feature.detect_all_filters()
      self.assertEqual(io.written, [])


def make_detected_slides(fluorescence_feature: Fluorescence) -> None:
  """Give the feature the reader's slides as it detected them, named as the vendor names them."""
  excitation = ExcitationFilterSlide(4)
  dichroic = DichroicFilterSlide(3)
  emission = EmissionFilterSlide(4)
  for filter_slide, slide, name in (
    (excitation, "excitation", "Ex TR"),
    (emission, "emission", "615-18"),
  ):
    detected = Fluorescence._compute_optical_filter(
      1, decode_filter_result(detection_result(slide, 1), slide)
    )
    assert detected is not None
    filter_slide.register(replace(detected, name=name))
  result = decode_filter_result(detection_result("dichroic", 1), "dichroic")
  mirror = Fluorescence._compute_dichroic_filter(1, result)
  assert mirror is not None
  dichroic.register(replace(mirror, name="LP TR"))
  fluorescence_feature.excitation_filter_slide = excitation
  fluorescence_feature.dichroic_filter_slide = dichroic
  fluorescence_feature.emission_filter_slide = emission


async def read_with_slides(io: FakeFTDI, fluorescence_channel: FluorescenceChannel, **arguments):
  """A whole-plate read through `fluorescence_channel`, gain 1000, the captured slides; feature,
  result."""
  io.replies = [
    STATUS_IDLE,
    RUN_REPLY,
    STATUS_MEASURING,
    STATUS_MEASURED,
    TEMPERATURE_37_DATA_REPLY,
  ]
  feature = make_fluorescence(io, fluorescence_plate())
  make_detected_slides(feature)
  return feature, await feature.measure(
    fluorescence_channel, **{"gain": 1000, "focal_height": CAPTURED_FOCAL_HEIGHT, **arguments}
  )


class FilterSlideTests(unittest.IsolatedAsyncioTestCase):
  async def test_slots_and_names_are_the_vendors_reads(self):
    """The three filters' read by slot and by name; the one-filter reads by slot."""
    reads: Dict[str, Tuple[bytes, List[FluorescenceChannel]]] = {
      "three_filters": (
        FILTERS_RUN_FRAME,
        [
          FluorescenceChannel(excitation_filter=1, dichroic_filter=1, emission_filter=1),
          FluorescenceChannel(
            excitation_filter="Ex TR", dichroic_filter="LP TR", emission_filter="615-18"
          ),
        ],
      ),
      "emission_filter": (
        EMISSION_FILTER_RUN_FRAME,
        [FluorescenceChannel(excitation_wavelength=400, dichroic_filter=1, emission_filter=1)],
      ),
      "excitation_filter": (
        EXCITATION_FILTER_RUN_FRAME,
        [FluorescenceChannel(excitation_filter=1, dichroic_filter=1, emission_wavelength=528)],
      ),
    }
    for name, (captured, fluorescence_channels) in reads.items():
      for fluorescence_channel in fluorescence_channels:
        with self.subTest(name, fluorescence_channel=fluorescence_channel):
          io = FakeFTDI()
          _, result = await read_with_slides(
            io, fluorescence_channel, measurement_settings=TEN_FLASHES
          )
          expected = with_nominal_edges(captured, [fluorescence_channel])
          self.assertEqual(ftdi_extract_payload(io.written[1]), expected)
          if fluorescence_channel.excitation_filter is not None:
            self.assertEqual(result.excitation_wavelength, (282.2 + 371.8) / 2)
          if fluorescence_channel.emission_filter is not None:
            self.assertEqual(result.emission_bandwidth, 623.2 - 604.9)

  async def test_every_slot_is_its_detection_position(self):
    """No RUN through slots 2 to 4 or dichroic B and C is captured: their RUN fields are the slide
    positions of the detection's `24`."""
    words = {
      (slide, slot): [int.from_bytes(request[i : i + 2], "big") for i in range(10, 28, 2)]
      for (slide, slot), request in zip(
        DETECTION_POSITIONS,
        (ftdi_extract_payload(bytes.fromhex(p["request"])) for p in load_filter_detection()),
      )
    }
    for slot in range(1, 5):
      for dichroic in range(1, 4):
        with self.subTest(slot=slot, dichroic=dichroic):
          io = FakeFTDI()
          await read_with_slides(
            io,
            FluorescenceChannel(
              excitation_filter=slot, dichroic_filter=dichroic, emission_filter=slot
            ),
          )
          payload = ftdi_extract_payload(io.written[1])
          fluorescence_channel = len(payload) - 36
          fields = [
            int.from_bytes(payload[fluorescence_channel + i : fluorescence_channel + i + 2], "big")
            for i in (2, 4, 6, 8, 10)
          ]
          # The detection's words: two slits, EX_SP, EX_LP, BSP, EM_SP, EM_LP, ...
          excitation = words[("excitation", slot)][2:4]
          mirror = words[("dichroic", dichroic)][4]
          emission = words[("emission", slot)][5:7]
          self.assertEqual(fields, [*excitation, mirror, *emission])

  async def test_unknown_slots_and_names_send_nothing(self):
    io = FakeFTDI()
    feature = make_fluorescence(io, fluorescence_plate())
    unresolved = FluorescenceChannel(
      excitation_filter=1, dichroic_filter=LP_TR, emission_wavelength=528
    )
    with self.assertRaisesRegex(ValueError, "detect_all_filters"):
      await feature.measure(unresolved, gain=1000)
    make_detected_slides(feature)
    refused: List[Tuple[Union[OpticalFilter, int, str], Union[DichroicFilter, int, str]]] = [
      (5, 1),
      (0, 1),
      ("Ex XX", 1),
      (1, 4),
      (1, "LP XX"),
    ]
    for excitation_filter, dichroic_filter in refused:
      fluorescence_channel = FluorescenceChannel(
        excitation_filter=excitation_filter,
        dichroic_filter=dichroic_filter,
        emission_wavelength=528,
      )
      with self.subTest(fluorescence_channel=fluorescence_channel), self.assertRaises(ValueError):
        await feature.measure(fluorescence_channel, gain=1000)
    self.assertEqual(io.written, [])


def load_z_profile_captures() -> Dict[str, Any]:
  """The reader's z-scans: of A1 without a target, of D6 to 90 %, and a brightest-well search.

  Each holds its frame (`09`; the search `0c`, which the driver does not send, kept for its result)
  and the replies the driver reads, as hex: the status before it, the reply to it, to `80`, to
  `05 05`.
  """
  path = os.path.join(os.path.dirname(__file__), "test_data", "z_profile_captures.json.gz")
  with gzip.open(path, "rt") as f:
    return dict(json.load(f))


def with_nominal_z_scan_edges(captured: bytes) -> bytes:
  """`captured`'s payload with 485/15 and 528/20 nm at their nominal edges (`with_nominal_edges`).

  The optics are the last 14 bytes.
  """
  payload = bytearray(ftdi_extract_payload(captured))
  for offset, edges in ((-14, _compute_band_edges(485, 15)), (-8, _compute_band_edges(528, 20))):
    for index, value in enumerate(edges):
      start = len(payload) + offset + 2 * index
      payload[start : start + 2] = value.to_bytes(2, "big")
  return bytes(payload)


async def run_z_profile(name: str, well: str, **arguments):
  """Capture `name`'s z-scan of `well` answered by its replies; the io, the capture, the result."""
  capture = load_z_profile_captures()[name]
  io = FakeFTDI(chunk=4096)
  io.replies = [bytes.fromhex(reply) for reply in capture["replies"]]
  plate = fluorescence_plate()
  feature = make_fluorescence(io, plate)
  result = await feature.measure_z_profile(BASELINE, well=plate.get_well(well), **arguments)
  return io, capture, result


class ZProfileTests(unittest.IsolatedAsyncioTestCase):
  async def test_z_scans_are_sent_and_decoded_as_the_vendors(self):
    """A1 without a target and D6 to 90 %."""
    z_scans = {
      "z_profile_without_target": ("A1", None, (15.0, None, "A1", None, None, 60920)),
      "z_profile_with_gain_target": ("D6", 90, (10.3, 1968, "D6", 231622, 89.08, 43243)),
    }
    for name, (well, target, expected) in z_scans.items():
      with self.subTest(name):
        io, capture, result = await run_z_profile(name, well, gain_target_percent=target)
        self.assertEqual(
          ftdi_extract_payload(io.written[1]),
          with_nominal_z_scan_edges(bytes.fromhex(capture["request"])),
        )
        self.assertEqual(
          (
            result.peak_focal_height,
            result.gain,
            result.well,
            result.peak_counts,
            result.peak_detector_percent,
            result.average_signal,
          ),
          expected,
        )
        self.assertEqual(len(result.z_profile), 144)
        self.assertEqual(result.z_profile[0][0], 15.0)
        self.assertEqual(result.z_profile[-1][0], 0.7)
        self.assertLessEqual(result.start_timestamp, result.end_timestamp)
        self.assertIsNone(result.temperature)
        # `05 05` follows the idle status without one of its own.
        self.assertEqual(ftdi_extract_payload(io.written[-1]), bytes.fromhex("05050000000000"))
        self.assertEqual(io.replies, [])

  def test_a_height_below_0_is_decoded(self):
    """Not a reader's reply: the brightest-well search's result, last point and peak -0.60 mm."""
    reply = bytearray(
      ftdi_extract_payload(
        bytes.fromhex(load_z_profile_captures()["brightest_well_search"]["replies"][-1])
      )
    )
    points = int.from_bytes(reply[23:25], "big")
    last = 25 + 8 * (points - 1)
    reply[last : last + 4] = (-60).to_bytes(4, "big", signed=True)
    reply[17:19] = (-60).to_bytes(2, "big", signed=True)  # the peak there too
    now = datetime.now()
    result = Fluorescence._decode_z_profile_result(
      bytes(reply), fluorescence_plate(), start_timestamp=now, end_timestamp=now
    )
    self.assertEqual(result.z_profile[-1][0], -0.6)
    self.assertEqual(result.peak_focal_height, -0.6)

  def test_a_reply_not_of_this_plate_is_refused(self):
    """The brightest-well search's result, B11, cut short; then of column 13 (not a reply)."""
    reply = ftdi_extract_payload(
      bytes.fromhex(load_z_profile_captures()["brightest_well_search"]["replies"][-1])
    )
    off_plate = bytearray(reply)
    off_plate[15] = 13
    for wrong in (reply[:-8], bytes(off_plate)):
      with self.subTest(length=len(wrong)), self.assertRaises(ValueError):
        Fluorescence._decode_z_profile_result(
          wrong, fluorescence_plate(), start_timestamp=datetime.now(), end_timestamp=datetime.now()
        )

  async def test_a_z_scan_ending_without_its_result_stops(self):
    """Not a reader's reply: an idle status with byte 2 bit 5 clear right after the `09`."""
    capture = load_z_profile_captures()["z_profile_without_target"]
    io = FakeFTDI()
    replies = [bytes.fromhex(reply) for reply in capture["replies"]]
    io.replies = [replies[0], replies[1], replies[0], STOP_REPLY]
    plate = fluorescence_plate()
    with self.assertRaises(RuntimeError):
      await make_fluorescence(io, plate).measure_z_profile(BASELINE, well=plate.get_well("A1"))
    self.assertEqual(io.written[-1], STOP_FRAME)

  async def test_out_of_range_z_scans_send_nothing(self):
    io = FakeFTDI()
    plate = fluorescence_plate()
    feature = make_fluorescence(io, plate)
    refused: List[Tuple[FluorescenceChannel, Dict[str, Any]]] = [
      (BASELINE, dict(maximum_focal_height=0.0)),  # the reader refuses it
      (BASELINE, dict(maximum_focal_height=25.1)),
      (BASELINE, dict(maximum_focal_height=8.55)),
      (BASELINE, dict(gain_target_percent=0)),
      (BASELINE, dict(gain_target_percent=101)),
      (BASELINE, dict(timeout=0)),
      (replace(BASELINE, excitation_wavelength=None), {}),
      (
        FluorescenceChannel(excitation_filter=1, dichroic_filter=1, emission_wavelength=528),
        {},
      ),  # no slides
    ]
    for fluorescence_channel, arguments in refused:
      with (
        self.subTest(fluorescence_channel=fluorescence_channel, arguments=arguments),
        self.assertRaises(ValueError),
      ):
        await feature.measure_z_profile(
          fluorescence_channel, well=plate.get_well("A1"), **arguments
        )
    other = Plate("other", 127.8, 85.5, 14.0, ordered_items={})
    with self.assertRaises(ValueError):
      await feature.measure_z_profile(BASELINE, well=other)  # type: ignore[arg-type]
    self.assertEqual(io.written, [])


if __name__ == "__main__":
  unittest.main()
