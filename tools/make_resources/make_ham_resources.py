import argparse
import sys
import os

from pylabrobot.resources import MFXCarrier, Plate, PlateCarrier, TipCarrier, TipRack
from pylabrobot.resources.hamilton_parse import (
  get_resource_type,
  create_plate_for_writing,
  create_tip_rack_for_writing,
  create_plate_carrier_for_writing,
  create_tip_carrier_for_writing,
  create_flex_carrier_for_writing
)


FILE_EXTENSIONS = [
  ".rck",  # tip racks and plates
  ".tml",  # carriers (plate, tip rack, flex, sample, etc.)
]


def write_plate_definition(out_file, plate: Plate, description: str = None, eqn: str = None):
  """ Write a Python plate definition to the given file.

  Args:
    out_file: The file to write the definition to. Must be open for writing.
    plate: The plate to write the definition for.
    description: The description of the plate.
    eqn: The equation code for computing the volume from the height of the liquid in the well.
  """

  # pylint: disable=protected-access

  well_a1 = plate.get_item("A1")
  dy = round(plate._size_y - well_a1.location.y - well_a1._size_y, 4)

  method_name = None
  if eqn is not None:
    method_name = f"_compute_volume_from_height_{plate.model}"
    out_file.write(f"def {method_name}(h: float) -> float:\n")
    for line in eqn.split("\n"):
      out_file.write(f"  {line}\n")
    out_file.write("\n\n")

  out_file.write(f"def {plate.model}(name: str, with_lid: bool = False) -> Plate:\n")
  if description is not None:
    out_file.write(f"  \"\"\" {description} \"\"\"\n")
  out_file.write( "  return Plate(\n")
  out_file.write( "    name=name,\n")
  out_file.write(f"    size_x={plate._size_x},\n")
  out_file.write(f"    size_y={plate._size_y},\n")
  out_file.write(f"    size_z={plate._size_z},\n")
  out_file.write( "    with_lid=with_lid,\n")
  out_file.write(f"    model=\"{plate.model}\",\n")
  out_file.write( "    items=create_equally_spaced(Well,\n")
  out_file.write(f"      num_items_x={plate.num_items_x},\n")
  out_file.write(f"      num_items_y={plate.num_items_y},\n")
  out_file.write(f"      dx={well_a1.location.x},\n")
  out_file.write(f"      dy={dy},\n")
  out_file.write(f"      dz={well_a1.location.z},\n")
  out_file.write(f"      item_dx={well_a1._size_x},\n")
  out_file.write(f"      item_dy={well_a1._size_y},\n")
  out_file.write(f"      size_x={well_a1._size_x},\n")
  out_file.write(f"      size_y={well_a1._size_y},\n")
  out_file.write(f"      size_z={well_a1._size_z},\n")
  out_file.write(f"      bottom_type={well_a1.bottom_type},\n")
  if method_name is not None:
    out_file.write(f"      compute_volume_from_height={method_name},\n")
  out_file.write( "    ),\n")
  out_file.write( "  )\n")


def write_plate_landscape_variant(out_file, plate: Plate, description: str = None):
  """ Write the landscape variant of a Python plate definition to the given file. """

  out_file.write(f"def {plate.model}_L(name: str, with_lid: bool = False) -> Plate:\n")
  if description is not None:
    out_file.write(f"  \"\"\" {description} \"\"\"\n")
  out_file.write(f"  return {plate.model}(name=name, with_lid=with_lid)\n")


def write_plate_portrait_variant(out_file, plate: Plate, description: str = None):
  """ Write the portrait variant of a Python plate definition to the given file. """

  out_file.write(f"def {plate.model}_P(name: str, with_lid: bool = False) -> Plate:\n")
  if description is not None:
    out_file.write(f"  \"\"\" {description} \"\"\"\n")
  out_file.write(f"  return {plate.model}(name=name, with_lid=with_lid).rotated(90)\n")


