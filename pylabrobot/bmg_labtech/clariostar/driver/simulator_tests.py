"""Simulator tests, and the recording it takes its configuration from."""

import os
import tempfile
import unittest

import pytest

from pylabrobot.bmg_labtech.clariostar.device import (
  RECORDING_CLARIOSTAR_PLUS,
  CLARIOstar,
  CLARIOstarPlus,
)
from pylabrobot.bmg_labtech.clariostar.driver.configuration import (
  SERIAL_NUMBER_PLACEHOLDER,
  compute_recording_name,
  configuration_from_json,
  configuration_to_json,
  read_configuration,
)
from pylabrobot.bmg_labtech.clariostar.driver.configuration_tests import decoded
from pylabrobot.bmg_labtech.clariostar.driver.master_tests import (
  PARAMETERS_REPLY,
  STATUS_IDLE,
  FakeFTDI,
  load_session_replies,
  make_driver,
  parse_status,
)
from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.lib.results.reading import load_rows
from pylabrobot.lib.results.table import get_table
from pylabrobot.resources import Coordinate, Plate
from pylabrobot.resources.biorad.plates import biorad_96_wellplate_200uL_Vb
from pylabrobot.resources.corning.plates import cor_96_wellplate_2mL_Vb
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.utils.timing import run_cycles

LOCATION_IN = Coordinate(10, 20, 30)  # made up: the real ones are not measured yet
LOCATION_OUT = Coordinate(10, -200, 30)


def make_plate(name: str) -> Plate:
  return biorad_96_wellplate_200uL_Vb(name)


async def decoded_with_parameters():
  """The configuration the recorded INIT and stored-parameters replies decode to."""
  io = FakeFTDI()
  io.replies = [STATUS_IDLE, PARAMETERS_REPLY]
  configuration = decoded()
  configuration.update_from_parameters(await make_driver(io).request_parameters())
  return configuration


class TestRecording(AnyioTestBase):
  async def test_recording_is_the_decoded_replies(self):
    saved = read_configuration(RECORDING_CLARIOSTAR_PLUS)["device"]
    assert saved == configuration_to_json(await decoded_with_parameters())

  def test_recording_is_named_from_its_contents(self):
    configuration = configuration_from_json(read_configuration(RECORDING_CLARIOSTAR_PLUS)["device"])
    assert compute_recording_name(configuration) == os.path.basename(RECORDING_CLARIOSTAR_PLUS)

  async def test_read_back_equals_decoded(self):
    configuration = configuration_from_json(read_configuration(RECORDING_CLARIOSTAR_PLUS)["device"])
    expected = await decoded_with_parameters()
    expected.serial_number = SERIAL_NUMBER_PLACEHOLDER
    assert configuration == expected


