/*
 * Small shared helpers for teacher/student pages:
 *   UIKit.autosave(...)  - debounced, ordered, retried saving instead of "Save" buttons
 *   UIKit.statusPill(el) - one place that shows "Saving… / Saved / Not saved"
 *   UIKit.tabs(root)     - accessible tabs that remember the last opened tab
 *   UIKit.dialog(el)     - native <dialog> with backdrop click and focus return
 * Texts come from data-* attributes on <body> or UIKit.configure({texts}).
 */
(function () {
  "use strict";

  var texts = {
    saving: "Saving…", saved: "Saved", failed: "Not saved",
    retrying: "No connection — retrying", idle: "", leave: "You have unsaved changes."
  };
  var savers = new Set();

  function configure(options) {
    Object.assign(texts, (options && options.texts) || {});
  }

  function readBodyTexts() {
    var data = document.body && document.body.dataset;
    if (!data) return;
    ["saving", "saved", "failed", "retrying", "idle", "leave"].forEach(function (key) {
      var value = data["autosave" + key.charAt(0).toUpperCase() + key.slice(1)];
      if (value) texts[key] = value;
    });
  }

  // ---------------------------------------------------------------- status
  function statusPill(element) {
    // `pill.element` may be swapped when the row it belongs to is re-rendered.
    var pill = { element: element };
    var hideTimer = null;
    pill.set = function (state, message) {
      var el = pill.element;
      if (!el) return;
      clearTimeout(hideTimer);
      el.dataset.state = state;
      el.hidden = false;
      el.textContent = message || texts[state] || "";
      el.title = message || "";
      if (state === "idle" && !texts.idle) el.hidden = true;
      if (state === "saved") {
        hideTimer = setTimeout(function () {
          if (pill.element !== el) return;
          el.dataset.state = "idle";
          el.textContent = texts.idle || "";
          if (!texts.idle) el.hidden = true;
        }, 2500);
      }
    };
    if (element && !element.dataset.state) pill.set("idle");
    return pill;
  }

  // -------------------------------------------------------------- autosave
  /**
   * autosave({save, delay, status, onSaved, onError})
   *   schedule(patch)  merge patch into the pending change and save after `delay`
   *   flush()          save now; resolves true when everything is stored
   *   pending()        true while unsaved or failed changes exist
   * `save(patch)` must resolve with the server data or throw an Error whose
   * `.status` is the HTTP status. 4xx errors are shown and wait for the next
   * edit; network/5xx errors are retried with backoff.
   */
  function autosave(options) {
    var delay = options.delay == null ? 700 : options.delay;
    var status = options.status && options.status.set ? options.status : statusPill(options.status);
    var pendingPatch = null;
    var failedPatch = null;
    var timer = null;
    var retryTimer = null;
    var retryIndex = 0;
    var chain = Promise.resolve(true);
    var api = {};

    function hasPending() { return Boolean(pendingPatch || failedPatch); }

    function schedule(patch, customDelay) {
      pendingPatch = Object.assign({}, pendingPatch || {}, patch || {});
      status.set("saving");
      clearTimeout(timer);
      timer = setTimeout(flush, customDelay == null ? delay : customDelay);
    }

    function flush() {
      clearTimeout(timer);
      clearTimeout(retryTimer);
      if (!pendingPatch && !failedPatch) return chain;
      var patch = Object.assign({}, failedPatch || {}, pendingPatch || {});
      pendingPatch = null;
      failedPatch = null;
      chain = chain.then(function () {
        status.set("saving");
        return Promise.resolve()
          .then(function () { return options.save(patch); })
          .then(function (data) {
            retryIndex = 0;
            if (!hasPending()) status.set("saved");
            if (options.onSaved) options.onSaved(data, patch);
            return true;
          })
          .catch(function (error) {
            var code = error && error.status;
            var retry = !code || code >= 500 || code === 429;
            // Keep the unsaved values; newer edits made meanwhile win.
            failedPatch = Object.assign({}, patch, failedPatch || {});
            if (retry) {
              status.set("failed", texts.retrying);
              var wait = [1500, 4000, 10000, 30000][Math.min(retryIndex, 3)];
              retryIndex += 1;
              retryTimer = setTimeout(flush, wait);
            } else {
              status.set("failed", texts.failed + ": " + ((error && error.message) || ""));
            }
            if (options.onError) options.onError(error, patch);
            return false;
          });
      });
      return chain;
    }

    function cancel() {
      clearTimeout(timer);
      clearTimeout(retryTimer);
      pendingPatch = null;
      failedPatch = null;
      status.set("idle");
    }

    api.schedule = schedule;
    api.flush = flush;
    api.cancel = cancel;
    api.pending = hasPending;
    api.status = status;
    savers.add(api);
    return api;
  }

  function flushAll() {
    return Promise.all(Array.from(savers).map(function (saver) { return saver.flush(); }))
      .then(function (results) { return results.every(Boolean); });
  }

  function anyPending() {
    return Array.from(savers).some(function (saver) { return saver.pending(); });
  }

  window.addEventListener("beforeunload", function (event) {
    if (!anyPending()) return;
    flushAll();
    event.preventDefault();
    event.returnValue = texts.leave;
    return texts.leave;
  });
  window.addEventListener("online", function () { flushAll(); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") flushAll();
  });

  // ------------------------------------------------------------------ tabs
  function tabs(root, options) {
    options = options || {};
    var buttons = Array.from(root.querySelectorAll("[data-tab]"));
    var panels = Array.from(root.querySelectorAll("[data-tab-panel]"));
    var storageKey = options.storageKey || ("tabs:" + location.pathname + ":" + (root.id || ""));
    var current = null;

    buttons.forEach(function (button) {
      button.setAttribute("role", "tab");
      button.type = "button";
      if (!button.id) button.id = "tab-" + button.dataset.tab;
    });
    panels.forEach(function (panel) {
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", "tab-" + panel.dataset.tabPanel);
    });
    var list = buttons.length ? buttons[0].parentElement : null;
    if (list) list.setAttribute("role", "tablist");

    function show(name, silent) {
      if (!buttons.some(function (b) { return b.dataset.tab === name && !b.hidden; })) {
        var first = buttons.find(function (b) { return !b.hidden; });
        name = first ? first.dataset.tab : name;
      }
      current = name;
      buttons.forEach(function (button) {
        var active = button.dataset.tab === name;
        button.setAttribute("aria-selected", active ? "true" : "false");
        button.tabIndex = active ? 0 : -1;
        button.classList.toggle("active", active);
      });
      panels.forEach(function (panel) { panel.hidden = panel.dataset.tabPanel !== name; });
      try { sessionStorage.setItem(storageKey, name); } catch (e) { /* ignore */ }
      if (!silent && options.onChange) options.onChange(name);
    }

    buttons.forEach(function (button, index) {
      button.addEventListener("click", function () { show(button.dataset.tab); });
      button.addEventListener("keydown", function (event) {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        var visible = buttons.filter(function (b) { return !b.hidden; });
        var position = visible.indexOf(button) + (event.key === "ArrowRight" ? 1 : -1);
        var next = visible[(position + visible.length) % visible.length];
        next.focus();
        show(next.dataset.tab);
      });
    });

    var saved = null;
    try { saved = sessionStorage.getItem(storageKey); } catch (e) { /* ignore */ }
    show(options.initial || saved || (buttons[0] && buttons[0].dataset.tab), true);
    return { show: show, get current() { return current; } };
  }

  // ---------------------------------------------------------------- dialog
  function dialog(element, options) {
    options = options || {};
    var opener = null;
    element.addEventListener("click", function (event) {
      if (event.target === element) close();
    });
    element.addEventListener("close", function () {
      if (options.onClose) options.onClose();
      if (opener && opener.focus) opener.focus();
    });
    element.querySelectorAll("[data-dialog-close]").forEach(function (button) {
      button.addEventListener("click", close);
    });
    function open() {
      opener = document.activeElement;
      if (typeof element.showModal === "function") element.showModal();
      else element.setAttribute("open", "");
      var focusTarget = element.querySelector("[autofocus], input, select, textarea, button");
      if (focusTarget) setTimeout(function () { focusTarget.focus(); }, 30);
      if (options.onOpen) options.onOpen();
    }
    function close() {
      if (typeof element.close === "function" && element.open) element.close();
      else element.removeAttribute("open");
    }
    return { open: open, close: close, element: element };
  }

  // ------------------------------------------------------------------ misc
  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, wait);
    };
  }

  function httpError(message, status) {
    var error = new Error(message || ("HTTP " + status));
    error.status = status;
    return error;
  }

  /** fetch JSON with CSRF; throws httpError on failure (status 0 = network). */
  async function requestJson(url, options) {
    options = options || {};
    var method = (options.method || "GET").toUpperCase();
    var headers = { "Accept": "application/json" };
    if (method !== "GET" && method !== "HEAD") {
      var row = document.cookie.split("; ").find(function (item) { return item.indexOf("csrftoken=") === 0; });
      headers["X-CSRFToken"] = row ? decodeURIComponent(row.split("=")[1] || "") : "";
    }
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    var response;
    try {
      response = await fetch(url, {
        method: method, credentials: "include", headers: headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body)
      });
    } catch (error) {
      throw httpError((error && error.message) || "network error", 0);
    }
    var type = response.headers.get("content-type") || "";
    var data = type.indexOf("application/json") >= 0 ? await response.json() : { ok: false, error: "HTTP " + response.status };
    if (!response.ok || data.ok === false) {
      var failure = httpError(data.error || ("HTTP " + response.status), response.status);
      failure.data = data;
      throw failure;
    }
    return data;
  }

  document.addEventListener("DOMContentLoaded", readBodyTexts);
  if (document.readyState !== "loading") readBodyTexts();

  window.UIKit = {
    configure: configure, statusPill: statusPill, autosave: autosave, flushAll: flushAll,
    anyPending: anyPending, tabs: tabs, dialog: dialog, debounce: debounce,
    requestJson: requestJson, httpError: httpError
  };
})();
