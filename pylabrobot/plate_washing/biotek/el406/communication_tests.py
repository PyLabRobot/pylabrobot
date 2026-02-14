# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Communication and protocol functionality.

This module contains tests for Communication and protocol functionality.
"""

import unittest

# Import the backend module (mock is already installed by test_el406_mock import)
from pylabrobot.plate_washing.biotek.el406 import (
  BioTekEL406Backend,
)
from pylabrobot.plate_washing.biotek.el406.mock_tests import MockFTDI


class TestTestCommunication(unittest.IsolatedAsyncioTestCase):
  """Test communication verification.

  The _test_communication() method should send a query command
  and verify the device responds with ACK (0x06).
  """

  async def asyncSetUp(self):
    self.backend = BioTekEL406Backend(timeout=0.5)
    # Don't call setup() yet - we want to test _test_communication() directly

  async def test_communication_sends_query_command(self):
    """Test communication should send a query command."""
    self.backend.io = MockFTDI()
    # _test_communication() sends two commands, need enough responses
    self.backend.io.set_read_buffer(b"\x06" * 10)

    await self.backend._test_communication()

    # Verify some command was sent
    self.assertGreater(len(self.backend.io.written_data), 0)
