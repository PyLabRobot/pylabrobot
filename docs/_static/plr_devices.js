/* Search and chip filtering for device tables rendered by the plr_devices extension. */

(function () {
  var AUTO_SHOW_MODELS_MAX_DEVICES = 10;

  function all(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  function activeFilters(container) {
    var filters = {};
    all(".plr-device-chip[aria-pressed='true']", container).forEach(function (chip) {
      var field = chip.getAttribute("data-field");
      (filters[field] = filters[field] || []).push(chip.getAttribute("data-value"));
    });
    return filters;
  }

  function rowMatches(row, field, wanted) {
    if (field === "capabilities") {
      var have = (row.getAttribute("data-capabilities") || "").split("|");
      return wanted.some(function (value) {
        return have.indexOf(value) !== -1;
      });
    }
    return wanted.indexOf(row.getAttribute("data-" + field)) !== -1;
  }

  function textMatches(row, terms) {
    var haystack = row.getAttribute("data-search") || "";
    return terms.every(function (term) {
      return haystack.indexOf(term) !== -1;
    });
  }

  function applyModels(container, terms, visibleDeviceIds, visibleDeviceCount) {
    var rowsByDevice = Object.create(null);

    all(".plr-device-model-row", container).forEach(function (row) {
      var deviceId = row.getAttribute("data-device-id");
      (rowsByDevice[deviceId] = rowsByDevice[deviceId] || []).push(row);
    });

    var hasVisibleModels = Object.keys(rowsByDevice).some(function (deviceId) {
      return visibleDeviceIds[deviceId];
    });
    var autoShow =
      terms.length > 0 &&
      visibleDeviceCount > 0 &&
      visibleDeviceCount <= AUTO_SHOW_MODELS_MAX_DEVICES &&
      hasVisibleModels;
    var override = container._plrModelsOverride;
    var showModels = override === null ? autoShow : override;

    Object.keys(rowsByDevice).forEach(function (deviceId) {
      var modelRows = rowsByDevice[deviceId];
      var matchingRows = terms.length
        ? modelRows.filter(function (row) {
            return textMatches(row, terms);
          })
        : [];
      var onlyMatchingModels = matchingRows.length > 0;

      modelRows.forEach(function (row) {
        var matchesSearch = !onlyMatchingModels || matchingRows.indexOf(row) !== -1;
        row.hidden = !(showModels && visibleDeviceIds[deviceId] && matchesSearch);
      });
    });

    container._plrModelsVisible = showModels;
    var toggle = container.querySelector(".plr-device-model-toggle");
    if (toggle) {
      toggle.setAttribute("aria-pressed", showModels ? "true" : "false");
      toggle.textContent = showModels ? "Hide models" : "Show models";
    }
  }

  function apply(container) {
    var input = container.querySelector(".plr-device-search__input");
    var query = (input ? input.value : "").trim().toLowerCase();
    var terms = query ? query.split(/\s+/) : [];
    var filters = activeFilters(container);
    var rows = all(".plr-device-row", container);
    var visible = 0;
    var visibleDeviceIds = Object.create(null);

    rows.forEach(function (row) {
      var textOk = textMatches(row, terms);
      var filterOk = Object.keys(filters).every(function (field) {
        return rowMatches(row, field, filters[field]);
      });
      var show = textOk && filterOk;
      row.hidden = !show;
      if (show) {
        visible++;
        visibleDeviceIds[row.getAttribute("data-device-id")] = true;
      }
    });

    applyModels(container, terms, visibleDeviceIds, visible);

    var empty = container.querySelector(".plr-device-empty");
    if (empty) empty.hidden = visible !== 0;
  }

  function init(container) {
    container._plrModelsOverride = null;
    container._plrModelsVisible = false;

    var input = container.querySelector(".plr-device-search__input");
    if (input) {
      input.addEventListener("input", function () {
        if (container._plrModelsOverride === false) container._plrModelsOverride = null;
        apply(container);
      });
      // A page linked as `...#device-<id>` should not hide that row behind a stale query.
      input.value = "";
    }

    var modelToggle = container.querySelector(".plr-device-model-toggle");
    if (modelToggle) {
      modelToggle.addEventListener("click", function () {
        container._plrModelsOverride = !container._plrModelsVisible;
        apply(container);
      });
    }

    all(".plr-device-chip", container).forEach(function (chip) {
      chip.addEventListener("click", function () {
        chip.setAttribute("aria-pressed", chip.getAttribute("aria-pressed") === "true" ? "false" : "true");
        apply(container);
      });
    });

    apply(container);
  }

  /* -------------------- TOOLTIPS -------------------- */

  /* The table is a horizontal scroll container, which clips absolutely positioned children. So a
     single tooltip lives on <body> and is positioned against the hovered element instead. */

  var tooltip = null;

  function showTip(trigger) {
    var text = trigger.getAttribute("data-tip");
    if (!text) return;

    if (!tooltip) {
      tooltip = document.createElement("div");
      tooltip.className = "plr-tooltip";
      tooltip.setAttribute("role", "tooltip");
      document.body.appendChild(tooltip);
    }
    tooltip.textContent = text;
    tooltip.setAttribute("data-visible", "true");

    var anchor = trigger.getBoundingClientRect();
    var box = tooltip.getBoundingClientRect();
    var margin = 8;

    // Below the trigger, unless that would run off the bottom of the viewport.
    var top = anchor.bottom + 6;
    if (top + box.height > window.innerHeight - margin) top = anchor.top - box.height - 6;

    // Left-aligned with the trigger, pulled back inside the viewport when necessary.
    var left = Math.min(anchor.left, window.innerWidth - box.width - margin);

    tooltip.style.top = Math.max(margin, top) + "px";
    tooltip.style.left = Math.max(margin, left) + "px";
  }

  function hideTip() {
    if (tooltip) tooltip.setAttribute("data-visible", "false");
  }

  function initTooltips() {
    document.addEventListener("mouseover", function (e) {
      var trigger = e.target.closest(".plr-tip");
      if (trigger) showTip(trigger);
    });
    document.addEventListener("mouseout", function (e) {
      if (e.target.closest(".plr-tip")) hideTip();
    });
    document.addEventListener("focusin", function (e) {
      var trigger = e.target.closest(".plr-tip");
      if (trigger) showTip(trigger);
    });
    document.addEventListener("focusout", hideTip);
    window.addEventListener("scroll", hideTip, true);
  }

  /* -------------------- INIT -------------------- */

  function start() {
    all("[data-plr-devices]").forEach(init);
    initTooltips();

    // Deep links to a specific device should reveal and highlight its row.
    if (window.location.hash.indexOf("#device-") === 0) {
      var target = document.getElementById(window.location.hash.slice(1));
      if (target) target.scrollIntoView({ block: "center" });
    }
  }

  if (document.readyState !== "loading") start();
  else document.addEventListener("DOMContentLoaded", start);
})();
