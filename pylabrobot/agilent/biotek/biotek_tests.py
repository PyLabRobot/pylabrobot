# mypy: disable-error-code = attr-defined


import math
import sys
import unittest
import unittest.mock
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterator, Optional, cast

import anyio
import pytest

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup

pytest.importorskip("pylibftdi")

from pylabrobot.agilent.biotek.cytation import Cytation1, Cytation5
from pylabrobot.agilent.biotek.cytation.microscope import CytationImagingConfig
from pylabrobot.agilent.biotek.plate_reader_base import BioTekPlateReaderDriver
from pylabrobot.agilent.biotek.synergy import SynergyH1
from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan
from pylabrobot.io.ftdi import FTDI
from pylabrobot.resources import CellVis_24_wellplate_3600uL_Fb, CellVis_96_wellplate_350uL_Fb
from pylabrobot.testing.concurrency import AnyioTestBase


def _byte_iter(s: str) -> Iterator[bytes]:
  for c in s:
    yield c.encode()


def _group_contains(group: BaseExceptionGroup, target: BaseException) -> bool:
  """Return whether a nested task-group failure contains the original exception."""
  for error in group.exceptions:
    if error is target:
      return True
    if isinstance(error, BaseExceptionGroup) and _group_contains(error, target):
      return True
  return False


