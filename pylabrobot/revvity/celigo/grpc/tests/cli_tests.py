"""Celigo command dispatch and argument validation without hardware connections."""

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pylabrobot.cli import main
from pylabrobot.revvity.celigo import cli


class CeligoCLITests(unittest.TestCase):
  """Exercise the registered Celigo CLI through the public launcher."""

  def test_serve_receives_options(self) -> None:
    """Forward service options and convert paths and numbers before starting the handler."""
    with patch.object(cli, "serve") as serve, patch.object(cli, "inspect") as inspect:
      main(
        [
          "dev",
          "celigo",
          "serve",
          "--data-dir",
          "/data/celigo",
          "--config-root",
          "/config/Celigo",
          "--lucam-sdk",
          "/sdk/liblucamapi.so",
          "--usb-address",
          "1-7",
          "--hardware-lock",
          "/locks/celigo.lock",
          "--device-id",
          "celigo-test",
          "--port",
          "50053",
        ]
      )
    serve.assert_awaited_once()
    inspect.assert_not_called()
    args = serve.call_args.args[0]
    self.assertEqual(args.data_dir, Path("/data/celigo"))
    self.assertEqual(args.config_root, Path("/config/Celigo"))
    self.assertEqual(args.lucam_sdk, "/sdk/liblucamapi.so")
    self.assertEqual(args.usb_address, "1-7")
    self.assertEqual(args.hardware_lock, Path("/locks/celigo.lock"))
    self.assertEqual(args.device_id, "celigo-test")
    self.assertEqual(args.port, 50053)

  def test_inspect_prints_json(self) -> None:
    """Inspect selects the client handler and prints its result with the requested identity."""
    result = {"device": {"device_id": "celigo-test"}, "status": {}, "configuration": {}}
    for options, device_id in (([], "celigo-01"), (["--device-id", "celigo-test"], "celigo-test")):
      with self.subTest(options=options):
        output = io.StringIO()
        with (
          patch.object(cli, "serve") as serve,
          patch.object(cli, "inspect", return_value=result) as inspect,
          contextlib.redirect_stdout(output),
        ):
          main(["dev", "celigo", "inspect", "--endpoint", "127.0.0.1:50052", *options])
        serve.assert_not_called()
        inspect.assert_awaited_once()
        args = inspect.call_args.args[0]
        self.assertEqual(args.endpoint, "127.0.0.1:50052")
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
          main(["dev", "celigo", *command, "--help"])
        self.assertEqual(error.exception.code, 0)
        self.assertIn(
          "usage: plr dev celigo" + (" " + command[0] if command else ""), output.getvalue()
        )
        serve.assert_not_called()
        inspect.assert_not_called()

  def test_invalid_arguments_do_not_start_operations(self) -> None:
    """Missing options and invalid commands fail before any service or client is started."""
    for arguments in (
      [],
      ["unknown"],
      ["serve"],
      ["inspect"],
      ["inspect", "--endpoint", "127.0.0.1:50052", "--unknown"],
    ):
      with self.subTest(arguments=arguments):
        with (
          patch.object(cli, "serve") as serve,
          patch.object(cli, "inspect") as inspect,
          contextlib.redirect_stderr(io.StringIO()),
          self.assertRaises(SystemExit) as error,
        ):
          main(["dev", "celigo", *arguments])
        self.assertEqual(error.exception.code, 2)
        serve.assert_not_called()
        inspect.assert_not_called()
