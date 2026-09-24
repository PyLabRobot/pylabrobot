import json
from pathlib import Path
from typing import Any, Dict, cast

import pytest

from pylabrobot import Config, configure, project_root
from pylabrobot.resources.opentrons import load as opentrons_load

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
