mode = MODE_GUI;

function newResourceName() {
  let i = 1;
  while (true) {
    const name = `Untitled Resource ${i}`;
    if (resources[name] === undefined) {
      return name;
    }
    i++;
  }
}

function addResource(resourceIdentifier) {
  fetch(
    `/resource/${resourceIdentifier}?` +
      new URLSearchParams({ name: newResourceName() })
  )
    .then((response) => response.json())
    .then((data) => {
      // Get the center of the deck.
      const deck = resources["deck"];
      const isHamilton = deck.constructor.name === "HamiltonDeck";
      const deckCenter = {
        x: deck.location.x + deck.size_x / 2,
        y: deck.location.y + deck.size_y / 2,
        z: isHamilton ? 100 : 0,
      };

      resource = loadResource(data);
      resource.location = deckCenter;
      deck.assignChild(resource);
      resource.draw(resourceLayer);
    });
}

function serializeState() {
  let state = {};
  for (let name in resources) {
    let resource = resources[name];
    if (resource.serializeState) {
      state[resource.name] = resource.serializeState();
    }
  }
  return state;
}

var saving = false;
function save() {
  const saveLabel = document.getElementById("save-label");
  saveLabel.style.display = "block";

  const data = {
    deck: resources["deck"].serialize(),
    state: serializeState(),
  };
  saving = true;
  fetch(`/editor/${filename}/save`, {
    method: "POST",
    body: JSON.stringify(data),
    headers: {
      "Content-Type": "application/json",
    },
  })
    .then((response) => response.json())
    .then((response) => {
      if (!response.success) {
        alert(`Error saving: ${response.error}`);
      }
      saving = false;
      saveLabel.style.display = "none";
    })
    .catch((error) => {
      alert(`Error saving: ${error}`);
      saving = false;
    });
}

var autoSaveTimeout = undefined;
const SAVING_WAIT_TIME = 1000; // ms
var previousDecks = [];
function autoSave() {
  // Save the state if it has changed.
  const deck = resources["deck"].serialize();
  let lastDeck = previousDecks[previousDecks.length - 1];
  if (JSON.stringify(deck) === JSON.stringify(lastDeck)) {
    return;
  }
  // max length of previous states is 30
  if (previousDecks.length >= 30) {
    previousDecks.shift();
  }
  previousDecks.push(deck);

  // Save the file after a delay to batch multiple changes into one save.
  if (autoSaveEnabled) {
    if (autoSaveTimeout) {
      clearTimeout(autoSaveTimeout);
    }

    autoSaveTimeout = setTimeout(() => {
      save();
      autoSaveTimeout = undefined;
    }, SAVING_WAIT_TIME);
  }
}

function undo() {
  if (previousDecks.length < 2) {
    return;
  }

  // The last entry is the current state, so we need to go back two.
  previousDecks.pop();
  let lastDeck = previousDecks.pop();

  resources["deck"].destroy();
  resources["deck"] = loadResource(lastDeck);
  resources["deck"].draw(resourceLayer);

  autoSave();
}

window.onbeforeunload = function () {
  if (saving || autoSaveTimeout !== undefined) {
    return "You have unsaved changes. Are you sure you want to leave?";
  }
};

function deleteResource(resource) {
  if (resource.canDelete) {
    selectedResource.destroy();
    autoSave();
    selectedResource = undefined;
  }
}

// Settings

let autoSaveEnabled = undefined;
let snappingEnabled = undefined;

function loadSettings() {
  // Load settings from local storage
  autoSaveEnabled = localStorage.getItem("autoSave") === "true";
  snappingEnabled = localStorage.getItem("snapping") === "true";

  // Set UI elements
  const enableSnapping = document.getElementById("enable-snapping");
  if (snappingEnabled) {
    enableSnapping.classList.add("enabled");
  } else {
    enableSnapping.classList.remove("enabled");
  }

  const enableAutoSave = document.getElementById("enable-auto-save");
  if (autoSaveEnabled) {
    enableAutoSave.classList.add("enabled");
  } else {
    enableAutoSave.classList.remove("enabled");
  }
}

function saveSettings() {
  // Save settings to local storage
  localStorage.setItem("autoSave", autoSaveEnabled);
  localStorage.setItem("snapping", snappingEnabled);
}

