"use strict";
(() => {
  const root = document.getElementById("tower-personal-strip");
  if (!root) return;
  const equipped = JSON.parse(document.getElementById("tower-equipped-data").textContent);
  if (equipped.background?.url) {
    const image = new Image();
    image.onload = () => { document.body.style.setProperty("--tower-background", `url("${equipped.background.url}")`); document.body.classList.add("tower-custom-background"); };
    image.src = equipped.background.url;
  }
  root.addEventListener("error", event => { if (event.target.tagName === "IMG") event.target.hidden = true; }, true);
  const companion = document.getElementById("tower-companion");
  const key = `tower-companion-open:${root.dataset.player}`;
  if (companion) {
    try { companion.open = localStorage.getItem(key) === "true"; } catch (_) { /* Storage is optional. */ }
    companion.addEventListener("toggle", () => { try { localStorage.setItem(key, String(companion.open)); } catch (_) {} });
    const quiet = () => {
      // Exams and games never expand a companion over the student's work.
      const exam = location.pathname === "/student/exams/";
      const game = document.body.classList.contains("module-bg-game");
      companion.hidden = exam || game;
    };
    new MutationObserver(quiet).observe(document.body, {attributes: true, attributeFilter: ["class"]});
    quiet();
  }
  const status = document.getElementById("tower-request-status");
  const originalFetch = window.fetch;
  let pending = 0;
  // Ignore background polling and timed games; only foreground learning requests use the loader.
  window.fetch = async function(input, options) {
    let foreground = false;
    try {
      const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, location.href);
      foreground = url.origin === location.origin && /^\/api\/student\/(?:task\/\d+(?:\/submit|\/hint\/\d+)?|theory-module\/\d+|theory-quiz\/\d+(?:\/submit)?|dashboard)\/?$/.test(url.pathname);
    } catch (_) {}
    if (foreground) { pending += 1; status.hidden = false; }
    try { return await originalFetch.call(this, input, options); }
    finally { if (foreground) { pending -= 1; status.hidden = pending === 0; } }
  };
})();
