# mypy: disable-error-code = attr-defined


import math
import sys
import threading
import unittest
import unittest.mock
from typing import Any, Callable, Coroutine, Iterator, List, Optional, cast

import anyio
import pytest

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup

pytest.importorskip("pylibftdi")

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.ftdi import FTDI
from pylabrobot.legacy.plate_reading.agilent.biotek_backend import BioTekPlateReaderBackend
from pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend import CytationBackend
from pylabrobot.legacy.plate_reading.agilent.biotek_synergyh1_backend import SynergyH1Backend
from pylabrobot.legacy.plate_reading.standard import Exposure, Image, ImagingMode, Objective
from pylabrobot.resources import (
  Plate,
  cellvis_24_wellplate_3600uL_Fb,
  cellvis_96_wellplate_350uL_Fb,
)
from pylabrobot.testing.concurrency import AnyioTestBase


def _byte_iter(s: str) -> Iterator[bytes]:
  for c in s:
    yield c.encode()


def _group_contains(group: BaseExceptionGroup, target: BaseException) -> bool:
  """Return whether an exception group contains the target by identity."""
  return any(
    error is target or (isinstance(error, BaseExceptionGroup) and _group_contains(error, target))
    for error in group.exceptions
  )


def _group_has_message(group: BaseExceptionGroup, message: str) -> bool:
  """Return whether an exception group contains an exception with the message."""
  return any(
    str(error) == message
    or (isinstance(error, BaseExceptionGroup) and _group_has_message(error, message))
    for error in group.exceptions
  )


class TestCytation5Backend(unittest.IsolatedAsyncioTestCase):
  """Tests for the Cytation5Backend."""

  async def asyncSetUp(self):
    self.backend = CytationBackend(timeout=0.1)
    self.backend.io = unittest.mock.MagicMock()
    self.backend.io.setup = unittest.mock.AsyncMock()
    self.backend.io.stop = unittest.mock.AsyncMock()
    self.backend.io.read = unittest.mock.AsyncMock()
    self.backend.io.write = unittest.mock.AsyncMock()
    self.backend.io.usb_reset = unittest.mock.AsyncMock()
    self.backend.io.usb_purge_rx_buffer = unittest.mock.AsyncMock()
    self.backend.io.usb_purge_tx_buffer = unittest.mock.AsyncMock()
    self.backend.io.set_latency_timer = unittest.mock.AsyncMock()
    self.backend.io.set_baudrate = unittest.mock.AsyncMock()
    self.backend.io.set_line_property = unittest.mock.AsyncMock()
    self.backend.io.set_flowctrl = unittest.mock.AsyncMock()
    self.backend.io.set_rts = unittest.mock.AsyncMock()
    self.plate = cellvis_24_wellplate_3600uL_Fb(name="plate")

    # Mock time.time() to control the timestamp in the results
    self.mock_time = unittest.mock.patch("time.time", return_value=12345.6789).start()
    self.addCleanup(self.mock_time.stop)

  async def test_setup(self):
    self.backend.io.read.side_effect = _byte_iter("\x061650200  Version 1.04   0000\x03")
    await self.backend.setup()
    assert self.backend.is_active
    self.backend.io.usb_reset.assert_called_once()
    self.backend.io.set_latency_timer.assert_called_with(16)
    self.backend.io.set_baudrate.assert_called_with(9600)
    # self.backend.io.set_line_property.assert_called_with(8, 2, 0)  #?
    self.backend.io.set_flowctrl.assert_called_with(0x100)
    self.backend.io.set_rts.assert_called_with(1)

    await self.backend.stop()
    assert not self.backend.is_active

  async def test_get_serial_number(self):
    self.backend.io.read.side_effect = _byte_iter("\x0600000000        0000\x03")
    assert await self.backend.get_serial_number() == "00000000"

  async def test_open(self):
    self.backend.io.read.side_effect = [b"\x06", b"\x03", b"\x03"]
    await self.backend.open()
    self.backend.io.write.assert_called_with(b"J")

  async def test_close(self):
    self.backend.io.read.side_effect = [b"\x06", b"\x03", b"\x06", b"\x03", b"\x03"]
    plate = cellvis_24_wellplate_3600uL_Fb(name="plate")
    await self.backend.close(plate=plate)
    self.backend.io.write.assert_called_with(b"A")

  async def test_get_current_temperature(self):
    self.backend.io.read.side_effect = _byte_iter("\x062360000\x03")
    assert await self.backend.get_current_temperature() == 23.6

  async def test_read_absorbance(self):
    self.backend.io.read.side_effect = _byte_iter(
      "\x06"
      + "\x03"
      + "\x06"
      + "0350000000000000010000000000490300000\x03"
      + "\x06"
      + "0000\x03"
      + (
        "01,1,\r000:00:00.0,228,01,01,+0.1917,01,02,+0.1225,01,03,+0.0667,01,04,+0.0728,01,05,+0."
        "0722,01,06,+0.0664,01,07,+0.0763,01,08,+0.0726,01,09,+0.0825,01,10,+0.1001,01,11,+0.1443"
        ",01,12,+0.2105\r\n,02,12,+0.1986,02,11,+0.0800,02,10,+0.0796,02,09,+0.0871,02,08,+0.1059"
        ",02,07,+0.0868,02,06,+0.0544,02,05,+0.0644,02,04,+0.0752,02,03,+0.0768,02,02,+0.0925,02"
        ",01,+0.0802\r\n,03,01,+0.0925,03,02,+0.1007,03,03,+0.0697,03,04,+0.0736,03,05,+0.0712,03"
        ",06,+0.0719,03,07,+0.0710,03,08,+0.0794,03,09,+0.0645,03,10,+0.0799,03,11,+0.0779,03,12,"
        "+0.1256\r\n,04,12,+0.1525,04,11,+0.0711,04,10,+0.0858,04,09,+0.0753,04,08,+0.0787,04,07,"
        "+0.0778,04,06,+0.0895,04,05,+0.0733,04,04,+0.0711,04,03,+0.0672,04,02,+0.0719,04,01,+0.0"
        "954\r\n,05,01,+0.0841,05,02,+0.0610,05,03,+0.0766,05,04,+0.0773,05,05,+0.0632,05,06,+0.0"
        "787,05,07,+0.1100,05,08,+0.0645,05,09,+0.0934,05,10,+0.1439,05,11,+0.1113,05,12,+0.1281"
        "\r\n,06,12,+0.1649,06,11,+0.0707,06,10,+0.0892,06,09,+0.0712,06,08,+0.0935,06,07,+0.1079"
        ",06,06,+0.0704,06,05,+0.0978,06,04,+0.0596,06,03,+0.0794,06,02,+0.0776,06,01,+0.0930\r\n"
        ",07,01,+0.1255,07,02,+0.0742,07,03,+0.0747,07,04,+0.0694,07,05,+0.1004,07,06,+0.0900,07,"
        "07,+0.0659,07,08,+0.0858,07,09,+0.0876,07,10,+0.0815,07,11,+0.0980,07,12,+0.1329\r\n,08,"
        "12,+0.1316,08,11,+0.1290,08,10,+0.1103,08,09,+0.0667,08,08,+0.0790,08,07,+0.0602,08,06,+"
        "0.0670,08,05,+0.0732,08,04,+0.0657,08,03,+0.0684,08,02,+0.1174,08,01,+0.1427\r\n228\x1a0"
        "41\x1a0000\x03"
      )
      + "\x062360000\x03"  # Temperature call
    )

    plate = cellvis_96_wellplate_350uL_Fb(name="plate")
    resp = await self.backend.read_absorbance(
      plate=plate, wells=plate.get_all_items(), wavelength=580
    )

    self.backend.io.write.assert_any_call(b"D")
    self.backend.io.write.assert_any_call(
      b"004701010108120001200100001100100000106000080580113\x03"
    )
    self.backend.io.write.assert_any_call(b"O")

    expected_data = [
      [
        0.1917,
        0.1225,
        0.0667,
        0.0728,
        0.0722,
        0.0664,
        0.0763,
        0.0726,
        0.0825,
        0.1001,
        0.1443,
        0.2105,
      ],
      [
        0.0802,
        0.0925,
        0.0768,
        0.0752,
        0.0644,
        0.0544,
        0.0868,
        0.1059,
        0.0871,
        0.0796,
        0.08,
        0.1986,
      ],
      [
        0.0925,
        0.1007,
        0.0697,
        0.0736,
        0.0712,
        0.0719,
        0.071,
        0.0794,
        0.0645,
        0.0799,
        0.0779,
        0.1256,
      ],
      [
        0.0954,
        0.0719,
        0.0672,
        0.0711,
        0.0733,
        0.0895,
        0.0778,
        0.0787,
        0.0753,
        0.0858,
        0.0711,
        0.1525,
      ],
      [0.0841, 0.061, 0.0766, 0.0773, 0.0632, 0.0787, 0.11, 0.0645, 0.0934, 0.1439, 0.1113, 0.1281],
      [
        0.093,
        0.0776,
        0.0794,
        0.0596,
        0.0978,
        0.0704,
        0.1079,
        0.0935,
        0.0712,
        0.0892,
        0.0707,
        0.1649,
      ],
      [0.1255, 0.0742, 0.0747, 0.0694, 0.1004, 0.09, 0.0659, 0.0858, 0.0876, 0.0815, 0.098, 0.1329],
      [0.1427, 0.1174, 0.0684, 0.0657, 0.0732, 0.067, 0.0602, 0.079, 0.0667, 0.1103, 0.129, 0.1316],
    ]
    self.assertEqual(
      resp,
      [
        {
          "wavelength": 580,
          "data": expected_data,
          "temperature": 23.6,
          "time": 12345.6789,
        }
      ],
    )

  async def test_read_luminescence_partial(self):
    self.backend.io.read.side_effect = _byte_iter(
      # plate
      "\x06"
      + "\x03"
      # focal height
      + "\x06"
      + "\x03"
      # read block 1
      + "\x06"
      + "0350000000000000010000000000490300000\x03"
      + "\x060000\x03"
      + "01,1,\r000:00:00.0,237,01,01,0000003\r\n,02,01,0000003\r\n,03,01,0000005\r\n,04,01,0000004\r\n,05,01,0000003\r\n,06,01,0000002\r\n,07,01,0000000\r\n237\x1a132\x1a0000\x03"
      # read block 2
      + "\x06"
      + "0350000000000000010000000000030200000\x03"
      + "\x060000\x03"
      + "01,1,\r000:00:00.0,237,02,02,0000043,02,03,0000014\r\n,03,03,0000014,03,02,0000012\r\n,04,02,0000011,04,03,0000014\r\n,05,03,0000010,05,02,0000010\r\n,06,02,0000011,06,03,0000027\r\n,07,03,0000009,07,02,0000010\r\n237\x1a160\x1a0000\x03"
      # read block 3
      + "\x06"
      + "0350000000000000010000000000000170000\x03"
      + "\x060000\x03"
      + "01,1,\r000:00:00.0,237,04,04,0000018\r\n,05,04,0000017\r\n,06,04,0000014\r\n237\x1a210\x1a0000\x03"
      + "\x062360000\x03"  # Temperature call
    )

    plate = cellvis_96_wellplate_350uL_Fb(name="plate")
    wells = plate["A1"] + plate["B1:G3"] + plate["D4:F4"]
    resp = await self.backend.read_luminescence(
      focal_height=4.5, integration_time=0.4, plate=plate, wells=wells
    )

    self.backend.io.write.assert_any_call(b"D")
    self.backend.io.write.assert_any_call(
      b"008401010107010001200100001100100000123000020200200-001000-00300000000000000000001351086"
    )
    self.backend.io.write.assert_any_call(
      b"008401020207030001200100001100100000123000020200200-001000-00300000000000000000001351090"
    )
    self.backend.io.write.assert_any_call(
      b"008401040406040001200100001100100000123000020200200-001000-00300000000000000000001351094"
    )
    self.backend.io.write.assert_any_call(b"O")

    expected_data = [
      [3.0, None, None, None, None, None, None, None, None, None, None, None],
      [3.0, 43.0, 14.0, None, None, None, None, None, None, None, None, None],
      [5.0, 12.0, 14.0, None, None, None, None, None, None, None, None, None],
      [4.0, 11.0, 14.0, 18.0, None, None, None, None, None, None, None, None],
      [3.0, 10.0, 10.0, 17.0, None, None, None, None, None, None, None, None],
      [2.0, 11.0, 27.0, 14.0, None, None, None, None, None, None, None, None],
      [0.0, 10.0, 9.0, None, None, None, None, None, None, None, None, None],
      [None, None, None, None, None, None, None, None, None, None, None, None],
    ]
    self.assertEqual(
      resp,
      [
        {
          "data": expected_data,
          "temperature": 23.6,
          "time": 12345.6789,
        }
      ],
    )

  async def test_read_fluorescence(self):
    self.backend.io.read.side_effect = _byte_iter(
      "\x06"
      + "\x03"
      + "\x06"
      + "0000\x03"
      + "\x06"
      + "0350000000000000010000000000490300000\x03"
      + "\x06"
      + "0000\x03"
      + (
        "01,1,\r000:00:00.0,227,01,01,0000427,01,02,0000746,01,03,0000598,01,04,0000742,01,05,0001"
        "516,01,06,0000704,01,07,0000676,01,08,0000734,01,09,0001126,01,10,0000790,01,11,0000531,0"
        "1,12,0000531\r\n,02,12,0002066,02,11,0000541,02,10,0000618,02,09,0000629,02,08,0000891,02"
        ",07,0000731,02,06,0000484,02,05,0000576,02,04,0000465,02,03,0000501,02,02,0002187,02,01,0"
        "000462\r\n,03,01,0000728,03,02,0000583,03,03,0000472,03,04,0000492,03,05,0000501,03,06,00"
        "00491,03,07,0000580,03,08,0000541,03,09,0000556,03,10,0000474,03,11,0000532,03,12,0000522"
        "\r\n,04,12,0000570,04,11,0000523,04,10,0000784,04,09,0000441,04,08,0000703,04,07,0000591,"
        "04,06,0000580,04,05,0000479,04,04,0000474,04,03,0000414,04,02,0000520,04,01,0000427\r\n,0"
        "5,01,0000486,05,02,0000422,05,03,0000612,05,04,0000588,05,05,0000805,05,06,0000510,05,07,"
        "0001697,05,08,0000615,05,09,0001137,05,10,0000653,05,11,0000558,05,12,0000648\r\n,06,12,0"
        "000765,06,11,0000487,06,10,0000683,06,09,0001068,06,08,0000721,06,07,0003269,06,06,000067"
        "9,06,05,0000532,06,04,0000601,06,03,0000491,06,02,0000538,06,01,0000688\r\n,07,01,0000653"
        ",07,02,0000783,07,03,0000522,07,04,0000536,07,05,0000673,07,06,0000858,07,07,0000526,07,0"
        "8,0000627,07,09,0000574,07,10,0001993,07,11,0000712,07,12,0000970\r\n,08,12,0000523,08,11"
        ",0000607,08,10,0003002,08,09,0000900,08,08,0000697,08,07,0000542,08,06,0000688,08,05,0000"
        "622,08,04,0000555,08,03,0000542,08,02,0000742,08,01,0001118\r\n228\x1a091\x1a0000\x03"
      )
      + "\x062360000\x03"  # Temperature call
    )

    plate = cellvis_96_wellplate_350uL_Fb(name="plate")
    resp = await self.backend.read_fluorescence(
      plate=plate,
      wells=plate.get_all_items(),
      excitation_wavelength=485,
      emission_wavelength=528,
      focal_height=7.5,
    )

    self.backend.io.write.assert_any_call(b"t")
    self.backend.io.write.assert_any_call(b"621720\x03")
    self.backend.io.write.assert_any_call(b"D")
    self.backend.io.write.assert_any_call(
      b"0084010101081200012001000011001000001350001002002000485000052800000000000000000021001119"
      b"\x03"
    )
    self.backend.io.write.assert_any_call(b"O")

    expected_data = [
      [427.0, 746.0, 598.0, 742.0, 1516.0, 704.0, 676.0, 734.0, 1126.0, 790.0, 531.0, 531.0],
      [462.0, 2187.0, 501.0, 465.0, 576.0, 484.0, 731.0, 891.0, 629.0, 618.0, 541.0, 2066.0],
      [728.0, 583.0, 472.0, 492.0, 501.0, 491.0, 580.0, 541.0, 556.0, 474.0, 532.0, 522.0],
      [427.0, 520.0, 414.0, 474.0, 479.0, 580.0, 591.0, 703.0, 441.0, 784.0, 523.0, 570.0],
      [486.0, 422.0, 612.0, 588.0, 805.0, 510.0, 1697.0, 615.0, 1137.0, 653.0, 558.0, 648.0],
      [688.0, 538.0, 491.0, 601.0, 532.0, 679.0, 3269.0, 721.0, 1068.0, 683.0, 487.0, 765.0],
      [653.0, 783.0, 522.0, 536.0, 673.0, 858.0, 526.0, 627.0, 574.0, 1993.0, 712.0, 970.0],
      [1118.0, 742.0, 542.0, 555.0, 622.0, 688.0, 542.0, 697.0, 900.0, 3002.0, 607.0, 523.0],
    ]
    self.assertEqual(
      resp,
      [
        {
          "ex_wavelength": 485,
          "em_wavelength": 528,
          "data": expected_data,
          "temperature": 23.6,
          "time": 12345.6789,
        }
      ],
    )

  async def test_parse_body_asterisks_as_nan(self):
    """Unmeasured wells return ******* which should be parsed as NaN."""
    plate = cellvis_96_wellplate_350uL_Fb(name="plate")
    self.backend._plate = plate

    body = (
      b"01,1,\r000:00:00.0,233,"
      b"01,01,4292723,01,02,*******,01,03,3492028,01,04,*******,"
      b"01,05,3263629,01,06,*******,01,07,3658798,01,08,*******,"
      b"01,09,4098038,01,10,*******,01,11,4145669,01,12,*******"
      b"\r\n,02,12,*******,02,11,*******,02,10,*******,02,09,*******,"
      b"02,08,*******,02,07,*******,02,06,*******,02,05,*******,"
      b"02,04,*******,02,03,*******,02,02,*******,02,01,*******"
      b"\r\n,03,01,4339544,03,02,*******,03,03,3698255,03,04,*******,"
      b"03,05,2832844,03,06,*******,03,07,2697772,03,08,*******,"
      b"03,09,2799316,03,10,*******,03,11,3675674,03,12,*******"
      b"\r\n,04,12,*******,04,11,*******,04,10,4180324,04,09,4570484,"
      b"04,08,3466838,04,07,4408238,04,06,3750929,04,05,5540778,"
      b"04,04,*******,04,03,*******,04,02,*******,04,01,*******"
      b"\r\n,05,01,4638239,05,02,*******,05,03,4478322,05,04,*******,"
      b"05,05,2829157,05,06,5192196,05,07,2045115,05,08,4018946,"
      b"05,09,1619483,05,10,4215321,05,11,2473302,05,12,*******"
      b"\r\n,06,12,4451055,06,11,4706624,06,10,2240607,06,09,3049162,"
      b"06,08,2516745,06,07,4645523,06,06,3999330,06,05,*******,"
      b"06,04,*******,06,03,*******,06,02,*******,06,01,*******"
      b"\r\n,07,01,4981153,07,02,*******,07,03,5391780,07,04,*******,"
      b"07,05,3862731,07,06,*******,07,07,2049449,07,08,4042665,"
      b"07,09,1386693,07,10,2328369,07,11,1745744,07,12,*******"
      b"\r\n,08,12,*******,08,11,3927160,08,10,2585026,08,09,3184874,"
      b"08,08,2299700,08,07,3675092,08,06,4756136,08,05,*******,"
      b"08,04,*******,08,03,*******,08,02,*******,08,01,*******"
      b"\r\n233\x1a014\x1a0000\x03"
    )

    result = self.backend._parse_body(body)
    NaN = float("nan")

    expected = [
      [4292723, NaN, 3492028, NaN, 3263629, NaN, 3658798, NaN, 4098038, NaN, 4145669, NaN],
      [NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN],
      [4339544, NaN, 3698255, NaN, 2832844, NaN, 2697772, NaN, 2799316, NaN, 3675674, NaN],
      [NaN, NaN, NaN, NaN, 5540778, 3750929, 4408238, 3466838, 4570484, 4180324, NaN, NaN],
      [
        4638239,
        NaN,
        4478322,
        NaN,
        2829157,
        5192196,
        2045115,
        4018946,
        1619483,
        4215321,
        2473302,
        NaN,
      ],
      [NaN, NaN, NaN, NaN, NaN, 3999330, 4645523, 2516745, 3049162, 2240607, 4706624, 4451055],
      [4981153, NaN, 5391780, NaN, 3862731, NaN, 2049449, 4042665, 1386693, 2328369, 1745744, NaN],
      [NaN, NaN, NaN, NaN, NaN, 4756136, 3675092, 2299700, 3184874, 2585026, 3927160, NaN],
    ]

    for r in range(8):
      for c in range(12):
        e, v = expected[r][c], result[r][c]
        if math.isnan(e):
          self.assertTrue(v is not None and math.isnan(v), f"Expected NaN at ({r},{c}), got {v}")
        else:
          self.assertEqual(v, e, f"Mismatch at ({r},{c})")


