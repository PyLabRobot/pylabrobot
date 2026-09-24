"""Add a Prep's enums and structs to a recorded firmware tree.

A recorded tree holds each object's methods, which says what can be sent. It does not say what the
values in those methods mean: a field typed as an enum on the wire is a bare number here, so a
question like "what else can `detect_mode` be?" can only be answered at the device. This reads the
enum and struct definitions each object declares, per interface, and merges them into the tree file
beside the methods, so the answer is in the recording.

Run it against the device the tree was recorded from:

    python tools/record_prep_firmware_types.py 192.168.100.102 \\
      pylabrobot/hamilton/prep/driver/recordings/prep_PRPAA1087_v1_2_2_firmware_tree.json

It reads only - no motion, no state changed - and rewrites the file in place, leaving every existing
key as it was and adding `enums` and `structs` to each object that declares any.
"""

import argparse
import asyncio
import json
import logging
from typing import Any, Dict, List

from pylabrobot.hamilton.prep import PrepDriver
from pylabrobot.hamilton.transport.tcp.introspection import EnumInfo, StructInfo
from pylabrobot.hamilton.transport.tcp.packets import Address
from pylabrobot.resources.hamilton import PrepDeck

logger = logging.getLogger(__name__)


def _enum_json(enum: EnumInfo) -> Dict[str, Any]:
  return {"enum_id": enum.enum_id, "name": enum.name, "values": dict(enum.values)}


def _struct_json(struct: StructInfo) -> Dict[str, Any]:
  return {
    "struct_id": struct.struct_id,
    "name": struct.name,
    "interface_id": struct.interface_id,
    "fields": {
      name: {"type_id": field.type_id, "source_id": field.source_id, "ref_id": field.ref_id}
      for name, field in struct.fields.items()
    },
  }


async def _types_of(driver: PrepDriver, node: Dict[str, Any]) -> None:
  """Read what one object declares, and every object under it, into the node."""
  address = Address(**node["address"])
  enums: List[Dict[str, Any]] = []
  structs: List[Dict[str, Any]] = []
  for interface in node["interfaces"]:
    interface_id = interface["interface_id"]
    try:
      enums += [
        _enum_json(e) for e in await driver.io.introspection.get_enums(address, interface_id)
      ]
      structs += [
        _struct_json(s) for s in await driver.io.introspection.get_structs(address, interface_id)
      ]
    except Exception as e:  # an object that declares none answers nothing, or refuses the query
      logger.debug("%s interface %d declares no types: %s", node["path"], interface_id, e)
  if enums:
    node["enums"] = enums
  if structs:
    node["structs"] = structs
  print(f"{node['path']}: {len(enums)} enums, {len(structs)} structs")
  for child in node["children"]:
    await _types_of(driver, child)


async def record(host: str, path: str, port: int = 2000) -> None:
  """Read the device's enums and structs into the tree recorded in `path`."""
  with open(path, encoding="utf-8") as f:
    recorded = json.load(f)

  driver = PrepDriver(deck=PrepDeck(), host=host, port=port)
  await driver._open()
  try:
    serial = await driver.request_device_serial_number()
    if serial != recorded.get("serial_number"):
      raise RuntimeError(
        f"{path} was recorded from {recorded.get('serial_number')}, this device is {serial}"
      )
    await _types_of(driver, recorded["tree"])
  finally:
    await driver._close()

  with open(path, "w", encoding="utf-8") as f:
    json.dump(recorded, f, indent=1)
    f.write("\n")
  print(f"written to {path}")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("host", help="the Prep to read, by address")
  parser.add_argument("tree", help="the recorded firmware tree to add the types to")
  parser.add_argument("--port", type=int, default=2000)
  args = parser.parse_args()
  logging.basicConfig(level=logging.INFO)
  asyncio.run(record(args.host, args.tree, args.port))


if __name__ == "__main__":
  main()
