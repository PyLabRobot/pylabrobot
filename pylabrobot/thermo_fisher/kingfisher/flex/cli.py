"""Offline Flex export inspection and an explicit VER-only hardware probe."""

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from pylabrobot.thermo_fisher.kingfisher.flex.flex import KingFisherFlex
from pylabrobot.thermo_fisher.kingfisher.flex.protocol import FlexProtocolError, read_flex_bdz


def inspect_protocol(path: Path) -> dict:
  """Return inspection and execution-coverage information without constructing HID."""
  protocol = read_flex_bdz(path)
  result = {
    "name": protocol.name,
    "sha256": protocol.sha256,
    "bindit_version": protocol.bindit_version,
    "script_lines": len(protocol.script.splitlines()),
    "plates": [asdict(plate) for plate in protocol.plates],
    "tips": [asdict(tip) for tip in protocol.tips],
  }
  issues = protocol.compatibility_issues()
  result["execution_supported"] = not issues
  result["issues"] = [asdict(issue) for issue in issues]
  result["hardware_validation"] = "Not determined by offline inspection"
  if not issues:
    commands = protocol.preflight()
    result.update(
      {
        "stream_preflight": "supported",
        "stream_commands": len(commands),
        "prompts": [asdict(prompt) for prompt in protocol.prompts()],
      }
    )
  else:
    result.update(
      {
        "stream_preflight": "unsupported",
        "reason": "; ".join(f"{issue.location}: {issue.message}" for issue in issues),
      }
    )
  return result


def audit_protocols(paths: Sequence[Path]) -> dict:
  """Inspect files or directories of BDZs, retaining failures instead of stopping early.

  Directories are scanned nonrecursively. Explicit files need not have a particular
  extension. An empty/unreadable directory or invalid export counts as a failure.
  No source is rewritten and no HID connection is constructed.
  """
  results = []
  for path in paths:
    try:
      files = (
        sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".bdz")
        if path.is_dir()
        else [path]
      )
      if not files:
        raise ValueError("Directory contains no BDZ files")
    except (OSError, ValueError) as error:
      results.append({"path": str(path), "status": "invalid", "reason": str(error)})
      continue
    for file in files:
      try:
        inspected = inspect_protocol(file)
      except (OSError, FlexProtocolError) as error:
        results.append({"path": str(file), "status": "invalid", "reason": str(error)})
      else:
        results.append(
          {
            "path": str(file),
            "status": "supported" if inspected["execution_supported"] else "unsupported",
            **inspected,
          }
        )
  return {
    "hardware_accessed": False,
    "total": len(results),
    "supported": sum(r["status"] == "supported" for r in results),
    "unsupported": sum(r["status"] == "unsupported" for r in results),
    "invalid": sum(r["status"] == "invalid" for r in results),
    "protocols": results,
  }


async def _probe(serial_number: Optional[str], transcript_path: Optional[Path]) -> dict:
  """Open the selected Flex, send VER only, and close it even on failure."""
  instrument = KingFisherFlex(serial_number=serial_number)
  try:
    await instrument.setup()
    return {"model": "KingFisherFlex", "firmware": instrument.version, "serial": instrument.serial}
  finally:
    await instrument.stop()
    if transcript_path is not None:
      transcript_path.write_text(
        json.dumps([asdict(entry) for entry in instrument.transcript], indent=2) + "\n"
      )


def main(argv: Optional[Sequence[str]] = None) -> None:
  """Dispatch offline commands or an explicitly selected identification-only probe."""
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="operation", required=True)
  inspect = commands.add_parser("inspect", help="Inspect a Flex .bdz without USB access")
  inspect.add_argument("path", type=Path)
  audit = commands.add_parser("audit", help="Check all BDZ files in folders, without USB access")
  audit.add_argument("paths", type=Path, nargs="+", help="Files or nonrecursive directories")
  probe = commands.add_parser("probe", help="Connect to real hardware and send VER only")
  probe.add_argument("--serial-number")
  probe.add_argument("--transcript", type=Path)
  args = parser.parse_args(argv)
  if args.operation == "inspect":
    result = inspect_protocol(args.path)
  elif args.operation == "audit":
    result = audit_protocols(args.paths)
  else:
    result = asyncio.run(_probe(args.serial_number, args.transcript))
  print(json.dumps(result, indent=2))
  if args.operation == "audit" and (result["unsupported"] or result["invalid"]):
    raise SystemExit(1)


if __name__ == "__main__":
  main()
