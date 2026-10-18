"""Collect shared RPC tests only when their optional test dependencies are installed."""

from importlib.util import find_spec


def dependencies_available() -> bool:
  """Check only transport dependencies; cameras, image codecs, and SQLite are unnecessary."""
  try:
    return all(
      find_spec(name) is not None
      for name in ("grpc", "google.protobuf", "cryptography", "pytest_asyncio")
    )
  except ModuleNotFoundError:
    return False


collect_ignore_glob = [] if dependencies_available() else ["*_tests.py"]
