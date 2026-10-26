"""Absorbance tests over a fake io: what the driver sends, and the optical densities it computes,
are pinned to the ground truth."""

import asyncio
import gzip
import json
import math
import os
import tempfile
import unittest
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

from pylabrobot.bmg_labtech.clariostar.device import CLARIOstarPlus
from pylabrobot.bmg_labtech.clariostar.driver.configuration_tests import decoded
from pylabrobot.bmg_labtech.clariostar.driver.features import measurement as measurement_module
from pylabrobot.bmg_labtech.clariostar.driver.features.absorbance import (
  Absorbance,
  AbsorbanceConfiguration,
  CLARIOstarAbsorbanceResult,
  CLARIOstarAbsorbanceSpectrumResult,
  _compute_readings,
)
from pylabrobot.bmg_labtech.clariostar.driver.features.drawer_tests import make_plate_holder
from pylabrobot.bmg_labtech.clariostar.driver.features.measurement import (
  MeasurementSettings,
  ShakeSettings,
)
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  STATUS_BUSY,
  STATUS_DRAWER_OPEN,
  STATUS_IDLE,
  STATUS_QUERY_FRAME,
  FakeFTDI,
  load_session_replies,
  make_driver,
)
from pylabrobot.bmg_labtech.clariostar.driver.simulator import CLARIOstarSimulationAbsorbance
from pylabrobot.bmg_labtech.protocol.errors import FrameError
from pylabrobot.bmg_labtech.protocol.framing import ftdi_extract_payload
from pylabrobot.lib.absorbance.results import AbsorbanceResult
from pylabrobot.lib.results import directory as directory_module
from pylabrobot.lib.results.reading import load_rows
from pylabrobot.resources import Plate, Well, cor_96_wellplate_360uL_Fb, create_ordered_items_2d
from pylabrobot.resources.biorad.plates import biorad_96_wellplate_200uL_Vb

# The default read: 600 nm, every well of the plate.
BASELINE_RUN_FRAME = bytes.fromhex(
  "0200900c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a020000000000000000000000000000000000000000000000000000"
  "00000000270f270f0501177000000064232826ca0000006400000005020000000000010000000100050001000013090d"
)
# The default read with 200 flashes.
FLASHES_200_RUN_FRAME = bytes.fromhex(
  "0200900c0431ec2166059604602c561d060c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a020000000000000000000000000000000000000000000000000000"
  "00000000270f270f0501177000000064232826ca0000006400000005020000000000010000000100c80001000013cc0d"
)
# Five wavelengths at 1 flash, every well of a 12776 x 8548 plate.
FIVE_WAVELENGTHS_RUN_FRAME = bytes.fromhex(
  "0200980c0431e82164059e04642c4a1d000c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a020000000000000000000000000000000000000000000000000000"
  "00000000270f270f05050a280af0119417701d4c00000064232826ca0000006400000005020000000000010000000100"
  "0100010000153f0d"
)

# Orbital, 3 mm, 7 flashes, 0.1 s settling, on 6.58 mm wells.
ORBITAL_RUN_FRAME = bytes.fromhex(
  "0200950c0431e82164059e04642c4a1d000c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a320000000000000000000000000000000000000000000000000000"
  "00000000270f270f02030292000501177000000064232826ca0000006400000005020000000000010000000100070001"
  "000013cd0d"
)
# Spiral, 4 mm, 15 flashes, unidirectional, no settling; its pause sends 0 s.
SPIRAL_RUN_FRAME = bytes.fromhex(
  "0200950c0431e82164059e04642c4a1d000c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000008a060000000000000000000000000000000000000000000000000000"
  "00000000270f270f02040292000101177000000064232826ca00000064000000000200000000000100000001000f0001"
  "000014210d"
)

# Orbital, shaken orbital at 300 rpm for 5 s first; its pause sends 0 s.
SHAKE_RUN_FRAME = bytes.fromhex(
  "0200950c0431e82164059e04642c4a1d000c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a320000000000000000000000020000000000020005000000000000"
  "00000000270f270f02030292000501177000000064232826ca0000006400000000020000000000010000000100070001"
  "000013d10d"
)
# The pre-separators of five shaken reads, by (pattern, rpm, seconds).
SHAKE_PRE_SEPARATORS = {
  ("orbital", 300, 5): "32000000000000000000000002000000000002000500000000000000000000",
  ("orbital", 500, 5): "32000000000000000000000002000000000004000500000000000000000000",
  ("orbital", 300, 10): "32000000000000000000000002000000000002000a00000000000000000000",
  ("double_orbital", 300, 5): "32000000000000000000000002000000000202000500000000000000000000",
  ("linear", 300, 5): "32000000000000000000000002000000000102000500000000000000000000",
}

# A 5 s pause before reading, otherwise the default read, on the ground-truth plate.
PAUSE_RUN_FRAME = bytes.fromhex(
  "0200900c0431e82164059e04642c4a1d000c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a020000000000000000000000000000000000000000000000000000"
  "00000000270f270f0501177000000064232826ca0000006400010005020000000000010000000100050001000012fe0d"
)

# One read of column 1 at 600 nm: the replies, in order.
RUN_REPLY = bytes.fromhex(
  "0200350c03250406000000002a9d0000002c010000000e00000001010000000000000001000000040001000000010000"
  "200001a00d"
)
STATUS_MEASURING = bytes.fromhex("0200180c012504260000040100000400000000c000013f0d")
STATUS_MEASURED = bytes.fromhex("0200180c010507260000000000000000000000c00001190d")
DATA_FRAME = bytes.fromhex("02000f0c050200000000000000240d")
COLUMN_1_DATA_REPLY = bytes.fromhex(
  "0200cc0c020506260000a900280028001dffe202200300010008010001000000010100000000000000321a740021b781"
  "00176887000b112a0002c8c50000b52400003b5f000017fe0013acbe00132d3c0013781700138a3100136693001377d8"
  "001393d10013381600089c6d00084714000879bf00086a4200086b7000086fa300086f4400085f74000047be000047bd"
  "000047ae00004794000047780000478e000047a800004798003bf59500008d14001774c500008f83000b3dc500008daa"
  "000047900000000000297a0d"
)

