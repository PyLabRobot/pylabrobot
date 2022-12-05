""" Utilities for working with positions and position strings.

These follow the Hamilton VENUS style, which is like MS Excel, but transposed. So `B1` is the cell
below the top left (`A1`) and `A2` is the cell to the right of `A1`.
"""

import itertools
import typing


def string_to_position(position_string: str) -> typing.Tuple[int, int]:
  """Convert a string to a list of patterns.

  Positions are formatted as `<row><column>` where `<row>` is the row string (`A` for row 1, `B`
  for row 2, etc.) and `<column>` is the column number. For example, `A1` is the top left corner
  of the resource and `H12` is the bottom right. This method returns the index for such a
  position.

  Examples:
    >>> _string_to_pattern("A1")
    (0, 0)

    >>> _string_to_pattern("A3")
    (0, 2)

    >>> _string_to_pattern("C1")
    (2, 0)

  Args:
    position_string: The string to convert.

  Returns:
    A list of patterns.
  """

  row = ord(position_string[0]) - 65
  column = int(position_string[1:]) - 1
  return (row, column)


def string_to_index(position_string: str, num_rows: int, num_columns: int) -> int:
  """ Convert a position string to an index.

  Args:
    position_string: The position string.
    num_rows: The number of rows of the resource that's indexed.
    num_columns: The number of columns of the resource that's indexed.

  Returns:
    The index of the position.
  """

  row, column = string_to_position(position_string)
  assert row < num_rows, f"Row must be less than {num_rows}"
  assert column < num_columns, f"Column must be less than {num_columns}"
  return row + column * num_rows


def string_to_indices(position_range_string: str, num_rows: int) \
  -> typing.List[int]:
  """ Convert a position string to a list of indices.

  Args:
    position_string: The position string.
    num_rows: The number of rows of the resource that's indexed.

  Returns:
    A list of indices.
  """

  start_str, end_str = position_range_string.split(":")

  start, end = string_to_position(start_str), string_to_position(end_str)
  x_dir = 1 if start[0] <= end[0] else -1
  y_dir = 1 if start[1] <= end[1] else -1
  indices = []
  for row, column in itertools.product(range(start[0], end[0] + x_dir, x_dir),
    range(start[1], end[1] + y_dir, y_dir)):
    indices.append(row + column * num_rows)
  return indices


def string_to_pattern(position_range_string: str, num_rows: int, num_columns: int) \
  -> typing.List[typing.List[bool]]:
  """ Convert a position string to a pattern.

  Args:
    position_string: The position string.

  Returns:
    A list of lists of booleans.

  Examples:
    Convert `"A1:A3"` to a pattern.

    >>> _string_range_to_pattern("A1:C3")
    [[True, True, True, False, False, ...], [True, True, True, False, False...],
      [True, True, True, False, False...], ...]

    Convert `"A1:A3"` to a pattern.

    >>> _string_range_to_pattern("A1:A3")
    [[True, True, True, False, False, ...], [False, False, ...], ...]

    Convert `"A1:C1"` to a pattern.

    >>> _string_range_to_pattern("A1:C1")
    [[True, False, ...], [True, False, ...], [True, False, ...], [False, False, ...], ...]

  """

  # Split the position string into a list of position strings
  start_str, end_str = position_range_string.split(":")
  start, end = string_to_position(start_str), string_to_position(end_str)
  positions = [[False for _ in range(num_columns)] for _ in range(num_rows)]
  for row, column in itertools.product(range(start[0], end[0] + 1), range(start[1], end[1] + 1)):
    positions[row][column] = True
  return positions
