import asyncio
import unittest
from unittest.mock import AsyncMock

from pylabrobot.qinstruments.bioshake import BioShake


class TestBioShakeReset(unittest.TestCase):
  def test_reset_completes_when_q1_initialization_succeeds(self) -> None:
    bioshake = BioShake.__new__(BioShake)
    bioshake.io = AsyncMock()
    bioshake.io.readline = AsyncMock(return_value=b"Initialization successfully completed.\r\n")

    asyncio.run(bioshake.reset())

    bioshake.io.write.assert_awaited_once_with(b"resetDevice\r")
