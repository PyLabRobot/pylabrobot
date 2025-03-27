Plates
======


Microplates are modelled by the :class:`~pylabrobot.resources.plate.Plate` class consisting of equally spaced wells. Wells are children of the :class:`~pylabrobot.resources.plate.Plate` and are modelled by the :class:`~pylabrobot.resources.well.Well` class. The relative positioning of a :class:`~pylabrobot.resources.well.Well` is what determines its location. :class:`~pylabrobot.resources.plate.Plate` is a subclass of :class:`~pylabrobot.resources.itemized_resource.ItemizedResource`, allowing convenient integer and string indexing.

There is some standardization on plate dimensions by SLAS, which you can read more about in the `ANSI SLAS 1-2004 (R2012): Footprint Dimensions doc <https://www.slas.org/SLAS/assets/File/public/standards/ANSI_SLAS_1-2004_FootprintDimensions.pdf>`_. Note that PLR fully supports all plate dimensions, sizes, relative well spacings, etc.

----

Special :class:`Plate` Tutorials
------------------------------------------

.. toctree::
   :maxdepth: 1

   definition-plate
   plate-quadrants

----

Lids
----

Plates can optionally have a lid, which will also be a child of the :class:`~pylabrobot.resources.plate.Plate` class. The lid is modelled by the ``Lid`` class.

----

Measuring ``nesting_z_height``
-------------------------------
The ``nesting_z_height`` is the overlap between the lid and the plate when the lid is placed on the plate. This property can be measured using a caliper.

.. image:: /resources/img/plate/lid_nesting_z_height.jpeg
   :alt: nesting_z_height measurement
