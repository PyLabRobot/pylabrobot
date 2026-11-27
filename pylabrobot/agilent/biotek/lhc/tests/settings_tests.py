"""What an instrument has fitted: read from a protocol file's document, and read from the instrument.

The document half is pure text handling and needs nothing. The instrument half is a sequence of
queries that differs per model, so the fake instrument is set to answer them and the sequence is
what is checked -- including that a query for hardware a model cannot carry is not sent at all.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

import pytest

from pylabrobot.agilent.biotek.lhc.devices import settings_document, settings_query
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.devices.settings_comparison import compare
from pylabrobot.agilent.biotek.lhc.enums.instrument.basecode import Basecode
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily
from pylabrobot.agilent.biotek.lhc.enums.instrument.strip_washer_manifold import StripWasherManifold
from pylabrobot.agilent.biotek.lhc.enums.instrument.syringe_box_size import SyringeBoxSize
from pylabrobot.agilent.biotek.lhc.enums.instrument.valve_box import ValveBox
from pylabrobot.agilent.biotek.lhc.enums.instrument.washer_manifold import WasherManifold
from pylabrobot.agilent.biotek.lhc.error_handling import BiotekError
from pylabrobot.agilent.biotek.lhc.serialization.command_numbers import CommandNumber
from pylabrobot.agilent.biotek.lhc.tests.helpers import fake_link

DOCUMENT = (
  '<?xml version="1.0"?>\r\n'
  "<CInstrumentSettings"
  ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
  ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">\r\n'
  "  <Instrument>e406Basic</Instrument>\r\n"
  "  <WasherManifold>e96TubeDual</WasherManifold>\r\n"
  "  <SyringeBox>eAutoclavable</SyringeBox>\r\n"
  "  <SyringeManifold>e16Tube</SyringeManifold>\r\n"
  "  <BufferSwitchingModule>true</BufferSwitchingModule>\r\n"
  "  <ValveBoxType>eWasher</ValveBoxType>\r\n"
  "  <VacuumFiltrationWasher>false</VacuumFiltrationWasher>\r\n"
  "  <PeriPumpModule>true</PeriPumpModule>\r\n"
  "  <PeriPumpModule2>false</PeriPumpModule2>\r\n"
  "  <UltrasonicModule>true</UltrasonicModule>\r\n"
  "  <CellWashingModule>true</CellWashingModule>\r\n"
  "  <m_bYAxisInstalled>true</m_bYAxisInstalled>\r\n"
  "  <m_bHalfULEnabled>true</m_bHalfULEnabled>\r\n"
  "  <StripWasherManifold>eNotInstalled</StripWasherManifold>\r\n"
  "  <m_bSingleWellEnabled>false</m_bSingleWellEnabled>\r\n"
  "  <m_bPWEnabled>false</m_bPWEnabled>\r\n"
  "</CInstrumentSettings>"
)
"""A fitted-options document of the shape a protocol file carries."""


class TestReadingTheDocument:
  """Turning the text a protocol file stores into a record."""

  def test_every_option_is_read(self):
    """Every option is read."""
    settings = settings_document.from_xml(DOCUMENT)
    assert settings.family is InstrumentFamily.EL406
    assert settings.washer_manifold is WasherManifold.TUBE_96_DUAL
    assert settings.valve_box is ValveBox.WASHER
    assert settings.peri_pump
    assert not settings.peri_pump_2
    assert settings.strip_washer_manifold is StripWasherManifold.NOT_INSTALLED

  def test_a_document_round_trips(self):
    """A document round trips."""
    assert settings_document.to_xml(settings_document.from_xml(DOCUMENT)) == DOCUMENT

  def test_the_defaults_round_trip(self):
    """The defaults round trip."""
    default = InstrumentSettings()
    assert settings_document.from_xml(settings_document.to_xml(default)) == default

  def test_an_element_an_older_release_did_not_write_keeps_its_default(self):
    """Documents written before an option existed simply do not carry it."""
    without = DOCUMENT.replace("  <m_bPWEnabled>false</m_bPWEnabled>\r\n", "")
    assert settings_document.from_xml(without).peri_wash_enabled is False

  @pytest.mark.parametrize("named", ["e50TS", "e406FX"])
  def test_a_model_this_package_does_not_work_with_is_refused(self, named: str):
    """Every field below the model is read as that model would mean it, so guessing the model would
    quietly misread the rest."""
    document = DOCUMENT.replace("e406Basic", named)
    with pytest.raises(ValueError, match=named):
      settings_document.from_xml(document)

  def test_text_that_is_not_a_document_is_refused(self):
    """Text that is not a document is refused."""
    with pytest.raises(ValueError, match="will not read"):
      settings_document.from_xml("not a document")

  def test_the_shorter_buffer_switching_element_is_read_too(self):
    """Some releases name that element without its suffix, and both names mean the same option."""
    document = DOCUMENT.replace(
      "<BufferSwitchingModule>true</BufferSwitchingModule>",
      "<BufferSwitching>false</BufferSwitching>",
    )
    assert not settings_document.from_xml(document).buffer_switching

  def test_two_fields_are_not_in_a_document_at_all(self):
    """How many bottles the syringe box holds, and whether the dispensers take the wider offsets,
    are only ever learned by asking the instrument."""
    settings = settings_document.from_xml(DOCUMENT)
    assert settings.syringe_box_size is SyringeBoxSize.UNKNOWN
    assert not settings.advanced_dispense_offsets


class TestComparingWhatIsDeclaredWithWhatIsFitted:
  """The one thing a protocol's declared options are used for."""

  def test_an_instrument_equipped_the_same_way_agrees(self):
    """An instrument equipped the same way agrees."""
    declared = settings_document.from_xml(DOCUMENT)
    assert compare(declared, declared)
    assert "equipped like this one" in str(compare(declared, declared))

  def test_each_option_that_differs_is_named(self):
    """Each option that differs is named."""
    declared = settings_document.from_xml(DOCUMENT)
    actual = InstrumentSettings(
      family=InstrumentFamily.MULTIFLO_FX,
      washer_manifold=WasherManifold.NOT_INSTALLED,
      peri_pump_2=True,
    )
    result = compare(declared, actual)
    assert not result
    named = {difference.option for difference in result.differences}
    assert "instrument model" in named
    assert "wash manifold" in named
    assert "secondary peristaltic pump" in named

  def test_a_difference_reads_as_a_sentence(self):
    """A difference reads as a sentence."""
    declared = settings_document.from_xml(DOCUMENT)
    actual = InstrumentSettings(family=InstrumentFamily.MULTIFLO)
    difference = compare(declared, actual).differences[0]
    assert "protocol says EL406" in str(difference)
    assert "instrument reports MULTIFLO" in str(difference)

  def test_the_two_fields_a_document_omits_are_not_compared(self):
    """Comparing them would report a difference against a default nobody declared."""
    declared = settings_document.from_xml(DOCUMENT)
    actual = replace(
      declared, syringe_box_size=SyringeBoxSize.DOUBLE, advanced_dispense_offsets=True
    )
    assert compare(declared, actual)


