"""Service errors shared by persistence, execution, and the transport adapter."""

from __future__ import annotations


class BusyError(Exception):
  """Another operation owns the instrument or store."""


class StateError(Exception):
  """An operation is invalid in the current state."""


class CapacityError(Exception):
  """Configured storage or subscription capacity is exhausted."""


class StorageError(Exception):
  """Persistence failed; further execution requires service recovery."""


class DataLossError(StorageError):
  """Retained data failed integrity validation."""
