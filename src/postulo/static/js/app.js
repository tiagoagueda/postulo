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
})();