class TransactionFTDI:
  """Typed FTDI double exposing command transaction boundaries."""

  def __init__(self) -> None:
    self.events: List[str] = []
    self.writes: List[bytes] = []
    self.responses = bytearray()
    self.purge_count = 0
    self.block_purge_call: Optional[int] = None
    self.purge_started = anyio.Event()
    self.release_purge = anyio.Event()
    self.block_write: Optional[bytes] = None
    self.write_started = anyio.Event()
    self.release_write = anyio.Event()
    self.block_first_read = False
    self.read_count = 0
    self.read_started = anyio.Event()
    self.release_read = anyio.Event()

  async def usb_purge_rx_buffer(self) -> None:
    self.purge_count += 1
    self.events.append(f"purge.rx:{self.purge_count}")
    if self.purge_count == self.block_purge_call:
      self.purge_started.set()
      with anyio.CancelScope(shield=True):
        await self.release_purge.wait()

  async def usb_purge_tx_buffer(self) -> None:
    self.events.append("purge.tx")

  async def write(self, data: bytes) -> int:
    self.events.append(f"write:{data.decode()}")
    self.writes.append(data)
    responses = {
      b"A": b"\x06A\x03",
      b"B": b"\x06B\x03",
      b"C": b"\x06C\x03",
      b"P": b"\x06",
      b"value": b"done\x03",
    }
    self.responses.extend(responses[data])
    if data == self.block_write:
      self.write_started.set()
      with anyio.CancelScope(shield=True):
        await self.release_write.wait()
    else:
      await anyio.lowlevel.checkpoint()
    return len(data)

  async def read(self, size: int) -> bytes:
    assert size == 1
    self.read_count += 1
    if self.block_first_read and self.read_count == 1:
      self.read_started.set()
      await self.release_read.wait()
    await anyio.lowlevel.checkpoint()
    if not self.responses:
      return b""
    value = bytes(self.responses[:1])
    del self.responses[:1]
    self.events.append(f"read:{value.hex()}")
    return value


