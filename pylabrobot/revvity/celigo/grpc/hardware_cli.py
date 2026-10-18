"""Physical Celigo server. Startup and inspection never construct or initialize a driver."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from pathlib import Path

from google.protobuf.json_format import MessageToDict

from pylabrobot.revvity.celigo import Celigo, CeligoConfig
from pylabrobot.revvity.celigo.grpc.client import CeligoClient
from pylabrobot.revvity.celigo.grpc.hardware import CeligoCore, CeligoServer
from pylabrobot.rpc.security import Certificates


async def serve(args) -> None:
  config = CeligoConfig.from_install(str(args.config_root))
  core = CeligoCore(
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
  )
  server = CeligoServer(Certificates(args.certificates), core, port=args.port)
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
          "configuration_id": core.configuration_id,
        }
      ),
      flush=True,
    )
    await stopped.wait()
  finally:
    await server.stop()


async def inspect(args) -> dict:
  async with CeligoClient(
    args.endpoint,
    Certificates(args.certificates),
    device_id=args.device_id,
  ) as client:
    return {
      "device": MessageToDict(await client.device_info(), preserving_proto_field_name=True),
      "status": MessageToDict(await client.status(), preserving_proto_field_name=True),
      "configuration": MessageToDict(
        await client.configuration(), preserving_proto_field_name=True
      ),
    }


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  server = commands.add_parser(
    "serve", help="start without motion; physical operations require StartJob"
  )
  server.add_argument("--certificates", type=Path, required=True)
  server.add_argument("--data-dir", type=Path, required=True)
  server.add_argument("--config-root", type=Path, required=True)
  server.add_argument("--lucam-sdk", required=True)
  server.add_argument(
    "--usb-address",
    help="controller USB topology, e.g. 1-7; supports devices without a serial number",
  )
  server.add_argument("--hardware-lock", type=Path, required=True)
  server.add_argument("--device-id", default="celigo-01")
  server.add_argument("--port", type=int, default=50052)
  check = commands.add_parser(
    "inspect", help="read identity, status, and configuration without USB access"
  )
  check.add_argument("--endpoint", required=True)
  check.add_argument("--device-id", default="celigo-01")
  check.add_argument("--certificates", type=Path, required=True)
  args = parser.parse_args()
  logging.basicConfig(level=logging.INFO)
  if args.command == "serve":
    asyncio.run(serve(args))
  else:
    print(json.dumps(asyncio.run(inspect(args)), indent=2))


if __name__ == "__main__":
  main()
