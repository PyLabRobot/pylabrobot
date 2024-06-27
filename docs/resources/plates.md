# Plates

Microplates are modelled by the {class}`~pylabrobot.resources.plate.Plate` class consist of equally spaced wells. Wells are children of the `Plate` and are modelled by the {class}`~pylabrobot.resources.well.Well` class. `Plate` is a subclass of {class}`~pylabrobot.resources.itemized_resource.ItemizedResource`, allowing convenient integer and string indexing.

## Lids

Plates can optionally have a lid, which will also be a child of the `Plate` class. The lid is modelled by the `Lid` class.

### Measuring `nesting_z_height`

The `nesting_z_height` is the overlap between the lid and the plate when the lid is placed on the plate. This property can be measured using a caliper.

![nesting_z_height measurement](/img/plate/lid_nesting_z_height.jpeg)