function saveAs(filename) {
  fetch(`/editor/${filename}/save`, {
    method: "POST",
    body: JSON.stringify({
      deck: resources["deck"].serialize(),
      state: serializeState(),
    }),
    headers: {
      "Content-Type": "application/json",
    },
  })
    .then((response) => response.json())
    .then((response) => {
      if (!response.success) {
        alert(`Error saving: ${response.error}`);
      } else {
        window.location = `/editor/${filename}`;
      }
    })
    .catch((error) => {
      alert(`Error saving: ${error}`);
    });
}

document.addEventListener("DOMContentLoaded", () => {
  const enableSnapping = document.getElementById("enable-snapping");
  const enableAutoSave = document.getElementById("enable-auto-save");

  loadSettings();

  enableAutoSave.addEventListener("click", () => {
    autoSaveEnabled = !autoSaveEnabled;
    enableAutoSave.classList.toggle("enabled");
    saveSettings();
  });

  enableSnapping.addEventListener("click", () => {
    snappingEnabled = !snappingEnabled;
    enableSnapping.classList.toggle("enabled");
    saveSettings();
  });

  document.getElementById("save-button").addEventListener("click", () => {
    save();
  });

  document.getElementById("save-as-button").addEventListener("click", () => {
    let filename = prompt("Enter a filename to save as:");
    if (filename) {
      saveAs(filename);
    }
  });
});

// Search bar

var searchOpen = false;

function openSearchBar() {
  document.getElementById("search-bar-background").style.display = "block";
  document.querySelector("#search-bar input").focus();
  searchOpen = true;
}

function closeSearchBar() {
  document.getElementById("search-bar-background").style.display = "none";
  document.querySelector("#search-bar input").value = "";
  searchOpen = false;
}

var highlightedResultIndex = 0;

function filterResults(query) {
  function match(name) {
    // match if query is a substring of name
    if (name.toLowerCase().includes(query.toLowerCase())) {
      return true;
    }

    // check all words in query are substrings of name
    let words = query.toLowerCase().split(" ");
    if (words.every((word) => name.toLowerCase().includes(word))) {
      return true;
    }

    return false;
  }

  const sections = [
    { title: "Tip Racks", names: allTipRackNames.filter(match) },
    { title: "Plates", names: allPlateNames.filter(match) },
    { title: "Plate Carriers", names: allPlateCarriers.filter(match) },
    { title: "Tip Rack Carriers", names: allTipRackCarriers.filter(match) },
  ];

  // if no results, show "No results found"
  if (sections.every((section) => section.names.length === 0)) {
    let results = document.querySelector("#search-bar .results");
    results.innerHTML = "";
    let noResults = document.createElement("h4");
    noResults.innerText = "No results found";
    noResults.classList.add("no-results");
    results.appendChild(noResults);
    return;
  }

  let results = document.querySelector("#search-bar .results");
  results.innerHTML = "";
  sections.forEach((section) => {
    if (section.names.length === 0) {
      return;
    }

    let sectionElement = document.createElement("div");
    sectionElement.classList.add("result-section");

    let sectionTitle = document.createElement("div");
    sectionTitle.classList.add("result-section-title");
    sectionTitle.innerText = section.title;
    sectionElement.appendChild(sectionTitle);

    // Add at most 3 results per section
    for (let i = 0; i < Math.min(3, section.names.length); i++) {
      let name = section.names[i];
      let result = document.createElement("div");
      result.classList.add("result");
      result.innerText = name;

      result.addEventListener("click", () => {
        addResource(name);
        closeSearchBar();
      });

      sectionElement.appendChild(result);
    }

    results.appendChild(sectionElement);
  });

  highlightedResultIndex = 0;
  highlightSearchResult(0);
}

function highlightSearchResult(idx) {
  const results = document.querySelectorAll("#search-bar .result");
  if (results.length === 0) {
    return;
  }

  if (idx < 0) {
    idx = results.length - 1;
  } else if (idx >= results.length) {
    idx = 0;
  }

  for (let result of results) {
    result.classList.remove("highlighted");
  }

  results[idx].classList.add("highlighted");
  highlightedResultIndex = idx;
}

document.addEventListener("keydown", (e) => {
  if ((e.key === "k" && e.metaKey) || (e.key === "k" && e.ctrlKey)) {
    e.preventDefault();
    openSearchBar();
  } else if (e.key === "Escape") {
    closeSearchBar();
  } else if (["ArrowUp", "ArrowDown", "Enter"].includes(e.key)) {
    // Search bar navigation
    e.preventDefault();
    if (!searchOpen) {
      return;
    }
    if (e.key === "ArrowUp") {
      highlightSearchResult(highlightedResultIndex - 1);
    } else if (e.key === "ArrowDown") {
      highlightSearchResult(highlightedResultIndex + 1);
    } else if (e.key === "Enter") {
      const results = document.querySelectorAll("#search-bar .result");
      if (results.length === 0) {
        return;
      }
      results[highlightedResultIndex].click();
    }
  }
});

