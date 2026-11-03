# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 communication functionality."""

from pylabrobot.legacy.plate_washing.biotek.el406.mock_tests import EL406TestCase, MockFTDI


class TestTestCommunication(EL406TestCase):
  """Test communication verification."""

  async def test_communication_sends_query_command(self):
    """Test communication should send a query command."""
    self.backend.io = MockFTDI()
    # _test_communication() sends two commands, need enough responses
    self.backend.io.set_read_buffer(b"\x06" * 10)

    await self.backend._test_communication()

    # Verify some command was sent
    self.assertGreater(len(self.backend.io.written_data), 0)
