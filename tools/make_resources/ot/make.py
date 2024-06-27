import os

from opentrons_shared_data.load import get_shared_data_root

from pylabrobot.resources import Plate, TipRack, TubeRack
from pylabrobot.resources.opentrons.load import (
  load_shared_opentrons_resource,
  UnknownResourceType
)


OT_PATH = get_shared_data_root()

PLATE_BASE_CLASS = "Plate"
PLATE_OUT_FILE = "plates.py"

TIP_RACK_BASE_CLASS = "TipRack"
TIP_RACK_OUT_FILE = "tip_racks.py"

TUBE_RACK_BASE_CLASS = "TubeRack"
TUBE_RACK_OUT_FILE = "tube_racks.py"


def main(po, to, tro):
  # pylint: disable=f-string-without-interpolation

  p = os.path.join(OT_PATH, "labware", "definitions", "2")
  p = os.path.expanduser(p)

  assert os.path.exists(p)

  for root, _, files in os.walk(p):
    for file in files:
      if file.endswith(".json"):
        version = int(file.split(".")[0])
        definition = root.split("/")[-1]

        try:
          # we don't really care about name
          resource = load_shared_opentrons_resource(definition, version=version, name=file)
        except UnknownResourceType:
          print(f"[SKIP] {definition} {version}")
          continue

        if isinstance(resource, Plate):
          po.write(f"def {definition.replace('.', 'point')}(name: str) -> Plate:\n")
          po.write(f"  return cast(Plate, load_shared_opentrons_resource(\n")
          po.write(f'    definition="{definition}",\n')
          po.write(f"    name=name,\n")
          po.write(f"    version={version}\n")
          po.write(f"  ))\n")
          po.write(f"\n\n")
        elif isinstance(resource, TipRack):
          to.write(f"def {definition.replace('.', 'point')}(name: str) -> TipRack:\n")
          to.write(f"  return cast(TipRack, load_shared_opentrons_resource(\n")
          to.write(f'    definition="{definition}",\n')
          to.write(f"    name=name,\n")
          to.write(f"    version={version}\n")
          to.write(f"  ))\n")
          to.write(f"\n\n")
        elif isinstance(resource, TubeRack):
          tro.write(f"def {definition.replace('.', 'point')}(name: str) -> TubeRack:\n")
          tro.write(f"  return cast(TubeRack, load_shared_opentrons_resource(\n")
          tro.write(f'    definition="{definition}",\n')
          tro.write(f"    name=name,\n")
          tro.write(f"    version={version}\n")
          tro.write(f"  ))\n")
          tro.write(f"\n\n")
        else:
          raise RuntimeError(f"Unknown resource type: {resource}")

        print(f"[DONE] {definition} {version}")


if __name__ == "__main__":
  with open(PLATE_OUT_FILE, "w", encoding="utf-8") as plate_file, \
       open(TIP_RACK_OUT_FILE, "w", encoding="utf-8") as tip_rack_file, \
        open(TUBE_RACK_OUT_FILE, "w", encoding="utf-8") as tube_rack_file:
    main(plate_file, tip_rack_file, tube_rack_file)
