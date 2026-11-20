"""The check that ties a declared configuration to one instrument: its serial numbers, on the
instrument's own path. The replayed generic cold start answers placeholder serial numbers, as long
as real ones; a configuration copy naming them stands for that instrument's own.

The instrument's path (no `boards` given) is taken with the replayed boards attached where `_open`
would open the USB devices, so `discover` runs exactly as on the instrument."""

import asyncio
import dataclasses
import unittest
from typing import Iterator, List
from unittest import mock

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import DeviceConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import ReplayAceUSB
from pylabrobot.beckman_coulter.biomek_i7.simulation_tests_support import (
  GENERIC_SERIAL_NUMBERS,
  as_instrument,
  configuration_file,
  generic_boards,
  generic_configuration,
  generic_driver,
  sealed,
)

READS = {name for name in p.COMMANDS.values() if name.startswith(("Get ", "Read ", "List "))}


class InstrumentPathTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)
    self.boards: List[AceBoard] = []

  def driver(self, configuration: DeviceConfiguration) -> BiomekI7Driver:
    driver = BiomekI7Driver(declared_configuration_json=configuration_file(configuration))
    self.boards = boards = generic_boards()

    async def attach() -> None:
      driver.boards = list(boards)
      driver._connected = True
      for board in driver.boards:
        await board.setup()

    patcher = mock.patch.object(driver, "_open", attach)
    patcher.start()
    self.addCleanup(patcher.stop)
    self.addAsyncCleanup(driver.stop)
    return driver

  def requests(self) -> Iterator[str]:
    for board in self.boards:
      assert isinstance(board.io, ReplayAceUSB)
      for message in board.io.requests:
        yield p.COMMANDS[(message[0], message[4])]

  async def test_the_instruments_own_configuration_is_accepted(self) -> None:
    driver = self.driver(as_instrument(generic_configuration()))
    await driver.discover()
    self.assertEqual(await driver.request_serial_numbers(), GENERIC_SERIAL_NUMBERS)
    self.assertEqual(set(self.requests()) - READS, set())

  async def test_another_instruments_serial_number_is_refused_before_anything_is_written(
    self,
  ) -> None:
    config = as_instrument(generic_configuration())
    config.pods["right"] = dataclasses.replace(config.pods["right"], module_serial_number="X" * 32)
    driver = self.driver(sealed(config))
    with self.assertRaisesRegex(ValueError, "right pod module X{32}, found SIMULATED009"):
      await driver.setup()
    self.assertEqual(set(self.requests()) - READS, set())

  async def test_another_chassis_is_refused(self) -> None:
    config = as_instrument(generic_configuration())
    config.chassis_serial_number = "Y" * 32
    with self.assertRaisesRegex(ValueError, "chassis Y{32}, found SIMULATED001"):
      await self.driver(sealed(config)).discover()

  async def test_the_generic_configuration_is_refused_even_with_matching_numbers(self) -> None:
    config = as_instrument(generic_configuration())
    config.generic = True
    with self.assertRaisesRegex(ValueError, "generic"):
      await self.driver(sealed(config)).discover()

  async def test_the_generic_configuration_is_refused(self) -> None:
    with self.assertRaisesRegex(ValueError, "generic configuration.*chassis not recorded"):
      await self.driver(generic_configuration()).discover()


class SimulationTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    asyncio.get_running_loop().set_debug(False)

  async def test_a_simulation_is_not_tied_to_an_instrument(self) -> None:
    driver = generic_driver()  # the generic configuration, no serial numbers
    self.addAsyncCleanup(driver.stop)
    await driver.discover()
    self.assertTrue(driver.declared.generic)


if __name__ == "__main__":
  unittest.main()
