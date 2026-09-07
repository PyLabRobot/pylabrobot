(function () {
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return [].slice.call((root || document).querySelectorAll(sel)); }

  /* -------------------- FILTERING -------------------- */

  function applyFilters() {
    var activeTags = $all(".plr-filter-btn.active")
      .map(b => b.getAttribute("data-tag"))
      .filter(t => t && t !== "All");

    var searchEl = $("#plr-card-search");
    var q = (searchEl ? searchEl.value : "").trim().toLowerCase();

    var cards = $all(".plr-card");
    var visibleCount = 0;

    cards.forEach(function (card) {
      var tags = (card.getAttribute("data-tags") || "").split(/\s+/).filter(Boolean);
      var title = (card.getAttribute("data-title") || "").toLowerCase();
      var desc = (card.getAttribute("data-desc") || "").toLowerCase();

      var tagOk = activeTags.length === 0 || activeTags.every(t => tags.includes(t));
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

    var tag = btn.getAttribute("data-tag");

    if (tag === "All") {
      $all(".plr-filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
    } else {
      btn.classList.toggle("active");
      var allBtn = $(".plr-filter-btn[data-tag='All']");
      if (allBtn) {
        if ($all(".plr-filter-btn.active").some(b => b.getAttribute("data-tag") !== "All"))
          allBtn.classList.remove("active");
        else
          allBtn.classList.add("active");
      }
    }

    applyFilters();
  }

  function onSearch() { applyFilters(); }

  function resetFilters() {
    $all(".plr-filter-btn").forEach(b => b.classList.remove("active"));
    var first = $(".plr-filter-btn[data-tag='All']");
    if (first) first.classList.add("active");
    var search = $("#plr-card-search");
    if (search) search.value = "";
    applyFilters();
    try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch (_) {}
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
  }

  if (document.readyState !== "loading") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
