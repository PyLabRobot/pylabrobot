from pathlib import Path
import tempfile
import unittest

from pylabrobot import load_config
from pylabrobot.config.config import Config
from pylabrobot.config.service.file import FileLoader, FileSaver
from pylabrobot.config.service.ini_file import IniReader, IniWriter
from pylabrobot.config.service.json_config import JsonReader, JsonWriter
from pylabrobot.config.service import ConfigReader, ConfigWriter


class ConfigTests(unittest.TestCase):
  """ Tests for pylabrobot.config """
  def run_file_reader_writer_test(
    self,
    format_reader: ConfigReader,
    format_writer: ConfigWriter,
    write_to: Path,
    should_be: Config,
  ):
    saver = FileSaver(
      format_writer=format_writer,
    )
    saver.save(write_to, should_be)
    loader = FileLoader(
      format_reader=format_reader,
    )
    cfg = loader.load(write_to)
    assert cfg == should_be

  def test_file_reader_writer(self):
    tmp_path: Path = Path(tempfile.mkdtemp())
    fake_config = Config(
      logging=Config.Logging(
        log_dir=tmp_path / "logs",
      )
    )
    cases = (
      (IniReader(), IniWriter(), "fake_config.ini"),
      (JsonReader(), JsonWriter(), "fake_config.json"),
    )
    for rdr, wr, fp in cases:
      self.run_file_reader_writer_test(
        rdr, wr, tmp_path / fp, fake_config
      )

  def test_load_config_creates_default(self):
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
