import unittest
from typing import Tuple
from unittest.mock import AsyncMock

from pylabrobot.azenta.xpeel import XPeel


def _xpeel(*lines: bytes) -> Tuple[XPeel, AsyncMock]:
  """An XPeel and its serial port, which answers with `lines`, one per readline."""
  xpeel = XPeel(port="/dev/null")
  io = AsyncMock()
  io.readline = AsyncMock(side_effect=list(lines))
  xpeel.io = io
  return xpeel, io


class TestXPeelRequestStatus(unittest.IsolatedAsyncioTestCase):
  async def test_returns_the_three_error_codes(self) -> None:
    xpeel, io = _xpeel(b"*ready:00,00,00\r\n")

    self.assertEqual(await xpeel.request_status(), (0, 0, 0))
    io.write.assert_awaited_once_with(b"*stat\r\n")

  async def test_returns_nonzero_error_codes(self) -> None:
    xpeel, _ = _xpeel(b"*ready:07,20,00\r\n")

    self.assertEqual(await xpeel.request_status(), (7, 20, 0))


class TestXPeelPeel(unittest.IsolatedAsyncioTestCase):
  async def test_sends_the_adhere_time_as_a_code(self) -> None:
    for adhere_time, code in ((2.5, 1), (5.0, 2), (7.5, 3), (10.0, 4)):
      with self.subTest(adhere_time=adhere_time):
        xpeel, io = _xpeel(b"*ack\r\n", b"*ready:00,00,00\r\n")

        await xpeel.peel(begin_location=0, fast=False, adhere_time=adhere_time)

        io.write.assert_awaited_once_with(f"*xpeel:4{code}\r\n".encode("ascii"))

  async def test_rejects_an_unsupported_adhere_time(self) -> None:
    xpeel, io = _xpeel()

    with self.assertRaises(ValueError):
      await xpeel.peel(adhere_time=3.0)
    io.write.assert_not_awaited()


if __name__ == "__main__":
  unittest.main()
