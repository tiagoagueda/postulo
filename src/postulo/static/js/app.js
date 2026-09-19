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

  /* ------------------------------------------------------- making a page ready, once
   *
   * Half of this file adds something to markup the server drew: a handle on a column
   * header, a *Select all* beside a bulk bar, chips over a checkbox group, `draggable` on a
   * card. Each of those has to run when the page arrives *and* after every htmx swap, and
   * each wrote that out for itself -- three registrations apiece, spelled slightly
   * differently every time. The ones written last forgot the swap: `readyWidgetDragging`
   * and the notifier card were never run again after htmx replaced anything, so a widget
   * row that came back in a swap could not be dragged (#226).
   *
   * `onContentReady` is that rule in one place. What is handed to it runs now, and again
   * after every swap; each of these functions checks for what it added before adding it,
   * which is what makes "run it again" the whole of the answer. The script is deferred, so
   * the document is parsed before any of this executes and there is no load event left to
   * wait for.
   *
   * There is deliberately no `htmx:historyRestore` here. Since #226 the back button reloads
   * the page from the server rather than putting back a copy of it, so a restored page is a
   * page load and arrives through the front door with everything else.
   */
  function onContentReady(ready) {
    if (document.body) {
      ready();
    } else {
      document.addEventListener("DOMContentLoaded", ready);
    }
    document.addEventListener("htmx:afterSwap", ready);
  }

  // Every form on a page carries the same token, so which one is found does not matter;
  // a page with no form at all has none to find, and the caller's request is refused,
  // which is the correct end for a request that cannot prove where it came from.
  function csrfToken() {
    var field = document.querySelector("input[name=csrfmiddlewaretoken]");
    return field ? field.value : "";
  }

  /* --------------------------------------- a request that fails, and one still running
   *
   * Nothing listened for `htmx:responseError` or `htmx:sendError`, and no template has ever
   * named an `hx-indicator`. Every live filter, sort and page link in this application
   * replaces a table, and htmx does not swap a 4xx or a 5xx -- so a filter that answered 500,
   * and a filter sent from a train with no signal, both left the previous rows sitting there
   * and said nothing whatsoever. The filter looked broken, or worse, looked as though it had
   * honestly found those rows (#226).
   *
   * **The words come from the page, not from here.** `base.html` renders one `role="alert"`
   * region carrying both sentences as data attributes, so they are translated with
   * everything else; this file contains no English a person will ever read.
   *
   * **`aria-busy` on the part being replaced.** htmx marks the element that *asked* with
   * `.htmx-request`, which is the sort link or the filter box -- but what somebody is waiting
   * for is the table. Setting it here rather than in the markup gives every swap in the
   * application the same treatment without a template having to remember, and it is a
   * screen reader's answer as much as the stylesheet's.
   *
   * The message is cleared by the next request that works, rather than by a timer or a
   * button. A failure that cleans itself up after five seconds is one somebody looking at
   * their keyboard never sees; the next successful swap is the moment it stopped being true.
   */
  function alertRegion() {
    return document.querySelector("[data-htmx-alert] [data-alert-words]");
  }

  function sayFailure(words) {
    var region = alertRegion();
    if (region) {
      region.textContent = words || "";
    }
  }

  // The alert can be put away (#275): the button beside it, or Escape from anywhere while
  // it is showing. Clearing the words is what hides it, so the region stays in the document
  // for the next announcement.
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-alert-close]")) {
      sayFailure("");
    }
  });

  document.addEventListener("keydown", function (event) {
    var region = alertRegion();
    if (event.key === "Escape" && region && region.textContent) {
      sayFailure("");
    }
  });

  function failureWords(name) {
    var region = alertRegion();
    return (region && region.dataset[name]) || "";
  }

  // The element being replaced, which is what `aria-busy` belongs on. htmx puts it on the
  // event's detail; `elt` -- the element that asked -- is the fallback for an event raised
  // before a target was worked out.
  function swapTarget(event) {
    var detail = event.detail || {};
    var node = detail.target || detail.elt || null;
    return node && node.nodeType === 1 && node.setAttribute ? node : null;
  }

  document.addEventListener("htmx:beforeRequest", function (event) {
    var target = swapTarget(event);
    if (target) {
      target.setAttribute("aria-busy", "true");
    }
  });

  /* Four events rather than one: `htmx:afterRequest` is not raised when the request never
   * reached the server, and a target left `aria-busy` for ever is a table announced as
   * loading long after everybody has given up on it. */
  ["htmx:afterRequest", "htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach(
    function (name) {
      document.addEventListener(name, function (event) {
        var target = swapTarget(event);
        if (target) {
          target.removeAttribute("aria-busy");
        }
      });
    }
  );

  document.addEventListener("htmx:responseError", function (event) {
    var xhr = (event.detail || {}).xhr;
    sayFailure(failureWords("alertFailed").replace("{status}", String((xhr && xhr.status) || 0)));
  });

  function saySilence() {
    sayFailure(failureWords("alertOffline"));
  }

  document.addEventListener("htmx:sendError", saySilence);
  document.addEventListener("htmx:timeout", saySilence);

  document.addEventListener("htmx:afterRequest", function (event) {
    if ((event.detail || {}).successful) {
      sayFailure("");
    }
  });

  /* ------------------------------------------- a control that saves when it is finished
   *
   * Controls marked `data-autosubmit` save as soon as they change. Used by the board, where
   * making somebody press a button to move a card guarantees stale statuses, and by the
   * plugins page's switch.
   *
   * **A select waits until the choosing has stopped.** In Chromium on Windows, arrowing
   * through a *closed* select fires `change` at every step it passes, so moving four
   * statuses down the list wrote four status changes and four timeline entries for
   * statuses nobody chose -- a change of context on every keystroke, and a record of a job
   * search that was not true (#227). So a choice made with the keyboard is committed on
   * Enter, on Tab, or when focus leaves; a choice made with the pointer is already
   * finished when `change` arrives and saves at once, which is what keeps the board one
   * click. The description on the board's menus says so, because a person cannot be
   * expected to guess it.
   *
   * A checkbox is not a select: its `change` is always the whole of the decision, so it is
   * left alone.
   */
  function autosubmitState(control) {
    // A checkbox's `value` is the word it posts, the same before and after it is ticked;
    // what changed is `checked`. Comparing the wrong one made the switch never save.
    if (control.type === "checkbox" || control.type === "radio") {
      return control.checked ? "on" : "off";
    }
    return control.value;
  }

  function commitAutosubmit(control) {
    var before = control.dataset.autosubmitFrom;
    delete control.dataset.autosubmitChoosing;
    if (!control.form || (before !== undefined && autosubmitState(control) === before)) {
      return false;
    }
    control.dataset.autosubmitFrom = autosubmitState(control);
    control.form.requestSubmit();
    return true;
  }

  function autosubmitControl(node) {
    return node && node.closest ? node.closest("[data-autosubmit]") : null;
  }

  document.addEventListener("focusin", function (event) {
    var control = autosubmitControl(event.target);
    if (control) {
      control.dataset.autosubmitFrom = autosubmitState(control);
      delete control.dataset.autosubmitChoosing;
    }
  });

  document.addEventListener("keydown", function (event) {
    var control = autosubmitControl(event.target);
    if (!control || control.tagName !== "SELECT" || event.isComposing) {
      return;
    }
    if (event.key === "Enter") {
      if (commitAutosubmit(control)) {
        event.preventDefault();
      }
    } else if (!event.ctrlKey && !event.metaKey && !event.altKey && event.key !== "Tab") {
      // Still choosing: an arrow, or a letter jumping down the list.
      control.dataset.autosubmitChoosing = "1";
    }
  });

  document.addEventListener("change", function (event) {
    var control = autosubmitControl(event.target);
    if (control && !control.dataset.autosubmitChoosing) {
      commitAutosubmit(control);
    }
  });

  document.addEventListener("focusout", function (event) {
    var control = autosubmitControl(event.target);
    if (control && control.dataset.autosubmitChoosing) {
      commitAutosubmit(control);
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
  //
  // `draggable` is set here rather than in the template, the same rule the dashboard's rows
  // are held to further down this file. The board's template had been setting it since the
  // board was written, so with this script blocked every card carried a grab cursor and an
  // affordance that did nothing (#227).
  var dragging = null;

  function readyBoardCards() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-board-column] [data-card]"),
      function (card) {
        card.draggable = true;
        card.classList.add("cursor-grab");
      }
    );
  }

  onContentReady(readyBoardCards);

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

  /* ------------------------------------------------------ a form is sent once
   *
   * A double click on Save, or Enter and then a click, posted a company form twice in
   * one second; the second request answered a 500 for a company the first had saved, and
   * the browser showed the 500 (#206). The first submit marks the form and its button;
   * a second submit of a marked form is stopped here. The button is not `disabled`,
   * because a button disabled during its own submit event drops out of the form data --
   * and the dashboard's arrows are buttons whose name and value *are* the request. Forms
   * htmx sends are its own business, and it swaps them away. `pageshow` clears the mark,
   * so the back button gets a form that works again; with this script blocked the form
   * is what it always was.
   *
   * **A form whose answer is a file never leaves the page**, so `pageshow` never comes and
   * the mark stayed on for the rest of the visit. *Download the archive*, *Export PDF* and
   * *Download PDF* were one-shot buttons: press one, take the file, and the button was
   * greyed out and `aria-disabled` until somebody thought to reload -- including the one on
   * the page that asks you to take a copy of everything before deleting your account
   * (#226). A form marked
   * `data-download` says that this is what it is, and its mark is lifted again a few
   * seconds later. It is a delay and not an exemption on purpose: the accident being
   * guarded against is a double click, which happens inside a second, and the second
   * export somebody asks for a minute later is not an accident.
   */
  var DOWNLOAD_RELEASE = 3000;

  function submitButtons(form) {
    return Array.prototype.slice.call(
      form.querySelectorAll("button:not([type]), button[type=submit], input[type=submit]")
    );
  }

  function releaseForm(form) {
    delete form.dataset.submitted;
    submitButtons(form).forEach(function (button) {
      button.removeAttribute("aria-disabled");
      button.classList.remove("opacity-60", "pointer-events-none");
    });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || form.tagName !== "FORM" || event.defaultPrevented) {
      return;
    }
    if ((form.getAttribute("method") || "get").toLowerCase() !== "post") {
      return;
    }
    if (form.hasAttribute("hx-post") || form.hasAttribute("hx-get") || form.hasAttribute("data-theme-switch")) {
      return;
    }
    if (form.dataset.submitted) {
      event.preventDefault();
      return;
    }
    form.dataset.submitted = "1";
    submitButtons(form).forEach(function (button) {
      button.setAttribute("aria-disabled", "true");
      button.classList.add("opacity-60", "pointer-events-none");
    });
    if (form.hasAttribute("data-download")) {
      window.setTimeout(function () {
        releaseForm(form);
      }, DOWNLOAD_RELEASE);
    }
  });

  window.addEventListener("pageshow", function () {
    Array.prototype.forEach.call(document.querySelectorAll("form[data-submitted]"), releaseForm);
  });

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
    display.dataset.adviceLanguage = template.getAttribute("data-advice-language") || "";
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
    // Only written when it changes. The word sits in a live region, and a region that is
    // rewritten on every keystroke is read out on every keystroke, even when it still says
    // the same thing (#225).
    var saying = words[result.score] || "";
    if (word.textContent !== saying) {
      word.textContent = saying;
    }
    // zxcvbn's advice is the vendored language pack's, and only the English pack is
    // vendored. Appending an English sentence to a translated word gives a French reader
    // "Fort · Add another word or two", so the advice is shown when the pack and the page
    // agree on the language and left out when they do not -- and the day a second pack is
    // vendored, `data-advice-language` is what turns it back on. It sits outside the live
    // region either way: what gets announced is the word.
    var pack = (display.dataset.adviceLanguage || "").toLowerCase();
    var reading = (document.documentElement.lang || "").toLowerCase().split("-")[0];
    var advice = result.feedback.warning || (result.feedback.suggestions || [])[0] || "";
    var wanted = pack && pack === reading && advice && result.score < 3;
    suggestion.textContent = wanted ? " · " + advice : "";
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

  /* ------------------------------------------------------ single-key shortcuts, and off
   *
   * "d", "j" and "/" are single-character shortcuts, and WCAG 2.1.4 is level A: a shortcut
   * that is one printable character and nothing else must be switchable off, remappable, or
   * live only while its own control has focus. These were none of the three. "d" discarded a
   * capture outright, and somebody using speech recognition dictates into a page rather than
   * into a field -- a stray "discard" in a sentence took the listing away (#227).
   *
   * So there is a switch, under Settings → Appearance, on by default because the review
   * screen is worked through forty times in a row and the keys are why that is bearable.
   * The server writes the answer onto <body>, which is read here. Ctrl+Enter keeps working
   * either way: a shortcut with a modifier is outside what the criterion is about.
   *
   * `isComposing` as well: while an input method is open, every keystroke is part of a
   * character being built and belongs to the composition, not to this page.
   */
  function singleKeysAllowed() {
    return document.body && document.body.dataset.shortcuts !== "off";
  }

  function keyboardIsBusy(event) {
    var active = document.activeElement;
    return (
      event.isComposing ||
      event.keyCode === 229 ||
      (active &&
        (active.tagName === "INPUT" ||
          active.tagName === "TEXTAREA" ||
          active.tagName === "SELECT" ||
          active.isContentEditable))
    );
  }

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
    var target = null;
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) {
      target = page.querySelector("[data-key-save-next]");
    } else if (
      singleKeysAllowed() &&
      !keyboardIsBusy(event) &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey
    ) {
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

  // The header floats (#195), and its height is not a constant: the row wraps on a
  // narrow screen and in a language with longer labels. So it is measured, once and on
  // every resize, into a custom property on the root that the stylesheet reads wherever
  // something has to clear it -- the sticky sidebars, the skip link, scroll-padding for
  // anchors -- and that the section observer below reads for its line. Set through the
  // CSSOM rather than a style attribute, which the content-security policy would drop.
  var siteHeader = document.querySelector("[data-site-header]");
  function measureHeader() {
    if (siteHeader) {
      document.documentElement.style.setProperty("--header-height", siteHeader.offsetHeight + "px");
    }
  }
  function headerHeight() {
    return siteHeader ? siteHeader.offsetHeight : 0;
  }
  measureHeader();
  window.addEventListener("resize", measureHeader);
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(measureHeader);
  }

  // "/" jumps to the search box, as on most sites with one, unless the person is
  // already typing somewhere -- or has switched single-key shortcuts off.
  document.addEventListener("keydown", function (event) {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) {
      return;
    }
    if (!singleKeysAllowed() || keyboardIsBusy(event)) {
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
    // The page wrote out the sentence for every count it can reach, in the reader's
    // language and with that language's plural rules already applied, so this indexes
    // rather than chooses. `n === 1` here would be the English rule imposed on sixty-eight
    // languages, thirteen of which disagree with it (#225).
    var sentences = [];
    try {
      sentences = JSON.parse(label.dataset.bulkCounts || "[]");
    } catch (error) {
      sentences = [];
    }
    // Nothing to say and nothing said: without the attribute the bar keeps the sentence
    // the page rendered, which is the same thing it does without any script at all.
    var saying = sentences[ticked] || sentences[sentences.length - 1];
    if (saying) {
      label.textContent = saying;
    }
  }

  function addSelectAll(form) {
    if (form.querySelector("[data-bulk-all]")) {
      return;
    }
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn";
    button.dataset.variant = "ghost";
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

  // The table is swapped by htmx when a filter changes, which also replaces the bar and
  // brings fresh, unticked boxes with it -- which is exactly the rule: a changed query
  // clears the selection.
  onContentReady(readyBulkForms);

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

  // A menu's items answer to the arrow keys, as a menu is expected to (#262). The
  // disclosure already opens on Enter and Space and closes on Escape (above). This adds
  // ArrowDown and ArrowUp on the summary, which open it onto the first or the last item,
  // and ArrowDown, ArrowUp, Home and End inside it, which move between the items and wrap.
  // The items are links and buttons, so Enter and Space act on them with no help. Only a
  // menu of actions -- `.dropdown-menu`, which carries `role="menu"` -- gets this; the
  // account disclosure is navigation, where Tab is the right key.
  function menuItems(menu) {
    return Array.prototype.slice
      .call(menu.querySelectorAll('[role="menuitem"]'))
      .filter(function (item) {
        return !item.disabled && item.getAttribute("aria-disabled") !== "true";
      });
  }

  document.addEventListener("keydown", function (event) {
    if (["ArrowDown", "ArrowUp", "Home", "End"].indexOf(event.key) === -1) {
      return;
    }
    if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing) {
      return;
    }
    var menu = event.target.closest("details[data-menu].dropdown-menu");
    if (!menu) {
      return;
    }
    var items = menuItems(menu);
    if (!items.length) {
      return;
    }
    event.preventDefault();
    var current = items.indexOf(event.target);
    var next;
    if (event.key === "Home" || (event.key === "ArrowDown" && (current === -1 || !menu.open))) {
      next = 0;
    } else if (event.key === "End" || (event.key === "ArrowUp" && (current === -1 || !menu.open))) {
      next = items.length - 1;
    } else if (event.key === "ArrowDown") {
      next = (current + 1) % items.length;
    } else {
      next = (current - 1 + items.length) % items.length;
    }
    closeMenus(menu);
    menu.open = true;
    items[next].focus();
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

  onContentReady(readyEveryLabelBox);

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

  /* --------------------------------------------- focus when a control swaps itself away
   *
   * htmx puts focus back after a swap by looking up the id the focused element had. That
   * covers a control which survives the swap, which is what the ids added in #227 are for,
   * and it can do nothing about one which does not survive it: paging to the last page
   * takes *Next* off the page, so the id htmx is holding names an element that no longer
   * exists and focus falls back to the body. Somebody paging a long list by keyboard
   * reached the end and started again from the skip link -- the very thing #227 set out to
   * stop, one press further on than its own test looked.
   *
   * So a control that can be swapped away says which group it belongs to, and if it is
   * gone when the dust settles, focus goes to whatever is left of that group. It is the
   * same rule the dashboard's arrows follow by keeping a disabled button rather than
   * removing it: the cluster keeps its shape, and the hand goes back to where it was.
   */

  var focusedBeforeSwap = null;

  document.addEventListener("htmx:beforeRequest", function () {
    var active = document.activeElement;
    focusedBeforeSwap =
      active && active.id
        ? { id: active.id, group: active.getAttribute("data-focus-group") || "" }
        : null;
  });

  document.addEventListener("htmx:afterSettle", function () {
    var was = focusedBeforeSwap;
    focusedBeforeSwap = null;
    if (!was || !was.group) {
      return;
    }
    if (document.getElementById(was.id)) {
      return; // It survived, and htmx has already put focus back on it.
    }
    var survivor = document.querySelector('[data-focus-group="' + was.group + '"]');
    if (survivor && survivor.focus) {
      survivor.focus();
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
      // Two-fifths of the window that is not under the header (#195).
      var covered = headerHeight();
      var line = covered + (window.innerHeight - covered) * 0.4;
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
  var RESIZE_MIN = 64;
  var RESIZE_MAX = 900;

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
        "X-CSRFToken": csrfToken(),
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
    var wanted = Math.max(RESIZE_MIN, Math.min(RESIZE_MAX, Math.round(pixels)));
    cell.style.width = wanted + "px";
    return wanted;
  }

  function addHandle(head, cell) {
    if (cell.querySelector("[data-col-handle]")) {
      return;
    }
    /* A splitter, in the ARIA sense: `role="separator"` with a value, a floor and a
     * ceiling. It was a button called "Widen Name", which was a lie half the time -- the
     * same control narrows the column with ArrowLeft -- and a press said nothing at all,
     * so from a screen reader the whole gesture was silent. A separator reports
     * `aria-valuenow`, which is announced as it moves, and its name can then be neutral
     * about which way it goes (#227).
     *
     * Still a `<button>` element underneath, so it is focusable, has a target size and
     * behaves the same with a pointer; the role is what it *is*, which is not a button. */
    var handle = document.createElement("button");
    handle.type = "button";
    handle.dataset.colHandle = "";
    handle.className = "col-handle";
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("aria-valuemin", String(RESIZE_MIN));
    handle.setAttribute("aria-valuemax", String(RESIZE_MAX));
    handle.setAttribute(
      "aria-label",
      (head.dataset.colResize || "Width of {column}").replace(
        "{column}",
        cell.dataset.colLabel || ""
      )
    );
    handle.title = handle.getAttribute("aria-label");

    /* A focusable separator without an `aria-valuenow` is an incomplete widget, so this is
     * never taken off -- not even when the column goes back to sizing itself, where the
     * honest answer is the width it settled at. Clamped to the pair of bounds beside it,
     * because a column that sizes itself wider than the ceiling would otherwise report a
     * value outside its own range. */
    function tellTheWidth() {
      var now = Math.max(RESIZE_MIN, Math.min(RESIZE_MAX, widthNow(cell)));
      handle.setAttribute("aria-valuenow", String(now));
    }

    tellTheWidth();

    var dragging = false;
    var startX = 0;
    var startWidth = 0;

    function stopDragging(event) {
      dragging = false;
      if (handle.hasPointerCapture && handle.hasPointerCapture(event.pointerId)) {
        handle.releasePointerCapture(event.pointerId);
      }
      tellTheWidth();
    }

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
      tellTheWidth();
    });
    handle.addEventListener("pointerup", function (event) {
      if (!dragging) {
        return;
      }
      stopDragging(event);
      saveWidth(head, cell.dataset.col, widthNow(cell));
    });
    /* A drag the browser took away: a touch became a scroll, a window lost the pointer, the
     * device was unplugged. Without this the handle stayed in `dragging` for ever, so the
     * next pointer move over the table went on dragging a column nobody was holding, and the
     * width that had been reached was never saved (#227). The column keeps where it got to,
     * which is what is on the screen, and that is what is written down. */
    handle.addEventListener("pointercancel", function (event) {
      if (!dragging) {
        return;
      }
      stopDragging(event);
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
        tellTheWidth();
        say(head, (head.dataset.colReset || "").replace("{column}", cell.dataset.colLabel || ""));
        return;
      }
      if (!step) {
        return;
      }
      event.preventDefault();
      var wanted = setWidth(head, cell, widthNow(cell) + step);
      tellTheWidth();
      say(
        head,
        (head.dataset.colSaid || "{column}: {width} pixels")
          .replace("{column}", cell.dataset.colLabel || "")
          .replace("{width}", String(wanted))
      );
      saveWidth(head, cell.dataset.col, wanted);
    });

    // The room the handle needs, added with it: a 24-wide target over a 16-pixel padding
    // would sit on the header's own link, and SC 2.5.8 wants both to be reachable (#136).
    cell.classList.add("has-col-handle");
    cell.appendChild(handle);
  }

  /* What a resize says, from *outside* the table.
   *
   * It used to be a `<caption>` put inside the table, and a caption is the table's
   * accessible name: announcing "Name: 240 pixels" renamed the table to that, for as long as
   * the words stayed there. A table called *Companies* became a table called by the last
   * thing somebody did to a column, which is a worse outcome than saying nothing (#227). The
   * region is a sibling of the table instead, where it can say whatever it likes. */
  function say(head, words) {
    var table = head.closest("table");
    if (!table) {
      return;
    }
    var live = table.parentNode && table.parentNode.querySelector(":scope > [data-col-live]");
    if (!live) {
      live = document.createElement("div");
      live.className = "sr-only";
      live.dataset.colLive = "";
      live.setAttribute("role", "status");
      table.parentNode.insertBefore(live, table.nextSibling);
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

  onContentReady(readyColumnWidths);

  /* ------------------------------------------------- dragging a widget into place
   *
   * The dashboard is a grid a person arranges, and this is the gesture that was asked for
   * (#125). Since #201 the grid is arranged on the dashboard itself, in its editing mode:
   * the cells are the rows (`data-widget-row`) and the grid is the list they are dropped
   * into. It is the *third* way to arrange it, not the first: the four arrows came first
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
      ["csrfmiddlewaretoken", csrfToken()],
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

  onContentReady(readyWidgetDragging);

  /*
   * The browser notifier (#209), in two halves.
   *
   * **Subscribing**, on its connection page. The plugin hands this script the instance's
   * public key, the worker's address and every sentence it may need to say, as data
   * attributes on the card; nothing here is written in English. The button is made here rather
   * than in the template, because without this script it could do nothing, and the field's
   * own help already says that scripts are needed.
   *
   * **Showing**, on every page, for somebody with the notifier switched on. The tab asks for
   * what is waiting now and then, and shows it -- through the service worker where there is
   * one, so a click lands the same way a push does.
   */
  function base64UrlToBytes(text) {
    var padded = (text + "===".slice((text.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    var raw = window.atob(padded);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) {
      bytes[i] = raw.charCodeAt(i);
    }
    return bytes;
  }

  function pushCanWork() {
    return window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window;
  }

  function readyPushSubscribe(card) {
    if (card.hasAttribute("data-web-push-ready")) {
      return;
    }
    card.setAttribute("data-web-push-ready", "");
    var field = card.querySelector("textarea[name=plugin_subscription]");
    var heading = card.querySelector("h3");

    var status = document.createElement("p");
    status.className = "mb-4 text-sm";
    status.setAttribute("role", "status");
    status.setAttribute("data-web-push-status", "");

    if (!window.isSecureContext || !("Notification" in window)) {
      status.textContent = card.dataset.webPushUnsupported || "";
      (heading || card.firstChild).after(status);
      return;
    }

    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn mb-2";
    button.dataset.variant = "outline";
    button.textContent = card.dataset.webPushAllow || "";
    var holder = document.createElement("div");
    holder.appendChild(button);
    holder.appendChild(status);
    if (heading) {
      heading.after(holder);
    } else {
      card.prepend(holder);
    }

    function say(words) {
      status.textContent = words || "";
    }

    button.addEventListener("click", function () {
      say(card.dataset.webPushAsking);
      Notification.requestPermission().then(function (permission) {
        if (permission !== "granted") {
          say(card.dataset.webPushDenied);
          return null;
        }
        if (!pushCanWork()) {
          say(card.dataset.webPushTabOnly);
          return null;
        }
        return navigator.serviceWorker
          .register(card.dataset.webPushWorker, { scope: "/" })
          .then(function () {
            return navigator.serviceWorker.ready;
          })
          .then(function (registration) {
            return registration.pushManager.subscribe({
              userVisibleOnly: true,
              applicationServerKey: base64UrlToBytes(card.dataset.webPushKey || ""),
            });
          })
          .then(function (subscription) {
            if (field) {
              field.value = JSON.stringify(subscription);
            }
            say(card.dataset.webPushSubscribed);
          });
      }).catch(function () {
        // Allowed, but the push service would not have us: private windows, some browsers
        // with push switched off. What is left still works while a tab is open.
        say(card.dataset.webPushTabOnly);
      });
    });
  }

  function readyEveryPushCard() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-web-push-key]"), readyPushSubscribe);
  }

  onContentReady(readyEveryPushCard);

  // Only somewhere on this site, for the reason the worker gives.
  function sameSiteAddress(address) {
    try {
      var target = new URL(address || "/", window.location.origin);
      return target.origin === window.location.origin ? target.href : "/";
    } catch (error) {
      return "/";
    }
  }

  function showNotice(notice) {
    var options = { body: notice.body || "", tag: notice.tag || undefined, data: { url: notice.url || "/" } };
    var direct = function () {
      var shown = new Notification(notice.title || "", options);
      shown.addEventListener("click", function () {
        window.focus();
        window.location.assign(sameSiteAddress(notice.url));
      });
    };
    if (!("serviceWorker" in navigator)) {
      direct();
      return;
    }
    navigator.serviceWorker.getRegistration("/").then(function (registration) {
      if (registration) {
        registration.showNotification(notice.title || "", options);
      } else {
        direct();
      }
    }).catch(direct);
  }

  var collectingNotices = false;

  function collectNotices() {
    var body = document.body;
    var address = body && body.dataset.browserNotices;
    if (!address || collectingNotices || !("Notification" in window) || Notification.permission !== "granted") {
      return;
    }
    collectingNotices = true;
    window
      .fetch(address, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": body.dataset.browserNoticesToken || "", Accept: "application/json" },
      })
      .then(function (response) {
        return response.ok ? response.json() : { notices: [] };
      })
      .then(function (data) {
        (data.notices || []).forEach(showNotice);
      })
      .catch(function () {
        // Offline, or the instance restarting: the notices wait for the next try.
      })
      .then(function () {
        collectingNotices = false;
      });
  }

  function readyNoticeCollection() {
    if (!document.body || !document.body.dataset.browserNotices || document.body.hasAttribute("data-browser-notices-ready")) {
      return;
    }
    document.body.setAttribute("data-browser-notices-ready", "");
    collectNotices();
    // Once a minute. A background tab's timers are slowed to about that anyway, and a reminder
    // is not so urgent that a second's accuracy is worth a request a second.
    window.setInterval(collectNotices, 60000);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        collectNotices();
      }
    });
  }

  onContentReady(readyNoticeCollection);

})();
