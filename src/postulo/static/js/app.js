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
    return document.querySelector("[data-htmx-alert]");
  }

  function sayFailure(words) {
    var region = alertRegion();
    if (region) {
      region.textContent = words || "";
    }
  }

  // A Copy button beside anything marked `data-copy-source` -- the invitation link an
  // administrator has to hand over (#276). Added here rather than written in the template,
  // because copying needs the clipboard API and a button that does nothing without a
  // script is worse than none; the link itself is on the page in full either way.
  function readyCopyButtons() {
    if (!navigator.clipboard) {
      return;
    }
    document.querySelectorAll("[data-copy-source]").forEach(function (source) {
      if (source.dataset.copyReady) {
        return;
      }
      source.dataset.copyReady = "1";
      var button = document.createElement("button");
      button.type = "button";
      button.className = "btn mt-1";
      button.dataset.variant = "outline";
      button.dataset.size = "xs";
      button.textContent = source.dataset.copyLabel || "";
      button.addEventListener("click", function () {
        navigator.clipboard.writeText(source.textContent.trim()).then(function () {
          button.textContent = source.dataset.copiedLabel || button.textContent;
          window.setTimeout(function () {
            button.textContent = source.dataset.copyLabel || "";
          }, 2000);
        });
      });
      source.insertAdjacentElement("afterend", button);
    });
  }

  onContentReady(readyCopyButtons);

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
    // A row's dialog told that the row has gone already is not a failure: it is taken off
    // this copy of the page as well, further down (#303).
    if (rowAlreadyGone(event)) {
      return;
    }
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

  /*
   * An answer for the address already shown is not a new place in the history (#313).
   *
   * Two elements ask for the same thing when Enter is pressed in a table's search box: the
   * box itself, three hundred milliseconds after the last letter, and the button Enter
   * presses. Each answer pushed its address, the same address twice, and Back then had to
   * be pressed twice to leave a search made once. The same was true of Enter in any filter
   * of a form that also narrows as you type. So a push to the address the window already
   * shows becomes a replace: the table is still redrawn from the newer answer, and the
   * history holds each question once.
   *
   * The second request is still sent. Refusing it here would mean deciding, before it
   * goes, that the table on screen is already its answer -- true only because htmx happens
   * to cancel the request in flight before it asks -- and a wrong guess leaves the box
   * saying one thing over a table showing another, which is the worse fault by far.
   */
  document.addEventListener("htmx:beforeHistoryUpdate", function (event) {
    var update = (event.detail || {}).history;
    if (
      update &&
      update.type === "push" &&
      update.path === window.location.pathname + window.location.search
    ) {
      update.type = "replace";
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

  // The flag beside a telephone field's country chooser, and beside the language chooser
  // on Server settings -> Defaults (#208). An <option> holds text and nothing else in
  // every browser, so the flag cannot live in the list; it sits over the closed select
  // instead, and this keeps it pointing at whatever is chosen (#88).
  //
  // The server has already drawn the right flag for the value the field loaded with, so
  // with this script blocked or still loading the field is correct -- it simply stops
  // following the select until the form is saved. Each option carries its own URL because
  // static files are served under a content hash, so there is no pattern to build one
  // from.
  document.addEventListener("change", function (event) {
    var select = event.target.closest("[data-phone-country], [data-flag-select]");
    if (!select) {
      return;
    }
    var holder = select.parentNode.querySelector("[data-phone-flag], [data-flag-holder]");
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

  // The icon beside a web link's choice of service, and the service chosen from the
  // address (#305).
  //
  // The icon is an inline SVG, so unlike a flag there is no address to point an image at.
  // The server draws every icon the choice can show and hides all but the one that goes
  // with the saved service; this shows the one named by the chosen option's `data-icon`.
  // With the script blocked the row is right as it loaded, and the name box still follows
  // the select, because that is the stylesheet's doing (`data-if-other`).
  function showServiceIcon(select) {
    var holder = select.parentNode.querySelector("[data-service-icons]");
    if (!holder) {
      return;
    }
    var option = select.options[select.selectedIndex];
    var wanted = option ? option.getAttribute("data-icon") : "";
    Array.prototype.forEach.call(
      holder.querySelectorAll("[data-service-icon]"),
      function (drawn) {
        drawn.hidden = drawn.getAttribute("data-service-icon") !== wanted;
      }
    );
  }

  // The host of what is in an address box, read as the server will read it: with no
  // scheme typed, https is assumed.
  function hostOfAddress(typed) {
    var address = (typed || "").trim();
    if (!address) {
      return "";
    }
    try {
      var whole = /^[a-z][a-z0-9+.-]*:\/\//i.test(address) ? address : "https://" + address;
      return new URL(whole).hostname.toLowerCase();
    } catch (error) {
      return "";
    }
  }

  // The service whose own host an address is on -- the host itself or a subdomain of it,
  // the rule `Service.hosted` follows in `core/link_services.py` -- or nothing.
  function serviceOfAddress(select, typed) {
    var host = hostOfAddress(typed);
    var found = "";
    if (!host) {
      return found;
    }
    Array.prototype.some.call(select.options, function (option) {
      var mine = (option.getAttribute("data-hosts") || "").split(" ").some(function (known) {
        return known && (host === known || host.slice(-known.length - 1) === "." + known);
      });
      if (mine) {
        found = option.value;
      }
      return mine;
    });
    return found;
  }

  document.addEventListener("change", function (event) {
    var select = event.target.closest && event.target.closest("[data-service-select]");
    if (select) {
      // Chosen by hand, so the address stops choosing for this row.
      delete select.dataset.serviceGuessed;
      showServiceIcon(select);
    }
  });

  // An address pasted or typed into a row where no service was chosen picks the service
  // whose host it is on, at once, so nobody chooses LinkedIn and then pastes a LinkedIn
  // address. Saving does the same for a row left unchosen, so this is the convenience and
  // not the rule. Only a row being added has "nothing chosen" to start from; a choice made
  // by hand is never changed, and one this made follows the address while it is typed.
  document.addEventListener("input", function (event) {
    var box = event.target;
    var row = box.closest && box.closest("[data-link-row]");
    if (!row || box.type !== "url") {
      return;
    }
    var select = row.querySelector("[data-service-select]");
    if (!select || (select.value !== "" && !select.dataset.serviceGuessed)) {
      return;
    }
    var guessed = serviceOfAddress(select, box.value);
    select.value = guessed;
    if (guessed) {
      select.dataset.serviceGuessed = "1";
    } else {
      delete select.dataset.serviceGuessed;
    }
    showServiceIcon(select);
  });

  // What this chose is shown and never posted. It reads the host alone, where saving reads
  // the host and the shape, so LinkedIn's front page, a single video and a page of
  // settings were each chosen for here and then refused as a choice somebody had made --
  // while saving alone, with this script blocked, files them under Other, by the rule that
  // guessing is never how an address comes to be refused. A row still carrying this
  // script's choice goes as it would have without it: with nothing chosen, for saving to
  // decide. The select is put back rather than the posted value changed, so that the page
  // Back returns to holds what was sent; the mark stays, so the address goes on choosing
  // if the page is still here.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.querySelectorAll) {
      return;
    }
    Array.prototype.forEach.call(
      form.querySelectorAll("[data-service-select][data-service-guessed]"),
      function (select) {
        select.value = "";
      }
    );
  });

  // One identifier of each kind (#307). The server switches off, in each row's choice of
  // kind, the kinds the other rows hold -- never the row's own, never Other -- so with this
  // script blocked the rows are right as drawn. This keeps them right while somebody
  // changes a row's kind, ticks *Remove* on a company's form, or takes a row off *Your
  // details* at once (#303) -- the last two give the kind back -- by running the same rule
  // as `OneOfEachKind` in `core/identifiers.py` over the whole block again.
  function holdKinds(block) {
    var rows = Array.prototype.map.call(
      block.querySelectorAll("select[name$='-scheme']"),
      function (select) {
        var line = select.closest("li");
        var remove = line && line.querySelector("input[name$='-DELETE']");
        var kind = remove && remove.checked ? "" : select.value;
        return { select: select, kind: kind === "other" ? "" : kind };
      }
    );
    rows.forEach(function (row) {
      var taken = {};
      rows.forEach(function (other) {
        if (other !== row && other.kind) {
          taken[other.kind] = true;
        }
      });
      Array.prototype.forEach.call(row.select.options, function (option) {
        option.disabled = option.value !== row.select.value && taken[option.value] === true;
      });
    });
  }

  onContentReady(function () {
    Array.prototype.forEach.call(document.querySelectorAll("[data-identifiers]"), holdKinds);
  });

  document.addEventListener("change", function (event) {
    var block = event.target.closest && event.target.closest("[data-identifiers]");
    if (block) {
      holdKinds(block);
    }
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

  /* ------------------------------------------------------- not losing what was typed
   *
   * There was nothing here at all, and there is an eighteen-row textarea for a cover
   * letter. Navigation is a full page load almost everywhere, so the sidebar, the search
   * box, the back button and every link on the page were one click away from discarding
   * an hour's writing, silently and with no way back. Nothing in Postulo has ever asked
   * anybody to retype a letter; it had simply never been tested by somebody who clicked
   * the wrong thing (#258).
   *
   * A form is *dirty* from the first keystroke in it until it is submitted or put back to
   * what it was. `beforeunload` then asks, and the browser decides what that looks like.
   *
   * **The words are the browser's and cannot be ours.** Every engine ignores whatever
   * string a page supplies and shows its own, in the browser's language rather than
   * Postulo's -- so there is nothing here to translate, which is unusual enough in this
   * project to be worth saying where somebody will read it rather than discovering it
   * after wondering where the message went. A browser also refuses to ask at all unless
   * the page has been interacted with, which a form that has been typed into always has.
   *
   * **Dirtiness is per form, and a submit clears only the form submitted.** The capture
   * review has two forms on one page: the review itself, and a discard beside it. The `d`
   * key presses the discard, and a correction typed into the review is exactly what would
   * be lost -- so submitting the discard does not forgive the review, and the question is
   * asked. The `j` key is an ordinary link and was never a special case.
   *
   * **What is typed is not kept.** A draft -- in this browser's storage or in a row of its
   * own -- is a larger decision than this one and was left deliberately: it is data with a
   * lifetime, a place, and an answer for what a half-written letter means in a list of
   * letters. This guard is worth having on its own and does not prejudge it.
   */
  var WATCHED = "input, textarea, select";

  function formsOnThePage() {
    return Array.prototype.slice.call(document.querySelectorAll("form"));
  }

  function isDirty(form) {
    return form.dataset.dirty === "1";
  }

  function anythingDirty() {
    return formsOnThePage().some(isDirty);
  }

  function markDirty(field) {
    var form = field && field.form;
    if (!form || isDirty(form)) {
      return;
    }
    // A form the server draws to hold a filter, a sort or a page number is a control, not
    // work: the list page rewrites the address as somebody narrows it, and asking "are you
    // sure?" for a sort order would teach everybody to click through the question without
    // reading it -- which is how a guard stops guarding anything.
    if ((form.getAttribute("method") || "get").toLowerCase() !== "post") {
      return;
    }
    if (form.hasAttribute("data-no-guard")) {
      return;
    }
    // A control that saves the moment it is finished with has nothing outstanding to lose
    // -- the board's status menus and the plugins page's switches are the whole form.
    if (autosubmitControl(field)) {
      return;
    }
    form.dataset.dirty = "1";
  }

  function watchedField(event) {
    var field = event.target;
    return field && field.form && field.matches && field.matches(WATCHED) ? field : null;
  }

  document.addEventListener("input", function (event) {
    markDirty(watchedField(event));
  });

  // A select changes without an `input` event in engines worth supporting, and a chosen
  // file is a change and never an input at all.
  document.addEventListener("change", function (event) {
    markDirty(watchedField(event));
  });

  document.addEventListener("submit", function (event) {
    // Only this form. The other forms on the page still hold whatever was typed into
    // them, and this submit is about to take the page away from all of them.
    if (event.target && event.target.tagName === "FORM") {
      delete event.target.dataset.dirty;
    }
  });

  // htmx leaves the page where it is, so a form it sends and swaps away is finished with
  // in the same sense a submitted one is.
  document.addEventListener("htmx:afterRequest", function (event) {
    var form = event.target && event.target.closest ? event.target.closest("form") : null;
    if (form) {
      delete form.dataset.dirty;
    }
  });

  window.addEventListener("beforeunload", function (event) {
    if (!anythingDirty()) {
      return;
    }
    // Both spellings: `preventDefault` is what the standard says now, `returnValue` is
    // what older engines act on, and a browser that wants neither ignores both.
    event.preventDefault();
    event.returnValue = "";
  });

  window.addEventListener("pageshow", function () {
    // Coming back to a page from the cache with yesterday's dirty mark on it would ask a
    // question about typing nobody has done in this visit.
    Array.prototype.forEach.call(document.querySelectorAll("form[data-dirty]"), function (form) {
      delete form.dataset.dirty;
    });
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

  // The panels that are not menus -- the column chooser, the saved views, the language
  // picker -- are <details> elements, which open and close themselves and are
  // keyboard-operable without help. What they do not do is close when the pointer goes
  // elsewhere or Escape is pressed, so that part is added here. A menu is a popover, which
  // does both by itself (#310).
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
  //
  // The main navigation is the same kind of problem at the other edge (#299): below `md` it
  // is a bar fixed to the foot of the window, a line taller wherever a label wraps, and the
  // page's bottom padding, `scroll-padding-bottom` and the failure alert all have to clear
  // it. So its height goes into `--bottom-bar-height` the same way, and comes out again
  // once the window is wide enough for the navigation to be a row in the masthead.
  //
  // A ResizeObserver as well as the window's resize: a label that wraps, a font that
  // arrives late and a reader's own text-spacing stylesheet all change a height without the
  // window changing at all. Writing the property moves nothing either element is sized by,
  // so the observer cannot feed itself.
  var siteHeader = document.querySelector("[data-site-header]");
  var mainNav = document.querySelector("[data-nav-main]");
  function measureHeader() {
    var root = document.documentElement;
    if (siteHeader) {
      root.style.setProperty("--header-height", siteHeader.offsetHeight + "px");
    }
    if (mainNav && window.getComputedStyle(mainNav).position === "fixed") {
      root.style.setProperty("--bottom-bar-height", mainNav.offsetHeight + "px");
    } else {
      root.style.removeProperty("--bottom-bar-height");
    }
  }
  function headerHeight() {
    return siteHeader ? siteHeader.offsetHeight : 0;
  }

  /* ------------------------------------------------ the row, fitted to its room
   *
   * How many items the masthead's row holds is decided in the stylesheet by breakpoint,
   * for the widest language the reflow walk is taken in, because without a script nothing
   * can measure what fits (#299). Where this runs it measures instead: the first items in
   * the person's order that fit beside the wordmark and the tools, and the rest under
   * *More* -- so English at 1440 gets all six where the count, written for Greek, gave
   * five, and a language wider than any the count was written for gets fewer rather than
   * a masthead on two lines.
   *
   * It refines and never replaces. Every item is already in the line or under *More*
   * before this runs, `data-fitted` is what lets its answer stand in for the count, and a
   * script that fails half way leaves the count as the whole answer. Below `md` it takes
   * its marks off again: the bar holds four, and that is not a question of room.
   *
   * Three measurements, because *More* has no width while it is hidden, and two widths
   * while it is not: everything in the line first, then *More* with an item under it so
   * that it is drawn -- once holding some other item, and once holding the page you are
   * on, because that is when it is drawn in bolder type (the stylesheet's rule for *More*
   * over the current page), and bolder is wider: ten pixels in Greek under the text-spacing
   * override. Measured only the first way, the row was short by those ten pixels wherever
   * the current page went under *More*, and focusing the search box put the masthead on
   * two lines (#313). Each count of items is tried against *More* in the weight it would
   * have at that count. The search box counts at the width it opens to (`focus:w-48`, half
   * as wide again as it rests), so that clicking into it never pushes the masthead onto a
   * second line.
   */
  function lineItems() {
    return Array.prototype.filter.call(mainNav.children, function (child) {
      return child.tagName === "A";
    });
  }

  function putUnderMore(key, under) {
    Array.prototype.forEach.call(
      mainNav.querySelectorAll('[data-nav="' + key + '"]'),
      function (copy) {
        copy.toggleAttribute("data-in-more", under);
      }
    );
  }

  function fitNavigation() {
    if (!mainNav || !siteHeader) {
      return;
    }
    var items = lineItems();
    var more = mainNav.querySelector("[data-nav-more]");
    var row = mainNav.parentElement;
    var wordmark = row.firstElementChild;
    var tools = mainNav.nextElementSibling;
    if (window.getComputedStyle(mainNav).position === "fixed" || !more || !tools) {
      mainNav.removeAttribute("data-fitted");
      items.forEach(function (item) {
        putUnderMore(item.dataset.nav, false);
      });
      return;
    }
    mainNav.setAttribute("data-fitted", "");
    items.forEach(function (item) {
      putUnderMore(item.dataset.nav, false);
    });
    var widths = items.map(function (item) {
      return item.getBoundingClientRect().width;
    });
    var current = -1;
    items.forEach(function (item, index) {
      if (item.getAttribute("aria-current") === "page") {
        current = index;
      }
    });
    function moreHolding(index) {
      putUnderMore(items[index].dataset.nav, true);
      var width = more.getBoundingClientRect().width;
      putUnderMore(items[index].dataset.nav, false);
      return width;
    }
    var last = items.length - 1;
    var other = current === last ? last - 1 : last;
    var moreBold = current >= 0 ? moreHolding(current) : 0;
    var morePlain = other >= 0 ? moreHolding(other) : moreBold;
    // With `count` items in the line, the page you are on is under *More* if it comes
    // after them.
    function moreWidth(count) {
      return current >= count ? moreBold : morePlain;
    }

    var rowStyle = window.getComputedStyle(row);
    var navGap = parseFloat(window.getComputedStyle(mainNav).columnGap) || 0;
    var rowGap = parseFloat(rowStyle.columnGap) || 0;
    var box = tools.querySelector("[data-search-shortcut]");
    var opens = box && drawn(box) && document.activeElement !== box ? box.offsetWidth / 2 : 0;
    var room =
      row.clientWidth -
      (parseFloat(rowStyle.paddingInlineStart) || 0) -
      (parseFloat(rowStyle.paddingInlineEnd) || 0) -
      wordmark.getBoundingClientRect().width -
      tools.getBoundingClientRect().width -
      opens -
      2 * rowGap;

    function lineWidth(count) {
      var total = 0;
      for (var i = 0; i < count; i++) {
        total += widths[i] + (i ? navGap : 0);
      }
      return total;
    }
    var fits = items.length;
    if (lineWidth(fits) > room) {
      while (fits > 0 && lineWidth(fits) + (fits ? navGap : 0) + moreWidth(fits) > room) {
        fits -= 1;
      }
    }
    items.forEach(function (item, index) {
      putUnderMore(item.dataset.nav, index >= fits);
    });
  }

  var fitScheduled = false;
  function scheduleFit() {
    if (!fitScheduled) {
      fitScheduled = true;
      window.requestAnimationFrame(function () {
        fitScheduled = false;
        fitNavigation();
      });
    }
  }

  function measureChrome() {
    measureHeader();
    scheduleFit();
  }

  fitNavigation();
  measureHeader();
  window.addEventListener("resize", measureChrome);
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(measureChrome);
  }
  if (window.ResizeObserver) {
    var chromeWatcher = new window.ResizeObserver(measureChrome);
    [siteHeader, mainNav].forEach(function (element) {
      if (element) {
        chromeWatcher.observe(element);
      }
    });
  }

  /*
   * The masthead is raised once something has scrolled behind it (#292). A sticky header
   * has one thing to say -- *the page continues above this* -- and a one-pixel border was
   * saying it quietly.
   *
   * An attribute rather than a style, because `style-src 'self'` refuses an inline style as
   * firmly as it refuses an inline script; the shadow is in the stylesheet and this only
   * says when it applies. A page with no script keeps the border it always had, which is
   * the whole fallback.
   */
  function markScrolled() {
    if (siteHeader) {
      if (window.scrollY > 0) {
        siteHeader.setAttribute("data-scrolled", "");
      } else {
        siteHeader.removeAttribute("data-scrolled");
      }
    }
  }
  markScrolled();
  window.addEventListener("scroll", markScrolled, { passive: true });

  // "/" jumps to the search box, as on most sites with one, unless the person is
  // already typing somewhere -- or has switched single-key shortcuts off.
  //
  // The masthead's box is there from `lg` up; below that the masthead has a link to the
  // search page instead (#299), and the key follows it -- unless the page already has a
  // box of its own on the screen, which is the search page itself. Whichever box is drawn
  // first wins, so on a wide screen it is still the one in the masthead. Where the box
  // narrows the page's table (#313), what is below `lg` is the button that opens it, and
  // clicking that opens the panel, which puts the focus in the box (below).
  function drawn(element) {
    return element.getClientRects().length > 0;
  }

  document.addEventListener("keydown", function (event) {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) {
      return;
    }
    if (!singleKeysAllowed() || keyboardIsBusy(event)) {
      return;
    }
    var box = Array.prototype.filter.call(
      document.querySelectorAll("[data-search-shortcut]"),
      drawn
    )[0];
    if (box) {
      event.preventDefault();
      box.focus();
      box.select();
      return;
    }
    var link = document.querySelector("[data-search-link]");
    if (link && drawn(link)) {
      event.preventDefault();
      link.click();
    }
  });

  /*
   * The masthead's search panel, below `lg` on a page whose table the box narrows (#313).
   * Opening it is asking to type, so the box takes the focus -- here, and not by
   * `autofocus`, which would take it on every page load wherever the box is drawn in the
   * row. Without a script the panel opens all the same and the box is the next stop for
   * Tab, which is where the browser puts a popover's contents.
   *
   * And it is closed when the window grows past `lg` while it is open: from there the same
   * element is drawn in the row, and one left open in the top layer would be drawn twice
   * over in the wrong place.
   *
   * **It closes when the focus goes anywhere else** (SC 2.4.11, Focus Not Obscured). The
   * browser closes a popover on Escape and on a click outside it, and not when Tab leaves
   * it; and this one is a bar across the window, in the top layer, over whatever the page
   * scrolls under the masthead -- which is where the browser puts a link it scrolls into
   * view. Tab on from the box, into the table, and the header of the table, its sort links
   * and every few rows had the focus where nobody could see it, behind the bar. A search
   * bar nobody is typing in has no reason to be open, so it goes as the focus leaves. Its
   * own button is not "anywhere else": Shift+Tab from the box lands on it, it is in the
   * masthead and never under the bar, and pressing it is how the bar is closed by hand.
   *
   * `focusin`, not `focusout`: by then the focus is on the new element, so closing the
   * panel has no focus of its own to hand back to the button and takes none away. Focus
   * that leaves the window altogether raises no `focusin` here, and the bar stays as it
   * was for when the window is come back to.
   *
   * Without a script the bar stays open, and the stylesheet keeps the focus clear of it
   * instead: more `scroll-padding-top` while it is open, and the page moved down by its
   * height (`app.css`, beside the panel's own rule).
   */
  document.addEventListener(
    "toggle",
    function (event) {
      var panel = event.target;
      if (event.newState !== "open" || !panel.matches || !panel.matches("[data-site-search]")) {
        return;
      }
      var box = panel.querySelector("[data-search-shortcut]");
      if (box) {
        box.focus();
      }
    },
    true
  );

  document.addEventListener("focusin", function (event) {
    var panel = document.querySelector("[data-site-search]");
    if (!panel || !panel.hidePopover || !panel.matches(":popover-open")) {
      return;
    }
    var target = event.target;
    if (panel.contains(target)) {
      return;
    }
    if (target.getAttribute && target.getAttribute("popovertarget") === panel.id) {
      return;
    }
    panel.hidePopover();
  });

  var wideSearch = window.matchMedia ? window.matchMedia("(min-width: 64rem)") : null;
  if (wideSearch && wideSearch.addEventListener) {
    wideSearch.addEventListener("change", function (query) {
      var panel = document.querySelector("[data-site-search]");
      if (query.matches && panel && panel.hidePopover && panel.matches(":popover-open")) {
        panel.hidePopover();
      }
    });
  }

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

  /* ------------------------------------------------------------------------ menus
   *
   * `<c-dropdown-menu>` is a `<button popovertarget>` and a `popover` panel (#310). The
   * browser opens it on a click, on Enter and on Space, closes it on Escape and on a click
   * anywhere else, gives focus back to the trigger when it closes from inside, and draws
   * it in the top layer, where nothing that scrolls can cut it off. What is added here is
   * what the platform leaves to the page: the arrow keys, and -- where the browser has no
   * anchor positioning -- putting the panel beside its trigger.
   */
  var popovers = typeof HTMLElement !== "undefined" && HTMLElement.prototype.hasOwnProperty("popover");

  function menuPanel(menu) {
    return menu.querySelector(":scope > [popover]");
  }

  function panelIsOpen(panel) {
    return popovers && panel.matches(":popover-open");
  }

  // A menu's items answer to the arrow keys, as a menu is expected to (#262). This adds
  // ArrowDown and ArrowUp on the trigger, which open it onto the first or the last item,
  // and ArrowDown, ArrowUp, Home and End inside it, which move between the items and wrap.
  // The items are links and buttons, so Enter and Space act on them with no help. Only a
  // menu of actions -- the kind whose panel holds `role="menu"` -- gets this; the account
  // menu and *More* are navigation, where Tab is the right key.
  //
  // Opened by pressing its own trigger rather than by `showPopover()`: a panel opened by
  // its `popovertarget` has that trigger for its anchor, and one opened from a script has
  // no anchor at all, so the stylesheet could not put it anywhere near the row.
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
    if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing || !popovers) {
      return;
    }
    var menu = event.target.closest(".dropdown-menu[data-menu]");
    var panel = menu && menuPanel(menu);
    if (!panel || !panel.querySelector('[role="menu"]')) {
      return;
    }
    var items = menuItems(panel);
    if (!items.length) {
      return;
    }
    event.preventDefault();
    var open = panelIsOpen(panel);
    var current = items.indexOf(event.target);
    var next;
    if (event.key === "Home" || (event.key === "ArrowDown" && (current === -1 || !open))) {
      next = 0;
    } else if (event.key === "End" || (event.key === "ArrowUp" && (current === -1 || !open))) {
      next = items.length - 1;
    } else if (event.key === "ArrowDown") {
      next = (current + 1) % items.length;
    } else {
      next = (current - 1 + items.length) % items.length;
    }
    if (!open) {
      var trigger = menu.querySelector(":scope > [popovertarget]");
      if (trigger) {
        trigger.click();
      }
    }
    items[next].focus();
  });

  /* ------------------------------------------------ a panel beside its trigger, by hand
   *
   * The stylesheet ties a menu's panel to its trigger with anchor positioning (#310).
   * Where the browser has none, the panel would stay where the browser puts a popover --
   * centred in the window -- so this places it by the stylesheet's rules instead: below
   * the trigger with its inline end on the trigger's inline end, above when there is no
   * room below, and when it fits on neither side, the first side with room for ten rem of
   * it, scrolling inside itself. It asks the question the stylesheet's `@supports` asks, so
   * exactly one of the two places any panel -- and where the answer was yes but the panel
   * the browser drew is not beside its trigger, it takes over from then on.
   *
   * The box can be measured only once it is drawn, and `toggle` comes after the drawing,
   * so `beforetoggle` puts it below the trigger first -- which needs only the trigger's box
   * -- and `toggle` turns it over or narrows it if it has to. A panel that is open follows
   * its trigger when anything scrolls or the window changes size, as an anchored one does.
   */
  var anchored =
    typeof CSS !== "undefined" &&
    typeof CSS.supports === "function" &&
    CSS.supports("position-area: block-end span-inline-start") &&
    CSS.supports("position-try-fallbacks: flip-block");

  var PANEL_GAP = 4; // the stylesheet's 0.25rem between trigger and panel
  var PANEL_EDGE = 8; // how near the window's edge a panel may come
  var PANEL_ROOM = 160; // 10rem: the least of a panel worth drawing, if it has to scroll

  function panelTrigger(panel) {
    return panel.id
      ? document.querySelector('[popovertarget="' + CSS.escape(panel.id) + '"]')
      : null;
  }

  function placePanel(panel) {
    var trigger = panelTrigger(panel);
    if (!trigger) {
      return;
    }
    var box = trigger.getBoundingClientRect();
    var width = document.documentElement.clientWidth;
    var height = document.documentElement.clientHeight;
    var style = panel.style;
    style.setProperty("position-area", "none");
    style.setProperty("position-try-fallbacks", "none");
    style.margin = "0";
    style.maxHeight = "";

    // Zero while it is not drawn yet, which reads as "fits below".
    var tall = panel.offsetHeight;
    var below = height - box.bottom - PANEL_GAP - PANEL_EDGE;
    var above = box.top - PANEL_GAP - PANEL_EDGE;
    var up = false;
    if (tall > below) {
      if (tall <= above) {
        up = true;
      } else if (below >= PANEL_ROOM) {
        style.maxHeight = below + "px";
      } else if (above >= PANEL_ROOM) {
        up = true;
        style.maxHeight = above + "px";
      }
    }
    if (up) {
      style.top = "auto";
      style.bottom = height - box.top + PANEL_GAP + "px";
    } else {
      style.top = box.bottom + PANEL_GAP + "px";
      style.bottom = "auto";
    }

    var wide = panel.offsetWidth;
    var rtl = window.getComputedStyle(panel).direction === "rtl";
    if (!wide) {
      // Not drawn yet: the inline end on the trigger's, which needs no width.
      style.left = rtl ? box.left + "px" : "auto";
      style.right = rtl ? "auto" : width - box.right + "px";
      return;
    }
    var left = rtl ? box.left : box.right - wide;
    left = Math.max(PANEL_EDGE, Math.min(left, width - wide - PANEL_EDGE));
    style.left = left + "px";
    style.right = "auto";
  }

  function isMenuPanel(node) {
    return node && node.nodeType === 1 && node.matches("[data-popover][popover]");
  }

  function placeOpenPanels(event) {
    var source = event && event.target;
    document.querySelectorAll("[data-popover][popover]").forEach(function (panel) {
      // Scrolling the panel's own list moves nothing it is placed by.
      if (panelIsOpen(panel) && !(source && source.nodeType === 1 && panel.contains(source))) {
        placePanel(panel);
      }
    });
  }

  var placingByHand = false;

  function placeByHand() {
    if (placingByHand) {
      return;
    }
    placingByHand = true;
    document.addEventListener(
      "beforetoggle",
      function (event) {
        if (event.newState === "open" && isMenuPanel(event.target)) {
          placePanel(event.target);
        }
      },
      true
    );
    document.addEventListener(
      "toggle",
      function (event) {
        if (event.newState === "open" && isMenuPanel(event.target)) {
          placePanel(event.target);
        }
      },
      true
    );
    document.addEventListener("scroll", placeOpenPanels, { capture: true, passive: true });
    window.addEventListener("resize", placeOpenPanels);
  }

  // Whether a panel the browser placed sits against its trigger. A trigger with no box --
  // *More* while the line holds every item -- has nothing to sit against and proves nothing.
  function besideItsTrigger(panel) {
    var trigger = panelTrigger(panel);
    if (!trigger) {
      return true;
    }
    var from = trigger.getBoundingClientRect();
    if (!from.width && !from.height) {
      return true;
    }
    var box = panel.getBoundingClientRect();
    var near = 2 * PANEL_GAP;
    return Math.abs(box.top - from.bottom) <= near || Math.abs(from.top - box.bottom) <= near;
  }

  if (popovers && !anchored) {
    placeByHand();
  } else if (popovers) {
    // `CSS.supports` vouches for anchor positioning; it cannot vouch for the browser taking a
    // `popovertarget` for the panel's anchor, which is the half the stylesheet relies on. So
    // each time a menu opens, look: a panel that is not beside its trigger is placed by hand,
    // and so is every one after it.
    document.addEventListener(
      "toggle",
      function (event) {
        if (placingByHand || event.newState !== "open" || !isMenuPanel(event.target)) {
          return;
        }
        if (!besideItsTrigger(event.target)) {
          placeByHand();
          placePanel(event.target);
        }
      },
      true
    );
  }

  /* ---------------------------------------------------------------------- dialogs
   *
   * `<c-dialog>` is a `<dialog popover>`, and the button that opens it a `<button
   * popovertarget>` (#303). With no script the browser opens it as a popover: over the page,
   * closed on Escape, on a click outside and on *Cancel*, and not modal. Here the same button
   * opens it with `showModal()` instead, so the rest of the page is inert and focus cannot
   * wander out of the question while it is being asked. Cancelling the click is what stops
   * the popover opening as well: a button's `popovertarget` is its activation behaviour,
   * which a cancelled click does not run.
   *
   * *Cancel* hides a popover by its own `popovertargetaction="hide"`, and a modal dialog is
   * not a popover, so it is closed here. Escape closes a modal dialog by itself. Either way
   * focus goes back to the button that opened it, which the browser does on its own; it is
   * done here too, for the one that does not, unless the button has gone with its row.
   */
  function dialogOf(button) {
    var id = button.getAttribute("popovertarget");
    var dialog = id ? document.getElementById(id) : null;
    return dialog && dialog.tagName === "DIALOG" && dialog.hasAttribute("data-dialog") ? dialog : null;
  }

  var dialogOpener = {};

  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("button[popovertarget]") : null;
    var dialog = button && dialogOf(button);
    if (!dialog || typeof dialog.showModal !== "function") {
      return;
    }
    if (button.getAttribute("popovertargetaction") === "hide") {
      if (dialog.open) {
        event.preventDefault();
        dialog.close();
      }
      return;
    }
    if (dialog.open || (popovers && dialog.matches(":popover-open"))) {
      return;
    }
    event.preventDefault();
    dialogOpener[dialog.id] = button;
    dialog.showModal();
  });

  document.addEventListener(
    "close",
    function (event) {
      var dialog = event.target;
      if (!dialog || dialog.tagName !== "DIALOG" || !dialog.hasAttribute("data-dialog")) {
        return;
      }
      var opener = dialogOpener[dialog.id];
      delete dialogOpener[dialog.id];
      var said = dialog.querySelector("[data-dialog-said]");
      if (said) {
        said.textContent = "";
      }
      var lost = !document.activeElement || document.activeElement === document.body;
      if (opener && opener.isConnected && (lost || dialog.contains(document.activeElement))) {
        opener.focus();
      }
    },
    true
  );

  /* ------------------------------------------------ a row off *Your details*, at once
   *
   * A row's dialog is a form of its own, posting to that row's address (#303). htmx sends it,
   * and the answer is JSON for this: whether the row went, the sentence to say, which row of
   * the block is primary now, and how many the block has left. Nothing else on the page is
   * touched -- whatever was typed into the other rows, or anywhere else, is still there, and
   * still asks before the page is left (#258), because the page is never left.
   *
   * **The row leaves its key behind.** The page is one formset per block, and its management
   * form counted this row when the page was drawn; a row that simply vanished would be a gap
   * the next *Save* fails on. So the row's hidden fields stay, in a hidden list item, with
   * its removal ticked -- which is how Django passes over a row that has already gone -- and
   * a page drawn again after a refused save draws the same thing back.
   *
   * **Focus goes on, not away.** The bin that was pressed went with its row, so focus goes
   * to the next row that can be removed, to its first control; where there is none, to the
   * block's own heading. The count beside the block in the sidebar is the new one, the star
   * moves to the row that inherited *Primary* -- and so does the sentence in its dialog that
   * says removing it hands the primary on -- and an identifier's kind is free for the other
   * rows again.
   *
   * **Then the block says what went**, in its polite live region, and only then: focus has
   * moved first, and the sentence is written a moment later, once. A screen reader that
   * drops what it is saying when focus moves would otherwise cut the sentence short, and
   * one written twice is read twice.
   *
   * **A row that had gone already goes from this copy too.** A second tab, or the page the
   * Back button brings back, still draws a row taken off somewhere else, and its address
   * answers 404: there is no such row. That is the outcome the person asked for, not a
   * failure, so the row is taken off here as well and the block says it had already been
   * removed -- the dialog carries that sentence, written by the server with the rest. The
   * answer names no primary and no count, so the star stays where this copy has it and the
   * sidebar counts the bins that are left.
   */
  // How long after focus has moved the block says what went.
  var SAID_AFTER = 100;

  function removalAnswer(xhr) {
    try {
      return JSON.parse((xhr && xhr.responseText) || "{}");
    } catch (error) {
      return {};
    }
  }

  function firstControl(row) {
    return row.querySelector("input:not([type=hidden]), select, textarea, button, a[href]");
  }

  function landingAfter(row, block) {
    var next = row.nextElementSibling;
    while (next && (next.hidden || !next.querySelector("[data-remove-trigger]"))) {
      next = next.nextElementSibling;
    }
    if (next) {
      return firstControl(next);
    }
    var heading = block.querySelector("legend");
    if (heading) {
      heading.setAttribute("tabindex", "-1");
    }
    return heading;
  }

  function leaveTheKey(row) {
    var ghost = document.createElement("li");
    ghost.hidden = true;
    ghost.setAttribute("data-removed", "");
    var key = null;
    Array.prototype.forEach.call(row.querySelectorAll("input[type=hidden]"), function (field) {
      if (/-id$/.test(field.name)) {
        key = field;
      }
      ghost.appendChild(field);
    });
    if (key) {
      var removal = document.createElement("input");
      removal.type = "hidden";
      removal.name = key.name.replace(/-id$/, "-DELETE");
      removal.value = "on";
      ghost.appendChild(removal);
    }
    row.replaceWith(ghost);
  }

  // The saved row's key, as the formset's hidden field carries it; nothing for a new row.
  function rowKey(row) {
    var found = "";
    Array.prototype.some.call(row.querySelectorAll("input[type=hidden]"), function (field) {
      if (/-id$/.test(field.name)) {
        found = field.value;
        return true;
      }
      return false;
    });
    return found;
  }

  function rowsLeft(block) {
    return Array.prototype.slice.call(block.querySelectorAll("li:not([hidden])"));
  }

  // Only where the star went with its row. A star somebody moved and has not saved yet is
  // what they typed, and stays where they put it.
  function markPrimary(block, pk) {
    if (pk === null || pk === undefined) {
      return;
    }
    if (block.querySelector("li:not([hidden]) input[type=radio][name$='-primary']:checked")) {
      return;
    }
    rowsLeft(block).some(function (row) {
      if (rowKey(row) !== String(pk)) {
        return false;
      }
      var star = row.querySelector("input[type=radio][name$='-primary']");
      if (star) {
        star.checked = true;
      }
      return true;
    });
  }

  // Each dialog of a block with a primary holds the sentence "the next one becomes the
  // primary", hidden on every row but the one it is true of. That was settled when the page
  // was drawn, and a row gone since may have changed it: the primary the server kept is the
  // row whose removal hands it on, and only while another row is left to take it.
  function sayWhoHandsOn(block, pk, count) {
    var kept = pk === null || pk === undefined ? "" : String(pk);
    rowsLeft(block).forEach(function (row) {
      var trigger = row.querySelector("[data-remove-trigger]");
      var dialog = trigger && dialogOf(trigger);
      var sentence = dialog && dialog.querySelector("[data-hands-on]");
      if (sentence) {
        sentence.hidden = !(kept && rowKey(row) === kept && count > 1);
      }
    });
  }

  // A request that failed is said inside the dialog as well as at the foot of the page:
  // behind a modal the page is inert, so the foot's alert can be neither seen nor heard.
  function sayInTheDialog(event, words) {
    var source = (event.detail || {}).elt;
    var dialog = source && source.closest ? source.closest("dialog[data-dialog]") : null;
    var said = dialog && dialog.querySelector("[data-dialog-said]");
    if (said) {
      said.textContent = words;
    }
  }

  // The dialog of a row whose address has just answered that there is no such row.
  function rowAlreadyGone(event) {
    var detail = event.detail || {};
    var form = detail.elt;
    var dialog = form && form.closest ? form.closest("dialog[data-remove-row]") : null;
    return dialog && detail.xhr && detail.xhr.status === 404 ? dialog : null;
  }

  function sayRemoved(block, words) {
    var region = block.querySelector("[data-removed-said]");
    if (region) {
      window.setTimeout(function () {
        region.textContent = words || "";
      }, SAID_AFTER);
    }
  }

  document.addEventListener("htmx:responseError", function (event) {
    if (rowAlreadyGone(event)) {
      return;
    }
    var xhr = (event.detail || {}).xhr;
    sayInTheDialog(
      event,
      failureWords("alertFailed").replace("{status}", String((xhr && xhr.status) || 0))
    );
  });

  ["htmx:sendError", "htmx:timeout"].forEach(function (name) {
    document.addEventListener(name, function (event) {
      sayInTheDialog(event, failureWords("alertOffline"));
    });
  });

  document.addEventListener("htmx:afterRequest", function (event) {
    var detail = event.detail || {};
    var form = detail.elt;
    var dialog = form && form.closest ? form.closest("dialog[data-remove-row]") : null;
    if (!dialog) {
      return;
    }
    var answer;
    if (detail.successful) {
      answer = removalAnswer(detail.xhr);
      if (!answer.removed) {
        var said = dialog.querySelector("[data-dialog-said]");
        if (said) {
          said.textContent = answer.said || "";
        }
        return;
      }
    } else if (rowAlreadyGone(event)) {
      answer = { said: dialog.getAttribute("data-gone-said") || "" };
      sayFailure("");
    } else {
      return;
    }
    var trigger = document.querySelector(
      '[data-remove-trigger][popovertarget="' + CSS.escape(dialog.id) + '"]'
    );
    var row = trigger ? trigger.closest("li") : null;
    var block = row ? row.closest("fieldset") : null;
    delete dialogOpener[dialog.id];
    if (dialog.open) {
      dialog.close();
    } else if (popovers && dialog.matches(":popover-open")) {
      dialog.hidePopover();
    }
    dialog.remove();
    if (!row || !block) {
      return;
    }
    var landing = landingAfter(row, block);
    leaveTheKey(row);
    var left =
      answer.count !== undefined
        ? answer.count
        : block.querySelectorAll("li:not([hidden]) [data-remove-trigger]").length;
    if (answer.primary !== undefined) {
      markPrimary(block, answer.primary);
      sayWhoHandsOn(block, answer.primary, left);
    }
    if (block.matches("[data-identifiers]")) {
      holdKinds(block);
    }
    var count = block.id
      ? document.querySelector('[data-section-link="' + CSS.escape(block.id) + '"] .badge')
      : null;
    if (count) {
      count.textContent = String(left);
    }
    if (landing) {
      landing.focus();
    }
    sayRemoved(block, answer.said);
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
    chip.className = "badge";
    chip.dataset.variant = "chip";
    if (isNew) chip.dataset.new = "";
    var text = document.createElement("span");
    text.textContent = isNew ? (box.dataset.labelsNewHint || "{label}").replace("{label}", name) : name;
    chip.appendChild(text);

    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn";
    button.dataset.variant = "ghost";
    button.dataset.size = "icon-xs";
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
    field.className = "mt-2";
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

  /* ------------------------------------------- a control that swaps itself out of existence
   *
   * Ticking a reminder off removes the button that was pressed. Focus then belongs to the
   * document, and the next Tab starts again at the skip link -- which is #227's rule, and
   * the reminder tick is exactly the shape it describes (#257).
   *
   * The button says where focus should go once it is gone. It is read *before* the swap,
   * because after it the button is not there to ask. Where the region it names is empty
   * afterwards -- the last reminder -- the region itself takes focus, which is why it is
   * given `tabindex="-1"`: a heading nobody can reach is not somewhere to land.
   */
  var focusAfterSwap = null;

  document.addEventListener("htmx:beforeRequest", function (event) {
    var source = event.detail && event.detail.elt;
    var button = source && source.querySelector ? source.querySelector("[data-focus-after]") : null;
    if (!button && source && source.closest) {
      button = source.closest("[data-focus-after]");
    }
    focusAfterSwap = button ? button.getAttribute("data-focus-after") : null;
  });

  document.addEventListener("htmx:afterSettle", function () {
    if (!focusAfterSwap) {
      return;
    }
    var landing = document.querySelector(focusAfterSwap);
    focusAfterSwap = null;
    if (!landing) {
      return;
    }
    // The next tick if there is one, so working through a list stays a list of Tab-free
    // presses; the region itself when that was the last of them.
    var next = landing.querySelector("[data-focus-after]");
    (next || landing).focus();
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
