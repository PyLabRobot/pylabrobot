# mypy: disable-error-code="union-attr,assignment,arg-type"
"""Tests for BioTek EL406 plate washer backend - Setup, serialization, and configuration.

This module contains tests for setup, serialization, error classes,
and plate type configuration.
"""

import unittest

from pylabrobot.plate_washing.biotek.el406 import (
  BioTekEL406Backend,
  EL406CommunicationError,
  EL406PlateType,
)
from pylabrobot.plate_washing.biotek.el406.helpers import validate_plate_type
from pylabrobot.plate_washing.biotek.el406.mock_tests import MockFTDI


class TestEL406BackendSetup(unittest.IsolatedAsyncioTestCase):
  """Test EL406 backend setup and teardown."""

  async def test_setup_creates_io(self):
    """Setup should create and configure FTDI IO wrapper."""
    backend = BioTekEL406Backend(timeout=0.5)
    backend.io = MockFTDI()
    await backend.setup()

    self.assertIsNotNone(backend.io)

  async def test_stop_closes_device(self):
    """Stop should close the FTDI device."""
    backend = BioTekEL406Backend(timeout=0.5)
    backend.io = MockFTDI()
    await backend.setup()

    self.assertIsNotNone(backend.io)
    await backend.stop()

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
    backend = BioTekEL406Backend(timeout=30.0)
    serialized = backend.serialize()

    self.assertEqual(serialized["type"], "BioTekEL406Backend")
    self.assertEqual(serialized["timeout"], 30.0)

  def test_init_without_ftdi_available(self):
    """Backend should be instantiable without FTDI library."""
    backend = BioTekEL406Backend()
    self.assertIsNone(backend.io)


class TestPlateTypeConfiguration(unittest.TestCase):
  """Test plate type configuration."""

  def test_set_and_get_plate_type_round_trip(self):
    """set_plate_type and get_plate_type should work together."""
    backend = BioTekEL406Backend()
    for plate_type in EL406PlateType:
      backend.set_plate_type(plate_type)
      result = backend.get_plate_type()
      self.assertEqual(result, plate_type)

  def test_serialize_includes_plate_type(self):
    """Backend serialization should include plate_type."""
    backend = BioTekEL406Backend(plate_type=EL406PlateType.PLATE_384_WELL)
    serialized = backend.serialize()
    self.assertEqual(serialized["plate_type"], EL406PlateType.PLATE_384_WELL.value)

  def test_set_plate_type_does_not_send_command(self):
    """set_plate_type should NOT send any command to the device."""
    backend = BioTekEL406Backend(timeout=0.5)
    backend.io = MockFTDI()
    initial_count = len(backend.io.written_data)
    backend.set_plate_type(EL406PlateType.PLATE_384_WELL)
    self.assertEqual(len(backend.io.written_data), initial_count)


class TestPlateTypeValidation(unittest.TestCase):
  """Test plate type validation."""

  def test_validate_plate_type_accepts_enum(self):
    """validate_plate_type should accept EL406PlateType enum values."""
    for plate_type in EL406PlateType:
      validate_plate_type(plate_type)

  def test_validate_plate_type_accepts_int_values(self):
    """validate_plate_type should accept valid integer values."""
    for value in [0, 1, 2, 4, 14]:
      result = validate_plate_type(value)
      self.assertIsInstance(result, EL406PlateType)

  def test_validate_plate_type_raises_for_invalid_int(self):
    """validate_plate_type should raise ValueError for invalid integers."""
    with self.assertRaises(ValueError):
      validate_plate_type(-1)
    with self.assertRaises(ValueError):
      validate_plate_type(3)
    with self.assertRaises(ValueError):
      validate_plate_type(100)

  def test_validate_plate_type_raises_for_invalid_type(self):
    """validate_plate_type should raise for invalid types."""
    with self.assertRaises((ValueError, TypeError)):
      validate_plate_type("96-well")
    with self.assertRaises((ValueError, TypeError)):
      validate_plate_type(None)
    with self.assertRaises((ValueError, TypeError)):
      validate_plate_type([1, 2, 3])
