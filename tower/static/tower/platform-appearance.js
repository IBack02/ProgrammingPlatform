"use strict";
(() => {
  const root = document.getElementById("tower-personal-strip");
  if (!root) return;
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
})();
