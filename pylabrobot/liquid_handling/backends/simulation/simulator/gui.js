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
      const deckCenter = {
        x: deck.location.x + deck.size_x / 2,
        y: deck.location.y + deck.size_y / 2,
        z: 0,
      };

      resource = loadResource(data);
      resource.location = deckCenter;
      deck.assignChild(resource);
      resource.draw(resourceLayer);
    });
}

var saving = false;
function save() {
  const saveLabel = document.getElementById("save-label");
  saveLabel.style.display = "block";

  const data = resources["deck"].serialize();
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
function autoSave() {
  // Save the file after a delay.
  // This is to batch multiple changes into one save.

  if (autoSaveEnabled) {
    if (autoSaveTimeout) {
      clearTimeout(autoSaveTimeout);
    }

    autoSaveTimeout = setTimeout(() => {
      save();
    }, SAVING_WAIT_TIME);
  }
}

window.addEventListener("keydown", (e) => {
  if (e.key === "s" && e.metaKey) {
    e.preventDefault();
    save();
  }
});

window.onbeforeunload = function () {
  if (saving) {
    return "You have unsaved changes. Are you sure you want to leave?";
  }
};

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
  if (e.key === "k" && e.metaKey) {
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

  fixEditorWidth();
}

function loadEditor(resource) {
  openRightSidebar();

  document.getElementById("resource-type").innerText =
    resource.constructor.name;

  // Update resource name
  document.getElementById("resource-name").value = resource.name;

  // Update resource location
  document.getElementById("resource-x").value = resource.location.x;
  document.getElementById("resource-y").value = resource.location.y;
  document.getElementById("resource-z").value = resource.location.z;
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

// If the user has not changed a property for 1 second, save the file.

for (let input of document.querySelectorAll("#right-sidebar input")) {
  input.addEventListener("input", () => {
    autoSave();
  });
}

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
      selectedResource.location.y -= 1;
      selectedResource.update();
      autoSave();
    }
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    if (selectedResource) {
      selectedResource.location.y += 1;
      selectedResource.update();
      autoSave();
    }
  } else if (e.key === "Delete") {
    e.preventDefault();
    if (selectedResource) {
      selectedResource.destroy();
      autoSave();
    }
  }
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
      <ul class="btn-toggle-nav list-unstyled fw-normal pb-1 small" id="${sectionId}">
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

function openContextMenu() {
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
}

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

      const data = response.data;
      let resource = loadResource(data);
      resource.draw(resourceLayer);
    })
    .catch((error) => {
      console.log(error);
      alert(error);
    });

  document.getElementById("delete").addEventListener("click", () => {
    selectedResource.destroy();
    autoSave();
    closeContextMenu();
  });
});
