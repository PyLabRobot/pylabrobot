"""What a user holds: one class per model, and the capability objects it exposes.

Each model is built against a fake instrument, so setup, the plate, the palette and the capability
objects are exercised without hardware. What a capability method does is one step run as a one-step
protocol, and that is what is checked here -- the payloads themselves are the cross-reference's job.
"""

from __future__ import annotations

import unittest

from pylabrobot.agilent.biotek.lhc import EL406, MultiFlo, MultiFloFX, Washer405TS
from pylabrobot.agilent.biotek.lhc.devices.components.peristaltic_dispenser import (
  PeristalticDispenser,
)
from pylabrobot.agilent.biotek.lhc.devices.components.syringe_dispenser import SyringeDispenser
from pylabrobot.agilent.biotek.lhc.devices.components.washer import PlateWasher
from pylabrobot.agilent.biotek.lhc.devices.handshake import BASECODE_PART_NUMBERS
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.enums.instrument.basecode import Basecode
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.instrument.strip_washer_manifold import (
  StripWasherManifold,
)
from pylabrobot.agilent.biotek.lhc.enums.plates.plate_type import PlateType
from pylabrobot.agilent.biotek.lhc.enums.steps.step_action import StepAction
from pylabrobot.agilent.biotek.lhc.enums.steps.step_type import StepType
from pylabrobot.agilent.biotek.lhc.error_handling import (
  SETTINGS_DATA_TOO_OLD,
  WRONG_BASECODE_PART_NUMBER,
  FirmwareError,
  RejectedError,
)
from pylabrobot.agilent.biotek.lhc.protocols.protocol import Protocol, ProtocolEntry
from pylabrobot.agilent.biotek.lhc.protocols.steps.step_parts.positioning import Positioning
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_aspirate import ManifoldAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_dispense import ManifoldDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_prime import ManifoldPrime
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.manifold_wash import ManifoldWash
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_dispense import PeriDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_wash_aspirate import PeriWashAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.peri_wash_dispense import PeriWashDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_aspirate import StripAspirate
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.strip_dispense import StripDispense
from pylabrobot.agilent.biotek.lhc.protocols.steps.steps.syringe_dispense import SyringeDispense
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber
from pylabrobot.agilent.biotek.lhc.tests.helpers import (
  ACCEPTS_EVERY_PLATE,
  FakeInstrument,
  make_plate,
)

_VERSION = CommandNumber.GET_BASECODE_VERSION


def _part_number(family: InstrumentFamily) -> bytes:
  """The basecode part number a model of one family reports.

  Args:
    family: Which family the model belongs to.

  Returns:
    The seven characters, of which the first three are what the handshake reads.
  """
  return BASECODE_PART_NUMBERS[family].encode() + b"0000"


def _version(part_number: bytes | None = None, data_version: bytes = b"103  ") -> bytes:
  """A firmware version record, with the two fields the handshake reads settable.

  Args:
    part_number: The seven-character basecode part number, defaulting to the original model's.
    data_version: The five-character settings data version.

  Returns:
    The record as the instrument writes it.
  """
  if part_number is None:
    part_number = _part_number(InstrumentFamily.EL406)
  return part_number + b"2.22.6  " + b"ABCD" + b"DCBA" + data_version + b"1.0" + b"2.0" + b" " * 12


