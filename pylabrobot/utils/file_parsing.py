""" Utilities for parsing Hamilton files (.lay, .tml, .ctr, .rck).

All are based on the seemingly arbitrary use of ascii escape characters.
"""

import itertools


def find_int(key, c):
  for i, j, k in itertools.product(range(16*2), range(16*2), range(16*2)):
    try:
      return int(c.split(chr(i) + key + chr(j))[1].split(chr(k))[0])
    except (IndexError, ValueError):
      continue


def find_float(key, c):
  for i, j, k in itertools.product(range(16*2), range(16*2), range(16*2)):
    try:
      return float(c.split(chr(i) + key + chr(j))[1].split(chr(k))[0])
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
