""" Utilities for working with numbers (int, float) """

def assert_clamp(v, min_, max_, name):
  if type(v) is not list:
    v = [v]
  for w in v:
    assert min_ <= w <= max_, f"{name} must be between {min_} and {max_}, but is {w}"
