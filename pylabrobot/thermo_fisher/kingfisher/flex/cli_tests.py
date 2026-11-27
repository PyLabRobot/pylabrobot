"""Offline CLI behavior using temporary synthetic input files."""

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pylabrobot.cli import main as plr_main

from .cli import _probe, main
from .test_helpers import HIDTestCase, bdz_bytes


class FlexCLITests(unittest.TestCase):
  """Inspection and auditing never create a hardware connection."""

  def test_inspect_and_audit_report_failures_without_usb(self) -> None:
    """Directory audits retain invalid inputs and use a failing exit status."""
    with (
      TemporaryDirectory() as tmp,
      patch(
        "pylabrobot.thermo_fisher.kingfisher.flex.cli.KingFisherFlex",
        side_effect=AssertionError("offline command opened hardware"),
      ),
    ):
      folder = Path(tmp)
      path = folder / "input.bdz"
      path.write_bytes(bdz_bytes())
      out = io.StringIO()
      with contextlib.redirect_stdout(out):
        plr_main(["dev", "kingfisher-flex", "bdz", "inspect", str(path)])
      self.assertTrue(json.loads(out.getvalue())["execution_supported"])
      (folder / "unsupported.BDZ").write_bytes(bdz_bytes("ABC 1\n"))
      (folder / "invalid.bdz").write_bytes(b"invalid")
      out = io.StringIO()
      with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as error:
        main(["bdz", "audit", str(folder)])
      self.assertEqual(error.exception.code, 1)
      result = json.loads(out.getvalue())
      self.assertEqual((result["supported"], result["unsupported"], result["invalid"]), (1, 1, 1))
      self.assertFalse(result["hardware_accessed"])

  def test_help_requires_no_hardware(self) -> None:
    """Device, BDZ and leaf-command help never construct a live transport."""
    for args, expected in (
      (["--help"], "{bdz,probe}"),
      (["bdz", "--help"], "{inspect,audit}"),
      (["bdz", "inspect", "--help"], "path"),
      (["bdz", "audit", "--help"], "paths"),
      (["probe", "--help"], "--serial-number"),
    ):
      with self.subTest(args=args):
        out = io.StringIO()
        with (
          patch("pylabrobot.thermo_fisher.kingfisher.flex.cli.KingFisherFlex") as hardware,
          contextlib.redirect_stdout(out),
          self.assertRaises(SystemExit) as error,
        ):
          main(args)
        self.assertEqual(error.exception.code, 0)
        self.assertIn(expected, out.getvalue())
        hardware.assert_not_called()

  def test_invalid_or_incomplete_commands_do_not_access_hardware(self) -> None:
    """File commands require the BDZ group and their arguments before dispatch."""
    for args in (
      ["inspect", "input.bdz"],
      ["audit", "input.bdz"],
      ["bdz"],
      ["bdz", "inspect"],
      ["bdz", "audit"],
      ["bdz", "probe"],
    ):
      with self.subTest(args=args):
        with (
          patch("pylabrobot.thermo_fisher.kingfisher.flex.cli.KingFisherFlex") as hardware,
          contextlib.redirect_stderr(io.StringIO()),
          self.assertRaises(SystemExit) as error,
        ):
          main(args)
        self.assertEqual(error.exception.code, 2)
        hardware.assert_not_called()

  def test_probe_remains_a_top_level_command(self) -> None:
    """The device probe forwards its arguments without entering BDZ dispatch."""
    result = {"model": "KingFisherFlex", "firmware": "1.01.0", "serial": "test"}
    out = io.StringIO()
    with (
      patch("pylabrobot.thermo_fisher.kingfisher.flex.cli._probe", return_value=result) as probe,
      contextlib.redirect_stdout(out),
    ):
      main(["probe", "--serial-number", "test", "--transcript", "output.json"])
    probe.assert_awaited_once_with("test", Path("output.json"))
    self.assertEqual(json.loads(out.getvalue()), result)


class FlexProbeTests(HIDTestCase):
  """Probe lifecycle with a simulated transport."""

  async def test_probe_identifies_and_closes(self) -> None:
    """The explicit probe sends only VER and closes its transport."""
    from .flex import KingFisherFlex

    flex = KingFisherFlex()
    self.mock_hid()
    flex.io = self.io
    with patch("pylabrobot.thermo_fisher.kingfisher.flex.cli.KingFisherFlex", return_value=flex):
      result = await _probe(None, None)
    self.assertEqual(self.commands, ["VER"])
    self.io.stop.assert_awaited_once_with()
    self.assertEqual(result["firmware"], "1.01.0")