# Spectrum RUNs, all on the ground-truth plate: 300-700/1 nm,
# 400-600/1, 300-700/5, column 1, orbital, and the verification read:
# 220-1000/1, spiral, unidirectional, 20 flashes, double orbital shaking, 5 s pause.
SPECTRUM_300_700_1_RUN_FRAME = bytes.fromhex(
  "0200900c0431e82164059e04642c4a1d000c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a020000000000000000000000000000000000000000000000000000"
  "00000000270f270f05000bb81b58000a232826ca00000064000000000200000000000100000001000500010000134c0d"
)
SPECTRUM_400_600_1_RUN_FRAME = bytes.fromhex(
  "0200900c0431e82164059e04642c4a1d000c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a020000000000000000000000000000000000000000000000000000"
  "00000000270f270f05000fa01770000a232826ca00000064000000000200000000000100000001000500010000134c0d"
)
SPECTRUM_300_700_5_RUN_FRAME = bytes.fromhex(
  "0200900c0431e82164059e04642c4a1d000c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a020000000000000000000000000000000000000000000000000000"
  "00000000270f270f05000bb81b580032232826ca0000006400000000020000000000010000000100050001000013740d"
)
SPECTRUM_COLUMN_1_RUN_FRAME = bytes.fromhex(
  "0200900c0431e82164059e04642c4a1d000c080080080080080080080080080000000000000000000000000000000000"
  "00000000000000000000000000000000000000000a020000000000000000000000000000000000000000000000000000"
  "00000000270f270f05000bb81b58000a232826ca0000006400000000020000000000010000000100050001000009780d"
)
SPECTRUM_ORBITAL_RUN_FRAME = bytes.fromhex(
  "0200950c0431e82164059e04642c4a1d000c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000000a320000000000000000000000000000000000000000000000000000"
  "00000000270f270f020302920005000bb81b58000a232826ca0000006400000000020000000000010000000100050001"
  "0000141a0d"
)
SPECTRUM_VERIFICATION_RUN_FRAME = bytes.fromhex(
  "0200950c0431e82164059e04642c4a1d000c0800ffffffffffffffffffffffff00000000000000000000000000000000"
  "00000000000000000000000000000000000000008a06000000000000000000000002000000000202000a000000000000"
  "00000000270f270f0203029200050008982710000a232826ca0000006400010005020000000000010000000100140001"
  "000014340d"
)

# A timeout that ends a read right after its RUN, so a test can pin the RUN and the STOP.
IMMEDIATE_TIMEOUT = 1e-9

# Column 1 at 600 nm, unidirectional, on the ground-truth plate: RUN_REPLY, STATUS_MEASURED and
# COLUMN_1_DATA_REPLY are this read's replies.
COLUMN_1_RUN_FRAME = bytes.fromhex(
  "0200900c0431e82164059e04642c4a1d000c080080080080080080080080080000000000000000000000000000000000"
  "00000000000000000000000000000000000000008a020000000000000000000000000000000000000000000000000000"
  "00000000270f270f0501177000000064232826ca0000006400000000020000000000010000000100050001000009a40d"
)

# STOP during a run: accepted.
STOP_FRAME = bytes.fromhex("02000a0c0b000000230d")
STOP_REPLY = bytes.fromhex("0200180c0125062600000b0000000000000000c00001430d")

# A 96-well read at 600 nm (verification plate) and its ground-truth optical densities.
VERIFICATION_DATA_REPLY = bytes.fromhex(
  "02064c0c020506260000a901880188001dffe20220030001006001000100000001010000000000f10000645800005e80"
  "00deccd0001278e10001ffe700008b42000070eb000070f500006f6400006b1c00cad94700dcfc9700e7389600005f28"
  "000068560000653b000055620000522e00006d9900006d080000869700009b3600d5fb5d00d3bdfd00d61d5e00d4a259"
  "00ee830600e8434600d736a000ceae3800d2eacd00ca31cd0001763a0001943e00db553600e8695e00d659e700d61c8d"
  "00ca141900c1abd200bcf15d00ae4d6a00d40c1e00ceab94000655a00006ad9a00e2813a00e69c0300ef00d600ef9e37"
  "00e5f0f500e40a1900f0813f00f00e9700ea1b7800e86b6e002796310024464900e727c700da8b6000ec0dbc00ed8c6f"
  "00e64d0b00e5924600eaefd700e3a5d800e5b6f900ebc34d00ce1d9f00f050aa00ec307400de0bda000084f5000077a2"
  "00007722000079e0000078d600006aa9000070d400007016000071dd00006c12000076a300006cb7000084a700007dd9"
  "0000780f0000740300007874000076bc00007338000076b60000775600007049000075600000695a005a6af10053b84e"
  "00574938005ea3460059b613005e6c48005d558c005e05e70057fae200574291005a7d9a00578d10005a4135005cee4c"
  "005e9ccc0059aab7005dfe9e0057d5650058af2c00583f3a00597203005bbafb005cb51e00537781005dd9de005dde23"
  "005d3e0f005df78f0059d5230059eeac005d9af40059940b005abee7005addb4005ce4be005bf366005e529a005eb458"
  "005be45f005b39b0005d0cfa0059ba20005db0e8005c0318005c1a84005c996e005d3d55005b2615005d8bf7005dc4ac"
  "005b1e090059d4a1005e6df9005d7f52005d1250005b8108005c78e6005c1692005cd0e8005709bb005be66d005d3db6"
  "005aa10a005ac8a9005cc9cd005a8fcc005aec13005c44a5005c02a2005e9e35005cf19a00584426005c41da005df66b"
  "005d5313005d3acf005cfe6a005cfd55005d154a005cdb49005d0708005cb818005d16ad005cca07005cb6f9005d6d39"
  "005e3ebb005e5315005e318e005d8270005d9c93005dacb4005dad52005c4dee005cbe8d005cf9ba00222cbb001f9b8c"
  "0020919a0023103b00217a8600234bb10022ea4f0023070c0020b3190020698800219dc1002055260021cfa00022e47f"
  "0022fa6c0021a2f600232dc5002109e4002111a40020c6a200213654002232cc00221806001f013f0023562600230e8a"
  "0022d9650022c7ae0021a560002198c60022a1a40021476f0021cb7f0021d00900228dc200221f4c0023959c00236840"
  "00226170002236a600228de0002198760022a50c0022504d00226dbc002263740022f4620021774c00231b2800232d96"
  "0021f5aa0021a68f0022edc70022c50a00229a9c0021ffdd0022b48c00221f260022cf7300202dea00228caa0022ebc8"
  "0021c9740021fc9a0022ce640021c8970021f865002289310022669600230edd0022c4290020ab590023171500237764"
  "00236879002369a300234976002328c6002321170023114e00234a670022d96500231b220022b94400232cea00238586"
  "0024013f0023f6c00023bea80023768100236de200236e3d0023820a0022e0440022e5390022adbe0000481c000047c9"
  "000047830000478700004764000047780000476b00004756000047460000475b000047340000472f0000481a000047c7"
  "000047aa0000478d00004766000047680000475b0000474e00004743000047370000473d0000473a00004800000047d7"
  "000047970000477e000047760000478000004752000047400000474e0000476d0000474a00004749000047fb000047da"
  "0000479a000047730000478b00004776000047600000475c0000472b000047450000473300004751000047ed000047bd"
  "000047a20000476f00004782000047650000477b000047570000473e0000474a0000474600004740000047fd000047bf"
  "000047960000476b0000477c0000475600004754000047450000473b00004744000047420000473b000047ed000047c8"
  "000047810000477f0000477700004759000047410000475f00004744000047390000473700004744000047e0000047ba"
  "00004790000047830000477c0000475b0000475c0000474c000047400000473e0000472b00004758012ce63d00028ec6"
  "0076afa900028c2e0038e03700028ec3000048320000000001c4620d"
)
VERIFICATION_GROUND_TRUTH = [
  [2.885, 2.909, 0.126, 1.208, 2.173, 2.738, 2.829, 2.828, 2.834, 2.852, 0.165, 0.128],
  [0.114, 2.906, 2.865, 2.878, 2.95, 2.967, 2.842, 2.844, 2.752, 2.69, 0.142, 0.147],
  [0.147, 0.149, 0.097, 0.108, 0.141, 0.159, 0.149, 0.167, 2.308, 2.275, 0.132, 0.107],
  [0.146, 0.146, 0.169, 0.187, 0.198, 0.233, 0.147, 0.158, 1.67, 1.648, 0.117, 0.11],
  [0.098, 0.096, 0.113, 0.116, 0.093, 0.093, 0.105, 0.107, 0.875, 0.913, 0.109, 0.133],
  [0.104, 0.1, 0.112, 0.113, 0.103, 0.116, 0.112, 0.1, 0.158, 0.092, 0.1, 0.126],
  [2.761, 2.806, 2.806, 2.796, 2.8, 2.854, 2.828, 2.832, 2.825, 2.847, 2.806, 2.845],
  [2.762, 2.784, 2.803, 2.818, 2.802, 2.807, 2.82, 2.807, 2.804, 2.831, 2.811, 2.859],
]