ANSWERS = {
  CommandNumber.GET_SYRINGE_MANIFOLD_INSTALLED: bytes([1]),
  CommandNumber.GET_SYRINGE_BOX_INFO: bytes([1, 2]),
  CommandNumber.GET_SELECTED_PERI_INSTALLED: bytes([1]),
  CommandNumber.GET_WASHER_MANIFOLD_INSTALLED: bytes([0]),
  CommandNumber.GET_EXT_VALVE_MODULE_INSTALLED: bytes([1]),
  CommandNumber.GET_VACUUM_FILTRATION_INSTALLED: bytes([0]),
  CommandNumber.GET_ULTRASONIC_CLEANER_INSTALLED: bytes([1]),
  CommandNumber.GET_CELL_WASHING_INSTALLED: bytes([1]),
  CommandNumber.GET_IS_PERI_HALF_UL_SUPPORTED: bytes([1]),
  CommandNumber.GET_Y_AXIS_INSTALLED: bytes([1]),
  CommandNumber.GET_SERIAL_NUMBER: b"SN0001".ljust(24),
  _VERSION: _version(),
  CommandNumber.IS_STRIP_WASHER_BOX_CONNECTED: bytes([0]),
  CommandNumber.GET_STRIP_WASHER_HW_INSTALLED: bytes([0]),
  CommandNumber.GET_WHICH_BASECODE_IS_INSTALLED: bytes([0]),
  CommandNumber.GET_FLUID_TRACKING_ENABLED: bytes([1]),
  **ACCEPTS_EVERY_PLATE,
}
"""What the fake instrument answers, which is a fully equipped instrument of the original model."""

PUMP_READY = {**ANSWERS, CommandNumber.GET_SELECTED_PERI_STATE: bytes([2])}
"""The same instrument, with its peristaltic pumps in a state to turn, which is what opening a
batch for a step that drives one checks before letting it run."""

PERI_WASHING = {
  **PUMP_READY,
  CommandNumber.GET_WHICH_BASECODE_IS_INSTALLED: bytes([int(Basecode.PERI_WASH)]),
}
"""An instrument running the firmware that carries the peristaltic wash step types. Which
variant is installed is only asked of the newest model, so this is only worth answering for
one."""

STRIP_WASHING = {
  **PUMP_READY,
  CommandNumber.IS_STRIP_WASHER_BOX_CONNECTED: bytes([1]),
  CommandNumber.GET_STRIP_WASHER_HW_INSTALLED: bytes([1]),
  CommandNumber.GET_STRIP_WASHER_MANIFOLD_TYPE: bytes([int(StripWasherManifold.PLATE_96_WELL)]),
}
"""An instrument with a strip washer fitted, carrying the manifold that works 96-well and
384-well plates."""


class DeviceTestCase(unittest.IsolatedAsyncioTestCase):
  """A model built onto a fake instrument."""

  async def build(self, cls, wells: int = 96, answers: dict | None = None):
    """Build a model, set it up and give it a plate.

    Args:
      cls: Which model to build.
      wells: How many wells the plate has.
      answers: What the instrument answers, defaulting to a fully equipped one.

    Returns:
      The device and the fake instrument behind it.
    """
    answers = dict(ANSWERS if answers is None else answers)
    # A test that named its own version record keeps it; every other one gets the record a model of
    # this family reports, since the handshake refuses one built for another family.
    if answers.get(_VERSION) == _version():
      answers[_VERSION] = _version(part_number=_part_number(cls.family))
    io = FakeInstrument(answers=answers)
    device = cls(port="fake", io=io)
    # The fake instrument answers at once, so none of the pacing a real one needs is wanted here.
    device.settle = 0
    await device.setup()
    device.set_plate(make_plate(wells))
    return device, io


