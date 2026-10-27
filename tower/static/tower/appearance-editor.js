"use strict";
(() => {
  const form = document.getElementById("appearance-form");
  if (!form) return;
  const data = JSON.parse(document.getElementById("wardrobe-data").textContent);
  const items = new Map(data.items.map(item => [item.id, item]));
  const slots = Object.keys(data.values.selections);
  const fields = name => form.elements.namedItem(name);
  const values = () => ({shape: fields("shape").value, color: fields("color").value, theme: fields("theme").value,
    selections: Object.fromEntries(slots.map(slot => [slot, fields(slot).value]))});
  function setValues(value) {
    fields("shape").value = value.shape; fields("color").value = value.color; fields("theme").value = value.theme;
    slots.forEach(slot => { fields(slot).value = value.selections[slot] || ""; });
  }
  function preview() {
    const draft = values();
    const chosen = Object.fromEntries(slots.map(slot => [slot, items.get(draft.selections[slot])]));
    const accessories = {...draft.selections};
    accessories._set_items = [chosen.head?.attachment, chosen.prop?.attachment].filter(Boolean);
    document.getElementById("appearance-avatar").innerHTML = gameAvatarHtml({avatar_shape: draft.shape, avatar_color: draft.color, avatar_accessories: accessories});
    const stage = document.getElementById("appearance-stage");
    stage.dataset.theme = draft.theme;
    stage.style.backgroundImage = chosen.background?.url ? `url("${chosen.background.url}")` : "";
    const decoration = document.getElementById("appearance-decoration");
    decoration.hidden = !chosen.ui_decoration?.url;
    if (chosen.ui_decoration?.url) decoration.style.setProperty("--tower-ui-decoration", `url("${chosen.ui_decoration.url}")`);
    else decoration.style.removeProperty("--tower-ui-decoration");
    for (const [slot, id] of [["ui_loader", "appearance-loader"], ["companion", "appearance-companion"]]) {
      const image = document.getElementById(id);
      const item = chosen[slot];
      image.hidden = !item?.url;
      if (item?.url) image.src = item.url;
      else image.removeAttribute("src");
      image.dataset.animation = item?.animation || "none";
    }
    document.getElementById("appearance-dirty").hidden = JSON.stringify(draft) === JSON.stringify(data.values);
  }
  function section(name) {
    form.querySelectorAll("[data-appearance-panel]").forEach(panel => { panel.hidden = panel.dataset.appearancePanel !== name; });
    form.querySelectorAll("[data-appearance-section]").forEach(link => {
      if (link.dataset.appearanceSection === name) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }
  function trySet(hero, onlyCompanion = false) {
    const selected = data.sets.find(entry => entry.hero_id === hero);
    if (!selected) return;
    Object.entries(selected.items).forEach(([slot, id]) => { if (!onlyCompanion || slot === "companion") fields(slot).value = id; });
    preview();
  }
  form.addEventListener("change", preview);
  form.querySelectorAll("[data-appearance-section]").forEach(link => link.addEventListener("click", event => {
    event.preventDefault(); section(link.dataset.appearanceSection);
  }));
  document.getElementById("appearance-try")?.addEventListener("click", () => trySet(document.getElementById("appearance-set").value));
  document.getElementById("appearance-undo").addEventListener("click", () => { setValues(data.values); preview(); });
  document.getElementById("appearance-reset").addEventListener("click", () => {
    setValues({shape: "square", color: "#3b82f6", theme: "midnight", selections: {}}); preview();
  });
  form.addEventListener("submit", () => { form.dataset.saving = "true"; });
  window.addEventListener("beforeunload", event => {
    if (!form.dataset.saving && !document.getElementById("appearance-dirty").hidden) { event.preventDefault(); event.returnValue = ""; }
  });
  form.addEventListener("error", event => { if (event.target.tagName === "IMG") event.target.hidden = true; }, true);
  section(form.dataset.section);
  setValues(data.draft || data.values);
  trySet(data.try_set, form.dataset.companionOnly === "1");
  preview();
})();
