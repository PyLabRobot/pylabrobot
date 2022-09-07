""" Create Python representations of various VENUS Method Editor resources. """
# pylint: skip-file

import os
import sys
from maker import make

sys.path.insert(0, '..')

from pylabrobot.utils.file_parsing import find_int, find_float, find_string


BASE_DIR = "LabWare/ML_STAR"
BASE_CLASS = "Tips"
OUT_FILE = "tips.py"


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

  dx = find_float('BndryX', c) or 0
  dy = find_float('BndryY', c) or 0
  dz = find_float('Cntr.1.base', c) or 0

  tip_size_x = find_float('Dx', c)
  tip_size_y = find_float('Dy', c)
  num_items_x = find_int("Columns", c)
  num_items_y = find_int("Rows", c)

  cname = os.path.basename(fn).split('.')[0]
  if cname[0] == '4': cname = 'Four' + cname[1:]
  elif cname[0] == '5': cname = 'Five' + cname[1:]
  description = find_string("Description", c)

  o.write(f'\n\n')
  o.write(f'#: {description}\n')
  o.write(f"{cname} = partial({BASE_CLASS},\n")
  o.write(f'  size_x={size_x},\n')
  o.write(f'  size_y={size_y},\n')
  o.write(f'  size_z={size_z},\n')
  o.write(f'  tip_type={tip_type},\n')
  o.write(f'  dx={dx},\n')
  o.write(f'  dy={dy},\n')
  o.write(f'  dz={dz},\n')
  o.write(f'  tip_size_x={tip_size_x},\n')
  o.write(f'  tip_size_y={tip_size_y},\n')
  o.write(f'  num_items_x={num_items_x},\n')
  o.write(f'  num_items_y={num_items_y}\n')
  o.write(f')\n')


if __name__ == "__main__":
  make(
    base_dir=BASE_DIR,
    out_file=OUT_FILE,
    pattern=r'[\S]+TF?_L\.rck',
    make_from_file=make_from_file
  )