def baseline_plate() -> Plate:
  """The default read's plate geometry: 127.80 x 85.50 mm, A1 centre 14.30 / 11.20 mm."""
  return Plate(
    "baseline",
    size_x=127.80,
    size_y=85.50,
    size_z=14.0,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=11.30,
      dy=8.20,
      dz=1.0,
      item_dx=99.2 / 11,
      item_dy=63.1 / 7,
      size_x=6.0,
      size_y=6.0,
      size_z=10.0,
    ),
  )


def ground_truth_96_well_plate() -> Plate:
  """The ground-truth plate's geometry, with 6.58 mm wells:
  127.76 x 85.48 mm, A1 centre 14.38 / 11.24 mm, 9 mm pitch."""
  return Plate(
    "ground_truth",
    size_x=127.76,
    size_y=85.48,
    size_z=14.0,
    ordered_items=create_ordered_items_2d(
      Well,
      num_items_x=12,
      num_items_y=8,
      dx=14.38 - 3.29,
      dy=11.24 - 3.29,
      dz=1.0,
      item_dx=9.0,
      item_dy=9.0,
      size_x=6.58,
      size_y=6.58,
      size_z=10.0,
    ),
  )


class YieldingFTDI(FakeFTDI):
  """A link that answers like a reader and yields before every read, as an executor read does;
  it cancels `cancel_on_status` right after a STATUS is written during a run."""

  def __init__(self) -> None:
    super().__init__()
    self.running = False
    self.cancel_on_status: Optional["asyncio.Task[Any]"] = None

  async def write(self, data: bytes) -> int:
    self.written.append(bytes(data))
    family = ftdi_extract_payload(bytes(data))[0]
    if family == 0x04:
      self.running = True
      self._pending += RUN_REPLY
    elif family == 0x0B:
      self.running = False
      self._pending += STOP_REPLY
    elif family == 0x80:
      self._pending += STATUS_MEASURING if self.running else STATUS_IDLE
      if self.running and self.cancel_on_status is not None:
        self.cancel_on_status.cancel()
        self.cancel_on_status = None
    return len(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    await asyncio.sleep(0)
    return await super().read(num_bytes)


def make_absorbance(io: FakeFTDI, plate: Optional[Plate] = None) -> Absorbance:
  """An absorbance feature whose drawer holds `plate` in its plate holder."""
  driver = make_driver(io)
  driver.configuration = decoded()
  driver.drawer.plate_holder = make_plate_holder()
  if plate is not None:
    driver.drawer.plate_holder.assign_child_resource(plate)
  return Absorbance(driver)


def settings_from(
  shake_pattern=None, shake_speed=None, shake_duration=None, **measurement
) -> Tuple[MeasurementSettings, Optional[ShakeSettings]]:
  """The measurement and shake settings from the flat keywords the tests use; shaking only when
  any shake keyword is given."""
  given = (shake_pattern, shake_speed, shake_duration)
  shake = None if given == (None, None, None) else ShakeSettings(*given)
  return MeasurementSettings(**measurement), shake


def absorbance_feature() -> Absorbance:
  """An absorbance feature without a plate, for its RUN parameters and its decoding."""
  return make_absorbance(FakeFTDI())


def run_parameters(plate, wells, wavelengths, flashes, **settings) -> bytes:
  return absorbance_feature()._compute_run_parameters(
    plate, wells, wavelengths, *settings_from(flashes=flashes, **settings)
  )


async def measure_with(
  absorbance,
  wavelengths,
  wells=None,
  timeout=None,
  simulated_data=None,
  report="optical_density",
  **settings,
):
  """`absorbance.measure` with the settings from the flat keywords the tests use."""
  extra = {} if simulated_data is None else {"simulated_data": simulated_data}
  measurement, shake = settings_from(**settings)
  return await absorbance.measure(
    wells=wells,
    wavelengths=wavelengths,
    measurement_settings=measurement,
    shake_settings=shake,
    report=report,
    timeout=timeout,
    **extra,
  )


# Column 1 of a 96-well plate, row by row, as a measurement keys it.
COLUMN_1 = [f"{row}1" for row in "ABCDEFGH"]


def without_pause_seconds(payload: bytes) -> bytes:
  """`payload` with the no-pause seconds set to v1's 5: the ground truth has 5 or 0."""
  payload = bytearray(payload)
  pause_seconds = len(payload) - 17
  if payload[pause_seconds - 3 : pause_seconds + 1] == bytes(4):
    payload[pause_seconds] = 0x05
  return bytes(payload)


class RunParametersTests(unittest.TestCase):
  def test_orbital(self):
    plate = ground_truth_96_well_plate()
    parameters = run_parameters(
      plate, plate.get_all_items(), [600], 7, well_scan="orbital", scan_diameter=3
    )
    self.assertEqual(b"\x04" + parameters, ftdi_extract_payload(ORBITAL_RUN_FRAME))

  def test_spiral_but_its_pause_seconds(self):
    plate = ground_truth_96_well_plate()
    payload = b"\x04" + run_parameters(
      plate,
      plate.get_all_items(),
      [600],
      15,
      well_scan="spiral",
      scan_diameter=4,
      settling_time=0.0,
      bidirectional=False,
    )
    recorded = ftdi_extract_payload(SPIRAL_RUN_FRAME)
    pause_seconds = payload.index(bytes.fromhex("0000000502"), 100) + 3
    self.assertEqual(payload[pause_seconds], 0x05)
    self.assertEqual(
      payload[:pause_seconds] + payload[pause_seconds + 1 :],
      recorded[:pause_seconds] + recorded[pause_seconds + 1 :],
    )

  def test_shake_but_its_pause_seconds(self):
    plate = ground_truth_96_well_plate()
    payload = b"\x04" + run_parameters(
      plate,
      plate.get_all_items(),
      [600],
      7,
      well_scan="orbital",
      shake_pattern="orbital",
      shake_speed=300,
      shake_duration=5,
    )
    recorded = ftdi_extract_payload(SHAKE_RUN_FRAME)
    pause_seconds = payload.index(bytes.fromhex("0000000502"), 100) + 3
    self.assertEqual(payload[pause_seconds], 0x05)
    self.assertEqual(
      payload[:pause_seconds] + payload[pause_seconds + 1 :],
      recorded[:pause_seconds] + recorded[pause_seconds + 1 :],
    )

  def test_shake_patterns_speeds_and_durations(self):
    plate = ground_truth_96_well_plate()
    for (pattern, speed, duration), recorded in SHAKE_PRE_SEPARATORS.items():
      parameters = run_parameters(
        plate,
        plate.get_all_items(),
        [600],
        7,
        well_scan="orbital",
        shake_pattern=pattern,
        shake_speed=speed,
        shake_duration=duration,
      )
      self.assertEqual(parameters[64:95].hex(), recorded, (pattern, speed, duration))

  def test_five_wavelengths(self):
    plate = biorad_96_wellplate_200uL_Vb("plate")
    parameters = run_parameters(plate, plate.get_all_items(), [260, 280, 450, 600, 750], 1)
    self.assertEqual(b"\x04" + parameters, ftdi_extract_payload(FIVE_WAVELENGTHS_RUN_FRAME))


class SettingsTests(unittest.TestCase):
  def test_the_defaults_are_the_ground_truth(self):
    plate = baseline_plate()
    parameters = absorbance_feature()._compute_run_parameters(
      plate, plate.get_all_items(), [600], MeasurementSettings(), None
    )
    self.assertEqual(b"\x04" + parameters, ftdi_extract_payload(BASELINE_RUN_FRAME))

  def test_every_error_is_listed_at_once(self):
    with self.assertRaises(ValueError) as context:
      MeasurementSettings(
        corner="TL",  # type: ignore[arg-type]
        reading_direction="vertical",  # type: ignore[arg-type]
        well_scan="grid",  # type: ignore[arg-type]
        flashes=2.5,  # type: ignore[arg-type]
      )
    for field_name in ("corner", "reading_direction", "well_scan", "flashes"):
      self.assertIn(field_name, str(context.exception))
    with self.assertRaises(ValueError) as context:
      ShakeSettings("meander", 150, 2.5)  # type: ignore[arg-type]
    for field_name in ("pattern", "speed", "duration"):
      self.assertIn(field_name, str(context.exception))

  def test_a_matrix_size_goes_with_a_matrix_well_scan(self):
    for arguments in (
      dict(well_scan="matrix"),
      dict(well_scan="matrix", matrix_size=11),
      dict(well_scan="matrix", matrix_size=3.0),
      dict(well_scan="point", matrix_size=3),
    ):
      with self.subTest(arguments), self.assertRaises(ValueError):
        MeasurementSettings(**arguments)  # type: ignore[arg-type]

  def test_every_range_error_is_listed_at_once(self):
    plate = baseline_plate()
    configuration = AbsorbanceConfiguration()
    settings = MeasurementSettings(pause_before_reading=0, settling_time=2.0, flashes=0)
    with self.assertRaises(ValueError) as context:
      settings.check(plate, plate.get_all_items(), configuration)
    for field_name in ("pause_before_reading", "settling_time", "flashes"):
      self.assertIn(field_name, str(context.exception))
    with self.assertRaises(ValueError) as context:
      ShakeSettings("orbital", 800, 0).check(configuration)
    for field_name in ("speed", "duration"):
      self.assertIn(field_name, str(context.exception))


class SpectrumRunParametersTests(unittest.TestCase):
  def spectrum_payload(self, start, end, step, flashes, wells=None, shake=None, **measurement):
    plate = ground_truth_96_well_plate()
    wells = plate.get_all_items() if wells is None else wells(plate)
    return b"\x04" + absorbance_feature()._compute_spectrum_run_parameters(
      plate, wells, start, end, step, MeasurementSettings(flashes=flashes, **measurement), shake
    )

  def assert_matches_but_its_pause_seconds(self, payload: bytes, frame: bytes):
    self.assertEqual(payload, without_pause_seconds(ftdi_extract_payload(frame)))

  def test_ranges_and_steps(self):
    for (start, end, step), frame in (
      ((300, 700, 1), SPECTRUM_300_700_1_RUN_FRAME),
      ((400, 600, 1), SPECTRUM_400_600_1_RUN_FRAME),
      ((300, 700, 5), SPECTRUM_300_700_5_RUN_FRAME),
    ):
      self.assert_matches_but_its_pause_seconds(self.spectrum_payload(start, end, step, 5), frame)

  def test_column_1(self):
    payload = self.spectrum_payload(300, 700, 1, 5, wells=lambda plate: plate.get_items("A1:H1"))
    self.assert_matches_but_its_pause_seconds(payload, SPECTRUM_COLUMN_1_RUN_FRAME)

  def test_orbital(self):
    payload = self.spectrum_payload(300, 700, 1, 5, well_scan="orbital", scan_diameter=3)
    self.assert_matches_but_its_pause_seconds(payload, SPECTRUM_ORBITAL_RUN_FRAME)

  def test_out_of_range_spectra_are_refused(self):
    absorbance = absorbance_feature()
    for start, end, step in (
      (219, 700, 1),
      (300, 1001, 1),
      (700, 300, 1),
      (300, 300, 1),
      (300, 700, 3),
      (300, 700, 0),
      (300.5, 700, 1),
    ):
      with self.assertRaises(ValueError):
        absorbance._check_spectrum_arguments(
          start,  # type: ignore[arg-type]
          end,
          step,
          "optical_density",
          MeasurementSettings(),
        )
    absorbance._check_spectrum_arguments(220, 1000, 1, "raw", MeasurementSettings())


def load_verification_spectrum() -> Tuple[List[bytes], List[int], Dict[str, List[float]]]:
  """The 220-1000 nm verification read: its 5 data pages as received, its wavelengths, and its
  ground-truth optical densities, per well and wavelength."""
  path = os.path.join(
    os.path.dirname(__file__), "test_data", "absorbance_spectrum_verification.json.gz"
  )
  with gzip.open(path, "rt") as f:
    data = json.load(f)
  return [bytes.fromhex(page) for page in data["pages"]], data["wavelengths"], data["expected"]


# The verification read's settings, as its RUN encodes them.
VERIFICATION_SPECTRUM_SETTINGS = MeasurementSettings(
  pause_before_reading=5, bidirectional=False, well_scan="spiral", scan_diameter=3, flashes=20
)
VERIFICATION_SPECTRUM_SHAKE = ShakeSettings("double_orbital", 300, 10)


class SpectrumReadTests(unittest.IsolatedAsyncioTestCase):
  async def read_verification_spectrum(self, report="optical_density"):
    pages, wavelengths, expected = load_verification_spectrum()
    io = FakeFTDI(chunk=4096)
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURED, *pages]
    absorbance = make_absorbance(io, ground_truth_96_well_plate())
    result = await absorbance.measure_spectrum(
      start_wavelength=220,
      end_wavelength=1000,
      step_size=1,
      measurement_settings=VERIFICATION_SPECTRUM_SETTINGS,
      shake_settings=VERIFICATION_SPECTRUM_SHAKE,
      report=report,
    )
    return io, result, wavelengths, expected

  async def test_the_verification_read_matches_the_ground_truth(self):
    io, result, wavelengths, expected = await self.read_verification_spectrum()
    self.assertEqual(
      io.written,
      [STATUS_QUERY_FRAME, SPECTRUM_VERIFICATION_RUN_FRAME, STATUS_QUERY_FRAME] + [DATA_FRAME] * 5,
    )
    self.assertEqual(len(result.data), 96)
    self.assertEqual(list(result.data["A1"]), wavelengths)
    differences = [
      abs(result.data[well][wavelength] - value)
      for well, values in expected.items()
      for wavelength, value in zip(wavelengths, values)
    ]
    self.assertEqual(len(differences), 74_976)
    self.assertLess(max(differences), 0.0006)  # the ground truth rounds to 3 decimals
    self.assertEqual(result.temperature, 24.1)
    self.assertLessEqual(result.start_timestamp, result.end_timestamp)

  async def test_raw_counts_compute_the_optical_densities(self):
    _, raw, wavelengths, expected = await self.read_verification_spectrum(report="raw")
    assert isinstance(raw, CLARIOstarAbsorbanceSpectrumResult)
    assert raw.references is not None and raw.calibration is not None
    assert raw.reference_calibration is not None
    for well in ("A1", "D9", "H12"):
      for wavelength in (220, 600, 1000):
        fraction = (raw.data[well][wavelength] / raw.calibration[wavelength][0]) * (
          raw.reference_calibration[0] / raw.references[well]
        )
        expected_density = expected[well][wavelengths.index(wavelength)]
        self.assertAlmostEqual(-math.log10(fraction), expected_density, delta=0.0006)

  def test_pages_out_of_order_or_missing_are_refused(self):
    pages = [ftdi_extract_payload(frame) for frame in load_verification_spectrum()[0]]
    for wrong in ([pages[1], pages[0], *pages[2:]], pages[:4], [*pages, pages[4]]):
      with self.assertRaises(ValueError):
        absorbance_feature()._decode_spectrum_pages(wrong, 781)
    with self.assertRaises(ValueError):
      absorbance_feature()._decode_spectrum_pages(pages, 780)  # another read's wavelength count

  async def test_a_timeout_not_positive_is_refused(self):
    io = FakeFTDI()
    absorbance = make_absorbance(io, ground_truth_96_well_plate())
    for timeout in (0, -1):
      with self.assertRaises(ValueError):
        await measure_with(absorbance, [600], timeout=timeout)
      with self.assertRaises(ValueError):
        await absorbance.measure_spectrum(
          start_wavelength=300, end_wavelength=700, step_size=1, timeout=timeout
        )
    self.assertEqual(io.written, [])


class SimulatedSpectrumTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.reader = CLARIOstarPlus(simulation=True)
    await self.reader.setup()
    self.plate = ground_truth_96_well_plate()
    await self.reader.drawer.open()
    self.reader.plate_holder.assign_child_resource(self.plate)
    await self.reader.drawer.close()
    absorbance = self.reader.absorbance
    assert isinstance(absorbance, CLARIOstarSimulationAbsorbance)
    self.absorbance = absorbance

  async def test_a_simulated_spectrum_reads_within_range(self):
    result = await self.absorbance.measure_spectrum(
      wells=self.plate.get_items("A1:H1"), start_wavelength=400, end_wavelength=500, step_size=10
    )
    self.assertEqual(list(result.data), COLUMN_1)
    for spectrum in result.data.values():
      self.assertEqual(list(spectrum), list(range(400, 501, 10)))
      self.assertTrue(all(0.0 <= value <= 4.0 for value in spectrum.values()))

  async def test_an_earlier_spectrum_is_returned_again(self):
    earlier = await self.absorbance.measure_spectrum(
      start_wavelength=400, end_wavelength=500, step_size=10
    )
    replayed = await self.absorbance.measure_spectrum(
      wells=self.plate.get_items("A1:H1"),
      start_wavelength=400,
      end_wavelength=500,
      step_size=10,
      simulated_data=earlier,
    )
    self.assertEqual(replayed.data, {well: earlier.data[well] for well in COLUMN_1})
    with self.assertRaises(ValueError):  # 405 nm was not read
      await self.absorbance.measure_spectrum(
        start_wavelength=400, end_wavelength=500, step_size=5, simulated_data=earlier
      )


