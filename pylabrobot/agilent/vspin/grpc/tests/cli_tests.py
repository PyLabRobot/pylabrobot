"""VSpin command dispatch and argument validation without hardware connections."""

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pylabrobot.agilent.vspin import cli
from pylabrobot.cli import main


class VSpinCLITests(unittest.TestCase):
  """Exercise the registered VSpin CLI through the public launcher."""

  def test_serve_receives_options(self) -> None:
    """Forward service options and convert paths and numbers before starting the handler."""
    with patch.object(cli, "serve") as serve, patch.object(cli, "inspect") as inspect:
      main(
        [
          "dev",
          "vspin",
          "serve",
          "--data-dir",
          "/data/vspin",
          "--ftdi-device-id",
          "VSPIN123",
          "--hardware-lock",
          "/locks/vspin.lock",
          "--device-id",
          "vspin-test",
          "--host",
          "0.0.0.0",
          "--port",
          "50054",
          "--max-jobs",
          "128",
        ]
      )
    serve.assert_awaited_once()
    inspect.assert_not_called()
    args = serve.call_args.args[0]
    self.assertEqual(args.data_dir, Path("/data/vspin"))
    self.assertEqual(args.ftdi_device_id, "VSPIN123")
    self.assertEqual(args.hardware_lock, Path("/locks/vspin.lock"))
    self.assertEqual(args.device_id, "vspin-test")
    self.assertEqual(args.host, "0.0.0.0")
    self.assertEqual(args.port, 50054)
    self.assertEqual(args.max_jobs, 128)

  def test_serve_defaults_keep_the_hardware_lock_independent_of_data_and_identity(self) -> None:
    """Renaming a server or relocating its data must not bypass the default hardware lock."""
    home = Path("/home/test-user")
    for options, data_dir in (
      ([], home / ".pylabrobot/vspin/vspin-01"),
      (["--device-id", "vspin-02"], home / ".pylabrobot/vspin/vspin-02"),
      (["--data-dir", "/custom/data"], Path("/custom/data")),
    ):
      with (
        self.subTest(options=options),
        patch.object(Path, "home", return_value=home),
        patch.object(cli, "serve") as serve,
      ):
        main(["dev", "vspin", "serve", *options])
      args = serve.call_args.args[0]
      self.assertEqual(args.data_dir, data_dir)
      self.assertEqual(args.hardware_lock, home / ".pylabrobot/vspin/hardware.lock")
      self.assertIsNone(args.ftdi_device_id)
      self.assertEqual(args.host, "127.0.0.1")

  def test_inspect_prints_json(self) -> None:
    """Inspect selects the client handler and prints its result with the requested identity."""
    result = {"device": {"device_id": "vspin-test"}, "status": {}, "configuration": {}}
    for options, device_id, endpoint in (
      ([], "vspin-01", "127.0.0.1:50053"),
      (
        ["--device-id", "vspin-test", "--endpoint", "vspin-host:50054"],
        "vspin-test",
        "vspin-host:50054",
      ),
    ):
      with self.subTest(options=options):
        output = io.StringIO()
        with (
          patch.object(cli, "serve") as serve,
          patch.object(cli, "inspect", return_value=result) as inspect,
          contextlib.redirect_stdout(output),
        ):
          main(["dev", "vspin", "inspect", *options])
        serve.assert_not_called()
        inspect.assert_awaited_once()
        args = inspect.call_args.args[0]
        self.assertEqual(args.endpoint, endpoint)
        self.assertEqual(args.device_id, device_id)
        self.assertEqual(json.loads(output.getvalue()), result)

  def test_help_does_not_start_operations(self) -> None:
    """Device and operation help show the public command path without executing handlers."""
    for command in ([], ["serve"], ["inspect"]):
      with self.subTest(command=command):
        output = io.StringIO()
        with (
          patch.object(cli, "serve") as serve,
          patch.object(cli, "inspect") as inspect,
          contextlib.redirect_stdout(output),
          self.assertRaises(SystemExit) as error,
        ):
          main(["dev", "vspin", *command, "--help"])
        self.assertEqual(error.exception.code, 0)
        self.assertIn(
          "usage: plr dev vspin" + (" " + command[0] if command else ""), output.getvalue()
        )
        serve.assert_not_called()
        inspect.assert_not_called()

  def test_invalid_arguments_do_not_start_operations(self) -> None:
    """Missing options and invalid commands fail before any service or client is started."""
    for arguments in (
      [],
      ["unknown"],
      ["serve", "--max-jobs", "invalid"],
      ["inspect", "--endpoint"],
      ["inspect", "--endpoint", "127.0.0.1:50053", "--unknown"],
    ):
      with self.subTest(arguments=arguments):
        with (
          patch.object(cli, "serve") as serve,
          patch.object(cli, "inspect") as inspect,
          contextlib.redirect_stderr(io.StringIO()),
          self.assertRaises(SystemExit) as error,
        ):
          main(["dev", "vspin", *arguments])
        self.assertEqual(error.exception.code, 2)
        serve.assert_not_called()
        inspect.assert_not_called()
