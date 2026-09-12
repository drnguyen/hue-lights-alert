(function () {
  "use strict";
  const $ = (sel) => document.querySelector(sel);

  // ---- helpers ------------------------------------------------------------
  let toastTimer = null;
  function toast(message, isError) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("error", !!isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 6000 : 3000);
  }

  async function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const res = await fetch(path, opts);
    if (res.status === 401) { window.location.href = "/admin/login"; throw new Error("Session expired"); }
    let data = null;
    try { data = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok) {
      const detail = data && data.detail;
      const msg = Array.isArray(detail) ? detail.map(d => d.msg).join("; ") : (detail || res.statusText);
      throw new Error(msg);
    }
    return data;
  }

  function busy(btn, on) { if (btn) { btn.disabled = on; } }

  // ---- 1. bridge discovery & pairing -----------------------------------------
  const discoverBtn = $("#discover-btn");
  if (discoverBtn) {
    discoverBtn.addEventListener("click", async () => {
      busy(discoverBtn, true);
      discoverBtn.textContent = "Searching… (a few seconds)";
      const list = $("#bridge-results");
      list.innerHTML = "";
      try {
        const { bridges } = await api("POST", "/admin/bridge/discover");
        if (!bridges.length) {
          list.innerHTML = '<li class="muted">No bridge found. Make sure it is powered on and on the same network, or enter its IP manually.</li>';
        }
        for (const b of bridges) {
          const li = document.createElement("li");
          li.innerHTML = `<span><strong>${b.name || b.id || "Hue Bridge"}</strong> <code>${b.ip}</code> <span class="muted">(${b.source})</span></span>`;
          const btn = document.createElement("button");
          btn.className = "primary"; btn.textContent = "Pair";
          btn.addEventListener("click", () => startPairing(b));
          li.appendChild(btn);
          list.appendChild(li);
        }
      } catch (e) { toast(e.message, true); }
      finally { busy(discoverBtn, false); discoverBtn.textContent = "Search for Hue Bridge"; }
    });

    $("#manual-pair-btn").addEventListener("click", () => {
      const ip = $("#manual-ip").value.trim();
      if (!ip) { toast("Enter the bridge IP first", true); return; }
      startPairing({ ip });
    });
  }

  let pairing = null;
  function stopPairing() {
    if (pairing) { clearInterval(pairing.interval); clearInterval(pairing.countdown); pairing = null; }
    const box = $("#pair-status"); if (box) box.hidden = true;
  }
  async function startPairing(bridge) {
    stopPairing();
    const box = $("#pair-status"); box.hidden = false;
    $("#pair-message").innerHTML = `Waiting for <code>${bridge.ip}</code>… (<span id="pair-countdown">60</span>s left)`;
    let remaining = 60;
    pairing = {};
    pairing.countdown = setInterval(() => {
      remaining -= 1;
      const c = $("#pair-countdown"); if (c) c.textContent = remaining;
      if (remaining <= 0) { stopPairing(); toast("Pairing timed out. Try again and press the link button within 60 seconds.", true); }
    }, 1000);
    const attempt = async () => {
      try {
        const res = await api("POST", "/admin/bridge/pair", { ip: bridge.ip, id: bridge.id || null, name: bridge.name || null });
        if (res.status === "paired") { stopPairing(); toast("Bridge paired!"); setTimeout(() => window.location.reload(), 600); }
      } catch (e) { stopPairing(); toast("Pairing failed: " + e.message, true); }
    };
    attempt();
    pairing.interval = setInterval(attempt, 2000);
  }
  const cancelBtn = $("#pair-cancel-btn");
  if (cancelBtn) cancelBtn.addEventListener("click", stopPairing);

  const unpairBtn = $("#unpair-btn");
  if (unpairBtn) unpairBtn.addEventListener("click", async () => {
    if (!confirm("Unpair the bridge? Light selection will be cleared. You will need to press the link button again.")) return;
    try { await api("DELETE", "/admin/bridge"); window.location.reload(); } catch (e) { toast(e.message, true); }
  });

  // ---- 2. lights --------------------------------------------------------------
  const lightsTable = $("#lights-table");
  async function loadLights() {
    if (!lightsTable) return;
    const tbody = lightsTable.querySelector("tbody");
    const err = $("#lights-error"); err.hidden = true;
    tbody.innerHTML = '<tr><td colspan="5" class="muted">Loading lights…</td></tr>';
    try {
      const { lights } = await api("GET", "/admin/lights");
      tbody.innerHTML = "";
      if (!lights.length) tbody.innerHTML = '<tr><td colspan="5" class="muted">The bridge reports no lights.</td></tr>';
      for (const l of lights) {
        const tr = document.createElement("tr");
        const state = !l.reachable ? '<span class="dot unreachable"></span>unreachable' : (l.on ? '<span class="dot on"></span>on' : '<span class="dot"></span>off');
        tr.innerHTML = `<td><input type="checkbox" class="light-cb" value="${l.id}" ${l.selected ? "checked" : ""}></td>
          <td>${l.id}</td><td>${l.name}${l.supports_color ? "" : ' <span class="muted">(no colour)</span>'}</td><td class="muted">${l.type || ""}</td><td>${state}</td>`;
        tbody.appendChild(tr);
      }
      updateSummary();
    } catch (e) { err.textContent = "Could not load lights: " + e.message; err.hidden = false; tbody.innerHTML = ""; }
  }
  function updateSummary() {
    const n = document.querySelectorAll(".light-cb:checked").length;
    const s = $("#lights-summary"); if (s) s.textContent = `${n} selected`;
  }
  if (lightsTable) {
    loadLights();
    lightsTable.addEventListener("change", updateSummary);
    $("#reload-lights-btn").addEventListener("click", loadLights);
    $("#save-lights-btn").addEventListener("click", async (ev) => {
      const ids = Array.from(document.querySelectorAll(".light-cb:checked")).map(cb => cb.value);
      busy(ev.target, true);
      try { await api("PUT", "/admin/lights/selection", { light_ids: ids }); toast(`Saved ${ids.length} light(s)`); }
      catch (e) { toast(e.message, true); } finally { busy(ev.target, false); }
    });
  }

  // ---- 3. settings ------------------------------------------------------------
  const settingsForm = $("#settings-form");
  if (settingsForm) settingsForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fd = new FormData(settingsForm);
    const body = {
      min_brightness: Number(fd.get("min_brightness")),
      max_brightness: Number(fd.get("max_brightness")),
      glow_period_seconds: Number(fd.get("glow_period_seconds")),
    };
    try { await api("PUT", "/admin/settings", body); toast("Levels saved"); } catch (e) { toast(e.message, true); }
  });

  // ---- 4. test alert ------------------------------------------------------------
  const statusEl = $("#alert-status");
  function renderStatus(a) {
    if (!statusEl) return;
    statusEl.dataset.active = a.active ? "true" : "false";
    statusEl.textContent = a.active
      ? `Alert active: ${a.level} / ${a.mode} (source ${a.source})${a.expires_at ? ", auto-clears at " + a.expires_at : ""}`
      : "No active alert";
  }
  async function refreshStatus() { try { renderStatus(await api("GET", "/admin/alert")); } catch (_) { /* ignore */ } }
  if (statusEl) {
    setInterval(refreshStatus, 2000);
    $("#test-btn").addEventListener("click", async (ev) => {
      busy(ev.target, true);
      try {
        const res = await api("POST", "/admin/alert/test", {
          level: $("#test-level").value, mode: $("#test-mode").value, duration_seconds: Number($("#test-duration").value) || 10,
        });
        renderStatus(res.alert); toast("Test alert started");
      } catch (e) { toast(e.message, true); } finally { busy(ev.target, false); }
    });
    $("#clear-btn").addEventListener("click", async () => {
      try { const res = await api("DELETE", "/admin/alert"); renderStatus(res.alert); toast(res.status === "cleared" ? "Alert cleared" : "No alert was active"); }
      catch (e) { toast(e.message, true); }
    });
  }

  // ---- 5. tokens ------------------------------------------------------------------
  const tokensTable = $("#tokens-table");
  if (tokensTable) {
    $("#token-create-btn").addEventListener("click", async (ev) => {
      const name = $("#token-name").value.trim();
      if (!name) { toast("Give the token a name", true); return; }
      busy(ev.target, true);
      try {
        const { token, record } = await api("POST", "/admin/tokens", { name });
        $("#new-token-value").textContent = token;
        $("#new-token").hidden = false;
        const empty = tokensTable.querySelector("tr.empty"); if (empty) empty.remove();
        const tr = document.createElement("tr");
        tr.dataset.id = record.id;
        tr.innerHTML = `<td>${record.name}</td><td><code>${record.prefix}…</code></td><td>${record.created_at}</td><td>never</td>
          <td><button class="danger small revoke-btn" data-id="${record.id}">Revoke</button></td>`;
        tokensTable.querySelector("tbody").appendChild(tr);
        $("#token-name").value = "";
      } catch (e) { toast(e.message, true); } finally { busy(ev.target, false); }
    });
    $("#copy-token-btn").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText($("#new-token-value").textContent); toast("Token copied"); }
      catch (_) { toast("Copy failed, select the token manually", true); }
    });
    tokensTable.addEventListener("click", async (ev) => {
      const btn = ev.target.closest(".revoke-btn"); if (!btn) return;
      if (!confirm("Revoke this token? Applications using it will get 401.")) return;
      try { await api("DELETE", `/admin/tokens/${btn.dataset.id}`); btn.closest("tr").remove(); toast("Token revoked"); }
      catch (e) { toast(e.message, true); }
    });
  }
})();
