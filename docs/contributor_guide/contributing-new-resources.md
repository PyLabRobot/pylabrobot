# Contributing New Resources

How to add a resource definition and its library entry, for humans and agents. This guide covers
resource-specific requirements; follow the [general contribution guide](contributing.md) for
shared code style and contribution conventions. Contributing definitions back to PyLabRobot
helps other labs use the same resources.

## 1. Identify the resource

Search `pylabrobot/resources/` and {doc}`the resource library </resources/index>` for the
manufacturer, part number, and equivalent definitions before adding one. Use a nearby definition
of the same resource type as a reference for naming and file layout.

Collect the manufacturer's name, product name, part number, product link, and technical drawings.
Confirm which variant you have: capacity, bottom shape, lid, and other physical differences can
require different definitions.

## 2. Write the definition

A resource definition normally creates an instance of an existing class with particular dimensions
and other parameters. Use a small, typed factory function that accepts `name: str` and returns the
appropriate class, such as `Plate`, `Tube`, `TipRack`, `Trough`, or `Carrier`. Set `model` to identify
the product independently of the instance's name. Create a subclass only when the resource needs
behavior that existing classes cannot express.

- **Location:** add the factory to `pylabrobot/resources/<manufacturer>/<resource_type>.py`, such
  as `plates.py` or `tubes.py`. Extend an existing module when it fits.
- **Units and geometry:** use millimeters for lengths and microliters for volumes. Follow the
  {doc}`resource coordinate conventions </resources/introduction>` and the relevant class's
  parameter definitions. Distinguish outer dimensions, cavity dimensions, and bottom material
  thickness; a nominal capacity does not determine the geometry.
- **Children:** define wells, tip spots, or holders relative to their parent. Reuse layout helpers
  such as `create_ordered_items_2d` for regular grids. Record item counts, offsets, and pitch from
  drawings or measurements instead of copying them from a similar-looking product.
- **Shared behavior:** reuse existing geometry and height/volume helpers when they describe the
  resource. Keep reusable logic in the implementation of the class or helper, so definitions only
  supply the resource-specific parameters.
- **Optional features:** include lids and stacking dimensions when supported by evidence.
  `stacking_z_height` is the vertical pitch one plate adds to a stack of identical plates
  (`size_z` minus how far the plates nest). Leave it unset when it is not needed or known.

For examples, see `pylabrobot/resources/agenbio/plates.py` for plates with regularly spaced wells
and `pylabrobot/resources/eppendorf/tubes.py` for individual containers.

## 3. Document attribute sources

Annotate each physical attribute with its origin. Link technical drawings in the factory's
docstring, using an archived copy for permanence, and mark values from them with `# from spec`.
Append `# measured` to values you measured yourself. For derived values, show the calculation and
identify the sources of its inputs. Document approximations and unknowns rather than presenting
them as measured facts.

For example, the AGenBio plate definitions distinguish measured inner well dimensions from the
manufacturer's outer dimensions:

```python
INNER_WELL_WIDTH = 8  # measured
INNER_WELL_LENGTH = 8  # measured
```

```python
plate_dimensions = {
  "size_x": 127.76,  # from spec
  "size_y": 85.48,  # from spec
  "size_z": 42.5,  # from spec
}
```

Include the product name, part number, material, and relevant capacity or shape information in the
factory's docstring. Explain how measurements were taken when their interpretation is not obvious.

## 4. Export and document the resource

Import a new module from the manufacturer's `__init__.py`. For a new manufacturer package, also
import it from `pylabrobot/resources/__init__.py`, so users can load the definition directly from
`pylabrobot.resources`.

Add an entry under the appropriate manufacturer page in `docs/resources/library/`.
List the part number, name, a picture, and a link to the manufacturer's website. Existing entries
use a table format like this:

```markdown
| Description | Image | PLR definition |
|-------------|-------|----------------|
| 'VWRReagentReservoirs25mL'<br>Part no.: 89094<br>[manufacturer website](https://us.vwr.com/store/product/4694822/vwr-disposable-pipetting-reservoirs)<br>Polystyrene Reservoirs | ![](img/vwr/VWRReagentReservoirs25mL.jpg) | `VWRReagentReservoirs25mL` |
```

Add the entry to an existing section for its type (such as Plates or Troughs), without blank lines
between table rows. Create a section if needed. For a new manufacturer page, add it to the Resource
Library toctree in `docs/resources/index.md`.

Place the image in `docs/resources/library/img/<manufacturer>/`, using the definition's name as
the filename stem. Follow the existing image layout, and compress and scale the image to keep it
small (ideally below 100 kB).

## 5. Verify the definition and understand testing

**No tests are needed for a definition that only instantiates existing resource classes with
particular numbers or other parameters.** This applies to all resource types: a plate, tube,
tip rack, trough, or carrier definition is an ordinary instance of its underlying class.
We test the implementation of resource classes and shared helpers independently of any particular
definition. Do not add tests that merely repeat a definition's constants or exercise unchanged
class behavior for another product.

If the contribution introduces or changes behavior in a class or helper, test that implementation
where the behavior lives. A new geometry algorithm, for example, needs tests for its calculations;
another definition using that algorithm with different dimensions does not.

The definition's physical data still needs verification. Compare dimensions, capacities, child
positions, and fit against the source drawings and the actual resource. State what you verified
and any remaining uncertainty in the pull request. Unit tests that repeat the supplied numbers
cannot establish whether they match the physical resource.

Check that the factory is importable through `pylabrobot.resources` and that its documentation
renders correctly. Follow the general contribution guide for formatting and documentation checks.

## 6. Submit a pull request

Before submitting, confirm that you have:

- [ ] added the resource definition using the appropriate existing classes and helpers
- [ ] documented the sources of physical attributes (measured, from spec, or derived)
- [ ] exported the definition through the package imports
- [ ] added the name, part number, link, and image to the resource library
- [ ] verified the physical data and documented any uncertainty
- [ ] tested any new or changed implementation behavior, if applicable

Please create a separate pull request for each resource you add. This makes it easier to review
and faster to merge.
