(function () {
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return [].slice.call((root || document).querySelectorAll(sel)); }

  /* -------------------- FILTERING -------------------- */

  function applyFilters() {
    var active = $(".plr-filter-btn.active");
    var tag = active ? active.getAttribute("data-tag") : "All";
    var searchEl = $("#plr-card-search");
    var q = (searchEl ? searchEl.value : "").trim().toLowerCase();

    var cards = $all(".plr-card");
    var visibleCount = 0;

    cards.forEach(function (card) {
      var tags = (card.getAttribute("data-tags") || "");
      var title = (card.getAttribute("data-title") || "").toLowerCase();
      var desc = (card.getAttribute("data-desc") || "").toLowerCase();

      var tagOk = (tag === "All") || tags.split(/\s+/).filter(Boolean).includes(tag);
      var textOk = !q || title.includes(q) || desc.includes(q);

      if (tagOk && textOk) {
        card.classList.remove("hidden");
        card.style.display = "";
        visibleCount++;
      } else {
        card.classList.add("hidden");
        card.style.display = "none";
      }
    });

    var viewAll = $("#plr-view-all");
    if (viewAll) {
      viewAll.style.display = visibleCount < cards.length ? "flex" : "none";
    }
  }

  function onClickTag(e) {
    var btn = e.target.closest(".plr-filter-btn");
    if (!btn) return;
    $all(".plr-filter-btn").forEach(function (b) { b.classList.remove("active"); });
    btn.classList.add("active");
    applyFilters();
  }

  function onSearch() { applyFilters(); }

  function resetFilters() {
    $all(".plr-filter-btn").forEach(function (b) { b.classList.remove("active"); });
    var first = $(".plr-filter-btn[data-tag='All']");
    if (first) first.classList.add("active");
    var search = $("#plr-card-search");
    if (search) search.value = "";
    applyFilters();
    try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch (_) {}
  }

  /* -------------------- HOVER IMAGE SWAP -------------------- */
  // Robust: uses event delegation on the grid, so no missed bindings.
  // Works if <img> has: src="..." data-hover="..."
  function setupDelegatedImageHover() {
    var grid = $(".plr-card-grid");
    if (!grid) return;

    // Preload hover images to avoid flash on first hover
    $all(".plr-card-image img[data-hover]").forEach(function (img) {
      var h = img.getAttribute("data-hover");
      if (h) { var pre = new Image(); pre.src = h; }
      // Cache original src explicitly so we can always restore
      if (!img.getAttribute("data-src-original")) {
        img.setAttribute("data-src-original", img.getAttribute("src") || "");
      }
    });

    // pointerenter / pointerleave fire reliably even with nested elements
    grid.addEventListener("pointerenter", function (e) {
      var img = e.target.closest(".plr-card-image img");
      if (!img) return;
      var hoverSrc = img.getAttribute("data-hover");
      if (!hoverSrc) return;
      // Store original if not already stored
      if (!img.getAttribute("data-src-original")) {
        img.setAttribute("data-src-original", img.getAttribute("src") || "");
      }
      // Swap to hover
      if (img.getAttribute("src") !== hoverSrc) {
        img.setAttribute("src", hoverSrc);
      }
    }, true);

    grid.addEventListener("pointerleave", function (e) {
      var img = e.target.closest(".plr-card-image img");
      if (!img) return;
      var original = img.getAttribute("data-src-original");
      if (!original) return;
      // Restore original
      if (img.getAttribute("src") !== original) {
        img.setAttribute("src", original);
      }
    }, true);
  }

  /* -------------------- INIT -------------------- */

  function init() {
    var menu = $(".plr-filter-menu");
    if (menu && !menu.querySelector(".plr-filter-btn.active")) {
      var first = menu.querySelector(".plr-filter-btn[data-tag='All']");
      if (first) first.classList.add("active");
    }
    if (menu) menu.addEventListener("click", onClickTag);

    var search = $("#plr-card-search");
    if (search) search.addEventListener("input", onSearch);

    var viewAllBtn = $("#plr-view-all button");
    if (viewAllBtn) viewAllBtn.addEventListener("click", resetFilters);

    applyFilters();
    setupDelegatedImageHover();
  }

  if (document.readyState !== "loading") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