class EmptyReadFTDI:
  """Typed FTDI double that never produces a response byte."""

  async def read(self, size: int) -> bytes:
    assert size == 1
    await anyio.lowlevel.checkpoint()
    return b""


def make_transaction_backend(io: TransactionFTDI) -> BioTekPlateReaderBackend:
  """Construct a BioTek backend with software-only transaction I/O."""
  with unittest.mock.patch("pylabrobot.legacy.plate_reading.agilent.biotek_backend.FTDI"):
    backend = BioTekPlateReaderBackend(timeout=0.1)
  backend.io = cast(FTDI, io)
  return backend


class TestBioTekTransactions(AnyioTestBase):
  async def test_concurrent_commands_do_not_cross_responses(self) -> None:
    io = TransactionFTDI()
    io.block_first_read = True
    backend = make_transaction_backend(io)
    responses: dict[str, Optional[bytes]] = {}

    async def command(name: str) -> None:
      responses[name] = await backend.send_command(name)

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(command, "A")
      await io.read_started.wait()
      task_group.start_soon(command, "B")
      await anyio.wait_all_tasks_blocked()
      assert io.writes == [b"A"]
      assert io.purge_count == 6
      io.release_read.set()

    assert responses == {"A": b"\x06A\x03", "B": b"\x06B\x03"}

  async def test_cancel_before_first_write_writes_nothing(self) -> None:
    io = TransactionFTDI()
    backend = make_transaction_backend(io)

    with anyio.CancelScope() as scope:
      scope.cancel()
      await backend.send_command("A")

    assert io.writes == []

  async def test_cancellation_during_purge_writes_nothing_and_releases_lock(self) -> None:
    io = TransactionFTDI()
    io.block_purge_call = 3
    backend = make_transaction_backend(io)
    scopes: List[anyio.CancelScope] = []
    finished = anyio.Event()

    async def cancelled_command() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await backend.send_command("P", "value")
      finally:
        finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(cancelled_command)
      await io.purge_started.wait()
      scopes[0].cancel()
      io.release_purge.set()
      await finished.wait()

    assert io.writes == []
    assert await backend.send_command("A") == b"\x06A\x03"

  async def test_cancel_after_command_write_consumes_response_before_propagating(self) -> None:
    io = TransactionFTDI()
    io.block_write = b"C"
    backend = make_transaction_backend(io)
    scopes: List[anyio.CancelScope] = []
    cancellation_observed = anyio.Event()

    async def command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await backend.send_command("C")
        except anyio.get_cancelled_exc_class():
          cancellation_observed.set()
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(command)
      await io.write_started.wait()
      scopes[0].cancel()
      io.release_write.set()
      with anyio.fail_after(1):
        await cancellation_observed.wait()

    assert cancellation_observed.is_set()
    assert io.writes == [b"C"]
    assert io.responses == b""

  async def test_cancel_after_parameter_write_consumes_terminal_response(self) -> None:
    io = TransactionFTDI()
    io.block_write = b"value"
    backend = make_transaction_backend(io)
    scopes: List[anyio.CancelScope] = []
    cancellation_observed = anyio.Event()

    async def command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await backend.send_command("P", "value")
        except anyio.get_cancelled_exc_class():
          cancellation_observed.set()
          raise

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(command)
      await io.write_started.wait()
      scopes[0].cancel()
      io.release_write.set()
      with anyio.fail_after(1):
        await cancellation_observed.wait()

    assert cancellation_observed.is_set()
    assert io.writes == [b"P", b"value"]
    assert io.responses == b""

  async def test_read_until_uses_one_anyio_deadline(self) -> None:
    with unittest.mock.patch("pylabrobot.legacy.plate_reading.agilent.biotek_backend.FTDI"):
      backend = BioTekPlateReaderBackend(timeout=0.02)
    backend.io = cast(FTDI, EmptyReadFTDI())

    with (
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_backend.time.time", return_value=0
      ),
      anyio.fail_after(0.2),
      pytest.raises(TimeoutError, match="Timeout while waiting for response"),
    ):
      await backend._read_until(b"\x03")


class RecordingFTDI(HasLifespan):
  """Software FTDI resource recording lifecycle and configuration boundaries."""

  def __init__(self, events: List[str], fail_phase: Optional[str] = None) -> None:
    super().__init__()
    self.events = events
    self.fail_phase = fail_phase
    self.closed = True

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingFTDI does not accept lifespan options")
    self.closed = False
    self.events.append("ftdi.enter")
    stack.push_shielded_async_callback(self._close)

  async def _close(self) -> None:
    self.closed = True
    self.events.append("ftdi.exit")

  async def _phase(self, name: str) -> None:
    if self.closed:
      raise RuntimeError("FTDI I/O after closure")
    self.events.append(name)
    if self.fail_phase == name:
      raise RuntimeError(f"{name} failed")

  async def usb_reset(self) -> None:
    await self._phase("usb_reset")

  async def set_latency_timer(self, latency: int) -> None:
    assert latency == 16
    await self._phase("latency")

  async def set_baudrate(self, baudrate: int) -> None:
    assert baudrate in (9600, 38_461)
    await self._phase("baudrate")

  async def set_line_property(self, bits: int, stop_bits: int, parity: int) -> None:
    assert (bits, stop_bits, parity) == (8, 2, 0)
    await self._phase("line_property")

  async def set_flowctrl(self, flow_control: int) -> None:
    assert flow_control == 0x100
    await self._phase("flow_control")

  async def set_rts(self, level: bool) -> None:
    assert level
    await self._phase("rts")


def make_lifecycle_backend(
  backend_type: type[BioTekPlateReaderBackend], fail_phase: Optional[str] = None
) -> tuple[BioTekPlateReaderBackend, RecordingFTDI]:
  events: List[str] = []
  with unittest.mock.patch("pylabrobot.legacy.plate_reading.agilent.biotek_backend.FTDI"):
    backend = backend_type(timeout=0.1)
  io = RecordingFTDI(events, fail_phase=fail_phase)
  backend.io = cast(FTDI, io)

  async def firmware() -> str:
    await io._phase("firmware")
    return "2.07"

  backend.get_firmware_version = firmware  # type: ignore[method-assign]
  return backend, io


class RecordingShakeBackend(BioTekPlateReaderBackend):
  """BioTek backend retaining real shaking control flow with command doubles."""

  def __init__(self, events: List[str]) -> None:
    with unittest.mock.patch("pylabrobot.legacy.plate_reading.agilent.biotek_backend.FTDI"):
      super().__init__(timeout=0.1)
    self.events = events
    self.operation_started = anyio.Event()
    self.release_operation = anyio.Event()
    self.block_operation = False
    self.fail_operation = False
    self.abort_failures = 0

  async def get_firmware_version(self) -> str:
    return "2.07"

  async def send_command(
    self,
    command: str,
    parameter: Optional[str] = None,
    wait_for_response: bool = True,
    timeout: Optional[float] = None,
  ) -> Optional[bytes]:
    del parameter, wait_for_response, timeout
    self.events.append(f"command:{command}")
    if command == "O" and self.block_operation:
      self.operation_started.set()
      await self.release_operation.wait()
    if command == "O" and self.fail_operation:
      raise RuntimeError("shake worker failed")
    if command == "x" and self.abort_failures:
      self.abort_failures -= 1
      raise RuntimeError("shake abort failed")
    await anyio.lowlevel.checkpoint()
    return b"\x060000\x03" if command == "O" else b"\x03"


def make_shake_backend() -> tuple[RecordingShakeBackend, RecordingFTDI]:
  events: List[str] = []
  backend = RecordingShakeBackend(events)
  io = RecordingFTDI(events)
  backend.io = cast(FTDI, io)
  return backend, io


class TestBioTekLifecycle(AnyioTestBase):
  def test_base_cytation_and_synergy_inherit_managed_lifecycle_methods(self) -> None:
    for backend_type in (BioTekPlateReaderBackend, CytationBackend, SynergyH1Backend):
      assert backend_type.__aenter__ is HasLifespan.__aenter__
      assert backend_type.__aexit__ is HasLifespan.__aexit__
      assert backend_type.setup is HasLifespan.setup
      assert backend_type.stop is HasLifespan.stop

  @pytest.mark.parametrize(
    "backend_type", [BioTekPlateReaderBackend, CytationBackend, SynergyH1Backend]
  )
  async def test_lifespan_closes_ftdi_on_body_failure_and_supports_reuse(
    self, backend_type: type[BioTekPlateReaderBackend]
  ) -> None:
    backend, io = make_lifecycle_backend(backend_type)
    body_error = RuntimeError("body failed")
    with pytest.raises(RuntimeError, match="body failed") as error_info:
      async with backend:
        assert backend.is_active and io.is_active
        raise body_error
    assert error_info.value is body_error
    assert io.events[-1] == "ftdi.exit"

    async with backend:
      pass
    assert io.events.count("ftdi.enter") == 2
    assert io.events.count("ftdi.exit") == 2

  @pytest.mark.parametrize(
    "phase",
    ["usb_reset", "latency", "baudrate", "line_property", "flow_control", "rts", "firmware"],
  )
  async def test_each_partial_base_entry_failure_closes_ftdi(self, phase: str) -> None:
    backend, io = make_lifecycle_backend(BioTekPlateReaderBackend, fail_phase=phase)
    with pytest.raises(RuntimeError, match=f"{phase} failed"):
      async with backend:
        pytest.fail("failed entry reached body")
    assert io.events[-1] == "ftdi.exit"

  async def test_base_rejects_lifespan_options_before_ftdi_entry(self) -> None:
    backend, io = make_lifecycle_backend(BioTekPlateReaderBackend)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with backend.lifespan(unknown=True):
        pytest.fail("invalid options reached body")
    assert io.events == []

  async def test_no_ftdi_io_after_backend_exit(self) -> None:
    backend, io = make_lifecycle_backend(BioTekPlateReaderBackend)
    async with backend:
      pass
    events_before = list(io.events)
    with pytest.raises(RuntimeError, match="not active"):
      await backend.send_command("J")
    assert io.events == events_before


class RecordingCytation(CytationBackend):
  """Cytation camera lifecycle double with blocking synchronous SDK calls."""

  def __init__(self, events: List[str]) -> None:
    with unittest.mock.patch("pylabrobot.legacy.plate_reading.agilent.biotek_backend.FTDI"):
      super().__init__(timeout=0.1)
    self.events = events
    self._filters = []
    self._objectives = []
    self.setup_started = threading.Event()
    self.setup_release = threading.Event()
    self.stop_started = threading.Event()
    self.stop_release = threading.Event()
    self.block_setup = False
    self.block_stop = False
    self.camera_followup_failure: Optional[str] = None
    self.camera = RecordingAcquisitionCamera(events)

  async def get_firmware_version(self) -> str:
    return "2.07"

  async def _set_up_camera(self) -> None:
    raise AssertionError("camera setup ran on the async owner")

  def _set_up_camera_sync(self) -> tuple[object, object]:
    self.events.append("camera.setup.start")
    self.setup_started.set()
    if self.block_setup:
      self.setup_release.wait(2)
    self.events.append("camera.setup.finish")
    return object(), self.camera

  def _stop_camera_sync(self, cam: object, system: object) -> None:
    del cam, system
    self.events.append("camera.stop.start")
    self.stop_started.set()
    if self.block_stop:
      self.stop_release.wait(2)
    self._force_stop_acquisition_sync()
    self.events.append("camera.stop.finish")

  async def _load_filters(self) -> None:
    self.events.append("filters.load")
    if self.camera_followup_failure == "filters":
      raise RuntimeError("filters failed")
    self._filters = []

  async def _load_objectives(self) -> None:
    self.events.append("objectives.load")
    if self.camera_followup_failure == "objectives":
      raise RuntimeError("objectives failed")
    self._objectives = []

  async def send_command(
    self,
    command: str,
    parameter: Optional[str] = None,
    wait_for_response: bool = True,
    timeout: Optional[float] = None,
  ) -> Optional[bytes]:
    del parameter, wait_for_response, timeout
    self.events.append(f"command:{command}")
    await anyio.lowlevel.checkpoint()
    return b"\x060000\x03" if command == "O" else b"\x03"


