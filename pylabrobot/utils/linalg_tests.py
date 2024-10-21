import unittest


from .linalg import matrix_multiply_3x3, matrix_vector_multiply_3x3


class TestLinalg(unittest.TestCase):
  """ Test the linalg functions """
  def test_matrix_multiply_3x3(self):
    A = [
      [1, 2, 3],
      [4, 5, 6],
      [7, 8, 9]
    ]
    B = [
      [1, 2, 3],
      [4, 5, 6],
      [7, 8, 9]
    ]
    C = matrix_multiply_3x3(A, B)
    assert C == [
      [30, 36, 42],
      [66, 81, 96],
      [102, 126, 150]
    ]

  def test_matrix_vector_multiply_3x3(self):
    A = [
      [1, 2, 3],
      [4, 5, 6],
      [7, 8, 9]
    ]
    B = [1, 2, 3]
    C = matrix_vector_multiply_3x3(A, B)
    assert C == [14, 32, 50]
