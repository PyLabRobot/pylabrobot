"""Simulator tests: a simulated VANTAstar set up from the tested reader's recording."""

import unittest
from datetime import datetime

from pylabrobot.bmg_labtech.vantastar.device import (
  RECORDING_VANTASTAR,
  VANTAstar,
  VANTAstarDevice,
)
from pylabrobot.bmg_labtech.vantastar.device_tests import make_device
from pylabrobot.bmg_labtech.vantastar.driver.configuration import SERIAL_NUMBER_PLACEHOLDER
from pylabrobot.bmg_labtech.vantastar.driver.master_tests import FakeUSB
from pylabrobot.bmg_labtech.vantastar.driver.responses_tests import FLUORESCENCE_LAST_PAGE
from pylabrobot.bmg_labtech.vantastar.driver.simulator import VANTAstarSimulationDriver
from pylabrobot.lib.absorbance.results import AbsorbanceResult
from pylabrobot.lib.fluorescence.channels import FluorescenceChannel
from pylabrobot.lib.fluorescence.results import FluorescenceResult
from pylabrobot.lib.luminescence.results import LuminescenceResult
from pylabrobot.resources import cor_96_wellplate_360uL_Fb
from pylabrobot.utils.timing import run_cycles

CHANNEL = FluorescenceChannel(excitation_wavelength=485, emission_wavelength=528)


class SimulatorSetupTests(unittest.IsolatedAsyncioTestCase):
  """Building and setting up a simulated reader."""

  async def test_setup_reads_the_recording(self):
    """A simulated reader defaults to the recording and reads its configuration from it."""
    reader = VANTAstar(simulation=True)
    self.assertIsInstance(reader.driver, VANTAstarSimulationDriver)
    await reader.setup()
    configuration = reader.driver.configuration
    self.assertEqual(configuration.serial_number, SERIAL_NUMBER_PLACEHOLDER)
    self.assertEqual(configuration.spectrometer_maximum_wavelength, 1050)
    self.assertTrue((await reader.driver.request_status()).motors_enabled)
    self.assertIn(
      "vantastar_1_12_temperature_controller.json", reader.driver.format_setup_summary()
    )

  def test_needs_a_configuration_and_no_link(self):
    """The device needs a declared configuration to simulate, and takes no link with it."""
    with self.assertRaises(ValueError):
      VANTAstarDevice(simulation=True)
    with self.assertRaises(ValueError):
      VANTAstarDevice(
        simulation=True, declared_configuration_json=RECORDING_VANTASTAR, io=FakeUSB(bytes)
      )

  async def test_nothing_is_sent(self):
    """A simulated reader sends nothing."""
    reader = VANTAstar(simulation=True)
    with self.assertRaises(NotImplementedError):
      await reader.driver.send_command(0x80)


