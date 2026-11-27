"""Selecting which columns or rows of a plate a step works on."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

COLUMNS = 48
"""How many entries a column selection always has, whatever the plate holds."""

ROWS = 4
"""How many entries a row selection always has."""


@dataclass
class WellMask:
  """A column or row selection, one entry per position, stored as a run of digits.

  A column selection with nothing selected is accepted and simply washes nothing; a row selection
  with nothing selected is rejected by validation. Two step types encode a row selection inverted,
  so a selected row contributes a zero bit; that is a property of those steps, not of the mask,
  which is why both packings are offered here.

  Attributes:
    values: One entry per position, 1 for selected and 0 for not.
  """

  values: list[int] = field(default_factory=lambda: [1] * COLUMNS)

  def to_definition(self) -> str:
    """The single field a protocol file stores, one digit per entry.

    Returns:
      The field text.
    """
    return "".join(str(value) for value in self.values)

  @classmethod
  def from_definition(cls, field_text: str) -> WellMask:
    """Read the field back.

    Args:
      field_text: One digit per entry.

    Returns:
      The selection.

    Raises:
      ValueError: If a character is not a digit.
    """
    return cls([int(digit) for digit in field_text])

  @classmethod
  def from_columns(cls, columns: Iterable[int]) -> WellMask:
    """A selection of the columns to work, counted from one.

    Args:
      columns: The columns to select. Order does not matter, and a column named twice is selected
        once. Naming none selects nothing, which is a selection a step accepts.

    Returns:
      The selection, with every column not named unselected.

    Raises:
      ValueError: If a column is not between 1 and 48. A column beyond what the plate holds is not
        an error: the instrument reads as many entries as the format on the carrier has.
    """
    chosen = set(columns)
    beyond = sorted(column for column in chosen if not 1 <= column <= COLUMNS)
    if beyond:
      raise ValueError(f"columns must be 1..{COLUMNS}; got {beyond}")
    return cls([1 if column in chosen else 0 for column in range(1, COLUMNS + 1)])

  @classmethod
  def all_columns(cls) -> WellMask:
    """Every column selected.

    Returns:
      The selection.
    """
    return cls([1] * COLUMNS)

  @classmethod
  def all_rows(cls) -> WellMask:
    """Every row selected.

    Returns:
      The selection.
    """
    return cls([1] * ROWS)

  @property
  def is_valid(self) -> bool:
    """Whether every entry is 0 or 1."""
    return all(value in (0, 1) for value in self.values)

  @property
  def selected_count(self) -> int:
    """How many entries are selected."""
    return sum(1 for value in self.values if value)

  def to_bits(self) -> int:
    """Pack a column selection into the 12 bits the aspirate steps send.

    The 48 entries are six blocks of eight, of which only the first two of each block are
    distinct: bit ``i`` comes from entry ``(i // 2) * 8 + i % 2``. The remaining entries are
    copies and never reach the instrument.

    Returns:
      The packed bits.
    """
    return sum(1 << i for i in range(12) if self.values[(i // 2) * 8 + i % 2])

  def to_bytes(self) -> bytes:
    """Pack the selection one bit per entry, least significant bit first.

    Returns:
      The packed bytes, one per eight entries.
    """
    packed = bytearray((len(self.values) + 7) // 8)
    for index, value in enumerate(self.values):
      if value:
        packed[index // 8] |= 1 << (index % 8)
    return bytes(packed)

  def to_bytes_inverted(self) -> bytes:
    """Pack the selection with every bit flipped, as the two dispensers encode their rows.

    Returns:
      The packed bytes, in which a selected entry contributes a zero bit.
    """
    packed = bytearray((len(self.values) + 7) // 8)
    for index, value in enumerate(self.values):
      if not value:
        packed[index // 8] |= 1 << (index % 8)
    return bytes(packed)
