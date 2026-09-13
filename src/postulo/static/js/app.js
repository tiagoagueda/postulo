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

  // The flag beside a telephone field's country chooser. An <option> holds text and
  // nothing else in every browser, so the flag cannot live in the list; it sits over the
  // closed select instead, and this keeps it pointing at whatever is chosen (#88).
  //
  // The server has already drawn the right flag for the country the field loaded with, so
  // with this script blocked or still loading the field is correct -- it simply stops
  // following the select until the form is saved. Each option carries its own URL because
  // static files are served under a content hash, so there is no pattern to build one
  // from.
  document.addEventListener("change", function (event) {
    var select = event.target.closest("[data-phone-country]");
    if (!select) {
      return;
    }
    var holder = select.parentNode.querySelector("[data-phone-flag]");
    if (!holder) {
      return;
    }
    var option = select.options[select.selectedIndex];
    var url = option ? option.getAttribute("data-flag") : "";
    if (!url) {
      holder.textContent = "";
      return;
    }
    var image = holder.querySelector("img");
    if (!image) {
      image = document.createElement("img");
      // Matches what the template renders, so the two cannot drift apart in appearance.
      image.className = "flag";
      image.width = 20;
      image.height = 15;
      image.alt = "";
      image.setAttribute("aria-hidden", "true");
      holder.appendChild(image);
    }
    image.setAttribute("data-flag", (select.value || "").toLowerCase());
    image.src = url;
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

  /* `dragenter` as well as `dragover`, and both cancelled. The specification makes an
   * element a drop target only once *both* are cancelled; Chromium accepts `dragover`
   * alone, Firefox does not, and refuses the drop with nothing logged. The suite runs
   * `--browser chromium`, so it agreed with the one browser that forgives the omission
   * (#174). */
  document.addEventListener("dragenter", function (event) {
    if (dragging && columnOf(event.target)) {
      event.preventDefault();
    }
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

  // The capture review page is the one screen somebody works through forty times in a
  // row, so it has keys: "d" discards and moves on, "j" skips to the next, Ctrl+Enter
  // saves and moves on -- never while typing in a field, and only where the page marks
  // itself. Each key presses the button that does the same thing, so the server path is
  // the button's and the buttons stay the whole control (#179).
  document.addEventListener("keydown", function (event) {
    var page = document.querySelector("[data-capture-review]");
    if (!page) {
      return;
    }
    var active = document.activeElement;
    var typing =
      active &&
      (active.tagName === "INPUT" ||
        active.tagName === "TEXTAREA" ||
        active.tagName === "SELECT" ||
        active.isContentEditable);
    var target = null;
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      target = page.querySelector("[data-key-save-next]");
    } else if (!typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      if (event.key === "d") {
        target = page.querySelector("[data-key-discard-next]");
      } else if (event.key === "j") {
        target = page.querySelector("[data-key-next]");
      }
    }
    if (target) {
      event.preventDefault();
      target.click();
    }
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

  /*
   * Bulk selection (#134). Two additions to a form that already works without any of this:
   * a live count of what is ticked, and a button that ticks everything on the page.
   *
   * The button is *added* here rather than revealed. A select-all checkbox rendered by the
   * template would be an inert control for anybody without script, and an inert control is
   * worse than a missing one; a button that does not exist until something can make it work
   * is honest in both directions.
   */
  function bulkBoxes(form) {
    return Array.prototype.slice.call(
      document.querySelectorAll('[data-bulk-row][form="' + form.id + '"]')
    );
  }

  function tellTheCount(form) {
    var label = form.querySelector("[data-bulk-count]");
    if (!label) {
      return;
    }
    var boxes = bulkBoxes(form);
    var ticked = boxes.filter(function (box) {
      return box.checked;
    }).length;
    if (!ticked) {
      label.textContent = label.dataset.bulkNone || label.textContent;
      return;
    }
    var template = ticked === 1 ? label.dataset.bulkOne : label.dataset.bulkMany;
    label.textContent = (template || "{count} selected").replace("{count}", ticked);
  }

  function addSelectAll(form) {
    if (form.querySelector("[data-bulk-all]")) {
      return;
    }
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn-ghost text-sm";
    button.dataset.bulkAll = "";
    button.textContent = form.dataset.bulkAllLabel || "Select all on this page";
    button.addEventListener("click", function () {
      var boxes = bulkBoxes(form);
      var everyOne = boxes.every(function (box) {
        return box.checked;
      });
      boxes.forEach(function (box) {
        box.checked = !everyOne;
      });
      tellTheCount(form);
    });
    var count = form.querySelector("[data-bulk-count]");
    if (count && count.parentNode) {
      count.parentNode.insertBefore(button, count.nextSibling);
    } else {
      form.appendChild(button);
    }
  }

  function readyBulkForms() {
    var forms = document.querySelectorAll("[data-bulk-form]");
    Array.prototype.forEach.call(forms, function (form) {
      if (!form.id) {
        return;
      }
      var label = form.querySelector("[data-bulk-count]");
      if (label && !label.dataset.bulkNone) {
        label.dataset.bulkNone = label.textContent.trim();
      }
      addSelectAll(form);
      tellTheCount(form);
    });
  }

  document.addEventListener("change", function (event) {
    var box = event.target.closest ? event.target.closest("[data-bulk-row]") : null;
    if (!box) {
      return;
    }
    var form = document.getElementById(box.getAttribute("form"));
    if (form) {
      tellTheCount(form);
    }
  });

  document.addEventListener("DOMContentLoaded", readyBulkForms);
  // The table is swapped by htmx when a filter changes, which also replaces the bar and
  // brings fresh, unticked boxes with it -- which is exactly the rule: a changed query
  // clears the selection.
  document.body.addEventListener("htmx:afterSwap", readyBulkForms);

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

  // The language picker, which is a disclosure rather than a <select> because an <option>
  // cannot hold a flag, a language-marked name and a symbol at once (#119).
  //
  // Choosing between them costs the keyboard behaviour a native dropdown gives away, so
  // this hands back the part the platform does not. Radios already do the rest: arrow keys
  // move between them, and they submit with no script at all, which is why none of what
  // follows is required for the control to work.
  var picker = document.querySelector("[data-language-picker]");

  function languageRadios() {
    return picker ? Array.prototype.slice.call(picker.querySelectorAll('input[type="radio"]')) : [];
  }

  function chooseLanguage(radio) {
    if (!radio) {
      return;
    }
    radio.checked = true;
    radio.focus();
    // The list scrolls inside itself, so moving to something below the fold has to bring
    // it into view or the focus ring is somewhere nobody can see.
    if (radio.scrollIntoView) {
      radio.scrollIntoView({ block: "nearest" });
    }
  }

  // Opening it puts the keyboard where the choosing happens, rather than leaving somebody
  // to tab past the whole list to reach the first row.
  if (picker) {
    picker.addEventListener("toggle", function () {
      if (!picker.open) {
        return;
      }
      var radios = languageRadios();
      var current = radios.filter(function (radio) {
        return radio.checked;
      })[0];
      chooseLanguage(current || radios[0]);
    });
  }

  var typed = "";
  var typedAt = 0;

  document.addEventListener("keydown", function (event) {
    if (!picker || !picker.open || !picker.contains(document.activeElement)) {
      return;
    }
    var radios = languageRadios();
    if (!radios.length) {
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      chooseLanguage(event.key === "Home" ? radios[0] : radios[radios.length - 1]);
      return;
    }
    // Type-ahead, on the name as it is written in its own language: somebody looking for
    // Ελληνικά types Ε, and somebody looking for Deutsch types D. A second key within a
    // second extends the search rather than starting a new one, as a <select> does.
    if (event.key.length !== 1 || event.ctrlKey || event.metaKey || event.altKey) {
      return;
    }
    var now = Date.now();
    typed = now - typedAt > 1000 ? event.key : typed + event.key;
    typedAt = now;
    var wanted = typed.toLowerCase();
    var found = radios.filter(function (radio) {
      var label = radio.closest("label");
      return label && label.textContent.trim().toLowerCase().indexOf(wanted) === 0;
    })[0];
    if (found) {
      event.preventDefault();
      chooseLanguage(found);
    }
  });
  /* ------------------------------------------------------------------ labels
   *
   * Choosing several things, shown as removable labels rather than as tick boxes (#139).
   *
   * **Layered, never substituted.** The markup already holds the control that works
   * everywhere -- a checkbox per industry, a multiple select for tags, and a box for a name
   * that does not exist yet. This draws chips over the top and hides those; they keep
   * submitting, because a hidden element still posts. With this script blocked the form is
   * exactly what it was, which is the same rule the board's dragging follows.
   *
   * **Backspace does not remove the last chip.** It is the convention and it is also a way
   * to delete something by pressing the key you press to correct a typo -- in a control
   * whose values are somebody's own vocabulary, that is a bad trade. Every chip has a
   * remove button, reachable by Tab and by the arrow keys.
   *
   * **A new name is visible as new before anything is saved.** Otherwise people create
   * Fintech, FinTech and fintech and find out afterwards that the slug collapsed them.
   */
  function labelSources(box) {
    /* The control this is layered over, found by its container.
     *
     * Not by an id: a checkbox group is a fieldset of inputs rather than one labelled
     * control, so Django gives it no id to point at -- `id_for_label` is empty by design.
     */
    var holder = box.querySelector("[data-labels-existing]");
    if (!holder) {
      return null;
    }
    var select = holder.querySelector("select[multiple]");
    if (select) {
      return { native: select, boxes: null };
    }
    var boxes = holder.querySelectorAll('input[type="checkbox"]');
    return boxes.length ? { native: holder, boxes: boxes } : null;
  }

  function labelValues(box) {
    /* Every option this control knows, as {name, on, set(bool)}. */
    var found = labelSources(box);
    if (!found) {
      return [];
    }
    if (found.boxes) {
      return Array.prototype.map.call(found.boxes, function (input) {
        var label = input.closest("label") || input.parentNode;
        return {
          name: (label ? label.textContent : input.value).trim(),
          get on() {
            return input.checked;
          },
          set: function (want) {
            input.checked = want;
          },
        };
      });
    }
    return Array.prototype.map.call(found.native.options, function (option) {
      return {
        name: option.textContent.trim(),
        get on() {
          return option.selected;
        },
        set: function (want) {
          option.selected = want;
        },
      };
    });
  }

  function typedNames(field) {
    if (!field || !field.value.trim()) {
      return [];
    }
    return field.value
      .split(/[;,/]/)
      .map(function (part) {
        return part.trim();
      })
      .filter(Boolean);
  }

  function writeTyped(field, names) {
    if (field) {
      field.value = names.join(", ");
    }
  }

  function sayIt(box, template, name) {
    var live = box.querySelector("[data-labels-live]");
    if (live && template) {
      live.textContent = template.replace("{label}", name);
    }
  }

  function drawLabels(box) {
    var list = box.querySelector("[data-labels-chips]");
    var input = box.querySelector("[data-labels-input]");
    var options = box.querySelector("[data-labels-options]");
    var newField = box.dataset.labelsNew ? document.getElementById(box.dataset.labelsNew) : null;
    if (!list) {
      return;
    }
    var values = labelValues(box);
    var typed = typedNames(newField);
    list.textContent = "";

    var chosen = values.filter(function (value) {
      return value.on;
    });
    if (!chosen.length && !typed.length) {
      var empty = document.createElement("li");
      empty.className = "text-sm text-ink-500 dark:text-ink-400";
      empty.textContent = box.dataset.labelsEmpty || "";
      list.appendChild(empty);
    }

    chosen.forEach(function (value) {
      list.appendChild(chipFor(box, value.name, false, function () {
        value.set(false);
        sayIt(box, box.dataset.labelsRemoved, value.name);
        drawLabels(box);
      }));
    });
    typed.forEach(function (name) {
      list.appendChild(chipFor(box, name, true, function () {
        writeTyped(newField, typedNames(newField).filter(function (other) {
          return other !== name;
        }));
        sayIt(box, box.dataset.labelsRemoved, name);
        drawLabels(box);
      }));
    });

    if (options && input) {
      options.textContent = "";
      values
        .filter(function (value) {
          return !value.on;
        })
        .forEach(function (value) {
          var option = document.createElement("option");
          option.value = value.name;
          options.appendChild(option);
        });
      input.disabled = !newField && !options.childElementCount;
    }
  }

  function chipFor(box, name, isNew, remove) {
    var item = document.createElement("li");
    var chip = document.createElement("span");
    chip.className = isNew ? "chip chip-new" : "chip";
    var text = document.createElement("span");
    text.className = "chip-text";
    text.textContent = isNew ? (box.dataset.labelsNewHint || "{label}").replace("{label}", name) : name;
    chip.appendChild(text);

    var button = document.createElement("button");
    button.type = "button";
    button.className = "chip-remove";
    button.dataset.labelsChip = "";
    button.setAttribute(
      "aria-label",
      (box.dataset.labelsRemove || "Remove {label}").replace("{label}", name)
    );
    button.textContent = "\u00d7";
    button.addEventListener("click", remove);
    chip.appendChild(button);
    item.appendChild(chip);
    return item;
  }

  function commitTyped(box) {
    var input = box.querySelector("[data-labels-input]");
    var newField = box.dataset.labelsNew ? document.getElementById(box.dataset.labelsNew) : null;
    var wanted = (input.value || "").trim();
    if (!wanted) {
      return;
    }
    var match = labelValues(box).filter(function (value) {
      return value.name.toLowerCase() === wanted.toLowerCase();
    })[0];
    if (match) {
      match.set(true);
    } else if (newField) {
      var typed = typedNames(newField);
      if (
        !typed.some(function (name) {
          return name.toLowerCase() === wanted.toLowerCase();
        })
      ) {
        typed.push(wanted);
        writeTyped(newField, typed);
      }
    } else {
      return;
    }
    input.value = "";
    sayIt(box, box.dataset.labelsAdded, wanted);
    drawLabels(box);
  }

  function readyLabels(box) {
    if (box.dataset.labelsReady || !labelSources(box)) {
      return;
    }
    box.dataset.labelsReady = "1";

    var chips = document.createElement("ul");
    chips.className = "flex flex-wrap items-center gap-2";
    chips.dataset.labelsChips = "";
    chips.setAttribute("aria-labelledby", box.dataset.labelsHeading);

    var field = document.createElement("input");
    field.type = "text";
    field.className = "field-input mt-2";
    field.dataset.labelsInput = "";
    field.autocomplete = "off";
    field.placeholder = box.dataset.labelsPlaceholder || "";
    field.setAttribute("aria-labelledby", box.dataset.labelsHeading);

    var options = document.createElement("datalist");
    options.id = box.dataset.labelsHeading + "-options";
    options.dataset.labelsOptions = "";
    field.setAttribute("list", options.id);

    var live = document.createElement("p");
    live.className = "sr-only";
    live.dataset.labelsLive = "";
    live.setAttribute("role", "status");

    field.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        // The form would otherwise submit, which is not what pressing Enter in a box that
        // adds something means.
        event.preventDefault();
        commitTyped(box);
      } else if (event.key === "Escape") {
        field.value = "";
      }
    });
    field.addEventListener("change", function () {
      commitTyped(box);
    });

    // Left and right walk the chips, mapped through the reading direction so the arrow
    // that means "the one before" is the one that points that way on the screen.
    chips.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
        return;
      }
      var buttons = Array.prototype.slice.call(chips.querySelectorAll("[data-labels-chip]"));
      var here = buttons.indexOf(document.activeElement);
      if (here === -1) {
        return;
      }
      var backwards = getComputedStyle(chips).direction === "rtl";
      var step = (event.key === "ArrowLeft") === backwards ? 1 : -1;
      var next = buttons[here + step];
      if (next) {
        event.preventDefault();
        next.focus();
      }
    });

    var native = box.querySelector("[data-labels-existing]");
    var newBox = box.querySelector("[data-labels-newbox]");
    box.insertBefore(chips, native);
    box.insertBefore(field, native);
    box.insertBefore(options, native);
    box.insertBefore(live, native);
    if (native) {
      native.hidden = true;
    }
    if (newBox) {
      newBox.hidden = true;
    }
    drawLabels(box);
  }

  function readyEveryLabelBox() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-labels]"), readyLabels);
  }

  document.addEventListener("DOMContentLoaded", readyEveryLabelBox);
  document.body && readyEveryLabelBox();
  document.addEventListener("htmx:afterSwap", readyEveryLabelBox);

  /* --------------------------------------------------------------- editing a cell
   *
   * A value changed where it sits (#135). htmx does the swapping; this does the two things
   * htmx cannot, and both are about the keyboard.
   *
   * **Focus follows the swap.** Opening an editor puts the caret in it; saving or
   * abandoning puts focus back on the value it came from. Without this, every edit throws
   * somebody to the top of a re-rendered table, which is the failure the issue names.
   *
   * **Escape abandons.** Enter is the form's own submit, which needs nothing; Escape is not
   * a form key and has to be asked for.
   */
  document.addEventListener("htmx:afterSwap", function (event) {
    var cell = event.target.closest ? event.target.closest("[data-cell]") : null;
    if (!cell) {
      return;
    }
    var input = cell.querySelector("[data-cell-editor] input:not([type=hidden])");
    if (input) {
      input.focus();
      input.select();
      return;
    }
    var link = cell.querySelector("[data-cell-open]");
    if (link) {
      link.focus();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    var editor = event.target.closest ? event.target.closest("[data-cell-editor]") : null;
    if (!editor) {
      return;
    }
    var cancel = editor.querySelector("[hx-get]");
    if (cancel) {
      event.preventDefault();
      // The editor arrived by a swap, and htmx wires what it swapped in -- this button's
      // hx-get among it -- only when the swap *settles*, 20 ms later by default. Focus is
      // put in the input the moment it lands (above), so for those 20 ms an Escape reached
      // a Cancel that nothing was listening to, and did nothing. A hand is never that quick;
      // a browser test is, one run in three (#161). Processing is idempotent -- htmx skips
      // an element it has already initialised -- so this wires the button if the settle
      // has not yet, and is a no-op if it has.
      if (window.htmx) {
        window.htmx.process(editor);
      }
      cancel.click();
    }
  });

  /* ------------------------------------------------------- a page's own sections
   *
   * The career page is seven sections down one scroll, and its sidebar lists them as
   * anchors. Which one is being looked at is a question only the browser can answer, so
   * this answers it: the last section whose top has passed a line two-fifths of the way
   * down the window carries `aria-current="location"` -- location rather than page,
   * because they are all one page (#175). At the bottom of the page it is the last
   * section, whatever the line says: a short final section can never reach the line,
   * and the person who scrolled to it is looking at it. Without this the list still
   * navigates; it only stops saying where you are.
   */
  (function () {
    var links = document.querySelectorAll("[data-section-link]");
    if (!links.length) {
      return;
    }
    var sections = [];
    links.forEach(function (link) {
      var section = document.getElementById(link.getAttribute("data-section-link"));
      if (section) {
        sections.push(section);
      }
    });
    if (!sections.length) {
      return;
    }
    var scheduled = false;
    function mark() {
      scheduled = false;
      var line = window.innerHeight * 0.4;
      var current = sections[0];
      for (var i = 0; i < sections.length; i++) {
        if (sections[i].getBoundingClientRect().top <= line) {
          current = sections[i];
        }
      }
      var height = document.documentElement.scrollHeight;
      if (window.innerHeight + window.scrollY >= height - 2) {
        current = sections[sections.length - 1];
      }
      links.forEach(function (link) {
        var here = link.getAttribute("data-section-link") === current.id;
        link.classList.toggle("nav-link-active", here);
        link.classList.toggle("nav-link", !here);
        if (here) {
          link.setAttribute("aria-current", "location");
        } else {
          link.removeAttribute("aria-current");
        }
      });
    }
    // Once per frame however often the page scrolls; a scroll listener that lays out on
    // every event is how a page starts to stutter.
    function schedule() {
      if (!scheduled) {
        scheduled = true;
        window.requestAnimationFrame(mark);
      }
    }
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule, { passive: true });
    mark();
  })();

  /* --------------------------------------------------------- resizing a column
   *
   * A column width is the one preference in a table that can only be *asked for* with a
   * pointer, so this is the one place a script is unavoidable (#136). It is still an
   * addition rather than a replacement: without it the columns size themselves exactly as
   * they always did, which is a complete fallback, and the handle does not exist at all --
   * an inert control is worse than a missing one, which is the same reason the bulk bar's
   * *Select all* is added here rather than drawn by the template.
   *
   * **It is not pointer-only, though the gesture is.** The handle is a button: the arrow
   * keys widen and narrow it, and Home lets the column size itself again. A control that
   * only a mouse can reach is a control half the people using this application cannot use.
   *
   * **The width is saved in the background.** The page already shows the new width, so a
   * reload to confirm it would be a page load to tell somebody what they can see. A failed
   * save leaves the width for this page and loses it on the next one, which is the honest
   * outcome and not worth a dialogue.
   *
   * **And a stored width is applied here rather than written into the markup.** The policy
   * this application serves is `style-src 'self'`, which refuses a `style` attribute as
   * firmly as it refuses an inline script -- so a width in the template is dropped by the
   * browser and does nothing at all on a real deployment, while working perfectly in
   * development where the policy is not enforced. A style set through the DOM by a script
   * the policy already allows is not an inline style and is not refused.
   */
  var RESIZE_STEP = 16;

  function tokenFor(head) {
    var field = document.querySelector("input[name=csrfmiddlewaretoken]");
    return field ? field.value : "";
  }

  function saveWidth(head, key, pixels) {
    var body = new URLSearchParams();
    body.set("width", key);
    body.set("px", String(pixels));
    body.set("next", head.dataset.colHere || "/");
    // Every column that is currently shown, so `clean_settings` keeps them rather than
    // falling back to the defaults: it reads one form, and this is that form.
    Array.prototype.forEach.call(head.querySelectorAll("th[data-col]"), function (cell) {
      body.append("order", cell.dataset.col);
      body.append("show", cell.dataset.col);
    });
    fetch(head.dataset.colSettings, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRFToken": tokenFor(head),
        "X-Requested-With": "XMLHttpRequest",
      },
      body: body.toString(),
      credentials: "same-origin",
    }).catch(function () {
      /* The width is applied either way; losing it on the next load is the whole cost. */
    });
  }

  function widthNow(cell) {
    return Math.round(cell.getBoundingClientRect().width);
  }

  function setWidth(head, cell, pixels) {
    if (!pixels) {
      cell.style.width = "";
      saveWidth(head, cell.dataset.col, 0);
      return;
    }
    var wanted = Math.max(64, Math.min(900, Math.round(pixels)));
    cell.style.width = wanted + "px";
    return wanted;
  }

  function addHandle(head, cell) {
    if (cell.querySelector("[data-col-handle]")) {
      return;
    }
    var handle = document.createElement("button");
    handle.type = "button";
    handle.dataset.colHandle = "";
    handle.className = "col-handle";
    handle.setAttribute(
      "aria-label",
      (head.dataset.colWider || "Widen {column}").replace("{column}", cell.dataset.colLabel || "")
    );
    handle.title = handle.getAttribute("aria-label");

    var dragging = false;
    var startX = 0;
    var startWidth = 0;

    handle.addEventListener("pointerdown", function (event) {
      dragging = true;
      startX = event.clientX;
      startWidth = widthNow(cell);
      handle.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    handle.addEventListener("pointermove", function (event) {
      if (!dragging) {
        return;
      }
      // Away from the start edge widens, whichever side of the screen that is.
      var backwards = getComputedStyle(cell).direction === "rtl";
      var moved = (event.clientX - startX) * (backwards ? -1 : 1);
      setWidth(head, cell, startWidth + moved);
    });
    handle.addEventListener("pointerup", function (event) {
      if (!dragging) {
        return;
      }
      dragging = false;
      handle.releasePointerCapture(event.pointerId);
      saveWidth(head, cell.dataset.col, widthNow(cell));
    });
    handle.addEventListener("keydown", function (event) {
      var backwards = getComputedStyle(cell).direction === "rtl";
      var step = 0;
      if (event.key === "ArrowRight") {
        step = backwards ? -RESIZE_STEP : RESIZE_STEP;
      } else if (event.key === "ArrowLeft") {
        step = backwards ? RESIZE_STEP : -RESIZE_STEP;
      } else if (event.key === "Home") {
        event.preventDefault();
        setWidth(head, cell, 0);
        say(head, (head.dataset.colReset || "").replace("{column}", cell.dataset.colLabel || ""));
        return;
      }
      if (!step) {
        return;
      }
      event.preventDefault();
      var wanted = setWidth(head, cell, widthNow(cell) + step);
      saveWidth(head, cell.dataset.col, wanted);
    });

    // The room the handle needs, added with it: a 24-wide target over a 16-pixel padding
    // would sit on the header's own link, and SC 2.5.8 wants both to be reachable (#136).
    cell.classList.add("has-col-handle");
    cell.appendChild(handle);
  }

  function say(head, words) {
    var live = head.querySelector("[data-col-live]");
    if (!live) {
      live = document.createElement("caption");
      live.className = "sr-only";
      live.dataset.colLive = "";
      live.setAttribute("role", "status");
      var table = head.closest("table");
      if (table) {
        table.insertBefore(live, table.firstChild);
      }
    }
    if (words) {
      live.textContent = words;
    }
  }

  function applyStoredWidth(cell) {
    var stored = parseInt(cell.dataset.colWidth || "", 10);
    if (stored) {
      cell.style.width = stored + "px";
    }
  }

  function readyColumnWidths() {
    Array.prototype.forEach.call(
      document.querySelectorAll("thead[data-col-settings]"),
      function (head) {
        Array.prototype.forEach.call(head.querySelectorAll("th[data-col]"), function (cell) {
          applyStoredWidth(cell);
          addHandle(head, cell);
        });
      }
    );
  }

  document.addEventListener("DOMContentLoaded", readyColumnWidths);
  document.addEventListener("htmx:afterSwap", readyColumnWidths);
  if (document.body) {
    readyColumnWidths();
  }

  /* ------------------------------------------------- dragging a widget into place
   *
   * The dashboard is a grid a person arranges, and this is the gesture that was asked for
   * (#125). It is the *third* way to arrange it, not the first: the four arrows came first
   * on purpose, because drag and drop does not fire on touch screens and is not reachable
   * from a keyboard, and this file has said so since the board learnt to drag.
   *
   * **No new endpoint and no second store.** A drop posts to the same address the arrows
   * post to, with the position it landed at, and the arrangement is the same ordered list
   * it has always been -- so a page arranged by dragging and a page arranged by pressing
   * arrows are the same page, saved the same way.
   *
   * **`draggable` is set here rather than in the template**, so that with this script
   * blocked nothing looks draggable. An affordance that does nothing is worse than none.
   */
  function widgetRows(list) {
    return Array.prototype.slice.call(list.querySelectorAll("[data-widget-row]"));
  }

  function postPlacement(list, key, index) {
    var form = document.createElement("form");
    form.method = "post";
    form.action = list.dataset.widgetPlace || "";
    form.hidden = true;
    [
      ["csrfmiddlewaretoken", (document.querySelector("input[name=csrfmiddlewaretoken]") || {}).value || ""],
      ["key", key],
      ["to", String(index)],
      ["action", "place"],
    ].forEach(function (pair) {
      var field = document.createElement("input");
      field.type = "hidden";
      field.name = pair[0];
      field.value = pair[1];
      form.appendChild(field);
    });
    document.body.appendChild(form);
    form.submit();
  }

  var draggedWidget = null;

  document.addEventListener("dragstart", function (event) {
    var row = event.target.closest && event.target.closest("[data-widget-row]");
    if (!row) {
      return;
    }
    draggedWidget = row;
    row.classList.add("opacity-50");
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      // Firefox will not start a drag without something on the transfer.
      event.dataTransfer.setData("text/plain", row.dataset.widgetRow || "");
    }
  });

  document.addEventListener("dragend", function () {
    if (draggedWidget) {
      draggedWidget.classList.remove("opacity-50");
      draggedWidget = null;
    }
  });

  /* Both, and for the reason the board drag gives above: an element is a drop target only
   * where `dragenter` and `dragover` are each cancelled, and Firefox holds to that. */
  document.addEventListener("dragenter", function (event) {
    var row = event.target.closest && event.target.closest("[data-widget-row]");
    if (draggedWidget && row) {
      event.preventDefault();
    }
  });

  document.addEventListener("dragover", function (event) {
    var row = event.target.closest && event.target.closest("[data-widget-row]");
    if (draggedWidget && row) {
      event.preventDefault();
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = "move";
      }
    }
  });

  document.addEventListener("drop", function (event) {
    var row = event.target.closest && event.target.closest("[data-widget-row]");
    if (!draggedWidget || !row || row === draggedWidget) {
      return;
    }
    var list = row.closest("[data-widget-list]");
    if (!list) {
      return;
    }
    event.preventDefault();
    postPlacement(list, draggedWidget.dataset.widgetRow, widgetRows(list).indexOf(row));
  });

  function readyWidgetDragging() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-widget-list] [data-widget-row]"),
      function (row) {
        row.draggable = true;
        row.classList.add("cursor-grab");
      }
    );
  }

  document.addEventListener("DOMContentLoaded", readyWidgetDragging);
  if (document.body) {
    readyWidgetDragging();
  }

})();
