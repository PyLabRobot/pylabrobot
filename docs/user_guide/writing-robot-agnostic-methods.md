# Writing robot agnostic methods

This document describes best practices for writing methods that are agnostic to the robot backend.

> This is a work in progress. Please contribute!

## Keeping the layout separate from the protocol

It is recommended to keep the layout of the deck separate from the protocol. This allows you to easily change the layout of the deck without having to change the protocol.

```py
from pylabrobot.liquid_handling import LiquidHandler, STAR
from pylabrobot.resources import Deck, TipRack, Plate

# Write a method that creates a deck and defines its layout.
def make_deck() -> Deck:
  deck = Deck()

  deck.assign_child_resource()
  deck.assign_child_resource()

  return deck

# Instantiate the liquid handler using a deck and backend.
deck = make_deck()
backend = STAR()
lh = LiquidHandler(backend=backend, deck=deck)

# Get references to the resources you need. Use type hinting for autocompletion.
tip_rack: TipRack = lh.deck.get_resource('tip_rack')
plate: Plate = lh.deck.get_resource('plate')

# the protocol...
lh.pick_up_tip(tip_rack["A1"])
```

## Strictness checking

Strictness checking is a feature that allows you to specify how strictly you want the {class}`LiquidHandler <pylabrobot.liquid_handling.liquid_handler.LiquidHandler>` to enforce the protocol. The following levels are available:

- {attr}`STRICT <pylabrobot.liquid_handling.strictness.Strictness.IGNORE>`: The {class}`LiquidHandler <pylabrobot.liquid_handling.liquid_handler.LiquidHandler>` will raise an exception if you are doing something that is not legal on the robot.
- {attr}`WARN <pylabrobot.liquid_handling.strictness.Strictness.WARN>`: The default. The {class}`LiquidHandler <pylabrobot.liquid_handling.liquid_handler.LiquidHandler>` will warn you if you are doing something that is not recommended, but will not stop you from doing it.
- {attr}`IGNORE <pylabrobot.liquid_handling.strictness.Strictness.STRICT>`: The {class}`LiquidHandler <pylabrobot.liquid_handling.liquid_handler.LiquidHandler>` will silently log on the debug level if you are doing something that is not legal on the robot.

You can set the strictness level for the entire protocol using {func}`pylabrobot.liquid_handling.strictness.set_strictness`.

```py
from pylabrobot.liquid_handling import Strictness, set_strictness

set_strictness(Strictness.IGNORE)
lh.pick_up_tips(my_tip_rack["A1"], illegal_argument=True) # will log on debug level

set_strictness(Strictness.WARN)
lh.pick_up_tips(my_tip_rack["A1"], illegal_argument=True) # will warn

set_strictness(Strictness.STRICT)
lh.pick_up_tips(my_tip_rack["A1"], illegal_argument=True) # will raise a TypeError
```
