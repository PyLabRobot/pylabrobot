def matrix_multiply_3x3(A, B):
  """Multiplies two 3x3 matrices A and B."""
  return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]

def matrix_vector_multiply_3x3(A, v):
  """Multiplies a 3x3 matrix A with a 3x1 vector v."""
  return [sum(A[i][j] * v[j] for j in range(3)) for i in range(3)]
