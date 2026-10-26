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
  refresh();
  schedule();
})();
