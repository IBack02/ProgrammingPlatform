"use strict";
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[character]));
}
function filterJournal() {
  const rarity = document.querySelector('[data-rarity-filter][aria-pressed="true"]')?.dataset.rarityFilter || "all";
  const state = document.querySelector('[data-state-filter][aria-pressed="true"]')?.dataset.stateFilter || "all";
  let visible = 0;
  const rows = document.querySelectorAll("[data-collection-rarity]");
  if (rows.length) {
    rows.forEach(row => {
      row.hidden = (rarity !== "all" && row.dataset.collectionRarity !== rarity) || (state !== "all" && row.dataset.collectionState !== state);
      if (!row.hidden) visible += 1;
    });
  } else {
    document.querySelectorAll("[data-card-rarity]").forEach(card => {
      card.hidden = rarity !== "all" && card.dataset.cardRarity !== rarity;
      if (!card.hidden) visible += 1;
    });
  }
  const count = document.getElementById("visibleCardCount");
  if (count) count.textContent = String(visible);
  const empty = document.querySelector(".journal-filter-empty");
  if (empty) empty.hidden = visible > 0;
}
document.addEventListener("click", event => {
  const button = event.target.closest("[data-rarity-filter], [data-state-filter]");
  if (!button) return;
  const selector = button.hasAttribute("data-rarity-filter") ? "[data-rarity-filter]" : "[data-state-filter]";
  document.querySelectorAll(selector).forEach(item => item.setAttribute("aria-pressed", String(item === button)));
  filterJournal();
});
document.addEventListener("error", event => {
  if (event.target.matches?.(".collection-card__portrait")) event.target.hidden = true;
}, true);
document.querySelectorAll("[data-purchase-form]").forEach(form => {
  form.querySelectorAll("button").forEach(button => { button.dataset.initialDisabled = String(button.disabled); });
  form.addEventListener("submit", event => {
    if (form.dataset.submitting) { event.preventDefault(); return; }
    if (!form.checkValidity()) return;
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    form.querySelectorAll("button").forEach(button => { button.disabled = true; });
  });
});
window.addEventListener("pageshow", () => {
  document.querySelectorAll("[data-purchase-form]").forEach(form => {
    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");
    form.querySelectorAll("button").forEach(button => { button.disabled = button.dataset.initialDisabled === "true"; });
  });
});
const dialog = document.getElementById("hero-detail");
if (dialog?.showModal) { dialog.close(); dialog.showModal(); }
const quantity = document.querySelector("[data-shard-unit]");
quantity?.addEventListener("input", () => {
  const total = document.querySelector("[data-shard-total]");
  if (total) total.textContent = String(Math.max(0, Number(quantity.value) || 0) * Number(quantity.dataset.shardUnit));
});
const avatarForm = document.getElementById("avatar-form");
function previewAvatar() {
  if (!avatarForm || typeof gameAvatarHtml !== "function") return;
  const values = new FormData(avatarForm);
  const avatar = {avatar_shape: values.get("shape"), avatar_color: values.get("color"), avatar_accessories: {head: values.get("head"), glasses: values.get("glasses"), mustache: values.get("mustache")}};
  const preview = document.getElementById("journal-avatar-preview");
  preview.innerHTML = gameAvatarHtml(avatar);
  animateGameAvatars(preview);
  document.documentElement.dataset.journalTheme = values.get("theme");
}
avatarForm?.addEventListener("change", previewAvatar);
previewAvatar();
