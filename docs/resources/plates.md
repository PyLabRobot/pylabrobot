# Plates

Microplates are modelled by the {class}`~pylabrobot.resources.plate.Plate` class consist of equally spaced wells. Wells are children of the `Plate` and are modelled by the {class}`~pylabrobot.resources.well.Well` class. The relative positioning of `Well`s is what determines their location. `Plate` is a subclass of {class}`~pylabrobot.resources.itemized_resource.ItemizedResource`, allowing convenient integer and string indexing.

There is some standardization on plate dimensions by SLAS, which you can read more about in the [ANSI SLAS 1-2004 (R2012): Footprint Dimensions doc](https://www.slas.org/SLAS/assets/File/public/standards/ANSI_SLAS_1-2004_FootprintDimensions.pdf). Note that PLR fully supports all plate dimensions, sizes, relative well spacings, etc.

## Lids

Plates can optionally have a lid, which will also be a child of the `Plate` class. The lid is modelled by the `Lid` class.

### Measuring `nesting_z_height`

The `nesting_z_height` is the overlap between the lid and the plate when the lid is placed on the plate. This property can be measured using a caliper.

![nesting_z_height measurement](/img/plate/lid_nesting_z_height.jpeg)