document.querySelector("#search-bar input").addEventListener("input", (e) => {
  if (e.target.value === "") {
    document.querySelector("#search-bar .results").innerHTML = "";
  } else {
    filterResults(e.target.value);
  }
});

// The sidebars

var leftSidebarOpened = true;
var rightSidebarOpened = true;

function fixEditorWidth() {
  const editor = document.getElementById("editor-column");

  // Remove all possible col-* classes.
  editor.classList.remove("col-12");
  editor.classList.remove("col-10");
  editor.classList.remove("col-8");

  // Add the correct class.
  if (leftSidebarOpened && rightSidebarOpened) {
    editor.classList.add("col-8");
  } else if (leftSidebarOpened || rightSidebarOpened) {
    editor.classList.add("col-10");
  } else {
    editor.classList.add("col-12");
  }
}

function openLeftSidebar() {
  const sidebar = document.getElementById("sidebar");
  sidebar.style.display = "block";
  leftSidebarOpened = true;

  fixEditorWidth();
}

function closeLeftSidebar() {
  const sidebar = document.getElementById("sidebar");
  sidebar.style.display = "none";
  leftSidebarOpened = false;

  fixEditorWidth();
}

function openRightSidebar() {
  const rightSidebar = document.getElementById("right-sidebar");
  rightSidebar.style.display = "block";
  rightSidebarOpened = true;

  fixEditorWidth();
}

function closeRightSidebar() {
  const rightSidebar = document.getElementById("right-sidebar");
  rightSidebar.style.display = "none";
  rightSidebarOpened = false;

  // Close the detail editors
  document.getElementById("tip-rack-detail-editor").style.display = "none";
  document.getElementById("tip-spot-detail-editor").style.display = "none";
  document.getElementById("plate-detail-editor").style.display = "none";
  document.getElementById("container-detail-editor").style.display = "none";

  fixEditorWidth();
}

function openTipRackEditor(tipRack) {
  const tipRackEditor = document.getElementById("tip-rack-detail-editor");
  tipRackEditor.style.display = "block";

  // Disable the option to empty the tip rack if none of the tip spots have a tip.
  document.getElementById("empty-tip-rack").disabled = tipRack.children.every(
    (tipSpot) => !tipSpot.has_tip
  );

  // Disable the option to fill the tip rack if all tip spots have a tip.
  document.getElementById("fill-tip-rack").disabled = tipRack.children.every(
    (tipSpot) => tipSpot.has_tip
  );
}

function openTipSpotEditor(tipSpot) {
  const tipSpotEditor = document.getElementById("tip-spot-detail-editor");
  tipSpotEditor.style.display = "block";

  // If the tip spot has a tip, enable the option to remove the tip.
  document.getElementById("empty-tip-spot").disabled = !tipSpot.has_tip;

  // If the tip spot does not have a tip, enable the option to add a tip.
  document.getElementById("fill-tip-spot").disabled = tipSpot.has_tip;
}

function addLiquids(liquidsElement, liquids) {
  // liquidsElement is in the DOM

  liquidsElement.innerHTML = "";
  for (let i = 0; i < liquids.length; i++) {
    let liquid = liquids[i];

    let liquidElement = document.createElement("div");
    liquidElement.classList.add("liquid", "mb-3");

    liquidElement.innerHTML = `
      <input
        type="text"
        class="form-control mb-2 liquid-name"
        placeholder="Liquid Name"
        data-liquid-index="${i}"
        value="${liquid.name}"
      />

      <div class="input-group">
        <input
          type="number"
          class="form-control liquid-volume"
          placeholder="Volume"
          value="${liquid.volume}"
          data-liquid-index="${i}"
        />

        <span class="input-group-text">uL</span>
      </div>
    `;

    liquidsElement.appendChild(liquidElement);
  }
}

function openContainerEditor(container) {
  const containerEditor = document.getElementById("container-detail-editor");
  containerEditor.style.display = "block";

  // Set the container volume
  document.getElementById("container-max-volume").value = container.maxVolume;

  // Add liquid inputs to the DOM
  const liquids = document.getElementById("container-liquids");
  addLiquids(liquids, container.liquids);

  // Add event listeners to liquid inputs
  for (let input of document.querySelectorAll("#container-liquids input")) {
    input.addEventListener("input", (event) => {
      const liquidIndex = parseInt(event.target.dataset.liquidIndex);
      const liquid = container.liquids[liquidIndex];

      if (event.target.classList.contains("liquid-name")) {
        liquid.name = event.target.value;
      } else if (event.target.classList.contains("liquid-volume")) {
        liquid.volume = parseFloat(event.target.value);
      }

      container.update();
      autoSave();
    });
  }
}

