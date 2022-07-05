""" Utilities for working with numbers (int, float) """

def assert_clamp(v, min_, max_, name, error=AssertionError):
  if type(v) is not list:
    v = [v]
  for w in v:
    if not min_ <= w <= max_:
      raise error(f"{name} must be between {min_} and {max_}, but is {w}")
