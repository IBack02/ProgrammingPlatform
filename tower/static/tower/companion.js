"use strict";
(() => {
  const panel = document.getElementById("tower-companion");
  if (!panel || panel.dataset.generationEnabled !== "true") return;
  const form = document.getElementById("companion-generate-form");
  const button = document.getElementById("companion-generate");
  const label = document.getElementById("companion-state");
  const bubble = document.getElementById("companion-bubble");
  const badge = document.getElementById("companion-ready");
  const thinking = document.getElementById("companion-thinking");
  const csrf = form.querySelector('[name="csrfmiddlewaretoken"]').value;
  let timer, busy = false, current = null, bubbleVisible = false, acknowledged = new Set(), acknowledging = new Set();
  const surface = () => location.pathname === "/student/exams/" ? "exam" : document.body.classList.contains("module-bg-game") ? "game" : "learning";
  const visible = () => bubbleVisible && !panel.hidden && panel.open && document.visibilityState === "visible" && surface() === "learning";
  async function api(path, payload) {
    const response = await fetch(`/api/tower/companion/${path}/`, payload ? {
      method:"POST", credentials:"same-origin", headers:{"Content-Type":"application/json", "X-CSRFToken":csrf},
      body:JSON.stringify({...payload, surface:surface()})
    } : {credentials:"same-origin", cache:"no-store"});
    if (!response.ok) throw new Error("request_failed");
    const data = await response.json();
    if (!data.ok) throw new Error("request_failed");
    return data;
  }
  async function acknowledge() {
    if (!visible() || !current || acknowledged.has(current.id) || acknowledging.has(current.id)) return;
    const id = current.id;
    acknowledging.add(id);
    try { await api("acknowledge", {message_id:id}); acknowledged.add(id); }
    catch (_) { /* Keep the saved reply for a later acknowledgement. */ }
    finally { acknowledging.delete(id); }
  }
  function render(data) {
    clearTimeout(timer);
    current = data.message || null;
    const quiet = data.state === "quiet" || data.state === "inactive" || surface() !== "learning";
    panel.hidden = quiet;
    badge.hidden = data.state !== "ready" || quiet;
    button.disabled = data.state !== "ready" || busy;
    thinking.hidden = data.state !== "generating" && !busy;
    form.setAttribute("aria-busy", String(!thinking.hidden));
    bubble.hidden = !current || quiet;
    if (current) {
      document.getElementById("companion-text").textContent = current.text;
      document.getElementById("companion-sender").textContent = current.hero_name;
      const link = document.getElementById("companion-link");
      if (current.url.startsWith("/student/")) link.href = current.url;
      else link.removeAttribute("href");
      if (current.delivered) acknowledged.add(current.id);
    }
    const states = {ready:"ready", empty:"empty", generating:"generating", cooldown:"cooldown", retry_wait:"retry", unavailable:"unavailable"};
    label.textContent = quiet ? "" : label.dataset[states[data.state] || "unavailable"];
    if (data.next_available_at && (data.state === "cooldown" || data.state === "retry_wait")) {
      const at = new Date(data.next_available_at);
      if (!Number.isNaN(at.valueOf())) label.textContent += ` ${at.toLocaleTimeString(document.documentElement.lang, {hour:"2-digit",minute:"2-digit"})}`;
    }
    requestAnimationFrame(acknowledge);
    if (document.visibilityState === "visible") {
      const remaining = data.next_available_at ? Date.parse(data.next_available_at)-Date.now() : 60000;
      const next = data.state === "generating" ? 3000 : Math.min(60000, Math.max(1000, remaining || 60000));
      timer = setTimeout(refresh, next);
    }
  }
  async function getStatus() {
    const response = await fetch(`/api/tower/companion/status/?surface=${surface()}`, {credentials:"same-origin",cache:"no-store"});
    if (!response.ok) throw new Error("request_failed");
    return response.json();
  }
  async function refresh() {
    if (busy) return;
    if (surface() !== "learning") { clearTimeout(timer); panel.hidden=true; return; }
    try { render(await getStatus()); }
    catch (_) { label.textContent=label.dataset.unavailable;button.disabled=true;timer=setTimeout(refresh,15000); }
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || button.disabled || surface() !== "learning") return;
    busy=true; button.disabled=true; badge.hidden=true; thinking.hidden=false;
    label.textContent=label.dataset.generating;
    const requestId = crypto.randomUUID();
    let result;
    try { result=await api("generate", {request_id:requestId}); }
    catch (_) { try { result=await getStatus(); } catch (_) {} }
    finally { busy=false; }
    if (result) render(result);
    else { thinking.hidden=true; label.textContent=label.dataset.unavailable; timer=setTimeout(refresh,15000); }
  });
  panel.addEventListener("toggle", () => { if (panel.open) acknowledge(); });
  new IntersectionObserver(entries => {
    bubbleVisible = entries.some(entry => entry.isIntersecting && entry.intersectionRatio >= .5);
    if (bubbleVisible) acknowledge();
  }, {threshold:[0,.5,1]}).observe(bubble);
  document.addEventListener("visibilitychange", () => {
    clearTimeout(timer); if (document.visibilityState === "visible") { refresh(); acknowledge(); }
  });
  new MutationObserver(() => { clearTimeout(timer); if (!busy) refresh(); }).observe(document.body,{attributes:true,attributeFilter:["class"]});
  refresh();
})();