function openPlateEditor(plate) {
  const plateEditor = document.getElementById("plate-detail-editor");
  plateEditor.style.display = "block";

  // Use A1 to get information about wells in the plate
  const well = plate.children[0];

  // Set the plate volume
  document.getElementById("plate-max-volume").value = well.maxVolume;

  // Add liquid inputs to the DOM
  const liquids = document.getElementById("plate-liquids");
  addLiquids(liquids, well.liquids);

  // Add event listeners to liquid inputs
  for (let input of document.querySelectorAll("#plate-liquids input")) {
    input.addEventListener("input", (event) => {
      const liquidIndex = parseInt(event.target.dataset.liquidIndex);
      const liquid = well.liquids[liquidIndex];

      if (event.target.classList.contains("liquid-name")) {
        liquid.name = event.target.value;
      } else if (event.target.classList.contains("liquid-volume")) {
        liquid.volume = parseFloat(event.target.value);
      }

      // Copy the liquid information from the first well to all wells so that
      // they may be edited together.
      for (let well of plate.children) {
        let liquidsCopy = plate.children[0].liquids.map((liquid) => {
          return { ...liquid };
        });
        well.liquids = liquidsCopy;
      }

      plate.update();
      autoSave();
    });
  }
}

function loadEditor(resource) {
  openRightSidebar();

  // Update resource name
  document.getElementById("resource-name").value = resource.name;

  // Update resource location
  document.getElementById("resource-x").value = resource.location.x;
  document.getElementById("resource-y").value = resource.location.y;
  document.getElementById("resource-z").value = resource.location.z;

  // Open detailed editor, if available
  if (resource.constructor.name === "TipRack") {
    openTipRackEditor(resource);
  } else if (resource.constructor.name === "TipSpot") {
    openTipSpotEditor(resource);
  } else if (resource.constructor.name === "Plate") {
    openPlateEditor(resource);
  } else if (["Container", "Well"].includes(resource.constructor.name)) {
    openContainerEditor(resource);
  }
}

closeRightSidebar();

// Add event listeners to resource properties
document.getElementById("resource-name").addEventListener("input", (event) => {
  selectedResource.name = event.target.value;
  selectedResource.update();
});

document.getElementById("resource-x").addEventListener("input", (event) => {
  selectedResource.location.x = parseFloat(event.target.value);
  selectedResource.update();
});

document.getElementById("resource-y").addEventListener("input", (event) => {
  selectedResource.location.y = parseFloat(event.target.value);
  selectedResource.update();
});

document.getElementById("resource-z").addEventListener("input", (event) => {
  selectedResource.location.z = parseFloat(event.target.value);
  selectedResource.update();
});

for (let input of document.querySelectorAll("#right-sidebar input")) {
  input.addEventListener("input", () => {
    autoSave();
  });
}

document.getElementById("fill-tip-rack").addEventListener("click", () => {
  if (selectedResource.constructor.name !== "TipRack") {
    return;
  }
  for (let tipSpot of selectedResource.children) {
    tipSpot.has_tip = true;
  }
  selectedResource.update();
  openTipRackEditor(selectedResource); // reopen editor to reload, bit hacky
  autoSave();
});
document.getElementById("empty-tip-rack").addEventListener("click", () => {
  if (selectedResource.constructor.name !== "TipRack") {
    return;
  }
  for (let tipSpot of selectedResource.children) {
    tipSpot.has_tip = false;
  }
  selectedResource.update();
  openTipRackEditor(selectedResource); // reopen editor to reload, bit hacky
  autoSave();
});

document.getElementById("empty-tip-spot").addEventListener("click", () => {
  if (selectedResource.constructor.name !== "TipSpot") {
    return;
  }
  selectedResource.has_tip = false;
  selectedResource.update();
  openTipSpotEditor(selectedResource); // reopen editor to reload, bit hacky
  autoSave();
});
document.getElementById("fill-tip-spot").addEventListener("click", () => {
  if (selectedResource.constructor.name !== "TipSpot") {
    return;
  }
  selectedResource.has_tip = true;
  selectedResource.update();
  openTipSpotEditor(selectedResource); // reopen editor to reload, bit hacky
  autoSave();
});

