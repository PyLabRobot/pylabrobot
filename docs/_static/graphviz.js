/** Link each generated SVG to its full-size view without depending on Sphinx's hashed filename. */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("object.graphviz").forEach((diagram) => {
    const paragraph = document.createElement("p");
    const link = document.createElement("a");
    link.href = diagram.data;
    link.textContent = "Open full-size diagram";
    link.target = "_blank";
    link.rel = "noopener";
    paragraph.appendChild(link);
    diagram.parentElement.appendChild(paragraph);
  });
});