class RecordingAcquisitionCamera:
  """Synchronous PySpin acquisition double with controllable transitions."""

  def __init__(self, events: List[str]) -> None:
    self.events = events
    self.begin_error: Optional[BaseException] = None
    self.end_error: Optional[BaseException] = None
    self.begin_started = threading.Event()
    self.begin_release = threading.Event()
    self.end_started = threading.Event()
    self.end_release = threading.Event()
    self.block_begin = False
    self.block_end = False
    self.streaming = False

  def BeginAcquisition(self) -> None:
    self.events.append("acquisition.begin")
    self.streaming = True
    self.begin_started.set()
    if self.block_begin:
      self.begin_release.wait(2)
    if self.begin_error is not None:
      raise self.begin_error

  def EndAcquisition(self) -> None:
    self.events.append("acquisition.end")
    self.end_started.set()
    if self.block_end:
      self.end_release.wait(2)
    if self.end_error is not None:
      raise self.end_error
    self.streaming = False

  class ExposureValue:
    @staticmethod
    def GetValue() -> float:
      return 1000.0

  ExposureTime = ExposureValue()


class RecordingImageResult:
  """Finite PySpin image result recording release on every processing path."""

  def __init__(
    self,
    events: List[str],
    *,
    incomplete: bool = False,
    release_error: Optional[BaseException] = None,
  ) -> None:
    self.events = events
    self.incomplete = incomplete
    self.release_error = release_error

  def IsIncomplete(self) -> bool:
    self.events.append("image.incomplete")
    return self.incomplete

  def Release(self) -> None:
    self.events.append("image.release")
    if self.release_error is not None:
      raise self.release_error


class RecordingFramePySpin:
  """PySpin processing double for finite image-result ownership tests."""

  def __init__(self, events: List[str], failure_phase: Optional[str] = None) -> None:
    self.events = events
    self.failure_phase = failure_phase
    owner = self

    class ImageProcessor:
      def SetColorProcessing(self, algorithm: int) -> None:
        owner.events.append(f"processor.color:{algorithm}")
        if owner.failure_phase == "set_color":
          raise RuntimeError("set color failed")

      def Convert(self, image_result: object, pixel_format: int) -> object:
        del image_result
        owner.events.append(f"processor.convert:{pixel_format}")
        if owner.failure_phase == "convert":
          raise RuntimeError("convert failed")
        return RecordingConvertedImage(owner)

    self.ImageProcessor = ImageProcessor

  @staticmethod
  def CCommandPtr(node: object) -> object:
    return node

  @staticmethod
  def IsWritable(node: object) -> bool:
    del node
    return True


class RecordingConvertedImage:
  """Converted image double with controllable ndarray extraction."""

  def __init__(self, owner: RecordingFramePySpin) -> None:
    self.owner = owner

  def GetNDArray(self) -> str:
    self.owner.events.append("image.ndarray")
    if self.owner.failure_phase == "ndarray":
      raise RuntimeError("ndarray failed")
    return "frame"


class RecordingFrameCamera:
  """Camera double that returns one finite image result."""

  class Command:
    def __init__(self, events: List[str]) -> None:
      self.events = events

    def Execute(self) -> None:
      self.events.append("trigger.execute")

  class NodeMap:
    def __init__(self, events: List[str]) -> None:
      self.events = events

    def GetNode(self, name: str) -> "RecordingFrameCamera.Command":
      assert name == "TriggerSoftware"
      return RecordingFrameCamera.Command(self.events)

  def __init__(self, events: List[str], image_result: RecordingImageResult) -> None:
    self.events = events
    self.image_result = image_result
    self.ExposureTime = RecordingAcquisitionCamera.ExposureValue()

  def GetNodeMap(self) -> "RecordingFrameCamera.NodeMap":
    return RecordingFrameCamera.NodeMap(self.events)

  def GetNextImage(self, timeout: int) -> RecordingImageResult:
    assert timeout == 1001
    self.events.append("image.acquire")
    return self.image_result


def make_recording_cytation() -> tuple[RecordingCytation, RecordingFTDI]:
  events: List[str] = []
  backend = RecordingCytation(events)
  io = RecordingFTDI(events)
  backend.io = cast(FTDI, io)
  return backend, io


class PySpinBoundaryCytation(CytationBackend):
  """Cytation retaining the real PySpin helper with software configuration loading."""

  def __init__(self, events: List[str], failure: Optional[BaseException] = None) -> None:
    with unittest.mock.patch("pylabrobot.legacy.plate_reading.agilent.biotek_backend.FTDI"):
      super().__init__(timeout=0.1)
    self.events = events
    self.failure = failure
    self.fail_device_info = False
    self._filters = []
    self._objectives = []

  async def get_firmware_version(self) -> str:
    return "2.07"

  def _get_device_info(self, cam: object) -> dict[str, str]:
    del cam
    self.events.append("camera.device_info")
    if self.fail_device_info:
      assert self.failure is not None
      raise self.failure
    return {"DeviceSerialNumber": "camera-123"}

  async def _load_filters(self) -> None:
    self.events.append("filters.load")
    self._filters = []

  async def _load_objectives(self) -> None:
    self.events.append("objectives.load")
    self._objectives = []


class RecordingPySpin:
  """Complete software PySpin boundary used by Cytation lifespan tests."""

  def __init__(
    self,
    events: List[str],
    failure: Optional[BaseException] = None,
    fail_phase: Optional[str] = None,
    block_init: bool = False,
  ) -> None:
    self.events = events
    self.failure = failure
    self.fail_phase = fail_phase
    self.block_init = block_init
    self.init_started = threading.Event()
    self.init_release = threading.Event()
    self._failed_phase = False
    self.System = self.SystemFactory(self)

  def _fail(self, phase: str) -> None:
    if self.fail_phase == phase and not self._failed_phase:
      self._failed_phase = True
      assert self.failure is not None
      raise self.failure

  class Entry:
    def __init__(self, value: int) -> None:
      self.value = value

    def GetNumericValue(self) -> int:
      return self.value

    def GetValue(self) -> int:
      return self.value

  class Enumeration:
    def __init__(self, owner: "RecordingPySpin", node_name: str) -> None:
      self.owner = owner
      self.node_name = node_name

    def GetEntryByName(self, entry_name: str) -> "RecordingPySpin.Entry":
      self.owner.events.append(f"trigger.entry:{entry_name}")
      return RecordingPySpin.Entry({"FrameStart": 1, "Software": 2, "On": 3, "Off": 0}[entry_name])

    def SetIntValue(self, value: int) -> None:
      self.owner.events.append(f"trigger.set:{self.node_name}:{value}")
      phase = {
        "TriggerSelector": "trigger_selector",
        "TriggerSource": "trigger_source",
        "TriggerMode": "trigger_mode",
      }[self.node_name]
      self.owner._fail(phase)

  class NodeMap:
    def __init__(self, owner: "RecordingPySpin") -> None:
      self.owner = owner

    def GetNode(self, name: str) -> "RecordingPySpin.Enumeration":
      self.owner.events.append(f"node:{name}")
      return RecordingPySpin.Enumeration(self.owner, name)

  class Camera:
    def __init__(self, owner: "RecordingPySpin") -> None:
      self.owner = owner

    def Init(self) -> None:
      self.owner.events.append("camera.init")
      if self.owner.block_init:
        self.owner.init_started.set()
        self.owner.init_release.wait(2)
        self.owner.block_init = False
      if self.owner.fail_phase == "initialization":
        assert self.owner.failure is not None
        raise self.owner.failure

    def GetNodeMap(self) -> "RecordingPySpin.NodeMap":
      self.owner.events.append("camera.node_map")
      self.owner._fail("node_map")
      return RecordingPySpin.NodeMap(self.owner)

    def DeInit(self) -> None:
      self.owner.events.append("camera.deinit")

  class CameraList:
    def __init__(self, owner: "RecordingPySpin", camera: "RecordingPySpin.Camera") -> None:
      self.owner = owner
      self.camera = camera

    def __iter__(self) -> Iterator["RecordingPySpin.Camera"]:
      return iter([self.camera])

    def GetSize(self) -> int:
      self.owner.events.append("camera_list.size")
      self.owner._fail("camera_count")
      return 1

    def GetByIndex(self, index: int) -> "RecordingPySpin.Camera":
      assert index == 0
      self.owner.events.append("camera_list.select")
      self.owner._fail("selection")
      return self.camera

    def Clear(self) -> None:
      self.owner.events.append("camera_list.clear")

  class SystemInstance:
    def __init__(self, owner: "RecordingPySpin") -> None:
      self.owner = owner
      self.camera = RecordingPySpin.Camera(owner)

    def GetLibraryVersion(self) -> object:
      self.owner.events.append("system.version")
      self.owner._fail("library_version")
      return type("Version", (), {"major": 1, "minor": 2, "type": 3, "build": 4})()

    def GetCameras(self) -> "RecordingPySpin.CameraList":
      self.owner.events.append("system.cameras")
      self.owner._fail("enumeration")
      return RecordingPySpin.CameraList(self.owner, self.camera)

    def ReleaseInstance(self) -> None:
      self.owner.events.append("system.release")

  class SystemFactory:
    def __init__(self, owner: "RecordingPySpin") -> None:
      self.owner = owner

    def GetInstance(self) -> "RecordingPySpin.SystemInstance":
      self.owner.events.append("system.acquire")
      return RecordingPySpin.SystemInstance(self.owner)

  @staticmethod
  def CEnumerationPtr(node: object) -> object:
    return node

  @staticmethod
  def CEnumEntryPtr(node: object) -> object:
    return node

  @staticmethod
  def IsReadable(node: object) -> bool:
    del node
    return True

  @staticmethod
  def IsWritable(node: object) -> bool:
    del node
    return True


def make_pyspin_boundary_cytation(
  fail_phase: Optional[str] = None, *, block_init: bool = False
) -> tuple[PySpinBoundaryCytation, RecordingFTDI, RecordingPySpin, Optional[BaseException]]:
  events: List[str] = []
  failure = RuntimeError(f"{fail_phase} failed") if fail_phase is not None else None
  backend = PySpinBoundaryCytation(events, failure=failure)
  backend.fail_device_info = fail_phase == "device_info"
  io = RecordingFTDI(events)
  backend.io = cast(FTDI, io)
  pyspin = RecordingPySpin(events, failure=failure, fail_phase=fail_phase, block_init=block_init)
  return backend, io, pyspin, failure


