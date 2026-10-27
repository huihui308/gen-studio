/* Model control page: client-side guardrails only; every rule is enforced
   again on the server (missing GPU selection, ownership, port conflicts). */
(function () {
  "use strict";

  function showError(form, message) {
    var existing = form.querySelector(".form-error");
    if (existing) {
      existing.textContent = message;
      return;
    }
    var node = document.createElement("p");
    node.className = "form-error";
    node.setAttribute("role", "alert");
    node.textContent = message;
    var row = form.querySelector(".select-row");
    if (row && row.parentNode) {
      row.parentNode.insertBefore(node, row.nextSibling);
    } else {
      form.appendChild(node);
    }
  }

  function clearError(form) {
    var existing = form.querySelector(".form-error");
    if (existing) {
      existing.remove();
    }
  }

  document.querySelectorAll("form.start-form").forEach(function (form) {
    var select = form.querySelector('select[name="gpu_index"]');
    var submit = form.querySelector('button[type="submit"]');
    if (!select || !submit) {
      return;
    }
    if (select.hasAttribute("disabled")) {
      return;
    }
    form.addEventListener("submit", function (event) {
      if (!select.value) {
        event.preventDefault();
        showError(form, "必须选择一张显卡后才能启动，系统不会自动选择默认显卡。");
        select.focus();
        return;
      }
      clearError(form);
      submit.disabled = true;
      submit.textContent = "正在启动…";
    });
    select.addEventListener("change", function () {
      if (select.value) {
        clearError(form);
      }
    });
  });


  document.querySelectorAll("form.access-form").forEach(function (form) {
    var submit = form.querySelector('button[type="submit"]');
    form.addEventListener("submit", function (event) {
      var checked = form.querySelector('input[name="access_host"]:checked');
      if (!checked) {
        event.preventDefault();
        return;
      }
      var model = form.getAttribute("data-model") || "该模型";
      var current = form.getAttribute("data-current") || "";
      if (checked.value === current) {
        return;
      }
      var message =
        model + " 的模型访问地址将由 " + current + " 改为 " + checked.value + "。\n\n" +
        "保存只写入配置，下次启动该模型时生效；若模型正在运行，需要先停止再重新启动，" +
        "新的监听地址才会生效。\n\n继续保存？";
      if (!window.confirm(message)) {
        event.preventDefault();
        return;
      }
      if (submit) {
        submit.disabled = true;
        submit.textContent = "正在保存…";
      }
    });
  });

  document.querySelectorAll("form.stop-form").forEach(function (form) {
    var submit = form.querySelector('button[type="submit"]');
    if (!submit || submit.hasAttribute("disabled")) {
      return;
    }
    form.addEventListener("submit", function (event) {
      var model = form.getAttribute("data-model") || "该模型";
      var confirmed = window.confirm(
        "确认停止 " + model + "？\n\n停止会终止其进程并释放占用的显卡；" +
        "正在进行的生成任务将被中断。"
      );
      if (!confirmed) {
        event.preventDefault();
        return;
      }
      submit.disabled = true;
      submit.textContent = "正在停止…";
    });
  });
})();