class TestCytation5Backend(AnyioTestBase):
  """Tests for the Cytation5Backend."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    del kwargs
    self.backend = BioTekPlateReaderDriver(timeout=0.1)
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
    self.backend.io.device_id = "mock-biotek"

    @asynccontextmanager
    async def io_lifespan():
      yield self.backend.io

    self.backend.io.lifespan = io_lifespan
    self.backend.request_firmware_version = unittest.mock.AsyncMock(return_value="2.07")  # type: ignore[method-assign]
    await stack.enter_async_context(self.backend.lifespan())
    self.plate = CellVis_24_wellplate_3600uL_Fb(name="plate")

    # Freeze the clock so the timestamp in the results is predictable.
    self.now = datetime(2024, 5, 17, 12, 34, 56)
    self._time_patcher = unittest.mock.patch(
      "pylabrobot.agilent.biotek.plate_reader_base.datetime",
      unittest.mock.Mock(now=unittest.mock.Mock(return_value=self.now)),
    )
    self._time_patcher.start()
    stack.callback(self._time_patcher.stop)

  async def test_setup(self):
    backend, io = make_reader(Cytation1)
    async with backend:
      assert backend.version == "2.07"
    assert io.events == [
      "ftdi.enter",
      "usb_reset",
      "latency:16",
      "baudrate:9600",
      "line:8:2:0",
      "flow:256",
      "rts:True",
      "ftdi.exit",
    ]

  async def test_request_serial_number(self):
    self.backend.io.read.side_effect = _byte_iter("\x0600000000        0000\x03")
    assert await self.backend.request_serial_number() == "00000000"

  async def test_request_firmware_version(self):
    self.backend.io.read.side_effect = _byte_iter("\x061650200  Version 1.04   0000\x03")
    version = await BioTekPlateReaderDriver.request_firmware_version(self.backend)
    assert version == "1.04"

  async def test_open(self):
    self.backend.io.read.side_effect = [b"\x06", b"\x03", b"\x03"]
    await self.backend.open()
    self.backend.io.write.assert_called_with(b"J")

  async def test_close(self):
    self.backend.io.read.side_effect = [b"\x06", b"\x03", b"\x06", b"\x03", b"\x03"]
    plate = CellVis_24_wellplate_3600uL_Fb(name="plate")
    await self.backend.close(plate=plate)
    self.backend.io.write.assert_called_with(b"A")

  async def test_request_current_temperature(self):
    self.backend.io.read.side_effect = _byte_iter("\x062360000\x03")
    assert await self.backend.request_current_temperature() == 23.6

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

    plate = CellVis_96_wellplate_350uL_Fb(name="plate")
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
    assert len(resp) == 1
    assert resp[0].wavelength == 580
    assert resp[0].data == expected_data
    assert resp[0].temperature == 23.6
    assert resp[0].timestamp == self.now

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

    plate = CellVis_96_wellplate_350uL_Fb(name="plate")
    wells = plate["A1"] + plate["B1:G3"] + plate["D4:F4"]
    resp = await self.backend.read_luminescence(
      focal_height=4.5,
      plate=plate,
      wells=wells,
      integration_time=0.4,
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
    assert len(resp) == 1
    assert resp[0].data == expected_data
    assert resp[0].temperature == 23.6
    assert resp[0].timestamp == self.now

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

    plate = CellVis_96_wellplate_350uL_Fb(name="plate")
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
    assert len(resp) == 1
    assert resp[0].excitation_wavelength == 485
    assert resp[0].emission_wavelength == 528
    assert resp[0].data == expected_data
    assert resp[0].temperature == 23.6
    assert resp[0].timestamp == self.now

  async def test_parse_body_asterisks_as_nan(self):
    """Unmeasured wells return ******* which should be parsed as NaN."""
    plate = CellVis_96_wellplate_350uL_Fb(name="plate")
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
          assert v is not None and math.isnan(v), f"Expected NaN at ({r},{c}), got {v}"
        else:
          assert v == e, f"Mismatch at ({r},{c})"


class TestBioTekLoadingTray(AnyioTestBase):
  """The tray must send the plate geometry to the firmware before closing (tall-plate clearance)."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    del stack, kwargs
    self.manager = unittest.mock.Mock()
    self.backend = BioTekPlateReaderDriver(timeout=0.1)
    self.backend.set_slow_mode = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    self.backend.set_plate = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    self.backend.send_command = unittest.mock.AsyncMock()  # type: ignore[method-assign]
    self.manager.attach_mock(self.backend.set_plate, "set_plate")
    self.manager.attach_mock(self.backend.send_command, "send_command")
    self.plate = CellVis_24_wellplate_3600uL_Fb(name="plate")

  async def test_close_sends_plate_geometry_then_closes(self):
    await self.backend.close(plate=self.plate)
    self.backend.set_plate.assert_awaited_once_with(self.plate)
    self.backend.send_command.assert_awaited_once_with("A")
    # set_plate must run before the close ("A") command.
    assert [call[0] for call in self.manager.mock_calls] == ["set_plate", "send_command"]

  async def test_close_without_plate_skips_set_plate(self):
    await self.backend.close(plate=None)
    self.backend.set_plate.assert_not_awaited()
    self.backend.send_command.assert_awaited_once_with("A")

  async def test_open_sends_j(self):
    await self.backend.open()
    self.backend.send_command.assert_awaited_once_with("J")

  async def test_slow_mode_forwarded(self):
    await self.backend.open(slow=True)
    self.backend.set_slow_mode.assert_awaited_once_with(True)


