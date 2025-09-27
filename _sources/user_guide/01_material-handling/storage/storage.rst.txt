Storage
=======

A storage machine is defined as a **machine whose primary feature is**

- `storage` of materials (e.g. wellplates, tipracks, tubes, ...) with *some form of automatable feature*.

Examples of this simplest form of a storage machine include:

- `Agilent Labware MiniHub <https://www.agilent.com/en/product/automated-liquid-handling/automated-microplate-management/labware-minihub>`_ – open storage of labware with rotation feature
- `Lab Services PlateCarousel <https://www.lab-services.nl/en/products/platebutler/platecarousel>`_ – open storage of labware with rotation feature


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
     - Agilent Labware MiniHub  
       Lab Services PlateCarousel
     - STX incubators with drawer-based shelves
   * - **Random Access**
     - Rare in open format (e.g. manual racks)
     - Thermo Cytomat 2 C450  
       LiCONiC STX Series


------------------------------------------

.. toctree::
   :maxdepth: 1
   :hidden:

   cytomat
