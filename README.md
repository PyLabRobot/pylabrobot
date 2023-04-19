<div style="text-align: center" align="center">
<img width="400" src=".github/img/logo.png" />
<h1>PyLabRobot</h1>
</div>

[**Docs**](https://docs.pylabrobot.org) | [**Forum**](https://forums.pylabrobot.org) | [**Installation**](https://docs.pylabrobot.org/installation.html) | [**Getting started**](https://docs.pylabrobot.org/basic.html)

## What is PyLabRobot?

PyLabRobot is a hardware agnostic, pure Python library for liquid handling robots and other lab automation equipment.

### Liquid handling robots

PyLabRobot provides a layer of general-purpose abstractions over robot functions, with various device drivers for communicating with different kinds of robots. Right now we only have drivers for Hamilton, Tecan and Opentrons liquid handling robots, but we will soon have drivers for many more. The two Hamilton drivers are Venus, which is derived from the [PyHamilton library](https://github.com/dgretton/pyhamilton), and STAR, which is a low-level firmware interface. The Tecan driver is EVO, also a low level, cross platform firmware interface. The Opentrons driver is based on the [Opentrons HTTP API](https://github.com/rickwierenga/opentrons-python-api). We also provide a simulator which plays the role of a device driver but renders commands in a browser-based deck visualization.

Here's a quick example showing how to move 100uL of liquid from well A1 to A2 using firmware on **Hamilton STAR** (this will work on any operating system!):

```python
from pylabrobot import LiquidHandler
from pylabrobot.liquid_handling.backends import STAR
from pylabrobot.resources import Deck

deck = Deck.load_from_json_file("hamilton-layout.json")
lh = LiquidHandler(backend=STAR(), deck=deck)
await lh.setup()

await lh.pick_up_tips(lh.get_resource("tip_rack")["A1"])
await lh.aspirate(lh.get_resource("plate")["A1"], vols=100)
await lh.dispense(lh.get_resource("plate")["A2"], vols=100)
await lh.return_tips()
```

To run the same procedure on an **Opentrons**, change the following lines:

```diff
- from pylabrobot.liquid_handling.backends import STAR
+ from pylabrobot.liquid_handling.backends import OpentronsBackend

- deck = Deck.load_from_json_file("hamilton-layout.json")
+ deck = Deck.load_from_json_file("opentrons-layout.json")

- lh = LiquidHandler(backend=STAR(), deck=deck)
+ lh = LiquidHandler(backend=OpentronsBackend(host="x.x.x.x"), deck=deck)
```

Or **Tecan** (also works on any operating system!):

```diff
- from pylabrobot.liquid_handling.backends import STAR
+ from pylabrobot.liquid_handling.backends import EVO

- deck = Deck.load_from_json_file("hamilton-layout.json")
+ deck = Deck.load_from_json_file("tecan-layout.json")

- lh = LiquidHandler(backend=STAR(), deck=deck)
+ lh = LiquidHandler(backend=EVO(), deck=deck)
```

### Plate readers

PyLabRobot also provides a layer of general-purpose abstractions for plate readers, currently with just a driver for the ClarioStar. This driver works on Windows, macOS and Linux. Here's a quick example showing how to read a plate using the ClarioStar:

```python
from pylabrobot.plate_reading import PlateReader, ClarioStar

pr = PlateReader(name="plate reader", backend=ClarioStar())
await pr.setup()

# Use in combination with a liquid handler
lh.assign_child_resource(pr, location=Coordinate(x, y, z))
lh.move_plate(lh.get_resource("plate"), pr)

data = await pr.read_luminescence()
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

---

**Disclaimer:** PyLabRobot is not officially endorsed or supported by any robot manufacturer. If you use a firmware driver such as the STAR driver provided here, you do so at your own risk. Usage of a firmware driver such as STAR may invalidate your warranty. Please contact us with any questions.

_Developed for the Sculpting Evolution Group at the MIT Media Lab_
