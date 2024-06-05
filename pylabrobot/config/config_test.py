import asyncio
import configparser
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import pytest

from pylabrobot import load_config
from pylabrobot.config.config import Config
from pylabrobot.config.service.file import FileReader, FileWriter
from pylabrobot.config.service.ini_file import IniReader, IniWriter
from pylabrobot.config.service.reader import ConfigReader
from pylabrobot.config.service.writer import ConfigWriter


@pytest.fixture
def tmp_dir(tmp_path):
  return tmp_path


@pytest.fixture
def fake_config(tmp_dir):
  return Config(
    logging=Config.Logging(
      log_dir=tmp_dir / "logs",
    )
  )


@pytest.fixture
def ini_path(tmp_dir):
  return tmp_dir / "config.ini"


@pytest.fixture
def ini_config(ini_path, fake_config):
  def make_ini_config():
    config = configparser.ConfigParser()
    cfg_dict = asdict(fake_config)
    for k, v in cfg_dict.items():
      config[k] = v

    with open(ini_path, "w") as f:
      config.write(f)

    return ini_path

  return make_ini_config


async def run_file_reader_test(
  format_reader: type[ConfigReader],
  create_cfg: Callable[[], Path], should_be: Config
):
  path = create_cfg()
  rdr = FileReader(
    format_reader=format_reader(),
  )
  cfg = rdr.read(path)
  assert cfg == should_be


async def run_file_reader_writer_test(
  format_reader: type[ConfigWriter],
  format_writer: type[ConfigWriter],
  write_to: Path,
  should_be: Config,
):
  wtr = FileWriter(
    format_writer=format_writer(),
  )
  wtr.write(write_to, should_be)
  rdr = FileReader(
    format_reader=format_reader(),
  )
  cfg = rdr.read(write_to)
  assert cfg == should_be


@pytest.mark.asyncio
async def test_file_reader_writer(tmp_dir, fake_config):
  cases = (
    (IniReader, IniWriter, "fake_config.ini"),
  )
  futures = []
  for rdr, wr, fp in cases:
    futures.append(
      run_file_reader_writer_test(
        rdr, wr, tmp_dir / fp, fake_config
      )
    )
  await asyncio.gather(*futures)


@pytest.mark.asyncio
async def test_load_config_creates_default():
  cfg = load_config("test_config", create_default=True, create_module_level=False)
  cwd = Path.cwd()
  assert (cwd / "test_config.ini").exists()
  assert cfg == Config()

  (cwd / "test_config.ini").unlink()