def write_tip_rack_definition(out_file, tip_rack: TipRack, description: str = None):
  """ Write a Python tip rack definition to the given file.

  Args:
    out_file: The file to write the definition to. Must be open for writing.
    tip_rack: The tip rack to write the definition for.
    description: The description of the tip rack.
  """

  # pylint: disable=protected-access

  tip_spot_a1 = tip_rack.get_item("A1")
  dy = round(tip_rack._size_y - tip_spot_a1.location.y - tip_spot_a1._size_y, 4)

  out_file.write(f"def {tip_rack.model}(name: str, with_tips: bool = True) -> TipRack:\n")
  if description is not None:
    out_file.write(f"  \"\"\" {description} \"\"\"\n")
  out_file.write( "  return TipRack(\n")
  out_file.write( "    name=name,\n")
  out_file.write(f"    size_x={tip_rack._size_x},\n")
  out_file.write(f"    size_y={tip_rack._size_y},\n")
  out_file.write(f"    size_z={tip_rack._size_z},\n")
  out_file.write(f"    model=\"{tip_rack.model}\",\n")
  out_file.write( "    items=create_equally_spaced(TipSpot,\n")
  out_file.write(f"      num_items_x={tip_rack.num_items_x},\n")
  out_file.write(f"      num_items_y={tip_rack.num_items_y},\n")
  out_file.write(f"      dx={tip_spot_a1.location.x},\n")
  out_file.write(f"      dy={dy},\n")
  out_file.write(f"      dz={tip_spot_a1.location.z},\n")
  out_file.write(f"      item_dx={tip_spot_a1._size_x},\n")
  out_file.write(f"      item_dy={tip_spot_a1._size_y},\n")
  out_file.write(f"      size_x={tip_spot_a1._size_x},\n")
  out_file.write(f"      size_y={tip_spot_a1._size_y},\n")
  out_file.write(f"      make_tip={tip_spot_a1.make_tip.__name__},\n")
  out_file.write( "    ),\n")
  out_file.write( "    with_tips=with_tips\n")
  out_file.write( "  )\n")


def write_plate_carrier_definition(out_file, plate_carrier: PlateCarrier, description: str = None):
  """ Write a Python plate carrier definition to the given file.

  Args:
    out_file: The file to write the definition to. Must be open for writing.
    plate_carrier: The plate carrier to write the definition for.
    description: The description of the plate carrier.
  """

  # pylint: disable=protected-access

  out_file.write(f"def {plate_carrier.model}(name: str) -> PlateCarrier:\n")
  if description is not None:
    out_file.write(f"  \"\"\" {description} \"\"\"\n")
  out_file.write( "  return PlateCarrier(\n")
  out_file.write( "    name=name,\n")
  out_file.write(f"    size_x={plate_carrier._size_x},\n")
  out_file.write(f"    size_y={plate_carrier._size_y},\n")
  out_file.write(f"    size_z={plate_carrier._size_z},\n")
  out_file.write( "    sites=create_homogeneous_carrier_sites([\n")
  for site in plate_carrier.sites:
    out_file.write(f"        Coordinate({site.location.x}, {site.location.y}, {site.location.z})" +
                    ",\n")
  out_file.write( "      ],\n")
  out_file.write(f"      site_size_x={plate_carrier.sites[0]._size_x},\n")
  out_file.write(f"      site_size_y={plate_carrier.sites[0]._size_y},\n")
  out_file.write( "    ),\n")
  out_file.write(f"    model=\"{plate_carrier.model}\"\n")
  out_file.write( "  )\n")


def write_tip_carrier_definition(out_file, tip_carrier: TipCarrier, description: str = None):
  """ Write a Python tip carrier definition to the given file.

  Args:
    out_file: The file to write the definition to. Must be open for writing.
    tip_carrier: The tip carrier to write the definition for.
    description: The description of the tip carrier.
  """

  # pylint: disable=protected-access

  out_file.write(f"def {tip_carrier.model}(name: str) -> TipCarrier:\n")
  if description is not None:
    out_file.write(f"  \"\"\" {description} \"\"\"\n")
  out_file.write( "  return TipCarrier(\n")
  out_file.write( "    name=name,\n")
  out_file.write(f"    size_x={tip_carrier._size_x},\n")
  out_file.write(f"    size_y={tip_carrier._size_y},\n")
  out_file.write(f"    size_z={tip_carrier._size_z},\n")
  out_file.write( "    sites=create_homogeneous_carrier_sites([\n")
  for site in tip_carrier.sites:
    out_file.write(f"        Coordinate({site.location.x}, {site.location.y}, {site.location.z})" +
                    ",\n")
  out_file.write( "      ],\n")
  out_file.write(f"      site_size_x={tip_carrier.sites[0]._size_x},\n")
  out_file.write(f"      site_size_y={tip_carrier.sites[0]._size_y},\n")
  out_file.write( "    ),\n")
  out_file.write(f"    model=\"{tip_carrier.model}\"\n")
  out_file.write( "  )\n")


