def _write_tip_rack_header(o, base_class, name, description, with_params: bool, size_x=None, size_y=None, size_z=None, model=None):
  o.write(f'\n\n')

  o.write(f'#: {description}\n')
  o.write(f"def {name}(name: str, with_tips: bool = True) -> {base_class}:\n")
  if with_params:
    o.write(f'  return {base_class}(\n')
    o.write(f'    name=name,\n')
    o.write(f'    size_x={size_x},\n')
    o.write(f'    size_y={size_y},\n')
    o.write(f'    size_z={size_z},\n')
    o.write(f'    model="{model}",\n')

def write_tip_rack_with_create_equally_spaced(o, base_class, name, description, size_x, size_y, size_z, dx, dy, dz, num_items_x, num_items_y, tip_size_x, tip_size_y, tip_type, model):
  _write_tip_rack_header(o, base_class, name, description, True, size_x, size_y, size_z, model=model)

  o.write(f'    items=create_equally_spaced(TipSpot,\n')
  o.write(f'      num_items_x={num_items_x},\n')
  o.write(f'      num_items_y={num_items_y},\n')
  o.write(f'      dx={dx},\n')
  o.write(f'      dy={dy},\n')
  o.write(f'      dz={dz},\n')
  o.write(f'      item_size_x={tip_size_x},\n')
  o.write(f'      item_size_y={tip_size_y},\n')
  o.write(f'      make_tip={tip_type},\n')
  o.write(f'    ),\n')
  o.write(f'    with_tips=with_tips\n')
  o.write(f'  )\n')

def write_tip_rack_p(o, cname, description, model):
  _write_tip_rack_header(o, "TipRack", cname, description, with_params=False, model=model)
  base = cname[:-2] + "_L"
  o.write(f'  return {base}(name=name, with_tips=with_tips).rotated(90)\n')


def _write_plate_header(o, base_class, name, description, with_params: bool,
  size_x=None, size_y=None, size_z=None, one_dot_max=None, lid_height=None, EqnOfVol=None, model=None):
  o.write(f'\n\n')

  if EqnOfVol is not None:
    o.write(f"def _compute_volume_from_height_{name}(h: float):\n")
    o.write(f"  return {EqnOfVol}\n")
    o.write(f'\n')

  o.write(f'#: {description}\n')
  o.write(f"def {name}(name: str, with_lid: bool = False) -> {base_class}:\n")
  if with_params:
    o.write(f'  return {base_class}(\n')
    o.write(f'    name=name,\n')
    o.write(f'    size_x={size_x},\n')
    o.write(f'    size_y={size_y},\n')
    o.write(f'    size_z={size_z},\n')
    o.write(f'    one_dot_max={one_dot_max},\n')
    o.write(f'    with_lid=with_lid,\n')
    o.write(f'    model="{model}",\n')

    if lid_height is not None:
      o.write(f'    lid_height={lid_height},\n')
    if EqnOfVol is not None:
      o.write(f'    compute_volume_from_height=_compute_volume_from_height_{name},\n')


def write_plate_with_create_equally_spaced(o, base_class, name, description, size_x, size_y, size_z, dx, dy, dz, num_items_x, num_items_y, well_size_x, well_size_y, one_dot_max, lid_height=None, EqnOfVol=None, model=None):
  _write_plate_header(o, base_class, name, description, True, size_x, size_y, size_z, one_dot_max, lid_height, EqnOfVol, model=model)

  o.write(f'    items=create_equally_spaced(Well,\n')
  o.write(f'      num_items_x={num_items_x},\n')
  o.write(f'      num_items_y={num_items_y},\n')
  o.write(f'      dx={dx},\n')
  o.write(f'      dy={dy},\n')
  o.write(f'      dz={dz},\n')
  o.write(f'      item_size_x={well_size_x},\n')
  o.write(f'      item_size_y={well_size_y},\n')
  o.write(f'    ),\n')
  o.write(f'  )\n')


def write_landscape_plate(o, cname, description, model):
  _write_plate_header(o, "Plate", cname, description, with_params=False, model=model)
  base = cname[:-2]
  o.write(f'  return {base}(name=name, with_lid=with_lid)\n')


def write_portrait_plate(o, cname, description, model):
  _write_plate_header(o, "Plate", cname, description, with_params=False, model=model)
  base = cname[:-2]
  o.write(f'  return {base}(name=name, with_lid=with_lid).rotated(90)\n')
