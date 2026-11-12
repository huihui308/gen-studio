/* API Keys page: copy buttons, description search and confirm dialogs.
   Every mutation is a plain form POST re-checked by the server; this file
   only improves ergonomics, and the page stays usable without it.
   Note: over plain http://<lan> the context is not secure, so
   navigator.clipboard is unavailable — execCommand('copy') is the fallback. */
(function () {
  "use strict";

  function fallbackCopy(text) {
    var helper = document.createElement("textarea");
    helper.className = "copy-helper";
    helper.setAttribute("readonly", "");
    helper.value = text;
    document.body.appendChild(helper);
    helper.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (err) {
      ok = false;
    }
    helper.remove();
    return ok;
  }

  function flashCopied(button) {
    if (button.getAttribute("data-copied") === "1") {
      return;
    }
    var original = button.textContent;
    button.setAttribute("data-copied", "1");
    button.textContent = "已复制";
    window.setTimeout(function () {
      button.textContent = original;
      button.removeAttribute("data-copied");
    }, 1500);
  }

  function copyWithFeedback(button, text) {
    function done(ok) {
      if (ok) {
        flashCopied(button);
      }
    }
    if (navigator.clipboard && window.isSecureContext) {
      var result = null;
      try {
        result = navigator.clipboard.writeText(text);
      } catch (err) {
        result = null;
      }
      if (result && typeof result.then === "function") {
        result.then(
          function () {
            done(true);
          },
          function () {
            done(fallbackCopy(text));
          }
        );
        return;
      }
    }
    done(fallbackCopy(text));
  }

  document.querySelectorAll("[data-copy-target]").forEach(function (button) {
    button.addEventListener("click", function () {
      var target = document.querySelector(button.getAttribute("data-copy-target"));
      if (!target) {
        return;
      }
      copyWithFeedback(button, (target.textContent || "").trim());
    });
  });

  document.querySelectorAll("[data-copy-key]").forEach(function (button) {
    button.addEventListener("click", function () {
      var row = button.closest("[data-key]");
      if (!row) {
        return;
      }
      copyWithFeedback(button, row.getAttribute("data-key") || "");
    });
  });

  var search = document.getElementById("key-search");
  if (search) {
    var rows = document.querySelectorAll("#api-keys-body tr[data-key-row]");
    search.addEventListener("input", function () {
      var query = search.value.trim().toLowerCase();
      rows.forEach(function (row) {
        var cell = row.querySelector("[data-key-desc]");
        var text = cell ? (cell.textContent || "").toLowerCase() : "";
        row.hidden = query !== "" && text.indexOf(query) === -1;
      });
    });
  }

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm") || "确认执行该操作？")) {
        event.preventDefault();
      }
    });
  });
})();
