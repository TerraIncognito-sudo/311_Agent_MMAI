// /admin page. Reads live from the database through /api/admin (src/web/admin.py).
// Chat text comes from the public site, so everything is rendered with
// textContent via h(), never innerHTML.
(() => {
  const $ = (id) => document.getElementById(id);
  const POLL_LIST_MS = 4000;
  const POLL_DETAIL_MS = 2500;

  // ---------- operating policy flow (311_Operating_Policy_v0.1) ----------
  // Each stage of an interaction and the rule IDs that govern it.
  const STAGES = [
    { id: "screen", name: "Screen", rules: ["E1", "E7", "E8", "S1", "S5", "D2"] },
    { id: "classify", name: "Classify", rules: ["A1", "E3", "F2"] },
    { id: "policy", name: "Policy lookup", rules: ["A2", "F4"] },
    { id: "duplicates", name: "Duplicates", rules: ["A3", "D3", "D4", "C6"] },
    { id: "fields", name: "Fields", rules: ["D1", "M2", "E6"] },
    { id: "knowledge", name: "Knowledge", rules: ["C5"] },
    { id: "escalate", name: "Escalate", rules: ["A6", "E2", "E4", "E5", "E9", "E10"] },
    { id: "ticket", name: "Ticket / status", rules: ["A4", "A5", "A8", "D5", "F3"] },
    { id: "reply", name: "Reply", rules: ["M1", "M3", "M7", "C1", "D8"] },
  ];
  const STAGE_NAME = Object.fromEntries(STAGES.map((s) => [s.id, s.name]));

  function stageOf(ev) {
    const step = ev.step;
    if (step === "input_screen") return "screen";
    if (step === "rule") return "escalate";
    if (step === "tool:classifier") return "classify";
    if (step === "tool:policy_lookup") return "policy";
    if (step === "tool:search_tickets") return "duplicates";
    if (step === "tool:kb_search") return "knowledge";
    if (step === "tool:handoff") return "escalate";
    if (step === "tool:get_status") return "ticket";
    if (step === "tool:create_ticket") {
      const missing = ev.data && ev.data.result && ev.data.result.missing;
      return missing && missing.length ? "fields" : "ticket";
    }
    if (step === "output_check" || step === "reply") return "reply";
    return null; // model calls sit between stages
  }

  // ---------- helpers ----------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  async function api(path, opts = {}) {
    const r = await fetch(`/api/admin${path}`, {
      ...opts,
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    });
    const body = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
    if (!r.ok) {
      const d = body && body.detail;
      const err = new Error(typeof d === "string" ? d : `Request failed (${r.status})`);
      // {errors: [...]} from our checks, or FastAPI's list of field problems
      err.errors = Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(-1)[0]}: ${x.msg}`) : d && d.errors;
      throw err;
    }
    return body;
  }

  function ago(iso) {
    if (!iso) return "";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return `${Math.floor(s)}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return new Date(iso).toLocaleDateString();
  }
  // replaceChildren() would print a skipped (null) section as the text "null".
  const fill = (el, ...children) => el.replaceChildren(...children.filter(Boolean));
  const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "");
  const short = (t, n = 90) => (!t ? "" : t.length > n ? t.slice(0, n - 1) + "…" : t);

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch { /* storage blocked: per-viewer convenience only */ }
    return null;
  }

  let RULES = {};
  function ruleChip(id, extra = "") {
    const r = RULES[id];
    return h("span", {
      class: `chip rule ${r && r.hard ? "hard" : ""} ${extra}`,
      title: r ? `${id}${r.hard ? " [hard]" : ""}: ${r.text}` : id,
    }, id);
  }

  function poller(fn, ms) {
    let timer = null;
    const tick = async () => {
      if (!document.hidden) {
        try { await fn(); } catch (e) { console.warn(e); }
      }
      timer = setTimeout(tick, ms);
    };
    return {
      start() { if (!timer) tick(); },
      stop() { clearTimeout(timer); timer = null; },
      now() { fn().catch(console.warn); },
    };
  }

  // ---------- tabs ----------
  const tabs = {
    interactions: { start: () => interactions.start(), stop: () => interactions.stop() },
    files: { start: () => files.load(), stop: () => {} },
    kb: { start: () => kb.start(), stop: () => kb.stop() },
  };
  let currentTab = "interactions";
  function showTab(name) {
    if (!tabs[name]) name = "interactions";
    tabs[currentTab].stop();
    currentTab = name;
    for (const b of document.querySelectorAll(".tabs button")) {
      b.setAttribute("aria-selected", String(b.dataset.tab === name));
    }
    for (const id of Object.keys(tabs)) $(`tab-${id}`).hidden = id !== name;
    tabs[name].start();
    if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
  }
  for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));
  window.addEventListener("hashchange", () => { if (location.hash.slice(1) !== currentTab) showTab(location.hash.slice(1)); });

  const author = $("author");
  author.value = store("admin-author") || "";
  author.addEventListener("change", () => store("admin-author", author.value.trim()));
  const who = () => author.value.trim() || null;

  // =====================================================================
  // Interactions: live walkthrough
  // =====================================================================
  const interactions = (() => {
    let selected = null;
    let detail = null;      // last payload
    let cursor = null;      // index into steps; null = show everything
    let signature = "";
    let listSig = "";
    let listDrawn = 0;
    const open = new Set(); // expanded event ids

    const listPoll = poller(loadList, POLL_LIST_MS);
    const detailPoll = poller(() => (selected ? loadDetail(selected) : Promise.resolve()), POLL_DETAIL_MS);

    async function loadList() {
      const { interactions: rows } = await api("/interactions?limit=60");
      $("int-status").textContent = rows.length ? `Live · updated ${new Date().toLocaleTimeString()}` : "No conversations logged yet.";
      if ($("follow").checked && rows[0] && rows[0].interaction_id !== selected) select(rows[0].interaction_id);
      // Redraw only when something changed (or every 30 s for the "ago" times).
      const sig = JSON.stringify([selected, rows]);
      if (sig === listSig && Date.now() - listDrawn < 30000) return;
      listSig = sig;
      listDrawn = Date.now();
      const list = $("int-list");
      list.replaceChildren(...rows.map((r) => {
        const live = Date.now() - new Date(r.last_at).getTime() < 120000;
        return h("button", {
          class: `item ${r.interaction_id === selected ? "selected" : ""}`,
          onclick: () => { $("follow").checked = false; select(r.interaction_id); },
        },
        h("div", { class: "title" }, r.first_message || "(no message)"),
        h("div", { class: "meta" },
          live ? h("span", { class: "dot live", title: "active in the last 2 minutes" }) : null,
          h("span", {}, ago(r.last_at)),
          h("span", {}, `${r.turns} turn${r.turns === 1 ? "" : "s"}`),
          r.tickets ? h("span", { class: "chip ok" }, "ticket") : null,
          r.handoffs ? h("span", { class: "chip warn" }, "handoff") : null,
          ...(r.rule_ids || []).slice(0, 5).map((id) => ruleChip(id))));
      }));
    }

    function select(id) {
      if (selected !== id) { cursor = null; signature = ""; open.clear(); }
      selected = id;
      detailPoll.stop();
      detailPoll.start();
      listPoll.now();
    }

    async function loadDetail(id) {
      const data = await api(`/interactions/${encodeURIComponent(id)}`);
      if (id !== selected) return;
      const sig = `${data.events.length}|${data.tickets.length}|${data.handoffs.length}`;
      if (sig === signature) return; // nothing new; keep scroll and state
      signature = sig;
      detail = data;
      render();
    }

    function steps() {
      return detail ? detail.events : [];
    }

    function render() {
      const evs = steps();
      if (cursor != null && cursor >= evs.length) cursor = evs.length - 1;
      const shown = cursor == null ? evs : evs.slice(0, cursor + 1);
      const reached = new Set(shown.map(stageOf).filter(Boolean));
      const fired = new Set(shown.map((e) => e.rule_id).filter(Boolean));
      const current = cursor == null ? null : stageOf(evs[cursor]);

      const models = [...new Set(evs.map((e) => e.model).filter(Boolean))];
      const tokens = evs.reduce((n, e) => n + (e.tokens_in || 0) + (e.tokens_out || 0), 0);
      const cost = evs.reduce((n, e) => n + Number(e.cost_usd || 0), 0);

      const head = h("div", { class: "int-head" },
        h("h2", {}, "Conversation"),
        h("span", { class: "meta mono" }, detail.interaction_id),
        h("span", { class: "meta" }, `${clock(evs[0] && evs[0].created_at)} · ${evs.length} events · ${tokens} tokens${cost ? ` · $${cost.toFixed(4)}` : ""}`),
        h("span", { class: "meta" }, models.join(", ")));

      const flow = h("div", { class: "flow" }, STAGES.map((s) => h("div", {
        class: `stage ${reached.has(s.id) ? "reached" : ""} ${current === s.id ? "current" : ""}`,
      },
      h("div", { class: "name" }, s.name),
      h("div", { class: "rules" }, s.rules.map((r) => ruleChip(r, fired.has(r) ? "fired" : ""))))));

      const controls = h("div", { class: "controls" },
        h("button", { class: "btn", onclick: () => step(-1), disabled: cursor === 0 || !evs.length, title: "Previous step (←)" }, "← Prev"),
        h("span", { class: "count" }, cursor == null ? `${evs.length} steps` : `Step ${cursor + 1} of ${evs.length}`),
        h("button", { class: "btn", onclick: () => step(1), disabled: cursor === evs.length - 1 || !evs.length, title: "Next step (→)" }, "Next →"),
        h("span", { class: "spacer" }),
        cursor == null
          ? h("button", { class: "btn primary", onclick: () => { cursor = 0; render(); focusCurrent(); }, disabled: !evs.length }, "Walk through")
          : h("button", { class: "btn", onclick: () => { cursor = null; render(); } }, "Show all"));

      const timeline = h("div", { class: "timeline" });
      let turn = 0;
      evs.forEach((e, i) => {
        if (e.step === "input_screen") timeline.append(h("div", { class: "turn-label" }, `Turn ${++turn}`));
        timeline.append(eventCard(e, i));
      });

      const records = h("div", { class: "records" },
        detail.tickets.length || detail.handoffs.length ? h("h3", {}, "Records in the database") : null,
        detail.tickets.map(ticketCard),
        detail.handoffs.map(handoffCard));

      $("int-detail").replaceChildren(head, h("div", { class: "pinned" }, flow, controls), timeline, records);
    }

    function summary(e) {
      const d = e.data || {};
      const res = d.result || {};
      switch (e.step) {
        case "input_screen": return e.outcome === "clean" ? "no flags" : `flags: ${e.outcome}`;
        case "model": return e.outcome === "tool_calls" ? "decided to call tools" : e.outcome === "error" ? (e.text || "error") : "wrote a reply";
        case "tool:classifier": return res.code ? `${res.code} at ${Math.round((res.confidence || 0) * 100)}%` : d.error || "";
        case "tool:policy_lookup": return res.code ? `${res.code} → ${res.unit}; needs ${(res.required_fields || []).join(", ")}` : d.error || "";
        case "tool:search_tickets": return res.tickets ? `${res.tickets.length} open ticket(s) nearby` : d.error || "";
        case "tool:create_ticket": return res.ticket_id ? `created ${res.ticket_id}` : res.missing ? `missing: ${res.missing.join(", ")}` : d.error || e.text || "";
        case "tool:get_status": return res.status ? `status: ${res.status}` : d.error || e.text || "";
        case "tool:kb_search": return res.passages ? `${res.passages.length} passage(s): ${res.passages.map((p) => p.title).join(", ")}` : d.error || "";
        case "tool:handoff": return res.ref_id ? `${res.ref_id}: ${d.reason || ""}` : d.reason || e.text || "";
        case "output_check": return e.outcome || "";
        case "rule": return e.outcome || "";
        default: return short(e.text || e.outcome || "");
      }
    }

    function eventCard(e, i) {
      const stage = stageOf(e);
      const cls = cursor == null ? "" : i === cursor ? "current" : i > cursor ? "future" : "";
      const id = e.event_id || String(i);
      const pick = () => { cursor = i; render(); };

      if (e.step === "input_screen" || e.step === "reply") {
        const resident = e.step === "input_screen";
        return h("div", { class: `ev ${resident ? "resident" : "reply"} ${cls}`, "data-i": i, onclick: pick },
          h("div", { class: "who-label" },
            resident ? "Resident (masked)" : "Agent reply", " · ", clock(e.created_at),
            resident && e.outcome !== "clean" ? h("span", { class: "chip warn" }, e.outcome) : null,
            e.rule_id ? [" ", ruleChip(e.rule_id)] : null),
          h("div", { class: "bubble" }, e.text || ""));
      }

      const outcomeClass = e.outcome === "ok" || e.outcome === "reply" || e.outcome === "tool_calls" ? "ok"
        : e.outcome === "error" || e.outcome === "blocked" ? "err" : "warn";
      const body = e.data
        ? JSON.stringify(e.data, null, 2)
        : e.text || "(nothing logged)";
      const card = h("div", { class: `ev ${cls} ${open.has(id) ? "open" : ""}`, "data-i": i },
        h("div", {
          class: "ev-head",
          onclick: () => { open.has(id) ? open.delete(id) : open.add(id); card.classList.toggle("open"); if (cursor != null) pick(); },
        },
        h("span", { class: "ev-stage" }, stage ? STAGE_NAME[stage] : "Model"),
        h("span", { class: "ev-step mono" }, e.step.replace("tool:", "")),
        e.outcome ? h("span", { class: `chip ${outcomeClass}` }, e.outcome) : null,
        e.rule_id ? ruleChip(e.rule_id) : null,
        h("span", { class: "ev-sum" }, summary(e)),
        h("span", { class: "ev-time" },
          [e.latency_ms != null ? `${e.latency_ms} ms` : null, e.tokens_in || e.tokens_out ? `${e.tokens_in}→${e.tokens_out} tok` : null]
            .filter(Boolean).join(" · "))),
        h("div", { class: "ev-body" },
          e.model ? h("p", { class: "hint" }, `Model: ${e.model}`) : null,
          h("pre", {}, body)));
      return card;
    }

    function kv(obj) {
      return h("dl", { class: "kv" }, Object.entries(obj).flatMap(([k, v]) => [
        h("dt", {}, k), h("dd", {}, typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "")),
      ]));
    }

    function ticketCard(t) {
      return h("div", { class: "record" },
        h("div", { class: "row" }, h("strong", { class: "mono" }, t.ticket_id),
          h("span", { class: "chip ok" }, t.status), h("span", {}, `${t.code} → ${t.unit || ""}`),
          h("span", { class: "hint" }, clock(t.opened_at))),
        kv({ location: t.location, postal_code: t.postal_code, ...(t.fields || {}) }));
    }

    function handoffCard(o) {
      return h("div", { class: "record" },
        h("div", { class: "row" }, h("strong", { class: "mono" }, o.ref_id), ruleChip(o.rule_id),
          h("span", { class: "chip warn" }, o.status), h("span", {}, o.reason || ""),
          h("span", { class: "hint" }, clock(o.created_at))),
        kv({ code: o.code, confidence: o.confidence, ...(o.fields || {}),
          possible_duplicates: (o.possible_duplicates || []).map((d) => d.ticket_id).join(", ") || "none" }));
    }

    function step(delta) {
      const n = steps().length;
      if (!n) return;
      cursor = cursor == null ? 0 : Math.min(n - 1, Math.max(0, cursor + delta));
      render();
      focusCurrent();
    }

    function focusCurrent() {
      const el = document.querySelector(`#int-detail [data-i="${cursor}"]`);
      if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
    }

    document.addEventListener("keydown", (ev) => {
      if (currentTab !== "interactions" || !detail) return;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
      if (ev.key === "ArrowRight") { step(1); ev.preventDefault(); }
      if (ev.key === "ArrowLeft") { step(-1); ev.preventDefault(); }
      if (ev.key === "Escape" && cursor != null) { cursor = null; render(); }
    });
    $("follow").addEventListener("change", () => listPoll.now());

    return {
      start() { listPoll.start(); if (selected) detailPoll.start(); },
      stop() { listPoll.stop(); detailPoll.stop(); },
    };
  })();

  // =====================================================================
  // Files: config/ in the browser
  // =====================================================================
  const files = (() => {
    let current = null; // {path, content, repo_content, edited, in_use}
    let dirty = false;

    async function load() {
      const { files: rows } = await api("/files");
      const groups = [["Config", rows.filter((f) => !f.path.startsWith("prompts/"))],
        ["Prompts", rows.filter((f) => f.path.startsWith("prompts/"))]];
      $("file-list").replaceChildren(...groups.flatMap(([name, list]) => [
        h("div", { class: "pane-head" }, h("span", { class: "hint" }, name)),
        ...list.map((f) => h("button", {
          class: `item ${current && current.path === f.path ? "selected" : ""}`,
          onclick: () => openFile(f.path),
        },
        h("div", { class: "title mono" }, f.path),
        h("div", { class: "meta" },
          f.edited ? h("span", { class: "chip accent" }, "edited") : h("span", { class: "chip" }, "repo version"),
          !f.in_repo ? h("span", { class: "chip" }, "new") : null,
          f.edited ? h("span", {}, `${f.updated_by || "someone"} · ${ago(f.updated_at)}`) : null))),
      ]));
    }

    async function openFile(path) {
      if (dirty && !confirm("Discard your unsaved changes?")) return;
      current = await api(`/file?path=${encodeURIComponent(path)}`);
      dirty = false;
      renderEditor();
      load();
    }

    function renderEditor(errors, message) {
      const f = current;
      const text = h("textarea", { class: "code", spellcheck: "false", "aria-label": `Contents of ${f.path}` });
      text.value = f.content;
      const status = h("span", { class: message ? "saved" : "dirty" }, message || "");
      const note = h("input", { class: "note", placeholder: "What did you change, and why? (kept in the history)", maxlength: "500" });
      const save = h("button", { class: "btn primary", title: "Save (Ctrl+S)" }, "Save");
      const history = h("div", { class: "history", hidden: true });

      text.addEventListener("input", () => { dirty = true; status.className = "dirty"; status.textContent = "Unsaved changes"; });
      text.addEventListener("keydown", (ev) => {
        if (ev.key === "Tab") {
          ev.preventDefault();
          text.setRangeText("  ", text.selectionStart, text.selectionEnd, "end");
          text.dispatchEvent(new Event("input"));
        }
        if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") { ev.preventDefault(); save.click(); }
      });

      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await api(`/file?path=${encodeURIComponent(f.path)}`, {
            method: "PUT", body: JSON.stringify({ content: text.value, author: who(), note: note.value.trim() || null }),
          });
          current = await api(`/file?path=${encodeURIComponent(f.path)}`);
          dirty = false;
          renderEditor(null, "Saved. Live for new conversations now.");
          load();
        } catch (e) {
          current = { ...f, content: text.value };
          renderEditor(e.errors || [e.message]);
          dirty = true;
        }
      });

      const revert = h("button", {
        class: "btn danger", disabled: !f.edited,
        title: f.in_repo ? "Go back to the version in the git repo" : "Remove this browser-only file",
        onclick: async () => {
          if (!confirm(f.in_repo ? `Revert ${f.path} to the repo version? The current text stays in the history.` : `Remove ${f.path}? It stays in the history.`)) return;
          try {
            await api(`/file?path=${encodeURIComponent(f.path)}${who() ? `&author=${encodeURIComponent(who())}` : ""}`, { method: "DELETE" });
            dirty = false;
            if (f.in_repo) { current = await api(`/file?path=${encodeURIComponent(f.path)}`); renderEditor(null, "Reverted to the repo version."); }
            else { current = null; $("file-detail").replaceChildren(h("div", { class: "empty-state" }, "File removed.")); }
            load();
          } catch (e) { renderEditor(e.errors || [e.message]); }
        },
      }, f.in_repo ? "Revert to repo" : "Remove");

      const download = h("button", {
        class: "btn", title: "Download to commit it to git",
        onclick: () => {
          const a = h("a", { href: URL.createObjectURL(new Blob([text.value], { type: "text/plain" })), download: f.path.split("/").pop() });
          a.click();
          setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        },
      }, "Download");

      const histBtn = h("button", {
        class: "btn",
        onclick: async () => {
          if (!history.hidden) { history.hidden = true; return; }
          const { revisions } = await api(`/revisions?path=${encodeURIComponent(f.path)}`);
          history.replaceChildren(...(revisions.length ? revisions.map((r) => h("button", {
            class: "item",
            title: r.content == null ? "Reverted to the repo version" : "Load this version into the editor (then Save to restore it)",
            onclick: () => {
              text.value = r.content == null ? (f.repo_content || "") : r.content;
              text.dispatchEvent(new Event("input"));
            },
          },
          h("div", {}, r.note || (r.content == null ? "reverted to the repo version" : "(no note)")),
          h("div", { class: "meta" }, h("span", {}, r.author || "someone"), h("span", {}, new Date(r.saved_at).toLocaleString())))) : [h("p", { class: "hint pad" }, "No browser edits yet.")]));
          history.hidden = false;
        },
      }, "History");

      fill($("file-detail"),
        h("div", { class: "ed-head" },
          h("h2", {}, `config/${f.path}`),
          h("span", { class: "meta" }, f.edited ? "Edited in the browser (overrides the repo file)" : f.in_repo ? "Repo version" : "New file"),
          f.in_use ? h("span", { class: "chip ok", title: "The live agent reads this file" }, "in use") : h("span", { class: "chip", title: "Not read by the agent right now" }, "not in use")),
        errors ? h("div", { class: "errors", role: "alert" }, h("strong", {}, "Not saved: this would break the agent."), h("ul", {}, errors.map((e) => h("li", {}, e)))) : null,
        h("div", { class: "ed-actions" }, save, note, status),
        text,
        h("div", { class: "ed-actions" }, revert, download, histBtn,
          h("span", { class: "hint" }, "Saves go live for new conversations. Download a file to commit it to git.")),
        history);
      if (!errors) text.focus();
    }

    $("new-prompt").addEventListener("click", async () => {
      const name = prompt("New prompt path under config/, e.g. prompts/agent/system_v2.md", "prompts/agent/system_v2.md");
      if (!name) return;
      const path = name.replace(/^config\//, "").trim();
      if (!/^prompts\/[a-z0-9_]+\/[A-Za-z0-9_.-]+\.(md|txt)$/.test(path)) {
        alert("Use prompts/<folder>/<name>.md (lowercase folder; letters, numbers, _ . - in the name).");
        return;
      }
      let base = "";
      try { base = (await api(`/file?path=${encodeURIComponent("prompts/agent/system_v1.md")}`)).content; } catch { /* start empty */ }
      current = { path, content: base, repo_content: null, edited: false, in_repo: false, in_use: false };
      dirty = true;
      renderEditor(null, "");
    });

    window.addEventListener("beforeunload", (ev) => { if (dirty) { ev.preventDefault(); ev.returnValue = ""; } });
    return { load };
  })();

  // =====================================================================
  // Knowledge base
  // =====================================================================
  const kb = (() => {
    let current = null;
    let docs = [];
    const poll = poller(load, 8000);

    async function load() {
      ({ documents: docs } = await api("/kb"));
      $("kb-list").replaceChildren(...(docs.length ? docs.map((d) => {
        const pending = d.active && (!d.chunked_at || new Date(d.updated_at) > new Date(d.chunked_at));
        return h("button", {
          class: `item ${current && current.id === d.id ? "selected" : ""}`,
          onclick: () => openDoc(d.id),
        },
        h("div", { class: "title" }, d.title),
        h("div", { class: "meta" },
          !d.active ? h("span", { class: "chip" }, "hidden") : pending ? h("span", { class: "chip warn" }, "indexing…") : h("span", { class: "chip ok" }, "searchable"),
          h("span", { class: "mono" }, d.source),
          h("span", {}, ago(d.updated_at))));
      }) : [h("p", { class: "hint pad" }, "No documents yet.")]));
    }

    function openDoc(id) {
      current = docs.find((d) => d.id === id) || null;
      if (current) render();
      load();
    }

    function render(errors, message) {
      const d = current;
      const isNew = !d.id;
      const title = h("input", { value: d.title || "", maxlength: "300", "aria-label": "Title" });
      const source = h("input", { class: "mono", value: d.source || "", maxlength: "200", placeholder: "unique label (optional)", disabled: !isNew, "aria-label": "Source label" });
      const active = h("input", { type: "checkbox", checked: d.active !== false });
      const content = h("textarea", { class: "prose", "aria-label": "Content (Markdown)" });
      content.value = d.content || "";

      const save = h("button", {
        class: "btn primary",
        onclick: async () => {
          const payload = { title: title.value.trim(), content: content.value, active: active.checked, source: isNew ? (source.value.trim() || null) : d.source };
          try {
            const { document } = isNew
              ? await api("/kb", { method: "POST", body: JSON.stringify(payload) })
              : await api(`/kb/${encodeURIComponent(d.id)}`, { method: "PUT", body: JSON.stringify(payload) });
            current = document;
            render(null, active.checked ? "Saved. The agent can find it now." : "Saved. Hidden from the agent.");
            load();
          } catch (e) {
            current = { ...d, ...payload };
            render(e.errors || [e.message]);
          }
        },
      }, isNew ? "Create" : "Save");

      const del = isNew ? null : h("button", {
        class: "btn danger",
        onclick: async () => {
          if (!confirm(`Delete "${d.title}" for good? To hide it instead, untick "Searchable by the agent".`)) return;
          await api(`/kb/${encodeURIComponent(d.id)}`, { method: "DELETE" });
          current = null;
          $("kb-detail").replaceChildren(h("div", { class: "empty-state" }, "Document deleted."));
          load();
        },
      }, "Delete");

      fill($("kb-detail"),
        h("div", { class: "ed-head" }, h("h2", {}, isNew ? "New document" : d.title)),
        errors ? h("div", { class: "errors", role: "alert" }, h("strong", {}, "Not saved."), h("ul", {}, errors.map((e) => h("li", {}, typeof e === "string" ? e : JSON.stringify(e))))) : null,
        h("div", { class: "fields-row" },
          h("label", {}, "Title", title),
          h("label", {}, "Source", source),
          h("label", {}, active, "Searchable by the agent")),
        content,
        h("div", { class: "ed-actions" }, save, del, h("span", { class: "saved" }, message || ""),
          h("span", { class: "hint" }, "One topic per document; blank lines separate chunks. Synthetic content only.")));
    }

    $("new-doc").addEventListener("click", () => {
      current = { title: "", content: "# Title\n\n", active: true };
      render();
    });

    return { start: () => poll.start(), stop: () => poll.stop() };
  })();

  // ---------- boot ----------
  (async () => {
    try {
      const cfg = await fetch("/api/config").then((r) => r.json());
      $("site-title").textContent = cfg.site.title;
      document.title = `${cfg.site.title} · Admin`;
      RULES = (await api("/rules")).rules || {};
    } catch (e) { console.warn(e); }
    showTab(location.hash.slice(1));
  })();
})();
