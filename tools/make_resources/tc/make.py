import re

from pylabrobot.resources import Coordinate


# path = "Carrier_Coley.cfg"
path = "Carrier.cfg"

RES = re.compile("(\d{2});(.*?);(\S*)")
SITE = re.compile("998;0;(\S{2,});")
DESC = re.compile("998;(Tecan part no\. .*);")
DESC2 = re.compile("998;(.* pn \d{8}.*);")


def main(pc, tc, p, tr):
  with open(path) as f:
    c = f.read().split("\n")

  for i in range(len(c)):
    m = RES.match(c[i])
    if m is None:
      continue

    name = m.group(2).replace("+", " ").replace("-", " ").replace(" ", "_")
    dim = [d.split("/") for d in m.group(3).split(";")]

    if m.group(1) == "13":
      off_x = float(dim[1][0]) / 10
      off_y = float(dim[1][1]) / 10
      size_x = float(dim[2][0]) / 10
      size_y = float(dim[2][1]) / 10
      size_z = float(dim[1][2]) / 10

      locations = []
      site_size_x = []
      site_size_y = []
      desc = ""
      while not RES.match(c[i + 1]):
        i += 1

        if s:=SITE.match(c[i]):
          site_dim = [d.split("/") for d in s.group(1).split(";")]
          w = float(site_dim[0][0]) / 10
          h = float(site_dim[0][1]) / 10
          x = float(site_dim[1][0]) / 10
          y = size_y - h - float(site_dim[1][1]) / 10
          z = float(site_dim[1][2]) / 10 + size_z
          locations = [Coordinate(x, y, z)] + locations
          site_size_x = [w] + site_size_x
          site_size_y = [h] + site_size_y

        if d:=DESC.match(c[i]):
          desc = d.group(1)

      if len(locations) != int(dim[3][0]):
        continue

      o = None
      if "MP" in name and "REMP" not in name:
        o = pc
        bc = "TecanPlateCarrier"
      elif "DiTi" in name:
        o = tc
        bc = "TecanTipCarrier"

      if o is not None:
        o.write(f'\n\n')
        o.write(f'def {name}(name: str) -> {bc}:\n')
        if desc:
          o.write(f'  """ {desc} """\n')
        o.write(f'  return {bc}(\n')
        o.write(f'    name=name,\n')
        o.write(f'    size_x={size_x},\n')
        o.write(f'    size_y={size_y},\n')
        o.write(f'    size_z={size_z},\n')
        o.write(f'    off_x={off_x},\n')
        o.write(f'    off_y={off_y},\n')
        if all(x == site_size_x[0] for x in site_size_x) and \
           all(y == site_size_y[0] for y in site_size_y):
          o.write(f'    sites=create_homogenous_carrier_sites(locations=[\n')
          for l in locations:
            o.write(f'        {repr(l)},\n')
          o.write(f'      ],\n')
          o.write(f'      site_size_x={site_size_x[0]},\n')
          o.write(f'      site_size_y={site_size_y[0]},\n')
          o.write(f'    ),\n')
        else:
          o.write(f'    sites=create_carrier_sites(locations = [\n')
          for l in locations:
            o.write(f'        {repr(l)},\n')
          o.write(f'      ], site_size_x=[\n')
          for x in site_size_x:
            o.write(f'        {x},\n')
          o.write(f'      ], site_size_y=[\n')
          for y in site_size_y:
            o.write(f'        {y},\n')
          o.write(f'    ]),\n')
        o.write(f'    model="{name}"\n')
        o.write(f'  )\n')

      # [Name];[?/Barcode?];[x off/y off/z off];[x size/y size];[sites];[?];
      # 0;[x size/y size];[x off/y off/z off];[group];
      # ...
      # [?];0;
      # ;;
      # [pos layout];
      # [description];
      # [link];
      # [sort];

    elif m.group(1) == "15":
      num_x = int(dim[1][0])
      num_y = int(dim[1][1])

      dx = float(dim[2][0]) / 10
      dy = float(dim[2][1]) / 10
      dz = float(dim[12][0]) / 10

      size_x = round(float(dim[2][2]) / 10 + dx, 2)
      size_y = round(float(dim[2][3]) / 10 + dy, 2)
      size_z = (float(dim[3][0]) - float(dim[3][2])) / 10

      if num_x <= 1 or num_y <= 1:
        continue
      res_size_x = round((size_x - 2 * dx) / (num_x - 1), 1)
      res_size_y = res_size_x

      dx = round(dx - res_size_x / 2, 2)
      dy = round(dy - res_size_y / 2, 2)

      z_travel = float(dim[3][3])
      z_start = float(dim[3][1])
      z_dispense = float(dim[3][2])
      z_max = float(dim[3][0])
      area = float(dim[4][0])

      desc = ""
      while not RES.match(c[i + 1]):
        i += 1
        if d:=DESC.match(c[i]):
          desc = d.group(1)
        elif d:=DESC2.match(c[i]):
          desc = d.group(1)

      o = None
      if "Well" in name:
        o = p
        bc = "TecanPlate"
        it = "Well"
        s = name.split("_")
        name = "_".join(s[2:]) + "_" + s[0] + "_" + s[1]
      elif "DiTi" in name:
        o = tr
        bc = "TecanTipRack"
        it = "TipSpot"

      if o is not None:
        o.write(f'\n\n')
        o.write(f'def {name}(name: str')
        if bc == 'TecanPlate':
          o.write(f', with_lid: bool = False')
        o.write(f') -> {bc}:\n')
        if desc:
          o.write(f'  """ {desc} """\n')
        o.write(f'  return {bc}(\n')
        o.write(f'    name=name,\n')
        o.write(f'    size_x={size_x},\n')
        o.write(f'    size_y={size_y},\n')
        o.write(f'    size_z={size_z},\n')
        if bc == 'TecanPlate':
          o.write(f'    with_lid=with_lid,\n')
          o.write(f'    lid_height=8,\n')
        o.write(f'    model="{name}",\n')
        o.write(f'    z_travel={z_travel},\n')
        o.write(f'    z_start={z_start},\n')
        o.write(f'    z_dispense={z_dispense},\n')
        o.write(f'    z_max={z_max},\n')
        o.write(f'    area={area},\n')
        o.write(f'    items=create_equally_spaced({it},\n')
        o.write(f'      num_items_x={num_x},\n')
        o.write(f'      num_items_y={num_y},\n')
        o.write(f'      dx={dx},\n')
        o.write(f'      dy={dy},\n')
        o.write(f'      dz={dz},\n')
        o.write(f'      item_size_x={res_size_x},\n')
        o.write(f'      item_size_y={res_size_y},\n')
        o.write(f'    ),\n')
        o.write(f'  )\n')

      # [Name];[?];[x num/y num/?];[x off/y off/x size/y size/z size?];[z-t,s,d,m];[area];[tip-touching distance];[tips per well]
      # ???
      # ...
      # ?
      # [type]
      # [vendor]
      # [description]
      # [link]
      # [sort]
      # [grip narrow],[grip wide];
      # [stacker plate type],[washer plate type];
      # [can have lide],[lid offset],[grip narrow],[grip wide];
      # [can have insert]
      # 15;96 Well Microplate;0;12/8/8;144/112/1134/742/112;2051/1957/1975/1900;33.2;1;1;0/10/10;100;256;-1;0;0;0;0;0;144;112;5;57;


if __name__ == "__main__":
  with open("plate_carriers.py", "w") as plate_carriers, \
       open("tip_carriers.py", "w") as tip_carriers, \
       open("plates.py", "w") as plates, \
       open("tip_racks.py", "w") as tip_racks:
    main(plate_carriers, tip_carriers, plates, tip_racks)
