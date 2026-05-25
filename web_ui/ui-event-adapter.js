/**
 * ui-event-adapter.js
 * ───────────────────────────────────────────────────────
 * Unified event consumer: backend response → typed UI events.
 *
 * Fix #5: compat with blocked_by / blockedTiles, and
 * latest_roll.result.raw_roll / is_success / rolls / dc variants.
 *
 * Exposed on window.ControlledAgentUIEventAdapter.
 */
(() => {
  "use strict";

  function safeObj(v) {
    return v && typeof v === "object" ? v : {};
  }
  function safeArr(v) {
    return Array.isArray(v) ? v : [];
  }
  function normalizeId(value) {
    return String(value || "").trim().toLowerCase();
  }

  /* ── Regex patterns for inference ── */
  const RE_LOS_BLOCKED =
    /NO_LOS|视线.*阻挡|视线.*被.*挡|line.of.sight.*block/i;
  const RE_ROLL_DETAIL =
    /(?:(\w+)\s*)?(?:检定|check|roll).*?(?:DC\s*(\d+)|dc(\d+))?.*?(?:(?:掷出|rolled?|结果)\s*(\d+))?/i;
  const RE_AFFECTION =
    /好感度?\s*([+\-]\s*\d+)|affection\s*([+\-]\s*\d+)/i;
  const RE_ITEM_GAINED =
    /获得\s*[了]?\s*(.+?)(?:\s*[x×]\s*(\d+))?(?:\s*$|\s*[。，,])/i;
  const RE_STATUS_CHANGE =
    /\[状态\]\s*(\S+)\s*[:：]\s*(tense|poisoned|prone|frightened|blessed|中毒|紧张|恐惧|倒地|祝福)/i;
  const RE_MEMORY = /\[记忆\]|\[记忆沉淀\]|memory.*added|记忆沉淀/i;
  const RE_DEMO_CLEARED =
    /demo.*clear|通关|逃出|escape.*complete|任务完成/i;
  const RE_TRAP_DISCOVERED =
    /发现.*陷阱|察觉.*陷阱|trap.*discovered|trap.*revealed/i;
  const RE_TRAP_TRIGGERED =
    /踩中.*陷阱|毒雾.*喷发|毒气.*喷发|poison.*damage|trap[_\s-]*triggered|trap\s+(?:was\s+)?triggered/i;
  const RE_COMPANION_GUIDANCE = /\[队友建议\]/i;
  const RE_NEGOTIATION_LEVERAGE = /\[交涉筹码\]/i;
  const RE_TRAP_INSIGHT_SIGNAL = /\[陷阱感知\]\s*([a-z0-9_\-]+)\s*->\s*([a-z0-9_\-]+)/i;
  const RE_TRAP_DISARMED_SIGNAL = /\[陷阱解除\]\s*([a-z0-9_\-]+)\s*->\s*([a-z0-9_\-]+)/i;
  const RE_POISON_TRAP_TRIGGER_SIGNAL = /\[毒气陷阱\]\s*([a-z0-9_\-]+)\s*(?:triggered|触发)/i;
  const RE_MEMORY_ECHO_SIGNAL = /\[记忆回响\]\s*([a-z0-9_\-]+)\s*->\s*(rebuked_by_player|sided_with_player)/i;
  const RE_SECRET_STUDY_SIGNAL = /\[秘密书房\]\s*cracked_wall\s*->\s*room_c_secret_study/i;
  const RE_MEMORY_ECHO_REBUKE_TOKEN = /现在又需要我|闭嘴|记住这笔账|记住.*账|need me now|now you need me|shut up|remember.*this/i;
  const RE_MEMORY_ECHO_COMPLICITY_TOKEN = /默契|一起嘲笑|共谋|shared cruelty|cruelty shared|complicit|conspiracy/i;
  const RE_PARTY_STANCE_SIGNAL = /\[站队\]\s*([a-z0-9_'’\-]+)\s*->\s*(mercy|execute|resentful|mocking)/i;
  const RE_MERCY_RESOLUTION_SIGNAL = /\[抉择\]\s*(gatekeeper|守门人)\s*->\s*(spared|executed)/i;
  const RE_BOSS_INTRO_SIGNAL = /\[Boss Encounter\]\s*gatekeeper_confrontation_started/i;
  const RE_BOSS_STRATEGY_SIGNAL = /\[Boss方案\]\s*([a-z0-9_'’\-]+)\s*->\s*(steal_key|contain_corruption|execute)/i;
  const RE_BOSS_ROUTE_SIGNAL = /\[Boss解决\]\s*(negotiation|scout_steal|assault)\s*->\s*([a-z0-9_\-]+)/i;
  const RE_BOSS_STEAL_FAILED_SIGNAL = /\[偷钥匙失败\]\s*([a-z0-9_'’\-]+)\s*->\s*(gatekeeper_alerted)/i;
  const RE_POISON_VALVE_SIGNAL = /\[毒气泄漏\]\s*(poison_valve|potion_tank)\s*->\s*(lab_poison)/i;

  /* ══════════════════════════════════════════════════════
   *  normalizeRollEvent(raw)
   *  Handles multiple backend roll shapes:
   *   - { result, dc, success }                  (current)
   *   - { raw_roll, dc, is_success }             (variant 1)
   *   - { rolls: [n], dc, is_success }           (variant 2)
   *   - { result: { raw_roll, dc, is_success } } (nested)
   * ══════════════════════════════════════════════════════ */
  function normalizeRollEvent(raw) {
    const r = safeObj(raw);

    /* Handle nested result object */
    const inner = r.result && typeof r.result === "object" ? r.result : null;
    const src = inner || r;

    /* Roll value: result > raw_roll > roll > rolls[0] */
    const rollVal =
      Number(inner ? 0 : r.result) ||
      Number(src.raw_roll) ||
      Number(src.roll) ||
      Number(safeArr(src.rolls)[0]) ||
      0;

    /* DC */
    const dc = Number(src.dc) || Number(r.dc) || 0;

    /* Success: is_success > success > (roll >= dc) */
    let success;
    if (typeof src.is_success === "boolean") {
      success = src.is_success;
    } else if (typeof r.success === "boolean") {
      success = r.success;
    } else if (typeof src.success === "boolean") {
      success = src.success;
    } else {
      success = dc > 0 ? rollVal >= dc : true;
    }

    return {
      type: "roll_result",
      actor: r.actor || r.character || src.actor || "player",
      skill: r.skill || r.ability || src.skill || src.ability || "",
      dc,
      roll: rollVal,
      advantage: Boolean(r.advantage || src.advantage),
      success,
      text: r.description || r.text || src.description || src.text || "",
    };
  }

  /* ══════════════════════════════════════════════════════
   *  normalizeLoSEvent(data, text)
   *  Handles blocked_by (backend field) and blockedTiles.
   * ══════════════════════════════════════════════════════ */
  function buildLoSEvent(data, rawText) {
    const d = safeObj(data);
    /* blocked_by: backend may return [{x,y}, …] or "entity_id" */
    let tiles = [];
    if (Array.isArray(d.blocked_by)) {
      tiles = d.blocked_by;
    } else if (Array.isArray(d.blockedTiles)) {
      tiles = d.blockedTiles;
    }
    return {
      type: "line_of_sight_blocked",
      source: d.source || "",
      target: d.target || "",
      blockedTiles: tiles,
      blocked_by: d.blocked_by || null,
      rawText: rawText || "",
    };
  }

  /* ══════════════════════════════════════════════════════
   *  extractUIEvents(backendResponse, previousState?)
   * ══════════════════════════════════════════════════════ */
  function extractUIEvents(backendResponse, previousState, options) {
    const data = safeObj(backendResponse);
    const opts = safeObj(options);
    const prev = safeObj(previousState);
    const stateProjectionOnly = opts.stateProjectionOnly === true
      || normalizeId(prev._eventSource) === "state_poll"
      || normalizeId(data._eventSource) === "state_poll";

    const events = stateProjectionOnly ? [] : safeArr(data.ui_events).map(normalizeDirectUIEvent);
    const gameState = safeObj(data.game_state || data.gameState || data.state);
    const journal = [
      ...safeArr(data.journal_events),
      ...safeArr(gameState.journal_events),
      ...safeArr(data.state && data.state.journal_events),
    ];
    const prevGameState = safeObj(prev.game_state || prev.gameState || prev.state);
    const prevJournal = new Set([
      ...safeArr(prev.journal_events),
      ...safeArr(prevGameState.journal_events),
      ...safeArr(prev.state && prev.state.journal_events),
    ].map((line) => String(line || "")));

    const journalForInference = stateProjectionOnly
      ? journal.filter((line) => prevJournal.has(String(line || "")) === false && prevJournal.size > 0)
      : journal;

    journalForInference.forEach((line) => {
      const text = String(line || "");
      inferFromLine(text, events);
    });

    /* latest_roll field (multiple shapes supported) */
    if (data.latest_roll) {
      events.push(normalizeRollEvent(data.latest_roll));
    }

    /* Top-level blocked_by field (from mechanics_processing) */
    if (data.blocked_by) {
      events.push(buildLoSEvent(data, ""));
    }

    /* Affection deltas from party_status comparison */
    inferAffectionDeltas(
      safeObj(prev.party_status),
      safeObj(data.party_status),
      events
    );
    if (opts.suppressInventoryDeltas !== true && prev._suppressInventoryDeltas !== true) {
      inferInventoryDeltas(
        safeObj(prev.player_inventory),
        safeObj(data.player_inventory),
        events
      );
    }
    inferMemoryDeltas(
      safeObj(prev.actor_runtime_state || safeObj(prev.game_state).actor_runtime_state),
      safeObj(data.actor_runtime_state || safeObj(data.game_state).actor_runtime_state),
      events
    );

    /* Demo cleared detection */
    if (data.demo_cleared === true || RE_DEMO_CLEARED.test(journal.join(" "))) {
      events.push({ type: "demo_cleared" });
    }

    inferMemoryEchoEvents(prev, data, events);
    inferMercyResolutionFromFlags(prev, data, events);
    inferTrapSignalEvents(prev, data, events);
    inferBossEventsFromFlags(prev, data, events);
    inferNegotiationFromFlags(prev, data, events);
    inferNegotiationEffects(prev, data, events);
    dedupePartyStanceEvents(events);
    dedupeMercyResolutionEvents(events);
    dedupeMemoryEchoEvents(events);
    dedupeTrapSignalEvents(events);
    dedupeBossEvents(events);
    dedupeItemGainedEvents(events);

    return events;
  }

  function normalizeDirectUIEvent(raw) {
    const e = safeObj(raw);
    const type = String(e.type || e.event_type || "").trim().toLowerCase();
    if (type === "item_transfer" || type === "actor_item_transaction_requested") {
      const tx = safeObj(e.transaction || safeObj(e.payload).transaction);
      const item = e.item || e.item_id || tx.item || tx.item_id;
      return {
        type: "item_gained",
        item,
        label: e.label || item,
        icon: e.icon || "◻",
        count: Number(e.count || tx.count) || 1,
      };
    }
    if (type === "memory_update" || type === "memory_added") {
      return {
        type: "memory_added",
        character: e.character || e.actor || e.actor_id || "",
        text: e.text || e.note || e.memory || "",
      };
    }
    if (type === "affection" || type === "affection_delta") {
      return {
        type: "affection_delta",
        character: e.character || e.actor || e.actor_id || "",
        delta: Number(e.delta) || 0,
        newValue: e.newValue ?? e.new_value ?? null,
        reason: e.reason || "",
      };
    }
    if (type === "companion_guidance" || type === "negotiation_leverage" || type === "memory_echo"
      || type === "party_stance" || type === "mercy_resolution"
      || type === "trap_insight" || type === "trap_disarmed" || type === "trap_triggered"
      || type === "boss_intro" || type === "boss_strategy" || type === "boss_route" || type === "poison_valve") {
      return e;
    }
    return e;
  }

  function inferFromLine(text, events) {
    const guidance = parseCompanionGuidance(text);
    if (guidance) {
      events.push(guidance);
    }

    const leverage = parseNegotiationLeverage(text);
    if (leverage) {
      events.push(leverage);
    }

    const trapSignal = parseTrapSignal(text);
    if (trapSignal) {
      events.push(trapSignal);
    }

    const memoryEcho = parseMemoryEchoSignal(text);
    if (memoryEcho) {
      events.push(memoryEcho);
    }
    if (RE_SECRET_STUDY_SIGNAL.test(text) || /墙后露出一间秘密书房|act3_secret_study/i.test(text)) {
      events.push({
        type: "secret_study_discovered",
        text: "墙后露出一间秘密书房。",
      });
    }

    const partyStance = parsePartyStanceSignal(text);
    if (partyStance) {
      events.push(partyStance);
    }

    const mercyResolution = parseMercyResolutionSignal(text);
    if (mercyResolution) {
      events.push(mercyResolution);
    }

    const bossSignal = parseBossSignal(text);
    if (bossSignal) {
      events.push(bossSignal);
    }

    /* LoS blocked */
    if (RE_LOS_BLOCKED.test(text)) {
      events.push(buildLoSEvent({}, text));
    }

    /* Roll result */
    const rollMatch = text.match(RE_ROLL_DETAIL);
    if (rollMatch && (rollMatch[2] || rollMatch[3] || rollMatch[4])) {
      const dc = Number(rollMatch[2] || rollMatch[3]) || 0;
      const result = Number(rollMatch[4]) || 0;
      events.push({
        type: "roll_result",
        actor: rollMatch[1] || "player",
        skill: "",
        dc,
        roll: result,
        advantage: /优势|advantage/i.test(text),
        success: dc > 0 ? result >= dc : true,
        text,
      });
    }

    /* Affection inline */
    const affMatch = text.match(RE_AFFECTION);
    if (affMatch) {
      const deltaStr = (affMatch[1] || affMatch[2] || "0").replace(/\s/g, "");
      const delta = parseInt(deltaStr, 10);
      if (delta !== 0) {
        events.push({
          type: "affection_delta",
          character: "",
          delta,
          newValue: null,
          reason: text,
        });
      }
    }

    /* Status change */
    const statusMatch = text.match(RE_STATUS_CHANGE);
    if (statusMatch) {
      events.push({
        type: "status_changed",
        character: statusMatch[1] || "",
        status: statusMatch[2] || "",
        added: true,
      });
    }

    /* Memory added */
    if (RE_MEMORY.test(text)) {
      const memText = text
        .replace(/\[记忆[沉淀]*\]\s*/, "")
        .replace(/memory.*?added\s*[:：]?\s*/i, "")
        .trim();
      events.push({
        type: "memory_added",
        character: "",
        text: memText || text,
      });
    }

    if (RE_TRAP_DISCOVERED.test(text) && !trapSignal) {
      events.push({
        type: "trap_discovered",
        text,
      });
    }

    if (RE_TRAP_TRIGGERED.test(text) && !trapSignal) {
      events.push({
        type: "trap_triggered",
        text,
      });
    }

    /* Item gained */
    const itemMatch = text.match(RE_ITEM_GAINED);
    if (itemMatch) {
      const itemId = (itemMatch[1] || "").trim();
      const count = Number(itemMatch[2]) || 1;
      if (itemId) {
        const meta =
          window.ControlledAgentHazardMeta &&
          window.ControlledAgentHazardMeta.ITEM_META_EXTENSIONS[
            itemId.toLowerCase().replace(/\s+/g, "_")
          ];
        events.push({
          type: "item_gained",
          item: itemId,
          label: meta ? meta.label : itemId,
          icon: meta ? meta.icon : "◻",
          count,
        });
      }
    }
  }

  function flagBool(value) {
    const raw = safeObj(value);
    if (Object.prototype.hasOwnProperty.call(raw, "value")) return raw.value === true;
    if (typeof value === "boolean") return value;
    if (typeof value === "string") return ["true", "yes", "1"].includes(value.trim().toLowerCase());
    return value === 1;
  }

  function responseTextBlob(data) {
    const payload = safeObj(data);
    const parts = [];
    const pushResponse = (item) => {
      if (item == null) return;
      if (typeof item === "string") {
        parts.push(item);
        return;
      }
      const record = safeObj(item);
      ["text", "content", "message", "line", "response"].forEach((key) => {
        if (record[key]) parts.push(String(record[key]));
      });
    };
    safeArr(payload.responses).forEach(pushResponse);
    safeArr(payload.response).forEach(pushResponse);
    pushResponse(payload.response);
    pushResponse(payload.message);
    pushResponse(payload.text);
    pushResponse(payload.narration);
    return parts.join(" ");
  }

  function stateFlags(rawState) {
    const state = safeObj(rawState);
    const gameState = safeObj(state.game_state || state.gameState || state.state);
    return safeObj(state.flags || gameState.flags);
  }

  function stateEnvironment(rawState) {
    const state = safeObj(rawState);
    const gameState = safeObj(state.game_state || state.gameState || state.state);
    return safeObj(state.environment_objects || state.environmentObjects || gameState.environment_objects || gameState.entities);
  }

  function statusEffectId(effect) {
    if (typeof effect === "string") return effect.trim().toLowerCase();
    const record = safeObj(effect);
    return String(record.type || record.id || record.name || record.status || "").trim().toLowerCase();
  }

  function actorStatusEffects(actor) {
    return safeArr(safeObj(actor).status_effects || safeObj(actor).statusEffects)
      .map(statusEffectId)
      .filter(Boolean);
  }

  function actorPools(rawState) {
    const state = safeObj(rawState);
    const gameState = safeObj(state.game_state || state.gameState || state.state);
    return [
      safeObj(state.party_status),
      safeObj(state.partyStatus),
      safeObj(gameState.party_status),
      safeObj(gameState.entities),
    ];
  }

  function inferPoisonedActors(previousState, currentState) {
    const previous = Object.assign({}, ...actorPools(previousState));
    const current = Object.assign({}, ...actorPools(currentState));
    const affected = [];
    Object.entries(current).forEach(([actorId, actor]) => {
      const currEffects = new Set(actorStatusEffects(actor));
      if (!currEffects.has("poisoned") && !currEffects.has("中毒")) return;
      const prevEffects = new Set(actorStatusEffects(previous[actorId]));
      if (prevEffects.has("poisoned") || prevEffects.has("中毒")) return;
      affected.push(String(actorId || "").toLowerCase());
    });
    return affected;
  }

  function parseTrapSignal(text) {
    const raw = String(text || "");
    let match = raw.match(RE_TRAP_INSIGHT_SIGNAL);
    if (match) {
      return buildTrapInsightEvent(match[1], match[2], "journal", raw);
    }
    if (/(?:Scout|侦察员).*?(?:毒气机关|毒气陷阱|gas trap|hidden gas trap|陷阱)|小心.*?(?:毒气机关|毒气陷阱|陷阱)/i.test(raw)) {
      return buildTrapInsightEvent("scout", "gas_trap_1", "journal", raw);
    }
    match = raw.match(RE_TRAP_DISARMED_SIGNAL);
    if (match) {
      return {
        type: "trap_disarmed",
        actor: String(match[1] || "scout").toLowerCase(),
        trapId: String(match[2] || "gas_trap_1").toLowerCase(),
        raw,
        source: "journal",
      };
    }
    match = raw.match(RE_POISON_TRAP_TRIGGER_SIGNAL);
    if (match) {
      return {
        type: "trap_triggered",
        trapId: String(match[1] || "gas_trap_1").toLowerCase(),
        affectedActors: [],
        raw,
        source: "journal",
      };
    }
    return null;
  }

  function memoryEchoConfig(memoryType) {
    const key = String(memoryType || "").trim().toLowerCase();
    if (key === "sided_with_player") {
      return {
        memoryType: "sided_with_player",
        tone: "complicit",
        title: "Shared cruelty remembered",
        message: "He remembers you sided with him.",
        quote: "Cruelty shared becomes trust.",
      };
    }
    return {
      memoryType: "rebuked_by_player",
      tone: "resentful",
      title: "Scout remembers",
      message: "He remembers you rebuked him.",
      quote: "Now you need me?",
    };
  }

  function buildMemoryEchoEvent(actor, memoryType, source, raw) {
    const cfg = memoryEchoConfig(memoryType);
    return {
      type: "memory_echo",
      actor: String(actor || "scout").toLowerCase(),
      memoryType: cfg.memoryType,
      tone: cfg.tone,
      title: cfg.title,
      message: cfg.message,
      quote: cfg.quote,
      source: source || "state",
      raw: raw || "",
    };
  }

  function parseMemoryEchoSignal(text) {
    const raw = String(text || "");
    const match = raw.match(RE_MEMORY_ECHO_SIGNAL);
    if (!match) return null;
    return buildMemoryEchoEvent(match[1], match[2], "journal", raw);
  }

  function flagChangedToTrue(previousFlags, currentFlags, key) {
    return flagBool(currentFlags[key]) && !flagBool(previousFlags[key]);
  }

  function pushMemoryEchoEvent(events, nextEvent) {
    if (!nextEvent || nextEvent.type !== "memory_echo") return;
    const actor = String(nextEvent.actor || "scout").toLowerCase();
    const memoryType = String(nextEvent.memoryType || "").toLowerCase();
    const existing = events.find((event) => {
      return event
        && event.type === "memory_echo"
        && String(event.actor || "scout").toLowerCase() === actor
        && String(event.memoryType || "").toLowerCase() === memoryType;
    });
    if (!existing) {
      events.push(nextEvent);
      return;
    }
    if (nextEvent.source === "journal") existing.source = "journal";
    if (nextEvent.raw && !existing.raw) existing.raw = nextEvent.raw;
    if (nextEvent.quote && !existing.quote) existing.quote = nextEvent.quote;
  }

  function inferMemoryEchoEvents(previousState, currentState, events) {
    const prevFlags = stateFlags(previousState);
    const currFlags = stateFlags(currentState);
    if (flagChangedToTrue(prevFlags, currFlags, "hazard_lab_scout_rebuke_echo_seen")) {
      pushMemoryEchoEvent(events, buildMemoryEchoEvent("scout", "rebuked_by_player", "state", "flags.hazard_lab_scout_rebuke_echo_seen=true"));
    }
    if (flagChangedToTrue(prevFlags, currFlags, "hazard_lab_scout_complicity_echo_seen")) {
      pushMemoryEchoEvent(events, buildMemoryEchoEvent("scout", "sided_with_player", "state", "flags.hazard_lab_scout_complicity_echo_seen=true"));
    }

    const responseText = responseTextBlob(currentState);
    if (responseText && RE_MEMORY_ECHO_REBUKE_TOKEN.test(responseText)) {
      pushMemoryEchoEvent(events, buildMemoryEchoEvent("scout", "rebuked_by_player", "response", responseText));
    }
    if (responseText && RE_MEMORY_ECHO_COMPLICITY_TOKEN.test(responseText)) {
      pushMemoryEchoEvent(events, buildMemoryEchoEvent("scout", "sided_with_player", "response", responseText));
    }
  }

  function dedupeMemoryEchoEvents(events) {
    const seen = new Map();
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (!event || event.type !== "memory_echo") continue;
      const key = String(event.actor || "scout").toLowerCase() + ":" + String(event.memoryType || "").toLowerCase();
      if (!seen.has(key)) {
        seen.set(key, event);
        continue;
      }
      const kept = seen.get(key);
      if (event.source === "journal") kept.source = "journal";
      if (event.raw && !kept.raw) kept.raw = event.raw;
      events.splice(index, 1);
    }
  }

  function normalizeStanceActor(actor) {
    const raw = String(actor || "").trim().toLowerCase().replace(/[’']/g, "");
    if (/analyst|分析员/.test(raw)) return "analyst";
    if (/tactician|tactician|战术员/.test(raw)) return "tactician";
    if (/scout|侦察员/.test(raw)) return "scout";
    return raw || "party";
  }

  function normalizeStance(value) {
    const raw = String(value || "").trim().toLowerCase();
    if (["mercy", "execute", "resentful", "mocking"].includes(raw)) return raw;
    return "unknown";
  }

  function parsePartyStanceSignal(text) {
    const raw = String(text || "");
    const match = raw.match(RE_PARTY_STANCE_SIGNAL);
    if (!match) return null;
    return {
      type: "party_stance",
      target: "gatekeeper",
      stances: [{
        actor: normalizeStanceActor(match[1]),
        stance: normalizeStance(match[2]),
      }],
      raw,
      source: "journal",
    };
  }

  function parseMercyResolutionSignal(text) {
    const raw = String(text || "");
    const match = raw.match(RE_MERCY_RESOLUTION_SIGNAL);
    if (!match) return null;
    return {
      type: "mercy_resolution",
      target: "gatekeeper",
      result: String(match[2] || "").trim().toLowerCase(),
      raw,
      source: "journal",
    };
  }

  function mergeStanceList(existing, incoming) {
    const byActor = new Map();
    safeArr(existing).forEach((entry) => {
      const item = safeObj(entry);
      const actor = normalizeStanceActor(item.actor);
      if (!actor) return;
      byActor.set(actor, { actor, stance: normalizeStance(item.stance) });
    });
    safeArr(incoming).forEach((entry) => {
      const item = safeObj(entry);
      const actor = normalizeStanceActor(item.actor);
      if (!actor) return;
      byActor.set(actor, { actor, stance: normalizeStance(item.stance) });
    });
    return Array.from(byActor.values());
  }

  function dedupePartyStanceEvents(events) {
    const firstIndexByTarget = new Map();
    for (let index = 0; index < events.length; index += 1) {
      const event = events[index];
      if (!event || event.type !== "party_stance") continue;
      const target = String(event.target || "gatekeeper").toLowerCase();
      if (!firstIndexByTarget.has(target)) {
        firstIndexByTarget.set(target, index);
        event.target = target;
        event.stances = mergeStanceList([], event.stances);
        continue;
      }
      const kept = events[firstIndexByTarget.get(target)];
      kept.stances = mergeStanceList(kept.stances, event.stances);
      if (event.raw) kept.raw = [kept.raw, event.raw].filter(Boolean).join("\n");
      events.splice(index, 1);
      index -= 1;
    }
  }

  function hasMercyResolutionEvent(events, result) {
    return events.some((event) => (
      event
      && event.type === "mercy_resolution"
      && (!result || String(event.result || "").toLowerCase() === result)
    ));
  }

  function inferMercyResolutionFromFlags(previousState, currentState, events) {
    const prevFlags = flagsFromState(previousState);
    const currFlags = flagsFromState(currentState);
    const keyAvailable = flagBool(currFlags.hazard_lab_gatekeeper_key_available);
    if (flagChangedToTrue(prevFlags, currFlags, "hazard_lab_gatekeeper_spared") && !hasMercyResolutionEvent(events, "spared")) {
      events.push({
        type: "mercy_resolution",
        target: "gatekeeper",
        result: "spared",
        keyAvailable,
        source: "state",
        raw: "flags.hazard_lab_gatekeeper_spared=true",
      });
    }
    if (flagChangedToTrue(prevFlags, currFlags, "hazard_lab_gatekeeper_executed") && !hasMercyResolutionEvent(events, "executed")) {
      events.push({
        type: "mercy_resolution",
        target: "gatekeeper",
        result: "executed",
        keyAvailable,
        source: "state",
        raw: "flags.hazard_lab_gatekeeper_executed=true",
      });
    }
    events.forEach((event) => {
      if (event && event.type === "mercy_resolution" && event.keyAvailable == null) {
        event.keyAvailable = keyAvailable;
      }
    });
  }

  function dedupeMercyResolutionEvents(events) {
    const seen = new Map();
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (!event || event.type !== "mercy_resolution") continue;
      const key = String(event.target || "gatekeeper").toLowerCase() + ":" + String(event.result || "").toLowerCase();
      if (!seen.has(key)) {
        seen.set(key, event);
        continue;
      }
      const kept = seen.get(key);
      if (event.source === "journal") kept.source = "journal";
      if (event.raw && !kept.raw) kept.raw = event.raw;
      if (event.keyAvailable === true) kept.keyAvailable = true;
      events.splice(index, 1);
    }
  }

  function normalizeBossPlan(value) {
    const raw = String(value || "").trim().toLowerCase();
    if (["steal_key", "contain_corruption", "execute"].includes(raw)) return raw;
    return raw || "unknown";
  }

  function parseBossSignal(text) {
    const raw = String(text || "");
    if (RE_BOSS_INTRO_SIGNAL.test(raw)) {
      return {
        type: "boss_intro",
        targetId: "gatekeeper",
        keyHolder: true,
        poisonValvePresent: true,
        diaryTruthAvailable: /diary_truth_available|日记|真相/i.test(raw),
        source: "journal",
        raw,
      };
    }
    const strategy = raw.match(RE_BOSS_STRATEGY_SIGNAL);
    if (strategy) {
      return {
        type: "boss_strategy",
        targetId: "gatekeeper",
        strategies: [{
          actor: normalizeStanceActor(strategy[1]),
          plan: normalizeBossPlan(strategy[2]),
        }],
        source: "journal",
        raw,
      };
    }
    const route = raw.match(RE_BOSS_ROUTE_SIGNAL);
    if (route) {
      return {
        type: "boss_route",
        targetId: "gatekeeper",
        route: String(route[1] || "").trim().toLowerCase(),
        result: String(route[2] || "").trim().toLowerCase(),
        source: "journal",
        raw,
      };
    }
    const stealFailed = raw.match(RE_BOSS_STEAL_FAILED_SIGNAL);
    if (stealFailed) {
      return {
        type: "boss_route",
        targetId: "gatekeeper",
        route: "scout_steal",
        result: String(stealFailed[2] || "gatekeeper_alerted").trim().toLowerCase(),
        actor: normalizeStanceActor(stealFailed[1]),
        failed: true,
        poisonValveTriggered: true,
        source: "journal",
        raw,
      };
    }
    const valve = raw.match(RE_POISON_VALVE_SIGNAL);
    if (valve) {
      return {
        type: "poison_valve",
        valveId: normalizeId(valve[1] || "poison_valve"),
        status: "triggered",
        result: String(valve[2] || "lab_poison").trim().toLowerCase(),
        source: "journal",
        raw,
      };
    }
    return null;
  }

  function hasBossRouteEvent(events, route, result) {
    return events.some((event) => (
      event
      && event.type === "boss_route"
      && (!route || String(event.route || "").toLowerCase() === route)
      && (!result || String(event.result || "").toLowerCase() === result)
    ));
  }

  function hasPoisonValveEvent(events, status) {
    return events.some((event) => (
      event
      && event.type === "poison_valve"
      && (!status || String(event.status || "").toLowerCase() === status)
    ));
  }

  function pushBossIntroEvent(events, flags, source, raw) {
    const existing = events.find((event) => event && event.type === "boss_intro");
    if (existing) {
      if (flagBool(flags.act4_diary_truth_available)) existing.diaryTruthAvailable = true;
      if (source === "journal") existing.source = "journal";
      if (raw && !existing.raw) existing.raw = raw;
      return;
    }
    events.push({
      type: "boss_intro",
      targetId: "gatekeeper",
      keyHolder: true,
      poisonValvePresent: true,
      diaryTruthAvailable: flagBool(flags.act4_diary_truth_available),
      source: source || "state",
      raw: raw || "",
    });
  }

  function inferBossEventsFromFlags(previousState, currentState, events) {
    const prevFlags = stateFlags(previousState);
    const currFlags = stateFlags(currentState);
    if (
      flagChangedToTrue(prevFlags, currFlags, "act4_boss_room_entered")
      || flagChangedToTrue(prevFlags, currFlags, "act4_gatekeeper_confrontation_started")
    ) {
      pushBossIntroEvent(events, currFlags, "state", "act4_gatekeeper_confrontation_started=true");
    } else if (events.some((event) => event && event.type === "boss_intro")) {
      events.forEach((event) => {
        if (event && event.type === "boss_intro" && flagBool(currFlags.act4_diary_truth_available)) {
          event.diaryTruthAvailable = true;
        }
      });
    }

    if (flagChangedToTrue(prevFlags, currFlags, "act4_negotiation_success") && !hasBossRouteEvent(events, "negotiation")) {
      events.push({ type: "boss_route", targetId: "gatekeeper", route: "negotiation", result: "key_surrendered", source: "state" });
    }
    if (flagChangedToTrue(prevFlags, currFlags, "act4_scout_steal_key_success") && !hasBossRouteEvent(events, "scout_steal", "heavy_iron_key")) {
      events.push({ type: "boss_route", targetId: "gatekeeper", route: "scout_steal", result: "heavy_iron_key", actor: "scout", source: "state" });
    }
    if (flagChangedToTrue(prevFlags, currFlags, "act4_assault_success") && !hasBossRouteEvent(events, "assault")) {
      events.push({ type: "boss_route", targetId: "gatekeeper", route: "assault", result: "gatekeeper_defeated", source: "state" });
    }
    if (flagChangedToTrue(prevFlags, currFlags, "act4_heavy_iron_key_obtained") && !hasBossRouteEvent(events, "key_obtained")) {
      events.push({ type: "boss_route", targetId: "gatekeeper", route: "key_obtained", result: "heavy_iron_key", source: "state" });
    }
    if (flagChangedToTrue(prevFlags, currFlags, "act4_final_exit_opened") && !hasBossRouteEvent(events, "final_exit")) {
      events.push({ type: "boss_route", targetId: "exit_door", route: "final_exit", result: "final_exit_opened", source: "state" });
    }
    if (
      (flagChangedToTrue(prevFlags, currFlags, "act4_poison_valve_triggered")
        || flagChangedToTrue(prevFlags, currFlags, "act4_lab_poison_leak"))
      && !hasPoisonValveEvent(events, "triggered")
    ) {
      events.push({
        type: "poison_valve",
        valveId: "poison_valve",
        status: "triggered",
        result: "lab_poison",
        affectedActors: inferPoisonedActors(previousState, currentState),
        source: "state",
      });
    }
    if (flagChangedToTrue(prevFlags, currFlags, "act4_poison_valve_disabled") && !hasPoisonValveEvent(events, "disabled")) {
      events.push({
        type: "poison_valve",
        valveId: "poison_valve",
        status: "disabled",
        result: "valve_disabled",
        source: "state",
      });
    }
  }

  function mergeBossStrategies(existing, incoming) {
    const byActor = new Map();
    safeArr(existing).forEach((entry) => {
      const item = safeObj(entry);
      const actor = normalizeStanceActor(item.actor);
      if (!actor) return;
      byActor.set(actor, { actor, plan: normalizeBossPlan(item.plan) });
    });
    safeArr(incoming).forEach((entry) => {
      const item = safeObj(entry);
      const actor = normalizeStanceActor(item.actor);
      if (!actor) return;
      byActor.set(actor, { actor, plan: normalizeBossPlan(item.plan) });
    });
    return Array.from(byActor.values());
  }

  function dedupeBossEvents(events) {
    const firstStrategyIndexByTarget = new Map();
    const seenRoutes = new Map();
    const seenValves = new Map();
    let introIndex = -1;
    for (let index = 0; index < events.length; index += 1) {
      const event = events[index];
      if (!event || !event.type) continue;
      if (event.type === "boss_intro") {
        if (introIndex < 0) {
          introIndex = index;
          continue;
        }
        const kept = events[introIndex];
        if (event.diaryTruthAvailable) kept.diaryTruthAvailable = true;
        if (event.source === "journal") kept.source = "journal";
        if (event.raw && !kept.raw) kept.raw = event.raw;
        events.splice(index, 1);
        index -= 1;
        continue;
      }
      if (event.type === "boss_strategy") {
        const target = String(event.targetId || "gatekeeper").toLowerCase();
        if (!firstStrategyIndexByTarget.has(target)) {
          firstStrategyIndexByTarget.set(target, index);
          event.targetId = target;
          event.strategies = mergeBossStrategies([], event.strategies);
          continue;
        }
        const kept = events[firstStrategyIndexByTarget.get(target)];
        kept.strategies = mergeBossStrategies(kept.strategies, event.strategies);
        if (event.raw) kept.raw = [kept.raw, event.raw].filter(Boolean).join("\n");
        events.splice(index, 1);
        index -= 1;
        continue;
      }
      if (event.type === "boss_route") {
        const key = [event.route || "", event.result || ""].map((v) => String(v).toLowerCase()).join(":");
        if (!seenRoutes.has(key)) {
          seenRoutes.set(key, index);
          continue;
        }
        const kept = events[seenRoutes.get(key)];
        if (event.failed) kept.failed = true;
        if (event.poisonValveTriggered) kept.poisonValveTriggered = true;
        if (event.source === "journal") kept.source = "journal";
        if (event.raw && !kept.raw) kept.raw = event.raw;
        events.splice(index, 1);
        index -= 1;
        continue;
      }
      if (event.type === "poison_valve") {
        const key = [event.valveId || "poison_valve", event.status || ""].map((v) => String(v).toLowerCase()).join(":");
        if (!seenValves.has(key)) {
          seenValves.set(key, index);
          continue;
        }
        const kept = events[seenValves.get(key)];
        kept.affectedActors = Array.from(new Set([
          ...safeArr(kept.affectedActors).map(String),
          ...safeArr(event.affectedActors).map(String),
        ].filter(Boolean)));
        if (event.source === "journal") kept.source = "journal";
        if (event.raw && !kept.raw) kept.raw = event.raw;
        events.splice(index, 1);
        index -= 1;
      }
    }
  }

  function buildTrapInsightEvent(actor, trapId, source, raw) {
    return {
      type: "trap_insight",
      actor: String(actor || "scout").toLowerCase(),
      trapId: String(trapId || "gas_trap_1").toLowerCase(),
      title: "Hidden Trap Spotted",
      message: "Scout saw what the player could not. Ask him to disarm it.",
      source: source || "state",
      raw: raw || "",
    };
  }

  function pushTrapEvent(events, nextEvent) {
    if (!nextEvent || !nextEvent.type) return;
    const trapId = String(nextEvent.trapId || "gas_trap_1").toLowerCase();
    const existing = events.find((event) => {
      return event && event.type === nextEvent.type && String(event.trapId || "gas_trap_1").toLowerCase() === trapId;
    });
    if (!existing) {
      events.push(nextEvent);
      return;
    }
    if (nextEvent.actor && !existing.actor) existing.actor = nextEvent.actor;
    if (nextEvent.raw && !existing.raw) existing.raw = nextEvent.raw;
    if (Array.isArray(nextEvent.affectedActors)) {
      existing.affectedActors = Array.from(new Set([
        ...safeArr(existing.affectedActors).map(String),
        ...nextEvent.affectedActors.map(String),
      ].filter(Boolean)));
    }
  }

  function inferTrapSignalEvents(previousState, currentState, events) {
    const prevFlags = stateFlags(previousState);
    const currFlags = stateFlags(currentState);
    const prevEnv = stateEnvironment(previousState);
    const currEnv = stateEnvironment(currentState);
    const prevTrap = safeObj(prevEnv.gas_trap_1);
    const currTrap = safeObj(currEnv.gas_trap_1);
    const prevStatus = String(prevTrap.status || "").trim().toLowerCase();
    const currStatus = String(currTrap.status || "").trim().toLowerCase();
    const wasRevealed = flagBool(prevFlags.hazard_lab_poison_trap_revealed)
      || flagBool(prevFlags.scout_detected_gas_trap)
      || flagBool(prevFlags.world_hazard_lab_trap_warned)
      || prevStatus === "revealed"
      || prevTrap.is_hidden === false;
    const isRevealed = flagBool(currFlags.hazard_lab_poison_trap_revealed)
      || flagBool(currFlags.scout_detected_gas_trap)
      || flagBool(currFlags.world_hazard_lab_trap_warned)
      || currStatus === "revealed"
      || currTrap.is_hidden === false;
    const wasDisabled = flagBool(prevFlags.hazard_lab_poison_trap_disarmed) || prevStatus === "disabled";
    const isDisabled = flagBool(currFlags.hazard_lab_poison_trap_disarmed) || currStatus === "disabled";
    const wasTriggered = flagBool(prevFlags.hazard_lab_poison_trap_triggered) || prevStatus === "triggered";
    const isTriggered = flagBool(currFlags.hazard_lab_poison_trap_triggered) || currStatus === "triggered";
    const affectedActors = inferPoisonedActors(previousState, currentState);

    if (!wasRevealed && isRevealed) {
      pushTrapEvent(events, buildTrapInsightEvent("scout", "gas_trap_1", "state", ""));
    }
    if (!wasDisabled && isDisabled) {
      pushTrapEvent(events, {
        type: "trap_disarmed",
        actor: "scout",
        trapId: "gas_trap_1",
        source: "state",
      });
    }
    if ((!wasTriggered && isTriggered) || affectedActors.length) {
      pushTrapEvent(events, {
        type: "trap_triggered",
        trapId: "gas_trap_1",
        affectedActors,
        source: affectedActors.length ? "state_diff" : "state",
      });
    }
  }

  function dedupeTrapSignalEvents(events) {
    const seen = new Map();
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (!event || !["trap_insight", "trap_disarmed", "trap_triggered"].includes(event.type)) continue;
      const key = event.type + ":" + String(event.trapId || "gas_trap_1").toLowerCase();
      if (!seen.has(key)) {
        seen.set(key, event);
        continue;
      }
      const kept = seen.get(key);
      if (Array.isArray(event.affectedActors)) {
        kept.affectedActors = Array.from(new Set([
          ...safeArr(kept.affectedActors).map(String),
          ...event.affectedActors.map(String),
        ].filter(Boolean)));
      }
      events.splice(index, 1);
    }
  }

  function dedupeItemGainedEvents(events) {
    const seen = new Map();
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (!event || event.type !== "item_gained") continue;
      const item = String(event.item || event.label || "").trim().toLowerCase();
      if (!item) continue;
      if (!seen.has(item)) {
        seen.set(item, event);
        continue;
      }
      const kept = seen.get(item);
      kept.count = (Number(kept.count) || 1) + (Number(event.count) || 1);
      events.splice(index, 1);
    }
  }

  function titleCaseId(id) {
    return String(id || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function parseActorId(text) {
    const raw = String(text || "");
    if (/scout|侦察员/i.test(raw)) return "scout";
    if (/analyst|分析员/i.test(raw)) return "analyst";
    if (/tactician|战术员/i.test(raw)) return "tactician";
    return "party";
  }

  function parseGuidanceState(text) {
    const raw = String(text || "");
    if (/has_key\s*=\s*true|key[_\s-]*acquired|钥匙在手|已.*钥匙|拿到.*钥匙|有.*钥匙/i.test(raw)) {
      return "key_acquired";
    }
    if (/has_key\s*=\s*false|missing|missing[_\s-]*key|找钥匙|没.*钥匙|缺.*钥匙|书房|撬锁|搜箱|箱子|study|lockpick|chest/i.test(raw)) {
      return "missing_key";
    }
    return "unknown";
  }

  function parseCompanionGuidance(text) {
    const raw = String(text || "");
    if (!RE_COMPANION_GUIDANCE.test(raw)) return null;
    const topicMatch = raw.match(/topic\s*=\s*([a-z0-9_\-]+)/i);
    const topic = topicMatch ? topicMatch[1].toLowerCase() : (/lab[_\s-]*key|实验室.*钥匙|钥匙/i.test(raw) ? "lab_key" : "unknown");
    const advice = raw
      .replace(RE_COMPANION_GUIDANCE, "")
      .replace(/topic\s*=\s*[a-z0-9_\-]+/i, "")
      .replace(/actor\s*=\s*[a-z0-9_'’\-]+/i, "")
      .replace(/\s+/g, " ")
      .trim();
    return {
      type: "companion_guidance",
      actorId: parseActorId(raw),
      topic,
      state: parseGuidanceState(raw),
      advice: advice || raw,
      raw,
    };
  }

  function labelToNegotiationKey(label) {
    const key = String(label || "").trim().toLowerCase();
    if (key === "耐心") return "patience";
    if (key === "恐惧") return "fear";
    if (key === "偏执" || key === "猜疑") return "paranoia";
    return key;
  }

  function parseInlineEffects(text) {
    const raw = String(text || "");
    const effects = {};
    const re = /(patience|fear|paranoia|耐心|恐惧|偏执|猜疑)\s*[:=]?\s*([+\-]\s*\d+)/ig;
    let match;
    while ((match = re.exec(raw))) {
      const key = labelToNegotiationKey(match[1]);
      const value = parseInt(String(match[2] || "0").replace(/\s/g, ""), 10);
      if (["patience", "fear", "paranoia"].includes(key) && value !== 0) effects[key] = value;
    }
    return effects;
  }

  function parseNegotiationLeverage(text) {
    const raw = String(text || "");
    if (!RE_NEGOTIATION_LEVERAGE.test(raw)) return null;
    const match = raw.match(/\[交涉筹码\]\s*([a-z0-9_\-]+)\s*->\s*([a-z0-9_\-]+)/i);
    const evidence = match ? match[1].toLowerCase() : (/diary|日记/i.test(raw) ? "diary_evidence" : "unknown_evidence");
    const pressure = match ? match[2].toLowerCase() : (/elixir|灵药|药剂/i.test(raw) ? "gatekeeper_elixir_truth" : "unknown_pressure");
    const targetId = /gatekeeper|守门人/i.test(raw + " " + pressure) ? "gatekeeper" : "unknown";
    return {
      type: "negotiation_leverage",
      evidence,
      targetId,
      pressure,
      effects: parseInlineEffects(raw),
      raw,
    };
  }

  function actorDynamicValue(record, key) {
    const actor = safeObj(record);
    const states = safeObj(actor.dynamic_states || actor.dynamicStates);
    const raw = states[key] ?? actor[key];
    if (raw && typeof raw === "object") {
      const current = Number(raw.current_value ?? raw.current ?? raw.value);
      return Number.isFinite(current) ? current : null;
    }
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  }

  function actorRecordFromState(rawState, actorId) {
    const state = safeObj(rawState);
    const gameState = safeObj(state.game_state || state.gameState || {});
    const pools = [
      state.environment_objects,
      state.environmentObjects,
      state.party_status,
      state.partyStatus,
      state.entities,
      gameState.environment_objects,
      gameState.party_status,
      gameState.entities,
    ];
    for (const pool of pools) {
      const obj = safeObj(pool);
      if (obj[actorId]) return obj[actorId];
    }
    return {};
  }

  function inferNegotiationEffects(previousState, currentState, events) {
    const leverageEvents = events.filter((event) => event && event.type === "negotiation_leverage");
    if (!leverageEvents.length) return;
    leverageEvents.forEach((event) => {
      const target = event.targetId || "gatekeeper";
      const prevActor = actorRecordFromState(previousState, target);
      const currActor = actorRecordFromState(currentState, target);
      const effects = { ...safeObj(event.effects) };
      ["patience", "fear", "paranoia"].forEach((key) => {
        if (Object.prototype.hasOwnProperty.call(effects, key)) return;
        const before = actorDynamicValue(prevActor, key);
        const after = actorDynamicValue(currActor, key);
        if (before == null || after == null || before === after) return;
        effects[key] = after - before;
      });
      event.effects = effects;
    });
  }

  function flagValue(raw) {
    const record = safeObj(raw);
    if (Object.prototype.hasOwnProperty.call(record, "value")) return record.value;
    return raw;
  }

  function flagsFromState(rawState) {
    const state = safeObj(rawState);
    const gameState = safeObj(state.game_state || state.gameState || state.state);
    return safeObj(state.flags || gameState.flags);
  }

  function hasNegotiationLeverageEvent(events) {
    return events.some((event) => event && event.type === "negotiation_leverage");
  }

  function inferNegotiationFromFlags(previousState, currentState, events) {
    if (hasNegotiationLeverageEvent(events)) return;
    const prevFlags = flagsFromState(previousState);
    const currFlags = flagsFromState(currentState);
    const before = flagValue(prevFlags.hazard_lab_gatekeeper_truth_pressure);
    const after = flagValue(currFlags.hazard_lab_gatekeeper_truth_pressure);
    if (before === true || after !== true) return;
    events.push({
      type: "negotiation_leverage",
      evidence: "diary_evidence",
      targetId: "gatekeeper",
      pressure: "gatekeeper_elixir_truth",
      effects: {},
      raw: "flags.hazard_lab_gatekeeper_truth_pressure=true",
    });
  }

  function inferAffectionDeltas(prevParty, currParty, events) {
    Object.keys(currParty).forEach((id) => {
      if (id === "player") return;
      const prev = safeObj(prevParty[id]);
      const curr = safeObj(currParty[id]);
      const prevAff = Number(prev.affection);
      const currAff = Number(curr.affection);
      if (
        Number.isFinite(prevAff) &&
        Number.isFinite(currAff) &&
        prevAff !== currAff
      ) {
        events.push({
          type: "affection_delta",
          character: id,
          delta: currAff - prevAff,
          newValue: currAff,
          reason: "",
        });
      }
    });
  }

  function inferInventoryDeltas(prevInventory, currInventory, events) {
    Object.keys(currInventory).forEach((id) => {
      const prevCount = Number(prevInventory[id]) || 0;
      const currCount = Number(currInventory[id]) || 0;
      const delta = currCount - prevCount;
      if (delta <= 0) return;
      const meta =
        window.ControlledAgentHazardMeta &&
        window.ControlledAgentHazardMeta.ITEM_META_EXTENSIONS[
          String(id || "").toLowerCase().replace(/\s+/g, "_")
        ];
      events.push({
        type: "item_gained",
        item: id,
        label: meta ? meta.label : id,
        icon: meta ? meta.icon : "◻",
        count: delta,
      });
    });
  }

  function memoryNotesFromActor(actor) {
    const record = safeObj(actor);
    const notes = [];
    safeArr(record.memory_notes).forEach((note) => notes.push(String(note || "")));
    safeArr(record.memories).forEach((item) => {
      if (typeof item === "string") notes.push(item);
      else if (safeObj(item).text) notes.push(String(safeObj(item).text));
      else if (safeObj(item).note) notes.push(String(safeObj(item).note));
    });
    return notes.filter(Boolean);
  }

  function inferMemoryDeltas(prevRuntime, currRuntime, events) {
    Object.keys(currRuntime).forEach((actorId) => {
      const prevNotes = new Set(memoryNotesFromActor(prevRuntime[actorId]));
      memoryNotesFromActor(currRuntime[actorId]).forEach((note) => {
        if (prevNotes.has(note)) return;
        events.push({
          type: "memory_added",
          character: actorId,
          text: note,
        });
      });
    });
  }

  /* ── Public API ── */
  window.ControlledAgentUIEventAdapter = Object.freeze({
    extractUIEvents,
  });
})();