class OpticalDensityTests(unittest.TestCase):
  def test_matches_the_ground_truth(self):
    decoded = absorbance_feature()._decode_data_reply(
      ftdi_extract_payload(VERIFICATION_DATA_REPLY), [600]
    )
    densities = _compute_readings(*decoded, "optical_density")[0]
    expected = [value for row in VERIFICATION_GROUND_TRUTH for value in row]
    for density, value in zip(densities, expected):
      self.assertAlmostEqual(density, value, delta=0.001)

  def test_a_reply_for_other_wavelengths_is_refused(self):
    with self.assertRaises(ValueError):
      absorbance_feature()._decode_data_reply(
        ftdi_extract_payload(VERIFICATION_DATA_REPLY), [450, 600]
      )


class ReadTests(unittest.IsolatedAsyncioTestCase):
  async def test_read_column_1(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED, COLUMN_1_DATA_REPLY]
    plate = ground_truth_96_well_plate()
    column_1 = plate.get_items("A1:H1")
    results = await measure_with(
      make_absorbance(io, plate), [600], wells=column_1, bidirectional=False
    )
    self.assertEqual(io.written[0], STATUS_QUERY_FRAME)
    run = ftdi_extract_payload(io.written[1])
    self.assertEqual(run, without_pause_seconds(ftdi_extract_payload(COLUMN_1_RUN_FRAME)))
    self.assertEqual(io.written[2:], [STATUS_QUERY_FRAME, STATUS_QUERY_FRAME, DATA_FRAME])
    (result,) = results
    self.assertEqual((result.wavelength, result.temperature), (600, None))
    self.assertEqual(list(result.data), COLUMN_1)

  async def test_the_first_poll_waits_after_the_run(self):
    # As the reader answered: a poll sent right after the RUN's reply went unanswered.
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED, COLUMN_1_DATA_REPLY]
    plate = ground_truth_96_well_plate()
    absorbance = make_absorbance(io, plate)
    absorbance._driver.default_poll_interval = 0.25
    waits = []
    real_sleep = asyncio.sleep

    async def sleep(seconds):
      waits.append((seconds, len(io.written)))
      await real_sleep(0)

    with patch.object(measurement_module.asyncio, "sleep", sleep):
      await measure_with(absorbance, [600], wells=plate.get_items("A1:H1"))
    self.assertEqual(waits[0], (0.25, 2))  # after the STATUS and the RUN, before any poll

  async def test_a_well_given_twice_is_read_once(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED, COLUMN_1_DATA_REPLY]
    plate = biorad_96_wellplate_200uL_Vb("plate")
    column_1 = plate.get_items("A1:H1")
    (result,) = await measure_with(make_absorbance(io, plate), [600], wells=column_1 + column_1[:2])
    run = b"\x04" + run_parameters(plate, column_1, [600], 5)
    self.assertEqual(ftdi_extract_payload(io.written[1]), run)
    self.assertEqual(list(result.data), COLUMN_1)

  async def test_a_timeout_stops_the_run(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STOP_REPLY]
    plate = biorad_96_wellplate_200uL_Vb("plate")
    with self.assertRaises(TimeoutError):
      await measure_with(
        make_absorbance(io, plate), [600], wells=plate.get_items("A1:H1"), timeout=IMMEDIATE_TIMEOUT
      )
    self.assertEqual(io.written[-1], STOP_FRAME)

  async def test_a_cancel_between_a_write_and_its_reply_leaves_the_link_clean(self):
    io = YieldingFTDI()
    absorbance = make_absorbance(io, ground_truth_96_well_plate())
    task = asyncio.create_task(absorbance.measure(wavelengths=[600], timeout=10))
    io.cancel_on_status = task  # cancelled after the first STATUS of the run is written
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertIn(STOP_FRAME, io.written)
    self.assertEqual(io._pending, b"")  # no reply left for the next command to read

  async def test_out_of_range_reads_send_nothing(self):
    io = FakeFTDI()
    plate = biorad_96_wellplate_200uL_Vb("plate")
    absorbance = make_absorbance(io, plate)
    other = biorad_96_wellplate_200uL_Vb("other")
    for wavelengths, wells in (
      ([], None),
      ([600] * 9, None),
      ([219], None),
      ([1001], None),
      ([600.5], None),
      ([600], []),
      ([600], other.get_all_items()),
    ):
      with self.assertRaises(ValueError):
        await measure_with(absorbance, wavelengths, wells=wells)  # type: ignore[arg-type]
    self.assertEqual(io.written, [])

  async def test_the_wavelength_limits_are_the_configurations(self):
    io = FakeFTDI()
    absorbance = make_absorbance(io, biorad_96_wellplate_200uL_Vb("plate"))
    absorbance.configuration = AbsorbanceConfiguration(
      wavelength_range=(400, 700), maximum_wavelengths=2
    )
    for wavelengths in ([450, 500, 600], [399], [701]):
      with self.assertRaises(ValueError):
        await measure_with(absorbance, wavelengths)
    self.assertEqual(io.written, [])

  async def test_the_ranges_are_the_configurations(self):
    io = FakeFTDI()
    absorbance = make_absorbance(io, biorad_96_wellplate_200uL_Vb("plate"))
    absorbance.configuration = AbsorbanceConfiguration(
      flashes_range=(1, 10), shake_speed_range=(100, 300)
    )
    for arguments in (
      {"flashes": 11},
      {"shake_pattern": "orbital", "shake_speed": 400, "shake_duration": 5},
    ):
      with self.assertRaises(ValueError):
        await measure_with(absorbance, [600], **arguments)  # type: ignore[arg-type]
    self.assertEqual(io.written, [])

  async def test_raw_counts_compute_the_ground_truth_optical_densities(self):
    readings = {}
    for report in ("optical_density", "raw"):
      io = FakeFTDI()
      io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURED, VERIFICATION_DATA_REPLY]
      (readings[report],) = await measure_with(
        make_absorbance(io, ground_truth_96_well_plate()), [600], report=report
      )  # type: ignore[arg-type]
    density, raw = readings["optical_density"], readings["raw"]
    self.assertIs(type(density), AbsorbanceResult)
    assert isinstance(raw, CLARIOstarAbsorbanceResult)
    assert raw.references is not None and raw.calibration and raw.reference_calibration
    expected = [value for row in VERIFICATION_GROUND_TRUTH for value in row]
    for well, value in zip(density.data, expected):  # both row by row
      fraction = (raw.data[well] / raw.calibration[0]) * (
        raw.reference_calibration[0] / raw.references[well]
      )
      self.assertAlmostEqual(-math.log10(fraction), value, delta=0.0006)
      self.assertAlmostEqual(density.data[well], value, delta=0.0006)
    self.assertLessEqual(density.start_timestamp, density.end_timestamp)

  async def test_flashes_reach_the_wire(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING]
    with self.assertRaises(TimeoutError):
      await measure_with(
        make_absorbance(io, baseline_plate()), [600], flashes=200, timeout=IMMEDIATE_TIMEOUT
      )
    self.assertEqual(io.written[1], FLASHES_200_RUN_FRAME)

  async def test_flashes_out_of_range_send_nothing(self):
    io = FakeFTDI()
    absorbance = make_absorbance(io, biorad_96_wellplate_200uL_Vb("plate"))
    for flashes in (0, 201, 2.5):
      with self.assertRaises(ValueError):
        await measure_with(absorbance, [600], flashes=flashes)  # type: ignore[arg-type]
    self.assertEqual(io.written, [])

  def test_the_read_timeout_default_is_checked_when_set(self):
    absorbance = make_absorbance(FakeFTDI())
    with self.assertRaises(ValueError):
      absorbance.default_read_timeout = 0

  async def test_scan_and_settling_bytes_are_the_ground_truth(self):
    # (arguments, scan byte, settling byte) as the ground truth has them: the corners and
    # directions, the pauses and three orbital reads (the same field).
    cases: List[Tuple[Dict[str, Any], int, int]] = [
      ({}, 0x0A, 0x05),
      ({"corner": "top_right"}, 0x2A, 0x05),
      ({"corner": "bottom_left"}, 0x4A, 0x05),
      ({"corner": "bottom_right"}, 0x6A, 0x05),
      ({"bidirectional": False}, 0x8A, 0x05),
      ({"reading_direction": "row_major"}, 0x02, 0x05),
      ({"settling_time": 0.0}, 0x0A, 0x01),
      ({"settling_time": 0.2}, 0x0A, 0x0A),
      ({"settling_time": 0.5}, 0x0A, 0x19),
      ({"settling_time": 1.0}, 0x0A, 0x32),
    ]
    for arguments, scan, settling in cases:
      io = FakeFTDI()
      io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING]
      plate = biorad_96_wellplate_200uL_Vb("plate")
      with self.assertRaises(TimeoutError):
        absorbance = make_absorbance(io, plate)
        await measure_with(absorbance, [600], timeout=IMMEDIATE_TIMEOUT, **arguments)
      run = ftdi_extract_payload(io.written[1])
      self.assertEqual((run[64], run[100]), (scan, settling), arguments)

  async def test_scan_and_settling_out_of_range_send_nothing(self):
    io = FakeFTDI()
    absorbance = make_absorbance(io, biorad_96_wellplate_200uL_Vb("plate"))
    for arguments in (
      {"settling_time": -0.1},
      {"settling_time": 1.1},
      {"corner": "TL"},
      {"report": "absorbance"},
      {"report": "transmittance"},
    ):
      with self.assertRaises(ValueError):
        await measure_with(absorbance, [600], **arguments)  # type: ignore[arg-type]
    self.assertEqual(io.written, [])

  async def test_every_read_defaults_to_five_flashes(self):
    for well_scan in ("point", "orbital", "spiral"):
      for spectrum in (False, True):
        io = FakeFTDI()
        io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING]
        absorbance = make_absorbance(io, ground_truth_96_well_plate())
        settings = MeasurementSettings(well_scan=well_scan)
        with self.assertRaises(TimeoutError):
          if spectrum:
            await absorbance.measure_spectrum(
              start_wavelength=300,
              end_wavelength=700,
              step_size=1,
              measurement_settings=settings,
              timeout=IMMEDIATE_TIMEOUT,
            )
          else:
            await absorbance.measure(
              wavelengths=[600], measurement_settings=settings, timeout=IMMEDIATE_TIMEOUT
            )
        run = ftdi_extract_payload(io.written[1])
        self.assertEqual(run[-5:-3], (5).to_bytes(2, "big"), (well_scan, spectrum))  # flashes

  async def test_well_scans_out_of_range_send_nothing(self):
    io = FakeFTDI()
    absorbance = make_absorbance(io, ground_truth_96_well_plate())
    for arguments in (
      {"well_scan": "matrix", "matrix_size": 3},  # its data not decoded
      {"well_scan": "orbital", "scan_diameter": 0},
      {"well_scan": "spiral", "scan_diameter": 7},  # wider than the 6.58 mm well
      {"well_scan": "spiral", "scan_diameter": 3.5},
    ):
      with self.assertRaises(ValueError):
        await measure_with(absorbance, [600], **arguments)  # type: ignore[arg-type]
    with self.assertRaises(ValueError):
      await absorbance.measure_spectrum(
        start_wavelength=300,
        end_wavelength=700,
        step_size=1,
        measurement_settings=MeasurementSettings(well_scan="matrix", matrix_size=3),
      )
    plate_384 = Plate(
      "plate_384",
      size_x=127.76,
      size_y=85.48,
      size_z=14.0,
      ordered_items=create_ordered_items_2d(
        Well,
        num_items_x=24,
        num_items_y=16,
        dx=10.0,
        dy=7.0,
        dz=1.0,
        item_dx=4.5,
        item_dy=4.5,
        size_x=3.6,
        size_y=3.6,
        size_z=10.0,
      ),
    )
    absorbance_384 = make_absorbance(io, plate_384)
    for well_scan, mode in (("orbital", 0x32), ("spiral", 0x06)):  # neither refused on 384 wells
      _, _, parameters = absorbance_384._get_run_parameters(
        [600], "optical_density", *settings_from(well_scan=well_scan, scan_diameter=3), None
      )
      self.assertEqual(parameters[64], mode)
    self.assertEqual(io.written, [])

  async def test_shaking_out_of_range_sends_nothing(self):
    io = FakeFTDI()
    absorbance = make_absorbance(io, biorad_96_wellplate_200uL_Vb("plate"))
    for arguments in (
      {"shake_pattern": "meander", "shake_speed": 300, "shake_duration": 5},
      {"shake_pattern": "orbital", "shake_speed": 300},
      {"shake_speed": 300, "shake_duration": 5},
      {"shake_pattern": "orbital", "shake_speed": 150, "shake_duration": 5},
      {"shake_pattern": "orbital", "shake_speed": 800, "shake_duration": 5},
      {"shake_pattern": "orbital", "shake_speed": 300, "shake_duration": 0},
      {"shake_pattern": "orbital", "shake_speed": 300, "shake_duration": 256},
    ):
      with self.assertRaises(ValueError):
        await measure_with(absorbance, [600], **arguments)  # type: ignore[arg-type]
    self.assertEqual(io.written, [])

  async def test_pause_before_reading_reaches_the_wire(self):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING]
    with self.assertRaises(TimeoutError):
      await measure_with(
        make_absorbance(io, biorad_96_wellplate_200uL_Vb("plate")),
        [600],
        pause_before_reading=5,
        timeout=IMMEDIATE_TIMEOUT,
      )
    self.assertEqual(io.written[1], PAUSE_RUN_FRAME)

  async def test_pause_out_of_range_sends_nothing(self):
    io = FakeFTDI()
    absorbance = make_absorbance(io, biorad_96_wellplate_200uL_Vb("plate"))
    for seconds in (0, 3601, 2.5):
      with self.assertRaises(ValueError):
        await measure_with(absorbance, [600], pause_before_reading=seconds)
    self.assertEqual(io.written, [])

  async def test_no_plate_sends_nothing(self):
    io = FakeFTDI()
    with self.assertRaises(RuntimeError):
      await measure_with(make_absorbance(io), [600])
    self.assertEqual(io.written, [])