class RecordingFTDI(HasLifespan):
  """Hardware-free FTDI resource that records reader lifecycle effects."""

  def __init__(
    self,
    events: Optional[list[str]] = None,
    *,
    entry_error: Optional[BaseException] = None,
    exit_error: Optional[BaseException] = None,
  ) -> None:
    super().__init__()
    self.events = events if events is not None else []
    self.entry_error = entry_error
    self.exit_error = exit_error
    self.device_id = "fake-biotek"
    self.reads: deque[bytes | Callable[[], Awaitable[bytes]]] = deque()
    self.writes: list[bytes] = []
    self.block_write: Optional[bytes] = None
    self.write_started = anyio.Event()
    self.release_write = anyio.Event()

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingFTDI does not accept lifespan options")
    self.events.append("ftdi.enter")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error

  async def _close(self) -> None:
    await anyio.lowlevel.checkpoint()
    self.events.append("ftdi.exit")
    if self.exit_error is not None:
      raise self.exit_error

  async def usb_reset(self) -> None:
    self.events.append("usb_reset")

  async def set_latency_timer(self, value: int) -> None:
    self.events.append(f"latency:{value}")

  async def set_baudrate(self, value: int) -> None:
    self.events.append(f"baudrate:{value}")

  async def set_line_property(self, bits: int, stopbits: int, parity: int) -> None:
    self.events.append(f"line:{bits}:{stopbits}:{parity}")

  async def set_flowctrl(self, value: int) -> None:
    self.events.append(f"flow:{value}")

  async def set_rts(self, value: bool) -> None:
    self.events.append(f"rts:{value}")

  async def usb_purge_rx_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()

  async def usb_purge_tx_buffer(self) -> None:
    await anyio.lowlevel.checkpoint()

  async def write(self, data: bytes) -> int:
    self.writes.append(data)
    self.events.append(f"write:{data.decode(errors='replace')}")
    if data == self.block_write:
      self.write_started.set()
      await self.release_write.wait()
    return len(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    del num_bytes
    await anyio.lowlevel.checkpoint()
    if not self.reads:
      return b""
    effect = self.reads.popleft()
    return await effect() if callable(effect) else effect


def make_reader(
  reader_type: type[BioTekPlateReaderDriver],
) -> tuple[BioTekPlateReaderDriver, RecordingFTDI]:
  with unittest.mock.patch("pylabrobot.agilent.biotek.plate_reader_base.FTDI"):
    reader: BioTekPlateReaderDriver
    if reader_type is Cytation1:
      reader = Cytation1(name="reader")
    elif reader_type is Cytation5:
      reader = Cytation5(
        name="reader",
        use_cam=False,
        imaging_config=CytationImagingConfig(filters=[], objectives=[]),
      )
    elif reader_type is SynergyH1:
      reader = SynergyH1(name="reader")
    else:
      reader = BioTekPlateReaderDriver(timeout=0.1)
  io = RecordingFTDI()
  reader.io = cast(FTDI, io)
  reader.request_firmware_version = unittest.mock.AsyncMock(return_value="2.07")  # type: ignore[method-assign]
  return reader, io


class RecordingShakeReader(BioTekPlateReaderDriver):
  """Reader that keeps the real shaking control flow and records command boundaries."""

  def __init__(self, events: list[str]) -> None:
    with unittest.mock.patch("pylabrobot.agilent.biotek.plate_reader_base.FTDI"):
      super().__init__(timeout=0.1)
    self.events = events
    self.block_command = False
    self.command_started = anyio.Event()
    self.block_operation = False
    self.operation_started = anyio.Event()
    self.release_operation = anyio.Event()
    self.fail_operation = False
    self.operation_error = RuntimeError("shake worker failed")
    self.abort_failures = 0

  async def request_firmware_version(self) -> str:
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
    if self.block_command and command == "D":
      self.command_started.set()
      await anyio.sleep_forever()
    if self.block_operation and command == "O":
      self.operation_started.set()
      await self.release_operation.wait()
    if self.fail_operation and command == "O":
      raise self.operation_error
    if command == "x" and self.abort_failures:
      self.abort_failures -= 1
      raise RuntimeError("shake abort failed")
    await anyio.lowlevel.checkpoint()
    return b"\x060000\x03" if command == "O" else b"\x03"


def make_shake_reader() -> tuple[RecordingShakeReader, RecordingFTDI]:
  events: list[str] = []
  reader = RecordingShakeReader(events)
  io = RecordingFTDI(events)
  reader.io = cast(FTDI, io)
  return reader, io


class TestBioTekLifecycle(AnyioTestBase):
  async def test_synergy_reply_deadline_interrupts_blocked_read(self) -> None:
    """The entire terminated reply has one caller-owned deadline."""
    reader, io = make_reader(SynergyH1)

    async def blocked_read() -> bytes:
      await anyio.sleep_forever()
      raise AssertionError("unreachable")

    io.reads.append(blocked_read)
    with anyio.fail_after(1):
      with pytest.raises(TimeoutError, match="partial="):
        await reader._read_until(b"\x03", timeout=0.01)

  def test_concrete_readers_use_managed_lifecycle_methods(self) -> None:
    for reader_type in (Cytation1, Cytation5, SynergyH1):
      assert reader_type.__aenter__ is HasLifespan.__aenter__
      assert reader_type.__aexit__ is HasLifespan.__aexit__
      assert reader_type.setup is HasLifespan.setup
      assert reader_type.stop is HasLifespan.stop

  async def test_reader_lifecycle_matrix(self) -> None:
    for reader_type in (Cytation1, Cytation5, SynergyH1):
      reader, io = make_reader(reader_type)
      body_error = RuntimeError("body failed")
      with pytest.raises(RuntimeError, match="body failed") as error_info:
        async with reader:
          assert reader.is_active and io.is_active
          raise body_error
      assert error_info.value is body_error
      assert io.events[-1] == "ftdi.exit"
      assert not reader.is_active and not io.is_active

      async with reader:
        pass
      assert io.events.count("ftdi.enter") == 2
      assert io.events.count("ftdi.exit") == 2

  async def test_entry_failure_rolls_back_ftdi(self) -> None:
    reader, io = make_reader(Cytation1)
    reader.request_firmware_version = unittest.mock.AsyncMock(  # type: ignore[method-assign]
      side_effect=RuntimeError("firmware failed")
    )

    with pytest.raises(RuntimeError, match="firmware failed"):
      async with reader:
        pytest.fail("failed entry reached body")

    assert io.events[-1] == "ftdi.exit"

  async def test_body_cancellation_closes_ftdi(self) -> None:
    reader, io = make_reader(SynergyH1)
    entered = anyio.Event()

    async def use_reader() -> None:
      async with reader:
        entered.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(use_reader)
      await entered.wait()
      task_group.cancel_scope.cancel()

    assert io.events[-1] == "ftdi.exit"

  async def test_rejects_lifespan_options_before_ftdi_entry(self) -> None:
    reader, io = make_reader(Cytation1)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
      async with reader.lifespan(unknown=True):
        pytest.fail("invalid options reached body")
    assert io.events == []

  async def test_send_command_serializes_complete_transactions(self) -> None:
    reader, io = make_reader(Cytation1)
    io.block_write = b"A"
    io.reads.extend([b"\x03", b"\x03"])

    async with reader:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(reader.send_command, "A")
        await io.write_started.wait()
        task_group.start_soon(reader.send_command, "B")
        await anyio.sleep(0.01)
        assert io.writes == [b"A"]
        io.release_write.set()

    assert io.writes[-2:] == [b"A", b"B"]

  @pytest.mark.parametrize("write_phase", ["command", "parameter"])
  async def test_cancellation_after_each_write_consumes_reply_before_next_command(
    self, write_phase: str
  ) -> None:
    reader, io = make_reader(Cytation1)
    response_started = anyio.Event()
    release_response = anyio.Event()
    scopes: list[anyio.CancelScope] = []
    events: list[str] = []

    async def blocked_response() -> bytes:
      response_started.set()
      await release_response.wait()
      events.append("response.complete")
      return b"\x03"

    if write_phase == "command":
      io.reads.extend([blocked_response, b"\x03"])
      parameter = None
    else:
      io.reads.extend([b"\x06", blocked_response, b"\x03"])
      parameter = "P"

    async def first_command() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          await reader.send_command("A", parameter)
        except anyio.get_cancelled_exc_class():
          events.append("cancelled")
          raise

    async with reader:
      io.writes.clear()
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(first_command)
        await response_started.wait()
        scopes[0].cancel()
        await anyio.lowlevel.checkpoint()
        release_response.set()

      assert await reader.send_command("B") == b"\x03"

    expected_writes = [b"A", b"B"] if parameter is None else [b"A", b"P", b"B"]
    assert io.writes == expected_writes
    assert not io.reads
    assert events == ["response.complete", "cancelled"]

  async def test_no_io_after_exit(self) -> None:
    reader, io = make_reader(Cytation1)
    async with reader:
      pass
    writes_before = list(io.writes)
    with pytest.raises(RuntimeError, match="not active"):
      await reader.send_command("J")
    assert io.writes == writes_before


class RecordingMicroscope(HasLifespan):
  def __init__(self, events: list[str], entry_error: Optional[BaseException] = None) -> None:
    super().__init__()
    self.events = events
    self.entry_error = entry_error

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    if kwargs:
      raise TypeError("RecordingMicroscope does not accept lifespan options")
    self.events.append("microscope.enter")
    stack.push_shielded_async_callback(self._close)
    if self.entry_error is not None:
      raise self.entry_error

  async def _close(self) -> None:
    self.events.append("microscope.exit")


class TestCytation5Composition(AnyioTestBase):
  async def test_enters_microscope_after_base_and_exits_it_before_ftdi(self) -> None:
    reader, io = make_reader(Cytation5)
    reader.microscope = RecordingMicroscope(io.events)  # type: ignore[assignment]

    async with reader:
      io.events.append("body")

    assert io.events[-4:] == ["microscope.enter", "body", "microscope.exit", "ftdi.exit"]

  async def test_microscope_entry_failure_rolls_back_base(self) -> None:
    reader, io = make_reader(Cytation5)
    failure = RuntimeError("microscope failed")
    reader.microscope = RecordingMicroscope(io.events, entry_error=failure)  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="microscope failed") as error_info:
      async with reader:
        pytest.fail("failed entry reached body")

    assert error_info.value is failure
    assert io.events[-3:] == ["microscope.enter", "microscope.exit", "ftdi.exit"]


