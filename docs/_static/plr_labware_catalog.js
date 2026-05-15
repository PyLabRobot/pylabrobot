(function () {
  "use strict";

  const state = {
    index: null,
    query: "",
    vendor: "All",
    section: "All",
    selectedDefinition: null,
    viewer: null,
  };

  function getUrlRoot() {
    if (window.DOCUMENTATION_OPTIONS && window.DOCUMENTATION_OPTIONS.URL_ROOT) {
      return window.DOCUMENTATION_OPTIONS.URL_ROOT;
    }
    if (document.documentElement && document.documentElement.dataset.content_root) {
      return document.documentElement.dataset.content_root;
    }
    return "";
  }

  function staticUrl(path) {
    return `${getUrlRoot()}${path}`;
  }

  function catalogIndexUrl() {
    const currentScript =
      document.currentScript ||
      document.querySelector('script[src*="plr_labware_catalog.js"]');
    if (currentScript && currentScript.src) {
      return new URL("labware_geometry_index.json", currentScript.src).toString();
    }
    return staticUrl("_static/labware_geometry_index.json");
  }

  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function unique(values) {
    return Array.from(new Set(values.filter(Boolean))).sort((left, right) =>
      left.localeCompare(right),
    );
  }

  function itemMatchesFilters(item) {
    const haystack = [
      item.definition,
      item.vendor,
      item.section,
      item.description_html,
    ]
      .join(" ")
      .toLowerCase();

    if (state.query && haystack.indexOf(state.query.toLowerCase()) === -1) {
      return false;
    }
    if (state.vendor !== "All" && item.vendor !== state.vendor) {
      return false;
    }
    if (state.section !== "All" && item.section !== state.section) {
      return false;
    }
    return true;
  }

  function setSelectOptions(select, values, selectedValue) {
    select.innerHTML = "";
    ["All"].concat(values).forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      option.selected = value === selectedValue;
      select.appendChild(option);
    });
  }

  function createModal() {
    const overlay = document.createElement("div");
    overlay.className = "plr-library-modal";
    overlay.setAttribute("hidden", "hidden");
    overlay.innerHTML = `
      <div class="plr-library-modal__backdrop"></div>
      <div class="plr-library-modal__dialog" role="dialog" aria-modal="true" aria-label="Labware 3D viewer">
        <div class="plr-library-modal__header">
          <div>
            <p class="plr-library-modal__eyebrow">3D geometry preview</p>
            <h3 class="plr-library-modal__title"></h3>
          </div>
          <button type="button" class="plr-library-modal__close" aria-label="Close 3D viewer">Close</button>
        </div>
        <div class="plr-library-modal__stage"></div>
      </div>
    `;
    document.body.appendChild(overlay);
    return overlay;
  }

  function ensureModal() {
    if (state.modal) {
      return state.modal;
    }

    const viewerApi = window.PLRGeometryViewer;
    if (!viewerApi || !viewerApi.CanvasCatalogViewer) {
      return null;
    }

    const modal = createModal();
    const stage = modal.querySelector(".plr-library-modal__stage");
    state.viewer = new viewerApi.CanvasCatalogViewer(stage);
    state.modal = modal;

    function closeModal() {
      modal.setAttribute("hidden", "hidden");
      document.body.classList.remove("plr-library-modal-open");
    }

    modal.querySelector(".plr-library-modal__close").addEventListener("click", closeModal);
    modal.querySelector(".plr-library-modal__backdrop").addEventListener("click", closeModal);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeModal();
    });

    return modal;
  }

  function openModel(definitionName) {
    const modal = ensureModal();
    const catalog = state.index.resources[definitionName];
    if (!modal || !catalog || !state.viewer) {
      return;
    }

    const modalTitle = modal.querySelector(".plr-library-modal__title");
    modalTitle.textContent = "";
    const modalCode = document.createElement("code");
    modalCode.textContent = definitionName;
    modalTitle.appendChild(modalCode);
    modal.removeAttribute("hidden");
    document.body.classList.add("plr-library-modal-open");
    state.viewer.setCatalog(catalog);
    window.requestAnimationFrame(() => state.viewer.resize());
  }

  function createCard(item) {
    const card = element("article", "plr-library-card");

    const media = element("button", "plr-library-card__media");
    media.type = "button";
    media.setAttribute("aria-label", `Open 3D preview for ${item.definition}`);
    if (item.image) {
      const image = document.createElement("img");
      image.src = item.image.startsWith("http") || item.image.startsWith("/")
        ? item.image
        : staticUrl(item.image);
      image.alt = item.definition;
      image.loading = "lazy";
      media.appendChild(image);
    } else {
      media.appendChild(element("div", "plr-library-card__placeholder", "No image"));
    }
    media.addEventListener("click", () => openModel(item.definition));

    const body = element("div", "plr-library-card__body");
    const vendor = element("div", "plr-library-card__vendor", item.vendor);
    const title = element("h3", "plr-library-card__title");
    const titleCode = document.createElement("code");
    titleCode.textContent = item.definition;
    title.appendChild(titleCode);
    const section = element("div", "plr-library-card__section", item.section || "Resource");
    const description = element("div", "plr-library-card__description");
    description.innerHTML = item.description_html || "";

    const footer = element("div", "plr-library-card__footer");
    const modelButton = element("button", "plr-library-card__action", "View 3D");
    modelButton.type = "button";
    modelButton.disabled = !item.has_geometry;
    modelButton.addEventListener("click", () => openModel(item.definition));

    footer.appendChild(modelButton);
    body.appendChild(vendor);
    body.appendChild(title);
    body.appendChild(section);
    if (item.description_html) body.appendChild(description);
    body.appendChild(footer);
    card.appendChild(media);
    card.appendChild(body);
    return card;
  }

  function renderCatalog(root) {
    const items = (state.index.items || []).filter(itemMatchesFilters);
    const grid = root.querySelector(".plr-catalog-grid");
    const count = root.querySelector(".plr-catalog-count");
    grid.innerHTML = "";
    count.textContent = `${items.length} resources`;

    if (items.length === 0) {
      grid.appendChild(element("div", "plr-catalog-empty", "No labware matches these filters."));
      return;
    }

    items.forEach((item) => {
      grid.appendChild(createCard(item));
    });
  }

  function renderCatalogShell(root) {
    root.innerHTML = `
      <div class="plr-catalog-toolbar">
        <div class="plr-catalog-search">
          <label for="plr-catalog-search-input">Search</label>
          <input id="plr-catalog-search-input" type="search" placeholder="Plate, tiprack, Hamilton, 96..." />
        </div>
        <div class="plr-catalog-filter">
          <label for="plr-catalog-vendor">Vendor</label>
          <select id="plr-catalog-vendor"></select>
        </div>
        <div class="plr-catalog-filter">
          <label for="plr-catalog-section">Type</label>
          <select id="plr-catalog-section"></select>
        </div>
        <div class="plr-catalog-count"></div>
      </div>
      <div class="plr-catalog-grid"></div>
    `;

    const search = root.querySelector("#plr-catalog-search-input");
    const vendor = root.querySelector("#plr-catalog-vendor");
    const section = root.querySelector("#plr-catalog-section");
    setSelectOptions(vendor, unique(state.index.items.map((item) => item.vendor)), state.vendor);
    setSelectOptions(section, unique(state.index.items.map((item) => item.section)), state.section);
    search.value = state.query;

    search.addEventListener("input", () => {
      state.query = search.value;
      writeUrlState();
      renderCatalog(root);
    });
    vendor.addEventListener("change", () => {
      state.vendor = vendor.value;
      writeUrlState();
      renderCatalog(root);
    });
    section.addEventListener("change", () => {
      state.section = section.value;
      writeUrlState();
      renderCatalog(root);
    });

    renderCatalog(root);
  }

  function readUrlState() {
    const params = new URLSearchParams(window.location.search);
    state.query = params.get("q") || "";
    state.vendor = params.get("vendor") || "All";
    state.section = params.get("section") || "All";
  }

  function writeUrlState() {
    const params = new URLSearchParams(window.location.search);
    if (state.query) params.set("q", state.query);
    else params.delete("q");
    if (state.vendor && state.vendor !== "All") params.set("vendor", state.vendor);
    else params.delete("vendor");
    if (state.section && state.section !== "All") params.set("section", state.section);
    else params.delete("section");
    const queryString = params.toString();
    const newUrl = `${window.location.pathname}${queryString ? "?" + queryString : ""}${window.location.hash}`;
    window.history.replaceState(null, "", newUrl);
  }

  function initializeCatalogPage() {
    const root = document.getElementById("plr-labware-catalog");
    if (!root) {
      return;
    }

    readUrlState();
    root.innerHTML = `<div class="plr-catalog-loading">Loading catalog...</div>`;
    fetch(catalogIndexUrl())
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((index) => {
        state.index = index;
        renderCatalogShell(root);
      })
      .catch((error) => {
        root.innerHTML = `<div class="plr-catalog-empty">Could not load the generated catalog index: ${error.message}</div>`;
      });
  }

  document.addEventListener("DOMContentLoaded", initializeCatalogPage);
})();
