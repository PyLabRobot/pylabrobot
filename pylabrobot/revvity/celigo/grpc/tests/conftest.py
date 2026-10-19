"""Collect optional transport tests when their test dependencies are installed."""

from importlib.util import find_spec


def transport_dependencies_available() -> bool:
  """Check optional imports without requiring them for the base driver suite."""
  try:
    return all(
      find_spec(name) is not None
      for name in (
        "fcntl",
        "grpc",
        "google.protobuf",
        "cryptography",
        "numpy",
        "PIL",
        "pytest_asyncio",
      )
    )
  except ModuleNotFoundError:
    return False


collect_ignore_glob = [] if transport_dependencies_available() else ["*_tests.py"]
