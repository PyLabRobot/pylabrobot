TubeRack
========

``TubeRack`` is a :class:`~pylabrobot.resources.container_rack.ContainerRack` whose
positions hold :class:`~pylabrobot.resources.tube.Tube` objects. Unlike the
base ``ContainerRack``, assigning another resource type raises ``ValueError``.
Each position is a ``ResourceHolder`` and can be empty. See the
:doc:`ContainerRack guide <../container-rack/container-rack>` for position
lookup, range assignment, and removal.

Create a rack
-------------

The dimensions below are illustrative and use millimeters; use measured
dimensions for physical labware.

.. code-block:: python

   from pylabrobot.resources import Tube, TubeRack, create_ordered_items_2d
   from pylabrobot.resources.resource_holder import ResourceHolder

   rack = TubeRack(
     name="sample_tube_rack",
     size_x=60,
     size_y=40,
     size_z=20,
     ordered_items=create_ordered_items_2d(
       ResourceHolder,
       num_items_x=2,
       num_items_y=2,
       dx=5,
       dy=5,
       dz=0,
       item_dx=25,
       item_dy=25,
       size_x=16,
       size_y=16,
       size_z=20,
       name_prefix="sample_tube_rack",
     ),
   )

   tube = Tube(name="sample_1", size_x=16, size_y=16, size_z=40, max_volume=5000)
   rack["A1"] = tube

   assert rack.get_tube("A1") is tube
   assert rack.get_tube("B1") is None
   assert rack.get_container("A1") is tube

``get_tube()`` returns ``None`` for an empty position. In contrast,
``get_container()`` raises ``ValueError`` for an empty position; check
``has_container()`` first when occupancy is uncertain. These assignments only
update the resource model; they do not move physical tubes.

See :class:`~pylabrobot.resources.tube_rack.TubeRack` for the API reference.
