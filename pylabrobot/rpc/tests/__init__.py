"""Hardware-free checks of the shared device capability boundary."""

import unittest
from importlib.util import find_spec

try:
  if any(find_spec(name) is None for name in ("grpc", "google.protobuf")):
    raise unittest.SkipTest("RPC dependencies are not installed")
except ModuleNotFoundError as exc:
  raise unittest.SkipTest("RPC dependencies are not installed") from exc
