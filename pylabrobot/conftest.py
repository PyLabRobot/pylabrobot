from pathlib import Path

import pytest

from pylabrobot import Config, configure, project_root

TEST_CONFIG = Config(
  logging=Config.Logging(
    log_dir=project_root() / Path("test_logs")
  )
)


@pytest.fixture(autouse=True)
def setup_test_config():
  configure(TEST_CONFIG)
  yield
