/* core/static/core/student_activity.js
   Student activity tracker (MVP):
   - copy / paste / cut
   - visibilitychange (tab switch / minimize)
   - blur/focus
   - time-on-task heartbeats
   - batched sending to backend endpoint
*/

(function () {
  "use strict";

  // ===== Config =====
  const ENDPOINT = "/api/student/activity";   // backend endpoint
  const FLUSH_INTERVAL_MS = 5000;             // send every 5s if queue not empty
  const MAX_BATCH_SIZE = 30;                  // max events per request
  const HEARTBEAT_SEC = 10;                   // heartbeat interval (time-on-task)
  const MAX_PAYLOAD_BYTES = 30_000;           // hard cap payload size

  // ===== State =====
  let currentTaskId = null;
  let currentSessionId = null;
  let lastTaskOpenedAt = Date.now();

  let queue = [];
  let lastFlushAt = 0;
  let flushTimer = null;
  let heartbeatTimer = null;

  // anti-spam counters (within 1 minute window)
  let minuteWindowStart = Date.now();
  let counters = {
    copy: 0,
    paste: 0,
    cut: 0,
    tab_hidden: 0,
    blur: 0,
  };

  function nowIso() {
    return new Date().toISOString();
  }

  function resetMinuteWindowIfNeeded() {
    const now = Date.now();
    if (now - minuteWindowStart >= 60_000) {
      minuteWindowStart = now;
      counters = { copy: 0, paste: 0, cut: 0, tab_hidden: 0, blur: 0 };
    }
  }

  function safeTextLen(text) {
    return (text || "").length;
  }

  function pushEvent(type, payload) {
    resetMinuteWindowIfNeeded();

    // basic anti-spam: cap some frequent events per minute
    if (type === "copy" && counters.copy++ > 40) return;
    if (type === "paste" && counters.paste++ > 60) return;
    if (type === "cut" && counters.cut++ > 40) return;
    if (type === "tab_hidden" && counters.tab_hidden++ > 30) return;
    if (type === "blur" && counters.blur++ > 60) return;

    queue.push({
      ts: nowIso(),
      type,
      task_id: currentTaskId,
      session_id: currentSessionId,
      payload: payload || {},
      page: location.pathname,
      ua: navigator.userAgent,
    });

    if (!flushTimer) {
      flushTimer = setTimeout(flush, FLUSH_INTERVAL_MS);
    }

    // if queue is large, flush immediately
    if (queue.length >= MAX_BATCH_SIZE) flush();
  }

  function approxByteSize(obj) {
    try {
      return new Blob([JSON.stringify(obj)]).size;
    } catch (e) {
      return 0;
    }
  }

  async function flush(useBeacon = false) {
    if (!queue.length) return;

    const batch = queue.splice(0, MAX_BATCH_SIZE);

    // Hard payload cap: if too big, trim payloads (drop heavy fields)
    let payload = { events: batch };
    if (approxByteSize(payload) > MAX_PAYLOAD_BYTES) {
      payload.events = batch.map(e => ({
        ts: e.ts,
        type: e.type,
        task_id: e.task_id,
        session_id: e.session_id,
        payload: { note: "trimmed" },
        page: e.page,
      }));
    }

    lastFlushAt = Date.now();

    try {
      if (useBeacon && navigator.sendBeacon) {
        const blob = new Blob([JSON.stringify(payload)], { type: "application/json" });
        navigator.sendBeacon(ENDPOINT, blob);
        return;
      }

      await fetch(ENDPOINT, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true,
      });
    } catch (e) {
      // on failure, requeue to avoid losing
      queue = batch.concat(queue);
      // backoff a little
      if (flushTimer) clearTimeout(flushTimer);
      flushTimer = setTimeout(flush, 9000);
    }
  }

  // ===== Heartbeat (time on current task) =====
  function heartbeat() {
    if (!currentTaskId) return;
    const now = Date.now();
    const secondsOnTask = Math.max(0, Math.round((now - lastTaskOpenedAt) / 1000));
    pushEvent("heartbeat", { seconds_on_task: secondsOnTask });
  }

  function startHeartbeat() {
    stopHeartbeat();
    heartbeatTimer = setInterval(heartbeat, HEARTBEAT_SEC * 1000);
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  // ===== Public API (called from your main portal script) =====
  window.StudentActivityTracker = {
    setContext: function ({ sessionId, taskId }) {
      if (typeof sessionId !== "undefined") currentSessionId = sessionId;
      if (typeof taskId !== "undefined") {
        // log time spent on previous task
        if (currentTaskId && currentTaskId !== taskId) {
          const spentSec = Math.max(0, Math.round((Date.now() - lastTaskOpenedAt) / 1000));
          pushEvent("task_switch", { from_task_id: currentTaskId, to_task_id: taskId, seconds_spent: spentSec });
        }
        currentTaskId = taskId;
        lastTaskOpenedAt = Date.now();
      }
      startHeartbeat();
    },
    log: function (type, payload) {
      pushEvent(type, payload);
    },
    flush: function () {
      flush(false);
    }
  };

  // ===== DOM listeners =====
  document.addEventListener("copy", () => {
    pushEvent("copy", { selection_len: safeTextLen((document.getSelection() || "").toString()) });
  });

  document.addEventListener("cut", () => {
    pushEvent("cut", { selection_len: safeTextLen((document.getSelection() || "").toString()) });
  });

  document.addEventListener("paste", (e) => {
    let len = 0;
    try {
      const txt = (e.clipboardData && e.clipboardData.getData("text")) || "";
      len = safeTextLen(txt);
    } catch (_) {}
    pushEvent("paste", { pasted_len: len });
  });

  document.addEventListener("visibilitychange", () => {
    pushEvent(document.hidden ? "tab_hidden" : "tab_visible", {});
  });

  window.addEventListener("blur", () => {
    pushEvent("blur", {});
  });

  window.addEventListener("focus", () => {
    pushEvent("focus", {});
  });

  window.addEventListener("beforeunload", () => {
    // final flush using beacon
    // also log session exit
    pushEvent("exit", {});
    flush(true);
    stopHeartbeat();
  });

})();
