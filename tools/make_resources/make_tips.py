""" Create Python representations of various VENUS Method Editor resources. """
# pylint: skip-file

import os
import sys
from maker import make

sys.path.insert(0, '..')

from pyhamilton.liquid_handling.resources.abstract import Coordinate
from pyhamilton.utils.file_parsing import find_int, find_float, find_string


BASE_DIR = "../../LabWare/ML_STAR"
BASE_CLASS = "Tips"
OUT_FILE = "tips.py"


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

  dx = find_float('BndryX', c) or 0
  dy = find_float('BndryY', c) or 0
  dz = find_float('Cntr.1.base', c) or 0

  cname = os.path.basename(fn).split('.')[0]
  if cname[0] == '4': cname = 'Four' + cname[1:]
  elif cname[0] == '5': cname = 'Five' + cname[1:]
  description = find_string("Description", c)
  EqnOfVol = None

  o.write(f'\n\n')
  o.write(f"class {cname}({BASE_CLASS}):\n")
  o.write(f'  """ {description} """\n')
  o.write('\n')
  o.write(f'  def __init__(self, name: str):\n')
  o.write(f'    super().__init__(\n')
  o.write(f'      name=name,\n')
  o.write(f'      size_x={size_x},\n')
  o.write(f'      size_y={size_y},\n')
  o.write(f'      size_z={size_z},\n')
  o.write(f'      dx={dx},\n')
  o.write(f'      dy={dy},\n')
  o.write(f'      dz={dz}\n')
  o.write(f'    )\n')
  o.write(f'\n')
  o.write(f'  def compute_volume_from_height(self, h):\n')
  o.write(f'    return {EqnOfVol}\n')


if __name__ == "__main__":
  make(
    base_dir=BASE_DIR,
    out_file=OUT_FILE,
    pattern=r'[\S]+TF?_L\.rck',
    make_from_file=make_from_file
  )
