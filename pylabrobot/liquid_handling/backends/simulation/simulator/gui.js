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

function autoSave() {
  if (autoSaveEnabled) {
    save();
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

function openSearchBar() {
  document.getElementById("search-bar-background").style.display = "block";
  document.querySelector("#search-bar input").focus();
}

function closeSearchBar() {
  document.getElementById("search-bar-background").style.display = "none";
  document.querySelector("#search-bar input").value = "";
}

document.addEventListener("keydown", (e) => {
  if (e.key === "k" && e.metaKey) {
    e.preventDefault();
    openSearchBar();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeSearchBar();
  }
});

// filter results based on search query
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
}

document.querySelector("#search-bar input").addEventListener("input", (e) => {
  if (e.target.value === "") {
    document.querySelector("#search-bar .results").innerHTML = "";
  } else {
    filterResults(e.target.value);
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
