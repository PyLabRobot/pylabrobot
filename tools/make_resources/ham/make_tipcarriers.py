""" Create Python representations of various VENUS Method Editor resources. """
# pylint: skip-file

import os

from pylabrobot.resources import Coordinate
from pylabrobot.utils.file_parsing import find_float, find_string
from tools.make_resources.maker import make


BASE_DIR = "./LabWare/ML_STAR"
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
    site_width = find_float(f"Site.{i}.Dx", c)
    site_height = find_float(f"Site.{i}.Dy", c)
    sites.append(Coordinate(x, y, z))
  sites = sorted(sites, key=lambda c: c.y)

  size_x = find_float('Dim.Dx', c)
  size_y = find_float('Dim.Dy', c)
  size_z = find_float('Dim.Dz', c)
  description = find_string("Description", c)
  cname = os.path.basename(fn).split('.')[0]

  o.write(f'\n\n')
  o.write(f"def {cname}(name: str) -> {BASE_CLASS}:\n")
  o.write(f'  """ {description} """\n')
  o.write(f'  return {BASE_CLASS}(\n')
  o.write(f'    name=name,\n')
  o.write(f'    size_x={size_x},\n')
  o.write(f'    size_y={size_y},\n')
  o.write(f'    size_z={size_z},\n')
  o.write(f'    sites=create_homogenous_carrier_sites([\n')
  for i, site in enumerate(sites):
    o.write(f'        {repr(site)}' + ('' if i == len(sites) - 1 else ',') + '\n')
  o.write(f'      ],\n')
  o.write(f'      site_size_x={site_width},\n')
  o.write(f'      site_size_y={site_height},\n')
  o.write(f'    ),\n')
  o.write(f'    model="{cname}"\n')
  o.write(f'  )\n')


if __name__ == "__main__":
  make(
    base_dir=BASE_DIR,
    out_file=OUT_FILE,
    pattern=r'TIP_CAR_[\S_]+.tml',
    make_from_file=make_from_file
  )