class TestAskingTheInstrument(unittest.IsolatedAsyncioTestCase):
  """The query sequence, which differs per model."""

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
    CommandNumber.IS_STRIP_WASHER_BOX_CONNECTED: bytes([1]),
    CommandNumber.GET_STRIP_WASHER_HW_INSTALLED: bytes([1]),
    CommandNumber.GET_STRIP_WASHER_MANIFOLD_TYPE: bytes([4]),
    CommandNumber.GET_SINGLE_WELL_DISPENSER_INSTALLED: bytes([1]),
    CommandNumber.GET_WHICH_BASECODE_IS_INSTALLED: bytes([int(Basecode.PERI_WASH)]),
    CommandNumber.GET_FLUID_TRACKING_ENABLED: bytes([1]),
  }
  """What the fake instrument answers each option query with."""

  async def read(self, family: InstrumentFamily, status: int = 0):
    """Read the fitted options off a fake instrument.

    Args:
      family: Which model to read as.
      status: The status word the instrument answers with.

    Returns:
      The settings and the fake instrument behind them.
    """
    link, io = fake_link(answers=self.ANSWERS, status=status, family=family)
    await link.setup()
    return await settings_query.read_settings(link, family), io

  async def test_the_original_model_reads_its_own_options(self):
    """The original model reads its own options."""
    settings, io = await self.read(InstrumentFamily.EL406)
    self.assertIs(settings.family, InstrumentFamily.EL406)
    self.assertIs(settings.washer_manifold, WasherManifold.TUBE_96_DUAL)
    self.assertIs(settings.valve_box, ValveBox.WASHER)
    self.assertTrue(settings.peri_pump)
    self.assertIs(settings.syringe_box_size, SyringeBoxSize.DOUBLE)
    # It has one peristaltic pump, so the second is never asked about.
    self.assertEqual(io.sent.count(int(CommandNumber.GET_SELECTED_PERI_INSTALLED)), 1)

  async def test_a_wash_only_model_is_not_asked_about_dispensers(self):
    """A wash only model is not asked about dispensers."""
    settings, io = await self.read(InstrumentFamily.MODEL_405_TS)
    self.assertNotIn(int(CommandNumber.GET_SYRINGE_BOX_INFO), io.sent)
    self.assertNotIn(int(CommandNumber.GET_SELECTED_PERI_INSTALLED), io.sent)
    self.assertFalse(settings.peri_pump)

  async def test_a_dispenser_only_model_is_not_asked_about_a_wash_manifold(self):
    """A dispenser only model is not asked about a wash manifold."""
    settings, io = await self.read(InstrumentFamily.MULTIFLO)
    self.assertNotIn(int(CommandNumber.GET_WASHER_MANIFOLD_INSTALLED), io.sent)
    self.assertIs(settings.washer_manifold, WasherManifold.NOT_INSTALLED)
    # It has two pumps, and both are asked about.
    self.assertEqual(io.sent.count(int(CommandNumber.GET_SELECTED_PERI_INSTALLED)), 2)

  async def test_the_newest_model_reads_its_strip_washer_and_its_firmware(self):
    """The newest model reads its strip washer and its firmware."""
    settings, _ = await self.read(InstrumentFamily.MULTIFLO_FX)
    self.assertIs(settings.strip_washer_manifold, StripWasherManifold.PLATE_96_WELL)
    self.assertTrue(settings.single_well_enabled)
    self.assertTrue(settings.peri_wash_enabled)
    self.assertTrue(settings.advanced_dispense_offsets)

  async def test_an_absent_strip_washer_box_stops_the_manifold_being_asked_for(self):
    """An absent strip washer box stops the manifold being asked for."""
    answers = dict(self.ANSWERS)
    answers[CommandNumber.IS_STRIP_WASHER_BOX_CONNECTED] = bytes([0])
    link, io = fake_link(answers=answers, family=InstrumentFamily.MULTIFLO_FX)
    await link.setup()
    settings = await settings_query.read_settings(link, InstrumentFamily.MULTIFLO_FX)
    self.assertIs(settings.strip_washer_manifold, StripWasherManifold.NOT_INSTALLED)
    self.assertNotIn(int(CommandNumber.GET_STRIP_WASHER_MANIFOLD_TYPE), io.sent)

  async def test_an_option_that_cannot_be_read_is_a_failure_rather_than_a_default(self):
    """A record read half way would encode every step against the wrong instrument."""
    with self.assertRaises(BiotekError):
      await self.read(InstrumentFamily.EL406, status=0x6029)
