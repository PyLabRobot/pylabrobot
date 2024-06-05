from pathlib import Path
from typing import Type

from pylabrobot import load_config
from pylabrobot.config.config import Config
from pylabrobot.config.service.file import FileReader, FileWriter
from pylabrobot.config.service.ini_file import IniReader, IniWriter
from pylabrobot.config.service.json_config import JsonReader, JsonWriter
from pylabrobot.config.service.reader import ConfigReader
from pylabrobot.config.service.writer import ConfigWriter


def run_file_reader_writer_test(
  format_reader: Type[ConfigReader],
  format_writer: Type[ConfigWriter],
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


def test_file_reader_writer(tmp_path):
  fake_config = Config(
    logging=Config.Logging(
      log_dir=tmp_path / "logs",
    )
  )
  cases = (
    (IniReader, IniWriter, "fake_config.ini"),
    (JsonReader, JsonWriter, "fake_config.json"),
  )
  for rdr, wr, fp in cases:
    run_file_reader_writer_test(
      rdr, wr, tmp_path / fp, fake_config
    )


def test_load_config_creates_default():
  cwd = Path.cwd()
  test_path = cwd / "test_config.ini"
  if test_path.exists():
    test_path.unlink()
  assert not test_path.exists()
  cfg = load_config("test_config", create_default=True,
                    create_module_level=False)
  assert test_path.exists()
  assert cfg == Config()

  (cwd / "test_config.ini").unlink()
