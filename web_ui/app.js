(() => {
  const API_URL = "/api/chat";
  const STATE_URL = "/api/state";
  const DEFAULT_SESSION_ID =
    new URLSearchParams(window.location.search).get("session_id") ||
    "hazard_lab_demo";
  let currentSessionId = DEFAULT_SESSION_ID;
  const QA_PARAMS = new URLSearchParams(window.location.search);
  const IS_QA_MODE = Array.from(QA_PARAMS.keys()).some((key) => key.startsWith("qa_"));
  const QA_NO_IDLE = QA_PARAMS.get("qa_no_idle") === "1" || window.__ControlledAgent_QA_NO_IDLE__ === true;
  const QA_TEST = QA_PARAMS.get("qa_test") === "1" || window.__ControlledAgent_QA_TEST__ === true;
  const QA_MAP_DEBUG = QA_PARAMS.get("qa_map_debug") === "1" || window.__ControlledAgent_QA_MAP_DEBUG__ === true;
  const QA_SHOWCASE = QA_PARAMS.get("qa_showcase") === "1" || window.__ControlledAgent_QA_SHOWCASE__ === true;
  const SHOULD_SYNC_INITIAL_STATE = !IS_QA_MODE || (QA_NO_IDLE && !QA_TEST && !QA_SHOWCASE && !QA_MAP_DEBUG);
  const QA_REST_CONTROLS = QA_PARAMS.get("qa_rest_controls") === "1" || window.__ControlledAgent_QA_REST_CONTROLS__ === true;
  const IDLE_MS = 30000;
  const DIALOGUE_POLL_MS = 1800;
  const BACKEND_REQUEST_TIMEOUT_MS = Math.max(5000, Number(QA_PARAMS.get("qa_backend_timeout_ms")) || 12000);
  const SILENT_FALLBACK_TEXT = "📖 [环境] 一阵阴冷的穿堂风吹过，你暂时失去了对周围环境的感知。";
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const BARK_BUILD_ID = "20260522_act2_trap_semantics_v10";

  window.__ControlledAgent_BARK_BUILD_ID__ = BARK_BUILD_ID;
  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.dataset.barkBuild = BARK_BUILD_ID;
  }

  /* Merge hazard-meta extensions if loaded */
  const _necroMeta = window.ControlledAgentHazardMeta || {};
  const MAP_ID = (_necroMeta.MAP_ID) || "hazard_lab";

  const SPEAKER_META = Object.assign({
    player: { name: "玩家", color: "#6eb5ff", sigil: "⌘" },
    analyst: { name: "分析员", color: "#9b84c6", sigil: "✦" },
    scout: { name: "侦察员", color: "#c97a75", sigil: "🜂" },
    tactician: { name: "战术员", color: "#70b99d", sigil: "⚔" },
    dm: { name: "地下城主", color: "#d0ab67", sigil: "☍" },
    npc: { name: "同行者", color: "#b29f7e", sigil: "◈" },
  }, _necroMeta.SPEAKER_META_EXTENSIONS || {});

  const ITEM_META = Object.assign({
    gold: { label: "金币", icon: "🪙" },
    gold_coin: { label: "金币", icon: "🪙" },
    scimitar: { label: "弯刀", icon: "🗡" },
    rusty_dagger: { label: "生锈匕首", icon: "🗡" },
    leather_armor: { label: "皮甲", icon: "▣" },
    shield: { label: "盾牌", icon: "◖" },
    shortbow: { label: "短弓", icon: "🏹" },
    longbow: { label: "长弓", icon: "🏹" },
    chain_mail: { label: "锁子甲", icon: "▣" },
    burnt_map: { label: "烧焦地图", icon: "🗺" },
    healing_potion: { label: "治疗药水", icon: "🧪" },
    mysterious_artifact: { label: "神秘遗物", icon: "🜄" },
    rusty_key: { label: "锈钥匙", icon: "🗝" },
  }, _necroMeta.ITEM_META_EXTENSIONS || {});

  const EQUIPMENT_SLOT_LABELS = {
    weapon: "主手",
    main_hand: "主手",
    ranged: "远程",
    offhand: "副手",
    armor: "护甲",
    shield: "盾牌",
    helmet: "头盔",
    boots: "靴子",
    accessory: "饰品",
  };

  const LOCATION_LABELS = Object.assign({
    camp_center: "营地中央",
    camp_fire: "篝火",
    iron_chest: "铁箱",
  }, _necroMeta.LOCATION_LABEL_EXTENSIONS || {});

  const ROOM_A = "room_a_spawn";
  const ROOM_B = "room_b_corridor";
  const ROOM_C = "room_c_secret_study";
  const ROOM_D = "room_d_lab";
  const ROOM_EXIT = "room_exit";
  const SECRET_DOOR_ID = "door_b_to_c";
  const FORMATION_COMPANIONS = Object.freeze(["scout", "analyst", "tactician"]);
  const FORMATION_OFFSETS = Object.freeze({
    scout: [
      { x: 0, y: 1 },
      { x: 0, y: -1 },
      { x: -1, y: 0 },
      { x: 1, y: 0 },
    ],
    analyst: [
      { x: 0, y: 2 },
      { x: 0, y: -2 },
      { x: -1, y: 1 },
      { x: 1, y: 1 },
    ],
    tactician: [
      { x: 0, y: 3 },
      { x: 0, y: -3 },
      { x: -1, y: 2 },
      { x: 1, y: 2 },
    ],
  });
  const ROOM_LABELS = Object.freeze({
    room_a_spawn: "A 区",
    room_b_corridor: "毒气走廊",
    room_c_secret_study: "暗室书房",
    room_d_lab: "实验室",
    room_exit: "出口区",
  });

  const state = {
    partyStatus: {},
    environmentObjects: {},
    playerInventory: {},
    combatState: {},
    mapData: {},
    activeLogFilters: new Set(["dialogue", "system", "narration"]),
    tacticalOverlayOpen: false,
    partyViewOpen: false,
    activePartyViewTab: "inventory",
    hasSyncedInitialState: false,
    hasStateProjectionBaseline: false,
    turnCount: 0,
    idleTimer: null,
    dialoguePollTimer: null,
    isLoading: false,
    currentLootTargetId: "",
    seenLootTargets: new Set(),
    currentInteractable: "",
    currentIntent: "",
    readTarget: "",
    activeDialogueTarget: "",
    dialogueText: "",
    xrayTraceTimers: [],
    xrayTraceAnimatingUntil: 0,
    xrayNodeTimings: {},
    qaTraceStepMs: Math.max(120, Number(QA_PARAMS.get("qa_trace_step_ms")) || 240),
    normalizedMap: null,
    fullNormalizedMap: null,
    mapLoadSource: "fixture",
    mapLoadReason: "",
    trapSenseEnabled: false,
    roomVisibleIds: new Set(),
    discoveredSecretDoorIds: new Set(),
    discoveredTrapIds: new Set(),
    backendRevealedTrapIds: new Set(),
    openedLocalDoorIds: new Set(),
    act1PerceptionResolved: false,
    speechRecognition: null,
    speechRecognitionSupported: Boolean(SpeechRecognition),
    isPttRecording: false,
    mapDebugChip: null,
    mapDebugSnapshot: null,
    mapDebugLastFetch: null,
    mapDebugLastMapLoad: null,
    mapSourceBadge: null,
    lastShowcaseSnapshot: null,
    demoScriptRunner: null,
    demoScriptControls: null,
    worldFlags: {},
    barkEpoch: 0,
    act3BarkEpoch: 0,
    lastProjectionJournalEvents: [],
  };

  const INTERACTION_SOURCES = new Set([
    "interaction",
    "ui_click",
    "ui_interaction",
    "keyboard_interaction",
  ]);
  const ACT3_SIDE_MARKERS = [
    "侦察员说得对",
    "顺着侦察员",
    "我同意侦察员",
    "一起嘲笑",
    "和侦察员一起嘲笑",
    "side_with_scout",
    "side with scout",
    "sided with scout",
    "mock gatekeeper",
  ];
  const ACT3_REBUKE_MARKERS = [
    "侦察员，闭嘴",
    "侦察员闭嘴",
    "训斥侦察员",
    "别拱火",
    "别再嘲笑",
    "rebuke_scout",
    "rebuke scout",
    "shut up scout",
  ];
  const DOOR_ATTACK_MARKERS = ["攻击门", "砸门", "打门", "破门", "attack door", "smash door"];
  const DOOR_INTERACT_MARKERS = [
    "打开门",
    "打开暗门",
    "开门",
    "开暗门",
    "检查暗门",
    "使用钥匙打开门",
    "用钥匙开门",
    "用 heavy_iron_key 打开门",
    "用钥匙打开门",
    "用钥匙打开出口门",
    "打开出口门",
    "打开 heavy_oak_door_1",
    "打开 exit_door",
    "检查 heavy_oak_door_1",
    "open secret door",
    "check secret door",
    "open heavy_oak_door_1",
    "open exit_door",
    "check heavy_oak_door_1",
  ];
  const READ_DIARY_MARKERS = [
    "读日记",
    "查看日记",
    "阅读日记",
    "hazard_diary",
    "diary",
  ];
  const STUDY_CONTEXT_READ_MARKERS = ["阅读", "调查", "查看", "read", "inspect", "check"];
  const CHEMICAL_NOTES_MARKERS = ["chemical_notes", "药剂笔记", "化学残页", "化学笔记"];
  const IRON_KEY_SKETCH_MARKERS = ["iron_key_sketch", "铁钥匙草图", "重铁钥匙草图", "钥匙草图"];
  const GATEKEEPER_TARGET_MARKERS = ["gatekeeper", "守门人", "训练无人机", "boss"];
  const GATEKEEPER_NEGOTIATION_MARKERS = ["日记", "药剂", "灵药", "危害", "实验", "解药", "钥匙", "真相"];
  const EXPLICIT_ATTACK_MARKERS = ["攻击", "打", "砍", "射击", "attack", "strike", "shoot"];
  const EXPLICIT_LOOT_MARKERS = ["搜刮", "洗劫", "拿走", "拾取", "loot", "take"];
  const EXPLICIT_MOVE_MARKERS = ["移动", "走到", "靠近", "前往", "move", "go to", "walk"];
  const TRAP_DISARM_ACTION_MARKERS = ["解除", "拆除", "拆掉", "disarm", "disable"];
  const TRAP_DISARM_TARGET_MARKERS = ["陷阱", "机关", "毒气", "gas_trap_1", "poison_trap", "trap"];
  const TRAP_DISARM_ACTOR_MARKERS = ["侦察员", "scout"];
  const BOSS_STRATEGY_MARKERS = ["怎么处理他", "怎么处理", "怎么办", "方案", "策略", "处理 gatekeeper", "deal with him", "strategy"];
  const BOSS_STEAL_KEY_MARKERS = ["偷钥匙", "偷 heavy_iron_key", "偷铁钥匙", "steal key", "steal heavy_iron_key"];
  const BOSS_DIARY_TRUTH_MARKERS = ["日记真相", "用日记", "真相说服", "说服他", "truth negotiation", "diary truth"];
  const BOSS_ASSAULT_MARKERS = ["动手", "杀了他", "解决他", "攻击 gatekeeper", "tactician 解决他", "战术员解决他", "assault", "attack gatekeeper"];

  function readQaNumber(name, fallback) {
    const value = Number(QA_PARAMS.get(name));
    return Number.isFinite(value) ? value : fallback;
  }

  function readQaActions() {
    const traceCommand = String(QA_PARAMS.get("qa_trace") || "").trim();
    const traceIntent = String(QA_PARAMS.get("qa_intent") || "").trim();
    const xrayMode = String(QA_PARAMS.get("qa_xray") || "").trim().toLowerCase();
    const previewTrace = QA_PARAMS.get("qa_preview_trace") === "1";
    const traceDelay = Math.max(0, readQaNumber("qa_trace_delay_ms", 900));
    const xrayDelay = Math.max(0, readQaNumber("qa_xray_delay_ms", 400));
    const shouldToggleXray =
      xrayMode === "toggle"
      || xrayMode === "collapse"
      || xrayMode === "expand";

    return {
      traceCommand,
      traceIntent,
      xrayMode,
      previewTrace,
      traceDelay,
      xrayDelay,
      shouldToggleXray,
    };
  }

  function extractEventLines(data) {
    const payload = safeObject(data);
    const gameState = safeObject(payload.game_state || payload.gameState || payload.state);
    const lines = [];
    [
      ...safeArray(payload.journal_events),
      ...safeArray(gameState.journal_events),
      ...safeArray(payload.state && payload.state.journal_events),
    ].forEach((entry) => {
      lines.push(String(entry || ""));
    });
    safeArray(payload.logs).forEach((entry) => {
      if (typeof entry === "string") {
        lines.push(entry);
        return;
      }
      const record = safeObject(entry);
      if (record.text != null) {
        lines.push(String(record.text));
      }
    });
    return lines;
  }

  function safeArrayOrObjectValues(value) {
    if (Array.isArray(value)) return value;
    if (value && typeof value === "object") return Object.values(value);
    return [];
  }

  const els = {
    currentLocation: document.getElementById("current-location"),
    networkState: document.getElementById("network-state"),
    turnCounter: document.getElementById("turn-counter"),
    tacticalOverlay: document.getElementById("tactical-pause-overlay"),
    tacticalToggleBtn: document.getElementById("tactical-toggle-btn"),
    restControls: document.getElementById("rest-controls"),
    shortRestBtn: document.getElementById("short-rest-btn"),
    longRestBtn: document.getElementById("long-rest-btn"),
    newTimelineBtn: document.getElementById("new-timeline-btn"),
    dialogueOverlay: document.getElementById("dialogue-overlay"),
    dialogueNpcName: document.getElementById("dialogue-npc-name"),
    dialogueText: document.getElementById("dialogue-text"),
    dialogueInput: document.getElementById("dialogue-input"),
    pttMicBtn: document.getElementById("ptt-mic-btn"),
    dialogueSendBtn: document.getElementById("dialogue-send-btn"),
    dialogueAttackBtn: document.getElementById("dialogue-attack-btn"),
    mainLayout: document.getElementById("main-layout"),
    xrayToggleBtn: document.getElementById("xray-toggle-btn"),
    nodeTimeline: document.getElementById("director-node-timeline") || document.getElementById("node-timeline"),
    patienceBar: document.getElementById("patience-bar"),
    patienceLabel: document.getElementById("patience-label"),
    patienceValue: document.getElementById("patience-value"),
    fearBar: document.getElementById("fear-bar"),
    fearLabel: document.getElementById("fear-label"),
    fearValue: document.getElementById("fear-value"),
    payloadSummary: document.getElementById("payload-summary"),
    jsonInspector: document.getElementById("json-inspector"),
    partyViewModal: document.getElementById("party-view-modal"),
    closePartyViewBtn: document.getElementById("close-party-view-btn"),
    partyViewTabs: document.getElementById("party-view-tabs"),
    partyViewContent: document.getElementById("party-view-content"),
    initiativeTracker: document.getElementById("initiative-tracker"),
    initiativeList: document.getElementById("initiative-list"),
    mapContainer: document.getElementById("map-container"),
    worldLog: document.getElementById("world-log"),
    partyRoster: document.getElementById("party-roster"),
    partyCount: document.getElementById("party-count"),
    environmentList: document.getElementById("environment-list"),
    environmentCount: document.getElementById("environment-count"),
    userInput: document.getElementById("user-input"),
    sendBtn: document.getElementById("send-btn"),
    shortcutButtons: Array.from(document.querySelectorAll(".shortcut-btn")),
    logFilterBar: document.getElementById("log-filter-bar"),
    logFilterButtons: Array.from(document.querySelectorAll(".log-filter-btn")),
    lootModal: document.getElementById("loot-modal"),
    lootTitle: document.getElementById("loot-title"),
    lootItems: document.getElementById("loot-items"),
    lootAllBtn: document.getElementById("loot-all-btn"),
    closeLootBtn: document.getElementById("close-loot-btn"),
    /* New layout elements */
    dockInput: document.getElementById("dock-input"),
    dockSendBtn: document.getElementById("dock-send-btn"),
    actProgress: document.getElementById("act-progress"),
    actTitle: document.getElementById("act-title"),
    actSummary: document.getElementById("act-summary"),
  };

  function setNetworkState(text, mode) {
    els.networkState.textContent = text;
    els.networkState.dataset.state = mode;
  }

  function setTacticalOverlay(open) {
    const allowLegacyConsole = QA_PARAMS.get("qa_tactical_console") === "1" || window.__ControlledAgent_QA_TACTICAL_CONSOLE__ === true;
    state.tacticalOverlayOpen = allowLegacyConsole && Boolean(open);
    if (els.tacticalOverlay) {
      els.tacticalOverlay.classList.toggle("is-hidden", !state.tacticalOverlayOpen);
      els.tacticalOverlay.classList.toggle("active", state.tacticalOverlayOpen);
      els.tacticalOverlay.setAttribute("aria-hidden", String(!state.tacticalOverlayOpen));
    }
    if (els.tacticalToggleBtn) {
      els.tacticalToggleBtn.setAttribute("aria-expanded", String(state.tacticalOverlayOpen));
    }
  }

  function toggleTacticalOverlay() {
    const allowLegacyConsole = QA_PARAMS.get("qa_tactical_console") === "1" || window.__ControlledAgent_QA_TACTICAL_CONSOLE__ === true;
    if (!allowLegacyConsole) return;
    setTacticalOverlay(!state.tacticalOverlayOpen);
  }

  function setPartyView(open) {
    state.partyViewOpen = Boolean(open);
    if (!els.partyViewModal) return;
    els.partyViewModal.classList.toggle("is-hidden", !state.partyViewOpen);
    els.partyViewModal.classList.toggle("active", state.partyViewOpen);
    els.partyViewModal.setAttribute("aria-hidden", String(!state.partyViewOpen));
    if (state.partyViewOpen) {
      renderPartyView();
    }
  }

  function togglePartyView() {
    setPartyView(!state.partyViewOpen);
  }

  function isEditableTarget(target) {
    if (!target || !(target instanceof Element)) return false;
    const tag = target.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable;
  }

  function safeObject(value) {
    return value && typeof value === "object" ? value : {};
  }

  function safeArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function padTurn(num) {
    return String(num).padStart(2, "0");
  }

  function nowStamp() {
    const now = new Date();
    return now.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  function normalizeId(id) {
    return String(id || "").trim().toLowerCase();
  }

  function isFiniteGridCoord(x, y) {
    return Number.isFinite(Number(x)) && Number.isFinite(Number(y));
  }

  function isWithinMapBounds(coord, mapLike) {
    const point = safeObject(coord);
    const map = safeObject(mapLike);
    const x = Number(point.x);
    const y = Number(point.y);
    const width = Number(map.width);
    const height = Number(map.height);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return false;
    return x >= 0 && x < width && y >= 0 && y < height;
  }

  function getSessionId() {
    return String(currentSessionId || "").trim() || DEFAULT_SESSION_ID;
  }

  function setSessionId(nextSessionId) {
    const normalized = String(nextSessionId || "").trim();
    if (!normalized) return getSessionId();
    currentSessionId = normalized;
    const nextUrl = new URL(window.location.href);
    nextUrl.searchParams.set("session_id", normalized);
    window.history.replaceState({}, "", nextUrl.toString());
    return normalized;
  }

  function buildTimelineSessionId() {
    return "hazard_lab_demo_" + Date.now();
  }

  function prettifyId(id) {
    return String(id || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  function getSpeakerMeta(id) {
    return SPEAKER_META[normalizeId(id)] || SPEAKER_META.npc;
  }

  function getDisplayName(id) {
    return getSpeakerMeta(id).name || prettifyId(id);
  }

  function getEntityDisplayName(id) {
    const key = normalizeId(id);
    const party = safeObject(state.partyStatus);
    const env = safeObject(state.environmentObjects);
    const record = safeObject(party[key] || env[key]);
    return String(record.name || getDisplayName(key) || prettifyId(key));
  }

  function getInitials(id) {
    const clean = normalizeId(id).replace(/[^a-z0-9]/g, "");
    if (!clean) return "??";
    return clean.slice(0, 2).toUpperCase();
  }

  function getCombatantLabel(id) {
    const key = normalizeId(id);
    const party = safeObject(state.partyStatus);
    const env = safeObject(state.environmentObjects);
    const data = safeObject(party[key] || env[key]);
    return data.name || getDisplayName(key) || prettifyId(key);
  }

  function getCombatantSigil(id) {
    const key = normalizeId(id);
    const env = safeObject(state.environmentObjects);
    const party = safeObject(state.partyStatus);
    if (key === "player") return "P";
    if (safeObject(env[key]).faction === "hostile") return "!";
    return getInitials(key || safeObject(party[key]).name);
  }

  function formatLocation(raw) {
    const key = normalizeId(raw);
    return LOCATION_LABELS[key] || raw || "未知地标";
  }

  function itemMeta(itemId) {
    const key = normalizeId(itemId);
    return ITEM_META[key] || { label: prettifyId(itemId), icon: "◻" };
  }

  function equipmentSlotLabel(slot) {
    const key = normalizeId(slot);
    return EQUIPMENT_SLOT_LABELS[key] || prettifyId(slot);
  }

  function equipmentSlotIcon(slot) {
    const key = normalizeId(slot);
    if (key === "main_hand" || key === "weapon") return "⚔";
    if (key === "ranged") return "🏹";
    if (key === "armor") return "▣";
    if (key === "shield" || key === "offhand") return "◖";
    return "◆";
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function objectEntries(obj) {
    return Object.entries(safeObject(obj)).filter(([, value]) => value && typeof value === "object");
  }

  function inventoryEntries(inv) {
    return Object.entries(safeObject(inv)).filter(([, count]) => Number(count) > 0);
  }

  function playerViewData() {
    const party = safeObject(state.partyStatus);
    return {
      ...safeObject(party.player),
      hp: safeObject(party.player).hp ?? 20,
      max_hp: safeObject(party.player).max_hp ?? safeObject(party.player).hp ?? 20,
      affection: safeObject(party.player).affection ?? 100,
      position: safeObject(party.player).position || "camp_center",
      inventory: state.playerInventory,
    };
  }

  function partyViewEntries() {
    const party = safeObject(state.partyStatus);
    const entries = [["player", playerViewData()]];
    objectEntries(party)
      .filter(([id, data]) => {
        const key = normalizeId(id);
        const status = normalizeId(safeObject(data).status || "alive");
        return key !== "player" && status !== "dead";
      })
      .sort(([leftId], [rightId]) => leftId.localeCompare(rightId))
      .forEach(([id, data]) => entries.push([id, safeObject(data)]));
    return entries.slice(0, 4);
  }

  function canLootTarget(target) {
    const data = safeObject(target);
    const status = normalizeId(data.status);
    return inventoryEntries(data.inventory).length > 0 && (status === "open" || status === "opened" || status === "dead");
  }

  function mapInteractableToStructuredAction(interactable) {
    const target = safeObject(interactable);
    const targetId = normalizeId(target.id || "");
    const targetType = normalizeId(target.type || target.entity_type || "");
    if (!targetId) return null;

    if (targetId === "hazard_diary" || targetType === "readable") {
      return {
        text: "阅读 " + targetId,
        intent: "READ",
        target: targetId,
        source: "interaction",
      };
    }
    if (targetId === "gatekeeper" || targetType === "npc" || targetType === "character") {
      return {
        text: "",
        intent: "CHAT",
        target: "gatekeeper",
        source: "interaction",
      };
    }
    if (targetType === "door" || targetId === "heavy_oak_door_1" || targetId === "exit_door") {
      const doorTarget = targetId === "exit_door" ? "heavy_oak_door_1" : targetId;
      return {
        text: "",
        intent: "INTERACT",
        target: doorTarget || "heavy_oak_door_1",
        source: "interaction",
      };
    }
    if (
      targetType === "loot"
      || targetType === "chest"
      || targetType === "corpse"
      || targetType === "container"
    ) {
      return {
        text: "",
        intent: "ui_action_loot",
        character: "player",
        target: targetId,
        source: "interaction",
      };
    }
    return {
      text: "检查 " + (target.label || target.name || targetId),
      intent: "INTERACT",
      target: targetId,
      source: "interaction",
    };
  }

  function isAct3ChoiceText(text) {
    const raw = String(text || "").trim();
    if (!raw) return false;
    const normalized = raw.toLowerCase();
    return ACT3_SIDE_MARKERS.some((marker) => raw.includes(marker) || normalized.includes(marker))
      || ACT3_REBUKE_MARKERS.some((marker) => raw.includes(marker) || normalized.includes(marker));
  }

  function isDoorAttackText(text) {
    const raw = String(text || "").trim();
    if (!raw) return false;
    const normalized = raw.toLowerCase();
    return DOOR_ATTACK_MARKERS.some((marker) => raw.includes(marker) || normalized.includes(marker));
  }

  function shouldRouteDoorInteractText(text) {
    const raw = String(text || "").trim();
    if (!raw) return false;
    const normalized = raw.toLowerCase();
    const hasDoorHint = raw.includes("门")
      || normalized.includes("door")
      || normalized.includes("heavy_oak_door_1");
    if (!hasDoorHint) return false;
    if (isDoorAttackText(raw)) return false;
    if (DOOR_INTERACT_MARKERS.some((marker) => raw.includes(marker) || normalized.includes(marker))) {
      return true;
    }
    if (hasDoorHint && /(打开|开门|使用|钥匙|检查|open|unlock|use|check)/i.test(raw)) {
      return true;
    }
    if (normalized.includes("heavy_oak_door_1")) {
      return /(打开|开门|使用|检查|open|interact|check)/i.test(raw);
    }
    return false;
  }

  function resolveMoveTargetFromText(text) {
    const raw = String(text || "").trim();
    if (!raw) return "";
    const coordinateMatch = raw.match(/(?:移动到|走到|靠近|前往|move(?:\s+to)?|go\s+to|walk\s+to)?\s*(\d{1,2})\s*[,，]\s*(\d{1,2})/i);
    if (coordinateMatch) {
      return coordinateMatch[1] + "," + coordinateMatch[2];
    }
    if (/b\s*-\s*d|b-d|door_b_to_d|实验室重门|实验室门|lab\s*door|laboratory\s*door/i.test(raw)) {
      return "4,8";
    }
    if (/heavy_oak_door_1|exit_door|出口门|最终门|final\s*exit|final\s*door/i.test(raw)) {
      return "17,4";
    }
    if (/a\s*-\s*b|a-b|door_a_to_b|走廊入口|corridor/i.test(raw) && /门|door|走廊|corridor/i.test(raw)) {
      return "3,5";
    }
    return "";
  }

  function resolveDoorTargetFromWorldContext(userLine) {
    const raw = String(userLine || "").trim();
    const normalized = raw.toLowerCase();
    if (!raw) return "";
    if (normalized.includes("door_a_to_b")) return "door_a_to_b";
    if (normalized.includes("door_b_to_c")) return "door_b_to_c";
    if (normalized.includes("door_b_to_d")) return "door_b_to_d";
    if (/a\s*-\s*b|a-b|走廊入口|corridor\s*entrance/i.test(raw)) {
      return "door_a_to_b";
    }
    if (/b\s*-\s*d|b-d|实验室重门|实验室门|lab\s*door|laboratory\s*door/i.test(raw)) {
      return "door_b_to_d";
    }
    if (normalized.includes("exit_door")) return "heavy_oak_door_1";
    if (normalized.includes("heavy_oak_door_1")) {
      return "heavy_oak_door_1";
    }
    if (/出口门|最终门|final\s*exit|final\s*door/i.test(raw)) {
      return "heavy_oak_door_1";
    }
    if (
      /暗门|密门|secret\s*door|secret\s*study|书房|study/i.test(raw)
      && (state.discoveredSecretDoorIds.has("door_b_to_c") || state.roomVisibleIds.has(ROOM_B))
    ) {
      return "door_b_to_c";
    }

    const fromCurrent = normalizeId(state.currentInteractable);
    if (["door_a_to_b", "door_b_to_c", "door_b_to_d", "heavy_oak_door_1"].includes(fromCurrent)) {
      return fromCurrent;
    }
    if (fromCurrent === "heavy_oak_door_1") {
      return "heavy_oak_door_1";
    }

    const inputController = window.ControlledAgentInputController;
    if (inputController && typeof inputController.getCurrentHighlightedInteractable === "function") {
      const highlighted = safeObject(inputController.getCurrentHighlightedInteractable());
      const highlightedId = normalizeId(highlighted.id);
      if (["door_a_to_b", "door_b_to_c", "door_b_to_d", "heavy_oak_door_1"].includes(highlightedId)) {
        return highlightedId;
      }
    }
    if (inputController && typeof inputController.findNearbyInteractable === "function") {
      const nearby = safeObject(inputController.findNearbyInteractable());
      const nearbyId = normalizeId(nearby.id);
      if (["door_a_to_b", "door_b_to_c", "door_b_to_d", "heavy_oak_door_1"].includes(nearbyId)) {
        return nearbyId;
      }
    }

    const worldDoor = safeObject(safeObject(state.environmentObjects).heavy_oak_door_1);
    if (Object.keys(worldDoor).length > 0) {
      const status = normalizeId(worldDoor.status || "");
      const isHidden = Boolean(
        worldDoor.is_hidden === true
        || worldDoor.isHidden === true
        || status === "hidden"
      );
      if (!isHidden) {
        return "heavy_oak_door_1";
      }
    }
    return "";
  }

  function clearTransientInteractionContext(options = {}) {
    const opts = options && typeof options === "object" ? options : {};
    state.currentInteractable = "";
    state.currentIntent = "";
    state.readTarget = "";
    if (opts.keepDialogueTarget !== true) {
      state.activeDialogueTarget = "";
    }
  }

  function rememberTransientInteractionContext(intent, target, source) {
    const normalizedIntent = String(intent || "").trim().toUpperCase();
    const normalizedTarget = normalizeId(target);
    const normalizedSource = String(source || "").trim().toLowerCase();
    if (INTERACTION_SOURCES.has(normalizedSource)) {
      state.currentInteractable = normalizedTarget;
      state.currentIntent = normalizedIntent;
    }
    if (normalizedIntent === "READ") {
      state.readTarget = normalizedTarget;
    }
  }

  function resolveChatRouting(text, intent, options = {}) {
    const opts = options && typeof options === "object" ? options : {};
    const userLine = String(text || "").trim();
    const explicitIntent = String(intent || "").trim();
    const explicitTarget = String(opts.target || "").trim();
    const explicitSource = String(opts.source || "").trim();

    let resolvedIntent = explicitIntent;
    let resolvedTarget = explicitTarget;
    let resolvedSource = explicitSource;
    const activeMapId = String(MAP_ID || "").trim().toLowerCase();
    const userLineLower = userLine.toLowerCase();
    const isHazardLab = activeMapId === "hazard_lab";
    const hasReadDiaryText = isHazardLab && READ_DIARY_MARKERS.some((marker) => {
      const key = String(marker || "");
      return key && (userLine.includes(key) || userLineLower.includes(key.toLowerCase()));
    });
    const hasStudyContextReadVerb = STUDY_CONTEXT_READ_MARKERS.some((marker) => {
      const key = String(marker || "");
      return key && (userLine.includes(key) || userLineLower.includes(key.toLowerCase()));
    });
    const hasChemicalNotesText = isHazardLab && CHEMICAL_NOTES_MARKERS.some((marker) => {
      const key = String(marker || "");
      return key && (userLine.includes(key) || userLineLower.includes(key.toLowerCase()));
    });
    const hasIronKeySketchText = isHazardLab && IRON_KEY_SKETCH_MARKERS.some((marker) => {
      const key = String(marker || "");
      return key && (userLine.includes(key) || userLineLower.includes(key.toLowerCase()));
    });
    const hasGatekeeperNegotiationText = isHazardLab
      && GATEKEEPER_TARGET_MARKERS.some((marker) => {
        const key = String(marker || "");
        return key && (userLine.includes(key) || userLineLower.includes(key.toLowerCase()));
      })
      && GATEKEEPER_NEGOTIATION_MARKERS.some((marker) => {
        const key = String(marker || "");
        return key && (userLine.includes(key) || userLineLower.includes(key.toLowerCase()));
      });
    const isExplicitAttack = EXPLICIT_ATTACK_MARKERS.some((marker) => {
      const key = String(marker || "");
      return key && (userLine.includes(key) || userLineLower.includes(key.toLowerCase()));
    });
    const isExplicitLoot = EXPLICIT_LOOT_MARKERS.some((marker) => {
      const key = String(marker || "");
      return key && (userLine.includes(key) || userLineLower.includes(key.toLowerCase()));
    });
    const isExplicitMove = EXPLICIT_MOVE_MARKERS.some((marker) => {
      const key = String(marker || "");
      return key && (userLine.includes(key) || userLineLower.includes(key.toLowerCase()));
    });
    const hasDoorOpenSemantics = shouldRouteDoorInteractText(userLine);
    const hasTrapDisarmText = isHazardLab
      && TRAP_DISARM_ACTION_MARKERS.some((marker) => userLine.includes(marker) || userLineLower.includes(String(marker).toLowerCase()))
      && TRAP_DISARM_TARGET_MARKERS.some((marker) => userLine.includes(marker) || userLineLower.includes(String(marker).toLowerCase()))
      && TRAP_DISARM_ACTOR_MARKERS.some((marker) => userLine.includes(marker) || userLineLower.includes(String(marker).toLowerCase()));
    const hasBossStrategyText = isHazardLab
      && BOSS_STRATEGY_MARKERS.some((marker) => userLine.includes(marker) || userLineLower.includes(String(marker).toLowerCase()))
      && (/他|gatekeeper|守门人|boss/i.test(userLine));
    const hasBossStealText = isHazardLab
      && TRAP_DISARM_ACTOR_MARKERS.some((marker) => userLine.includes(marker) || userLineLower.includes(String(marker).toLowerCase()))
      && BOSS_STEAL_KEY_MARKERS.some((marker) => userLine.includes(marker) || userLineLower.includes(String(marker).toLowerCase()));
    const hasBossDiaryTruthText = isHazardLab
      && BOSS_DIARY_TRUTH_MARKERS.some((marker) => userLine.includes(marker) || userLineLower.includes(String(marker).toLowerCase()));
    const hasImplicitBossTruthNegotiationText = isHazardLab
      && !hasDoorOpenSemantics
      && /(你不是守卫|实验品|药剂对你|药剂.*做了什么|喝了.*药剂|危害药剂|实验.*真相|日记.*真相|gatekeeper_elixir_truth|解药)/i.test(userLine)
      && /(钥匙|heavy_iron_key|key|带你离开|带你走|放你走|交出|交出来|给我)/i.test(userLine);
    const hasBossTruthNegotiationText = hasBossDiaryTruthText || hasImplicitBossTruthNegotiationText;
    const hasBossAssaultText = isHazardLab
      && BOSS_ASSAULT_MARKERS.some((marker) => userLine.includes(marker) || userLineLower.includes(String(marker).toLowerCase()));
    const normalizedIntent = String(resolvedIntent || "").trim().toUpperCase();
    const isExplicitNonDialogue = isExplicitAttack || isExplicitLoot || isExplicitMove || hasDoorOpenSemantics;
    const shouldBackfillDoorTarget =
      activeMapId === "hazard_lab"
      && hasDoorOpenSemantics
      && !isDoorAttackText(userLine);
    const resolvedDoorTarget = shouldBackfillDoorTarget
      ? resolveDoorTargetFromWorldContext(userLine)
      : "";
    const resolvedMoveTarget = isHazardLab ? resolveMoveTargetFromText(userLine) : "";

    if (hasStudyContextReadVerb && (hasChemicalNotesText || hasIronKeySketchText)) {
      resolvedIntent = "READ";
      resolvedTarget = hasIronKeySketchText ? "iron_key_sketch" : "chemical_notes";
      resolvedSource = "act3_study_context";
      return {
        userLine,
        intentValue: resolvedIntent,
        target: resolvedTarget,
        source: resolvedSource,
        intentContext: {
          action_target: resolvedTarget,
          source: resolvedSource,
        },
      };
    }

    if (
      isExplicitMove
      && resolvedMoveTarget
      && !explicitIntent
      && !isExplicitAttack
      && !isExplicitLoot
      && !hasBossTruthNegotiationText
    ) {
      resolvedIntent = "MOVE";
      resolvedTarget = resolvedMoveTarget;
      resolvedSource = "ui_text_move";
      return {
        userLine,
        intentValue: resolvedIntent,
        target: resolvedTarget,
        source: resolvedSource,
        intentContext: {
          action_target: resolvedTarget,
          source: resolvedSource,
        },
      };
    }

    if (hasBossStrategyText) {
      resolvedIntent = "CHAT";
      resolvedTarget = "gatekeeper";
      resolvedSource = "boss_strategy";
      return {
        userLine,
        intentValue: resolvedIntent,
        target: resolvedTarget,
        source: resolvedSource,
        intentContext: { boss_strategy_request: true },
      };
    }

    if (hasBossStealText) {
      resolvedIntent = "INTERACT";
      resolvedTarget = "gatekeeper";
      resolvedSource = "boss_steal_key";
      return {
        userLine,
        intentValue: resolvedIntent,
        target: resolvedTarget,
        source: resolvedSource,
        actor: "scout",
        intentContext: {
          boss_route: "scout_steal",
          action_actor: "scout",
          action_target: "heavy_iron_key",
          source: "boss_steal_key",
        },
      };
    }

    if (hasBossTruthNegotiationText) {
      resolvedIntent = "CHAT";
      resolvedTarget = "gatekeeper";
      resolvedSource = "boss_diary_truth";
      return {
        userLine,
        intentValue: resolvedIntent,
        target: resolvedTarget,
        source: resolvedSource,
        intentContext: {
          act4_diary_truth: true,
          boss_route: "negotiation",
        },
      };
    }

    if (hasBossAssaultText) {
      resolvedIntent = "ATTACK";
      resolvedTarget = "gatekeeper";
      resolvedSource = "boss_assault";
      return {
        userLine,
        intentValue: resolvedIntent,
        target: resolvedTarget,
        source: resolvedSource,
        actor: /tactician|战术员/i.test(userLine) ? "tactician" : "",
        intentContext: {
          boss_route: "assault",
          source: "boss_assault",
        },
      };
    }

    if (hasGatekeeperNegotiationText && !isExplicitAttack) {
      resolvedIntent = "CHAT";
      resolvedTarget = "gatekeeper";
      resolvedSource = "ui_text_normalized";
      return {
        userLine,
        intentValue: resolvedIntent,
        target: resolvedTarget,
        source: resolvedSource,
        intentContext: { diary_negotiation_hint: true },
      };
    }

    if (hasReadDiaryText) {
      resolvedIntent = "READ";
      resolvedTarget = "hazard_diary";
      resolvedSource = "ui_text_normalized";
      return {
        userLine,
        intentValue: resolvedIntent,
        target: resolvedTarget,
        source: resolvedSource,
        intentContext: {},
      };
    }

    if (hasTrapDisarmText) {
      resolvedIntent = "DISARM";
      resolvedTarget = "gas_trap_1";
      resolvedSource = "ui_text_normalized";
      return {
        userLine,
        intentValue: resolvedIntent,
        target: resolvedTarget,
        source: resolvedSource,
        actor: "scout",
        intentContext: {
          action_actor: "scout",
          action_target: "gas_trap_1",
          source: "ui_text_normalized",
          action: "disarm_trap",
        },
      };
    }

    if (
      shouldBackfillDoorTarget
      && (!resolvedIntent || normalizedIntent === "INTERACT")
    ) {
      resolvedIntent = "INTERACT";
      if (!String(resolvedTarget || "").trim()) {
        resolvedTarget = resolvedDoorTarget || "heavy_oak_door_1";
      }
      resolvedSource = resolvedSource || "text_input";
    }

    if (!resolvedIntent) {
      const activeDialogueTarget = normalizeId(state.activeDialogueTarget);
      if (activeDialogueTarget === "gatekeeper" && isExplicitAttack) {
        resolvedIntent = "ATTACK";
        resolvedTarget = resolvedTarget || "gatekeeper";
        resolvedSource = resolvedSource || "text_input";
      } else if (activeDialogueTarget === "gatekeeper" && !isExplicitNonDialogue) {
        resolvedIntent = "CHAT";
        resolvedTarget = resolvedTarget || "gatekeeper";
        resolvedSource = resolvedSource || "dialogue_input";
      } else if (isAct3ChoiceText(userLine)) {
        resolvedIntent = "CHAT";
        resolvedTarget = resolvedTarget || "gatekeeper";
        resolvedSource = resolvedSource || "text_input";
      } else {
        resolvedIntent = "chat";
        resolvedSource = resolvedSource || "text_input";
      }
    }

    if (String(resolvedIntent).trim().toUpperCase() === "READ") {
      resolvedSource = resolvedSource || "interaction";
      if (!String(resolvedTarget || "").trim()) {
        resolvedTarget = state.readTarget || "hazard_diary";
      }
    }

    return {
      userLine,
      intentValue: String(resolvedIntent || "").trim(),
      target: String(resolvedTarget || "").trim(),
      source: String(resolvedSource || "").trim(),
      intentContext: {},
    };
  }

  function buildChatPayload(text, intent, character, options = {}) {
    const routed = resolveChatRouting(text, intent, options);
    const characterId = character ? normalizeId(character) : normalizeId(routed.actor);
    const clientPosition = resolveClientPlayerGridPositionForPayload(routed.target, options);
    const payload = {
      user_input: routed.userLine,
      intent: routed.intentValue,
      target: routed.target,
      source: routed.source,
      session_id: getSessionId(),
      map_id: MAP_ID,
    };
    if (clientPosition) {
      payload.client_player_position = { x: clientPosition.x, y: clientPosition.y };
      payload.player_position = [clientPosition.x, clientPosition.y];
    }
    if (characterId) {
      payload.character = characterId;
    }
    if (routed.intentContext && Object.keys(routed.intentContext).length > 0) {
      payload.intent_context = routed.intentContext;
    }
    return { payload, routed };
  }

  function hpPercent(hp, maxHp) {
    if (!Number.isFinite(hp) || !Number.isFinite(maxHp) || maxHp <= 0) return 0;
    return clamp((hp / maxHp) * 100, 0, 100);
  }

  function affectionPercent(affection) {
    if (!Number.isFinite(affection)) return 50;
    return clamp(((affection + 100) / 200) * 100, 0, 100);
  }

  function affectionLabel(affection) {
    if (!Number.isFinite(affection)) return "未知";
    if (affection >= 60) return "忠诚";
    if (affection >= 20) return "友善";
    if (affection > -20) return "中立";
    if (affection > -60) return "警惕";
    return "敌意";
  }

  function describeLogKind(line) {
    const text = String(line || "");
    if (/(d20|dc\s*\d+|掷骰|检定|优势|劣势|critical|暴击|大失败|大成功)/i.test(text)) {
      return "roll";
    }
    return "system";
  }

  function createEmptyState(text) {
    const block = document.createElement("div");
    block.className = "empty-state";
    block.textContent = text;
    return block;
  }

  function appendLogEntry(kind, label, text, options = {}) {
    const logType = options.logType || "system";
    const entry = document.createElement("article");
    entry.className = "log-entry log-entry--" + kind + " type-" + logType;
    entry.dataset.logType = logType;

    const meta = document.createElement("div");
    meta.className = "log-meta";

    const badge = document.createElement("div");
    badge.className = "log-badge";

    const sigil = document.createElement("span");
    sigil.className = "log-sigil";
    sigil.textContent = options.sigil || "◈";

    const badgeLabel = document.createElement("span");
    badgeLabel.className = "log-label";
    badgeLabel.textContent = label;
    if (options.color) {
      badgeLabel.style.color = options.color;
    }

    badge.appendChild(sigil);
    badge.appendChild(badgeLabel);

    const stamp = document.createElement("span");
    stamp.textContent = "T" + padTurn(state.turnCount) + " · " + nowStamp();

    meta.appendChild(badge);
    meta.appendChild(stamp);

    const body = document.createElement("div");
    body.className = "log-body";
    body.textContent = text;

    entry.appendChild(meta);
    entry.appendChild(body);
    els.worldLog.appendChild(entry);
    applyLogFilters();
    els.worldLog.scrollTop = els.worldLog.scrollHeight;
  }

  function setFilterButtonState(button, active) {
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }

  function applyLogFilters() {
    const entries = els.worldLog.querySelectorAll(".log-entry");
    entries.forEach((entry) => {
      const type = entry.dataset.logType || "system";
      const visible = state.activeLogFilters.has(type);
      entry.classList.toggle("is-hidden", !visible);
    });
  }

  function handleLogFilterClick(event) {
    const button = event.target.closest(".log-filter-btn");
    if (!button) return;

    const filter = button.dataset.filter;
    if (!filter) return;

    const isActive = state.activeLogFilters.has(filter);
    if (isActive && state.activeLogFilters.size === 1) {
      return;
    }

    if (isActive) {
      state.activeLogFilters.delete(filter);
    } else {
      state.activeLogFilters.add(filter);
    }

    els.logFilterButtons.forEach((candidate) => {
      setFilterButtonState(
        candidate,
        state.activeLogFilters.has(candidate.dataset.filter || "")
      );
    });
    applyLogFilters();
  }

  function renderChrome(currentLocation) {
    els.currentLocation.textContent = currentLocation || "未知区域";
    els.turnCounter.textContent = padTurn(state.turnCount);
  }

  function createItemAction(label, action, itemId, ownerId) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "item-action";
    if (action === "equip") button.classList.add("btn-equip");
    if (action === "unequip") button.classList.add("btn-unequip");
    button.dataset.partyAction = action;
    button.dataset.itemId = itemId;
    button.dataset.ownerId = ownerId;
    button.textContent = label;
    return button;
  }

  function createEquipmentPanel(ownerId, equipment) {
    const panel = document.createElement("div");
    panel.className = "equipped-container equipment-panel";

    const label = document.createElement("p");
    label.className = "gear-section-label";
    label.textContent = "已装备 Equipped";
    panel.appendChild(label);

    const gear = safeObject(equipment);
    const slots = [
      { key: "main_hand", empty: "拳头 (未装备主手)" },
      { key: "ranged", empty: "未装备远程武器" },
      { key: "armor", empty: "未装备护甲" },
    ];

    slots.forEach(({ key, empty }) => {
      const rawItemId = gear[key];
      const normalizedItemId = normalizeId(rawItemId);
      const meta = itemMeta(normalizedItemId);
      const item = document.createElement("div");
      item.className = "equipped-item";

      const text = document.createElement("span");
      if (!normalizedItemId) {
        item.classList.add("equipped-item--empty");
        text.className = "empty-slot";
        text.textContent = equipmentSlotIcon(key) + " " + empty;
        item.appendChild(text);
        panel.appendChild(item);
        return;
      }

      text.textContent = equipmentSlotIcon(key) + " " + meta.label + " (" + equipmentSlotLabel(key) + ")";

      const actions = document.createElement("div");
      actions.className = "item-actions";
      actions.appendChild(createItemAction("卸下", "unequip", normalizedItemId, ownerId));

      item.appendChild(text);
      item.appendChild(actions);
      panel.appendChild(item);
    });

    return panel;
  }

  function createInventoryPanel(ownerId, inventory) {
    const panel = document.createElement("div");
    panel.className = "party-pack inventory-list";

    const label = document.createElement("p");
    label.className = "gear-section-label";
    label.textContent = "你的背包 Inventory";
    panel.appendChild(label);

    const invItems = inventoryEntries(inventory).slice(0, 6);
    if (invItems.length === 0) {
      panel.appendChild(makeItemTag("空背包", "⟡"));
      return panel;
    }

    invItems.forEach(([itemId, count]) => {
      const normalizedItemId = normalizeId(itemId);
      const metaItem = itemMeta(normalizedItemId);
      const row = document.createElement("div");
      row.className = "inventory-item-row";

      const itemTag = makeItemTag(metaItem.label + " x" + count, metaItem.icon);
      const actions = document.createElement("div");
      actions.className = "item-actions";
      actions.appendChild(createItemAction("检查", "inspect", normalizedItemId, ownerId));
      actions.appendChild(createItemAction("装备", "equip", normalizedItemId, ownerId));

      row.appendChild(itemTag);
      row.appendChild(actions);
      panel.appendChild(row);
    });

    return panel;
  }

  function createPartyViewEquipment(ownerId, equipment) {
    const wrap = document.createElement("div");
    wrap.className = "party-view-equipment";

    const title = document.createElement("h4");
    title.textContent = "装备槽位";
    wrap.appendChild(title);

    const gear = safeObject(equipment);
    ["main_hand", "offhand", "ranged", "armor"].forEach((slot) => {
      const itemId = normalizeId(gear[slot]);
      const row = document.createElement("div");
      row.className = "party-view-slot" + (itemId ? "" : " party-view-slot--empty");

      const label = document.createElement("span");
      label.textContent = equipmentSlotIcon(slot) + " " + equipmentSlotLabel(slot);

      const value = document.createElement("strong");
      value.textContent = itemId ? itemMeta(itemId).label : "空";

      row.appendChild(label);
      row.appendChild(value);
      if (itemId) {
        row.appendChild(createItemAction("卸下", "unequip", itemId, ownerId));
      }
      wrap.appendChild(row);
    });

    return wrap;
  }

  function createPartyViewInventory(ownerId, inventory) {
    const wrap = document.createElement("div");
    wrap.className = "party-view-inventory";

    const title = document.createElement("h4");
    title.textContent = "背包格";
    wrap.appendChild(title);

    const grid = document.createElement("div");
    grid.className = "party-view-inventory-grid";

    const items = inventoryEntries(inventory);
    const slotCount = Math.max(12, Math.ceil(items.length / 4) * 4);
    for (let index = 0; index < slotCount; index += 1) {
      const slot = document.createElement("div");
      slot.className = "party-view-inventory-slot";

      const entry = items[index];
      if (!entry) {
        slot.classList.add("party-view-inventory-slot--empty");
        grid.appendChild(slot);
        continue;
      }

      const [itemId, count] = entry;
      const normalizedItemId = normalizeId(itemId);
      const meta = itemMeta(normalizedItemId);
      slot.dataset.partyAction = "use";
      slot.dataset.itemId = normalizedItemId;
      slot.dataset.ownerId = ownerId;
      const icon = document.createElement("span");
      icon.className = "party-view-item-icon";
      icon.textContent = meta.icon;

      const name = document.createElement("strong");
      name.textContent = meta.label;

      const qty = document.createElement("small");
      qty.textContent = "x" + count;

      const actions = document.createElement("div");
      actions.className = "party-view-item-actions";
      actions.appendChild(createItemAction("使用", "use", normalizedItemId, ownerId));
      actions.appendChild(createItemAction("装备", "equip", normalizedItemId, ownerId));

      slot.appendChild(icon);
      slot.appendChild(name);
      slot.appendChild(qty);
      slot.appendChild(actions);
      grid.appendChild(slot);
    }

    wrap.appendChild(grid);
    return wrap;
  }

  function createPartyViewColumn(id, rawData) {
    const data = safeObject(rawData);
    const card = document.createElement("article");
    card.className = "party-view-character";

    const meta = getSpeakerMeta(id);
    const head = document.createElement("div");
    head.className = "party-view-character-head";

    const portrait = document.createElement("div");
    portrait.className = "party-view-portrait";
    portrait.textContent = getInitials(id);
    portrait.style.background = "radial-gradient(circle at 30% 30%, " + meta.color + ", #101319 72%)";

    const text = document.createElement("div");
    const name = document.createElement("h3");
    name.textContent = getDisplayName(id);
    name.style.color = meta.color;
    const role = document.createElement("p");
    role.textContent = "HP " + (data.hp ?? "—") + " / " + (data.max_hp ?? data.hp ?? "—") + " · " + formatLocation(data.position || "camp_center");

    text.appendChild(name);
    text.appendChild(role);
    head.appendChild(portrait);
    head.appendChild(text);

    card.appendChild(head);
    card.appendChild(createPartyViewEquipment(normalizeId(id), data.equipment));
    card.appendChild(createPartyViewInventory(normalizeId(id), data.inventory));
    return card;
  }

  function renderPartyViewTabs() {
    if (!els.partyViewTabs) return;
    els.partyViewTabs.querySelectorAll(".party-view-tab").forEach((button) => {
      const active = normalizeId(button.dataset.partyTab) === state.activePartyViewTab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function renderPartyView() {
    if (!els.partyViewContent) return;
    renderPartyViewTabs();
    els.partyViewContent.innerHTML = "";

    if (state.activePartyViewTab !== "inventory") {
      const wip = document.createElement("div");
      wip.className = "party-view-wip";
      wip.textContent = "建设中 WIP";
      els.partyViewContent.appendChild(wip);
      return;
    }

    const grid = document.createElement("div");
    grid.className = "party-view-grid";
    partyViewEntries().forEach(([id, data]) => {
      grid.appendChild(createPartyViewColumn(id, data));
    });
    els.partyViewContent.appendChild(grid);
  }

  function createTurnResourceIcon(label, value, type) {
    const available = Number(value) > 0;
    const icon = document.createElement("span");
    icon.className = "turn-resource-icon turn-resource-icon--" + type + (available ? " is-available" : " is-spent");
    icon.title = label + ": " + (available ? "可用" : "已消耗");
    icon.setAttribute("aria-label", icon.title);
    icon.textContent = type === "bonus" ? "▲" : "●";
    return icon;
  }

  function createTurnResourcesPanel(id, data) {
    const entity = safeObject(data);
    if (normalizeId(entity.faction) === "hostile") return null;

    const resources = safeObject(safeObject(state.combatState.turn_resources)[normalizeId(id)]);
    const hasResources = Object.prototype.hasOwnProperty.call(resources, "action")
      || Object.prototype.hasOwnProperty.call(resources, "bonus_action")
      || Object.prototype.hasOwnProperty.call(resources, "movement");
    if (!hasResources) return null;

    const panel = document.createElement("div");
    panel.className = "turn-resources";

    const label = document.createElement("span");
    label.className = "turn-resources-label";
    label.textContent = "本回合";

    const icons = document.createElement("div");
    icons.className = "turn-resource-icons";
    icons.appendChild(createTurnResourceIcon("主动作", resources.action, "action"));
    icons.appendChild(createTurnResourceIcon("附赠动作", resources.bonus_action, "bonus"));

    const movement = Number(resources.movement);
    const move = document.createElement("div");
    move.className = "turn-resource-move";

    const moveText = document.createElement("span");
    moveText.textContent = "🥾 " + (Number.isFinite(movement) ? movement : 0) + "ft";

    const moveTrack = document.createElement("span");
    moveTrack.className = "turn-resource-move-track";

    const moveFill = document.createElement("span");
    moveFill.className = "turn-resource-move-fill";
    moveFill.style.width = clamp(Number.isFinite(movement) ? movement : 0, 0, 30) / 30 * 100 + "%";

    moveTrack.appendChild(moveFill);
    move.appendChild(moveText);
    move.appendChild(moveTrack);

    panel.appendChild(label);
    panel.appendChild(icons);
    panel.appendChild(move);
    return panel;
  }

  function turnResourcesFor(id) {
    return safeObject(safeObject(state.combatState.turn_resources)[normalizeId(id)]);
  }

  function hasTurnResourcesFor(id) {
    const resources = turnResourcesFor(id);
    return Object.prototype.hasOwnProperty.call(resources, "action")
      || Object.prototype.hasOwnProperty.call(resources, "bonus_action")
      || Object.prototype.hasOwnProperty.call(resources, "movement");
  }

  function isHostileCombatant(id) {
    const key = normalizeId(id);
    const entity = safeObject(safeObject(state.partyStatus)[key] || safeObject(state.environmentObjects)[key]);
    return normalizeId(entity.faction) === "hostile";
  }

  function createInitiativeResourceDots(id) {
    const resources = turnResourcesFor(id);
    const wrap = document.createElement("span");
    wrap.className = "initiative-resources";

    const action = document.createElement("span");
    action.className = "initiative-resource-dot initiative-resource-dot--action" + (Number(resources.action) > 0 ? " is-available" : " is-spent");
    action.title = "主动作: " + (Number(resources.action) > 0 ? "可用" : "已耗尽");

    const bonus = document.createElement("span");
    bonus.className = "initiative-resource-triangle initiative-resource-triangle--bonus" + (Number(resources.bonus_action) > 0 ? " is-available" : " is-spent");
    bonus.title = "附赠动作: " + (Number(resources.bonus_action) > 0 ? "可用" : "已耗尽");

    wrap.appendChild(action);
    wrap.appendChild(bonus);
    return wrap;
  }

  function gridFromCollision(collision, width, height) {
    const w = Math.max(1, Number(width) || 1);
    const h = Math.max(1, Number(height) || 1);
    const rows = safeArray(collision);
    const grid = [];
    for (let y = 0; y < h; y += 1) {
      const srcRow = safeArray(rows[y]);
      const row = [];
      for (let x = 0; x < w; x += 1) {
        row.push(Boolean(srcRow[x]) ? "W" : ".");
      }
      grid.push(row);
    }
    return grid;
  }

  function mapDataFromNormalized(normalizedMap) {
    const map = safeObject(normalizedMap);
    const width = Math.max(1, Number(map.width) || 1);
    const height = Math.max(1, Number(map.height) || 1);
    const collision = safeArray(map.collision).map((row) => safeArray(row).map(Boolean));
    const losBlockers = safeArray(map.losBlockers).map((row) => safeArray(row).map(Boolean));
    const groundTypes = safeArray(map.groundTypes).map((row) => safeArray(row).map((v) => Number(v) || 0));
    const rooms = safeArray(map.rooms).map((room) => {
      const record = safeObject(room);
      return {
        id: String(record.id || "").trim(),
        x: Number(record.x) || 0,
        y: Number(record.y) || 0,
        w: Math.max(1, Number(record.w) || 1),
        h: Math.max(1, Number(record.h) || 1),
      };
    });
    const visibleRooms = safeArray(map.visibleRooms || map.visible_rooms).map((roomId) => String(roomId || "").trim()).filter(Boolean);
    return {
      id: String(map.id || MAP_ID || "").trim(),
      width,
      height,
      grid: gridFromCollision(collision, width, height),
      collision,
      los_blockers: losBlockers,
      ground_types: groundTypes,
      rooms,
      visible_rooms: visibleRooms,
      obstacles: [],
    };
  }

  function attachVisibleMapObjectsToMapData(mapData, projectionMap) {
    const projection = safeObject(projectionMap);
    return {
      ...safeObject(mapData),
      interactables: safeArray(projection.interactables).map((item) => ({ ...safeObject(item) })),
      triggers: safeArray(projection.triggers).map((item) => ({ ...safeObject(item) })),
      spawns: safeArray(projection.spawns).map((item) => ({ ...safeObject(item) })),
    };
  }

  function isCoordInsideVisibleRooms(coord, normalizedMap) {
    const point = safeObject(coord);
    const map = safeObject(normalizedMap);
    const rooms = safeArray(map.rooms);
    if (!rooms.length) return true;
    const room = roomAtPosition(rooms, point.x, point.y);
    if (!room) return false;
    const roomId = String(safeObject(room).id || "").trim();
    if (!roomId) return false;
    return state.roomVisibleIds.has(roomId);
  }

  function visibleRoomIdsForMap(mapLike) {
    const map = safeObject(mapLike);
    const explicit = safeArray(map.visible_rooms || map.visibleRooms)
      .map((roomId) => String(roomId || "").trim())
      .filter(Boolean);
    if (explicit.length) return new Set(explicit);
    if (state.roomVisibleIds && state.roomVisibleIds.size) return new Set(Array.from(state.roomVisibleIds));
    return new Set();
  }

  function isCoordInsideVisibleRoomsForFormation(coord, mapLike) {
    const map = safeObject(mapLike);
    const rooms = safeArray(map.rooms);
    if (!rooms.length) return true;
    const room = roomAtPosition(rooms, Number(safeObject(coord).x), Number(safeObject(coord).y));
    if (!room) return false;
    const visible = visibleRoomIdsForMap(map);
    if (!visible.size) return true;
    return visible.has(String(safeObject(room).id || "").trim());
  }

  function isWalkableFormationCell(mapLike, coord, occupiedCells) {
    const map = safeObject(mapLike);
    const point = {
      x: Math.round(Number(safeObject(coord).x)),
      y: Math.round(Number(safeObject(coord).y)),
    };
    if (!isWithinMapBounds(point, map)) return false;
    const key = point.x + "," + point.y;
    if (occupiedCells && occupiedCells.has(key)) return false;
    const collision = safeArray(map.collision);
    if (Boolean(safeArray(collision[point.y])[point.x])) return false;
    const grid = safeArray(map.grid);
    const cell = String(safeArray(grid[point.y])[point.x] || ".").toUpperCase();
    if (cell === "W" || cell === "#") return false;
    return isCoordInsideVisibleRoomsForFormation(point, map);
  }

  function formationSearchCandidates(desiredCell) {
    const desired = {
      x: Math.round(Number(safeObject(desiredCell).x)),
      y: Math.round(Number(safeObject(desiredCell).y)),
    };
    const out = [desired];
    for (let radius = 1; radius <= 4; radius += 1) {
      for (let dy = -radius; dy <= radius; dy += 1) {
        for (let dx = -radius; dx <= radius; dx += 1) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) !== radius) continue;
          out.push({ x: desired.x + dx, y: desired.y + dy });
        }
      }
    }
    return out;
  }

  function findNearestWalkableFormationCell(mapLike, desiredCell, occupiedCells) {
    return formationSearchCandidates(desiredCell).find((candidate) => (
      isWalkableFormationCell(mapLike, candidate, occupiedCells)
    )) || null;
  }

  function getInputControllerPosition() {
    if (!window.ControlledAgentInputController || typeof window.ControlledAgentInputController.getPlayerPosition !== "function") {
      return null;
    }
    return safeObject(window.ControlledAgentInputController.getPlayerPosition());
  }

  function getTacticalPlayerPosition() {
    if (!window.ControlledAgentTacticalMap || typeof window.ControlledAgentTacticalMap.getPlayerGridPosition !== "function") {
      return null;
    }
    return safeObject(window.ControlledAgentTacticalMap.getPlayerGridPosition());
  }

  function getClientPlayerGridPosition() {
    const candidates = [getTacticalPlayerPosition(), getInputControllerPosition()];
    for (const candidate of candidates) {
      const x = Number(safeObject(candidate).x);
      const y = Number(safeObject(candidate).y);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        return { x: Math.round(x), y: Math.round(y) };
      }
    }
    return null;
  }

  function getLocalMovementPlayerGridPosition() {
    const candidates = [getInputControllerPosition(), getTacticalPlayerPosition()];
    for (const candidate of candidates) {
      const x = Number(safeObject(candidate).x);
      const y = Number(safeObject(candidate).y);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        return { x: Math.round(x), y: Math.round(y) };
      }
    }
    return null;
  }

  function normalizeGridPositionCandidate(candidate) {
    const x = Number(safeObject(candidate).x);
    const y = Number(safeObject(candidate).y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x: Math.round(x), y: Math.round(y) };
  }

  function resolveTargetGridPosition(targetId) {
    const key = normalizeId(targetId);
    if (!key) return null;
    if (key.includes("trap") || key.includes("gas_trap") || key.includes("poison_trap")) {
      return findTrapGridPosition(key);
    }
    const mapTarget = findMapInteractableById(state.fullNormalizedMap, key)
      || findMapInteractableById(state.normalizedMap, key);
    const mapPosition = normalizeGridPositionCandidate(mapTarget);
    if (mapPosition) return mapPosition;
    return normalizeGridPositionCandidate(state.environmentObjects[key]);
  }

  function resolveClientPlayerGridPositionForPayload(targetId, options = {}) {
    const explicit = normalizeGridPositionCandidate(
      safeObject(options).client_player_position
      || safeObject(options).clientPosition
      || safeObject(options).playerPosition
    );
    if (explicit) return explicit;
    const source = normalizeId(safeObject(options).source);
    const shouldPreferTargetNearest = [
      "interaction",
      "trap_awareness",
      "trap_trigger",
      "trigger_zone",
    ].includes(source);
    const target = resolveTargetGridPosition(targetId);
    const tactical = normalizeGridPositionCandidate(getTacticalPlayerPosition());
    const input = normalizeGridPositionCandidate(getInputControllerPosition());
    if (shouldPreferTargetNearest && target && tactical && input) {
      return chebyshevDistance(input, target) <= chebyshevDistance(tactical, target)
        ? input
        : tactical;
    }
    if (shouldPreferTargetNearest && target && input) return input;
    if (shouldPreferTargetNearest && target && tactical) return tactical;
    return getClientPlayerGridPosition();
  }

  function syncLocalPlayerProjectionState(source = "client_local") {
    const coord = getClientPlayerGridPosition();
    if (!coord) return null;
    const previous = safeObject(state.partyStatus.player);
    state.partyStatus = {
      ...safeObject(state.partyStatus),
      player: {
        ...previous,
        x: coord.x,
        y: coord.y,
        name: previous.name || "玩家",
        faction: previous.faction || "player",
        _projection_source: source,
      },
    };
    return coord;
  }

  function getCameraFollowTarget() {
    if (!window.ControlledAgentTacticalMap || typeof window.ControlledAgentTacticalMap.getCameraFollowTarget !== "function") {
      return null;
    }
    return safeObject(window.ControlledAgentTacticalMap.getCameraFollowTarget());
  }

  function ensureMapDebugChip() {
    if (!QA_MAP_DEBUG) return null;
    if (state.mapDebugChip && document.body.contains(state.mapDebugChip)) return state.mapDebugChip;
    const chip = document.createElement("div");
    chip.id = "qa-map-debug-chip";
    chip.style.position = "fixed";
    chip.style.left = "12px";
    chip.style.bottom = "12px";
    chip.style.zIndex = "9999";
    chip.style.maxWidth = "420px";
    chip.style.maxHeight = "46vh";
    chip.style.overflow = "auto";
    chip.style.padding = "8px 10px";
    chip.style.border = "1px solid rgba(255,198,109,0.45)";
    chip.style.borderRadius = "8px";
    chip.style.background = "rgba(10,8,14,0.82)";
    chip.style.color = "#f6d39a";
    chip.style.fontSize = "11px";
    chip.style.lineHeight = "1.35";
    chip.style.whiteSpace = "pre-wrap";
    chip.style.pointerEvents = "none";
    document.body.appendChild(chip);
    state.mapDebugChip = chip;
    return chip;
  }

  function ensureMapSourceBadge() {
    if (!IS_QA_MODE) return null;
    if (state.mapSourceBadge && document.body.contains(state.mapSourceBadge)) return state.mapSourceBadge;
    const badge = document.createElement("div");
    badge.id = "qa-map-source-badge";
    badge.className = "qa-map-source-badge is-hidden";
    badge.textContent = "mapSource=json";
    document.body.appendChild(badge);
    state.mapSourceBadge = badge;
    return badge;
  }

  function collectMapDebugSnapshot(reason = "") {
    const fullMap = safeObject(state.fullNormalizedMap);
    const mapData = safeObject(state.mapData);
    const backendPlayer = safeObject(safeObject(state.partyStatus).player);
    const snapshot = {
      reason: reason || "",
      mapLoadSource: state.mapLoadSource || "",
      mapLoadReason: state.mapLoadReason || "",
      fullNormalizedMap: {
        width: Number(fullMap.width) || 0,
        height: Number(fullMap.height) || 0,
        playerStart: safeObject(fullMap.playerStart),
      },
      mapData: {
        width: Number(mapData.width) || 0,
        height: Number(mapData.height) || 0,
        visible_rooms: safeArray(mapData.visible_rooms),
      },
      roomVisibleIds: Array.from(state.roomVisibleIds),
      backendPlayer: {
        x: Number(backendPlayer.x),
        y: Number(backendPlayer.y),
      },
      inputControllerPlayer: getInputControllerPosition(),
      tacticalPlayer: getTacticalPlayerPosition(),
      cameraFollowTarget: getCameraFollowTarget(),
      lastFetch: safeObject(state.mapDebugLastFetch),
      lastMapLoad: safeObject(state.mapDebugLastMapLoad),
      barkBuild: BARK_BUILD_ID,
      hasCompanionBarkRenderer: !!(
        window.ControlledAgentHudRenderers
        && typeof window.ControlledAgentHudRenderers.dispatchCompanionBarks === "function"
      ),
      hasExtractSpeechBarks: typeof extractSpeechBarks === "function",
    };
    return snapshot;
  }

  function updateMapDebug(reason = "") {
    if (!QA_MAP_DEBUG) return;
    const snapshot = collectMapDebugSnapshot(reason);
    state.mapDebugSnapshot = snapshot;
    window.__ControlledAgent_QA_STATE__ = {
      ...safeObject(window.__ControlledAgent_QA_STATE__),
      barkBuild: snapshot.barkBuild,
      hasCompanionBarkRenderer: snapshot.hasCompanionBarkRenderer,
      hasExtractSpeechBarks: snapshot.hasExtractSpeechBarks,
      mapDebugSnapshot: snapshot,
    };
    const chip = ensureMapDebugChip();
    if (chip) {
      chip.textContent = [
        "[qa_map_debug]",
        "barkBuild=" + snapshot.barkBuild,
        "hasCompanionBarkRenderer=" + String(snapshot.hasCompanionBarkRenderer),
        "hasExtractSpeechBarks=" + String(snapshot.hasExtractSpeechBarks),
        "reason=" + (snapshot.reason || "n/a"),
        "mapSource=" + snapshot.mapLoadSource + " reason=" + snapshot.mapLoadReason,
        "fullMap=" + snapshot.fullNormalizedMap.width + "x" + snapshot.fullNormalizedMap.height
          + " start=(" + Number(snapshot.fullNormalizedMap.playerStart.x || 0) + "," + Number(snapshot.fullNormalizedMap.playerStart.y || 0) + ")",
        "mapData=" + snapshot.mapData.width + "x" + snapshot.mapData.height
          + " visibleRooms=" + JSON.stringify(snapshot.mapData.visible_rooms || []),
        "roomVisibleIds=" + JSON.stringify(snapshot.roomVisibleIds || []),
        "backendPlayer=(" + snapshot.backendPlayer.x + "," + snapshot.backendPlayer.y + ")",
        "inputPlayer=" + JSON.stringify(snapshot.inputControllerPlayer || null),
        "tacticalPlayer=" + JSON.stringify(snapshot.tacticalPlayer || null),
        "cameraFollow=" + JSON.stringify(snapshot.cameraFollowTarget || null),
        "lastMapLoad=" + JSON.stringify(snapshot.lastMapLoad || null),
        "lastFetch=" + JSON.stringify(snapshot.lastFetch || null),
      ].join("\n");
    }
    if (typeof console !== "undefined" && typeof console.info === "function") {
      console.info("[qa_map_debug]", snapshot);
    }
  }

  function buildShowcaseSnapshot(extra = {}) {
    const data = safeObject(extra);
    const gameState = safeObject(data.game_state || {});
    return {
      ...data,
      app_state: {
        partyStatus: state.partyStatus,
        environmentObjects: state.environmentObjects,
        playerInventory: state.playerInventory,
        combatState: state.combatState,
        mapData: state.mapData,
        normalizedMap: state.normalizedMap,
        roomVisibleIds: Array.from(state.roomVisibleIds),
      },
      roomVisibleIds: Array.from(state.roomVisibleIds),
      party_status: data.party_status || state.partyStatus,
      environment_objects: data.environment_objects || state.environmentObjects,
      player_inventory: data.player_inventory || state.playerInventory,
      combat_state: data.combat_state || state.combatState,
      map_data: data.map_data || state.mapData,
      flags: data.flags || gameState.flags || safeObject(state.worldFlags) || safeObject(state.lastShowcaseSnapshot).flags || {},
      actor_runtime_state: data.actor_runtime_state || gameState.actor_runtime_state || {},
      demo_cleared: data.demo_cleared === true || gameState.demo_cleared === true,
    };
  }

  function updateWorldStateDiff(previousSnapshot, nextSnapshot, options = {}) {
    if (!window.ControlledAgentStateDiffRenderer || typeof window.ControlledAgentStateDiffRenderer.update !== "function") return [];
    const diffs = window.ControlledAgentStateDiffRenderer.update(previousSnapshot || {}, nextSnapshot || {}, options);
    state.lastShowcaseSnapshot = nextSnapshot || buildShowcaseSnapshot();
    return diffs;
  }

  function recordShowcaseBaseline(extra = {}) {
    state.lastShowcaseSnapshot = buildShowcaseSnapshot(extra);
    if (window.ControlledAgentStateDiffRenderer && typeof window.ControlledAgentStateDiffRenderer.ensurePanel === "function") {
      window.ControlledAgentStateDiffRenderer.ensurePanel();
    }
    return state.lastShowcaseSnapshot;
  }

  async function fetchShowcaseStateSnapshot() {
    if (!QA_SHOWCASE && !QA_MAP_DEBUG) return null;
    try {
      const url = STATE_URL
        + "?session_id=" + encodeURIComponent(getSessionId())
        + "&map_id=" + encodeURIComponent(MAP_ID);
      const response = await fetchWithTimeout(url, {}, BACKEND_REQUEST_TIMEOUT_MS);
      if (!response.ok) return null;
      return safeObject(await response.json());
    } catch (_error) {
      return null;
    }
  }

  function boolish(value) {
    if (typeof value === "boolean") return value;
    const normalized = normalizeId(value);
    return normalized === "true" || normalized === "yes" || normalized === "1";
  }

  function roomAtPosition(rooms, x, y) {
    const rx = Number(x);
    const ry = Number(y);
    if (!Number.isFinite(rx) || !Number.isFinite(ry)) return null;
    return safeArray(rooms).find((room) => {
      const r = safeObject(room);
      const sx = Number(r.x) || 0;
      const sy = Number(r.y) || 0;
      const sw = Math.max(1, Number(r.w) || 1);
      const sh = Math.max(1, Number(r.h) || 1);
      return rx >= sx && rx < sx + sw && ry >= sy && ry < sy + sh;
    }) || null;
  }

  function findMapInteractableById(map, targetId) {
    const key = normalizeId(targetId);
    return [
      ...safeArray(safeObject(map).interactables),
      ...safeArray(safeObject(map).triggers),
    ].find((item) => {
      const record = safeObject(item);
      const data = safeObject(record.data);
      return normalizeId(record.id) === key
        || normalizeId(record.source_id) === key
        || normalizeId(record.alias_id) === key
        || normalizeId(data.alias_id) === key;
    }) || null;
  }

  function getMapInteractableMetadata(targetId) {
    const full = findMapInteractableById(state.fullNormalizedMap, targetId);
    if (full) return full;
    return findMapInteractableById(state.normalizedMap, targetId);
  }

  function normalizeDoorProjectionRecord(record) {
    const item = {
      ...safeObject(record),
      data: { ...safeObject(safeObject(record).data) },
    };
    const id = normalizeId(item.id || item.alias_id || item.source_id || item.data.alias_id);
    if (id === "door_a_to_b") {
      item.w = 3;
      item.width = 3;
      item.h = Math.max(1, Number(item.h ?? item.height ?? item.data.h ?? item.data.height) || 1);
      item.data.w = 3;
      item.data.width = 3;
      item.data.h = item.h;
      item.data.height = item.h;
      const ix = Math.round(Number(item.x) || 0);
      const iy = Math.round(Number(item.y) || 0);
      const ih = Math.max(1, Math.round(Number(item.h) || 1));
      const cells = [];
      for (let dx = 0; dx < 3; dx += 1) {
        cells.push({ x: ix + dx, y: iy - 1 });
        cells.push({ x: ix + dx, y: iy + ih });
      }
      item.interaction_cells = cells;
      item.data.interaction_cells = cells;
    }
    return item;
  }

  function isDoorOpened(targetId, record = {}) {
    const id = normalizeId(targetId || safeObject(record).id);
    const data = safeObject(safeObject(record).data);
    if (state.openedLocalDoorIds.has(id)) return true;
    const explicit = safeObject(record).is_open ?? safeObject(record).open ?? safeObject(record).opened
      ?? data.is_open ?? data.open ?? data.opened;
    if (typeof explicit === "boolean") return explicit;
    const explicitText = normalizeId(explicit);
    if (["true", "yes", "open", "opened"].includes(explicitText)) return true;
    const status = normalizeId(safeObject(record).status || data.status);
    return ["open", "opened"].includes(status);
  }

  function describeDoorHint(interactable, mapRecord) {
    const target = safeObject(interactable);
    const record = safeObject(mapRecord);
    const data = safeObject(record.data);
    const id = normalizeId(target.id || record.id);
    const fromRoom = String(record.connects_from || data.connects_from || "").trim();
    const toRoom = String(record.connects_to || data.connects_to || "").trim();
    const fromLabel = ROOM_LABELS[fromRoom] || fromRoom;
    const toLabel = ROOM_LABELS[toRoom] || toRoom;
    const detectDc = Number(data.detect_dc) || 0;
    const keyRequired = String(data.key_required || "").trim();
    const lockpickDc = Number(data.lockpick_dc) || 0;
    if (isDoorOpened(id, target) || isDoorOpened(id, record)) {
      if (fromLabel && toLabel) return "通道已开启：" + fromLabel + " ↔ " + toLabel;
      return "门已开启";
    }

    if (id === "door_b_to_c" && detectDc > 0) {
      return "E 检查暗门：DC " + detectDc;
    }
    if (id === "door_b_to_d" && keyRequired && lockpickDc > 0) {
      return "E 打开实验室门：需要 " + keyRequired + " 或 DC " + lockpickDc + " 撬锁";
    }
    if (id === "door_a_to_b" && fromLabel && toLabel) {
      return "E 打开门：" + fromLabel + "通往" + toLabel;
    }
    if ((id === "heavy_oak_door_1" || id === "exit_door") && keyRequired) {
      return "E 打开出口门：需要 " + keyRequired;
    }
    const label = target.label || target.name || data.name || target.id || "门";
    return "E 打开门：" + label;
  }

  function formatInteractionHint(interactable) {
    const target = safeObject(interactable);
    const idRaw = String(target.id || "").trim();
    const id = normalizeId(idRaw);
    if (!idRaw) return "";
    const type = normalizeId(target.type || safeObject(target.data).type);
    const mapRecord = safeObject(getMapInteractableMetadata(idRaw));
    const data = safeObject(mapRecord.data);
    const targetName = String(target.label || target.name || data.name || idRaw || "未知目标");
    let coreText = "E 交互：" + targetName;

    if (type === "door" || id.includes("door")) {
      coreText = describeDoorHint(target, mapRecord);
    } else if (id === "hazard_diary" || type === "readable") {
      coreText = "E 阅读：" + targetName;
    } else if (id === "gatekeeper" || type === "npc" || type === "character") {
      coreText = "E 对话：" + targetName;
    } else if (type === "chest" || type === "loot" || type === "container" || id === "study_chest" || id === "chest_1") {
      coreText = "E 搜刮：" + targetName;
    } else if (type === "trap" || id.includes("trap")) {
      coreText = "可疑机关：" + targetName + " · 让侦察员解除";
    }

    return coreText + " [" + idRaw + "]";
  }

  function resetRoomVisibility(map) {
    const rooms = safeArray(safeObject(map).rooms);
    state.roomVisibleIds = new Set();
    if (!rooms.length) return;
    const roomIds = rooms.map((room) => String(safeObject(room).id || "").trim()).filter(Boolean);
    if (roomIds.includes(ROOM_A)) {
      state.roomVisibleIds.add(ROOM_A);
    } else {
      roomIds.forEach((roomId) => state.roomVisibleIds.add(roomId));
    }
  }

  function revealRoom(roomId) {
    const key = String(roomId || "").trim();
    if (!key) return false;
    if (state.roomVisibleIds.has(key)) {
      if (key === ROOM_C) hardClearAct3TrapBarks("room_c_reveal");
      return false;
    }
    state.roomVisibleIds.add(key);
    if (key === ROOM_C) hardClearAct3TrapBarks("room_c_reveal");
    return true;
  }

  function updateExplorationActProgress() {
    if (!window.ControlledAgentHudRenderers || typeof window.ControlledAgentHudRenderers.updateActProgress !== "function") return;
    const flags = safeObject(state.worldFlags);
    if (
      state.roomVisibleIds.has(ROOM_D)
      || flags.act4_boss_room_entered === true
      || flags.act4_gatekeeper_confrontation_started === true
      || flags.act4_heavy_iron_key_obtained === true
      || flags.act4_final_exit_opened === true
    ) {
      window.ControlledAgentHudRenderers.updateActProgress(
        4,
        "Gatekeeper 攥着沉重铁钥匙，身后的毒气罐低声翻滚。"
      );
      return;
    }
    if (
      state.roomVisibleIds.has(ROOM_C)
      || flags.act3_secret_study_entered === true
      || flags.act3_secret_study_discovered === true
      || flags.act3_diary_read === true
    ) {
      window.ControlledAgentHudRenderers.updateActProgress(
        3,
        "墙后露出一间狭窄书房，日记与残页把 Gatekeeper、钥匙和毒气真相串在一起。"
      );
      return;
    }
    if (state.roomVisibleIds.has(ROOM_B)) {
      window.ControlledAgentHudRenderers.updateActProgress(
        2,
        "侦察员在前方停下脚步。空气里有甜腻的腐臭味，墙缝间隐约传来气压声。"
      );
      return;
    }
    window.ControlledAgentHudRenderers.updateActProgress(1);
  }

  function discoverTrap(trapId) {
    const key = normalizeId(trapId);
    if (!key) return false;
    if (state.discoveredTrapIds.has(key)) return false;
    state.discoveredTrapIds.add(key);
    return true;
  }

  function trapAliases(trapId) {
    const key = normalizeId(trapId);
    const aliases = new Set([key]);
    if (!key || key.includes("gas_trap") || key.includes("poison_trap")) {
      aliases.add("gas_trap_1");
      aliases.add("poison_trap_1");
      aliases.add("poison_trap_2");
    }
    return Array.from(aliases).filter(Boolean);
  }

  function markBackendTrapSignal(trapId, status = "revealed") {
    const normalizedStatus = normalizeId(status) || "revealed";
    trapAliases(trapId || "gas_trap_1").forEach((id) => state.backendRevealedTrapIds.add(id));
    const targetId = normalizeId(trapId || "gas_trap_1");
    const targetKeys = trapAliases(targetId);
    targetKeys.forEach((id) => {
      const existing = safeObject(state.environmentObjects[id]);
      if (!Object.keys(existing).length) return;
      state.environmentObjects[id] = {
        ...existing,
        status: normalizedStatus,
        is_hidden: false,
        is_revealed: true,
        discovered: true,
      };
    });
  }

  function valveAliases(valveId) {
    const key = normalizeId(valveId);
    const aliases = new Set([key || "poison_valve", "poison_valve", "potion_tank"]);
    return Array.from(aliases).filter(Boolean);
  }

  function markPoisonValveSignal(valveId, status = "triggered") {
    const normalizedStatus = normalizeId(status) || "triggered";
    valveAliases(valveId).forEach((id) => {
      const existing = safeObject(state.environmentObjects[id]);
      if (!Object.keys(existing).length) return;
      state.environmentObjects[id] = {
        ...existing,
        status: normalizedStatus,
        is_hidden: false,
        is_revealed: true,
        discovered: true,
      };
    });
    if (window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.refreshMapOnly === "function") {
      window.ControlledAgentTacticalMap.refreshMapOnly(state.mapData, state.environmentObjects);
    }
  }

  function isAct4LabObject(id, entity = {}) {
    const key = normalizeId(id);
    const type = normalizeId(safeObject(entity).type || safeObject(entity).kind || safeObject(entity).entity_type);
    return key === "poison_valve"
      || key === "potion_tank"
      || key.includes("poison_valve")
      || key.includes("potion_tank")
      || type === "poison_valve"
      || type === "potion_tank";
  }

  function shouldRevealAct4LabObjects() {
    const flags = safeObject(state.worldFlags);
    return state.roomVisibleIds.has(ROOM_D)
      || flags.act4_boss_room_entered === true
      || flags.act4_gatekeeper_confrontation_started === true
      || flags.act4_poison_valve_triggered === true
      || flags.act4_lab_poison_leak === true
      || flags.act4_poison_valve_disabled === true;
  }

  function refreshWorldFlags(data = {}) {
    const payload = safeObject(data);
    const gameState = safeObject(payload.game_state || payload.gameState || payload.state);
    const nestedState = safeObject(payload.state);
    state.worldFlags = {
      ...safeObject(state.worldFlags),
      ...safeObject(gameState.flags),
      ...safeObject(nestedState.flags),
      ...safeObject(payload.flags),
    };
    const flags = safeObject(state.worldFlags);
    const journalBlob = [
      ...safeArray(payload.journal_events),
      ...safeArray(gameState.journal_events),
      ...safeArray(nestedState.journal_events),
    ].join("\n");
    const visibleRooms = [
      ...safeArray(payload.visibleRooms),
      ...safeArray(payload.visible_rooms),
      ...safeArray(safeObject(payload.map_data).visibleRooms),
      ...safeArray(safeObject(payload.map_data).visible_rooms),
      ...safeArray(safeObject(gameState.map_data).visibleRooms),
      ...safeArray(safeObject(gameState.map_data).visible_rooms),
      ...safeArray(safeObject(nestedState.map_data).visibleRooms),
      ...safeArray(safeObject(nestedState.map_data).visible_rooms),
    ].map((roomId) => normalizeId(roomId));
    const hasSecretStudyRevealSignal = Boolean(
      visibleRooms.includes(ROOM_C)
      || flags.act3_secret_study_entered === true
      || flags.act3_secret_study_discovered === true
      || flags.act2_secret_study_route_unlocked === true
      || flags.hazard_lab_secret_study_discovered === true
      || flags.hazard_lab_secret_study_entered === true
      || /\[秘密书房\]\s*cracked_wall\s*->\s*room_c_secret_study/i.test(journalBlob)
    );
    if (hasSecretStudyRevealSignal) {
      discoverSecretDoor("door_b_to_c");
      revealRoomByDoorTarget("door_b_to_c");
      refreshVisibilityProjection();
    }
    if (flags.act4_boss_room_entered === true || flags.act4_gatekeeper_confrontation_started === true) {
      revealRoom(ROOM_D);
    }
    if (flags.act4_poison_valve_triggered === true || flags.act4_lab_poison_leak === true) {
      markPoisonValveSignal("poison_valve", "triggered");
    }
    if (flags.act4_poison_valve_disabled === true) {
      markPoisonValveSignal("poison_valve", "disabled");
    }
    updateExplorationActProgress();
    hardClearAct3TrapBarksIfNeeded(payload, "state_transition");
  }

  function hasTrapRevealFlag() {
    const flags = safeObject(state.worldFlags);
    return Boolean(
      flags.hazard_lab_poison_trap_revealed === true
      || flags.hazard_lab_poison_trap_disarmed === true
      || flags.hazard_lab_poison_trap_triggered === true
    );
  }

  function isBackendTrapVisible(trapId, entity = {}) {
    const id = normalizeId(trapId);
    const data = safeObject(entity);
    const status = normalizeId(data.status || data.state || "");
    if (["revealed", "disabled", "disarmed", "triggered"].includes(status)) return true;
    if (data.is_revealed === true || data.discovered === true || data.is_discovered === true) return true;
    if (state.backendRevealedTrapIds.has(id) || state.backendRevealedTrapIds.has(normalizeId(data.alias_id))) return true;
    if (hasTrapRevealFlag() && (id === "gas_trap_1" || id.includes("poison_trap") || id.includes("gas_trap"))) return true;
    return false;
  }

  function resolvedTrapVisualStatus(trapId, entity = {}) {
    const status = normalizeId(safeObject(entity).status || safeObject(entity).state || "");
    if (["disabled", "disarmed"].includes(status)) return "disabled";
    if (["triggered", "active", "burst"].includes(status)) return "triggered";
    const flags = safeObject(state.worldFlags);
    if (flags.hazard_lab_poison_trap_disarmed === true) return "disabled";
    if (flags.hazard_lab_poison_trap_triggered === true) return "triggered";
    if (isBackendTrapVisible(trapId, entity)) return "revealed";
    return "hidden";
  }

  function normalizeTrapTriggerTarget(trigger = {}) {
    const record = safeObject(trigger);
    const data = safeObject(record.data);
    const rawId = normalizeId(record.alias_id || data.alias_id || record.id || data.id);
    if (!rawId) return "";
    if (rawId.includes("gas_trap") || rawId.includes("poison_trap") || rawId.includes("trap")) return "gas_trap_1";
    return rawId;
  }

  function chebyshevDistance(a, b) {
    if (!a || !b) return Number.POSITIVE_INFINITY;
    return Math.max(
      Math.abs(Math.round(Number(a.x)) - Math.round(Number(b.x))),
      Math.abs(Math.round(Number(a.y)) - Math.round(Number(b.y)))
    );
  }

  function findTrapGridPosition(trapId = "gas_trap_1") {
    const aliases = trapAliases(trapId || "gas_trap_1");
    const aliasSet = new Set(aliases);
    const maps = [state.fullNormalizedMap, state.normalizedMap];
    for (const map of maps) {
      const match = findMapInteractableById(map, trapId)
        || [
          ...safeArray(safeObject(map).triggers),
          ...safeArray(safeObject(map).interactables),
        ].find((item) => {
          const record = safeObject(item);
          const data = safeObject(record.data);
          return aliasSet.has(normalizeId(record.id))
            || aliasSet.has(normalizeId(record.alias_id))
            || aliasSet.has(normalizeId(record.source_id))
            || aliasSet.has(normalizeId(data.alias_id))
            || aliasSet.has(normalizeId(data.source_id));
        });
      if (!match) continue;
      const x = Number(match.x);
      const y = Number(match.y);
      if (Number.isFinite(x) && Number.isFinite(y)) return { x, y };
    }
    for (const alias of aliases) {
      const env = safeObject(state.environmentObjects[alias]);
      const x = Number(env.x);
      const y = Number(env.y);
      if (Number.isFinite(x) && Number.isFinite(y)) return { x, y };
    }
    return { x: 4, y: 6 };
  }

  function shouldQueueTrapAwareness(trapId = "gas_trap_1") {
    const flags = safeObject(state.worldFlags);
    if (flags.act2_scout_perception_checked === true) return false;
    if (flags.hazard_lab_poison_trap_disarmed === true || flags.hazard_lab_poison_trap_triggered === true) return false;
    const door = safeObject(state.environmentObjects.door_a_to_b || state.environmentObjects.ab_door);
    const corridorVisible = state.roomVisibleIds.has(ROOM_B)
      || safeArray(safeObject(state.mapData).visible_rooms).includes(ROOM_B)
      || flags.act2_corridor_entered === true
      || door.is_open === true
      || ["open", "opened"].includes(normalizeId(door.status || door.state));
    if (!corridorVisible) return false;
    const trapState = safeObject(state.environmentObjects[trapId]);
    const trapStatus = resolvedTrapVisualStatus(trapId, trapState);
    if (trapStatus === "disabled" || trapStatus === "triggered" || trapStatus === "revealed") return false;
    const player = getLocalMovementPlayerGridPosition();
    const trap = findTrapGridPosition(trapId);
    return chebyshevDistance(player, trap) <= 3;
  }

  function shouldTriggerTrapMechanic(trapId = "gas_trap_1") {
    const id = normalizeId(trapId || "gas_trap_1");
    const aliases = trapAliases(id);
    const flags = safeObject(state.worldFlags);
    if (flags.hazard_lab_poison_trap_disarmed === true) return false;
    if (flags.hazard_lab_poison_trap_triggered === true) return false;
    const candidates = aliases
      .map((alias) => safeObject(state.environmentObjects[alias]))
      .filter((record) => Object.keys(record).length > 0);
    return !candidates.some((record) => {
      const status = normalizeId(record.status || record.state || "");
      return ["disabled", "disarmed", "triggered", "active", "burst"].includes(status);
    });
  }

  function discoverSecretDoor(doorId) {
    const key = normalizeId(doorId);
    if (!key) return false;
    if (state.discoveredSecretDoorIds.has(key)) return false;
    state.discoveredSecretDoorIds.add(key);
    if (key === "door_b_to_c" && state.roomVisibleIds.has(ROOM_C)) {
      hardClearAct3TrapBarks("room_c_reveal");
    }
    return true;
  }

  function resolveRecordRoomId(record, rooms) {
    const entity = safeObject(record);
    const data = safeObject(entity.data);
    const explicit = String(entity.room_id || data.room_id || data.roomId || "").trim();
    if (explicit) return explicit;
    const room = roomAtPosition(rooms, entity.x, entity.y);
    return room ? String(room.id || "") : "";
  }

  function deriveVisibleNormalizedMap(baseMap) {
    const map = safeObject(baseMap);
    const rooms = safeArray(map.rooms).map((room) => ({ ...safeObject(room) }));
    const roomAware = rooms.length > 0;
    const visibleRoomIds = state.roomVisibleIds;

    const isRoomVisible = (roomId) => {
      if (!roomAware) return true;
      const key = String(roomId || "").trim();
      if (!key) return false;
      return visibleRoomIds.has(key);
    };

    const visibleInteractables = safeArray(map.interactables)
      .map((item) => normalizeDoorProjectionRecord(item))
      .filter((item) => {
        const type = normalizeId(item.type || safeObject(item.data).type);
        const id = normalizeId(item.id);
        const roomId = resolveRecordRoomId(item, rooms);
        const fromRoom = String(item.connects_from || safeObject(item.data).connects_from || "").trim();
        const toRoom = String(item.connects_to || safeObject(item.data).connects_to || "").trim();
        const isSecretDoor = type === "door" && boolish(safeObject(item.data).is_secret);
        if (isSecretDoor && !state.discoveredSecretDoorIds.has(id)) {
          return false;
        }
        if (type === "door" && (fromRoom || toRoom)) {
          return isRoomVisible(fromRoom) || isRoomVisible(toRoom);
        }
        return isRoomVisible(roomId);
      })
      .map((item) => {
        const id = normalizeId(item.id);
        const type = normalizeId(item.type || safeObject(item.data).type);
        if (type === "trap" || id.includes("trap")) {
          const discovered = isBackendTrapVisible(id, {
            ...safeObject(item.data),
            ...item,
          });
          const status = resolvedTrapVisualStatus(id, {
            ...safeObject(item.data),
            ...item,
          });
          item.discovered = discovered;
          item.is_revealed = discovered;
          item.is_hidden = !discovered;
          item.data.discovered = discovered;
          item.data.is_revealed = discovered;
          item.data.is_hidden = !discovered;
          item.status = discovered ? status : "hidden";
        }
        return item;
      });

    const visibleTriggers = safeArray(map.triggers)
      .map((item) => ({ ...safeObject(item), data: { ...safeObject(safeObject(item).data) } }))
      .filter((item) => {
        const roomId = resolveRecordRoomId(item, rooms);
        return isRoomVisible(roomId);
      });

    const visibleSpawns = safeArray(map.spawns)
      .map((item) => ({ ...safeObject(item), data: { ...safeObject(safeObject(item).data) } }))
      .filter((item) => {
        const roomId = resolveRecordRoomId(item, rooms);
        return isRoomVisible(roomId);
      });

    return {
      ...map,
      rooms,
      visibleRooms: Array.from(visibleRoomIds),
      triggers: visibleTriggers,
      interactables: visibleInteractables,
      spawns: visibleSpawns,
    };
  }

  function mergeVisualMapData(runtimeMapData, normalizedMap) {
    const runtime = safeObject(runtimeMapData);
    const visual = mapDataFromNormalized(normalizedMap);
    if (!Object.keys(visual).length) return runtime;
    const useVisualAsTruth = normalizeId(state.mapLoadSource) === "json";
    const visibleProjection = deriveVisibleNormalizedMap(normalizedMap);
    const merged = useVisualAsTruth ? { ...runtime, ...visual } : { ...visual, ...runtime };
    if (useVisualAsTruth) {
      merged.id = visual.id;
      merged.width = visual.width;
      merged.height = visual.height;
      merged.grid = visual.grid;
      merged.collision = visual.collision;
      merged.los_blockers = visual.los_blockers;
      merged.ground_types = visual.ground_types;
      merged.rooms = visual.rooms;
      merged.visible_rooms = visual.visible_rooms;
      merged.obstacles = visual.obstacles;
      return attachVisibleMapObjectsToMapData(merged, visibleProjection);
    }
    const hasGrid = safeArray(runtime.grid).length > 0;
    if (!hasGrid) merged.grid = visual.grid;
    if (!safeArray(runtime.collision).length) merged.collision = visual.collision;
    if (!safeArray(runtime.los_blockers).length) merged.los_blockers = visual.los_blockers;
    if (!safeArray(runtime.ground_types).length) merged.ground_types = visual.ground_types;
    if (!runtime.width) merged.width = visual.width;
    if (!runtime.height) merged.height = visual.height;
    if (!runtime.id) merged.id = visual.id;
    if (!safeArray(merged.interactables).length && safeArray(visibleProjection.interactables).length) {
      merged.interactables = visibleProjection.interactables;
    }
    if (!safeArray(merged.triggers).length && safeArray(visibleProjection.triggers).length) {
      merged.triggers = visibleProjection.triggers;
    }
    if (!safeArray(merged.spawns).length && safeArray(visibleProjection.spawns).length) {
      merged.spawns = visibleProjection.spawns;
    }
    return merged;
  }

  function updateMapSourceStatus(source, reason) {
    state.mapLoadSource = source || "fixture";
    state.mapLoadReason = reason || "";
    if (els.mapContainer) {
      els.mapContainer.dataset.mapSource = state.mapLoadSource;
      if (state.mapLoadReason) {
        els.mapContainer.dataset.mapFallbackReason = state.mapLoadReason;
      } else {
        delete els.mapContainer.dataset.mapFallbackReason;
      }
    }
    const badge = ensureMapSourceBadge();
    if (badge) {
      const isJson = normalizeId(state.mapLoadSource) === "json";
      badge.classList.toggle("is-hidden", !isJson);
      badge.textContent = "mapSource=" + state.mapLoadSource + (state.mapLoadReason ? " (" + state.mapLoadReason + ")" : "");
    }
  }

  function applyNormalizedMap(normalizedMap, meta = {}) {
    const map = safeObject(normalizedMap);
    if (!Object.keys(map).length) return;
    state.fullNormalizedMap = map;
    state.trapSenseEnabled = false;
    state.act1PerceptionResolved = false;
    state.discoveredTrapIds = new Set();
    state.backendRevealedTrapIds = new Set();
    state.discoveredSecretDoorIds = new Set();
    state.openedLocalDoorIds = new Set();
    state.worldFlags = {};
    resetRoomVisibility(map);
    state.normalizedMap = deriveVisibleNormalizedMap(map);
    state.mapData = attachVisibleMapObjectsToMapData(
      mapDataFromNormalized({ ...map, visibleRooms: Array.from(state.roomVisibleIds) }),
      state.normalizedMap
    );
    updateExplorationActProgress();
    updateMapSourceStatus(meta.source || "fixture", meta.reason || "");
    if (window.ControlledAgentInputController && typeof window.ControlledAgentInputController.setMap === "function") {
      window.ControlledAgentInputController.setMap(state.normalizedMap);
      const playerStart = safeObject(map.playerStart);
      if (typeof window.ControlledAgentInputController.setPlayerPosition === "function") {
        window.ControlledAgentInputController.setPlayerPosition(
          Number(playerStart.x) || 0,
          Number(playerStart.y) || 0,
        );
      }
    }
    if (window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.setTrapSenseMode === "function") {
      window.ControlledAgentTacticalMap.setTrapSenseMode(state.trapSenseEnabled === true);
    }
    if (window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.resetLocalPartyTrail === "function") {
      window.ControlledAgentTacticalMap.resetLocalPartyTrail();
    }
    updateMapDebug("applyNormalizedMap");
  }

  function refreshVisibilityProjection() {
    const fullMap = safeObject(state.fullNormalizedMap);
    if (!Object.keys(fullMap).length) return;
    state.normalizedMap = deriveVisibleNormalizedMap(fullMap);
    const visualMapData = mapDataFromNormalized({
      ...fullMap,
      visibleRooms: Array.from(state.roomVisibleIds),
    });
    state.mapData = {
      ...safeObject(state.mapData),
      ...attachVisibleMapObjectsToMapData(visualMapData, state.normalizedMap),
    };
    if (window.ControlledAgentInputController && typeof window.ControlledAgentInputController.setMap === "function") {
      window.ControlledAgentInputController.setMap(state.normalizedMap);
    }
    updateExplorationActProgress();
    hardClearAct3TrapBarksIfNeeded({ visibleRooms: Array.from(state.roomVisibleIds) }, "visibility_projection");
  }

  function revealRoomByDoorTarget(targetId) {
    const key = normalizeId(targetId);
    if (!key) return false;
    if (key === "door_a_to_b") return revealRoom(ROOM_B);
    if (key === "door_b_to_d") return revealRoom(ROOM_D);
    if (key === "door_b_to_c") return revealRoom(ROOM_C);
    if (key === "exit_door" || key === "heavy_oak_door_1") return revealRoom(ROOM_EXIT);
    return false;
  }

  function mergePartyStatusResponse(previous, incoming) {
    const prev = safeObject(previous);
    const next = safeObject(incoming);
    if (!Object.keys(next).length) return prev;
    const merged = { ...prev };
    Object.entries(next).forEach(([id, data]) => {
      const key = String(id || "").trim();
      if (!key) return;
      const previousRecord = safeObject(prev[key]);
      const incomingRecord = safeObject(data);
      const mergedRecord = {
        ...safeObject(prev[key]),
        ...incomingRecord,
      };
      if (
        normalizeId(key) === "player"
        && normalizeId(previousRecord._projection_source).startsWith("client_")
      ) {
        const local = getClientPlayerGridPosition();
        if (local) {
          mergedRecord.x = local.x;
          mergedRecord.y = local.y;
          mergedRecord._projection_source = previousRecord._projection_source;
        }
      }
      if (
        FORMATION_COMPANIONS.includes(normalizeId(key))
        && normalizeId(previousRecord._projection_source) === "local_party_trail"
      ) {
        mergedRecord.x = previousRecord.x;
        mergedRecord.y = previousRecord.y;
        mergedRecord._projection_source = "local_party_trail";
      }
      merged[key] = mergedRecord;
    });
    return merged;
  }

  function handleLocalExplorationDoor(targetId) {
    const key = normalizeId(targetId);
    const isLocalDoor = key === "door_a_to_b"
      || (key === "door_b_to_c" && state.discoveredSecretDoorIds.has("door_b_to_c"));
    if (!isLocalDoor) return false;
    if (state.openedLocalDoorIds.has(key)) {
      if (window.ControlledAgentHudRenderers && typeof window.ControlledAgentHudRenderers.showToast === "function") {
        window.ControlledAgentHudRenderers.showToast("narration", "通道已经打开。", 1200);
      }
      return true;
    }

    const before = buildShowcaseSnapshot();
    state.openedLocalDoorIds.add(key);
    const revealed = revealRoomByDoorTarget(key);
    refreshVisibilityProjection();
    const visibleEnvironment = filterEnvironmentObjectsForTactical(state.environmentObjects);
    if (window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.refreshMapOnly === "function") {
      window.ControlledAgentTacticalMap.refreshMapOnly(state.mapData, visibleEnvironment);
      updateMapDebug("localDoorReveal:mapOnly");
    } else {
      renderTacticalGrid(state.partyStatus, state.environmentObjects, state.mapData);
    }
    if (window.ControlledAgentHudRenderers && typeof window.ControlledAgentHudRenderers.showToast === "function") {
      window.ControlledAgentHudRenderers.showToast(
        "narration",
        key === "door_b_to_c" ? "墙后露出一间秘密书房。" : "铁门打开，前方区域进入视野。",
        1800
      );
    }
    if (revealed) {
      updateWorldStateDiff(before, buildShowcaseSnapshot({
        journal_events: ["[探索] " + key + " opened"],
      }), { autoExpand: true });
    }
    return true;
  }

  function resolveAct1Perception() {
    if (state.act1PerceptionResolved) return;
    state.act1PerceptionResolved = true;
    if (window.ControlledAgentHudRenderers && typeof window.ControlledAgentHudRenderers.dispatchUIEvents === "function") {
      window.ControlledAgentHudRenderers.dispatchUIEvents([{
        type: "narration",
        actor: "Scout",
        text: "Scout 停下脚步观察走廊；感知结果等待后端判定。",
      }]);
    }
    if (window.ControlledAgentHudRenderers && typeof window.ControlledAgentHudRenderers.showToast === "function") {
      window.ControlledAgentHudRenderers.showToast("narration", "Scout 正在观察走廊。", 1800);
    }
    refreshVisibilityProjection();
  }

  function filterEnvironmentObjectsForTactical(environmentObjects) {
    const env = safeObject(environmentObjects);
    const fullMap = safeObject(state.fullNormalizedMap);
    const rooms = safeArray(fullMap.rooms);
    const out = {};
    const copyVisibleEntity = (id, raw, mapInteractable = null) => {
      const entity = safeObject(raw);
      if (isAct4LabObject(id, entity) && !shouldRevealAct4LabObjects()) return;
      const x = Number(entity.x);
      const y = Number(entity.y);
      if (rooms.length && (!Number.isFinite(x) || !Number.isFinite(y))) return;
      const roomIdFromMap = mapInteractable
        ? resolveRecordRoomId(mapInteractable, rooms)
        : (roomAtPosition(rooms, x, y) || {}).id;
      const mapData = safeObject(safeObject(mapInteractable).data);
      const fromRoom = String(safeObject(mapInteractable).connects_from || mapData.connects_from || "").trim();
      const toRoom = String(safeObject(mapInteractable).connects_to || mapData.connects_to || "").trim();
      const type = normalizeId(entity.type || entity.kind || safeObject(mapInteractable).type || mapData.type);
      const isDoorEntity = type === "door" || normalizeId(id).includes("door");
      const isVisibleDoorBoundary = isDoorEntity && (
        (fromRoom && state.roomVisibleIds.has(fromRoom))
        || (toRoom && state.roomVisibleIds.has(toRoom))
      );
      if (!rooms.length || isVisibleDoorBoundary || !roomIdFromMap || state.roomVisibleIds.has(String(roomIdFromMap))) {
        const copy = isDoorEntity ? normalizeDoorProjectionRecord(entity) : { ...entity };
        if (isVisibleDoorBoundary && state.openedLocalDoorIds.has(normalizeId(id))) {
          copy.is_open = true;
          copy.status = "open";
        }
        const isTrapEntity = normalizeId(entity.type || entity.kind || "").includes("trap") || normalizeId(id).includes("trap");
        if (isTrapEntity) {
          const discovered = isBackendTrapVisible(id, entity);
          const status = resolvedTrapVisualStatus(id, entity);
          copy.discovered = discovered;
          copy.is_revealed = discovered;
          copy.is_hidden = !discovered;
          copy.status = discovered ? status : "hidden";
          if (mapInteractable) {
            copy.x = Number(mapInteractable.x);
            copy.y = Number(mapInteractable.y);
            copy.w = Math.max(1, Number(mapInteractable.w || mapInteractable.width || 1));
            copy.h = Math.max(1, Number(mapInteractable.h || mapInteractable.height || 1));
            if (roomIdFromMap) copy.room_id = String(roomIdFromMap);
          }
        }
        out[id] = copy;
      }
    };

    Object.entries(env).forEach(([id, raw]) => {
      copyVisibleEntity(id, raw, findMapInteractableById(fullMap, id));
    });

    safeArray(state.normalizedMap.interactables).forEach((item) => {
      const record = safeObject(item);
      const id = normalizeId(record.alias_id || safeObject(record.data).alias_id || record.id);
      if (!id || out[id]) return;
      const type = normalizeId(record.type || safeObject(record.data).type);
      if (type !== "door" && !id.includes("door")) return;
      copyVisibleEntity(id, {
        ...safeObject(record.data),
        ...normalizeDoorProjectionRecord(record),
        id,
        type: "door",
        kind: "door",
        name: record.name || safeObject(record.data).name || id,
        x: Number(record.x),
        y: Number(record.y),
        w: Number(record.w ?? record.width) || 1,
        h: Number(record.h ?? record.height) || 1,
      }, record);
    });
    return out;
  }

  function resolveTacticalPlayerCoordinates(player) {
    const source = safeObject(player);
    const backendX = Number(source.x);
    const backendY = Number(source.y);
    const map = safeObject(state.fullNormalizedMap);
    const fullMapData = mapDataFromNormalized({
      ...map,
      visibleRooms: Array.from(state.roomVisibleIds),
    });
    const playerStart = safeObject(map.playerStart);
    const startX = Number(playerStart.x);
    const startY = Number(playerStart.y);
    const hasStart = Number.isFinite(startX) && Number.isFinite(startY);
    const inputPosition = safeObject(getInputControllerPosition());
    const localX = Number(inputPosition.x);
    const localY = Number(inputPosition.y);
    const hasBackend = isFiniteGridCoord(backendX, backendY);
    const hasLocal = isFiniteGridCoord(localX, localY);
    const backendCoord = { x: backendX, y: backendY };
    const localCoord = { x: localX, y: localY };
    const startCoord = hasStart ? { x: startX, y: startY } : null;
    const canUseBackend = hasBackend
      && isWithinMapBounds(backendCoord, fullMapData)
      && isCoordInsideVisibleRooms(backendCoord, state.normalizedMap);
    const canUseLocal = hasLocal
      && isWithinMapBounds(localCoord, fullMapData)
      && isCoordInsideVisibleRooms(localCoord, state.normalizedMap);

    const isRealMapSource = normalizeId(state.mapLoadSource) === "json";
    if (isRealMapSource && canUseLocal) {
      return { x: localX, y: localY, source: "input_local" };
    }
    if (canUseBackend) return { x: backendX, y: backendY, source: "backend" };
    if (!isRealMapSource) {
      if (hasBackend && isWithinMapBounds(backendCoord, fullMapData)) {
        return { x: backendX, y: backendY, source: "backend" };
      }
      if (canUseLocal) return { x: localX, y: localY, source: "input_local" };
      return hasStart ? { x: startX, y: startY, source: "visual_start" } : null;
    }
    if (hasStart) {
      return { x: startX, y: startY, source: "visual_start" };
    }
    if (hasBackend && isWithinMapBounds(backendCoord, fullMapData)) {
      return { x: backendX, y: backendY, source: "backend" };
    }
    return null;
  }

  function tacticalFormationMapData(mapData) {
    const provided = safeObject(mapData);
    if (Number(provided.width) > 0 && Number(provided.height) > 0) return provided;
    if (Number(safeObject(state.mapData).width) > 0 && Number(safeObject(state.mapData).height) > 0) {
      return state.mapData;
    }
    const fullMap = safeObject(state.fullNormalizedMap);
    if (Number(fullMap.width) > 0 && Number(fullMap.height) > 0) {
      return mapDataFromNormalized({ ...fullMap, visibleRooms: Array.from(state.roomVisibleIds) });
    }
    return { width: 1, height: 1, grid: [["."]], collision: [[false]], rooms: [], visible_rooms: [] };
  }

  function addOccupiedCell(occupiedCells, data) {
    const entity = safeObject(data);
    const x = Number(entity.x);
    const y = Number(entity.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    occupiedCells.add(Math.round(x) + "," + Math.round(y));
  }

  function buildFormationOccupiedCells(party, environmentObjects) {
    const occupied = new Set();
    Object.values(safeObject(party)).forEach((entry) => addOccupiedCell(occupied, entry));
    Object.values(safeObject(environmentObjects)).forEach((entry) => {
      const entity = safeObject(entry);
      if (boolish(entity.is_hidden) || boolish(entity.hidden)) return;
      addOccupiedCell(occupied, entity);
    });
    return occupied;
  }

  function firstFormationDesiredCell(playerPosition, companionId) {
    const base = safeObject(playerPosition);
    const offsets = safeArray(FORMATION_OFFSETS[companionId]);
    const offset = offsets[0] || { x: 0, y: 0 };
    return {
      x: Math.round(Number(base.x)) + Number(offset.x || 0),
      y: Math.round(Number(base.y)) + Number(offset.y || 0),
    };
  }

  function projectCompanionFormationForTactical(partyStatus, mapData, playerPosition) {
    const party = safeObject(partyStatus);
    const projected = { ...party };
    const map = tacticalFormationMapData(mapData);
    const player = safeObject(playerPosition || projected.player);
    const playerX = Number(player.x);
    const playerY = Number(player.y);
    if (!Number.isFinite(playerX) || !Number.isFinite(playerY)) return projected;
    const occupied = buildFormationOccupiedCells({
      ...projected,
      scout: hasUsableFormationCoord(projected.scout, map) ? projected.scout : {},
      analyst: hasUsableFormationCoord(projected.analyst, map) ? projected.analyst : {},
      tactician: hasUsableFormationCoord(projected.tactician, map) ? projected.tactician : {},
    }, state.environmentObjects);
    occupied.add(Math.round(playerX) + "," + Math.round(playerY));

    FORMATION_COMPANIONS.forEach((companionId) => {
      if (!Object.prototype.hasOwnProperty.call(projected, companionId)) return;
      const companion = safeObject(projected[companionId]);
      if (hasUsableFormationCoord(companion, map)) {
        occupied.add(Math.round(Number(companion.x)) + "," + Math.round(Number(companion.y)));
        return;
      }

      const offsets = safeArray(FORMATION_OFFSETS[companionId]);
      let target = null;
      for (const offset of offsets) {
        const desired = {
          x: Math.round(playerX) + Number(safeObject(offset).x || 0),
          y: Math.round(playerY) + Number(safeObject(offset).y || 0),
        };
        target = findNearestWalkableFormationCell(map, desired, occupied);
        if (target) break;
      }
      if (!target) {
        target = findNearestWalkableFormationCell(map, firstFormationDesiredCell(player, companionId), occupied);
      }
      if (!target) return;
      occupied.add(target.x + "," + target.y);
      projected[companionId] = {
        ...companion,
        x: target.x,
        y: target.y,
        _projection_source: "visual_party_formation",
      };
    });

    return projected;
  }

  function projectAct2DisarmActorApproach(partyStatus, mapData) {
    const flags = safeObject(state.worldFlags);
    const trapState = safeObject(state.environmentObjects.gas_trap_1);
    const trapStatus = resolvedTrapVisualStatus("gas_trap_1", trapState);
    let actorId = normalizeId(flags.act2_disarm_actor || (flags.act2_scout_ordered_to_disarm ? "scout" : ""));
    if (!actorId && trapStatus === "disabled") actorId = "scout";
    if (!actorId || !Object.prototype.hasOwnProperty.call(safeObject(partyStatus), actorId)) return partyStatus;
    if (!(
      flags.act2_disarm_attempted === true
      || flags.act2_gas_trap_disarmed === true
      || flags.act2_gas_trap_triggered === true
      || flags.hazard_lab_poison_trap_disarmed === true
      || flags.hazard_lab_poison_trap_triggered === true
      || trapStatus === "disabled"
    )) {
      return partyStatus;
    }
    const trap = findMapInteractableById(state.fullNormalizedMap, "gas_trap_1")
      || safeObject(state.environmentObjects.gas_trap_1);
    const trapX = Math.round(Number(safeObject(trap).x));
    const trapY = Math.round(Number(safeObject(trap).y));
    if (!Number.isFinite(trapX) || !Number.isFinite(trapY)) return partyStatus;
    const map = tacticalFormationMapData(mapData);
    const candidates = [
      { x: trapX, y: trapY + 1 },
      { x: trapX + 1, y: trapY },
      { x: trapX - 1, y: trapY },
      { x: trapX, y: trapY - 1 },
    ];
    const target = candidates.find((coord) => hasUsableFormationCoord(coord, map));
    if (!target) return partyStatus;
    return {
      ...partyStatus,
      [actorId]: {
        ...safeObject(partyStatus[actorId]),
        x: target.x,
        y: target.y,
        _projection_source: "actor_action",
      },
    };
  }

  function hasUsableFormationCoord(entity, mapLike) {
    const data = safeObject(entity);
    const x = Number(data.x);
    const y = Number(data.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
    const coord = { x: Math.round(x), y: Math.round(y) };
    if (!isWithinMapBounds(coord, mapLike)) return false;
    const collision = safeArray(safeObject(mapLike).collision);
    if (Boolean(safeArray(collision[coord.y])[coord.x])) return false;
    const grid = safeArray(safeObject(mapLike).grid);
    const cell = String(safeArray(grid[coord.y])[coord.x] || ".").toUpperCase();
    if (cell === "W" || cell === "#") return false;
    return isCoordInsideVisibleRoomsForFormation(coord, mapLike);
  }

  function projectPartyStatusForTactical(partyStatus, mapData) {
    const party = safeObject(partyStatus);
    const projected = { ...party };
    const rawPlayer = safeObject(party.player);
    const coords = resolveTacticalPlayerCoordinates(rawPlayer);
    const applyLocalTokens = (candidateParty) => preserveLocalPartyTokenPositions(candidateParty);
    if (!coords) return applyLocalTokens(projectCompanionFormationForTactical(projected, mapData, safeObject(projected.player)));
    projected.player = {
      ...rawPlayer,
      x: coords.x,
      y: coords.y,
      name: rawPlayer.name || "玩家",
      faction: rawPlayer.faction || "player",
      _projection_source: coords.source || "backend",
    };
    return applyLocalTokens(projectAct2DisarmActorApproach(
      projectCompanionFormationForTactical(projected, mapData, projected.player),
      mapData
    ));
  }

  function preserveLocalPartyTokenPositions(partyStatus) {
    const party = { ...safeObject(partyStatus) };
    if (!window.ControlledAgentTacticalMap || typeof window.ControlledAgentTacticalMap.getLocalPartyTokenPositions !== "function") {
      return party;
    }
    const localPositions = safeObject(window.ControlledAgentTacticalMap.getLocalPartyTokenPositions());
    FORMATION_COMPANIONS.forEach((companionId) => {
      const local = safeObject(localPositions[companionId]);
      const source = normalizeId(local._projection_source);
      if (!["local_party_trail", "local_party_follow", "visual_party_formation"].includes(source)) return;
      const x = Number(local.x);
      const y = Number(local.y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const incoming = safeObject(party[companionId]);
      const incomingSource = normalizeId(incoming._projection_source);
      if (incomingSource === "actor_action") return;
      const shouldPreserve =
        source === "local_party_trail"
        || source === "local_party_follow"
        || !Number.isFinite(Number(incoming.x))
        || !Number.isFinite(Number(incoming.y))
        || incomingSource === "visual_party_formation"
        || incomingSource === "local_party_trail"
        || incomingSource === "local_party_follow";
      if (!shouldPreserve) return;
      party[companionId] = {
        ...incoming,
        ...local,
        x: Math.round(x),
        y: Math.round(y),
        _projection_source: source,
      };
    });
    return party;
  }

  function renderTacticalGrid(partyStatus, environmentObjects, mapData) {
    const projectedPartyStatus = projectPartyStatusForTactical(partyStatus, mapData);
    const player = safeObject(projectedPartyStatus.player);
    if (window.ControlledAgentInputController && typeof window.ControlledAgentInputController.setPlayerPosition === "function") {
      const px = Number(player.x);
      const py = Number(player.y);
      const projectionSource = String(player._projection_source || "");
      const shouldApplyToInput =
        projectionSource === "backend"
        || projectionSource === "visual_start"
        || !isFiniteGridCoord(Number(safeObject(getInputControllerPosition()).x), Number(safeObject(getInputControllerPosition()).y));
      if (shouldApplyToInput && Number.isFinite(px) && Number.isFinite(py)) {
        window.ControlledAgentInputController.setPlayerPosition(px, py);
      }
    }
    const visibleEnvironment = filterEnvironmentObjectsForTactical(environmentObjects);
    if (window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.update === "function") {
      window.ControlledAgentTacticalMap.update(projectedPartyStatus, visibleEnvironment, mapData);
    }
    if (
      window.ControlledAgentInputController
      && typeof window.ControlledAgentInputController.getCurrentHighlightedInteractable === "function"
      && window.ControlledAgentTacticalMap
      && typeof window.ControlledAgentTacticalMap.setInteractionFocus === "function"
    ) {
      window.ControlledAgentTacticalMap.setInteractionFocus(window.ControlledAgentInputController.getCurrentHighlightedInteractable());
    }
    updateMapDebug("renderTacticalGrid");
  }

  function getTacticalEntities() {
    const records = [];
    const addRecord = (id, data, source) => {
      const entity = safeObject(data);
      const x = Number(entity.x);
      const y = Number(entity.y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      records.push({
        id: normalizeId(id),
        data: entity,
        source,
        name: entity.name || getDisplayName(id),
        x,
        y,
      });
    };

    Object.entries(safeObject(state.partyStatus)).forEach(([id, data]) => addRecord(id, data, "party"));
    Object.entries(safeObject(state.environmentObjects)).forEach(([id, data]) => addRecord(id, data, "environment"));
    return records;
  }

  function entityAliases(record) {
    const id = normalizeId(record.id);
    const aliases = [
      id,
      safeObject(record.data).id,
      record.name,
      safeObject(record.data).name,
      prettifyId(id),
      getDisplayName(id),
    ];
    if (id === "player") {
      aliases.push("玩家", "你", "我");
    }
    return Array.from(new Set(aliases.map((alias) => String(alias || "").trim()).filter(Boolean)));
  }

  function entityMentionIndex(text, record) {
    const haystack = String(text || "").toLowerCase();
    return entityAliases(record).reduce((best, alias) => {
      const index = haystack.indexOf(alias.toLowerCase());
      return index >= 0 ? Math.min(best, index) : best;
    }, Number.POSITIVE_INFINITY);
  }

  function mentionedEntities(text, records) {
    return records
      .map((record) => ({ record, index: entityMentionIndex(text, record) }))
      .filter((item) => Number.isFinite(item.index))
      .sort((a, b) => a.index - b.index);
  }

  function activeCombatantRecord(records) {
    const combat = safeObject(state.combatState);
    const order = safeArray(combat.initiative_order).map(normalizeId);
    const index = Number(combat.current_turn_index);
    const id = order[Number.isFinite(index) ? index : 0];
    return records.find((record) => record.id === id) || null;
  }

  function isHostileRecord(record) {
    return normalizeId(safeObject(record && record.data).faction) === "hostile";
  }

  function fallbackSourceRecord(records, blockedId) {
    const active = activeCombatantRecord(records);
    if (active && active.id !== blockedId) return active;
    return records.find((record) => record.id === "player" && record.id !== blockedId)
      || records.find((record) => !isHostileRecord(record) && record.id !== blockedId)
      || null;
  }

  function fallbackTargetRecord(source, records) {
    const sourceHostile = isHostileRecord(source);
    return records.find((record) => record.id !== source.id && isHostileRecord(record) !== sourceHostile)
      || records.find((record) => record.id !== source.id)
      || null;
  }

  function inferVisualEntities(text, mode) {
    const records = getTacticalEntities();
    if (!records.length) return { source: null, target: null };

    const mentions = mentionedEntities(text, records);
    if ((mode === "spell" || mode === "knockback") && mentions.length === 1) {
      const target = mentions[0].record;
      const source = fallbackSourceRecord(records, target.id);
      return { source, target };
    }

    let source = mentions[0] ? mentions[0].record : fallbackSourceRecord(records, "");
    let target = mentions.find((item) => !source || item.record.id !== source.id)?.record || null;

    if (!source && target) {
      source = fallbackSourceRecord(records, target.id);
    }
    if (source && (!target || target.id === source.id)) {
      target = fallbackTargetRecord(source, records);
    }

    return { source, target };
  }

  function parseGridPointFromText(text) {
    const value = String(text || "");
    const patterns = [
      /(?:坐标|位置|落点|推到|推至|击退到|撞到)[^\d-]*\(?\s*(-?\d+)\s*[,，]\s*(-?\d+)\s*\)?/i,
      /\(\s*(-?\d+)\s*[,，]\s*(-?\d+)\s*\)/,
    ];

    for (const pattern of patterns) {
      const match = value.match(pattern);
      if (!match) continue;
      const x = Number(match[1]);
      const y = Number(match[2]);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        return { x, y };
      }
    }
    return null;
  }

  function inferKnockbackTarget(text) {
    const records = getTacticalEntities();
    if (!records.length) return null;

    const value = String(text || "");
    const mentions = mentionedEntities(value, records);
    if (mentions.length === 0) return null;
    if (mentions.length === 1) return mentions[0].record;

    const passiveTarget = mentions.find(({ index }) => {
      const windowText = value.slice(index, index + 48);
      return /被|遭|受到|挨|推开|击退|推入|推到|推至/.test(windowText);
    });
    if (passiveTarget) return passiveTarget.record;

    const verbIndex = value.search(/推击|力量对抗|强制位移|击退|推开|推入|推到|推至/);
    if (verbIndex >= 0) {
      const afterVerb = mentions.find(({ index }) => index > verbIndex);
      if (afterVerb) return afterVerb.record;
    }

    const { target } = inferVisualEntities(value, "knockback");
    return target || mentions[1].record || mentions[0].record;
  }

  function hasTerrainDamageCue(text) {
    return /火焰伤害|篝火|营火|火堆|campfire|fire/i.test(String(text || ""));
  }

  function inferSingleVisualEntity(text) {
    const records = getTacticalEntities();
    if (!records.length) return null;

    const mentions = mentionedEntities(text, records);
    if (mentions.length > 0) {
      return mentions[0].record;
    }
    return activeCombatantRecord(records) || records.find((record) => record.id === "player") || records[0];
  }

  function parseDamageAmount(text) {
    const value = String(text || "");
    const match = value.match(/(?:受到|扣除|损失|造成)?\s*(\d+)\s*点(?:中毒|毒素|毒性|伤害)?/);
    return match ? Number(match[1]) : null;
  }

  function resolveSpeechSpeaker(rawSpeaker, text) {
    const speaker = normalizeId(rawSpeaker);
    if (speaker) {
      const direct = getTacticalEntities().find((record) => {
        return record.id === speaker || entityAliases(record).some((alias) => normalizeId(alias) === speaker);
      });
      if (direct) return direct.id;
    }

    const mentioned = mentionedEntities(text || "", getTacticalEntities());
    return mentioned[0] ? mentioned[0].record.id : "";
  }

  function parseBarkString(line) {
    const value = String(line || "").trim();
    const tagged = value.match(/\[台词\]\s*([^:：]+)?[:：]\s*(.+)$/);
    if (tagged) {
      return { speaker: tagged[1] || "", text: tagged[2] || "" };
    }

    const plain = value.match(/^([^:：]{1,32})[:：]\s*(.+)$/);
    if (plain) {
      return { speaker: plain[1] || "", text: plain[2] || "" };
    }

    return { speaker: "", text: value.replace(/\[台词\]\s*/, "") };
  }

  const BARK_ACTOR_ORDER = ["scout", "analyst", "tactician"];

  function normalizeBarkSpeakerId(value) {
    const raw = normalizeId(value).replace(/[’']/g, "");
    if (/scout|侦察员/.test(raw)) return "scout";
    if (/analyst|分析员/.test(raw)) return "analyst";
    if (/tactician|战术员/.test(raw)) return "tactician";
    if (/gatekeeper|守门人/.test(raw)) return "gatekeeper";
    if (/party|队伍|同伴/.test(raw)) return "party";
    return raw;
  }

  function resolveBarkSpeaker(rawSpeaker, text) {
    const normalized = normalizeBarkSpeakerId(rawSpeaker);
    if (["scout", "analyst", "tactician", "gatekeeper", "party"].includes(normalized)) return normalized;
    return resolveSpeechSpeaker(rawSpeaker, text);
  }

  function bossStrategyText(actor, plan) {
    const speaker = normalizeBarkSpeakerId(actor);
    const key = normalizeId(plan);
    if (speaker === "scout" || key === "steal_key") {
      return "给我一个机会，我能把钥匙弄出来。";
    }
    if (speaker === "analyst" || key === "contain_corruption") {
      return "逼太狠，毒气罐可能会先炸。";
    }
    if (speaker === "tactician" || key === "execute") {
      return "杀掉守门人，拿走钥匙，打开门。";
    }
    return "先决定路线，再动手。";
  }

  function studyObservationText(actor) {
    const speaker = normalizeBarkSpeakerId(actor);
    if (speaker === "scout") return "钥匙草图、逃生路线……终于有点有用的东西。";
    if (speaker === "analyst") return "这里的危害气息很重，别乱碰。";
    if (speaker === "tactician") return "找到能开门的东西，然后离开。";
    return "";
  }

  function partyStanceText(actor, stance) {
    const speaker = normalizeBarkSpeakerId(actor);
    const key = normalizeId(stance);
    if (key === "mercy") return "放过他。我们不需要再添一具尸体。";
    if (key === "execute") return speaker === "tactician" ? "结束这一切。仁慈只会喂养软弱。" : "在他说出更多废话前杀了他。";
    if (key === "resentful") return "哦，现在我的意见又重要了？";
    if (key === "mocking") return "仁慈？真是英勇，也真是麻烦。";
    return "我有立场，如果有人愿意听的话。";
  }

  function journalLineToBarks(line) {
    const text = String(line || "");
    const result = [];
    let match = text.match(/\[陷阱感知\]\s*([a-z0-9_'’\-]+)\s*->\s*(gas_trap_1|poison_trap_[12])/i);
    if (match) {
      result.push({
        speaker: normalizeBarkSpeakerId(match[1]) || "scout",
        text: "附近有陷阱，小心。",
        source: "trap_insight",
        priority: 9,
      });
    }
    match = text.match(/\[陷阱解除\]\s*([a-z0-9_'’\-]+)\s*->\s*(gas_trap_1|poison_trap_[12])/i);
    if (match) {
      result.push({
        speaker: normalizeBarkSpeakerId(match[1]) || "scout",
        text: "处理好了。走廊安全了。",
        source: "trap_disarmed",
        priority: 8,
      });
    }
    match = text.match(/\[书房观察\]\s*([a-z0-9_'’\-]+)\s*->\s*(.+)$/i);
    if (match) {
      const speaker = normalizeBarkSpeakerId(match[1]);
      const barkText = studyObservationText(speaker);
      if (barkText) {
        result.push({ speaker, text: barkText, source: "study_observation", priority: 6 });
      }
    }
    match = text.match(/\[Boss方案\]\s*([a-z0-9_'’\-]+)\s*->\s*(steal_key|contain_corruption|execute)/i);
    if (match) {
      const speaker = normalizeBarkSpeakerId(match[1]);
      result.push({
        speaker,
        text: bossStrategyText(speaker, match[2]),
        source: "boss_strategy",
        priority: 6,
      });
    }
    match = text.match(/\[记忆回响\]\s*([a-z0-9_'’\-]+)\s*->\s*(rebuked_by_player|sided_with_player)/i);
    if (match) {
      result.push({
        speaker: normalizeBarkSpeakerId(match[1]) || "scout",
        text: normalizeId(match[2]) === "sided_with_player" ? "残忍共享，信任也会发芽。" : "现在又需要我了？",
        source: "memory_echo",
        priority: 7,
      });
    }
    match = text.match(/\[站队\]\s*([a-z0-9_'’\-]+)\s*->\s*(mercy|execute|resentful|mocking)/i);
    if (match) {
      const speaker = normalizeBarkSpeakerId(match[1]);
      result.push({
        speaker,
        text: partyStanceText(speaker, match[2]),
        source: "party_stance",
        priority: 6,
      });
    }
    match = text.match(/\[抉择\]\s*(gatekeeper|守门人)\s*->\s*(spared|executed)/i);
    if (match) {
      result.push({
        speaker: "gatekeeper",
        text: normalizeId(match[2]) === "spared" ? "我会走。钥匙的路还在。" : "你们……会把这里也一起埋掉。",
        source: "mercy_resolution",
        priority: 6,
      });
    }
    return result;
  }

  function responseRecordToBark(response) {
    if (response == null) return null;
    if (typeof response === "string") {
      const parsed = parseBarkString(response);
      const speaker = resolveBarkSpeaker(parsed.speaker, parsed.text);
      return speaker ? { speaker, text: parsed.text, source: "response" } : null;
    }
    const record = safeObject(response);
    const text = record.text || record.line || record.content || record.message || record.response || record.reply || "";
    const speaker = resolveBarkSpeaker(
      record.speaker || record.name || record.actor || record.actorId || record.actor_id || record.character || record.entity_id || record.id,
      text,
    );
    return speaker && text ? { speaker, text, source: "response" } : null;
  }

  function responseIndicatesInteractionBlocked(data) {
    const payload = safeObject(data);
    const gameState = safeObject(payload.game_state || payload.gameState || payload.state);
    const roll = safeObject(payload.latest_roll || payload.raw_roll_data || gameState.latest_roll || gameState.raw_roll_data);
    const result = safeObject(roll.result);
    const resultType = normalizeId(result.result_type || roll.result_type);
    if ([
      "missing_key",
      "not_found",
      "out_of_range",
      "invalid_target",
      "invalid_object",
      "no_bonus_action",
      "blocked",
    ].includes(resultType)) {
      return true;
    }
    if (result.is_success === false || roll.is_success === false) {
      return true;
    }
    return extractEventLines(payload).some((line) => {
      const text = String(line || "");
      return /距离过远|需相邻|需要.*钥匙|requires?\s+key|missing[_\s-]?key|too\s*far|not\s*adjacent|cannot\s+interact|无法交互|交互失败/i.test(text);
    });
  }

  function barksFromUIEvents(events) {
    if (!window.ControlledAgentHudRenderers || typeof window.ControlledAgentHudRenderers.barksFromUIEvent !== "function") return [];
    return safeArray(events).flatMap((event) => window.ControlledAgentHudRenderers.barksFromUIEvent(event) || []);
  }

  function sortKnownPartyBarks(barks, source) {
    if (!String(source || "").match(/boss_strategy|study_observation/i)) return barks;
    return barks.slice().sort((a, b) => {
      const ai = BARK_ACTOR_ORDER.indexOf(normalizeBarkSpeakerId(a.speaker));
      const bi = BARK_ACTOR_ORDER.indexOf(normalizeBarkSpeakerId(b.speaker));
      return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
    });
  }

  function extractSpeechBarks(data, options = {}) {
    const payload = safeObject(data);
    const gameState = safeObject(payload.game_state || payload.gameState || payload.state);
    const barks = [];
    const dispatchedEvents = safeArray(options.dispatchedEvents);
    const pushBark = (speaker, text, meta = {}) => {
      const content = String(text || "").trim();
      if (!content) return;
      const speakerId = resolveBarkSpeaker(speaker, content);
      if (!speakerId) return;
      barks.push({ speaker: speakerId, text: content, source: meta.source || "", priority: meta.priority || 0 });
    };

    safeArrayOrObjectValues(payload.recent_barks || gameState.recent_barks).forEach((entry) => {
      if (typeof entry === "string") {
        const parsed = parseBarkString(entry);
        pushBark(parsed.speaker, parsed.text, { source: "recent_barks" });
        return;
      }
      const record = safeObject(entry);
      pushBark(
        record.speaker || record.entity_id || record.actor || record.character || record.id,
        record.text || record.line || record.content || record.message,
        { source: "recent_barks" },
      );
    });

    [
      ...safeArray(payload.responses),
      ...safeArray(gameState.responses),
      ...safeArray(payload.response),
    ].forEach((response) => {
      const bark = responseRecordToBark(response);
      if (bark && !["dm", "narrator", "system", "npc"].includes(normalizeId(bark.speaker))) {
        pushBark(bark.speaker, bark.text, { source: bark.source });
      }
    });
    const singularResponse = responseRecordToBark(payload.response);
    if (singularResponse && !["dm", "narrator", "system", "npc"].includes(normalizeId(singularResponse.speaker))) {
      pushBark(singularResponse.speaker, singularResponse.text, { source: singularResponse.source });
    }

    extractEventLines(data).forEach((line) => {
      if (/\[台词\]/.test(line)) {
        const parsed = parseBarkString(line);
        pushBark(parsed.speaker, parsed.text, { source: "journal_line" });
      }
      journalLineToBarks(line).forEach((bark) => pushBark(bark.speaker, bark.text, bark));
    });

    if (options.skipUIEvents !== true) {
      barksFromUIEvents([
        ...safeArray(payload.ui_events),
        ...safeArray(gameState.ui_events),
        ...dispatchedEvents,
      ]).forEach((bark) => pushBark(bark.speaker, bark.text, bark));
    }

    const dedupe = new Set();
    const speakerTextDedupe = new Set();
    const signalSources = new Set([
      "trap_insight",
      "trap_disarmed",
      "trap_triggered",
      "study_observation",
      "boss_strategy",
      "memory_echo",
      "party_stance",
      "mercy_resolution",
    ]);
    const deduped = barks.filter((bark) => {
      const speakerTextKey = normalizeBarkSpeakerId(bark.speaker) + "::" + normalizeId(bark.text);
      if (speakerTextDedupe.has(speakerTextKey)) return false;
      speakerTextDedupe.add(speakerTextKey);
      const source = normalizeId(bark.source);
      const key = signalSources.has(source)
        ? normalizeBarkSpeakerId(bark.speaker) + "::" + source
        : normalizeBarkSpeakerId(bark.speaker) + "::" + source + "::" + normalizeId(bark.text);
      if (dedupe.has(key)) return false;
      dedupe.add(key);
      return true;
    });
    const grouped = [];
    const bySource = new Map();
    deduped.forEach((bark) => {
      const source = bark.source || "";
      if (!bySource.has(source)) bySource.set(source, []);
      bySource.get(source).push(bark);
    });
    bySource.forEach((items, source) => {
      grouped.push(...sortKnownPartyBarks(items, source));
    });
    return grouped;
  }

  function triggerSpeechBubbles(data, options = {}) {
    if (!window.ControlledAgentHudRenderers || typeof window.ControlledAgentHudRenderers.dispatchCompanionBarks !== "function") return;
    hardClearAct3TrapBarksIfNeeded(data, "speech_dispatch");
    const barks = extractSpeechBarks(data, options)
      .filter((bark) => normalizeId(bark.speaker) !== "player")
      .slice(0, 4)
      .map((bark) => ({
        ...bark,
        source: bark.source || "response",
        epoch: state.barkEpoch,
      }));
    if (hasAct3BarkResetSignal(data) && typeof window.ControlledAgentHudRenderers.clearCompanionBarks === "function") {
      window.ControlledAgentHudRenderers.clearCompanionBarks({
        groups: ["trap", "generic_response"],
        resetCompleted: true,
        suppress: true,
        reason: "act3_context",
      });
    }
    if (barks.length) {
      window.ControlledAgentHudRenderers.dispatchCompanionBarks(barks);
    }
    if (hasFinalBarkClearSignal(data) && typeof window.ControlledAgentHudRenderers.clearCompanionBarks === "function") {
      window.ControlledAgentHudRenderers.clearCompanionBarks({
        force: true,
        resetCompleted: true,
        resetGroup: true,
        reason: "final_clear",
      });
    }
  }

  function hasAct3BarkResetSignal(data) {
    const payload = safeObject(data);
    const gameState = safeObject(payload.game_state || payload.gameState || payload.state);
    const flags = {
      ...safeObject(gameState.flags),
      ...safeObject(payload.flags),
    };
    const lines = extractEventLines(payload).join("\n");
    const visibleRooms = [
      ...safeArray(payload.visibleRooms),
      ...safeArray(payload.visible_rooms),
      ...safeArray(safeObject(payload.map_data).visibleRooms),
      ...safeArray(safeObject(payload.map_data).visible_rooms),
      ...safeArray(safeObject(gameState.map_data).visibleRooms),
      ...safeArray(safeObject(gameState.map_data).visible_rooms),
    ].map((roomId) => normalizeId(roomId));
    return visibleRooms.includes(ROOM_C)
      || state.roomVisibleIds.has(ROOM_C)
      || /\[秘密书房\]|\[书房观察\]|\[线索整合\]/i.test(lines)
      || flags.act3_diary_context_gathered === true
      || flags.act3_diary_read === true
      || flags.act3_diary_decoded === true
      || flags.act3_secret_study_entered === true
      || flags.act3_secret_study_discovered === true
      || flags.hazard_lab_diary_decoded === true;
  }

  function isAct3BarkRequest(routed = {}, payload = {}) {
    const text = normalizeId(routed.userLine || safeObject(payload).user_input || "");
    const target = normalizeId(routed.target || safeObject(payload).target || "");
    const source = normalizeId(routed.source || safeObject(payload).source || "");
    const intent = normalizeId(routed.intentValue || safeObject(payload).intent || "");
    if (source === "act3_study_context") return true;
    if (["chemical_notes", "iron_key_sketch", "hazard_diary"].includes(target)) return true;
    if (intent === "read" && /(chemical_notes|iron_key_sketch|hazard_diary|药剂笔记|化学|钥匙草图|日记|diary|notes|sketch)/i.test(text)) {
      return true;
    }
    return false;
  }

  function hardClearAct3TrapBarks(reason = "act3_transition") {
    if (!window.ControlledAgentHudRenderers || typeof window.ControlledAgentHudRenderers.clearCompanionBarks !== "function") return false;
    state.act3BarkEpoch = Math.max(Number(state.act3BarkEpoch) || 0, Number(state.barkEpoch) || 0);
    if (typeof window.ControlledAgentHudRenderers.setBarkSceneContext === "function") {
      window.ControlledAgentHudRenderers.setBarkSceneContext({
        act: "act3",
        visibleRooms: Array.from(state.roomVisibleIds || []),
      });
    }
    if (typeof window.ControlledAgentHudRenderers.clearBarksByScope === "function") {
      window.ControlledAgentHudRenderers.clearBarksByScope("act2_corridor", { suppressGroups: ["trap"], reason });
    }
    window.ControlledAgentHudRenderers.clearCompanionBarks({
      groups: ["trap", "generic_response"],
      force: true,
      resetCompleted: true,
      suppress: true,
      reason,
    });
    return true;
  }

  function hardClearAct3TrapBarksIfNeeded(data = {}, reason = "act3_transition") {
    if (!hasAct3BarkResetSignal(data)) return false;
    return hardClearAct3TrapBarks(reason);
  }

  function applyLocalAct3ReadProjection(actionIntent, actionTarget, data = {}) {
    if (normalizeId(actionIntent) !== "read") return false;
    const target = normalizeId(actionTarget);
    const isStudyContext = target === "chemical_notes" || target === "iron_key_sketch";
    const isDiary = target === "hazard_diary";
    if (!isStudyContext && !isDiary) return false;

    const flags = safeObject(state.worldFlags);
    flags.act3_secret_study_entered = true;
    if (target === "chemical_notes") {
      flags.act3_chemical_notes_seen = true;
      flags.act3_diary_context_gathered = true;
      flags.act3_diary_context_bonus = flags.act3_diary_context_bonus || 10;
    }
    if (target === "iron_key_sketch") {
      flags.act3_key_sketch_seen = true;
      flags.act3_diary_context_gathered = true;
      flags.act3_diary_context_bonus = flags.act3_diary_context_bonus || 10;
    }
    if (isDiary) {
      const latest = safeObject(data.latest_roll);
      const result = safeObject(latest.result);
      const succeeded = result.is_success === true
        || latest.is_success === true
        || normalizeId(result.result_type).includes("success");
      flags.act3_diary_read = true;
      if (succeeded || flags.act3_diary_context_gathered === true) {
        flags.act3_diary_decoded = true;
        flags.act3_gatekeeper_potion_truth_known = true;
        flags.act3_heavy_key_hint_known = true;
        flags.act3_party_knows_gatekeeper_truth = true;
      }
    }
    state.worldFlags = flags;
    revealRoom(ROOM_C);
    refreshVisibilityProjection();
    hardClearAct3TrapBarks("act3_read_projection");
    updateExplorationActProgress();
    return true;
  }

  function hasFinalBarkClearSignal(data) {
    const payload = safeObject(data);
    const gameState = safeObject(payload.game_state || payload.gameState || payload.state);
    const flags = {
      ...safeObject(gameState.flags),
      ...safeObject(payload.flags),
    };
    const lines = extractEventLines(payload).join("\n");
    return payload.demo_cleared === true
      || gameState.demo_cleared === true
      || flags.act4_final_exit_opened === true
      || /demo\s*cleared|\[DEMO CLEARED\]/i.test(lines);
  }

  function triggerMapTransitionEffects(data) {
    if (!window.ControlledAgentTacticalMap || typeof window.ControlledAgentTacticalMap.playMapTransition !== "function") return;
    const hasMapCue = extractEventLines(data).some((line) => {
      return /\[地图探索\]|地图探索|地图切换|进入新地图|加载地图|场景切换/.test(String(line || ""));
    });
    if (!hasMapCue) return;
    window.setTimeout(() => {
      window.ControlledAgentTacticalMap.playMapTransition();
    }, 40);
  }

  function triggerRestVisualEffects(data, fallbackIntent) {
    if (!window.ControlledAgentTacticalMap) return;
    const responseText = safeArray(data && data.responses)
      .map((response) => String(safeObject(response).text || ""))
      .join("\n");
    const events = [extractEventLines(data).join("\n"), responseText].join("\n");
    const intent = normalizeId(fallbackIntent);
    const shortRest = /短休|short\s*rest|short_rest/i.test(events) || intent === "short_rest";
    const longRest = /长休|long\s*rest|long_rest|一夜过去|the next day/i.test(events) || intent === "long_rest";

    if (longRest && typeof window.ControlledAgentTacticalMap.playLongRest === "function") {
      window.setTimeout(() => window.ControlledAgentTacticalMap.playLongRest(), 80);
      return;
    }

    if (shortRest && typeof window.ControlledAgentTacticalMap.playShortRest === "function") {
      window.setTimeout(() => window.ControlledAgentTacticalMap.playShortRest(), 80);
    }
  }

  function extractActiveDialogueTarget(data) {
    const payload = safeObject(data);
    const gameState = safeObject(payload.game_state || payload.gameState);
    const combat = safeObject(payload.combat_state);
    return normalizeId(
      payload.active_dialogue_target
      || gameState.active_dialogue_target
      || combat.active_dialogue_target
      || "",
    );
  }

  function dialogueTargetAliases(targetId) {
    const target = normalizeId(targetId);
    const name = normalizeId(getEntityDisplayName(target));
    return new Set([target, name].filter(Boolean));
  }

  function parseDialogueJournalLine(line) {
    const text = String(line || "").trim();
    const match = text.match(/\[([^\]]+)\]\s*[:：]\s*[“"]?([\s\S]*?)[”"]?\s*$/);
    if (!match) return null;
    return {
      speaker: normalizeId(match[1]),
      text: String(match[2] || "").trim(),
    };
  }

  function extractDialogueTextForTarget(data, targetId) {
    const aliases = dialogueTargetAliases(targetId);
    const responses = safeArray(data && data.responses).slice().reverse();
    for (const response of responses) {
      const record = safeObject(response);
      const speaker = normalizeId(record.speaker);
      const text = String(record.text || "").trim();
      if (text && aliases.has(speaker)) {
        return text;
      }
    }

    const events = extractEventLines(data).slice().reverse();
    for (const line of events) {
      const parsed = parseDialogueJournalLine(line);
      if (parsed && parsed.text && aliases.has(parsed.speaker)) {
        return parsed.text;
      }
    }

    return "";
  }

  function updateDialogueOverlay(data) {
    if (!els.dialogueOverlay) return;
    const target = extractActiveDialogueTarget(data);
    state.activeDialogueTarget = target;
    window.ControlledAgentDialogueActive = Boolean(target);

    if (!target) {
      state.dialogueText = "";
      els.dialogueOverlay.classList.add("hidden");
      els.dialogueOverlay.setAttribute("aria-hidden", "true");
      return;
    }

    const wasHidden = els.dialogueOverlay.classList.contains("hidden");
    const npcName = getEntityDisplayName(target);
    const dialogueText = extractDialogueTextForTarget(data, target) || state.dialogueText || "……";
    state.dialogueText = dialogueText;

    els.dialogueNpcName.textContent = npcName;
    els.dialogueText.textContent = dialogueText;
    els.dialogueOverlay.classList.remove("hidden");
    els.dialogueOverlay.setAttribute("aria-hidden", "false");

    if (wasHidden && els.dialogueInput) {
      window.requestAnimationFrame(() => {
        els.dialogueInput.focus();
      });
    }
  }

  function triggerCombatVisualEffects(data, userLine) {
    if (!window.ControlledAgentTacticalMap) return;

    const dedupe = new Set();
    const events = extractEventLines(data).filter((line) => {
      const key = String(line || "").trim();
      if (!key || dedupe.has(key)) return false;
      dedupe.add(key);
      return true;
    });
    let playedSpellEffect = false;
    const responseHasTerrainDamage = events.some(hasTerrainDamageCue);

    events.forEach((line) => {
      const combinedText = [line, userLine].filter(Boolean).join(" ");
      if (/失败|找不到|未指定|无需再次|动作资源不足/.test(line)) return;

      if (/\[状态结算\]|状态结算/.test(line) && /中毒|poison/i.test(line)) {
        const target = inferSingleVisualEntity(combinedText);
        const damage = parseDamageAmount(line);
        if (target && typeof window.ControlledAgentTacticalMap.playStatusDamage === "function") {
          window.setTimeout(() => {
            window.ControlledAgentTacticalMap.playStatusDamage(target.id, damage ? "-" + damage : "中毒");
          }, 80);
        }
      }

      if (/获得优势|advantage/i.test(line)) {
        const actor = inferSingleVisualEntity(combinedText);
        if (actor && typeof window.ControlledAgentTacticalMap.playAdvantage === "function") {
          window.setTimeout(() => {
            window.ControlledAgentTacticalMap.playAdvantage(actor.id);
          }, 80);
        }
      }

      if (/推击|力量对抗|强制位移|击退|推开|推入|推到|推至/.test(line)) {
        const target = inferKnockbackTarget(combinedText);
        const destination = parseGridPointFromText(combinedText) || target;
        if (target && destination && typeof window.ControlledAgentTacticalMap.playKnockback === "function") {
          window.setTimeout(() => {
            window.ControlledAgentTacticalMap.playKnockback(
              target.id,
              { x: destination.x, y: destination.y },
              {
                terrainDamage: responseHasTerrainDamage || hasTerrainDamageCue(line),
                label: "火焰伤害",
              },
            );
          }, 80);
        }
        return;
      }

      if (!playedSpellEffect && /施放了|施展|吟唱|雷鸣波|圣火术|范围轰炸|aoe/i.test(line)) {
        const { source, target } = inferVisualEntities(combinedText, "spell");
        const center = target || source;
        if (center && typeof window.ControlledAgentTacticalMap.playAoE === "function") {
          window.setTimeout(() => {
            window.ControlledAgentTacticalMap.playAoE({ x: center.x, y: center.y });
          }, 80);
          playedSpellEffect = true;
        }
        return;
      }

      if (/发起攻击/.test(line)) {
        const { source, target } = inferVisualEntities(combinedText, "attack");
        if (source && target && typeof window.ControlledAgentTacticalMap.playProjectile === "function") {
          const color = isHostileRecord(source) ? 0xff4a4a : 0x00ffff;
          window.setTimeout(() => {
            window.ControlledAgentTacticalMap.playProjectile(
              { x: source.x, y: source.y },
              { x: target.x, y: target.y },
              color,
            );
          }, 80);
        }
      }
    });
  }

  function isCombatStateActive(combatState) {
    const combat = safeObject(combatState);
    const phase = normalizeId(combat.combat_phase || combat.phase || "");
    const isOutOfCombatPhase = ["out_of_combat", "outofcombat", "exploration", "free_roam", "victory"].includes(phase);
    return combat.combat_active === true && !isOutOfCombatPhase;
  }

  function updateRestControls(combatState) {
    if (!els.restControls) return;
    const isExploration = !isCombatStateActive(combatState);
    const visible = QA_REST_CONTROLS && isExploration;
    els.restControls.classList.toggle("is-hidden", !visible);
    els.restControls.setAttribute("aria-hidden", String(!visible));
  }

  function normalizeNodeName(nodeName) {
    const node = normalizeId(nodeName);
    const aliases = {
      input_node: "player_input",
      input_processing: "player_input",
      dm_node: "dm_router",
      dm_analysis: "dm_router",
      mechanics_node: "domain_event",
      mechanics_processing: "domain_event",
      dialogue_node: "actor_runtime",
      dialogue_processing: "actor_runtime",
      generation_node: "ui_events",
      generation: "ui_events",
      actor_view: "actor_view_filter",
      party_coordinator: "actor_runtime",
      party_turn_coordinator: "actor_runtime",
      eventdrain: "event_drain",
    };
    return aliases[node] || node;
  }

  function normalizeTimingMs(value) {
    const num = Number(value);
    if (!Number.isFinite(num) || num < 0) return null;
    return Math.round(num);
  }

  function extractTimingMsFromEntry(entry) {
    if (entry == null) return null;
    if (typeof entry === "number" || typeof entry === "string") {
      return normalizeTimingMs(entry);
    }
    if (typeof entry !== "object") return null;
    const record = safeObject(entry);
    return normalizeTimingMs(
      record.timing_ms
      ?? record.duration_ms
      ?? record.elapsed_ms
      ?? record.latency_ms
      ?? record.ms
      ?? record.time_ms
      ?? record.timeMs
      ?? record.duration
      ?? record.elapsed
      ?? null
    );
  }

  function mergeTimingRecord(target, source) {
    const src = safeObject(source);
    Object.entries(src).forEach(([rawNode, rawTiming]) => {
      const node = normalizeNodeName(rawNode);
      if (!node) return;
      const ms = extractTimingMsFromEntry(rawTiming);
      if (ms == null) return;
      target[node] = ms;
    });
  }

  function extractTimingPairsFromArray(target, list) {
    safeArray(list).forEach((rawItem) => {
      const item = safeObject(rawItem);
      const node = normalizeNodeName(item.node || item.node_name || item.name || item.id || "");
      if (!node) return;
      const ms = extractTimingMsFromEntry(item);
      if (ms == null) return;
      target[node] = ms;
    });
  }

  function resolveNodeTimings(payload, gameState) {
    const timings = {};
    const p = safeObject(payload);
    const g = safeObject(gameState);
    const streamState = safeObject(p.state);

    const objectCandidates = [
      p.node_timing_map,
      p.node_timings,
      p.node_timing,
      p.timings,
      p.timing,
      p.node_metrics,
      p.trace_timings,
      p.xray_timing,
      streamState.node_timing_map,
      streamState.node_timings,
      streamState.node_timing,
      streamState.timings,
      streamState.timing,
      streamState.node_metrics,
      streamState.trace_timings,
      streamState.xray_timing,
      g.node_timing_map,
      g.node_timings,
      g.node_timing,
      g.timings,
      g.timing,
      g.node_metrics,
      g.trace_timings,
      g.xray_timing,
    ];
    objectCandidates.forEach((candidate) => mergeTimingRecord(timings, candidate));

    const arrayCandidates = [
      p.node_results,
      p.node_timings_list,
      p.trace_results,
      p.trace_events,
      g.node_results,
      g.node_timings_list,
      g.trace_results,
      g.trace_events,
    ];
    arrayCandidates.forEach((candidate) => extractTimingPairsFromArray(timings, candidate));

    const directNode = normalizeNodeName(p.node_name || streamState.node_name || "");
    const directMs = extractTimingMsFromEntry(
      p.timing_ms
      ?? p.duration_ms
      ?? p.elapsed_ms
      ?? streamState.timing_ms
      ?? streamState.duration_ms
      ?? streamState.elapsed_ms
      ?? null
    );
    if (directNode && directMs != null) {
      timings[directNode] = directMs;
    }

    return timings;
  }

  function timingClassForMs(ms) {
    if (ms == null) return "";
    if (ms <= 120) return "node-timing--fast";
    if (ms <= 450) return "node-timing--medium";
    return "node-timing--slow";
  }

  function updateXrayNodeTimings(timingMap) {
    if (!els.nodeTimeline) return;
    const timings = safeObject(timingMap);
    els.nodeTimeline.querySelectorAll("li[data-node]").forEach((item) => {
      const node = normalizeNodeName(item.dataset.node);
      const badge = item.querySelector(".node-timing");
      if (!badge) return;
      const ms = normalizeTimingMs(timings[node]);
      badge.textContent = ms == null ? "--ms" : ms + "ms";
      badge.classList.remove("node-timing--fast", "node-timing--medium", "node-timing--slow");
      const klass = timingClassForMs(ms);
      if (klass) badge.classList.add(klass);
    });
  }

  function countObjectKeys(value) {
    return Object.keys(safeObject(value)).length;
  }

  function payloadInspectorSource(options = {}) {
    const lastFetch = safeObject(state.mapDebugLastFetch);
    const url = String(lastFetch.url || "");
    if (url.includes("/api/state")) return "/api/state";
    if (url.includes("/api/chat")) return "/api/chat";
    if (options.intent || options.userLine) return "/api/chat";
    return "local/bootstrap";
  }

  function renderPayloadSummary(payload, gameState, trace, watcher, options = {}) {
    if (!els.payloadSummary) return;
    const flags = safeObject(payload.flags || gameState.flags);
    const entities = safeObject(gameState.entities || payload.entities || payload.party_status);
    const party = safeObject(payload.party_status || gameState.party_status);
    const env = safeObject(payload.environment_objects || gameState.environment_objects);
    const journalEvents = safeArray(payload.journal_events || gameState.journal_events);
    const uiEvents = safeArray(payload.ui_events || gameState.ui_events);
    const responses = safeArray(payload.responses || gameState.responses);
    const traceList = safeArray(trace).map(normalizeNodeName).filter(Boolean);
    const lastNode = normalizeNodeName(payload.last_node || gameState.last_node || gameState.current_node || traceList[traceList.length - 1] || "");
    const traceState = window.ControlledAgentDirectorTrace && typeof window.ControlledAgentDirectorTrace.getState === "function"
      ? window.ControlledAgentDirectorTrace.getState()
      : "idle";
    const rows = [
      ["Source", payloadInspectorSource(options)],
      ["Trace", traceState + (lastNode ? " / " + lastNode : "")],
      ["Watcher", watcher.targetId || "none"],
      ["Responses", String(responses.length)],
      ["Journal", String(journalEvents.length)],
      ["UI Events", String(uiEvents.length)],
      ["Entities", String(countObjectKeys(entities) || countObjectKeys(party))],
      ["Flags", String(countObjectKeys(flags))],
      ["Objects", String(countObjectKeys(env))],
    ];

    els.payloadSummary.innerHTML = "";
    rows.forEach(([label, value]) => {
      const item = document.createElement("div");
      item.className = "payload-summary-row";
      const key = document.createElement("span");
      key.textContent = label;
      const val = document.createElement("strong");
      val.textContent = value;
      item.appendChild(key);
      item.appendChild(val);
      els.payloadSummary.appendChild(item);
    });
  }

  function inferNodeTrace(data, userLine, intent) {
    if (window.ControlledAgentDirectorTrace && typeof window.ControlledAgentDirectorTrace.buildTraceNodes === "function") {
      return window.ControlledAgentDirectorTrace.buildTraceNodes(data, {
        userLine,
        intent,
      });
    }
    const payload = safeObject(data);
    const gameState = safeObject(payload.game_state);
    const explicit = normalizeNodeName(payload.last_node || gameState.last_node || gameState.current_node);
    if (explicit) {
      const trace = ["player_input", "dm_router"];
      if (explicit === "domain_event") trace.push("domain_event", "event_drain");
      if (explicit === "actor_runtime") trace.push("actor_runtime");
      if (explicit === "ui_events") {
        const combined = [userLine, intent, extractEventLines(data).join(" "), JSON.stringify(gameState.intent_context || {})].join(" ");
        if (/检定|掷骰|潜行|攻击|推击|法术|装备|卸下|搜刮|开锁|解除陷阱|短休|长休|移动|交互/.test(combined)) {
          trace.push("domain_event", "event_drain");
        }
        if (/对话|交涉|台词|说|回复|dialogue/i.test(combined)) {
          trace.push("actor_runtime");
        }
        trace.push("ui_events");
      } else {
        trace.push(explicit);
      }
      return Array.from(new Set(trace));
    }

    const text = [userLine, intent, extractEventLines(data).join(" ")].join(" ");
    const trace = ["player_input", "dm_router", "actor_view_filter"];
    if (/检定|掷骰|潜行|攻击|推击|法术|装备|卸下|搜刮|开锁|解除陷阱|短休|长休|移动|交互/.test(text)) {
      trace.push("domain_event", "event_drain");
    }
    if (/对话|交涉|台词|说|回复|dialogue/i.test(text)) {
      trace.push("actor_runtime");
    }
    trace.push("ui_events");
    return Array.from(new Set(trace));
  }

  function clearXrayNodeTraceAnimation() {
    state.xrayTraceTimers.forEach((timerId) => window.clearTimeout(timerId));
    state.xrayTraceTimers = [];
    state.xrayTraceAnimatingUntil = 0;
  }

  function applyXrayNodeClasses(visited, active) {
    if (!els.nodeTimeline) return;
    els.nodeTimeline.querySelectorAll("li[data-node]").forEach((item) => {
      const node = normalizeNodeName(item.dataset.node);
      item.classList.toggle("is-active", node === active);
      item.classList.toggle("is-visited", visited.includes(node));
    });
  }

  function setXrayNodeTrace(nodes, options = {}) {
    const normalized = safeArray(nodes).map(normalizeNodeName).filter(Boolean);
    if (!normalized.length) {
      clearXrayNodeTraceAnimation();
      applyXrayNodeClasses([], "");
      return;
    }

    const animate = options.animate === true;
    const now = Date.now();
    if (!animate && state.xrayTraceAnimatingUntil > now) {
      return;
    }

    clearXrayNodeTraceAnimation();
    if (!animate || normalized.length === 1) {
      applyXrayNodeClasses(normalized, normalized[normalized.length - 1] || "");
      return;
    }

    const stepMs = state.qaTraceStepMs;
    state.xrayTraceAnimatingUntil = now + normalized.length * stepMs + 180;
    normalized.forEach((node, index) => {
      const timerId = window.setTimeout(() => {
        applyXrayNodeClasses(normalized.slice(0, index + 1), node);
      }, index * stepMs);
      state.xrayTraceTimers.push(timerId);
    });
    const finalTimer = window.setTimeout(() => {
      applyXrayNodeClasses(normalized, normalized[normalized.length - 1] || "");
      state.xrayTraceTimers = [];
      state.xrayTraceAnimatingUntil = 0;
    }, normalized.length * stepMs + 20);
    state.xrayTraceTimers.push(finalTimer);
  }

  function normalizePercent(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return null;
    const percent = num <= 1 ? num * 100 : num;
    return Math.max(0, Math.min(100, percent));
  }

  function readDynamicState(dynamicStates, key) {
    const states = safeObject(dynamicStates);
    const value = states[key] ?? states[key.toLowerCase()] ?? states[key.toUpperCase()];
    if (value && typeof value === "object") {
      const record = safeObject(value);
      const direct = record.value ?? record.current ?? record.percent ?? record.current_value;
      const max = record.max ?? record.max_value ?? record.maximum ?? record.cap;
      const currentNum = Number(direct);
      const maxNum = Number(max);
      if (Number.isFinite(currentNum) && Number.isFinite(maxNum) && maxNum > 0) {
        return currentNum / maxNum;
      }
      return direct;
    }
    return value;
  }

  function updateXrayMeter(bar, label, title, value) {
    const percent = normalizePercent(value);
    if (bar) bar.style.width = percent == null ? "0%" : percent.toFixed(0) + "%";
    if (label) label.textContent = percent == null ? "--" : percent.toFixed(0) + "%";
    if (title) title.textContent = title.textContent || "";
  }

  function stateLabelFromEntry(key, value, fallback) {
    const record = safeObject(value);
    return String(record.name || fallback || prettifyId(key));
  }

  function watcherContextActive(payload, gameState, options = {}) {
    const intent = safeObject(gameState.intent_context || payload.intent_context);
    const activeDialogueTarget = normalizeId(
      payload.active_dialogue_target
      || gameState.active_dialogue_target
      || intent.action_target
      || options.target
      || "",
    );
    if (activeDialogueTarget === "gatekeeper") return true;
    const uiEventTypes = safeArray(payload.ui_events || gameState.ui_events)
      .map((event) => normalizeId(safeObject(event).type));
    if (uiEventTypes.some((type) => (
      type === "negotiation_leverage"
      || type === "party_stance"
      || type === "mercy_resolution"
    ))) return true;
    const text = [
      options.userLine,
      options.intent,
      safeArray(payload.journal_events || gameState.journal_events).join(" "),
      JSON.stringify(intent || {}),
    ].join(" ");
    return /\bgatekeeper\b|守门人|\[交涉筹码\]|\[站队\]|\[抉择\]|gatekeeper_elixir_truth|patience|paranoia/i.test(text);
  }

  function setWatcherSectionVisible(visible) {
    const section = els.patienceBar && els.patienceBar.closest
      ? els.patienceBar.closest(".xray-section--state-watcher")
      : null;
    if (section) section.classList.toggle("is-hidden", !visible);
  }

  function resolveWatcherTarget(payload, gameState, options = {}) {
    if (!watcherContextActive(payload, gameState, options)) {
      return { targetId: "", entity: {}, dynamicStates: {}, inactive: true };
    }
    const entities = safeObject(gameState.entities || payload.entities);
    const activeDialogueTarget = normalizeId(
      payload.active_dialogue_target
      || gameState.active_dialogue_target
      || safeObject(gameState.intent_context).action_target
      || "",
    );

    const candidates = [];
    const pushCandidate = (id) => {
      const key = normalizeId(id);
      if (!key || candidates.includes(key) || !entities[key]) return;
      candidates.push(key);
    };

    pushCandidate(activeDialogueTarget);
    Object.keys(entities).forEach((id) => {
      if (/gatekeeper/.test(normalizeId(id)) || /gatekeeper/.test(normalizeId(safeObject(entities[id]).name))) {
        pushCandidate(id);
      }
    });
    Object.keys(entities).forEach((id) => {
      const dynamicStates = safeObject(safeObject(entities[id]).dynamic_states || safeObject(entities[id]).dynamicStates);
      if (readDynamicState(dynamicStates, "patience") != null || readDynamicState(dynamicStates, "fear") != null) {
        pushCandidate(id);
      }
    });
    Object.keys(entities).forEach((id) => {
      const dynamicStates = safeObject(safeObject(entities[id]).dynamic_states || safeObject(entities[id]).dynamicStates);
      if (Object.keys(dynamicStates).length) pushCandidate(id);
    });

    const targetId = candidates[0] || "";
    return {
      targetId,
      entity: safeObject(entities[targetId]),
      dynamicStates: safeObject(
        safeObject(entities[targetId]).dynamic_states || safeObject(entities[targetId]).dynamicStates
      ),
    };
  }

  function resolveWatcherEntries(dynamicStates) {
    const states = safeObject(dynamicStates);
    const patienceValue = readDynamicState(states, "patience");
    const fearValue = readDynamicState(states, "fear");

    if (patienceValue != null || fearValue != null) {
      return {
        primary: {
          label: stateLabelFromEntry("patience", states.patience, "耐心 Patience"),
          value: patienceValue,
        },
        secondary: {
          label: stateLabelFromEntry("fear", states.fear, "恐惧 Fear"),
          value: fearValue,
        },
      };
    }

    const fallbackEntries = Object.entries(states)
      .map(([key, value]) => ({
        key,
        label: stateLabelFromEntry(key, value, prettifyId(key)),
        value: readDynamicState(states, key),
      }))
      .filter((entry) => entry.value != null);

    return {
      primary: fallbackEntries[0] || { label: "耐心 Patience", value: null },
      secondary: fallbackEntries[1] || { label: "恐惧 Fear", value: null },
    };
  }

  function updateXrayPanel(data, options = {}) {
    if (!els.jsonInspector) return;
    const payload = safeObject(data);
    const gameState = safeObject(payload.game_state || payload.gameState || payload);
    const watcher = resolveWatcherTarget(payload, gameState, options);
    const watcherEntries = resolveWatcherEntries(watcher.dynamicStates);
    setWatcherSectionVisible(!watcher.inactive && Boolean(watcher.targetId));

    if (els.patienceLabel) els.patienceLabel.textContent = watcherEntries.primary.label;
    if (els.fearLabel) els.fearLabel.textContent = watcherEntries.secondary.label;
    updateXrayMeter(els.patienceBar, els.patienceValue, els.patienceLabel, watcherEntries.primary.value);
    updateXrayMeter(els.fearBar, els.fearValue, els.fearLabel, watcherEntries.secondary.value);

    const trace = options.trace || inferNodeTrace(payload, options.userLine || "", options.intent || "");
    setXrayNodeTrace(trace, { animate: options.animateTrace === true });
    const currentTimings = resolveNodeTimings(payload, gameState);
    if (Object.keys(currentTimings).length) {
      state.xrayNodeTimings = {
        ...state.xrayNodeTimings,
        ...currentTimings,
      };
    }
    updateXrayNodeTimings(state.xrayNodeTimings);

    const inspectorPayload = gameState === payload
      ? payload
      : {
          last_node: payload.last_node || gameState.last_node || gameState.current_node || null,
          node_trace: trace,
          node_timings_ms: state.xrayNodeTimings,
          watcher_target: watcher.targetId || null,
          intent_context: gameState.intent_context || payload.intent_context || null,
          active_dialogue_target: payload.active_dialogue_target || gameState.active_dialogue_target || null,
          entities: gameState.entities || null,
          combat_state: payload.combat_state || gameState.combat_state || null,
          journal_events: payload.journal_events || gameState.journal_events || [],
        };
    renderPayloadSummary(payload, gameState, trace, watcher, options);
    els.jsonInspector.textContent = JSON.stringify(inspectorPayload, null, 2);
  }

  function renderInitiativeTracker(combatState, wasCombatActive) {
    const combat = safeObject(combatState);
    const isCombatActive = isCombatStateActive(combat);
    const order = safeArray(combat.initiative_order).map(normalizeId).filter(Boolean);
    const currentIndex = Number(combat.current_turn_index);
    const activeIndex = Number.isFinite(currentIndex) ? currentIndex : -1;

    if (!els.initiativeTracker || !els.initiativeList) return;

    els.initiativeTracker.classList.toggle("is-hidden", !isCombatActive);
    els.initiativeTracker.classList.toggle("is-active", isCombatActive);
    els.initiativeTracker.setAttribute("aria-hidden", String(!isCombatActive));
    els.initiativeList.innerHTML = "";

    if (!isCombatActive) {
      if (wasCombatActive) {
        appendLogEntry("system", "战斗结束", "战斗态势解除，先攻顺位条已收起。", {
          color: "#73c6c3",
          sigil: "◇",
          logType: "system",
        });
        if (window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.playVictoryBanner === "function") {
          window.ControlledAgentTacticalMap.playVictoryBanner();
        }
      }
      return;
    }

    if (!wasCombatActive) {
      appendLogEntry("combat", "战斗开始", "⚔ 战斗开始！先攻顺位已锁定。", {
        color: "#ff8a7a",
        sigil: "⚔",
        logType: "system",
      });
    }

    if (order.length === 0) {
      const empty = document.createElement("span");
      empty.className = "initiative-empty";
      empty.textContent = "等待先攻数据...";
      els.initiativeList.appendChild(empty);
      return;
    }

    const fragment = document.createDocumentFragment();
    const activeCombatantId = order[activeIndex] || "";
    const shouldShowResources = hasTurnResourcesFor(activeCombatantId) && !isHostileCombatant(activeCombatantId);

    order.forEach((id, index) => {
      const chip = document.createElement("div");
      chip.className = "initiative-chip";
      chip.classList.toggle("active-turn", index === activeIndex);
      chip.dataset.combatantId = id;

      const avatarStack = document.createElement("span");
      avatarStack.className = "initiative-avatar-stack";

      const avatar = document.createElement("span");
      avatar.className = "initiative-avatar";
      avatar.textContent = getCombatantSigil(id);
      avatarStack.appendChild(avatar);

      if (shouldShowResources && hasTurnResourcesFor(id) && !isHostileCombatant(id)) {
        avatarStack.appendChild(createInitiativeResourceDots(id));
      }

      const label = document.createElement("span");
      label.className = "initiative-name";
      label.textContent = getCombatantLabel(id);

      chip.appendChild(avatarStack);
      chip.appendChild(label);
      fragment.appendChild(chip);
    });

    els.initiativeList.appendChild(fragment);
  }

  function createPartyCard(id, rawData) {
    const data = safeObject(rawData);
    const isPlayer = normalizeId(id) === "player";
    const card = document.createElement("article");
    card.className = "party-card";

    const meta = getSpeakerMeta(id);
    const avatar = document.createElement("div");
    avatar.className = "avatar-medallion";
    avatar.textContent = getInitials(id);
    avatar.style.background = "radial-gradient(circle at 30% 30%, " + meta.color + ", #101319 72%)";

    const content = document.createElement("div");

    const head = document.createElement("div");
    head.className = "party-card-head";

    const headText = document.createElement("div");
    const name = document.createElement("h3");
    name.textContent = getDisplayName(id);
    name.style.color = meta.color;
    const role = document.createElement("p");
    role.className = "party-role";
    role.textContent = "位置 · " + formatLocation(data.position || "camp_center");

    headText.appendChild(name);
    headText.appendChild(role);

    head.appendChild(headText);
    if (!isPlayer) {
      const affinity = document.createElement("span");
      affinity.className = "status-pill";
      affinity.textContent = affectionLabel(Number(data.affection));
      head.appendChild(affinity);
    }

    const bars = document.createElement("div");
    bars.className = "party-bars";

    const rawHp = Number(data.hp);
    const maxHp = Number(data.max_hp || 20);
    const hp = Number.isFinite(rawHp) ? Math.min(rawHp, maxHp) : rawHp;
    const aff = Number(data.affection);

    bars.appendChild(createMeter("HP", Number.isFinite(hp) ? hp + " / " + maxHp : "—", hpPercent(hp, maxHp), false));
    if (!isPlayer) {
      bars.appendChild(
        createMeter(
          "好感度",
          Number.isFinite(aff) ? String(aff) : "—",
          affectionPercent(aff),
          true
        )
      );
    }

    content.appendChild(head);
    content.appendChild(bars);
    const resourcesPanel = createTurnResourcesPanel(normalizeId(id), data);
    if (resourcesPanel) {
      content.appendChild(resourcesPanel);
    }
    content.appendChild(createEquipmentPanel(normalizeId(id), data.equipment));

    card.appendChild(avatar);
    card.appendChild(content);
    return card;
  }

  function renderPartyRoster() {
    const party = safeObject(state.partyStatus);
    const companionEntries = objectEntries(party)
      .filter(([id]) => normalizeId(id) !== "player")
      .sort(([leftId], [rightId]) => leftId.localeCompare(rightId));

    els.partyCount.textContent = companionEntries.length + 1 + " 名单位";
    els.partyRoster.innerHTML = "";

    const fragment = document.createDocumentFragment();
    fragment.appendChild(createPartyCard("player", playerViewData()));

    if (companionEntries.length === 0) {
      els.partyRoster.appendChild(fragment);
      return;
    }

    companionEntries.forEach(([id, data]) => {
      fragment.appendChild(createPartyCard(id, data));
    });

    els.partyRoster.appendChild(fragment);
  }

  function createMeter(label, value, percent, isAffection) {
    const wrap = document.createElement("div");
    wrap.className = "meter";

    const head = document.createElement("div");
    head.className = "meter-head";

    const left = document.createElement("span");
    left.textContent = label;
    const right = document.createElement("span");
    right.textContent = value;
    head.appendChild(left);
    head.appendChild(right);

    const track = document.createElement("div");
    track.className = "meter-track";
    const fill = document.createElement("div");
    if (isAffection) {
      track.classList.add("meter-track--bipolar");
      fill.className = "meter-cursor";
      fill.style.left = percent + "%";
    } else {
      fill.className = "meter-fill";
      fill.style.width = percent + "%";
    }
    track.appendChild(fill);

    wrap.appendChild(head);
    wrap.appendChild(track);
    return wrap;
  }

  function makeItemTag(text, icon) {
    const tag = document.createElement("span");
    tag.className = "item-tag";
    tag.textContent = icon + " " + text;
    return tag;
  }

  function renderEnvironmentObjects() {
    const host = els.environmentList;
    const entries = Object.entries(safeObject(state.environmentObjects)).filter(
      ([, value]) => value && typeof value === "object" && !Array.isArray(value)
    ).sort(([leftId], [rightId]) => leftId.localeCompare(rightId));

    els.environmentCount.textContent = entries.length + " 个对象";
    host.innerHTML = "";

    if (entries.length === 0) {
      host.appendChild(createEmptyState("当前房间没有可感知的环境对象。"));
      return;
    }

    const fragment = document.createDocumentFragment();

    entries.forEach(([id, rawData]) => {
      const data = safeObject(rawData);
      const card = document.createElement("article");
      card.className = "environment-card env-object";

      const head = document.createElement("div");
      head.className = "environment-card-head";

      const left = document.createElement("div");
      const title = document.createElement("h4");
      title.textContent = data.name || prettifyId(id);
      const keyLine = document.createElement("p");
      keyLine.className = "environment-desc";
      keyLine.textContent = "ID · " + id;
      left.appendChild(title);
      left.appendChild(keyLine);

      const status = document.createElement("span");
      status.className = "status-pill";
      status.textContent = String(data.status || "unknown");

      head.appendChild(left);
      head.appendChild(status);

      const desc = document.createElement("p");
      desc.className = "environment-desc";
      desc.textContent = data.description || "没有可用描述。";

      const lootWrap = document.createElement("div");
      lootWrap.className = "environment-loot";
      const lootEntries = Object.entries(safeObject(data.inventory)).filter(([, count]) => Number(count) > 0);
      if (lootEntries.length === 0) {
        lootWrap.appendChild(makeItemTag("无可拾取物", "·"));
      } else {
        lootEntries.forEach(([itemId, count]) => {
          const metaItem = itemMeta(itemId);
          lootWrap.appendChild(makeItemTag(metaItem.icon + " " + itemId + " x " + count, "·"));
        });
      }

      const actions = document.createElement("div");
      actions.className = "environment-actions";

      const inspectBtn = document.createElement("button");
      inspectBtn.type = "button";
      inspectBtn.className = "object-action";
      inspectBtn.dataset.command = "检查" + (data.name || prettifyId(id));
      inspectBtn.dataset.targetId = id;
      inspectBtn.dataset.targetType = String(data.type || data.entity_type || "");
      inspectBtn.dataset.targetLabel = String(data.name || prettifyId(id));
      inspectBtn.dataset.targetName = String(data.name || prettifyId(id));
      inspectBtn.textContent = "检查";
      actions.appendChild(inspectBtn);

      if (canLootTarget(data)) {
        const lootBtn = document.createElement("button");
        lootBtn.type = "button";
        lootBtn.className = "object-action";
        lootBtn.dataset.loot = "true";
        lootBtn.dataset.targetId = id;
        lootBtn.textContent = "搜刮";
        actions.appendChild(lootBtn);
      }

      card.appendChild(head);
      card.appendChild(desc);
      card.appendChild(lootWrap);
      card.appendChild(actions);
      fragment.appendChild(card);
    });

    host.appendChild(fragment);
  }

  function renderLootItems(environmentObjects, targetId) {
    const env = safeObject(environmentObjects);
    const normalizedTargetId = normalizeId(targetId);
    const target = safeObject(env[normalizedTargetId]);
    const targetName = target.name || prettifyId(normalizedTargetId);
    const items = inventoryEntries(target.inventory);
    els.lootItems.innerHTML = "";
    els.lootTitle.textContent = "搜刮: " + targetName;

    if (items.length === 0) {
      els.lootItems.appendChild(createEmptyState(targetName + " 已经被搬空。"));
      return;
    }

    items.forEach(([itemId, count]) => {
      const meta = itemMeta(itemId);
      const card = document.createElement("div");
      card.className = "loot-item";

      const icon = document.createElement("div");
      icon.className = "inventory-slot-icon";
      icon.textContent = meta.icon;

      const name = document.createElement("h4");
      name.textContent = meta.label;

      const amount = document.createElement("p");
      amount.textContent = "ID " + itemId + " · x" + count;

      card.appendChild(icon);
      card.appendChild(name);
      card.appendChild(amount);
      els.lootItems.appendChild(card);
    });
  }

  function showLootModal() {
    setTacticalOverlay(true);
    els.lootModal.classList.remove("hidden");
    els.lootModal.setAttribute("aria-hidden", "false");
  }

  function hideLootModal() {
    els.lootModal.classList.add("hidden");
    els.lootModal.setAttribute("aria-hidden", "true");
  }

  function openLootModalForTarget(targetId) {
    const normalizedTargetId = normalizeId(targetId);
    if (!normalizedTargetId) return;
    state.currentLootTargetId = normalizedTargetId;
    renderLootItems(state.environmentObjects, normalizedTargetId);
    showLootModal();
  }

  function maybeShowLootModal(environmentObjects, context = {}) {
    const intent = normalizeId(safeObject(context).intent);
    if (intent !== "ui_action_loot") return;
    const env = safeObject(environmentObjects);
    const eligibleTarget = Object.entries(env).find(([id, rawData]) => {
      return !state.seenLootTargets.has(normalizeId(id)) && canLootTarget(rawData);
    });
    if (!eligibleTarget) return;
    openLootModalForTarget(eligibleTarget[0]);
  }

  function updateWorldLog(data, userLine) {
    if (userLine) {
      const playerMeta = getSpeakerMeta("player");
      appendLogEntry("player", "你", userLine, {
        color: playerMeta.color,
        sigil: playerMeta.sigil,
        logType: "dialogue",
      });
    }

    (data.responses || []).forEach((response) => {
      const speaker = normalizeId(response.speaker || "npc");
      const meta = getSpeakerMeta(speaker);
      appendLogEntry("npc", getDisplayName(speaker) + " · " + speaker, response.text || "", {
        color: meta.color,
        sigil: meta.sigil,
        logType: "dialogue",
      });
    });

    (data.journal_events || []).forEach((line) => {
      const kind = describeLogKind(line);
      appendLogEntry(kind, kind === "roll" ? "命运检定" : "系统裁定", line, {
        color: kind === "roll" ? "#73c6c3" : "#d0ab67",
        sigil: kind === "roll" ? "🎲" : "◎",
        logType: kind === "roll" ? "narration" : "system",
      });
    });
  }

  function setLoading(loading) {
    state.isLoading = loading;
    if (loading) {
      stopSpeechRecognition();
    }
    els.userInput.disabled = loading;
    els.sendBtn.disabled = loading;
    els.sendBtn.textContent = loading ? "命运演算中…" : "执行指令";
    if (els.shortRestBtn) els.shortRestBtn.disabled = loading;
    if (els.longRestBtn) els.longRestBtn.disabled = loading;
    if (els.dialogueInput) els.dialogueInput.disabled = loading;
    if (els.pttMicBtn) els.pttMicBtn.disabled = loading || !state.speechRecognitionSupported;
    if (els.dialogueSendBtn) els.dialogueSendBtn.disabled = loading;
    if (els.dialogueAttackBtn) els.dialogueAttackBtn.disabled = loading;
    els.shortcutButtons.forEach((button) => {
      button.disabled = loading;
    });
    Array.from(document.querySelectorAll(".object-action")).forEach((button) => {
      button.disabled = loading;
    });
    Array.from(document.querySelectorAll(".item-action")).forEach((button) => {
      button.disabled = loading;
    });
    els.lootAllBtn.disabled = loading;
    if (loading) {
      setNetworkState("命运演算中", "loading");
    }
  }

  async function fetchWithTimeout(url, options = {}, timeoutMs = BACKEND_REQUEST_TIMEOUT_MS) {
    const startedAt = (typeof performance !== "undefined" && typeof performance.now === "function")
      ? performance.now()
      : Date.now();
    const controller = new AbortController();
    const timerId = window.setTimeout(() => controller.abort(), Math.max(0, Number(timeoutMs) || 0));
    try {
      const requestOptions = { ...(options || {}), signal: controller.signal };
      const response = await fetch(url, requestOptions);
      const endedAt = (typeof performance !== "undefined" && typeof performance.now === "function")
        ? performance.now()
        : Date.now();
      state.mapDebugLastFetch = {
        url: String(url || ""),
        duration_ms: Math.round(Math.max(0, endedAt - startedAt)),
        status: Number(response.status) || 0,
        ok: response.ok === true,
        error: null,
      };
      updateMapDebug("fetch:ok");
      return response;
    } catch (error) {
      const endedAt = (typeof performance !== "undefined" && typeof performance.now === "function")
        ? performance.now()
        : Date.now();
      state.mapDebugLastFetch = {
        url: String(url || ""),
        duration_ms: Math.round(Math.max(0, endedAt - startedAt)),
        status: 0,
        ok: false,
        error: {
          name: String(error && error.name || ""),
          message: String(error && error.message || ""),
        },
      };
      updateMapDebug("fetch:error");
      throw error;
    } finally {
      window.clearTimeout(timerId);
    }
  }

  function buildSilentFallbackPayload(userLine, intentValue) {
    const location = String((els.currentLocation && els.currentLocation.textContent) || "未知区域").trim() || "未知区域";
    const fallbackTrace = ["player_input", "dm_router", "actor_view_filter", "ui_events"];
    const fallbackTimings = {
      player_input: 0,
      dm_router: BACKEND_REQUEST_TIMEOUT_MS,
      actor_view_filter: 0,
      ui_events: 0,
    };
    return {
      responses: [],
      journal_events: [SILENT_FALLBACK_TEXT],
      current_location: location,
      party_status: state.partyStatus,
      environment_objects: state.environmentObjects,
      player_inventory: state.playerInventory,
      combat_state: state.combatState,
      last_node: "generation",
      node_trace: fallbackTrace,
      node_timing_map: fallbackTimings,
      game_state: {
        last_node: "generation",
        intent_context: {
          action_actor: "player",
          action_target: "",
          fallback_intent: normalizeId(intentValue || "fallback"),
          fallback_reason: "network_timeout_or_unavailable",
        },
        entities: state.partyStatus,
        combat_state: state.combatState,
      },
      _local_fallback: true,
      _fallback_trace: fallbackTrace,
      _fallback_user_line: userLine || "",
      _fallback_intent: intentValue || "",
    };
  }

  function applySilentNetworkFallback(userLine, intentValue, options = {}) {
    const data = buildSilentFallbackPayload(userLine, intentValue);
    const trace = safeArray(data._fallback_trace);
    const opts = options && typeof options === "object" ? options : {};
    if (opts.incrementTurn !== false) {
      state.turnCount += 1;
      if (els.turnCounter) els.turnCounter.textContent = padTurn(state.turnCount);
    }
    updateXrayPanel(data, {
      userLine: data._fallback_user_line,
      intent: data._fallback_intent,
      trace,
      animateTrace: true,
    });
    if (!opts.skipLogUpdate) {
      updateWorldLog(data, userLine || null);
    }
    setNetworkState("链路在线", "ok");
    return data;
  }

  function resetIdleTimer() {
    if (IS_QA_MODE || QA_NO_IDLE) return;
    window.clearTimeout(state.idleTimer);
    state.idleTimer = window.setTimeout(() => {
      sendMessage("", "trigger_idle_banter");
    }, IDLE_MS);
  }

  function ensureAct2CorridorTrapInsightEvent(uiEvents, actionIntent, actionTarget, actionSource) {
    const events = safeArray(uiEvents).slice();
    if (events.some((event) => normalizeId(safeObject(event).type) === "trap_insight")) return events;
    if (normalizeId(actionIntent) !== "chat") return events;
    if (normalizeId(actionTarget) !== "gas_trap_1") return events;
    if (normalizeId(actionSource) !== "trap_awareness") return events;
    if (!shouldQueueTrapAwareness()) return events;
    events.push({
      type: "trap_insight",
      actor: "scout",
      trapId: "gas_trap_1",
      source: "trap_awareness",
    });
    return events;
  }

  async function sendStructuredAction(action = {}) {
    const descriptor = action && typeof action === "object" ? action : {};
    const options = descriptor.options && typeof descriptor.options === "object" ? descriptor.options : {};
    const opts = options && typeof options === "object" ? options : {};
    const text = descriptor.text;
    const intent = descriptor.intent;
    const character = descriptor.character;
    const rawText = String(text == null ? "" : text).trim();
    if (IS_QA_MODE && rawText.toLowerCase().startsWith("qa_")) {
      const qaLocalHandled = handleQaLocalCommand(rawText, "qa_local");
      if (qaLocalHandled) {
        return { ok: true, qa_local: true };
      }
    }

    const built = buildChatPayload(text, intent, character, opts);
    const payload = built.payload;
    const routed = built.routed;
    const userLine = routed.userLine;
    const intentValue = routed.intentValue;

    if (!userLine && !intentValue) {
      return;
    }

    const qaLocalHandled = handleQaLocalCommand(userLine, intentValue);
    if (qaLocalHandled) {
      return { ok: true, qa_local: true };
    }

    if (normalizeId(intentValue) !== "init_sync") {
      state.barkEpoch = (Number(state.barkEpoch) || 0) + 1;
    }
    if (isAct3BarkRequest(routed, payload)) {
      hardClearAct3TrapBarks("act3_request");
    }
    syncLocalPlayerProjectionState("client_before_narrative");
    setLoading(true);
    state.xrayNodeTimings = {};
    updateXrayNodeTimings(state.xrayNodeTimings);

    /* Activate Director Trace only for narrative requests */
    const _isNarrative = isNarrativeRequest(intentValue, userLine, routed.source);
    if (_isNarrative && window.ControlledAgentDirectorTrace && typeof window.ControlledAgentDirectorTrace.setPending === "function") {
      window.ControlledAgentDirectorTrace.setPending({ userLine, intent: intentValue });
    }
    const previousShowcaseSnapshot = buildShowcaseSnapshot();
    rememberTransientInteractionContext(intentValue, routed.target, routed.source);
    const shouldClearReadContext = normalizeId(intentValue) === "read";

    try {
      const response = await fetchWithTimeout(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }, BACKEND_REQUEST_TIMEOUT_MS);

      if (!response.ok) {
        return applySilentNetworkFallback(userLine, intentValue, opts);
      }

      const data = await response.json();
      state.lastProjectionJournalEvents = extractEventLines(data);
      if (opts.incrementTurn !== false) {
        state.turnCount += 1;
      }
      const previousWorldFlagsSnapshot = { ...safeObject(state.worldFlags) };
      refreshWorldFlags(data);
      const wasCombatActive = isCombatStateActive(state.combatState);
      const prevPartySnapshot = { ...state.partyStatus };
      const prevEnvironmentSnapshot = { ...state.environmentObjects };
      const prevInventorySnapshot = { ...state.playerInventory };
      const previousUIEventState = {
        party_status: prevPartySnapshot,
        environment_objects: prevEnvironmentSnapshot,
        player_inventory: prevInventorySnapshot,
        combat_state: state.combatState,
        flags: previousWorldFlagsSnapshot,
      };
      state.partyStatus = mergePartyStatusResponse(prevPartySnapshot, data.party_status);
      state.environmentObjects = safeObject(data.environment_objects);
      state.playerInventory = safeObject(data.player_inventory);
      state.combatState = safeObject(data.combat_state);
      const responseMapData = safeObject(data.map_data);
      state.mapData = Object.keys(responseMapData).length
        ? responseMapData
        : safeObject(state.combatState.map_data);
      if (state.normalizedMap) {
        state.mapData = mergeVisualMapData(state.mapData, state.normalizedMap);
      }
      updateMapDebug("sendStructuredAction:response");
      const actionIntent = normalizeId(payload.intent);
      const actionTarget = normalizeId(payload.target || routed.target);
      if (actionIntent === "init_sync") {
        state.hasStateProjectionBaseline = true;
      }
      applyLocalAct3ReadProjection(actionIntent, actionTarget, data);
      if (actionIntent === "interact" && !responseIndicatesInteractionBlocked(data)) {
        if (actionTarget === "door_b_to_c" && !state.discoveredSecretDoorIds.has("door_b_to_c")) {
          discoverSecretDoor("door_b_to_c");
        }
        if (revealRoomByDoorTarget(actionTarget)) {
          refreshVisibilityProjection();
        }
      }
      updateDialogueOverlay(data);

      if (data.current_location) {
        renderChrome(data.current_location);
      } else {
        renderChrome(els.currentLocation.textContent);
      }

      renderPartyRoster();
      renderEnvironmentObjects();
      if (state.partyViewOpen) {
        renderPartyView();
      }
      renderTacticalGrid(state.partyStatus, state.environmentObjects, state.mapData);
      renderInitiativeTracker(state.combatState, wasCombatActive);
      updateRestControls(state.combatState);
      updateExplorationActProgress();
      let uiEvents = window.ControlledAgentUIEventAdapter
        ? window.ControlledAgentUIEventAdapter.extractUIEvents(data, previousUIEventState, {
          suppressInventoryDeltas: actionIntent === "init_sync",
        })
        : [];
      uiEvents = ensureAct2CorridorTrapInsightEvent(uiEvents, actionIntent, actionTarget, routed.source);
      const trace = window.ControlledAgentDirectorTrace && typeof window.ControlledAgentDirectorTrace.buildTraceNodes === "function"
        ? window.ControlledAgentDirectorTrace.buildTraceNodes(data, { userLine, intent: intentValue, uiEvents })
        : inferNodeTrace(data, userLine, intentValue);
      updateXrayPanel(data, {
        userLine,
        intent: intentValue,
        trace,
        animateTrace: true,
      });

      /* Director Trace lifecycle: activateTrace on narrative response */
      if (_isNarrative && window.ControlledAgentDirectorTrace && typeof window.ControlledAgentDirectorTrace.activateTrace === "function") {
        window.ControlledAgentDirectorTrace.activateTrace(trace, {
          animate: true,
          data,
          userLine,
          intent: intentValue,
          uiEvents,
          timings: resolveNodeTimings(data, safeObject(data.game_state)),
          autoIdleMs: QA_NO_IDLE ? 999999 : undefined,
        });
      }

      if (window.ControlledAgentStateDiffRenderer && typeof window.ControlledAgentStateDiffRenderer.update === "function") {
        if (normalizeId(intentValue) === "init_sync") {
          recordShowcaseBaseline(data);
        } else {
          let diffData = data;
          if (QA_SHOWCASE || QA_MAP_DEBUG) {
            const liveState = await fetchShowcaseStateSnapshot();
            if (liveState) {
              diffData = {
                ...data,
                game_state: liveState,
                flags: liveState.flags,
                actor_runtime_state: liveState.actor_runtime_state,
                demo_cleared: data.demo_cleared === true || liveState.demo_cleared === true,
              };
              if (window.ControlledAgentUIEventAdapter) {
                const enrichedEvents = window.ControlledAgentUIEventAdapter.extractUIEvents(
                  diffData,
                  previousShowcaseSnapshot
                );
                enrichedEvents.forEach((event) => {
                  if (!event || !["negotiation_leverage", "memory_echo", "party_stance", "mercy_resolution"].includes(event.type)) return;
                  const exists = uiEvents.some((candidate) => {
                    if (!candidate || candidate.type !== event.type) return false;
                    if (event.type === "negotiation_leverage") {
                      return candidate.evidence === event.evidence
                        && candidate.targetId === event.targetId
                        && candidate.pressure === event.pressure;
                    }
                    if (event.type === "party_stance") {
                      return candidate.target === event.target;
                    }
                    if (event.type === "mercy_resolution") {
                      return candidate.target === event.target && candidate.result === event.result;
                    }
                    return candidate.actor === event.actor && candidate.memoryType === event.memoryType;
                  });
                  if (!exists) uiEvents.push(event);
                });
              }
            }
          }
          updateWorldStateDiff(previousShowcaseSnapshot, buildShowcaseSnapshot(diffData), {
            autoExpand: _isNarrative || normalizeId(intentValue) === "ui_action_loot",
          });
        }
      }

      /* Dispatch HUD UI events from response (#1) */
      let dispatchedEvents = [];
      if (intentValue.toLowerCase() !== "init_sync") {
        dispatchedEvents = dispatchUIEventsFromResponse(data, previousUIEventState, uiEvents) || [];
        if (safeArray(dispatchedEvents).some((event) => normalizeId(safeObject(event).type).startsWith("trap_"))) {
          refreshVisibilityProjection();
          renderTacticalGrid(state.partyStatus, state.environmentObjects, state.mapData);
        }
      }

      if (!opts.skipLogUpdate) {
        updateWorldLog(data, userLine || null);
        triggerMapTransitionEffects(data);
        triggerRestVisualEffects(data, intentValue);
        triggerCombatVisualEffects(data, userLine || "");
      }
      if (intentValue.toLowerCase() !== "init_sync") {
        triggerSpeechBubbles(data, { dispatchedEvents, skipUIEvents: true });
      }
      maybeShowLootModal(data.environment_objects, { intent: intentValue });
      setNetworkState("链路在线", "ok");
      return data;
    } catch (error) {
      /* Director Trace: reset to idle on error */
      if (_isNarrative && window.ControlledAgentDirectorTrace && typeof window.ControlledAgentDirectorTrace.setIdle === "function") {
        window.ControlledAgentDirectorTrace.setIdle();
      }
      if (error && error.name === "AbortError") {
        return applySilentNetworkFallback(userLine, intentValue, opts);
      }
      return applySilentNetworkFallback(userLine, intentValue, opts);
    } finally {
      if (shouldClearReadContext) {
        clearTransientInteractionContext({ keepDialogueTarget: true });
      }
      setLoading(false);
      if (opts.skipIdleReset !== true) {
        resetIdleTimer();
      }
    }
  }

  function handleQaLocalCommand(userLine, intentValue) {
    if (!IS_QA_MODE) return false;
    if (intentValue && normalizeId(intentValue) !== "qa_local") return false;
    const text = String(userLine || "").trim();
    if (!text) return false;
    if (!text.toLowerCase().startsWith("qa_")) return false;

    const parts = text.split(/\s+/).filter(Boolean);
    const command = normalizeId(parts[0]);
    const arg1 = parts[1] || "";
    const arg2 = parts[2] || "";
    const defaultRoomSpawn = {
      room_a_spawn: { x: 4, y: 18 },
      room_b_corridor: { x: 8, y: 18 },
      room_c_secret_study: { x: 12, y: 16 },
      room_d_lab: { x: 17, y: 14 },
      room_exit: { x: 21, y: 12 },
    };

    const applyLocalRender = () => {
      refreshVisibilityProjection();
      renderTacticalGrid(state.partyStatus, state.environmentObjects, state.mapData);
      updateMapDebug("qa_local");
    };

    const setLocalPlayer = (x, y) => {
      if (
        window.ControlledAgentInputController
        && typeof window.ControlledAgentInputController.setPlayerPosition === "function"
        && Number.isFinite(Number(x))
        && Number.isFinite(Number(y))
      ) {
        window.ControlledAgentInputController.setPlayerPosition(Number(x), Number(y));
      }
    };

    if (command === "qa_open" || command === "qa_reveal") {
      const target = normalizeId(arg1);
      if (!target) return false;
      if (target === "door_b_to_c") {
        discoverSecretDoor("door_b_to_c");
      }
      if (revealRoomByDoorTarget(target)) {
        if (target === "door_a_to_b") setLocalPlayer(defaultRoomSpawn.room_b_corridor.x, defaultRoomSpawn.room_b_corridor.y);
        if (target === "door_b_to_c") setLocalPlayer(defaultRoomSpawn.room_c_secret_study.x, defaultRoomSpawn.room_c_secret_study.y);
        if (target === "door_b_to_d") setLocalPlayer(defaultRoomSpawn.room_d_lab.x, defaultRoomSpawn.room_d_lab.y);
        if (target === "exit_door" || target === "heavy_oak_door_1") setLocalPlayer(defaultRoomSpawn.room_exit.x, defaultRoomSpawn.room_exit.y);
        applyLocalRender();
        return true;
      }
      if (defaultRoomSpawn[target]) {
        revealRoom(target);
        setLocalPlayer(defaultRoomSpawn[target].x, defaultRoomSpawn[target].y);
        applyLocalRender();
        return true;
      }
      return false;
    }

    if (command === "qa_perception") {
      resolveAct1Perception();
      applyLocalRender();
      return true;
    }

    if (command === "qa_move") {
      const x = Number(arg1);
      const y = Number(arg2);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
      setLocalPlayer(x, y);
      applyLocalRender();
      return true;
    }

    return false;
  }

  function runShowcaseLocalStep(commandText, options = {}) {
    if (!QA_SHOWCASE) return false;
    const before = buildShowcaseSnapshot();
    const handled = handleQaLocalCommand(commandText, "qa_local");
    if (!handled) return false;
    const after = buildShowcaseSnapshot({
      journal_events: [String(safeObject(options).title || commandText || "showcase local step")],
    });
    updateWorldStateDiff(before, after, { autoExpand: true });
    if (window.ControlledAgentDirectorTrace && typeof window.ControlledAgentDirectorTrace.activateTrace === "function") {
      const text = String(commandText || "");
      const localData = {
        journal_events: [
          text.includes("qa_perception")
            ? "trap discovered via local showcase perception"
            : "visibleRooms reveal via local showcase command",
        ],
      };
      const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes(localData, {
        userLine: text,
        intent: "qa_local",
      });
      window.ControlledAgentDirectorTrace.activateTrace(nodes, {
        data: localData,
        userLine: text,
        intent: "qa_local",
        animate: true,
      });
    }
    return true;
  }

  function completeShowcaseLocally(reason = "frontend_showcase_completion") {
    if (!QA_SHOWCASE) return false;
    const before = buildShowcaseSnapshot();
    const completionData = {
      demo_cleared: true,
      journal_events: ["DEMO CLEARED · " + String(reason || "showcase")],
    };
    if (window.ControlledAgentHudRenderers && typeof window.ControlledAgentHudRenderers.dispatchUIEvents === "function") {
      window.ControlledAgentHudRenderers.dispatchUIEvents([{ type: "demo_cleared" }]);
    }
    updateWorldStateDiff(before, buildShowcaseSnapshot(completionData), { autoExpand: true, collapseAfterMs: 8000 });
    if (window.ControlledAgentDirectorTrace && typeof window.ControlledAgentDirectorTrace.activateTrace === "function") {
      const nodes = ["player_input", "dm_router", "actor_view_filter", "domain_event", "event_drain", "ui_events"];
      window.ControlledAgentDirectorTrace.activateTrace(nodes, {
        data: completionData,
        userLine: "open exit_door",
        intent: "INTERACT",
        uiEvents: [{ type: "demo_cleared" }],
        animate: true,
        autoIdleMs: 7000,
      });
    }
    return true;
  }

  function ensureShowcaseControls() {
    if (!QA_SHOWCASE) return null;
    if (state.demoScriptControls && document.body.contains(state.demoScriptControls)) return state.demoScriptControls;
    const host = document.getElementById("game-viewport") || document.body;
    const wrap = document.createElement("div");
    wrap.id = "showcase-controls";
    wrap.className = "showcase-controls";
    wrap.innerHTML =
      '<div class="showcase-controls-title">Demo Showcase</div>' +
      '<button type="button" id="run-demo-script-btn" class="showcase-btn showcase-btn--run">Run Demo Script</button>' +
      '<button type="button" id="stop-demo-script-btn" class="showcase-btn showcase-btn--stop" disabled>Stop</button>' +
      '<span id="demo-script-status" class="showcase-status">ready</span>';
    host.appendChild(wrap);
    state.demoScriptControls = wrap;
    return wrap;
  }

  function initShowcaseMode() {
    if (window.ControlledAgentStateDiffRenderer && typeof window.ControlledAgentStateDiffRenderer.ensurePanel === "function") {
      window.ControlledAgentStateDiffRenderer.ensurePanel();
    }
    recordShowcaseBaseline();
    if (!QA_SHOWCASE) return;
    const controls = ensureShowcaseControls();
    if (!controls || !window.ControlledAgentDemoScriptRunner) return;
    const runBtn = controls.querySelector("#run-demo-script-btn");
    const stopBtn = controls.querySelector("#stop-demo-script-btn");
    const statusEl = controls.querySelector("#demo-script-status");
    const setStatus = (text) => {
      if (statusEl) statusEl.textContent = text;
    };
    state.demoScriptRunner = window.ControlledAgentDemoScriptRunner.createRunner(window.__ControlledAgent_APP_TEST_API__ || {
      sendMessage,
      startNewTimeline,
      runShowcaseLocalStep,
      completeShowcaseLocally,
    }, {
      delayMs: Math.max(800, Math.min(1500, readQaNumber("qa_showcase_step_ms", 1050))),
      onStep: (step, index) => {
        setStatus("step " + (index + 1) + ": " + step.id);
        if (runBtn) runBtn.disabled = true;
        if (stopBtn) stopBtn.disabled = false;
      },
      onDone: (result) => {
        setStatus(result.stopped ? "stopped" : "complete");
        if (runBtn) runBtn.disabled = false;
        if (stopBtn) stopBtn.disabled = true;
      },
      onStop: () => {
        setStatus("stopping");
        if (runBtn) runBtn.disabled = false;
        if (stopBtn) stopBtn.disabled = true;
      },
    });
    if (runBtn) {
      runBtn.addEventListener("click", () => {
        if (state.demoScriptRunner) void state.demoScriptRunner.run();
      });
    }
    if (stopBtn) {
      stopBtn.addEventListener("click", () => {
        if (state.demoScriptRunner) state.demoScriptRunner.stop();
      });
    }
  }

  async function sendMessage(text, intent, character, options = {}) {
    return sendStructuredAction({
      text,
      intent,
      character,
      options,
    });
  }

  function getMapFocusTarget() {
    return els.mapContainer
      || document.getElementById("map-stage")
      || document.getElementById("game-viewport")
      || document.body;
  }

  function restoreMapInputFocus(reason = "map_focus") {
    const active = document.activeElement;
    if (active && typeof active.blur === "function" && isEditableTarget(active)) {
      active.blur();
    }
    const target = getMapFocusTarget();
    if (target && target !== document.body && typeof target.focus === "function") {
      if (!target.hasAttribute("tabindex")) {
        target.setAttribute("tabindex", "-1");
      }
      try {
        target.focus({ preventScroll: true });
      } catch (_error) {
        target.focus();
      }
    }
    state.lastMapInputFocusReason = String(reason || "map_focus");
  }

  function submitInput() {
    const text = els.userInput.value.trim();
    if (!text) return;
    els.userInput.value = "";
    clearTransientInteractionContext({ keepDialogueTarget: true });
    restoreMapInputFocus("text_send_start");
    return sendMessage(text, null, null, { source: "text_input" })
      .finally(() => restoreMapInputFocus("text_send_done"));
  }

  function queueCommand(command) {
    if (state.isLoading) return;
    els.userInput.value = command;
    els.userInput.focus();
    window.requestAnimationFrame(submitInput);
  }

  function handleShortcutClick(event) {
    const button = event.target.closest(".shortcut-btn");
    if (!button) return;
    queueCommand(button.dataset.command || "");
  }

  function handleRestClick(event) {
    const button = event.target.closest(".rest-btn");
    if (!button || state.isLoading || isCombatStateActive(state.combatState)) return;

    const restType = normalizeId(button.dataset.restType);
    if (restType === "short") {
      sendMessage("", "SHORT_REST");
      return;
    }
    if (restType === "long") {
      sendMessage("", "LONG_REST");
    }
  }

  function submitDialogueInput() {
    if (state.isLoading || !state.activeDialogueTarget) return;
    const text = String(els.dialogueInput.value || "").trim();
    if (!text) return;
    els.dialogueInput.value = "";
    restoreMapInputFocus("dialogue_send_start");
    return sendMessage(text, null, null, {
      source: "dialogue_input",
      target: normalizeId(state.activeDialogueTarget),
    }).finally(() => restoreMapInputFocus("dialogue_send_done"));
  }

  function updatePttButtonState() {
    if (!els.pttMicBtn) return;
    els.pttMicBtn.classList.toggle("recording-pulse", state.isPttRecording);
    els.pttMicBtn.setAttribute("aria-pressed", String(state.isPttRecording));
    els.pttMicBtn.textContent = state.isPttRecording ? "🎙️ 正在聆听..." : "🎙️ 按住指令";
    els.pttMicBtn.title = state.isPttRecording ? "正在聆听... 松开发送" : "按住说话，松开发送";
  }

  function stopSpeechRecognition() {
    if (!state.speechRecognition) return;
    if (!state.isPttRecording) return;
    state.isPttRecording = false;
    updatePttButtonState();
    try {
      state.speechRecognition.stop();
    } catch (_error) {
      state.isPttRecording = false;
      updatePttButtonState();
    }
  }

  function startSpeechRecognition() {
    if (!state.speechRecognition || state.isLoading) return;
    if (state.isPttRecording) return;
    state.isPttRecording = true;
    updatePttButtonState();
    try {
      state.speechRecognition.start();
    } catch (_error) {
      state.isPttRecording = false;
      updatePttButtonState();
    }
  }

  function handlePttPressStart(event) {
    if (!state.speechRecognition || state.isLoading) return;
    if (event && event.type === "mousedown" && event.button !== 0) return;
    if (event && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    startSpeechRecognition();
  }

  function handlePttPressEnd(event) {
    if (!state.speechRecognition || state.isLoading) return;
    if (event && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    stopSpeechRecognition();
  }

  function initSpeechRecognition() {
    if (!els.pttMicBtn) return;
    if (!SpeechRecognition) {
      state.speechRecognitionSupported = false;
      els.pttMicBtn.disabled = true;
      els.pttMicBtn.title = "当前浏览器不支持语音输入";
      return;
    }

    state.speechRecognitionSupported = true;
    const recognition = new SpeechRecognition();
    recognition.lang = "zh-CN";
    recognition.interimResults = false;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      state.isPttRecording = true;
      updatePttButtonState();
    };

    recognition.onresult = (event) => {
      const transcript = String(event?.results?.[0]?.[0]?.transcript || "").trim();
      if (!transcript) return;
      if (els.dialogueInput) {
        els.dialogueInput.value = transcript;
        els.dialogueInput.focus();
      }
      if (!state.isLoading) {
        if (state.activeDialogueTarget) {
          submitDialogueInput();
        } else if (els.userInput) {
          els.userInput.value = transcript;
          submitInput();
        }
      }
    };

    recognition.onerror = () => {
      state.isPttRecording = false;
      updatePttButtonState();
    };

    recognition.onend = () => {
      state.isPttRecording = false;
      updatePttButtonState();
    };

    state.speechRecognition = recognition;
    els.pttMicBtn.disabled = false;
    updatePttButtonState();
  }

  function interruptDialogueWithAttack() {
    if (state.isLoading || !state.activeDialogueTarget) return;
    els.dialogueInput.value = "";
    sendMessage("我直接拔出武器攻击！", null, null, {
      source: "dialogue_input",
      target: normalizeId(state.activeDialogueTarget),
    });
  }

  function toggleXrayPanel() {
    if (!els.mainLayout) return;
    const collapsed = els.mainLayout.classList.toggle("xray-collapsed");
    if (els.xrayToggleBtn) {
      els.xrayToggleBtn.setAttribute("aria-expanded", String(!collapsed));
      els.xrayToggleBtn.textContent = collapsed ? "X-Ray +" : "X-Ray";
    }
    window.setTimeout(() => {
      window.dispatchEvent(new Event("resize"));
      if (window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.resize === "function") {
        window.ControlledAgentTacticalMap.resize();
      }
    }, 280);
  }

  function handleEnvironmentAction(event) {
    const actionButton = event.target.closest(".object-action");
    if (!actionButton) return;
    if (state.isLoading) return;
    if (actionButton.dataset.loot === "true") {
      openLootModalForTarget(actionButton.dataset.targetId || "");
      return;
    }
    const mapped = mapInteractableToStructuredAction({
      id: actionButton.dataset.targetId || "",
      type: actionButton.dataset.targetType || "",
      label: actionButton.dataset.targetLabel || "",
      name: actionButton.dataset.targetName || "",
    });
    if (!mapped) return;
    sendMessage(mapped.text || "", mapped.intent || null, mapped.character || null, {
      target: mapped.target || "",
      source: mapped.source || "ui_click",
    });
  }

  function handlePartyAction(event) {
    const button = event.target.closest(".item-action");
    const inventorySlot = event.target.closest(".party-view-inventory-slot[data-item-id]");
    const target = button || inventorySlot;
    if (!target || state.isLoading) return;
    if (inventorySlot && button) {
      event.stopPropagation();
    }

    const itemId = normalizeId(target.dataset.itemId);
    const action = normalizeId(target.dataset.partyAction);
    const characterId = normalizeId(target.dataset.ownerId) || "player";
    if (!itemId || !action) return;

    if (action === "inspect") {
      queueCommand("检查 " + itemId);
      return;
    }

    if (action === "equip") {
      const command = characterId === "player" ? "我要装备 " + itemId : "让 " + characterId + " 装备 " + itemId;
      sendMessage(command);
      return;
    }

    if (action === "use") {
      const command = characterId === "player" ? "我要使用 " + itemId : "让 " + characterId + " 使用 " + itemId;
      sendMessage(command);
      return;
    }

    if (action === "unequip") {
      const command = characterId === "player" ? "我要卸下 " + itemId : "让 " + characterId + " 卸下 " + itemId;
      sendMessage(command);
    }
  }

  function bindEvents() {
    if (els.tacticalToggleBtn) {
      els.tacticalToggleBtn.addEventListener("click", toggleTacticalOverlay);
    }
    els.sendBtn.addEventListener("click", () => {
      void submitInput();
    });
    els.userInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void submitInput();
      }
    });

    document.querySelector(".shortcut-bar").addEventListener("click", handleShortcutClick);
    els.restControls.addEventListener("click", handleRestClick);
    if (els.newTimelineBtn) {
      els.newTimelineBtn.addEventListener("click", () => {
        void startNewTimeline();
      });
    }
    if (els.pttMicBtn) {
      els.pttMicBtn.addEventListener("mousedown", handlePttPressStart);
      els.pttMicBtn.addEventListener("touchstart", handlePttPressStart, { passive: false });
      els.pttMicBtn.addEventListener("mouseup", handlePttPressEnd);
      els.pttMicBtn.addEventListener("mouseleave", handlePttPressEnd);
      els.pttMicBtn.addEventListener("touchend", handlePttPressEnd, { passive: false });
      els.pttMicBtn.addEventListener("touchcancel", handlePttPressEnd, { passive: false });
    }
    els.dialogueSendBtn.addEventListener("click", () => {
      void submitDialogueInput();
    });
    els.dialogueAttackBtn.addEventListener("click", interruptDialogueWithAttack);
    els.dialogueInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        void submitDialogueInput();
      }
    });
    [els.mapContainer, document.getElementById("map-stage")].forEach((target) => {
      if (!target) return;
      target.addEventListener("pointerdown", () => {
        restoreMapInputFocus("map_pointer");
      });
    });
    els.xrayToggleBtn.addEventListener("click", toggleXrayPanel);
    els.logFilterBar.addEventListener("click", handleLogFilterClick);
    els.partyRoster.addEventListener("click", handlePartyAction);
    els.partyViewContent.addEventListener("click", handlePartyAction);
    els.environmentList.addEventListener("click", handleEnvironmentAction);

    els.closePartyViewBtn.addEventListener("click", () => setPartyView(false));
    els.partyViewModal.addEventListener("click", (event) => {
      if (event.target === els.partyViewModal) {
        setPartyView(false);
      }
    });
    els.partyViewTabs.addEventListener("click", (event) => {
      const button = event.target.closest(".party-view-tab");
      if (!button) return;
      state.activePartyViewTab = normalizeId(button.dataset.partyTab || "inventory");
      renderPartyView();
    });

    els.closeLootBtn.addEventListener("click", hideLootModal);
    els.lootModal.addEventListener("click", (event) => {
      if (event.target === els.lootModal) {
        hideLootModal();
      }
    });

    els.lootAllBtn.addEventListener("click", () => {
      const targetId = normalizeId(state.currentLootTargetId);
      hideLootModal();
      if (!targetId) return;
      state.seenLootTargets.add(targetId);
      sendMessage("我要搜刮 " + targetId, "ui_action_loot", "player", {
        target: targetId,
        source: "ui_click",
      });
    });

    document.addEventListener("keydown", (event) => {
      if (isEditableTarget(event.target)) return;
      if (event.key === "Escape" && state.partyViewOpen) {
        event.preventDefault();
        setPartyView(false);
        return;
      }
      if (event.key === "Tab" || event.key.toLowerCase() === "i") {
        event.preventDefault();
        togglePartyView();
        return;
      }
      const allowLegacyConsole = QA_PARAMS.get("qa_tactical_console") === "1" || window.__ControlledAgent_QA_TACTICAL_CONSOLE__ === true;
      if (allowLegacyConsole && (event.code === "Space" || event.key === " ")) {
        event.preventDefault();
        toggleTacticalOverlay();
      }
    });

    ["keydown", "pointerdown"].forEach((eventName) => {
      document.addEventListener(
        eventName,
        () => {
          if (!state.isLoading) {
            resetIdleTimer();
          }
        },
        { passive: true }
      );
    });
  }

  async function syncInitialState() {
    if (state.hasSyncedInitialState || state.isLoading) return;
    state.hasSyncedInitialState = true;

    const data = await sendMessage("", "init_sync", null, {
      incrementTurn: false,
      skipLogUpdate: true,
    });

    if (!data) {
      state.hasSyncedInitialState = false;
      return;
    }

    appendLogEntry("system", "存档同步", "已接入当前世界状态，战术桌完成初始校准。", {
      color: "#73c6c3",
      sigil: "◌",
      logType: "system",
    });
  }

  async function pollDialogueState() {
    if (state.isLoading) return;
    try {
      const response = await fetchWithTimeout(
        STATE_URL + "?session_id=" + encodeURIComponent(getSessionId()),
        {},
        BACKEND_REQUEST_TIMEOUT_MS,
      );
      if (!response.ok) return;
      const data = await response.json();
      refreshWorldFlags(data);
      const partyStatus = safeObject(data.party_status);
      const environmentObjects = safeObject(data.environment_objects);
      const playerInventory = safeObject(data.player_inventory);
      const combatState = safeObject(data.combat_state);
      const prevPollParty = { ...state.partyStatus };
      const prevPollEnvironment = { ...state.environmentObjects };
      const prevPollInventory = { ...state.playerInventory };
      const prevPollCombat = { ...state.combatState };
      const prevPollFlags = { ...safeObject(state.worldFlags) };
      const prevPollJournal = safeArray(state.lastProjectionJournalEvents).slice();
      const hadProjectionBaseline = state.hasStateProjectionBaseline === true;
      if (Object.keys(partyStatus).length) state.partyStatus = mergePartyStatusResponse(prevPollParty, partyStatus);
      if (Object.keys(environmentObjects).length) state.environmentObjects = environmentObjects;
      if (Object.keys(playerInventory).length) state.playerInventory = playerInventory;
      state.combatState = combatState;
      const pollMapData = safeObject(data.map_data);
      if (Object.keys(pollMapData).length) {
        state.mapData = state.normalizedMap
          ? mergeVisualMapData(pollMapData, state.normalizedMap)
          : pollMapData;
      }
      updateDialogueOverlay(data);
      updateRestControls(state.combatState);
      updateXrayPanel(data);
      renderTacticalGrid(state.partyStatus, state.environmentObjects, state.mapData);
      updateMapDebug("pollDialogueState");
      if (!hadProjectionBaseline) {
        state.hasStateProjectionBaseline = true;
      } else {
        const previousPollState = {
          party_status: prevPollParty,
          environment_objects: prevPollEnvironment,
          player_inventory: prevPollInventory,
          combat_state: prevPollCombat,
          flags: prevPollFlags,
          journal_events: prevPollJournal,
          _eventSource: "state_poll",
        };
        const pollEvents = dispatchUIEventsFromResponse(data, previousPollState, undefined, {
          stateProjectionOnly: true,
        });
        if (safeArray(pollEvents).some((event) => normalizeId(safeObject(event).type).startsWith("trap_"))) {
          refreshVisibilityProjection();
          renderTacticalGrid(state.partyStatus, state.environmentObjects, state.mapData);
        }
      }
      state.lastProjectionJournalEvents = extractEventLines(data);
    } catch (error) {
      if (error && error.name === "AbortError") {
        setNetworkState("链路在线", "ok");
      }
      // Dialogue polling is an enhancement; chat responses remain the source of truth.
    }
  }

  function startDialoguePolling() {
    if (IS_QA_MODE) return;
    window.clearInterval(state.dialoguePollTimer);
    state.dialoguePollTimer = window.setInterval(() => {
      void pollDialogueState();
    }, DIALOGUE_POLL_MS);
  }

  function submitDockInput() {
    if (!els.dockInput) return;
    const text = els.dockInput.value.trim();
    if (!text) return;
    els.dockInput.value = "";
    clearTransientInteractionContext({ keepDialogueTarget: true });
    restoreMapInputFocus("dock_send_start");
    return sendMessage(text, null, null, { source: "dock_input" })
      .finally(() => restoreMapInputFocus("dock_send_done"));
  }

  async function startNewTimeline() {
    if (state.isLoading) return;
    window.clearTimeout(state.idleTimer);
    const newSessionId = setSessionId(buildTimelineSessionId());
    clearTransientInteractionContext();
    state.hasSyncedInitialState = false;
    state.hasStateProjectionBaseline = false;
    state.lastProjectionJournalEvents = [];
    state.seenLootTargets.clear();
    state.currentLootTargetId = "";
    state.turnCount = 0;
    if (els.turnCounter) {
      els.turnCounter.textContent = padTurn(state.turnCount);
    }
    if (els.worldLog) {
      els.worldLog.innerHTML = "";
    }
    appendLogEntry("system", "新时间线", "已创建新会话：" + newSessionId + "，开始同步干净状态。", {
      color: "#73c6c3",
      sigil: "◎",
      logType: "system",
    });
    await sendMessage("", "init_sync", null, {
      source: "ui_click",
      incrementTurn: false,
      skipLogUpdate: true,
      skipIdleReset: true,
    });
    recordShowcaseBaseline();
  }

  function bindDockEvents() {
    if (els.dockInput) {
      els.dockInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          void submitDockInput();
        }
      });
    }
    if (els.dockSendBtn) {
      els.dockSendBtn.addEventListener("click", () => {
        void submitDockInput();
      });
    }
  }

  function initNewModules() {
    /* Initialize Director Trace Panel */
    if (window.ControlledAgentDirectorTrace && typeof window.ControlledAgentDirectorTrace.init === "function") {
      window.ControlledAgentDirectorTrace.init();
    }

    /* Load and normalize map */
    let normalizedMap = null;
    if (window.ControlledAgentTiledAdapter) {
      normalizedMap = window.ControlledAgentTiledAdapter.normalizeTiledMap(null);
      applyNormalizedMap(normalizedMap, { source: "fixture", reason: "boot_fixture" });
    }

    /* Initialize Input Controller for WASD */
    if (window.ControlledAgentInputController && normalizedMap) {
      window.ControlledAgentInputController.init({
        normalizedMap: state.normalizedMap || normalizedMap,
        playerStart: normalizedMap.playerStart,
        onNarrativeTrigger: (trigger) => {
          const triggerId = normalizeId(trigger && trigger.id);
          const triggerType = normalizeId(trigger && trigger.type);
          const isTrapMechanicTrigger = triggerType === "trap" || triggerId.includes("trap");
          const shouldRouteToBackend = triggerId === "act1_corridor_approach" || isTrapMechanicTrigger;
          if (triggerId === "act1_corridor_approach") {
            resolveAct1Perception();
          }
          if (QA_NO_IDLE && !shouldRouteToBackend) return;
          const data = trigger && trigger.data ? trigger.data : {};
          const triggerEventId = (trigger && trigger.id) ? String(trigger.id) : "unknown";
          if (isTrapMechanicTrigger) {
            const trapTarget = normalizeTrapTriggerTarget(trigger) || "gas_trap_1";
            if (!shouldTriggerTrapMechanic(trapTarget)) return;
            sendStructuredAction({
              text: "触发毒气陷阱",
              intent: "INTERACT",
              character: null,
              options: {
                target: trapTarget,
                source: "trap_trigger",
              },
            });
            return;
          }
          if (triggerId === "act1_corridor_approach") {
            if (!shouldQueueTrapAwareness("gas_trap_1")) return;
            sendStructuredAction({
              text: "侦察员检查走廊里的可疑机关。",
              intent: "CHAT",
              character: null,
              options: {
                target: "gas_trap_1",
                source: "trap_awareness",
              },
            });
            return;
          }
          sendMessage(
            "我踩到了一个触发区域: " + triggerEventId,
            "trigger_zone",
            null,
            {
              target: triggerEventId,
              source: "trigger_zone",
            }
          );
        },
        onInteraction: (interactable) => {
          const mapped = mapInteractableToStructuredAction(interactable);
          if (!mapped) return;
          const interactionTarget = normalizeId(mapped.target);
          if (interactionTarget === "door_b_to_c" && !state.discoveredSecretDoorIds.has("door_b_to_c")) {
            discoverSecretDoor("door_b_to_c");
          }
          if (handleLocalExplorationDoor(interactionTarget)) {
            return;
          }
          sendMessage(mapped.text || "", mapped.intent || null, mapped.character || null, {
            target: mapped.target || "",
            source: mapped.source || "interaction",
          });
          if (revealRoomByDoorTarget(interactionTarget)) {
            refreshVisibilityProjection();
          }
        },
        onHighlightChanged: (interactable) => {
          if (window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.setInteractionFocus === "function") {
            window.ControlledAgentTacticalMap.setInteractionFocus(interactable || null);
          }
        },
        formatInteractionHint,
      });
    }

    /* Show initial act progress */
    if (window.ControlledAgentHudRenderers) {
      window.ControlledAgentHudRenderers.updateActProgress(1);
    }

    state.mapId = MAP_ID;
    const shouldAutoLoadRealMap =
      window.__ControlledAgent_ENABLE_TEST_API__ !== true || window.__ControlledAgent_FORCE_REAL_MAP_LOAD__ === true;
    if (
      shouldAutoLoadRealMap
      && window.ControlledAgentTiledAdapter
      && typeof window.ControlledAgentTiledAdapter.loadMapById === "function"
    ) {
      void window.ControlledAgentTiledAdapter.loadMapById(MAP_ID).then((result) => {
        try {
          const record = safeObject(result);
          state.mapDebugLastMapLoad = {
            source: String(record.source || ""),
            reason: String(record.reason || ""),
            assetPath: String(record.assetPath || ""),
            mapId: String(record.mapId || MAP_ID || ""),
          };
          const loadedMap = safeObject(record.map);
          if (Object.keys(loadedMap).length) {
            applyNormalizedMap(loadedMap, {
              source: record.source || "fixture",
              reason: record.reason || "",
            });
            renderTacticalGrid(state.partyStatus, state.environmentObjects, state.mapData);
            updateMapDebug("loadMapById:resolved");
            if (IS_QA_MODE) {
              void pollDialogueState();
            }
          }
          const isFallback = normalizeId(record.source) !== "json";
          if (IS_QA_MODE && isFallback && window.ControlledAgentHudRenderers && typeof window.ControlledAgentHudRenderers.showToast === "function") {
            window.ControlledAgentHudRenderers.showToast("warning", "⚠ 地图资产加载失败，已回退 fixture", 2800);
          }
        } catch (error) {
          const reason = normalizeId(error && error.name) || "map_apply_error";
          state.mapDebugLastMapLoad = {
            source: state.mapLoadSource || "fixture",
            reason,
            assetPath: "/web_ui/assets/maps/" + MAP_ID + ".json",
            mapId: MAP_ID,
            error: {
              name: String(error && error.name || ""),
              message: String(error && error.message || ""),
            },
          };
          updateMapDebug("loadMapById:apply_error");
        }
      }).catch((error) => {
        const reason = normalizeId(error && error.name) || "load_error";
        state.mapDebugLastMapLoad = {
          source: "fixture",
          reason,
          assetPath: "/web_ui/assets/maps/" + MAP_ID + ".json",
          mapId: MAP_ID,
          error: {
            name: String(error && error.name || ""),
            message: String(error && error.message || ""),
          },
        };
        updateMapSourceStatus("fixture", reason);
        updateMapDebug("loadMapById:rejected");
        if (IS_QA_MODE && window.ControlledAgentHudRenderers && typeof window.ControlledAgentHudRenderers.showToast === "function") {
          window.ControlledAgentHudRenderers.showToast("warning", "⚠ 地图资产加载失败，已回退 fixture", 2800);
        }
      });
    }
  }

  /**
   * isNarrativeRequest — determines if an intent/text/source should
   * activate the Director Trace panel.
   *
   * Only these sources activate it:
   *   - trigger zones (intent: trigger_zone)
   *   - E-key interactions (source: interaction)
   *   - dialogue / choices (intent contains 'dialogue', 'choice', 'talk')
   *   - companion interrupts (intent: companion_interrupt)
   *   - explicit user commands (non-empty text without system intents)
   *
   * These do NOT activate it:
   *   - init_sync
   *   - trigger_idle_banter
   *   - ui_action_loot (loot pickup is a UI action, not narrative)
   *   - state polling
   */
  function isNarrativeRequest(intent, text, source) {
    const i = String(intent || "").toLowerCase().trim();
    const s = String(source || "").toLowerCase().trim();
    const n = String(intent || "").trim().toUpperCase();

    const NON_NARRATIVE = new Set([
      "init_sync",
      "trigger_idle_banter",
      "ui_action_loot",
      "short_rest",
      "long_rest",
      "state_poll",
    ]);
    if (NON_NARRATIVE.has(i)) return false;

    if (s === "trigger_zone" || i === "trigger_zone") return true;
    if (s === "trap_trigger") return true;
    if (s.startsWith("boss_")) return true;
    if (s === "interaction") return true;
    if (i === "companion_interrupt") return true;
    if (s === "dialogue_input") return true;

    if (n === "READ" || n === "INTERACT" || n === "CHAT" || n === "ATTACK" || n === "DISARM") return true;
    if (/dialogue|choice|talk|speak|converse/i.test(i)) return true;
    return false;
  }

  function dispatchUIEventsFromResponse(data, previousState, providedEvents, options) {
    if (!window.ControlledAgentUIEventAdapter || !window.ControlledAgentHudRenderers) return;
    const events = Array.isArray(providedEvents)
      ? providedEvents
      : window.ControlledAgentUIEventAdapter.extractUIEvents(data, previousState, options);
    window.ControlledAgentHudRenderers.dispatchUIEvents(events);
    events.forEach((event) => {
      const ev = safeObject(event);
      if (ev.type === "trap_insight") {
        markBackendTrapSignal(ev.trapId || "gas_trap_1", "revealed");
        state.trapSenseEnabled = false;
        if (window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.setTrapSenseMode === "function") {
          window.ControlledAgentTacticalMap.setTrapSenseMode(false);
        }
      }
      if (ev.type === "trap_disarmed") {
        markBackendTrapSignal(ev.trapId || "gas_trap_1", "disabled");
      }
      if (ev.type === "trap_triggered") {
        markBackendTrapSignal(ev.trapId || "gas_trap_1", "triggered");
      }
      if (ev.type === "boss_intro" || ev.type === "boss_strategy" || ev.type === "boss_route") {
        revealRoom(ROOM_D);
        updateExplorationActProgress();
      }
      if (ev.type === "poison_valve") {
        markPoisonValveSignal(ev.valveId || "poison_valve", ev.status || "triggered");
      }
      if (ev.type === "trap_insight" && window.ControlledAgentTacticalMap && typeof window.ControlledAgentTacticalMap.playTrapDiscoveryHighlight === "function") {
        window.ControlledAgentTacticalMap.playTrapDiscoveryHighlight([ev.trapId || "gas_trap_1"]);
      }
      if (ev.type === "trap_triggered" && window.ControlledAgentTacticalMap) {
        if (typeof window.ControlledAgentTacticalMap.playTrapHazardPulse === "function") {
          const trap = safeObject(state.environmentObjects[ev.trapId || "gas_trap_1"]);
          window.ControlledAgentTacticalMap.playTrapHazardPulse({
            id: ev.trapId || "gas_trap_1",
            x: Number(trap.x) || 0,
            y: Number(trap.y) || 0,
            w: Number(trap.w || trap.width) || 1,
            h: Number(trap.h || trap.height) || 1,
          });
        }
        if (typeof window.ControlledAgentTacticalMap.playStatusDamage === "function") {
          safeArray(ev.affectedActors).forEach((actorId) => window.ControlledAgentTacticalMap.playStatusDamage(actorId, "中毒"));
        }
      }
      if (ev.type === "poison_valve" && window.ControlledAgentTacticalMap) {
        const status = normalizeId(ev.status || "");
        const valve = safeObject(state.environmentObjects[ev.valveId || "poison_valve"]) || safeObject(state.environmentObjects.potion_tank);
        if (status === "triggered" && typeof window.ControlledAgentTacticalMap.playTrapHazardPulse === "function") {
          window.ControlledAgentTacticalMap.playTrapHazardPulse({
            id: ev.valveId || "poison_valve",
            x: Number(valve.x) || 0,
            y: Number(valve.y) || 0,
            w: Number(valve.w || valve.width) || 1,
            h: Number(valve.h || valve.height) || 1,
          });
        }
        if (status === "triggered" && typeof window.ControlledAgentTacticalMap.playStatusDamage === "function") {
          safeArray(ev.affectedActors).forEach((actorId) => window.ControlledAgentTacticalMap.playStatusDamage(actorId, "中毒"));
        }
      }
    });
    return events;
  }

  async function boot() {
    const qa = readQaActions();
    initSpeechRecognition();
    bindEvents();
    bindDockEvents();
    initNewModules();
    initShowcaseMode();
    setTacticalOverlay(false);
    renderChrome(LOCATION_LABELS[MAP_ID] || "废弃危害实验室");
    renderPartyRoster();
    renderEnvironmentObjects();
    renderTacticalGrid(state.partyStatus, state.environmentObjects, state.mapData);
    renderInitiativeTracker(state.combatState, false);
    updateRestControls(state.combatState);
    updateXrayPanel({});
    appendLogEntry("system", "终端接入", "已进入 " + (LOCATION_LABELS[MAP_ID] || MAP_ID) + "。WASD 移动探索，E 键交互。", {
      color: "#d0ab67",
      sigil: "◎",
      logType: "system",
    });

    if (qa.shouldToggleXray) {
      window.setTimeout(() => {
        const isCollapsed = els.mainLayout && els.mainLayout.classList.contains("xray-collapsed");
        const shouldToggle =
          qa.xrayMode === "toggle"
          || (qa.xrayMode === "collapse" && !isCollapsed)
          || (qa.xrayMode === "expand" && isCollapsed);
        if (shouldToggle) {
          toggleXrayPanel();
        }
      }, qa.xrayDelay);
    }

    if (qa.traceCommand || qa.traceIntent) {
      if (qa.previewTrace) {
        clearXrayNodeTraceAnimation();
        applyXrayNodeClasses([], "");
        window.setTimeout(() => {
          const previewTrace = inferNodeTrace({}, qa.traceCommand, qa.traceIntent || "");
          setXrayNodeTrace(previewTrace, { animate: true });
        }, 120);
      }

      window.setTimeout(() => {
        void sendMessage(qa.traceCommand, qa.traceIntent || null);
      }, qa.traceDelay);
    }

    if (SHOULD_SYNC_INITIAL_STATE) {
      await syncInitialState();
    }
    if (!IS_QA_MODE) {
      startDialoguePolling();
    }
  }

  function exposeTestApi() {
    if (window.__ControlledAgent_ENABLE_TEST_API__ !== true) return;
    window.__ControlledAgent_APP_TEST_API__ = {
      boot,
      sendMessage,
      sendStructuredAction,
      submitInput,
      submitDockInput,
      restoreMapInputFocus,
      startNewTimeline,
      buildChatPayload,
      pollDialogueState,
      updateDialogueOverlay,
      updateXrayPanel,
      interruptDialogueWithAttack,
      inferNodeTrace,
      normalizeNodeName,
      isNarrativeRequest,
      dispatchUIEventsFromResponse,
      extractSpeechBarks,
      triggerSpeechBubbles,
      hardClearAct3TrapBarks,
      hardClearAct3TrapBarksIfNeeded,
      hasAct3BarkResetSignal,
      isAct3BarkRequest,
      buildShowcaseSnapshot,
      updateWorldStateDiff,
      recordShowcaseBaseline,
      runShowcaseLocalStep,
      applyNormalizedMap,
      refreshVisibilityProjection,
      revealRoom,
      revealRoomByDoorTarget,
      handleLocalExplorationDoor,
      resolveAct1Perception,
      discoverSecretDoor,
      projectPartyStatusForTactical,
      projectCompanionFormationForTactical,
      findNearestWalkableFormationCell,
      renderTacticalGrid,
      updateMapDebug,
      collectMapDebugSnapshot,
      get demoScriptRunner() {
        return state.demoScriptRunner;
      },
      state,
      els,
      MAP_ID,
      get SESSION_ID() {
        return getSessionId();
      },
      ITEM_META,
    };
  }

  exposeTestApi();

  document.addEventListener("DOMContentLoaded", () => {
    void boot();
  });
})();