class TestTheLifecycle(DeviceTestCase):
  """Opening a device, and what it learns while doing so."""

  async def test_setup_reads_what_is_fitted(self):
    """Setup reads what is fitted."""
    device, _ = await self.build(EL406)
    self.assertIs(device.settings.family, InstrumentFamily.EL406)
    self.assertTrue(device.settings.peri_pump)

  async def test_setup_proves_something_is_listening_before_reading_anything(self):
    """Setup proves something is listening before reading anything."""
    _, io = await self.build(EL406)
    self.assertEqual(io.sent[0], int(CommandNumber.PING))

  async def test_setup_reads_the_version_record_next(self):
    """Setup reads the version record next, which is what says what is listening."""
    _, io = await self.build(EL406)
    self.assertEqual(io.sent[1], int(CommandNumber.GET_BASECODE_VERSION))

  async def test_setup_refuses_a_basecode_built_for_another_family(self):
    """Setup refuses a basecode built for another family."""
    answers = {**ANSWERS, _VERSION: _version(part_number=_part_number(InstrumentFamily.MULTIFLO))}
    with self.assertRaises(FirmwareError) as raised:
      await self.build(Washer405TS, answers=answers)
    self.assertEqual(raised.exception.code, WRONG_BASECODE_PART_NUMBER)

  async def test_setup_accepts_the_basecode_of_the_model_being_driven(self):
    """Setup accepts the basecode of the model being driven."""
    answers = {**ANSWERS, _VERSION: _version(part_number=b"1170202")}
    device, _ = await self.build(Washer405TS, answers=answers)
    self.assertIs(device.settings.family, InstrumentFamily.MODEL_405_TS)

  async def test_setup_refuses_settings_data_older_than_it_reads(self):
    """Setup refuses settings data older than this package reads."""
    answers = {**ANSWERS, _VERSION: _version(data_version=b"99   ")}
    with self.assertRaises(FirmwareError) as raised:
      await self.build(EL406, answers=answers)
    self.assertEqual(raised.exception.code, SETTINGS_DATA_TOO_OLD)

  async def test_setup_refuses_firmware_that_keeps_no_version_record(self):
    """Setup refuses firmware that keeps no version record."""
    with self.assertRaises(FirmwareError) as raised:
      await self.build(EL406, answers={**ANSWERS, _VERSION: b""})
    self.assertEqual(raised.exception.code, WRONG_BASECODE_PART_NUMBER)

  async def test_a_device_reports_its_own_name(self):
    """A device reports its own name."""
    device, _ = await self.build(EL406)
    self.assertEqual(device.name, "EL406")

  async def test_stopping_closes_the_link(self):
    """Stopping closes the link."""
    device, io = await self.build(EL406)
    await device.stop()
    self.assertFalse(io.is_open)

  async def test_the_serial_number_and_the_firmware_can_be_read(self):
    """The serial number and the firmware can be read."""
    device, _ = await self.build(EL406)
    self.assertEqual(await device.get_serial_number(), "SN0001")
    self.assertEqual((await device.get_firmware_version()).software_version.strip(), "2.22.6")


class TestThePlate(DeviceTestCase):
  """Telling a device what is on its carrier."""

  async def test_a_plate_is_resolved_to_a_format_the_model_works(self):
    """A plate is resolved to a format the model works."""
    device, _ = await self.build(EL406)
    self.assertIsNotNone(device.plate)
    self.assertIs(device.plate.plate_type, PlateType.PLATE_96_WELL)

  async def test_a_format_can_be_named_outright(self):
    """A format can be named outright."""
    device, _ = await self.build(EL406)
    device.set_plate(make_plate(384), plate_type=PlateType.PLATE_384_WELL_PCR)
    self.assertIs(device.plate.plate_type, PlateType.PLATE_384_WELL_PCR)

  async def test_a_plate_the_model_does_not_work_is_an_error_naming_what_it_does(self):
    """A plate the model does not work is an error naming what it does."""
    device, _ = await self.build(Washer405TS)
    with self.assertRaisesRegex(ValueError, "it offers"):
      device.set_plate(make_plate(6))

  async def test_forgetting_the_plate_stops_everything(self):
    """Forgetting the plate stops everything."""
    device, _ = await self.build(EL406)
    device.clear_plate()
    self.assertIsNone(device.plate)
    with self.assertRaises(Exception):
      await device.run_step(ManifoldPrime())


