"""Every resource factory in `pylabrobot.resources` builds something its serialization rebuilds."""

import importlib
import inspect
import marshal
import pkgutil
import unittest
import warnings
from typing import Any, Callable, List, Set, Tuple
from unittest.mock import patch

import pylabrobot.resources
from pylabrobot.resources.resource import Resource

# Packages that raise on import because they are deprecated aliases of another package.
DEPRECATED_PACKAGES = {"pylabrobot.resources.falcon"}

# Factories whose state does not survive deserialize and load_all_state. The CO-RE gripper tools
# parked in a holder are left out of `HamiltonCoreGrippers.serialize()` as state, but its state does
# not carry them either, so a rebuilt holder, or a deck with one, has no tools.
STATE_NOT_RESTORED = {
  "pylabrobot.resources.hamilton.core_grippers.hamilton_core_gripper_1000ul_5ml_on_waste",
  "pylabrobot.resources.hamilton.core_grippers.hamilton_core_gripper_1000ul_at_waste",
  "pylabrobot.resources.hamilton.core_grippers.prep_core_gripper_holder",
  "pylabrobot.resources.hamilton.core_grippers.prep_core_gripper_mount",
  "pylabrobot.resources.hamilton.star_decks.STARDeck",
  "pylabrobot.resources.hamilton.star_decks.STARLetDeck",
  "pylabrobot.resources.hamilton.star_decks.STARPlusDeck",
}


class _DownloadRefused(Exception):
  """Raised in place of fetching an Opentrons labware definition from GitHub."""


def _refuse_download(ot_name: str, force_download: bool):
  raise _DownloadRefused(ot_name)


def _is_incomplete_definition(error: Exception) -> bool:
  """Whether a factory refused to build because its definition is not finished.

  Such a factory builds nothing, so there is nothing to round-trip. Any other error fails the test.
  """
  if isinstance(error, (NotImplementedError, _DownloadRefused)):
    return True
  return isinstance(error, ValueError) and "pedestal_size_z must be provided" in str(error)


def _factories() -> List[Tuple[str, Callable[..., Any]]]:
  """Public functions in `pylabrobot.resources` that build a resource from `name` alone."""
  failed_imports: Set[str] = set()
  factories: List[Tuple[str, Callable[..., Any]]] = []
  for info in pkgutil.walk_packages(
    pylabrobot.resources.__path__,
    pylabrobot.resources.__name__ + ".",
    onerror=failed_imports.add,
  ):
    if info.name.endswith("_tests") or info.name in DEPRECATED_PACKAGES:
      continue
    module = importlib.import_module(info.name)
    for name, function in inspect.getmembers(module, inspect.isfunction):
      if name.startswith("_") or function.__module__ != module.__name__:
        continue
      parameters = inspect.signature(function).parameters
      if "name" not in parameters:
        continue
      if any(
        p.name != "name" and p.default is inspect.Parameter.empty for p in parameters.values()
      ):
        continue
      factories.append((f"{info.name}.{name}", function))
  assert failed_imports == DEPRECATED_PACKAGES, failed_imports
  return factories


def _comparable(data: Any) -> Any:
  """Serialized data with marshalled functions replaced by their code objects.

  Marshalling the same code object twice need not give the same bytes, but the code objects the
  bytes load to compare equal when the functions are the same.
  """
  if isinstance(data, dict):
    if data.get("type") == "function":
      return {
        **{k: _comparable(v) for k, v in data.items()},
        "code": marshal.loads(bytes.fromhex(data["code"])),
      }
    return {k: _comparable(v) for k, v in data.items()}
  if isinstance(data, list):
    return [_comparable(v) for v in data]
  return data


class FactorySerializationTests(unittest.TestCase):
  def test_factories_round_trip(self):
    """Serializing is repeatable, and a resource rebuilt from its serialization and state
    serializes the same as the original."""
    built = 0
    with (
      warnings.catch_warnings(),
      patch("pylabrobot.resources.opentrons.load._download_ot_resource_file", _refuse_download),
    ):
      warnings.simplefilter("ignore")
      for qualified_name, factory in _factories():
        with self.subTest(factory=qualified_name):
          try:
            resource = factory(name="resource")
          except Exception as error:
            if _is_incomplete_definition(error):
              continue
            raise
          if not isinstance(resource, Resource):
            continue
          built += 1
          data = resource.serialize()
          self.assertEqual(_comparable(resource.serialize()), _comparable(data))
          restored = Resource.deserialize(data, allow_marshal=True)
          self.assertIs(type(restored), type(resource))
          self.assertEqual(_comparable(restored.serialize()), _comparable(data))
          state = resource.serialize_all_state()
          restored.load_all_state(state)
          if qualified_name in STATE_NOT_RESTORED:
            # Fails once the state is restored: take it out of STATE_NOT_RESTORED then.
            self.assertNotEqual(restored.serialize_all_state(), state)
          else:
            self.assertEqual(restored.serialize_all_state(), state)
    self.assertGreater(built, 0)
