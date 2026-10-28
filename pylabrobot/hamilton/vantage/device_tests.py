import unittest
from typing import List

from pylabrobot.hamilton.transport.text_tcp import DEFAULT_PORT
from pylabrobot.hamilton.vantage import (
  Vantage,
  VantageDevice,
  VantageDriver,
  VantageSimulationDriver,
)
from pylabrobot.io.usb import USB
from pylabrobot.resources.hamilton.vantage_decks import VantageDeck


class TestFactory(unittest.TestCase):
  def test_a_simulated_vantage_has_a_simulation_driver_and_a_default_deck(self):
    vantage = Vantage(simulation=True)
    self.assertIsInstance(vantage, VantageDevice)
    self.assertIsInstance(vantage.driver, VantageSimulationDriver)
    self.assertEqual(vantage.name, "vantage")
    self.assertEqual(vantage.model, "Vantage")
    self.assertEqual(vantage.deck.name, "vantage_deck")
    self.assertEqual(vantage.deck.size, 1.3)  # type: ignore[attr-defined]

  def test_the_default_deck_takes_its_name_from_the_device(self):
    self.assertEqual(Vantage(simulation=True, name="lab").deck.name, "lab_deck")

  def test_a_deck_that_is_given_keeps_its_name(self):
    deck = VantageDeck(size=1.3, name="my_deck")
    vantage = Vantage(deck=deck, simulation=True)
    self.assertIs(vantage.deck, deck)
    self.assertEqual(deck.name, "my_deck")

  def test_without_simulation_the_driver_reaches_a_machine_over_usb(self):
    vantage = Vantage()
    self.assertIs(type(vantage.driver), VantageDriver)
    self.assertIsInstance(vantage.driver.io, USB)

  def test_a_host_is_given_to_the_driver_with_the_default_port(self):
    driver = Vantage(host="127.0.0.1").driver
    self.assertEqual((driver._host, driver._port), ("127.0.0.1", DEFAULT_PORT))

  def test_the_port_can_be_chosen(self):
    driver = Vantage(host="127.0.0.1", port=1234).driver
    self.assertEqual((driver._host, driver._port), ("127.0.0.1", 1234))

  def test_a_driver_that_is_given_is_the_one_driven(self):
    driver = VantageSimulationDriver()
    self.assertIs(Vantage(driver=driver).driver, driver)

  def test_a_host_with_simulation_is_refused(self):
    with self.assertRaises(ValueError):
      Vantage(host="127.0.0.1", simulation=True)

  def test_a_host_with_a_driver_is_refused(self):
    with self.assertRaises(ValueError):
      Vantage(host="127.0.0.1", driver=VantageSimulationDriver())

  def test_a_driver_with_simulation_warns_and_drives_the_driver(self):
    driver = VantageDriver()
    with self.assertLogs("pylabrobot.hamilton.vantage.device", "WARNING"):
      vantage = Vantage(driver=driver, simulation=True)
    self.assertIs(vantage.driver, driver)


class TestDevice(unittest.TestCase):
  def test_the_deck_is_a_child_of_the_device(self):
    vantage = Vantage(simulation=True)
    self.assertEqual(vantage.children, [vantage.deck])
    self.assertIs(vantage.deck.parent, vantage)

  def test_the_device_is_as_big_as_its_deck(self):
    vantage = Vantage(simulation=True)
    deck = vantage.deck
    self.assertEqual(
      (vantage.get_size_x(), vantage.get_size_y(), vantage.get_size_z()),
      (deck.get_size_x(), deck.get_size_y(), deck.get_size_z()),
    )

  def test_the_driver_models_into_the_devices_deck(self):
    vantage = Vantage(simulation=True)
    self.assertIs(vantage.driver.deck, vantage.deck)

  def test_a_driver_with_another_deck_is_told_so_and_uses_the_devices(self):
    driver = VantageSimulationDriver(deck=VantageDeck(size=1.3, name="other"))
    with self.assertLogs("pylabrobot.hamilton.vantage.device", "WARNING"):
      vantage = Vantage(driver=driver)
    self.assertIs(driver.deck, vantage.deck)

  def test_the_features_are_not_there_before_the_machine_is_discovered(self):
    vantage = Vantage(simulation=True)
    self.assertIsNone(vantage.pipettes)
    self.assertIsNone(vantage.ipg)

  def test_it_names_itself_with_its_driver_and_deck(self):
    self.assertEqual(
      str(Vantage(simulation=True)), "vantage(VantageSimulationDriver, 54-track deck)"
    )


class TestSession(unittest.IsolatedAsyncioTestCase):
  async def test_setup_brings_a_simulated_machine_up(self):
    vantage = Vantage(simulation=True)
    await vantage.setup()
    try:
      driver = vantage.driver
      assert isinstance(driver, VantageSimulationDriver)
      self.assertTrue(driver.arm_up and driver.ipg_up and driver.ipg_parked and driver.channels_up)
      self.assertIs(vantage.pipettes, driver.pipettes)
      self.assertIsNotNone(vantage.ipg)
      self.assertEqual(vantage.pipettes.num_channels, 8)  # type: ignore[union-attr]
    finally:
      await vantage.stop()
    self.assertFalse(vantage.driver.connected)

  async def test_connect_and_discover_move_nothing(self):
    vantage = Vantage(simulation=True)
    await vantage.connect()
    tips: List[bool] = await vantage.discover()
    self.assertEqual(tips, [False] * 8)
    driver = vantage.driver
    assert isinstance(driver, VantageSimulationDriver)
    self.assertFalse(driver.arm_up or driver.ipg_up or driver.channels_up)
    self.assertIsNotNone(vantage.ipg)
    await vantage.stop()

  async def test_a_machine_without_an_ipg_has_none(self):
    vantage = Vantage(driver=VantageSimulationDriver(ipg_installed=False))
    await vantage.setup()
    self.assertIsNone(vantage.ipg)
    await vantage.stop()

  async def test_a_simulated_machine_can_be_set_up_again_after_it_was_stopped(self):
    vantage = Vantage(simulation=True)
    await vantage.setup()
    await vantage.stop()
    await vantage.setup()
    self.assertTrue(vantage.driver.connected)
    await vantage.stop()


class TestSerialization(unittest.TestCase):
  def test_the_device_serializes_with_its_deck(self):
    data = Vantage(simulation=True).serialize()
    self.assertEqual(data["name"], "vantage")
    self.assertEqual([child["name"] for child in data["children"]], ["vantage_deck"])


if __name__ == "__main__":
  unittest.main()
