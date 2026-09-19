"""Packing step parameters into the payload of a step command.

Every multi-byte value is little-endian, and every step type pads its payload to a fixed length
that belongs to the step type rather than to its contents.
"""

from __future__ import annotations


def u16(value: int) -> bytes:
  """Two bytes for an unsigned value.

  Args:
    value: The value to pack.

  Returns:
    The two bytes, least significant first.
  """
  return int(value).to_bytes(2, "little", signed=False)


def i16(value: int) -> bytes:
  """Two bytes for a signed value.

  Args:
    value: The value to pack.

  Returns:
    The two bytes, least significant first.
  """
  return int(value).to_bytes(2, "little", signed=True)


def u8(value: int) -> bytes:
  """One byte for an unsigned value.

  Args:
    value: The value to pack.

  Returns:
    The byte.
  """
  return int(value).to_bytes(1, "little", signed=False)


def i8(value: int) -> bytes:
  """One byte for a signed value.

  Args:
    value: The value to pack.

  Returns:
    The byte.
  """
  return int(value).to_bytes(1, "little", signed=True)


def pad(payload: bytes, length: int) -> bytes:
  """Pad a payload to the length its step type sends.

  Args:
    payload: The payload so far.
    length: The length to pad to.

  Returns:
    The payload followed by zero bytes.

  Raises:
    ValueError: If the payload is already longer, which means the layout is miscounted.
  """
  if len(payload) > length:
    raise ValueError(f"step payload is {len(payload)} bytes, expected at most {length}")
  return payload + bytes(length - len(payload))
