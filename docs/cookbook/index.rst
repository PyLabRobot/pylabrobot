Cookbook
========

The PyLabRobot **Cookbook** is a collection of modular, reusable code snippets designed to
illustrate solutions to common automation problems — *short, practical examples*
rather than full experimental protocols.

------------------------------------------

.. raw:: html

   <div id="tutorial-cards-container">

     <div class="row">

     <div class="plr-card-grid">

.. Add recipe cards below this line

.. plrcard::
   :header: Hamilton iSWAP Basics
   :card_description: Moving plates using the Hamilton iSWAP arm.
   :image: _static/cookbook_img/hi.png
   :image_hover: _static/cookbook_img/recipe_01_core_move_static.png
   :link: recipes/hamilton_iswap_movement.html
   :tags: Hamilton

.. plrcard::
   :header: Move plate to Alpaqua magnet using CORE grippers
   :card_description: Learn about...<br>
    • Resource movement using CORE grippers<br>
    • Resource position check using grippers<br>
    • PLR autocorrection of plate placement onto PlateAdapter/magnet
   :image: _static/cookbook_img/recipe_01_core_move_static.png
   :image_hover: _static/cookbook_img/recipe_01_core_move.gif
   :link: recipes/star_movement_plate_to_alpaqua_core.html
   :tags: ResourceMovement PlateAdapter STAR

.. plrcardgrid::

.. End of tutorial card section

.. -----------------------------------------
.. Page TOC
.. -----------------------------------------
.. toctree::
   :maxdepth: 2
   :hidden:

   recipes/star_movement_plate_to_alpaqua_core
   recipes/hamilton_iswap_movement