# The driver's asyncio.Lock needs an event loop on Python 3.9: build drivers inside one.
class SessionTests(unittest.IsolatedAsyncioTestCase):
  """Column 1 of an empty plate, as the reader answered: what this driver sends is the RUN the
  reader ran, and the reads agree with each other."""

  async def read(self, step: str, **arguments):
    """Step `step`'s read of column 1, answered by the reader's reply that step; its RUN and
    results."""
    session = load_session_replies()
    io = FakeFTDI(chunk=4096)
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED]
    io.replies.append(
      bytes.fromhex(session[f"{step}_reply" if f"{step}_reply" in session else f"{step}_page"])
    )
    plate = cor_96_wellplate_360uL_Fb("plate")
    absorbance = make_absorbance(io, plate)
    wells = plate.get_items("A1:H1")
    result: Any
    if "start_wavelength" in arguments:
      result = await absorbance.measure_spectrum(wells=wells, **arguments)
    else:
      result = await absorbance.measure(wells=wells, **arguments)
    self.assertEqual(io.written[1], bytes.fromhex(session[f"{step}_run"]))
    return result

  async def test_one_eight_and_a_spectrum_of_wavelengths_agree(self):
    (single,) = await self.read("absorbance_600_nm", wavelengths=[600])
    eight = await self.read(
      "absorbance_8_wavelengths", wavelengths=[260, 280, 350, 450, 530, 600, 700, 750]
    )
    spectrum = await self.read(
      "absorbance_spectrum", start_wavelength=300, end_wavelength=700, step_size=1
    )
    self.assertEqual([r.wavelength for r in eight], [260, 280, 350, 450, 530, 600, 700, 750])
    at_600 = eight[5].data
    self.assertLess(max(abs(at_600[well] - single.data[well]) for well in single.data), 0.002)
    self.assertEqual(sorted(spectrum.data["A1"]), list(range(300, 701)))
    for discrete, wavelength in ((single.data, 600), (eight[3].data, 450)):
      differences = [abs(spectrum.data[well][wavelength] - discrete[well]) for well in discrete]
      self.assertLess(max(differences), 0.003, wavelength)


