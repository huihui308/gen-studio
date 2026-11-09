(function () {
  "use strict";

  const form = document.getElementById("image-form");
  if (!form) return;

  const promptInput = document.getElementById("prompt");
  const promptCount = document.getElementById("prompt-count");
  const resolutionInput = document.getElementById("resolution");
  const aspectInput = document.getElementById("aspect");
  const seedInput = document.getElementById("seed");
  const referenceInput = document.getElementById("reference");
  const clearReference = document.getElementById("clear-reference");
  const thumbWrap = document.getElementById("thumb-wrap");
  const thumb = document.getElementById("thumb");
  const submitButton = document.getElementById("image-submit");
  const errorBox = document.getElementById("image-error");
  const idleBox = document.getElementById("image-idle");
  const progressBox = document.getElementById("image-progress");
  const resultBox = document.getElementById("image-result");
  const jobPill = document.getElementById("job-pill");
  const jobStage = document.getElementById("job-stage");
  const jobPercent = document.getElementById("job-percent");
  const progressTrack = document.getElementById("progress-track");
  const progressBar = document.getElementById("progress-bar");
  const jobElapsed = document.getElementById("job-elapsed");
  const jobResolution = document.getElementById("job-resolution");
  const jobReference = document.getElementById("job-reference");
  const jobAspect = document.getElementById("job-aspect");
  const sizePreview = document.getElementById("size-preview");
  const resultImage = document.getElementById("result-image");
  const lightbox = document.getElementById("lightbox");
  const lightboxImage = document.getElementById("lightbox-image");
  const lightboxClose = document.getElementById("lightbox-close");
  const downloadLink = document.getElementById("download-image");
  const downloadHint = document.getElementById("download-hint");
  const resultPrompt = document.getElementById("result-prompt");
  const resultResolution = document.getElementById("result-resolution");
  const resultAspect = document.getElementById("result-aspect");
  const resultSize = document.getElementById("result-size");
  const resultSeed = document.getElementById("result-seed");
  const resultElapsed = document.getElementById("result-elapsed");

  const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
  const CLIENT_TIMEOUT_MS = 11 * 60 * 1000;
  const ALLOWED_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

  let downloadUrl = null;
  let thumbReadToken = 0;
  let lightboxOpen = false;
  let lastFocused = null;
  let elapsedTimer = null;
  let elapsedStart = 0;
  let submitting = false;

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.hidden = true;
  }

  function setBusy(busy) {
    submitButton.disabled = busy;
    submitButton.textContent = busy ? "生成中…" : "生成图片";
  }

  function setPill(text, kind) {
    jobPill.textContent = text;
    jobPill.className = "pill " + kind;
  }

  let previewToken = 0;

  async function refreshSizePreview() {
    if (!sizePreview) return;
    const token = previewToken + 1;
    previewToken = token;
    const params = new URLSearchParams({
      resolution: resolutionInput.value,
      aspect: aspectInput ? aspectInput.value : "",
    });
    try {
      const response = await fetch("/api/image/size?" + params.toString(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (token !== previewToken) return;
      if (response.status === 401) return;
      const data = await response.json().catch(function () { return null; });
      if (!data || !data.ok) {
        sizePreview.textContent = "预计输出：无法计算（" + ((data && data.error) || "请求失败") + "）";
        return;
      }
      const size = String(data.width) + " × " + String(data.height);
      if (data.auto) {
        sizePreview.textContent =
          "预计输出：" + size + "（无参考图时；有参考图则跟随参考图，网格值 " + data.resolution + "）";
      } else {
        sizePreview.textContent = "预计输出：" + size + "（网格值 " + data.resolution + "）";
      }
    } catch (error) {
      if (token !== previewToken) return;
      sizePreview.textContent = "预计输出：无法计算（控制台连接失败）";
    }
  }

  function aspectLabel() {
    const value = aspectInput ? aspectInput.value : "";
    return value ? value : "自动";
  }

  function formatSeconds(total) {
    const value = Math.max(0, Math.round(Number(total) || 0));
    if (value < 60) return value + " 秒";
    const minutes = Math.floor(value / 60);
    const seconds = value % 60;
    return minutes + " 分 " + (seconds < 10 ? "0" : "") + seconds + " 秒";
  }

  function startElapsed() {
    elapsedStart = Date.now();
    if (elapsedTimer) window.clearInterval(elapsedTimer);
    jobElapsed.textContent = "0 秒";
    elapsedTimer = window.setInterval(function () {
      jobElapsed.textContent = formatSeconds((Date.now() - elapsedStart) / 1000);
    }, 1000);
  }

  function stopElapsed() {
    if (elapsedTimer) {
      window.clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }

  function selectedFile() {
    return referenceInput.files && referenceInput.files.length
      ? referenceInput.files[0]
      : null;
  }

  function clearThumb() {
    thumbReadToken += 1;
    thumb.removeAttribute("src");
    thumbWrap.hidden = true;
    clearReference.hidden = true;
  }

  function onReferenceChange() {
    clearError();
    const file = selectedFile();
    clearThumb();
    if (!file) return;
    if (ALLOWED_TYPES.indexOf(file.type) === -1 && !/\.(png|jpe?g|webp|gif)$/i.test(file.name)) {
      showError("参考图格式不支持：请选择 png / jpg / webp / gif 图片。");
      referenceInput.value = "";
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      showError("参考图过大：单张不超过 20 MB。");
      referenceInput.value = "";
      return;
    }
    // 预览必须用 data: URL：CSP 的 img-src 只有 'self' 与 data:，blob: 会被浏览器拦掉。
    const token = thumbReadToken + 1;
    thumbReadToken = token;
    const reader = new FileReader();
    reader.onload = function () {
      if (token !== thumbReadToken || !reader.result) return;
      thumb.src = String(reader.result);
      thumbWrap.hidden = false;
      clearReference.hidden = false;
    };
    reader.onerror = function () {
      showError("读取参考图失败，请重新选择文件。");
      referenceInput.value = "";
      clearThumb();
    };
    reader.readAsDataURL(file);
  }

  function onClearReference() {
    referenceInput.value = "";
    clearThumb();
  }

  function resetResult() {
    closeLightbox();
    if (downloadUrl) {
      URL.revokeObjectURL(downloadUrl);
      downloadUrl = null;
    }
    downloadLink.removeAttribute("href");
    downloadLink.setAttribute("aria-disabled", "true");
    resultImage.removeAttribute("src");
  }

  function showResult(data, prompt, resolution) {
    progressBox.hidden = true;
    resultBox.hidden = false;
    idleBox.hidden = true;

    const image = data.image || {};
    const filename = String(image.filename || "image.png");

    if (image.b64) {
      const binary = window.atob(image.b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      const blob = new Blob([bytes], { type: "image/png" });
      downloadUrl = URL.createObjectURL(blob);
      // 预览走 data: URL（CSP 允许），blob: 只用于 <a download> 下载。
      resultImage.src = "data:image/png;base64," + image.b64;
      downloadLink.href = downloadUrl;
      downloadLink.setAttribute("download", filename);
      downloadLink.setAttribute("aria-disabled", "false");
      downloadHint.textContent = "文件名取自服务返回的 filename：" + filename;
    } else {
      resultImage.removeAttribute("src");
      downloadHint.textContent = "服务未返回 base64 数据，无法预览或下载，请检查服务配置。";
      showError("服务返回了图片信息，但没有 base64 数据，无法预览。");
    }

    resultPrompt.textContent = prompt;
    resultResolution.textContent = String(resolution);
    resultAspect.textContent = aspectLabel();
    resultSize.textContent =
      image.width && image.height ? String(image.width) + " × " + String(image.height) : "—";
    resultSeed.textContent = data.seed !== null && data.seed !== undefined ? String(data.seed) : "随机";
    resultElapsed.textContent = formatSeconds(data.elapsed_s);
  }

  function openLightbox() {
    const src = resultImage.getAttribute("src");
    if (!src || lightboxOpen) return;
    lightboxImage.src = src;
    lightbox.hidden = false;
    lightboxOpen = true;
    document.body.classList.add("lightbox-open");
    lastFocused = document.activeElement;
    lightboxClose.focus();
  }

  function closeLightbox() {
    if (!lightboxOpen) return;
    lightbox.hidden = true;
    lightboxImage.removeAttribute("src");
    lightboxOpen = false;
    document.body.classList.remove("lightbox-open");
    if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
    lastFocused = null;
  }

  function fail(message) {
    stopElapsed();
    setBusy(false);
    setPill("生成失败", "pill-danger");
    jobStage.textContent = "生成失败";
    jobPercent.textContent = "失败";
    showError(message || "生成失败，请稍后重试。");
    progressBox.hidden = false;
  }

  function updatePromptCount() {
    promptCount.textContent = promptInput.value.length + " / 20000";
  }

  async function onSubmit(event) {
    event.preventDefault();
    if (submitting) return;

    const prompt = promptInput.value.trim();
    if (!prompt) {
      showError("请输入提示词后再生成。");
      promptInput.focus();
      return;
    }

    const file = selectedFile();
    if (file && file.size > MAX_UPLOAD_BYTES) {
      showError("参考图过大：单张不超过 20 MB。");
      return;
    }

    clearError();
    resetResult();
    submitting = true;
    setBusy(true);
    setPill("生成中", "pill-warning");
    idleBox.hidden = true;
    resultBox.hidden = true;
    progressBox.hidden = false;
    jobStage.textContent = file ? "正在上传参考图并生成" : "正在生成（纯文生图）";
    jobPercent.textContent = "生成中";
    jobResolution.textContent = resolutionInput.value;
    jobReference.textContent = file ? file.name : "无";
    jobAspect.textContent = aspectLabel();
    progressTrack.classList.add("is-indeterminate");
    progressBar.style.width = "35%";
    progressTrack.setAttribute("aria-valuenow", "0");
    startElapsed();

    const body = new FormData();
    body.append("csrf_token", form.querySelector('input[name="csrf_token"]').value);
    body.append("prompt", prompt);
    body.append("resolution", resolutionInput.value);
    body.append("aspect", aspectInput.value);
    if (seedInput.value.trim() !== "") body.append("seed", seedInput.value.trim());
    if (file) body.append("file", file, file.name);

    const controller = new AbortController();
    const timeout = window.setTimeout(function () {
      controller.abort();
    }, CLIENT_TIMEOUT_MS);

    try {
      const response = await fetch("/api/image/generate", {
        method: "POST",
        headers: { Accept: "application/json" },
        body: body,
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (response.status === 401) {
        window.location.href = "/login?next=%2Fimage";
        return;
      }
      const data = await response.json().catch(function () { return null; });
      if (!response.ok || !data || !data.ok) {
        const message = (data && data.error) || ("生成失败（HTTP " + response.status + "）");
        fail(message);
        return;
      }
      stopElapsed();
      setBusy(false);
      setPill("已完成", "pill-success");
      jobStage.textContent = "生成完成";
      jobPercent.textContent = "100%";
      progressTrack.classList.remove("is-indeterminate");
      progressBar.style.width = "100%";
      progressTrack.setAttribute("aria-valuenow", "100");
      jobElapsed.textContent = formatSeconds(data.elapsed_s);
      showResult(data, prompt, resolutionInput.value);
    } catch (error) {
      if (error && error.name === "AbortError") {
        fail("请求超时：服务在 " + Math.round(CLIENT_TIMEOUT_MS / 60000) + " 分钟内没有返回结果，请检查生图服务后重试。");
      } else {
        fail("无法连接控制台或生图服务，请检查网络与服务状态后重试。");
      }
    } finally {
      window.clearTimeout(timeout);
      submitting = false;
    }
  }

  promptInput.addEventListener("input", updatePromptCount);
  resolutionInput.addEventListener("change", refreshSizePreview);
  if (aspectInput) aspectInput.addEventListener("change", refreshSizePreview);
  referenceInput.addEventListener("change", onReferenceChange);
  clearReference.addEventListener("click", onClearReference);
  form.addEventListener("submit", onSubmit);
  resultImage.addEventListener("click", openLightbox);
  resultImage.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openLightbox();
    }
  });
  lightboxClose.addEventListener("click", closeLightbox);
  lightboxImage.addEventListener("click", closeLightbox);
  lightbox.addEventListener("click", function (event) {
    if (event.target === lightbox) closeLightbox();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && lightboxOpen) {
      event.preventDefault();
      closeLightbox();
    }
  });
  resultImage.addEventListener("error", function () {
    if (resultImage.getAttribute("src")) {
      showError("图片预览加载失败：返回的图片数据可能不完整，请重试。");
    }
  });
  downloadLink.setAttribute("aria-disabled", "true");

  updatePromptCount();
  refreshSizePreview();
}());
