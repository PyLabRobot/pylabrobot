"""Device routing and import isolation for the public plr command."""

import contextlib
import io
import subprocess
import sys
import unittest
from typing import Optional, Sequence
from unittest.mock import patch

from pylabrobot.cli import main
from pylabrobot.cli_registry import DeviceCLI


class RecordingCLI:
  """Record arguments forwarded to an independently implemented device CLI."""

  def __init__(self) -> None:
    """Start with no dispatched commands."""
    self.calls: list[list[str]] = []

  def main(self, argv: Optional[Sequence[str]] = None) -> None:
    """Record the device's command line without interpreting its options."""
    self.calls.append(list(argv or []))


class TestDeviceRouting(unittest.TestCase):
  """New devices register independently of the root parser and each other."""

  def test_only_selected_device_loads_and_receives_all_arguments(self) -> None:
    """Forward arbitrary device operations, options, and help without importing siblings."""
    registry = {
      "first": DeviceCLI("example.first.cli", "First device"),
      "second": DeviceCLI("example.second.cli", "Second device"),
    }
    for tail in (
      ["calibrate", "--position", "-12.5", "--name=sample one"],
      ["--help"],
      ["inspect", "--help"],
    ):
      with self.subTest(arguments=tail):
        handler = RecordingCLI()
        with (
          patch("pylabrobot.cli.DEVICE_CLIS", registry),
          patch("pylabrobot.cli_registry.import_module", return_value=handler) as load,
        ):
          main(["dev", "second", *tail])
        load.assert_called_once_with("example.second.cli")
        self.assertEqual(handler.calls, [tail])

  def test_top_level_help_and_invalid_devices_never_load_a_device(self) -> None:
    """Help and invalid routes remain available when device dependencies are unavailable."""
    for arguments, expected_status in (
      (["--help"], 0),
      (["dev", "--help"], 0),
      ([], 2),
      (["dev"], 2),
      (["dev", "missing-device"], 2),
      (["missing-command"], 2),
    ):
      with self.subTest(arguments=arguments):
        with (
          patch(
            "pylabrobot.cli.DEVICE_CLIS",
            {"example": DeviceCLI("example.cli", "Example device")},
          ),
          patch("pylabrobot.cli_registry.import_module") as load,
          contextlib.redirect_stdout(io.StringIO()),
          contextlib.redirect_stderr(io.StringIO()),
          self.assertRaises(SystemExit) as error,
        ):
          main(arguments)
        self.assertEqual(error.exception.code, expected_status)
        load.assert_not_called()

  def test_help_works_without_the_registered_device_dependencies(self) -> None:
    """Listing a device must work even when its module cannot be imported."""
    result = subprocess.run(
      [
        sys.executable,
        "-c",
        "from pylabrobot import cli\n"
        "from pylabrobot.cli_registry import DeviceCLI\n"
        "cli.DEVICE_CLIS = {'example': DeviceCLI('missing_device_package.cli', 'Example device')}\n"
        "try:\n"
        "  cli.main(['dev', '--help'])\n"
        "except SystemExit as error:\n"
        "  assert error.code == 0\n",
      ],
      capture_output=True,
      text=True,
      timeout=10,
    )
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("Example device", result.stdout)

  def test_empty_registry_explains_that_no_device_clis_are_available(self) -> None:
    """An installation without device registrations still has useful command help."""
    for arguments in (["dev"], ["dev", "--help"]):
      with self.subTest(arguments=arguments):
        output = io.StringIO()
        with (
          patch("pylabrobot.cli.DEVICE_CLIS", {}),
          patch("pylabrobot.cli_registry.import_module") as load,
          contextlib.redirect_stdout(output),
        ):
          if "--help" in arguments:
            with self.assertRaises(SystemExit) as error:
              main(arguments)
            self.assertEqual(error.exception.code, 0)
          else:
            main(arguments)
        self.assertIn("No device CLIs are registered", output.getvalue())
        self.assertNotIn("{}", output.getvalue())
        load.assert_not_called()

  def test_empty_registry_rejects_unknown_arguments(self) -> None:
    """The empty-registry message must not mask misspelled commands or options."""
    for arguments in (["dev", "unknown"], ["dev", "--unknown"]):
      with self.subTest(arguments=arguments):
        with (
          patch("pylabrobot.cli.DEVICE_CLIS", {}),
          contextlib.redirect_stderr(io.StringIO()),
          self.assertRaises(SystemExit) as error,
        ):
          main(arguments)
        self.assertEqual(error.exception.code, 2)
