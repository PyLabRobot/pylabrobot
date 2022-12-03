""" Utilities for working with lists. """

import collections.abc
from typing import List, Tuple, TypeVar, Union, Sequence, cast


T = TypeVar("T")


def assert_shape(list_: List[List[T]], shape: Tuple[int, int]):
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


def reshape_2d(list_: List[T], shape: Tuple[int, int]) -> List[List[T]]:
  """ Reshape a list into a 2d list.

  Args:
    list_: The list to reshape.
    shape: The new shape.

  Returns:
    The reshaped list.
  """

  if not len(list_) == shape[0] * shape[1]:
    raise ValueError(f"Cannot reshape list {list_} into shape {shape}")

  new_list: List[List[T]] = []

  for i in range(shape[0]):
    new_list.append([])
    for j in range(shape[1]):
      new_list[i].append(list_[i * shape[1] + j])

  return new_list


def expand(list_or_item: Union[Sequence[T], T], n: int) -> List[T]:
  if n <= 0:
    raise ValueError(f"Cannot expand list {list_or_item} by {n}.")
  if isinstance(list_or_item, collections.abc.Sequence) and not isinstance(list_or_item, str):
    if len(list_or_item) != n:
      raise ValueError(f"Expected list of length {n}, got {len(list_or_item)}.")
    return list(list_or_item)
  # cast to T to avoid mypy error (thinks it's a string). This can probably be written better.
  return [cast(T, list_or_item)] * n