class TestBioTekShaking(AnyioTestBase):
  async def test_worker_waits_for_stop_or_program_expiry_without_polling(self) -> None:
    reader, _ = make_shake_reader()
    sleep_calls: list[float] = []
    sleep = anyio.sleep

    async def record_sleep(delay: float) -> None:
      sleep_calls.append(delay)
      await sleep(delay)

    async with reader:
      with unittest.mock.patch(
        "pylabrobot.agilent.biotek.plate_reader_base.anyio.sleep", record_sleep
      ):
        await reader.shake(reader.ShakeType.ORBITAL, 3)
        await anyio.lowlevel.checkpoint()
        assert sleep_calls == []
        await reader.stop_shaking()

  async def test_shake_returns_after_worker_starts(self) -> None:
    reader, _ = make_shake_reader()
    async with reader:
      await reader.shake(reader.ShakeType.ORBITAL, 3)
      assert reader._shaking
      assert reader.events[-2:] == ["command:D", "command:O"]
      await reader.stop_shaking()

  async def test_explicit_stop_aborts_and_joins_worker(self) -> None:
    reader, _ = make_shake_reader()
    async with reader:
      await reader.shake(reader.ShakeType.LINEAR, 2)
      await reader.stop_shaking()
      assert reader._shaking is False
      assert reader._shaking_task is None
      assert reader.events[-1] == "command:x"

  async def test_exit_aborts_worker_before_ftdi_close(self) -> None:
    reader, _ = make_shake_reader()
    async with reader:
      await reader.shake(reader.ShakeType.LINEAR, 2)
    assert reader.events[-2:] == ["command:x", "ftdi.exit"]

  async def test_cancelled_shake_is_joined_on_exit(self) -> None:
    reader, io = make_shake_reader()
    reader.block_command = True
    cancel_scope_ready = anyio.Event()
    cancellation_seen = anyio.Event()
    operation_finished = anyio.Event()
    operation_scopes: list[anyio.CancelScope] = []

    async def run_shake() -> None:
      try:
        with anyio.CancelScope() as operation_scope:
          operation_scopes.append(operation_scope)
          cancel_scope_ready.set()
          try:
            async with reader:
              await reader.shake(reader.ShakeType.LINEAR, 2)
          except anyio.get_cancelled_exc_class():
            cancellation_seen.set()
            raise
      finally:
        operation_finished.set()

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(run_shake)
      await cancel_scope_ready.wait()
      await reader.command_started.wait()
      operation_scopes[0].cancel()
      await operation_finished.wait()

    assert cancellation_seen.is_set()
    assert reader.events[-2:] == ["command:x", "ftdi.exit"]
    assert reader._shaking_task is None
    writes_before = list(io.writes)
    with pytest.raises(RuntimeError, match="not active"):
      await BioTekPlateReaderDriver.send_command(reader, "J")
    assert io.writes == writes_before

  async def test_worker_failure_reaches_owning_lifespan(self) -> None:
    reader, _ = make_shake_reader()
    reader.fail_operation = True

    with pytest.raises(BaseExceptionGroup) as error_info:
      async with reader:
        await reader.shake(reader.ShakeType.LINEAR, 2)
        await anyio.sleep_forever()

    assert any(str(error) == "shake worker failed" for error in error_info.value.exceptions)
    assert reader.events[-1] == "ftdi.exit"


