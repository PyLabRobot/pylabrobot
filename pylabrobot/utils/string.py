""" Utilities for working with strings. """

import typing


def pad_string(item: typing.Union[str, int], desired_length: int, left=False) -> str:
  """ Pad a string or integer with spaces to the desired length.

  Args:
    item: string or integer to pad
    desired_length: length to pad to
    left: pad to the left instead of the right

  Returns:
    padded string of length `desired_length`
  """

  length = None
  if isinstance(item, str):
    length = len(item)
  elif isinstance(item, int):
    length = item // 10
  spaces = max(0, desired_length - length) * " "
  item = str(item)
  return (spaces+item) if left else (item+spaces)
