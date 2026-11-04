import importlib
import inspect
import json
import pkgutil
from pathlib import Path
from typing import Any, Dict, cast

import pytest

import pylabrobot.legacy as legacy_root
from pylabrobot import Config, configure, project_root
from pylabrobot.concurrency import HasLifespan
from pylabrobot.legacy.machines.backend import MachineBackend
from pylabrobot.resources.opentrons import load as opentrons_load


def _import_all_legacy_production_modules() -> list[str]:
  failures: list[str] = []
  for module_info in pkgutil.walk_packages(legacy_root.__path__, legacy_root.__name__ + "."):
    if module_info.name.endswith("_tests") or ".tests." in module_info.name:
      continue
    try:
      importlib.import_module(module_info.name)
    except BaseException as error:
      failures.append(f"{module_info.name}: {type(error).__name__}: {error}")
  return sorted(failures)


def _all_machine_backend_descendants() -> set[type[Any]]:
  pending = list(MachineBackend.__subclasses__())
  descendants: set[type[Any]] = set()
  while pending:
    backend_type = pending.pop()
    if backend_type in descendants:
      continue
    descendants.add(backend_type)
    pending.extend(backend_type.__subclasses__())
  return descendants


def _final_native_contract_violations() -> tuple[list[str], list[str]]:
  production: list[str] = []
  tests: list[str] = []
  for backend_type in _all_machine_backend_descendants():
    if inspect.isabstract(backend_type):
      continue
    if (
      backend_type.setup is HasLifespan.setup
      and backend_type.stop is HasLifespan.stop
      and backend_type.__aenter__ is HasLifespan.__aenter__
      and backend_type.__aexit__ is HasLifespan.__aexit__
    ):
      continue
    qualified_name = f"{backend_type.__module__}.{backend_type.__qualname__}"
    if backend_type.__module__.endswith("_tests") or ".tests." in backend_type.__module__:
      tests.append(qualified_name)
    else:
      production.append(qualified_name)
  return sorted(production), sorted(tests)


def pytest_collection_finish(session: pytest.Session) -> None:
  del session
  import_failures = _import_all_legacy_production_modules()
  production_violations, test_violations = _final_native_contract_violations()
  if import_failures or production_violations or test_violations:
    lines = ["legacy MachineBackend final native contract audit failed"]
    lines.extend(f"import failure: {failure}" for failure in import_failures)
    lines.extend(f"production: {name}" for name in production_violations)
    lines.extend(f"test class: {name}" for name in test_violations)
    raise pytest.UsageError("\n".join(lines))


TEST_CONFIG = Config(logging=Config.Logging(log_dir=project_root() / Path("test_logs")))


@pytest.fixture(autouse=True)
def setup_test_config():
  configure(TEST_CONFIG)
  yield


@pytest.fixture(autouse=True)
def offline_opentrons_labware(monkeypatch: pytest.MonkeyPatch) -> None:
  """Load pinned labware fixtures independently of the network and the system cache."""
  fixture_dir = Path(__file__).parent / "testing" / "test_data" / "opentrons"

  def load_definition(ot_name: str, force_download: bool = False) -> Dict[str, Any]:
    """Read a fresh definition so mutations cannot leak between resource loads."""
    return cast(
      Dict[str, Any], json.loads((fixture_dir / f"{ot_name}.json").read_text(encoding="utf-8"))
    )

  monkeypatch.setattr(opentrons_load, "_download_ot_resource_file", load_definition)
