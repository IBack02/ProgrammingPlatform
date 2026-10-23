"use strict";
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-rarity-filter]");
  if (!button) return;
  const rarity = button.dataset.rarityFilter;
  document.querySelectorAll("[data-rarity-filter]").forEach((item) => {
    item.setAttribute("aria-pressed", String(item === button));
  });
  let visible = 0;
  document.querySelectorAll("[data-card-rarity]").forEach((card) => {
    card.hidden = rarity !== "all" && card.dataset.cardRarity !== rarity;
    if (!card.hidden) visible += 1;
  });
  document.getElementById("visibleCardCount").textContent = String(visible);
});
document.addEventListener("error", (event) => {
  if (event.target.matches?.(".collection-card__portrait")) {
    event.target.hidden = true;
  }
}, true);