document
  .getElementById("container-max-volume")
  .addEventListener("input", (event) => {
    if (selectedResource.constructor.name !== "Well") {
      return;
    }
    selectedResource.maxVolume = parseFloat(event.target.value);
    selectedResource.update();
    autoSave();
  });
document
  .getElementById("add-container-liquid")
  .addEventListener("click", () => {
    if (selectedResource.constructor.name !== "Well") {
      return;
    }
    selectedResource.liquids.push({ name: "Untitled Liquid", volume: 0 });
    selectedResource.update();
    openContainerEditor(selectedResource); // reopen editor to reload, bit hacky
    autoSave();
  });

document
  .getElementById("plate-max-volume")
  .addEventListener("input", (event) => {
    if (selectedResource.constructor.name !== "Plate") {
      return;
    }
    let volume = parseFloat(event.target.value);
    for (let well of selectedResource.children) {
      well.maxVolume = volume;
    }
    selectedResource.update();
    autoSave();
  });
document.getElementById("add-plate-liquid").addEventListener("click", () => {
  if (selectedResource.constructor.name !== "Plate") {
    return;
  }

  // Add the liquid to all wells in the plate
  for (let well of selectedResource.children) {
    well.addLiquid({ name: "Untitled Liquid", volume: 0 });
    well.update();
  }

  openPlateEditor(selectedResource); // reopen editor to reload, bit hacky
  autoSave();
});

// Some keyboard shortcuts

document.addEventListener("keydown", (e) => {
  if (e.key === "ArrowRight") {
    e.preventDefault();
    if (selectedResource) {
      selectedResource.location.x += 1;
      selectedResource.update();
      autoSave();
    }
  } else if (e.key === "ArrowLeft") {
    e.preventDefault();
    if (selectedResource) {
      selectedResource.location.x -= 1;
      selectedResource.update();
      autoSave();
    }
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    if (selectedResource) {
      selectedResource.location.y += 1;
      selectedResource.update();
      autoSave();
    }
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    if (selectedResource) {
      selectedResource.location.y -= 1;
      selectedResource.update();
      autoSave();
    }
  } else if (e.key === "Delete") {
    e.preventDefault();
    deleteResource(selectResource);
  } else if (e.key === "Escape") {
    e.preventDefault();
    unselectResource();
    closeContextMenu();
  } else if ((e.key === "c" && e.metaKey) || (e.key === "c" && e.ctrlKey)) {
    e.preventDefault();
    copyResource(selectedResource);
  } else if ((e.key === "x" && e.metaKey) || (e.key === "x" && e.ctrlKey)) {
    e.preventDefault();
    cutResource(selectedResource);
  } else if ((e.key === "v" && e.metaKey) || (e.key === "v" && e.ctrlKey)) {
    e.preventDefault();
    pasteResource();
  } else if ((e.key === "s" && e.metaKey) || (e.key === "s" && e.ctrlKey)) {
    e.preventDefault();
    save();
  } else if ((e.key === "z" && e.metaKey) || (e.key === "z" && e.ctrlKey)) {
    e.preventDefault();
    undo();
  }
});

// Custom resources
var customResourceModalOpen = false;

function openCustomResourceModal() {
  let customResourceModal = document.getElementById("custom-resource-modal");
  customResourceModal.style.display = "block";
  customResourceModalOpen = true;

  // Set default resource name
  document.getElementById("custom-resource-name").value = newResourceName();
}

function closeCustomResourceModal() {
  let customResourceModal = document.getElementById("custom-resource-modal");
  customResourceModal.style.display = "none";
  customResourceModalOpen = false;
}

document
  .getElementById("open-custom-resource-modal")
  .addEventListener("click", function (e) {
    openCustomResourceModal();
  });

document.addEventListener("keydown", function (e) {
  if (e.key == "Escape" && customResourceModalOpen) {
    closeCustomResourceModal();
  }
});

document
  .getElementById("custom-resource-aspiratable")
  .addEventListener("change", function (e) {
    let aspiratable = document.getElementById(
      "custom-resource-aspiratable"
    ).checked;

    if (aspiratable) {
      document.getElementById("custom-resource-max-volume-div").style.display =
        "block";

      // set default value to size_x * size_y * size_z
      let size_x = parseFloat(
        document.getElementById("custom-resource-size_x").value
      );
      let size_y = parseFloat(
        document.getElementById("custom-resource-size_y").value
      );
      let size_z = parseFloat(
        document.getElementById("custom-resource-size_z").value
      );
      document.getElementById("custom-resource-max-volume").value =
        (size_x * size_y * size_z) / 1000;
    } else {
      document.getElementById("custom-resource-max-volume-div").style.display =
        "none";
    }
  });

