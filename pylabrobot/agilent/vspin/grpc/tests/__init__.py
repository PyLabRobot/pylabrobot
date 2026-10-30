"""Tests for the VSpin gRPC service."""

import unittest
from importlib.util import find_spec

try:
  if any(find_spec(name) is None for name in ("fcntl", "grpc", "google.protobuf")):
    raise unittest.SkipTest("VSpin RPC dependencies are not installed")
except ModuleNotFoundError as exc:
  raise unittest.SkipTest("VSpin RPC dependencies are not installed") from exc
