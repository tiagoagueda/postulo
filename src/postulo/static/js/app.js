/*
 * Postulo's client-side behaviour, such as it is.
 *
 * Everything here is delegated from the document, so markup swapped in by htmx works
 * without re-binding. Inline handlers are avoided deliberately: the Content-Security-
 * Policy forbids inline script, and weakening it for the sake of an onchange attribute
 * would be a poor trade in an application that stores personal documents.
 */
(function () {
  "use strict";

  // Selects marked data-autosubmit save as soon as they change. Used by the board,
  // where making someone press a button to move a card guarantees stale statuses.
  document.addEventListener("change", function (event) {
    var control = event.target.closest("[data-autosubmit]");
    if (control && control.form) {
      control.form.requestSubmit();
    }
  });

  // Dragging a card between board columns. No library and no new endpoint: on drop the
  // card's own status menu is set and its form submitted, so the server path is exactly
  // the one the menu already uses and the timeline entry is written the same way.
  //
  // The menu stays. Drag and drop does not fire on touch screens and is not reachable
  // from a keyboard, so it is an addition to the control that works everywhere, never a
  // replacement for it.
  var dragging = null;

  function columnOf(node) {
    return node && node.closest ? node.closest("[data-board-column]") : null;
  }

  function highlight(column, on) {
    if (!column) {
      return;
    }
    column.classList.toggle("bg-ink-100", on);
    column.classList.toggle("dark:bg-ink-800", on);
  }

  function recount(column) {
    if (!column) {
      return;
    }
    var section = column.closest("section");
    var counter = section && section.querySelector("[data-column-count]");
    if (counter) {
      counter.textContent = String(column.querySelectorAll("[data-card]").length);
    }
    var empty = column.querySelector("[data-empty]");
    if (empty) {
      empty.hidden = column.querySelectorAll("[data-card]").length > 0;
    }
  }

  document.addEventListener("dragstart", function (event) {
    var card = event.target.closest && event.target.closest("[data-card]");
    if (!card) {
      return;
    }
    dragging = { card: card, from: columnOf(card) };
    card.classList.add("opacity-50");
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      // Firefox will not start a drag without something on the transfer.
      event.dataTransfer.setData("text/plain", card.dataset.card || "");
    }
  });

  document.addEventListener("dragend", function () {
    if (dragging) {
      dragging.card.classList.remove("opacity-50");
    }
    document.querySelectorAll("[data-board-column]").forEach(function (column) {
      highlight(column, false);
    });
    dragging = null;
  });

  document.addEventListener("dragover", function (event) {
    var column = columnOf(event.target);
    if (!dragging || !column) {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "move";
    }
    highlight(column, column !== dragging.from);
  });

  document.addEventListener("dragleave", function (event) {
    var column = columnOf(event.target);
    if (column && !column.contains(event.relatedTarget)) {
      highlight(column, false);
    }
  });

  document.addEventListener("drop", function (event) {
    var column = columnOf(event.target);
    if (!dragging || !column) {
      return;
    }
    event.preventDefault();
    highlight(column, false);
    var card = dragging.card;
    var from = dragging.from;
    var status = column.dataset.boardColumn;
    if (!status || column === from) {
      return;
    }
    var select = card.querySelector("select[name='status']");
    if (!select) {
      return;
    }
    // Optimistic: the card moves now, and the form that was already there does the
    // saving. If the server refuses, the page it sends back is the truth.
    column.appendChild(card);
    card.dataset.status = status;
    recount(from);
    recount(column);
    select.value = status;
    if (select.form) {
      select.form.requestSubmit();
    }
  });

  // The theme switch in the header applies its change the moment it is pressed, before
  // the server has confirmed it. "system" means removing the attribute so the operating
  // system preference applies again; the reply then replaces the switch in its new
  // state, and the stylesheet picks the icon from data-current.
  var NEXT_THEME = { light: "dark", dark: "system", system: "light" };

  function applyTheme(theme) {
    var root = document.documentElement;
    if (theme === "system") {
      delete root.dataset.theme;
    } else {
      root.dataset.theme = theme;
    }
  }

  document.addEventListener("submit", function (event) {
    var form = event.target.closest("[data-theme-switch]");
    if (!form) {
      return;
    }
    var choice = form.querySelector("input[name=theme]");
    if (!choice) {
      return;
    }
    applyTheme(choice.value);
    form.dataset.current = choice.value;
    choice.value = NEXT_THEME[choice.value] || "system";
  });

  // A strength meter under any field where a password is chosen. The estimate is
  // zxcvbn's, run in the browser, so the password never leaves it until the form is
  // submitted; the markup and the words come from a <template> the page renders, so
  // they are translated and styled like everything else. The word carries the meaning;
  // the colour only repeats it.
  var METER_TONES = [
    "bg-red-500",
    "bg-red-500",
    "bg-amber-500",
    "bg-emerald-500",
    "bg-emerald-600",
  ];
  var zxcvbnChecker = null;

  function readyZxcvbn() {
    if (zxcvbnChecker) {
      return zxcvbnChecker;
    }
    var lib = window.zxcvbnts;
    if (!lib || !lib.core || !lib["language-common"] || !lib["language-en"]) {
      return null;
    }
    // @zxcvbn-ts/core 4: one factory, configured once with the dictionaries it scores
    // against, then check() for each keystroke.
    zxcvbnChecker = new lib.core.ZxcvbnFactory({
      translations: lib["language-en"].translations,
      graphs: lib["language-common"].adjacencyGraphs,
      dictionary: Object.assign(
        {},
        lib["language-common"].dictionary,
        lib["language-en"].dictionary
      ),
    });
    return zxcvbnChecker;
  }

  function meterFor(input) {
    var existing = input.parentElement.querySelector("[data-password-meter-display]");
    if (existing) {
      return existing;
    }
    var template = document.getElementById("password-meter");
    if (!template) {
      return null;
    }
    var display = template.content.firstElementChild.cloneNode(true);
    display.dataset.words = JSON.stringify([0, 1, 2, 3, 4].map(function (score) {
      return template.getAttribute("data-word-" + score) || "";
    }));
    input.insertAdjacentElement("afterend", display);
    return display;
  }

  function userInputsAround(input) {
    // What the person has typed elsewhere on the form: an address, a username, a name.
    // A password built from them scores low, which is what the server will say too.
    var values = [];
    function add(text) {
      if (!text) {
        return;
      }
      values.push(text);
      text.split(/[@._\-\s]+/).forEach(function (part) {
        if (part.length > 2) {
          values.push(part);
        }
      });
    }
    // On change and set, the form holds no name or address; the page passes the signed-in
    // person's own instead.
    var template = document.getElementById("password-meter");
    if (template) {
      add(template.getAttribute("data-user-inputs") || "");
    }
    if (input.form) {
      input.form.querySelectorAll("input[type=text], input[type=email]").forEach(function (field) {
        add(field.value);
      });
    }
    return values;
  }

  document.addEventListener("input", function (event) {
    var input = event.target;
    if (!input.matches || !input.matches("input[data-password-meter]")) {
      return;
    }
    var display = meterFor(input);
    var checker = readyZxcvbn();
    if (!display || !checker) {
      return;
    }
    var words = JSON.parse(display.dataset.words);
    var segments = display.querySelectorAll("[data-segment]");
    var word = display.querySelector("[data-word]");
    var suggestion = display.querySelector("[data-suggestion]");
    if (!input.value) {
      segments.forEach(function (segment) {
        segment.className = segment.className.replace(/\bbg-\S+/g, "").trim() + " bg-ink-200 dark:bg-ink-700";
      });
      word.textContent = "";
      suggestion.textContent = "";
      return;
    }
    var result = checker.check(input.value, userInputsAround(input));
    segments.forEach(function (segment, index) {
      var lit = index < Math.max(result.score, 1);
      segment.className =
        segment.className.replace(/\bbg-\S+/g, "").trim() +
        (lit ? " " + METER_TONES[result.score] : " bg-ink-200 dark:bg-ink-700");
    });
    word.textContent = words[result.score] || "";
    var advice = result.feedback.warning || (result.feedback.suggestions || [])[0] || "";
    suggestion.textContent = advice && result.score < 3 ? " · " + advice : "";
  });

  // The account menu is a <details> element, which opens and closes itself and is
  // keyboard-operable without help. What it does not do is close when the pointer goes
  // elsewhere or Escape is pressed, so that part is added here.
  function closeMenus(except) {
    document.querySelectorAll("details[data-menu][open]").forEach(function (menu) {
      if (menu !== except) {
        menu.removeAttribute("open");
      }
    });
  }

  document.addEventListener("click", function (event) {
    closeMenus(event.target.closest("details[data-menu]"));
  });

  // "/" jumps to the search box, as on most sites with one, unless the person is
  // already typing somewhere.
  document.addEventListener("keydown", function (event) {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) {
      return;
    }
    var active = document.activeElement;
    if (
      active &&
      (active.tagName === "INPUT" ||
        active.tagName === "TEXTAREA" ||
        active.tagName === "SELECT" ||
        active.isContentEditable)
    ) {
      return;
    }
    var box = document.querySelector("[data-search-shortcut]");
    if (box) {
      event.preventDefault();
      box.focus();
      box.select();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    var open = document.querySelector("details[data-menu][open]");
    if (!open) {
      return;
    }
    var inside = open.contains(document.activeElement);
    closeMenus(null);
    var summary = open.querySelector("summary");
    if (inside && summary) {
      summary.focus();
    }
  });
})();