def write_flex_carrier_definition(out_file, flex_carrier: MFXCarrier, description: str = None):
  """ Write a Python flex carrier definition to the given file.

  Args:
    out_file: The file to write the definition to. Must be open for writing.
    flex_carrier: The flex carrier to write the definition for.
    description: The description of the flex carrier.
  """

  # pylint: disable=protected-access

  out_file.write(f"def {flex_carrier.model}(name: str) -> MFXCarrier:\n")
  if description is not None:
    out_file.write(f"  \"\"\" {description} \"\"\"\n")
  out_file.write( "  return MFXCarrier(\n")
  out_file.write( "    name=name,\n")
  out_file.write(f"    size_x={flex_carrier._size_x},\n")
  out_file.write(f"    size_y={flex_carrier._size_y},\n")
  out_file.write(f"    size_z={flex_carrier._size_z},\n")
  out_file.write( "    sites=create_homogeneous_carrier_sites([\n")
  for site in flex_carrier.sites:
    out_file.write(f"        Coordinate({site.location.x}, {site.location.y}, {site.location.z})" +
                    ",\n")
  out_file.write( "      ],\n")
  out_file.write(f"      site_size_x={flex_carrier.sites[0]._size_x},\n")
  out_file.write(f"      site_size_y={flex_carrier.sites[0]._size_y},\n")
  out_file.write( "    ),\n")
  out_file.write(f"    model=\"{flex_carrier.model}\"\n")
  out_file.write( "  )\n")


def main():
  parser = argparse.ArgumentParser(description="Create resources from TML files")

  # either base_dir or filepath must be provided
  group = parser.add_mutually_exclusive_group(required=True)
  group.add_argument("--filepath", help="The file path to the source file")
  group.add_argument("--base-dir", help="The base directory to search for source files")

  parser.add_argument("-o", "--out_file", help="The output file. If not provided, output will be "
                      "written to stdout")
  parser.add_argument("--type", help="The type of resource to create (Plate, PlateCarrier, "
                      "TipRack, TipCarrier, MFXCarrier). Resources found for other types will be "
                      "ignored. If not provided, resources of all types will be created",
                      choices=["Plate", "PlateCarrier", "TipRack", "TipCarrier", "MFXCarrier"])

  args = parser.parse_args()

  if args.filepath is not None:
    if os.path.isdir(args.filepath):
      raise ValueError("filepath must be a file, not a directory")
    extension = os.path.splitext(args.filepath)[1]
    if extension not in FILE_EXTENSIONS:
      raise ValueError(f"File extension {extension} not supported")
    filepaths = [args.filepath]
  else:
    if not os.path.isdir(args.base_dir):
      raise ValueError("base_dir must be a directory")

    filepaths = []
    for root, _, files in os.walk(args.base_dir):
      for file in files:
        extension = os.path.splitext(file)[1]
        if extension in FILE_EXTENSIONS:
          filepaths.append(os.path.join(root, file))

  if args.out_file is None:
    out = sys.stdout
  else:
    out = open(args.out_file, "w", encoding="utf-8") # pylint: disable=consider-using-with

  for filepath in filepaths:
    try:
      resource_type = get_resource_type(filepath)
      if args.type is not None and resource_type != args.type:
        continue

      if resource_type == "Plate":
        if filepath.endswith("_L.rck"):
          landscape = True
          filepath = filepath.replace("_L.rck", ".rck")
        elif filepath.endswith("_P.rck"):
          landscape = False
          filepath = filepath.replace("_P.rck", ".rck")
        else:
          landscape = None

        plate, description, eqn = create_plate_for_writing(filepath)
        write_plate_definition(out, plate=plate, description=description, eqn=eqn)

        if landscape:
          out.write("\n\n")
          write_plate_landscape_variant(out, plate=plate, description=description)
        elif landscape is False:
          out.write("\n\n")
          write_plate_portrait_variant(out, plate=plate, description=description)
      elif resource_type == "PlateCarrier":
        plate_carrier, description = create_plate_carrier_for_writing(filepath)
        write_plate_carrier_definition(out, plate_carrier=plate_carrier, description=description)
      elif resource_type == "TipRack":
        tip_rack, description = create_tip_rack_for_writing(filepath)
        write_tip_rack_definition(out, tip_rack=tip_rack, description=description)
      elif resource_type == "TipCarrier":
        tip_carrier, description = create_tip_carrier_for_writing(filepath)
        write_tip_carrier_definition(out, tip_carrier=tip_carrier, description=description)
      elif resource_type == "MFXCarrier":
        flex_carrier, description = create_flex_carrier_for_writing(filepath)
        write_flex_carrier_definition(out, flex_carrier=flex_carrier, description=description)
      else:
        raise ValueError(f"Unknown resource type {resource_type}")
    except Exception as e:  # pylint: disable=broad-except
      print(f"{filepath}: error: {e}")
    else:
      if args.out_file is not None:
        print(f"{filepath}: success")

  if args.out_file is not None:
    out.close()


if __name__ == "__main__":
  main()
