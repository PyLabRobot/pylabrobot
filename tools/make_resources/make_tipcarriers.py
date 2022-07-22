""" Create Python representations of various VENUS Method Editor resources. """
# pylint: skip-file

import os
import sys
from maker import make

sys.path.insert(0, '..')

from pyhamilton.liquid_handling.resources.abstract import Coordinate, CarrierSite
from pyhamilton.utils.file_parsing import find_int, find_float, find_string


BASE_DIR = "../../LabWare/ML_STAR"
BASE_CLASS = "TipCarrier"
OUT_FILE = "tipcar.py"


def make_from_file(fn, o):
  with open(fn, 'r', encoding='ISO-8859-1') as f:
    c = f.read()

  site_count = int(c.split("Site.Cnt\x01")[1].split("\x08")[0])
  sites = []
  for i in range(1, site_count+1):
    x = find_float(f"Site.{i}.X", c)
    y = find_float(f"Site.{i}.Y", c)
    z = find_float(f"Site.{i}.Z", c)
    width = find_float(f"Site.{i}.Dx", c)
    height = find_float(f"Site.{i}.Dy", c)
    sites.append(CarrierSite(Coordinate(x, y, z), width, height))
  sites = sorted(sites, key=lambda c: c.location.y)

  size_x = find_float('Dim.Dx', c)
  size_y = find_float('Dim.Dy', c)
  size_z = find_float('Dim.Dz', c)
  description = find_string("Description", c)
  cname = os.path.basename(fn).split('.')[0]

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
  o.write(f'      sites=[\n')
  for i, site in enumerate(sites):
    o.write(f'        {site.__repr__()}' + ('' if i == len(sites) - 1 else ',') + '\n')
  o.write(f'      ]\n')
  o.write(f'    )\n')


if __name__ == "__main__":
  make(
    base_dir=BASE_DIR,
    out_file=OUT_FILE,
    pattern=r'TIP_CAR_[\S_]+.tml',
    make_from_file=make_from_file
  )
