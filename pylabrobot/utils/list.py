""" Utilities for working with lists. """

import typing


def assert_shape(list_: list, shape: typing.Tuple[int]):
  """Assert that a list has the correct shape.

  Args:
    list_: The list to check.
    shape: The expected shape.
  """

  if len(list_) != shape[0]:
    raise ValueError(f"List has incorrect shape: {list_}")
  for row in list_:
    if len(row) != shape[1]:
      raise ValueError(f"List has incorrect shape: {list_}")
