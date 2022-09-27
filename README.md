<div style="text-align: center" align="center">
<img width="400" src=".github/img/logo.png" />
<h1>PyLabRobot</h1>
</div>

[**Docs**](https://docs.pylabrobot.org) | [**Forum**](https://forums.pylabrobot.org) | [**Installation**](https://docs.pylabrobot.org/installation.html) | [**Getting started**](https://docs.pylabrobot.org/basic.html)

## What is PyLabRobot?

PyLabRobot is a hardware agnostic, pure Python library for liquid handling robots.

PyLabRobot provides a layer of general-purpose abstractions over robot functions, with various device drivers for communicating with different kinds of robots. Right now we only have drivers for Hamilton and Opentrons robots, but we will soon have drivers for many more. The two Hamilton drivers are Venus, which is derived from the [PyHamilton library](https://github.com/dgretton/pyhamilton), and STAR, which is a low-level firmware interface. The Opentrons driver is based on [Opentrons Python API](https://github.com/rickwierenga/opentrons python-api). We also provide a simulator which plays the role of a device driver but renders commands in a browser-based deck visualization.

Here's a quick example showing how to move 100uL of liquid from well A1 to A2 using firmware on Hamilton STAR (this will work with any operating system!):

```python
from pylabrobot import LiquidHandler
from pylabrobot.liquid_handling.backends import STAR
from pylabrobot.liquid_handling.resources import STARLetDeck

lh = LiquidHandler(backend=STAR(), deck=STARLetDeck())
lh.setup()
lh.load_layout("hamilton-layout.json")

lh.pickup_tips(lh.get_resource("tips")["A1:H8"])
lh.aspirate(lh.get_resource("plate")["A1"], volume=100)
lh.dispense(lh.get_resource("plate")["A2"], volume=100)
lh.return_tips()
```

To run the same procedure on an Opentrons, switch out the following lines:

```diff
- from pylabrobot.liquid_handling.backends import STAR
- from pylabrobot.liquid_handling.resources import STARLetDeck
+ from pylabrobot.liquid_handling.backends import OpentronsBackend
+ from pylabrobot.liquid_handling.resource import OTDeck

- lh = LiquidHandler(backend=STAR(), deck=STARLetDeck())
+ lh = LiquidHandler(backend=OpentronsBackend(), deck=OTDeck())

- lh.load_layout("hamilton-layout.json")
+ lh.load_layout("opentrons-layout.json")
```

## Resources

### Documentation

[docs.pylabrobot.org](https://docs.pylabrobot.org)

- [Installation](https://docs.pylabrobot.org/installation.html)
- [Getting Started](https://docs.pylabrobot.org/basic.html)
- [Contributing](CONTRIBUTING.md)
- [API Reference](https://docs.pylabrobot.org/pylabrobot.html)

### Support

- [forums.pylabrobot.org](https://forums.pylabrobot.org) for questions and discussions.
- [GitHub Issues](https://github.com/pylabrobot/pylabrobot/issues) for bug reports and feature requests.

_Developed for the Sculpting Evolution Group at the MIT Media Lab_