class TestThePalette(DeviceTestCase):
  """What a model offers, which is its own list narrowed by what is fitted."""

  async def test_a_wash_only_model_offers_no_dispensing(self):
    """A wash only model offers no dispensing."""
    device, _ = await self.build(Washer405TS)
    offered = device.get_available_steps()
    self.assertIn(StepType.MANIFOLD_WASH, offered)
    self.assertNotIn(StepType.SYRINGE_DISPENSE, offered)
    self.assertNotIn(StepType.PERI_DISPENSE, offered)

  async def test_a_dispenser_only_model_offers_no_washing(self):
    """A dispenser only model offers no washing."""
    device, _ = await self.build(MultiFlo)
    offered = device.get_available_steps()
    self.assertIn(StepType.PERI_DISPENSE, offered)
    self.assertNotIn(StepType.MANIFOLD_WASH, offered)

  async def test_hardware_that_is_not_fitted_is_not_offered(self):
    """The instrument reporting no peristaltic pump takes those step types out of the palette."""
    answers = dict(ANSWERS)
    answers[CommandNumber.GET_SELECTED_PERI_INSTALLED] = bytes([0])
    device, _ = await self.build(EL406, answers=answers)
    self.assertFalse(device.settings.peri_pump)
    self.assertNotIn(StepType.PERI_DISPENSE, device.get_available_steps())

  async def test_the_palette_is_in_the_order_the_model_offers_them(self):
    """Not the order the types are numbered: a model lists what it is for first, which is what a
    user reading the palette wants to see."""
    device, _ = await self.build(EL406)
    offered = device.get_available_steps()
    self.assertEqual(offered[0], StepType.MANIFOLD_WASH)
    self.assertNotEqual(offered, sorted(offered, key=lambda member: member.value))
    self.assertEqual(len(set(offered)), len(offered))


class TestTheCapabilityObjects(DeviceTestCase):
  """Which capability objects each model exposes, and what calling one does."""

  async def test_a_wash_only_model_exposes_only_a_washer(self):
    """A wash only model exposes only a washer."""
    device, _ = await self.build(Washer405TS)
    self.assertIsInstance(device.washer, PlateWasher)
    with self.assertRaises(AttributeError):
      device.syringe_dispenser  # noqa: B018 - the point is that it is not there

  async def test_a_dispenser_only_model_exposes_no_washer(self):
    """A dispenser only model exposes no washer."""
    device, _ = await self.build(MultiFlo)
    self.assertIsInstance(device.syringe_dispenser, SyringeDispenser)
    self.assertIsInstance(device.peristaltic_dispenser, PeristalticDispenser)
    with self.assertRaises(AttributeError):
      device.washer  # noqa: B018 - the point is that it is not there

  async def test_the_newest_model_exposes_all_three(self):
    """The newest model exposes all three."""
    device, _ = await self.build(MultiFloFX)
    self.assertIsInstance(device.washer, PlateWasher)
    self.assertIsInstance(device.syringe_dispenser, SyringeDispenser)
    self.assertIsInstance(device.peristaltic_dispenser, PeristalticDispenser)

  async def test_one_call_brackets_itself_in_a_batch(self):
    """One call brackets itself in a batch."""
    device, io = await self.build(EL406)
    await device.washer.prime(volume=40_000)
    self.assertEqual(io.sent.count(int(CommandNumber.INIT_PROTOCOL)), 1)
    self.assertEqual(io.sent.count(int(CommandNumber.EXIT_PROTOCOL)), 1)
    self.assertIn(int(CommandNumber.MANIFOLD_PRIME), io.sent)

  async def test_several_calls_in_one_batch_share_the_opening(self):
    """Several calls in one batch share the opening."""
    device, io = await self.build(EL406)
    async with device.batch():
      await device.washer.prime(volume=40_000)
      await device.washer.auto_clean(duration=60)
    self.assertEqual(io.sent.count(int(CommandNumber.INIT_PROTOCOL)), 1)
    self.assertEqual(io.sent.count(int(CommandNumber.EXIT_PROTOCOL)), 1)

  async def test_a_capability_method_sends_the_step_it_names(self):
    """A capability method sends the step it names."""
    device, io = await self.build(EL406)
    await device.washer.dispense(volume=100, buffer="B", flow_rate=5)
    self.assertIn(int(CommandNumber.MANIFOLD_DISPENSE), io.sent)


