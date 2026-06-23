Storage
=======

A storage machine is defined as a **machine whose primary feature is**

- `storage` of materials (e.g. wellplates, tipracks, tubes, ...) with *some form of automatable feature*.

Examples of this simplest form of a storage machine include:

- `Agilent BenchCel Microplate Handler <https://www.agilent.com/en/product/automated-liquid-handling/automated-microplate-management/benchcel-microplate-handler>`_ - open sequential stacker storage with robotic plate handling
- `Agilent Labware MiniHub <https://www.agilent.com/en/product/automated-liquid-handling/automated-microplate-management/labware-minihub>`_ - open storage of labware with rotation feature
- `Lab Services PlateCarousel <https://www.lab-services.nl/en/products/platebutler/platecarousel>`_ - open storage of labware with rotation feature


However, this purposefully broad definition means most storage machines also include other features such as:

- `automated material retrieval` (e.g. `Cytomat™ 2 Hotel Automated Storage <https://www.thermofisher.com/order/catalog/product/50078485>`_)
- `heating` (e.g. "incubators")
- `active cooling` (e.g. "fridges" and "freezers")
- `shaking` (e.g. "incubator shakers")
- `barcode scanning` (1D and/or 2D barcodes)

.. raw:: html

   <details style="background-color:#f8f9fa; border-left:5px solid #007bff;
                   padding:10px; border-radius:5px;">
     <summary style="font-weight: bold; cursor: pointer;">
       Note: The difference between a <code>storage machine</code> and a "plate hotel"
     </summary>
     <hr>
     <p>
        Across biowetlab automation there are many terms that do not have a standardised definition,
        leading to confusion and misunderstandings when automators communicate.
     </p>
     <p>
        The term <code>plate hotel</code> is one of them.
        We can differentiate between passive storage systems (e.g. shelves) and active
        storage systems (e.g. automated retrieval system or an opening tray.)
     </p>
     <p>
        In PLR, passive systems are not considered machines, they are just labware/Resources.
        For example, a PlateCarrier can be considered a passive storage system (whether it is
        vertical or horizontal).
     </p>
     <p>
        We use the term <code>storage machine</code> to refer to machines that store materials
        and have some form of automatable feature.
        This is a more precise definition that avoids the ambiguity of the term <code>plate hotel</code>.
     </p>
     <p>
        The only time the term <code>plate hotel</code> or <code>hotel</code> is used in PyLabRobot is when
        referring to the name of a specific machine, such as the
        <a href="https://www.thermofisher.com/order/catalog/product/50078485" target="_blank" rel="noopener">
          TFS Cytomat™ 2 Hotel Automated Storage</a>.
        In this case, it is used as a noun to refer to a specific product.
     </p>
   </details>

------------------------------------------

Material Access
-----------------

The way a storage machine allows access to materials determines how easily other machines
(e.g. robotic arms, grippers, pipettors) can interface with it. PyLabRobot distinguishes
storage machines using two axes:

Retrieval Pattern: Stacking (Sequential) vs. Random Access
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - **Stacking Access (Sequential)**
     - **Random Access**
   * - Materials stored in a fixed order (e.g. vertical stack, rotating carousel).
       Only the top/front-most item is accessible without mechanical movement.
     - Materials stored in individually addressable slots or shelves.
       Any item can be accessed directly.
   * - Slower access time for deeper items.
     - Faster access to any item.
   * - Simpler mechanics, smaller footprint.
     - More flexible but mechanically complex.
   * - **Examples:**

       - Agilent BenchCel 4R
       - Agilent Labware MiniHub
       - Lab Services PlateCarousel
     - **Examples:**

       - Thermo Cytomat 2 C450
       - LiCONiC STX Series

Accessibility: Open vs. Closed Storage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - **Open Storage**
     - **Closed Storage**
   * - Materials are exposed without obstruction.
       No barrier between the robot and the stored material.
     - Materials enclosed in a chamber.
       Access requires opening a door, drawer, or robotic port.
   * - Simplifies integration and visual inspection.
     - Enables environmental control (temperature, humidity, sterility).
   * - No protection from contamination or temperature drift.
     - Ideal for incubators, cold storage, and sterile handling.
   * - **Examples:**
       - Agilent BenchCel 4R
       - Agilent Labware MiniHub
       - Manual stackers
     - **Examples:**
       - Thermo Cytomat 2
       - LiCONiC STX incubators

Combined Retrieval & Access Summary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * -
     - **Open Storage**
     - **Closed Storage**
   * - **Stacking Access (Sequential)**
     - Agilent BenchCel 4R
       Agilent Labware MiniHub
       Lab Services PlateCarousel
     - STX incubators with drawer-based shelves
   * - **Random Access**
     - Rare in open format (e.g. manual racks)
     - Thermo Cytomat 2 C450
       LiCONiC STX Series


------------------------------------------

In PyLabRobot, these two retrieval patterns map to two capabilities:

* **Random access** -> the ``Incubator`` frontend, which holds addressable
  ``PlateHolder`` sites in ``PlateCarrier`` racks (any plate is directly
  reachable).
* **Stacking access (sequential)** -> the ``Stacker`` capability
  (``pylabrobot.storage.Stacker``), described below.

The ``Stacker`` capability
--------------------------------------------------

A ``Stacker`` models one or more single-ended LIFO stacks -- each a
``ResourceStack`` with ``direction="z"`` -- plus a single transfer position, the
*loading tray* (the same term incubators use). Only the **accessible** (top)
plate of a stack can be moved without first moving the plates above it, and
plates nest by their ``stacking_z_height`` so the stack height is computed
correctly.

Two primitives move plates between a stack and the loading tray:

* ``downstack(stack)`` -- move the accessible plate of ``stack`` onto the loading
  tray (and return it).
* ``upstack(stack, plate=None)`` -- move a plate from the loading tray onto
  ``stack`` (defaults to whatever is currently on the tray).

``Stacker`` is a *capability*, not a device-specific frontend: a machine that is
a stacker (e.g. the Agilent BenchCel or HighRes MicroServe) composes it and
provides a ``StackerBackend`` that implements the device-specific
``downstack``/``upstack`` transfers. ``StackerChatterboxBackend`` is a no-op
backend useful for trying the API out without hardware:

.. code-block:: python

   from pylabrobot.resources import Coordinate
   from pylabrobot.resources.resource_stack import ResourceStack
   from pylabrobot.storage import Stacker, StackerChatterboxBackend

   stacker = Stacker(
     backend=StackerChatterboxBackend(),
     name="stacker",
     size_x=200,
     size_y=200,
     size_z=300,
     stacks=[ResourceStack(f"stack_{i}", "z") for i in range(4)],
     loading_tray_location=Coordinate(0, 0, 0),
   )
   await stacker.setup()

   # Move the accessible plate of stack 0 onto the loading tray:
   plate = await stacker.downstack(0)
   # ... hand it to a robot arm / reader, then return a plate from the tray:
   await stacker.upstack(1)


------------------------------------------

.. toctree::
   :maxdepth: 1
   :hidden:

   agilent_benchcel
   cytomat
   inheco/incubator_shaker
   inheco/scila
   liconic
