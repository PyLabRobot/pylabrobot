let deckType = null;
let fileContents = null;

function showWelcome() {
  document.getElementById("welcome").style.display = "flex";
  document.getElementById("filename").style.display = "none";
}

function hideWelcome() {
  document.getElementById("welcome").style.display = "none";
}

function showChooseDeck() {
  document.getElementById("welcome").style.display = "flex";
  document.getElementById("choose-deck").style.display = "block";
}

function hideChooseDeck() {
  document.getElementById("choose-deck").style.display = "none";
}

function showFilename() {
  document.getElementById("welcome").style.display = "flex";
  document.getElementById("filename").style.display = "flex";
}

function hideFilename() {
  document.getElementById("filename").style.display = "none";
}

function chooseDeck(deck_type) {
  hideChooseDeck();
  showFilename();
  deckType = deck_type;
}

function showSecurityMessage() {
  document.getElementById("security-message").style.display = "block";
}

function hideSecurityMessage() {
  document.getElementById("security-message").style.display = "none";
}

function openFile(filename) {
  window.location.href = `/editor/${filename}`;
}

function backToChooseDeck() {
  showChooseDeck();
  hideFilename();
  hideSecurityMessage();
  fileContents = null;
  deckType = null;
}

window.addEventListener("load", () => {
  document
    .querySelectorAll(".deck")
    .forEach((deck) =>
      deck.addEventListener("click", () => chooseDeck(deck.dataset.deckType))
    );

  document
    .getElementById("open-existing-deck")
    .addEventListener("change", (e) => {
      const file = e.target.files[0];

      if (!file) {
        return;
      }

      const reader = new FileReader();

      reader.onload = (e) => {
        const deck = JSON.parse(e.target.result);
        fileContents = deck;

        // TODO: validate deck on the server

        hideChooseDeck();
        showFilename();
        showSecurityMessage();
      };

      reader.readAsText(file);
    });

  document
    .getElementById("welcome-back-to-deck")
    .addEventListener("click", () => {
      backToChooseDeck();
    });

  document.getElementById("create-new-deck").addEventListener("click", () => {
    const filename = document.getElementById("filename-field").value;

    if (!filename) {
      alert("Please enter a filename");
      return;
    }

    hideWelcome();

    let data = {};
    if (deckType !== null) {
      data = {
        type: "new_deck",
        deck_type: deckType,
      };
    } else if (fileContents !== null) {
      data = {
        type: "from_file",
        deck: fileContents,
      };
    } else {
      alert("Unknown error");
      return;
    }

    // Create new file
    fetch(`/create`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        filename: filename,
        ...data,
      }),
    })
      .then((res) => res.json())
      .then((res) => {
        if (res.error) {
          alert(res.error);
          return;
        }

        if (!res.success) {
          alert("Unknown error");
          return;
        }

        openFile(filename);
      });
  });
});

// open recent

document.addEventListener("DOMContentLoaded", () => {
  fetch("/recents")
    .then((res) => res.json())
    .then((data) => {
      const recentFiles = document.getElementById("recent-files");
      console.log(data);

      const files = data.files;
      if (files.length === 0) {
        recentFiles.innerHTML = "<p>No recent files</p>";
        return;
      }

      for (let i = 0; i < files.length; i++) {
        let file = files[i];

        const a = document.createElement("a");
        a.classList.add("recent-deck");
        a.href = `/editor/${file}`;

        const icon = document.createElement("i");
        icon.classList.add("bi", "bi-file-earmark");

        const code = document.createElement("code");
        code.innerText = file;

        a.appendChild(icon);
        a.appendChild(code);
        recentFiles.appendChild(a);
      }
    });
});
