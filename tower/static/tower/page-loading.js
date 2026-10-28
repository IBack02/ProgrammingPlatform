"use strict";
(() => {
  if (window.studentPageLoading) return;
  const config = document.currentScript;
  if (!config || document.documentElement.dataset.themeRole === "teacher") return;
  let indicator;
  let pending = 1;
  let showTimer;
  const afterPaint = callback => requestAnimationFrame(() => requestAnimationFrame(callback));
  function render() {
    if (!indicator) return;
    clearTimeout(showTimer);
    if (!pending) indicator.hidden = true;
    else if (indicator.hidden) showTimer = setTimeout(() => { indicator.hidden = pending === 0; }, 120);
  }
  function begin() {
    pending += 1;
    render();
    let done = false;
    const timeout = setTimeout(finish, 30000);
    function finish() {
      if (done) return;
      done = true;
      clearTimeout(timeout);
      pending = Math.max(0, pending - 1);
      render();
    }
    return finish;
  }
  window.studentPageLoading = {begin};

  // Install in the head before inline page scripts start their first requests.
  const originalFetch = window.fetch;
  window.fetch = async function(input, options) {
    let foreground = false;
    try {
      const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, location.href);
      const method = (options?.method || input?.method || "GET").toUpperCase();
      foreground = url.origin === location.origin && method === "GET" && (
        /^\/api\/student\/(?:active-session|dashboard|task\/\d+|theory-module\/\d+|theory-quiz\/\d+|game-module\/\d+|exams(?:\/\d+)?|peer-sessions|peer-assignments\/\d+)\/?$/.test(url.pathname));
    } catch (_) { /* Other fetch inputs retain native behavior. */ }
    const finish = foreground ? begin() : null;
    try {
      const response = await originalFetch.call(this, input, options);
      // Wait for the response body, then give the caller time to render its DOM.
      if (finish) response.clone().arrayBuffer().then(() => afterPaint(finish), finish);
      return response;
    } catch (error) {
      if (finish) finish();
      throw error;
    }
  };

  function mount() {
    indicator = document.createElement("div");
    indicator.id = "student-page-loading";
    indicator.className = "student-page-loading";
    indicator.hidden = true;
    indicator.setAttribute("role", "status");
    indicator.setAttribute("aria-live", "polite");
    const fallback = document.createElement("span");
    fallback.className = "student-page-loading__default";
    fallback.setAttribute("aria-hidden", "true");
    indicator.append(fallback);
    if (config.dataset.image) {
      const image = document.createElement("img");
      image.alt = "";
      image.hidden = true;
      image.dataset.animation = config.dataset.animation || "";
      image.addEventListener("load", () => { fallback.hidden = true; image.hidden = false; });
      image.addEventListener("error", () => { fallback.hidden = false; image.hidden = true; });
      image.src = config.dataset.image;
      indicator.append(image);
    }
    const label = document.createElement("span");
    label.textContent = config.dataset.label || "Загрузка…";
    indicator.append(label);
    document.body.append(indicator);
    render();

    // Lazy illustrations start loading only on approach to the viewport.
    const seen = new WeakSet();
    const waiting = new Map();
    const visibleImages = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const image = entry.target;
      visibleImages.unobserve(image);
      if (image.complete) return;
      const release = begin();
      const finish = () => { waiting.delete(image); release(); };
      waiting.set(image, finish);
      image.addEventListener("load", finish, {once: true});
      image.addEventListener("error", finish, {once: true});
      if (image.complete) finish();
    }));
    function images() {
      waiting.forEach((finish, image) => { if (!image.isConnected) finish(); });
      document.querySelectorAll("main img, .content img").forEach(image => {
        if (seen.has(image) || image.closest("#student-page-loading")) return;
        seen.add(image);
        if (!image.complete) visibleImages.observe(image);
      });
    }
    images();
    let scheduled = false;
    new MutationObserver(() => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => { scheduled = false; images(); });
    }).observe(document.body, {subtree: true, childList: true});
  }
  let pageDone = false;
  function ready() {
    if (pageDone) return;
    pageDone = true;
    pending = Math.max(0, pending - 1);
    render();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, {once: true});
  else mount();
  if (document.readyState === "complete") afterPaint(ready);
  else window.addEventListener("load", () => afterPaint(ready), {once: true});
  setTimeout(ready, 10000);
})();
