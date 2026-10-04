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

  /* ---------------------------------- what a swap brought in answers from the moment it lands
   *
   * htmx wires what it swapped in when the swap *settles*, twenty milliseconds after the new
   * markup is on the page. For those twenty milliseconds a control that was replaced -- a
   * list, a tick box, a date in a table's header -- is on the screen with the focus in it,
   * and hears nothing: a change made then is kept by the control and never sent, so the
   * list says one status over a table narrowed by another. A hand choosing with the mouse
   * is never that quick. A key held down in a list is, on a server that answers between two
   * repeats of it, and the last repeat is the one that is lost (#314).
   *
   * Wiring is idempotent -- htmx skips what it has already initialised, which is what the
   * cell editor's Escape relies on below (#161) -- so this does at once what the settle
   * would have done, and the settle finds nothing left to do.
   */
  document.addEventListener("htmx:afterSwap", function (event) {
    if (window.htmx && event.target && event.target.nodeType === 1) {
      window.htmx.process(event.target);
    }
  });

  /* --------------------------------- a header's filter that was opened stays open in a swap
   *
   * A column's filter is a disclosure in its header, and the header is redrawn with the
   * table every time a filter narrows it. The server draws open the ones that are narrowing
   * and the one whose control asked (#626) -- which is the one with the focus in it, and the
   * reason that part is the server's: htmx puts the focus back as the markup lands, and a
   * control inside a closed disclosure cannot take it.
   *
   * It cannot know about the others. Somebody who opens *Status* and *Role* to set both, and
   * chooses a status, had the role's filter fold shut before they reached it (#314): since
   * the filters above the applications table moved into its headers, narrowing by two
   * things is opening two headers. So what was open in the table being replaced is open in
   * the one that replaces it. Nothing is closed by this: a header the server drew open
   * stays open.
   */
  var openColumnFilters = [];

  document.addEventListener("htmx:beforeSwap", function (event) {
    var target = event.detail && event.detail.target;
    openColumnFilters = [];
    if (!target || !target.querySelectorAll) {
      return;
    }
    Array.prototype.forEach.call(
      target.querySelectorAll("th[data-col] [data-col-filter][open]"),
      function (disclosure) {
        openColumnFilters.push(disclosure.closest("th[data-col]").getAttribute("data-col"));
      }
    );
  });

  document.addEventListener("htmx:afterSwap", function () {
    var keys = openColumnFilters;
    openColumnFilters = [];
    Array.prototype.forEach.call(document.querySelectorAll("th[data-col]"), function (cell) {
      var disclosure = cell.querySelector("[data-col-filter]");
      if (disclosure && keys.indexOf(cell.getAttribute("data-col")) !== -1) {
        disclosure.open = true;
      }
    });
  });

  // An answer that was not swapped in -- a failure, a 204 -- leaves the table as it was, and
  // what was noted for it must not be applied to some later swap.
  document.addEventListener("htmx:afterRequest", function () {
    openColumnFilters = [];
  });

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
  // every browser, so the flag cannot live in a native list; it sits over the closed
  // select instead, and this keeps it pointing at whatever is chosen (#88).
  //
  // Since #301 a select has a control of its own, which draws the flag in its button and
  // in its list and puts this one away. This is what is left for a select that has none:
  // one a page keeps native, or a browser the control cannot be built in.
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

  // The service chosen from the address (#305).
  //
  // The icon beside the choice used to be this script's as well: the server drew every
  // icon the choice could show and this un-hid the one that went with it. A select's own
  // control draws its options' icons now (#301), the chosen one in the closed control,
  // so that special case is gone; with the script blocked the server's one icon stands
  // beside the native select, right as the row loaded.
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
    selectChanged(select);
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
        selectChanged(select);
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
  //
  // What a card is dropped on is its column's whole section, `data-board-section`, and not
  // only the list of cards in it (#315): a folded column is a strip whose list is hidden,
  // and it is still a column a card can be moved to. The card goes into that hidden list
  // and off the screen, and the strip's count says it arrived.
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
    return node && node.closest ? node.closest("[data-board-section]") : null;
  }

  function cardsOf(column) {
    return column ? column.querySelector("[data-board-column]") : null;
  }

  // An open column tints its list of cards, as it always did. A strip has no list on the
  // screen to tint, so the strip itself is.
  function highlight(column, on) {
    var cards = cardsOf(column);
    if (!cards) {
      return;
    }
    [column, cards].forEach(function (part) {
      var tinted = on && part === (cards.hidden ? column : cards);
      part.classList.toggle("bg-ink-100", tinted);
      part.classList.toggle("dark:bg-ink-800", tinted);
    });
  }

  // By one more or one fewer, and not by counting the cards: a strip's are not on the page,
  // and its count is the server's. The count is also said in words, for a screen reader,
  // and those are the server's too: until the page comes back with them, the figure stands
  // in their place, which is less than a sentence and not untrue.
  function recount(column, by) {
    var counter = column && column.querySelector("[data-column-count]");
    if (counter) {
      var held = parseInt(counter.textContent, 10) || 0;
      var now = String(Math.max(0, held + by));
      counter.textContent = now;
      var words = column.querySelector("[data-column-count-words]");
      if (words) {
        words.textContent = now;
      }
    }
    var cards = cardsOf(column);
    var empty = cards && cards.querySelector("[data-empty]");
    if (empty) {
      empty.hidden = cards.querySelectorAll("[data-card]").length > 0;
    }
  }

  // The page's filter form, which the board names: the question as it now stands.
  function boardFilters() {
    var box = document.querySelector("[data-board][data-board-filters]");
    return box ? document.getElementById(box.getAttribute("data-board-filters")) : null;
  }

  // Whether the board on the screen was drawn for *Gone quiet*: the address is what it was
  // drawn for, and the server reads the last of a parameter given twice.
  function drawnQuiet() {
    var asked = new URLSearchParams(window.location.search).getAll("quiet");
    return asked.length > 0 && asked[asked.length - 1].trim() !== "";
  }

  // One application fewer under the page's title, in the sentence the server wrote for
  // that (`application_list.html`): this file has no words of its own. Once.
  function oneFewerOnThePage() {
    var words = document.querySelector("#applications-count [data-count-words][data-one-fewer]");
    if (words) {
      words.textContent = words.getAttribute("data-one-fewer");
      words.removeAttribute("data-one-fewer");
    }
  }

  /* ------------------------ the board scrolls while a card is held near its edge (#315)
   *
   * A column off the screen could be dropped on only by letting go, scrolling and starting
   * again. While a card is being dragged, the board's scroll box scrolls towards whichever
   * of its two side edges the pointer is near, faster the nearer it is, up to where the
   * browser's own scrolling takes over.
   *
   * **Only while a card is held.** A board that moved whenever the pointer passed near its
   * edge would move under somebody reading it. It stops the moment the card is dropped,
   * the drag is abandoned (Escape ends a drag, and the browser says so with `dragend`), or
   * the pointer leaves the window -- and, because a browser reports a drag only through
   * `dragover`, when none has come for longer than a browser ever leaves between two.
   *
   * **Never for somebody who asked for less motion.** The board then scrolls by hand, and
   * a card still moves by its menu, which is the way that works everywhere.
   *
   * **A browser may scroll a box under a drag by itself**, and this makes room for that
   * and does not add to it. Chromium does, within twenty pixels of the edge, the faster
   * the nearer -- nothing at twenty, about 150 pixels a second at eighteen, 530 at twelve,
   * 1,000 at four -- and whatever the person's preference about motion: that is the
   * browser's, on every page, and is left alone. Both at once were twice this file's
   * fastest, eight pixels from the edge. So the speed here rises from the far side of the
   * band to its fastest where the browser's band begins, and falls from there to nothing
   * half way into it, about as fast as the browser's rises: together they stay near the
   * fastest until the browser is scrolling alone. Past the edge the browser does nothing,
   * and this scrolls at its fastest.
   *
   * A browser with no band of its own is left with ten pixels along the edge in which the
   * board does not scroll, and ten more in which it slows. That is accepted: the other way
   * round is a board that bolts in the browser most people use.
   *
   * **The band is a fifth of the box at most.** Seventy-two pixels at either side of a
   * narrow box would be most of it, and a card picked up near its end would scroll at once.
   *
   * The edges are the box's as drawn, and `scrollLeft` moves the way its sign says in
   * either direction of writing, so a right-to-left board needs nothing of its own.
   */
  var EDGE = 72; // pixels from an edge within which the board scrolls, at the most
  var EDGE_SHARE = 5; // and never more than one part in this many of the box's width
  var BROWSERS = 20; // pixels from an edge within which a browser scrolls the box itself
  var FASTEST = 700; // pixels a second, where the browser's band begins and past the edge
  var SLOWEST = 0.15; // of that, at the far side of the band
  var SILENCE = 700; // milliseconds without a `dragover` after which the drag has gone
  var lessMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var edgeScroll = null;

  function stopEdgeScroll() {
    if (edgeScroll && edgeScroll.frame) {
      window.cancelAnimationFrame(edgeScroll.frame);
    }
    edgeScroll = null;
  }

  // Pixels a second, signed the way `scrollLeft` moves: nothing unless the pointer is level
  // with the box and within the band along one of its side edges, or beyond that edge.
  function edgeSpeed(box, x, y) {
    var around = box.getBoundingClientRect();
    if (y < around.top || y > around.bottom) {
      return 0;
    }
    var before = x - around.left;
    var after = around.right - x;
    var near = Math.min(before, after);
    var band = Math.min(EDGE, around.width / EDGE_SHARE);
    if (near >= band) {
      return 0;
    }
    var pace = 1; // past the edge, where no browser scrolls the box
    if (near >= BROWSERS) {
      // From the far side of the band up to where the browser's begins.
      pace = SLOWEST + (1 - SLOWEST) * (1 - (near - BROWSERS) / Math.max(band - BROWSERS, 1));
    } else if (near >= 0) {
      // Inside the browser's: down to nothing by the middle of it.
      pace = Math.max(0, (2 * near) / BROWSERS - 1);
    }
    return (before < after ? -1 : 1) * FASTEST * pace;
  }

  function stepEdgeScroll(now) {
    if (!edgeScroll) {
      return;
    }
    if (!dragging || now - edgeScroll.seen > SILENCE) {
      stopEdgeScroll();
      return;
    }
    // By the time that passed, so a slow machine scrolls as far in a second as a fast one;
    // but a frame that was held up is not a reason to jump, and no step counts for more
    // than a tenth of a second.
    var elapsed = Math.min(Math.max(now - edgeScroll.last, 0), 100);
    edgeScroll.last = now;
    edgeScroll.box.scrollLeft += (edgeScroll.speed * elapsed) / 1000;
    edgeScroll.frame = window.requestAnimationFrame(stepEdgeScroll);
  }

  function followTheEdge(event) {
    var box = document.querySelector("[data-board]");
    var speed =
      dragging && box && !(lessMotion && lessMotion.matches)
        ? edgeSpeed(box, event.clientX, event.clientY)
        : 0;
    if (!speed) {
      stopEdgeScroll();
      return;
    }
    var now = window.performance.now();
    if (!edgeScroll) {
      edgeScroll = { last: now, frame: 0 };
      edgeScroll.frame = window.requestAnimationFrame(stepEdgeScroll);
    }
    edgeScroll.box = box;
    edgeScroll.speed = speed;
    edgeScroll.seen = now;
  }

  /* ------------------------------------- a card that is held is not swapped away (#315)
   *
   * The board is replaced whenever something live asks for it: a filter, the masthead's
   * box, a column's heading. A filter chosen a moment before a card was picked up can
   * answer while it is held, and the answer would take the card out from under the
   * pointer: the drag ends on an element that is no longer on the page, and the drop does
   * nothing. So while a card is held nothing is asked for the board and no answer is put
   * in it -- a column does not fold either -- and once the card is let go without being
   * moved, the board is asked for again, as the filters now stand. A card that was moved
   * needs no asking: its form loads the page. But the address that form goes back to was
   * written with the board, before the question that was refused, so it is written again
   * from the filter form as it stands (`theQuestionAsItStands`): a filter chosen a moment
   * before a card was moved was otherwise lost without a word.
   */
  var boardOwed = false;

  // The address the page's live controls would ask for now: the filter form's fields, the
  // status in force among them, and the masthead's box, which the form includes. Empty
  // values and all, as htmx sends them, so that it is never the bare address, which a
  // default view answers in place of the board.
  function theQuestionAsItStands(filters) {
    var asked = new URLSearchParams(new FormData(filters));
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-table-search]"),
      function (box) {
        if (box.name && box.form !== filters) {
          asked.set(box.name, box.value);
        }
      }
    );
    return window.location.pathname + "?" + asked.toString();
  }

  // Whether what a request is for, or what a swap put in, has a board in it.
  function hasABoard(node) {
    return Boolean(
      node &&
      node.nodeType === 1 &&
      (node.matches("[data-board]") || node.querySelector("[data-board]"))
    );
  }

  function holdsTheBoard(node) {
    return Boolean(dragging) && hasABoard(node);
  }

  document.addEventListener("htmx:confirm", function (event) {
    if (holdsTheBoard((event.detail || {}).target)) {
      event.preventDefault();
      boardOwed = true;
    }
  });

  document.addEventListener("htmx:beforeSwap", function (event) {
    if (holdsTheBoard((event.detail || {}).target)) {
      event.detail.shouldSwap = false;
      boardOwed = true;
    }
  });

  function endDrag() {
    var held = dragging;
    var owed = boardOwed && held && !held.moved;
    stopEdgeScroll();
    dragging = null;
    boardOwed = false;
    if (held) {
      held.card.classList.remove("opacity-50");
    }
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-board-section]"),
      function (column) {
        highlight(column, false);
      }
    );
    // Asking the page's filter form is asking for the board as the filters, the search and
    // the fold now stand.
    var filters = boardFilters();
    if (owed && filters && window.htmx) {
      window.htmx.trigger(filters, "submit");
    }
  }

  document.addEventListener("dragstart", function (event) {
    var card = event.target.closest && event.target.closest("[data-card]");
    if (!card) {
      return;
    }
    dragging = { card: card, from: columnOf(card), moved: false };
    card.classList.add("opacity-50");
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      // Firefox will not start a drag without something on the transfer.
      event.dataTransfer.setData("text/plain", card.dataset.card || "");
    }
  });

  document.addEventListener("dragend", endDrag);

  // A browser ends a drag on Escape by itself and says so with `dragend`. This is the same
  // end for one that hands the key to the page instead.
  document.addEventListener("keydown", function (event) {
    if (dragging && event.key === "Escape") {
      endDrag();
    }
  });

  // A browser hands the page no mouse event while something is dragged. So a `mousemove`
  // with a card still counted as held is a drag whose `dragend` never came, and a card
  // held for ever would be a board that is never drawn again.
  document.addEventListener("mousemove", function () {
    if (dragging) {
      endDrag();
    }
  });

  /* `dragenter` as well as `dragover`, and both cancelled. The specification makes an
   * element a drop target only once *both* are cancelled; Chromium accepts `dragover`
   * alone, Firefox does not, and refuses the drop with nothing logged. The suite runs
   * `--browser chromium`, so it agreed with the one browser that forgives the omission
   * (#174). */
  document.addEventListener("dragenter", function (event) {
    if (!dragging) {
      return;
    }
    // Where the pointer is now, said before the first `dragover` over what it entered.
    followTheEdge(event);
    if (columnOf(event.target)) {
      event.preventDefault();
    }
  });

  document.addEventListener("dragover", function (event) {
    if (!dragging) {
      return;
    }
    followTheEdge(event);
    var column = columnOf(event.target);
    if (!column) {
      return;
    }
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "move";
    }
    highlight(column, column !== dragging.from);
  });

  document.addEventListener("dragleave", function (event) {
    // Out of the window altogether: nothing is being left *for*.
    if (dragging && !event.relatedTarget) {
      stopEdgeScroll();
    }
    var column = columnOf(event.target);
    if (column && !column.contains(event.relatedTarget)) {
      highlight(column, false);
    }
  });

  document.addEventListener("drop", function (event) {
    stopEdgeScroll();
    var column = columnOf(event.target);
    if (!dragging || !column) {
      return;
    }
    event.preventDefault();
    highlight(column, false);
    var card = dragging.card;
    var from = dragging.from;
    var cards = cardsOf(column);
    var status = cards && cards.dataset.boardColumn;
    if (!status || column === from) {
      return;
    }
    var select = card.querySelector("select[name='status']");
    if (!select) {
      return;
    }
    // Optimistic: the card moves now, and the form that was already there does the
    // saving. If the server refuses, the page it sends back is the truth.
    //
    // The counts say what that page will say. A move is something happening, so under
    // *Gone quiet* the card is quiet no longer: it leaves the board, and the column it
    // went to gains nothing. And a card that leaves what the address asks for -- that, or
    // out of an open column of a folded board into a strip -- is one fewer under the
    // page's title as well as in its column. Into another open column it is still what the
    // address asks for (#709).
    var quiet = drawnQuiet();
    var folded = column.hasAttribute("data-board-strip");
    cards.appendChild(card);
    card.dataset.status = status;
    recount(from, -1);
    if (quiet) {
      card.hidden = true;
    } else {
      recount(column, 1);
    }
    if (quiet || folded) {
      oneFewerOnThePage();
    }
    select.value = status;
    selectChanged(select);
    if (select.form) {
      var filters = boardFilters();
      var back = select.form.elements.namedItem("next");
      if (boardOwed && filters && back) {
        back.value = theQuestionAsItStands(filters);
      }
      dragging.moved = true;
      select.form.requestSubmit();
    }
  });

  /* ------------------------------------ a move leaves the board where it was (#315)
   *
   * A move loads the page, and a page that loads is a board at its start: a card dropped
   * on a column that was reached by scrolling came back with that column off the screen
   * again. How far the box was scrolled is kept across that one load -- in the tab's own
   * storage, with the address the form goes back to, and not in the address, which is the
   * question and nothing else -- and put back once, on the page that address draws.
   * However the move was made: a card's menu moves it from a column scrolled to as well.
   */
  var BOARD_PLACE = "postulo.board.place";

  function tabStorage() {
    try {
      return window.sessionStorage;
    } catch (refused) {
      return null;
    }
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var box = form && form.closest ? form.closest("[data-board]") : null;
    var back = box && form.elements.namedItem("next");
    var storage = tabStorage();
    if (!back || !storage) {
      return;
    }
    try {
      storage.setItem(BOARD_PLACE, JSON.stringify({ at: back.value, left: box.scrollLeft }));
    } catch (full) {
      // No room, or no leave: the board comes back at its start, as it used to.
    }
  });

  // True when the board was put back where a move left it.
  function backWhereItWas() {
    var box = document.querySelector("[data-board]");
    var storage = tabStorage();
    if (!box || !storage) {
      return false;
    }
    var kept = null;
    try {
      kept = JSON.parse(storage.getItem(BOARD_PLACE) || "null");
      storage.removeItem(BOARD_PLACE);
    } catch (unreadable) {
      return false;
    }
    if (!kept || kept.at !== window.location.pathname + window.location.search || !kept.left) {
      return false;
    }
    box.scrollLeft = kept.left;
    return true;
  }

  /* --------------------------------------- a column's heading folds the board (#315)
   *
   * The heading of a column is a link to the board folded to it, and htmx makes the link
   * fold the board in place (`board.html`). Five things are left for this file.
   *
   * **It is called a button while it folds in place.** A link goes somewhere; this changes
   * what is on the page and says whether its column is open. So where htmx is running it is
   * given the role, and Space presses it as Enter does -- once, however long it is held: a
   * key held down repeats, and each repeat was a press, folding and unfolding by turns.
   * Without a script it is the link the server drew, and goes to the address that draws
   * the same thing.
   *
   * **A click with a modifier is the browser's.** Ctrl, Meta or Shift and a click on a
   * link asks for it in a new tab or window, and it is still a link: htmx would have
   * folded the board where it was and opened nothing. The click is kept from htmx, and
   * the browser does what it does with a link. (A middle click never reaches htmx.)
   *
   * **The focus is on it when it is pressed.** htmx puts the focus back, after the swap, on
   * the element with the id the focused one had. Not every browser focuses a link that is
   * clicked, and one that does not would leave the focus on the page's body.
   *
   * **The fold that was pressed is the status the filter form sends.** The status in force
   * is a hidden field of that form, drawn with the board, so until the answer is drawn it
   * is the old one: a filter chosen while a fold was on its way asked for the old status
   * and, being the newer request, took the fold back. As the fold is sent the field is
   * given the status it asks for -- made, if the board had none, inside what the swap
   * replaces -- or taken away, for a fold that opens every column. It is written when
   * htmx is about to send and not at the click, so a fold that is refused, while a card
   * is held, leaves the form's question alone.
   *
   * **The open columns are brought to the middle of the scroll box** (#709). Folded, the
   * open columns sit among the strips in the order of the statuses, and on a narrow screen
   * the last of them is off the edge. The box is scrolled sideways until they are in the
   * middle of it -- and where they do not fit, the column whose heading was pressed, or
   * the first open one; a column wider than the box shows where it starts. At once when
   * the page arrives folded, and after a swap that brought a board. Not after a swap of
   * anything else on the page -- the theme switch is one -- which would take the board out
   * of the hands of somebody who had scrolled it; and not on the page a move loads, where
   * the board is put back where it was. The columns are centred by the stylesheet while
   * they fit (`board.html`), so this moves only a board wider than its box.
   */
  function foldControl(node) {
    return node && node.closest ? node.closest("[data-board-fold]") : null;
  }

  document.addEventListener(
    "click",
    function (event) {
      if (foldControl(event.target) && (event.ctrlKey || event.metaKey || event.shiftKey)) {
        // Before it reaches the control, where htmx listens. Not cancelled: the browser
        // is to follow the link, its own way.
        event.stopPropagation();
      }
    },
    true
  );

  function statusInForce(filters) {
    return document.querySelector('input[type="hidden"][name="status"][form="' + filters.id + '"]');
  }

  // The filter form's status: `status`, in a field inside `within`, or no field for none.
  function setStatusInForce(filters, within, status) {
    var field = statusInForce(filters);
    if (!status) {
      if (field) {
        field.remove();
      }
      return;
    }
    if (!field) {
      field = document.createElement("input");
      field.type = "hidden";
      field.name = "status";
      field.setAttribute("form", filters.id);
      within.insertBefore(field, within.firstChild);
    }
    field.value = status;
  }

  // While a pressed fold has not been drawn: the status the board on the screen was drawn
  // with. Put back if a request for the board fails, the fold's or a filter's after it,
  // since the board is then still that one, and the form's status is to be the drawn one
  // whenever nothing is on its way. A request replaced by a newer one has not failed: the
  // newer one took the pressed status with it. Forgotten when a board is drawn.
  var drawnStatus = null;

  document.addEventListener("htmx:configRequest", function (event) {
    var detail = event.detail || {};
    var filters = boardFilters();
    if (!foldControl(detail.elt) || !filters || !detail.target || !detail.formData) {
      return;
    }
    if (!drawnStatus) {
      var field = statusInForce(filters);
      drawnStatus = { was: field ? field.value : "" };
    }
    setStatusInForce(filters, detail.target, detail.formData.get("status") || "");
  });

  ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach(function (failure) {
    document.addEventListener(failure, function (event) {
      var within = (event.detail || {}).target;
      var filters = boardFilters();
      if (drawnStatus && filters && hasABoard(within)) {
        setStatusInForce(filters, within, drawnStatus.was);
        drawnStatus = null;
      }
    });
  });

  // Where the box is to be scrolled to have the open columns in its middle, or null where
  // nothing is to move: an open board, with no column pressed. `pressed` is the section of
  // the column whose heading asked for this board. In the box's own reckoning, which is
  // negative in a right-to-left page; the edges are the box's as drawn, so the sum is the
  // same either way.
  function boardMiddle(box, pressed) {
    var open = box.querySelectorAll("[data-board-section]:not([data-board-strip])");
    var folded = Boolean(box.querySelector("[data-board-strip]"));
    var around = box.getBoundingClientRect();
    var start = around.left + box.clientLeft;
    var wide = box.clientWidth;
    var span = null;
    if (folded && open.length) {
      var first = open[0].getBoundingClientRect();
      var last = open[open.length - 1].getBoundingClientRect();
      span = { left: Math.min(first.left, last.left), right: Math.max(first.right, last.right) };
    }
    if (!span || span.right - span.left > wide) {
      var column = pressed || (folded ? open[0] : null);
      if (!column) {
        return null;
      }
      span = column.getBoundingClientRect();
    }
    var by = (span.left + span.right) / 2 - (start + wide / 2);
    if (span.right - span.left > wide) {
      // Wider than the box: it shows where it starts.
      var forwards = window.getComputedStyle(box).direction !== "rtl";
      by = forwards ? span.left - start : span.right - (start + wide);
    }
    var most = box.scrollWidth - box.clientWidth;
    var rtl = window.getComputedStyle(box).direction === "rtl";
    return Math.min(Math.max(box.scrollLeft + by, rtl ? -most : 0), rtl ? 0 : most);
  }

  function centreTheBoard(pressed) {
    var box = document.querySelector("[data-board]");
    var goal = box ? boardMiddle(box, pressed) : null;
    if (goal !== null) {
      box.scrollLeft = goal;
    }
  }

  function readyBoardFolds(event) {
    if (window.htmx) {
      Array.prototype.forEach.call(
        document.querySelectorAll("[data-board-fold]"),
        function (control) {
          control.setAttribute("role", "button");
        }
      );
    }
    if (!event || event.type !== "htmx:afterSwap") {
      // The page arriving: where a move left the board, or with its open columns in sight.
      if (!backWhereItWas()) {
        centreTheBoard(null);
      }
      return;
    }
    // A swap: only one that brought a board with it. htmx says so on what it put in. Where
    // its columns go is the next block's, once the swap has settled.
    if (hasABoard(event.target)) {
      drawnStatus = null;
    }
  }

  onContentReady(readyBoardFolds);

  /* --------------------------------------------------- a column opens and folds in motion (#709)
   *
   * A fold redraws the board: the server sends the columns at their new widths, and htmx
   * puts them in place of the old ones. Drawn as it comes, the board jumped -- every column
   * after the one pressed leapt sideways by the width of a column, and the box, being a new
   * one, was back at its start. So the board that is replaced is measured as the answer
   * lands, and the new one is moved there from it:
   *
   * - **the box stays where it was scrolled**, and then goes to where the open columns are
   *   in its middle (`boardMiddle`), on the same beat as the columns;
   * - **each column that changes width goes from its old width to its new one**, its
   *   content laid out at the new width and uncovered by its edge as it opens, or covered
   *   as it folds; what it held fades out over what it holds now, which fades in. What
   *   fades out is a copy, inert and hidden from assistive technology, taken from the board
   *   that was replaced and gone when the column arrives;
   * - **the gap between the columns** goes from the open board's to the folded one's, and
   *   **the box's height** from the old board's to the new one's, so that nothing below the
   *   board leaps when a long column folds.
   *
   * Any swap of the board does this, a filter's as much as a fold's: a filter changes no
   * width, so only the box moves, and only where the open columns are not in its middle.
   *
   * **The new widths are measured once the swap has settled.** htmx gives an element with
   * the id an old one had that element's classes for twenty milliseconds, to let a style
   * change between the two be animated; a heading is such an element, and a strip measured
   * with an open column's heading in it was a dozen pixels too narrow. Until then the
   * columns are held at their old widths, so the board does not move twice.
   *
   * **Nobody who asked for less motion sees anything move.** The board is drawn at its new
   * widths and the box is put where it goes, at once. Nor does anything move while a card
   * is held, since nothing of the board is swapped then (above).
   */
  var FOLD_TIME = 240; // milliseconds
  var FOLD_EASING = "cubic-bezier(0.2, 0, 0, 1)";

  // The board on the screen as an answer for it lands: each column's width and a copy of
  // what it holds, the gap between columns, the box's height and how far it is scrolled,
  // and the id of the heading the answer puts the focus on, when a fold asked for it. Then,
  // once the new board is in, the same for the settle to finish with.
  var boardWas = null;
  var boardLanding = null;
  var boardScrolling = 0;

  function lessMotionAsked() {
    return Boolean(lessMotion && lessMotion.matches);
  }

  function sectionsOf(box) {
    return Array.prototype.slice.call(box.querySelectorAll("[data-board-section]"));
  }

  // What a column held, to fade out over what it holds now: nothing in it can be reached,
  // read out, found by an id or taken for a part of the board.
  function copyOfColumn(section) {
    var copy = document.createElement("div");
    Array.prototype.forEach.call(section.childNodes, function (node) {
      // Not the copy of an older board still fading out of it, on a second press.
      if (!(node.nodeType === 1 && node.hasAttribute("data-fold-copy"))) {
        copy.appendChild(node.cloneNode(true));
      }
    });
    [copy].concat(Array.prototype.slice.call(copy.querySelectorAll("*"))).forEach(function (node) {
      Array.prototype.slice.call(node.attributes).forEach(function (attribute) {
        if (/^(id|name|form|for|hx-.*|data-(board|card|column|empty|count).*)$/.test(attribute.name)) {
          node.removeAttribute(attribute.name);
        }
      });
    });
    copy.inert = true;
    copy.setAttribute("aria-hidden", "true");
    copy.setAttribute("data-fold-copy", "");
    return copy;
  }

  document.addEventListener("htmx:beforeSwap", function (event) {
    var detail = event.detail || {};
    var box = document.querySelector("[data-board]");
    boardWas = null;
    if (!detail.shouldSwap || !box || !detail.target || !detail.target.contains(box)) {
      return;
    }
    // `elt` is the target here; the element that asked is the request's.
    var asking = foldControl((detail.requestConfig || {}).elt);
    var pressed = null;
    if (asking) {
      pressed = asking.hasAttribute("data-focus-after")
        ? asking.getAttribute("data-focus-after").replace(/^#/, "")
        : asking.id;
    }
    var widths = {};
    var copies = {};
    sectionsOf(box).forEach(function (section) {
      var status = section.getAttribute("data-board-section");
      widths[status] = section.getBoundingClientRect().width;
      copies[status] = copyOfColumn(section);
    });
    boardWas = {
      widths: widths,
      copies: copies,
      gap: parseFloat(window.getComputedStyle(box).columnGap) || 0,
      height: box.getBoundingClientRect().height,
      left: box.scrollLeft,
      pressed: pressed,
    };
  });

  document.addEventListener("htmx:afterSwap", function (event) {
    var was = boardWas;
    var box = hasABoard(event.target) ? document.querySelector("[data-board]") : null;
    if (!box) {
      return;
    }
    boardWas = null;
    window.cancelAnimationFrame(boardScrolling);
    boardLanding = { box: box, was: was };
    if (!was) {
      return;
    }
    // Held where the old board was until the swap settles.
    if (!lessMotionAsked()) {
      sectionsOf(box).forEach(function (section) {
        var width = was.widths[section.getAttribute("data-board-section")];
        if (width !== undefined) {
          section.style.width = width + "px";
        }
      });
      box.style.columnGap = was.gap + "px";
      box.style.height = was.height + "px";
    }
    box.scrollLeft = was.left;
  });

  // An answer that was not swapped in leaves the board as it was, and what was measured
  // for it must not be taken for some later swap's.
  document.addEventListener("htmx:afterRequest", function () {
    boardWas = null;
  });

  // Every width is read before anything moves: an animation that has begun is the width
  // it begins from, to whatever reads it next.
  //
  // What a column holds is laid out at the width it arrives at through a property set on
  // the column, which its content takes (`w-(--board-arrives)`, `board.html`), and never
  // through a style on the content itself: htmx carries the style of an element with an id
  // -- the list of cards has one -- into the next board, and the page's content security
  // policy refuses a style written that way.
  function moveTheColumns(box, was) {
    var sections = sectionsOf(box).map(function (section) {
      return {
        section: section,
        status: section.getAttribute("data-board-section"),
        to: section.getBoundingClientRect().width,
        inside: section.clientWidth,
        parts: Array.prototype.slice.call(section.children),
      };
    });
    var gap = parseFloat(window.getComputedStyle(box).columnGap) || 0;
    var high = box.getBoundingClientRect().height;
    var moves = [];
    var tidy = [];
    var timing = { duration: FOLD_TIME, easing: FOLD_EASING };
    sections.forEach(function (column) {
      var section = column.section;
      var parts = column.parts;
      var from = was.widths[column.status];
      var to = column.to;
      if (from === undefined || Math.abs(from - to) < 1) {
        return;
      }
      var copy = was.copies[column.status];
      // Laid out at the width it arrives at, and uncovered or covered by its edge.
      section.style.overflow = "clip";
      section.style.position = "relative";
      section.style.setProperty("--board-arrives", column.inside + "px");
      parts.forEach(function (part) {
        moves.push(part.animate([{ opacity: 0 }, { opacity: 1 }], timing));
      });
      if (copy) {
        // At the width it was, whatever the column it is in arrives at.
        copy.style.setProperty("--board-arrives", "auto");
        copy.style.position = "absolute";
        copy.style.insetBlockStart = "0";
        copy.style.insetInlineStart = "0";
        copy.style.width = from + "px";
        copy.style.pointerEvents = "none";
        section.appendChild(copy);
        moves.push(copy.animate([{ opacity: 1 }, { opacity: 0 }], timing));
      }
      moves.push(section.animate([{ width: from + "px" }, { width: to + "px" }], timing));
      tidy.push(function () {
        section.style.overflow = "";
        section.style.position = "";
        section.style.removeProperty("--board-arrives");
        if (copy) {
          copy.remove();
        }
      });
    });
    if (Math.abs(was.gap - gap) >= 1) {
      moves.push(box.animate([{ columnGap: was.gap + "px" }, { columnGap: gap + "px" }], timing));
    }
    if (Math.abs(was.height - high) >= 1) {
      // Shorter than what it holds on the way, and no scroll bar for it.
      box.style.overflowY = "hidden";
      moves.push(box.animate([{ height: was.height + "px" }, { height: high + "px" }], timing));
      tidy.push(function () {
        box.style.overflowY = "";
      });
    }
    var done = function () {
      tidy.forEach(function (step) {
        step();
      });
    };
    Promise.all(
      moves.map(function (move) {
        return move.finished;
      })
    ).then(done, done);
    return moves[0] || null;
  }

  // The box from where it was to `goal`, on the columns' beat: eased as they are, and timed
  // by their motion, `lead`, where they move -- a frame they are late is a frame the box
  // waits, or the room it is scrolled into would not be there yet, and it would stop short.
  // To the very place at the end.
  function scrollTheBoard(box, goal, lead) {
    var from = box.scrollLeft;
    var began = window.performance.now();
    var step = function (now) {
      if (!box.isConnected || (lead && lead.playState === "idle")) {
        boardScrolling = 0;
        return;
      }
      var time = lead ? lead.currentTime || 0 : now - began;
      var done = lead && lead.playState === "finished" ? 1 : Math.min(time / FOLD_TIME, 1);
      box.scrollLeft = from + (goal - from) * (1 - Math.pow(1 - done, 3));
      boardScrolling = done < 1 ? window.requestAnimationFrame(step) : 0;
    };
    boardScrolling = window.requestAnimationFrame(step);
  }

  document.addEventListener("htmx:afterSettle", function () {
    var landing = boardLanding;
    boardLanding = null;
    if (!landing || !landing.box.isConnected) {
      return;
    }
    var box = landing.box;
    var was = landing.was;
    var moving = Boolean(was) && !lessMotionAsked();
    // Where everything goes is measured on the new board as it will be drawn, before
    // anything is moved.
    sectionsOf(box).forEach(function (section) {
      section.style.width = "";
    });
    box.style.columnGap = "";
    box.style.height = "";
    var heading = was && was.pressed ? document.getElementById(was.pressed) : null;
    var goal = boardMiddle(box, heading ? columnOf(heading) : null);
    var lead = null;
    if (moving) {
      lead = moveTheColumns(box, was);
      // Measured at the new widths, a narrower board had the box pulled back into it; at
      // the old ones, where the columns start from, there is room again.
      box.scrollLeft = was.left;
    }
    if (goal === null || Math.abs(goal - box.scrollLeft) < 1) {
      return;
    }
    if (moving) {
      scrollTheBoard(box, goal, lead);
    } else {
      box.scrollLeft = goal;
    }
  });

  document.addEventListener("click", function (event) {
    var control = foldControl(event.target);
    if (control && document.activeElement !== control) {
      control.focus({ preventScroll: true });
    }
  });

  document.addEventListener("keydown", function (event) {
    var control = foldControl(event.target);
    if (
      control &&
      control.getAttribute("role") === "button" &&
      (event.key === " " || event.key === "Spacebar") &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey
    ) {
      // Space scrolls the page from a link; from a button it presses it. A repeat of a key
      // held down is kept from scrolling too, and is not another press.
      event.preventDefault();
      if (!event.repeat) {
        control.click();
      }
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

  // Asking can cancel a submit that was already marked as sent (#518): the person answers
  // *Stay*, and no `pageshow` comes to lift the mark, so the button they pressed would stay
  // dead. Their next pointer or key press is proof that they stayed -- if they left, no
  // further input reaches this page -- and it lifts the marks again, before the click it
  // belongs to is handled.
  var askedToStay = false;

  function releaseAfterStaying() {
    if (!askedToStay) {
      return;
    }
    askedToStay = false;
    Array.prototype.forEach.call(document.querySelectorAll("form[data-submitted]"), releaseForm);
  }

  document.addEventListener("pointerdown", releaseAfterStaying, true);
  document.addEventListener("keydown", releaseAfterStaying, true);

  window.addEventListener("beforeunload", function (event) {
    if (!anythingDirty()) {
      return;
    }
    askedToStay = true;
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
          // A select's own control (#301): a letter typed on it looks down its list.
          active.hasAttribute("data-select-trigger") ||
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
   * The masthead's search over everything, below `lg` (#350). The icon is a link to the
   * search page, and with scripts off that is all it is. Here it becomes the button of a
   * disclosure: tapping it opens the box in the masthead, in the place of the wordmark
   * (`data-search-open` on the masthead, and the stylesheet draws it), with the focus in
   * the box and the page where it was. Escape and the close button shut it and give the
   * focus back to the icon, which says `aria-expanded` meanwhile -- set from here, with
   * `role="button"`, because a link may not carry it. The nav menu's *Search* opens the
   * same thing (`data-nav-search`), and so does "/". Past `lg` the box is drawn in the row
   * and none of this applies, and it is shut as the window grows. Where the box narrows
   * the page's table the icon is a popover's button, not a link, and is left to the
   * browser (#313).
   */
  var searchHeader = document.querySelector("[data-site-header]");
  var searchIcon = searchHeader && searchHeader.querySelector("a[data-search-link]");
  var searchBox = searchIcon && searchHeader.querySelector("[data-site-search-form] input");
  var searchWide = window.matchMedia ? window.matchMedia("(min-width: 64rem)") : null;

  function searchIsOpen() {
    return !!searchHeader && searchHeader.hasAttribute("data-search-open");
  }

  function openSearch() {
    searchHeader.setAttribute("data-search-open", "");
    searchIcon.setAttribute("aria-expanded", "true");
    searchBox.focus();
    searchBox.select();
  }

  function closeSearch(giveFocusBack) {
    searchHeader.removeAttribute("data-search-open");
    searchIcon.setAttribute("aria-expanded", "false");
    if (giveFocusBack) {
      searchIcon.focus();
    }
  }

  if (searchIcon && searchBox) {
    searchIcon.setAttribute("role", "button");
    searchIcon.setAttribute("aria-expanded", "false");
    searchIcon.setAttribute("aria-controls", searchBox.id);
    searchIcon.addEventListener("click", function (event) {
      if (searchWide && searchWide.matches) {
        return;
      }
      event.preventDefault();
      openSearch();
    });
    // A link with a button's role answers Space as a button does.
    searchIcon.addEventListener("keydown", function (event) {
      if (event.key === " ") {
        event.preventDefault();
        searchIcon.click();
      }
    });
    searchHeader.querySelector("[data-search-close]").addEventListener("click", function () {
      closeSearch(true);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && searchIsOpen() && !event.defaultPrevented) {
        // Not the search box's own first Escape, which would only empty it.
        event.preventDefault();
        closeSearch(true);
      }
    });
    if (searchWide && searchWide.addEventListener) {
      searchWide.addEventListener("change", function (query) {
        if (query.matches && searchIsOpen()) {
          closeSearch(false);
        }
      });
    }
  }

  // The navigation menu's Search is the same search, not the page: the field in the masthead,
  // or the table's panel where the page has one.
  document.addEventListener("click", function (event) {
    var entry = event.target.closest && event.target.closest("[data-nav-search]");
    var opener = searchHeader && searchHeader.querySelector("[data-search-link]");
    if (!entry || !opener || event.defaultPrevented) {
      return;
    }
    event.preventDefault();
    var menu = entry.closest("[popover]");
    if (menu && menu.hidePopover && menu.matches(":popover-open")) {
      menu.hidePopover();
    }
    opener.click();
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
    // A select's list is never narrower than the control it opens from (#301).
    if (panel.hasAttribute("data-select-panel")) {
      style.minWidth = Math.min(box.width, width - 2 * PANEL_EDGE) + "px";
    }

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
    // A menu's inline end sits on its trigger's; a panel marked `data-align="start"` -- a
    // select's list -- starts where its trigger starts. Either is the left edge in one
    // direction of writing and the right edge in the other.
    var fromLeft = (panel.getAttribute("data-align") === "start") !== rtl;
    if (!wide) {
      // Not drawn yet: one edge on the trigger's, which needs no width.
      style.left = fromLeft ? box.left + "px" : "auto";
      style.right = fromLeft ? "auto" : width - box.right + "px";
      return;
    }
    var left = fromLeft ? box.left : box.right - wide;
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

  /* ------------------------------------------------ a link that opens a dialog instead
   *
   * A dialog that holds something to read or to do, rather than a question, has a page
   * behind it: the same thing at an address of its own, which is what works with no script
   * at all. So what opens it is a link to that page, `<a href="…" data-opens-dialog="id">`,
   * and here the link opens the dialog it names in place of being followed -- modal, like
   * every other, with focus given back to the link when it closes. A click that asks for a
   * new tab or a new window is left to do that, and so is a link whose dialog is not on
   * the page. A card's question mark is one (#302).
   *
   * `aria-haspopup` is added here and not written in the template: without this script the
   * link is a link, and saying it opens a dialog would be untrue.
   *
   * **Taken over, it is a button** (#302): it opens something on this page, and that is
   * what a button does. So it is said to be one, and it answers to Space as a button does
   * -- on the key's release, the press not scrolling the page, a key held down pressing it
   * once -- where a link would have scrolled the page and opened nothing. And what the
   * server wrote for the link it was is taken off where it is no longer true: a link that
   * leaves a form opens its page in a new tab, so that nothing typed is lost, and says so
   * in words marked `data-new-tab`; opening the dialog here leaves nothing, so the `target`
   * and those words go.
   */
  function dialogOfLink(link) {
    var dialog = document.getElementById(link.getAttribute("data-opens-dialog") || "");
    return dialog && dialog.tagName === "DIALOG" && dialog.hasAttribute("data-dialog") ? dialog : null;
  }

  onContentReady(function () {
    Array.prototype.forEach.call(document.querySelectorAll("a[data-opens-dialog]"), function (link) {
      var dialog = dialogOfLink(link);
      if (dialog && typeof dialog.showModal === "function") {
        link.setAttribute("aria-haspopup", "dialog");
        link.setAttribute("role", "button");
        link.removeAttribute("target");
        Array.prototype.forEach.call(link.querySelectorAll("[data-new-tab]"), function (words) {
          words.remove();
        });
      }
    });
  });

  document.addEventListener("click", function (event) {
    var link = event.target.closest ? event.target.closest("a[data-opens-dialog]") : null;
    if (!link || event.defaultPrevented || event.button !== 0) {
      return;
    }
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    var dialog = dialogOfLink(link);
    if (!dialog || typeof dialog.showModal !== "function") {
      return;
    }
    event.preventDefault();
    if (dialog.open) {
      return;
    }
    dialogOpener[dialog.id] = link;
    dialog.showModal();
    // A box inside it that names a line was drawn while the dialog was shut, where a box
    // has no size and cannot be scrolled: it is put on its line now that it has one.
    placeCarets(dialog);
  });

  var spacePressed = null;

  document.addEventListener("keydown", function (event) {
    if (event.key !== " " || event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    var link = event.target.closest ? event.target.closest('a[data-opens-dialog][role="button"]') : null;
    if (!link) {
      return;
    }
    event.preventDefault();
    if (!event.repeat) {
      spacePressed = link;
    }
  });

  document.addEventListener("keyup", function (event) {
    if (event.key !== " ") {
      return;
    }
    var link = spacePressed;
    spacePressed = null;
    if (link && link === event.target) {
      event.preventDefault();
      link.click();
    }
  });

  /* ------------------------------------------- the caret, on the line that is wrong (#311)
   *
   * A box of code that was refused says which line each problem is on, and a `<textarea>`
   * has no line numbers to count by. So the server names the first problem's line on the
   * box, `data-caret-line` and where it knows it `data-caret-column`, and this puts the
   * caret there and brings that line into view **inside the box** -- a box of code has a
   * height of its own and scrolls within it (`textarea[data-code]`), which is what makes
   * there be anything to scroll. Once for each time the box is drawn: it comes back with
   * the page, or in a swap, and where somebody has moved the caret since is theirs.
   *
   * Then what was said is brought into view. The sentences stand above the box, in the
   * region the box is described by; after a refusal they are what has to be read first,
   * and a page that came back scrolled to the box, or a dialog scrolled to its foot, would
   * have them just out of sight. Only where the box asks for the focus, which is where
   * something was refused: a page that is merely opened is not scrolled anywhere.
   *
   * A box that is not drawn -- inside a dialog nobody has opened -- is left for the moment
   * its dialog opens. Without this script the box still has the focus after a refusal, the
   * sentences are still above it, and each still says its line.
   */
  function placeCaret(box) {
    if (box.hasAttribute("data-caret-placed") || !box.getClientRects().length) {
      return;
    }
    box.setAttribute("data-caret-placed", "");
    var line = parseInt(box.getAttribute("data-caret-line"), 10) || 1;
    var column = parseInt(box.getAttribute("data-caret-column"), 10) || 1;
    var lines = box.value.split("\n");
    var index = 0;
    for (var before = 0; before < line - 1 && before < lines.length; before += 1) {
      index += lines[before].length + 1;
    }
    index += Math.min(column - 1, (lines[line - 1] || "").length);
    index = Math.min(index, box.value.length);
    var refused = box.hasAttribute("autofocus");
    if (refused) {
      // Not scrolled to by the browser: where the page stands is decided below.
      box.focus({ preventScroll: true });
    }
    box.setSelectionRange(index, index);
    var style = window.getComputedStyle(box);
    var height = parseFloat(style.lineHeight) || 16;
    // A line or two above it left in view, so that what led to the mistake can be read.
    box.scrollTop = Math.max(0, (line - 3) * height);
    // And across, for a line longer than the box is wide: one width of letter, so a
    // column is a distance. Half a box of what comes before it is left in view.
    var across = (column - 1) * (parseFloat(style.fontSize) || 12) * 0.6;
    box.scrollLeft = across > box.clientWidth * 0.75 ? across - box.clientWidth / 2 : 0;
    if (!refused) {
      return;
    }
    var said = null;
    (box.getAttribute("aria-describedby") || "").split(/\s+/).forEach(function (id) {
      var described = id ? document.getElementById(id) : null;
      if (described && described.getAttribute("role") === "alert") {
        said = described;
      }
    });
    // The box first and then the sentences, each only as far as it takes: where both fit
    // both are seen, and where they do not the sentences win, which is the order to read.
    box.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (said) {
      said.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }

  function placeCarets(within) {
    Array.prototype.forEach.call(within.querySelectorAll("textarea[data-caret-line]"), placeCaret);
  }

  onContentReady(function () {
    placeCarets(document);
  });

  /* ---------------------------------------------------------------------- selects
   *
   * Every `<select>` is Basecoat's select (#301): a button showing the choice and a list
   * that opens from it, whose options are elements and so can hold a flag or an icon,
   * which an `<option>` cannot. Basecoat's stylesheet gives the look and the vocabulary --
   * `.select`, a `<button>`, a `[data-popover]` holding a `role="listbox"` -- and its
   * script is not used, by the test that kept its menu script out (#262): its markup does
   * nothing at all until the script has run, and a page here works with scripts off.
   *
   * **The native select stays, and stays the form's control.** The server draws it, it
   * posts the name and the value, and with this script blocked it is the whole control.
   * Here it is put out of sight and out of the tab order, and the button and the list are
   * built beside it and drive it: a choice sets the select's value and raises its `input`
   * and `change`, so whatever listened to a select -- htmx, the board's saving, the rows
   * that hide a name unless the kind is Other, which the stylesheet reads off the select
   * itself -- is listening still. The other way round, anything that sets the select
   * tells this with `selectChanged`, and a `change` raised by anybody is followed.
   *
   * **It is the ARIA combobox with a listbox.** The button is the combobox and says its
   * name, its choice and whether the list is open; the list's options say which is chosen
   * and where each stands. Closed: the arrows, Enter, Space, Home, End and a letter open
   * it. Open: the arrows, Home, End and Page Up and Down move; a letter looks down the
   * list; Enter and Space choose; Escape closes it and chooses nothing; Tab chooses and
   * moves on, which is what the pattern asks and what leaving an open native list by Tab
   * does: the option the keys were on is kept. Nothing is chosen by passing over it, so a
   * list that saves as it changes saves once (#227).
   *
   * **A long list has a box to narrow it**: sixteen options or more. The box takes the
   * focus while the list is open and is itself a combobox over the same list; what is
   * typed is matched anywhere in an option, whatever its case or its accents, and
   * `00351` finds `+351`. Under a finger the focus stays on the button, so that opening
   * a list does not call up the keyboard over it, and the box is a tap away.
   *
   * **The closed control is built for every select as the page arrives, and its list
   * when it is opened**, every time, from the select as it then stands: a board of two
   * hundred cards is two hundred buttons and no lists, and an option switched off since
   * the last time (#307) is switched off in the list.
   *
   * **The list is a popover**, opened by the button's own `popovertarget`, so the browser
   * draws it in the top layer -- above a dialog, outside every box that scrolls -- closes
   * it on a click elsewhere, and ties it to its button for the stylesheet to place, as it
   * does a menu's panel (#310).
   *
   * Everything is delegated from the document: there is no listener on any select, button
   * or list, so what a swap takes away leaves nothing behind, and what it brings in is
   * made ready by `onContentReady` like everything else.
   */
  var SELECT_FILTER_FROM = 16; // options: a list this long gets a box to narrow it
  var SELECT_PAGE = 10; // options: how far Page Up and Page Down move
  var SELECT_TYPED_FOR = 1000; // milliseconds a typed letter goes on being part of a word
  var SELECT_PRESS_FOR = 1500; // milliseconds a press outside a list owns the click it ends in

  var selectStates = new WeakMap(); // select -> what was built for it
  var selectOfPart = new WeakMap(); // its button, its panel -> the select
  var selectCount = 0;
  var openSelectState = null;
  var selectFocusBeforeSwap = "";

  function selectPartsTemplate() {
    return document.getElementById("select-parts");
  }

  // A list of one choice, drawn closed. A `<select multiple>` or one with a `size` is a
  // list box on the page and not this control, and a page may keep one select as the
  // browser draws it by saying `data-native` (`tests/test_template_lint.py` holds the
  // list of those, each with its reason).
  function selectIsEnhanced(select) {
    return !select.multiple && select.size <= 1 && !select.hasAttribute("data-native");
  }

  /* An option's icon is a copy of one the server drew: `{% icon %}` writes each icon a
   * list may show into a `<template data-option-icons>`, and an option names the one it
   * wants in `data-icon`. Nothing is built from a string and nothing is fetched. The
   * chevron and the tick come the same way, out of the page's `select-parts`. */
  var optionIcons = {};

  function optionIcon(name) {
    if (!name) {
      return null;
    }
    if (!optionIcons[name]) {
      var wanted = '[data-icon="' + CSS.escape(name) + '"]';
      Array.prototype.some.call(
        document.querySelectorAll("template[data-option-icons], template#select-parts"),
        function (template) {
          var drawn = template.content.querySelector(wanted);
          if (drawn) {
            optionIcons[name] = drawn.cloneNode(true);
          }
          return Boolean(drawn);
        }
      );
    }
    return optionIcons[name] ? optionIcons[name].cloneNode(true) : null;
  }

  // The image the `{% flag %}` tag draws, from the address an option carries: static
  // files are served under a content hash, so there is no pattern to build one from.
  function optionFlag(url) {
    var image = document.createElement("img");
    image.className = "flag";
    image.width = 20;
    image.height = 15;
    image.alt = "";
    image.setAttribute("aria-hidden", "true");
    image.loading = "lazy";
    image.decoding = "async";
    image.src = url;
    return image;
  }

  // What an option shows beside its words: its flag, its icon, or the room for one, so
  // that the words of a list line up and choosing moves nothing.
  function drawOptionMark(holder, option) {
    var flag = option ? option.getAttribute("data-flag") : "";
    var icon = option ? option.getAttribute("data-icon") : "";
    var key = flag ? "flag " + flag : icon ? "icon " + icon : "";
    if (holder.getAttribute("data-select-mark") === key) {
      return;
    }
    holder.setAttribute("data-select-mark", key);
    holder.textContent = "";
    var drawn = flag ? optionFlag(flag) : optionIcon(icon);
    if (drawn) {
      holder.appendChild(drawn);
    }
  }

  function optionWords(option) {
    return option.label || option.text || "";
  }

  // The language and the direction an option says it is written in (#309): *Mme* in a
  // menu drawn in English is French, and is said and laid out as French.
  function copyLanguage(from, to) {
    ["lang", "dir"].forEach(function (name) {
      var value = from ? from.getAttribute(name) : null;
      if (value) {
        to.setAttribute(name, value);
      } else {
        to.removeAttribute(name);
      }
    });
  }

  function drawChosen(state) {
    var select = state.select;
    var option = select.options[select.selectedIndex] || null;
    var words = option ? optionWords(option) : "";
    if (state.label.textContent !== words) {
      state.label.textContent = words;
    }
    copyLanguage(option, state.label);
    if (state.mark) {
      drawOptionMark(state.mark, option);
    }
    state.trigger.disabled = select.matches(":disabled");
  }

  // Called by whatever sets a select from a script, which raises no event of its own.
  function selectChanged(select) {
    var state = select && selectStates.get(select);
    if (state) {
      drawChosen(state);
    }
  }

  /* A native select is as wide as its longest option whatever it shows, and pages are
   * laid out on that: a menu with no width written on it, a list in a table's header that
   * holds its column open (#314). The button is told its longest option too, in an
   * attribute the stylesheet draws out of sight under the chosen one, so it is that wide
   * and choosing never changes its width -- and the button's own text is the choice and
   * nothing else. Which option is the longest is measured on a canvas, which lays nothing
   * out. */
  var measuringContext = null;
  var measuredWords = {};

  function longestWords(select) {
    if (!measuringContext) {
      measuringContext = document.createElement("canvas").getContext("2d");
      if (measuringContext) {
        measuringContext.font = "16px " + window.getComputedStyle(document.body).fontFamily;
      }
    }
    var longest = "";
    var width = -1;
    Array.prototype.forEach.call(select.options, function (option) {
      var words = optionWords(option);
      var measured = measuredWords[words];
      if (measured === undefined) {
        measured = measuringContext ? measuringContext.measureText(words).width : words.length;
        measuredWords[words] = measured;
      }
      if (measured > width) {
        width = measured;
        longest = words;
      }
    });
    return longest;
  }

  /* The labels that name a select, for every select on the page, from one pass over it.
   * `select.labels` is the same answer and walks the whole document for each select it
   * is asked about: on *Your details* with forty rows that was over half of the time
   * it took to build every button. */
  function labelsByControl() {
    var found = {};
    Array.prototype.forEach.call(document.querySelectorAll("label[for]"), function (label) {
      var id = label.getAttribute("for");
      (found[id] = found[id] || []).push(label);
    });
    return found;
  }

  function buildSelect(select, parts, labels) {
    var base = select.id;
    if (!base || document.getElementById(base + "-trigger")) {
      selectCount += 1;
      base = "select-" + selectCount;
    }

    var root = document.createElement("div");
    root.className = "select";
    root.setAttribute("data-select", "");

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.id = base + "-trigger";
    // The widths, the padding and the size of type a page wrote on its select -- and not
    // what htmx is saying about it at this moment. A select a swap brings in carries the
    // classes of the one it replaced until the swap settles, `htmx-request` among them,
    // and a button that copied that would say a request was in flight for ever.
    trigger.className = select.className
      .split(/\s+/)
      .filter(function (name) {
        return name && name.indexOf("htmx-") !== 0;
      })
      .join(" ");
    trigger.setAttribute("data-select-trigger", "");
    trigger.setAttribute("role", "combobox");
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", base + "-listbox");
    trigger.setAttribute("popovertarget", base + "-popover");

    var panel = document.createElement("div");
    panel.id = base + "-popover";
    panel.setAttribute("popover", "");
    panel.setAttribute("data-popover", "");
    panel.setAttribute("data-align", "start");
    panel.setAttribute("data-select-panel", "");

    var listbox = document.createElement("div");
    listbox.id = base + "-listbox";
    listbox.setAttribute("role", "listbox");
    listbox.setAttribute("data-empty", parts.getAttribute("data-empty") || "");

    // The name is the select's own: its `aria-labelledby`, its `aria-label`, or the
    // labels that point at it, which go on pointing at it -- a click on one gives the
    // select the focus, and the focus is handed on to the button (below).
    //
    // A label written round its select is not pointed at: the button and its list are
    // built inside that label, and a control named by an element it sits in is named by
    // its own contents as well -- every option of the list, while it is open. So the
    // name is the label's own words, the ones that are not the select.
    var named = select.getAttribute("aria-labelledby");
    var called = select.getAttribute("aria-label");
    if (!named && !called) {
      named = ((select.id && labels[select.id]) || [])
        .filter(function (label) {
          return !label.contains(select);
        })
        .map(function (label, index) {
          if (!label.id) {
            label.id = base + "-label" + (index ? "-" + index : "");
          }
          return label.id;
        })
        .join(" ");
      var around = select.closest("label");
      if (!named && around) {
        called = Array.prototype.filter
          .call(around.childNodes, function (node) {
            return node !== select && !(node.contains && node.contains(select));
          })
          .map(function (node) {
            return node.textContent;
          })
          .join(" ")
          .replace(/\s+/g, " ")
          .trim();
      }
    }
    [trigger, listbox].forEach(function (element) {
      if (named) {
        element.setAttribute("aria-labelledby", named);
      } else if (called) {
        element.setAttribute("aria-label", called);
      }
    });
    ["aria-describedby", "aria-invalid", "title", "dir", "lang"].forEach(function (name) {
      if (select.hasAttribute(name)) {
        trigger.setAttribute(name, select.getAttribute(name));
      }
    });
    ["dir", "lang"].forEach(function (name) {
      if (select.hasAttribute(name)) {
        panel.setAttribute(name, select.getAttribute(name));
      }
    });
    if (select.required) {
      trigger.setAttribute("aria-required", "true");
    }

    var value = document.createElement("span");
    var mark = null;
    if (select.querySelector("option[data-flag], option[data-icon]")) {
      mark = document.createElement("span");
      mark.setAttribute("data-select-mark", "");
      value.appendChild(mark);
    }
    // The words sit in a cell of their own inside the grid: a grid's own items cannot
    // be clamped to a line, and the words are (see the stylesheet).
    var text = document.createElement("span");
    text.setAttribute("data-select-text", "");
    var cell = document.createElement("span");
    var label = document.createElement("span");
    label.setAttribute("data-select-label", "");
    cell.appendChild(label);
    text.appendChild(cell);
    value.appendChild(text);
    trigger.appendChild(value);
    var chevron = optionIcon("chevron-down");
    if (chevron) {
      trigger.appendChild(chevron);
    }

    panel.appendChild(listbox);
    root.appendChild(trigger);
    root.appendChild(panel);

    var state = {
      select: select,
      base: base,
      root: root,
      trigger: trigger,
      mark: mark,
      label: label,
      text: text,
      panel: panel,
      listbox: listbox,
      filter: null,
      said: null,
      rows: [],
      plain: [],
      active: null,
      settled: false,
      typed: "",
      typedAt: 0,
      // Whether somebody moved the keys to the option they are on, or the box put them there.
      moved: false,
    };
    selectStates.set(select, state);
    selectOfPart.set(trigger, select);
    selectOfPart.set(panel, select);
    drawChosen(state);

    // The flag or the icon the server drew beside the closed native select is the
    // scripts-off path (#88, #208, #304, #305); the button draws its own.
    var beside = select.parentNode.querySelector(
      ":scope > [data-phone-flag], :scope > [data-flag-holder], :scope > [data-option-mark]"
    );
    if (beside) {
      beside.hidden = true;
    }

    var focused = document.activeElement === select;
    select.setAttribute("data-select-ready", "");
    select.setAttribute("aria-hidden", "true");
    select.tabIndex = -1;
    select.insertAdjacentElement("afterend", root);
    if (focused) {
      trigger.focus({ preventScroll: true });
    }
    return state;
  }

  function readySelects() {
    var parts = selectPartsTemplate();
    if (!parts || !popovers) {
      return;
    }
    // What a swap left behind: a button whose select has gone, which only happens where
    // something replaced the select and not what was beside it.
    Array.prototype.forEach.call(document.querySelectorAll("[data-select]"), function (root) {
      var select = selectOfPart.get(root.firstElementChild);
      if (!select || !select.isConnected || select.nextElementSibling !== root) {
        root.remove();
        if (select) {
          selectStates.delete(select);
        }
      }
    });
    var fresh = [];
    var labels = null;
    Array.prototype.forEach.call(document.querySelectorAll("select"), function (select) {
      var state = selectStates.get(select);
      if (state && state.root.isConnected) {
        return;
      }
      if (selectIsEnhanced(select)) {
        labels = labels || labelsByControl();
        fresh.push(buildSelect(select, parts, labels));
      }
    });
    // After every button is on the page, so that nothing is measured between two writes.
    fresh.forEach(function (state) {
      state.text.setAttribute("data-select-longest", longestWords(state.select));
    });

    // The focus was on a select's button and the swap replaced it: htmx looks for the
    // focused element's id as the new markup lands, and the button that will carry it
    // was not built until now.
    if (selectFocusBeforeSwap) {
      var again = document.getElementById(selectFocusBeforeSwap);
      selectFocusBeforeSwap = "";
      var lost = !document.activeElement || document.activeElement === document.body;
      if (again && lost) {
        again.focus({ preventScroll: true });
      }
    }
  }

  document.addEventListener("htmx:beforeSwap", function () {
    var active = document.activeElement;
    selectFocusBeforeSwap =
      active && active.hasAttribute && active.hasAttribute("data-select-trigger")
        ? active.id
        : "";
  });

  document.addEventListener("htmx:afterRequest", function () {
    selectFocusBeforeSwap = "";
  });

  onContentReady(readySelects);

  // A page that comes back from the browser's own cache, or with its form filled in
  // again, may hold selects that say something other than what their buttons were
  // drawn with.
  window.addEventListener("pageshow", function () {
    Array.prototype.forEach.call(
      document.querySelectorAll("select[data-select-ready]"),
      selectChanged
    );
  });

  ["change", "input"].forEach(function (name) {
    document.addEventListener(name, function (event) {
      var state = event.target && selectStates.get(event.target);
      if (state) {
        drawChosen(state);
        if (name === "change" && state.select.validity.valid) {
          unsaySelect(state);
        }
      }
    });
  });

  document.addEventListener("reset", function (event) {
    var form = event.target;
    // The controls go back to what they were drawn with after this event, not before.
    window.setTimeout(function () {
      Array.prototype.forEach.call(form.elements || [], selectChanged);
    }, 0);
  });

  // The focus given to the select -- by a click on its label, by a script that looks for
  // the first control of a row, by the browser on a page that says `autofocus` -- is the
  // button's.
  document.addEventListener("focusin", function (event) {
    var state = event.target && selectStates.get(event.target);
    if (state && state.root.isConnected) {
      state.trigger.focus();
    }
  });

  /* ---- a select that must have an answer, and has none
   *
   * The browser's own bubble points at the control that is wrong, and that control is out
   * of sight. So the browser's is declined, and the button says it instead: in an alert
   * under it, which the button is described by, in the words the page was drawn with --
   * the browser's would be in the browser's language. The first control of the form that
   * is wrong takes the focus, as it would have; where that is a box the browser still
   * handles, the browser takes it there.
   */
  function saySelect(state, words) {
    var id = state.base + "-said";
    var said = document.getElementById(id);
    if (!said) {
      said = document.createElement("div");
      said.id = id;
      said.setAttribute("role", "alert");
      said.setAttribute("data-select-said", "");
      said.appendChild(document.createElement("p"));
      state.root.insertAdjacentElement("afterend", said);
      var described = state.trigger.getAttribute("aria-describedby");
      state.trigger.setAttribute("aria-describedby", described ? described + " " + id : id);
    }
    said.firstChild.textContent = words;
    state.trigger.setAttribute("aria-invalid", "true");
  }

  function unsaySelect(state) {
    var id = state.base + "-said";
    var said = document.getElementById(id);
    if (!said) {
      return;
    }
    said.remove();
    var described = (state.trigger.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .filter(function (other) {
        return other && other !== id;
      })
      .join(" ");
    if (described) {
      state.trigger.setAttribute("aria-describedby", described);
    } else {
      state.trigger.removeAttribute("aria-describedby");
    }
    // What the server said about the field stands; what this said is taken back.
    if (state.select.hasAttribute("aria-invalid")) {
      state.trigger.setAttribute("aria-invalid", state.select.getAttribute("aria-invalid"));
    } else {
      state.trigger.removeAttribute("aria-invalid");
    }
  }

  document.addEventListener(
    "invalid",
    function (event) {
      var select = event.target;
      var state = select && selectStates.get(select);
      var parts = selectPartsTemplate();
      if (!state || !parts) {
        return;
      }
      event.preventDefault();
      saySelect(state, parts.getAttribute("data-required") || select.validationMessage);
      var first = Array.prototype.filter.call(
        (select.form && select.form.elements) || [select],
        function (control) {
          return control.willValidate && !control.validity.valid;
        }
      )[0];
      if (first === select) {
        state.trigger.focus();
        // The browser goes on down the form and reports the first control whose
        // `invalid` was not declined, which would take the focus off this button and
        // show its bubble. So for the rest of this check, the others are declined too.
        selectAnsweredInvalid = select.form || select;
        window.setTimeout(function () {
          selectAnsweredInvalid = null;
        }, 0);
      }
    },
    true
  );

  // The rest of a check whose first wrong control was a select's: see above.
  var selectAnsweredInvalid = null;
  document.addEventListener(
    "invalid",
    function (event) {
      var control = event.target;
      if (
        selectAnsweredInvalid &&
        control !== selectAnsweredInvalid &&
        !selectStates.get(control) &&
        (control.form === selectAnsweredInvalid || control === selectAnsweredInvalid)
      ) {
        event.preventDefault();
      }
    },
    true
  );

  /* ---- the list */

  // Lower case, without its accents, its spaces single: how what is typed and what an
  // option says are compared.
  function plainWords(text) {
    var folded = String(text || "").toLowerCase();
    if (folded.normalize) {
      folded = folded.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    }
    return folded.replace(/\s+/g, " ").trim();
  }

  // How well an option's plain words answer what was typed: 0 where they start with it, 1
  // where one of their words does, 2 where they only hold it, and -1 where they do not. A
  // dialling code in front is not one of the words: "+33 france" starts with "france".
  function selectMatchRank(plain, wanted) {
    if (!wanted) {
      return 0;
    }
    var at = plain.indexOf(wanted);
    if (at === -1) {
      return -1;
    }
    var bare = plain.replace(/^\+\d[\d\s-]*\s/, "");
    if (at === 0 || bare.indexOf(wanted) === 0) {
      return 0;
    }
    var words = bare.split(/[\s\-‐-―(),.\/'’]+/);
    for (var step = 0; step < words.length; step += 1) {
      if (words[step].indexOf(wanted) === 0) {
        return 1;
      }
    }
    return 2;
  }

  // Of these rows, the one the keys should go to for what was typed: the first that ranks
  // best, looking from `from` round to it again.
  function bestSelectMatch(state, rows, wanted, from, worst) {
    var best = null;
    var bestRank = 3;
    var last = worst === undefined ? 2 : worst;
    for (var step = 0; step < rows.length && bestRank > 0; step += 1) {
      var row = rows[(Math.max(from, 0) + step) % rows.length];
      var rank = selectMatchRank(state.plain[state.rows.indexOf(row)], wanted);
      if (rank !== -1 && rank <= last && rank < bestRank) {
        best = row;
        bestRank = rank;
      }
    }
    return best;
  }

  function selectRow(state, option, index) {
    var row = document.createElement("div");
    row.id = state.base + "-option-" + index;
    row.setAttribute("role", "option");
    row.setAttribute("data-value", option.value);
    row.setAttribute("data-option", String(option.index));
    row.setAttribute("aria-selected", option.selected ? "true" : "false");
    var group = option.parentNode;
    if (option.disabled || (group.tagName === "OPTGROUP" && group.disabled)) {
      row.setAttribute("aria-disabled", "true");
    }
    if (state.mark) {
      var mark = document.createElement("span");
      mark.setAttribute("data-select-mark", "");
      drawOptionMark(mark, option);
      row.appendChild(mark);
    }
    var words = document.createElement("span");
    words.textContent = optionWords(option);
    copyLanguage(option, words);
    row.appendChild(words);
    if (option.selected) {
      var tick = optionIcon("check");
      if (tick) {
        row.appendChild(tick);
      }
    }
    return row;
  }

  function fillSelectList(state) {
    var select = state.select;
    var parts = selectPartsTemplate();
    var listbox = state.listbox;
    listbox.textContent = "";
    state.rows = [];
    state.plain = [];
    state.active = null;
    state.typed = "";
    state.settled = false;

    var long = select.options.length >= SELECT_FILTER_FROM;
    // Said to the stylesheet too: a long list keeps its height while it is narrowed.
    state.panel.setAttribute("data-select-panel", long ? "long" : "");
    if (long && !state.filter) {
      var header = document.createElement("header");
      var filter = document.createElement("input");
      filter.type = "text";
      filter.setAttribute("data-select-filter", "");
      filter.setAttribute("role", "combobox");
      filter.setAttribute("aria-expanded", "true");
      filter.setAttribute("aria-autocomplete", "list");
      filter.setAttribute("aria-controls", listbox.id);
      ["aria-labelledby", "aria-label"].forEach(function (name) {
        if (state.trigger.hasAttribute(name)) {
          filter.setAttribute(name, state.trigger.getAttribute(name));
        }
      });
      filter.placeholder = (parts && parts.getAttribute("data-filter")) || "";
      // Not one of the form's controls: it has no name, and with no form it is not
      // checked, not sent and not what Enter submits.
      filter.setAttribute("form", "");
      filter.autocomplete = "off";
      filter.spellcheck = false;
      filter.setAttribute("autocapitalize", "none");
      filter.setAttribute("enterkeyhint", "done");
      header.appendChild(filter);
      // What the box found, for somebody who cannot see that the list is empty.
      var said = document.createElement("p");
      said.className = "sr-only";
      said.setAttribute("role", "status");
      header.appendChild(said);
      state.panel.insertBefore(header, listbox);
      state.filter = filter;
      state.said = said;
    } else if (!long && state.filter) {
      state.filter.parentNode.remove();
      state.filter = null;
      state.said = null;
    }
    if (state.filter) {
      state.filter.value = "";
      state.said.textContent = "";
    }

    var count = 0;
    function add(option, into) {
      if (option.hidden) {
        return;
      }
      var row = selectRow(state, option, count);
      count += 1;
      state.rows.push(row);
      state.plain.push(plainWords(optionWords(option)));
      into.appendChild(row);
    }
    var groups = 0;
    Array.prototype.forEach.call(select.children, function (child) {
      if (child.tagName === "OPTION") {
        add(child, listbox);
      } else if (child.tagName === "OPTGROUP") {
        groups += 1;
        var group = document.createElement("div");
        var heading = document.createElement("div");
        heading.id = state.base + "-group-" + groups;
        heading.setAttribute("role", "presentation");
        heading.setAttribute("data-select-heading", "");
        heading.textContent = child.label;
        group.setAttribute("role", "group");
        group.setAttribute("aria-labelledby", heading.id);
        group.appendChild(heading);
        Array.prototype.forEach.call(child.children, function (option) {
          if (option.tagName === "OPTION") {
            add(option, group);
          }
        });
        listbox.appendChild(group);
      }
    });
    showMatching(state);
  }

  function selectRowIsOffered(row) {
    return row.getAttribute("aria-hidden") !== "true";
  }

  function selectRowCanBeChosen(row) {
    return selectRowIsOffered(row) && row.getAttribute("aria-disabled") !== "true";
  }

  // Which options the box leaves in the list, and where each stands among them.
  function showMatching(state) {
    var wanted = plainWords(state.filter ? state.filter.value : "").replace(/^00(?=\d)/, "+");
    var shown = state.rows.filter(function (row, index) {
      var matches = !wanted || state.plain[index].indexOf(wanted) !== -1;
      if (matches) {
        row.removeAttribute("aria-hidden");
      } else {
        row.setAttribute("aria-hidden", "true");
      }
      return matches;
    });
    shown.forEach(function (row, index) {
      row.setAttribute("aria-posinset", String(index + 1));
      row.setAttribute("aria-setsize", String(shown.length));
    });
    if (state.said) {
      var words = shown.length ? "" : state.listbox.getAttribute("data-empty") || "";
      if (state.said.textContent !== words) {
        state.said.textContent = words;
      }
    }
    // What was typed puts the keys on the option it most likely means -- "fr" on France,
    // not on the first country whose name holds the two letters -- and that is a place the
    // keys were put, not one somebody moved to: Tab does not choose it.
    if (wanted || !state.active || !selectRowCanBeChosen(state.active)) {
      var offered = shown.filter(selectRowCanBeChosen);
      var chosen = offered.filter(function (row) {
        return row.getAttribute("aria-selected") === "true";
      })[0];
      activateSelectRow(
        state,
        wanted ? bestSelectMatch(state, offered, wanted, 0) : chosen || offered[0]
      );
      state.moved = false;
    }
  }

  // The option the keys are on. The focus stays where it is -- on the button, or in the
  // box of a long list -- and that control says which option it means.
  function activateSelectRow(state, row) {
    if (state.active) {
      state.active.classList.remove("active");
    }
    state.active = row || null;
    // Said on the button and on a long list's box alike: whichever of the two has the
    // focus is the one that is asked.
    [state.trigger, state.filter].forEach(function (holder) {
      if (holder && row) {
        holder.setAttribute("aria-activedescendant", row.id);
      } else if (holder) {
        holder.removeAttribute("aria-activedescendant");
      }
    });
    if (!row) {
      return;
    }
    row.classList.add("active");
    // Within the list alone: `scrollIntoView` would move the page as well.
    var list = state.listbox;
    var box = row.getBoundingClientRect();
    var frame = list.getBoundingClientRect();
    if (box.height && frame.height) {
      if (box.top < frame.top) {
        list.scrollTop -= frame.top - box.top;
      } else if (box.bottom > frame.bottom) {
        list.scrollTop += box.bottom - frame.bottom;
      }
    }
  }

  function moveInSelect(state, where) {
    var rows = state.rows.filter(selectRowCanBeChosen);
    if (!rows.length) {
      return;
    }
    var here = rows.indexOf(state.active);
    var next;
    if (where === "first") {
      next = 0;
    } else if (where === "last") {
      next = rows.length - 1;
    } else if (here === -1) {
      next = where > 0 ? 0 : rows.length - 1;
    } else {
      // No further than either end: a list does not go round.
      next = Math.max(0, Math.min(rows.length - 1, here + where));
    }
    activateSelectRow(state, rows[next]);
    state.moved = true;
  }

  // A letter typed on a short list goes to the next option that starts with it, and
  // letters typed quickly spell the start of one, as in a native list.
  function typeInSelect(state, key) {
    var now = Date.now();
    state.typed = now - state.typedAt > SELECT_TYPED_FOR ? key : state.typed + key;
    state.typedAt = now;
    var rows = state.rows.filter(selectRowCanBeChosen);
    var here = rows.indexOf(state.active);
    var wanted = plainWords(state.typed);
    var same = wanted.split("").every(function (letter) {
      return letter === wanted[0];
    });
    // One letter, or the same one again, moves on from where the keys are to an option
    // that starts with it, or failing that has a word that does; a word being spelled
    // stays on an option that still matches it, ranked as the box ranks them.
    var found =
      wanted.length > 1 && !same
        ? bestSelectMatch(state, rows, wanted, Math.max(here, 0))
        : bestSelectMatch(state, rows, wanted[0] || "", here + 1, 1);
    if (found) {
      activateSelectRow(state, found);
      // A list with no box is worked by its letters as by its arrows, as a native one is.
      state.moved = true;
    }
  }

  function selectIsOpen(state) {
    return panelIsOpen(state.panel);
  }

  // By pressing its own button, as a menu is opened (above): a panel opened by its
  // `popovertarget` has that button for its anchor.
  function openSelect(state) {
    if (!selectIsOpen(state) && !state.trigger.disabled) {
      state.trigger.click();
    }
    if (selectIsOpen(state)) {
      settleSelect(state);
    }
    return selectIsOpen(state);
  }

  // Under a finger, a box that takes the focus calls up the keyboard, over half of the
  // list somebody opened in order to look down it. So there the focus stays on the
  // button, and the box is one tap away for whoever wants to type.
  function selectIsUnderAFinger() {
    return Boolean(window.matchMedia) && window.matchMedia("(pointer: coarse)").matches;
  }

  // Once the list is drawn: the focus goes into a long list's box, and the option the
  // keys start on is brought into view.
  function settleSelect(state) {
    if (state.settled) {
      return;
    }
    state.settled = true;
    openSelectState = state;
    state.trigger.setAttribute("aria-expanded", "true");
    if (state.filter && !selectIsUnderAFinger()) {
      state.filter.focus({ preventScroll: true });
    } else if (document.activeElement !== state.trigger) {
      // Safari gives a button no focus when it is clicked, and the keys of an open list
      // are heard where the focus is.
      state.trigger.focus({ preventScroll: true });
    }
    activateSelectRow(state, state.active);
  }

  // A letter typed while the focus is on the button of a long list goes into its box.
  function typeInFilter(state, key, afresh) {
    state.filter.focus({ preventScroll: true });
    state.filter.value = afresh ? key : state.filter.value + key;
    showMatching(state);
  }

  // What the button says once its list has gone, however it went: by a choice, by
  // Escape, by a click elsewhere, which is the browser's doing and reaches here through
  // the panel's `beforetoggle`.
  function selectHasClosed(state) {
    state.settled = false;
    state.trigger.setAttribute("aria-expanded", "false");
    state.trigger.removeAttribute("aria-activedescendant");
    if (state.filter) {
      state.filter.removeAttribute("aria-activedescendant");
    }
    if (openSelectState === state) {
      openSelectState = null;
    }
  }

  function closeSelect(state, focusTrigger) {
    if (selectIsOpen(state)) {
      state.panel.hidePopover();
    }
    if (focusTrigger && document.activeElement !== state.trigger) {
      state.trigger.focus({ preventScroll: true });
    }
  }

  function chooseInSelect(state, row, focusTrigger) {
    var select = state.select;
    var index = row && selectRowCanBeChosen(row) ? Number(row.getAttribute("data-option")) : -1;
    closeSelect(state, focusTrigger);
    if (index < 0 || index === select.selectedIndex) {
      return;
    }
    select.selectedIndex = index;
    drawChosen(state);
    // What a native select raises when somebody chooses from it, in that order.
    select.dispatchEvent(new Event("input", { bubbles: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  document.addEventListener(
    "beforetoggle",
    function (event) {
      var select = selectOfPart.get(event.target);
      var state = select && selectStates.get(select);
      if (!state) {
        return;
      }
      if (event.newState === "open") {
        fillSelectList(state);
      } else {
        selectHasClosed(state);
      }
    },
    true
  );

  document.addEventListener(
    "toggle",
    function (event) {
      var select = selectOfPart.get(event.target);
      var state = select && selectStates.get(select);
      if (!state) {
        return;
      }
      if (event.newState === "open" && selectIsOpen(state)) {
        settleSelect(state);
      } else if (!selectIsOpen(state)) {
        selectHasClosed(state);
      }
    },
    true
  );

  function selectStateAt(target) {
    if (!target || !target.closest) {
      return null;
    }
    var part = target.closest("[data-select-trigger], [data-select-panel]");
    var select = part && selectOfPart.get(part);
    return (select && selectStates.get(select)) || null;
  }

  function selectKeyIsALetter(event) {
    return event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
  }

  // In the capturing phase, and stopped once it is answered: Escape that closes a list
  // must not also close the disclosure the select sits in, and a letter typed on a list
  // must not also be one of the page's single-key shortcuts.
  document.addEventListener(
    "keydown",
    function (event) {
      var state = selectStateAt(event.target);
      if (!state || event.isComposing || event.keyCode === 229) {
        return;
      }
      var key = event.key;
      var inFilter = event.target === state.filter;
      function answered() {
        event.preventDefault();
        event.stopPropagation();
      }

      if (!selectIsOpen(state)) {
        if (["ArrowDown", "ArrowUp", "Enter", " ", "Home", "End"].indexOf(key) !== -1) {
          if (event.ctrlKey || event.metaKey) {
            return;
          }
          answered();
          if (openSelect(state) && (key === "Home" || key === "End")) {
            moveInSelect(state, key === "Home" ? "first" : "last");
          }
        } else if (selectKeyIsALetter(event)) {
          answered();
          if (!openSelect(state)) {
            return;
          }
          if (state.filter) {
            typeInFilter(state, key, true);
          } else {
            typeInSelect(state, key);
          }
        }
        return;
      }

      if (key === "Escape") {
        answered();
        closeSelect(state, true);
      } else if (key === "ArrowDown" || key === "ArrowUp") {
        answered();
        if (key === "ArrowUp" && event.altKey) {
          chooseInSelect(state, state.active, true);
        } else {
          moveInSelect(state, key === "ArrowDown" ? 1 : -1);
        }
      } else if ((key === "Home" || key === "End") && !inFilter) {
        // In a long list's box they move the caret in what was typed, as in any box.
        answered();
        moveInSelect(state, key === "Home" ? "first" : "last");
      } else if (key === "PageDown" || key === "PageUp") {
        answered();
        moveInSelect(state, key === "PageDown" ? SELECT_PAGE : -SELECT_PAGE);
      } else if (key === "Enter" || (key === " " && !inFilter)) {
        answered();
        chooseInSelect(state, state.active, true);
      } else if (key === "Tab") {
        // Not cancelled: the focus is back on the button, and the browser moves it on from
        // there. The option the keys are on is chosen only if somebody moved to it -- with
        // the arrows, Home, End, the page keys, a letter or the pointer. Where only the box
        // put them there, Tab leaves the choice as it was: what the box found first is a
        // guess, and on a list that saves as it changes it would be saved.
        if (state.moved) {
          chooseInSelect(state, state.active, true);
        } else {
          closeSelect(state, true);
        }
      } else if (!inFilter && selectKeyIsALetter(event)) {
        answered();
        if (state.filter) {
          typeInFilter(state, key, false);
        } else {
          typeInSelect(state, key);
        }
      }
    },
    true
  );

  // Space presses a button as the key comes up, and that press would open the list
  // again, or close it without the choice.
  document.addEventListener(
    "keyup",
    function (event) {
      if (
        event.key === " " &&
        event.target.hasAttribute &&
        event.target.hasAttribute("data-select-trigger")
      ) {
        event.preventDefault();
      }
    },
    true
  );

  // What is typed in a long list's box is the list's business and nobody else's: the box
  // sits inside the form the select belongs to, and its `input` and `change` would
  // otherwise reach a form that narrows as it changes, and mark a form as having work in
  // it that nobody has done (#258).
  ["input", "change"].forEach(function (name) {
    document.addEventListener(
      name,
      function (event) {
        var state = selectStateAt(event.target);
        if (state && event.target === state.filter) {
          event.stopPropagation();
          if (name === "input") {
            showMatching(state);
          }
        }
      },
      true
    );
  });

  function selectRowAt(state, target) {
    var row = target.closest ? target.closest('[role="option"]') : null;
    return row && state.listbox.contains(row) ? row : null;
  }

  document.addEventListener("click", function (event) {
    var state = selectStateAt(event.target);
    var row = state && selectRowAt(state, event.target);
    if (row && selectRowCanBeChosen(row)) {
      chooseInSelect(state, row, true);
    }
  });

  // A press outside an open list closes it -- the browser's light dismiss -- and does
  // nothing else: the click it ends in would otherwise also press whatever it landed on,
  // a button that opens a dialog, another select's button, a link. Under a finger the
  // list covers much of the screen, and the tap that was meant only to put it away would
  // act on the page behind. A press on the list's own button is the button's, which
  // closes it. So the press is noted, and the click that follows it is declined; one
  // made from the keyboard, which says it was no press (`detail` 0), never is.
  var selectPressedOutside = 0;
  window.addEventListener(
    "pointerdown",
    function (event) {
      var state = openSelectState;
      var target = event.target;
      // The button and the list are both inside the select's own wrapper.
      selectPressedOutside =
        state && selectIsOpen(state) && !(target instanceof Node && state.root.contains(target))
          ? Date.now()
          : 0;
    },
    true
  );

  window.addEventListener(
    "click",
    function (event) {
      if (!selectPressedOutside) {
        return;
      }
      var recent = Date.now() - selectPressedOutside < SELECT_PRESS_FOR;
      selectPressedOutside = 0;
      if (recent && event.detail !== 0) {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
      }
    },
    true
  );

  // A press in the list must not take the focus off the control that is driving it --
  // the button, or a long list's box -- or the keys would have nowhere to go: an option,
  // a group's heading and the room round them hold no focus of their own. A press in the
  // box itself is the box's.
  document.addEventListener("mousedown", function (event) {
    var state = selectStateAt(event.target);
    if (state && state.panel.contains(event.target) && event.target !== state.filter) {
      event.preventDefault();
    }
  });

  // The pointer moves the keys' place with it, so the two never mark different options.
  // Only a pointer that moved: a list scrolled under a still pointer changes nothing.
  document.addEventListener("mousemove", function (event) {
    var state = openSelectState;
    if (!state) {
      return;
    }
    var row = selectRowAt(state, event.target);
    if (!row || !selectRowCanBeChosen(row)) {
      return;
    }
    if (row !== state.active) {
      var scroll = state.listbox.scrollTop;
      activateSelectRow(state, row);
      state.listbox.scrollTop = scroll;
    }
    // Onto the option the box had found, too: now somebody has moved to it.
    state.moved = true;
  });

  /* ---------------------------------------------------------------------- drawers
   *
   * `<c-drawer>` is a `<dialog>` a link opens, above, and so only ever a modal (#302). The
   * browser closes a modal dialog on Escape and hands the focus back; what is left to the
   * page is its *Close* button and a press outside the panel. The dialog fills the window
   * and the panel is its one child, so a press outside the panel is a press on the dialog
   * itself -- and it has to have started there too: a selection dragged out of the text
   * and let go over the page is not a press on the page.
   */
  var pressedOn = null;

  document.addEventListener(
    "pointerdown",
    function (event) {
      pressedOn = event.target;
    },
    true
  );

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    var close = target.closest("[data-drawer-close]");
    var drawer = close
      ? close.closest("dialog[data-drawer]")
      : target.matches("dialog[data-drawer]") && pressedOn === target
        ? target
        : null;
    if (drawer && drawer.open) {
      drawer.close();
    }
  });

  /* ---------------------------------------------------------------------- tooltips
   *
   * A sentence that says what a field is for is drawn by the server as an ordinary
   * paragraph under the field, carrying `data-tooltip` and the id the control names in
   * `aria-describedby` (#302). With no script that is where it stays. Here it becomes a
   * tooltip: a popover, hidden until the field is hovered or has the focus, and still the
   * control's description either way, so a screen reader reads it with the field exactly
   * as before. A card's one sentence is the same thing on the card's question mark.
   *
   * **What shows one.** The pointer resting on the field -- its label, its control, or
   * the tooltip itself -- and the focus being anywhere in the field. A field is the
   * nearest `.field` that holds something described by a tooltip; a control outside one,
   * like a question mark, is its own. Focus shows it at once; the pointer after a moment,
   * so that crossing a form does not light every sentence on the way. Once shown it stays
   * while the pointer is on the field, on the tooltip, or on the strip between them (the
   * tooltip's `::after`, as wide as the two together), so there is no gap to cross at
   * any speed; and for half a second after the pointer has left all three, so that a hand
   * that slips off an edge, or takes the long way round to a corner, loses nothing. A
   * touch does not hover: touching a control gives it the focus, and that shows it.
   *
   * **What takes it away** (SC 1.4.13): the pointer and the focus both having gone, or
   * Escape. Escape puts away the tooltips that are showing. Where one of them is the
   * focused field's, the key is the tooltip's and nobody else's -- the focus stays, a
   * dialog the field is in stays open, and a second Escape is the dialog's. A tooltip
   * shown only for a pointer resting somewhere is put away by the same Escape, which then
   * goes on to whatever has the focus: somebody typing in the search with the pointer
   * parked on a field is not looking at that field's tooltip. One put away stays away
   * until the pointer or the focus that showed it has left and come back. A press that
   * opens a modal dialog takes away the one the pointer was resting on: it is behind the
   * dialog now.
   *
   * **Where it goes: only where there is a clear place for it.** A place is clear when the
   * tooltip, put there and measured,
   *
   * - is wholly inside the window that can be seen: under the masthead that floats at the
   *   top and the search bar under it, above the bar at the foot of a phone's window, and
   *   inside the part of the page a zoomed or keyboard-shortened window shows;
   * - is not over any part of its own field -- the label, the control, a mark or an error
   *   under it -- nor, for a card's sentence, over the question mark it is shown for;
   * - and is not over anything that must never be hidden: a refusal or an alert, a mark
   *   or a note under a row (*Not checked*, *Kept as it was*, a place's note), a card's
   *   question mark, a select's list that is open (#301), or the control that has the
   *   focus (`TIP_KEEPS_CLEAR_OF`).
   *
   * **A field whose list is open shows no tooltip.** The list is what is being read then,
   * and it opens over the place the field's tooltip would take. So opening a select's list
   * puts its field's tooltip away at once, and none comes back for that field while the
   * list is open, whether for the focus or for the pointer resting on the list. Once the
   * list has closed, the usual rule holds again: the focus is back on the select's button,
   * so its sentence shows -- unless Escape had put it away before.
   *
   * The places are tried in order: above the field from its start edge, above from its
   * end edge, above it anywhere along the window, then the same three below; a question
   * mark is in its card's corner at the inline end, so its sentence tries the end edge
   * first. Where all that is in the way of a place is a question mark, or a side of the
   * window, the tooltip stops short of it -- moved along the line as little as it can be,
   * and narrowed to the room that is left, never below ten rem -- and is asked again.
   * **Where no place is clear, the help is not a tooltip**: it goes back to being
   * the paragraph under the field that the server drew, where it was before #302 and is
   * with scripts off, and it stays there until the window changes width -- a sentence
   * that came and went with the focus would move the page twice at every stop, in the
   * windows that have least room to spare, and the room it is looking for is the room it
   * takes. A window too short to hold a field and its tooltip gets the help under every
   * field it visits, and no popover at all. A field scrolled out of sight hides its
   * tooltip until it is back.
   *
   * It is placed here, in every browser, in the page's own co-ordinates: it scrolls with
   * the page as its field does, and is asked again whenever anything scrolls or the window
   * changes. Not with CSS anchor positioning: the rule is about where the tooltip *is*,
   * and a box the browser ties to an anchor is moved when the browser next draws, not
   * when it is measured, so a place checked in the same breath as a scroll would be the
   * place it had before. Where a browser has no popover at all nothing here runs, and the
   * sentences stay under their fields.
   */
  var TIP_SHOWN_AFTER = 300; // how long the pointer rests on a field before its sentence shows
  var TIP_KEPT_FOR = 500; // how long it stays once the pointer has left field, strip and tooltip
  var TIP_GAP = 6; // between a field and its tooltip; the stylesheet's strip spans it
  var TIP_EDGE = 8; // how near the window's edge a tooltip placed along it may come
  var TIP_NARROWEST = 160; // 10rem: the narrowest a tooltip is made, to stop short of something

  // What a tooltip may never lie over, besides its own field and the focused control.
  var TIP_KEEPS_CLEAR_OF =
    '[role="alert"], .alert, .errorlist, [data-phone-mark], [data-kept-as-it-was], ' +
    "[data-place-note], [data-country-note], [data-help-mark], [data-select-panel]:popover-open";

  // A field's sentence starts at the field's start edge; a question mark's at its end.
  var TIP_PLACES = ["above-start", "above-end", "above", "below-start", "below-end", "below"];
  var MARK_PLACES = ["above-end", "above-start", "above", "below-end", "below-start", "below"];

  // Where the focus is and where the pointer is, each as the field and its tooltips, or
  // nothing; where the pointer is about to be, once it has rested there; and what is to
  // be shown, each tooltip with the field it is shown for, placed in the next frame.
  var tipFocus = null;
  var tipHover = null;
  var tipHoverNext = null;
  var tipHoverTimer = 0;
  var tipWanted = [];
  var tipFrame = 0;
  var tipWidth = window.innerWidth;

  function isTip(node) {
    return !!node && node.nodeType === 1 && node.matches("[data-tooltip][popover]");
  }

  function tipsOf(control) {
    return (control.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .map(function (id) {
        return id ? document.getElementById(id) : null;
      })
      .filter(isTip);
  }

  function tipsIn(box) {
    var found = [];
    Array.prototype.forEach.call(box.querySelectorAll("[aria-describedby]"), function (control) {
      tipsOf(control).forEach(function (tip) {
        if (found.indexOf(tip) === -1) {
          found.push(tip);
        }
      });
    });
    return found;
  }

  // The field a node is in and the tooltips that describe it, or nothing.
  function tipPlace(node) {
    if (!node || !node.closest) {
      return null;
    }
    var box = node.closest(".field");
    while (box) {
      var inside = tipsIn(box);
      if (inside.length) {
        return { anchor: box, tips: inside, spent: false };
      }
      box = box.parentElement ? box.parentElement.closest(".field") : null;
    }
    var described = node.closest("[aria-describedby]");
    var own = described ? tipsOf(described) : [];
    return own.length ? { anchor: described, tips: own, spent: false } : null;
  }

  function samePlace(one, other) {
    return (!one && !other) || (!!one && !!other && one.anchor === other.anchor);
  }

  // A select's list while it is being asked to open, before the browser has opened it:
  // its field's tooltip goes then, before the list is drawn.
  var tipListOpening = null;

  // Whether a list of a select in this field is open, or opening.
  function listIsOpenIn(anchor) {
    return Array.prototype.some.call(document.querySelectorAll("[data-select-panel]"), function (panel) {
      if (panel !== tipListOpening && !panel.matches(":popover-open")) {
        return false;
      }
      var select = selectOfPart.get(panel);
      return anchor.contains(panel) || (!!select && (select === anchor || anchor.contains(select)));
    });
  }

  function tipIsOpen(tip) {
    return tip.isConnected && isTip(tip) && tip.matches(":popover-open");
  }

  function behindAModal(node) {
    var modal = document.querySelector("dialog:modal");
    return !!modal && !modal.contains(node);
  }

  function overlap(one, other) {
    return (
      one.left < other.right - 1 &&
      other.left < one.right - 1 &&
      one.top < other.bottom - 1 &&
      other.top < one.bottom - 1
    );
  }

  // The part of the window a tooltip can be seen in, in the window's co-ordinates.
  function tipWindow() {
    var root = document.documentElement;
    var seen = { left: 0, top: 0, right: root.clientWidth, bottom: root.clientHeight };
    var viewport = window.visualViewport;
    if (viewport) {
      seen.left = Math.max(seen.left, viewport.offsetLeft);
      seen.top = Math.max(seen.top, viewport.offsetTop);
      seen.right = Math.min(seen.right, viewport.offsetLeft + viewport.width);
      seen.bottom = Math.min(seen.bottom, viewport.offsetTop + viewport.height);
    }
    if (siteHeader) {
      seen.top = Math.max(seen.top, siteHeader.getBoundingClientRect().bottom);
    }
    var bar = document.querySelector(".site-search:popover-open");
    if (bar && window.getComputedStyle(bar).position === "fixed") {
      seen.top = Math.max(seen.top, bar.getBoundingClientRect().bottom);
    }
    if (mainNav && window.getComputedStyle(mainNav).position === "fixed") {
      seen.bottom = Math.min(seen.bottom, mainNav.getBoundingClientRect().top);
    }
    return seen;
  }

  // Everything a tooltip for this field may not lie over, as boxes, the question marks
  // apart: a place in the way of nothing but a mark can be narrowed to stop short of it.
  function tipObstacles(anchor) {
    var found = { boxes: [], marks: [] };
    var take = function (node) {
      if (!node || node === anchor || anchor.contains(node) || node.contains(anchor)) {
        return;
      }
      var box = node.getBoundingClientRect();
      if (box.width || box.height) {
        (node.matches("[data-help-mark]") ? found.marks : found.boxes).push(box);
      }
    };
    Array.prototype.forEach.call(document.querySelectorAll(TIP_KEEPS_CLEAR_OF), take);
    if (document.activeElement && document.activeElement !== document.body) {
      take(document.activeElement);
    }
    return found;
  }

  // What is in the way of a tooltip at `box`: nothing (`null`); `true` for what no moving
  // along the line can cure -- the window's top or foot, its own field, a refusal, a mark
  // under a row, the focused control; or else the question marks it would lie over, an
  // empty list where all it does is run past a side of the window.
  function inTheWay(box, anchor, seen, obstacles) {
    if (box.top < seen.top - 1 || box.bottom > seen.bottom + 1) {
      return true;
    }
    if (overlap(box, anchor.getBoundingClientRect())) {
      return true;
    }
    for (var i = 0; i < obstacles.boxes.length; i += 1) {
      if (overlap(box, obstacles.boxes[i])) {
        return true;
      }
    }
    var marks = obstacles.marks.filter(function (mark) {
      return overlap(box, mark);
    });
    var sideways = box.left < seen.left - 1 || box.right > seen.right + 1;
    return marks.length || sideways ? marks : null;
  }

  // The same place, short of the question marks in the way and inside the window's sides:
  // the band of the line between them. A tooltip put at one of its field's edges keeps
  // that edge and is narrowed to the band, where that leaves a tooltip worth reading; else
  // it is narrowed to the whole band and moved along it as little as it can be. Nothing
  // where even the band is narrower than that.
  function withinTheBand(tip, place, box, marks, from, seen) {
    var bandLeft = seen.left + TIP_EDGE;
    var bandRight = seen.right - TIP_EDGE;
    var middle = (from.left + from.right) / 2;
    marks.forEach(function (mark) {
      if ((mark.left + mark.right) / 2 > middle) {
        bandRight = Math.min(bandRight, mark.left - TIP_GAP);
      } else {
        bandLeft = Math.max(bandLeft, mark.right + TIP_GAP);
      }
    });
    if (bandRight - bandLeft < TIP_NARROWEST) {
      return null;
    }
    var along = place !== "above" && place !== "below";
    var atRight = along && Math.abs(box.right - from.right) < 1;
    var atLeft = along && !atRight;
    var edge = atLeft ? Math.max(box.left, bandLeft) : Math.min(box.right, bandRight);
    var fromEdge = atLeft ? bandRight - edge : edge - bandLeft;
    var keep = along && fromEdge >= TIP_NARROWEST;
    var room = Math.floor(keep ? fromEdge : bandRight - bandLeft);
    var wide = box.right - box.left;
    if (wide > room) {
      tip.style.maxInlineSize = room + "px";
      wide = tip.offsetWidth;
    }
    var tall = tip.offsetHeight;
    var left;
    if (keep) {
      left = atLeft ? edge : edge - wide;
    } else if (along) {
      left = atLeft ? box.left : box.right - wide;
    } else {
      left = (box.left + box.right) / 2 - wide / 2;
    }
    left = Math.round(Math.max(bandLeft, Math.min(left, bandRight - wide)));
    var top = Math.round(
      place.indexOf("above") === 0 ? from.top - TIP_GAP - tall : from.bottom + TIP_GAP
    );
    return { left: left, top: top, right: left + wide, bottom: top + tall };
  }

  // Where a tooltip of this size would be, put at one of the places, in the window's
  // co-ordinates.
  function tipBoxAt(place, from, wide, tall, seen, rtl) {
    var top = place.indexOf("above") === 0 ? from.top - TIP_GAP - tall : from.bottom + TIP_GAP;
    var left;
    if (place === "above" || place === "below") {
      left = from.left + (from.width - wide) / 2;
      left = Math.max(seen.left + TIP_EDGE, Math.min(left, seen.right - TIP_EDGE - wide));
    } else if (/-start$/.test(place) !== rtl) {
      left = from.left;
    } else {
      left = from.right - wide;
    }
    left = Math.round(left);
    top = Math.round(top);
    return { left: left, top: top, right: left + wide, bottom: top + tall };
  }

  // Put it there: where it is drawn, which side its field is on, and the strip over the
  // gap between them, as wide as the two together. Through the CSSOM, which the content
  // security policy allows.
  function putTip(tip, box, origin, place, from, rtl) {
    var style = tip.style;
    style.left = box.left - origin.left + "px";
    style.top = box.top - origin.top + "px";
    tip.setAttribute("data-tooltip-side", place.indexOf("above") === 0 ? "above" : "below");
    var edge = tip.clientLeft;
    var outLeft = Math.max(0, box.left - from.left) + edge;
    var outRight = Math.max(0, from.right - box.right) + edge;
    style.setProperty("--tooltip-bridge-start", -(rtl ? outRight : outLeft) + "px");
    style.setProperty("--tooltip-bridge-end", -(rtl ? outLeft : outRight) + "px");
  }

  // No clear place: the help goes back to being the paragraph under its field.
  function tipUnder(tip) {
    if (tipIsOpen(tip)) {
      tip.hidePopover();
    }
    tip.removeAttribute("popover");
    tip.removeAttribute("role");
    tip.removeAttribute("data-tooltip-side");
    tip.style.cssText = "";
    tip.tooltipFor = null;
    tip.setAttribute("data-tooltip-in-flow", "");
  }

  function placeTip(tip, anchor) {
    if (!isTip(tip) || !tip.isConnected || !anchor.isConnected) {
      return;
    }
    if (listIsOpenIn(anchor)) {
      if (tipIsOpen(tip)) {
        tip.hidePopover();
      }
      return;
    }
    var seen = tipWindow();
    var from = anchor.getBoundingClientRect();
    if (!overlap(from, seen)) {
      // Out of sight, and so is what it would say: shown again when the field is back.
      if (tipIsOpen(tip)) {
        tip.hidePopover();
      }
      return;
    }
    if (!tipIsOpen(tip)) {
      tip.showPopover();
    }
    tip.tooltipFor = anchor;
    var style = tip.style;
    style.maxInlineSize = "";
    style.left = "0px";
    style.top = "0px";
    var origin = tip.getBoundingClientRect();
    var rtl = window.getComputedStyle(anchor).direction === "rtl";
    var places = anchor.hasAttribute("data-help-mark") ? MARK_PLACES : TIP_PLACES;
    var obstacles = tipObstacles(anchor);
    for (var i = 0; i < places.length; i += 1) {
      var place = places[i];
      style.maxInlineSize = "";
      var box = tipBoxAt(place, from, tip.offsetWidth, tip.offsetHeight, seen, rtl);
      var blocked = inTheWay(box, anchor, seen, obstacles);
      if (blocked && blocked !== true) {
        // Only question marks, or the window's sides, in the way: stop short of them.
        var moved = withinTheBand(tip, place, box, blocked, from, seen);
        if (moved) {
          box = moved;
          blocked = inTheWay(box, anchor, seen, obstacles);
        }
      }
      if (!blocked) {
        putTip(tip, box, origin, place, from, rtl);
        return;
      }
    }
    style.maxInlineSize = "";
    tipUnder(tip);
  }

  function placeTips() {
    tipFrame = 0;
    tipWanted.forEach(function (pair) {
      placeTip(pair.tip, pair.anchor);
    });
  }

  function placeTipsSoon() {
    if (!tipFrame && tipWanted.length) {
      tipFrame = window.requestAnimationFrame(placeTips);
    }
  }

  // Every tooltip that should be showing, each with the field it is shown for: the
  // pointer's place first, so that a sentence two fields share goes to the one pointed
  // at. What should not be showing goes at once; what should is placed in the next frame,
  // after the browser has scrolled a field that took the focus into view.
  function drawTips() {
    var wanted = [];
    [tipHover, tipFocus].forEach(function (place) {
      if (!place || place.spent || behindAModal(place.anchor) || listIsOpenIn(place.anchor)) {
        return;
      }
      place.tips.forEach(function (tip) {
        var already = wanted.some(function (pair) {
          return pair.tip === tip;
        });
        if (!already && isTip(tip)) {
          wanted.push({ tip: tip, anchor: place.anchor });
        }
      });
    });
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-tooltip][popover]:popover-open"),
      function (tip) {
        var kept = wanted.some(function (pair) {
          return pair.tip === tip;
        });
        if (!kept) {
          tip.hidePopover();
        }
      }
    );
    tipWanted = wanted;
    placeTipsSoon();
  }

  function readyTooltips() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-tooltip][id]:not([popover]):not([data-tooltip-in-flow])"),
      function (tip) {
        // Only a sentence something is described by: one nothing names would be put out
        // of sight with nothing left to show it, and is better left where it is.
        if (document.querySelector('[aria-describedby~="' + CSS.escape(tip.id) + '"]')) {
          tip.setAttribute("popover", "manual");
          tip.setAttribute("role", "tooltip");
        }
      }
    );
    // A field that had the focus before this ran -- an `autofocus`, or a swap that put
    // the focus back -- has had no event to say so.
    // And its tooltips are asked for afresh, since a sentence under a field may have become
    // one again.
    var focused = tipPlace(document.activeElement);
    if (focused && samePlace(focused, tipFocus)) {
      focused.spent = tipFocus.spent;
    }
    tipFocus = focused;
    drawTips();
  }

  function hoverSettles() {
    tipHoverTimer = 0;
    if (!samePlace(tipHoverNext, tipHover)) {
      tipHover = tipHoverNext;
      drawTips();
    }
  }

  function hoverMoves(place) {
    if (samePlace(place, tipHoverNext)) {
      return;
    }
    tipHoverNext = samePlace(place, tipHover) ? tipHover : place;
    window.clearTimeout(tipHoverTimer);
    tipHoverTimer = window.setTimeout(hoverSettles, place ? TIP_SHOWN_AFTER : TIP_KEPT_FOR);
  }

  if (popovers) {
    onContentReady(readyTooltips);

    document.addEventListener("focusin", function (event) {
      var place = tipPlace(event.target);
      if (!samePlace(place, tipFocus)) {
        tipFocus = place;
        drawTips();
      }
    });

    document.addEventListener("focusout", function (event) {
      // Where the focus is going, which is nowhere when it leaves the window. Within one
      // field -- from a country to its number -- nothing changes and nothing blinks.
      var place = tipPlace(event.relatedTarget);
      if (!samePlace(place, tipFocus)) {
        tipFocus = place;
        drawTips();
      }
    });

    document.addEventListener("pointerover", function (event) {
      if (event.pointerType === "touch") {
        return;
      }
      var target = event.target;
      var onTip = target && target.closest ? target.closest("[data-tooltip][popover]") : null;
      if (onTip) {
        // Resting on the tooltip, or on the strip between it and its field, keeps it:
        // it is part of what it describes.
        if (tipHover && tipHover.tips.indexOf(onTip) !== -1) {
          hoverMoves(tipHover);
        }
        return;
      }
      hoverMoves(tipPlace(target));
    });

    document.addEventListener("pointerout", function (event) {
      if (event.pointerType !== "touch" && !event.relatedTarget) {
        hoverMoves(null);
      }
    });

    // A press that opened a modal dialog: the tooltip the pointer was resting on is
    // behind it now, and nobody is looking at it.
    document.addEventListener("click", function () {
      if ((tipHover || tipHoverNext) && document.querySelector("dialog:modal")) {
        var behind = [tipHover, tipHoverNext].some(function (place) {
          return place && behindAModal(place.anchor);
        });
        if (behind) {
          window.clearTimeout(tipHoverTimer);
          tipHover = null;
          tipHoverNext = null;
          drawTips();
        }
      }
    });

    document.addEventListener(
      "keydown",
      function (event) {
        if (event.key !== "Escape" || event.isComposing) {
          return;
        }
        var showing = function (place) {
          return !!place && !place.spent && place.tips.some(tipIsOpen);
        };
        var focused = showing(tipFocus);
        if (!focused && !showing(tipHover)) {
          return;
        }
        if (focused) {
          // The focused field's tooltip: Escape is its and nobody else's this once -- not
          // the dialog's the field is in, not the cell's being edited. The capture phase is
          // what lets it say so.
          event.preventDefault();
          event.stopPropagation();
        }
        [tipFocus, tipHover].forEach(function (place) {
          if (place) {
            place.spent = true;
          }
        });
        drawTips();
      },
      true
    );

    // A select's list opening or closing (#301): its field's tooltip goes at once, or may
    // come back; and every other tooltip showing is asked again, to keep clear of the list.
    document.addEventListener(
      "beforetoggle",
      function (event) {
        if (!event.target.matches || !event.target.matches("[data-select-panel]")) {
          return;
        }
        if (event.newState === "open") {
          // The list is not open yet, and is the moment this event is over.
          tipListOpening = event.target;
          drawTips();
          tipListOpening = null;
        }
      },
      true
    );
    document.addEventListener(
      "toggle",
      function (event) {
        if (!event.target.matches || !event.target.matches("[data-select-panel]")) {
          return;
        }
        drawTips();
      },
      true
    );

    document.addEventListener("scroll", placeTipsSoon, { capture: true, passive: true });
    window.addEventListener("resize", function () {
      // A window of another width is another page to find places on: the help that went
      // back under its field is a tooltip again, until it finds no place at this width
      // either. A change of height alone -- a phone's address bar going -- is not.
      if (window.innerWidth !== tipWidth) {
        tipWidth = window.innerWidth;
        Array.prototype.forEach.call(document.querySelectorAll("[data-tooltip-in-flow]"), function (tip) {
          tip.removeAttribute("data-tooltip-in-flow");
        });
        readyTooltips();
      }
      placeTipsSoon();
    });
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", placeTipsSoon);
      window.visualViewport.addEventListener("scroll", placeTipsSoon);
    }
  }

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

  // A select's own control stands for it (#301): the focus is sent straight to the
  // button, not to the select for the button to take it from, which would be two moves
  // and, to a screen reader, two announcements.
  function firstControl(row) {
    var first = row.querySelector("input:not([type=hidden]), select, textarea, button, a[href]");
    var state = first && selectStates.get(first);
    return state ? state.trigger : first;
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
    button.addEventListener("click", function () {
      // The chips are drawn again, so this button is about to be destroyed. If it held
      // focus, hand it to the chip now in its place, else the one before, else the input;
      // a removal by pointer leaves focus where it was (#519).
      var buttons = Array.prototype.slice.call(box.querySelectorAll("[data-labels-chip]"));
      var index = buttons.indexOf(button);
      var held = document.activeElement === button;
      remove();
      if (!held) {
        return;
      }
      var left = box.querySelectorAll("[data-labels-chip]");
      var next = left[index] || left[index - 1] || box.querySelector("[data-labels-input]");
      if (next) {
        next.focus();
      }
    });
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
    // The help and the errors of both controls stay on the page (#522); the chip input is
    // described by them, as the controls it stands in for were.
    var feedback = Array.prototype.map
      .call(box.querySelectorAll("[data-labels-feedback] [id]"), function (node) {
        return node.id;
      })
      .join(" ");
    if (feedback) {
      field.setAttribute("aria-describedby", feedback);
    }
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

  /* ------------------------------------ what was entered under *Narrow* outlives a swap
   *
   * The *Narrow* block is a form of its own, and nothing in it is sent until its button is
   * pressed. It is drawn with the table, so that it holds the question as the server last
   * answered it (#622) -- which also means it is replaced whenever anything live swaps the
   * table: the masthead's box, a header's control, a sort, a page link. The block that
   * came back was the server's, folded and holding the answer's values, so whatever had
   * been typed or chosen in it and not yet applied was thrown away and the block shut over
   * it. Somebody on a phone who set a role and a tag under *Narrow* and then typed in the
   * search box had to start again.
   *
   * So what the block holds that the server did not draw it with is noted as the swap
   * begins and put back in the block that replaces it: whether it was open, each field
   * that had been changed, and the focus with its caret if it was in one of them -- an
   * answer can land while somebody is typing there. Only what was changed: every other
   * field takes the answer's value, which is the reason the block is drawn with the table.
   * And a changed field is left to the answer where the answer itself moved that filter,
   * because its other copy, in the column's header, was used since: that is the later word.
   *
   * Nothing is sent by this and nothing is closed. The block still applies when its button
   * is pressed, and one the server drew open stays open.
   */
  var narrowKept = null;

  // What the server drew a field with, in the terms `autosubmitState` says what it holds.
  function drawnWith(field) {
    if (field.type === "checkbox" || field.type === "radio") {
      return field.defaultChecked ? "on" : "off";
    }
    if (field.tagName !== "SELECT") {
      return field.defaultValue;
    }
    var chosen = Array.prototype.filter.call(field.options, function (option) {
      return option.defaultSelected;
    })[0];
    return (chosen || field.options[0] || { value: "" }).value;
  }

  function caretOf(field) {
    // A date and a number have no caret to ask for, and some engines throw on the asking.
    try {
      return { start: field.selectionStart, end: field.selectionEnd };
    } catch (error) {
      return { start: null, end: null };
    }
  }

  document.addEventListener("htmx:beforeSwap", function (event) {
    var target = event.detail && event.detail.target;
    var block = target && target.querySelector ? target.querySelector("[data-narrow]") : null;
    narrowKept = null;
    if (!block) {
      return;
    }
    var changed = [];
    Array.prototype.forEach.call(block.querySelectorAll("input, select"), function (field) {
      var drawn = drawnWith(field);
      var holds = autosubmitState(field);
      if (field.id && field.type !== "hidden" && holds !== drawn) {
        changed.push({ id: field.id, drawn: drawn, holds: holds });
      }
    });
    var active = document.activeElement;
    var focused = active && active.id && block.contains(active) ? caretOf(active) : null;
    if (focused) {
      focused.id = active.id;
    }
    narrowKept = { open: block.open, changed: changed, focused: focused };
  });

  document.addEventListener("htmx:afterSwap", function () {
    var kept = narrowKept;
    var block = document.querySelector("[data-narrow]");
    narrowKept = null;
    if (!kept || !block) {
      return;
    }
    function fieldOf(id) {
      var field = document.getElementById(id);
      return field && block.contains(field) ? field : null;
    }
    if (kept.open) {
      block.open = true;
    }
    kept.changed.forEach(function (was) {
      var field = fieldOf(was.id);
      if (!field || drawnWith(field) !== was.drawn) {
        return;
      }
      if (field.type === "checkbox" || field.type === "radio") {
        field.checked = was.holds === "on";
      } else {
        // A list that no longer offers what was chosen keeps what the answer drew.
        field.value = was.holds;
        if (field.value !== was.holds) {
          field.value = was.drawn;
        }
        selectChanged(field);
      }
    });
    // htmx puts the focus back as the markup lands, before this runs: into a block the
    // server drew folded it could not, and a value put back just now took the caret to
    // the end of it.
    var field = kept.focused && fieldOf(kept.focused.id);
    if (field) {
      if (document.activeElement !== field) {
        field.focus({ preventScroll: true });
      }
      if (kept.focused.start !== null && field.setSelectionRange) {
        try {
          field.setSelectionRange(kept.focused.start, kept.focused.end);
        } catch (error) {
          // Not a field with a caret.
        }
      }
    }
  });

  // An answer that was not swapped in leaves the block as it was, and what was noted for
  // it must not be put into the answer to some later request.
  document.addEventListener("htmx:afterRequest", function () {
    narrowKept = null;
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
   *
   * The label over the list says "On this page" until the title of the section being
   * read has scrolled behind the masthead, and then says that title, so somebody deep in
   * a long section still sees which one it is (#677). Only from `lg` up, where the list
   * stays in view; the text it puts back is the template's, so nothing English is here.
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
    var label = document.querySelector("[data-section-label]");
    var original = label ? label.textContent : "";
    var wide = window.matchMedia("(min-width: 64rem)");
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
      if (label) {
        var title = current.querySelector("h2, legend");
        var away = wide.matches && title && title.getBoundingClientRect().bottom <= covered;
        var wanted = away ? title.textContent.trim() : original;
        if (label.textContent !== wanted) {
          label.textContent = wanted;
        }
      }
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
    if (wide.addEventListener) {
      wide.addEventListener("change", schedule);
    }
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
    // Only the width: `clean_settings` keeps the stored columns when none arrive, so a drag
    // under an applied saved view does not write the view's columns over the person's own.
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

  // The menus above a table (Views, Columns, the Table / Board switch) send the person back
  // to "here" when they are used. They sit outside what a live filter, sort or page swaps,
  // so the address written into their `next` field is the one the page loaded with; the
  // address bar is the one on screen. Read it as the form is sent, so a view is kept as,
  // and every other action returns to, the table as it is now (#623). A field may name the
  // parameters to leave out: the shape switch drops `view`, which it is about to change.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.querySelectorAll) {
      return;
    }
    Array.prototype.forEach.call(form.querySelectorAll("input[data-next-here]"), function (input) {
      var here = new URL(window.location.href);
      var leave = (input.getAttribute("data-next-here") || "").split(" ").filter(Boolean);
      leave.forEach(function (name) {
        here.searchParams.delete(name);
      });
      input.value = here.pathname + (leave.length ? here.search : window.location.search);
    });
  });

  onContentReady(readyNoticeCollection);

})();