document
  .getElementById("add-custom-resource")
  .addEventListener("click", function () {
    const deck = resources["deck"];
    const deckCenter = {
      x: deck.location.x + deck.size_x / 2,
      y: deck.location.y + deck.size_y / 2,
      z: 0,
    };

    let resourceName = document.getElementById("custom-resource-name").value;

    let size_x = parseFloat(
      document.getElementById("custom-resource-size_x").value
    );
    let size_y = parseFloat(
      document.getElementById("custom-resource-size_y").value
    );
    let size_z = parseFloat(
      document.getElementById("custom-resource-size_z").value
    );

    let aspiratable = document.getElementById(
      "custom-resource-aspiratable"
    ).checked;

    let resourceData = {
      name: resourceName,
      location: deckCenter,
      size_x: size_x,
      size_y: size_y,
      size_z: size_z,
      children: [],
    };

    let resource;
    if (aspiratable) {
      resource = new Container(resourceData);
    } else {
      resource = new Resource(resourceData);
    }
    // TODO: there should be a better way to do the three below.
    resources[resourceName] = resource;
    deck.assignChild(resource);
    resource.draw(resourceLayer);

    closeCustomResourceModal();
  });

// Loading the library

let allPlateNames = [];
let allTipRackNames = [];
let allPlateCarriers = [];
let allTipRackCarriers = [];

function addResourceListToSidebar(resourceList, title) {
  let labwareList = document.getElementById("labware-list");
  const sectionId = `labware-list-${title}`.replaceAll(" ", "-");

  labwareList.innerHTML += `
  <li class="mb-1">
    <button
      class="btn btn-toggle d-inline-flex align-items-center rounded border-0 collapsed"
      data-bs-toggle="collapse"
      data-bs-target="#${sectionId}-collapse"
      aria-expanded="false"
    >
      ${title}
    </button>

    <div class="collapse" id="${sectionId}-collapse">
      <ul class="btn-toggle-nav list-unstyled fw-normal pb-1 small labware-list" id="${sectionId}">
      </ul>
    </div>
  </li>
  `;

  let list = document.getElementById(sectionId);

  for (let resource of resourceList) {
    list.innerHTML += `
      <li>
        <button
          href="#"
          class="link-body-emphasis d-inline-flex text-decoration-none rounded border-0 mb-1"
          onclick="addResource('${resource}')"
        >
          ${resource}
        </button>
      </li>
    `;
  }
}

function loadResourceNames() {
  fetch("/resources")
    .then((response) => response.json())
    .then((data) => {
      allPlateNames = data.plates;
      addResourceListToSidebar(allPlateNames, "Plates");

      allTipRackNames = data.tip_racks;
      addResourceListToSidebar(allTipRackNames, "Tip Racks");

      allPlateCarriers = data.plate_carriers;
      addResourceListToSidebar(allPlateCarriers, "Plate Carriers");

      allTipRackCarriers = data.tip_carriers;
      addResourceListToSidebar(allTipRackCarriers, "Tip Rack Carriers");
    });
}

window.addEventListener("load", loadResourceNames);

function showFileNotFoundError() {
  let error = document.getElementById("file-not-found");
  error.style.display = "block";
}

function hideEditor() {
  let editor = document.getElementById("editor");
  editor.style.display = "none";
}

var contextMenuOpen = false;
function openContextMenu() {
  // Show paste if we have a copied resource.
  let pasteElement = document.getElementById("context-menu-paste");
  pasteElement.style.display = copiedResource ? "initial" : "none";

  // Show cut / copy if there is a selected resource and it's deletable.
  let cutElement = document.getElementById("context-menu-cut");
  let copyElement = document.getElementById("context-menu-copy");
  cutElement.style.display =
    selectedResource && selectedResource.canDelete ? "initial" : "none";
  copyElement.style.display =
    selectedResource && selectedResource.canDelete ? "initial" : "none";

  // Show delete if the selected resource can be deleted.
  let deleteElement = document.getElementById("context-menu-delete");
  deleteElement.style.display =
    selectedResource && selectedResource.canDelete ? "initial" : "none";

  contextMenuOpen = true;

  // Open the context menu at the mouse position.
  const menu = document.getElementById("context-menu");
  var containerRect = stage.container().getBoundingClientRect();

  menu.style.display = "block";
  menu.style.left = `${stage.getPointerPosition().x + containerRect.left}px`;
  menu.style.top = `${stage.getPointerPosition().y + containerRect.top}px`;
  menu.style.zIndex = 1000;
}

