# %% [markdown]
# # ResourceStack
#
# `ResourceStack` represents a collection of resources stacked together into a single
# resource. It can grow along the x, y or z axis. This is useful when you want to
# treat multiple resources as a single unit, for instance stacking lids vertically or
# arranging plates side by side before placing them on the deck.
#
# Because the stack is itself a `Resource`, it can be assigned to other resources or
# the deck like any other labware. When the stack grows along the z-axis it behaves
# like a traditional *stack* where items are added and removed from the top.
#
# Below we demonstrate creating stacks in different orientations and interacting with
# them.

# %%
from pylabrobot.resources import Resource, Plate, Lid, Coordinate
from pylabrobot.resources import ResourceStack

# %% [markdown]
# ## Creating an empty stack
# Pass the name and direction of growth (`"x"`, `"y"`, or `"z"`).

# %%
stack_x = ResourceStack("stack_x", "x")
stack_y = ResourceStack("stack_y", "y")
stack_z = ResourceStack("stack_z", "z")
(stack_x.children, stack_y.children, stack_z.children)

# %% [markdown]
# ## Stacking resources at construction time
# You can also supply a list of resources which will be assigned immediately.

# %%
stack = ResourceStack(
    "stack",
    "x",
    [
        Resource("A", size_x=10, size_y=10, size_z=10),
        Resource("B", size_x=10, size_y=10, size_z=10),
    ],
)
([child.name for child in stack.children], stack.get_size_x())

# %% [markdown]
# The total size along the x-axis equals the sum of the children sizes.

# %%
stack_y2 = ResourceStack(
    "stack_y2",
    "y",
    [
        Resource("A", size_x=10, size_y=10, size_z=10),
        Resource("B", size_x=10, size_y=10, size_z=10),
    ],
)
stack_y2.get_size_y()

# %% [markdown]
# ## Adding and removing items
# New items are positioned automatically at the edge returned by
# `get_resource_stack_edge()`. When stacking in the z direction you can only remove
# the current top item.

# %%
lid1 = Lid(name="L1", size_x=10, size_y=10, size_z=5, nesting_z_height=1)
lid2 = Lid(name="L2", size_x=10, size_y=10, size_z=5, nesting_z_height=1)
stack_z.assign_child_resource(lid1)
stack_z.assign_child_resource(lid2)
stack_z.get_top_item().name

# %%
stack_z.unassign_child_resource(lid2)
stack_z.get_top_item().name

# %% [markdown]
# Attempting to remove `lid1` now would raise a `ValueError` because it is not the
# top item in this z-growing stack.

# %% [markdown]
# ## Using a ResourceStack as a stacking area
# A common use case is stacking plates next to a reader or washer. After placing a
# plate on the stack you can retrieve it again using `get_top_item()`.

# %%
plate = Plate("p1", size_x=1, size_y=1, size_z=1, ordered_items={})
stacking_area = ResourceStack("stacking_area", "z")
stacking_area.assign_child_resource(plate)
stacking_area.get_top_item() is plate

# %% [markdown]
# When using a :class:`~pylabrobot.liquid_handling.liquid_handler.LiquidHandler` the
# stack behaves just like any other resource:
#
# ```python
# lh.move_plate(stacking_area.get_top_item(), plate_carrier[0])
# ```
#
# This allows temporary storage of plates or lids during automated workflows.

