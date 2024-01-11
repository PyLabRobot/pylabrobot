""" Create Python representations of various VENUS Method Editor resources. """
# pylint: skip-file

import os

from pylabrobot.utils.file_parsing import find_int, find_float, find_string
import tools.make_resources.writer as writer
from tools.make_resources.maker import make


BASE_DIR = "LabWare/ML_STAR"
BASE_CLASS = "TipRack"
OUT_FILE = "tip_racks.py"


tip_table = {
  "MlStar4mlTipWithFilter": "four_ml_tip_with_filter",
  "MlStar5mlTipWithFilter": "five_ml_tip_with_filter",
  "MlStar10ulLowVolumeTip": "low_volume_tip_no_filter",
  "MlStar10ulLowVolumeTipWithFilter": "low_volume_tip_with_filter",
  "MlStar1000ulHighVolumeTipWithFilter": "high_volume_tip_with_filter",
  "MlStar1000ulHighVolumeTip": "high_volume_tip_no_filter",
  "MlStar5mlTip": "five_ml_tip",
  "MlStar300ulStandardVolumeTipWithFilter": "standard_volume_tip_with_filter",
  "MlStar300ulStandardVolumeTip": "standard_volume_tip_no_filter",
}


def make_from_file(fn, o):
  with open(fn, 'r', encoding='ISO-8859-1') as f:
    c = f.read()

  size_x = find_float('Dim.Dx', c)
  size_y = find_float('Dim.Dy', c)
  size_z = find_float('Dim.Dz', c)
  tip_type = None
  try:
    tip_type = find_string("PropertyValue.6", c)
  except ValueError:
    tip_type = find_string("PropertyValue.4", c)
  tip_type = tip_table[tip_type]

  tip_size_x = find_float('Dx', c)
  tip_size_y = find_float('Dy', c)

  # rck files use the center of the well, but we want the bottom left corner.
  dx = round(find_float('BndryX', c) - tip_size_x/2, 4)
  dy = round(find_float('BndryY', c) - tip_size_y/2, 4)
  dz = find_float('Cntr.1.base', c)

  num_items_x = find_int("Columns", c)
  num_items_y = find_int("Rows", c)

  cname = os.path.basename(fn).split('.')[0]
  if cname[0] == '4': cname = 'Four' + cname[1:]
  elif cname[0] == '5': cname = 'Five' + cname[1:]
  description = find_string("Description", c)

  if cname.endswith("_P"):
    writer.write_tip_rack_p(o=o, cname=cname, description=description, model=cname)
  else:
    writer.write_tip_rack_with_create_equally_spaced(o=o, base_class=BASE_CLASS, name=cname, description=description, size_x=size_x, size_y=size_y, size_z=size_z, tip_type=tip_type, dx=dx, dy=dy, dz=dz, tip_size_x=tip_size_x, tip_size_y=tip_size_y, num_items_x=num_items_x, num_items_y=num_items_y, model=cname)


if __name__ == "__main__":
  make(
    base_dir=BASE_DIR,
    out_file=OUT_FILE,
    pattern=r'[\S]+TF?_[LP]\.rck',
    make_from_file=make_from_file
  )
