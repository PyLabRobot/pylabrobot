"""A session log holds the driver's commands and, at DEBUG, every message to and from the boards."""

import asyncio
import re
import tempfile
import unittest
from pathlib import Path

from pylabrobot.beckman_coulter.biomek_i7.session_log import start_session_log
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  generic_driver,
  generic_simulated_boards,
)


class SessionLogTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)

  async def run_setup(self) -> None:
    driver = generic_driver(generic_simulated_boards())  # just powered up: configured at setup
    await driver.setup()
    await driver.stop()

  async def test_a_run_is_logged_with_its_frames(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
      log = start_session_log(directory)
      try:
        await self.run_setup()
      finally:
        log.stop()
      lines = Path(log.path).read_text(encoding="utf-8").splitlines()
    self.assertTrue(Path(log.path).name.startswith("biomek_i7_"))
    self.assertTrue(all(re.match(r"\d{13} ", line) for line in lines))
    self.assertTrue(any(" MC_1 configure" in line for line in lines))
    self.assertTrue(any(re.search(r" write [0-9a-f]+$", line) for line in lines))
    self.assertTrue(any(re.search(r" read [0-9a-f]+$", line) for line in lines))

  async def test_info_only_leaves_out_the_frames(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
      with start_session_log(directory, usb_frames=False) as log:
        await self.run_setup()
      text = Path(log.path).read_text(encoding="utf-8")
    self.assertIn(" SPAN8_2 configure", text)
    self.assertNotIn(" write ", text)


if __name__ == "__main__":
  unittest.main()
