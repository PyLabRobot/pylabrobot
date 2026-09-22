"""Durations, which a protocol file stores as clock text but a caller gives in seconds."""

from __future__ import annotations


def format_hours_minutes(duration: int) -> str:
  """Write a duration as the ``HH:MM`` text a protocol file stores.

  Args:
    duration: Duration in seconds. Seconds below a whole minute are dropped, which is the
      resolution the instrument runs these at.

  Returns:
    The field text.
  """
  minutes = duration // 60
  return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_hours_minutes(field: str) -> int:
  """Read a ``HH:MM`` field.

  Args:
    field: The field text.

  Returns:
    The duration in seconds.
  """
  hours, _, minutes = field.strip().partition(":")
  return (int(hours) * 60 + int(minutes)) * 60


def format_minutes_seconds(duration: int) -> str:
  """Write a duration as the ``MM:SS`` text a protocol file stores.

  Args:
    duration: Duration in seconds.

  Returns:
    The field text.
  """
  return f"{duration // 60:02d}:{duration % 60:02d}"


def parse_minutes_seconds(field: str) -> int:
  """Read a ``MM:SS`` field.

  Args:
    field: The field text.

  Returns:
    The duration in seconds.
  """
  minutes, _, seconds = field.strip().partition(":")
  return int(minutes) * 60 + int(seconds)
