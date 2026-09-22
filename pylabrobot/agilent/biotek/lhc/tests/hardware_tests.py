"""Tests that need an instrument on the other end of a port.

Deselected by default. Run them with ``-m hardware`` and ``LHC_INSTRUMENT_PORT`` set to the port the
instrument is on, and read what each one does first: opening a batch homes the motors, so the
carrier moves, and the self-check moves more than that. Nothing here dispenses, and nothing runs a
protocol.

The point is to prove against real firmware the one thing a fake instrument cannot: that the link,
the identity queries, the fitted-options read and the batch bracket work as they do here.
"""

from __future__ import annotations

import os
import unittest

import pytest

from pylabrobot.agilent.biotek.lhc import EL406
from pylabrobot.agilent.biotek.lhc.enums.instrument.instrument_family import InstrumentFamily

PORT = os.environ.get("LHC_INSTRUMENT_PORT", "")
"""The port an instrument is on, if there is one to talk to."""

MODEL = os.environ.get("LHC_INSTRUMENT_MODEL", InstrumentFamily.EL406.name)
"""Which model is attached, so what it reports can be checked against what it should be."""

pytestmark = [
  pytest.mark.hardware,
  pytest.mark.skipif(not PORT, reason="set LHC_INSTRUMENT_PORT to the instrument's port"),
]


class TestAgainstRealFirmware(unittest.IsolatedAsyncioTestCase):
  """One instrument, opened for each test and closed again."""

  async def asyncSetUp(self) -> None:
    """Open the instrument."""
    self.device = EL406(port=PORT)
    await self.device.setup()

  async def asyncTearDown(self) -> None:
    """Close the instrument, whatever the test did."""
    await self.device.stop()

  async def test_the_instrument_answers_and_says_what_it_is(self):
    """The whole link in one test: it opened, it answered, and it named itself."""
    self.assertTrue(await self.device.get_serial_number())
    self.assertTrue((await self.device.get_firmware_version()).software_version)

  async def test_the_fitted_options_read_back(self):
    """What setup already did, asserted: a record read half way would encode every step wrongly."""
    self.assertEqual(self.device.settings.family.name, MODEL)
    self.assertTrue(self.device.get_available_steps())

  async def test_the_status_can_be_read_without_a_batch_open(self):
    """The status can be read without a batch open."""
    self.assertIsNotNone((await self.device.get_status()).state)

  async def test_a_batch_opens_and_closes(self):
    """Opening a batch homes the motors, so the carrier moves. Nothing is dispensed."""
    from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb

    self.device.set_plate(cor_96_wellplate_360uL_Fb("plate"))
    async with self.device.batch():
      self.assertIsNotNone((await self.device.get_status()).state)

  async def test_the_self_check_passes(self):
    """The instrument's own check of itself, which takes a while and moves things."""
    await self.device.self_check()

  async def test_a_protocol_this_instrument_cannot_run_is_refused_rather_than_attempted(self):
    """The check, against a real fitted-options record: a step whose hardware is absent is refused
    before anything moves."""
    from pylabrobot.agilent.biotek.lhc.protocols.steps.steps import STEP_CLASSES
    from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb

    self.device.set_plate(cor_96_wellplate_360uL_Fb("plate"))
    offered = set(self.device.get_available_steps())
    absent = [step_type for step_type in STEP_CLASSES if step_type not in offered]
    if not absent:
      self.skipTest("this instrument offers every step type, so there is nothing it cannot run")
    step = STEP_CLASSES[absent[0]]()
    self.assertFalse(await self.device.can_run([step]))
