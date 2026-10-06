import unittest
from typing import Tuple
from unittest.mock import AsyncMock

from pylabrobot.legacy.peeling.xpeel_backend import XPeelBackend


def _backend(*lines: bytes) -> Tuple[XPeelBackend, AsyncMock]:
  """An XPeelBackend and its serial port, which answers with `lines`, one per readline."""
  backend = XPeelBackend(port="/dev/null")
  io = AsyncMock()
  io.readline = AsyncMock(side_effect=list(lines))
  backend.io = io
  return backend, io


class TestXPeelBackendGetStatus(unittest.IsolatedAsyncioTestCase):
  async def test_returns_the_three_error_codes(self) -> None:
    backend, _ = _backend(b"*ready:07,20,00\r\n")

    self.assertEqual(await backend.get_status(), (7, 20, 0))


class TestXPeelBackendPeel(unittest.IsolatedAsyncioTestCase):
  async def test_sends_the_adhere_time_as_a_code(self) -> None:
    for adhere_time, code in ((2.5, 1), (5.0, 2), (7.5, 3), (10.0, 4)):
      with self.subTest(adhere_time=adhere_time):
        backend, io = _backend(b"*ack\r\n", b"*ready:00,00,00\r\n")

        await backend.peel(begin_location=0, fast=False, adhere_time=adhere_time)

        io.write.assert_awaited_once_with(f"*xpeel:4{code}\r\n".encode("ascii"))


if __name__ == "__main__":
  unittest.main()
