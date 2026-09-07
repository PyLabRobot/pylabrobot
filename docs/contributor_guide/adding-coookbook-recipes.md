# Contributing a New Recipe

This page explains how to contribute a new **Cookbook-Recipe** to PyLabRobot.
It is assumed you are already familiar with the general contributor workflow as outlined in the Contributor Guide (see [Contributing to PyLabRobot](contributing.md)).

<hr>

## 1. Where to add the Recipe

1. Fork the repository and clone to your local machine, as per the usual workflow.
2. Navigate to your fork's documentation directory: `pylabrobot/docs/cookbook/recipes/`
3. Create a new Jupyter Notebook (recommended) for your recipe. For example:  
`star_movement_plate_to_alpaqua_core.ipynb`  
4. Ensure the notebook is self-contained (includes code, narrative, images, and outputs) and follows the style of existing recipes.

<hr>

## 2. Adding a Recipe Card to the Cookbook Page

Once your recipe notebook is ready and committed to `docs/cookbook/`, you must add an entry to the `pylabrobot/docs/cookbook/index.rst` file so that the recipe shows up on the website.
Add something like the following:

```rst
.. plrcard::
   :header: Move plate to Alpaqua magnet using CORE grippers
   :card_description: <ul>
      <li>Resource movement using CORE grippers</li>
      <li>Resource position check using grippers</li>
      <li>PLR autocorrection of plate placement onto PlateAdapter/magnet</li>
      </ul>
   :link: star_movement_plate_to_alpaqua_core.html
   :tags: ResourceMovement PlateAdapter HamiltonSTAR
```

Then, further down in the same index.rst, add the TOC reference:

```rst
.. -----------------------------------------
.. Page TOC
.. -----------------------------------------
.. toctree::
   :maxdepth: 2
   :hidden:

   recipes/star_movement_plate_to_alpaqua_core
```

Replace `star_movement_plate_to_alpaqua_core` with your recipe’s base name.

<hr>

## 3. Adding New Tags (Optional)

**Tags** categorize Cookbook recipes by topic, device, or feature.  
They populate the filter buttons at the top of the Cookbook page, helping users quickly find related content - for example:  
- `ResourceMovement` (type of operation)  
- `HamiltonSTAR` (robot platform)
- `PlateAdapter` (PLR resource type)

If your recipe introduces a **new tag** (for example, `MagneticSeparation`), follow these steps:

1. Add the new tag to the `:tags:` line in your `plrcard` above.  
2. Open `pylabrobot/docs/_templates/plr_card_grid.html` and add a new filter button, for example:

   ```html
   <div class="plr-filter-btn" data-tag="MagneticSeparation">Magnetic Separation</div>

This ensures your new tag appears as a filterable button on the Cookbook cards grid.

<hr>

## 4. Checklist Before Submitting Your Pull Request

- The new notebook is located in `docs/cookbook/recipes/`.  
- You added a `plrcard` entry and a `toctree` reference to `index.rst`.  
- Any new tags are added to `plr_card_grid.html`. 

- Documentation builds without errors (`make docs`).  
- The notebook executes cleanly from start to finish.  
- PR title and description clearly describe your new recipe.  
- Linting, formatting, and typing checks pass:

```bash
make lint
make format-check
make typecheck
```
- Tests run successfully and coverage is unaffected.

<hr>

## 5. Example Pull Request

Example PR: [PR#726 Start PLR Cookbook](https://github.com/PyLabRobot/pylabrobot/pull/726/)

- Suppose you are adding the recipe file `star_movement_plate_to_alpaqua_core.ipynb`.
- Your PR would contain:
   - the recipe notebook: `docs/cookbook/recipes/star_movement_plate_to_alpaqua_core.ipynb`
   - Modifications to `docs/cookbook/index.rst` adding the card and TOC entry.
   - If relevant: Modification to `docs/_templates/plr_card_grid.html` to add new tag(s).
   - In the PR description mention something like: *“Add new Cookbook recipe: Move plate to Alpaqua magnet using CORE grippers (resources movement, gripper position check, placement correction).”*
   - Link to any relevant issue or discussion for context.

<hr>

## 6. Style Tips for Cookbook Recipes

- **Write clearly and narratively** – start with the recipe’s goal, list prerequisites (hardware, deck layout, robot backend), and describe the expected outcome.  
- **Combine code and explanation** – keep code cells short and follow them with brief commentary rather than large, unannotated blocks.  
- **Include visuals** – add static screenshots for deck layouts and optional GIF or MP4 animations to show motion or results.  
- **Use consistent section headings** – for example: *Prerequisites*, *Protocol Mode*, *Setup*, *Execution*, and *Variations*.  
- **Ensure smooth execution** – notebooks must run cleanly from start to finish without errors or manual input.  
- **Always include a simulation run** - recipes must support `protocol_mode == "simulation"` to allow a complete *in silico* execution of the automated protocol (aP) without errors, and remain valid when executed on actual hardware.  
- **Keep language accessible** – avoid hardware-specific jargon without context. Even if a recipe targets a particular machine (for example, `HamiltonSTAR`), write so readers unfamiliar with that platform can follow the logic.  
- **Be explicit about external dependencies** – if a recipe relies on components outside PyLabRobot (for example, third-party Python libraries, SDKs, or local data files), **document this clearly** in the *Prerequisites* section.  
- **Make recipes concise and complete** – each should be self-contained and reproducible, demonstrating a full automation concept without hidden dependencies.

<hr>

## 7. Questions & Support

For questions, feedback, or collaboration on Cookbook recipes - including structure, formatting, or contribution workflow - 
visit the community forum at [discuss.pylabrobot.org](https://discuss.pylabrobot.org).

<hr>

Thank you for helping expand the PyLabRobot Cookbook - concise, self-contained, and well-documented recipes are invaluable for both human developers and AI systems.  
They illustrate how to write complete, reproducible automation protocols and form the foundation for AI-assisted protocol generation.
