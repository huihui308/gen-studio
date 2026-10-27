(function () {
  "use strict";

  const form = document.getElementById("generate-form");
  if (!form) return;

  const promptInput = document.getElementById("prompt");
  const promptCount = document.getElementById("prompt-count");
  const resolutionInput = document.getElementById("resolution");
  const durationInput = document.getElementById("duration");
  const submitButton = document.getElementById("generate-submit");
  const errorBox = document.getElementById("generate-error");
  const idleBox = document.getElementById("generate-idle");
  const progressBox = document.getElementById("generate-progress");
  const resultBox = document.getElementById("generate-result");
  const jobPill = document.getElementById("job-pill");
  const jobStage = document.getElementById("job-stage");
  const jobPercent = document.getElementById("job-percent");
  const progressTrack = document.getElementById("progress-track");
  const progressBar = document.getElementById("progress-bar");
  const jobElapsed = document.getElementById("job-elapsed");
  const jobResolution = document.getElementById("job-resolution");
  const jobDuration = document.getElementById("job-duration");
  const resultVideo = document.getElementById("result-video");
  const resultPrompt = document.getElementById("result-prompt");
  const resultResolution = document.getElementById("result-resolution");
  const resultDuration = document.getElementById("result-duration");
  const resultElapsed = document.getElementById("result-elapsed");

  let activeJobId = null;
  let pollTimer = null;
  let elapsedTimer = null;
  let elapsedBase = 0;
  let elapsedAt = 0;
  let pollFailures = 0;
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
    submitButton.textContent = busy ? "生成中…" : "生成视频";
  }

  function setPill(text, kind) {
    jobPill.textContent = text;
    jobPill.className = "pill " + kind;
  }

  function formatSeconds(total) {
    const value = Math.max(0, Math.round(Number(total) || 0));
    if (value < 60) return value + " 秒";
    const minutes = Math.floor(value / 60);
    const seconds = value % 60;
    return minutes + " 分 " + (seconds < 10 ? "0" : "") + seconds + " 秒";
  }

  function setProgress(percent) {
    progressTrack.classList.remove("is-indeterminate");
    if (percent === null || percent === undefined || Number.isNaN(Number(percent))) {
      progressTrack.classList.add("is-indeterminate");
      progressBar.style.width = "35%";
      progressTrack.setAttribute("aria-valuenow", "0");
      jobPercent.textContent = "无百分比";
      return;
    }
    const value = Math.max(0, Math.min(100, Math.round(Number(percent))));
    progressBar.style.width = value + "%";
    progressTrack.setAttribute("aria-valuenow", String(value));
    jobPercent.textContent = value + "%";
  }

  function startElapsed(seconds) {
    elapsedBase = Math.max(0, Number(seconds) || 0);
    elapsedAt = Date.now();
    if (elapsedTimer) window.clearInterval(elapsedTimer);
    elapsedTimer = window.setInterval(function () {
      const current = elapsedBase + (Date.now() - elapsedAt) / 1000;
      jobElapsed.textContent = formatSeconds(current);
    }, 1000);
  }

  function stopTimers() {
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (elapsedTimer) {
      window.clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }

  function showResult(job) {
    progressBox.hidden = true;
    resultBox.hidden = false;
    resultPrompt.textContent = job.prompt;
    resultResolution.textContent = job.resolution + "（短边）";
    resultDuration.textContent = job.duration + " 秒";
    resultElapsed.textContent = formatSeconds(job.elapsed_seconds);
    resultVideo.src = job.video_url;
    resultVideo.load();
  }

  function handleFailure(job, message) {
    stopTimers();
    setBusy(false);
    setPill("生成失败", "pill-danger");
    jobStage.textContent = "生成失败";
    jobPercent.textContent = "失败";
    progressTrack.classList.remove("is-indeterminate");
    showError(message || job.error || "生成失败，请检查 H3 服务后重试。");
    if (progressBox.hidden) progressBox.hidden = false;
  }

  function applyJob(job) {
    if (!job || typeof job !== "object") return;
    const status = String(job.status || "");
    const resolution = job.resolution ? job.resolution + "（短边）" : "—";
    const duration = job.duration ? job.duration + " 秒" : "—";

    jobResolution.textContent = resolution;
    jobDuration.textContent = duration;
    startElapsed(job.elapsed_seconds);

    if (status === "queued") {
      setBusy(true);
      setPill("生成中", "pill-warning");
      jobStage.textContent = "H3 已接收（排队或生成中，不返回百分比）";
      setProgress(null);
      progressBox.hidden = false;
      resultBox.hidden = true;
      idleBox.hidden = true;
      pollFailures = 0;
      schedulePoll();
      return;
    }

    if (status === "in_progress") {
      setBusy(true);
      setPill("生成中", "pill-warning");
      jobStage.textContent = job.stage || "H3 正在生成";
      setProgress(job.progress);
      progressBox.hidden = false;
      resultBox.hidden = true;
      idleBox.hidden = true;
      pollFailures = 0;
      schedulePoll();
      return;
    }

    if (status === "completed") {
      stopTimers();
      setBusy(false);
      setPill("已完成", "pill-success");
      jobStage.textContent = "生成完成";
      setProgress(100);
      showResult(job);
      return;
    }

    if (status === "failed") {
      setPill("生成失败", "pill-danger");
      handleFailure(job, job.error);
      return;
    }

    // Unknown status: keep polling, but show the raw stage rather than guessing.
    setPill("处理中", "pill-warning");
    jobStage.textContent = job.stage || status || "处理中";
    setProgress(job.progress);
    progressBox.hidden = false;
    resultBox.hidden = true;
    idleBox.hidden = true;
    schedulePoll();
  }

  function schedulePoll() {
    if (!activeJobId || pollTimer) return;
    pollTimer = window.setTimeout(pollJob, 2000);
  }

  async function pollJob() {
    pollTimer = null;
    if (!activeJobId) return;
    try {
      const response = await fetch("/api/generate/" + encodeURIComponent(activeJobId), {
        method: "GET",
        headers: { "Accept": "application/json" },
        credentials: "same-origin",
      });
      if (response.status === 401) {
        window.location.href = "/login?next=%2Fgenerate";
        return;
      }
      const data = await response.json().catch(function () { return null; });
      if (response.status === 404) {
        handleFailure({}, "生成任务不存在或已过期，请刷新页面后重新提交。");
        return;
      }
      if (!response.ok || !data || !data.job) {
        pollFailures += 1;
        if (pollFailures <= 3) {
          schedulePoll();
          return;
        }
        handleFailure({}, "无法获取生成进度，请刷新页面后重试。");
        return;
      }
      pollFailures = 0;
      applyJob(data.job);
    } catch (error) {
      pollFailures += 1;
      if (pollFailures <= 3) {
        schedulePoll();
        return;
      }
      handleFailure({}, "无法获取生成进度：控制台连接失败，请刷新页面后重试。");
    }
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

    clearError();
    submitting = true;
    activeJobId = null;
    stopTimers();
    pollFailures = 0;
    setBusy(true);
    setPill("提交中", "pill-warning");
    idleBox.hidden = true;
    resultBox.hidden = true;
    progressBox.hidden = false;
    jobStage.textContent = "正在提交给 H3";
    jobElapsed.textContent = "0 秒";
    jobResolution.textContent = resolutionInput.value + "（短边）";
    jobDuration.textContent = durationInput.value + " 秒";
    setProgress(null);

    const body = new URLSearchParams({
      csrf_token: form.querySelector('input[name="csrf_token"]').value,
      prompt: prompt,
      resolution: resolutionInput.value,
      duration: durationInput.value,
    });

    try {
      const response = await fetch("/api/generate", {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
        body: body.toString(),
        credentials: "same-origin",
      });
      if (response.status === 401) {
        window.location.href = "/login?next=%2Fgenerate";
        return;
      }
      const data = await response.json().catch(function () { return null; });
      if (!response.ok || !data || !data.job) {
        const message = (data && data.error) || ("提交失败（HTTP " + response.status + "）");
        showError(message);
        setPill("提交失败", "pill-danger");
        setBusy(false);
        jobStage.textContent = "提交失败";
        jobPercent.textContent = "失败";
        return;
      }
      activeJobId = data.job.id;
      startElapsed(data.job.elapsed_seconds);
      applyJob(data.job);
    } catch (error) {
      showError("提交失败：无法连接控制台，请检查网络后重试。");
      setPill("提交失败", "pill-danger");
      setBusy(false);
      jobStage.textContent = "提交失败";
      jobPercent.textContent = "失败";
    } finally {
      submitting = false;
    }
  }

  promptInput.addEventListener("input", updatePromptCount);
  form.addEventListener("submit", onSubmit);
  resultVideo.addEventListener("error", function () {
    showError("视频加载失败：文件可能尚未写入完成，请稍后重试。");
  });

  updatePromptCount();
}());
