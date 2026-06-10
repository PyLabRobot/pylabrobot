# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Setup, serialization, and configuration."""

import unittest

from pylabrobot.plate_washing.biotek.el406 import (
  EL406CommunicationError,
  ExperimentalBioTekEL406Backend,
)
from pylabrobot.plate_washing.biotek.el406.mock_tests import EL406TestCase, MockFTDI


class TestEL406BackendSetup(EL406TestCase):
  """Test EL406 backend setup and teardown."""

  async def test_setup_creates_io(self):
    """Setup should create and configure FTDI IO wrapper."""
    backend = ExperimentalBioTekEL406Backend(timeout=0.01)
    backend.io = MockFTDI()
    async with backend:
      self.assertIsNotNone(backend.io)

  async def test_stop_closes_device(self):
    """Stop should close the FTDI device."""
    backend = ExperimentalBioTekEL406Backend(timeout=0.01)
    backend.io = MockFTDI()
    async with backend:
      self.assertIsNotNone(backend.io)

    self.assertIsNone(backend.io)


class TestEL406CommunicationError(unittest.TestCase):
  """Test EL406CommunicationError exception class."""

  def test_exception_attributes(self):
    """EL406CommunicationError should preserve message, operation, and original error."""
    original = OSError("USB disconnect")
    error = EL406CommunicationError("FTDI error", operation="read", original_error=original)
    self.assertEqual(str(error), "FTDI error")
    self.assertEqual(error.operation, "read")
    self.assertIs(error.original_error, original)

    # Defaults
    simple = EL406CommunicationError("Test")
    self.assertEqual(simple.operation, "")
    self.assertIsNone(simple.original_error)


class TestEL406BackendSerialization(unittest.TestCase):
  """Test EL406 backend serialization."""

  def test_serialize(self):
    """Backend should serialize correctly."""
    backend = ExperimentalBioTekEL406Backend(timeout=30.0)
    serialized = backend.serialize()

    self.assertEqual(serialized["type"], "ExperimentalBioTekEL406Backend")
    self.assertEqual(serialized["timeout"], 30.0)

  def test_init_without_ftdi_available(self):
    """Backend should be instantiable without FTDI library."""
    backend = ExperimentalBioTekEL406Backend()
    self.assertIsNone(backend.io)
