from string import ascii_uppercase as LETTERS


def expand_string_range(range_str: str) -> list:
  """Turns a range string into a list of position strings. Horizontal, vertical, or grids.

  Args:
    range_str: A string showing a range, like "A1:C3".

  Returns:
    A list of position identifier strings.
  """
  if ":" not in range_str:
    raise ValueError(f"Invalid range: {range_str}")

  start, end = range_str.split(":")
  start_col, start_row = LETTERS.index(start[0]), int(start[1:])
  end_col, end_row = LETTERS.index(end[0]), int(end[1:])
  row_range = (
    range(start_row, end_row + 1) if start_row < end_row else range(start_row, end_row - 1, -1)
  )
  col_range = (
    range(start_col, end_col + 1) if start_col < end_col else range(start_col, end_col - 1, -1)
  )
  return [f"{LETTERS[col]}{row}" for col in col_range for row in row_range]
