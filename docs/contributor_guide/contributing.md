# Contributing to PyLabRobot

Thank you for your interest in contributing to PyLabRobot! This document will help you get started.

## Getting Started

See the installation instructions [here](../user_guide/_getting-started/installation.md). For contributing, you should install PyLabRobot from source.

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
