"""Offline CLI behavior using temporary synthetic input files."""

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from .cli import _probe, main
from .test_helpers import bdz_bytes


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
        main(["inspect", str(path)])
      self.assertTrue(json.loads(out.getvalue())["execution_supported"])
      (folder / "unsupported.BDZ").write_bytes(bdz_bytes("ABC 1\n"))
      (folder / "invalid.bdz").write_bytes(b"invalid")
      out = io.StringIO()
      with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as error:
        main(["audit", str(folder)])
      self.assertEqual(error.exception.code, 1)
      result = json.loads(out.getvalue())
      self.assertEqual((result["supported"], result["unsupported"], result["invalid"]), (1, 1, 1))
      self.assertFalse(result["hardware_accessed"])

  def test_help_requires_no_hardware(self) -> None:
    """Help exits without importing or constructing a live transport."""
    with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
      main(["--help"])
    self.assertEqual(error.exception.code, 0)


class FlexProbeTests(unittest.IsolatedAsyncioTestCase):
  """Probe lifecycle with a simulated transport."""

  async def test_probe_identifies_and_closes(self) -> None:
    """The explicit probe sends only VER and closes its transport."""
    from .flex import KingFisherFlex
    from .flex_tests import ScriptedHID

    flex = KingFisherFlex()
    transport = ScriptedHID()
    flex.io = transport
    with patch("pylabrobot.thermo_fisher.kingfisher.flex.cli.KingFisherFlex", return_value=flex):
      result = await _probe(None, None)
    self.assertEqual(transport.commands, ["VER"])
    self.assertEqual(transport.closed, 1)
    self.assertEqual(result["firmware"], "1.01.0")
