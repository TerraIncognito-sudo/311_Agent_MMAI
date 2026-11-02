// Chat page. The OpenRouter key lives only in the `state` object below:
// never in localStorage, sessionStorage, cookies or the URL. Reloading the
// page drops it, by design.
(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    provider: null,
    model: { ollama: "", openrouter: "" },
    apiKey: "",
    interactionId: null,
    busy: false,
    defaults: {},
    modelCache: {},
  };

  const els = {
    input: $("input"), send: $("send"), messages: $("messages"), main: $("main"),
    modelBtn: $("model-btn"), modelLabel: $("model-label"), panel: $("model-panel"),
    keyRow: $("key-row"), apiKey: $("api-key"), modelInput: $("model-input"),
    modelList: $("model-list"), modelHint: $("model-hint"),
  };

  async function init() {
    const cfg = await fetch("/api/config").then((r) => r.json());
    document.title = cfg.site.title;
    $("city-title").textContent = cfg.site.title;
    $("topbar-title").textContent = cfg.site.title;
    $("tagline").textContent = cfg.site.tagline;
    els.input.placeholder = cfg.site.input_placeholder;
    state.defaults = cfg.default_models;
    state.model = { ...cfg.default_models };
    for (const btn of document.querySelectorAll(".seg button")) {
      if (!cfg.providers.includes(btn.dataset.provider)) btn.remove();
    }
    setProvider(cfg.default_provider);
    els.input.focus();
  }

  // ---------- model settings ----------
  function ready() {
    return state.model[state.provider] && (state.provider !== "openrouter" || state.apiKey);
  }

  function refreshModelButton() {
    const m = state.model[state.provider];
    const needsKey = state.provider === "openrouter" && !state.apiKey;
    els.modelLabel.textContent = needsKey ? "Add OpenRouter key" : (m || "Choose model");
    els.modelBtn.classList.toggle("needs-setup", !ready());
    updateSend();
  }

  async function setProvider(provider) {
    state.provider = provider;
    for (const btn of document.querySelectorAll(".seg button")) {
      btn.setAttribute("aria-checked", String(btn.dataset.provider === provider));
    }
    els.keyRow.hidden = provider !== "openrouter";
    els.modelInput.value = state.model[provider] || "";
    refreshModelButton();
    await loadModels(provider);
  }

  async function loadModels(provider) {
    els.modelHint.textContent = "";
    els.modelHint.classList.remove("error");
    els.modelList.innerHTML = "";
    if (!state.modelCache[provider]) {
      els.modelInput.placeholder = "Loading models...";
      try {
        const r = await fetch(`/api/models?provider=${provider}`);
        if (!r.ok) throw new Error((await r.json()).detail);
        state.modelCache[provider] = (await r.json()).models;
      } catch (e) {
        els.modelHint.textContent = provider === "ollama"
          ? "Can't reach the local Ollama server." : "Can't load the OpenRouter model list.";
        els.modelHint.classList.add("error");
        els.modelInput.placeholder = "Type a model id";
        return;
      }
    }
    if (provider !== state.provider) return;
    const models = state.modelCache[provider];
    const frag = document.createDocumentFragment();
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m.id;
      if (m.name !== m.id) opt.label = m.name;
      frag.appendChild(opt);
    }
    els.modelList.appendChild(frag);
    els.modelInput.placeholder = "Search models";
    els.modelHint.textContent = `${models.length} model${models.length === 1 ? "" : "s"} with tool support`;
    if (!state.model[provider] && models.length) {
      state.model[provider] = models[0].id;
      els.modelInput.value = models[0].id;
      refreshModelButton();
    }
  }

  function openPanel(open) {
    els.panel.hidden = !open;
    els.modelBtn.setAttribute("aria-expanded", String(open));
    if (open) (state.provider === "openrouter" && !state.apiKey ? els.apiKey : els.modelInput).focus();
  }

  els.modelBtn.addEventListener("click", () => openPanel(els.panel.hidden));
  $("model-done").addEventListener("click", () => { openPanel(false); els.input.focus(); });
  document.addEventListener("click", (e) => {
    if (!els.panel.hidden && !e.target.closest(".model-wrap")) openPanel(false);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") openPanel(false); });
  for (const btn of document.querySelectorAll(".seg button")) {
    btn.addEventListener("click", () => setProvider(btn.dataset.provider));
  }
  els.apiKey.addEventListener("input", () => { state.apiKey = els.apiKey.value.trim(); refreshModelButton(); });
  els.modelInput.addEventListener("input", () => {
    state.model[state.provider] = els.modelInput.value.trim();
    refreshModelButton();
  });

  // ---------- chat ----------
  function updateSend() {
    els.send.disabled = state.busy || !els.input.value.trim();
  }

  function addMessage(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = text;
    els.messages.appendChild(div);
    els.main.scrollTop = els.main.scrollHeight;
    return div;
  }

  async function send() {
    const text = els.input.value.trim();
    if (!text || state.busy) return;
    if (!ready()) { openPanel(true); return; }
    document.body.classList.remove("empty");
    addMessage("user", text);
    els.input.value = "";
    autoresize();
    state.busy = true;
    updateSend();
    const typing = addMessage("assistant", "");
    typing.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
    try {
      const headers = { "Content-Type": "application/json" };
      if (state.provider === "openrouter") headers["X-OpenRouter-Key"] = state.apiKey;
      const r = await fetch("/api/chat", {
        method: "POST",
        headers,
        body: JSON.stringify({
          interaction_id: state.interactionId,
          message: text,
          provider: state.provider,
          model: state.model[state.provider],
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed");
      state.interactionId = data.interaction_id;
      typing.textContent = data.reply;
    } catch (e) {
      typing.className = "msg assistant error";
      typing.textContent = `Couldn't get a reply: ${e.message}`;
    } finally {
      state.busy = false;
      updateSend();
      els.main.scrollTop = els.main.scrollHeight;
      els.input.focus();
    }
  }

  function autoresize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 200) + "px";
  }

  els.input.addEventListener("input", () => { autoresize(); updateSend(); });
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  els.send.addEventListener("click", send);
  $("new-chat").addEventListener("click", () => {
    state.interactionId = null;
    els.messages.innerHTML = "";
    document.body.classList.add("empty");
    els.input.focus();
  });

  init();
})();
