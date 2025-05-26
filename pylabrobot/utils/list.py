"""Utilities for working with lists."""

from typing import List, Tuple, TypeVar

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
  """Reshape a list into a 2d list.

  Args:
    list_: The list to reshape.
    shape: A tuple (rows, columns) specifying the desired shape of the 2D list.

  Returns:
    A 2D list with the specified number of rows and columns.

  Raises:
    ValueError: If the total number of elements in the list does not match the specified shape (rows * columns).
  """

  if not len(list_) == shape[0] * shape[1]:
    raise ValueError(f"Cannot reshape list {list_} into shape {shape}")

  new_list: List[List[T]] = []

  for i in range(shape[0]):  # Iterating over rows
    new_list.append([])
    for j in range(shape[1]):  # Iterating over columns
      new_list[i].append(list_[i * shape[1] + j])

  return new_list


def chunk_list(list_: List[T], chunk_size: int) -> List[List[T]]:
  """Divide a list into smaller chunks of a specified size.

  Args:
    list_: The list to be divided into chunks.
    chunk_size: The size of each chunk.

  Returns:
    A list of chunks, where each chunk is a list of elements.

  Example:
    >>> chunk_list([1, 2, 3, 4, 5, 6, 7, 8, 9], 3)
    [[1, 2, 3], [4, 5, 6], [7, 8, 9]]

    >>> chunk_list([1, 2, 3, 4, 5], 2)
    [[1, 2], [3, 4], [5]]
  """
  chunks: List[List[T]] = []
  for i in range(0, len(list_), chunk_size):
    chunks.append(list_[i : i + chunk_size])
  return chunks
