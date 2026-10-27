"use strict";
(() => {
  const menu = document.getElementById("studentModuleMenu");
  const workspace = document.getElementById("studentWorkspace");
  if (!menu || !workspace) return;
  const narrow = window.matchMedia("(max-width: 980px)");
  const syncNavigation = () => {
    menu.open = !narrow.matches;
    const hints = document.getElementById("hintsPanel");
    if (hints) hints.open = !narrow.matches;
  };
  syncNavigation();
  narrow.addEventListener("change", syncNavigation);
  const placeThemeControl = () => {
    const button = document.querySelector(".site-theme-toggle");
    const actions = document.querySelector(".student-account-header .topRight");
    if (button && actions) { actions.append(button); actions.classList.add("has-theme-control"); }
  };
  placeThemeControl();
  document.addEventListener("DOMContentLoaded", placeThemeControl, {once:true});
  document.addEventListener("student-module-selected", () => {
    if (!narrow.matches) return;
    menu.open = false;
    workspace.focus({preventScroll: true});
    workspace.scrollIntoView({block: "start", behavior: "auto"});
  });
})();
