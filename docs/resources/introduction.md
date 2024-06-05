# Resources Introduction

This document introduces PyLabRobot Resources (labware and deck definitions) and general subclasses. You can find more information on creating custom resources in the {doc}`custom-resources` section.

In PyLabRobot, a {class}`pylabrobot.resources.Resource` is a piece of labware or equipment used in a protocol or program, a part of a labware item (such as a Well) or a container of labware items (such as a Deck). All resources inherit from a single base class {class}`pylabrobot.resources.Resource` that provides most of the functionality, such as the name, sizing, type, model, as well as methods for dealing with state. The name and sizing are required for all resources, with the name being a unique identifier for the resource and the sizing being the x, y and z-dimensions of the resource in millimeters when conceptualized as a cuboid.

While you can instantiate a `Resource` directly, several subclasses of methods exist to provide additional functionality and model specific resource attributes. For example, a {class}`pylabrobot.resources.plate.Plate` has methods for easily accessing {class}`pylabrobot.resources.Well`s.

The relation between resources is modelled by a tree, specifically an [_arborescence_](<https://en.wikipedia.org/wiki/Arborescence_(graph_theory)>) (a directed, rooted tree). The location of a resource in the tree is a Cartesian coordinate and always relative to the bottom front left corner of its immediate parent. The absolute location can be computed using {func}`pylabrobot.resources.Resource.get_absolute_location`. The x-axis is left (smaller) and right (larger); the y-axis is front (small) and back (larger); the z-axis is down (smaller) and up (higher). Each resource has `children` and `parent` attributes that allow you to navigate the tree.

{class}`pylabrobot.machine.Machine` is a special type of resource that represents a physical machine, such as a liquid handling robot ({class}`pylabrobot.liquid_handling.liquid_handler.LiquidHandler`) or a plate reader ({class}`pylabrobot.plate_reading.plate_reader.PlateReader`). Machines have a `backend` attribute linking to the backend that is responsible for converting PyLabRobot commands into commands that a specific machine can understand. Other than that, Machines, including {class}`pylabrobot.liquid_handling.liquid_handler.LiquidHandler`, are just like any other Resource.

## Defining a simple resource

The simplest way to define a resource is to subclass {class}`pylabrobot.resources.Resource` and define the `name` and `size_x`, `size_y` and `size_z` attributes. Here's an example of a simple resource:

```python
from pylabrobot.resources import Resource
resource = Resource(name="resource", size_x=10, size_y=10, size_z=10)
```

To assign a child resource, you can use the `assign_child_resource` method:

```python
from pylabrobot.resources import Resource, Coordinate
child = Resource(name="child", size_x=5, size_y=5, size_z=5)
# assign to bottom front left corner of parent
resource.assign_child_resource(child, Coordinate(x=0, y=0, z=0))
```

## Some common subclasses of `Resource`

### `Container`: Resources that contain liquid

Resources that contain liquid are subclasses of {class}`pylabrobot.resources.container.Container`. This class provides a {class}`pylabrobot.resources.volume_tracker.VolumeTracker` that helps {class}`pylabrobot.liquid_handling.liquid_handler.LiquidHandler` keep track of the liquid in the resource. (For more information on trackers, check out {doc}`/using-trackers`). Examples of subclasses of `Container` are {class}`pylabrobot.resources.Well` and {class}`pylabrobot.resources.trough.Trough`.

It is possible to instantiate a `Container` directly:

```python
from pylabrobot.resources import Container
container = Container(name="container", size_x=10, size_y=10, size_z=10)
# volume is computed by assuming the container is a cuboid, and can be adjusted with the max_volume
# parameter
```

### `ItemizedResource`: Resources that contain items in a grid

Resources that contain items in a grid are subclasses of {class}`pylabrobot.resources.itemized_resource.ItemizedResource`. This class provides convenient methods for accessing the child-resources, such as by integer or SBS "A1" style-notation, as well as for traversing items in an `ItemizedResource`. Examples of subclasses of `ItemizedResource`s are {class}`pylabrobot.resources.plate.Plate` and {class}`pylabrobot.resources.tip_rack.TipRack`.

To instantiate an `ItemizedResource`, it is convenient to use the `pylabrobot.resources.utils.create_equally_spaced_2d` method to quickly initialize a grid of child-resources in a grid. Here's an example of a simple `ItemizedResource`:

```python
from pylabrobot.resources import ItemizedResource
from pylabrobot.resources.utils import create_equally_spaced_2d
from pylabrobot.resources.well import Well, WellBottomType

plate = ItemizedResource(
  name="plate",
  size_x=127,
  size_y=86,
  size_z=10,
  items=create_equally_spaced_2d(
    Well,                            # the class of the items
    num_items_x=12,
    num_items_y=8,
    dx=12,                           # distance between the first well and the border in the x-axis
    dy=12,                           # distance between the first well and the border in the y-axis
    dz=0,                            # distance between the first well and the border in the z-axis
    item_dx=9,                       # distance between the wells in the x-axis
    item_dy=9,                       # distance between the wells in the y-axis

    bottom_type=WellBottomType.FLAT, # a custom keyword argument passed to the Well initializer
  )
)
```

## Saving and loading resources

PyLabRobot provide utilities to save and load resources and their states to and from files, as well as to serialize and deserialize resources and their states to and from Python dictionaries.

### Definitions

#### Saving to and loading from a file

Resource definitions, that includes deck definitions, can be saved to and loaded from a file using the `pylabrobot.resources.Resource.save` and `pylabrobot.resources.Resource.load` methods. The file format is JSON.

To save a resource to a file:

```python
resource.save("resource.json")
```

This will create a file `resource.json` with the resource definition.

```json
{
  "name": "resource",
  "type": "Resource",
  "size_x": 10,
  "size_y": 10,
  "size_z": 10,
  "location": null,
  "category": null,
  "model": null,
  "children": [],
  "parent_name": null
}
```

To load the resource from the file:

```python
resource = Resource.load_from_json_file("resource.json")
```

#### Serialization and deserialization

To simply serialize a resource to a Python dictionary:

```python
resource_dict = resource.serialize()
```

To load a resource from a Python dictionary:

```python
resource = Resource.deserialize(resource_dict)
```

### State

Each Resource is responsible for managing its own state, as deep down in the arborescence as possible (eg a Well instead of a Plate). The state of a resource is a Python dictionary that contains all the information necessary to restore the resource to a given state as far as PyLabRobot is concerned. This includes the liquids in a container, the presence of tips in a tip rack, and so on.

#### Serializing and deserializing state

The state of a single resource, that includes the volume of a container, can be serialized to and deserialized from a Python dictionary using the `pylabrobot.resources.Resource.serialize_state` and `pylabrobot.resources.Resource.deserialize_state` methods.

To serialize the state of a resource:

```python
from pylabrobot.resources import Container
c = Container(name="container", size_x=10, size_y=10, size_z=10)
c.serialize_state()
```

This will return a dictionary with the state of the resource:

```json
{ "liquids": [], "pending_liquids": [] }
```

To deserialize the state of a resource:

```python
c = Container(name="container", size_x=10, size_y=10, size_z=10)
c.load_state({ "liquids": [], "pending_liquids": [] })
```

This is convenient if you want to use PLR state in your own state management system, or save to a database.

Note that above, only the state of a single resource is serialized. If you want to serialize the state of a resource and all its children, you can use the {func}`pylabrobot.resources.Resource.serialize_all_state` and {func}`pylabrobot.resources.Resource.load_all_state` methods. These methods are used internally by the `save_state_to_file` and `load_state_from_file` methods.

#### Saving and loading state to and from a file

The state of a resource, that includes the volume of a container, can be saved to and loaded from a file using the `pylabrobot.resources.Resource.save_state_to_file` and `pylabrobot.resources.Resource.load_state_from_file` methods. The file format is JSON.

To save the state of a resource to a file:

```python
resource.save_state_to_file("resource_state.json")
```

By default, a Resource will not have a state:

```json
{}
```

If you had serialized a {class}`pylabrobot.resources.Container` with a volume of 1000 uL, the file would look like this:

```json
{ "liquids": [], "pending_liquids": [] }
```

To load the state of a resource from a file:

```python
resource.load_state_from_file("resource_state.json")
```
