from pylabrobot.resources.container_rack import ContainerRack
from pylabrobot.resources.resource_holder import ResourceHolder
from pylabrobot.resources.utils import create_ordered_items_2d


def alpaqua_12_tuberack_5mL_eppis(name: str) -> ContainerRack:
  """Alpaqua Engineering LLC cat. no.: A000080
  SLAS-compliant tube rack for use on liquid handlers to
  accommodate 5 mL Eppendorf Tubes with snap cap lids

  - URL: https://www.alpaqua.com/product/12-position-eppendorf-rack/
  """

  return ContainerRack(
    name=name,
    size_x=127.76,
    size_y=85.48,
    size_z=51.5,
    ordered_items=create_ordered_items_2d(
      klass=ResourceHolder,
      num_items_x=4,
      num_items_y=3,
      dx=2.4,
      dy=10.2,
      dz=4.8,
      item_dx=31.0,  # Column spacing
      item_dy=20.0,  # Row spacing
      size_x=16.7,  # Holder width
      size_y=16.7,  # Holder depth
      size_z=0.0,  # Holder height
    ),
  )