class ShortPageTests(unittest.IsolatedAsyncioTestCase):
  """A spectrum data page, as the reader answered: short on its first request (15,783 of 16,204
  bytes), whole on the next."""

  def setUp(self):
    session = load_session_replies()
    self.short = b"".join(
      bytes.fromhex(data) for _, data in session["absorbance_spectrum_short_page_reads"]
    )
    self.whole = bytes.fromhex(session["absorbance_spectrum_page"])

  async def read(self, io: FakeFTDI):
    plate = cor_96_wellplate_360uL_Fb("plate")
    absorbance = make_absorbance(io, plate)
    return await absorbance.measure_spectrum(
      wells=plate.get_items("A1:H1"), start_wavelength=300, end_wavelength=700, step_size=1
    )

  def count(self, io: FakeFTDI, family: int) -> int:
    return sum(ftdi_extract_payload(frame)[0] == family for frame in io.written)

  async def test_a_short_page_stops_the_run_and_the_plate_is_read_again(self):
    io = FakeFTDI(chunk=4096)
    run = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURED]
    io.replies = [*run, self.short, STOP_REPLY, *run, self.whole]
    result = await self.read(io)
    self.assertEqual(len(result.data), 8)
    self.assertEqual(sorted(result.data["A1"]), list(range(300, 701)))
    self.assertEqual(self.count(io, 0x04), 2)  # two RUNs
    self.assertEqual(io.written.count(DATA_FRAME), 2)  # one request per read: none repeated
    self.assertEqual(io.written.count(STOP_FRAME), 1)

  async def test_a_page_short_twice_raises(self):
    io = FakeFTDI(chunk=4096)
    run = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURED]
    io.replies = [*run, self.short, STOP_REPLY, *run, self.short, STOP_REPLY]
    with self.assertRaises(FrameError):
      await self.read(io)
    self.assertEqual(self.count(io, 0x04), 2)
    self.assertEqual(io.written.count(STOP_FRAME), 2)


