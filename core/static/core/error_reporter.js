/*
 * Reports 502/503/504 answers (and dropped connections) of same-origin API calls
 * to /api/client-error-report/. A 502 comes from the proxy, so the browser is
 * the only place that sees it together with the page, timing and edge headers.
 * Reports that cannot be sent while the server is down are queued in
 * localStorage and retried with backoff and on the next page load.
 */
(function () {
  "use strict";
  if (window.__errorReporterInstalled || typeof window.fetch !== "function") return;
  window.__errorReporterInstalled = true;

  var ENDPOINT = "/api/client-error-report/";
  var STORE_KEY = "progaplatform:error-report-queue";
  var MAX_QUEUE = 20;
  // 500s already carry a server-side traceback; the browser adds value for the
  // gateway errors Django never sees.
  var REPORTED_STATUSES = { 502: true, 503: true, 504: true };
  var HEADER_NAMES = ["cf-ray", "x-request-id", "rndr-id", "server", "content-type", "retry-after", "cf-cache-status", "via"];
  var originalFetch = window.fetch.bind(window);
  var backoff = [2000, 5000, 15000, 60000];
  var flushTimer = null;
  var attempt = 0;
  var flushing = false;

  function readQueue() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || "[]") || []; } catch (e) { return []; }
  }
  function writeQueue(rows) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(rows.slice(-MAX_QUEUE))); } catch (e) { /* private mode */ }
  }
  function csrfToken() {
    var row = document.cookie.split("; ").find(function (item) { return item.indexOf("csrftoken=") === 0; });
    return row ? decodeURIComponent(row.split("=")[1] || "") : "";
  }
  function requestInfo(input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || String(input);
    var method = (init && init.method) || (input && input.method) || "GET";
    var parsed;
    try { parsed = new URL(url, window.location.href); } catch (e) { return null; }
    if (parsed.origin !== window.location.origin) return null;
    if (parsed.pathname.indexOf(ENDPOINT) === 0) return null;
    return { path: parsed.pathname, method: String(method).toUpperCase() };
  }
  function connectionLabel() {
    var c = navigator.connection;
    return c ? [c.effectiveType, c.downlink ? c.downlink + "Mbps" : "", c.rtt ? c.rtt + "ms" : ""].filter(Boolean).join(" ") : "";
  }

  function enqueue(report) {
    var rows = readQueue();
    var now = Date.now();
    var duplicate = rows.find(function (row) {
      return row.url === report.url && row.method === report.method && row.status === report.status && now - row.ts < 15000;
    });
    if (duplicate) {
      duplicate.count = (duplicate.count || 1) + 1;
      duplicate.ts = now;
    } else {
      report.ts = now;
      report.count = 1;
      rows.push(report);
    }
    writeQueue(rows);
    scheduleFlush(300);
  }

  function scheduleFlush(delay) {
    clearTimeout(flushTimer);
    flushTimer = setTimeout(flush, delay);
  }

  async function flush() {
    if (flushing) return;
    var rows = readQueue();
    if (!rows.length) { attempt = 0; return; }
    flushing = true;
    var remaining = rows.slice();
    try {
      while (remaining.length) {
        var row = remaining[0];
        var payload = Object.assign({}, row, { queued: attempt > 0 || Date.now() - row.ts > 10000 });
        delete payload.ts;
        var response = await originalFetch(ENDPOINT, {
          method: "POST",
          credentials: "include",
          keepalive: true,
          headers: { "Accept": "application/json", "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
          body: JSON.stringify(payload)
        });
        if (response.status === 429 || response.status >= 500) throw new Error("report endpoint unavailable");
        remaining.shift(); // 2xx stored; 4xx would never succeed, drop it as well
        writeQueue(remaining);
      }
      attempt = 0;
    } catch (e) {
      writeQueue(remaining);
      scheduleFlush(backoff[Math.min(attempt, backoff.length - 1)]);
      attempt += 1;
    } finally {
      flushing = false;
    }
  }

  async function describe(response) {
    var headers = {};
    HEADER_NAMES.forEach(function (name) {
      var value = response.headers.get(name);
      if (value) headers[name] = value;
    });
    var excerpt = "";
    try { excerpt = (await response.clone().text()).slice(0, 1500); } catch (e) { /* body already used */ }
    return { headers: headers, excerpt: excerpt };
  }

  window.fetch = async function (input, init) {
    var info = requestInfo(input, init);
    var started = performance.now();
    try {
      var response = await originalFetch(input, init);
      if (info && REPORTED_STATUSES[response.status]) {
        describe(response).then(function (details) {
          enqueue({
            status: response.status,
            method: info.method,
            url: info.path,
            page: window.location.pathname,
            duration_ms: Math.round(performance.now() - started),
            request_id: details.headers["x-request-id"] || "",
            response_headers: details.headers,
            body_excerpt: details.excerpt,
            occurred_at: new Date().toISOString(),
            online: navigator.onLine,
            visibility: document.visibilityState,
            connection: connectionLabel()
          });
        });
      }
      return response;
    } catch (error) {
      var aborted = error && error.name === "AbortError";
      if (info && !aborted && navigator.onLine) {
        enqueue({
          status: 0,
          method: info.method,
          url: info.path,
          page: window.location.pathname,
          duration_ms: Math.round(performance.now() - started),
          error: String((error && error.message) || error),
          occurred_at: new Date().toISOString(),
          online: navigator.onLine,
          visibility: document.visibilityState,
          connection: connectionLabel()
        });
      }
      throw error;
    }
  };

  window.addEventListener("online", function () { scheduleFlush(500); });
  if (readQueue().length) scheduleFlush(1500);
})();
