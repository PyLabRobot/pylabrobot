"""Celigo gRPC service and inspection tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from pathlib import Path
from typing import Optional, Sequence

from google.protobuf.json_format import MessageToDict

from pylabrobot.revvity.celigo import Celigo, CeligoConfig
from pylabrobot.revvity.celigo.grpc.client import CeligoClient
from pylabrobot.revvity.celigo.grpc.job_store import DEFAULT_QUOTA
from pylabrobot.revvity.celigo.grpc.server import CeligoServer
from pylabrobot.revvity.celigo.grpc.session import CeligoSession


async def serve(args: argparse.Namespace) -> None:
  """Run the service without constructing or initializing a driver at startup."""
  config = CeligoConfig.from_install(str(args.config_root))
  session = CeligoSession(
    args.data_dir,
    args.device_id,
    config,
    lambda: Celigo(
      config=config,
      lucam_sdk=args.lucam_sdk,
      usb_address=args.usb_address,
      allow_laser=False,
    ),
    args.hardware_lock,
    connection_id=args.usb_address or "serial-auto",
    image_storage_bytes=args.image_storage_bytes,
  )
  server = CeligoServer(session, host=args.host, port=args.port)
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
          "device_type": "Celigo",
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
  async with CeligoClient(
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
  """Parse Celigo arguments and run the selected operation."""
  parser = argparse.ArgumentParser(prog="plr dev celigo", description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  server = commands.add_parser("serve", help="start the service without opening or moving hardware")
  server.add_argument(
    "--data-dir", type=Path, help="job/image directory (default: ~/.pylabrobot/celigo/<device-id>)"
  )
  server.add_argument(
    "--config-root",
    type=Path,
    required=True,
    help="instrument installation or configuration directory",
  )
  server.add_argument(
    "--lucam-sdk", help="SDK library path (default: LUCAM_SDK_LIBRARY or system discovery)"
  )
  server.add_argument(
    "--usb-address",
    help="controller USB topology, e.g. 1-7; supports devices without a serial number",
  )
  server.add_argument(
    "--hardware-lock",
    type=Path,
    default=Path.home() / ".pylabrobot" / "celigo" / "hardware.lock",
    help="shared instrument lock (default: ~/.pylabrobot/celigo/hardware.lock)",
  )
  server.add_argument("--device-id", default="celigo-01")
  server.add_argument("--host", default="127.0.0.1", help="listen address (default: 127.0.0.1)")
  server.add_argument("--port", type=int, default=50052)
  server.add_argument(
    "--image-storage-bytes",
    type=int,
    default=DEFAULT_QUOTA,
    help="maximum retained and reserved image bytes across jobs",
  )
  check = commands.add_parser(
    "inspect", help="read identity, status, and configuration without USB access"
  )
  check.add_argument(
    "--endpoint", default="127.0.0.1:50052", help="service address (default: 127.0.0.1:50052)"
  )
  check.add_argument("--device-id", default="celigo-01")
  args = parser.parse_args(argv)
  logging.basicConfig(level=logging.INFO)
  if args.command == "serve":
    if args.data_dir is None:
      args.data_dir = Path.home() / ".pylabrobot" / "celigo" / args.device_id
    asyncio.run(serve(args))
  else:
    print(json.dumps(asyncio.run(inspect(args)), indent=2))


if __name__ == "__main__":
  main()