class TestCytationLifecycle(AnyioTestBase):
  @pytest.mark.parametrize(
    ("phase", "cleanup"),
    [
      ("library_version", ["system.release", "ftdi.exit"]),
      ("enumeration", ["system.release", "ftdi.exit"]),
      ("camera_count", ["camera_list.clear", "system.release", "ftdi.exit"]),
      ("device_info", ["camera_list.clear", "system.release", "ftdi.exit"]),
      ("selection", ["camera_list.clear", "system.release", "ftdi.exit"]),
      ("initialization", ["system.release", "ftdi.exit"]),
      ("node_map", ["camera.deinit", "system.release", "ftdi.exit"]),
      ("trigger_selector", ["camera.deinit", "system.release", "ftdi.exit"]),
      ("trigger_source", ["camera.deinit", "system.release", "ftdi.exit"]),
      ("trigger_mode", ["camera.deinit", "system.release", "ftdi.exit"]),
    ],
  )
  async def test_each_pyspin_acquisition_failure_unwinds_inside_ftdi(
    self, phase: str, cleanup: List[str]
  ) -> None:
    backend, io, pyspin, failure = make_pyspin_boundary_cytation(phase)
    assert failure is not None
    with (
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.USE_PYSPIN", True
      ),
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin",
        pyspin,
        create=True,
      ),
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.time.sleep"
      ),
      pytest.raises(RuntimeError) as error_info,
    ):
      async with backend.lifespan(use_cam=True):
        pytest.fail("failed camera entry reached body")

    if phase == "initialization":
      assert str(error_info.value) == (
        "Failed to initialize camera. Make sure the camera is connected and the "
        "Spinnaker SDK is installed correctly."
      )
    else:
      assert error_info.value is failure
    assert io.events[-len(cleanup) :] == cleanup
    assert backend._cam is None
    assert backend._spinnaker_system is None

  async def test_cancellation_during_camera_initialization_waits_for_reverse_cleanup(self) -> None:
    backend, io, pyspin, _ = make_pyspin_boundary_cytation(block_init=True)
    scopes: List[anyio.CancelScope] = []
    finished = anyio.Event()

    async def enter() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with backend.lifespan(use_cam=True):
            pytest.fail("cancelled camera entry reached body")
      finally:
        finished.set()

    with (
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.USE_PYSPIN", True
      ),
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin",
        pyspin,
        create=True,
      ),
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.time.sleep"
      ),
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter)
        assert await anyio.to_thread.run_sync(pyspin.init_started.wait, 1)
        scopes[0].cancel()
        await anyio.sleep(0.01)
        assert not finished.is_set()
        assert "camera.deinit" not in io.events
        assert "ftdi.exit" not in io.events
        pyspin.init_release.set()

    assert io.events[-3:] == ["camera.deinit", "system.release", "ftdi.exit"]

  async def test_camera_enabled_lifespan_supports_sequential_reuse(self) -> None:
    backend, io, pyspin, _ = make_pyspin_boundary_cytation()
    with (
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.USE_PYSPIN", True
      ),
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin",
        pyspin,
        create=True,
      ),
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.time.sleep"
      ),
    ):
      for _ in range(2):
        async with backend.lifespan(use_cam=True):
          assert backend._cam is not None
          assert backend._spinnaker_system is not None
        assert backend._cam is None
        assert backend._spinnaker_system is None

    assert io.events.count("system.acquire") == 2
    assert io.events.count("camera.init") == 2
    assert io.events.count("camera.deinit") == 2
    assert io.events.count("system.release") == 2
    assert io.events.count("ftdi.exit") == 2

  def test_library_version_failure_releases_acquired_pyspin_system(self) -> None:
    events: List[str] = []

    class RecordingSystem:
      def GetLibraryVersion(self) -> None:
        raise RuntimeError("version failed")

      def ReleaseInstance(self) -> None:
        events.append("system.release")

    system = RecordingSystem()

    class SystemFactory:
      @staticmethod
      def GetInstance() -> RecordingSystem:
        return system

    class RecordingPySpin:
      System = SystemFactory

    backend, _ = make_lifecycle_backend(CytationBackend)
    assert isinstance(backend, CytationBackend)
    with (
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.USE_PYSPIN", True
      ),
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin",
        RecordingPySpin,
        create=True,
      ),
      pytest.raises(RuntimeError, match="version failed"),
    ):
      backend._set_up_camera_sync()
    assert events == ["system.release"]

  async def test_use_cam_is_the_only_keyword_lifespan_option(self) -> None:
    backend, io = make_recording_cytation()
    async with backend.lifespan(use_cam=False):
      pass
    assert "camera.setup.start" not in io.events

    with pytest.raises(TypeError):
      async with backend.lifespan(use_cam=False, unknown=True):
        pytest.fail("unknown option reached body")
    assert io.events.count("ftdi.enter") == 1

  async def test_camera_setup_and_teardown_are_ordered_inside_ftdi(self) -> None:
    backend, io = make_recording_cytation()
    async with backend.lifespan(use_cam=True):
      io.events.append("body")
    assert io.events[-6:] == [
      "camera.setup.start",
      "camera.setup.finish",
      "body",
      "camera.stop.start",
      "camera.stop.finish",
      "ftdi.exit",
    ]

  async def test_cancelled_camera_setup_waits_for_worker_then_closes_ftdi(self) -> None:
    backend, io = make_recording_cytation()
    backend.block_setup = True
    scopes: List[anyio.CancelScope] = []
    finished = anyio.Event()

    async def enter() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with backend.lifespan(use_cam=True):
            pytest.fail("cancelled setup reached body")
      finally:
        finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      assert await anyio.to_thread.run_sync(backend.setup_started.wait, 1)
      scopes[0].cancel()
      await anyio.sleep(0.01)
      assert not finished.is_set()
      assert "ftdi.exit" not in io.events
      backend.setup_release.set()

    assert io.events[-4:] == [
      "camera.setup.finish",
      "camera.stop.start",
      "camera.stop.finish",
      "ftdi.exit",
    ]

  async def test_cancelled_camera_teardown_finishes_before_ftdi_closes(self) -> None:
    backend, io = make_recording_cytation()
    backend.block_stop = True
    scopes: List[anyio.CancelScope] = []
    body_entered = anyio.Event()
    leave_body = anyio.Event()

    async def use_backend() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        async with backend.lifespan(use_cam=True):
          body_entered.set()
          await leave_body.wait()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_backend)
      await body_entered.wait()
      leave_body.set()
      assert await anyio.to_thread.run_sync(backend.stop_started.wait, 1)
      scopes[0].cancel()
      await anyio.sleep(0.01)
      assert "ftdi.exit" not in io.events
      backend.stop_release.set()

    assert io.events[-2:] == ["camera.stop.finish", "ftdi.exit"]

  @pytest.mark.parametrize("phase", ["filters", "objectives"])
  async def test_camera_followup_failure_stops_camera_then_closes_ftdi(self, phase: str) -> None:
    backend, io = make_recording_cytation()
    backend.camera_followup_failure = phase
    backend._filters = None
    backend._objectives = None if phase == "objectives" else []

    with pytest.raises(RuntimeError, match=f"{phase} failed"):
      async with backend.lifespan(use_cam=True):
        pytest.fail("failed camera entry reached body")

    assert io.events[-3:] == ["camera.stop.start", "camera.stop.finish", "ftdi.exit"]

  async def test_camera_deinit_failure_still_releases_pyspin_system(self) -> None:
    events: List[str] = []

    class FailingCamera:
      def DeInit(self) -> None:
        events.append("camera.deinit")
        raise RuntimeError("deinit failed")

    class RecordingSystem:
      def ReleaseInstance(self) -> None:
        events.append("system.release")

    backend, _ = make_lifecycle_backend(CytationBackend)
    assert isinstance(backend, CytationBackend)
    backend._reset_camera_trigger = lambda cam: None  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="deinit failed"):
      await backend._stop_camera_worker(FailingCamera(), RecordingSystem())
    assert events == ["camera.deinit", "system.release"]


