""" Utilities for parsing Hamilton files (.lay, .tml, .ctr, .rck).

All are based on the seemingly arbitrary use of ascii escape characters.
"""

def find_int(key, c):
  for i in range(16*4):
    for j in range(16*4):
      try:
        return int(c.split(key + chr(i))[1].split(chr(j))[0])
      except (IndexError, ValueError):
        continue


def find_float(key, c):
  for i in range(16*4):
    for j in range(16*4):
      try:
        return float(c.split(key + chr(i))[1].split(chr(j))[0])
      except (IndexError, ValueError):
        continue


def find_string(key, c):
  finds = []
  for i in range(32):
    try:
      finds.append(c.split(key)[1][1:].split(chr(i))[0])
    except (IndexError, ValueError):
      continue
  return min(finds, key=len)
