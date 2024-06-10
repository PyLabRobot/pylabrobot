from pathlib import Path
import tempfile
import unittest

from pylabrobot import load_config
from pylabrobot.config.config import Config
from pylabrobot.config.io.file import FileReader, FileWriter
from pylabrobot.config.formats import ConfigLoader, ConfigSaver
from pylabrobot.config.formats.ini_config import IniLoader, IniSaver
from pylabrobot.config.formats.json_config import JsonLoader, JsonSaver


class ConfigTests(unittest.TestCase):
  """ Tests for pylabrobot.config """
  def run_file_reader_writer_test(
    self,
    format_loader: ConfigLoader,
    format_saver: ConfigSaver,
    write_to: Path,
    should_be: Config,
  ):
    writer = FileWriter(
      format_saver=format_saver
    )
    writer.write(write_to, should_be)
    reader = FileReader(
      format_loader=format_loader
    )
    cfg = reader.read(write_to)
    assert cfg == should_be

  def test_file_reader_writer(self):
    tmp_path: Path = Path(tempfile.mkdtemp())
    fake_config = Config(
      logging=Config.Logging(
        log_dir=tmp_path / "logs",
      )
    )
    cases = (
      (IniLoader(), IniSaver(), "fake_config.ini"),
      (JsonLoader(), JsonSaver(), "fake_config.json"),
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