class TestRunningAProtocol(DeviceTestCase):
  """Handing a device a whole protocol rather than one operation."""

  async def test_a_list_of_steps_runs_in_one_batch(self):
    """A list of steps runs in one batch."""
    device, io = await self.build(EL406)
    await device.run_protocol([ManifoldPrime(volume=40_000), ManifoldPrime(volume=10_000)])
    self.assertEqual(io.sent.count(int(CommandNumber.INIT_PROTOCOL)), 1)
    self.assertEqual(io.sent.count(int(CommandNumber.MANIFOLD_PRIME)), 2)

  async def test_a_protocol_object_has_its_steps_built_from_its_entries(self):
    """A protocol object has its steps built from its entries."""
    device, io = await self.build(EL406)
    protocol = Protocol(
      entries=[
        ProtocolEntry(
          action=StepAction.CUSTOM,
          step_type=StepType.MANIFOLD_PRIME,
          definition="DV103|9|A|40|9|True|5|False|00:05",
        )
      ]
    )
    await device.run_protocol(protocol)
    self.assertIn(int(CommandNumber.MANIFOLD_PRIME), io.sent)

  async def test_the_check_can_be_asked_for_on_its_own(self):
    """The check can be asked for on its own."""
    device, _ = await self.build(EL406)
    self.assertTrue(await device.can_run([ManifoldPrime(volume=40_000)]))

  async def test_a_step_built_outright_can_be_run(self):
    """A step built outright can be run."""
    device, io = await self.build(EL406)
    await device.run_step(ManifoldPrime(volume=40_000))
    self.assertIn(int(CommandNumber.MANIFOLD_PRIME), io.sent)


class TestTheServiceSurface(DeviceTestCase):
  """The operations that are about the instrument rather than about liquid."""

  async def test_resetting_sends_its_own_command(self):
    """Resetting sends its own command."""
    device, io = await self.build(EL406)
    await device.reset()
    self.assertIn(int(CommandNumber.RESET_INSTRUMENT), io.sent)

  async def test_homing_everything_is_the_default(self):
    """Homing everything is the default."""
    device, io = await self.build(EL406)
    await device.home()
    self.assertIn(int(CommandNumber.HOME_VERIFY_MOTORS), io.sent)

  async def test_the_self_check_sends_its_own_command(self):
    """The self check sends its own command."""
    device, io = await self.build(EL406)
    await device.self_check()
    self.assertIn(int(CommandNumber.RUN_SELF_CHECK), io.sent)

  async def test_shaking_runs_as_a_step(self):
    """Shaking runs as a step."""
    device, io = await self.build(EL406)
    await device.shake(duration=5, soak_duration=30)
    self.assertIn(int(CommandNumber.SHAKE_SOAK), io.sent)


class TestComparingWhatAProtocolExpects(DeviceTestCase):
  """The one use a protocol's declared options are put to, and only when asked."""

  async def test_a_settings_record_can_be_compared_directly(self):
    """A settings record can be compared directly."""
    device, _ = await self.build(EL406)
    self.assertTrue(device.compare_settings(device.settings))

  async def test_a_protocol_without_a_document_is_an_explicit_error(self):
    """A protocol without a document is an explicit error."""
    device, _ = await self.build(EL406)
    with self.assertRaisesRegex(ValueError, "no fitted-options document"):
      device.compare_settings(Protocol(protocol_name="rinse"))

  async def test_a_protocol_written_for_another_instrument_still_runs(self):
    """The comparison is never consulted on the way to running something: a protocol is measured
    against the instrument as it is, which is what the check does."""
    device, io = await self.build(EL406)
    declared = InstrumentSettings(family=InstrumentFamily.MULTIFLO_FX)
    self.assertFalse(device.compare_settings(declared))
    await device.run_protocol([ManifoldPrime(volume=40_000)])
    self.assertIn(int(CommandNumber.MANIFOLD_PRIME), io.sent)


