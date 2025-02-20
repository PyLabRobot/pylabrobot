import logging

LOG_LEVEL_IO = 5
logging.addLevelName(LOG_LEVEL_IO, "IO")


def align_sequences(expected, actual):
  """Needleman-Wunsch algorithm for aligning the expected and actual write sequences.
  Convenient for seeing where firmware commands differ.
  """

  # Initialize DP table
  m, n = len(expected), len(actual)
  dp = [[0] * (n + 1) for _ in range(m + 1)]

  # Fill the DP table with gap penalties
  for i in range(m + 1):
    dp[i][0] = i
  for j in range(n + 1):
    dp[0][j] = j

  # Compute DP table
  for i in range(1, m + 1):
    for j in range(1, n + 1):
      cost = 0 if expected[i - 1] == actual[j - 1] else 1
      dp[i][j] = min(
        dp[i - 1][j - 1] + cost,  # Substitution/match
        dp[i - 1][j] + 1,  # Deletion (gap in s2)
        dp[i][j - 1] + 1,
      )  # Insertion (gap in s1)

  # Traceback to construct alignment
  i, j = m, n
  aligned_expected, aligned_actual, markers = [], [], []

  while i > 0 or j > 0:
    if (
      i > 0
      and j > 0
      and (dp[i][j] == dp[i - 1][j - 1] + (0 if expected[i - 1] == actual[j - 1] else 1))
    ):
      aligned_expected.append(expected[i - 1])
      aligned_actual.append(actual[j - 1])
      markers.append(" " if expected[i - 1] == actual[j - 1] else "^")
      i -= 1
      j -= 1
    elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
      aligned_expected.append(expected[i - 1])
      aligned_actual.append("-")
      markers.append("^")
      i -= 1
    else:
      aligned_expected.append("-")
      aligned_actual.append(actual[j - 1])
      markers.append("^")
      j -= 1

  # Reverse since we built the alignment backwards
  print("expected:", "".join(reversed(aligned_expected)))
  print("actual:  ", "".join(reversed(aligned_actual)))
  print("         ", "".join(reversed(markers)))
