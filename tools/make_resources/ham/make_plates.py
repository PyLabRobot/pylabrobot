""" Create Python representations of various VENUS Method Editor resources. """
# pylint: skip-file

import os

from pylabrobot.utils.file_parsing import find_int, find_float, find_string
import tools.make_resources.writer as writer
from tools.make_resources.maker import make


BASE_DIR = "./LabWare/Corning-Costar"
BASE_CLASS = "Plate"
OUT_FILE = "plates.py"


def make_from_file(fn, o):
  with open(fn, 'r', encoding='ISO-8859-1') as f:
    c = f.read()

  size_x = find_float('Dim.Dx', c)
  size_y = find_float('Dim.Dy', c)
  size_z = find_float('Dim.Dz', c)

  num_items_x = find_int('Columns', c)
  num_items_y = find_int('Rows', c)
  well_size_x = find_float('Dx', c)
  well_size_y = find_float('Dy', c)

  # rck files use the center of the well, but we want the bottom left corner.
  dx = round(find_float('BndryX', c) - well_size_x/2, 4)
  dy = round(find_float('BndryY', c) - well_size_y/2, 4)
  dz = 0

  cname = os.path.basename(fn).split('.')[0]
  description = cname
  EqnOfVol = None

  # .rck to .ctr filename
  def rck2ctr(fn):
    return fn.replace("_P.rck", ".ctr").replace("_L.rck", ".ctr").replace(".rck", ".ctr") \
      .replace("ProtCryst", "Post")

  with open(rck2ctr(fn), 'r', encoding='ISO-8859-1') as f2:
    c2 = f2.read()
    num_segments = find_int("Segments", c2)
    EqnCode = ""
    height_so_far = 0
    for i in range(num_segments, 0, -1):
      EqnOfVol = find_string(f"{i}.EqnOfVol", c2)
      section_max_height = find_float(f"{i}.Max", c2)
      if i == num_segments: # first section from bottom
        EqnOfVol = EqnOfVol.replace("h", f"min(h, {section_max_height})")
        EqnCode += f"volume = {EqnOfVol}\n"
      else:
        EqnOfVol = EqnOfVol.replace("h", f"(h-{height_so_far})")
        EqnCode += f"if h <= {section_max_height}:\n"
        EqnCode += f"  volume += {EqnOfVol}\n"
      height_so_far += section_max_height
    EqnCode += f"if h > {height_so_far}:\n"
    EqnCode +=  f"  raise ValueError(f\"Height {{h}} is too large for {cname}\")\n"
    EqnCode += "return volume"
    dz = find_float("BaseMM", c2)

    well_bottom_type_code = find_int(f"{num_segments}.Shape", c2)
    well_bottom_type = {
      0: "WellBottomType.FLAT",
      4: "WellBottomType.U",
    }.get(well_bottom_type_code, "WellBottomType.UNKNOWN")

    well_size_z = find_float("Depth", c2)

  if fn.endswith("_L.rck"): # landscape mode
    writer.write_landscape_plate(o=o, cname=cname, description=description, model=cname)

  elif fn.endswith("_P.rck"): # portrait mode
    writer.write_portrait_plate(o=o, cname=cname, description=description, model=cname)

  else: # definition
    writer.write_plate_with_create_equally_spaced(
      o=o,
      base_class=BASE_CLASS,
      name=cname,
      description=description,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      dx=dx,
      dy=dy,
      dz=dz,
      num_items_x=num_items_x,
      num_items_y=num_items_y,
      well_size_x=well_size_x,
      well_size_y=well_size_y,
      well_size_z=well_size_z,
      well_bottom_type=well_bottom_type,
      EqnCode=EqnCode,
      lid_height=10,
      model=cname
    )


if __name__ == "__main__":
  make(
    base_dir=BASE_DIR,
    out_file=OUT_FILE,
    pattern=r'[\S]+\.rck',
    make_from_file=make_from_file
  )