class TestTheHeightADefaultedCallWorksAt(DeviceTestCase):
  """Where a capability method puts the head when the caller names no position.

  A step carries the height it works at outright, as an absolute head position rather than an
  offset from the labware, and nothing downstream measures it against the plate: a step is checked
  against the instrument's travel, not against how deep the well is. One fixed default is therefore
  only ever right for one format -- on a shallower plate the same number is a head driven further
  down than the well is deep. A defaulted call takes the height from the plate on the carrier
  instead, and these hold every capability method that positions a head to that.

  Which model and which plate each test uses is decided by what will run: a manifold step is not
  offered on a 1536-well plate, the peristaltic wash step types need the firmware that carries
  them, and the strip step types need a strip washer fitted.
  """

  NOMINALS = {
    96: {"dispenser": 336, "manifold dispense": 121, "manifold aspirate": 29},
    384: {"dispenser": 333, "manifold dispense": 120, "manifold aspirate": 22},
    1536: {"dispenser": 250, "manifold dispense": 94, "manifold aspirate": 42},
  }
  """The nominal height of each head over each plate on the original model, pinned so a change to
  the plate table shows up here as well as in the payloads it moves."""

  FX_NOMINALS = {
    96: {"dispenser": 336, "manifold dispense": 336, "manifold aspirate": 84},
    384: {"dispenser": 333, "manifold dispense": 333, "manifold aspirate": 64},
  }
  """The same for the newest model, which keeps a plate table of its own: it dispenses through the
  wash manifold at the dispensing height rather than a height of its own, and holds the manifold
  markedly higher to aspirate."""

  def assertSent(self, device, io, command: CommandNumber, expected):
    """Assert the step sent for a command is the one given.

    Comparing whole payloads rather than the height's own bytes saves the assertion from having to
    know where in the payload the field sits, and catches a height that landed in the field of a
    different step. What goes out is the plate the step is run on followed by the step itself.

    Args:
      device: The device that sent it, which is what the step is encoded against.
      io: The fake instrument it was sent to.
      command: Which command to look for.
      expected: The step that should have been sent.
    """
    on_plate = bytes([int(device.plate.plate_type)])
    self.assertEqual(
      io.payload_of(command).hex(), (on_plate + expected.to_bytes(device.settings)).hex()
    )

  async def test_the_plates_this_uses_are_the_formats_it_names(self):
    """The plates this uses are the formats it names.

    Every test below reads heights off the plate the device resolved, so labware that resolved to a
    different format than intended would make all of them pass while proving nothing.
    """
    types = {
      96: PlateType.PLATE_96_WELL,
      384: PlateType.PLATE_384_WELL,
      1536: PlateType.PLATE_1536_WELL,
    }
    for cls, nominals in ((EL406, self.NOMINALS), (MultiFloFX, self.FX_NOMINALS)):
      for wells, heights in nominals.items():
        with self.subTest(model=cls.__name__, wells=wells):
          device, _ = await self.build(cls, wells=wells)
          self.assertIs(device.plate.plate_type, types[wells])
          for head, height in heights.items():
            self.assertEqual(device.plate.height_for(head), height)

  async def test_a_defaulted_manifold_dispense_works_at_the_plates_dispensing_height(self):
    """A defaulted manifold dispense works at the plate's dispensing height."""
    for wells in (96, 384):
      with self.subTest(wells=wells):
        device, io = await self.build(EL406, wells=wells)
        await device.washer.dispense(volume=100)
        self.assertSent(
          device,
          io,
          CommandNumber.MANIFOLD_DISPENSE,
          ManifoldDispense(
            volume=100,
            positioning=Positioning(z_steps=self.NOMINALS[wells]["manifold dispense"]),
          ),
        )

  async def test_a_defaulted_manifold_aspirate_works_at_the_plates_aspirating_height(self):
    """A defaulted manifold aspirate works at the plate's aspirating height."""
    for wells in (96, 384):
      with self.subTest(wells=wells):
        device, io = await self.build(EL406, wells=wells)
        await device.washer.aspirate()
        self.assertSent(
          device,
          io,
          CommandNumber.MANIFOLD_ASPIRATE,
          ManifoldAspirate(
            positioning=Positioning(z_steps=self.NOMINALS[wells]["manifold aspirate"])
          ),
        )

  async def test_a_defaulted_syringe_dispense_works_at_the_plates_dispensing_height(self):
    """A defaulted syringe dispense works at the plate's dispensing height."""
    for wells in (96, 384):
      with self.subTest(wells=wells):
        device, io = await self.build(EL406, wells=wells)
        await device.syringe_dispenser.dispense(volume=100)
        self.assertSent(
          device,
          io,
          CommandNumber.SYRINGE_DISPENSE,
          SyringeDispense(
            volume=100, positioning=Positioning(z_steps=self.NOMINALS[wells]["dispenser"])
          ),
        )

  async def test_a_defaulted_peristaltic_dispense_works_at_the_plates_dispensing_height(self):
    """A defaulted peristaltic dispense works at the plate's dispensing height.

    The peristaltic dispenser is offered on every plate the instrument works, which makes this the
    one capability method the whole range of heights can be seen through: 336 steps over a 96-well
    plate down to 250 over a 1536-well one.
    """
    for wells in (96, 384, 1536):
      with self.subTest(wells=wells):
        device, io = await self.build(EL406, wells=wells, answers=PUMP_READY)
        await device.peristaltic_dispenser.dispense(volume=10)
        self.assertSent(
          device,
          io,
          CommandNumber.PERI_DISPENSE,
          PeriDispense(
            volume=10, positioning=Positioning(z_steps=self.NOMINALS[wells]["dispenser"])
          ),
        )

  async def test_a_defaulted_peristaltic_wash_works_at_the_plates_aspirating_height(self):
    """A defaulted peristaltic wash works at the plate's aspirating height.

    Both halves of a peristaltic wash work at the aspirating height, the dispense included, which
    is the instrument's own pairing rather than this package's.
    """
    for wells in (96, 384):
      with self.subTest(wells=wells):
        device, io = await self.build(MultiFloFX, wells=wells, answers=PERI_WASHING)
        aspirating = Positioning(z_steps=self.FX_NOMINALS[wells]["manifold aspirate"])
        await device.peristaltic_dispenser.wash_aspirate()
        await device.peristaltic_dispenser.wash_dispense(volume=100)
        self.assertSent(
          device, io, CommandNumber.PERI_WASH_ASPIRATE, PeriWashAspirate(positioning=aspirating)
        )
        self.assertSent(
          device,
          io,
          CommandNumber.PERI_WASH_DISPENSE,
          PeriWashDispense(volume=100, positioning=aspirating),
        )

  async def test_a_defaulted_strip_step_works_at_the_plates_heights(self):
    """A defaulted strip step works at the plate's heights.

    A strip dispense comes out of a dispenser and a strip aspirate through the wash manifold, so
    the two take their heights from different rows of the plate's record.
    """
    for wells in (96, 384):
      with self.subTest(wells=wells):
        device, io = await self.build(MultiFloFX, wells=wells, answers=STRIP_WASHING)
        await device.washer.strip_dispense(volume=100)
        await device.washer.strip_aspirate()
        self.assertSent(
          device,
          io,
          CommandNumber.STRIP_DISPENSE,
          StripDispense(
            volume=100, positioning=Positioning(z_steps=self.FX_NOMINALS[wells]["dispenser"])
          ),
        )
        self.assertSent(
          device,
          io,
          CommandNumber.STRIP_ASPIRATE,
          StripAspirate(
            positioning=Positioning(z_steps=self.FX_NOMINALS[wells]["manifold aspirate"])
          ),
        )

  async def test_the_steps_a_defaulted_wash_owns_are_all_at_the_plates_heights(self):
    """The steps a defaulted wash owns are all at the plate's heights.

    A wash sends four steps of its own inside one payload, each with a height of its own, which is
    where a height put into a neighbouring field would do the most damage. The plate here is the
    96-well one because its heights are the two that differ from what the step classes default to,
    so a sub-step left at its own default fails rather than passing by coincidence.
    """
    device, io = await self.build(EL406, wells=96)
    aspirating = Positioning(z_steps=self.NOMINALS[96]["manifold aspirate"])
    dispensing = Positioning(z_steps=self.NOMINALS[96]["manifold dispense"])
    # The wash's own dispense needs a volume: the step type defaults to zero, which is below what
    # the instrument will dispense, so a wash that names no volume is refused before it is sent.
    await device.washer.wash(
      cycles=2, dispense=ManifoldDispense(volume=100, positioning=dispensing)
    )
    self.assertSent(
      device,
      io,
      CommandNumber.MANIFOLD_WASH,
      ManifoldWash(
        cycles=2,
        bottom_wash=ManifoldDispense(positioning=dispensing),
        aspirate=ManifoldAspirate(in_wash=True, positioning=aspirating),
        dispense=ManifoldDispense(volume=100, positioning=dispensing),
        final_aspirate=ManifoldAspirate(in_wash=True, positioning=aspirating),
      ),
    )

  async def test_a_named_position_is_left_alone(self):
    """A named position is left alone.

    The plate is only where a default comes from. A caller who names a height means it, including
    one the plate would not have chosen.
    """
    device, io = await self.build(EL406, wells=384)
    named = Positioning(z_steps=60, x_steps=-3, y_steps=4)
    await device.washer.dispense(volume=100, positioning=named)
    self.assertSent(
      device, io, CommandNumber.MANIFOLD_DISPENSE, ManifoldDispense(volume=100, positioning=named)
    )

  async def test_a_step_built_outright_keeps_the_height_its_class_defaults_to(self):
    """A step built outright keeps the height its class defaults to.

    Only the capability methods reach the plate. A step constructed by hand and handed to
    :meth:`run_step` carries the height its own class defaults to, which is the nominal for a
    384-well plate -- the shallowest of the formats every model works.
    """
    device, io = await self.build(EL406, wells=96)
    await device.run_step(ManifoldDispense(volume=100))
    self.assertSent(
      device,
      io,
      CommandNumber.MANIFOLD_DISPENSE,
      ManifoldDispense(volume=100, positioning=Positioning(z_steps=120)),
    )


