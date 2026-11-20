"""VSpin gRPC service and inspection tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from pathlib import Path
from typing import Optional, Sequence

from google.protobuf.json_format import MessageToDict

from pylabrobot.agilent.vspin import VSpin
from pylabrobot.agilent.vspin.grpc.client import VSpinClient
from pylabrobot.agilent.vspin.grpc.server import VSpinServer
from pylabrobot.agilent.vspin.grpc.session import VSpinSession


async def serve(args: argparse.Namespace) -> None:
  """Run the service without constructing or initializing a driver at startup."""
  session = VSpinSession(
    args.data_dir,
    args.device_id,
    lambda: VSpin(name=args.device_id, device_id=args.ftdi_device_id),
    args.hardware_lock,
    connection_id=args.ftdi_device_id or "serial-auto",
    max_jobs=args.max_jobs,
  )
  server = VSpinServer(session, host=args.host, port=args.port)
  stopped = asyncio.Event()
  for signum in (signal.SIGINT, signal.SIGTERM):
    asyncio.get_running_loop().add_signal_handler(signum, stopped.set)
  await server.start()
  try:
    print(
      json.dumps(
        {
          "endpoint": server.endpoint,
          "device_id": args.device_id,
          "device_type": "VSpin",
          "state": "Uninitialized",
          "configuration_id": session.configuration_id,
        }
      ),
      flush=True,
    )
    await stopped.wait()
  finally:
    await server.stop()


async def inspect(args: argparse.Namespace) -> dict:
  """Read identity, status, and configuration from a running service."""
  async with VSpinClient(
    args.endpoint,
    device_id=args.device_id,
  ) as client:
    return {
      "device": MessageToDict(await client.get_device_info(), preserving_proto_field_name=True),
      "status": MessageToDict(await client.get_status(), preserving_proto_field_name=True),
      "configuration": MessageToDict(
        await client.get_configuration(), preserving_proto_field_name=True
      ),
    }


def main(argv: Optional[Sequence[str]] = None) -> None:
  """Parse VSpin arguments and run the selected operation."""
  parser = argparse.ArgumentParser(prog="plr dev vspin", description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  server = commands.add_parser("serve", help="start the service without opening or moving hardware")
  server.add_argument(
    "--data-dir", type=Path, help="job directory (default: ~/.pylabrobot/vspin/<device-id>)"
  )
  server.add_argument("--ftdi-device-id", help="libftdi device ID for the centrifuge")
  server.add_argument(
    "--hardware-lock",
    type=Path,
    default=Path.home() / ".pylabrobot" / "vspin" / "hardware.lock",
    help="shared instrument lock (default: ~/.pylabrobot/vspin/hardware.lock)",
  )
  server.add_argument("--device-id", default="vspin-01")
  server.add_argument("--host", default="127.0.0.1", help="listen address (default: 127.0.0.1)")
  server.add_argument("--port", type=int, default=50053)
  server.add_argument("--max-jobs", type=int, default=1024, help="maximum retained job records")
  check = commands.add_parser(
    "inspect", help="read identity, status, and configuration without USB access"
  )
  check.add_argument(
    "--endpoint", default="127.0.0.1:50053", help="service address (default: 127.0.0.1:50053)"
  )
  check.add_argument("--device-id", default="vspin-01")
  args = parser.parse_args(argv)
  logging.basicConfig(level=logging.INFO)
  if args.command == "serve":
    if args.data_dir is None:
      args.data_dir = Path.home() / ".pylabrobot" / "vspin" / args.device_id
    asyncio.run(serve(args))
  else:
    print(json.dumps(asyncio.run(inspect(args)), indent=2))


if __name__ == "__main__":
  main()
