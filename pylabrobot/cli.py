"""Command-line entry point for PyLabRobot development tools."""

import argparse
from typing import Optional, Sequence

from pylabrobot.cli_registry import DEVICE_CLIS


def main(argv: Optional[Sequence[str]] = None) -> None:
  """Resolve a device and forward its arguments to its own CLI."""
  parser = argparse.ArgumentParser(prog="plr", description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  dev = commands.add_parser("dev", help="Device development and service tools")
  if not DEVICE_CLIS:
    dev.description = "No device CLIs are registered in this installation."
    parser.parse_args(argv)
    dev.print_help()
    return
  devices = dev.add_subparsers(dest="device", required=True)
  for name, device in sorted(DEVICE_CLIS.items()):
    devices.add_parser(name, help=device.description, add_help=False)
  args, device_args = parser.parse_known_args(argv)
  DEVICE_CLIS[args.device].load().main(device_args)


if __name__ == "__main__":
  main()