class TestNothingIsAnsweredBeforeTheInstrumentIsRead(DeviceTestCase):
  """What a device says about hardware it has not asked about yet.

  A record nobody read describes some other machine, and the one that used to stand in was a fully
  equipped instrument of the first model -- so a 405 TS would report syringes it does not have and
  a check would allow a step it cannot run. Every question about the fitted hardware is refused
  until `setup()` has asked the instrument.
  """

  def unopened(self, cls):
    """Build a device without setting it up.

    Args:
      cls: Which model to build.

    Returns:
      The device.
    """
    return cls(port="fake", io=FakeInstrument(answers=ANSWERS))

  def test_the_fitted_options_are_refused(self):
    with self.assertRaises(RejectedError):
      _ = self.unopened(Washer405TS).settings

  def test_what_it_can_run_is_refused(self):
    with self.assertRaises(RejectedError):
      self.unopened(Washer405TS).get_available_steps()

  async def test_a_check_is_refused_rather_than_answered_from_a_default(self):
    """The dangerous one: a 405 TS has no syringes, and this used to report that it had."""
    device = self.unopened(Washer405TS)
    device.set_plate(make_plate(96))
    with self.assertRaises(RejectedError):
      await device.can_run([SyringeDispense(volume=50)])

  async def test_the_same_check_answers_once_the_instrument_has_been_read(self):
    device, _ = await self.build(Washer405TS)
    self.assertFalse(await device.can_run([SyringeDispense(volume=50)]))

  async def test_what_it_can_run_follows_what_is_fitted(self):
    """A pump that is not there takes its steps with it, which is what the default hid."""
    answers = {**ANSWERS, CommandNumber.GET_SELECTED_PERI_INSTALLED: bytes([0])}
    device, _ = await self.build(MultiFlo, answers=answers)
    available = device.get_available_steps()
    self.assertNotIn(StepType.PERI_DISPENSE, available)
    self.assertIn(StepType.SYRINGE_DISPENSE, available)