function closeContextMenu() {
  document.getElementById("context-menu").style.display = "none";
  contextMenuOpen = false;
}

var copiedResource;

function copyResource(resource) {
  copiedResource = resource;
}

function cutResource(resource) {
  resource = loadResource(resource.serialize()); // make a copy before deleting
  copyResource(resource);
  deleteResource(resource);
}

function pasteResource() {
  if (copiedResource === undefined) {
    return;
  }

  // Only make a copy of resource once we actually paste it (supports multiple pastes & is faster)
  copiedResource = loadResource(copiedResource.serialize());

  let deck = resources["deck"];
  let pointerLocation = resourceLayer.getRelativePointerPosition();
  copiedResource.location = {
    x: pointerLocation.x - deck.getAbsoluteLocation().x,
    y: pointerLocation.y - deck.getAbsoluteLocation().y,
    z: 0,
  };

  copiedResource.name = newResourceName();

  // Below should be abstracted.
  copiedResource.update();
  resources[copiedResource.name] = copiedResource;
  resources["deck"].assignChild(copiedResource);
  autoSave();
}

function moveToTop(resource) {
  // Recursively move the resource to the top of the layer.
  resource.group.moveToTop();
  if (resource.parent !== undefined) {
    moveToTop(resource.parent);
  }
}

resourceLayer.on("dragstart", (e) => {
  // Move dragged resource to top of layer
  let resource = e.target.resource;
  moveToTop(resource);

  // Show the trash icon
  resourceLayer.add(trash);
  trash.moveToTop();
});

function _deleteSnappingLines() {
  if (snapLines.length > 0) {
    for (let i = snapLines.length - 1; i >= 0; i--) {
      snapLines[i].destroy();
      snapLines.splice(i, 1);
    }
  }

  if (snappingBox !== undefined) {
    snappingBox.destroy();
  }
}

resourceLayer.on("dragmove", (e) => {
  if (tooltip !== undefined) {
    tooltip.destroy();
  }

  _deleteSnappingLines();

  // Get the absolute location of this resource in this drag. We replace the resource's relative
  // location with its drag location (drag is relative to the parent too).
  let resource = e.target.resource;
  x = resource.parent.getAbsoluteLocation().x + e.target.position().x;
  y = resource.parent.getAbsoluteLocation().y + e.target.position().y;

  // If we have a snapping box match, draw a snapping box indicator around the area.
  const snapResult = getSnappingResourceAndLocationAndSnappingBox(
    resource,
    x + resource.size_x / 2,
    y + resource.size_y / 2
  );

  if (snapResult !== undefined) {
    let {
      snappingBox: { x: snapX, y: snapY, width, height },
    } = snapResult;

    snappingBox = new Konva.Rect({
      x: snapX,
      y: snapY,
      width: width,
      height: height,
      fill: "rgba(0, 0, 0, 0.1)",
      stroke: "red",
      strokeWidth: 1,
      dash: [10, 5],
    });
    resourceLayer.add(snappingBox);
  } else {
    // If there is no box snapping match, check if there is a grid snapping match.
    let { snappingX, snappingY, resourceX, resourceY } = getSnappingGrid(
      x,
      y,
      resource.size_x,
      resource.size_y
    );

    // If we have a snapping match, show an indicator and snap to the grid.
    if (snappingX !== undefined) {
      e.target.x(resourceX - resource.parent.getAbsoluteLocation().x);

      // Draw a vertical line
      let snapLine = new Konva.Line({
        points: [snappingX, 0, snappingX, canvasHeight],
        stroke: "red",
        strokeWidth: 2,
        dash: [10, 5],
      });
      resourceLayer.add(snapLine);
      snapLines.push(snapLine);
    }
    if (snappingY !== undefined) {
      e.target.y(resourceY - resource.parent.getAbsoluteLocation().y);

      // Draw a vertical line
      let snapLine = new Konva.Line({
        points: [0, snappingY, canvasWidth, snappingY],
        stroke: "red",
        strokeWidth: 2,
        dash: [10, 5],
      });
      resourceLayer.add(snapLine);
      snapLines.push(snapLine);
    }
  }
});

