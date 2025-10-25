# Contributing new resources

PyLabRobot ships with a growing library of resource definitions for common labware.
If you define a new resource it helps the community when you contribute it back.
This page describes the information to include and how to format it.

```{attention}
PLEASE TEST YOUR RESOURCE DEFINITION BEFORE CONTRIBUTING!
```

## 1. Update the resource library

Add an entry for your resource under the appropriate manufacturer page in
{doc}`the resource library </resources/index>`.
List the part number, name, a picture and a link to the manufacturer's website.
Existing entries use a table format like this:

```
| Description | Image | PLR definition |
|-------------|-------|----------------|
| 'VWRReagentReservoirs25mL'<br>Part no.: 89094<br>[manufacturer website](https://us.vwr.com/store/product/4694822/vwr-disposable-pipetting-reservoirs)<br>Polystyrene Reservoirs | ![](img/vwr/VWRReagentReservoirs25mL.jpg) | `VWRReagentReservoirs25mL` |
```

Use the same style and folder structure for images.

If a section for your resource type (e.g. "Plates"/"Troughs"/etc.) already exists, add your resource to it. Add no new line between the rows.
If a section does not exist, create a new section (see other files for examples).

Please add an image of the resource to the `resources/library/img/<manufacturer>` folder.
The image should have the same name as the resource definition you will create.
The file type can be anything. Please compress and scale down the image to reduce its size (ideally below 100kB).

## 2. Document attribute sources

When writing a resource definition annotate each attribute with its origin.
Link any technical drawings you consulted using an archived copy for
permanence and mark those values with `# from spec`.
If you measured a value yourself append `# measured`.

````python
# example

def AGenBio_96_wellplate_Ub_2200ul(name: str, lid: Optional[Lid] = None) -> Plate:
 """
  AGenBio Catalog No. P-2.2-SQG-96
  - Material: Polypropylene
  - Max. volume: 2200 uL
  """
  INNER_WELL_WIDTH = 8  # measured
  INNER_WELL_LENGTH = 8  # measured

  well_kwargs = {
    "size_x": INNER_WELL_WIDTH,  # measured
    ...
  }

  return Plate(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.48,  # from spec
    size_z=42.5,   # from spec
    ...
  )
````

## 3. Add imports

If you place your resource in a new module, remember to import it from the
package's `__init__.py` so users can load it directly.

Thank you for helping expand the resource library!

## 4. Submit a pull request

Once you have:
- [ ] added your resource to the library
- [ ] documented the sources of your attributes (measured, from spec, etc.)
- [ ] added the name, part number, link and image to the docs
- [ ] added the imports
- [ ] verified that your resource works as expected

You are ready to submit a pull request.

Please create a separate new pull request for each resource you add.
This makes it easier to review and faster to merge.
