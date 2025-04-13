ItemizedResource
================

Resources that contain items in a grid are subclasses of :class:`pylabrobot.resources.itemized_resource.ItemizedResource`. This class provides convenient methods for accessing the child-resources, such as by integer or SBS "A1" style-notation, as well as for traversing items in an ``ItemizedResource``. Examples of subclasses of ``ItemizedResource`` are :class:`pylabrobot.resources.plate.Plate` and :class:`pylabrobot.resources.tip_rack.TipRack`.

To instantiate an ``ItemizedResource``, it is convenient to use the ``pylabrobot.resources.utils.create_equally_spaced_2d`` method to quickly initialize a grid of child-resources in a grid. Here's an example of a simple ``ItemizedResource``:

.. code-block:: python

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
       dx=12,                           # distance between the left items and the left border in the x-axis
       dy=12,                           # distance between the bottom items and the front border in the y-axis
       dz=0,                            # distance between the items and the bottom border in the z-axis
       item_dx=9,                       # distance between the items in the x-axis
       item_dy=9,                       # distance between the items in the y-axis

       bottom_type=WellBottomType.FLAT, # a custom keyword argument passed to the Well initializer
     )
   )


------------------------------------------

.. toctree::
   :maxdepth: 1
   :hidden:

   plate/plate
   tiprack/tiprack
