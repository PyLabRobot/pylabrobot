"""Run daily pressure and cLLD checks on STAR 1000 uL channels.

The default prints the plan without connecting. --execute initializes the instrument,
then runs the checks with closed teaching needles.
The instrument's overall maintenance record is not updated.
"""

import argparse
import asyncio
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from pylabrobot import setup_logger
from pylabrobot.hamilton.star.driver.master import STARDriver
from pylabrobot.hamilton.star.maintenance import (
  DailyMaintenance,
  DailyMaintenanceReport,
  get_default_verification_pad_position,
)
from pylabrobot.io.validation_utils import LOG_LEVEL_IO
from pylabrobot.resources import Coordinate, STARDeck, STARLetDeck, TipRack
from pylabrobot.resources.hamilton.star_decks import HamiltonSTARDeck
from pylabrobot.resources.tip_tracking import set_tip_tracking


def create_maintenance_deck(model: str, needle_position: List[float]) -> HamiltonSTARDeck:
  """Place A1 at its calibrated center X/Y and collar support Z, preserving rack pitch."""
  if not all(math.isfinite(value) for value in needle_position):
    raise ValueError("Needle coordinates must be finite")
  deck = STARDeck() if model == "STAR" else STARLetDeck()
  rack = deck.get_resource(deck.get_component_name("teaching_tip_rack"))
  assert isinstance(rack, TipRack) and rack.location is not None
  current = rack.get_item(0).get_location_wrt(deck, x="c", y="c")
  rack.location += Coordinate(*needle_position) - current
  return deck


def write_json_report(path: Path, result: Dict[str, Any]) -> None:
  """Write the report atomically."""
  temporary = path.with_suffix(".tmp")
  temporary.write_text(json.dumps(result, indent=2) + "\n")
  temporary.replace(path)


async def run_daily_maintenance_cli(args: argparse.Namespace) -> int:
  """Initialize the instrument, run maintenance, and stop the driver."""
  set_tip_tracking(True)
  deck = create_maintenance_deck(args.model, args.needle_position)
  verification_position = (
    get_default_verification_pad_position(deck)
    if args.verification_position is None
    else Coordinate(*args.verification_position)
  )
  channels = None if args.channels is None else [channel - 1 for channel in args.channels]
  plan = {
    "model": args.model,
    "needle_position": args.needle_position,
    "verification_position": list(verification_position),
    "needle_count": args.needle_count,
    "traverse_height": args.traverse_height,
    "channels": channels,
  }
  rack = deck.get_resource(deck.get_component_name("teaching_tip_rack"))
  assert isinstance(rack, TipRack)
  print(json.dumps(plan, indent=2))
  if not args.execute:
    print("Plan only; no connection opened.")
    return 0
  stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
  args.output_dir.mkdir(parents=True, exist_ok=True)
  path = args.output_dir / f"star-daily-{stamp}.json"
  setup_logger(path.with_suffix(".log"), level=LOG_LEVEL_IO)
  result: Dict[str, Any] = {"plan": plan}

  def save_report_update(current: DailyMaintenanceReport) -> None:
    """Save measurements with evaluated criteria after each procedure boundary."""
    result["report"] = asdict(current)
    for name, checks in (("pressure", current.pressure), ("clld", current.clld)):
      result["report"][name] = [{**asdict(item), "passed": item.passed} for item in checks]
    write_json_report(path, result)

  write_json_report(path, result)
  driver = STARDriver(deck=deck, serial_number=args.serial_number)
  try:
    await driver.setup()
    config = driver.configuration
    assert config is not None
    result["device"] = {
      "serial": config.serial_number,
      "usb_serial": args.serial_number,
      "firmware": dict(driver.firmware),
      "channels": driver.num_channels,
    }
    maintenance = DailyMaintenance(
      driver=driver,
      needles=rack.get_all_items()[: args.needle_count],
      verification_position=verification_position,
      on_update=save_report_update,
      traverse_height=args.traverse_height,
      channels=channels,
    )
    report = await maintenance.run()
  except BaseException as error:
    result["error"] = f"{type(error).__name__}: {error}"
    raise
  finally:
    try:
      await driver.stop()
    finally:
      write_json_report(path, result)
  print(f"{report.state}. Report: {path}")
  return 0 if report.state == "passed" else 1


def main() -> int:
  """Parse calibrated positions and require an explicit hardware execution flag."""
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--model", choices=("STAR", "STARlet"), default="STAR")
  parser.add_argument(
    "--serial-number", help="Optional USB serial number to select a specific STAR"
  )
  parser.add_argument(
    "--needle-position", type=float, nargs=3, required=True, metavar=("X", "Y", "Z")
  )
  parser.add_argument(
    "--verification-position",
    type=float,
    nargs=3,
    metavar=("X", "Y", "Z"),
    help="Override the model's VENUS rear verification-pad position, in mm",
  )
  parser.add_argument("--needle-count", type=int, choices=range(1, 9), default=1)
  parser.add_argument("--channels", type=int, nargs="+", help="Physical channel numbers, 1-based")
  parser.add_argument("--traverse-height", type=float, default=245)
  parser.add_argument("--output-dir", type=Path, default=Path("star-service-runs"))
  parser.add_argument("--execute", action="store_true", help="Initialize and run daily maintenance")
  args = parser.parse_args()
  return asyncio.run(run_daily_maintenance_cli(args))


if __name__ == "__main__":
  raise SystemExit(main())