class TestBioTekShakingScope(AnyioTestBase):
  async def test_scope_waits_for_first_confirmed_program_before_entering(self) -> None:
    reader, _ = make_shake_reader()
    reader.block_operation = True
    entered = anyio.Event()
    release_body = anyio.Event()

    async def use_scope() -> None:
      async with reader.shaking(reader.ShakeType.ORBITAL, 3):
        entered.set()
        await release_body.wait()

    async with reader:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_scope)
        await reader.operation_started.wait()
        assert not entered.is_set()
        assert reader._shaking is None
        reader.release_operation.set()
        await entered.wait()
        assert reader._shaking
        release_body.set()

    assert reader.events[-2:] == ["command:x", "ftdi.exit"]

  async def test_scope_start_failure_reconciles_attempt_before_lifespan_failure(self) -> None:
    reader, _ = make_shake_reader()
    reader.fail_operation = True

    with pytest.raises(BaseExceptionGroup) as error_info:
      async with reader:
        async with reader.shaking(reader.ShakeType.LINEAR, 2):
          pytest.fail("failed shaking scope reached its body")

    assert _group_contains(error_info.value, reader.operation_error)
    assert reader.events[-2:] == ["command:x", "ftdi.exit"]
    assert reader._shaking is False

  async def test_scope_cancellation_aborts_and_joins_before_propagating(self) -> None:
    reader, _ = make_shake_reader()
    entered = anyio.Event()
    cancelled = anyio.Event()
    scopes: list[anyio.CancelScope] = []

    async def use_scope() -> None:
      with anyio.CancelScope() as scope:
        scopes.append(scope)
        try:
          async with reader.shaking(reader.ShakeType.LINEAR, 2):
            entered.set()
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
          assert reader.events[-1] == "command:x"
          assert reader._shaking_task is None
          cancelled.set()
          raise

    async with reader:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(use_scope)
        await entered.wait()
        scopes[0].cancel()
        await cancelled.wait()

  async def test_nested_and_concurrent_scopes_reject_conflicting_parameters(self) -> None:
    reader, _ = make_shake_reader()
    entered = anyio.Event()
    release = anyio.Event()
    failures: list[RuntimeError] = []

    async def hold_scope() -> None:
      async with reader.shaking(reader.ShakeType.LINEAR, 2):
        entered.set()
        await release.wait()

    async def enter_conflicting_scope() -> None:
      try:
        async with reader.shaking(reader.ShakeType.ORBITAL, 4):
          pytest.fail("conflicting scope reached its body")
      except RuntimeError as error:
        failures.append(error)

    async with reader:
      async with anyio.create_task_group() as task_group:
        task_group.start_soon(hold_scope)
        await entered.wait()
        commands_before = list(reader.events)
        with pytest.raises(RuntimeError, match="already scoped"):
          async with reader.shaking(reader.ShakeType.LINEAR, 2):
            pytest.fail("nested scope reached its body")
        task_group.start_soon(enter_conflicting_scope)
        await anyio.sleep(0)
        assert reader.events == commands_before
        release.set()

    assert len(failures) == 1
    assert "already scoped" in str(failures[0])

  async def test_scope_rejects_preexisting_durable_shaking_without_stopping_it(self) -> None:
    reader, _ = make_shake_reader()

    async with reader:
      await reader.shake(reader.ShakeType.LINEAR, 2)
      commands_before = list(reader.events)
      with pytest.raises(RuntimeError, match="already active"):
        async with reader.shaking(reader.ShakeType.ORBITAL, 4):
          pytest.fail("scope adopted durable shaking")
      assert reader.events == commands_before
      await reader.stop_shaking()

  async def test_scope_guards_direct_shake_and_stop_transitions(self) -> None:
    reader, _ = make_shake_reader()

    async with reader:
      async with reader.shaking(reader.ShakeType.LINEAR, 2):
        commands_before = list(reader.events)
        with pytest.raises(RuntimeError, match="scope"):
          await reader.shake(reader.ShakeType.ORBITAL, 4)
        with pytest.raises(RuntimeError, match="scope"):
          await reader.stop_shaking()
        assert reader.events == commands_before

  async def test_failed_scope_abort_retains_retryable_state_for_direct_stop(self) -> None:
    reader, _ = make_shake_reader()
    reader.abort_failures = 1

    async with reader:
      with pytest.raises(RuntimeError, match="shake abort failed"):
        async with reader.shaking(reader.ShakeType.LINEAR, 2):
          pass
      assert reader._shaking is None
      assert reader._shaking_scope_token is None

      await reader.stop_shaking()
      assert reader._shaking is False

    assert reader.events.count("command:x") == 2

  async def test_failed_scope_join_retains_retryable_state_for_direct_stop(self) -> None:
    reader, _ = make_shake_reader()

    class FailFirstWait:
      def __init__(self, event: anyio.Event) -> None:
        self.event = event
        self.failed = False

      def set(self) -> None:
        self.event.set()

      def is_set(self) -> bool:
        return self.event.is_set()

      async def wait(self) -> None:
        if not self.failed:
          self.failed = True
          raise RuntimeError("shake join failed")
        await self.event.wait()

    async with reader:
      with pytest.raises(RuntimeError, match="shake join failed"):
        async with reader.shaking(reader.ShakeType.LINEAR, 2):
          assert reader._shaking_stopped is not None
          reader._shaking_stopped = cast(anyio.Event, FailFirstWait(reader._shaking_stopped))
      assert reader._shaking is None
      assert reader._shaking_scope_token is None

      await reader.stop_shaking()
      assert reader._shaking is False
      assert reader._shaking_task is None

  async def test_parent_cleanup_invalidates_live_scope_before_ftdi_close(self) -> None:
    reader, _ = make_shake_reader()
    entered = anyio.Event()
    release = anyio.Event()

    async def use_scope() -> None:
      async with reader.shaking(reader.ShakeType.LINEAR, 2):
        entered.set()
        await release.wait()

    async with anyio.create_task_group() as task_group:
      lifespan = reader.lifespan()
      await lifespan.__aenter__()
      task_group.start_soon(use_scope)
      await entered.wait()
      await lifespan.__aexit__(None, None, None)
      assert reader.events[-2:] == ["command:x", "ftdi.exit"]
      assert reader._shaking_task is None
      assert reader._shaking_scope_token is None
      abort_count = reader.events.count("command:x")
      release.set()

    assert reader.events.count("command:x") == abort_count
