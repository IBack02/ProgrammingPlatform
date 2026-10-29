"use strict";
(() => {
  const root = document.getElementById("tower-personal-strip");
  if (!root) return;
  root.addEventListener("error", event => { if (event.target.tagName === "IMG") event.target.hidden = true; }, true);
  const companion = document.getElementById("tower-companion");
  if (companion) {
    document.getElementById("companion-close").addEventListener("click", () => {
      companion.open = false;
      document.getElementById("companion-toggle").focus({preventScroll:true});
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && companion.open) {
        companion.open = false;
        document.getElementById("companion-toggle").focus({preventScroll:true});
      }
    });
    document.addEventListener("click", event => {
      if (companion.open && !companion.contains(event.target)) companion.open = false;
    });
    const quiet = () => {
      // Exams and games never expand a companion over the student's work.
      const exam = location.pathname === "/student/exams/";
      const game = document.body.classList.contains("module-bg-game");
      companion.hidden = exam || game;
      if (companion.hidden) companion.open = false;
    };
    new MutationObserver(quiet).observe(document.body, {attributes: true, attributeFilter: ["class"]});
    quiet();
  }
})();