class TestCytationAcquisition(AnyioTestBase):
  @pytest.mark.parametrize("phase", ["incomplete", "set_color", "convert", "ndarray"])
  async def test_every_acquired_image_result_is_released(self, phase: str) -> None:
    backend, _ = make_recording_cytation()
    events: List[str] = []
    result = RecordingImageResult(events, incomplete=phase == "incomplete")
    backend._cam = RecordingFrameCamera(events, result)  # type: ignore[assignment]
    pyspin = RecordingFramePySpin(events, failure_phase=phase)

    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin",
      pyspin,
      create=True,
    ):
      if phase == "incomplete":
        assert backend._acquire_image_once_sync(7, 8) is None
      else:
        with pytest.raises(RuntimeError, match=f"{phase.replace('_', ' ')} failed"):
          backend._acquire_image_once_sync(7, 8)

    assert events.count("image.release") == 1

  async def test_release_failure_overrides_conversion_with_primary_as_context(self) -> None:
    backend, _ = make_recording_cytation()
    events: List[str] = []
    release_error = RuntimeError("release failed")
    result = RecordingImageResult(events, release_error=release_error)
    backend._cam = RecordingFrameCamera(events, result)  # type: ignore[assignment]
    pyspin = RecordingFramePySpin(events, failure_phase="convert")

    with (
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin",
        pyspin,
        create=True,
      ),
      pytest.raises(RuntimeError) as error_info,
    ):
      backend._acquire_image_once_sync(7, 8)

    assert error_info.value is release_error
    assert isinstance(error_info.value.__context__, RuntimeError)
    assert str(error_info.value.__context__) == "convert failed"

  async def test_release_failure_replaces_successful_frame_result(self) -> None:
    backend, _ = make_recording_cytation()
    events: List[str] = []
    release_error = RuntimeError("release failed")
    result = RecordingImageResult(events, release_error=release_error)
    backend._cam = RecordingFrameCamera(events, result)  # type: ignore[assignment]
    pyspin = RecordingFramePySpin(events)

    with (
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin",
        pyspin,
        create=True,
      ),
      pytest.raises(RuntimeError) as error_info,
    ):
      backend._acquire_image_once_sync(7, 8)

    assert error_info.value is release_error

  async def test_set_position_uses_backend_neutral_delay(self) -> None:
    backend, _ = make_recording_cytation()
    backend._imaging_mode = ImagingMode.BRIGHTFIELD
    backend._objective = Objective.O_4X_PL_FL
    backend._objectives = [Objective.O_4X_PL_FL]
    backend._row = 1
    backend._column = 1

    await backend.set_position(0.1, 0)

    assert backend._pos_x == 0.1
    assert "command:Y" in backend.events

  async def test_scope_owns_begin_end_and_supports_sequential_reuse(self) -> None:
    backend, io = make_recording_cytation()
    async with backend.lifespan(use_cam=True):
      for _ in range(2):
        async with backend.acquisition():
          assert backend._acquiring
        assert not backend._acquiring

    assert io.events.count("acquisition.begin") == 2
    assert io.events.count("acquisition.end") == 2

  async def test_camera_disabled_scope_rejects_before_token_or_transition(self) -> None:
    backend, io = make_recording_cytation()
    async with backend.lifespan(use_cam=False):
      with pytest.raises(RuntimeError, match="active configured camera"):
        async with backend.acquisition():
          pytest.fail("camera-disabled scope reached body")
      assert backend._acquisition_scope_token is None
    assert "acquisition.begin" not in io.events

  async def test_direct_transitions_are_guarded_during_scope(self) -> None:
    backend, _ = make_recording_cytation()
    async with backend.lifespan(use_cam=True):
      async with backend.acquisition():
        with pytest.raises(RuntimeError, match="acquisition scope"):
          backend.start_acquisition()
        with pytest.raises(RuntimeError, match="acquisition scope"):
          backend.stop_acquisition()

  async def test_begin_and_end_failures_preserve_state(self) -> None:
    backend, _ = make_recording_cytation()
    camera = backend.camera
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.time.sleep"
    ):
      async with backend.lifespan(use_cam=True):
        begin_error = RuntimeError("begin failed")
        camera.begin_error = begin_error
        with pytest.raises(RuntimeError) as error_info:
          async with backend.acquisition():
            pytest.fail("failed Begin reached body")
        assert error_info.value.__cause__ is begin_error
        assert backend._acquisition_scope_token is None
        assert not backend._acquiring
        assert not camera.streaming
        assert backend.events.count("acquisition.end") == 1

        camera.begin_error = None
        camera.end_error = RuntimeError("end failed")
        with pytest.raises(RuntimeError, match="Failed after 10 tries"):
          async with backend.acquisition():
            pass
        assert backend._acquisition_scope_token is None
        assert backend._acquiring
        assert backend._acquisition_state_unknown
        assert camera.streaming
        camera.end_error = None
        backend.stop_acquisition()
        assert not backend._acquiring
        assert not backend._acquisition_state_unknown
        assert not camera.streaming

  async def test_failed_begin_rollback_retains_unknown_for_parent_retry(self) -> None:
    backend, _ = make_recording_cytation()
    camera = backend.camera
    camera.begin_error = RuntimeError("begin failed after commit")
    camera.end_error = RuntimeError("rollback failed")
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.time.sleep"
    ):
      async with backend.lifespan(use_cam=True):
        with pytest.raises(RuntimeError, match="Failed after 10 tries") as error_info:
          async with backend.acquisition():
            pytest.fail("failed Begin reached scope body")
        assert str(error_info.value.__context__) == "Failed after 10 tries"
        assert backend._acquisition_state_unknown
        assert camera.streaming
        camera.begin_error = None
        camera.end_error = None

      assert not camera.streaming
      assert backend.events.count("acquisition.end") == 11

      async with backend.lifespan(use_cam=True):
        async with backend.acquisition():
          assert camera.streaming

  async def test_cancelled_scope_contender_does_not_outlive_thread_lock_wait(self) -> None:
    backend, _ = make_recording_cytation()
    camera = backend.camera
    camera.block_begin = True
    contender_started = anyio.Event()
    contender_finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def contend() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          contender_started.set()
          async with backend.acquisition():
            pytest.fail("cancelled contender reached scope body")
      finally:
        contender_finished.set()

    async with backend.lifespan(use_cam=True):
      try:
        async with anyio.create_task_group() as task_group:
          task_group.start_soon(anyio.to_thread.run_sync, backend.start_acquisition)
          assert await anyio.to_thread.run_sync(camera.begin_started.wait, 1)
          task_group.start_soon(contend)
          await contender_started.wait()
          await anyio.sleep(0.02)
          scopes[0].cancel()
          await anyio.sleep(0.02)
          assert contender_finished.is_set()
          assert backend.events.count("acquisition.begin") == 1
      finally:
        camera.begin_release.set()
      backend.stop_acquisition()

  async def test_cancelled_scope_before_worker_dispatch_does_not_touch_camera(self) -> None:
    backend, _ = make_recording_cytation()
    worker_started = anyio.Event()
    worker_release = threading.Event()
    entry_started = anyio.Event()
    entry_finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    def occupy_worker() -> None:
      anyio.from_thread.run_sync(worker_started.set)
      worker_release.wait()

    async def enter_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          entry_started.set()
          async with backend.acquisition():
            pytest.fail("cancelled pre-dispatch entry reached scope body")
      finally:
        entry_finished.set()

    limiter = anyio.to_thread.current_default_thread_limiter()
    original_tokens = limiter.total_tokens
    limiter.total_tokens = 1
    try:
      async with backend.lifespan(use_cam=True):
        async with anyio.create_task_group() as task_group:
          try:
            task_group.start_soon(anyio.to_thread.run_sync, occupy_worker)
            await worker_started.wait()
            task_group.start_soon(enter_scope)
            await entry_started.wait()
            await anyio.wait_all_tasks_blocked()
            scopes[0].cancel()
            with anyio.move_on_after(1):
              await entry_finished.wait()
            returned_before_worker_release = entry_finished.is_set()
          finally:
            worker_release.set()

        assert returned_before_worker_release
        assert "acquisition.begin" not in backend.events
        assert "acquisition.end" not in backend.events
        assert backend._acquisition_scope_token is None
        assert not backend._acquisition_state_unknown
        assert not backend._acquiring
    finally:
      limiter.total_tokens = original_tokens

  async def test_scope_cancellation_waits_for_begin_and_end_workers(self) -> None:
    backend, _ = make_recording_cytation()
    camera = backend.camera
    camera.block_begin = True
    finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def enter_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with backend.acquisition():
            pytest.fail("cancelled Begin reached body")
      finally:
        finished.set()

    async with backend.lifespan(use_cam=True):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(enter_scope)
        assert await anyio.to_thread.run_sync(camera.begin_started.wait, 1)
        scopes[0].cancel()
        await anyio.sleep(0.01)
        assert not finished.is_set()
        camera.begin_release.set()
        await finished.wait()
      assert backend.events.count("acquisition.end") == 1
      assert not backend._acquiring

  async def test_scope_cancellation_waits_for_blocked_end_worker(self) -> None:
    backend, _ = make_recording_cytation()
    camera = backend.camera
    camera.block_end = True
    body_finished = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def use_scope() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          async with backend.acquisition():
            pass
      finally:
        body_finished.set()

    async with backend.lifespan(use_cam=True):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_scope)
        assert await anyio.to_thread.run_sync(camera.end_started.wait, 1)
        scopes[0].cancel()
        await anyio.sleep(0.01)
        assert not body_finished.is_set()
        camera.end_release.set()
        await body_finished.wait()
      assert not backend._acquiring
      assert backend._acquisition_scope_token is None

  async def test_concurrent_direct_start_calls_execute_one_begin(self) -> None:
    backend, _ = make_recording_cytation()
    camera = backend.camera
    camera.block_begin = True

    async with backend.lifespan(use_cam=True):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(anyio.to_thread.run_sync, backend.start_acquisition)
        assert await anyio.to_thread.run_sync(camera.begin_started.wait, 1)
        task_group.start_soon(anyio.to_thread.run_sync, backend.start_acquisition)
        await anyio.sleep(0.01)
        assert backend.events.count("acquisition.begin") == 1
        camera.begin_release.set()
      assert backend.events.count("acquisition.begin") == 1

  async def test_capture_borrows_scope_without_auto_stopping(self) -> None:
    backend, _ = make_recording_cytation()
    backend._objective = Objective.O_4X_PL_FL
    backend._objectives = [Objective.O_4X_PL_FL]
    backend._focal_height = 1.0
    backend._imaging_mode = ImagingMode.BRIGHTFIELD
    backend._row = 1
    backend._column = 1
    backend.set_plate = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    backend.set_objective = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    backend.set_imaging_mode = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    backend.select = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    backend.set_exposure = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    backend.set_gain = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    backend.set_focus = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    backend.led_off = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    backend._acquire_image = unittest.mock.AsyncMock(return_value="frame")  # type: ignore[method-assign]
    plate = cellvis_24_wellplate_3600uL_Fb(name="plate")

    async with backend.lifespan(use_cam=True):
      async with backend.acquisition():
        result = await backend.capture(
          1,
          1,
          ImagingMode.BRIGHTFIELD,
          Objective.O_4X_PL_FL,
          1.0,
          1.0,
          1.0,
          plate,
          auto_stop_acquisition=True,
        )
        assert backend._acquiring
        assert result.images == ["frame"]
    assert backend.events.count("acquisition.end") == 1

  async def test_scoped_frame_retry_stays_in_one_acquisition(self) -> None:
    backend, _ = make_recording_cytation()
    backend.imaging_config.max_image_read_attempts = 2
    attempts = 0
    worker_threads: List[int] = []
    caller_thread = threading.get_ident()

    def acquire_once(color_processing_algorithm: int, pixel_format: int) -> str:
      nonlocal attempts
      del color_processing_algorithm, pixel_format
      worker_threads.append(threading.get_ident())
      attempts += 1
      if attempts == 1:
        raise RuntimeError("frame failed")
      return "frame"

    backend._acquire_image_once_sync = acquire_once  # type: ignore[method-assign,assignment]
    async with backend.lifespan(use_cam=True):
      async with backend.acquisition():
        assert await backend._acquire_image() == "frame"
        assert backend.events.count("acquisition.begin") == 1
        assert backend.events.count("acquisition.end") == 0

    assert attempts == 2
    assert all(thread_id != caller_thread for thread_id in worker_threads)
    assert backend.events.count("acquisition.end") == 1

  async def test_retry_delay_is_cancellable_on_supported_backend(self) -> None:
    backend, _ = make_recording_cytation()
    backend.imaging_config.max_image_read_attempts = 2
    attempts = 0

    def fail_frame(color_processing_algorithm: int, pixel_format: int) -> None:
      nonlocal attempts
      del color_processing_algorithm, pixel_format
      attempts += 1
      raise RuntimeError("frame failed")

    backend._acquire_image_once_sync = fail_frame  # type: ignore[method-assign]
    async with backend.lifespan(use_cam=True):
      async with backend.acquisition():
        with anyio.move_on_after(0.05) as cancel_scope:
          await backend._acquire_image()
        assert cancel_scope.cancel_called

    assert attempts == 1

  async def test_parent_cleanup_stops_shaking_and_acquisition(self) -> None:
    backend, io = make_recording_cytation()
    lifespan = backend.lifespan(use_cam=True)
    await lifespan.__aenter__()
    shaking = backend.shaking(backend.ShakeType.ORBITAL, 2)
    acquisition = backend.acquisition()
    await shaking.__aenter__()
    await acquisition.__aenter__()

    await lifespan.__aexit__(None, None, None)
    assert "command:x" in io.events
    assert "acquisition.end" in io.events
    assert io.events.index("acquisition.end") < io.events.index("ftdi.exit")
    assert backend._shaking_scope_token is None
    assert backend._acquisition_scope_token is None
    await acquisition.__aexit__(None, None, None)
    await shaking.__aexit__(None, None, None)


class BlockingSDKCall:
  """Controllable synchronous SDK call recording thread and completion order."""

  def __init__(self, events: List[str], name: str) -> None:
    self.events = events
    self.name = name
    self.started = threading.Event()
    self.release = threading.Event()
    self.block = False
    self.thread_ids: List[int] = []

  def __call__(self, *args: object) -> None:
    del args
    self.thread_ids.append(threading.get_ident())
    self.events.append(f"{self.name}.start")
    self.started.set()
    if self.block:
      self.release.wait(1)
    self.events.append(f"{self.name}.finish")


class WorkerBoundaryNode:
  """Typed numeric/enum node exposing the PySpin methods used by Cytation."""

  def __init__(
    self, call: BlockingSDKCall, value: float = 1000.0, get_call: Optional[BlockingSDKCall] = None
  ) -> None:
    self.call = call
    self.get_call = get_call or call
    self.value = value

  def GetAccessMode(self) -> int:
    return 1

  def SetValue(self, value: object) -> None:
    self.call(value)
    if isinstance(value, (int, float)):
      self.value = float(value)

  def SetIntValue(self, value: int) -> None:
    self.call(value)

  def GetValue(self) -> float:
    self.get_call()
    return self.value

  def GetMin(self) -> float:
    return 1.0

  def GetMax(self) -> float:
    return 30_000.0

  def GetEntryByName(self, name: str) -> "WorkerBoundaryNode":
    del name
    return self


class WorkerBoundaryNodeMap:
  """Node map supplying gain nodes to the real Cytation setter."""

  def __init__(self, gain_auto: WorkerBoundaryNode, gain: WorkerBoundaryNode) -> None:
    self.gain_auto = gain_auto
    self.gain = gain

  def GetNode(self, name: str) -> WorkerBoundaryNode:
    return {"GainAuto": self.gain_auto, "Gain": self.gain}[name]


