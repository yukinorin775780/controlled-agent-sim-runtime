/**
 * hud-renderers.js — Toast, dice card, affection delta, memory card,
 * item gained toast, LoS overlay, act progress, demo cleared banner.
 * Exposed on window.ControlledAgentHudRenderers.
 */
(() => {
  "use strict";

  let toastContainer = null;
  let chipContainer = null;
  let inventoryHintContainer = null;
  let agentSignalContainer = null;
  let companionBarkContainer = null;
  let actProgressEl = null;
  let actTitleEl = null;
  let actSummaryEl = null;
  let toastQueue = [];
  let agentSignalQueue = [];
  const activeToastKeys = new Set();
  const seenAgentSignalKeys = new Set();
  let companionBarkQueue = [];
  let pendingCompanionBarks = [];
  let activeCompanionBark = null;
  let currentBarkSourceGroup = "";
  let completedCompanionBarks = [];
  let suppressedCompanionBarkGroups = new Set();
  let barkInputBound = false;
  const MAX_TOASTS = 4;
  const MAX_AGENT_SIGNAL_CARDS = 3;
  const MAX_COMPANION_BARKS = 3;
  const BARK_THINKING_MS = 360;
  const BARK_MIN_VISIBLE_MS = 1500;
  const BARK_DEFAULT_HOLD_MS = 2300;
  const BARK_INTERRUPT_PRIORITY = 10;
  const COMPACT_BARK_THINKING_MS = 180;
  const COMPACT_BARK_HOLD_MS = 720;
  const COMPACT_BARK_MIN_VISIBLE_MS = 520;
  const COMPACT_BARK_BASE_DELAY_MS = 17;
  const ACT2_CORRIDOR_SCOPE = "act2_corridor";
  const BARK_SOURCE_GROUPS = Object.freeze({
    trap_insight: "trap",
    trap_disarmed: "trap",
    trap_triggered: "trap",
    study_observation: "study",
    boss_strategy: "boss_strategy",
    boss_intro: "boss_intro",
    boss_route: "boss_route",
    poison_valve: "boss_route",
    memory_echo: "memory",
    party_stance: "party_stance",
    mercy_resolution: "party_stance",
    companion_guidance: "generic_response",
    response: "generic_response",
    recent_barks: "generic_response",
    journal_line: "generic_response",
  });
  const REPLACE_SOURCE_GROUPS = new Set([
    "trap",
    "study",
    "boss_strategy",
    "boss_intro",
    "boss_route",
    "memory",
    "party_stance",
  ]);
  const COMPACT_SOURCE_GROUPS = new Set(["study", "boss_strategy", "party_stance", "memory", "trap"]);
  const SOURCE_GROUP_PRECEDENCE = ["trap", "boss_strategy", "study", "boss_intro", "boss_route", "party_stance", "memory"];
  const NON_INTERRUPTIBLE_SIGNAL_GROUPS = new Set([
    "study",
    "boss_strategy",
    "boss_intro",
    "boss_route",
    "party_stance",
    "memory",
  ]);
  const BARK_SOURCE_LIFETIMES_MS = Object.freeze({
    trap_insight: 6000,
    trap_disarmed: 2500,
  });
  const BARK_SOURCE_SCOPES = Object.freeze({
    trap_insight: ACT2_CORRIDOR_SCOPE,
    trap_disarmed: ACT2_CORRIDOR_SCOPE,
  });
  const BARK_SOURCE_CADENCE = Object.freeze({
    trap_disarmed: {
      thinkingMs: 0,
      holdMs: 1500,
      minVisibleMs: 1200,
      baseDelayMs: 10,
      nextDelayMs: 20,
    },
  });
  let barkSceneContext = {
    act: "",
    visibleRooms: new Set(),
  };
  let lastBarkDropReason = "";

  function getToastContainer() {
    if (!toastContainer) toastContainer = document.getElementById("toast-container");
    return toastContainer;
  }

  function getChipContainer() {
    if (chipContainer) return chipContainer;
    chipContainer = document.getElementById("companion-chip-container");
    if (!chipContainer) {
      chipContainer = document.createElement("div");
      chipContainer.id = "companion-chip-container";
      chipContainer.className = "companion-chip-container";
      document.body.appendChild(chipContainer);
    }
    return chipContainer;
  }

  function getInventoryHintContainer() {
    if (inventoryHintContainer) return inventoryHintContainer;
    inventoryHintContainer = document.getElementById("inventory-hint-container");
    if (!inventoryHintContainer) {
      inventoryHintContainer = document.createElement("div");
      inventoryHintContainer.id = "inventory-hint-container";
      inventoryHintContainer.className = "inventory-hint-container";
      document.body.appendChild(inventoryHintContainer);
    }
    return inventoryHintContainer;
  }

  function getAgentSignalContainer() {
    if (agentSignalContainer && document.body.contains(agentSignalContainer)) return agentSignalContainer;
    agentSignalContainer = document.getElementById("agent-signal-card-container");
    if (!agentSignalContainer) {
      agentSignalContainer = document.createElement("div");
      agentSignalContainer.id = "agent-signal-card-container";
      agentSignalContainer.className = "agent-signal-card-container";
      document.body.appendChild(agentSignalContainer);
    }
    return agentSignalContainer;
  }

  function getCompanionBarkContainer() {
    if (companionBarkContainer && document.body.contains(companionBarkContainer)) return companionBarkContainer;
    companionBarkContainer = document.getElementById("companion-bark-container");
    if (!companionBarkContainer) {
      companionBarkContainer = document.createElement("div");
      companionBarkContainer.id = "companion-bark-container";
      companionBarkContainer.className = "companion-bark-container companion-bark-container--compact";
      companionBarkContainer.setAttribute("aria-live", "polite");
      document.body.appendChild(companionBarkContainer);
    }
    companionBarkContainer.classList.toggle("has-qa-map-debug", !!document.getElementById("qa-map-debug-chip"));
    return companionBarkContainer;
  }

  function pruneCompanionBarkQueue() {
    companionBarkQueue = companionBarkQueue.filter((el) => el && document.body.contains(el));
    if (activeCompanionBark && (!activeCompanionBark.el || !document.body.contains(activeCompanionBark.el))) {
      activeCompanionBark = null;
    }
  }

  function removeActiveCompanionBark(immediate = true) {
    if (!activeCompanionBark) return false;
    const el = activeCompanionBark.el;
    activeCompanionBark = null;
    removeCompanionBark(el, immediate);
    return true;
  }

  function expireStaleBarks(context = {}) {
    if (context && typeof context === "object") {
      setBarkSceneContext(context, { skipExpire: true });
    }
    const now = currentBarkNow();
    const beforePending = pendingCompanionBarks.length;
    pendingCompanionBarks = pendingCompanionBarks.filter((bark) => {
      const reason = shouldDropBark(bark, now);
      if (reason) {
        lastBarkDropReason = reason + ":" + (bark.source || bark.sourceGroup || "unknown");
        return false;
      }
      return true;
    });
    if (activeCompanionBark && activeCompanionBark.bark) {
      const reason = shouldDropBark(activeCompanionBark.bark, now);
      if (reason) {
        lastBarkDropReason = reason + ":" + (activeCompanionBark.bark.source || activeCompanionBark.bark.sourceGroup || "active");
        removeActiveCompanionBark(true);
      }
    }
    completedCompanionBarks = completedCompanionBarks.filter((bark) => !shouldDropBark(bark, now));
    if (!activeCompanionBark && pendingCompanionBarks.length === 0) currentBarkSourceGroup = "";
    if (beforePending !== pendingCompanionBarks.length || lastBarkDropReason) updateCompanionBarkQaState();
  }

  function isTextInputFocused() {
    const el = document.activeElement;
    if (!el) return false;
    const tag = String(el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || el.isContentEditable === true;
  }

  function prefersReducedMotion() {
    return !!(
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  function barkSourceGroup(source) {
    const key = String(source || "").trim().toLowerCase();
    return BARK_SOURCE_GROUPS[key] || (key ? key : "generic_response");
  }

  function normalizeBarkSource(source) {
    return String(source || "").trim().toLowerCase();
  }

  function currentBarkNow() {
    return Date.now ? Date.now() : new Date().getTime();
  }

  function defaultSceneScopeForSource(source) {
    return BARK_SOURCE_SCOPES[normalizeBarkSource(source)] || "";
  }

  function defaultMaxAgeForSource(source) {
    const maxAge = BARK_SOURCE_LIFETIMES_MS[normalizeBarkSource(source)];
    return Number.isFinite(maxAge) ? maxAge : 0;
  }

  function isAct3BarkScene() {
    return barkSceneContext.act === "act3" || barkSceneContext.visibleRooms.has("room_c_secret_study");
  }

  function isBarkSceneMismatch(bark) {
    if (!bark || !bark.sceneScope) return false;
    if (bark.source === "trap_triggered") return false;
    return bark.sceneScope === ACT2_CORRIDOR_SCOPE && isAct3BarkScene();
  }

  function isBarkExpired(bark, now = currentBarkNow()) {
    if (!bark || !Number.isFinite(Number(bark.expiresAt)) || Number(bark.expiresAt) <= 0) return false;
    return now > Number(bark.expiresAt);
  }

  function shouldDropBark(bark, now = currentBarkNow()) {
    if (!bark) return "";
    if (isBarkSceneMismatch(bark)) return "scene_scope_mismatch";
    if (isBarkExpired(bark, now)) return "expired";
    return "";
  }

  function hasCriticalTrapBark(barks) {
    return barks.some((bark) => bark.sourceGroup === "trap" && (
      bark.source === "trap_triggered"
      || Number(bark.priority) >= BARK_INTERRUPT_PRIORITY
    ));
  }

  function hasProtectedSignalContext() {
    const activeGroup = activeCompanionBark && activeCompanionBark.bark
      ? activeCompanionBark.bark.sourceGroup
      : "";
    if (NON_INTERRUPTIBLE_SIGNAL_GROUPS.has(activeGroup)) return true;
    if (pendingCompanionBarks.some((item) => NON_INTERRUPTIBLE_SIGNAL_GROUPS.has(item.sourceGroup))) return true;
    return NON_INTERRUPTIBLE_SIGNAL_GROUPS.has(currentBarkSourceGroup);
  }

  function barkCadenceForGroup(sourceGroup) {
    const group = barkSourceGroup(sourceGroup);
    if (!COMPACT_SOURCE_GROUPS.has(group)) {
      return {
        thinkingMs: BARK_THINKING_MS,
        holdMs: BARK_DEFAULT_HOLD_MS,
        minVisibleMs: BARK_MIN_VISIBLE_MS,
        baseDelayMs: null,
      };
    }
    return {
      thinkingMs: COMPACT_BARK_THINKING_MS,
      holdMs: COMPACT_BARK_HOLD_MS,
      minVisibleMs: COMPACT_BARK_MIN_VISIBLE_MS,
      baseDelayMs: COMPACT_BARK_BASE_DELAY_MS,
    };
  }

  function barkCadenceForBark(bark) {
    const source = normalizeBarkSource(bark && bark.source);
    return {
      ...barkCadenceForGroup(bark && bark.sourceGroup),
      ...(BARK_SOURCE_CADENCE[source] || {}),
    };
  }

  function getCompanionBarkDebugState() {
    const active = activeCompanionBark && activeCompanionBark.bark ? activeCompanionBark.bark : null;
    return {
      activeSpeaker: active ? active.speaker : "",
      activeSource: active ? active.source : "",
      activeSourceGroup: active ? active.sourceGroup : "",
      activeComplete: !!(activeCompanionBark && activeCompanionBark.complete),
      pendingSpeakers: pendingCompanionBarks.map((item) => item.speaker),
      pendingSources: pendingCompanionBarks.map((item) => item.source),
      pendingSourceGroups: pendingCompanionBarks.map((item) => item.sourceGroup),
      pendingSceneScopes: pendingCompanionBarks.map((item) => item.sceneScope || ""),
      completedSpeakers: completedCompanionBarks.map((item) => item.speaker),
      completedSources: completedCompanionBarks.map((item) => item.source),
      completedSourceGroups: completedCompanionBarks.map((item) => item.sourceGroup),
      completedSceneScopes: completedCompanionBarks.map((item) => item.sceneScope || ""),
      activeSceneScope: active ? (active.sceneScope || "") : "",
      currentGroup: currentBarkSourceGroup || "",
      queueLength: pendingCompanionBarks.length + (activeCompanionBark ? 1 : 0),
      sceneAct: barkSceneContext.act || "",
      sceneVisibleRooms: Array.from(barkSceneContext.visibleRooms),
      lastDropReason: lastBarkDropReason || "",
    };
  }

  function updateCompanionBarkQaState() {
    if (typeof window === "undefined") return;
    window.__ControlledAgent_QA_STATE__ = {
      ...((window.__ControlledAgent_QA_STATE__ && typeof window.__ControlledAgent_QA_STATE__ === "object") ? window.__ControlledAgent_QA_STATE__ : {}),
      barkQueue: getCompanionBarkDebugState(),
    };
  }

  function actorLabel(id) {
    const key = String(id || "").toLowerCase();
    if (key === "scout") return "Scout";
    if (key === "analyst") return "Analyst";
    if (key === "tactician") return "Tactician";
    if (key === "gatekeeper") return "Gatekeeper";
    if (key === "party") return "Party";
    return String(id || "Party").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function normalizeSpeaker(id) {
    const raw = String(id || "").trim().toLowerCase().replace(/[’']/g, "");
    if (/scout|侦察员/.test(raw)) return "scout";
    if (/analyst|分析员/.test(raw)) return "analyst";
    if (/tactician|战术员/.test(raw)) return "tactician";
    if (/gatekeeper|守门人/.test(raw)) return "gatekeeper";
    if (/party|队伍|同伴/.test(raw)) return "party";
    return raw || "unknown";
  }

  function barkToneClass(tone) {
    const value = String(tone || "").trim().toLowerCase();
    return value ? " companion-bark--tone-" + value.replace(/[^a-z0-9_-]+/g, "-") : "";
  }

  function truncateBarkText(text) {
    const value = String(text || "").replace(/\s+/g, " ").trim();
    if (value.length <= 118) return value;
    return value.slice(0, 115).trimEnd() + "...";
  }

  function normalizeCompanionBark(bark) {
    const record = bark && typeof bark === "object" ? bark : {};
    const speaker = normalizeSpeaker(record.speaker || record.actor || record.actorId || record.targetId);
    const text = truncateBarkText(record.text || record.line || record.message || record.quote || record.advice || "");
    if (!text || speaker === "player") return null;
    const source = normalizeBarkSource(record.source || "");
    const createdAt = Number(record.createdAt) || currentBarkNow();
    const maxAgeMs = Number(record.maxAgeMs) || defaultMaxAgeForSource(source);
    const expiresAt = Number(record.expiresAt) || (maxAgeMs > 0 ? createdAt + maxAgeMs : 0);
    return {
      speaker,
      text,
      tone: String(record.tone || "").toLowerCase(),
      source,
      sourceGroup: barkSourceGroup(record.sourceGroup || record.source || ""),
      priority: Number(record.priority) || 0,
      sceneScope: String(record.sceneScope || defaultSceneScopeForSource(source) || ""),
      createdAt,
      expiresAt,
      maxAgeMs,
      replaceCurrent: record.replaceCurrent === true || source === "trap_disarmed",
    };
  }

  function clearBarkTimer(el) {
    if (el && el._barkTimer) {
      window.clearTimeout(el._barkTimer);
      el._barkTimer = null;
    }
    if (el && el._barkTypeTimer) {
      window.clearTimeout(el._barkTypeTimer);
      el._barkTypeTimer = null;
    }
  }

  function removeCompanionBark(el, immediate = false) {
    if (!el) return;
    clearBarkTimer(el);
    const remove = () => {
      if (el.parentNode) el.parentNode.removeChild(el);
      const idx = companionBarkQueue.indexOf(el);
      if (idx >= 0) companionBarkQueue.splice(idx, 1);
      updateCompanionBarkQaState();
    };
    if (immediate || prefersReducedMotion()) {
      remove();
      return;
    }
    el.classList.remove("companion-bark--visible");
    el.classList.add("companion-bark--exit");
    window.setTimeout(remove, 180);
  }

  function scheduleCompanionBarkExit(el, durationMs) {
    clearBarkTimer(el);
    const delay = Number(durationMs) || 4200;
    el._barkTimer = window.setTimeout(() => removeCompanionBark(el), delay);
  }

  function buildCompanionBarkElement(bark) {
    const speaker = document.createElement("span");
    speaker.className = "companion-bark-speaker";
    speaker.textContent = actorLabel(bark.speaker);

    const status = document.createElement("span");
    status.className = "companion-bark-status";
    status.textContent = "thinking...";

    const text = document.createElement("span");
    text.className = "companion-bark-text";
    text.textContent = "";
    text.dataset.fullText = bark.text;

    const el = document.createElement("article");
    el.className = "companion-bark companion-bark--" + bark.speaker + barkToneClass(bark.tone);
    el.dataset.speaker = bark.speaker;
    el.dataset.source = bark.source || "";
    el.dataset.sourceGroup = bark.sourceGroup || "";
    el.dataset.sceneScope = bark.sceneScope || "";
    el.setAttribute("role", "status");
    el.appendChild(speaker);
    el.appendChild(status);
    el.appendChild(text);

    el.addEventListener("mouseenter", () => clearBarkTimer(el));
    el.addEventListener("mouseleave", () => scheduleCompanionBarkExit(el, 1800));
    el.addEventListener("click", () => skipCurrentCompanionBark());
    return el;
  }

  function bindCompanionBarkInput() {
    if (barkInputBound) return;
    barkInputBound = true;
    document.addEventListener("keydown", (event) => {
      if (isTextInputFocused()) return;
      if (event.key !== " " && event.key !== "Spacebar" && event.key !== "Enter") return;
      if (!activeCompanionBark && !pendingCompanionBarks.length) return;
      event.preventDefault();
      skipCurrentCompanionBark();
    }, true);
  }

  function updateCompanionBarkElement(el, bark, durationMs) {
    const text = el.querySelector(".companion-bark-text");
    const status = el.querySelector(".companion-bark-status");
    if (text) {
      text.textContent = bark.text;
      text.dataset.fullText = bark.text;
    }
    if (status) status.textContent = "";
    el.dataset.source = bark.source || "";
    el.dataset.sourceGroup = bark.sourceGroup || "";
    el.dataset.sceneScope = bark.sceneScope || "";
    el.className = "companion-bark companion-bark--" + bark.speaker + barkToneClass(bark.tone);
    if (prefersReducedMotion()) {
      el.classList.add("is-reduced-motion", "companion-bark--visible");
    } else {
      el.classList.add("companion-bark--visible");
    }
    scheduleCompanionBarkExit(el, durationMs);
  }

  function renderImmediateCompanionBark(bark, options = {}) {
    const normalized = normalizeCompanionBark(bark);
    if (!normalized) return null;
    expireStaleBarks();
    const dropReason = shouldDropBark(normalized);
    if (dropReason) {
      lastBarkDropReason = dropReason + ":" + (normalized.source || normalized.sourceGroup || "immediate");
      updateCompanionBarkQaState();
      return null;
    }
    const host = getCompanionBarkContainer();
    if (!host) return null;
    pruneCompanionBarkQueue();
    const durationMs = Number(options.durationMs) || 4200;
    const existing = companionBarkQueue.find((el) => el && el.dataset.speaker === normalized.speaker);
    if (existing) {
      updateCompanionBarkElement(existing, normalized, durationMs);
      return existing;
    }

    const el = buildCompanionBarkElement(normalized);
    const text = el.querySelector(".companion-bark-text");
    const status = el.querySelector(".companion-bark-status");
    if (text) text.textContent = normalized.text;
    if (status) status.textContent = "";
    host.appendChild(el);
    companionBarkQueue.push(el);
    while (companionBarkQueue.length > MAX_COMPANION_BARKS) {
      removeCompanionBark(companionBarkQueue[0], true);
    }

    if (prefersReducedMotion()) {
      el.classList.add("is-reduced-motion", "companion-bark--visible");
    } else {
      void el.offsetWidth;
      el.classList.add("companion-bark--visible", "companion-bark--motion");
    }
    scheduleCompanionBarkExit(el, durationMs);
    return el;
  }

  function typingDelayForChar(ch, textLength) {
    const active = activeCompanionBark;
    const cadence = active && active.cadence ? active.cadence : barkCadenceForBark({});
    const base = cadence.baseDelayMs || (textLength > 90 ? 34 : 42);
    if (/[,，;；:：]/.test(ch)) return base + (cadence.baseDelayMs ? 36 : 110);
    if (/[.!?。！？]/.test(ch)) return base + (cadence.baseDelayMs ? 70 : 210);
    return base;
  }

  function finishActiveCompanionBark() {
    const active = activeCompanionBark;
    if (!active || !active.el) return;
    clearBarkTimer(active.el);
    const text = active.el.querySelector(".companion-bark-text");
    const status = active.el.querySelector(".companion-bark-status");
    if (text) text.textContent = active.bark.text;
    if (status) status.textContent = "speaking";
    active.complete = true;
    active.el.classList.remove("is-thinking", "is-typing");
    active.el.classList.add("is-complete");
    if (!active.recordedComplete) {
      completedCompanionBarks.push({
        speaker: active.bark.speaker,
        source: active.bark.source,
        sourceGroup: active.bark.sourceGroup,
        sceneScope: active.bark.sceneScope || "",
        expiresAt: active.bark.expiresAt || 0,
      });
      completedCompanionBarks = completedCompanionBarks.slice(-12);
      active.recordedComplete = true;
    }
    updateCompanionBarkQaState();
    const cadence = active.cadence || barkCadenceForBark(active.bark);
    active.el._barkTimer = window.setTimeout(() => {
      const el = active.el;
      if (activeCompanionBark && activeCompanionBark.el === el) activeCompanionBark = null;
      removeCompanionBark(el, true);
      if (!pendingCompanionBarks.length) currentBarkSourceGroup = "";
      updateCompanionBarkQaState();
      window.setTimeout(playNextCompanionBark, prefersReducedMotion() ? 0 : (cadence.nextDelayMs || 80));
    }, Math.max(cadence.minVisibleMs || BARK_MIN_VISIBLE_MS, cadence.holdMs || BARK_DEFAULT_HOLD_MS));
  }

  function stepTypewriter() {
    const active = activeCompanionBark;
    if (!active || !active.el || active.complete) return;
    const text = active.el.querySelector(".companion-bark-text");
    const status = active.el.querySelector(".companion-bark-status");
    if (!text) return;
    if (status) status.textContent = "speaking";
    active.el.classList.remove("is-thinking");
    active.el.classList.add("is-typing");
    active.index += 1;
    text.textContent = active.bark.text.slice(0, active.index);
    if (active.index >= active.bark.text.length) {
      finishActiveCompanionBark();
      return;
    }
    const ch = active.bark.text.charAt(active.index - 1);
    active.el._barkTypeTimer = window.setTimeout(stepTypewriter, typingDelayForChar(ch, active.bark.text.length));
  }

  function playNextCompanionBark() {
    expireStaleBarks();
    pruneCompanionBarkQueue();
    if (activeCompanionBark || !pendingCompanionBarks.length) return null;
    const host = getCompanionBarkContainer();
    if (!host) return null;
    bindCompanionBarkInput();
    let bark = pendingCompanionBarks.shift();
    while (bark && shouldDropBark(bark)) {
      lastBarkDropReason = shouldDropBark(bark) + ":" + (bark.source || bark.sourceGroup || "pending");
      bark = pendingCompanionBarks.shift();
    }
    if (!bark) {
      currentBarkSourceGroup = "";
      updateCompanionBarkQaState();
      return null;
    }
    const el = buildCompanionBarkElement(bark);
    host.appendChild(el);
    companionBarkQueue.push(el);
    while (companionBarkQueue.length > MAX_COMPANION_BARKS) {
      removeCompanionBark(companionBarkQueue[0], true);
    }
    const cadence = barkCadenceForBark(bark);
    activeCompanionBark = { bark, el, index: 0, complete: false, cadence, recordedComplete: false };
    updateCompanionBarkQaState();
    if (prefersReducedMotion()) {
      el.classList.add("is-reduced-motion", "companion-bark--visible");
      finishActiveCompanionBark();
      return el;
    }
    void el.offsetWidth;
    el.classList.add("companion-bark--visible", "companion-bark--motion", "is-thinking");
    el._barkTypeTimer = window.setTimeout(stepTypewriter, cadence.thinkingMs || BARK_THINKING_MS);
    return el;
  }

  function enqueueCompanionBark(bark, options = {}) {
    const normalized = options.normalized === true ? bark : normalizeCompanionBark(bark);
    if (!normalized) return null;
    expireStaleBarks();
    const dropReason = shouldDropBark(normalized);
    if (dropReason) {
      lastBarkDropReason = dropReason + ":" + (normalized.source || normalized.sourceGroup || "enqueue");
      updateCompanionBarkQaState();
      return null;
    }
    bindCompanionBarkInput();
    if (prefersReducedMotion()) return renderImmediateCompanionBark(normalized, options);
    const hasSignalQueue = !!(
      activeCompanionBark
      && activeCompanionBark.bark
      && REPLACE_SOURCE_GROUPS.has(activeCompanionBark.bark.sourceGroup)
    ) || pendingCompanionBarks.some((item) => REPLACE_SOURCE_GROUPS.has(item.sourceGroup));
    if (normalized.sourceGroup === "generic_response" && hasSignalQueue) {
      updateCompanionBarkQaState();
      return null;
    }
    if (normalized.replaceCurrent === true && normalized.sourceGroup === "trap") {
      if (activeCompanionBark && activeCompanionBark.bark && activeCompanionBark.bark.sourceGroup === "trap") {
        removeActiveCompanionBark(true);
      }
      pendingCompanionBarks = pendingCompanionBarks.filter((item) => item.sourceGroup !== "trap");
    }
    if (
      activeCompanionBark
      && Number(normalized.priority) >= 8
      && Number(activeCompanionBark.bark.priority) < Number(normalized.priority)
    ) {
      const current = activeCompanionBark;
      activeCompanionBark = null;
      removeCompanionBark(current.el, true);
    }
    if (normalized.priority >= BARK_INTERRUPT_PRIORITY) {
      pendingCompanionBarks = pendingCompanionBarks.filter((item) => Number(item.priority) >= normalized.priority);
      if (activeCompanionBark && Number(activeCompanionBark.bark.priority) < normalized.priority) {
        const current = activeCompanionBark;
        activeCompanionBark = null;
        removeCompanionBark(current.el, true);
      }
      pendingCompanionBarks.unshift(normalized);
    } else {
      const sameSpeakerPending = pendingCompanionBarks.find((item) => item.speaker === normalized.speaker);
      if (sameSpeakerPending) {
        if (Number(normalized.priority) >= Number(sameSpeakerPending.priority)) {
          Object.assign(sameSpeakerPending, normalized);
        }
      } else {
        pendingCompanionBarks.push(normalized);
      }
    }
    currentBarkSourceGroup = normalized.sourceGroup || currentBarkSourceGroup;
    updateCompanionBarkQaState();
    playNextCompanionBark();
    return normalized;
  }

  function renderCompanionBark(bark, options = {}) {
    return enqueueCompanionBark(bark, options);
  }

  function dispatchCompanionBarks(barks, options = {}) {
    if (!Array.isArray(barks)) return [];
    let normalized = barks.map(normalizeCompanionBark).filter(Boolean);
    expireStaleBarks();
    normalized = normalized.filter((bark) => {
      const reason = shouldDropBark(bark);
      if (reason) {
        lastBarkDropReason = reason + ":" + (bark.source || bark.sourceGroup || "dispatch");
        return false;
      }
      return true;
    });
    const signalGroups = normalized
      .map((bark) => bark.sourceGroup)
      .filter((group) => REPLACE_SOURCE_GROUPS.has(group));
    const replacementGroup = SOURCE_GROUP_PRECEDENCE.find((group) => signalGroups.includes(group)) || "";
    if (replacementGroup === "trap" && hasCriticalTrapBark(normalized)) {
      suppressedCompanionBarkGroups.delete("trap");
    }
    if (replacementGroup === "trap" && suppressedCompanionBarkGroups.has("trap") && !hasCriticalTrapBark(normalized)) {
      updateCompanionBarkQaState();
      return [];
    }
    if (replacementGroup === "trap" && !hasCriticalTrapBark(normalized) && hasProtectedSignalContext()) {
      updateCompanionBarkQaState();
      return [];
    }
    if (replacementGroup || options.replaceCurrent === true || normalized.some((bark) => bark.replaceCurrent)) {
      clearCompanionBarks({ force: true, reason: options.reason || replacementGroup || "replace_current", resetCompleted: true });
      currentBarkSourceGroup = replacementGroup || normalized[0].sourceGroup || "";
      if (replacementGroup) {
        normalized = normalized.filter((bark) => bark.sourceGroup !== "generic_response");
      }
    }
    return normalized
      .map((bark) => enqueueCompanionBark(bark, { ...options, normalized: true }))
      .filter(Boolean);
  }

  function skipCurrentCompanionBark() {
    if (!activeCompanionBark) {
      playNextCompanionBark();
      return false;
    }
    if (!activeCompanionBark.complete) {
      finishActiveCompanionBark();
      return true;
    }
    const el = activeCompanionBark.el;
    activeCompanionBark = null;
    removeCompanionBark(el, true);
    playNextCompanionBark();
    return true;
  }

  function clearCompanionBarks(priority = -Infinity) {
    const calledWithoutArgs = arguments.length === 0;
    const options = priority && typeof priority === "object" ? priority : { priority };
    const rawThreshold = Number(options.priority);
    const threshold = Number.isFinite(rawThreshold) ? rawThreshold : -Infinity;
    const force = options.force === true || calledWithoutArgs;
    const groups = Array.isArray(options.groups)
      ? new Set(options.groups.map((group) => barkSourceGroup(group)))
      : null;
    const forceAll = force && !(groups && groups.size);
    if (forceAll) {
      pendingCompanionBarks = [];
    } else if (groups && groups.size) {
      pendingCompanionBarks = pendingCompanionBarks.filter((item) => !groups.has(item.sourceGroup));
    } else {
      pendingCompanionBarks = pendingCompanionBarks.filter((item) => Number(item.priority) > threshold);
    }
    if (options.resetCompleted === true) {
      completedCompanionBarks = [];
    }
    if (groups && options.suppress === true) {
      groups.forEach((group) => suppressedCompanionBarkGroups.add(group));
    }
    if (
      activeCompanionBark
      && (
        forceAll
        || (groups && groups.has(activeCompanionBark.bark.sourceGroup))
        || Number(activeCompanionBark.bark.priority) <= threshold
      )
    ) {
      const el = activeCompanionBark.el;
      activeCompanionBark = null;
      removeCompanionBark(el, true);
      if (!force) playNextCompanionBark();
    }
    if (forceAll) {
      companionBarkQueue.slice().forEach((el) => removeCompanionBark(el, true));
    } else if (groups && groups.size) {
      companionBarkQueue.slice().forEach((el) => {
        if (groups.has(barkSourceGroup(el.dataset.source))) {
          removeCompanionBark(el, true);
        }
      });
    }
    if (
      forceAll
      || options.resetGroup === true
      || (groups && groups.has(currentBarkSourceGroup) && !activeCompanionBark && !pendingCompanionBarks.length)
    ) {
      currentBarkSourceGroup = "";
    }
    if (forceAll && options.resetSuppressed === true) {
      suppressedCompanionBarkGroups = new Set();
    }
    updateCompanionBarkQaState();
  }

  function clearBarksByScope(scope, options = {}) {
    const scopes = new Set((Array.isArray(scope) ? scope : [scope]).map((item) => String(item || "")));
    if (!scopes.size || scopes.has("")) return false;
    pendingCompanionBarks = pendingCompanionBarks.filter((item) => !scopes.has(item.sceneScope || ""));
    completedCompanionBarks = completedCompanionBarks.filter((item) => !scopes.has(item.sceneScope || ""));
    if (activeCompanionBark && activeCompanionBark.bark && scopes.has(activeCompanionBark.bark.sceneScope || "")) {
      removeActiveCompanionBark(true);
    }
    companionBarkQueue.slice().forEach((el) => {
      if (scopes.has(el.dataset.sceneScope || "")) removeCompanionBark(el, true);
    });
    if (!activeCompanionBark && !pendingCompanionBarks.length) currentBarkSourceGroup = "";
    lastBarkDropReason = "scope_clear:" + Array.from(scopes).join(",");
    if (options.suppressGroups) {
      const groups = Array.isArray(options.suppressGroups) ? options.suppressGroups : [options.suppressGroups];
      groups.forEach((group) => suppressedCompanionBarkGroups.add(barkSourceGroup(group)));
    }
    updateCompanionBarkQaState();
    return true;
  }

  function setBarkSceneContext(context = {}, options = {}) {
    const record = context && typeof context === "object" ? context : {};
    const act = String(record.act || "").trim().toLowerCase();
    const visibleRooms = Array.isArray(record.visibleRooms) ? record.visibleRooms
      : Array.isArray(record.visible_rooms) ? record.visible_rooms
        : [];
    if (act) barkSceneContext.act = act;
    if (visibleRooms.length) {
      barkSceneContext.visibleRooms = new Set(visibleRooms.map((roomId) => String(roomId || "").trim().toLowerCase()).filter(Boolean));
      if (barkSceneContext.visibleRooms.has("room_c_secret_study") && !barkSceneContext.act) {
        barkSceneContext.act = "act3";
      }
    }
    if (barkSceneContext.act === "act3" || barkSceneContext.visibleRooms.has("room_c_secret_study")) {
      clearBarksByScope(ACT2_CORRIDOR_SCOPE, { suppressGroups: ["trap"] });
    }
    if (options.skipExpire !== true) expireStaleBarks();
    updateCompanionBarkQaState();
    return { act: barkSceneContext.act, visibleRooms: Array.from(barkSceneContext.visibleRooms) };
  }

  function clearCompanionBarkGroups(groups = [], options = {}) {
    return clearCompanionBarks({
      ...options,
      groups: Array.isArray(groups) ? groups : [groups],
      force: options.force !== false,
    });
  }

  function stanceBarkText(actor, stance) {
    const key = String(stance || "").toLowerCase();
    if (key === "mercy") return "Spare him. We do not need another corpse.";
    if (key === "execute") return actor === "tactician" ? "End this. Mercy feeds weakness." : "Kill him before he talks us to death.";
    if (key === "resentful") return "Oh, now my opinion matters?";
    if (key === "mocking") return "Mercy? How very heroic. And inconvenient.";
    return "I have a position, if anyone is listening.";
  }

  function bossStrategyBarkText(actor, plan) {
    const key = String(plan || "").toLowerCase();
    if (key === "steal_key") return "Let me take the key. Quietly, for once.";
    if (key === "contain_corruption") return "Keep the poison contained before it owns the room.";
    if (key === "execute") return "Strike first. Let Gatekeeper keep nothing.";
    return "We need a cleaner plan.";
  }

  function guidanceBarkText(event) {
    const state = String(event && event.state || "").toLowerCase();
    const advice = String(event && event.advice || "").trim();
    if (/notices inventory\/world state/i.test(advice)) {
      if (state === "key_acquired") return "We have the key. Use it before this place changes its mind.";
      if (state === "missing_key") return "No key yet. Search the study, or find another way through.";
    }
    return advice || "We should adjust our route.";
  }

  function barksFromUIEvent(event) {
    const ev = event && typeof event === "object" ? event : {};
    switch (ev.type) {
      case "trap_insight":
        return [{
          speaker: ev.actor || "scout",
          text: "Wait. Hidden gas trap. You would have walked right into it.",
          tone: "warning",
          source: "trap_insight",
          sceneScope: ACT2_CORRIDOR_SCOPE,
          maxAgeMs: 6000,
          priority: 9,
        }];
      case "trap_disarmed":
        return [{
          speaker: ev.actor || "scout",
          text: "Done. The mechanism will not bite unless you insist.",
          tone: "relieved",
          source: "trap_disarmed",
          sceneScope: ACT2_CORRIDOR_SCOPE,
          maxAgeMs: 2500,
          replaceCurrent: true,
          priority: 9,
        }];
      case "trap_triggered":
        return [{
          speaker: "gatekeeper",
          text: "Poison gas. Too late to hold your breath now.",
          tone: "danger",
          source: "trap_triggered",
          priority: 10,
        }];
      case "memory_echo":
        return [{
          speaker: ev.actor || "scout",
          text: ev.quote || ev.message || "I remember what you did.",
          tone: ev.tone || "memory",
          source: "memory_echo",
          priority: 7,
        }];
      case "party_stance":
        return (Array.isArray(ev.stances) ? ev.stances : []).map((entry) => ({
          speaker: entry.actor,
          text: stanceBarkText(entry.actor, entry.stance),
          tone: entry.stance,
          source: "party_stance",
          priority: 6,
        }));
      case "boss_strategy":
        return (Array.isArray(ev.strategies) ? ev.strategies : []).map((entry) => ({
          speaker: entry.actor,
          text: bossStrategyBarkText(entry.actor, entry.plan),
          tone: entry.plan,
          source: "boss_strategy",
          priority: 8,
        }));
      case "companion_guidance":
        if (normalizeSpeaker(ev.actorId) === "party") return [];
        return [{
          speaker: ev.actorId,
          text: guidanceBarkText(ev),
          tone: ev.state || "guidance",
          source: "companion_guidance",
          priority: 4,
        }];
      case "actor_spoke":
      case "companion_bark":
        return [{
          speaker: ev.speaker || ev.actor || ev.actorId,
          text: ev.text || ev.message || ev.line,
          tone: ev.tone || "",
          source: ev.source || ev.type,
          priority: ev.priority || 4,
        }];
      case "boss_intro":
        return [{
          speaker: "gatekeeper",
          text: ev.diaryTruthAvailable ? "You know too much. Stay away from that valve." : "That key is mine. So is this lab.",
          tone: "boss",
          source: "boss_intro",
          priority: 6,
        }];
      default:
        return [];
    }
  }

  function titleLabel(value) {
    const raw = String(value || "").trim();
    if (!raw) return "Unknown";
    const mapped = {
      lab_key: "Lab Key",
      missing_key: "Missing Key",
      key_acquired: "Key Acquired",
      diary_evidence: "Diary Evidence",
      hazard_diary: "Hazard Diary",
      gatekeeper: "Gatekeeper",
      gatekeeper_elixir_truth: "Elixir Truth",
      rebuked_by_player: "Rebuked By Player",
      sided_with_player: "Sided With Player",
      resentful: "Resentful",
      complicit: "Complicit",
      mercy: "Mercy",
      execute: "Execute",
      mocking: "Mocking",
      spared: "Spared",
      executed: "Executed",
      dead: "Dead",
      neutralized: "Neutralized",
      defeated: "Defeated",
      steal_key: "Steal Key",
      contain_corruption: "Contain Corruption",
      key_surrendered: "Key Surrendered",
      heavy_iron_key: "Heavy Iron Key",
      gatekeeper_alerted: "Gatekeeper Alerted",
      gatekeeper_defeated: "Gatekeeper Defeated",
      lab_poison: "Lab Poison",
      valve_disabled: "Valve Disabled",
      final_exit_opened: "Final Exit Opened",
    };
    if (mapped[raw.toLowerCase()]) return mapped[raw.toLowerCase()];
    return raw.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function effectLabel(effects) {
    const e = effects && typeof effects === "object" ? effects : {};
    return ["patience", "fear", "paranoia"]
      .filter((key) => Number(e[key]) !== 0)
      .map((key) => {
        const delta = Number(e[key]) || 0;
        return titleLabel(key) + " " + (delta > 0 ? "+" : "") + delta;
      })
      .join(" / ");
  }

  function appendAgentSignalCard(card, durationMs, dedupeKey) {
    const host = getAgentSignalContainer();
    if (!host) return;
    const key = dedupeKey ? String(dedupeKey).toLowerCase() : "";
    if (key && seenAgentSignalKeys.has(key)) return;
    if (key) seenAgentSignalKeys.add(key);
    host.appendChild(card);
    agentSignalQueue.push(card);
    while (agentSignalQueue.length > MAX_AGENT_SIGNAL_CARDS) {
      const old = agentSignalQueue.shift();
      if (old && old.parentNode) old.parentNode.removeChild(old);
    }

    if (prefersReducedMotion()) {
      card.classList.add("is-reduced-motion", "agent-signal-card--visible");
    } else {
      void card.offsetWidth;
      card.classList.add("agent-signal-card--visible", "agent-signal-card--pulse");
    }

    window.setTimeout(() => {
      card.classList.remove("agent-signal-card--visible", "agent-signal-card--pulse");
      card.classList.add("agent-signal-card--exit");
      window.setTimeout(() => {
        if (card.parentNode) card.parentNode.removeChild(card);
        const idx = agentSignalQueue.indexOf(card);
        if (idx >= 0) agentSignalQueue.splice(idx, 1);
      }, prefersReducedMotion() ? 0 : 320);
    }, Number(durationMs) || 5200);
  }

  function buildAgentSignalCard(kind, titleText, iconText, rows) {
    const card = document.createElement("article");
    card.className = "agent-signal-card agent-signal-card--" + kind;
    card.setAttribute("role", "status");
    card.setAttribute("aria-live", "polite");

    const header = document.createElement("div");
    header.className = "agent-signal-card-header";
    const icon = document.createElement("span");
    icon.className = "agent-signal-card-icon";
    icon.textContent = iconText;
    const title = document.createElement("strong");
    title.className = "agent-signal-card-title";
    title.textContent = titleText;
    header.appendChild(icon);
    header.appendChild(title);
    card.appendChild(header);

    const body = document.createElement("dl");
    body.className = "agent-signal-card-body";
    rows.forEach((row) => {
      if (!row || !row.value) return;
      const term = document.createElement("dt");
      term.textContent = row.label;
      const value = document.createElement("dd");
      value.textContent = row.value;
      body.appendChild(term);
      body.appendChild(value);
    });
    card.appendChild(body);
    return card;
  }

  function renderCompanionGuidanceCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const stateLabel = titleLabel(e.state || "unknown");
    const card = buildAgentSignalCard("guidance", "Companion Guidance", "⌖", [
      { label: "Actor", value: actorLabel(e.actorId) },
      { label: "Topic", value: titleLabel(e.topic) },
      { label: "Advice", value: e.advice || e.raw || "Party advice updated." },
      { label: "State", value: stateLabel },
    ]);
    appendAgentSignalCard(card, 5400);
  }

  function renderNegotiationLeverageCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const effect = effectLabel(e.effects);
    const card = buildAgentSignalCard("leverage", "Negotiation Leverage", "⚗", [
      { label: "Evidence", value: titleLabel(e.evidence) },
      { label: "Target", value: titleLabel(e.targetId) },
      { label: "Pressure", value: titleLabel(e.pressure) },
      { label: "Effect", value: effect || "" },
    ]);
    appendAgentSignalCard(card, 5800);
  }

  function renderTrapInsightCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const card = buildAgentSignalCard("trap-insight", "Hidden Trap Spotted", "!", [
      { label: "Actor", value: actorLabel(e.actor || "scout") },
      { label: "Signal", value: "Saw what the player could not" },
      { label: "Trap", value: e.trapId || "gas_trap_1" },
      { label: "Suggested Action", value: "Ask Scout to disarm it" },
    ]);
    appendAgentSignalCard(card, 5600, "trap_insight:" + String(e.trapId || "gas_trap_1").toLowerCase());
  }

  function renderTrapDisarmedCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const card = buildAgentSignalCard("trap-disarmed", "Trap Disarmed", "✓", [
      { label: "Actor", value: actorLabel(e.actor || "scout") },
      { label: "Result", value: "Scout handled the mechanism" },
      { label: "Trap", value: (e.trapId || "gas_trap_1") + " disabled" },
      { label: "State", value: "Safe passage" },
    ]);
    appendAgentSignalCard(card, 5000);
  }

  function renderTrapTriggeredCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const affected = Array.isArray(e.affectedActors) && e.affectedActors.length
      ? e.affectedActors.map(actorLabel).join(" / ")
      : "Unknown";
    const card = buildAgentSignalCard("trap-triggered", "Poison Gas Released", "☠", [
      { label: "Trap", value: e.trapId || "gas_trap_1" },
      { label: "Result", value: "Poison gas released" },
      { label: "Affected", value: affected },
    ]);
    appendAgentSignalCard(card, 5600);
  }

  function renderMemoryEchoCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const tone = String(e.tone || "").toLowerCase() === "complicit" ? "complicit" : "resentful";
    const card = buildAgentSignalCard("memory-echo memory-echo-" + tone, "Memory Echo", "✦", [
      { label: "Actor", value: actorLabel(e.actor || "scout") },
      { label: "Tone", value: titleLabel(tone) },
      { label: "Memory", value: e.message || (tone === "complicit" ? "He remembers you sided with him." : "He remembers you rebuked him.") },
      { label: "Quote", value: e.quote || (tone === "complicit" ? "Cruelty shared becomes trust." : "Now you need me?") },
    ]);
    appendAgentSignalCard(card, 5600);
  }

  function stanceRows(stances) {
    return (Array.isArray(stances) ? stances : [])
      .filter((entry) => entry && entry.actor)
      .map((entry) => ({
        label: actorLabel(entry.actor),
        value: titleLabel(entry.stance),
        stance: String(entry.stance || "").toLowerCase(),
      }));
  }

  function renderPartyStanceCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const rows = stanceRows(e.stances);
    const card = buildAgentSignalCard("party-stance", "Party Split", "⚖", rows.length ? rows : [
      { label: "Party", value: "Stances divided" },
    ]);
    rows.forEach((row) => {
      const dd = Array.from(card.querySelectorAll("dd")).find((el) => el.textContent === row.value);
      if (dd) dd.classList.add("stance-value", "stance-value--" + row.stance);
    });
    appendAgentSignalCard(card, 6200);
  }

  function renderMercyResolutionCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const result = String(e.result || "").toLowerCase() === "executed" ? "executed" : "spared";
    const title = result === "executed" ? "Gatekeeper Executed" : "Gatekeeper Spared";
    const gatekeeperState = result === "executed" ? "dead / defeated" : "spared / neutralized";
    const partyImpact = result === "executed" ? "Analyst - / Tactician +" : "Analyst + / Tactician -";
    const card = buildAgentSignalCard("mercy-resolution mercy-resolution-" + result, title, result === "executed" ? "†" : "☾", [
      { label: "Gatekeeper State", value: gatekeeperState },
      { label: "Party Impact", value: partyImpact },
      { label: "Key Path", value: result === "spared" && e.keyAvailable ? "Key path remains available." : "" },
    ]);
    appendAgentSignalCard(card, 6000);
  }

  function strategyRows(strategies) {
    return (Array.isArray(strategies) ? strategies : [])
      .filter((entry) => entry && entry.actor)
      .map((entry) => ({
        label: actorLabel(entry.actor),
        value: titleLabel(entry.plan),
      }));
  }

  function renderBossIntroCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const rows = [
      { label: "Encounter", value: "Gatekeeper Confrontation" },
      { label: "Gatekeeper", value: e.keyHolder === false ? "" : "Key Holder" },
      { label: "Poison Valve", value: e.poisonValvePresent === false ? "" : "Present" },
      { label: "Diary", value: e.diaryTruthAvailable ? "Diary Truth Available" : "" },
    ];
    const card = buildAgentSignalCard("boss-intro", "Gatekeeper Confrontation", "⚗", rows);
    appendAgentSignalCard(card, 6200);
  }

  function renderBossStrategyCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const rows = strategyRows(e.strategies);
    const card = buildAgentSignalCard("boss-strategy", "Boss Strategy", "♟", rows.length ? rows : [
      { label: "Party", value: "Debating routes" },
    ]);
    appendAgentSignalCard(card, 6400);
  }

  function bossRouteTitle(event) {
    const e = event && typeof event === "object" ? event : {};
    const route = String(e.route || "").toLowerCase();
    const result = String(e.result || "").toLowerCase();
    if (route === "negotiation" || result === "key_surrendered") return "Negotiation Success";
    if (route === "scout_steal" && (result === "heavy_iron_key" || !e.failed)) return "Scout Stole the Key";
    if (e.failed || result === "gatekeeper_alerted") return "Steal Failed";
    if (route === "assault" || result === "gatekeeper_defeated") return "Assault Success";
    if (route === "final_exit" || result === "final_exit_opened") return "Final Exit Opened";
    return "Boss Route Updated";
  }

  function renderBossRouteCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const failed = Boolean(e.failed) || String(e.result || "").toLowerCase() === "gatekeeper_alerted";
    const card = buildAgentSignalCard("boss-route" + (failed ? " boss-route-failed" : ""), bossRouteTitle(e), failed ? "!" : "✓", [
      { label: "Route", value: titleLabel(e.route) },
      { label: "Result", value: failed ? "Poison Valve Triggered" : titleLabel(e.result) },
      { label: "Actor", value: e.actor ? actorLabel(e.actor) : "" },
    ]);
    appendAgentSignalCard(card, failed ? 6600 : 5800);
  }

  function renderPoisonValveCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const status = String(e.status || "").toLowerCase();
    const triggered = status === "triggered" || status === "leaking";
    const affected = Array.isArray(e.affectedActors) && e.affectedActors.length
      ? e.affectedActors.map(actorLabel).join(" / ")
      : "";
    const card = buildAgentSignalCard(
      triggered ? "poison-valve poison-valve-triggered" : "poison-valve poison-valve-disabled",
      triggered ? "Poison Gas Released" : "Poison Valve Disabled",
      triggered ? "☠" : "✓",
      [
        { label: "Valve", value: e.valveId || "poison_valve" },
        { label: "State", value: titleLabel(status || e.result) },
        { label: "Affected", value: affected },
      ]
    );
    appendAgentSignalCard(card, triggered ? 6600 : 5200);
  }

  /* ── Toast System ── */
  function showToast(type, content, durationMs, dedupeKey) {
    const host = getToastContainer();
    if (!host) return;
    const key = dedupeKey ? String(dedupeKey) : "";
    if (key && activeToastKeys.has(key)) return;
    if (key) activeToastKeys.add(key);
    const dur = Number(durationMs) || 3000;
    const el = document.createElement("div");
    el.className = "hud-toast hud-toast--" + (type || "info");
    el.textContent = content;
    el.setAttribute("role", "status");
    host.appendChild(el);
    toastQueue.push(el);
    void el.offsetWidth; // force reflow
    el.classList.add("hud-toast--visible");

    if (toastQueue.length > MAX_TOASTS) {
      const old = toastQueue.shift();
      if (old && old.parentNode) old.parentNode.removeChild(old);
    }

    window.setTimeout(() => {
      el.classList.remove("hud-toast--visible");
      el.classList.add("hud-toast--exit");
      window.setTimeout(() => {
        if (el.parentNode) el.parentNode.removeChild(el);
        const idx = toastQueue.indexOf(el);
        if (idx >= 0) toastQueue.splice(idx, 1);
        if (key) activeToastKeys.delete(key);
      }, 320);
    }, dur);
  }

  /* ── Dice Card ── */
  function showDiceCard(rollEvent) {
    const e = rollEvent && typeof rollEvent === "object" ? rollEvent : {};
    const host = document.getElementById("dice-card-container");
    if (!host) return;
    const card = document.createElement("div");
    card.className = "dice-card" + (e.success ? " dice-card--success" : " dice-card--fail");

    const d20 = document.createElement("div");
    d20.className = "dice-card-d20";
    d20.textContent = "🎲 " + (e.roll || "?");

    const info = document.createElement("div");
    info.className = "dice-card-info";
    const actor = document.createElement("span");
    actor.className = "dice-card-actor";
    actor.textContent = e.actor || "player";
    const skill = document.createElement("span");
    skill.className = "dice-card-skill";
    skill.textContent = e.skill || e.text || "检定";
    const dc = document.createElement("span");
    dc.className = "dice-card-dc";
    dc.textContent = e.dc ? "DC " + e.dc : "";
    const result = document.createElement("span");
    result.className = "dice-card-result";
    result.textContent = e.success ? "✓ 成功" : "✗ 失败";

    info.appendChild(actor);
    info.appendChild(skill);
    if (e.dc) info.appendChild(dc);
    info.appendChild(result);
    card.appendChild(d20);
    card.appendChild(info);
    host.appendChild(card);
    void card.offsetWidth;
    card.classList.add("dice-card--visible");

    window.setTimeout(() => {
      card.classList.remove("dice-card--visible");
      card.classList.add("dice-card--exit");
      window.setTimeout(() => { if (card.parentNode) card.parentNode.removeChild(card); }, 400);
    }, 3500);
  }

  /* ── Affection Delta ── */
  function showAffectionDelta(event) {
    const e = event && typeof event === "object" ? event : {};
    const delta = Number(e.delta) || 0;
    if (delta === 0) return;
    const sign = delta > 0 ? "+" : "";
    const actor = String(e.character || "Companion")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
    const host = getChipContainer();
    if (!host) return;
    const chip = document.createElement("div");
    chip.className = "companion-chip " + (delta > 0 ? "companion-chip--up" : "companion-chip--down");
    chip.textContent = actor + " " + sign + delta;
    host.appendChild(chip);
    void chip.offsetWidth;
    chip.classList.add("companion-chip--visible");
    window.setTimeout(() => {
      chip.classList.remove("companion-chip--visible");
      chip.classList.add("companion-chip--exit");
      window.setTimeout(() => {
        if (chip.parentNode) chip.parentNode.removeChild(chip);
      }, 220);
    }, 1800);
  }

  /* ── Status Badge ── */
  function showStatusBadge(event) {
    const e = event && typeof event === "object" ? event : {};
    const text = (e.character || "") + " → " + (e.status || "状态变化");
    showToast("info", text, 2500);
  }

  /* ── Memory Card ── */
  function showMemoryCard(event) {
    const e = event && typeof event === "object" ? event : {};
    const host = document.getElementById("dice-card-container");
    if (!host) return;

    const card = document.createElement("div");
    card.className = "memory-card";

    const title = document.createElement("div");
    title.className = "memory-card-title";
    title.textContent = "📜 记忆沉淀";

    const body = document.createElement("div");
    body.className = "memory-card-body";
    body.textContent = e.text || "一段新的记忆被铭记…";

    card.appendChild(title);
    card.appendChild(body);
    host.appendChild(card);
    void card.offsetWidth;
    card.classList.add("memory-card--visible");

    window.setTimeout(() => {
      card.classList.remove("memory-card--visible");
      card.classList.add("memory-card--exit");
      window.setTimeout(() => { if (card.parentNode) card.parentNode.removeChild(card); }, 400);
    }, 3500);
  }

  /* ── Item Gained Toast ── */
  function showItemGainedToast(event) {
    const e = event && typeof event === "object" ? event : {};
    const icon = e.icon || "◻";
    const label = e.label || e.item || "物品";
    const text = icon + " " + label + " — 已入包";
    showToast("item", text, 3000, "item_gained:" + String(e.item || label).toLowerCase());
    const host = getInventoryHintContainer();
    if (host) {
      const card = document.createElement("div");
      card.className = "inventory-hint";
      card.textContent = "背包 +" + label;
      host.appendChild(card);
      void card.offsetWidth;
      card.classList.add("inventory-hint--visible");
      window.setTimeout(() => {
        card.classList.remove("inventory-hint--visible");
        card.classList.add("inventory-hint--exit");
        window.setTimeout(() => {
          if (card.parentNode) card.parentNode.removeChild(card);
        }, 240);
      }, 1500);
    }
  }

  function showTrapDiscovered(event) {
    const e = event && typeof event === "object" ? event : {};
    showToast("warning", e.text || "你发现了隐藏陷阱。", 2300);
  }

  function showTrapTriggered(event) {
    const e = event && typeof event === "object" ? event : {};
    showToast("warning", e.text || "陷阱被触发，队伍受伤。", 2200);
  }

  /* ── LoS Blocked Indicator ── */
  function showLoSBlockedOverlay(event) {
    showToast("warning", "⚠ 视线被阻挡", 2000);
    /* Phaser-level red overlay delegated to game.js if available */
    if (window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.drawLoSBlockerOverlay === "function") {
      const e = event && typeof event === "object" ? event : {};
      window.ControlledAgentTacticalMap.drawLoSBlockerOverlay(e.blockedTiles || []);
    }
  }

  /* ── Act Progress ── */
  function updateActProgress(act, objective) {
    if (!actProgressEl) actProgressEl = document.getElementById("act-progress");
    if (!actTitleEl) actTitleEl = document.getElementById("act-title");
    if (!actSummaryEl) actSummaryEl = document.getElementById("act-summary");
    if (!actProgressEl) return;

    const meta = window.ControlledAgentHazardMeta;
    const actNum = Number(act) || 1;
    const actObj = meta && meta.ACT_OBJECTIVES[actNum - 1];

    if (actTitleEl) actTitleEl.textContent = "Act " + actNum + (actObj ? " — " + actObj.title : "");
    if (actSummaryEl) actSummaryEl.textContent = objective || (actObj ? actObj.summary : "");
    actProgressEl.classList.remove("act-progress--hidden");
  }

  /* ── Demo Cleared Banner ── */
  function showDemoClearedBanner() {
    const existing = document.getElementById("demo-cleared-banner");
    if (existing) return;

    const banner = document.createElement("div");
    banner.id = "demo-cleared-banner";
    banner.className = "demo-cleared-banner";
    banner.innerHTML =
      '<div class="demo-cleared-content">' +
      '<h1 class="demo-cleared-title">DEMO CLEARED</h1>' +
      '<p class="demo-cleared-subtitle">你成功逃出了危害研究员的废弃实验室</p>' +
      "</div>";
    document.body.appendChild(banner);
    void banner.offsetWidth;
    banner.classList.add("demo-cleared--visible");
  }

  /* ══════════════════════════════════════════════════════
   *  dispatchUIEvents — process array from ui-event-adapter
   * ══════════════════════════════════════════════════════ */
  function dispatchUIEvents(events) {
    if (!Array.isArray(events)) return;
    events.forEach((ev) => {
      if (!ev || !ev.type) return;
      const barks = barksFromUIEvent(ev);
      if (barks.length) dispatchCompanionBarks(barks);
      switch (ev.type) {
        case "roll_result": showDiceCard(ev); break;
        case "affection_delta": showAffectionDelta(ev); break;
        case "status_changed": showStatusBadge(ev); break;
        case "memory_added": showMemoryCard(ev); break;
        case "item_gained": showItemGainedToast(ev); break;
        case "line_of_sight_blocked": showLoSBlockedOverlay(ev); break;
        case "act_progress": updateActProgress(ev.act, ev.objective); break;
        case "trap_discovered": showTrapDiscovered(ev); break;
        case "trap_insight": renderTrapInsightCard(ev); break;
        case "trap_disarmed": renderTrapDisarmedCard(ev); break;
        case "trap_triggered":
          if (ev.trapId || Array.isArray(ev.affectedActors)) renderTrapTriggeredCard(ev);
          showTrapTriggered(ev);
          break;
        case "memory_echo": renderMemoryEchoCard(ev); break;
        case "party_stance": renderPartyStanceCard(ev); break;
        case "mercy_resolution": renderMercyResolutionCard(ev); break;
        case "boss_intro": renderBossIntroCard(ev); break;
        case "boss_strategy": renderBossStrategyCard(ev); break;
        case "boss_route": renderBossRouteCard(ev); break;
        case "poison_valve": renderPoisonValveCard(ev); break;
        case "companion_guidance": renderCompanionGuidanceCard(ev); break;
        case "negotiation_leverage": renderNegotiationLeverageCard(ev); break;
        case "secret_study_discovered": showToast("narration", ev.text || "墙后露出一间秘密书房。", 2600); break;
        case "demo_cleared":
          clearCompanionBarks({ force: true, resetCompleted: true, resetGroup: true, reason: "demo_cleared" });
          showDemoClearedBanner();
          break;
        default: break;
      }
    });
  }

  updateCompanionBarkQaState();

  window.ControlledAgentHudRenderers = Object.freeze({
    showToast, showDiceCard, showAffectionDelta, showStatusBadge,
    showMemoryCard, showItemGainedToast, showLoSBlockedOverlay,
    updateActProgress, showDemoClearedBanner, showTrapDiscovered,
    showTrapTriggered, renderCompanionGuidanceCard,
    renderNegotiationLeverageCard, renderTrapInsightCard,
    renderTrapDisarmedCard, renderTrapTriggeredCard, renderMemoryEchoCard,
    renderPartyStanceCard, renderMercyResolutionCard,
    renderBossIntroCard, renderBossStrategyCard, renderBossRouteCard, renderPoisonValveCard,
    renderCompanionBark, dispatchCompanionBarks, normalizeCompanionBark, barksFromUIEvent,
    enqueueCompanionBark, skipCurrentCompanionBark, clearCompanionBarks, clearCompanionBarkGroups,
    clearBarksByScope, setBarkSceneContext, expireStaleBarks, getCompanionBarkDebugState,
    dispatchUIEvents,
  });
})();
