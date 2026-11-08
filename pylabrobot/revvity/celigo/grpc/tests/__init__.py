"""Tests for the optional Celigo gRPC package."""

import unittest
from importlib.util import find_spec

try:
  if any(find_spec(name) is None for name in ("fcntl", "grpc", "google.protobuf", "numpy", "PIL")):
    raise unittest.SkipTest("Celigo RPC dependencies are not installed")
except ModuleNotFoundError as exc:
  raise unittest.SkipTest("Celigo RPC dependencies are not installed") from exc
