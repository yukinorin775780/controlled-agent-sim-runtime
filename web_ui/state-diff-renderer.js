/**
 * state-diff-renderer.js
 * Targeted world-state diff renderer for showcase mode.
 * Exposed on window.ControlledAgentStateDiffRenderer.
 */
(() => {
  "use strict";

  const AUTO_COLLAPSE_MS = 4200;
  let panel = null;
  let body = null;
  let badge = null;
  let toggle = null;
  let autoTimer = null;
  let latestDiffs = [];

  function safeObj(value) {
    return value && typeof value === "object" ? value : {};
  }

  function safeArr(value) {
    return Array.isArray(value) ? value : [];
  }

  function normalizeId(value) {
    return String(value || "").trim().toLowerCase();
  }

  function clone(value) {
    try {
      return JSON.parse(JSON.stringify(value == null ? null : value));
    } catch (_err) {
      return value;
    }
  }

  function flagValue(raw) {
    const value = safeObj(raw).value;
    if (Object.prototype.hasOwnProperty.call(safeObj(raw), "value")) return value;
    return raw;
  }

  function valuesEqual(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  function setFrom(value) {
    if (value instanceof Set) return new Set(Array.from(value).map(String));
    return new Set(safeArr(value).map(String).filter(Boolean));
  }

  function inventoryMap(value) {
    const out = {};
    Object.entries(safeObj(value)).forEach(([key, raw]) => {
      const count = Number(raw) || 0;
      if (count !== 0) out[key] = count;
    });
    return out;
  }

  function dynamicValue(actor, key) {
    const record = safeObj(actor);
    const states = safeObj(record.dynamic_states || record.dynamicStates);
    const raw = states[key] ?? record[key];
    if (raw && typeof raw === "object") {
      const current = Number(raw.current_value ?? raw.current ?? raw.value);
      return Number.isFinite(current) ? current : null;
    }
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  }

  function statusEffectLabel(effect) {
    if (typeof effect === "string") return effect;
    const record = safeObj(effect);
    return String(record.type || record.id || record.name || record.status || "").trim();
  }

  function normalizeActorMemories(actorState) {
    const actor = safeObj(actorState);
    const notes = [];
    safeArr(actor.memory_notes).forEach((item) => notes.push(String(item || "")));
    safeArr(actor.memories).forEach((item) => {
      if (typeof item === "string") notes.push(item);
      else if (safeObj(item).text) notes.push(String(safeObj(item).text));
      else if (safeObj(item).note) notes.push(String(safeObj(item).note));
    });
    return notes.filter(Boolean);
  }

  function normalizeSnapshot(raw) {
    const src = safeObj(raw);
    const gameState = safeObj(src.game_state || src.gameState || src.state || {});
    const appState = safeObj(src.app_state || src.appState || {});
    const normalizedMap = safeObj(src.normalizedMap || appState.normalizedMap || {});
    const mapData = safeObj(src.map_data || src.mapData || appState.mapData || {});
    const roomVisibleIds = src.roomVisibleIds || appState.roomVisibleIds;
    const party = safeObj(src.party_status || src.partyStatus || gameState.party_status || gameState.entities || {});
    const env = safeObj(src.environment_objects || src.environmentObjects || gameState.environment_objects || gameState.entities || {});
    const actorRuntime = safeObj(src.actor_runtime_state || gameState.actor_runtime_state || {});
    const journalEvents = [
      ...safeArr(src.journal_events),
      ...safeArr(gameState.journal_events),
    ];
    const flags = synthesizeAct4BossFlags(
      synthesizeMercyFlags(
        synthesizeMemoryEchoFlags(safeObj(src.flags || gameState.flags || {}), journalEvents),
        journalEvents
      ),
      journalEvents
    );
    const inventory = inventoryMap(src.player_inventory || src.playerInventory || gameState.player_inventory || {});
    const combat = safeObj(src.combat_state || src.combatState || gameState.combat_state || {
      combat_active: src.combat_active || gameState.combat_active,
      initiative_order: src.initiative_order || gameState.initiative_order,
    });

    const visibleRooms = setFrom(
      roomVisibleIds || normalizedMap.visibleRooms || normalizedMap.visible_rooms || mapData.visible_rooms || mapData.visibleRooms
    );

    const actors = {};
    Object.entries({ ...env, ...party }).forEach(([id, value]) => {
      const actor = safeObj(value);
      actors[normalizeId(id)] = {
        id: normalizeId(id),
        name: actor.name || id,
        affection: Number(actor.affection),
        faction: normalizeId(actor.faction),
        status: actor.status,
        status_effects: safeArr(actor.status_effects || actor.statusEffects).map(statusEffectLabel).filter(Boolean),
        inventory: inventoryMap(actor.inventory),
        agentSignals: {
          patience: dynamicValue(actor, "patience"),
          fear: dynamicValue(actor, "fear"),
          paranoia: dynamicValue(actor, "paranoia"),
        },
      };
    });

    const memories = {};
    Object.entries(actorRuntime).forEach(([id, runtime]) => {
      const notes = normalizeActorMemories(runtime);
      if (notes.length) memories[normalizeId(id)] = notes;
    });

    return {
      visibleRooms,
      flags: clone(flags) || {},
      inventory,
      actors,
      combat: clone(combat) || {},
      memories,
      demo_cleared: src.demo_cleared === true || gameState.demo_cleared === true,
    };
  }

  function actorLabel(id, actor) {
    const raw = String(safeObj(actor).name || id || "actor");
    if (normalizeId(raw) === "scout") return "Scout";
    if (normalizeId(raw) === "analyst") return "Analyst";
    if (normalizeId(raw).replace(/[’']/g, "") === "tactician") return "Tactician";
    if (normalizeId(raw) === "gatekeeper") return "Gatekeeper";
    return raw.replace(/_/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  function shortMemory(text) {
    const raw = String(text || "").trim();
    if (!raw) return "memory_note";
    return "memory_note";
  }

  function pushDiff(out, type, label, detail) {
    out.push({ type, label, detail: detail || "" });
  }

  function isTrapFlag(key) {
    return /^hazard_lab_poison_trap_/.test(String(key || ""));
  }

  function isMemoryEchoFlag(key) {
    return /^hazard_lab_scout_(?:memory_echo_seen|rebuke_echo_seen|complicity_echo_seen)$/.test(String(key || ""));
  }

  function isMercyFlag(key) {
    return /^hazard_lab_gatekeeper_(?:mercy_window|mercy_resolved|spared|executed|key_available)$/.test(String(key || ""));
  }

  function isAct3StudyFlag(key) {
    return /^act3_(?:secret_study_entered|secret_study_discovered|chemical_notes_seen|key_sketch_seen|diary_context_gathered|diary_read|diary_decoded|gatekeeper_potion_truth_known|heavy_key_hint_known|party_knows_gatekeeper_truth)$/.test(String(key || ""));
  }

  function isAct4BossFlag(key) {
    return /^act4_(?:boss_room_entered|gatekeeper_confrontation_started|diary_truth_available|heavy_iron_key_obtained|poison_valve_triggered|lab_poison_leak|poison_valve_disabled|scout_steal_key_success|negotiation_success|assault_success|final_exit_opened)$/.test(String(key || ""));
  }

  function synthesizeMemoryEchoFlags(flags, journalEvents) {
    const out = { ...safeObj(flags) };
    safeArr(journalEvents).forEach((line) => {
      const text = String(line || "");
      if (!/\[记忆回响\]\s*scout\s*->/i.test(text)) return;
      out.hazard_lab_scout_memory_echo_seen = true;
      if (/rebuked_by_player/i.test(text)) out.hazard_lab_scout_rebuke_echo_seen = true;
      if (/sided_with_player/i.test(text)) out.hazard_lab_scout_complicity_echo_seen = true;
    });
    return out;
  }

  function synthesizeMercyFlags(flags, journalEvents) {
    const out = { ...safeObj(flags) };
    safeArr(journalEvents).forEach((line) => {
      const text = String(line || "");
      if (/\[站队\]\s*[a-z0-9_'’\-]+\s*->\s*(mercy|execute|resentful|mocking)/i.test(text)) {
        out.hazard_lab_gatekeeper_mercy_window = true;
      }
      const decision = text.match(/\[抉择\]\s*(?:gatekeeper|守门人)\s*->\s*(spared|executed)/i);
      if (!decision) return;
      out.hazard_lab_gatekeeper_mercy_resolved = true;
      if (String(decision[1]).toLowerCase() === "spared") out.hazard_lab_gatekeeper_spared = true;
      if (String(decision[1]).toLowerCase() === "executed") out.hazard_lab_gatekeeper_executed = true;
    });
    return out;
  }

  function synthesizeAct4BossFlags(flags, journalEvents) {
    const out = { ...safeObj(flags) };
    safeArr(journalEvents).forEach((line) => {
      const text = String(line || "");
      if (/\[Boss Encounter\]\s*gatekeeper_confrontation_started/i.test(text)) {
        out.act4_boss_room_entered = true;
        out.act4_gatekeeper_confrontation_started = true;
      }
      if (/\[Boss方案\]\s*[a-z0-9_'’\-]+\s*->\s*(steal_key|contain_corruption|execute)/i.test(text)) {
        out.act4_boss_room_entered = true;
      }
      const route = text.match(/\[Boss解决\]\s*(negotiation|scout_steal|assault)\s*->\s*([a-z0-9_\-]+)/i);
      if (route) {
        if (String(route[1]).toLowerCase() === "negotiation") out.act4_negotiation_success = true;
        if (String(route[1]).toLowerCase() === "scout_steal") out.act4_scout_steal_key_success = true;
        if (String(route[1]).toLowerCase() === "assault") out.act4_assault_success = true;
        if (/heavy_iron_key|key_surrendered/i.test(route[2])) out.act4_heavy_iron_key_obtained = true;
      }
      if (/\[偷钥匙失败\]\s*scout\s*->\s*gatekeeper_alerted/i.test(text)) {
        out.act4_poison_valve_triggered = true;
      }
      if (/\[毒气泄漏\]\s*(poison_valve|potion_tank)\s*->\s*lab_poison/i.test(text)) {
        out.act4_lab_poison_leak = true;
      }
    });
    return out;
  }

  function diffSnapshots(previousRaw, nextRaw) {
    const prev = normalizeSnapshot(previousRaw);
    const next = normalizeSnapshot(nextRaw);
    const out = [];

    next.visibleRooms.forEach((roomId) => {
      if (!prev.visibleRooms.has(roomId)) pushDiff(out, "visibleRooms", "visibleRooms += " + roomId);
    });

    Object.entries(next.flags).forEach(([key, raw]) => {
      const before = flagValue(prev.flags[key]);
      const after = flagValue(raw);
      if (!valuesEqual(before, after)) {
        const type = isTrapFlag(key)
          ? "trap_signal"
          : (isMemoryEchoFlag(key) ? "memory_echo_signal" : (isMercyFlag(key) ? "mercy_signal" : (isAct4BossFlag(key) ? "boss_signal" : (isAct3StudyFlag(key) ? "act3_study_signal" : "flags"))));
        pushDiff(out, type, "flags." + key + " = " + JSON.stringify(after));
      }
    });

    Object.entries(next.inventory).forEach(([itemId, count]) => {
      const prevCount = Number(prev.inventory[itemId]) || 0;
      const delta = Number(count) - prevCount;
      if (delta > 0) pushDiff(out, "inventory", "player.inventory += " + itemId + (delta > 1 ? " x" + delta : ""));
      if (delta < 0) pushDiff(out, "inventory", "player.inventory -= " + itemId + (delta < -1 ? " x" + Math.abs(delta) : ""));
    });

    Object.entries(next.actors).forEach(([id, actor]) => {
      const before = safeObj(prev.actors[id]);
      const label = actorLabel(id, actor);
      const prevAff = Number(before.affection);
      const nextAff = Number(actor.affection);
      if (Number.isFinite(prevAff) && Number.isFinite(nextAff) && prevAff !== nextAff) {
        const delta = nextAff - prevAff;
        pushDiff(out, "affection", label + ".affection " + (delta > 0 ? "+" : "") + delta);
      }

      const prevStatus = String(before.status || "").trim();
      const nextStatus = String(actor.status || "").trim();
      if (nextStatus && nextStatus !== prevStatus) {
        const isTrap = id === "gas_trap_1" || /trap/i.test(id);
        const isBossContext = Object.keys(next.flags).some(isAct4BossFlag) || Object.keys(prev.flags).some(isAct4BossFlag);
        const isMercyTarget = id === "gatekeeper";
        const statusLabel = prevStatus
          ? label + ".status " + prevStatus + " -> " + nextStatus
          : label + ".status += " + nextStatus;
        pushDiff(out, isTrap ? "trap_signal" : (isMercyTarget ? (isBossContext ? "boss_signal" : "mercy_signal") : "status"), statusLabel);
      }

      const prevEffects = new Set(safeArr(before.status_effects).map(String));
      safeArr(actor.status_effects).forEach((effect) => {
        if (!prevEffects.has(effect)) pushDiff(out, normalizeId(effect) === "poisoned" ? "trap_signal" : "status", label + ".status += " + effect);
      });

      const prevFaction = String(before.faction || "");
      if (actor.faction && prevFaction && actor.faction !== prevFaction) {
        const isBossContext = Object.keys(next.flags).some(isAct4BossFlag) || Object.keys(prev.flags).some(isAct4BossFlag);
        pushDiff(out, id === "gatekeeper" ? (isBossContext ? "boss_signal" : "mercy_signal") : "hostility", label + ".faction = " + actor.faction);
      }

      const prevSignals = safeObj(before.agentSignals);
      const nextSignals = safeObj(actor.agentSignals);
      ["patience", "fear", "paranoia"].forEach((key) => {
        const beforeValue = Number(prevSignals[key]);
        const afterValue = Number(nextSignals[key]);
        if (!Number.isFinite(beforeValue) || !Number.isFinite(afterValue) || beforeValue === afterValue) return;
        const delta = afterValue - beforeValue;
        pushDiff(out, "agent_signal", label + "." + key + " " + (delta > 0 ? "+" : "") + delta);
      });
    });

    Object.entries(next.memories).forEach(([actorId, notes]) => {
      const before = new Set(safeArr(prev.memories[actorId]).map(String));
      notes.forEach((note) => {
        if (!before.has(note)) {
          pushDiff(out, "memory", "actor_private:" + actorId + " += " + shortMemory(note), note);
        }
      });
    });

    const prevCombat = safeObj(prev.combat);
    const nextCombat = safeObj(next.combat);
    if (prevCombat.combat_active !== nextCombat.combat_active && nextCombat.combat_active === true) {
      pushDiff(out, "combat", "combat.active = true");
    }
    const prevOrder = safeArr(prevCombat.initiative_order).join(",");
    const nextOrder = safeArr(nextCombat.initiative_order).join(",");
    if (nextOrder && prevOrder !== nextOrder) {
      pushDiff(out, "combat", "combat.initiative_order = " + nextOrder);
    }

    if (prev.demo_cleared !== next.demo_cleared && next.demo_cleared === true) {
      pushDiff(out, "completion", "demo_cleared = true");
    }

    return out;
  }

  function ensurePanel() {
    if (panel && document.body.contains(panel)) return panel;
    const host = document.getElementById("director-trace-panel") || document.body;
    panel = document.createElement("section");
    panel.id = "world-state-diff-panel";
    panel.className = "world-diff-panel";
    panel.innerHTML =
      '<button type="button" id="world-state-diff-toggle" class="world-diff-toggle" aria-expanded="true">' +
      '<span>World State Diff</span><strong id="world-state-diff-badge" class="world-diff-badge">0</strong></button>' +
      '<div id="world-state-diff-body" class="world-diff-body" aria-live="polite"></div>';
    const inspector = host.querySelector(".xray-section--inspector");
    if (inspector) {
      host.insertBefore(panel, inspector);
    } else {
      host.appendChild(panel);
    }
    body = panel.querySelector("#world-state-diff-body");
    badge = panel.querySelector("#world-state-diff-badge");
    toggle = panel.querySelector("#world-state-diff-toggle");
    toggle.addEventListener("click", () => setCollapsed(!panel.classList.contains("is-collapsed")));
    return panel;
  }

  function setCollapsed(collapsed) {
    ensurePanel();
    panel.classList.toggle("is-collapsed", Boolean(collapsed));
    if (toggle) toggle.setAttribute("aria-expanded", String(!collapsed));
  }

  function renderDiffs(diffs, options = {}) {
    ensurePanel();
    latestDiffs = safeArr(diffs);
    if (badge) {
      badge.textContent = String(latestDiffs.length);
      badge.classList.toggle("has-changes", latestDiffs.length > 0);
    }
    if (body) {
      body.innerHTML = "";
      if (!latestDiffs.length) {
        const empty = document.createElement("p");
        empty.className = "world-diff-empty";
        empty.textContent = "No narrative state changes yet. Trigger diary, loot, trap, or Gatekeeper choices to see technical diffs.";
        body.appendChild(empty);
      } else {
        latestDiffs.forEach((entry) => {
          const row = document.createElement("div");
          row.className = "world-diff-row world-diff-row--" + normalizeId(entry.type || "change");
          if (normalizeId(entry.type) === "agent_signal") {
            row.classList.add("agent-signal-diff");
          }
          if (normalizeId(entry.type) === "trap_signal") {
            row.classList.add("trap-signal-diff");
          }
          if (normalizeId(entry.type) === "memory_echo_signal") {
            row.classList.add("memory-echo-diff");
          }
          if (normalizeId(entry.type) === "mercy_signal") {
            row.classList.add("mercy-signal-diff");
          }
          if (normalizeId(entry.type) === "boss_signal") {
            row.classList.add("agent-signal-diff", "boss-signal-diff");
          }
          if (normalizeId(entry.type) === "act3_study_signal") {
            row.classList.add("agent-signal-diff");
          }
          const sigil = document.createElement("span");
          sigil.className = "world-diff-sigil";
          sigil.textContent = "◆";
          const label = document.createElement("code");
          label.textContent = entry.label || "state changed";
          row.appendChild(sigil);
          row.appendChild(label);
          body.appendChild(row);
        });
      }
    }

    if (latestDiffs.length && options.autoExpand !== false) {
      setCollapsed(false);
      window.clearTimeout(autoTimer);
      if (options.autoCollapse === true) {
        autoTimer = window.setTimeout(() => setCollapsed(true), Number(options.collapseAfterMs) || AUTO_COLLAPSE_MS);
      }
    }
    return latestDiffs;
  }

  function update(previous, next, options = {}) {
    return renderDiffs(diffSnapshots(previous, next), options);
  }

  function getLatestDiffs() {
    return latestDiffs.slice();
  }

  window.ControlledAgentStateDiffRenderer = Object.freeze({
    normalizeSnapshot,
    diffSnapshots,
    ensurePanel,
    renderDiffs,
    update,
    setCollapsed,
    getLatestDiffs,
  });
})();
