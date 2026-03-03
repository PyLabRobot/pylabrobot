# mypy: disable-error-code = attr-defined


import math
import unittest
import unittest.mock
from typing import Iterator

from pylabrobot.plate_reading.agilent.biotek_cytation_backend import CytationBackend
from pylabrobot.resources import CellVis_24_wellplate_3600uL_Fb, CellVis_96_wellplate_350uL_Fb


def _byte_iter(s: str) -> Iterator[bytes]:
  for c in s:
    yield c.encode()


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
    self.plate = CellVis_24_wellplate_3600uL_Fb(name="plate")

    # Mock time.time() to control the timestamp in the results
    self.mock_time = unittest.mock.patch("time.time", return_value=12345.6789).start()
    self.addCleanup(self.mock_time.stop)

  async def test_setup(self):
    self.backend.io.read.side_effect = _byte_iter("\x061650200  Version 1.04   0000\x03")
    await self.backend.setup()
    assert self.backend.io.setup.called
    self.backend.io.usb_reset.assert_called_once()
    self.backend.io.set_latency_timer.assert_called_with(16)
    self.backend.io.set_baudrate.assert_called_with(9600)
    # self.backend.io.set_line_property.assert_called_with(8, 2, 0)  #?
    self.backend.io.set_flowctrl.assert_called_with(0x100)
    self.backend.io.set_rts.assert_called_with(1)

    await self.backend.stop()
    assert self.backend.io.stop.called

  async def test_get_serial_number(self):
    self.backend.io.read.side_effect = _byte_iter("\x0600000000        0000\x03")
    assert await self.backend.get_serial_number() == "00000000"

  async def test_open(self):
    self.backend.io.read.side_effect = [b"\x06", b"\x03", b"\x03"]
    await self.backend.open()
    self.backend.io.write.assert_called_with(b"J")

  async def test_close(self):
    self.backend.io.read.side_effect = [b"\x06", b"\x03", b"\x06", b"\x03", b"\x03"]
    plate = CellVis_24_wellplate_3600uL_Fb(name="plate")
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

    plate = CellVis_96_wellplate_350uL_Fb(name="plate")
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
          self.assertTrue(v is not None and math.isnan(v), f"Expected NaN at ({r},{c}), got {v}")
        else:
          self.assertEqual(v, e, f"Mismatch at ({r},{c})")