class GatingTests(unittest.IsolatedAsyncioTestCase):
  def build_with(self, absorbance_available: bool):
    driver = make_driver(FakeFTDI())
    driver.configuration = decoded()
    driver.configuration.absorbance_available = absorbance_available
    driver._discover_features()
    return driver

  def test_absorbance_is_built_when_reported(self):
    self.assertIsInstance(self.build_with(True).absorbance, Absorbance)

  def test_no_absorbance_is_not_built(self):
    self.assertIsNone(self.build_with(False).absorbance)


class SimulatedAbsorbanceTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_simulated_read_is_checked_and_reads_within_range(self):
    reader = CLARIOstarPlus(simulation=True)
    await reader.setup()
    absorbance = reader.absorbance
    assert absorbance is not None
    with self.assertRaises(RuntimeError):
      await measure_with(absorbance, [600])
    plate = biorad_96_wellplate_200uL_Vb("plate")
    await reader.drawer.open()
    reader.plate_holder.assign_child_resource(plate)
    await reader.drawer.close()
    with self.assertRaises(ValueError):
      await measure_with(absorbance, [1001])
    at_450, at_600 = await measure_with(absorbance, [450, 600], wells=plate.get_items("A1:H1"))
    self.assertEqual((at_450.wavelength, at_600.wavelength), (450, 600))
    for result in (at_450, at_600):
      self.assertEqual(list(result.data), COLUMN_1)
      self.assertTrue(all(0.0 <= value <= 4.0 for value in result.data.values()))


class SimulatedDataTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.reader = CLARIOstarPlus(simulation=True)
    await self.reader.setup()
    self.plate = biorad_96_wellplate_200uL_Vb("plate")
    await self.reader.drawer.open()
    self.reader.plate_holder.assign_child_resource(self.plate)
    await self.reader.drawer.close()
    absorbance = self.reader.absorbance
    assert isinstance(absorbance, CLARIOstarSimulationAbsorbance)
    self.absorbance = absorbance

  async def test_earlier_results_are_returned_again(self):
    earlier = await measure_with(self.absorbance, [450, 600])
    replayed = await measure_with(self.absorbance, [450, 600], simulated_data=earlier)
    self.assertEqual([r.data for r in replayed], [r.data for r in earlier])

  async def test_a_subset_reads_its_wells_of_the_earlier_plate(self):
    (earlier,) = await measure_with(self.absorbance, [600])
    column_1 = self.plate.get_items("A1:H1")
    (replayed,) = await measure_with(
      self.absorbance, [600], wells=column_1, simulated_data=[earlier]
    )
    self.assertEqual(replayed.data, {well: earlier.data[well] for well in COLUMN_1})

  async def test_raw_counts_need_simulated_data(self):
    with self.assertRaises(NotImplementedError):
      await measure_with(self.absorbance, [600], report="raw")

  async def test_earlier_raw_results_are_returned_again(self):
    earlier = CLARIOstarAbsorbanceResult(
      data={well: 1000.0 for well in COLUMN_1},
      wavelength=600,
      temperature=None,
      start_timestamp=datetime.now(),
      end_timestamp=datetime.now(),
      references={well: 18000.0 for well in COLUMN_1},
      calibration=(3900000, 0),
      reference_calibration=(18300, 0),
    )
    (replayed,) = await measure_with(
      self.absorbance,
      [600],
      wells=self.plate.get_items("A1:H1"),
      report="raw",
      simulated_data=[earlier],
    )
    assert isinstance(replayed, CLARIOstarAbsorbanceResult)
    self.assertEqual(
      (replayed.data, replayed.references, replayed.calibration, replayed.reference_calibration),
      (earlier.data, earlier.references, earlier.calibration, earlier.reference_calibration),
    )
    with self.assertRaises(ValueError):
      await measure_with(self.absorbance, [600], simulated_data=[earlier])

  async def test_simulated_data_must_match_the_measurement(self):
    (whole_plate,) = await measure_with(self.absorbance, [600])
    (column_1,) = await measure_with(self.absorbance, [600], wells=self.plate.get_items("A1:H1"))
    other_wells = AbsorbanceResult(
      data={"A1": 0.1, "B1": 0.1},
      wavelength=600,
      temperature=None,
      start_timestamp=whole_plate.start_timestamp,
      end_timestamp=whole_plate.end_timestamp,
    )
    for wavelengths, simulated_data in (
      ([450], [whole_plate]),
      ([600], [other_wells]),
      ([600], [column_1]),
    ):
      with self.assertRaises(ValueError):
        await measure_with(self.absorbance, wavelengths, simulated_data=simulated_data)


class ResultsDirectoryTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.directory = os.path.join(temporary.name, "run")

  async def read_column_1(self, **arguments):
    io = FakeFTDI()
    io.replies = [STATUS_IDLE, RUN_REPLY, STATUS_MEASURING, STATUS_MEASURED, COLUMN_1_DATA_REPLY]
    plate = ground_truth_96_well_plate()
    return await make_absorbance(io, plate).measure(
      wells=plate.get_items("A1:H1"), wavelengths=[600], **arguments
    )

  async def test_a_read_appends_its_rows(self):
    results = await self.read_column_1(results_directory=self.directory)
    await self.read_column_1(results_directory=self.directory)
    rows = load_rows(self.directory)
    self.assertEqual([row["read"] for row in rows], [0] * 8 + [1] * 8)
    self.assertEqual([row["value"] for row in rows[:8]], list(results[0].data.values()))

  async def test_a_failed_write_still_returns_the_result_and_is_raised_by_the_next_read(self):
    with (
      patch.object(directory_module.os, "fsync", side_effect=OSError(28, "No space left")),
      self.assertLogs(directory_module.logger, "ERROR"),
    ):
      results = await self.read_column_1(results_directory=self.directory)
    self.assertEqual(list(results[0].data), COLUMN_1)
    with self.assertRaisesRegex(RuntimeError, "not all written"):
      await self.read_column_1(results_directory=self.directory)


class DrawerOpenTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_read_with_the_drawer_out_is_refused_before_the_run(self):
    io = FakeFTDI()
    io.replies = [STATUS_DRAWER_OPEN]
    with self.assertRaisesRegex(RuntimeError, "the drawer is open"):
      await make_absorbance(io, ground_truth_96_well_plate()).measure(wavelengths=[600])
    self.assertEqual(io.written, [STATUS_QUERY_FRAME])

  async def test_the_drawer_is_read_from_the_idle_status_the_run_waits_for(self):
    io = FakeFTDI()
    io.replies = [STATUS_BUSY, STATUS_DRAWER_OPEN]
    absorbance = make_absorbance(io, ground_truth_96_well_plate())
    absorbance._driver.default_poll_interval = 0
    with self.assertRaisesRegex(RuntimeError, "the drawer is open"):
      await absorbance.measure(wavelengths=[600])
    self.assertEqual(io.written, [STATUS_QUERY_FRAME, STATUS_QUERY_FRAME])


if __name__ == "__main__":
  unittest.main()