resourceLayer.on("dragend", (e) => {
  _deleteSnappingLines();

  // Get the absolute location of this resource in this drag. We replace the resource's relative
  // location with its drag location (drag is relative to the parent too).
  let resource = e.target.resource;
  x = resource.parent.getAbsoluteLocation().x + e.target.position().x;
  y = resource.parent.getAbsoluteLocation().y + e.target.position().y;

  const snapResult = getSnappingResourceAndLocationAndSnappingBox(
    resource,
    x + resource.size_x / 2,
    y + resource.size_y / 2
  );

  if (snapResult !== undefined) {
    const { resource: parent, location } = snapResult;

    if (parent === trash) {
      // Delete the plate.
      resource.destroy();
    } else {
      const { x, y } = location;

      // Update the deck layout with the new parent.
      if (resource.parent !== undefined) {
        resource.parent.unassignChild(resource);
      }
      resource.location = { x: x, y: y, z: 0 };
      parent.assignChild(resource);

      // Snap to position in UI after it has been added to the new UI group by assignChild.
      e.target.position({ x: x, y: y });
    }
  } else {
    // Update the deck layout with the new location.
    resource.location.x = x;
    resource.location.y = y;
    // Assign resource to deck.
    if (resource.parent !== undefined) {
      resource.parent.unassignChild(resource);
    }
    resources["deck"].assignChild(resource);
    e.target.position({ x: x, y: y });
  }

  // hide the trash icon
  trash.remove();

  autoSave();
});

function selectResource(resource) {
  selectedResource = resource;
  loadEditor(selectedResource);

  // Draw a selection box around the resource.
  selectedResource.mainShape.stroke("orange");
  selectedResource.mainShape.strokeWidth(1);
  selectedResource.mainShape.strokeEnabled(true);
}

function unselectResource() {
  closeRightSidebar();
  closeContextMenu();

  if (selectedResource !== undefined) {
    // Redraw the resource layer to remove the selection box.
    selectedResource.draw(resourceLayer);
  }

  selectedResource = undefined;
}

function handleClick(e) {
  // ignore if it is a context menu click
  if (e.evt.button === 2) {
    return;
  }

  if (tooltip !== undefined) {
    tooltip.destroy();
  }

  let resourceClicked = e.target.resource;

  if (resourceClicked === undefined) {
    // If the user clicked on the background, unselect the current resource.
    unselectResource();
  } else if (resourceClicked === selectedResource) {
    // If the user clicked on the selected resource, unselect it.
    unselectResource();
  } else if (
    ["HamiltonDeck", "OTDeck", "Deck"].includes(
      resourceClicked.constructor.name
    )
  ) {
    // The deck cannot be selected. If the user clicks on it, unselect the current resource.
    unselectResource();
  } else {
    unselectResource();
    // Select the resource.
    selectResource(resourceClicked);
  }
}

// on right click, show options
resourceLayer.on("contextmenu", (e) => {
  e.evt.preventDefault();
  selectedResource = e.target.resource;

  // If the resource is not the trash, show the context menu.
  if (selectResource !== trash) {
    openContextMenu();
  }
});

document.addEventListener("DOMContentLoaded", () => {
  fetch(`/data/${filename}`)
    .then((response) => response.json())
    .then((response) => {
      if (response.not_found) {
        hideEditor();
        showFileNotFoundError();
        return;
      }

      if (response.error) {
        alert(response.error);
        return;
      }

      if (response.data === undefined) {
        alert("No data found");
        return;
      }

      const deckData = response.data;
      let resource = loadResource(deckData);
      resource.draw(resourceLayer);
      previousDecks.push(resource.serialize());
    })
    .catch((error) => {
      console.log(error);
      alert(error);
    });

  document
    .getElementById("context-menu-delete")
    .addEventListener("click", () => {
      deleteResource(selectedResource);
      closeContextMenu();
    });

  document.getElementById("context-menu-copy").addEventListener("click", () => {
    copyResource(selectedResource);
    closeContextMenu();
  });

  document.getElementById("context-menu-cut").addEventListener("click", () => {
    cutResource(selectedResource);
    closeContextMenu();
  });

  document
    .getElementById("context-menu-paste")
    .addEventListener("click", () => {
      pasteResource();
      closeContextMenu();
    });
});

function afterStageSetup() {
  // Add click handler to stage
  stage.on("click", handleClick);

  // add a trash icon for deleting resources
  var imageObj = new Image();
  trash = new Konva.Image({
    x: 700,
    y: 100,
    image: imageObj,
    width: 50,
    height: 50,
  });
  imageObj.src = "/trash3.svg";
  // Flip the image vertically in place
  trash.scaleY(-1);
  trash.offsetY(50);
}