class TestSimulator(AnyioTestBase):
  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    await super()._enter_lifespan(stack)
    self.reader = CLARIOstarPlus(simulation=True)
    drawer = self.reader.drawer
    drawer.location_in, drawer.location_out = LOCATION_IN, LOCATION_OUT
    await stack.enter_async_context(self.reader.lifespan(accept_plate_left_in_device=True))

  async def test_setup_reads_the_recording(self):
    configuration = self.reader.driver.configuration
    assert configuration.model == "CLARIOstarPlus"
    assert configuration.serial_number == SERIAL_NUMBER_PLACEHOLDER
    assert (await self.reader.driver.request_status()).motors_enabled

  async def test_drawer_moves_the_plate_holder(self):
    await self.reader.drawer.open()
    assert await self.reader.drawer.sense_drawer_open()
    assert self.reader.plate_holder.location == LOCATION_OUT
    await self.reader.drawer.close()
    assert not await self.reader.drawer.sense_drawer_open()
    assert self.reader.plate_holder.location == LOCATION_IN

  async def test_drawer_position_is_read_from_the_resource_model(self):
    self.reader.plate_holder.location = LOCATION_OUT
    assert (await self.reader.driver.request_status()).drawer_open
    self.reader.plate_holder.location = LOCATION_IN
    assert not (await self.reader.driver.request_status()).drawer_open

  async def test_a_plate_goes_on_the_plate_holder_only(self):
    with pytest.raises(RuntimeError):
      self.reader.assign_child_resource(make_plate("plate"), location=Coordinate.zero())
    assert self.reader.children == [self.reader.plate_holder]

  async def test_a_plate_needs_the_drawer_sensed_open(self):
    holder = self.reader.plate_holder
    with pytest.raises(RuntimeError):  # not sensed yet
      holder.assign_child_resource(make_plate("plate"))
    await self.reader.drawer.close()
    with pytest.raises(RuntimeError):  # sensed closed
      holder.assign_child_resource(make_plate("plate"))
    await self.reader.drawer.open()
    plate = make_plate("plate")
    holder.assign_child_resource(plate)
    assert holder.resource is plate

  async def test_a_second_plate_is_refused(self):
    await self.reader.drawer.open()
    self.reader.plate_holder.assign_child_resource(make_plate("first"))
    with pytest.raises(RuntimeError):
      self.reader.plate_holder.assign_child_resource(make_plate("second"))
    assert [c.name for c in self.reader.plate_holder.children] == ["first"]

  async def test_a_plate_comes_off_only_with_the_drawer_open(self):
    await self.reader.drawer.open()
    plate = make_plate("plate")
    self.reader.plate_holder.assign_child_resource(plate)
    await self.reader.drawer.close()
    with pytest.raises(RuntimeError):
      plate.unassign()
    await self.reader.drawer.open()
    plate.unassign()
    assert self.reader.plate_holder.resource is None

  async def test_stop_with_a_plate_inside_opens_the_drawer_and_raises(self):
    await self.reader.drawer.open()
    self.reader.plate_holder.assign_child_resource(make_plate("plate"))
    await self.reader.drawer.close()
    with pytest.raises(RuntimeError):
      await self.reader.driver._shut_down(accept_plate_left_in_device=False)
    assert await self.reader.drawer.sense_drawer_open()
    await self.reader.driver._shut_down(accept_plate_left_in_device=True)

  async def test_stop_with_a_plate_too_tall_raises_and_leaves_the_drawer_open(self):
    await self.reader.drawer.open()
    tall = cor_96_wellplate_2mL_Vb("deep_well")  # 43.5 mm
    self.reader.plate_holder.assign_child_resource(tall)
    with pytest.raises(ValueError):
      await self.reader.driver._shut_down(accept_plate_left_in_device=False)
    assert await self.reader.drawer.sense_drawer_open()
    tall.unassign()
    await self.reader.driver._shut_down(accept_plate_left_in_device=False)

  async def test_stop_switches_heating_off_and_closes_the_drawer(self):
    temperature_controller = self.reader.temperature_controller
    assert temperature_controller is not None
    await temperature_controller.start_temperature_control(37.0)
    await self.reader.drawer.open()
    await self.reader.driver._shut_down(accept_plate_left_in_device=False)
    assert temperature_controller.target_temperature is None
    assert not await self.reader.drawer.sense_drawer_open()

  async def test_plate_present_needs_a_plate_and_the_drawer_in(self):
    assert not await self.reader.drawer.sense_plate_present()
    await self.reader.drawer.open()
    self.reader.plate_holder.assign_child_resource(
      Plate("plate", size_x=127.76, size_y=85.48, size_z=14.0, ordered_items={}),
      location=Coordinate.zero(),
    )
    simulated = await self.reader.driver.request_status()
    # As the reader answered with the plate on the plate holder and the drawer out.
    real = parse_status(bytes.fromhex(load_session_replies()["status_drawer_out_plate_on_holder"]))
    assert (simulated.drawer_open, simulated.plate_detected) == (
      real.drawer_open,
      real.plate_detected,
    )
    await self.reader.drawer.close()
    assert await self.reader.drawer.sense_plate_present()

  async def test_convenience_requests_answer_from_the_recording(self):
    driver = self.reader.driver
    assert await driver.request_absorbance_available()
    assert await driver.request_fluorescence_channels() == 1
    assert not await driver.request_well_1536_plates_supported()

  async def test_reads_in_a_run_carry_their_cycle_on_the_result_in_the_table_and_the_rows(self):
    await self.reader.drawer.open()
    self.reader.plate_holder.assign_child_resource(make_plate("plate"))
    await self.reader.drawer.close()
    absorbance = self.reader.absorbance
    assert absorbance is not None
    directory = tempfile.mkdtemp()

    async def read(cycle: int):
      (result,) = await absorbance.measure(wavelengths=[600], results_directory=directory)
      return result

    results = [r for r in await run_cycles(read, cycle_time=0.05, cycles=2) if r is not None]
    assert [result.current_cycle["cycle"] for result in results] == [0, 1]
    assert [row["cycle"] for row in get_table(results)[::96]] == [0, 1]  # after the run
    rows = load_rows(directory)
    assert [row["cycle"] for row in rows[::96]] == [0, 1]
    due = results[1].current_cycle["due_timestamp"]
    assert rows[96]["due_timestamp"] == due.isoformat()
    (outside,) = await absorbance.measure(wavelengths=[600])
    assert outside.current_cycle["cycle"] is None

  async def test_a_simulated_read_with_the_drawer_out_is_refused(self):
    await self.reader.drawer.open()
    self.reader.plate_holder.assign_child_resource(make_plate("plate"))
    absorbance, luminescence = self.reader.absorbance, self.reader.luminescence
    assert absorbance is not None and luminescence is not None
    reads = {
      "absorbance": lambda: absorbance.measure(wavelengths=[600]),
      "spectrum": lambda: absorbance.measure_spectrum(
        start_wavelength=300, end_wavelength=310, step_size=1
      ),
      "luminescence": lambda: luminescence.measure(),
    }
    for name, read in reads.items():
      with pytest.raises(RuntimeError, match="the drawer is open"):
        await read()

  async def test_nothing_reaches_a_wire(self):
    driver = self.reader.driver
    with pytest.raises(NotImplementedError):
      await driver.send_command(driver.CommandFamily.STATUS)
    with pytest.raises(NotImplementedError):
      await driver.request_parameters()


class TestSimulatorWithoutLocations(AnyioTestBase):
  async def test_drawer_falls_back_to_the_last_move(self):
    reader = CLARIOstarPlus(simulation=True)
    async with reader:
      await reader.drawer.open()
      assert (await reader.driver.request_status()).drawer_open

  async def test_convenience_request_before_setup_raises(self):
    with pytest.raises(RuntimeError):
      await CLARIOstarPlus(simulation=True).driver.request_absorbance_available()


class TestSimulatorConstruction(AnyioTestBase):
  def test_clariostar_has_no_recording(self):
    with pytest.raises(ValueError):
      CLARIOstar(simulation=True)

  def test_simulation_takes_no_link(self):
    with pytest.raises(ValueError):
      CLARIOstarPlus(simulation=True, io=FakeFTDI())


if __name__ == "__main__":
  unittest.main()
