# Contributing to PyLabRobot

Thank you for your interest in contributing to PyLabRobot! This document will help you get started.

## Getting Started

See the installation instructions [here](/user_guide/installation.md). For contributing, you should install PyLabRobot from source.

If this is your first time contributing to open source, check out [How to Open Source](/contributor_guide/how-to-open-source.md) for an easy introduction.

It's highly appreciated by the PyLabRobot developers if you communicate what you want to work on, to minimize any duplicate work. You can do this on [discuss.pylabrobot.org](https://discuss.pylabrobot.org).

## Development Tips

It is recommend that you use VSCode, as we provide a workspace config in `/.vscode/settings.json`, but you can use any editor you like, of course.

Some VSCode Extensions I'd recommend:

- [Python](https://marketplace.visualstudio.com/items?itemName=ms-python.python)
- [Code Spell Checker](https://marketplace.visualstudio.com/items?itemName=streetsidesoftware.code-spell-checker)
- [mypy](https://marketplace.visualstudio.com/items?itemName=matangover.mypy)

## Testing, linting, formatting

PyLabRobot uses `pytest` to run unit tests. Please make sure tests pass when you submit a PR. You can run tests as follows.

```bash
make test # run test on the latest version
```

`ruff` is used to lint and to enforce code style. The rc file is `/pyproject.toml`.

```bash
make lint
make format-check
```

Running the auto formatter:

```bash
make format
```

`mypy` is used to enforce type checking.

```bash
make typecheck
```

### Pre-commit hooks

PyLabRobot uses [pre-commit](https://pre-commit.com/) to run the above commands before every commit. To install pre-commit, run `pip install pre-commit` and then `pre-commit install`.

## Writing documentation

It is important that you write documentation for your code. As a rule of thumb, all functions and classes, whether public or private, are required to have a docstring. PyLabRobot uses [Google Style Python Docstrings](https://sphinxcontrib-napoleon.readthedocs.io/en/latest/example_google.html). In addition, PyLabRobot uses [type hints](https://docs.python.org/3/library/typing.html) to document the types of variables.

To build the documentation, run `make docs` in the root directory. The documentation will be built in `docs/_build/html`. Run `open docs/_build/html/index.html` to open the documentation in your browser.

## Common Tasks

### Fixing a bug

Bug fixes are an easy way to get started contributing.

Make sure you write a test that fails before your fix and passes after your fix. This ensures that this bug will never occur again. Tests are written in `<filename>_tests.py` files. See [Python's unittest module](https://docs.python.org/3/library/unittest.html) and existing tests for more information. In most cases, adding a few additional lines to an existing test should be sufficient.

### Adding resources

If you have defined a new resource, it is highly appreciated by the community if you add them to the repo. In most cases, a [partial function](https://docs.python.org/3/library/functools.html#functools.partial) is enough. There are many examples, like [tipracks.py](https://github.com/PyLabRobot/pylabrobot/blob/main/pylabrobot/liquid_handling/resources/hamilton/tipracks.py). If you are writing a new kind of resource, you should probably subclass resource in a new file.

Make sure to add your file to the imports in `__init__.py` of your resources package.

### Writing a new backend

Backends are the primary objects used to communicate with hardware. If you want to integrate a new piece of hardware into PyLabRobot, writing a new backend is the way to go. Here's, very generally, how you'd do it:

1. Copy the `pylabrobot/liquid_handling/backends/backend.py` file to a new file, and rename the class to `<BackendName>Backend`.
2. Remove all `abc` (abstract base class) imports and decorators from the class.
3. Implement the methods in the class.

## Support

If you have any questions, feel free to reach out using the [PyLabRobot forum](https://discuss.pylabrobot.org).



# Contributor guide

```{toctree}
:maxdepth: 2

contributing
how-to-open-source
```

```{toctree}
:maxdepth: 2

new-machine-type
new-concrete-backend
```



# Contributing a new type of machine to PLR

PyLabRobot supports a number of different types of machines. Currently, these are:

- [Liquid handlers](/user_guide/basic)
- [Plate readers](/user_guide/plate_reading)
- [Pumps](/user_guide/pumps)
- [Temperature controllers](/user_guide/temperature)
- [Heater shakers](/user_guide/heating-shaking)

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



# How to Open Source

This is a general guide on how to contribute to open source projects, like PyLabRobot, for people who are new to open source. If you are looking for a tutorial on how to contribute specifically to PyLabRobot, please see [CONTRIBUTING.md](https://github.com/PyLabRobot/pylabrobot/blob/main/CONTRIBUTING.md). You are invited to follow along with this guide and create your first contribution to PyLabRobot!

Before we start, I recommend you use a git gui, like [GitHub Desktop](https://desktop.github.com/), [Tower](https://www.git-tower.com), or the one built into [VSCode](https://code.visualstudio.com). **While command line commands are included in this guide, it is generally much easier to just use the gui.** This guide will use GitHub Desktop as an example, so if you have never contributed to open source before, start by installing [GitHub Desktop](https://desktop.github.com).

_If you just want to make a small changes, like fixing a typo, check out {ref}`this section <quick-changes>`._

## Prerequisites

- A GitHub account. If you don't have one, you can create one [here](https://github.com/signup)
- Git installed on your computer. If you don't have it, you can download it [here](https://git-scm.com/downloads).
- Recommended: a git gui.

## The 8 step workflow

This 8 step workflow is the general process for contributing to open source projects.

### 1. Forking a Project

Forking a project is just making a copy of the project on your own GitHub account. This is done by clicking the "Fork" button in the top right of [the project's GitHub page](https://github.com/pylabrobot/pylabrobot).

![Forking a project](img/how-to-os/fork-0.png)

Then click "Create fork".

![Forking a project](img/how-to-os/fork-1.png)

### 2. Cloning a Project

Cloning a project means downloading the project to your computer. This is done by clicking "Clone a Repository from the Internet..." in GitHub Desktop.

![Cloning a project](img/how-to-os/clone-0.png)

Then click "URL" and paste the URL of your forked project. Then click "Clone".

![Cloning a project](img/how-to-os/clone-1.png)

```bash
git clone https://github.com/<your-username>/pylabrobot.git
```

### 3. Creating a Branch

A branch is just a copy of the project that you can make changes to without affecting the main project. This is done by clicking the "Current branch" button in the top left of the GitHub Desktop window, and then clicking "New branch".

![Creating a branch](img/how-to-os/branch-0.png)
![Creating a branch](img/how-to-os/branch-1.png)

Then type in a name for your branch, and click "Create branch".

![Creating a branch](img/how-to-os/branch-2.png)

Branches are useful because you can have multiple branches, each with different changes. This is useful if you want to make multiple changes to a project, but you don't want to submit them all at once. You can submit each branch as a separate pull request.

```bash
git checkout -b <branch-name>
```

### 4. Making Changes

Now that you have a copy of the project on your computer, you can make changes to it. You can use any editor you like, but I recommend [VSCode](https://code.visualstudio.com/).

### 5. Committing Changes

Committing changes is just saving your changes to your local copy of the project. Select all files you changed and want to commit (this is a good time to go over your changes!). Write a short description in the bottom left of the GitHub Desktop window. Then click "Commit to \<branch-name\>".

![Committing changes](img/how-to-os/commit-0.png)

```bash
git add .
git commit -m "A short description"
```

Optionally, if your contribution consists of multiple parts, you can go back to step 4 and make some more changes. This can make it easier to track why changes are made. Keep in mind that PRs should be self contained and not too large. Aim for 1-3 commits and <300 lines of code per PR.

### 6. Pushing Changes

After you commit your changes, you need to push them to your forked copy of the project on GitHub. This is done by clicking the "Publish branch" button in the top left of the GitHub Desktop window. If you have pushed commits from this branch before, select "Push origin".

![Pushing changes](img/how-to-os/push-0.png)

```bash
git push origin <branch-name>
```

### 7. Creating a Pull Request

After you push your changes, you need to submit a pull request. This is done by going back to your browser and refreshing the page. You should see a button that says "Compare & pull request".

![Creating a pull request](img/how-to-os/pull-0.png)

Please write a short description of your changes and click "Create pull request".

![Creating a pull request](img/how-to-os/pull-1.png)

### 8. Code Review

After you submit a pull request, the project maintainers will review your code. They may ask you to make changes, or they may merge your pull request. Go to step 4 and repeat the process until the merge!

(quick-changes)=

## Quick changes

If your changes are small, you can do everything in the GitHub web interface. This is useful if you are fixing a typo in the code or documentation.

_Hint: If you are fixing a typo in the documentation, there is an edit button at the top right of every page. That button can be used instead of step 1 in this guide._

### 1. Clicking edit

Navigate to the file you want to edit, and click the edit button.

![Clicking edit](img/how-to-os/quick-0.png)

### 2. Making changes

Make your changes in the text editor.

![Editing a file](img/how-to-os/quick-1.png)

### 3. Committing changes

After you make your changes, you need to commit them.

Go to "Preview changes" to review your changes. Then write a good commit message and click "Commit changes".

![Committing changes](img/how-to-os/quick-2.png)

### 4. Creating a pull request

After you commit your changes, you need to submit a pull request. This is done by clicking "Create pull request".

![Creating a pull request](img/how-to-os/quick-3.png)

Optionally, you can add a description of your changes. Then click "Create pull request".

![Creating a pull request](img/how-to-os/quick-4.png)

## Support

If you have any questions, feel free to reach out using the [PyLabRobot forum](https://discuss.pylabrobot.org).



# Adding support for a new machine of an existing type

This guide explains how to add support for a new machine of an existing type. For example, if you want to add support for a new liquid handler, you should read this guide. If you want to add support for a new type of machine, you should read {doc}`this guide <new-machine-type>` first.

The machine types that are currently supported are:

- [Liquid handlers](/user_guide/basic)
- [Plate readers](/user_guide/plate_reading)
- [Pumps](/user_guide/pumps)
- [Temperature controllers](/user_guide/temperature)
- [Heater shakers](/user_guide/heating-shaking)

Two documents that you can read before you start are:

- [CONTRIBUTING.md](https://github.com/PyLabRobot/pylabrobot/blob/main/CONTRIBUTING.md): This document contains general information about contributing to PyLabRobot, and covers things like installation and testing.
- [How to Open Source](https://docs.pylabrobot.org/how-to-open-source.html): This document contains step-by-step instructions for contributing to an open source project. It is not specific to PyLabRobot, and serves as a reference.

Thank you for contributing to PyLabRobot!

## Background

Backends are minimal classes that are responsible for communicating with a machine and are thus specific to one machine. Frontends are higher level classes that are responsible for orchestrating higher-level state and providing nice interfaces to users, and should work with any machine. For example, the STAR liquid handler backend is responsible for executing the liquid handling operations on a Hamilton STAR, while the LiquidHandler frontend is responsible for making sure a requested operation is valid given the current state of the deck.

Backends should contain minimal state. We prefer to manage the state in the frontend, because this allows us to share the code across all machines of a type. For example, the liquid handler backend does not contain any information about the deck, because this is managed by the frontend. If a certain machine has a specific state that needs to be managed, like whether the gripper arm is parked on a liquid handling robot, that should be done by the backend because it is specific to the machine.

## 0. Get in touch

Please make a post on [the PyLabRobot Development forum](https://discuss.pylabrobot.org) to let us know what you are working on. This will help you avoid duplicating work, and it is also a good place to get support.

## 1. Creating a new concrete backend class

It is easiest to start by copying the abstract base class for the machine type to a new file. You will find this in `backend.py` in the module for the machine type. For example, the liquid handling abstract base class is located at `pylabrobot.liquid_handling.backends.backend`. You should copy this file to a new file called `<machine_name>.py` in the same directory. For example, the liquid handling backend for the Hamilton STAR is located at `pylabrobot.liquid_handling.backends.hamilton.STAR`.

## 2. Implementing the abstract methods

The abstract base class contains a number of abstract methods. These are the methods that are expected to be implemented by the concrete backend. You should implement these methods in the concrete backend. You can use the abstract base class as a reference for what the methods should do.

PyLabRobot aims to be OS-agnostic, meaning that it should work on Windows, Mac, and Linux. This maximizes flexibility for users and the reproducibility of experiments. However, this also means that you should not use any OS-specific libraries or dlls in the backend.

If an operation is not supported by the machine, you should raise a `NotImplementedError`.

The actual process of implementing the methods varies widely from machine to machine. It is generally useful to search for firmware documents, search for logs files generated by a manufacturer's software, or find other open source projects that have implemented the same machine.

## 3. Adding documentation (recommended)

Find the relevant module in the `docs` directory. For example, the liquid handling backends module is located at `docs/pylabrobot.liquid_handling.backends.rst`. Then, add the name of the new backend to make sure that the new backend is automatically documented in the API reference.

If you want, you can also add a new page to the `docs` directory that explains how to use the new backend. This is not required, but it is strongly recommended. Experience has shown that this is the best way to get people to actually use the new backend.



# Welcome to PyLabRobot's documentation!

PyLabRobot is a hardware agnostic, pure Python SDK for liquid handling robots and accessories.

- GitHub repository: [https://github.com/PyLabRobot/pylabrobot](https://github.com/PyLabRobot/pylabrobot)
- Community: [https://discuss.pylabrobot.org](https://discuss.pylabrobot.org)
- Paper: [https://www.cell.com/device/fulltext/S2666-9986(23)00170-9](<https://www.cell.com/device/fulltext/S2666-9986(23)00170-9>)

![Graphical abstract of PyLabRobot](/img/plr.jpg)

```{note}
PyLabRobot is different from [PyHamilton](https://github.com/dgretton/pyhamilton). While both packages are created by the same lab and both provide a Python interfaces to Hamilton robots, PyLabRobot aims to provide a universal interface to many different robots runnable on many different computers, where PyHamilton is a Windows only interface to Hamilton's VENUS.
```

## Used by

```{image} /img/used_by/mit.jpg
:alt: MIT
:class: company
:target: https://www.mit.edu/
```

```{image} /img/used_by/retrobio.webp
:alt: Retro
:class: company
:target: https://www.retro.bio/
```

```{image} /img/used_by/tt.png
:alt: T-Therapeutics
:class: company tt
:target: https://www.t-therapeutics.com/
```

```{image} /img/used_by/duke.png
:alt: Duke
:class: company
```

```{raw} html
<style>
.company {
  max-width: 200px;
  display: inline-block;
  margin: 0 1em;
}
.tt {
  max-width: 300px; /* T-Therapeutics logo is wider */
}
</style>
```

## Documentation

```{toctree}
:maxdepth: 2
:caption: User Guide

user_guide/index
```

```{toctree}
:maxdepth: 2
:caption: Development

contributor_guide/index
```

```{toctree}
:maxdepth: 2
:caption: Resource Library

resources/index
```

```{toctree}
:maxdepth: 2
:caption: API documentation

api/pylabrobot
```

```{toctree}
:hidden:

Community <https://discuss.pylabrobot.org/>
```

## Citing

If you use PyLabRobot in your research, please cite the following paper:

```bibtex
@article{WIERENGA2023100111,
  title = {PyLabRobot: An open-source, hardware-agnostic interface for liquid-handling robots and accessories},
  journal = {Device},
  volume = {1},
  number = {4},
  pages = {100111},
  year = {2023},
  issn = {2666-9986},
  doi = {https://doi.org/10.1016/j.device.2023.100111},
  url = {https://www.sciencedirect.com/science/article/pii/S2666998623001709},
  author = {Rick P. Wierenga and Stefan M. Golas and Wilson Ho and Connor W. Coley and Kevin M. Esvelt},
  keywords = {laboratory automation, open source, standardization, liquid-handling robots},
}
```

```
Wierenga, R., Golas, S., Ho, W., Coley, C., & Esvelt, K. (2023). PyLabRobot: An Open-Source, Hardware Agnostic Interface for Liquid-Handling Robots and Accessories. Device. https://doi.org/10.1016/j.device.2023.100111
```

[Cited by](https://scholar.google.com/scholar?cites=4498189371108132583):

- Tom, Gary, et al. "Self-driving laboratories for chemistry and materials science." Chemical Reviews (2024).
- Anhel, Ana-Mariya, Lorea Alejaldre, and Ángel Goñi-Moreno. "The Laboratory Automation Protocol (LAP) Format and Repository: a platform for enhancing workflow efficiency in synthetic biology." ACS synthetic biology 12.12 (2023): 3514-3520.
- Bultelle, Matthieu, Alexis Casas, and Richard Kitney. "Engineering biology and automation–Replicability as a design principle." Engineering Biology (2024).
- Pleiss, Jürgen. "FAIR Data and Software: Improving Efficiency and Quality of Biocatalytic Science." ACS Catalysis 14.4 (2024): 2709-2718.
- Gopal, Anjali, et al. "Will releasing the weights of large language models grant widespread access to pandemic agents?." arXiv preprint arXiv:2310.18233 (2023).
- Padhy, Shakti P., and Sergei V. Kalinin. "Domain hyper-languages bring robots together and enable the machine learning community." Device 1.4 (2023).
- Beaucage, Peter A., Duncan R. Sutherland, and Tyler B. Martin. "Automation and Machine Learning for Accelerated Polymer Characterization and Development: Past, Potential, and a Path Forward." Macromolecules (2024).
- Bultelle, Matthieu, Alexis Casas, and Richard Kitney. "Construction of a Calibration Curve for Lycopene on a Liquid-Handling Platform─ Wider Lessons for the Development of Automated Dilution Protocols." ACS Synthetic Biology (2024).
- Hysmith, Holland, et al. "The future of self-driving laboratories: from human in the loop interactive AI to gamification." Digital Discovery 3.4 (2024): 621-636.
- Casas, Alexis, Matthieu Bultelle, and Richard Kitney. "An engineering biology approach to automated workflow and biodesign." (2024).
- Jiang, Shuo, et al. "ProtoCode: Leveraging Large Language Models for Automated Generation of Machine-Readable Protocols from Scientific Publications." arXiv preprint arXiv:2312.06241 (2023).
- Jiang, Shuo, et al. "ProtoCode: Leveraging large language models (LLMs) for automated generation of machine-readable PCR protocols from scientific publications." SLAS technology 29.3 (2024): 100134.
- Thieme, Anton, et al. "Deep integration of low-cost liquid handling robots in an industrial pharmaceutical development environment." SLAS technology (2024): 100180.
- Daniel, Čech. Adaptace algoritmů pro navigaci robota na základě apriorních informací. BS thesis. České vysoké učení technické v Praze. Vypočetní a informační centrum., 2024.
- Tenna Alexiadis Møller, Thom Booth, Simon Shaw, Vilhelm Krarup Møller, Rasmus J.N. Frandsen, Tilmann Weber. ActinoMation: a literate programming approach for medium-throughput robotic conjugation of Streptomyces spp. bioRxiv 2024.12.05.622625; doi: https://doi.org/10.1101/2024.12.05.622625



# Plates

Microplates are modelled by the {class}`~pylabrobot.resources.plate.Plate` class consisting of equally spaced wells. Wells are children of the `Plate` and are modelled by the {class}`~pylabrobot.resources.well.Well` class. The relative positioning of `Well`s is what determines their location. `Plate` is a subclass of {class}`~pylabrobot.resources.itemized_resource.ItemizedResource`, allowing convenient integer and string indexing.

There is some standardization on plate dimensions by SLAS, which you can read more about in the [ANSI SLAS 1-2004 (R2012): Footprint Dimensions doc](https://www.slas.org/SLAS/assets/File/public/standards/ANSI_SLAS_1-2004_FootprintDimensions.pdf). Note that PLR fully supports all plate dimensions, sizes, relative well spacings, etc.

## Lids

Plates can optionally have a lid, which will also be a child of the `Plate` class. The lid is modelled by the `Lid` class.

### Measuring `nesting_z_height`

The `nesting_z_height` is the overlap between the lid and the plate when the lid is placed on the plate. This property can be measured using a caliper.

![nesting_z_height measurement](/resources/img/plate/lid_nesting_z_height.jpeg)



# Resource Library

The PLR Resource Library (PLR-RL) is the world's biggest and most accurate centralized collection of labware. If you cannot find something, please contribute what you are looking for!

```{toctree}
:maxdepth: 1

introduction
custom-resources
```

## `Resource` subclasses

In PLR every physical object is a subclass of the `Resource` superclass (except for `Tip`).
Each subclass adds unique methods or attributes to represent its unique physical specifications and behavior.

Some standard `Resource` subclasses in the inheritance tree are:

```
Resource
├── Carrier
│   ├── TipCarrier
│   ├── PlateCarrier
│   ├── MFXCarrier
│   ├── ShakerCarrier
│   └── TubeCarrier
├── Container
│   ├── Well
│   ├── PetriDish
│   ├── Tube
│   └── Trough
├── ItemizedResource
│   ├── Plate
│   ├── TipRack
│   └── TubeRack
├── Lid
└── PlateAdapter
```

See more detailed documentatino below (WIP).

```{toctree}
:caption: Resource subclasses

containers
itemized_resource
plates
plate_carriers
mfx
```

## Library

### Plate Naming Standard

PLR is not actively enforcing a specific plate naming standard but recommends the following:

![PLR_plate_naming_standards](img/PLR_plate_naming_standards.png)

This standard is similar to the [Opentrons API labware naming standard](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/3516) but 1) further sub-categorizes "wellplates" to facilitate communication with day-to-day users, and 2) adds information about the well-bottom geometry.

```{toctree}
:caption: Library

library/agenbio
library/alpaqua
library/azenta
library/biorad
library/boekel
library/celltreat
library/cellvis
library/corning_axygen
library/corning_costar
library/eppendorf
library/falcon
library/hamilton
library/nest
library/opentrons
library/porvair
library/revvity
library/thermo_fisher
library/vwr
```



# Containers

Resources that contain liquid are subclasses of {class}`pylabrobot.resources.container.Container`. This class provides a {class}`pylabrobot.resources.volume_tracker.VolumeTracker` that helps {class}`pylabrobot.liquid_handling.liquid_handler.LiquidHandler` keep track of the liquid in the resource. (For more information on trackers, check out {doc}`/user_guide/using-trackers`). Examples of subclasses of `Container` are {class}`pylabrobot.resources.Well` and {class}`pylabrobot.resources.trough.Trough`.

It is possible to instantiate a `Container` directly:

```python
from pylabrobot.resources import Container
container = Container(name="container", size_x=10, size_y=10, size_z=10)
# volume is computed by assuming the container is a cuboid, and can be adjusted with the max_volume
# parameter
```



# Corning - Axygen

Company page: [Corning - Axygen® Brand Products](https://www.corning.com/emea/en/products/life-sciences/resources/brands/axygen-brand-products.html)

> Corning acquired Axygen BioScience, Inc. and its subsidiaries in 2009. This acquisition included Axygen's broad portfolio of high-quality plastic consumables, liquid handling products, and bench-top laboratory equipment, which complemented and expanded Corning's offerings in the life sciences segment​.

## Plates

| Description | Image | PLR definition |
|-|-|-|
| 'Axy_24_DW_10ML'<br>Part no.: P-DW-10ML-24-C-S<br>[manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Genomics-&-Molecular-Biology/Automation-Consumables/Deep-Well-Plate/Axygen%C2%AE-Deep-Well-and-Assay-Plates/p/P-DW-10ML-24-C-S) | ![](img/corning_axygen/axygen_Axy_24_DW_10ML.jpg) | `Axy_24_DW_10ML` |



# CellTreat

## Plates

| Description | Image | PLR definition |
|-|-|-|
| 'CellTreat_6_DWP_16300ul_Fb'<br>Part no.: 229105<br>[manufacturer website](https://www.celltreat.com/product/229105/) | ![](img/celltreat/CellTreat_6_DWP_16300ul_Fb.jpg) | `CellTreat_6_DWP_16300ul_Fb` |
| Same as 229590 (229590 is sold with lids) 'CellTreat_96_wellplate_350ul_Ub'<br>Part no.: 229591<br>[manufacturer website](https://www.celltreat.com/product/229591/)  | ![](img/celltreat/CellTreat_96_wellplate_350ul_Ub.jpg) | `CellTreat_96_wellplate_350ul_Ub`  |



# Eppendorf

Company page: [Eppendorf Wikipedia](https://en.wikipedia.org/wiki/Eppendorf_(company))

> Eppendorf, a company with its registered office in Germany, develops, produces and sells products and services for laboratories around the world.

> Founding year: 1945
> Company type: private


## Plates

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'Eppendorf_96_wellplate_250ul_Vb'<br>Part no.: 0030133374<br>[manufacturer website](https://www.eppendorf.com/gb-en/Products/Laboratory-Consumables/Plates/Eppendorf-twintec-PCR-Plates-p-0030133374) <br><br> - Material: polycarbonate (frame), polypropylene (wells)<br> - part of the twin.tec(R) product line<br> - WARNING: not ANSI/SLAS 1-2004 footprint dimenions (123x81 mm^2!) ==> requires `PlateAdapter`<br> - 'Can be divided into 4 segments of 24 wells each to prevent waste and save money'. | ![](img/eppendorf/Eppendorf_96_wellplate_250ul_Vb_COMPLETE.png) ![](img/eppendorf/Eppendorf_96_wellplate_250ul_Vb_DIVIDED.png) | `Eppendorf_96_wellplate_250ul_Vb` |

## Tubes

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| "Common eppendorf tube" 'Eppendorf_DNA_LoBind_1_5ml_Vb'<br>Part no.: 0030133374<br>[manufacturer website](https://www.fishersci.com/shop/products/dna-lobind-microcentrifuge-tubes/13698791) | ![](img/eppendorf/Eppendorf_DNA_LoBind_1_5ml_Vb.webp) | `Eppendorf_DNA_LoBind_1_5ml_Vb` |



# VWR

Company page: [Wikipedia](https://en.wikipedia.org/wiki/VWR_International)

## Troughs

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'VWRReagentReservoirs25mL'<br>Part no.: 89094<br>[manufacturer website](https://us.vwr.com/store/product/4694822/vwr-disposable-pipetting-reservoirs)<br>Polystyrene Reservoirs | ![](img/vwr/VWRReagentReservoirs25mL.jpg) | `VWRReagentReservoirs25mL` |



# Revvity

Company wikipedia: [Revvity, Inc. (formerly PerkinElmer, Inc.)](https://en.wikipedia.org/wiki/Revvity)

> In 2022, a split of PerkinElmer resulted in one part, comprising its applied, food and enterprise services businesses, being sold to the private equity firm New Mountain Capital for $2.45 billion and thus no longer being public but keeping the PerkinElmer name. The other part, comprised of the life sciences and diagnostics businesses, remained public but required a new name, which in 2023 was announced as Revvity, Inc. From the perspective of Revvity, the goal of creating a separate company was that its businesses might show greater profit margins and more in the way of growth potential. An associated goal was to have more financial flexibility moving forward. On May 16, 2023, the PerkinElmer stock symbol PKI was replaced by the new symbol RVTY.

## Plates

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'Revvity_384_wellplate_28ul_Ub'<br>Part no.: 6008280<br>[manufacturer website](https://www.revvity.com/product/proxiplate-384-plus-50w-6008280) | ![](img/revvity/Revvity_384_wellplate_28ul_Ub.jpg) | `Revvity_384_wellplate_28ul_Ub`



# Thermo Fisher Scientific Inc.

Company page: [Thermo Fisher Scientific Inc. Wikipedia](https://en.wikipedia.org/wiki/Thermo_Fisher_Scientific)

> Thermo Fisher Scientific Inc. is an American supplier of analytical instruments, life sciences solutions, specialty diagnostics, laboratory, pharmaceutical and biotechnology services. Based in Waltham, Massachusetts, Thermo Fisher was formed through the **merger of Thermo Electron and Fisher Scientific in 2006**. Thermo Fisher Scientific has acquired other reagent, consumable, instrumentation, and service providers, including Life Technologies Corporation (2013), Alfa Aesar (2015), Affymetrix (2016), FEI Company (2016), BD Advanced Bioprocessing (2018),and PPD (2021).

A basic structure of the companiy, [its brands](https://www.thermofisher.com/uk/en/home/brands.html) and product lines looks like this:

```
Thermo Fisher Scientific Inc. (TFS, aka "Thermo")
├── Applied Biosystems (AB; brand)
│   └── MicroAmp
│      └── EnduraPlate
├── Fisher Scientific (FS; brand)
├── Invitrogen (INV; brand)
├── Ion Torrent (IT; brand)
├── Gibco (GIB; brand)
├── Thermo Scientific (TS; brand)
│   ├── Nalgene
│   ├── Nunc
│   └── Pierce
├── Unity Lab Services (brand, services)
├── Patheon (brand, services)
└── PPD (brand, services)
```

## Plates

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'Thermo_TS_96_wellplate_1200ul_Rb'<br>Part no.: AB-1127 or 10243223<br>[manufacturer website](https://www.fishersci.co.uk/shop/products/product/10243223) <br><br>- Material: Polypropylene (AB-1068, polystyrene) <br> | ![](img/thermo_fisher/Thermo_TS_96_wellplate_1200ul_Rb.webp) | `Thermo_TS_96_wellplate_1200ul_Rb` |
| 'Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate'<br>Part no.: 4483354 (TFS) or 15273005 (FS) (= with barcode)<br>Part no.: 16698853 (FS) (= **without** barcode)<br>[manufacturer website](https://www.thermofisher.com/order/catalog/product/4483354) <br><br>- Material: Polycarbonate, Polypropylene<br>- plate_type: semi-skirted<br>- product line: "MicroAmp"<br>- (sub)product line: "EnduraPlate" | ![](img/thermo_fisher/Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate.png) | `Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate` |
| 'Thermo_Nunc_96_well_plate_1300uL_Rb'<br>Part no.: 26025X | ![](img/thermo_fisher/Thermo_Nunc_96_well_plate_1300uL_Rb.jpg) | `Thermo_Nunc_96_well_plate_1300uL_Rb` |

## Troughs

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'ThermoFisherMatrixTrough8094'<br>Part no.: 8094<br>[manufacturer website](https://www.thermofisher.com/order/catalog/product/8094) | ![](img/thermo_fisher/ThermoFisherMatrixTrough8094.jpg.avif) | `ThermoFisherMatrixTrough8094` |



# Wuxi Nest

Wuxi NEST Biotechnology Co., Ltd. a leading life science plastic consumables manufactory, who is integrated with R&D production and sales, was established in 2009, located in Wuxi, Jiangsu, China. Our products have been exported to North America, Europe, Japan, Korea, India and other countries, enjoys an excellent reputation nationwide and abroad. Customers are almost all over the world.

## Plates

| Description | Image | PLR definition |
|-|-|-|
| 'nest_1_troughplate_195000uL_Vb'<br>Part no.: 360101<br>[manufacturer website](https://www.nest-biotech.com/reagent-reserviors/59178416.html)<br>- Material: polypropylene | ![](img/nest/nest_1_troughplate_195000uL_Vb.webp) | `nest_1_troughplate_195000uL_Vb` |
| 'nest_1_troughplate_185000uL_Vb'<br>Part no.: 360101<br>[manufacturer website](https://www.nest-biotech.com/reagent-reserviors/59178415.html)<br>- Material: polypropylene | ![](img/nest/nest_1_troughplate_185000uL_Vb.webp) | `nest_1_troughplate_185000uL_Vb` |
| 'nest_8_troughplate_22000uL_Vb'<br>Part no.: 360101<br>[manufacturer website](https://www.nestscientificusa.com/product/detail/513006470820794368)<br>- Material: polypropylene | ![](img/nest/nest_8_troughplate_22000uL_Vb.jpg) | `nest_8_troughplate_22000uL_Vb` |
| 'nest_12_troughplate_15000uL_Vb'<br>Part no.: 360102<br>[manufacturer website](https://www.nestscientificusa.com/product/detail/513006470820794368)<br>- Material: polypropylene | ![](img/nest/nest_12_troughplate_15000uL_Vb.jpg) | `nest_12_troughplate_15000uL_Vb` |



# Agenbio

[Company Page](https://agenbio.en.made-in-china.com)

## Plates

| Description | Image | PLR definition |
|-|-|-|
| 'AGenBio_4_troughplate_75000_Vb'<br>Part no.: RES-75-4MW<br>[manufacturer website](https://agenbio.en.made-in-china.com/product/ZTqYVMiCkpcF/China-Medical-Consumable-Plastic-Reagent-Reservoir-Disposable-4-Channel-Troughs-Reagent-Reservoir.html?) | ![](img/agenbio/AGenBio_4_troughplate_75000_Vb.webp) | `AGenBio_4_troughplate_75000_Vb` |
| 'AGenBio_1_troughplate_190000uL_Fl'<br>Part no.: RES-190-F<br>[manufacturer website](https://agenbio.en.made-in-china.com/product/pZWaBIPvZMkm/China-Res-190-F-Lad-Consumables-of-Flat-Reservoir.html) | ![](img/agenbio/AGenBio_1_troughplate_190000uL_Fl.webp) | `AGenBio_1_troughplate_190000uL_Fl` |
| 'AGenBio_1_troughplate_100000uL_Fl'<br>Part no.: RES-100-F<br>[manufacturer website](https://agenbio.en.made-in-china.com/product/rxgRnesJIjcQ/China-100ml-Flat-Bottom-Single-Well-Low-Profile-Design-Reagent-Reservoir.html) | ![](img/agenbio/AGenBio_1_troughplate_100000uL_Fl.jpg) | `AGenBio_1_troughplate_100000uL_Fl` |



# Alpaqua Engineering, LLC

Company page: [Alpaqua Engineering, LLC](https://www.alpaqua.com/about-us/)

> Alpaqua Engineering, LLC, founded in 2006, is a global provider of tools for accelerating genomic applications such as NGS, nucleic acid extraction and clean up, target capture, and molecular diagnostics.
Our products include a line of innovative, high performance magnet plates built with proprietary magnet architecture and spring cushion technology.  Also available are aluminum tube blocks to help maintain temperature control, SBS /ANSI standard tube racks and the Alpillo® Plate Cushion, which enables pipetting from the bottom of a well without tip occlusion​.

## Labware

| Description | Image | PLR definition |
|-|-|-|
| 'Alpaqua_96_magnum_flx'<br>Part no.: A000400<br>[manufacturer website](https://www.alpaqua.com/product/magnum-flx/) | ![](img/alpaqua/Alpaqua_96_magnum_flx.jpg) | `Alpaqua_96_magnum_flx` |



# Opentrons

Company page: [Opentrons Wikipedia](https://en.wikipedia.org/wiki/Opentrons)

> Opentrons Labworks, Inc. (or Opentrons) is a biotechnology company that manufactures liquid handling robots that use open-source software, which at one point used open-source hardware but no longer does.

NB: The [Opentrons Labware Library](https://labware.opentrons.com/) is a wonderful resource to see what Opentrons offers in terms of resources.

We can automatically convert Opentrons resources to PLR resources using two methods in `pylabrobot.resources.opentrons`:

- {func}`pylabrobot.resources.opentrons.load.load_opentrons_resource`: loading from a file
- {func}`pylabrobot.resources.opentrons.load.load_shared_opentrons_resource`: load from https://pypi.org/project/opentrons-shared-data/ (https://github.com/Opentrons/opentrons/tree/edge/shared-data)

In addition, we provide convenience methods for loading many resources (see below).

## Plates

Note that Opentrons definitions typically lack information that is required to make them work on other robots.

- `corning_384_wellplate_112ul_flat`
- `corning_96_wellplate_360ul_flat`
- `nest_96_wellplate_2ml_deep`
- `nest_96_wellplate_100ul_pcr_full_skirt`
- `appliedbiosystemsmicroamp_384_wellplate_40ul`
- `thermoscientificnunc_96_wellplate_2000ul`
- `usascientific_96_wellplate_2point4ml_deep`
- `thermoscientificnunc_96_wellplate_1300ul`
- `nest_96_wellplate_200ul_flat`
- `corning_6_wellplate_16point8ml_flat`
- `corning_24_wellplate_3point4ml_flat`
- `corning_12_wellplate_6point9ml_flat`
- `biorad_96_wellplate_200ul_pcr`
- `corning_48_wellplate_1point6ml_flat`
- `biorad_384_wellplate_50ul`

## Tip racks

- `eppendorf_96_tiprack_1000ul_eptips`
- `tipone_96_tiprack_200ul`
- `opentrons_96_tiprack_300ul`
- `opentrons_96_tiprack_10ul`
- `opentrons_96_filtertiprack_10ul`
- `geb_96_tiprack_10ul`
- `opentrons_96_filtertiprack_200ul`
- `eppendorf_96_tiprack_10ul_eptips`
- `opentrons_96_tiprack_1000ul`
- `opentrons_96_tiprack_20ul`
- `opentrons_96_filtertiprack_1000ul`
- `opentrons_96_filtertiprack_20ul`
- `geb_96_tiprack_1000ul`

## Reservoirs

- `agilent_1_reservoir_290ml`
- `axygen_1_reservoir_90ml`
- `nest_12_reservoir_15ml`
- `nest_1_reservoir_195ml`
- `nest_1_reservoir_290ml`
- `usascientific_12_reservoir_22ml`

## Tube racks

- `opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap`
- `opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap_acrylic`
- `opentrons_6_tuberack_falcon_50ml_conical`
- `opentrons_15_tuberack_nest_15ml_conical`
- `opentrons_24_tuberack_nest_2ml_screwcap`
- `opentrons_24_tuberack_generic_0point75ml_snapcap_acrylic`
- `opentrons_10_tuberack_nest_4x50ml_6x15ml_conical`
- `opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical_acrylic`
- `opentrons_24_tuberack_nest_1point5ml_screwcap`
- `opentrons_24_tuberack_nest_1point5ml_snapcap`
- `opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical`
- `opentrons_24_tuberack_nest_2ml_snapcap`
- `opentrons_24_tuberack_nest_0point5ml_screwcap`
- `opentrons_24_tuberack_eppendorf_1point5ml_safelock_snapcap`
- `opentrons_6_tuberack_nest_50ml_conical`
- `opentrons_15_tuberack_falcon_15ml_conical`
- `opentrons_24_tuberack_generic_2ml_screwcap`
- `opentrons_96_well_aluminum_block`
- `opentrons_24_aluminumblock_generic_2ml_screwcap`
- `opentrons_24_aluminumblock_nest_1point5ml_snapcap`

## Plate Adapters

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'Opentrons_96_adapter_Vb'<br>Part no.: 999-00028 (one of the three adapters purchased in the "Aluminum Block Set")<br>[manufacturer website](https://opentrons.com/products/aluminum-block-set) | ![](img/opentrons/Opentrons_96_adapter_Vb.jpg) | `Opentrons_96_adapter_Vb` |



# Falcon

# Plates

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| Falcon_96_wellplate_Fl [manufacturer website](https://www.fishersci.com/shop/products/falcon-96-well-cell-culture-treated-flat-bottom-microplate/087722C) | ![](img/falcon/Falcon_96_wellplate_Fl.webp) | `Falcon_96_wellplate_Fl`
| Falcon_96_wellplate_Rb [manufacturer website](https://ecatalog.corning.com/life-sciences/b2c/US/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon®-96-well-Polystyrene-Microplates/p/353077) | ![](img/falcon/Falcon_96_wellplate_Rb.jpg) | `Falcon_96_wellplate_Rb`
| Falcon_96_wellplate_Fl_Black [manufacturer website](https://www.fishersci.com/shop/products/falcon-96-well-imaging-plate-lid/08772225) | ![](img/falcon/Falcon_96_wellplate_Fl_Black.jpg.webp) | `Falcon_96_wellplate_Fl_Black`

## Tubes

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 50mL Falcon Tube [manufacturer website](https://www.fishersci.com/shop/products/falcon-50ml-conical-centrifuge-tubes-2/1495949A) | ![](img/falcon/falcon-tube-50mL.webp) | `falcon_tube_50mL`
| 15mL Falcon Tube [manufacturer website](https://www.fishersci.com/shop/products/falcon-15ml-conical-centrifuge-tubes-5/p-193301) | ![](img/falcon/falcon-tube-15mL.webp) | `falcon_tube_15mL`
| Falcon_tube_14mL_Rb <br> Corning cat. no.: 352059 <br>[manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/General-Labware/Tubes/Tubes,-Round-Bottom/Falcon%C2%AE-Round-Bottom-High-clarity-Polypropylene-Tube/p/352059) | ![](img/falcon/Falcon_tube_14mL_Rb.jpg) | `Falcon_tube_14mL_Rb`



# Biorad

[Company Page](https://en.wikipedia.org/wiki/Bio-Rad_Laboratories)

## Plates

| Description | Image | PLR definition |
|-|-|-|
| 'BioRad_384_DWP_50uL_Vb'<br>Part no.: HSP3805<br>[manufacturer website](https://www.bio-rad.com/en-us/sku/HSP3805-hard-shell-384-well-pcr-plates-thin-wall-skirted-clear-white?ID=HSP3805) | ![](img/biorad/BioRad_384_DWP_50uL_Vb.webp) | `BioRad_384_DWP_50uL_Vb` |



# Boekel

## Tube carrier

The following rack exists in 4 orientations:

- 50ml falcon tubes = `boekel_50mL_falcon_carrier`
- 15ml falcon tubes = `boekel_15mL_falcon_carrier`
- 1.5/2ml microcentrifuge tubes = `boekel_1_5mL_microcentrifuge_carrier`
- ?ml microcentrifuge tubes = `boekel_mini_microcentrifuge_carrier`

| Description               | Image              | PLR definition          |
|--------------------|--------------------|--------------------|
| Multi Tube Rack For 50ml Conical, 15ml Conical, And Microcentrifuge Tubes, PN:120008 [manufacturer website](https://www.boekelsci.com/multi-tube-rack-for-50ml-conical-15ml-conical-and-microcentrifuge-tubes-pn-120008.html) | ![](img/boekel/boekel_carrier50mL.jpg) | `boekel_50mL_falcon_carrier` |
| Multi Tube Rack For 50ml Conical, 15ml Conical, And Microcentrifuge Tubes, PN:120008 [manufacturer website](https://www.boekelsci.com/multi-tube-rack-for-50ml-conical-15ml-conical-and-microcentrifuge-tubes-pn-120008.html) | ![](img/boekel/boekel_carrier15mL.jpg) | `boekel_15mL_falcon_carrier` |
| Multi Tube Rack For 50ml Conical, 15ml Conical, And Microcentrifuge Tubes, PN:120008 [manufacturer website](https://www.boekelsci.com/multi-tube-rack-for-50ml-conical-15ml-conical-and-microcentrifuge-tubes-pn-120008.html) | ![](img/boekel/boekel_carrier1_5mL.jpg) | `boekel_1_5mL_microcentrifuge_carrier` |
| Multi Tube Rack For 50ml Conical, 15ml Conical, And Microcentrifuge Tubes, PN:120008 [manufacturer website](https://www.boekelsci.com/multi-tube-rack-for-50ml-conical-15ml-conical-and-microcentrifuge-tubes-pn-120008.html) | ![](img/boekel/boekel_carrier_mini.jpg) | `boekel_mini_microcentrifuge_carrier` |



# Hamilton STAR "ML_STAR"

Company history: [Hamilton Robotics history](https://www.hamiltoncompany.com/history)

> Hamilton Robotics provides automated liquid handling workstations for the scientific community. Our portfolio includes three liquid handling platforms, small devices, consumables, and OEM solutions.

## Carriers

### Tip carriers

| Description                                                                                                                                                                                                                                | Image                                        | PLR definition    |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------- | ----------------- |
| 'TIP_CAR_480_A00'<br>Part no.: 182085<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/182085) <br>Carrier for 5x 96 tip (10μl, 50μl, 300μl, 1000μl) racks or 5x 24 tip (5ml) racks (6T) | ![](img/hamilton/TIP_CAR_480_A00_182085.jpg) | `TIP_CAR_480_A00` |

### Plate carriers

| Description                                                                                                                                                                                                                                      | Image                                         | PLR definition     |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------- | ------------------ |
| 'PLT_CAR_L5AC_A00'<br>Part no.: 182090<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/182090) <br>Carrier for 5x 96 Deep Well Plates or for 5x 384 tip racks (e.g.384HEAD_384TIPS_50μl) (6T) | ![](img/hamilton/PLT_CAR_L5AC_A00_182090.jpg) | `PLT_CAR_L5AC_A00` |
| 'PLT_CAR_L5MD_A00'<br>Part no.: 182365/02<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/182365) <br>Carries five ANSI/SLAS footprint MTPs in landscape orientation. Occupies six tracks.    | ![](img/hamilton/182365-Plate-Carrier.webp)   | `PLT_CAR_L5MD_A00` |
| 'PLT_CAR_P3AC'<br>Part no.: 182365/03<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/182365) <br>Hamilton Deepwell Plate Carrier for 3 Plates (Portrait, 6 tracks wide)                      | ![](img/hamilton/PLT_CAR_P3AC.jpg)            | `PLT_CAR_P3AC`     |

### MFX carriers

| Description                                                                                                                                                                                                                                                                                                                                                                                                                   | Image                                          | PLR definition             |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | -------------------------- |
| 'MFX_CAR_L5_base'<br>Part no.: 188039<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/188039) <br>Labware carrier base for up to 5 Multiflex Modules <br>Occupies 6 tracks (6T).                                                                                                                                                                                           | ![](img/hamilton/MFX_CAR_L5_base_188039.jpg)   | `MFX_CAR_L5_base`          |
| 'MFX_CAR_L4_SHAKER'<br>Part no.: 187001<br>[secondary supplier website](https://www.testmart.com/estore/unit.cfm/PIPPET/HAMROB/187001/automated_pippetting_devices_and_systems/8.html) (cannot find information on Hamilton website)<br>Sometimes referred to as "PLT_CAR_L4_SHAKER" by Hamilton. <br>Template carrier with 4 positions for Hamilton Heater Shaker. <br>Occupies 7 tracks (7T). Can be screwed onto the deck. | ![](img/hamilton/MFX_CAR_L4_SHAKER_187001.png) | `MFX_CAR_L4_SHAKER_187001` |

### MFX modules

| Description                                                                                                                                                                                                                                              | Image                                           | PLR definition             |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | -------------------------- |
| 'MFX_TIP_module'<br>Part no.: 188160 or 188040<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/188040) <br>Module to position a high-, standard-, low volume or 5ml tip rack (but not a 384 tip rack) | ![](img/hamilton/MFX_TIP_module_188040.jpg)     | `MFX_TIP_module`           |
| 'MFX_DWP_rackbased_module'<br>Part no.: 188229?<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/188229) (<-non-functional link?) <br>MFX DWP module rack-based                                        | ![](img/hamilton/MFX_DWP_RB_module_188229_.jpg) | `MFX_DWP_rackbased_module` |
| 'MFX_DWP_module_flat'<br>Part no.: 6601988-01<br>manufacturer website unknown                                                                                                                                                                            | ![](img/hamilton/MFX_DWP_module_flat.jpg)       | `MFX_DWP_module_flat`      |

### Tube carriers

Sometimes called "sample carriers" in Hamilton jargon.

| Description                                                                                                                                                                                                                                        | Image                                 | PLR definition    |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- | ----------------- |
| 'Tube_CAR_24_A00'<br>Part no.: 173400<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/173400) <br>Carries 24 "sample" tubes with 14.5–18 mm outer diameter, 60–120 mm high. Occupies one track. | ![](img/hamilton/Tube_CAR_24_A00.png) | `Tube_CAR_24_A00` |

### Trough carriers

Sometimes called "reagent carriers" in Hamilton jargon.

| Description                                                                                                                                                                                                                            | Image                                      | PLR definition         |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ | ---------------------- |
| 'Trough_CAR_4R200_A00'<br>Part no.: 185436 (same as 96890-01?)<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/96890-01) <br>Trough carrier for 4x 200ml troughs. 2 tracks(T) wide. | ![](img/hamilton/Trough_CAR_4R200_A00.png) | `Trough_CAR_4R200_A00` |

## Labware

### TipRacks

| Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Image                                                                                                       | PLR definition                 |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------ |
| 'TIP_50ul_L'<br>Formats:<br> - "50μL CO-RE Tips, sterile with filter": Part no.: [235979](https://www.hamiltoncompany.com/automated-liquid-handling/disposable-tips/50-%CE%BCl-conductive-sterile-filter-tips)<br>&nbsp;&nbsp;&nbsp;&nbsp;• Filter=Filter <br>&nbsp;&nbsp;&nbsp;&nbsp;• Sterile=Sterile<br>&nbsp;&nbsp;&nbsp;&nbsp;• Tip Color (Conductivity)=Black (Conductive)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | ![](img/hamilton/TIP_50ul_L.png)                                                                            | `TIP_50ul_L`                   |
| 'Hamilton_96_tiprack_50ul_NTR'<br>Formats:<br> - "50μL CO-REII Tips, stacked NTRs, sterile": Part no.: [235987](https://www.hamiltoncompany.com/automated-liquid-handling/disposable-tips/50-%C2%B5l-nested-clear-sterile-tips)<br>&nbsp;&nbsp;&nbsp;&nbsp;• Filter=Non-Filter <br>&nbsp;&nbsp;&nbsp;&nbsp;• Sterile=Sterile<br>&nbsp;&nbsp;&nbsp;&nbsp;• Tip Color (Conductivity)=Black (Conductive)<br> - "50uL CO-REII Nested Clear Tips": Part no.: [235964](https://www.hamiltoncompany.com/automated-liquid-handling/disposable-tips/50-%C2%B5l-nested-clear-tips)<br>&nbsp;&nbsp;&nbsp;&nbsp;• Filter=Non-Filter <br>&nbsp;&nbsp;&nbsp;&nbsp;• Sterile=Non-Sterile<br>&nbsp;&nbsp;&nbsp;&nbsp;• Tip Color (Conductivity)=Clear (Non-Conductive) <br> <br> Note: a **single** `NTR` is only **one rack**.<br> Multiple NTRs stacked on top of each other (as shown in the images on the right) are called a `TipStack`. | ![](img/hamilton/Hamilton_96_tiprack_50ul_NTR.png) ![](img/hamilton/Hamilton_96_tiprack_50ul_NTR_CLEAR.png) | `Hamilton_96_tiprack_50ul_NTR` |

### Troughs

| Description                                                                                                                                                                                                                                                       | Image                                            | PLR definition               |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ | ---------------------------- |
| 'Hamilton_1_trough_200ml_Vb'<br>Part no.: 56695-02<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/56695-02) <br>Trough 200ml, w lid, self standing, Black. <br>Compatible with Trough_CAR_4R200_A00 (185436). | ![](img/hamilton/Hamilton_1_trough_200ml_Vb.jpg) | `Hamilton_1_trough_200ml_Vb` |

## Adapters

| Description                                                                                                                                                                                                                                                                                                           | Image                                            | PLR definition               |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ | ---------------------------- |
| 'Hamilton_96_adapter_188182'<br>Part no.: 188182<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/188182) (<-non-functional link?) <br>Adapter for 96 well PCR plate, plunged. Does not have an ANSI/SLAS footprint -> requires assignment with specified location. | ![](img/hamilton/Hamilton_96_adapter_188182.png) | `Hamilton_96_adapter_188182` |



# Corning - Costar

Wikipedia page: [Corning](https://en.wikipedia.org/wiki/Corning_Inc.)

> CCorning Incorporated is an American multinational technology company that specializes in specialty glass, ceramics, and related materials and technologies including advanced optics, primarily for industrial and scientific applications. The company was named Corning Glass Works until 1989. Corning divested its consumer product lines (including CorningWare and Visions Pyroceram-based cookware, Corelle Vitrelle tableware, and Pyrex glass bakeware) in 1998 by selling the Corning Consumer Products Company subsidiary (later Corelle Brands, now known as Instant Brands) to Borden.

As of 2014, Corning had five major business sectors: display technologies, environmental technologies, life sciences, optical communications, and specialty materials.

## Plates

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'Cos_6_wellplate_16800ul_Fb'<br>Part no.s: <br><ul> <li>[3335 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/3335)</li> <li>[3506 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/3506)</li> <li>[3516 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/3516)</li> <li>[3471 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/3471)</li> </ul> <br>- Material: ? <br>- Cleanliness: 3516: sterilized by gamma irradiation <br>- Nonreversible lids with condensation rings to reduce contamination <br>- Treated for optimal cell attachment <br>- Cell growth area: 9.5 cm² (approx.) <br>- Total volume: 16.8 mL | ![](img/corning_costar/Cos_6_wellplate_16800ul_Fb.jpg) | `Cos_6_wellplate_16800ul_Fb` |
| 'Cor_12_wellplate_6900ul_Fb' <br>Part no.s: <br><ul> <li>[3336 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/3336)</li> <li>[3512 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/3512)</li> <li>[3513 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/3513)</li> </ul> <br>- Total volume: 6.9 mL | ![](img/corning_costar/Cor_12_wellplate_6900ul_Fb.jpg) | `Cor_12_wellplate_6900ul_Fb` |
| 'Cor_24_wellplate_3470ul_Fb' <br>Part no.s: <br><ul> <li>[3337 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/3337)</li> <li>[3524 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/3524)</li> <li>[3526 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/3526)</li> <li>[3527 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/3527)</li> <li>[3473 manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/3473)</li> </ul> <br>- Total volume: 3.47 mL | ![](img/corning_costar/Cor_24_wellplate_3470ul_Fb.jpg) | `Cor_24_wellplate_3470ul_Fb` |
| 'Cor_48_wellplate_1620ul_Fb' <br>Part no.: 3548<br>[manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon%C2%AE-96-well-Polystyrene-Microplates/p/3548) <br><br>- Material: TC-treated polystyrene <br>- Cleanliness: sterile <br>- Total volume: 1.62 mL | ![](img/corning_costar/Cor_48_wellplate_1620ul_Fb.jpg) | `Cor_48_wellplate_1620ul_Fb` |
| 'Cos_96_wellplate_2mL_Vb'<br>Part no.: 3516<br>[manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/UK/en/Microplates/Assay-Microplates/96-Well-Microplates/Costar%C2%AE-Multiple-Well-Cell-Culture-Plates/p/3516) <br><br>- Material: Polypropylene <br>- Resistant to many common organic solvents (e.g., DMSO, ethanol, methanol) <br>- 3960: Sterile and DNase- and RNase-free <br>- Total volume: 2 mL <br>- Features uniform skirt heights for greater robotic gripping surface| ![](img/corning_costar/Cos_96_wellplate_2mL_Vb.jpg) | `Cos_96_wellplate_2mL_Vb` |
'Cor_96_wellplate_360ul_Fb' <br>Part no.: 353376<br>[manufacturer website](https://ecatalog.corning.com/life-sciences/b2b/NL/en/Microplates/Assay-Microplates/96-Well-Microplates/Falcon®-96-well-Polystyrene-Microplates/p/353376) <br><br>- Material: TC-treated polystyrene <br> - Cleanliness: sterile <br>- Total volume:  392 uL <br>- Working volume: 25-340 uL | ![](img/corning_costar/Cor_96_wellplate_360ul_Fb.jpg) | `Cor_96_wellplate_360ul_Fb` |



# Azenta

Company wikipedia: [Azenta](https://en.wikipedia.org/wiki/Azenta)

> Azenta (formerly Brooks Automation) was founded in 1978, and is based in Chelmsford, Massachusetts, United States. The company is a provider of life sciences services including genomics, cryogenic storage, automation, and informatics.
> In 2017, Brooks acquired 4titude, a maker of scientific tools and consumables, while in 2018, Brooks acquired GENEWIZ, a genomics services provider as part of their life sciences division's expansion.
> In November 2021, Brooks Automation Inc. split into two entities, Brooks Automation and Azenta Life Sciences. The latter will focus exclusively on their life science division.

## Plates

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'Azenta4titudeFrameStar_96_wellplate_skirted'<br><br> - Man. part no.: 4ti-0960<br> - Supplier part no.: PCR1232<br> - [manufacturer website](https://www.azenta.com/products/framestar-96-well-skirted-pcr-plate)<br> - [supplier website](https://www.scientificlabs.co.uk/product/pcr-plates/PCR1232)<br> - working volume: <100µl<br> - total well capacity: 200µl| ![](img/azenta/azenta_4titude_96PCR_4ti-0960.jpg) | `Azenta4titudeFrameStar_96_wellplate_skirted` |



# Cellvis

[Company Page](https://www.cellvis.com)

## Plates

| Description | Image | PLR definition |
|-|-|-|
| 'CellVis_24_wellplate_3600uL_Fb'<br>Part no.: P24-1.5P<br>[manufacturer website](https://www.cellvis.com/_24-well-plate-with--number-1.5-glass-like-polymer-coverslip-bottom-tissue-culture-treated-for-better-cell-attachment-than-cover-glass_/product_detail.php?product_id=65) | ![](img/cellvis/CellVis_24_wellplate_3600uL_Fb.jpg) | `CellVis_24_wellplate_3600uL_Fb` |
| 'CellVis_96_wellplate_350uL_Fb'<br>Part no.: P96-1.5H-N<br>[manufacturer website](https://www.cellvis.com/_96-well-glass-bottom-plate-with-high-performance-number-1.5-cover-glass_/product_detail.php?product_id=50) | ![](img/cellvis/CellVis_96_wellplate_350uL_Fb.jpg) | `CellVis_96_wellplate_350uL_Fb` |



# Porvair

Company history: [Porvair Filtration Group](https://www.porvairfiltration.com/about/our-history/)

> Porvair Filtration Group, a wholly owned subsidiary of Porvair plc, is a specialist filtration and environmental technology group involved in developing, designing and manufacturing filtration and separation solutions to industry sectors such as the aviation, molten metal, energy, water treatment and life sciences markets. Porvair plc is a publically owned company with four principal subsidiaries: Porvair Filtration Group Ltd., Porvair Sciences Ltd., Selee Corporation and Seal Analytical Ltd.

## Plates

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'Porvair_24_wellplate_Vb'<br>Part no.: 390108<br>[manufacturer website](https://www.microplates.com/product/78-ml-reservoir-plate-2-rows-of-12-v-bottom/) | ![](img/porvair/Porvair_24_wellplate_Vb.jpg) | `Porvair_24_wellplate_Vb` |

## Reservoirs

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'Porvair_6_reservoir_47ml_Vb'<br>Part no.: 6008280<br>[manufacturer website](https://www.microplates.com/product/282-ml-reservoir-plate-6-columns-v-bottom/) <br>- Material: Polypropylene <br>- Sterilization compatibility: Autoclaving (15 minutes at 121°C) or Gamma Irradiation <br>- Chemical resistance: "High chemical resistance"   <br>- Temperature resistance: high: -196°C to + 121°C <br>- Cleanliness: 390015: Free of detectable DNase, RNase <br>- ANSI/SLAS-format for compatibility with automated systems <br>- Tolerances: "Uniform external dimensions and tolerances"| ![](img/porvair/porvair_6x47_reservoir_390015.jpg) | `Porvair_6_reservoir_47ml_Vb` |



# Resources Introduction

This document introduces PyLabRobot Resources (labware and deck definitions) and general subclasses. You can find more information on creating custom resources in the {doc}`custom-resources` section.

In PyLabRobot, a {class}`pylabrobot.resources.resource.Resource` is a piece of labware or equipment used in a protocol or program, a part of a labware item (such as a Well) or a container of labware items (such as a Deck). All resources inherit from a single base class {class}`pylabrobot.resources.resource.Resource` that provides most of the functionality, such as the name, sizing, type, model, as well as methods for dealing with state. The name and sizing are required for all resources, with the name being a unique identifier for the resource and the sizing being the x, y and z-dimensions of the resource in millimeters when conceptualized as a cuboid.

While you can instantiate a `Resource` directly, several subclasses of methods exist to provide additional functionality and model specific resource attributes. For example, a {class}`pylabrobot.resources.plate.Plate` has methods for easily accessing {class}`pylabrobot.resources.well.Well`s.

The relation between resources is modelled by a tree, specifically an [_arborescence_](<https://en.wikipedia.org/wiki/Arborescence_(graph_theory)>) (a directed, rooted tree). The location of a resource in the tree is a Cartesian coordinate and always relative to the bottom front left corner of its immediate parent. The absolute location can be computed using {meth}`~pylabrobot.resources.resource.Resource.get_absolute_location`. The x-axis is left (smaller) and right (larger); the y-axis is front (small) and back (larger); the z-axis is down (smaller) and up (higher). Each resource has `children` and `parent` attributes that allow you to navigate the tree.

{class}`pylabrobot.machines.machine.Machine` is a special type of resource that represents a physical machine, such as a liquid handling robot ({class}`pylabrobot.liquid_handling.liquid_handler.LiquidHandler`) or a plate reader ({class}`pylabrobot.plate_reading.plate_reader.PlateReader`). Machines have a `backend` attribute linking to the backend that is responsible for converting PyLabRobot commands into commands that a specific machine can understand. Other than that, Machines, including {class}`pylabrobot.liquid_handling.liquid_handler.LiquidHandler`, are just like any other Resource.

## Defining a simple resource

The simplest way to define a resource is to subclass {class}`pylabrobot.resources.resource.Resource` and define the `name` and `size_x`, `size_y` and `size_z` attributes. Here's an example of a simple resource:

```python
from pylabrobot.resources import Resource
resource = Resource(name="resource", size_x=10, size_y=10, size_z=10)
```

To assign a child resource, you can use the `assign_child_resource` method:

```python
from pylabrobot.resources import Resource, Coordinate
child = Resource(name="child", size_x=5, size_y=5, size_z=5)
# assign to bottom front left corner of parent
resource.assign_child_resource(child, Coordinate(x=0, y=0, z=0))
```

## Saving and loading resources

PyLabRobot provide utilities to save and load resources and their states to and from files, as well as to serialize and deserialize resources and their states to and from Python dictionaries.

### Definitions

#### Saving to and loading from a file

Resource definitions, that includes deck definitions, can be saved to and loaded from a file using the `pylabrobot.resources.resource.Resource.save` and `pylabrobot.resources.resource.Resource.load` methods. The file format is JSON.

To save a resource to a file:

```python
resource.save("resource.json")
```

This will create a file `resource.json` with the resource definition.

```json
{
  "name": "resource",
  "type": "Resource",
  "size_x": 10,
  "size_y": 10,
  "size_z": 10,
  "location": null,
  "category": null,
  "model": null,
  "children": [],
  "parent_name": null
}
```

To load the resource from the file:

```python
resource = Resource.load_from_json_file("resource.json")
```

#### Serialization and deserialization

To simply serialize a resource to a Python dictionary:

```python
resource_dict = resource.serialize()
```

To load a resource from a Python dictionary:

```python
resource = Resource.deserialize(resource_dict)
```

### State

Each Resource is responsible for managing its own state, as deep down in the arborescence as possible (eg a Well instead of a Plate). The state of a resource is a Python dictionary that contains all the information necessary to restore the resource to a given state as far as PyLabRobot is concerned. This includes the liquids in a container, the presence of tips in a tip rack, and so on.

#### Serializing and deserializing state

The state of a single resource, that includes the volume of a container, can be serialized to and deserialized from a Python dictionary using the `pylabrobot.resources.resource.Resource.serialize_state` and `pylabrobot.resources.resource.Resource.deserialize_state` methods.

To serialize the state of a resource:

```python
from pylabrobot.resources import Container
c = Container(name="container", size_x=10, size_y=10, size_z=10)
c.serialize_state()
```

This will return a dictionary with the state of the resource:

```json
{ "liquids": [], "pending_liquids": [] }
```

To deserialize the state of a resource:

```python
c = Container(name="container", size_x=10, size_y=10, size_z=10)
c.load_state({ "liquids": [], "pending_liquids": [] })
```

This is convenient if you want to use PLR state in your own state management system, or save to a database.

Note that above, only the state of a single resource is serialized. If you want to serialize the state of a resource and all its children, you can use the {meth}`pylabrobot.resources.resource.Resource.serialize_all_state` and {meth}`pylabrobot.resources.resource.Resource.load_all_state` methods. These methods are used internally by the `save_state_to_file` and `load_state_from_file` methods.

#### Saving and loading state to and from a file

The state of a resource, that includes the volume of a container, can be saved to and loaded from a file using the `pylabrobot.resources.resource.Resource.save_state_to_file` and `pylabrobot.resources.resource.Resource.load_state_from_file` methods. The file format is JSON.

To save the state of a resource to a file:

```python
resource.save_state_to_file("resource_state.json")
```

By default, a Resource will not have a state:

```json
{}
```

If you had serialized a {class}`pylabrobot.resources.Container` with a volume of 1000 uL, the file would look like this:

```json
{ "liquids": [], "pending_liquids": [] }
```

To load the state of a resource from a file:

```python
resource.load_state_from_file("resource_state.json")
```



# ItemizedResource

Resources that contain items in a grid are subclasses of {class}`pylabrobot.resources.itemized_resource.ItemizedResource`. This class provides convenient methods for accessing the child-resources, such as by integer or SBS "A1" style-notation, as well as for traversing items in an `ItemizedResource`. Examples of subclasses of `ItemizedResource`s are {class}`pylabrobot.resources.plate.Plate` and {class}`pylabrobot.resources.tip_rack.TipRack`.

To instantiate an `ItemizedResource`, it is convenient to use the `pylabrobot.resources.utils.create_equally_spaced_2d` method to quickly initialize a grid of child-resources in a grid. Here's an example of a simple `ItemizedResource`:

```python
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
    dx=12,                           # distance between the first well and the border in the x-axis
    dy=12,                           # distance between the first well and the border in the y-axis
    dz=0,                            # distance between the first well and the border in the z-axis
    item_dx=9,                       # distance between the wells in the x-axis
    item_dy=9,                       # distance between the wells in the y-axis

    bottom_type=WellBottomType.FLAT, # a custom keyword argument passed to the Well initializer
  )
)
```



# Defining custom resources

This document describes how to define custom resources in PyLabRobot. We will build a custom liquid container (called "Blue Bucket") and a custom plate, consisting of tubes stuck on top of a plate.

## Defining a custom liquid container

![Blue Bucket](/resources/img/custom-resources/blue-bucket.jpg)

Defining create a custom liquid container, like the blue bucket above, is as easy as instantiating a {class}`pylabrobot.resources.Resource` object:

```python
from pylabrobot.resources import Coordinate, Resource

blue_bucket = Resource(
  name='Blue Bucket',
  size_x=123, # in mm
  size_y=86,
  size_z=75,
)
```

If you want to instantiate many resources sharing the same properties, you can create a subclass of {class}`pylabrobot.resources.Resource` and override the class attributes:

```python
class BlueBucket(Resource):
  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=123,
      size_y=86,
      size_z=75,
    )
```

This will allow you to creates instances like so:

```python
blue_bucket = BlueBucket(name="my blue bucket")
```

Because a blue bucket cannot have children, we override the {meth}`pylabrobot.resources.Resource.assign_child_resource` method to raise an exception:

```python
class BlueBucket(Resource):
  ...

  def assign_child_resource(self, child_resource: Resource, location: Coordinate) -> None:
    raise RuntimeError("BlueBuckets cannot have children")
```

### Aspirating from the custom resource

To help PLR track liquids in a container, all liquid-containing resources are subclasses of `Container`. Let's modify the class definition of BlueBucket to be a subclass of `Container`:

```python
class BlueBucket(Container):
```

The default behavior when aspirating from a resource is to aspirate from the bottom center:

```python
lh.aspirate(blue_bucket, vols=10)
```

![Aspirating from the blue bucket](/resources/img/custom-resources/aspirate-blue-bucket.jpg)

With multiple channels, the channels will be spread evenly across the bottom of the resource:

```python
await lh.aspirate(blue_bucket, vols=[10, 10, 10], use_channels=[0, 1, 2])
```

![Aspirating from the blue bucket with multiple channels](/resources/img/custom-resources/aspirate-blue-bucket-multiple-channels.jpg)

What happens when aspirating resources is that PLR creates a list of offsets that equally space the channels across the y-axis in the middle of the resource. These offsets are computed using {meth}`pylabrobot.resources.Resource.get_2d_center_offsets`. We can use this list and modify it to aspirate from a different location. In the example below, we will aspirate 10 mm from the left edge of the resource:

```python
offsets = blue_bucket.get_2d_center_offsets(n=2) # n=2, because we are using 2 channels
offsets = [Coordinate(x=10, y=c.y, z=c.z) for c in offsets] # set x coordinate of offsets to 10 mm
await lh.aspirate(blue_bucket, vols=[10, 10], offsets=offsets) # pass the offsets to the aspirate
```

![Aspirating from the blue bucket with multiple channels and custom offsets](/resources/img/custom-resources/aspirate-blue-bucket-multiple-channels-custom-offsets.jpg)

### Serializing data

Resources in PyLabRobot should be able to serialize and deserialize themselves, to allow them to be saved to disk and transmitted over a network.

On a high level, the `serialize` method creates a dictionary containing all data necessary to reconstruct a resource. This dictionary is passed to a resource's initializer as kwargs, meaning keys in the dictionary must correspond to initializer arguments.

The default Resource serializer encodes information for all resource properties, including the `size_x`, `size_y` and `size_z` attributes. Since we have these fixed for the `BlueBucket` class, we only have to serialize the name (the rest of the data is inferred by the type). So let's override the `serialize` method:

```python
class BlueBucket(Resource):
  ...

  def serialize(self) -> dict:
    return {
      "name": self.name,
      "type": self.__class__.__name__,
    }
```

## Defining a custom plate

![Custom Plate](/resources/img/custom-resources/tube-plate.jpg)

The resource pictured above is a custom plate, consisting of tubes in a rack.

To define the custom "tube plate", we will create a subclass of {class}`pylabrobot.resources.itemized_resource.ItemizedResource`. This class handles item indexing (think `plate["A1"]` and `plate.get_item(0)`).

{class}`pylabrobot.resources.itemized_resource.ItemizedResource` is a [generic class](https://mypy.readthedocs.io/en/stable/generics.html) that expects another class, of which the child resources will be instances. In this case, that class will be a custom `Tube` class. Let's define that first:

```python
class Tube(Container):
  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=9,
      size_y=9,
      size_z=45,
    )
```

Next, let's define the custom plate. The `Tube` class is passed as a type argument to the `ItemizedResource` class with `[Tube]`:

```python
from pylabrobot.resources import ItemizedResource, create_equally_spaced_2d

class TubePlate(ItemizedResource[Tube]):
  def __init__(self, name: str):
    super().__init__(
      name=name,
      size_x=127.0,
      size_y=86.0,
      size_z=45.0,
      items=create_equally_spaced_2d(Tube,
        num_items_x=12,
        num_items_y=8,
        dx=9.5,
        dy=7.0,
        dz=1.0,
        item_dx=9.0,
        item_dy=9.0,
      )
    )
```

The {meth}`pylabrobot.resources.create_equally_spaced_2d` function creates a list of items, equally spaced in a grid.

This resource is automatically compatible with the rest of PyLabRobot. For example, we can aspirate from the plate:

```python
tube_plate = TubePlate(name="tube_plate")
lh.deck.assign_child_resource(tube_plate, location=location)

lh.aspirate(tube_plate["A1":"C1"], vols=10)
lh.dispense(tube_plate["A2":"C2"], vols=10)
```

![Aspirating from the tube plate](/resources/img/custom-resources/aspirate-tube-plate.jpg)



# Plate Readers

PyLabRobot supports the following plate readers:

- {ref}`BMG Clariostar <clariostar>`

Plate readers are controlled by the {class}`~pylabrobot.plate_reading.plate_reader.PlateReader` class. This class takes a backend as an argument. The backend is responsible for communicating with the plate reader and is specific to the hardware being used.

```python
from pylabrobot.plate_reading import PlateReader
backend = SomePlateReaderBackend()
pr = PlateReader(backend=backend)
await pr.setup()
```

The {meth}`~pylabrobot.plate_reading.plate_reader.PlateReader.setup` method is used to initialize the plate reader. This is where the backend will connect to the plate reader and perform any necessary initialization.

The {class}`~pylabrobot.plate_reading.plate_reader.PlateReader` class has a number of methods for controlling the plate reader. These are:

- {meth}`~pylabrobot.plate_reading.plate_reader.PlateReader.open`: Open the plate reader and make the plate accessible to robotic arms.
- {meth}`~pylabrobot.plate_reading.plate_reader.PlateReader.close`: Close the plate reader and prepare the machine for reading.
- {meth}`~pylabrobot.plate_reading.plate_reader.PlateReader.read_luminescence`: Read luminescence from the plate.
- {meth}`~pylabrobot.plate_reading.plate_reader.PlateReader.read_absorbance`: Read absorbance from the plate.

Read a plate:

```python
await pr.open()
move_plate_to_reader()
await pr.close()
results = await pr.read_absorbance()
```

`results` will be a width x height array of absorbance values.

(clariostar)=

## BMG ClarioSTAR

The BMG CLARIOStar plate reader is controlled by the {class}`~pylabrobot.plate_reading.clario_star.CLARIOStar` class.

```python
from pylabrobot.plate_reading.clario_star import CLARIOStar
c = CLARIOStar()
```



# Setting up PLR on a Raspberry Pi

You can use PLR on any operating system, but Raspberry Pis can be a good choice if you want to run PLR on a dedicated device. They are cheap ($50) and can be left running 24/7. Any user on your network can ssh into it and use a workcell.

## Setting up the Raspberry Pi

- Use the Raspberry Pi Imager to install the Raspberry Pi OS on a microSD card: [https://www.raspberrypi.com/software/](https://www.raspberrypi.com/software/).
  - During the flashing, it is recommended to add a hostname and create an initial user so that you can SSH into the Raspberry Pi headlessly.
- After flashing, insert the microSD card into the Raspberry Pi and boot it up. Connect it to your network using an Ethernet cable.
- Alternatively, you can use WiFi if you configured it during flashing.
- SSH into the Raspberry Pi using the hostname and user you created during flashing.
  ```bash
  ssh <username>@<hostname>.local
  ```
- Update the Raspberry Pi:
  ```bash
  sudo apt update
  sudo apt upgrade
  ```
- Make USB devices accessible to users: add the following line to `/etc/udev/rules.d/99-usb.rules`:
  ```
  SUBSYSTEM=="usb", MODE="0666"
  ```
- Reload the udev rules with
  ```bash
  sudo udevadm control --reload-rules && sudo udevadm trigger
  ```

```{warning}
This adds permissions to all USB devices. This is useful when you control the device and don't want to worry when plugging in new devices, but it could be a security risk if the machine is shared with untrusted users. See [udev documentation](https://www.kernel.org/pub/linux/utils/kernel/hotplug/udev/udev.html) for more granular control.
```

## Setting up PLR

- See [installation instructions](https://docs.pylabrobot.org/user_guide/installation.html#installing-pylabrobot).



# User guide

```{toctree}
:maxdepth: 1
:caption: Getting started

installation
rpi
```

```{toctree}
:maxdepth: 1
:caption: Liquid handling

basic
using-the-visualizer
using-trackers
writing-robot-agnostic-methods
hamilton-star/hamilton-star
moving-channels-around
tip-spot-generators
96head
validation
```

```{toctree}
:maxdepth: 1
:caption: Centrifuge

centrifuge
```

```{toctree}
:maxdepth: 1
:caption: Plate reading

plate_reading
cytation5
```

```{toctree}
:maxdepth: 1
:caption: Pumps

pumps
```

```{toctree}
:maxdepth: 1
:caption: Scales

scales
```

```{toctree}
:maxdepth: 1
:caption: Temperature controlling

temperature
```

```{toctree}
:maxdepth: 1
:caption: Tilting

tilting
```

```{toctree}
:maxdepth: 1
:caption: Heater shakers

heating-shaking
```

```{toctree}
:maxdepth: 1
:caption: Fans

fans
```

```{toctree}
:maxdepth: 1
:caption: Configuration

configuration
```



# Pumps

PyLabRobot supports the following pumps:

- {ref}`Cole Parmer Masterflex <masterflex>`

## Introduction

Pumps are controlled by the {class}`~pylabrobot.pumps.pump.Pump` class. These take a backend as an argument. The backend is responsible for communicating with the pump and is specific to the hardware being used.

```python
from pylabrobot.pumps import Pump
backend = SomePumpBackend()
p = Pump(backend=backend)
await p.setup()
```

The {meth}`~pylabrobot.pumps.pump.Pump.setup` method is used to initialize the pump. This is where the backend will connect to the pump and perform any necessary initialization.

The {class}`~pylabrobot.pumps.pump.Pump` class has a number of methods for controlling the pump. These are:

- {meth}`~pylabrobot.pumps.pump.Pump.run_continuously`: Run the pump continuously at a given speed.

- {meth}`~pylabrobot.pumps.pump.Pump.run_revolutions`: Run the pump for a given number of revolutions.

- {meth}`~pylabrobot.pumps.pump.Pump.halt`: Stop the pump immediately.

Run the pump for 5 seconds at 100 RPM:

```python
await p.run_continuously(speed=100)
await asyncio.sleep(5)
await p.halt()
```

(masterflex)=

## Cole Parmer Masterflex

The Masterflex pump is controlled by the {class}`~pylabrobot.pumps.cole_parmer.masterflex.Masterflex` class. This takes a serial port as an argument. The serial port is used to communicate with the pump.

```python
from pylabrobot.pumps.cole_parmer.masterflex import Masterflex
m = Masterflex(com_port='/dev/cu.usbmodemDEMO000000001')
```

(I have tried on the L/S 07551-20, but it should work on other models as well.)

Documentation available at: [https://web.archive.org/web/20210924061132/https://pim-resources.coleparmer.com/instruction-manual/a-1299-1127b-en.pdf](https://web.archive.org/web/20210924061132/https://pim-resources.coleparmer.com/instruction-manual/a-1299-1127b-en.pdf)



# Tilting

Currently only the Hamilton tilt module is supported.

```python
from pylabrobot.tilting.hamilton import HamiltonTiltModule

tilter = HamiltonTiltModule(name="tilter", com_port="COM1")

await lh.move_plate(my_plate, tilter)

await tilter.set_angle(10) # absolute angle, clockwise, in degrees
await tilter.tilt(-1) # relative
```



# Installation

These instructions describe how to install PyLabRobot.

Note that there are additional installation steps for using the firmware (universal) interface to Hamiltons and Tecans, see {ref}`below <using-the-firmware-interface>`.

## Installing PyLabRobot

It is highly recommended that you install PyLabRobot in a virtual environment. [virtualenv](https://virtualenv.pypa.io/en/latest/) is a popular tool for doing that, but you can use any tool you like. Note that virtualenv needs to be installed separately first.

Here's how to create a virtual environment using virtualenv:

```bash
mkdir your_project
cd your_project
python -m virtualenv env
source env/bin/activate  # on Windows: .\env\Scripts\activate
```

### From source

Alternatively, you can install PyLabRobot from source. This is particularly useful if you want to contribute to the project.

```bash
git clone https://github.com/pylabrobot/pylabrobot.git
cd pylabrobot
pip install -e '.[dev]'
```

See [CONTRIBUTING.md](/contributor_guide/contributing) for specific instructions on testing, documentation and development.

### Using pip (often outdated NOT recommended)

> The PyPI package is often out of date. Please install from source (see above).

The following will install PyLabRobot and the essential dependencies:

```bash
pip install pylabrobot
```

If you want to build documentation or run tests, you need install the additional
dependencies. Also using pip:

```bash
pip install 'pylabrobot[docs]'
pip install 'pylabrobot[testing]'
```

There's a multitude of other optional dependencies that you can install. Replace `[docs]` with one of the following items to install the desired dependencies.

- `fw`: Needed for firmware control over Hamilton robots.
- `http`: Needed for the HTTP backend.
- `websockets`: Needed for the WebSocket backend.
- `simulation`: Needed for the simulation backend.
- `opentrons`: Needed for the Opentrons backend.
- `server`: Needed for LH server, an HTTP front end to LH.
- `agrow`: Needed for the AgrowPumpArray backend.
- `plate_reading`: Needed to interact with the CLARIO Star plate reader.
- `inheco`: Needed for the Inheco backend.
- `dev`: Everything you need for development.
- `all`: Everything. May not be available on all platforms.

To install multiple dependencies, separate them with a comma:

```bash
pip install 'pylabrobot[fw,server]'
```

Or install all dependencies at once:

```bash
pip install 'pylabrobot[all]'
```

(using-the-firmware-interface)=

## Using the firmware interface with Hamilton or Tecan robots

If you want to use the firmware version of the Hamilton or Tecan interfaces, you need to install a backend for [PyUSB](https://github.com/pyusb/pyusb/). You can find the official installation instructions [here](https://github.com/pyusb/pyusb#requirements-and-platform-support). The following is a complete (and probably easier) guide for macOS, Linux and Windows.

Reminder: when you are using the firmware version, make sure to install the firmware dependencies as follows:

```bash
pip install pylabrobot[fw]
```

### On Linux

You should be all set!

### On Mac

You need to install [libusb](https://libusb.info/). You can do this using [Homebrew](https://brew.sh/):

```bash
brew install libusb
```

### On Windows

#### Installing

1. Download and install [Zadig](https://zadig.akeo.ie).

2. Make sure the Hamilton is connected using the USB cable and that no other Hamilton/VENUS software is running.

3. Open Zadig and select "Options" -> "List All Devices".

![](/user_guide/img/installation/install-1.png)

4. Select "ML Star" from the list if you're using a Hamilton STAR or STARlet. If you're using a Tecan robot, select "TECU".

![](/user_guide/img/installation/install-2.png)

5. Select "libusbK" using the arrow buttons.

![](/user_guide/img/installation/install-3.png)

6. Click "Replace Driver".

![](/user_guide/img/installation/install-4.png)

7. Click "Close" to finish.

![](/user_guide/img/installation/install-5.png)

#### Uninstalling

_These instructions only apply if you are using VENUS on your computer!_

If you ever wish to switch back from firmware command to use `pyhamilton` or plain VENUS, you have to replace the updated driver with the original Hamilton or Tecan one.

1. This guide is only relevant if ML Star is listed under libusbK USB Devices in the Device Manager program.

![](/user_guide/img/installation/uninstall-1.png)

2. If that"s the case, double click "ML Star" (or similar) to open this dialog, then click "Driver".

![](/user_guide/img/installation/uninstall-2.png)

3. Click "Update Driver".

![](/user_guide/img/installation/uninstall-3.png)

4. Select "Browse my computer for driver software".

![](/user_guide/img/installation/uninstall-4.png)

5. Select "Let me pick from a list of device drivers on my computer".

![](/user_guide/img/installation/uninstall-5.png)

6. Select "Microlab STAR" and click "Next".

![](/user_guide/img/installation/uninstall-6.png)

7. Click "Close" to finish.

![](/user_guide/img/installation/uninstall-7.png)

### Troubleshooting

If you get a `usb.core.NoBackendError: No backend available` error: [this](https://github.com/pyusb/pyusb/blob/master/docs/faq.rst#how-do-i-fix-no-backend-available-errors) may be helpful.

If you are still having trouble, please reach out on [discuss.pylabrobot.org](https://discuss.pylabrobot.org).

## Cytation5 imager

In order to use imaging on the Cytation5, you need to:

1. Install python 3.10
2. Download Spinnaker SDK and install (including Python) [https://www.teledynevisionsolutions.com/products/spinnaker-sdk/](https://www.teledynevisionsolutions.com/products/spinnaker-sdk/)
3. Install numpy==1.26 (this is an older version)

If you just want to do plate reading, heating, shaknig, etc. you don't need to follow these specific steps.



# Centrifuges

PyLabRobot supports the following centrifuges:

- {ref}`VSpin <VSpin>`

Centrifuges are controlled by the {class}`~pylabrobot.centrifuge.centrifuge.Centrifuge` class. This class takes a backend as an argument. The backend is responsible for communicating with the centrifuge and is specific to the hardware being used.

```python
from pylabrobot.centrifuge import Centrifuge
backend = SomeCentrifugeBackend()
pr = Centrifuge(backend=backend)
await pr.setup()
```

The {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.setup` method is used to initialize the centrifuge. This is where the backend will connect to the centrifuge and perform any necessary initialization.

The {class}`~pylabrobot.centrifuge.centrifuge.Centrifuge` class has a number of methods for controlling the centrifuge. These are:

- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.open_door`: Open the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.close_door`: Close the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.lock_door`: Lock the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.unlock_door`: Unlock the centrifuge door.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.lock_bucket`: Lock centrifuge buckets.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.unlock_bucket`: Unlock centrifuge buckets.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.go_to_bucket1`: Rotate to Bucket 1.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.go_to_bucket2`: Rotate to Bucket 2.
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.rotate_distance`: Rotate the buckets a specified distance (8000 = 360 degrees).
- {meth}`~pylabrobot.centrifuge.centrifuge.Centrifuge.start_spin_cycle`: Start centrifuge spin cycle.

Start spin cycle:

```python
await cf.start_spin_cycle(g = 800, duration = 60)
```

(VSpin)=

## VSpin

The VSpin centrifuge is controlled by the {class}`~pylabrobot.centrifuge.vspin.VSpin` class.

```python
from pylabrobot.centrifuge import Centrifuge, VSpin
cf = Centrifuge(name = 'centrifuge', backend = VSpin(bucket_1_position=0), size_x= 1, size_y=1, size_z=1)
```

### Installation

The VSpin centrifuge connects to your system via a COM port. Integrating it with `pylabrobot` library requires some setup. Follow this guide to get started.

#### 1. Preparing Your Environment

- Windows:

##### Find Your Python Directory

To use the necessary FTDI `.dll` files, you need to locate your Python environment:

1. Open Python in your terminal:
   ```python
   python
   >>> import sys
   >>> sys.executable
   ```
2. This will print a path, e.g., `C:\Python39\python.exe`.
3. Navigate to the `Scripts` folder in the same directory as `python.exe`.

##### **Download FTDI DLLs**

Download the required `.dll` files from the following link:
[FTDI Development Kit](https://sourceforge.net/projects/picusb/files/libftdi1-1.5_devkit_x86_x64_19July2020.zip/download) (link will start download).

1. Extract the downloaded zip file.
2. Locate the `bin64` folder.
3. Copy the files named:
   - `libftdi1.dll`
   - `libusb-1.0.dll`

##### Place DLLs in Python Scripts Folder

Paste the copied `.dll` files into the `Scripts` folder of your Python environment. This enables Python to communicate with FTDI devices.

- macOS:

Install libftdi using [Homebrew](https://brew.sh/):

```bash
brew install libftdi
```

- Linux:

Debian (rpi) / Ubuntu etc:

```bash
sudo apt-get install libftdi-dev
```

Other distros may have similar packages.

#### 2. Configuring the Driver with Zadig

- **This step is only required on Windows.**

Use Zadig to replace the default driver of the VSpin device with `libusbk`:

1. **Identify the VSpin Device**

   - Open Zadig.
   - To confirm the VSpin device, disconnect the RS232 port from the centrifuge while monitoring the Zadig device list.
   - The device that disappears is your VSpin, likely titled "USB Serial Converter."

2. **Replace the Driver**
   - Select the identified VSpin device in Zadig.
   - Replace its driver with `libusbk`.
   - Optionally, rename the device to "VSpin" for easy identification.

> **Note:** If you need to revert to the original driver for tools like the Agilent Centrifuge Config Tool, go to **Device Manager** and uninstall the `libusbk` driver. The default driver will reinstall automatically.

#### 3. Finding the FTDI ID

To interact with the centrifuge programmatically, you need its FTDI device ID. Use the following steps to find it:

1. Open a terminal and run:
   ```bash
   python -m pylibftdi.examples.list_devices
   ```
2. This will output something like:
   ```
   FTDI:USB Serial Converter:FTE0RJ5T
   ```
3. Copy the ID (`FTE0RJ5T` or your equivalent).

#### **4. Setting Up the Centrifuge**

Use the following code to configure the centrifuge in Python:

```python
from pylabrobot.centrifuge import Centrifuge, VSpin

# Replace with your specific FTDI device ID and bucket position for profile in Agilent Centrifuge Config Tool.
backend = VSpin(bucket_1_position=6969, device_id="XXXXXXXX")
centrifuge = Centrifuge(
   backend=backend,
   name="centrifuge",
   size_x=1, size_y=1, size_z=1
)

# Initialize the centrifuge.
await centrifuge.setup()
```

You’re now ready to use your VSpin centrifuge with `pylabrobot`!



# iSWAP Module

The `R0` module allows fine grained control of the iSWAP gripper.

## Common tasks

- Parking

You can park the iSWAP using {meth}`~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.park_iswap`.

```python
await lh.backend.park_iswap()
```

- Opening gripper:

You can open the iSWAP gripper using {meth}`~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_open_gripper`. Warning: this will release any object that is gripped. Used for error recovery.

```python
await lh.backend.iswap_open_gripper()
```

## Rotations

You can rotate the iSWAP to 12 predifined positions using {meth}`~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.iswap_rotate`.

the positions and their corresponding integer specifications are shown visually here.

![alt text](iswap_positions.png)

For example to extend the iSWAP fully to the left, the position parameter to `iswap_rotate` would be `12`

You can control the wrist (T-drive) and rotation drive (W-drive) individually using {meth}`~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.rotate_iswap_wrist` and {meth}`~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.rotate_iswap_rotation_drive` respectively. Make sure you have enough space (you can use {meth}`~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.move_iswap_y_relative`)

```python
rotation_drive = random.choice([STAR.RotationDriveOrientation.LEFT, STAR.RotationDriveOrientation.RIGHT, STAR.RotationDriveOrientation.FRONT])
wrist_drive = random.choice([STAR.WristOrientation.LEFT, STAR.WristOrientation.RIGHT, STAR.WristOrientation.STRAIGHT, STAR.WristOrientation.REVERSE])
await lh.backend.rotate_iswap_rotation_drive(rotation_drive)
await lh.backend.rotate_iswap_wrist(wrist_drive)
```

## Slow movement

You can make the iswap move more slowly during sensitive operations using {meth}`~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR.slow_iswap`. This is useful when you want to avoid splashing or other disturbances.

```python
async with lh.backend.slow_iswap():
  await lh.move_plate(plate, plt_car[1])
```



# Liquid level detection on Hamilton STAR(let)

Liquid level detection (LLD) is a feature that allows the Hamilton STAR(let) to move the pipetting tip down slowly until a liquid is found using either a) the pressure sensor, or b) a change in capacitance, or c) both. This feature is useful if you want to aspirate or dispense at a distance relative to the liquid surface, but you don't know the exact height of the liquid in the container.

To use LLD, you need to specify the LLD mode when calling the `aspirate` or `dispense` methods. Here is how you can use pressure or capacative LLD with the `aspirate` :

```python
await lh.aspirate([tube], vols=[300], lld_mode=[STAR.LLDMode.GAMMA])
```

The `lld_mode` parameter can be one of the following:

- `STAR.LLDMode.OFF`: default, no LLD
- `STAR.LLDMode.GAMMA`: capacative LLD
- `STAR.LLDMode.PRESSURE`: pressure LLD
- `STAR.LLDMode.DUAL`: both capacative and pressure LLD
- `STAR.LLDMode.Z_TOUCH_OFF`: find the bottom of the container

The `lld_mode` parameter is a list, so you can specify a different LLD mode for each channel.

```{note}
The `lld_mode` parameter is only avilable when using the `STAR` backend.
```

## Catching errors

All channelized pipetting operations raise a `ChannelizedError` exception when an error occurs, so that we can have specific error handling for each channel.

When no liquid is found in the container, the channel will have a `TooLittleLiquidError` error. This is useful for detecting that your container is empty.

You can catch the error like this:

```python
from pylabrobot.liquid_handling.errors import ChannelizedError
from pylabrobot.resources.errors import TooLittleLiquidError
channel = 0
try:
  await lh.aspirate([tube], vols=[300], lld_mode=[STAR.LLDMode.GAMMA], use_channels=[channel])
except ChannelizedError as e:
  if isinstance(e.errors[channel], TooLittleLiquidError):
    print("Too little liquid in tube")
```



# Configuring PLR

The `pylabrobot.config` module provides the `Config` class for configuring PLR. The configuration can be set programmatically or loaded from a file.

The configuration currently only supports logging configuration.

## The `Config` class

You can create a `Config` object as follows:

```python
import logging
from pathlib import Path
from pylabrobot.config import Config

config = Config(
  logging=Config.Logging(
    level=logging.DEBUG,
    log_dir=Path("my_logs")
  )
)
```

Then, call `pylabrobot.configure` to apply the configuration:

```python
import pylabrobot
pylabrobot.configure(config)
```

## Loading from a file

PLR supports loading configuration from a number of file formats. The supported formats are:

- INI files
- JSON files

Files are loaded using the `pylabrobot.config.load_config` function:

```python
from pylabrobot.config import load_config
config = load_config("config.json")

import pylabrobot
pylabrobot.configure(config)
```

If no file is found, a default configuration is used.

`load_config` has the following parameters:

```python
def load_config(
  base_file_name: str,
  create_default: bool = False,
  create_module_level: bool = True
) -> Config:
```

A `pylabrobot.ini` file is used if found in the current directory. If not found, it is searched for in all parent directories. If it still is not found, it gets created at either the project level that contains the `.git` directory, or the current directory.

### INI files

Example of an INI file:

```ini
[logging]
level = DEBUG
log_dir = .
```

### JSON files

```json
{
  "logging": {
    "level": "DEBUG",
    "log_dir": "."
  }
}
```



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



# MFX Carriers and Modules

MFX Carriers are a user-configurable carrier system, created by Hamilton. The user can configure the carrier system by placing plate sites, tip racks, tilt modules and other items at specific l;locations by screwing them into pre-threaded holes in the carrier. Different carrier bases are available.

In this tutorial, we will show how to create a custom carrier system using the MFX Carriers in PyLabRobot. We will use the `MFX_CAR_L5_base` as the base base, and a deep well plate module (`MFX_DWP_rackbased_module`) and a tip module (`MFX_TIP_module`) as the modules.


```python
%load_ext autoreload
%autoreload 2
```


```python
from pylabrobot.resources import (
  MFX_CAR_L5_base,
  MFX_DWP_rackbased_module,
  MFX_TIP_module,
)
```

Start by creating variables for your mfx modules. Depending on the type of module, the class might be a {class}`pylabrobot.resources.resource_holder.ResourceHolder` (for tip rack holders), a {class}`pylabrobot.resources.carrier.PlateHolder` (for plate modules), or a `Machine` class.

Let's create plate and a tip rack modules:


```python
my_plate_module = MFX_DWP_rackbased_module(name="my_plate_module")
my_plate_module
```




    PlateHolder(name=my_plate_module, location=None, size_x=135.0, size_y=94.0, size_z=59.80500000000001, category=plate_holder)




```python
my_tip_rack_module = MFX_TIP_module(name="my_tip_rack_module")
my_tip_rack_module
```




    ResourceHolder(name=my_tip_rack_module, location=None, size_x=135.0, size_y=94.0, size_z=96.60500000000002, category=resource_holder)



Using a dictionary, you can place your mfx modules in arbitrary locations:


```python
carrier = MFX_CAR_L5_base(
  name="my_carrier",
  modules={
    0: my_plate_module,
    3: my_tip_rack_module,
  }
)
```

    {0: PlateHolder(name=my_plate_module, location=(000.000, 005.000, 018.195), size_x=135.0, size_y=94.0, size_z=59.80500000000001, category=plate_holder), 3: ResourceHolder(name=my_tip_rack_module, location=(000.000, 293.000, 018.195), size_x=135.0, size_y=94.0, size_z=96.60500000000002, category=resource_holder)}


The children of an MFXCarrier are the sites you specified when creating the carrier.


```python
carrier[0]
```




    PlateHolder(name=carrier-my_carrier-spot-0, location=(000.000, 005.000, 018.195), size_x=135.0, size_y=94.0, size_z=59.80500000000001, category=plate_holder)




```python
carrier[3]
```




    ResourceHolder(name=carrier-my_carrier-spot-3, location=(000.000, 293.000, 018.195), size_x=135.0, size_y=94.0, size_z=96.60500000000002, category=resource_holder)



When a site is not defined, indexing into it will raise a `KeyError`.


```python
try:
  carrier[1]
except KeyError as e:
  print(f"KeyError, as expected.")
```

    KeyError, as expected.


To define in PLR that there is a plate on some module in the carrier, you can assign a plate to that module using the usual `assign_child_resource` method.


```python
from pylabrobot.resources import Cos_96_wellplate_2mL_Vb
my_plate = Cos_96_wellplate_2mL_Vb(name="my_plate")
carrier[0].assign_child_resource(my_plate)
my_plate.parent
```




    PlateHolder(name=carrier-my_carrier-spot-0, location=(000.000, 005.000, 018.195), size_x=135.0, size_y=94.0, size_z=59.80500000000001, category=plate_holder)



As with other carriers, you can also assign it directly to the site using the following syntax:


```python
from pylabrobot.resources import HTF
my_tip_rack = HTF(name="my_tip_rack")
carrier[3] = my_tip_rack
my_tip_rack.parent
```




    ResourceHolder(name=carrier-my_carrier-spot-3, location=(000.000, 293.000, 018.195), size_x=135.0, size_y=94.0, size_z=96.60500000000002, category=resource_holder)





# Plate Carriers

Plate carriers slide into rails on railed-decks like Hamilton STAR(let) and Tecan EVO, and are used to hold Plates.

## Using a plate carrier

The PyLabRobot Resource Library (PLR-RL) has a big number of predefined carriers. You can find these in the [PLR-RL docs](https://docs.pylabrobot.org/resources/index.html). [Hamilton Plate Carriers](https://docs.pylabrobot.org/resources/library/ml_star.html#plate-carriers) may be of particular interest.


```python
from pylabrobot.resources.hamilton import PLT_CAR_L5AC_A00
```


```python
my_plate_carrier = PLT_CAR_L5AC_A00(name="my_plate_carrier")
my_plate_carrier.capacity
```




    5



To assign a plate at a specific location in the plate carrier, simply set it at a specific index. In PLR, carriers are 0-indexed where the site at the front of the robot (nearest to the door) is 0.


```python
from pylabrobot.resources import Cor_96_wellplate_360ul_Fb

my_plate = Cor_96_wellplate_360ul_Fb(name="my_plate")
my_plate_carrier[0] = my_plate
```

You can assign plates to a variable and to the carrier in a single line.


```python
my_plate_carrier[1] = my_other_plate = Cor_96_wellplate_360ul_Fb(name="my_other_plate")
```

The children (in the arborescence) of a plate carrier are {class}`pylabrobot.resources.carrier.PlateHolder` objects. These model the sites for plates on the carrier. A `PlateHolder` may or may not have a `Plate` as a child, depending on whether the spot is occupied.


```python
my_plate_carrier[0]
```




    PlateHolder(name=carrier-my_plate_carrier-spot-0, location=(004.000, 008.500, 086.150), size_x=127.0, size_y=86.0, size_z=0, category=plate_holder)




```python
my_plate.parent
```




    PlateHolder(name=carrier-my_plate_carrier-spot-0, location=(004.000, 008.500, 086.150), size_x=127.0, size_y=86.0, size_z=0, category=plate_holder)



You can use the `PlateHolder.resource` attribute to access the `Plate` object, if it exists.


```python
my_plate_carrier[0].resource
```




    Plate(name=my_plate, size_x=127.76, size_y=85.48, size_z=14.2, location=(000.000, 000.000, -03.030))




```python
my_plate_carrier[2].resource is None
```




    True



### Moving plates onto carrier sites

If your liquid handling robot has a robotic arm, or if you are using an external robot arm that can interface with carriers, you can move plates out of or onto carriers using the `move_plate` method. For this, you can specify the destination by indexing into the carrier. This will return a `PlateHolder` object.

As an example, we will use the LiquidHandlerChatterboxBackend, but this code will work on any robot that supports moving plates.


```python
from pylabrobot.liquid_handling import LiquidHandler, LiquidHandlerChatterboxBackend
from pylabrobot.resources import STARDeck
lh = LiquidHandler(backend=LiquidHandlerChatterboxBackend(), deck=STARDeck())
lh.deck.assign_child_resource(my_plate_carrier, rails=1)
await lh.setup()
```

    Resource my_plate_carrier was assigned to the liquid handler.
    Setting up the liquid handler.
    Resource deck was assigned to the liquid handler.
    Resource trash was assigned to the liquid handler.
    Resource trash_core96 was assigned to the liquid handler.
    Resource my_plate_carrier was assigned to the liquid handler.



```python
await lh.move_resource(my_plate, my_plate_carrier[2])
```

    Moving Move(resource=Plate(name=my_plate, size_x=127.76, size_y=85.48, size_z=14.2, location=(000.000, 000.000, -03.030)), destination=PlateHolder(name=carrier-my_plate_carrier-spot-2, location=(004.000, 200.500, 086.150), size_x=127.0, size_y=86.0, size_z=0, category=plate_holder), intermediate_locations=[], resource_offset=Coordinate(x=0, y=0, z=0), destination_offset=Coordinate(x=0, y=0, z=0), pickup_distance_from_top=0, get_direction=<GripDirection.FRONT: 1>, put_direction=<GripDirection.FRONT: 1>).


## Pedestal z height

> ValueError("pedestal_size_z must be provided. See https://docs.pylabrobot.org/resources/plate_carriers.html#pedestal_size_z for more information.")

Many plate carriers feature a "pedestal" or "platform" on the sites. Plates can sit on this pedestal, or directly on the bottom of the site. This depends on the pedestal _and_ plate geometry, so it is important that we know the height of the pedestal.

The pedestal information is not typically available in labware databases (like the VENUS or EVOware databases), and so we rely on users to measure and contribute this information.

Here's how you measure the pedestal height:

![Pedestal height measurement](/resources/img/pedestal/measure.jpeg)

Once you have measured the pedestal height, you can contribute this information to the PyLabRobot Labware database. Here's a guide on contributing to the open-source project: ["How to Open Source"](/contributor_guide/how-to-open-source.md).

For background, see PR 143: [https://github.com/PyLabRobot/pylabrobot/pull/143](https://github.com/PyLabRobot/pylabrobot/pull/143).



# Using the Visualizer

The Visualizer is a tool that allows you to visualize the a Resource (like LiquidHandler) including its state to easily see what is going on, for example when executing a protocol on a robot or when developing a new protocol.

When using a backend that does not require access to a physical robot, such as the {class}`~pylabrobot.liquid_handling.backends.chatterbox.ChatterboxBackend`, the Visualizer can be used to simulate a robot's behavior. Of course, you may also use the Visualizer when working with a real robot to see what is happening in the PLR resource and state trackers.

## Setting up a connection with the robot

As described in the [basic liquid handling tutorial](basic), we will use the {class}`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler` class to control the robot. This time, however, instead of using the Hamilton {class}`~pylabrobot.liquid_handling.backends.hamilton.STAR.STAR` backend, we are using the software-only {class}`~pylabrobot.liquid_handling.backends.chatterbox.ChatterboxBackend` backend. This means that liquid handling will work exactly the same, but commands are simply printed out to the console instead of being sent to a physical robot. We are still using the same deck.


```python
from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import LiquidHandlerChatterboxBackend
from pylabrobot.visualizer.visualizer import Visualizer
```


```python
from pylabrobot.resources.hamilton import STARLetDeck
```


```python
lh = LiquidHandler(backend=LiquidHandlerChatterboxBackend(), deck=STARLetDeck())
```

Calling {func}`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler.setup` will print out "Setting up the robot" and also that two resources were assigned: the deck and the trash. Other than that, the chatter box backend has no setup to do.


```python
await lh.setup()
```

    Setting up the liquid handler.
    Resource deck was assigned to the liquid handler.
    Resource trash was assigned to the liquid handler.
    Resource trash_core96 was assigned to the liquid handler.


Next, we will create a {class}`~pylabrobot.visualizer.visualizer.Visualizer` object. The Visualizer expects a Resource, and we will pass the {class}`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler` object to it. This will allow us to visualize the robot's state and actions.


```python
vis = Visualizer(resource=lh)
await vis.setup()
```

    Websocket server started at http://127.0.0.1:2121
    File server started at http://127.0.0.1:1337 . Open this URL in your browser.


![The empty simulator](./img/visualizer/empty.png)

## Build the deck layout: Assigning plates and tips

When resources are assigned to the root resource of the Visualizer, in this case `lh`, they will automatically appear in the visualization.


```python
from pylabrobot.resources import (
    TIP_CAR_480_A00,
    PLT_CAR_L5AC_A00,
    Cor_96_wellplate_360ul_Fb,
    HTF
)
```


```python
tip_car = TIP_CAR_480_A00(name='tip carrier')
tip_car[0] = tip_rack1 = HTF(name='tips_01', with_tips=False)
tip_car[1] = tip_rack2 = HTF(name='tips_02', with_tips=False)
tip_car[2] = tip_rack3 = HTF(name='tips_03', with_tips=False)
tip_car[3] = tip_rack4 = HTF(name='tips_04', with_tips=False)
tip_car[4] = tip_rack5 = HTF(name='tips_05', with_tips=False)
```


```python
lh.deck.assign_child_resource(tip_car, rails=15)
```

    Resource tip carrier was assigned to the liquid handler.



```python
plt_car = PLT_CAR_L5AC_A00(name='plate carrier')
plt_car[0] = plate_1 = Cor_96_wellplate_360ul_Fb(name='plate_01')
plt_car[1] = plate_2 = Cor_96_wellplate_360ul_Fb(name='plate_02')
plt_car[2] = plate_3 = Cor_96_wellplate_360ul_Fb(name='plate_03')
```


```python
lh.deck.assign_child_resource(plt_car, rails=8)
```

    Resource plate carrier was assigned to the liquid handler.


![The simulator after the resources have been assigned](./img/visualizer/assignment.png)

### Configuring the state of the deck

As with every PyLabRobot script, you have the option of updating the state of the deck before you actually start your method. This will allow PyLabRobot to keep track of what is going on, enabling features like {func}`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler.return_tips` and catching errors (like missed tips) before a command would be executed on the robot. With the visualizer, this state has the additional effect of updating the visualization.

### Tips

Let's use {func}`~pylabrobot.resources.tip_rack.fill` to place tips at all spots in the tip rack in location `0`.


```python
tip_rack1.fill()
```


You can precisely control the presence of tips using {func}`~pylabrobot.resources.tip_rack.set_tip_state`. This function allows you to set whether there is a tip in each {class}`~pylabrobot.resources.tip_rack.TipSpot`.


```python
tip_rack4 = lh.deck.get_resource("tips_04")
tip_rack4.set_tip_state([True]*48 + [False]*48)
```


```python
tip_rack3.set_tip_state(([True]*8 +[False]*8)*6)
```


```python
tip_rack2.set_tip_state(([True]*16 +[False]*16)*3)
```

### Liquids

Adding liquid to wells works similarly. You can use {func}`~pylabrobot.resources.plate.set_well_liquids` to set the liquid in each well of a plate. Each liquid is represented by a tuple where the first element corresponds to the type of liquid and the second to the volume in uL. Here, `None` is used to designate an unknown liquid.


```python
plate_1_liquids = [[(None, 500)]]*96
plate_1.set_well_liquids(plate_1_liquids)
```


```python
plate_2_liquids = [[(None, 100)], [(None, 500)]]*(96//2)
plate_2.set_well_liquids(plate_2_liquids)
```

In the visualizer, you can see that the opacity of the well is proportional to how full the well is relative to its maximum volume.

![Simulator after the tips have been placed and the volumes adjusted](./img/visualizer/resources.png)

## Liquid handling

Once the layout is complete, you can run the same commands as described in the [basic liquid handling tutorial](basic).

It is important that both tip tracking and volume tracking are enabled globally, so that the visualizer can keep track of the state of the tips and the volumes of the liquids.


```python
from pylabrobot.resources import set_tip_tracking, set_volume_tracking
set_tip_tracking(True), set_volume_tracking(True)
```




    (None, None)



### Picking up tips

Note that since we are using the {class}`~pylabrobot.liquid_handling.backends.chatterbox.ChatterboxBackend`, we just print out "Picking up tips" instead of actually performing an operation. The visualizer will show the tips being picked up.


```python
await lh.pick_up_tips(tip_rack1["A1", "B2", "C3", "D4"])
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tips_01_tipspot_0_0  0,0,0            HamiltonTip  1065             8                    95.1             Yes       
      p1: tips_01_tipspot_1_1  0,0,0            HamiltonTip  1065             8                    95.1             Yes       
      p2: tips_01_tipspot_2_2  0,0,0            HamiltonTip  1065             8                    95.1             Yes       
      p3: tips_01_tipspot_3_3  0,0,0            HamiltonTip  1065             8                    95.1             Yes       



```python
await lh.drop_tips(tip_rack1["A1", "B2", "C3", "D4"])
```

    Dropping tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tips_01_tipspot_0_0  0,0,0            HamiltonTip  1065             8                    95.1             Yes       
      p1: tips_01_tipspot_1_1  0,0,0            HamiltonTip  1065             8                    95.1             Yes       
      p2: tips_01_tipspot_2_2  0,0,0            HamiltonTip  1065             8                    95.1             Yes       
      p3: tips_01_tipspot_3_3  0,0,0            HamiltonTip  1065             8                    95.1             Yes       


### Aspirating and dispensing


```python
await lh.pick_up_tips(tip_rack1["A1"])
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tips_01_tipspot_0_0  0,0,0            HamiltonTip  1065             8                    95.1             Yes       



```python
await lh.aspirate(plate_1["A2"], vols=[200])
```

    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 200.0    plate_01_well_1_0    0,0,0            None       None       None       



```python
await lh.dispense(plate_2["A1"], vols=[200])
```

    Dispensing:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 200.0    plate_02_well_0_0    0,0,0            None       None       None       



```python
await lh.return_tips()
```

    Dropping tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tips_01_tipspot_0_0  0,0,0            HamiltonTip  1065             8                    95.1             Yes       


### Aspirating using CoRe 96

The CoRe 96 head supports liquid handling operations for 96 channels at once. Here's how to use:

- {func}`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler.pick_up_tips96` for picking up 96 tips;
- {func}`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler.aspirate96` for aspirating liquid from an entire plate at once;
- {func}`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler.dispense96` for dispensing liquid to an entire plate at once;
- {func}`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler.drop_tips96` for dropping tips to the tip rack.



```python
await lh.pick_up_tips96(tip_rack1)
```

    Picking up tips from tips_01.



```python
await lh.aspirate96(plate_1, volume=100)
```

    Aspirating 100.0 from Plate(name=plate_01, size_x=127.76, size_y=85.48, size_z=14.2, location=(000.000, 000.000, -03.030)).



```python
await lh.dispense96(plate_3, volume=100)
```

    Dispensing 100.0 to Plate(name=plate_03, size_x=127.76, size_y=85.48, size_z=14.2, location=(000.000, 000.000, -03.030)).



```python
await lh.drop_tips96(tip_rack1)
```

    Dropping tips to tips_01.


![The simulator after the liquid handling operations completed](./img/visualizer/after_lh.png)

## Shutting down

When you're done, you can stop the visualizer by calling {func}`~pylabrobot.visualizer.visualizer.Visualizer.stop`. This will stop the visualization.


```python
await vis.stop()
```



# Heater Shakers

Heater-shakers are a hybrid of {class}`~pylabrobot.temperature_controllers.temperature_controller.TemperatureController` and {class}`~pylabrobot.shakers.shaker.Shaker`. They are used to control the temperature of a sample while shaking it.

PyLabRobot supports the following heater shakers:

- Inheco ThermoShake RM (tested)
- Inheco ThermoShake (should have the same API as RM)
- Inheco ThermoShake AC (should have the same API as RM)

Heater-shakers are controlled by the {class}`~pylabrobot.heating_shaking.heater_shaker.HeaterShaker` class. This class takes a backend as an argument. The backend is responsible for communicating with the scale and is specific to the hardware being used.


```python
from pylabrobot.heating_shaking import HeaterShaker
from pylabrobot.heating_shaking import InhecoThermoShake
```


```python
backend = InhecoThermoShake()  # take any ScaleBackend you want
hs = HeaterShaker(backend=backend, name="HeaterShaker", size_x=0, size_y=0, size_z=0)
await hs.setup()
```

The {meth}`~pylabrobot.heating_shaking.heater_shaker.HeaterShaker.setup` method is used to initialize the scale. This is where the backend will connect to the scale and perform any necessary initialization.

The {class}`~pylabrobot.heating_shaking.heater_shaker.HeaterShaker` class has a number of methods for controlling the temperature and shaking of the sample. These are inherited from the {class}`~pylabrobot.temperature_controllers.temperature_controller.TemperatureController` and {class}`~pylabrobot.shakers.shaker.Shaker` classes.

- {meth}`~pylabrobot.heating_shaking.heater_shaker.HeaterShaker.set_temperature`: Set the temperature of the module.
- {meth}`~pylabrobot.heating_shaking.heater_shaker.HeaterShaker.get_temperature`: Get the current temperature of the module.
- {meth}`~pylabrobot.heating_shaking.heater_shaker.HeaterShaker.shake`: Set the shaking speed of the module.
- {meth}`~pylabrobot.heating_shaking.heater_shaker.HeaterShaker.stop_shaking`: Stop the shaking of the module.

=(InhecoThermoShake)

## Inheco ThermoShake

The Inheco ThermoShaker heater shaker is controlled by the {class}`~pylabrobot.heating_shaking.heater_shaker.InhecoThermoShake` class. This heater shaker connects using a USB-B cable.

We will reuse the same `hs` as above:

```python
backend = InhecoThermoShake() # take any ScaleBackend you want
hs = HeaterShaker(backend=backend, name="HeaterShaker", size_x=0, size_y=0, size_z=0)
await hs.setup()
```

Shake indefinitely:


```python
await hs.shake(speed=100)  # speed in rpm
```

Shake for 10 seconds:


```python
await hs.shake(speed=100, duration=10)  # speed in rpm, duration in seconds
```

Get the current temperature:


```python
await hs.get_temperature()  # get current temperature
```




    23.2



Set the temperature to 37&deg;C:


```python
await hs.set_temperature(37)  # temperature in degrees C
```



# Cytation 5


```python
%load_ext autoreload
%autoreload 2
```


```python
import matplotlib.pyplot as plt
from pylabrobot.plate_reading import ImageReader, Cytation5Backend, ImagingMode
```


```python
# for imaging, we need an environment variable to point to the Spinnaker GenTL file
import os
os.environ["SPINNAKER_GENTL64_CTI"] = "/usr/local/lib/spinnaker-gentl/Spinnaker_GenTL.cti"
```


```python
import logging
logger = logging.getLogger("pylabrobot.plate_reading.biotek")
logger.setLevel(logging.DEBUG)
```


```python
pr = ImageReader(name="PR", size_x=0,size_y=0,size_z=0, backend=Cytation5Backend())
await pr.setup(use_cam=True)
```


```python
await pr.backend.get_firmware_version()
```




    '1320200  Version 2.07'




```python
await pr.backend.get_current_temperature()
```




    23.5




```python
await pr.open()
```


```python
await pr.close()
```

## Plate reading


```python
data = await pr.read_absorbance(wavelength=434)
plt.imshow(data)
```




    <matplotlib.image.AxesImage at 0x1353cc790>




    
![png](cytation5.ipynb_files/cytation5.ipynb_11_1.png)
    



```python
data = await pr.read_fluorescence(
  excitation_wavelength=485, emission_wavelength=528, focal_height=7.5
)
plt.imshow(data)
```




    <matplotlib.image.AxesImage at 0x16e144850>




    
![png](cytation5.ipynb_files/cytation5.ipynb_12_1.png)
    



```python
data = await pr.read_luminescence(focal_height=4.5)
plt.imshow(data)
```




    <matplotlib.image.AxesImage at 0x16e1a83d0>




    
![png](cytation5.ipynb_files/cytation5.ipynb_13_1.png)
    


## Shaking


```python
await pr.backend.shake(shake_type=Cytation5Backend.ShakeType.LINEAR)
```


```python
await pr.backend.stop_shaking()
```

## Imaging

### Installation

See [Cytation 5 imager installation instructions](https://docs.pylabrobot.org/user_guide/installation.html#cytation5-imager).

### Usage


```python
im = await pr.capture(
  well=(1, 1),
  mode=ImagingMode.BRIGHTFIELD,
  focal_height="auto",  # PLR supports auto-focus
  exposure_time=5,
  gain=10
)
plt.imshow(im, cmap="gray", vmin=0, vmax=255)
```




    <matplotlib.image.AxesImage at 0x177fe1880>




    
![png](cytation5.ipynb_files/cytation5.ipynb_20_1.png)
    



```python
im = await pr.capture(
  well=(1, 1),
  mode=ImagingMode.PHASE_CONTRAST,
  focal_height=3,
  exposure_time=12,
  gain=24
)
plt.imshow(im, cmap="gray", vmin=0, vmax=255)
```




    <matplotlib.image.AxesImage at 0x345a47a60>




    
![png](cytation5.ipynb_files/cytation5.ipynb_21_1.png)
    



```python
await pr.backend.set_gain(24)
im = await pr.capture(
  well=(1, 1),
  mode=ImagingMode.GFP,
  focal_height=3,
  exposure_time=1904
)
plt.imshow(im, cmap="viridis", vmin=0, vmax=255)
```




    <matplotlib.image.AxesImage at 0x3459d5d90>




    
![png](cytation5.ipynb_files/cytation5.ipynb_22_1.png)
    



```python
im = await pr.capture(
  well=(1, 1),
  mode=ImagingMode.TEXAS_RED,
  focal_height=3,
  exposure_time=1904
)
plt.imshow(im, cmap="gray", vmin=0, vmax=255)
```




    <matplotlib.image.AxesImage at 0x14c7d7880>




    
![png](cytation5.ipynb_files/cytation5.ipynb_23_1.png)
    



```python
import time
import numpy as np

exposure_time = 1904

# first time setting imaging mode is slower
_ = await pr.capture(well=(1, 1), mode=ImagingMode.BRIGHTFIELD, focal_height=3.3, exposure_time=exposure_time)

l = []
for i in range(10):
  t0 = time.monotonic_ns()
  _ = await pr.capture(well=(1, 1), mode=ImagingMode.BRIGHTFIELD, focal_height=3.3, exposure_time=exposure_time)
  t1 = time.monotonic_ns()
  l.append((t1 - t0) / 1e6)

print(f"{np.mean(l):.2f} ms ± {np.std(l):.2f} ms")
print(f"Overhead: {(np.mean(l) - exposure_time):.2f} ms")
```

    2089.59 ms ± 15.72 ms
    Overhead: 185.59 ms




# Scales

PyLabRobot supports the following scales:

- Mettler Toledo WXS205SDU

Scales are controlled by the {class}`~pylabrobot.scales.scale.Scale` class. This class takes a backend as an argument. The backend is responsible for communicating with the scale and is specific to the hardware being used.


```python
from pylabrobot.scales import Scale
from pylabrobot.scales.mettler_toledo import MettlerToledoWXS205SDU
```


```python
backend = MettlerToledoWXS205SDU(port="/dev/cu.usbserial-110")  # take any ScaleBackend you want
scale = Scale(backend=backend, size_x=0, size_y=0, size_z=0)
await scale.setup()
```

The {meth}`~pylabrobot.scales.scale.Scale.setup` method is used to initialize the scale. This is where the backend will connect to the scale and perform any necessary initialization.

The {class}`~pylabrobot.scales.scale.Scale` class has a number of methods for controlling the scale and reading measurements. These are:

- {meth}`~pylabrobot.scales.scale.Scale.get_weight`: Read the weight from the scale in grams.
- {meth}`~pylabrobot.scales.scale.Scale.tare`: Tare the scale.
- {meth}`~pylabrobot.scales.scale.Scale.zero`: Zero the scale.

=(MettlerToledoWXS205SDU)

## Mettler Toledo WXS205SDU

The Mettler Toledo XS205 scale is controlled by the {class}`~pylabrobot.scales.mettler_toledo.MettlerToledoWXS205SDU` class. This scale is used by the Hamilton Liquid Verification Kit (LVK).

The scale comes with an RS-232 serial port. You'll probably want to use a USB to serial adapter to connect it to your computer. Any $10 generic USB to serial adapter should work (e.g. something that uses FTDI).

Note that this scale has a 'warm-up' time after plugging in that is documented as 60-90 minutes by Mettler Toledo depending on the document you like at. In our experience, 30 minutes is sufficient. If you try to take a measurement before this time, you will likely get a "Command understood but currently not executable (balance is currently executing another command)" error. Sometimes plugging the power cord in and out will make things work faster.


```python
backend = MettlerToledoWXS205SDU(port="/dev/cu.usbserial-110")
scale = Scale(backend=backend)
await scale.setup()
await scale.get_weight(timeout="stable")
```




    0.00148



This scale provides various timeouts:

- `"stable"`: The time to wait for the scale to stabilize before reading the weight. Note that this may take a very long time to finish if the scale cannot take a stable reading. If you're not using the air enclosure, even being near the scale can cause it to never stabilize.
- 0: Read the value immediately
- n>0: Try to get a stable value for n seconds. If the value is stable before n seconds, return it immediately. Otherwise, return the value after n seconds.

These parameters are available for {meth}`~pylabrobot.scales.mettler_toledo.MettlerToledoWXS205SDU.get_weight`, {meth}`~pylabrobot.scales.mettler_toledo.MettlerToledoWXS205SDU.tare`, and {meth}`~pylabrobot.scales.mettler_toledo.MettlerToledoWXS205SDU.zero`.


```python
await scale.get_weight(timeout=0)
```




    0.00148



## Example: getting timing information

Let's say you wanted to determine how long it takes to take a measurement. In PyLabRobot, this is easy:


```python
backend = MettlerToledoWXS205SDU(port="/dev/cu.usbserial-110")
s = Scale(backend=backend)
await s.setup()
```


```python
import time
import numpy as np

l = []
for i in range(10):
  t0 = time.monotonic_ns()
  await scale.get_weight(timeout="stable")
  t1 = time.monotonic_ns()
  l.append((t1 - t0) / 1e6)

print(f"{np.mean(l):.2f} ms ± {np.std(l):.2f} ms")
```

    100.44 ms ± 6.78 ms




# Looping through tip racks

In the `pylabrobot.resources.functional` module, we have utilities for looping through tip spots in one or more tip racks. They support caching to disk, so that you can resume where you left off if your script is interrupted.


```python
# instantiate some hamilton tip racks as an example
from pylabrobot.resources.hamilton import HT # an example tip rack
tip_rack_0 = HT(name='tip_rack_0')
tip_rack_1 = HT(name='tip_rack_1')
tip_racks = [tip_rack_0, tip_rack_1]
```

Tip spot generators take a list of tip spots (`list[TipSpot]`) as an argument. With `F.get_all_tip_spots`, you can get all tip spots in one or more tip racks. The tip spots will be column-first, i.e. the first tip spot is the top left corner, the second tip spot is the one below it, and so on.


```python
import pylabrobot.resources.functional as F
tip_spots = F.get_all_tip_spots(tip_racks)
tip_spots[0]
```




    TipSpot(name=tip_rack_0_tipspot_0_0, location=Coordinate(007.200, 068.300, -83.500), size_x=9.0, size_y=9.0, size_z=0, category=tip_spot)



## Basic linear generator 

The linear generator will loop through all tip spots in the order they are given, with the option to repeat.


```python
linear_generator = F.linear_tip_spot_generator(
  tip_spots=tip_spots,                      # the list of tip spots to use
  cache_file_path="./linear_cache.json",    # load/save tip spot cache for state in between runs
  repeat=False,                             # don't repeat the tip spots if they run out
)
```

Tip spot generators are asynchronous, so use `await` and `__anext__` to get the next tip spot.


```python
await linear_generator.__anext__()
```




    TipSpot(name=tip_rack_0_tipspot_0_0, location=Coordinate(007.200, 068.300, -83.500), size_x=9.0, size_y=9.0, size_z=0, category=tip_spot)



To get multiple tip spots, call `__anext__` multiple times.


```python
N = 3
tip_spots = [await linear_generator.__anext__() for _ in range(N)]
[ts.name for ts in tip_spots]
```




    ['tip_rack_0_tipspot_0_1', 'tip_rack_0_tipspot_0_2', 'tip_rack_0_tipspot_0_3']



Save the state of the generator at an arbitrary point by calling `save_state`. This method will be called automatically when the program crashes or is interrupted.


```python
linear_generator.save_state()
```

Override the index by calling `set_index`.


```python
linear_generator.set_index(12)
await linear_generator.__anext__()
```




    TipSpot(name=tip_rack_0_tipspot_1_4, location=Coordinate(016.200, 032.300, -83.500), size_x=9.0, size_y=9.0, size_z=0, category=tip_spot)



## Randomized generator

The randomized generator will loop through all tip spots in a random order, with the option to repeat. If repeating, set the parameter `K` to not sample a tip spot that has been sampled in the last `K` samples.


```python
random_generator = F.randomized_tip_spot_generator(
  tip_spots=tip_spots,                      # the list of tip spots to use
  cache_file_path="./random_cache.json",    # load/save tip spot cache for state in between runs
  K=10,                                     # don't sample tip spots that have been used in the last K samples
)
```


```python
await random_generator.__anext__()
```




    TipSpot(name=tip_rack_0_tipspot_0_3, location=Coordinate(007.200, 041.300, -83.500), size_x=9.0, size_y=9.0, size_z=0, category=tip_spot)





# Using trackers

Trackers in PyLabRobot are objects that keep track of the state of the deck throughout a protocol. Two types of trackers currently exist: tip trackers (tracking the presence of tips in tip racks and on the pipetting channels) and volume trackers (tracking the volume in pipetting tips and wells).


```python
from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend
from pylabrobot.resources import (
  TIP_CAR_480_A00,
  HTF,
  PLT_CAR_L5AC_A00,
  Cor_96_wellplate_360ul_Fb,
  set_tip_tracking,
  set_volume_tracking
)
from pylabrobot.resources.hamilton import STARLetDeck

lh = LiquidHandler(backend=LiquidHandlerChatterboxBackend(num_channels=8), deck=STARLetDeck())
await lh.setup()
```

    Setting up the liquid handler.
    Resource deck was assigned to the liquid handler.
    Resource trash was assigned to the liquid handler.
    Resource trash_core96 was assigned to the liquid handler.



```python
tip_carrier = TIP_CAR_480_A00(name="tip carrier") # initialize a tip carrier
```


```python
plt_carrier = PLT_CAR_L5AC_A00(name="plate carrier") # initialize a plate carrier
```

We enable tip and volume tracking globally using the `set_volume_tracking` and `set_tip_tracking` methods.


```python
set_volume_tracking(enabled=True)
set_tip_tracking(enabled=True)
```

## Tip trackers

The tip tracker is a simple class that keeps track of the current tip, and the previous operations that have been performed on an object. This enables features like {meth}`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler.return_tips` and automated tip type detection.

### Initializing tip racks

Whether or not tip tracking is turned on, spots on a tip rack initialize with a tip tracker that defaults to having a tip. The tip tracker only comes into play with performing operations.


```python
tip_carrier[0] = tip_rack = HTF(name="tip rack")
```


```python
tip_rack.get_item("A1").tracker.has_tip
```




    True



To initialize a tip rack without tips, pass `with_tips=False`:


```python
tip_carrier[1] = empty_tip_rack = HTF(name="empty tip rack", with_tips=False)
```


```python
empty_tip_rack.get_item("A1").tracker.has_tip
```




    False



To "empty" a tip rack after initialization, use the {meth}`~pylabrobot.resources.TipRack.empty()` method. To "fill" a tip rack after initialization, use the {meth}`~pylabrobot.resources.TipRack.fill()` method.


```python
empty_tip_rack.fill()
empty_tip_rack.get_item("A1").tracker.has_tip
```




    True




```python
empty_tip_rack.empty()
empty_tip_rack.get_item("A1").tracker.has_tip
```




    False




```python
lh.deck.assign_child_resource(tip_carrier, rails=3)
```

    Resource tip carrier was assigned to the liquid handler.


### Tip tracker errors

The tip tracker is most useful for catching hardware errors before they happen. With tip tracking turned on, the following errors can be raised:


```python
from pylabrobot.resources.errors import HasTipError, NoTipError
```

#### `NoTipError` when picking up a tip

This error is raised when the tip tracker is trying to access a spot that has no tip.


```python
await lh.pick_up_tips(tip_rack[0])
await lh.drop_tips(empty_tip_rack[0])

try:
  await lh.pick_up_tips(tip_rack[0])
except NoTipError as e:
  print("As expected:", e)
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tip rack_tipspot_0_0 0,0,0            HamiltonTip  1065             8                    95.1             Yes       
    Dropping tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: empty tip rack_tipspot_0_0 0,0,0            HamiltonTip  1065             8                    95.1             Yes       
    As expected: Tip spot does not have a tip.


#### `HasTipError` when dropping a tip

This error is raised when the tip tracker is trying to access a spot that has a tip.


```python
await lh.pick_up_tips(tip_rack[1])

try:
  await lh.drop_tips(empty_tip_rack[0])
except HasTipError as e:
  print("As expected:", e)
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tip rack_tipspot_0_1 0,0,0            HamiltonTip  1065             8                    95.1             Yes       
    As expected: Tip spot already has a tip.



```python
await lh.drop_tips(empty_tip_rack[1])
```

    Dropping tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: empty tip rack_tipspot_0_1 0,0,0            HamiltonTip  1065             8                    95.1             Yes       


#### `NoTipError` when dropping a tip

This error is raised when the tip tracker is trying to use a channel that has no tip.


```python
try:
  await lh.drop_tips(empty_tip_rack[2])
except NoTipError as e:
  print("As expected:", e)
```

    As expected: Channel 0 does not have a tip.


#### `HasTipError` when picking up a tip

This error is raised when the tip tracker is trying to use a channel that has a tip.


```python
await lh.pick_up_tips(tip_rack[2])

try:
  await lh.pick_up_tips(tip_rack[3])
except HasTipError as e:
  print("As expected:", e)
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tip rack_tipspot_0_2 0,0,0            HamiltonTip  1065             8                    95.1             Yes       
    As expected: Channel has tip


### Disabling the tip tracker

The tip tracker can be disabled in three different ways, depending on the desired behavior.

#### Using a context manager

The {meth}`pylabrobot.resources.no_tip_tracking` context manager can be used to disable the tip tracker for a set of operations.

Note that we use the {meth}`pylabrobot.liquid_handling.liquid_handler.LiquidHandler.clear_head_state` method to forget the tips that are currently mounted on the channels. This is needed because even though the tip tracker is disabled, the channels still keep track of the tips that are mounted on them.


```python
lh.clear_head_state()
```


```python
from pylabrobot.resources import no_tip_tracking

with no_tip_tracking():
  await lh.pick_up_tips(tip_rack[4])
  await lh.pick_up_tips(tip_rack[4], use_channels=[1]) # no error
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tip rack_tipspot_0_4 0,0,0            HamiltonTip  1065             8                    95.1             Yes       
    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p1: tip rack_tipspot_0_4 0,0,0            HamiltonTip  1065             8                    95.1             Yes       


#### For a single tip spot

The tip tracker can be disabled for a single object by calling {meth}`pylabrobot.resources.tip_tracker.TipTracker.disable()` on the tracker object.


```python
lh.clear_head_state()
```


```python
tip_rack.get_item(5).tracker.disable()

await lh.pick_up_tips(tip_rack[5])
await lh.pick_up_tips(tip_rack[5], use_channels=[1]) # no error

tip_rack.get_item(5).tracker.enable()
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tip rack_tipspot_0_5 0,0,0            HamiltonTip  1065             8                    95.1             Yes       
    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p1: tip rack_tipspot_0_5 0,0,0            HamiltonTip  1065             8                    95.1             Yes       


### For a single tip rack

Disable the tip tracker for a single tip rack by calling {meth}`pylabrobot.resources.TipRack.disable_tip_trackers()` and {meth}`pylabrobot.resources.TipRack.enable_tip_trackers()` on the tip rack object.


```python
lh.clear_head_state()
```


```python
tip_rack.disable_tip_trackers()

await lh.pick_up_tips(tip_rack[5])
await lh.pick_up_tips(tip_rack[5], use_channels=[1]) # no error

tip_rack.enable_tip_trackers()
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tip rack_tipspot_0_5 0,0,0            HamiltonTip  1065             8                    95.1             Yes       
    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p1: tip rack_tipspot_0_5 0,0,0            HamiltonTip  1065             8                    95.1             Yes       


#### Globally

The tip tracker can be disabled globally by using {meth}`pylabrobot.resources.set_tip_tracking`.


```python
lh.clear_head_state()
```


```python
from pylabrobot.resources import set_tip_tracking

set_tip_tracking(enabled=False)

await lh.pick_up_tips(tip_rack[6])
await lh.pick_up_tips(tip_rack[6], use_channels=[1]) # no error

set_tip_tracking(enabled=True)
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tip rack_tipspot_0_6 0,0,0            HamiltonTip  1065             8                    95.1             Yes       
    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p1: tip rack_tipspot_0_6 0,0,0            HamiltonTip  1065             8                    95.1             Yes       


## Volume trackers

The volume tracker is a simple class that keeps track of the current volume, and the previous operations that have been performed on an object. This enables features like automated liquid class selection in STAR, and raises errors before they happen on the robot.

### Initializing wells

Wells automatically initialize with a volume tracker that defaults to having no volume.


```python
plt_carrier[0] = plate = Cor_96_wellplate_360ul_Fb(name="plate")
```


```python
plate.get_item("A1").tracker.get_used_volume()
```




    0




```python
plate.get_item("A1").tracker.get_free_volume()
```




    360




```python
from pylabrobot.resources.liquid import Liquid
```


```python
plate.get_item("A1").tracker.set_liquids([(Liquid.WATER, 10)])
plate.get_item("A1").tracker.get_used_volume(), plate.get_item("A1").tracker.get_free_volume()
```




    (10, 350)




```python
lh.deck.assign_child_resource(plt_carrier, rails=9)
```

    Resource plate carrier was assigned to the liquid handler.


### Inspecting volume tracker operation history


```python
await lh.aspirate(plate["A1"], vols=[10])
plate.get_item("A1").tracker.get_used_volume(), plate.get_item("A1").tracker.get_free_volume()
```

    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 10.0     plate_well_0_0       0,0,0            None       None       None       





    (0, 360)




```python
await lh.dispense(plate["A1"], vols=[10])
plate.get_item("A1").tracker.get_used_volume(), plate.get_item("A1").tracker.get_free_volume()
```

    Dispensing:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 10.0     plate_well_0_0       0,0,0            None       None       None       





    (10, 350)



### Volume tracker errors


```python
from pylabrobot.resources.volume_tracker import TooLittleLiquidError, TooLittleVolumeError
```

#### `TooLittleLiquidError` when dispensing

This error is raised when the volume tracker is trying to dispense from a tip that has less liquid than the requested volume.


```python
try:
  await lh.dispense(plate["A1"], vols=[100]) # this is less liquid than is currently in the tip
except TooLittleLiquidError as e:
  print("As expected:", e)
```

    As expected: Tracker only has 0uL


#### `TooLittleVolumeError` when aspirating

This error is raised when the volume tracker is trying to aspirate from a tip that has less free volume than the requested volume.


```python
lh.clear_head_state()
await lh.pick_up_tips(tip_rack[8])
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tip rack_tipspot_1_0 0,0,0            HamiltonTip  1065             8                    95.1             Yes       



```python
# fill the first two columns
for i in range(16):
  plate.get_item(i).tracker.set_liquids([(Liquid.WATER, 100)])

try:
  # aspirate from the first two columns - this is more liquid than the tip can hold
  for i in range(16):
    await lh.aspirate(plate[i], vols=[100])
except TooLittleVolumeError as e:
  print("As expected:", e)
```

    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_0       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_1       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_2       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_3       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_4       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_5       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_6       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_7       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_1_0       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_1_1       0,0,0            None       None       None       
    As expected: Container has too little volume: 100uL > 65uL.


#### `TooLittleLiquidError` when aspirating

This error is raised when trying to dispense into a well that has less free volume than the requested volume.


```python
try:
  await lh.aspirate(plate["A1"], vols=[100]) # this is less liquid than is currently in the well
except TooLittleLiquidError as e:
  print("As expected:", e)
```

    As expected: Tracker only has 0uL


#### `TooLittleVolumeError` when dispensing

This error is raised when trying to aspirate from a well that has less liquid than the requested volume.


```python
lh.clear_head_state()
await lh.pick_up_tips(tip_rack[9])
```

    Picking up tips:
    pip#  resource             offset           tip type     max volume (µL)  fitting depth (mm)   tip length (mm)  filter    
      p0: tip rack_tipspot_1_1 0,0,0            HamiltonTip  1065             8                    95.1             Yes       



```python
# fill the first column
for i in range(8):
  plate.get_item(i).tracker.set_liquids([(Liquid.WATER, 100)])

try:
  # aspirate liquid from the first column into the first well
  for i in range(1, 8):
    await lh.aspirate(plate[i], vols=[100])
    await lh.dispense(plate["A1"], vols=[100])
except TooLittleVolumeError as e:
  print("As expected:", e)
```

    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_1       0,0,0            None       None       None       
    Dispensing:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_0       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_2       0,0,0            None       None       None       
    Dispensing:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_0       0,0,0            None       None       None       
    Aspirating:
    pip#  vol(ul)  resource             offset           flow rate  blowout    lld_z       
      p0: 100.0    plate_well_0_3       0,0,0            None       None       None       
    As expected: Container has too little volume: 100uL > 60uL.




# Getting started with liquid handling on a Hamilton STAR(let)

In this notebook, you will learn how to use PyLabRobot to move water from one range of wells to another.

**Note: before running this notebook, you should have**:

- Installed PyLabRobot and the USB driver as described in [the installation guide](/user_guide/installation).
- Connected the Hamilton to your computer using the USB cable.

Video of what this code does:

<iframe width="640" height="360" src="https://www.youtube.com/embed/NN6ltrRj3bU" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>

## Setting up a connection with the robot

Start by importing the {class}`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler` class, which will serve as a front end for all liquid handling operations.

Backends serve as communicators between `LiquidHandler`s and the actual hardware. Since we are using a Hamilton STAR, we also import the {class}`~pylabrobot.liquid_handling.backends.STAR` backend.


```python
%load_ext autoreload
%autoreload 2
```


```python
from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import STAR
```

In addition, import the {class}`~pylabrobot.resources.hamilton.STARLetDeck`, which represents the deck of the Hamilton STAR.


```python
from pylabrobot.resources.hamilton import STARLetDeck
```

Create a new liquid handler using `STAR` as its backend.


```python
backend = STAR()
lh = LiquidHandler(backend=backend, deck=STARLetDeck())
```

The final step is to open communication with the robot. This is done using the {func}`~pylabrobot.liquid_handling.LiquidHandler.setup` method.


```python
await lh.setup()
```

## Creating the deck layout

Now that we have a `LiquidHandler` instance, we can define the deck layout.

The layout in this tutorial will contain five sets of standard volume tips with filter, 1 set of 96 1mL wells, and tip and plate carriers on which these resources are positioned.

Start by importing the relevant objects and variables from the PyLabRobot package. This notebook uses the following resources:

- {class}`~pylabrobot.resources.hamilton.tip_carriers.TIP_CAR_480_A00` tip carrier
- {class}`~pylabrobot.resources.hamilton.plate_carriers.PLT_CAR_L5AC_A00` plate carrier
- {class}`~pylabrobot.resources.corning_costar.plates.Cor_96_wellplate_360ul_Fb` wells
- {class}`~pylabrobot.resources.hamilton.tip_racks.HTF` tips


```python
from pylabrobot.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  Cor_96_wellplate_360ul_Fb,
  HTF,
)
```

Then create a tip carrier named `tip carrier`, which will contain tip rack at all 5 positions. These positions can be accessed using `tip_car[x]`, and are 0 indexed.


```python
tip_car = TIP_CAR_480_A00(name="tip carrier")
tip_car[0] = HTF(name="tips_01")
```

Use {func}`~pylabrobot.resources.abstract.assign_child_resources` to assign the tip carrier to the deck of the liquid handler. All resources contained by this carrier will be assigned automatically.

In the `rails` parameter, we can pass the location of the tip carrier. The locations of the tips will automatically be calculated.


```python
lh.deck.assign_child_resource(tip_car, rails=3)
```

Repeat this for the plates.


```python
plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
plt_car[0] = Cor_96_wellplate_360ul_Fb(name="plate_01")
```


```python
lh.deck.assign_child_resource(plt_car, rails=15)
```

Let's look at a summary of the deck layout using {func}`~pylabrobot.liquid_handling.LiquidHandler.summary`.


```python
lh.summary()
```

    Rail     Resource                   Type                Coordinates (mm)
    ===============================================================================================
    (3)  ├── tip carrier                TipCarrier          (145.000, 063.000, 100.000)
         │   ├── tips_01                TipRack             (162.900, 145.800, 131.450)
         │   ├── <empty>
         │   ├── <empty>
         │   ├── <empty>
         │   ├── <empty>
         │
    (15) ├── plate carrier              PlateCarrier        (415.000, 063.000, 100.000)
         │   ├── plate_01               Plate               (433.000, 146.000, 187.150)
         │   ├── <empty>
         │   ├── <empty>
         │   ├── <empty>
         │   ├── <empty>
         │
    (32) ├── trash                      Trash               (800.000, 190.600, 137.100)
    


## Picking up tips

Picking up tips is as easy as querying the tips from the tiprack.


```python
tiprack = lh.deck.get_resource("tips_01")
await lh.pick_up_tips(tiprack["A1:C1"])
```

    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Sent command: C0TTid0004tt01tf1tl0871tv12500tg3tu0
    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Received response: C0TTid0004er00/00
    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Sent command: C0TPid0005xp01629 01629 01629 00000&yp1458 1368 1278 0000&tm1 1 1 0&tt01tp2244tz2164th2450td0
    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Received response: C0TPid0005er00/00


## Aspirating and dispensing

Aspirating and dispensing work similarly to picking up tips: where you use booleans to specify which tips to pick up, with aspiration and dispensing you use floats to specify the volume to aspirate or dispense in $\mu L$.

The cells below move liquid from wells `'A1:C1'` to `'D1:F1'` using channels 1, 2, and 3 using the {func}`~pylabrobot.liquid_handling.LiquidHandler.aspirate` and {func}`~pylabrobot.liquid_handling.LiquidHandler.dispense` methods.


```python
plate = lh.deck.get_resource("plate_01")
await lh.aspirate(plate["A1:C1"], vols=[100.0, 50.0, 200.0])
```

    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Sent command: C0ASid0006at0&tm1 1 1 0&xp04330 04330 04330 00000&yp1460 1370 1280 0000&th2450te2450lp1931 1931 1931&ch000 000 000&zl1881 1881 1881&po0100 0100 0100&zu0032 0032 0032&zr06180 06180 06180&zx1831 1831 1831&ip0000 0000 0000&it0 0 0&fp0000 0000 0000&av01072 00551 02110&as1000 1000 1000&ta000 000 000&ba0000 0000 0000&oa000 000 000&lm0 0 0&ll1 1 1&lv1 1 1&zo000 000 000&ld00 00 00&de0020 0020 0020&wt10 10 10&mv00000 00000 00000&mc00 00 00&mp000 000 000&ms1000 1000 1000&mh0000 0000 0000&gi000 000 000&gj0gk0lk0 0 0&ik0000 0000 0000&sd0500 0500 0500&se0500 0500 0500&sz0300 0300 0300&io0000 0000 0000&il00000 00000 00000&in0000 0000 0000&
    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Received response: C0ASid0006er00/00


After the liquid has been aspirated, dispense it in the wells below. Note that while we specify different wells, we are still using the same channels. This is needed because only these channels contain liquid, of course.


```python
await lh.dispense(plate["D1:F1"], vols=[100.0, 50.0, 200.0])
```

    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Sent command: C0DSid0007dm2 2 2&tm1 1 1 0&xp04330 04330 04330 00000&yp1190 1100 1010 0000&zx1871 1871 1871&lp2321 2321 2321&zl1881 1881 1881&po0100 0100 0100&ip0000 0000 0000&it0 0 0&fp0000 0000 0000&zu0032 0032 0032&zr06180 06180 06180&th2450te2450dv01072 00551 02110&ds1200 1200 1200&ss0050 0050 0050&rv000 000 000&ta000 000 000&ba0000 0000 0000&lm0 0 0&dj00zo000 000 000&ll1 1 1&lv1 1 1&de0020 0020 0020&wt00 00 00&mv00000 00000 00000&mc00 00 00&mp000 000 000&ms0010 0010 0010&mh0000 0000 0000&gi000 000 000&gj0gk0
    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Received response: C0DSid0007er00/00


Let's move the liquid back to the original wells.


```python
await lh.aspirate(plate["D1:F1"], vols=[100.0, 50.0, 200.0])
await lh.dispense(plate["A1:C1"], vols=[100.0, 50.0, 200.0])
```

    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Sent command: C0ASid0008at0&tm1 1 1 0&xp04330 04330 04330 00000&yp1190 1100 1010 0000&th2450te2450lp1931 1931 1931&ch000 000 000&zl1881 1881 1881&po0100 0100 0100&zu0032 0032 0032&zr06180 06180 06180&zx1831 1831 1831&ip0000 0000 0000&it0 0 0&fp0000 0000 0000&av01072 00551 02110&as1000 1000 1000&ta000 000 000&ba0000 0000 0000&oa000 000 000&lm0 0 0&ll1 1 1&lv1 1 1&zo000 000 000&ld00 00 00&de0020 0020 0020&wt10 10 10&mv00000 00000 00000&mc00 00 00&mp000 000 000&ms1000 1000 1000&mh0000 0000 0000&gi000 000 000&gj0gk0lk0 0 0&ik0000 0000 0000&sd0500 0500 0500&se0500 0500 0500&sz0300 0300 0300&io0000 0000 0000&il00000 00000 00000&in0000 0000 0000&
    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Received response: C0ASid0008er00/00
    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Sent command: C0DSid0009dm2 2 2&tm1 1 1 0&xp04330 04330 04330 00000&yp1460 1370 1280 0000&zx1871 1871 1871&lp2321 2321 2321&zl1881 1881 1881&po0100 0100 0100&ip0000 0000 0000&it0 0 0&fp0000 0000 0000&zu0032 0032 0032&zr06180 06180 06180&th2450te2450dv01072 00551 02110&ds1200 1200 1200&ss0050 0050 0050&rv000 000 000&ta000 000 000&ba0000 0000 0000&lm0 0 0&dj00zo000 000 000&ll1 1 1&lv1 1 1&de0020 0020 0020&wt00 00 00&mv00000 00000 00000&mc00 00 00&mp000 000 000&ms0010 0010 0010&mh0000 0000 0000&gi000 000 000&gj0gk0
    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Received response: C0DSid0009er00/00


## Dropping tips

Finally, you can drop tips anywhere on the deck by using the {func}`~pylabrobot.liquid_handling.LiquidHandler.discard_tips` method.


```python
await lh.drop_tips(tiprack["A1:C1"])
```

    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Sent command: C0TRid0010xp01629 01629 01629 00000&yp1458 1368 1278 0000&tm1 1 1 0&tt01tp1314tz1414th2450ti0
    INFO:pylabrobot.liquid_handling.backends.hamilton.STAR:Received response: C0TRid0010er00/00kz381 356 365 000 000 000 000 000vz303 360 368 000 000 000 000 000



```python
await lh.stop()
```

    WARNING:root:Closing connection to USB device.




# Manually moving channels around

![star supported](https://img.shields.io/badge/STAR-supported-blue)
![Vantage supported](https://img.shields.io/badge/Vantage-supported-blue)
![OT supported](https://img.shields.io/badge/OT-supported-blue)
![EVO not tested](https://img.shields.io/badge/EVO-not%20tested-orange)

With PLR, you can easily move individual channels around. This is useful for calibrating labware locations, calibrating labware sizes, and other things.

```{warning}
Be very careful about collisions! Move channels to a safe z height before traversing.
```

```{note}
With Hamilton robots, when a tip is mounted, the z location will refer to the point of the pipetting tip. With no tip mounted, it will refer to the bottom of the channel.
```

## Example: Hamilton STAR

Here, we'll use a Hamilton STAR as an example. For other robots, simply change the deck layout, makign sure that you have at least a tip rack to use.


```python
from pylabrobot.liquid_handling import LiquidHandler, STAR
from pylabrobot.resources import STARDeck, TIP_CAR_480_A00, HTF

lh = LiquidHandler(backend=STAR(), deck=STARDeck())
await lh.setup()

# assign a tip rack
tip_carrier = TIP_CAR_480_A00(name="tip_carrier")
tip_carrier[0] = tip_rack = HTF(name="tip_rack")
lh.deck.assign_child_resource(tip_carrier, rails=0)
```

## Moving channels

All positions are in mm. The movements are to absolute positions. The origin will be near the left front bottom of the deck, but it differs between robots.

* x: left (low) to right (high)
* y: front (low) to back (high)
* z: bottom (low) to top (high)


```python
channel = 1  # the channel to use

# start by picking up a single tip
await lh.pick_up_tips(tip_rack["A1"], use_channels=[channel])

# prepare for manual operation
# this will space the other channels to safe positions
await lh.prepare_for_manual_channel_operation(channel)
```

Since the channnel will be above the tip rack, it should be safe to move up. We perform a quick check to make sure the z_safe is at least above the resources we know about.


```python
z_safe = 240  # WARNING: this might NOT be safe for your setup

if z_safe <= lh.deck.get_highest_known_point():
  raise ValueError(f"z_safe position is not safe, it is lower than the highest known point: {lh.deck.get_highest_known_point()}")

await lh.move_channel_z(channel, z_safe)
```

```{warning}
The z position in the code above should be safe for most setups, but we can't guarantee that it will be safe for all setups. Move to a z position that is above all your labware before moving in the xy plane.
```

When the z position of the bottom of the tip is above the labware, you can move the channel around in the xy plane.


```python
# move the channel around
await lh.move_channel_x(channel, 100)
await lh.move_channel_y(channel, 100)
```

After reaching a spot where the channel can move down, you can use `move_channel_z` again.


```python
await lh.move_channel_z(channel, 100)
```

Before returning the tip to the tip rack, make sure to move the channel to a safe z position again.


```python
await lh.move_channel_z(channel, z_safe)
```

You can run the code above as often as you like. When you're done, you can return the channel to the tip rack.


```python
await lh.return_tips()
```



# Fans


```python
from pylabrobot.only_fans import Fan
from pylabrobot.only_fans import HamiltonHepaFan
```


```python
fan = Fan(backend=HamiltonHepaFan(), name="my fan")
await fan.setup()
```

Running for 60 seconds:


```python
await fan.turn_on(intensity=100, duration=60)
```

Running until stop:


```python
await fan.turn_on(intensity=100)
```


```python
await fan.turn_off()
```



# Z-probing

With PyLabRobot, one can probe the surface of any object on a STAR(let) deck. This effectively makes the STAR act as a [Coordinate-Measurement Machine (CMM)](https://en.wikipedia.org/wiki/Coordinate-measuring_machine).

There are two ways to probe the surface of an object:

- Using capacitive liquid level sensors (cLLD) to map capacitive objects.
- Moving the tip down onto an object until resistance is detected (a "controlled crash"), which works with both capacitive and non-capacitive objects.

## Example setup


```python
from pylabrobot.liquid_handling import LiquidHandler, STAR
from pylabrobot.resources import STARLetDeck
from pylabrobot.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  TIP_50ul,
  Cor_96_wellplate_360ul_Fb
)

star = STAR()
lh = LiquidHandler(backend=star, deck=STARLetDeck())
await lh.setup()

# assign a tip rack
tip_carrier = TIP_CAR_480_A00(name="tip_carrier")
tip_carrier[1] = tip_rack = TIP_50ul(name="tip_rack")
lh.deck.assign_child_resource(tip_carrier, rails=1)

# assign a plate
plt_carrier = PLT_CAR_L5AC_A00(name="plt_carrier")
plt_carrier[0] = plate = Cor_96_wellplate_360ul_Fb(name="plt")
lh.deck.assign_child_resource(plt_carrier, rails=7)
```

## Capacitive probing using cLLD

If you are mapping a capacitive surface, you can use the cLLD sensor to detect the surface. This is safer and more accurate than the controlled crash method.

```{warning}
For safety purposes, we recommend using Hamilton 50ul tips for mapping surfaces. These are relatively long and soft, acting as 'cushions' in case you try out faster detection speeds (not recommended). Small bends are tolerated well by the 50ul tips.
```

Introduced in [PR #69](https://github.com/PyLabRobot/pylabrobot/pull/69).

### Mapping a single point


```python
await lh.pick_up_tips(tip_rack["A1"])
```

For more information on manually moving channels, see [Manually moving channels around](../moving-channels-around.ipynb).


```python
await star.prepare_for_manual_channel_operation(0)
```


```python
# TODO: change this to a position that works for you
await star.move_channel_x(0, 260)
await star.move_channel_y(0, 190)
```

Use `STAR.probe_z_height_using_channel` to probe the z-height of a single point at the current location. This function will slowly lower the channel until the liquid level sensor detects a change in capacitance. The z-height of the point of the tip is then returned.


```python
await star.clld_probe_z_height_using_channel(0, move_channels_to_save_pos_after=True)
```




    186.0




```python
await lh.return_tips()
```

(mapping-a-3d-surface)=
### Mapping a 3D surface


```python
await lh.pick_up_tips(tip_rack["A1"])
await star.prepare_for_manual_channel_operation(0)
```


```python
xs = [260 + i * 3 for i in range(13)]  # in mm, absolute coordinates
ys = [190 + i * 3 for i in range(10)]  # in mm, absolute coordinates

data = []

for x in xs:
  await star.move_channel_x(0, x)
  for y in ys:
    await star.move_channel_y(0, y)
    height = await star.clld_probe_z_height_using_channel(0, start_pos_search=25000)
    data.append((x, y, height))
    await lh.move_channel_z(0, 230)  # move up slightly for traversal
```


```python
await lh.return_tips()
```

Plotting requires `matplotlib` and `numpy`. If you don't have them installed, you can install them with `pip install matplotlib numpy`.


```python
import matplotlib.pyplot as plt
import numpy as np

data = np.array(data)
fig = plt.figure()
ax = fig.add_subplot(111, projection='3d')
ax.scatter(data[:, 0], data[:, 1], data[:, 2])
ax.set_xlabel('X')
ax.set_ylabel('Y')
plt.show()
```


    
![png](z-probing.ipynb_files/z-probing.ipynb_17_0.png)
    


Check out the following video demo of mapping a 3D surface:

<iframe width="640" height="360" src="https://www.youtube.com/embed/_uPf9hyTBog" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>

## Non-capacitive probing using the force sensor

This uses moves a tip down slowly until resistance is detected (a "controlled crash"), to measure the surface z-height. This technique is similar to what is routinely used for discarding tips into the trash in both VENUS and PLR.

Sensor accuracy for z-height readings needs to be further tested but in initial tests has been at least 0.2 mm.

### Using teaching needles

Most STAR(let)s come with a teaching block that includes 8 teaching needles. These needles are equivalent to standard volume (300uL) pipette tips but are made from metal instead of plastic. This leads to more accurate results when probing surfaces.

```{warning}
When using the teaching needles, be careful not to damage the STAR(let) deck or channels. The needles are made from metal and can bend the pipetting channels more easily than soft plastic tips.
```


```python
teaching_tip_rack = lh.deck.get_resource("teaching_tip_rack")
await lh.pick_up_tips(teaching_tip_rack["A2"])
```

Alternatively, you can use plastic tips for probing surfaces. However, these are softer and therefore less accurate than the metal teaching needles. 50uL tips are the softest and cannot be used with force z-probing.

### Moving the channel and mapping a point

See above for more information on moving channels.

```{warning}
Make sure the tip is at a safe height above the labware before moving the channel. Use `STAR.move_channel_z` to move the channel to a safe height.
```


```python
await star.prepare_for_manual_channel_operation(0)
await star.move_channel_x(0, 260)
await star.move_channel_y(0, 190)
```


```python
await star.ztouch_probe_z_height_using_channel(
  channel_idx=0,
  move_channels_to_save_pos_after=True)
```




    184.76




```python
await lh.return_tips()
```

Check out [mapping a 3d surface](#mapping-a-3d-surface) for more information on mapping a surface.



# Validating against log file example

All communication between PLR and the outside world (the hardware) happens through the io layer. This is a layer below backends, and is responsible for sending and receiving messages to and from the hardware. Schematically,

```
Frontends <-> backends <-> io <-> hardware
```

PLR supports capturing all communication in the io layer, both write and read commands. This can later be played back to validate that a protocol has not changed. The key here is that if we send the same commands to the hardware, the hardware will do the same thing. "Reading" data (from the capture file) is useful because some protocols are dynamic and depend on responses from the hardware.

In this notebook, we will run a simple protocol on a robot while capturing all data passing through the io layer. We will then replay the capture file while executing the protocol again to demonstrate how validation works. Finally, we slightly modify the protocol and show that the validation fails.

## Defining a simple protocol


```python
import pylabrobot
from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import STAR
from pylabrobot.resources.hamilton import STARDeck

backend = STAR()
lh = LiquidHandler(backend=backend, deck=STARDeck())
```


```python
from pylabrobot.resources import TIP_CAR_480_A00, HT
tip_car = TIP_CAR_480_A00(name="tip_car")
tip_car[0] = tr = HT(name="ht")
lh.deck.assign_child_resource(tip_car, rails=1)
```


```python
async def simple_protocol(tips):
  await lh.setup()
  await lh.pick_up_tips(tips)
  await lh.return_tips()
  await lh.stop()
```

## Capturing data during protocol execution

Do a real run first, without validating against a capture file. This will generate the capture file you can later compare against.

While it might seem cumbersome, this actually ensures you have a real working protocol before doing validation. The benefit of using capture files is whenever you change your protocol and have seen it run, you can just grab the capture file and use it for validation. No need to manually write tests.


```python
validation_file = "./validation.json"
pylabrobot.start_capture(validation_file)
await simple_protocol(tr["A1:H1"])
pylabrobot.stop_capture()
```

    Validation file written to validation.json


The validation file is just json:


```python
!head -n15 validation.json
```

    {
      "version": "0.1.6",
      "commands": [
        {
          "module": "usb",
          "device_id": "[0x8af:0x8000][][]",
          "action": "write",
          "data": "C0RTid0001"
        },
        {
          "module": "usb",
          "device_id": "[0x8af:0x8000][][]",
          "action": "read",
          "data": "C0RTid0001er00/00rt0 0 0 0 0 0 0 0"
        },


## Replaying the capture file for validation

On validation, before calling setup, run `pylabrobot.validate` to enable the validation. Pass a capture file that contains the commands we should check against.

Call `pylabrobot.end_validation` at the end to make sure there are no remaining commands in the capture file. This marks the end of the validation.


```python
pylabrobot.validate(validation_file)
await simple_protocol(tr["A1:H1"])
pylabrobot.end_validation()
```

    Validation successful!


## Failing validation

When validation is not successful, we use the Needleman-Wunsch algorithm to find the difference between the expected and the actual output.


```python
pylabrobot.validate(validation_file)
await simple_protocol(tr["A2:H2"])
pylabrobot.end_validation()
```

    expected: C0TPid0009xp01179 01179 01179 01179 01179 01179 01179 01179yp1458 1368 1278 1188 1098 1008 0918 0828tm1 1 1 1 1 1 1 1tt01tp2266tz2166th2450td0
    actual:   C0TPid0009xp01269 01269 01269 01269 01269 01269 01269 01269yp1458 1368 1278 1188 1098 1008 0918 0828tm1 1 1 1 1 1 1 1tt01tp2266tz2166th2450td0
                            ^^    ^^    ^^    ^^    ^^    ^^    ^^    ^^                                                                                    



    ---------------------------------------------------------------------------

    ValidationError                           Traceback (most recent call last)

    Cell In[7], line 2
          1 pylabrobot.validate(validation_file)
    ----> 2 await simple_protocol(tr["A2:H2"])
          3 pylabrobot.end_validation()


    Cell In[3], line 3, in simple_protocol(tips)
          1 async def simple_protocol(tips):
          2   await lh.setup()
    ----> 3   await lh.pick_up_tips(tips)
          4   await lh.return_tips()
          5   await lh.stop()


    File ~/retro/pylabrobot/pylabrobot/machines/machine.py:35, in need_setup_finished.<locals>.wrapper(*args, **kwargs)
         33 if not self.setup_finished:
         34   raise RuntimeError("The setup has not finished. See `setup`.")
    ---> 35 return await func(*args, **kwargs)


    File ~/retro/pylabrobot/pylabrobot/liquid_handling/liquid_handler.py:467, in LiquidHandler.pick_up_tips(self, tip_spots, use_channels, offsets, **backend_kwargs)
        464   (self.head[channel].commit if success else self.head[channel].rollback)()
        466 # trigger callback
    --> 467 self._trigger_callback(
        468   "pick_up_tips",
        469   liquid_handler=self,
        470   operations=pickups,
        471   use_channels=use_channels,
        472   error=error,
        473   **backend_kwargs,
        474 )


    File ~/retro/pylabrobot/pylabrobot/liquid_handling/liquid_handler.py:2204, in LiquidHandler._trigger_callback(self, method_name, error, *args, **kwargs)
       2202   callback(self, *args, error=error, **kwargs)
       2203 elif error is not None:
    -> 2204   raise error


    File ~/retro/pylabrobot/pylabrobot/liquid_handling/liquid_handler.py:451, in LiquidHandler.pick_up_tips(self, tip_spots, use_channels, offsets, **backend_kwargs)
        449 error: Optional[Exception] = None
        450 try:
    --> 451   await self.backend.pick_up_tips(ops=pickups, use_channels=use_channels, **backend_kwargs)
        452 except Exception as e:
        453   error = e


    File ~/retro/pylabrobot/pylabrobot/liquid_handling/backends/hamilton/STAR.py:1484, in STAR.pick_up_tips(self, ops, use_channels, begin_tip_pick_up_process, end_tip_pick_up_process, minimum_traverse_height_at_beginning_of_a_command, pickup_method)
       1481 pickup_method = pickup_method or tip.pickup_method
       1483 try:
    -> 1484   return await self.pick_up_tip(
       1485     x_positions=x_positions,
       1486     y_positions=y_positions,
       1487     tip_pattern=channels_involved,
       1488     tip_type_idx=ttti,
       1489     begin_tip_pick_up_process=begin_tip_pick_up_process,
       1490     end_tip_pick_up_process=end_tip_pick_up_process,
       1491     minimum_traverse_height_at_beginning_of_a_command=minimum_traverse_height_at_beginning_of_a_command,
       1492     pickup_method=pickup_method,
       1493   )
       1494 except STARFirmwareError as e:
       1495   if plr_e := convert_star_firmware_error_to_plr_error(e):


    File ~/retro/pylabrobot/pylabrobot/liquid_handling/backends/hamilton/STAR.py:98, in need_iswap_parked.<locals>.wrapper(self, *args, **kwargs)
         93 if self.iswap_installed and not self.iswap_parked:
         94   await self.park_iswap(
         95     minimum_traverse_height_at_beginning_of_a_command=int(self._iswap_traversal_height * 10)
         96   )
    ---> 98 result = await method(self, *args, **kwargs)
        100 return result


    File ~/retro/pylabrobot/pylabrobot/liquid_handling/backends/hamilton/STAR.py:4062, in STAR.pick_up_tip(self, x_positions, y_positions, tip_pattern, tip_type_idx, begin_tip_pick_up_process, end_tip_pick_up_process, minimum_traverse_height_at_beginning_of_a_command, pickup_method)
       4055 assert (
       4056   0 <= end_tip_pick_up_process <= 3600
       4057 ), "end_tip_pick_up_process must be between 0 and 3600"
       4058 assert (
       4059   0 <= minimum_traverse_height_at_beginning_of_a_command <= 3600
       4060 ), "minimum_traverse_height_at_beginning_of_a_command must be between 0 and 3600"
    -> 4062 return await self.send_command(
       4063   module="C0",
       4064   command="TP",
       4065   tip_pattern=tip_pattern,
       4066   read_timeout=60,
       4067   xp=[f"{x:05}" for x in x_positions],
       4068   yp=[f"{y:04}" for y in y_positions],
       4069   tm=tip_pattern,
       4070   tt=f"{tip_type_idx:02}",
       4071   tp=f"{begin_tip_pick_up_process:04}",
       4072   tz=f"{end_tip_pick_up_process:04}",
       4073   th=f"{minimum_traverse_height_at_beginning_of_a_command:04}",
       4074   td=pickup_method.value,
       4075 )


    File ~/retro/pylabrobot/pylabrobot/liquid_handling/backends/hamilton/base.py:247, in HamiltonLiquidHandler.send_command(self, module, command, auto_id, tip_pattern, write_timeout, read_timeout, wait, fmt, **kwargs)
        222 """Send a firmware command to the Hamilton machine.
        223 
        224 Args:
       (...)
        237   A dictionary containing the parsed response, or None if no response was read within `timeout`.
        238 """
        240 cmd, id_ = self._assemble_command(
        241   module=module,
        242   command=command,
       (...)
        245   **kwargs,
        246 )
    --> 247 resp = await self._write_and_read_command(
        248   id_=id_,
        249   cmd=cmd,
        250   write_timeout=write_timeout,
        251   read_timeout=read_timeout,
        252   wait=wait,
        253 )
        254 if resp is not None and fmt is not None:
        255   return self._parse_response(resp, fmt)


    File ~/retro/pylabrobot/pylabrobot/liquid_handling/backends/hamilton/base.py:267, in HamiltonLiquidHandler._write_and_read_command(self, id_, cmd, write_timeout, read_timeout, wait)
        258 async def _write_and_read_command(
        259   self,
        260   id_: Optional[int],
       (...)
        264   wait: bool = True,
        265 ) -> Optional[str]:
        266   """Write a command to the Hamilton machine and read the response."""
    --> 267   self.io.write(cmd.encode(), timeout=write_timeout)
        269   if not wait:
        270     return None


    File ~/retro/pylabrobot/pylabrobot/io/usb.py:325, in USBValidator.write(self, data, timeout)
        323 if not next_command.data == data.decode("unicode_escape"):
        324   align_sequences(expected=next_command.data, actual=data.decode("unicode_escape"))
    --> 325   raise ValidationError("Data mismatch: difference was written to stdout.")


    ValidationError: Data mismatch: difference was written to stdout.




# Using the 96 head

![star supported](https://img.shields.io/badge/STAR-supported-blue)
![Vantage supported](https://img.shields.io/badge/Vantage-supported-blue)
![OT2 not supported](https://img.shields.io/badge/OT-not%20supported-red)
![EVO not implemented](https://img.shields.io/badge/EVO-not%20implemented-orange)

Some liquid handling robots have a 96 head, which can be used to pipette 96 samples at once. This notebook shows how to use the 96 head in PyLabRobot.

## Example: Hamilton STARLet

Here, we'll use a Hamilton STARLet as an example. For other robots, simply change the deck layout, makign sure that you have at least a tip rack and a plate to use.


```python
from pylabrobot.liquid_handling import LiquidHandler, STAR
from pylabrobot.resources import STARLetDeck
from pylabrobot.resources import (
  TIP_CAR_480_A00,
  PLT_CAR_L5AC_A00,
  TIP_50ul,
  Cor_96_wellplate_360ul_Fb
)

lh = LiquidHandler(backend=STAR(), deck=STARLetDeck())
await lh.setup()

# assign a tip rack
tip_carrier = TIP_CAR_480_A00(name="tip_carrier")
tip_carrier[1] = tip_rack = TIP_50ul(name="tip_rack")
lh.deck.assign_child_resource(tip_carrier, rails=1)

# assign a plate
plt_carrier = PLT_CAR_L5AC_A00(name="plt_carrier")
plt_carrier[0] = plate = Cor_96_wellplate_360ul_Fb(name="plt")
lh.deck.assign_child_resource(plt_carrier, rails=7)
```

## Liquid handling with the 96 head

Liquid handling with the 96 head is very similar to what you would do with individual channels. The methods have `96` in their names, and they take `TipRack`s and `Plate`s as arguments, as opposed to `TipSpot`s and `Well`s in case of heads with individual pipetting channels.


```python
await lh.pick_up_tips96(tip_rack)
```

For aspirations and dispenses, a single volume is passed.

```{note}
Only single-volume aspirations and dispenses are supported because all robots that are currently implemented only support single-volume operations. When we add support for robots that can do variable-volume, this will be updated.
```


```python
await lh.aspirate96(plate, volume=50)
```


```python
await lh.dispense96(plate, volume=50)
```


```python
await lh.return_tips96()
```

## Quadrants

96 heads can also be used to pipette quadrants of a 384 well plate. Here, we'll show how to do that.

![quadrants](img/96head/quadrants.png)


```python
from pylabrobot.resources import BioRad_384_DWP_50uL_Vb
plt_carrier[1] = plate384 = BioRad_384_DWP_50uL_Vb(name="plt384")
```


```python
await lh.pick_up_tips96(tip_rack)
```


```python
await lh.aspirate96(plate384.get_quadrant(1), volume=10)
```


```python
await lh.dispense96(plate384.get_quadrant(2), volume=10)
```


```python
await lh.aspirate96(plate384.get_quadrant(3), volume=10)
```


```python
await lh.dispense96(plate384.get_quadrant(4), volume=10)
```


```python
await lh.return_tips96()
```



# Temperature controllers (heaters and coolers)

PyLabRobot supports the following temperature controllers:

- Opentrons Temperature Module V2

Temperature controllers are controlled by the {class}`~pylabrobot.temperature_controlling.temperature_controller.TemperatureController` class. This class takes a backend as an argument. The backend is responsible for communicating with the scale and is specific to the hardware being used.

The {class}`~pylabrobot.temperature_controlling.opentrons.OpentronsTemperatureModuleV2` is a TemperatureController subclass initialized with a tube rack.


```python
from pylabrobot.temperature_controlling import TemperatureController
from pylabrobot.temperature_controlling.opentrons import OpentronsTemperatureModuleV2
from pylabrobot.temperature_controlling.opentrons_backend import (
  OpentronsTemperatureModuleBackend,
)
```

Using the Opentrons temperature controller currently requires an Opentrons robot. The robot must be connected to the host computer and to the temperature module.


```python
from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.opentrons_backend import OpentronsBackend
from pylabrobot.resources.opentrons import OTDeck

ot = OpentronsBackend(host="169.254.184.185", port=31950)  # Get the ip from the Opentrons app
lh = LiquidHandler(backend=ot, deck=OTDeck())
await lh.setup()
```

After setting up the robot, use the `OpentronsBackend.list_connected_modules()` to list the connected temperature modules. You are looking for the `'id'` of the module you want to use.


```python
await ot.list_connected_modules()
```




    [{'id': 'fc409cc91770129af8eb0a01724c56cb052b306a',
      'serialNumber': 'TDV21P20201224B13',
      'firmwareVersion': 'v2.1.0',
      'hardwareRevision': 'temp_deck_v21',
      'hasAvailableUpdate': False,
      'moduleType': 'temperatureModuleType',
      'moduleModel': 'temperatureModuleV2',
      'data': {'status': 'idle', 'currentTemperature': 34.0},
      'usbPort': {'port': 1,
       'portGroup': 'main',
       'hub': False,
       'path': '1.0/tty/ttyACM0/dev'}}]



Initialize the OpentronsTemperatureModuleV2 with the `id` of the module you want to use.


```python
t = OpentronsTemperatureModuleV2(name="t", opentrons_id="fc409cc91770129af8eb0a01724c56cb052b306a")
await t.setup()
```

The `OpentronsTemperatureModuleV2` is a subclass of {class}`~pylabrobot.temperature_controlling.temperature_controller.TemperatureController`.


```python
isinstance(t, TemperatureController)
```




    True



Be sure to assign the temperature controller to the robot deck before you use it. This is done with the usual {func}`~pylabrobot.resources.opentrons.deck.assign_child_at_slot` function.


```python
lh.deck.assign_child_at_slot(t, slot=3)
```

You can set the temperature in Celsius using {func}`~pylabrobot.temperature_controlling.temperature_controller.TemperatureController.set_temperature`.


```python
await t.set_temperature(37)
```

Use {func}`~pylabrobot.temperature_controlling.temperature_controller.TemperatureController.wait_for_temperature` to wait for the temperature to stabilize at the target temperature.


```python
await t.wait_for_temperature()
```

The temperature can be read using {func}`~pylabrobot.temperature_controlling.temperature_controller.TemperatureController.get_temperature`.


```python
await t.get_temperature()
```




    37.0



If you are done with the temperature controller, you can use {func}`~pylabrobot.temperature_controlling.temperature_controller.TemperatureController.deactivate` to turn it off. The temperature controller will return to ambient temperature.


```python
await t.deactivate()
```

## Pipetting from the OT-2 temperature module

Assign some tips to the deck and pick one up so that we can aspirate:


```python
from pylabrobot.resources.opentrons import opentrons_96_tiprack_300ul

tips300 = opentrons_96_tiprack_300ul(name="tips")
lh.deck.assign_child_at_slot(tips300, slot=11)
```


```python
await lh.pick_up_tips(tips300["A5"])
```

Access the temperature controller's tube rack with the `tube_rack` attribute.


```python
await lh.aspirate(t.tube_rack["A1"], vols=[20])
```


```python
await lh.aspirate(t.tube_rack["A6"], vols=[20])
```

Return the tips to the tip rack when you are done.


```python
await lh.return_tips()
```



