# Contributing a new type of machine to PLR

PyLabRobot supports a number of different types of machines. Currently, these are:

- [Liquid handlers](/user_guide/00_liquid-handling/hamilton-star/basic)
- [Plate readers](/user_guide/02_analytical/plate-reading/plate-reading)
- [Pumps](/user_guide/00_liquid-handling/pumps/_pumps)
- [Temperature controllers](/user_guide/01_material-handling/temperature)
- [Heater shakers](/user_guide/01_material-handling/heating_shaking/heating_shaking)

If you want to add support for a new type of machine, this guide will explain the process. If you want to add a new machine for a type that already exists, you should read {doc}`this guide <new-concrete-backend>` instead.

This guide is not a definitive step-by-step guide (otherwise we would have automated it), but rather a collection of high-level ideas and suggestions. Often, it only becomes clear what the best abstractions are after two or more machines for a type have been implemented, so it is totally valid (and encouraged) to make some assumptions and then refactor later.

Two documents that you can read before you start are:

- [CONTRIBUTING.md](https://github.com/PyLabRobot/pylabrobot/blob/main/CONTRIBUTING.md): This document contains general information about contributing to PyLabRobot, and covers things like installation and testing.
- [How to Open Source](https://docs.pylabrobot.org/how-to-open-source.html): This document contains step-by-step instructions for contributing to an open source project. It is not specific to PyLabRobot, and serves as a reference.

Thank you for contributing to PyLabRobot!

## 0. Get in touch

Please make a post on [the PyLabRobot Development forum](https://discuss.pylabrobot.org) to let us know what you are working on. This will help you avoid duplicating work, and it is also a good place to get support.

## 1. Creating a new module

Each machine type has its own module in PLR. For example, the liquid handling module is located at `pylabrobot.liquid_handling`. This module contains:

- the machine front end: the user-facing API for the machine type. Example: `LiquidHandler`.
- the abstract base class for the machine type: the minimal set of atomic commands that the machine type is expected to support. Example: `LiquidHandlerBackend`.
- the concrete backends: the actual implementations of the abstract base class for specific machines. See {doc}`the concrete backends guide <new-concrete-backend>` for more information. Example: `STAR`.

## 2. Creating a new abstract backend class

Abstract backends are used to define the interface for a type of machine in terms of the minimal set of atomic commands. For example, all liquid handlers should have an `aspirate` method.

The commands should be interactive and minimal. Interactive means that the command is expected to be executed immediately when its method is called. Minimal means the commands cannot be broken into sub-commands that a user would reasonably want to use. For example, the abstract liquid handler backend contains commands for `aspirate` and `dispense`, but not `transfer` (a convenience method that exists on the frontend). At the same time, `aspirate` does move the pipetting head to a certain location because this move and the actual aspiration are reasonably expected to always occur together. For new machines, it is fine to make some assumptions and revisit them later.

The purpose of minimality is to make adding new concrete backends as easy as possible. The purpose of interactivity is to make the iteration cycle when developing new methods as short as possible.

You must put the abstract base class in `backend.py` in the module you created in step 1. The abstract class {class}`~pylabrobot.machine.MachineBackend` must be used as the base class for all backends. This class defines the `setup` and `stop` methods, which are used to initialize and stop the machine.

## 3. Creating a new front end class

Front ends are used to define the user-facing interface for a specific machine type, and shared across all machines of this type in PLR. They expose the atomic backend commands in addition to providing higher level utilities and orchestrating state. For example, `LiquidHandler` has a `transfer` method that is not defined in the abstract backend (it is not minimal), but instead simply calls `aspirate` and `dispense` on the backend. This way, the `transfer` implementation is shared across all supported liquid handling robots. `LiquidHandler` also maintains a reference to the deck, to make sure the requested operations are valid given the current state of the deck.

The abstract class {class}`~pylabrobot.machine.MachineFrontend` must be used as the base class for all front ends. This class defines the `setup` and `stop` methods, which are used to initialize and stop the machine. It also defines the `backend` attribute, which is used to access the backend.

You should put the front end in a file called `<machine_type>.py` in the module you created in step 1. For example, the liquid handling front end is located at `pylabrobot.liquid_handling.liquid_handler.py`.

If your devices updates the resource tree or its state, the front end should handle this. See [the resources guide](/resources/introduction.md) for more information.

## 4. Creating a new concrete backend for a specific machine

Refer to the {doc}`the concrete backends guide <new-concrete-backend>`.

## 5. Adding documentation (strongly recommended)

Each module should have a corresponding documentation page in the `docs` directory. Experience has shown that this is the best way to get people to actually use the new module.

### API documentation

API documentation is generated automatically based on docstrings, but has to be linked to from the main API documentation.

1. Create a new file in the `docs` directory called `pylabrobot.<module_name>.rst`. You can look at the existing files for examples.
2. Add a link to this file to the API documentation in [`docs/pylabrobot.rst`](https://github.com/PyLabRobot/pylabrobot/blob/main/docs/pylabrobot.rst).

### Brief introduction

It is also recommended to add a brief introduction to the module which explains what it is and how to use it. You can write this introduction in Markdown, reStructuredText, or a Jupyter notebook (recommended). You can look at [`basic.ipynb`](https://github.com/PyLabRobot/pylabrobot/blob/main/docs/basic.ipynb) for an example.

1. Put the introduction in the `docs` folder.
2. Link to the new file from [`docs/index.rst`](https://github.com/PyLabRobot/pylabrobot/blob/main/docs/index.rst).
