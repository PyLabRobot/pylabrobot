PetriDishHolder
===============

``PetriDishHolder`` represents a holder for one ``PetriDish``. Its default outer dimensions
are 127.76 x 85.48 x 14.5 mm, the footprint of a 96-well plate. The holder and the dish
are separate resources: assign the dish to the holder, then assign the holder to a deck.

.. code-block:: python

   from pylabrobot.resources import Coordinate, Deck, PetriDish, PetriDishHolder

   deck = Deck(name="deck", size_x=1000, size_y=600, size_z=100)
   holder = PetriDishHolder(name="culture_dish_holder")
   dish = PetriDish(name="culture_dish", diameter=90, height=15)

   holder.assign_child_resource(dish, location=Coordinate.zero())
   deck.assign_child_resource(holder, location=Coordinate(100, 100, 0))

   assert holder.dish is dish

The ``location`` passed to ``assign_child_resource`` is the dish's position relative to
the holder. Set it to the position of the dish in your physical holder; the zero coordinate
above is only an example. ``holder.dish`` is ``None`` until a dish is assigned.

Only one ``PetriDish`` can be assigned to a ``PetriDishHolder``. Assigning another kind of
resource raises ``TypeError``; assigning a second dish raises ``ValueError``.

See :class:`~pylabrobot.resources.petri_dish.PetriDishHolder` for the API reference.
