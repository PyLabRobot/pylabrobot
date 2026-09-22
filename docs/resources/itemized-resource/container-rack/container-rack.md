# ContainerRack

`ContainerRack` models a movable rack with positions that can each hold one resource, such as a tube.
Each position is a [ResourceHolder](../../resource-holder/resource-holder.ipynb), which stays in the
rack when its contents are removed. A rack can be partly filled or completely empty.

The resource hierarchy is:

```text
ContainerRack
└── ResourceHolder (one per position)
    └── Resource (present when the position is occupied)
```

`ContainerRack` inherits the position lookup methods of
[ItemizedResource](../itemized-resource.rst) and adds methods for working with the contents of those
positions. Its dimensions and layout are configurable; it does not require a particular footprint.

## Create a rack

This example creates an empty rack with two rows and three columns. The dimensions are illustrative,
in millimeters. Use measured dimensions when defining your own labware.

```python
from pylabrobot.resources import ContainerRack, Tube, create_ordered_items_2d
from pylabrobot.resources.resource_holder import ResourceHolder

rack = ContainerRack(
  name="sample_rack",
  size_x=80,
  size_y=60,
  size_z=20,
  ordered_items=create_ordered_items_2d(
    ResourceHolder,
    num_items_x=3,
    num_items_y=2,
    dx=5,
    dy=5,
    dz=0,
    item_dx=25,
    item_dy=25,
    size_x=16,
    size_y=16,
    size_z=20,
    name_prefix="sample_rack",
  ),
)
```

`ordered_items` must contain at least one holder. The helper creates holders with positions relative
to the rack and assigns identifiers `A1`, `B1`, `A2`, `B2`, `A3`, `B3`, in that order. Row A is at the
back of the rack (larger y); column 1 is at the left (smaller x).

`dx`, `dy`, and `dz` locate the leftmost, frontmost holder's origin relative to the rack.
`item_dx` and `item_dy` give the spacing between holder origins. Each holder also has a
`child_location`, which defaults to `(0, 0, 0)` and determines where its contents sit relative to it.

## Fill positions

Assign a resource to a position with `rack[identifier] = resource`:

```python
tube = Tube(
  name="sample_1",
  size_x=16,
  size_y=16,
  size_z=40,
  max_volume=5000,
)
rack["A1"] = tube

assert rack.has_container("A1")
assert not rack.has_container("B1")
```

This assigns the tube as a child of the holder at A1. Assigning another resource to A1 replaces the
existing contents in the resource model. These operations describe the layout; they do not command
a robot to move anything.

To fill several positions, supply a list or tuple with one resource per position. The endpoints of
the string range `"A2:A3"` are included:

```python
tubes = [
  Tube(name=f"sample_{i}", size_x=16, size_y=16, size_z=40, max_volume=5000)
  for i in (2, 3)
]
rack["A2:A3"] = tubes
```

The base `ContainerRack` accepts any `Resource`. For a rack whose contents must be tubes, use
`TubeRack`, its specialization that checks the assigned resource type.

## Access holders and containers

Use `get_item()` to retrieve a holder and `get_container()` to retrieve its contents.
Bracket lookup always returns a list of holders, even for a single position:

```python
holder = rack.get_item("A1")
assert rack["A1"] == [holder]
assert holder.resource is tube
assert rack.get_container("A1") is tube

assert rack.get_containers("A2:A3") == tubes
assert rack.row_containers("A") == [tube, *tubes]
assert len(rack.get_occupied_containers()) == 3
```

| Method | Result |
| --- | --- |
| `has_container("A1")` | Whether a known position is occupied. |
| `get_container("A1")` | The resource at one position. |
| `get_containers("A2:A3")` | The resources at the selected positions. |
| `row_containers("A")` | The resources in a row; row indices can also be zero-based integers. |
| `column_containers(0)` | The resources in a column, using a zero-based index. |
| `get_all_containers()` | The resources at every position, in rack order. |
| `get_occupied_containers()` | The resources at occupied positions, skipping empty positions. |

The retrieval methods raise `ValueError` if any requested position is empty, except
`get_occupied_containers()`. For example, `get_all_containers()` would raise for the partly filled
rack above. Check `has_container()` before retrieving an individual position that may be empty.

For an irregular layout, you can supply your own dictionary of positioned `ResourceHolder` objects
with identifiers such as `"primary"` and `"backup"`. Single-position lookup and assignment work with
these names. Row, column, and string-range selection require Excel-style identifiers such as `A1`.

## Remove containers

Unassign a container to remove it from its holder. Use `empty()` to remove all containers while
keeping the rack and its holders:

```python
rack.get_container("A1").unassign()
assert not rack.has_container("A1")

rack.empty()
assert rack.get_occupied_containers() == []
assert rack.num_items == 6
```

`empty()` removes resources from the modeled rack; it does not empty liquid from the containers.
