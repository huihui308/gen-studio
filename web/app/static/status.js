/* Status page: refresh the server-rendered snapshot every 5 seconds.
   The snapshot is produced by the same collector as the initial page, so the
   browser never computes or fabricates metrics. */
(function () {
  "use strict";

  var REFRESH_MS = 5000;
  var root = document.getElementById("status-root");
  var pollState = document.getElementById("poll-state");
  if (!root) {
    return;
  }

  var inFlight = false;
  var timer = null;
  var activeTab = null;

  function tabButtons() {
    return Array.prototype.slice.call(root.querySelectorAll("[data-status-tab]"));
  }

  function applyTab() {
    var buttons = tabButtons();
    if (!buttons.length) {
      return;
    }
    var found = buttons.some(function (button) {
      return button.getAttribute("data-status-tab") === activeTab;
    });
    if (!found) {
      activeTab = buttons[0].getAttribute("data-status-tab");
    }
    buttons.forEach(function (button) {
      var selected = button.getAttribute("data-status-tab") === activeTab;
      button.setAttribute("aria-selected", selected ? "true" : "false");
      button.setAttribute("tabindex", selected ? "0" : "-1");
    });
    root.querySelectorAll("[data-model-panel]").forEach(function (panel) {
      panel.hidden = panel.getAttribute("data-model-panel") !== activeTab;
    });
  }

  root.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-status-tab]") : null;
    if (!button || !root.contains(button)) {
      return;
    }
    activeTab = button.getAttribute("data-status-tab");
    applyTab();
  });

  root.addEventListener("keydown", function (event) {
    var button = event.target.closest ? event.target.closest("[data-status-tab]") : null;
    if (!button) {
      return;
    }
    var buttons = tabButtons();
    var index = buttons.indexOf(button);
    if (index < 0) {
      return;
    }
    var next = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = buttons[(index + 1) % buttons.length];
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = buttons[(index - 1 + buttons.length) % buttons.length];
    } else if (event.key === "Home") {
      next = buttons[0];
    } else if (event.key === "End") {
      next = buttons[buttons.length - 1];
    }
    if (next) {
      event.preventDefault();
      activeTab = next.getAttribute("data-status-tab");
      applyTab();
      next.focus();
    }
  });

  function setPollState(kind, text) {
    if (!pollState) {
      return;
    }
    pollState.setAttribute("data-state", kind);
    pollState.textContent = text;
  }

  function localizeTimes(scope) {
    scope.querySelectorAll("time[datetime]").forEach(function (node) {
      var iso = node.getAttribute("datetime");
      if (!iso) {
        return;
      }
      var parsed = new Date(iso);
      if (isNaN(parsed.getTime())) {
        return;
      }
      try {
        node.textContent = parsed.toLocaleString("zh-CN", {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false
        });
        node.setAttribute("title", parsed.toISOString());
      } catch (err) {
        /* keep the server-rendered UTC text */
      }
    });
  }

  function openDetailsIndexes(scope) {
    var indexes = [];
    scope.querySelectorAll("details").forEach(function (node, index) {
      if (node.open) {
        indexes.push(index);
      }
    });
    return indexes;
  }

  function restoreDetails(scope, indexes) {
    var nodes = scope.querySelectorAll("details");
    indexes.forEach(function (index) {
      if (nodes[index]) {
        nodes[index].open = true;
      }
    });
  }

  function refresh() {
    if (inFlight) {
      return;
    }
    inFlight = true;
    var opened = openDetailsIndexes(root);
    window
      .fetch("/status/partial", {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "text/html" },
        cache: "no-store"
      })
      .then(function (response) {
        if (response.status === 401) {
          window.location.assign("/login?next=%2Fstatus");
          throw new Error("unauthenticated");
        }
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.text();
      })
      .then(function (html) {
        root.innerHTML = html;
        applyTab();
        restoreDetails(root, opened);
        localizeTimes(root);
        setPollState("ok", "已同步 " + new Date().toLocaleTimeString("zh-CN", { hour12: false }));
      })
      .catch(function (err) {
        if (err && err.message === "unauthenticated") {
          return;
        }
        setPollState("error", "刷新失败，显示上次结果");
      })
      .then(function () {
        inFlight = false;
      });
  }

  function schedule() {
    if (timer) {
      window.clearTimeout(timer);
    }
    timer = window.setTimeout(function () {
      if (document.hidden) {
        schedule();
        return;
      }
      refresh();
      schedule();
    }, REFRESH_MS);
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) {
      refresh();
      schedule();
    }
  });

  localizeTimes(root);
  applyTab();
  refresh();
  schedule();
})();