class WorkerBoundaryCamera:
  """Camera double covering every SDK call reachable from the async paths."""

  def __init__(self, events: List[str]) -> None:
    self.events = events
    self.begin = BlockingSDKCall(events, "begin")
    self.end = BlockingSDKCall(events, "end")
    self.exposure_auto_call = BlockingSDKCall(events, "exposure_auto")
    self.exposure_call = BlockingSDKCall(events, "exposure")
    self.exposure_get = BlockingSDKCall(events, "exposure_get")
    self.gain_auto_call = BlockingSDKCall(events, "gain_auto")
    self.gain_call = BlockingSDKCall(events, "gain")
    self.ExposureAuto = WorkerBoundaryNode(self.exposure_auto_call)
    self.ExposureTime = WorkerBoundaryNode(self.exposure_call, get_call=self.exposure_get)
    self.gain_auto = WorkerBoundaryNode(self.gain_auto_call)
    self.gain = WorkerBoundaryNode(self.gain_call)
    self.node_map = WorkerBoundaryNodeMap(self.gain_auto, self.gain)
    self.streaming = False
    self.begin_error: Optional[Exception] = None

  def BeginAcquisition(self) -> None:
    self.streaming = True
    self.begin()
    if self.begin_error is not None:
      raise self.begin_error

  def EndAcquisition(self) -> None:
    self.end()
    self.streaming = False

  def GetNodeMap(self) -> WorkerBoundaryNodeMap:
    return self.node_map


class WorkerBoundaryPySpin:
  """Minimal PySpin namespace for setter boundary tests."""

  RW = 1
  ExposureAuto_Off = 0
  ExposureAuto_Once = 1
  ExposureAuto_Continuous = 2

  @staticmethod
  def CEnumerationPtr(node: object) -> object:
    return node

  @staticmethod
  def CEnumEntryPtr(node: object) -> object:
    return node

  @staticmethod
  def CFloatPtr(node: object) -> object:
    return node

  @staticmethod
  def IsReadable(node: object) -> bool:
    del node
    return True

  @staticmethod
  def IsWritable(node: object) -> bool:
    del node
    return True


class WorkerBoundaryCytation(CytationBackend):
  """Cytation retaining real camera control while stubbing protocol positioning."""

  def __init__(self, events: List[str]) -> None:
    with unittest.mock.patch("pylabrobot.legacy.plate_reading.agilent.biotek_backend.FTDI"):
      super().__init__(timeout=0.1)
    self.events = events
    self.camera = WorkerBoundaryCamera(events)
    self._cam = self.camera  # type: ignore[assignment]
    self._objective = Objective.O_4X_PL_FL
    self._objectives = [Objective.O_4X_PL_FL]
    self._imaging_mode = ImagingMode.BRIGHTFIELD
    self._focal_height = 1.0

  async def set_plate(self, plate: Plate) -> None:
    del plate
    await anyio.lowlevel.checkpoint()

  async def set_objective(self, objective: Objective) -> None:
    self._objective = objective
    await anyio.lowlevel.checkpoint()

  async def set_imaging_mode(self, mode: ImagingMode, led_intensity: int) -> None:
    del led_intensity
    self._imaging_mode = mode
    await anyio.lowlevel.checkpoint()

  async def select(self, row: int, column: int) -> None:
    self._row, self._column = row, column
    await anyio.lowlevel.checkpoint()

  async def set_position(self, x: float, y: float) -> None:
    self._pos_x, self._pos_y = x, y
    await anyio.lowlevel.checkpoint()

  async def set_focus(self, focal_position: Exposure) -> None:
    assert focal_position != "machine-auto"
    self._focal_height = focal_position
    await anyio.lowlevel.checkpoint()

  async def led_off(self) -> None:
    self.events.append("cleanup")
    await anyio.lowlevel.checkpoint()

  async def _acquire_image(
    self, color_processing_algorithm: int = 0, pixel_format: int = 0
  ) -> Image:
    del color_processing_algorithm, pixel_format
    await anyio.lowlevel.checkpoint()
    return cast(Image, "frame")


def make_worker_boundary_cytation() -> tuple[WorkerBoundaryCytation, WorkerBoundaryPySpin]:
  """Construct the software-only Cytation and PySpin namespace."""
  events: List[str] = []
  return WorkerBoundaryCytation(events), WorkerBoundaryPySpin()


