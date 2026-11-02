"use strict";
(() => {
  const stage = document.querySelector("[data-pack-reveal]");
  const summary = document.querySelector("[data-pack-summary]");
  if (!stage || !summary) return;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const key = `tower:pack-reveal:${stage.dataset.receipt}`;
  const cards = [...stage.querySelectorAll("[data-reveal-card]")];
  const booster = stage.querySelector(".nis-booster");
  const boosterStage = stage.querySelector(".booster-stage");
  const grid = stage.querySelector(".reveal-card-grid");
  const status = stage.querySelector(".pack-reveal-status");
  const finishButton = stage.querySelector("[data-reveal-finish]");
  const repeat = document.querySelector("[data-reveal-repeat]");
  let state = {torn: false, revealed: [], complete: false};
  let tearing = false, tearTimer, flipTimer;
  try {
    const saved = JSON.parse(sessionStorage.getItem(key));
    if (saved && typeof saved === "object") state = {torn: saved.torn === true,
      complete: saved.complete === true, revealed: Array.isArray(saved.revealed) ? saved.revealed.filter(n => Number.isInteger(n) && n >= 0 && n < cards.length) : []};
  } catch (_) { /* Browsing storage is optional; the server receipt is permanent. */ }
  function save() { try { sessionStorage.setItem(key, JSON.stringify(state)); } catch (_) {} }
  function paint() {
    cards.forEach((card, index) => {
      const revealed = state.revealed.includes(index);
      card.classList.toggle("is-revealed", revealed);
      const button = card.querySelector(".reveal-flip");
      button.setAttribute("aria-pressed", String(revealed));
      if (revealed) button.setAttribute("aria-label", button.dataset.cardName);
      card.querySelector(".reveal-front").setAttribute("aria-hidden", String(!revealed));
      card.querySelector(".reveal-card-copy").hidden = !revealed;
    });
    finishButton.hidden = state.revealed.length !== cards.length;
    status.textContent = state.revealed.length === cards.length ? stage.dataset.doneLabel : stage.dataset.cardsLabel;
  }
  function showCards(focus) {
    boosterStage.hidden = true;
    grid.hidden = false;
    cards.forEach(card => { card.style.animation = "none"; });
    const gridBox = grid.getBoundingClientRect();
    const center = (gridBox.left + gridBox.right) / 2;
    cards.forEach(card => {
      const box = card.getBoundingClientRect();
      card.style.setProperty("--deal-shift", `${center - (box.left + box.right) / 2}px`);
    });
    cards.forEach(card => { card.style.removeProperty("animation"); });
    paint();
    if (focus) (cards.find((_, i) => !state.revealed.includes(i)) || cards[0])?.querySelector("button").focus({preventScroll: true});
  }
  function complete() {
    clearTimeout(tearTimer);
    clearTimeout(flipTimer);
    state = {torn: true, revealed: cards.map((_, i) => i), complete: true};
    save();
    stage.hidden = true;
    summary.hidden = false;
    repeat.hidden = false;
    repeat.focus({preventScroll: true});
  }
  booster.addEventListener("click", () => {
    if (tearing || state.torn) return;
    tearing = true;
    state.torn = true;
    save();
    booster.disabled = true;
    boosterStage.classList.add("is-tearing");
    status.textContent = stage.dataset.cardsLabel;
    tearTimer = setTimeout(() => { tearing = false; showCards(true); }, reduced ? 0 : 900);
  });
  cards.forEach((card, index) => card.querySelector("button").addEventListener("click", () => {
    if (state.revealed.includes(index)) return;
    state.revealed.push(index);
    save();
    paint();
    if (state.revealed.length === cards.length) flipTimer = setTimeout(() => finishButton.focus({preventScroll: true}), reduced ? 0 : 700);
  }));
  stage.querySelector("[data-reveal-skip]").addEventListener("click", complete);
  finishButton.addEventListener("click", complete);
  repeat.addEventListener("click", () => {
    state = {torn: false, revealed: [], complete: false};
    tearing = false;
    save();
    cards.forEach((card, i) => card.querySelector("button").setAttribute("aria-label", `${stage.dataset.cardsLabel} ${i + 1}`));
    paint();
    status.textContent = stage.dataset.sealedLabel;
    grid.hidden = true;
    boosterStage.hidden = false;
    boosterStage.classList.remove("is-tearing");
    booster.disabled = false;
    stage.hidden = false;
    summary.hidden = true;
    repeat.hidden = true;
    booster.focus({preventScroll: true});
  });
  if (state.complete) { repeat.hidden = false; return; }
  stage.hidden = false;
  summary.hidden = true;
  // Start portrait requests before a card is turned, without affecting its result.
  stage.querySelectorAll("img").forEach(img => { img.loading = "eager"; });
  if (state.torn) showCards(false);
})();
