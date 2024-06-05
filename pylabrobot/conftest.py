import pytest

from pylabrobot import Config, configure

TEST_CONFIG = Config(
  logging=Config.Logging(
    log_dir="test_logs"
  )
)


@pytest.fixture(autouse=True)
def setup_test_config():
  configure(TEST_CONFIG)
  yield