class TestCytationWorkerBoundaries(AnyioTestBase):
  async def _assert_off_loop(
    self, operation: Callable[[], Coroutine[Any, Any, object]], call: BlockingSDKCall
  ) -> None:
    caller_thread = threading.get_ident()
    call.block = True
    watchdog = threading.Timer(0.5, call.release.set)
    watchdog.start()

    async def prove_progress() -> None:
      assert await anyio.to_thread.run_sync(call.started.wait, 0.4), call.events
      call.events.append("sibling")
      call.release.set()

    try:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(operation)
        task_group.start_soon(prove_progress)
    finally:
      call.release.set()
      watchdog.cancel()
    assert call.thread_ids and all(thread_id != caller_thread for thread_id in call.thread_ids)
    assert call.events.index("sibling") < call.events.index(f"{call.name}.finish")

  async def _capture(self, backend: WorkerBoundaryCytation, auto_stop: bool = True) -> object:
    return await backend.capture(
      1,
      1,
      ImagingMode.BRIGHTFIELD,
      Objective.O_4X_PL_FL,
      1.0,
      1.0,
      1.0,
      cellvis_24_wellplate_3600uL_Fb(name="plate"),
      auto_stop_acquisition=auto_stop,
    )

  async def test_set_auto_exposure_runs_pyspin_off_loop(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      await self._assert_off_loop(
        lambda: backend.set_auto_exposure("continuous"), backend.camera.exposure_auto_call
      )

  async def test_set_exposure_runs_pyspin_off_loop(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      await self._assert_off_loop(lambda: backend.set_exposure(1.0), backend.camera.exposure_call)

  async def test_set_gain_runs_pyspin_off_loop(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      await self._assert_off_loop(lambda: backend.set_gain(1.0), backend.camera.gain_call)

  async def test_cancelled_sdk_mutation_waits_for_worker_before_camera_cleanup(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    call = backend.camera.gain_call
    call.block = True
    scopes: List[anyio.CancelScope] = []
    finished = anyio.Event()

    async def mutate() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await backend.set_gain(1.0)
      finally:
        backend.events.append("camera.cleanup")
        finished.set()

    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(mutate)
        assert await anyio.to_thread.run_sync(call.started.wait, 1)
        scopes[0].cancel()
        await anyio.wait_all_tasks_blocked()
        assert not finished.is_set()
        call.release.set()
    assert backend.events.index("gain.finish") < backend.events.index("camera.cleanup")
    assert backend._gain == 1.0

  async def test_capture_start_acquisition_runs_pyspin_off_loop(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      await self._assert_off_loop(lambda: self._capture(backend), backend.camera.begin)

  async def test_capture_retry_restart_runs_pyspin_off_loop(self) -> None:
    backend, _ = make_worker_boundary_cytation()
    backend._acquiring = True
    backend.imaging_config.max_image_read_attempts = 2
    attempts = 0

    class RetryableError(Exception):
      pass

    def acquire_once(color_processing_algorithm: int, pixel_format: int) -> str:
      nonlocal attempts
      del color_processing_algorithm, pixel_format
      attempts += 1
      backend.events.append(f"frame.attempt:{attempts}")
      if attempts == 1:
        raise RetryableError("retry")
      return "frame"

    backend._acquire_image_once_sync = acquire_once  # type: ignore[method-assign,assignment]
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.SpinnakerException",
      RetryableError,
    ):
      await self._assert_off_loop(
        lambda: CytationBackend._acquire_image(backend), backend.camera.end
      )
    assert backend.camera.begin.thread_ids == backend.camera.end.thread_ids

  async def test_failed_retry_begin_rolls_back_when_auto_stop_is_disabled(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    backend.imaging_config.max_image_read_attempts = 2

    class RetryableError(Exception):
      pass

    def fail_frame(color_processing_algorithm: int, pixel_format: int) -> None:
      del color_processing_algorithm, pixel_format
      backend.camera.begin_error = RetryableError("begin failed after activating stream")
      raise RetryableError("frame failed")

    backend._acquire_image_once_sync = fail_frame  # type: ignore[method-assign]
    backend._acquire_image = CytationBackend._acquire_image.__get__(backend)  # type: ignore[method-assign]
    with (
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin",
        pyspin,
        create=True,
      ),
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.SpinnakerException",
        RetryableError,
      ),
      unittest.mock.patch(
        "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.time.sleep"
      ),
    ):
      with pytest.raises(RuntimeError, match="Failed after 10 tries"):
        await self._capture(backend, auto_stop=False)

    assert backend.events.count("begin.start") == 11
    assert backend.events.count("end.start") == 2
    assert not backend.camera.streaming
    assert not backend._acquiring
    assert not backend._acquisition_state_unknown
    assert len(set(backend.camera.begin.thread_ids + backend.camera.end.thread_ids)) == 1

  async def test_capture_stop_acquisition_runs_pyspin_off_loop(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      await self._assert_off_loop(lambda: self._capture(backend), backend.camera.end)

  async def test_capture_exposure_get_value_runs_pyspin_off_loop(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      await self._assert_off_loop(lambda: self._capture(backend), backend.camera.exposure_get)

  async def test_cancelled_capture_waits_for_start_worker_before_cleanup(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    call = backend.camera.begin
    call.block = True
    scopes: List[anyio.CancelScope] = []
    finished = anyio.Event()

    async def capture() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await self._capture(backend)
      finally:
        finished.set()

    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(capture)
        assert await anyio.to_thread.run_sync(call.started.wait, 1)
        scopes[0].cancel()
        await anyio.wait_all_tasks_blocked()
        assert not finished.is_set()
        assert "cleanup" not in backend.events
        call.release.set()
    assert backend.events.index("begin.finish") < backend.events.index("cleanup")

  async def test_cancelled_capture_waits_for_stop_worker_before_propagating(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    call = backend.camera.end
    call.block = True
    scopes: List[anyio.CancelScope] = []
    finished = anyio.Event()

    async def capture() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await self._capture(backend)
      finally:
        backend.events.append("cancel.propagated")
        finished.set()

    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(capture)
        assert await anyio.to_thread.run_sync(call.started.wait, 1)
        scopes[0].cancel()
        await anyio.wait_all_tasks_blocked()
        assert not finished.is_set()
        call.release.set()
    assert backend.events.index("end.finish") < backend.events.index("cancel.propagated")

  async def test_capture_auto_stop_false_leaves_acquisition_running(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      result = await self._capture(backend, auto_stop=False)
    assert result.images == ["frame"]
    assert backend._acquiring
    assert backend.events.count("end.start") == 0

  async def test_capture_auto_stop_true_stops_before_return(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      result = await self._capture(backend)
      backend.events.append("capture.return")
    assert result.images == ["frame"]
    assert not backend._acquiring
    assert backend.events.index("end.finish") < backend.events.index("capture.return")

  async def test_capture_led_failure_still_stops_owned_acquisition(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    led_error = RuntimeError("LED off failed")

    async def fail_led_off() -> None:
      backend.events.append("led_off.command")
      raise led_error

    backend.led_off = fail_led_off  # type: ignore[method-assign]
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      with pytest.raises(RuntimeError) as error_info:
        await self._capture(backend)

    assert error_info.value is led_error
    assert backend.events.index("led_off.command") < backend.events.index("end.start")
    assert backend.events.count("end.start") == 1
    assert not backend._acquiring
    assert not backend._acquisition_state_unknown
    assert not backend.camera.streaming

  async def test_capture_cleanup_failures_retain_body_context(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    body_error = ValueError("capture failed")
    led_error = RuntimeError("LED off failed")

    async def fail_capture(*args: object, **kwargs: object) -> Image:
      del args, kwargs
      raise body_error

    async def fail_led_off() -> None:
      backend.events.append("led_off.command")
      raise led_error

    backend._acquire_image = fail_capture  # type: ignore[method-assign]
    backend.led_off = fail_led_off  # type: ignore[method-assign]
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      with pytest.raises(RuntimeError) as error_info:
        await self._capture(backend)

    assert error_info.value is led_error
    assert led_error.__context__ is body_error
    assert backend.events.index("led_off.command") < backend.events.index("end.start")
    assert backend.events.count("end.start") == 1
    assert not backend._acquiring
    assert not backend._acquisition_state_unknown
    assert not backend.camera.streaming

  async def test_capture_all_failures_retain_complete_context_chain(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    body_error = ValueError("capture failed")
    led_error = RuntimeError("LED off failed")
    stop_error = RuntimeError("stop acquisition failed")

    async def fail_capture(*args: object, **kwargs: object) -> Image:
      del args, kwargs
      raise body_error

    async def fail_led_off() -> None:
      backend.events.append("led_off.command")
      raise led_error

    def fail_stop_acquisition() -> None:
      backend.events.append("stop.command")
      backend._acquisition_state_unknown = True
      raise stop_error

    backend._acquire_image = fail_capture  # type: ignore[method-assign]
    backend.led_off = fail_led_off  # type: ignore[method-assign]
    backend.stop_acquisition = fail_stop_acquisition  # type: ignore[method-assign]
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      with pytest.raises(RuntimeError) as error_info:
        await self._capture(backend)

    assert error_info.value is stop_error
    assert stop_error.__context__ is led_error
    assert led_error.__context__ is body_error
    assert backend.events.index("led_off.command") < backend.events.index("stop.command")
    assert backend.events.count("stop.command") == 1
    assert backend._acquiring
    assert backend._acquisition_state_unknown
    assert backend.camera.streaming

  async def test_cancelled_capture_led_failure_still_stops_and_preserves_cancellation(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    led_error = RuntimeError("LED off failed")
    capture_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    failures: List[BaseException] = []

    async def block_capture(*args: object, **kwargs: object) -> Image:
      del args, kwargs
      capture_started.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    async def fail_led_off() -> None:
      backend.events.append("led_off.command")
      raise led_error

    async def capture() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await self._capture(backend)
      except BaseException as error:
        failures.append(error)

    backend._acquire_image = block_capture  # type: ignore[method-assign]
    backend.led_off = fail_led_off  # type: ignore[method-assign]
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(capture)
        await capture_started.wait()
        scopes[0].cancel()

    assert failures == [led_error]
    assert isinstance(led_error.__context__, anyio.get_cancelled_exc_class())
    assert backend.events.index("led_off.command") < backend.events.index("end.start")
    assert backend.events.count("end.start") == 1
    assert not backend._acquiring
    assert not backend._acquisition_state_unknown
    assert not backend.camera.streaming

  async def test_cancelled_capture_cleanup_failures_retain_complete_context_chain(self) -> None:
    backend, pyspin = make_worker_boundary_cytation()
    led_error = RuntimeError("LED off failed")
    stop_error = RuntimeError("stop acquisition failed")
    capture_started = anyio.Event()
    scopes: List[anyio.CancelScope] = []
    failures: List[BaseException] = []

    async def block_capture(*args: object, **kwargs: object) -> Image:
      del args, kwargs
      capture_started.set()
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    async def fail_led_off() -> None:
      backend.events.append("led_off.command")
      raise led_error

    def fail_stop_acquisition() -> None:
      backend.events.append("stop.command")
      backend._acquisition_state_unknown = True
      raise stop_error

    async def capture() -> None:
      try:
        with anyio.CancelScope() as scope:
          scopes.append(scope)
          await self._capture(backend)
      except BaseException as error:
        failures.append(error)

    backend._acquire_image = block_capture  # type: ignore[method-assign]
    backend.led_off = fail_led_off  # type: ignore[method-assign]
    backend.stop_acquisition = fail_stop_acquisition  # type: ignore[method-assign]
    with unittest.mock.patch(
      "pylabrobot.legacy.plate_reading.agilent.biotek_cytation_backend.PySpin", pyspin, create=True
    ):
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(capture)
        await capture_started.wait()
        scopes[0].cancel()

    assert failures == [stop_error]
    assert stop_error.__context__ is led_error
    assert isinstance(led_error.__context__, anyio.get_cancelled_exc_class())
    assert backend.events.index("led_off.command") < backend.events.index("stop.command")
    assert backend.events.count("stop.command") == 1
    assert backend._acquiring
    assert backend._acquisition_state_unknown
    assert backend.camera.streaming


class TestBioTekShaking(AnyioTestBase):
  async def test_worker_failure_is_surfaced_and_retained_for_direct_stop_retry(self) -> None:
    backend, _ = make_shake_backend()
    worker_error = RuntimeError("shake worker failed")

    async with backend:
      backend._shaking = True
      backend._shaking_state_unknown = True
      backend._shaking_worker_error = worker_error
      backend._shaking_stopped = anyio.Event()
      backend._shaking_stopped.set()

      with pytest.raises(RuntimeError, match="shake worker failed") as error_info:
        await backend.stop_shaking()
      assert error_info.value is worker_error
      assert backend._shaking
      assert backend._shaking_state_unknown

      await backend.stop_shaking()
      assert not backend._shaking
      assert not backend._shaking_state_unknown
      assert backend.events.count("command:x") == 2

  async def test_join_failure_retains_state_for_direct_stop_retry(self) -> None:
    backend, _ = make_shake_backend()

    class FailFirstWait:
      def __init__(self, event: anyio.Event) -> None:
        self.event = event
        self.failed = False

      def set(self) -> None:
        self.event.set()

      async def wait(self) -> None:
        if not self.failed:
          self.failed = True
          raise RuntimeError("shake join failed")
        await self.event.wait()

    async with backend:
      await backend.shake(backend.ShakeType.LINEAR, 2)
      assert backend._shaking_stopped is not None
      backend._shaking_stopped = cast(anyio.Event, FailFirstWait(backend._shaking_stopped))

      with pytest.raises(RuntimeError, match="shake join failed"):
        await backend.stop_shaking()
      assert backend._shaking
      assert backend._shaking_state_unknown

      await backend.stop_shaking()
      assert not backend._shaking
      assert not backend._shaking_state_unknown

  async def test_lifespan_join_failure_preserves_unknown_state_for_reentry_retry(self) -> None:
    backend, _ = make_shake_backend()
    join_error = RuntimeError("shake join failed")

    class FailingWait:
      def set(self) -> None:
        pass

      async def wait(self) -> None:
        raise join_error

    with pytest.raises(RuntimeError, match="shake join failed") as error_info:
      async with backend:
        backend._shaking = True
        backend._shaking_state_unknown = True
        backend._shaking_stopped = cast(anyio.Event, FailingWait())
    assert error_info.value is join_error

    assert backend._shaking
    assert backend._shaking_state_unknown

    async with backend:
      await backend.stop_shaking()
      assert not backend._shaking
      assert not backend._shaking_state_unknown
    assert backend.events.count("command:x") == 2

  async def test_lifespan_owns_worker_and_explicit_stop_joins_it(self) -> None:
    backend, _ = make_shake_backend()
    async with backend:
      await backend.shake(backend.ShakeType.LINEAR, 2)
      assert backend._shaking
      await backend.stop_shaking()
      assert not backend._shaking
      assert backend._shaking_task is None
    assert backend.events[-1] == "ftdi.exit"

  async def test_worker_failure_fails_owning_lifespan(self) -> None:
    backend, _ = make_shake_backend()
    backend.fail_operation = True
    with pytest.raises(BaseExceptionGroup) as error_info:
      async with backend:
        await backend.shake(backend.ShakeType.LINEAR, 2)
        await anyio.sleep_forever()
    assert any(str(error) == "shake worker failed" for error in error_info.value.exceptions)
    assert backend.events[-1] == "ftdi.exit"

  async def test_exit_aborts_worker_before_ftdi_close(self) -> None:
    backend, _ = make_shake_backend()
    async with backend:
      await backend.shake(backend.ShakeType.ORBITAL, 3)
    assert backend.events[-2:] == ["command:x", "ftdi.exit"]
    assert backend._shaking_task is None
    events_after_exit = list(backend.events)
    await anyio.sleep(0.01)
    assert backend.events == events_after_exit


class TestBioTekShakingScope(AnyioTestBase):
  async def test_scope_waits_for_first_confirmed_program_before_entering(self) -> None:
    backend, _ = make_shake_backend()
    backend.block_operation = True
    entered = anyio.Event()

    async def use_scope() -> None:
      async with backend.shaking(backend.ShakeType.ORBITAL, 3):
        entered.set()

    async with backend:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_scope)
        await backend.operation_started.wait()
        assert not entered.is_set()
        assert not backend._shaking
        backend.release_operation.set()

  async def test_scope_cleans_up_after_body_failure(self) -> None:
    backend, _ = make_shake_backend()
    async with backend:
      with pytest.raises(ValueError, match="body failed"):
        async with backend.shaking(backend.ShakeType.LINEAR, 2):
          raise ValueError("body failed")
      assert backend.events[-1] == "command:x"
      assert backend._shaking_task is None

  async def test_scope_cancellation_aborts_and_joins_before_propagating(self) -> None:
    backend, _ = make_shake_backend()
    entered = anyio.Event()
    cancelled = anyio.Event()
    scopes: List[anyio.CancelScope] = []

    async def use_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with backend.shaking(backend.ShakeType.LINEAR, 2):
            entered.set()
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
          assert backend.events[-1] == "command:x"
          assert backend._shaking_task is None
          cancelled.set()
          raise

    async with backend:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_scope)
        await entered.wait()
        scopes[0].cancel()
        await cancelled.wait()

  async def test_scope_rejects_nested_and_direct_transitions(self) -> None:
    backend, _ = make_shake_backend()
    async with backend:
      async with backend.shaking(backend.ShakeType.LINEAR, 2):
        commands_before = list(backend.events)
        with pytest.raises(RuntimeError, match="scop"):
          async with backend.shaking(backend.ShakeType.ORBITAL, 3):
            pytest.fail("nested scope reached body")
        with pytest.raises(RuntimeError, match="scope"):
          await backend.shake(backend.ShakeType.ORBITAL, 3)
        with pytest.raises(RuntimeError, match="scope"):
          await backend.stop_shaking()
        assert backend.events == commands_before

  async def test_scope_rejects_preexisting_durable_shaking(self) -> None:
    backend, _ = make_shake_backend()
    async with backend:
      await backend.shake(backend.ShakeType.LINEAR, 2)
      commands_before = list(backend.events)
      with pytest.raises(RuntimeError, match="already active"):
        async with backend.shaking(backend.ShakeType.ORBITAL, 3):
          pytest.fail("scope adopted durable shaking")
      assert backend.events == commands_before
      await backend.stop_shaking()

  async def test_failed_abort_retains_state_for_direct_retry(self) -> None:
    backend, _ = make_shake_backend()
    backend.abort_failures = 1
    async with backend:
      with pytest.raises(RuntimeError, match="shake abort failed"):
        async with backend.shaking(backend.ShakeType.LINEAR, 2):
          pass
      assert backend._shaking
      assert backend._shaking_scope_token is None
      await backend.stop_shaking()
      assert not backend._shaking
    assert backend.events.count("command:x") == 2