class SimulatorTests(unittest.IsolatedAsyncioTestCase):
  """A set-up simulated reader with a 96-well plate in it."""

  async def asyncSetUp(self):
    self.reader = VANTAstar(simulation=True)
    await self.reader.setup()
    await self.reader.drawer.open()
    self.plate = cor_96_wellplate_360uL_Fb(name="plate")
    self.reader.plate_holder.assign_child_resource(self.plate)
    await self.reader.drawer.close()

  async def test_drawer_and_plate(self):
    """The drawer moves at once; a plate is detected with the drawer in only."""
    self.assertTrue(await self.reader.drawer.sense_plate_present())
    await self.reader.drawer.open()
    self.assertTrue(await self.reader.drawer.sense_drawer_open())
    self.assertFalse(await self.reader.drawer.sense_plate_present())

  async def test_absorbance(self):
    """Every measured well reads an optical density of 0-3.5 at every wavelength."""
    results = await self.reader.absorbance.measure([450, 600], wells=self.plate.get_items("A1:B2"))
    self.assertEqual([result.wavelength for result in results], [450, 600])
    for result in results:
      self.assertEqual(sorted(result.data), ["A1", "A2", "B1", "B2"])
      self.assertTrue(all(0 <= value <= 3.5 for value in result.data.values()))

  async def test_absorbance_simulated_data(self):
    """Given an earlier result, the measured wells read its values; another wavelength or a
    missing well is refused."""
    earlier = await self.reader.absorbance.measure([450])
    again = await self.reader.absorbance.measure(
      [450], wells=self.plate.get_items(["C3"]), simulated_data=earlier
    )
    self.assertEqual(again[0].data, {"C3": earlier[0].data["C3"]})
    with self.assertRaises(ValueError):
      await self.reader.absorbance.measure([600], simulated_data=earlier)
    partial = AbsorbanceResult(
      data={"A1": 1.0},
      wavelength=450,
      temperature=None,
      start_timestamp=datetime.now(),
      end_timestamp=datetime.now(),
    )
    with self.assertRaises(ValueError):
      await self.reader.absorbance.measure([450], simulated_data=[partial])

  async def test_arguments_are_checked_as_on_a_reader(self):
    """A simulated read refuses what a reader's refuses, and refuses with the drawer out."""
    with self.assertRaises(ValueError):
      await self.reader.absorbance.measure([1100])
    await self.reader.drawer.open()
    with self.assertRaises(RuntimeError):
      await self.reader.absorbance.measure([450])

  async def test_absorbance_spectrum(self):
    """Every measured well reads a spectrum; given an earlier one, its values."""
    spectrum = await self.reader.absorbance.measure_spectrum(
      400, 420, 10, wells=self.plate.get_items(["A1", "H12"])
    )
    self.assertEqual(spectrum.data["H12"].keys(), {400, 410, 420})
    again = await self.reader.absorbance.measure_spectrum(
      400, 410, 10, wells=self.plate.get_items(["A1"]), simulated_data=spectrum
    )
    self.assertEqual(
      again.data, {"A1": {400: spectrum.data["A1"][400], 410: spectrum.data["A1"][410]}}
    )

  async def test_fluorescence(self):
    """Every well reads counts up to 260,000, or 750,000,000 with enhanced dynamic range; one
    result per channel, with its default bandwidths."""
    results = await self.reader.fluorescence.measure([CHANNEL, CHANNEL], gain=1000)
    self.assertEqual(len(results), 2)
    self.assertEqual((results[0].excitation_bandwidth, results[0].emission_bandwidth), (8.0, 12.0))
    self.assertTrue(all(0 <= value <= 260_000 for value in results[0].data.values()))
    edr = await self.reader.fluorescence.measure(CHANNEL, enhanced_dynamic_range=True)
    self.assertTrue(all(0 <= value <= 750_000_000 for value in edr.data.values()))

  async def test_fluorescence_simulated_data(self):
    """Given earlier results, the wells read their counts; results of other optics are refused."""
    earlier = await self.reader.fluorescence.measure(CHANNEL, gain=1000)
    again = await self.reader.fluorescence.measure(CHANNEL, gain=1000, simulated_data=[earlier])
    self.assertEqual(again.data, earlier.data)
    other = FluorescenceResult(
      data=earlier.data,
      excitation_wavelength=450,
      excitation_bandwidth=8.0,
      emission_wavelength=528,
      emission_bandwidth=12.0,
      temperature=None,
      start_timestamp=datetime.now(),
      end_timestamp=datetime.now(),
    )
    with self.assertRaises(ValueError):
      await self.reader.fluorescence.measure(CHANNEL, gain=1000, simulated_data=[other])

  async def test_luminescence(self):
    """Every well reads counts; given an earlier result, its counts."""
    result = await self.reader.luminescence.measure(wells=self.plate.get_items(["A1", "A2"]))
    self.assertEqual(sorted(result.data), ["A1", "A2"])
    given = LuminescenceResult(
      data={"A1": 5.0, "A2": 6.0},
      emission_wavelength=None,
      emission_bandwidth=None,
      temperature=None,
      start_timestamp=datetime.now(),
      end_timestamp=datetime.now(),
    )
    again = await self.reader.luminescence.measure(
      wells=self.plate.get_items(["A2"]), simulated_data=given
    )
    self.assertEqual(again.data, {"A2": 6.0})

  async def test_not_simulated(self):
    """Fluorescence spectra, the gain and focus adjustment, z-profiles and run-time estimates are
    not simulated."""
    fluorescence = self.reader.fluorescence
    for call in (
      lambda: fluorescence.measure_spectrum("emission", 500, 600, gain=1000),
      lambda: fluorescence.adjust_gain_and_focus(CHANNEL, wells=self.plate.get_items(["A1"])),
      lambda: fluorescence.measure_z_profile(CHANNEL, well=self.plate.get_well("A1")),
      lambda: self.reader.estimate_duration(lambda: self.reader.absorbance.measure([450])),
    ):
      with self.assertRaises(NotImplementedError):
        await call()

  async def test_temperature_controller(self):
    """The temperature controller reaches its target at once, and reads report it; without a
    target there is no temperature."""
    controller = self.reader.temperature_controller
    with self.assertRaises(NotImplementedError):
      await controller.measure_temperature()
    await controller.start_temperature_control(30.0)
    self.assertEqual(await controller.wait_for_temperature(), 30.0)
    results = await self.reader.absorbance.measure([450], wells=self.plate.get_items(["A1"]))
    self.assertEqual(results[0].temperature, 30.0)
    await controller.stop_temperature_control()
    self.assertIsNone(controller.target_temperature)

  async def test_shaker(self):
    """The shaker shakes at once, alone or before a read."""
    await self.reader.shaker.shake("orbital", speed=300, duration=5)

  async def test_stop_with_a_plate_left_in(self):
    """Stop refuses with a plate in the reader and opens the drawer, unless allowed."""
    with self.assertRaises(RuntimeError):
      await self.reader.stop()
    self.assertTrue(await self.reader.drawer.sense_drawer_open())
    await self.reader.setup()
    await self.reader.drawer.close()
    await self.reader.stop(accept_plate_left_in_device=True)

  async def test_run_cycles(self):
    """A simulated read runs in run_cycles."""
    cycles = await run_cycles(
      cycle_function=lambda cycle: self.reader.absorbance.measure(
        [450], wells=self.plate.get_items(["A1"])
      ),
      cycle_time=0.01,
      cycles=3,
    )
    self.assertEqual(len(cycles), 3)


class ReaderIgnoresSimulatedDataTests(unittest.IsolatedAsyncioTestCase):
  async def test_a_reader_ignores_simulated_data(self):
    """A reader's read takes ``simulated_data`` and returns what it read."""
    channel = FluorescenceChannel(
      excitation_wavelength=365,
      excitation_bandwidth=8,
      emission_wavelength=400,
      emission_bandwidth=10,
    )
    read = await make_device([FLUORESCENCE_LAST_PAGE]).fluorescence.measure(
      channel, focal_height=5.0, gain=500
    )
    fake = FluorescenceResult(
      data={well: -1.0 for well in read.data},
      excitation_wavelength=365,
      excitation_bandwidth=8,
      emission_wavelength=400,
      emission_bandwidth=10,
      temperature=None,
      start_timestamp=datetime.now(),
      end_timestamp=datetime.now(),
    )
    again = await make_device([FLUORESCENCE_LAST_PAGE]).fluorescence.measure(
      channel, focal_height=5.0, gain=500, simulated_data=[fake]
    )
    self.assertEqual(again.data, read.data)


if __name__ == "__main__":
  unittest.main()
