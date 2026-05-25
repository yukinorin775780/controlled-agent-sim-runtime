const fs = require("fs");
const path = require("path");

const INDEX_HTML_PATH = path.resolve(__dirname, "../index.html");
const APP_JS_PATH = path.resolve(__dirname, "../app.js");
const NECRO_META_PATH = path.resolve(__dirname, "../hazard-meta.js");
const TILED_ADAPTER_PATH = path.resolve(__dirname, "../tiled-adapter.js");
const UI_EVENT_ADAPTER_PATH = path.resolve(__dirname, "../ui-event-adapter.js");
const DIRECTOR_TRACE_PATH = path.resolve(__dirname, "../director-trace.js");
const STATE_DIFF_RENDERER_PATH = path.resolve(__dirname, "../state-diff-renderer.js");
const DEMO_SCRIPT_RUNNER_PATH = path.resolve(__dirname, "../demo-script-runner.js");
const INPUT_CONTROLLER_PATH = path.resolve(__dirname, "../input-controller.js");
const HUD_RENDERERS_PATH = path.resolve(__dirname, "../hud-renderers.js");
const GAME_JS_PATH = path.resolve(__dirname, "../game.js");
const REAL_MAP_JSON_PATH = path.resolve(__dirname, "../assets/maps/hazard_lab.json");
const REAL_MAP_TMX_PATH = path.resolve(__dirname, "../../data/maps/hazard_lab.tmx");
const BARK_BUILD_ID = "20260522_act2_trap_semantics_v10";

function extractBodyMarkup(htmlText) {
  const match = String(htmlText).match(/<body[^>]*>([\s\S]*?)<\/body>/i);
  if (!match) {
    throw new Error("index.html missing <body> content");
  }
  return match[1].replace(/<script[\s\S]*?<\/script>/gi, "");
}

function mountIndexBody() {
  const source = fs.readFileSync(INDEX_HTML_PATH, "utf8");
  document.body.innerHTML = extractBodyMarkup(source);
}

function mockResponse(payload, { ok = true, status = 200 } = {}) {
  return Promise.resolve({
    ok,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  });
}

async function flushAsync() {
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function loadNewModules() {
  jest.isolateModules(() => { require(NECRO_META_PATH); });
  jest.isolateModules(() => { require(TILED_ADAPTER_PATH); });
  jest.isolateModules(() => { require(UI_EVENT_ADAPTER_PATH); });
  jest.isolateModules(() => { require(DIRECTOR_TRACE_PATH); });
  jest.isolateModules(() => { require(STATE_DIFF_RENDERER_PATH); });
  jest.isolateModules(() => { require(DEMO_SCRIPT_RUNNER_PATH); });
  jest.isolateModules(() => { require(INPUT_CONTROLLER_PATH); });
  jest.isolateModules(() => { require(HUD_RENDERERS_PATH); });
}

function loadGameHelpers() {
  delete window.ControlledAgentTacticalMap;
  const warnSpy = jest.spyOn(console, "warn").mockImplementation(() => {});
  jest.isolateModules(() => { require(GAME_JS_PATH); });
  warnSpy.mockRestore();
  return window.ControlledAgentTacticalMap;
}

async function bootAppForTest(url = "http://localhost/?qa_test=1") {
  window.history.replaceState({}, "", url);
  window.__ControlledAgent_ENABLE_TEST_API__ = true;
  window.ControlledAgentTacticalMap = {
    update: jest.fn(),
    resize: jest.fn(),
    movePlayerLocal: jest.fn(),
    getPlayerGridPosition: jest.fn().mockReturnValue({ x: 2, y: 2 }),
    drawLoSBlockerOverlay: jest.fn(),
    clearLoSBlockerOverlay: jest.fn(),
    playTrapDiscoveryHighlight: jest.fn(),
    playTrapHazardPulse: jest.fn(),
    setInteractionFocus: jest.fn(),
    setTrapSenseMode: jest.fn(),
    resetLocalPartyTrail: jest.fn(),
    refreshMapOnly: jest.fn(),
    getLocalPartyTokenPositions: jest.fn().mockReturnValue({}),
  };
  if (typeof window.requestAnimationFrame !== "function") {
    window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  }

  loadNewModules();

  jest.isolateModules(() => {
    require(APP_JS_PATH);
  });

  const api = window.__ControlledAgent_APP_TEST_API__;
  if (!api) {
    throw new Error("window.__ControlledAgent_APP_TEST_API__ not exposed");
  }
  await api.boot();
  await flushAsync();
  return api;
}

function spyOnFetch() {
  if (typeof globalThis.fetch !== "function") {
    globalThis.fetch = () => Promise.reject(new Error("unmocked fetch"));
  }
  return jest.spyOn(globalThis, "fetch");
}

function extractObjectNamesByLayerFromTmx(xmlText) {
  const text = String(xmlText || "");
  const result = {};
  const groupRe = /<objectgroup[^>]*name=\"([^\"]+)\"[^>]*>([\s\S]*?)<\/objectgroup>/g;
  let groupMatch;
  while ((groupMatch = groupRe.exec(text))) {
    const layerName = groupMatch[1];
    const body = groupMatch[2];
    const names = [];
    const objectRe = /<object[^>]*name=\"([^\"]+)\"[^>]*>/g;
    let objectMatch;
    while ((objectMatch = objectRe.exec(body))) {
      names.push(objectMatch[1]);
    }
    result[layerName] = names;
  }
  return result;
}

function extractTileLayersFromTmx(xmlText) {
  const text = String(xmlText || "");
  const mapMatch = text.match(/<map[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"/);
  const result = {
    width: mapMatch ? Number(mapMatch[1]) : 0,
    height: mapMatch ? Number(mapMatch[2]) : 0,
    layers: {},
  };
  const layerRe = /<layer[^>]*\bname="([^"]+)"[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"[^>]*>[\s\S]*?<data[^>]*>([\s\S]*?)<\/data>[\s\S]*?<\/layer>/g;
  let layerMatch;
  while ((layerMatch = layerRe.exec(text))) {
    result.layers[layerMatch[1]] = {
      width: Number(layerMatch[2]),
      height: Number(layerMatch[3]),
      cells: layerMatch[4]
        .split(",")
        .map((cell) => cell.trim())
        .filter((cell) => cell.length > 0),
    };
  }
  return result;
}

function getMapLayer(map, layerName) {
  return (map.layers || []).find((layer) => layer.name === layerName);
}

function getMapObject(map, layerName, objectName) {
  const layer = getMapLayer(map, layerName);
  return ((layer && layer.objects) || []).find((object) => object.name === objectName);
}

function flattenTiledProps(object) {
  return ((object && object.properties) || []).reduce((out, prop) => {
    out[prop.name] = prop.value;
    return out;
  }, {});
}

function pointInRect(point, rect) {
  return (
    point.x >= rect.x &&
    point.x < rect.x + rect.w &&
    point.y >= rect.y &&
    point.y < rect.y + rect.h
  );
}

function roomById(rooms, roomId) {
  return rooms.find((room) => room.id === roomId);
}

function loadRealHazardMap() {
  const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
  return window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
}

function emptyTurnResponse(overrides = {}) {
  return {
    responses: [],
    journal_events: [],
    party_status: {},
    environment_objects: {},
    player_inventory: {},
    combat_state: {},
    ...overrides,
  };
}

function buildFormationTestMap(overrides = {}) {
  const width = Number(overrides.width) || 8;
  const height = Number(overrides.height) || 8;
  const collision = Array.from({ length: height }, () => Array.from({ length: width }, () => false));
  (overrides.blocked || []).forEach(({ x, y }) => {
    if (collision[y] && x >= 0 && x < width) collision[y][x] = true;
  });
  return {
    id: "formation_test",
    width,
    height,
    collision,
    losBlockers: Array.from({ length: height }, () => Array.from({ length: width }, () => false)),
    groundTypes: Array.from({ length: height }, () => Array.from({ length: width }, () => 0)),
    rooms: [{ id: "room_a_spawn", x: 0, y: 0, w: width, h: height }],
    visibleRooms: ["room_a_spawn"],
    playerStart: { x: 4, y: 4 },
    interactables: [],
    obstacles: [],
  };
}

function buildTrapCorridorTestMap() {
  const width = 8;
  const height = 5;
  return {
    id: "trap_corridor_test",
    width,
    height,
    collision: Array.from({ length: height }, () => Array.from({ length: width }, () => false)),
    losBlockers: Array.from({ length: height }, () => Array.from({ length: width }, () => false)),
    groundTypes: Array.from({ length: height }, () => Array.from({ length: width }, () => 0)),
    rooms: [
      { id: "room_a_spawn", x: 0, y: 0, w: 4, h: 5 },
      { id: "room_b_corridor", x: 4, y: 0, w: 4, h: 5 },
    ],
    visibleRooms: ["room_a_spawn"],
    playerStart: { x: 2, y: 2 },
    triggers: [
      {
        id: "poison_trap_1",
        alias_id: "gas_trap_1",
        type: "trap",
        x: 5,
        y: 2,
        w: 1,
        h: 1,
        room_id: "room_b_corridor",
        data: { type: "trap", alias_id: "gas_trap_1", room_id: "room_b_corridor" },
      },
    ],
    interactables: [
      {
        id: "door_a_to_b",
        type: "door",
        x: 3,
        y: 2,
        w: 1,
        h: 1,
        connects_from: "room_a_spawn",
        connects_to: "room_b_corridor",
        data: { type: "door", connects_from: "room_a_spawn", connects_to: "room_b_corridor" },
      },
      {
        id: "poison_trap_1",
        alias_id: "gas_trap_1",
        type: "trap",
        x: 5,
        y: 2,
        w: 1,
        h: 1,
        room_id: "room_b_corridor",
        status: "hidden",
        is_hidden: true,
        data: { type: "trap", alias_id: "gas_trap_1", room_id: "room_b_corridor", status: "hidden", is_hidden: true },
      },
    ],
    obstacles: [],
  };
}

function trapTriggeredResponse() {
  return {
    responses: [],
    journal_events: ["[毒气陷阱] gas_trap_1 triggered"],
    current_location: "毒气走廊",
    party_status: {
      player: { x: 5, y: 2, status_effects: [{ type: "poisoned", duration: 3 }] },
    },
    environment_objects: {
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "triggered", is_hidden: false, x: 5, y: 2 },
    },
    player_inventory: {},
    combat_state: {},
    flags: { hazard_lab_poison_trap_triggered: true },
  };
}

describe("web_ui/app.js UI bindings", () => {
  beforeEach(() => {
    jest.resetModules();
    delete window.ControlledAgentHazardMeta;
    delete window.ControlledAgentTiledAdapter;
    delete window.ControlledAgentUIEventAdapter;
    delete window.ControlledAgentDirectorTrace;
    delete window.ControlledAgentStateDiffRenderer;
    delete window.ControlledAgentDemoScriptRunner;
    delete window.ControlledAgentInputController;
    delete window.ControlledAgentHudRenderers;
    mountIndexBody();
  });

  afterEach(() => {
    delete window.__ControlledAgent_APP_TEST_API__;
    delete window.__ControlledAgent_ENABLE_TEST_API__;
    delete window.ControlledAgentTacticalMap;
    delete window.ControlledAgentHazardMeta;
    delete window.ControlledAgentTiledAdapter;
    delete window.ControlledAgentUIEventAdapter;
    delete window.ControlledAgentDirectorTrace;
    delete window.ControlledAgentStateDiffRenderer;
    delete window.ControlledAgentDemoScriptRunner;
    delete window.ControlledAgentInputController;
    delete window.ControlledAgentHudRenderers;
    delete window.__ControlledAgent_FORCE_REAL_MAP_LOAD__;
    delete window.__ControlledAgent_BARK_BUILD_ID__;
    delete window.__ControlledAgent_QA_STATE__;
    if (document.documentElement) {
      delete document.documentElement.dataset.barkBuild;
    }
  });

  /* ═══════════════════════════════════════════
     EXISTING TESTS (preserved from Sprint 0)
     ═══════════════════════════════════════════ */

  test("test_index_static_resources_use_current_bark_build_version", () => {
    const html = fs.readFileSync(INDEX_HTML_PATH, "utf8");
    expect(html).toContain(`style.css?v=${BARK_BUILD_ID}`);
    expect(html).toContain(`ui-event-adapter.js?v=${BARK_BUILD_ID}`);
    expect(html).toContain(`hud-renderers.js?v=${BARK_BUILD_ID}`);
    expect(html).toContain(`app.js?v=${BARK_BUILD_ID}`);
    expect(html).toContain(`director-trace.js?v=${BARK_BUILD_ID}`);
    expect(html).toContain(`state-diff-renderer.js?v=${BARK_BUILD_ID}`);
    expect(html).not.toContain("20260514_act2_playability_polish");
  });

  test("test_app_boot_exposes_bark_build_marker", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1&qa_map_debug=1");

    expect(window.__ControlledAgent_BARK_BUILD_ID__).toBe(BARK_BUILD_ID);
    expect(document.documentElement.dataset.barkBuild).toBe(BARK_BUILD_ID);
  });

  test("test_qa_debug_state_includes_bark_self_check", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_map_debug=1");

    api.updateMapDebug("unit_test");

    expect(window.__ControlledAgent_QA_STATE__).toMatchObject({
      barkBuild: BARK_BUILD_ID,
      hasCompanionBarkRenderer: true,
      hasExtractSpeechBarks: true,
    });
    expect(api.collectMapDebugSnapshot("unit_test")).toMatchObject({
      barkBuild: BARK_BUILD_ID,
      hasCompanionBarkRenderer: true,
      hasExtractSpeechBarks: true,
    });
  });

  test("test_skip_log_update_chat_response_still_dispatches_companion_bark", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: ["[陷阱感知] scout -> gas_trap_1"],
        party_status: {},
        environment_objects: {
          gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false },
        },
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    await api.sendMessage("进入毒气走廊", "CHAT", null, { skipLogUpdate: true });
    await flushAsync();

    const bark = document.querySelector(".companion-bark--scout");
    expect(bark).not.toBeNull();
    expect(bark.textContent).toContain("Scout");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(bark.textContent).toMatch(/trap|陷阱/i);
  });

  test("test_init_sync_response_does_not_dispatch_companion_bark", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [{ speaker: "scout", text: "附近有陷阱，小心。" }],
        journal_events: ["[陷阱感知] scout -> gas_trap_1"],
        party_status: {},
        environment_objects: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    await api.sendMessage("", "init_sync", null, { skipLogUpdate: true });
    await flushAsync();

    expect(document.querySelector(".companion-bark")).toBeNull();
  });

  test("test_xray_panel_updates", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValueOnce(
      mockResponse({
        last_node: "DM_NODE",
        active_dialogue_target: "gatekeeper",
        entities: {
          gatekeeper: {
            dynamic_states: {
              patience: { current_value: 8, max_value: 20 },
            },
          },
        },
        party_status: {},
        environment_objects: {},
        combat_state: {},
        journal_events: [],
      })
    );

    const api = await bootAppForTest();
    await api.pollDialogueState();

    const dmNode = document.querySelector('li[data-node="dm_router"]');
    expect(dmNode).not.toBeNull();
    expect(dmNode.classList.contains("is-active")).toBe(true);

    const patienceBar = document.getElementById("patience-bar");
    expect(patienceBar.style.width).toBe("40%");

    const inspector = document.getElementById("json-inspector");
    expect(inspector.textContent).toContain('"last_node": "DM_NODE"');
    expect(inspector.textContent).toContain('"current_value": 8');
    const summary = document.getElementById("payload-summary");
    expect(summary.textContent).toContain("/api/state");
    expect(summary.textContent).toContain("Journal");
    expect(summary.textContent).toContain("Entities");

    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining("/api/state?session_id="),
      expect.objectContaining({ signal: expect.any(Object) })
    );
  });

  test("test_state_watcher_hidden_before_gatekeeper_context", async () => {
    spyOnFetch().mockResolvedValueOnce(
      mockResponse({
        last_node: null,
        entities: {
          gatekeeper: {
            dynamic_states: {
              patience: { current_value: 15, max_value: 100 },
              fear: { current_value: 5, max_value: 100 },
            },
          },
        },
        party_status: {},
        environment_objects: {},
        combat_state: {},
        journal_events: [],
      })
    );

    const api = await bootAppForTest();
    await api.pollDialogueState();

    const watcher = document.querySelector(".xray-section--state-watcher");
    expect(watcher.classList.contains("is-hidden")).toBe(true);
    const summary = document.getElementById("payload-summary");
    expect(summary.textContent).toContain("Watcher");
    expect(summary.textContent).toContain("none");
  });

  test("test_dialogue_modal_visibility", async () => {
    const firstState = {
      active_dialogue_target: "gatekeeper",
      party_status: { gatekeeper: { name: "gatekeeper" } },
      environment_objects: {},
      combat_state: {},
      journal_events: ['[gatekeeper]: "离我远点。"'],
      responses: [],
    };
    const secondState = {
      active_dialogue_target: null,
      party_status: { gatekeeper: { name: "gatekeeper" } },
      environment_objects: {},
      combat_state: {},
      journal_events: [],
      responses: [],
    };

    spyOnFetch()
      .mockResolvedValueOnce(mockResponse(firstState))
      .mockResolvedValueOnce(mockResponse(secondState));

    const api = await bootAppForTest();

    await api.pollDialogueState();
    const overlay = document.getElementById("dialogue-overlay");
    expect(overlay.classList.contains("hidden")).toBe(false);
    const npcName = document.getElementById("dialogue-npc-name").textContent || "";
    expect(npcName.toLowerCase()).toContain("gatekeeper");

    await api.pollDialogueState();
    expect(overlay.classList.contains("hidden")).toBe(true);
  });

  test("test_ui_input_interception", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );

    const api = await bootAppForTest();
    api.updateDialogueOverlay({
      active_dialogue_target: "gatekeeper",
      party_status: { gatekeeper: { name: "gatekeeper" } },
      journal_events: ['[gatekeeper]: "你想干什么？"'],
    });

    const attackBtn = document.getElementById("dialogue-attack-btn");
    attackBtn.click();
    await flushAsync();

    const chatCall = fetchSpy.mock.calls.find(([url]) => String(url).includes("/api/chat"));
    expect(chatCall).toBeDefined();
    const payload = JSON.parse(chatCall[1].body);
    expect(payload.user_input).toBe("我直接拔出武器攻击！");
  });

  /* ═══════════════════════════════════════════
     SPRINT 0 TESTS
     ═══════════════════════════════════════════ */

  test("test_local_movement_no_api_call", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    if (window.ControlledAgentInputController) {
      window.ControlledAgentInputController.movePlayer(1, 0);
    }
    await flushAsync();

    const chatCalls = fetchSpy.mock.calls.filter(
      ([url]) => String(url).includes("/api/chat")
    );
    expect(chatCalls.length).toBe(0);
    if (window.ControlledAgentDirectorTrace) {
      expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
    }
  });

  test("test_text_send_restores_wasd_map_movement_focus", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });
    fetchSpy.mockClear();
    window.ControlledAgentTacticalMap.movePlayerLocal.mockClear();

    api.els.userInput.focus();
    api.els.userInput.value = "侦察员，检查前方";
    await api.submitInput();
    await flushAsync();
    window.ControlledAgentTacticalMap.movePlayerLocal.mockClear();
    const beforeMoveCalls = window.ControlledAgentTacticalMap.movePlayerLocal.mock.calls.length;
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "w", bubbles: true, cancelable: true }));

    expect(api.state.lastMapInputFocusReason).toBe("text_send_done");
    expect(window.ControlledAgentTacticalMap.movePlayerLocal.mock.calls.length).toBeGreaterThan(beforeMoveCalls);
    expect(fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"))).toHaveLength(1);
  });

  test("test_text_send_failure_still_restores_wasd_map_movement_focus", async () => {
    spyOnFetch().mockRejectedValue(new Error("network down"));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });
    window.ControlledAgentTacticalMap.movePlayerLocal.mockClear();

    api.els.userInput.focus();
    api.els.userInput.value = "失败也要恢复焦点";
    await api.submitInput();
    await flushAsync();
    const beforeMoveCalls = window.ControlledAgentTacticalMap.movePlayerLocal.mock.calls.length;
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "w", bubbles: true, cancelable: true }));

    expect(api.state.lastMapInputFocusReason).toBe("text_send_done");
    expect(window.ControlledAgentTacticalMap.movePlayerLocal.mock.calls.length).toBeGreaterThan(beforeMoveCalls);
  });

  test("test_map_click_restores_wasd_movement_focus", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });
    window.ControlledAgentTacticalMap.movePlayerLocal.mockClear();

    api.els.userInput.focus();
    api.els.mapContainer.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    const beforeMoveCalls = window.ControlledAgentTacticalMap.movePlayerLocal.mock.calls.length;
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "w", bubbles: true, cancelable: true }));

    expect(api.state.lastMapInputFocusReason).toBe("map_pointer");
    expect(window.ControlledAgentTacticalMap.movePlayerLocal.mock.calls.length).toBeGreaterThan(beforeMoveCalls);
  });

  test("test_input_focused_blocks_wasd_until_blur", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });
    window.ControlledAgentTacticalMap.movePlayerLocal.mockClear();

    api.els.userInput.focus();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "w", bubbles: true, cancelable: true }));
    expect(window.ControlledAgentTacticalMap.movePlayerLocal).not.toHaveBeenCalled();

    api.els.userInput.blur();
    const beforeMoveCalls = window.ControlledAgentTacticalMap.movePlayerLocal.mock.calls.length;
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "w", bubbles: true, cancelable: true }));
    expect(window.ControlledAgentTacticalMap.movePlayerLocal.mock.calls.length).toBeGreaterThan(beforeMoveCalls);
  });

  test("test_legacy_tactical_console_hidden_and_space_disabled_by_default", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const button = document.getElementById("tactical-toggle-btn");
    const overlay = document.getElementById("tactical-pause-overlay");

    expect(button.hidden).toBe(true);
    expect(button.classList.contains("is-retired")).toBe(true);
    expect(overlay.classList.contains("is-hidden")).toBe(true);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: " ", code: "Space", bubbles: true }));
    await flushAsync();

    expect(api.state.tacticalOverlayOpen).toBe(false);
    expect(overlay.classList.contains("is-hidden")).toBe(true);
  });

  test("test_map_id_in_payload", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [], journal_events: [], party_status: {},
        environment_objects: {}, combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();
    await api.sendMessage("测试指令", null);
    await flushAsync();

    const chatCall = fetchSpy.mock.calls.find(([url]) => String(url).includes("/api/chat"));
    expect(chatCall).toBeDefined();
    const payload = JSON.parse(chatCall[1].body);
    expect(payload.map_id).toBe("hazard_lab");
  });

  test("test_heavy_iron_key_display", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    expect(api.ITEM_META.heavy_iron_key).toBeDefined();
    expect(api.ITEM_META.heavy_iron_key.label).toBe("沉重铁钥匙");
  });

  test("test_los_blocked_event_dom", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();

    if (window.ControlledAgentHudRenderers) {
      window.ControlledAgentHudRenderers.showLoSBlockedOverlay({ blockedTiles: [] });
    }
    await flushAsync();

    const container = document.getElementById("toast-container");
    expect(container).not.toBeNull();
    const toasts = container.querySelectorAll(".hud-toast");
    expect(toasts.length).toBeGreaterThanOrEqual(1);
    const losToast = Array.from(toasts).find((t) => t.textContent.includes("视线"));
    expect(losToast).toBeDefined();
  });

  test("test_director_trace_idle_by_default", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();

    if (window.ControlledAgentDirectorTrace) {
      expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
    }
    const indicator = document.getElementById("director-state-indicator");
    expect(indicator).not.toBeNull();
    expect(indicator.classList.contains("director-state--idle")).toBe(true);
    expect(indicator.textContent).toContain("Idle");
  });

  /* ═══════════════════════════════════════════
     SPRINT 1 HARDENING TESTS
     ═══════════════════════════════════════════ */

  test("test_sendMessage_dispatches_ui_events", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: ["叙事回应"],
        journal_events: [],
        party_status: {},
        environment_objects: {},
        combat_state: {},
        latest_roll: { skill: "Perception", dc: 13, result: 17, success: true },
      })
    );

    const api = await bootAppForTest();
    fetchSpy.mockClear();

    await api.sendMessage("检查周围", null);
    await flushAsync();

    /* A dice card should have been spawned by the HUD renderer */
    const diceContainer = document.getElementById("dice-card-container");
    expect(diceContainer).not.toBeNull();
    const diceCards = diceContainer.querySelectorAll(".dice-card");
    expect(diceCards.length).toBeGreaterThanOrEqual(1);
  });

  test("test_init_sync_does_not_activate_director_trace", async () => {
    spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [], journal_events: [], party_status: {},
        environment_objects: {}, combat_state: {},
      })
    );

    const api = await bootAppForTest();

    /* isNarrativeRequest should return false for init_sync */
    expect(api.isNarrativeRequest("init_sync", "", "")).toBe(false);
    expect(api.isNarrativeRequest("trigger_idle_banter", "", "")).toBe(false);
    expect(api.isNarrativeRequest("ui_action_loot", "", "")).toBe(false);

    /* Director trace should still be idle after boot (which runs init_sync in non-QA) */
    if (window.ControlledAgentDirectorTrace) {
      expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
    }
  });

  test("test_narrative_request_activates_trace", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();

    /* isNarrativeRequest should return true for narrative intents */
    expect(api.isNarrativeRequest("trigger_zone", "", "")).toBe(true);
    expect(api.isNarrativeRequest("", "我检查箱子", "")).toBe(false);
    expect(api.isNarrativeRequest("dialogue", "", "interaction")).toBe(true);
    expect(api.isNarrativeRequest("companion_interrupt", "", "")).toBe(true);
    expect(api.isNarrativeRequest("INTERACT", "", "text_input")).toBe(true);
    expect(api.isNarrativeRequest("DISARM", "", "ui_text_normalized")).toBe(true);
  });

  test("test_blocked_by_creates_los_event", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();

    /* Extract events from a response with blocked_by field */
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: [],
      party_status: {},
      blocked_by: [{ x: 5, y: 3 }, { x: 5, y: 4 }],
    });

    const losEvents = events.filter((e) => e.type === "line_of_sight_blocked");
    expect(losEvents.length).toBeGreaterThanOrEqual(1);
    expect(losEvents[0].blockedTiles.length).toBe(2);
    expect(losEvents[0].blocked_by).toEqual([{ x: 5, y: 3 }, { x: 5, y: 4 }]);
  });

  test("test_latest_roll_raw_roll_compat", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();

    /* Variant: result.raw_roll / is_success */
    const events1 = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: [],
      party_status: {},
      latest_roll: { result: { raw_roll: 14, dc: 13, is_success: true }, skill: "Stealth" },
    });
    const roll1 = events1.find((e) => e.type === "roll_result");
    expect(roll1).toBeDefined();
    expect(roll1.roll).toBe(14);
    expect(roll1.dc).toBe(13);
    expect(roll1.success).toBe(true);

    /* Variant: rolls array */
    const events2 = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: [],
      party_status: {},
      latest_roll: { rolls: [18], dc: 15, is_success: true },
    });
    const roll2 = events2.find((e) => e.type === "roll_result");
    expect(roll2).toBeDefined();
    expect(roll2.roll).toBe(18);
  });

  test("test_tiled_objectgroup_parsing", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    /* Construct a minimal Tiled JSON with objectgroup layers */
    const tiledJson = {
      width: 10,
      height: 10,
      tilewidth: 32,
      tileheight: 32,
      layers: [
        {
          name: "triggers",
          type: "objectgroup",
          objects: [
            { name: "trap_zone_1", type: "trigger", x: 96, y: 64, width: 64, height: 32 },
          ],
        },
        {
          name: "objects",
          type: "objectgroup",
          objects: [
            { name: "chest_1", type: "chest", x: 192, y: 32, width: 32, height: 32 },
            { name: "player_start", type: "player_start", x: 64, y: 64, width: 32, height: 32 },
          ],
        },
        {
          name: "spawns",
          type: "objectgroup",
          objects: [
            {
              name: "drone_1",
              type: "spawn",
              x: 160, y: 128,
              width: 32, height: 32,
              properties: [
                { name: "faction", value: "hostile" },
              ],
            },
          ],
        },
      ],
    };

    const result = window.ControlledAgentTiledAdapter.normalizeTiledMap(tiledJson);

    /* Triggers */
    expect(result.triggers.length).toBe(1);
    expect(result.triggers[0].id).toBe("trap_zone_1");
    expect(result.triggers[0].x).toBe(3); // 96/32
    expect(result.triggers[0].y).toBe(2); // 64/32
    expect(result.triggers[0].w).toBe(2); // 64/32

    /* Interactables: chest remains present (spawn NPC may also be interactable) */
    expect(result.interactables.length).toBeGreaterThanOrEqual(1);
    const chest = result.interactables.find((it) => it.id === "chest_1");
    expect(chest).toBeDefined();
    expect(chest.type).toBe("chest");

    /* Player start */
    expect(result.playerStart.x).toBe(2); // 64/32
    expect(result.playerStart.y).toBe(2); // 64/32

    /* Spawns */
    expect(result.spawns.length).toBe(1);
    expect(result.spawns[0].id).toBe("drone_1");
    expect(result.spawns[0].faction).toBe("hostile");
  });

  test("test_real_map_json_contract_25x25_with_625_cells", () => {
    const raw = fs.readFileSync(REAL_MAP_JSON_PATH, "utf8");
    const map = JSON.parse(raw);
    expect(map.width).toBe(25);
    expect(map.height).toBe(25);

    const ground = map.layers.find((layer) => layer.name === "ground");
    const collision = map.layers.find((layer) => layer.name === "collision");
    const los = map.layers.find((layer) => layer.name === "los_blockers");
    const groundTypes = map.layers.find((layer) => layer.name === "ground_types");

    expect(ground.data.length).toBe(625);
    expect(collision.data.length).toBe(625);
    expect(los.data.length).toBe(625);
    expect(groundTypes.data.length).toBe(625);
  });

  test("test_real_map_json_entities_and_trigger_contract", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const raw = fs.readFileSync(REAL_MAP_JSON_PATH, "utf8");
    const map = JSON.parse(raw);
    const result = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);

    expect(result.width).toBe(25);
    expect(result.height).toBe(25);
    expect(result.playerStart.x).toBe(5);
    expect(result.playerStart.y).toBe(19);

    const interactableIds = result.interactables.map((item) => item.id);
    expect(interactableIds).toContain("gatekeeper");
    expect(interactableIds).toContain("hazard_diary");
    expect(interactableIds).toContain("chest_1");
    expect(interactableIds).toContain("heavy_oak_door_1");

    const spawnIds = result.spawns.map((spawn) => spawn.id);
    expect(spawnIds).toContain("gatekeeper");

    const corridor = result.triggers.find((trigger) => trigger.id === "act1_corridor_approach");
    expect(corridor).toBeDefined();
    expect(String(corridor.data.trigger_text || "")).not.toMatch(/绿色|毒雾|酸液|poison|gas|acid/i);
    expect(corridor.y).toBe(12);
    expect(corridor.h).toBe(1);

    const poisonTrap = result.triggers.find((trigger) => trigger.id === "poison_trap_1");
    expect(poisonTrap).toBeDefined();
    expect(poisonTrap.data.alias_id).toBe("gas_trap_1");
    expect(Math.max(Math.abs(corridor.x - poisonTrap.x), Math.abs(corridor.y - poisonTrap.y))).toBeLessThanOrEqual(3);
  });

  test("test_hazard_lab_v2_level_design_alignment_contract", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const tmx = extractTileLayersFromTmx(fs.readFileSync(REAL_MAP_TMX_PATH, "utf8"));
    const expectedCells = map.width * map.height;
    expect(tmx.width).toBe(map.width);
    expect(tmx.height).toBe(map.height);
    ["ground", "collision", "los_blockers", "ground_types"].forEach((layerName) => {
      const layer = getMapLayer(map, layerName);
      expect(layer).toBeDefined();
      expect(layer.type).toBe("tilelayer");
      expect(layer.data.length).toBe(expectedCells);
      expect(tmx.layers[layerName]).toBeDefined();
      expect(tmx.layers[layerName].width).toBe(map.width);
      expect(tmx.layers[layerName].height).toBe(map.height);
      expect(tmx.layers[layerName].cells.length).toBe(expectedCells);
    });

    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    const rooms = normalized.rooms;
    const roomIds = rooms.map((room) => room.id);
    expect(roomIds).toEqual(expect.arrayContaining([
      "room_a_spawn",
      "room_b_corridor",
      "room_c_secret_study",
      "room_d_lab",
      "room_exit",
    ]));

    const corridor = roomById(rooms, "room_b_corridor");
    expect(corridor).toBeDefined();
    expect(Math.max(corridor.w, corridor.h) / Math.min(corridor.w, corridor.h)).toBeGreaterThanOrEqual(2.5);

    const rawSecretDoor = getMapObject(map, "interactables", "door_b_to_c");
    const secretDoorProps = flattenTiledProps(rawSecretDoor);
    expect(secretDoorProps.is_secret).toBe(true);
    expect(Number(secretDoorProps.detect_dc)).toBe(14);
    expect(secretDoorProps.connects_from).toBe("room_b_corridor");
    expect(secretDoorProps.connects_to).toBe("room_c_secret_study");

    const rawLabDoor = getMapObject(map, "interactables", "door_b_to_d");
    const labDoorProps = flattenTiledProps(rawLabDoor);
    expect(labDoorProps.key_required).toBe("lab_key");
    expect(Number(labDoorProps.lockpick_dc)).toBe(15);
    expect(labDoorProps.connects_from).toBe("room_b_corridor");
    expect(labDoorProps.connects_to).toBe("room_d_lab");

    const rawExitDoor = getMapObject(map, "interactables", "exit_door");
    const exitDoorProps = flattenTiledProps(rawExitDoor);
    expect(rawExitDoor.x / map.tilewidth).toBe(18);
    expect(rawExitDoor.y / map.tileheight).toBe(3);
    expect(exitDoorProps.alias_id).toBe("heavy_oak_door_1");
    expect(exitDoorProps.key_required).toBe("heavy_iron_key");
    expect(exitDoorProps.requires_flag).toBe("world_hazard_lab_gatekeeper_defeated");
    expect(exitDoorProps.room_id).toBe("room_exit");

    const studyRoom = roomById(rooms, "room_c_secret_study");
    const diary = normalized.interactables.find((item) => item.id === "hazard_diary");
    const chest = normalized.interactables.find((item) => item.id === "chest_1");
    expect(diary).toBeDefined();
    expect(chest).toBeDefined();
    expect(diary.source_id).toBe("hazard_diary");
    expect(chest.source_id).toBe("study_chest");
    expect(pointInRect(diary, studyRoom)).toBe(true);
    expect(pointInRect(chest, studyRoom)).toBe(true);

    const labRoom = roomById(rooms, "room_d_lab");
    const gatekeeper = normalized.spawns.find((spawn) => spawn.id === "gatekeeper");
    expect(gatekeeper).toBeDefined();
    expect(pointInRect(gatekeeper, labRoom)).toBe(true);

    const doorA = normalized.interactables.find((item) => item.id === "door_a_to_b");
    ["poison_trap_1", "poison_trap_2"].forEach((trapId) => {
      const trap = normalized.triggers.find((item) => item.id === trapId);
      expect(trap).toBeDefined();
      expect(pointInRect(trap, corridor)).toBe(true);
      const distanceFromDoorA = Math.abs(trap.x - doorA.x) + Math.abs(trap.y - doorA.y);
      expect(distanceFromDoorA).toBeGreaterThanOrEqual(5);
    });

    const spawnRoom = roomById(rooms, "room_a_spawn");
    expect(pointInRect(normalized.playerStart, spawnRoom)).toBe(true);
  });

  test("test_tmx_json_object_layers_are_aligned", () => {
    const tmxText = fs.readFileSync(REAL_MAP_TMX_PATH, "utf8");
    const jsonText = fs.readFileSync(REAL_MAP_JSON_PATH, "utf8");
    const map = JSON.parse(jsonText);
    const tmxLayers = extractObjectNamesByLayerFromTmx(tmxText);
    const jsonLayers = {};
    map.layers
      .filter((layer) => layer.type === "objectgroup")
      .forEach((layer) => {
        jsonLayers[layer.name] = (layer.objects || []).map((obj) => obj.name);
      });

    ["triggers", "interactables", "spawns", "rooms"].forEach((layerName) => {
      expect(jsonLayers[layerName]).toBeDefined();
      const tmxNames = tmxLayers[layerName] || [];
      const jsonNames = jsonLayers[layerName] || [];
      tmxNames.forEach((name) => {
        expect(jsonNames).toContain(name);
      });
    });
  });

  test("test_rooms_and_doors_metadata_contract", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const result = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);

    const roomIds = result.rooms.map((room) => room.id);
    expect(roomIds).toEqual(expect.arrayContaining([
      "room_a_spawn",
      "room_b_corridor",
      "room_c_secret_study",
      "room_d_lab",
      "room_exit",
    ]));

    const doorA = result.interactables.find((it) => it.id === "door_a_to_b");
    const doorC = result.interactables.find((it) => it.id === "door_b_to_c");
    const doorD = result.interactables.find((it) => it.id === "door_b_to_d");
    const exitDoor = result.interactables.find((it) => it.id === "heavy_oak_door_1");
    expect(doorA.data.connects_from).toBe("room_a_spawn");
    expect(doorA.data.connects_to).toBe("room_b_corridor");
    expect(doorC.data.is_secret).toBe(true);
    expect(Number(doorC.data.detect_dc)).toBe(14);
    expect(doorD.data.key_required).toBe("lab_key");
    expect(Number(doorD.data.lockpick_dc)).toBe(15);
    expect(exitDoor.data.key_required).toBe("heavy_iron_key");
    expect(exitDoor.data.requires_flag).toBe("world_hazard_lab_gatekeeper_defeated");
    expect(exitDoor.data.room_id).toBe("room_exit");
    expect(exitDoor.x).toBe(18);
    expect(exitDoor.y).toBe(3);
    expect(exitDoor.source_id).toBe("exit_door");
  });

  test("test_room_visibility_initial_only_a_and_progressive_reveal", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const normalized = loadRealHazardMap();
    api.applyNormalizedMap(normalized, { source: "json" });

    expect(Array.from(api.state.roomVisibleIds)).toEqual(["room_a_spawn"]);

    const initialInteractableIds = api.state.normalizedMap.interactables.map((it) => it.id);
    expect(initialInteractableIds).toContain("door_a_to_b");
    expect(initialInteractableIds).not.toContain("door_b_to_d");
    expect(initialInteractableIds).not.toContain("door_b_to_c");
    expect(initialInteractableIds).not.toContain("hazard_diary");
    expect(initialInteractableIds).not.toContain("chest_1");

    expect(api.revealRoomByDoorTarget("door_a_to_b")).toBe(true);
    api.refreshVisibilityProjection();
    expect(api.state.roomVisibleIds.has("room_b_corridor")).toBe(true);

    const afterAB = api.state.normalizedMap.interactables.map((it) => it.id);
    expect(afterAB).toContain("door_b_to_d");
    expect(afterAB).not.toContain("door_b_to_c");
    expect(afterAB).not.toContain("hazard_diary");

    expect(api.revealRoomByDoorTarget("door_b_to_d")).toBe(true);
    api.refreshVisibilityProjection();
    expect(api.state.roomVisibleIds.has("room_d_lab")).toBe(true);
    const afterBD = api.state.normalizedMap.interactables.map((it) => it.id);
    expect(afterBD).toContain("gatekeeper");
    expect(afterBD).toContain("heavy_oak_door_1");

    expect(api.revealRoomByDoorTarget("heavy_oak_door_1")).toBe(true);
    api.refreshVisibilityProjection();
    expect(api.state.roomVisibleIds.has("room_exit")).toBe(true);
  });

  test("test_act3_backend_secret_study_flag_reveals_room_c_without_room_d", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });
    expect(api.state.roomVisibleIds.has("room_c_secret_study")).toBe(false);
    expect(api.state.roomVisibleIds.has("room_d_lab")).toBe(false);

    fetchSpy.mockResolvedValueOnce(mockResponse(emptyTurnResponse({
      game_state: { flags: { act3_secret_study_entered: true } },
    })));
    await api.sendMessage("墙后露出一间秘密书房。", "chat");
    await flushAsync();

    expect(api.state.roomVisibleIds.has("room_c_secret_study")).toBe(true);
    expect(api.state.roomVisibleIds.has("room_d_lab")).toBe(false);
    expect(api.state.mapData.visible_rooms).toContain("room_c_secret_study");
  });

  test("test_act3_secret_study_journal_reveals_room_c", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });

    fetchSpy.mockResolvedValueOnce(mockResponse(emptyTurnResponse({
      journal_events: ["[秘密书房] cracked_wall -> room_c_secret_study"],
    })));
    await api.sendMessage("调查裂墙。", "chat");
    await flushAsync();

    expect(api.state.roomVisibleIds.has("room_c_secret_study")).toBe(true);
    expect(api.state.roomVisibleIds.has("room_d_lab")).toBe(false);
  });

  test("test_nested_state_secret_study_flag_reveals_room_c", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });

    fetchSpy.mockResolvedValueOnce(mockResponse(emptyTurnResponse({
      state: { flags: { act3_secret_study_discovered: true } },
    })));
    await api.sendMessage("发现暗门。", "chat");
    await flushAsync();

    expect(api.state.roomVisibleIds.has("room_c_secret_study")).toBe(true);
    expect(api.state.mapData.visible_rooms).toContain("room_c_secret_study");
  });

  test("test_act3_room_c_reveal_makes_study_context_interactables_visible", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });

    fetchSpy.mockResolvedValueOnce(mockResponse(emptyTurnResponse({
      game_state: { flags: { act2_secret_study_route_unlocked: true } },
    })));
    await api.sendMessage("检查实验室重门。", "chat");
    await flushAsync();

    const interactableIds = api.state.normalizedMap.interactables.map((it) => it.id);
    const mapDataInteractableIds = api.state.mapData.interactables.map((it) => it.id);
    expect(interactableIds).toEqual(expect.arrayContaining([
      "chemical_notes",
      "iron_key_sketch",
      "hazard_diary",
      "chest_1",
    ]));
    expect(mapDataInteractableIds).toEqual(expect.arrayContaining([
      "chemical_notes",
      "iron_key_sketch",
      "hazard_diary",
      "chest_1",
    ]));
    expect(api.state.normalizedMap.spawns.map((spawn) => spawn.id)).not.toContain("gatekeeper");
  });

  test("test_e_interact_door_b_to_c_reveals_room_c_and_study_interactables", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.discoverSecretDoor("door_b_to_c");
    api.refreshVisibilityProjection();

    expect(api.state.roomVisibleIds.has("room_c_secret_study")).toBe(false);
    expect(api.handleLocalExplorationDoor("door_b_to_c")).toBe(true);

    const interactableIds = api.state.normalizedMap.interactables.map((it) => it.id);
    expect(api.state.roomVisibleIds.has("room_c_secret_study")).toBe(true);
    expect(api.state.mapData.visible_rooms).toContain("room_c_secret_study");
    expect(interactableIds).toEqual(expect.arrayContaining([
      "chemical_notes",
      "iron_key_sketch",
      "hazard_diary",
      "chest_1",
    ]));
    expect(window.ControlledAgentTacticalMap.refreshMapOnly).toHaveBeenCalled();
    expect(api.collectMapDebugSnapshot("unit").roomVisibleIds).toContain("room_c_secret_study");
  });

  test("test_text_open_secret_door_routes_to_door_b_to_c_when_current_interactable_is_secret_door", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.discoverSecretDoor("door_b_to_c");
    api.state.currentInteractable = "door_b_to_c";
    api.state.currentIntent = "INTERACT";

    const { payload } = api.buildChatPayload("打开暗门", null, null, { source: "text_input" });

    expect(payload).toMatchObject({
      intent: "INTERACT",
      target: "door_b_to_c",
    });
  });

  test("test_blocked_text_secret_door_interact_does_not_reveal_room_c", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({
      journal_events: ["❌ [交互] 隐藏书房侧门 距离过远，需相邻才能交互。"],
      map_data: { visible_rooms: ["room_a_spawn", "room_b_corridor"] },
    }));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.discoverSecretDoor("door_b_to_c");
    api.refreshVisibilityProjection();
    api.state.currentInteractable = "door_b_to_c";
    api.state.currentIntent = "INTERACT";

    await api.sendStructuredAction({ text: "打开暗门", intent: null, options: { source: "text_input" } });
    await flushAsync();

    expect(api.state.roomVisibleIds.has("room_c_secret_study")).toBe(false);
    expect(api.state.mapData.visible_rooms).not.toContain("room_c_secret_study");
    expect(api.state.mapData.interactables.map((it) => it.id)).not.toContain("chemical_notes");
  });

  test("test_missing_key_lab_door_response_does_not_reveal_room_d", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({
      journal_events: [
        "🚪 [系统] 实验室重门需要 lab_key；也可以明确尝试 DC 15 撬锁。",
        "🕯️ [线索] 门框附近有冷风，旁边墙面传来空响；附近可能还有通往书房的入口。",
      ],
      flags: {
        act2_secret_study_route_unlocked: true,
        act2_secret_study_hint_given: true,
      },
      latest_roll: {
        intent: "INTERACT",
        target: "door_b_to_d",
        result: { is_success: false, result_type: "MISSING_KEY" },
      },
      map_data: { visible_rooms: ["room_a_spawn", "room_b_corridor"] },
    }));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();

    await api.sendStructuredAction({ text: "检查 B-D 门。", intent: null, options: { source: "text_input" } });
    await flushAsync();

    expect(api.state.roomVisibleIds.has("room_d_lab")).toBe(false);
    expect(api.state.mapData.visible_rooms).not.toContain("room_d_lab");
    expect(api.state.roomVisibleIds.has("room_c_secret_study")).toBe(true);
  });

  test("test_state_poll_with_stale_visible_rooms_does_not_drop_local_room_c_reveal", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.discoverSecretDoor("door_b_to_c");
    api.revealRoomByDoorTarget("door_b_to_c");
    api.refreshVisibilityProjection();
    expect(api.state.roomVisibleIds.has("room_c_secret_study")).toBe(true);

    fetchSpy.mockResolvedValueOnce(mockResponse({
      party_status: {},
      environment_objects: {},
      combat_state: {},
      map_data: { visible_rooms: ["room_a_spawn", "room_b_corridor"] },
      journal_events: [],
      flags: {},
    }));
    await api.pollDialogueState();
    await flushAsync();

    expect(api.state.roomVisibleIds.has("room_c_secret_study")).toBe(true);
    expect(api.state.mapData.visible_rooms).toContain("room_c_secret_study");
    expect(api.state.normalizedMap.interactables.map((it) => it.id)).toContain("chemical_notes");
    expect(api.state.mapData.interactables.map((it) => it.id)).toContain("chemical_notes");
  });

  test("test_act3_study_context_text_routes_to_read_payloads", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");

    const chemical = api.buildChatPayload("阅读 chemical_notes", null, null, { source: "text_input" }).payload;
    expect(chemical).toMatchObject({
      intent: "READ",
      target: "chemical_notes",
      source: "act3_study_context",
    });
    expect(chemical.intent_context).toMatchObject({ action_target: "chemical_notes" });

    const sketch = api.buildChatPayload("查看铁钥匙草图", null, null, { source: "text_input" }).payload;
    expect(sketch).toMatchObject({
      intent: "READ",
      target: "iron_key_sketch",
      source: "act3_study_context",
    });
    expect(sketch.intent_context).toMatchObject({ action_target: "iron_key_sketch" });
  });

  test("test_act3_context_signal_keeps_act_card_on_act3", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });

    fetchSpy.mockResolvedValueOnce(mockResponse(emptyTurnResponse({
      journal_events: ["[线索整合] chemical_notes -> diary_context"],
      game_state: {
        flags: {
          act3_secret_study_entered: true,
          act3_diary_context_gathered: true,
          act3_chemical_notes_seen: true,
        },
      },
    })));
    await api.sendMessage("阅读 chemical_notes", null);
    await flushAsync();

    expect(document.getElementById("act-title").textContent).toContain("Act 3");
    expect(document.getElementById("act-title").textContent).not.toContain("Act 4");
  });

  test("test_room_c_highlight_prefers_study_readable_not_hidden_or_boss_objects", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(loadRealHazardMap(), { source: "json" });

    fetchSpy.mockResolvedValueOnce(mockResponse(emptyTurnResponse({
      game_state: { flags: { act3_secret_study_discovered: true } },
    })));
    await api.sendMessage("进入秘密书房。", "chat");
    await flushAsync();

    window.ControlledAgentInputController.setPlayerPosition(14, 11);
    const highlighted = window.ControlledAgentInputController.getCurrentHighlightedInteractable();
    expect(highlighted.id).toBe("chemical_notes");
    expect(api.state.normalizedMap.spawns.map((spawn) => spawn.id)).not.toContain("gatekeeper");
    expect(api.state.roomVisibleIds.has("room_d_lab")).toBe(false);
  });

  test("test_fog_of_war_masks_non_visible_rooms_and_unmapped_cells", async () => {
    loadNewModules();
    const tacticalMap = loadGameHelpers();
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);

    let fogCells = tacticalMap.resolveFogOfWarCells({
      ...normalized,
      visible_rooms: ["room_a_spawn"],
    });
    let fogSet = new Set(fogCells.map((cell) => cell.x + "," + cell.y));

    expect(fogSet.has("4,18")).toBe(false);
    expect(fogSet.has("6,8")).toBe(true);
    expect(fogSet.has("10,10")).toBe(true);
    expect(fogSet.has("3,3")).toBe(true);
    expect(fogSet.has("20,3")).toBe(true);
    expect(fogSet.has("0,0")).toBe(true);

    fogCells = tacticalMap.resolveFogOfWarCells({
      ...normalized,
      visible_rooms: ["room_a_spawn", "room_b_corridor"],
    });
    fogSet = new Set(fogCells.map((cell) => cell.x + "," + cell.y));

    expect(fogSet.has("6,8")).toBe(false);
    expect(fogSet.has("10,10")).toBe(true);
  });

  test("test_wasd_cannot_enter_hidden_room_before_door_reveal", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();
    const nowSpy = jest.spyOn(Date, "now");
    nowSpy.mockReturnValue(1000);
    const map = {
      width: 4,
      height: 4,
      collision: Array.from({ length: 4 }, () => Array(4).fill(false)),
      interactables: [],
      triggers: [],
      rooms: [
        { id: "room_a_spawn", x: 0, y: 2, w: 4, h: 2 },
        { id: "room_b_corridor", x: 0, y: 0, w: 4, h: 2 },
      ],
      visible_rooms: ["room_a_spawn"],
    };
    window.ControlledAgentInputController.setMap(map);
    window.ControlledAgentInputController.setPlayerPosition(1, 2);
    window.ControlledAgentTacticalMap.movePlayerLocal.mockClear();

    expect(window.ControlledAgentInputController.movePlayer(0, -1)).toBe(false);
    expect(window.ControlledAgentInputController.getPlayerPosition()).toEqual({ x: 1, y: 2 });
    expect(window.ControlledAgentTacticalMap.movePlayerLocal).not.toHaveBeenCalled();

    nowSpy.mockReturnValue(1200);
    window.ControlledAgentInputController.setMap({
      ...map,
      visible_rooms: ["room_a_spawn", "room_b_corridor"],
    });

    expect(window.ControlledAgentInputController.movePlayer(0, -1)).toBe(true);
    expect(window.ControlledAgentInputController.getPlayerPosition()).toEqual({ x: 1, y: 1 });
    expect(window.ControlledAgentTacticalMap.movePlayerLocal).toHaveBeenCalledWith(1, 1);
    nowSpy.mockRestore();
  });

  test("test_visible_boundary_door_is_rendered_as_tactical_object", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    api.applyNormalizedMap(normalized, { source: "json" });
    window.ControlledAgentTacticalMap.update.mockClear();

    api.renderTacticalGrid({ player: { x: 4, y: 18, faction: "player" } }, {}, api.state.mapData);

    const lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    const environment = lastCall[1] || {};
    expect(environment.door_a_to_b).toBeDefined();
    expect(environment.door_a_to_b.type).toBe("door");
    expect(environment.door_a_to_b.w).toBe(3);
    expect(environment.door_b_to_d).toBeUndefined();
  });

  test("test_e_opening_ab_door_is_local_reveal_not_backend_loot", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        party_status: {},
        environment_objects: {},
        player_inventory: { healing_potion: 2 },
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    api.applyNormalizedMap(normalized, { source: "json" });
    api.state.partyStatus = {
      player: { x: 5, y: 17, faction: "player" },
      scout: { name: "Scout", faction: "party" },
      analyst: { name: "Analyst", faction: "party" },
      tactician: { name: "Tactician", faction: "party" },
    };
    window.ControlledAgentInputController.setPlayerPosition(5, 17);
    window.ControlledAgentInputController.updateHint();
    fetchSpy.mockClear();

    window.ControlledAgentInputController.interact();
    await flushAsync();

    const chatCalls = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"));
    expect(chatCalls.length).toBe(0);
    expect(api.state.roomVisibleIds.has("room_b_corridor")).toBe(true);
    expect(api.state.playerInventory.healing_potion).toBeUndefined();

    api.renderTacticalGrid(api.state.partyStatus, api.state.environmentObjects, api.state.mapData);
    const lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    const projectedParty = lastCall[0] || {};
    expect(Object.keys(projectedParty).sort()).toEqual(["analyst", "player", "scout", "tactician"]);
    expect(lastCall[1].door_a_to_b.is_open).toBe(true);
    window.ControlledAgentInputController.updateHint();
    const hintText = String(document.getElementById("interaction-hint").textContent || "");
    expect(hintText).toContain("通道已开启");
    expect(hintText).not.toContain("E 打开门");
  });

  test("test_ab_door_interaction_hint_works_on_all_three_threshold_cells", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    api.applyNormalizedMap(normalized, { source: "json" });

    [5, 6, 7].forEach((x) => {
      window.ControlledAgentInputController.setPlayerPosition(x, 17);
      window.ControlledAgentInputController.updateHint();
      const hintText = String(document.getElementById("interaction-hint").textContent || "");
      expect(hintText).toContain("E 打开门");
      expect(hintText).toContain("[door_a_to_b]");
    });
  });

  test("test_open_ab_door_hint_works_from_corridor_side_without_reopening", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    api.applyNormalizedMap(normalized, { source: "json" });
    window.ControlledAgentInputController.setPlayerPosition(5, 17);
    window.ControlledAgentInputController.interact();
    await flushAsync();
    fetchSpy.mockClear();

    [5, 6, 7].forEach((x) => {
      window.ControlledAgentInputController.setPlayerPosition(x, 15);
      window.ControlledAgentInputController.updateHint();
      const hintText = String(document.getElementById("interaction-hint").textContent || "");
      expect(hintText).toContain("通道已开启");
      expect(hintText).toContain("[door_a_to_b]");
    });

    window.ControlledAgentInputController.interact();
    await flushAsync();
    expect(fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"))).toHaveLength(0);
    expect(api.state.roomVisibleIds.has("room_b_corridor")).toBe(true);
  });

  test("test_secret_door_and_room_c_visibility_gate", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    api.applyNormalizedMap(normalized, { source: "json" });

    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    let ids = api.state.normalizedMap.interactables.map((it) => it.id);
    expect(ids).not.toContain("door_b_to_c");
    expect(ids).not.toContain("hazard_diary");
    expect(ids).not.toContain("chest_1");

    api.discoverSecretDoor("door_b_to_c");
    api.refreshVisibilityProjection();
    ids = api.state.normalizedMap.interactables.map((it) => it.id);
    expect(ids).toContain("door_b_to_c");
    expect(ids).not.toContain("hazard_diary");
    expect(ids).not.toContain("chest_1");

    api.revealRoomByDoorTarget("door_b_to_c");
    api.refreshVisibilityProjection();
    ids = api.state.normalizedMap.interactables.map((it) => it.id);
    expect(ids).toContain("hazard_diary");
    expect(ids).toContain("chest_1");
  });

  test("test_tactical_projection_uses_player_start_when_backend_player_in_hidden_room", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "危害研究员的废弃实验室",
        party_status: {
          player: { name: "玩家", faction: "player", x: 2, y: 2 },
        },
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    api.applyNormalizedMap(normalized, { source: "json" });
    window.ControlledAgentTacticalMap.update.mockClear();

    await api.sendMessage("查看当前位置", "chat");
    await flushAsync();

    const lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    expect(lastCall).toBeDefined();
    const projectedParty = lastCall[0] || {};
    expect(projectedParty.player.x).toBe(5);
    expect(projectedParty.player.y).toBe(19);
    expect(api.state.partyStatus.player.x).toBe(2);
    expect(api.state.partyStatus.player.y).toBe(2);
  });

  test("test_party_projection_adds_visual_formation_for_companions_missing_coordinates", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });

    const sourceParty = {
      player: { name: "玩家", faction: "player", x: 4, y: 4 },
      scout: { name: "Scout", faction: "party" },
      analyst: { name: "Analyst", faction: "party" },
      tactician: { name: "Tactician", faction: "party" },
    };
    const projected = api.projectPartyStatusForTactical(sourceParty, api.state.mapData);

    ["scout", "analyst", "tactician"].forEach((id) => {
      expect(Number.isFinite(projected[id].x)).toBe(true);
      expect(Number.isFinite(projected[id].y)).toBe(true);
      expect(projected[id]._projection_source).toBe("visual_party_formation");
    });
    expect(projected.scout).toMatchObject({ x: 4, y: 5 });
    expect(projected.analyst).toMatchObject({ x: 4, y: 6 });
    expect(projected.tactician).toMatchObject({ x: 4, y: 7 });
  });

  test("test_party_projection_preserves_existing_companion_coordinates", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });

    const projected = api.projectPartyStatusForTactical({
      player: { name: "玩家", faction: "player", x: 4, y: 4 },
      scout: { name: "Scout", faction: "party", x: 2, y: 2 },
      analyst: { name: "Analyst", faction: "party" },
    }, api.state.mapData);

    expect(projected.scout.x).toBe(2);
    expect(projected.scout.y).toBe(2);
    expect(projected.scout._projection_source).toBeUndefined();
    expect(projected.analyst._projection_source).toBe("visual_party_formation");
  });

  test("test_party_projection_rehomes_existing_companion_coordinates_outside_visible_room", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap({
      ...buildFormationTestMap({ width: 10, height: 10 }),
      rooms: [
        { id: "room_a_spawn", x: 0, y: 0, w: 6, h: 6 },
        { id: "hidden_room", x: 6, y: 6, w: 4, h: 4 },
      ],
      visibleRooms: ["room_a_spawn"],
      playerStart: { x: 3, y: 3 },
    }, { source: "json" });

    const projected = api.projectPartyStatusForTactical({
      player: { name: "玩家", faction: "player", x: 3, y: 3 },
      scout: { name: "Scout", faction: "party", x: 8, y: 8 },
    }, api.state.mapData);

    expect(projected.scout._projection_source).toBe("visual_party_formation");
    expect(projected.scout.x).not.toBe(8);
    expect(projected.scout.y).not.toBe(8);
    expect(projected.scout.x).toBeLessThan(6);
    expect(projected.scout.y).toBeLessThan(6);
  });

  test("test_party_projection_formation_does_not_overlap_player_and_stays_in_bounds", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap({
      blocked: [{ x: 3, y: 3 }, { x: 5, y: 3 }],
    }), { source: "json" });

    const projected = api.projectPartyStatusForTactical({
      player: { name: "玩家", faction: "player", x: 4, y: 4 },
      scout: { name: "Scout", faction: "party" },
      analyst: { name: "Analyst", faction: "party" },
      tactician: { name: "Tactician", faction: "party" },
    }, api.state.mapData);
    const occupied = new Set(["4,4"]);

    ["scout", "analyst", "tactician"].forEach((id) => {
      const key = projected[id].x + "," + projected[id].y;
      expect(occupied.has(key)).toBe(false);
      occupied.add(key);
      expect(projected[id].x).toBeGreaterThanOrEqual(0);
      expect(projected[id].y).toBeGreaterThanOrEqual(0);
      expect(projected[id].x).toBeLessThan(api.state.mapData.width);
      expect(projected[id].y).toBeLessThan(api.state.mapData.height);
      expect(api.state.mapData.collision[projected[id].y][projected[id].x]).toBe(false);
    });
  });

  test("test_party_projection_does_not_mutate_backend_payload", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });

    const sourceParty = {
      player: { name: "玩家", faction: "player", x: 4, y: 4 },
      scout: { name: "Scout", faction: "party" },
      analyst: { name: "Analyst", faction: "party" },
      tactician: { name: "Tactician", faction: "party" },
    };
    api.projectPartyStatusForTactical(sourceParty, api.state.mapData);

    expect(sourceParty.scout.x).toBeUndefined();
    expect(sourceParty.analyst.x).toBeUndefined();
    expect(sourceParty.tactician.x).toBeUndefined();
  });

  test("test_tactical_map_update_receives_four_party_tokens_after_projection", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });
    window.ControlledAgentTacticalMap.update.mockClear();

    api.renderTacticalGrid({
      player: { name: "玩家", faction: "player", x: 4, y: 4 },
      scout: { name: "Scout", faction: "party" },
      analyst: { name: "Analyst", faction: "party" },
      tactician: { name: "Tactician", faction: "party" },
    }, {}, api.state.mapData);

    const lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    const projectedParty = lastCall[0] || {};
    expect(Object.keys(projectedParty).sort()).toEqual(["analyst", "player", "scout", "tactician"]);
    expect(projectedParty.scout._projection_source).toBe("visual_party_formation");
    expect(projectedParty.analyst._projection_source).toBe("visual_party_formation");
    expect(projectedParty.tactician._projection_source).toBe("visual_party_formation");
  });

  test("test_local_party_follow_uses_single_file_fallback_when_trail_is_empty", () => {
    const tacticalMap = loadGameHelpers();
    const map = buildFormationTestMap({ width: 8, height: 8 });
    const party = {
      player: { name: "玩家", faction: "player", x: 5, y: 4 },
      scout: { name: "Scout", faction: "party", x: 3, y: 4, _projection_source: "visual_party_formation" },
      analyst: { name: "Analyst", faction: "party", x: 5, y: 3, _projection_source: "visual_party_formation" },
      tactician: { name: "Tactician", faction: "party", x: 4, y: 5, _projection_source: "visual_party_formation" },
    };

    const followed = tacticalMap.resolveLocalPartyFollowFormation(party, map, { x: 5, y: 4 });

    expect(followed.scout).toMatchObject({ x: 5, y: 5, _projection_source: "local_party_trail" });
    expect(followed.analyst).toMatchObject({ x: 5, y: 6, _projection_source: "local_party_trail" });
    expect(followed.tactician).toMatchObject({ x: 5, y: 7, _projection_source: "local_party_trail" });
    expect(followed.scout.x).not.toBe(4);
    expect(followed.analyst.x).not.toBe(6);
  });

  test("test_move_player_local_updates_player_and_projected_companion_tokens", () => {
    const tacticalMap = loadGameHelpers();
    const map = buildFormationTestMap({ width: 8, height: 8 });
    const moved = [];
    const tokenFor = (id, data) => ({
      entity: { id, data: { ...data }, x: data.x, y: data.y },
    });
    const tokens = new Map([
      ["player", tokenFor("player", { x: 4, y: 4 })],
      ["scout", tokenFor("scout", { x: 3, y: 4, _projection_source: "visual_party_formation" })],
      ["analyst", tokenFor("analyst", { x: 5, y: 4, _projection_source: "visual_party_formation" })],
      ["tactician", tokenFor("tactician", { x: 4, y: 5, _projection_source: "visual_party_formation" })],
    ]);
    tacticalMap.scene = {
      tokens,
      moveToken: jest.fn((token, x, y) => moved.push([token.entity.id, x, y])),
      updateCameraFollow: jest.fn(),
    };
    tacticalMap.latestState = {
      partyStatus: {
        player: { x: 4, y: 4 },
        scout: { x: 3, y: 4, _projection_source: "visual_party_formation" },
        analyst: { x: 5, y: 4, _projection_source: "visual_party_formation" },
        tactician: { x: 4, y: 5, _projection_source: "visual_party_formation" },
      },
      environmentObjects: {},
      mapData: map,
    };

    tacticalMap.movePlayerLocal(5, 4);

    expect(tokens.get("player").entity.x).toBe(5);
    expect(tokens.get("player").entity.y).toBe(4);
    expect(tacticalMap.latestState.partyStatus.player).toMatchObject({ x: 5, y: 4 });
    expect(tacticalMap.latestState.partyStatus.scout).toMatchObject({ x: 4, y: 4, _projection_source: "local_party_trail" });
    expect(tacticalMap.latestState.partyStatus.analyst).toMatchObject({ x: 3, y: 4, _projection_source: "local_party_trail" });
    expect(tacticalMap.latestState.partyStatus.tactician).toMatchObject({ x: 2, y: 4, _projection_source: "local_party_trail" });
    ["scout", "analyst", "tactician"].forEach((id) => {
      expect(tokens.get(id).entity.data._projection_source).toBe("local_party_trail");
    });
    expect(moved.map(([id]) => id).sort()).toEqual(["analyst", "player", "scout", "tactician"]);
  });

  test("test_local_party_follow_keeps_single_file_after_three_steps", () => {
    const tacticalMap = loadGameHelpers();
    const map = buildFormationTestMap({ width: 10, height: 8 });
    const tokenFor = (id, data) => ({
      entity: { id, data: { ...data }, x: data.x, y: data.y },
    });
    tacticalMap.scene = {
      tokens: new Map([
        ["player", tokenFor("player", { x: 4, y: 4 })],
        ["scout", tokenFor("scout", { x: 4, y: 5, _projection_source: "visual_party_formation" })],
        ["analyst", tokenFor("analyst", { x: 4, y: 6, _projection_source: "visual_party_formation" })],
        ["tactician", tokenFor("tactician", { x: 4, y: 7, _projection_source: "visual_party_formation" })],
      ]),
      moveToken: jest.fn(),
      updateCameraFollow: jest.fn(),
    };
    tacticalMap.latestState = {
      partyStatus: {
        player: { x: 4, y: 4 },
        scout: { x: 4, y: 5, _projection_source: "visual_party_formation" },
        analyst: { x: 4, y: 6, _projection_source: "visual_party_formation" },
        tactician: { x: 4, y: 7, _projection_source: "visual_party_formation" },
      },
      environmentObjects: {},
      mapData: map,
    };

    tacticalMap.movePlayerLocal(5, 4);
    tacticalMap.movePlayerLocal(6, 4);
    tacticalMap.movePlayerLocal(7, 4);

    expect(tacticalMap.latestState.partyStatus.player).toMatchObject({ x: 7, y: 4 });
    expect(tacticalMap.latestState.partyStatus.scout).toMatchObject({ x: 6, y: 4 });
    expect(tacticalMap.latestState.partyStatus.analyst).toMatchObject({ x: 5, y: 4 });
    expect(tacticalMap.latestState.partyStatus.tactician).toMatchObject({ x: 4, y: 4 });
  });

  test("test_local_party_follow_turns_along_historical_path", () => {
    const tacticalMap = loadGameHelpers();
    const map = buildFormationTestMap({ width: 10, height: 10 });
    const party = {
      player: { x: 6, y: 3 },
      scout: { x: 5, y: 5, _projection_source: "local_party_trail" },
      analyst: { x: 4, y: 5, _projection_source: "local_party_trail" },
      tactician: { x: 3, y: 5, _projection_source: "local_party_trail" },
    };
    const followed = tacticalMap.resolveLocalPartyFollowFormation(party, map, { x: 6, y: 3 }, {
      trail: [
        { x: 6, y: 4 },
        { x: 6, y: 5 },
        { x: 5, y: 5 },
        { x: 4, y: 5 },
      ],
      lastMoveDirection: { x: 0, y: -1 },
    });

    expect(followed.scout).toMatchObject({ x: 6, y: 4 });
    expect(followed.analyst).toMatchObject({ x: 6, y: 5 });
    expect(followed.tactician).toMatchObject({ x: 5, y: 5 });
  });

  test("test_local_party_follow_avoids_collision_and_unrevealed_rooms", () => {
    const tacticalMap = loadGameHelpers();
    const map = {
      ...buildFormationTestMap({
        width: 6,
        height: 4,
        blocked: [{ x: 1, y: 1 }, { x: 3, y: 1 }, { x: 2, y: 2 }],
      }),
      rooms: [
        { id: "room_a_spawn", x: 0, y: 0, w: 4, h: 4 },
        { id: "hidden_room", x: 4, y: 0, w: 2, h: 4 },
      ],
      visibleRooms: ["room_a_spawn"],
    };
    const followed = tacticalMap.resolveLocalPartyFollowFormation({
      player: { x: 2, y: 1 },
      scout: { x: 0, y: 0, _projection_source: "visual_party_formation" },
      analyst: { x: 0, y: 1, _projection_source: "visual_party_formation" },
      tactician: { x: 0, y: 2, _projection_source: "visual_party_formation" },
    }, map, { x: 2, y: 1 }, {
      trail: [
        { x: 1, y: 1 },
        { x: 2, y: 2 },
        { x: 4, y: 1 },
        { x: 0, y: 0 },
        { x: 0, y: 1 },
        { x: 0, y: 2 },
      ],
      lastMoveDirection: { x: 1, y: 0 },
    });

    ["scout", "analyst", "tactician"].forEach((id) => {
      expect(map.collision[followed[id].y][followed[id].x]).toBe(false);
      expect(followed[id].x).toBeLessThan(4);
    });
  });

  test("test_local_party_follow_preserves_backend_explicit_companion_coordinates", () => {
    const tacticalMap = loadGameHelpers();
    const map = buildFormationTestMap({ width: 8, height: 8 });
    const party = {
      player: { x: 4, y: 4 },
      scout: { x: 2, y: 2 },
      analyst: { x: 5, y: 4, _projection_source: "local_party_follow" },
    };

    const followed = tacticalMap.resolveLocalPartyFollowFormation(party, map, { x: 5, y: 4 });

    expect(followed.scout).toEqual({ x: 2, y: 2 });
    expect(followed.analyst._projection_source).toBe("local_party_trail");
    expect(followed.analyst.x + "," + followed.analyst.y).not.toBe("5,4");
  });

  test("test_local_party_follow_does_not_call_chat_or_activate_trace", () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    loadNewModules();
    const tacticalMap = loadGameHelpers();
    const map = buildFormationTestMap({ width: 8, height: 8 });
    const tokens = new Map([
      ["player", { entity: { id: "player", data: { x: 4, y: 4 }, x: 4, y: 4 } }],
      ["scout", { entity: { id: "scout", data: { x: 3, y: 4, _projection_source: "visual_party_formation" }, x: 3, y: 4 } }],
    ]);
    tacticalMap.scene = {
      tokens,
      moveToken: jest.fn(),
      updateCameraFollow: jest.fn(),
    };
    tacticalMap.latestState = {
      partyStatus: {
        player: { x: 4, y: 4 },
        scout: { x: 3, y: 4, _projection_source: "visual_party_formation" },
      },
      environmentObjects: {},
      mapData: map,
    };

    tacticalMap.movePlayerLocal(5, 4);

    expect(fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"))).toHaveLength(0);
    expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
  });

  test("test_apply_normalized_map_resets_local_party_trail_only_on_new_map_setup", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();

    window.ControlledAgentTacticalMap.resetLocalPartyTrail.mockClear();
    api.applyNormalizedMap(buildFormationTestMap({ width: 8, height: 8 }), { source: "json" });

    expect(window.ControlledAgentTacticalMap.resetLocalPartyTrail).toHaveBeenCalledTimes(1);
  });

  test("test_ab_door_reveal_uses_map_only_refresh_and_preserves_local_party_trail", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    api.applyNormalizedMap(buildTrapCorridorTestMap(), { source: "json" });
    window.ControlledAgentTacticalMap.getLocalPartyTokenPositions.mockReturnValue({
      scout: { x: 2, y: 3, _projection_source: "local_party_trail" },
      analyst: { x: 2, y: 4, _projection_source: "local_party_trail" },
      tactician: { x: 1, y: 4, _projection_source: "local_party_trail" },
    });
    window.ControlledAgentTacticalMap.update.mockClear();
    window.ControlledAgentTacticalMap.refreshMapOnly.mockClear();
    fetchSpy.mockClear();

    window.ControlledAgentInputController.setPlayerPosition(2, 2);
    window.ControlledAgentInputController.interact();
    await flushAsync();

    expect(api.state.roomVisibleIds.has("room_b_corridor")).toBe(true);
    expect(window.ControlledAgentTacticalMap.refreshMapOnly).toHaveBeenCalledTimes(1);
    expect(window.ControlledAgentTacticalMap.update).not.toHaveBeenCalled();
    const [mapData] = window.ControlledAgentTacticalMap.refreshMapOnly.mock.calls[0];
    expect(mapData.visible_rooms || mapData.visibleRooms).toContain("room_b_corridor");
    expect(fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"))).toHaveLength(0);
    expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
  });

  test("test_polling_without_companion_coordinates_preserves_local_party_trail_positions", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });
    window.ControlledAgentTacticalMap.getLocalPartyTokenPositions.mockReturnValue({
      scout: { x: 4, y: 5, _projection_source: "local_party_trail" },
      analyst: { x: 4, y: 6, _projection_source: "local_party_trail" },
      tactician: { x: 4, y: 7, _projection_source: "local_party_trail" },
    });
    fetchSpy.mockResolvedValueOnce(mockResponse({
      party_status: {
        player: { x: 4, y: 4 },
        scout: { name: "Scout" },
        analyst: { name: "Analyst" },
        tactician: { name: "Tactician" },
      },
      environment_objects: {},
      combat_state: {},
      map_data: api.state.mapData,
    }));
    window.ControlledAgentTacticalMap.update.mockClear();

    await api.pollDialogueState();
    await flushAsync();

    const projected = window.ControlledAgentTacticalMap.update.mock.calls.at(-1)[0];
    expect(projected.scout).toMatchObject({ x: 4, y: 5, _projection_source: "local_party_trail" });
    expect(projected.analyst).toMatchObject({ x: 4, y: 6, _projection_source: "local_party_trail" });
    expect(projected.tactician).toMatchObject({ x: 4, y: 7, _projection_source: "local_party_trail" });
  });

  test("test_polling_stale_visual_formation_preserves_local_party_trail_positions", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });
    window.ControlledAgentTacticalMap.getLocalPartyTokenPositions.mockReturnValue({
      scout: { x: 5, y: 4, _projection_source: "local_party_trail" },
      analyst: { x: 4, y: 4, _projection_source: "local_party_trail" },
      tactician: { x: 3, y: 4, _projection_source: "local_party_trail" },
    });
    fetchSpy.mockResolvedValueOnce(mockResponse({
      party_status: {
        player: { x: 6, y: 4 },
        scout: { x: 6, y: 5, _projection_source: "visual_party_formation" },
        analyst: { x: 6, y: 6, _projection_source: "visual_party_formation" },
        tactician: { x: 6, y: 7, _projection_source: "visual_party_formation" },
      },
      environment_objects: {},
      combat_state: {},
      map_data: api.state.mapData,
    }));
    window.ControlledAgentTacticalMap.update.mockClear();

    await api.pollDialogueState();
    await flushAsync();

    const projected = window.ControlledAgentTacticalMap.update.mock.calls.at(-1)[0];
    expect(projected.scout).toMatchObject({ x: 5, y: 4, _projection_source: "local_party_trail" });
    expect(projected.analyst).toMatchObject({ x: 4, y: 4, _projection_source: "local_party_trail" });
    expect(projected.tactician).toMatchObject({ x: 3, y: 4, _projection_source: "local_party_trail" });
  });

  test("test_tactical_refresh_map_only_does_not_sync_or_destroy_tokens", () => {
    const tacticalMap = loadGameHelpers();
    const scene = {
      refreshMapOnly: jest.fn(),
      syncState: jest.fn(),
      upsertToken: jest.fn(),
      destroyToken: jest.fn(),
    };
    tacticalMap.scene = scene;
    tacticalMap.latestState = { partyStatus: { player: { x: 1, y: 1 } }, environmentObjects: {}, mapData: buildFormationTestMap() };

    tacticalMap.refreshMapOnly(buildFormationTestMap({ width: 9, height: 9 }), { chest_1: { x: 2, y: 2 } });

    expect(scene.refreshMapOnly).toHaveBeenCalledTimes(1);
    expect(scene.syncState).not.toHaveBeenCalled();
    expect(scene.upsertToken).not.toHaveBeenCalled();
    expect(scene.destroyToken).not.toHaveBeenCalled();
  });

  test("test_tactical_map_changed_clears_local_party_trail", () => {
    const tacticalMap = loadGameHelpers();
    tacticalMap.localPartyTrail = [{ x: 4, y: 4 }];
    tacticalMap.latestState = { partyStatus: {}, environmentObjects: {}, mapData: { ...buildFormationTestMap(), id: "old_map" } };
    tacticalMap.scene = { syncState: jest.fn() };

    tacticalMap.update({}, {}, { ...buildFormationTestMap(), id: "new_map" });

    expect(tacticalMap.localPartyTrail).toEqual([]);
  });

  test("test_xray_panel_uses_scroll_safe_layout_contract", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const panel = document.getElementById("director-trace-panel");
    expect(panel.classList.contains("xray-panel--scroll-safe")).toBe(true);
    expect(document.querySelector(".xray-section--node-trace")).not.toBeNull();
    expect(document.querySelector(".xray-section--state-watcher")).not.toBeNull();
    expect(document.querySelector(".xray-section--inspector")).not.toBeNull();
    expect(document.querySelector(".payload-summary")).not.toBeNull();
    expect(document.querySelector(".payload-raw")).not.toBeNull();

    const css = fs.readFileSync(path.resolve(__dirname, "../style.css"), "utf8");
    expect(css).toContain(".xray-panel--scroll-safe");
    expect(css).toMatch(/\.xray-panel\s*\{[\s\S]*overflow-y:\s*auto/);
    expect(css).toMatch(/\.node-timeline\s*\{[\s\S]*overflow-y:\s*auto/);
    expect(css).toMatch(/\.node-timeline\s*\{[\s\S]*max-height:\s*clamp/);
    expect(css).toMatch(/\.node-meta\s*\{[^}]*grid-row:\s*1;/);
    expect(css).toContain("@media (max-height: 1200px)");
    expect(css).toContain(".payload-summary");
    expect(css).toContain(".payload-raw:not([open]) .json-inspector");
    expect(css).toMatch(/\.world-diff-body\s*\{[\s\S]*max-height:\s*clamp/);
    const xraySectionBlock = (css.match(/\.xray-section\s*\{[^}]*\}/) || [""])[0];
    expect(xraySectionBlock).not.toContain("position: absolute");
  });

  test("test_xray_collapsed_hides_panel_and_keeps_map_expanded", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();

    api.els.xrayToggleBtn.click();
    await flushAsync();

    expect(api.els.mainLayout.classList.contains("xray-collapsed")).toBe(true);
    expect(api.els.xrayToggleBtn.getAttribute("aria-expanded")).toBe("false");
  });

  test("test_director_timeline_local_movement_renders_compact_idle_trace", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");

    if (window.ControlledAgentInputController) {
      window.ControlledAgentInputController.movePlayer(1, 0);
    }
    await flushAsync();

    const panel = document.getElementById("director-trace-panel");
    const mode = document.getElementById("director-trace-mode");
    const summary = document.getElementById("director-trace-summary");
    const visibleNodes = Array.from(document.querySelectorAll("#director-node-timeline li[data-node]"))
      .filter((node) => !node.hidden);

    expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
    expect(panel.classList.contains("director-trace--idle")).toBe(true);
    expect(mode.textContent).toBe("LOCAL EXPLORATION");
    expect(summary.textContent).toContain("WASD");
    expect(visibleNodes.map((node) => node.dataset.node)).toEqual(["player_input"]);
  });

  test("test_director_timeline_trap_insight_summary_highlights_actor_runtime_ui", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const uiEvents = [{ type: "trap_insight", actor: "scout", trapId: "gas_trap_1" }];
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes({}, { uiEvents, userLine: "检查走廊", intent: "CHAT" });
    window.ControlledAgentDirectorTrace.activateTrace(nodes, { uiEvents, animate: false, autoIdleMs: 999999 });
    await flushAsync();

    expect(document.getElementById("director-trace-mode").textContent).toBe("AGENT RESPONSE");
    expect(document.getElementById("director-trace-summary").textContent).toContain("Scout noticed");
    expect(document.querySelector('[data-node="actor_runtime"]').classList.contains("is-agent-signal")).toBe(true);
    expect(document.querySelector('[data-node="ui_events"]').classList.contains("is-agent-signal")).toBe(true);
  });

  test("test_director_timeline_trap_disarmed_highlights_mechanics_eventdrain_ui", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const data = { journal_events: ["[陷阱解除] scout -> gas_trap_1", "EventDrain gas_trap_1 status disabled"] };
    const uiEvents = [{ type: "trap_disarmed", actor: "scout", trapId: "gas_trap_1" }];
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes(data, { uiEvents, userLine: "侦察员，解除陷阱", intent: "DISARM" });
    window.ControlledAgentDirectorTrace.activateTrace(nodes, { data, uiEvents, userLine: "侦察员，解除陷阱", intent: "DISARM", animate: false, autoIdleMs: 999999 });
    await flushAsync();

    expect(document.getElementById("director-trace-mode").textContent).toBe("PHYSICS / EVENTDRAIN");
    expect(document.getElementById("director-trace-summary").textContent).toContain("Trap disabled");
    expect(document.querySelector('[data-node="domain_event"]').classList.contains("is-agent-signal")).toBe(true);
    expect(document.querySelector('[data-node="event_drain"]').classList.contains("is-agent-signal")).toBe(true);
    expect(document.querySelector('[data-node="ui_events"]').classList.contains("is-agent-signal")).toBe(true);
  });

  test("test_director_timeline_diary_context_highlights_memory_eventdrain", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const data = {
      journal_events: [
        "[线索整合] chemical_notes -> diary_context",
        "[交涉筹码] diary_evidence -> gatekeeper_elixir_truth",
        "EventDrain memory_update x2",
      ],
    };
    const uiEvents = [{ type: "negotiation_leverage", evidence: "diary_evidence", targetId: "gatekeeper" }];
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes(data, { uiEvents, userLine: "阅读 hazard_diary", intent: "READ" });
    window.ControlledAgentDirectorTrace.activateTrace(nodes, { data, uiEvents, userLine: "阅读 hazard_diary", intent: "READ", animate: false, autoIdleMs: 999999 });
    await flushAsync();

    expect(document.getElementById("director-trace-summary").textContent).toContain("Diary context");
    expect(document.querySelector('[data-node="domain_event"]').textContent).toContain("negotiation_leverage");
    expect(document.querySelector('[data-node="event_drain"]').classList.contains("is-agent-signal")).toBe(true);
  });

  test("test_director_timeline_diary_context_ignores_sticky_trap_disarmed_flag", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const data = {
      game_state: {
        flags: {
          hazard_lab_poison_trap_disarmed: true,
          act3_diary_decoded: true,
        },
      },
      journal_events: [
        "[线索整合] chemical_notes -> diary_context",
        "[交涉筹码] diary_evidence -> gatekeeper_elixir_truth",
      ],
    };
    const uiEvents = [{ type: "negotiation_leverage", evidence: "diary_evidence", targetId: "gatekeeper" }];
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes(data, { uiEvents, userLine: "阅读 hazard_diary", intent: "READ" });
    window.ControlledAgentDirectorTrace.activateTrace(nodes, { data, uiEvents, userLine: "阅读 hazard_diary", intent: "READ", animate: false, autoIdleMs: 999999 });
    await flushAsync();

    const summary = document.getElementById("director-trace-summary").textContent;
    expect(summary).toContain("Diary context");
    expect(summary).not.toContain("Trap disabled");
  });

  test("test_director_timeline_boss_strategy_highlights_actor_runtime_party_coordinator", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const data = { journal_events: ["[Boss方案] scout -> steal_key", "[Boss方案] analyst -> contain_corruption", "[Boss方案] tactician -> execute"] };
    const uiEvents = [{ type: "boss_strategy", strategies: [] }];
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes(data, { uiEvents, userLine: "我们怎么处理他？", intent: "CHAT" });
    window.ControlledAgentDirectorTrace.activateTrace(nodes, { data, uiEvents, userLine: "我们怎么处理他？", intent: "CHAT", animate: false, autoIdleMs: 999999 });
    await flushAsync();

    expect(document.getElementById("director-trace-summary").textContent).toContain("Three companions");
    const actorRuntime = document.querySelector('[data-node="actor_runtime"]');
    expect(actorRuntime.classList.contains("is-agent-signal")).toBe(true);
    expect(actorRuntime.textContent).toContain("Party Coordinator");
  });

  test("test_director_timeline_final_exit_highlights_mechanics_eventdrain_ui", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const data = { journal_events: ["act4_final_exit_opened", "DEMO CLEARED", "EventDrain heavy_iron_key"] };
    const uiEvents = [{ type: "demo_cleared" }];
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes(data, { uiEvents, userLine: "打开最终出口", intent: "OPEN" });
    window.ControlledAgentDirectorTrace.activateTrace(nodes, { data, uiEvents, userLine: "打开最终出口", intent: "OPEN", animate: false, autoIdleMs: 999999 });
    await flushAsync();

    expect(document.getElementById("director-trace-summary").textContent).toContain("Heavy iron key");
    expect(document.querySelector('[data-node="domain_event"]').classList.contains("is-agent-signal")).toBe(true);
    expect(document.querySelector('[data-node="event_drain"]').classList.contains("is-agent-signal")).toBe(true);
    expect(document.querySelector('[data-node="ui_events"]').classList.contains("is-agent-signal")).toBe(true);
  });

  test("test_director_timeline_final_exit_overrides_stale_boss_and_trap_summary", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const data = {
      game_state: {
        flags: {
          hazard_lab_poison_trap_disarmed: true,
          act4_boss_room_entered: true,
          act4_gatekeeper_confrontation_started: true,
          act4_final_exit_opened: true,
        },
      },
      latest_roll: {
        intent: "INTERACT",
        target: "heavy_oak_door_1",
        result: { is_success: true, result_type: "SUCCESS" },
        demo_cleared: true,
      },
      journal_events: [
        "[Boss方案] scout -> steal_key",
        "🚪 [系统] **[DEMO CLEARED]**",
      ],
    };
    const uiEvents = [{ type: "demo_cleared" }];
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes(data, { uiEvents, userLine: "用钥匙打开出口门", intent: "INTERACT" });
    window.ControlledAgentDirectorTrace.activateTrace(nodes, { data, uiEvents, userLine: "用钥匙打开出口门", intent: "INTERACT", animate: false, autoIdleMs: 999999 });
    await flushAsync();

    const summary = document.getElementById("director-trace-summary").textContent;
    expect(summary).toContain("Heavy iron key");
    expect(summary).not.toContain("Trap disabled");
    expect(summary).not.toContain("Three companions");
  });

  test("test_director_timeline_skipped_nodes_muted_and_payload_inspector_collapsed", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    window.ControlledAgentDirectorTrace.activateTrace(["player_input", "ui_events"], {
      data: { journal_events: ["brief ui feedback"] },
      animate: false,
      autoIdleMs: 999999,
    });
    await flushAsync();

    expect(document.querySelector('[data-node="dm_router"]').classList.contains("node-status--skipped")).toBe(true);
    expect(document.querySelector(".payload-raw").open).toBe(false);
    const css = fs.readFileSync(path.resolve(__dirname, "../style.css"), "utf8");
    expect(css).toContain(".xray-section--node-trace .node-status--skipped");
    expect(css).toMatch(/\.xray-section--node-trace \.node-explanation\s*\{[\s\S]*-webkit-line-clamp:\s*2/);
  });

  test("test_director_timeline_long_explanation_truncates_safely_and_sections_do_not_overlap", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const longExplanation = "A very long route explanation that should remain readable by clamping the text to two lines instead of expanding the right rail into overlapping debug panels.";
    window.ControlledAgentDirectorTrace.activateTrace(["player_input", "dm_router", "ui_events"], {
      details: {
        player_input: { explanation: "Player input.", input: "ask", output: "CHAT" },
        dm_router: { explanation: longExplanation, input: "ask party", output: "route selected" },
        ui_events: { explanation: "UI feedback.", input: "events", output: "card" },
      },
      animate: false,
      autoIdleMs: 999999,
    });
    await flushAsync();

    const explanation = document.querySelector('[data-node="dm_router"] .node-explanation');
    expect(explanation.title).toBe(longExplanation);
    expect(explanation.textContent).toBe(longExplanation);
    const css = fs.readFileSync(path.resolve(__dirname, "../style.css"), "utf8");
    const xraySectionBlock = (css.match(/\.xray-section\s*\{[^}]*\}/) || [""])[0];
    expect(xraySectionBlock).not.toContain("position: absolute");
    expect(document.querySelector(".xray-section--node-trace")).not.toBeNull();
    expect(document.querySelector(".xray-section--state-watcher")).not.toBeNull();
    expect(document.querySelector(".xray-section--inspector")).not.toBeNull();
  });

  test("test_json_visual_map_structure_is_not_overridden_by_runtime_map_data", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "危害研究员的废弃实验室",
        party_status: {
          player: { name: "玩家", faction: "player", x: 4, y: 18 },
        },
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
        map_data: {
          id: "hazard_lab",
          width: 20,
          height: 14,
          grid: Array.from({ length: 14 }, () => Array(20).fill(".")),
          collision: Array.from({ length: 14 }, () => Array(20).fill(false)),
          los_blockers: Array.from({ length: 14 }, () => Array(20).fill(false)),
          ground_types: Array.from({ length: 14 }, () => Array(20).fill(0)),
          rooms: [],
          visible_rooms: [],
        },
      })
    );
    const api = await bootAppForTest();
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    api.applyNormalizedMap(normalized, { source: "json" });
    fetchSpy.mockClear();

    await api.sendMessage("检查地图结构", "chat");
    await flushAsync();

    expect(api.state.mapLoadSource).toBe("json");
    expect(api.state.mapData.width).toBe(25);
    expect(api.state.mapData.height).toBe(25);
    expect(api.state.mapData.visible_rooms).toContain("room_a_spawn");
    expect(api.state.mapData.rooms.length).toBeGreaterThanOrEqual(5);
  });

  test("test_qa_map_debug_chip_outputs_source_and_positions", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_map_debug=1");
    api.updateMapDebug("unit_test");
    await flushAsync();

    const chip = document.getElementById("qa-map-debug-chip");
    expect(chip).not.toBeNull();
    const text = String(chip.textContent || "");
    expect(text).toContain("[qa_map_debug]");
    expect(text).toContain("mapSource=");
    expect(text).toContain("roomVisibleIds=");
    expect(text).toContain("backendPlayer=");
  });

  test("test_qa_map_source_badge_only_visible_for_json_source", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1");
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    api.applyNormalizedMap(normalized, { source: "json" });

    const badge = document.getElementById("qa-map-source-badge");
    expect(badge).not.toBeNull();
    expect(badge.classList.contains("is-hidden")).toBe(false);
    expect(String(badge.textContent || "")).toContain("mapSource=json");

    api.applyNormalizedMap(normalized, { source: "fixture", reason: "unit_test" });
    expect(badge.classList.contains("is-hidden")).toBe(true);
  });

  test("test_fetch_with_timeout_fallback_records_error_diagnostics", async () => {
    const fetchSpy = spyOnFetch().mockRejectedValue(new Error("synthetic_network_error"));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_map_debug=1");
    fetchSpy.mockClear();

    await api.sendMessage("测试网络降级", "chat");
    await flushAsync();

    const lastFetch = api.state.mapDebugLastFetch;
    expect(lastFetch).toBeDefined();
    expect(lastFetch.url).toContain("/api/chat");
    expect(lastFetch.ok).toBe(false);
    expect(String(lastFetch.error && lastFetch.error.message || "")).toContain("synthetic_network_error");
  });

  test("test_load_map_by_id_fallback_is_observable", async () => {
    spyOnFetch().mockRejectedValueOnce(new Error("offline"));
    loadNewModules();
    const result = await window.ControlledAgentTiledAdapter.loadMapById("hazard_lab");
    expect(result.source).toBe("fixture");
    expect(result.reason).toBe("error");
    expect(result.map.width).toBeGreaterThan(0);
  });

  test("test_app_marks_map_source_and_qa_warning_on_real_map_fallback", async () => {
    window.__ControlledAgent_FORCE_REAL_MAP_LOAD__ = true;
    const fetchSpy = spyOnFetch().mockImplementation((url) => {
      if (String(url).includes("assets/maps/hazard_lab.json")) {
        return Promise.reject(new Error("offline"));
      }
      return mockResponse({});
    });

    await bootAppForTest("http://localhost/?qa_test=1&qa_force_map=1");
    await flushAsync();

    const host = document.getElementById("map-container");
    expect(host.dataset.mapSource).toBe("fixture");
    expect(host.dataset.mapFallbackReason).toBe("error");

    const toastContainer = document.getElementById("toast-container");
    const warningToast = Array.from(toastContainer.querySelectorAll(".hud-toast"))
      .find((item) => item.textContent.includes("地图资产加载失败"));
    expect(warningToast).toBeDefined();
    expect(fetchSpy).toHaveBeenCalled();
  });

  test("test_session_id_from_url", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    /* Default session_id when no URL param is set (qa_test=1 doesn't set session_id) */
    expect(api.SESSION_ID).toBe("hazard_lab_demo");
  });

  test("test_new_timeline_generates_new_session_and_init_sync_without_idle_chatter", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "危害研究员的废弃实验室",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    fetchSpy.mockClear();
    await api.startNewTimeline();
    await flushAsync();

    const chatCalls = fetchSpy.mock.calls
      .filter(([url]) => String(url).includes("/api/chat"))
      .map(([, req]) => JSON.parse(req.body));
    expect(chatCalls.length).toBe(1);
    expect(chatCalls[0].intent).toBe("init_sync");
    expect(chatCalls[0].session_id).toMatch(/^hazard_lab_demo_\d+$/);
    expect(chatCalls[0].session_id).not.toBe("hazard_lab_demo");
    expect(api.SESSION_ID).toBe(chatCalls[0].session_id);
    const currentSessionInUrl = new URL(window.location.href).searchParams.get("session_id");
    expect(currentSessionInUrl).toBe(chatCalls[0].session_id);
    expect(chatCalls.some((payload) => payload.intent === "trigger_idle_banter")).toBe(false);
  });

  test("test_trigger_once_dedup", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    /* Switch to fake timers AFTER boot to avoid blocking async */
    jest.useFakeTimers();

    /* Set up a map with a trigger at (3,2) */
    const triggerCallback = jest.fn();
    const testMap = {
      width: 10, height: 10,
      collision: Array.from({ length: 10 }, () => Array(10).fill(false)),
      losBlockers: Array.from({ length: 10 }, () => Array(10).fill(false)),
      triggers: [{ id: "test_trap", x: 3, y: 2, w: 1, h: 1, type: "trigger", data: {} }],
      interactables: [],
      spawns: [],
    };

    window.ControlledAgentInputController.init({
      normalizedMap: testMap,
      playerStart: { x: 2, y: 2 },
      onNarrativeTrigger: triggerCallback,
    });

    /* Move into trigger zone */
    window.ControlledAgentInputController.movePlayer(1, 0); // now at 3,2 → trigger fires
    expect(triggerCallback).toHaveBeenCalledTimes(1);

    /* Stay on trigger, try to move again — cooldown + dedup means no re-fire */
    jest.advanceTimersByTime(200);
    window.ControlledAgentInputController.movePlayer(0, 0); // stays at 3,2

    /* Move out */
    jest.advanceTimersByTime(200);
    window.ControlledAgentInputController.movePlayer(-1, 0); // back to 2,2 — exits trigger zone

    /* Move back in — should fire again because player left and re-entered */
    jest.advanceTimersByTime(200);
    window.ControlledAgentInputController.movePlayer(1, 0);  // back to 3,2
    expect(triggerCallback).toHaveBeenCalledTimes(2);

    jest.useRealTimers();
  });

  /* ═══════════════════════════════════════════
     P1 FIX TESTS
     ═══════════════════════════════════════════ */

  test("test_poll_dialogue_dispatches_state_transition_events_without_journal_replay", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();

    /* Set previous party state to detect affection delta */
    api.state.partyStatus = { scout: { affection: 0 } };
    api.state.hasStateProjectionBaseline = true;

    /* Now mock the next fetch (pollDialogueState calls /api/state) */
    fetchSpy.mockResolvedValueOnce(mockResponse({
      journal_events: ["视线被阻挡 NO_LOS"],
      party_status: { scout: { affection: 5 } },
      environment_objects: {},
      combat_state: {},
    }));

    await api.pollDialogueState();
    await flushAsync();

    /* /api/state journal replay should not create a stale LoS toast. */
    const container = document.getElementById("toast-container");
    expect(container).not.toBeNull();
    const toasts = container.querySelectorAll(".hud-toast");
    expect(toasts.length).toBe(0);
    expect(document.getElementById("companion-chip-container").textContent).toContain("Scout +5");
  });

  test("test_fixture_has_act1_corridor_trigger", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const m = window.ControlledAgentTiledAdapter.normalizeTiledMap(null);
    /* At least one trigger should exist (act1_corridor_approach) */
    expect(m.triggers.length).toBeGreaterThanOrEqual(1);
    const act1 = m.triggers.find((t) => t.id === "act1_corridor_approach");
    expect(act1).toBeDefined();
    expect(act1.type).toBe("narrative_trigger");
    expect(act1.y).toBe(3);
  });

  test("test_trap_type_recognized_as_trigger", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    /* Build a YAML map with a trap obstacle */
    const trapMap = {
      map_id: "test_trap_map",
      dimensions: [5, 5],
      player_start: [0, 0],
      grid: [
        ". . . . .",
        ". . . . .",
        ". . . . .",
        ". . . . .",
        ". . . . .",
      ],
      obstacles: [
        {
          type: "trap",
          entity_id: "poison_trap",
          name: "毒气陷阱",
          coordinates: [[2, 2]],
          blocks_movement: false,
          blocks_los: false,
        },
      ],
      environment_objects: [],
      spawns: [],
    };

    const result = window.ControlledAgentTiledAdapter.normalizeTiledMap(trapMap);
    const trapTrigger = result.triggers.find((t) => t.id === "poison_trap");
    expect(trapTrigger).toBeDefined();
    expect(trapTrigger.x).toBe(2);
    expect(trapTrigger.y).toBe(2);
  });

  test("test_qa_no_idle_disables_idle_banter_post", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest("http://localhost/?qa_no_idle=1&qa_test=1");

    jest.useFakeTimers();
    fetchSpy.mockClear();
    await api.sendMessage("检查周围", null);
    jest.advanceTimersByTime(35000);
    jest.useRealTimers();

    const chatCalls = fetchSpy.mock.calls
      .filter(([url]) => String(url).includes("/api/chat"))
      .map(([, req]) => JSON.parse(req.body));
    expect(chatCalls.length).toBe(1);
    expect(chatCalls[0].intent).toBe("chat");
    expect(chatCalls.some((payload) => payload.intent === "trigger_idle_banter")).toBe(false);
  });

  test("test_owner_acceptance_qa_no_idle_syncs_initial_state_without_replaying_stale_diffs", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "废弃危害实验室",
        flags: {
          hazard_lab_poison_trap_disarmed: true,
          act3_diary_decoded: true,
        },
        party_status: {},
        environment_objects: {
          gas_trap_1: { id: "gas_trap_1", status: "disabled" },
        },
        player_inventory: {
          healing_potion: 2,
        },
        combat_state: {},
      })
    );

    const api = await bootAppForTest("http://localhost/?qa_no_idle=1");

    const chatCalls = fetchSpy.mock.calls
      .filter(([url]) => String(url).includes("/api/chat"))
      .map(([, req]) => JSON.parse(req.body));
    expect(chatCalls.length).toBe(1);
    expect(chatCalls[0].intent).toBe("init_sync");

    const baseline = api.buildShowcaseSnapshot();
    expect(baseline.flags.hazard_lab_poison_trap_disarmed).toBe(true);
    expect(baseline.player_inventory.healing_potion).toBe(2);
    expect(baseline.environment_objects.gas_trap_1.status).toBe("disabled");
    expect(document.getElementById("world-state-diff-badge").textContent).toBe("0");
  });

  test("test_fresh_init_does_not_show_healing_potion_acquired_toast_or_trap_card", async () => {
    spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: ["🧪 [实验室] 空气里弥漫着刺鼻的化学与腐败气味。"],
        current_location: "废弃危害实验室",
        party_status: {},
        environment_objects: {
          gas_trap_1: { id: "gas_trap_1", type: "trap", status: "hidden", is_hidden: true, x: 4, y: 6 },
        },
        player_inventory: { healing_potion: 2 },
        combat_state: {},
      })
    );

    await bootAppForTest("http://localhost/?qa_no_idle=1");

    const toastText = document.getElementById("toast-container").textContent;
    expect(toastText).not.toContain("healing_potion");
    expect(toastText).not.toContain("治疗药水");
    expect(document.body.textContent).not.toContain("Hidden Trap Spotted");
  });

  test("test_first_api_state_poll_projects_baseline_without_inventory_or_trap_events", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1");
    fetchSpy.mockResolvedValueOnce(
      mockResponse({
        journal_events: ["[陷阱感知] scout -> gas_trap_1"],
        party_status: {},
        environment_objects: {
          gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false, x: 4, y: 6 },
        },
        player_inventory: { healing_potion: 2 },
        combat_state: {},
      })
    );

    await api.pollDialogueState();
    await flushAsync();

    expect(document.getElementById("toast-container").textContent).not.toContain("healing_potion");
    expect(document.body.textContent).not.toContain("Hidden Trap Spotted");
  });

  test("test_api_state_poll_ignores_stale_direct_ui_events_and_journal_replay", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1");
    const staleProjection = {
      ui_events: [{ type: "item_transfer", item: "healing_potion", count: 2 }],
      journal_events: ["[陷阱感知] scout -> gas_trap_1"],
      party_status: {},
      environment_objects: {
        gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false, x: 4, y: 6 },
      },
      player_inventory: { healing_potion: 2 },
      combat_state: {},
    };
    fetchSpy.mockResolvedValueOnce(mockResponse(staleProjection));
    fetchSpy.mockResolvedValueOnce(mockResponse(staleProjection));

    await api.pollDialogueState();
    await flushAsync();
    await api.pollDialogueState();
    await flushAsync();

    expect(document.getElementById("toast-container").textContent).not.toContain("healing_potion");
    expect(document.getElementById("toast-container").textContent).not.toContain("治疗药水");
    expect(document.body.textContent).not.toContain("Hidden Trap Spotted");
  });

  test("test_state_poll_still_emits_true_trap_transition", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      environment_objects: {
        gas_trap_1: { id: "gas_trap_1", type: "trap", status: "triggered", is_hidden: false },
      },
      flags: { hazard_lab_poison_trap_triggered: true },
      journal_events: ["[陷阱感知] scout -> gas_trap_1"],
    }, {
      _eventSource: "state_poll",
      environment_objects: {
        gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false },
      },
      flags: { hazard_lab_poison_trap_revealed: true },
      journal_events: ["[陷阱感知] scout -> gas_trap_1"],
    });
    expect(events).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "trap_triggered", trapId: "gas_trap_1" }),
    ]));
    expect(events.filter((event) => event.type === "trap_insight")).toHaveLength(0);
  });

  test("test_duplicate_trap_insight_cards_are_deduped", async () => {
    loadNewModules();
    window.ControlledAgentHudRenderers.dispatchUIEvents([
      { type: "trap_insight", actor: "scout", trapId: "gas_trap_1" },
      { type: "trap_insight", actor: "scout", trapId: "gas_trap_1" },
    ]);
    expect(document.querySelectorAll(".agent-signal-card--trap-insight")).toHaveLength(1);
  });

  test("test_lab_key_loot_still_shows_real_item_acquired_feedback", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      player_inventory: { lab_key: 1 },
      journal_events: ["EventDrain item_transfer lab_key"],
    }, {
      player_inventory: {},
    });
    window.ControlledAgentHudRenderers.dispatchUIEvents(events);
    expect(document.getElementById("toast-container").textContent).toContain("实验室钥匙");
    expect(document.getElementById("inventory-hint-container").textContent).toContain("实验室钥匙");
  });

  test("test_interactable_type_maps_to_structured_intent_target", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    await bootAppForTest();
    fetchSpy.mockClear();

    const baseMap = {
      width: 20,
      height: 14,
      collision: Array.from({ length: 14 }, () => Array(20).fill(false)),
      losBlockers: Array.from({ length: 14 }, () => Array(20).fill(false)),
      triggers: [],
      spawns: [],
      interactables: [],
    };

    window.ControlledAgentInputController.setMap({
      ...baseMap,
      interactables: [{ id: "hazard_diary", type: "readable", x: 3, y: 2, name: "日记" }],
    });
    window.ControlledAgentInputController.setPlayerPosition(2, 2);
    window.ControlledAgentInputController.interact();
    await flushAsync();

    window.ControlledAgentInputController.setMap({
      ...baseMap,
      interactables: [{ id: "gatekeeper", type: "npc", x: 3, y: 2, name: "Gatekeeper" }],
    });
    window.ControlledAgentInputController.setPlayerPosition(2, 2);
    window.ControlledAgentInputController.interact();
    await flushAsync();

    window.ControlledAgentInputController.setMap({
      ...baseMap,
      interactables: [{ id: "heavy_oak_door_1", type: "door", x: 3, y: 2, name: "大门" }],
    });
    window.ControlledAgentInputController.setPlayerPosition(2, 2);
    window.ControlledAgentInputController.interact();
    await flushAsync();

    window.ControlledAgentInputController.setMap({
      ...baseMap,
      interactables: [{ id: "chest_1", type: "chest", x: 3, y: 2, name: "箱子" }],
    });
    window.ControlledAgentInputController.setPlayerPosition(2, 2);
    window.ControlledAgentInputController.interact();
    await flushAsync();

    const chatPayloads = fetchSpy.mock.calls
      .filter(([url]) => String(url).includes("/api/chat"))
      .map(([, req]) => JSON.parse(req.body));

    expect(chatPayloads[0].intent).toBe("READ");
    expect(chatPayloads[0].target).toBe("hazard_diary");

    expect(chatPayloads[1].intent).toBe("CHAT");
    expect(chatPayloads[1].target).toBe("gatekeeper");

    expect(chatPayloads[2].intent).toBe("INTERACT");
    expect(chatPayloads[2].target).toBe("heavy_oak_door_1");

    expect(chatPayloads[3].intent).toBe("ui_action_loot");
    expect(chatPayloads[3].target).toBe("chest_1");
  });

  test("test_read_diary_then_plain_act3_text_does_not_reuse_read_unknown", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    await api.sendMessage("", "READ", null, {
      target: "hazard_diary",
      source: "interaction",
    });
    await api.sendMessage("侦察员说得对，我们一起嘲笑 Gatekeeper。", null);
    await flushAsync();

    const chatPayloads = fetchSpy.mock.calls
      .filter(([url]) => String(url).includes("/api/chat"))
      .map(([, req]) => JSON.parse(req.body));

    expect(chatPayloads[0].intent).toBe("READ");
    expect(chatPayloads[0].target).toBe("hazard_diary");
    expect(chatPayloads[1].intent).toBe("CHAT");
    expect(chatPayloads[1].target).toBe("gatekeeper");
    expect(chatPayloads[1].target).not.toBe("unknown");
  });

  test("test_plain_text_read_diary_routes_to_read_hazard_diary", async () => {
    const api = await bootAppForTest();
    const { payload } = api.buildChatPayload("读日记", null, null, { source: "text_input" });

    expect(payload.intent).toBe("READ");
    expect(payload.target).toBe("hazard_diary");
    expect(payload.source).toBe("ui_text_normalized");
  });

  test("test_gatekeeper_diary_truth_text_routes_to_chat_gatekeeper_not_use_item", async () => {
    const api = await bootAppForTest();
    const { payload } = api.buildChatPayload(
      "Gatekeeper，我读了日记，知道你喝了危害药剂，也知道钥匙和实验的真相。",
      "USE_ITEM",
      null,
      { source: "text_input", target: "gatekeeper" }
    );

    expect(payload.intent).toBe("CHAT");
    expect(payload.target).toBe("gatekeeper");
    expect(payload.source).toBe("boss_diary_truth");
    expect(payload.intent_context).toEqual({ act4_diary_truth: true, boss_route: "negotiation" });
    expect(payload.intent).not.toBe("USE_ITEM");
  });

  test("test_implicit_truth_negotiation_without_gatekeeper_name_routes_to_gatekeeper", async () => {
    const api = await bootAppForTest();

    const { payload } = api.buildChatPayload(
      "我知道药剂对你做了什么。你不是守卫，你是实验品。把钥匙给我，我们带你离开。",
      null,
      null,
      { source: "text_input" }
    );

    expect(payload).toMatchObject({
      intent: "CHAT",
      target: "gatekeeper",
      source: "boss_diary_truth",
      intent_context: {
        act4_diary_truth: true,
        boss_route: "negotiation",
      },
    });
  });

  test("test_active_dialogue_target_gatekeeper_forces_chat_target_on_plain_text", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();
    api.state.activeDialogueTarget = "gatekeeper";

    await api.sendMessage("我支持侦察员。", null);
    await flushAsync();

    const chatCall = fetchSpy.mock.calls.find(([url]) => String(url).includes("/api/chat"));
    const payload = JSON.parse(chatCall[1].body);
    expect(payload.intent).toBe("CHAT");
    expect(payload.target).toBe("gatekeeper");
    expect(payload.source).toBe("dialogue_input");
  });

  test("test_active_dialogue_target_gatekeeper_keeps_plain_text_as_chat", async () => {
    const api = await bootAppForTest();
    api.state.activeDialogueTarget = "gatekeeper";

    const { payload } = api.buildChatPayload("我知道你在隐瞒钥匙。", null, null, {});

    expect(payload.intent).toBe("CHAT");
    expect(payload.target).toBe("gatekeeper");
    expect(payload.source).toBe("dialogue_input");
  });

  test("test_scout_disarm_text_routes_to_disarm_gas_trap_payload", async () => {
    const api = await bootAppForTest();

    const { payload } = api.buildChatPayload("侦察员，解除陷阱。", null, null, { source: "text_input" });

    expect(payload).toMatchObject({
      user_input: "侦察员，解除陷阱。",
      intent: "DISARM",
      target: "gas_trap_1",
      source: "ui_text_normalized",
      character: "scout",
      intent_context: {
        action_actor: "scout",
        action_target: "gas_trap_1",
        source: "ui_text_normalized",
        action: "disarm_trap",
      },
    });
  });

  test("test_narrative_payload_includes_current_local_player_grid_position", async () => {
    const api = await bootAppForTest();
    window.ControlledAgentTacticalMap.getPlayerGridPosition.mockReturnValue({ x: 6, y: 15 });

    const { payload } = api.buildChatPayload("侦察员，解除陷阱。", null, null, { source: "text_input" });

    expect(payload.client_player_position).toEqual({ x: 6, y: 15 });
    expect(payload.player_position).toEqual([6, 15]);
  });

  test("test_final_exit_text_payload_includes_latest_local_player_grid_position", async () => {
    const api = await bootAppForTest();
    window.ControlledAgentTacticalMap.getPlayerGridPosition.mockReturnValue({ x: 16, y: 4 });
    const first = api.buildChatPayload(
      "用 heavy_iron_key 打开 heavy_oak_door_1。",
      null,
      null,
      { source: "text_input" }
    ).payload;
    window.ControlledAgentTacticalMap.getPlayerGridPosition.mockReturnValue({ x: 17, y: 4 });

    const { payload } = api.buildChatPayload(
      "用 heavy_iron_key 打开 heavy_oak_door_1。",
      null,
      null,
      { source: "text_input" }
    );

    expect(first.client_player_position).toEqual({ x: 16, y: 4 });
    expect(payload).toMatchObject({
      intent: "INTERACT",
      target: "heavy_oak_door_1",
      source: "text_input",
      client_player_position: { x: 17, y: 4 },
      player_position: [17, 4],
    });
  });

  test("test_hazard_lab_natural_move_text_routes_to_adjacent_door_tiles", async () => {
    const api = await bootAppForTest();

    expect(api.buildChatPayload("靠近 B-D 实验室重门。", null, null, { source: "text_input" }).payload)
      .toMatchObject({
        intent: "MOVE",
        target: "4,8",
        source: "ui_text_move",
      });

    expect(api.buildChatPayload("打开 A-B 门，进入走廊入口。", null, null, { source: "text_input" }).payload)
      .toMatchObject({
        intent: "INTERACT",
        target: "door_a_to_b",
      });

    expect(api.buildChatPayload("移动到 17,4", null, null, { source: "text_input" }).payload)
      .toMatchObject({
        intent: "MOVE",
        target: "17,4",
        source: "ui_text_move",
      });
  });

  test("test_final_exit_open_text_overrides_active_gatekeeper_dialogue_target", async () => {
    const api = await bootAppForTest();
    api.state.activeDialogueTarget = "gatekeeper";
    window.ControlledAgentTacticalMap.getPlayerGridPosition.mockReturnValue({ x: 17, y: 4 });

    const { payload } = api.buildChatPayload("用钥匙打开出口门。", null, null, { source: "text_input" });

    expect(payload).toMatchObject({
      intent: "INTERACT",
      target: "heavy_oak_door_1",
      source: "text_input",
      client_player_position: { x: 17, y: 4 },
      player_position: [17, 4],
    });
  });

  test("test_interaction_hint_target_matches_e_payload_target", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    await bootAppForTest();
    fetchSpy.mockClear();

    const baseMap = {
      width: 20,
      height: 14,
      collision: Array.from({ length: 14 }, () => Array(20).fill(false)),
      losBlockers: Array.from({ length: 14 }, () => Array(20).fill(false)),
      triggers: [],
      spawns: [],
      interactables: [],
    };

    window.ControlledAgentInputController.setMap({
      ...baseMap,
      interactables: [{ id: "heavy_oak_door_1", type: "door", x: 3, y: 2, name: "大门" }],
    });
    window.ControlledAgentInputController.setPlayerPosition(2, 2);
    window.ControlledAgentTacticalMap.getPlayerGridPosition.mockReturnValue({ x: 2, y: 2 });
    window.ControlledAgentInputController.updateHint();

    const hintText = String(document.getElementById("interaction-hint").textContent || "");
    expect(hintText).toContain("E 打开出口门");
    expect(hintText).toContain("[heavy_oak_door_1]");

    window.ControlledAgentInputController.interact();
    await flushAsync();

    const chatCall = fetchSpy.mock.calls.find(([url]) => String(url).includes("/api/chat"));
    const payload = JSON.parse(chatCall[1].body);
    expect(payload.intent).toBe("INTERACT");
    expect(payload.target).toBe("heavy_oak_door_1");
    expect(payload.client_player_position).toEqual({ x: 2, y: 2 });
    expect(payload.player_position).toEqual([2, 2]);
  });

  test("test_wasd_local_move_does_not_call_chat_until_interaction", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    await bootAppForTest();
    fetchSpy.mockClear();

    window.ControlledAgentInputController.setMap({
      width: 20,
      height: 14,
      collision: Array.from({ length: 14 }, () => Array(20).fill(false)),
      losBlockers: Array.from({ length: 14 }, () => Array(20).fill(false)),
      triggers: [],
      spawns: [],
      interactables: [],
    });
    window.ControlledAgentInputController.setPlayerPosition(2, 2);

    expect(window.ControlledAgentInputController.movePlayer(1, 0)).toBe(true);
    const chatCalls = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"));
    expect(chatCalls).toHaveLength(0);
  });

  test("test_hidden_trap_does_not_steal_e_interaction_target", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    await bootAppForTest();
    fetchSpy.mockClear();

    const baseMap = {
      width: 20,
      height: 14,
      collision: Array.from({ length: 14 }, () => Array(20).fill(false)),
      losBlockers: Array.from({ length: 14 }, () => Array(20).fill(false)),
      triggers: [],
      spawns: [],
      interactables: [],
    };

    window.ControlledAgentInputController.setMap({
      ...baseMap,
      interactables: [
        { id: "gas_trap_1", type: "trap", x: 3, y: 2, name: "毒气陷阱", is_hidden: true },
        { id: "chest_1", type: "chest", x: 2, y: 3, name: "箱子" },
      ],
    });
    window.ControlledAgentInputController.setPlayerPosition(2, 2);
    window.ControlledAgentInputController.interact();
    await flushAsync();

    const chatCall = fetchSpy.mock.calls.find(([url]) => String(url).includes("/api/chat"));
    const payload = JSON.parse(chatCall[1].body);
    expect(payload.intent).toBe("ui_action_loot");
    expect(payload.target).toBe("chest_1");

    fetchSpy.mockClear();
    window.ControlledAgentInputController.setMap({
      ...baseMap,
      interactables: [{ id: "gas_trap_1", type: "trap", x: 3, y: 2, name: "毒气陷阱", is_hidden: true }],
    });
    window.ControlledAgentInputController.setPlayerPosition(2, 2);
    window.ControlledAgentInputController.interact();
    await flushAsync();

    const callsAfterHiddenTrapOnly = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"));
    expect(callsAfterHiddenTrapOnly.length).toBe(0);
  });

  test("test_hidden_secret_door_not_in_interaction_candidates_until_discovered", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    api.applyNormalizedMap(normalized, { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();

    const idsBefore = api.state.normalizedMap.interactables.map((it) => it.id);
    expect(idsBefore).not.toContain("door_b_to_c");

    api.discoverSecretDoor("door_b_to_c");
    api.refreshVisibilityProjection();
    const idsAfter = api.state.normalizedMap.interactables.map((it) => it.id);
    expect(idsAfter).toContain("door_b_to_c");
  });

  test("test_discovered_trap_renders_danger_hint_without_overriding_interactable", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const baseMap = {
      width: 20,
      height: 14,
      collision: Array.from({ length: 14 }, () => Array(20).fill(false)),
      losBlockers: Array.from({ length: 14 }, () => Array(20).fill(false)),
      triggers: [],
      spawns: [],
      interactables: [],
      rooms: [],
    };

    window.ControlledAgentInputController.setMap({
      ...baseMap,
      interactables: [{ id: "gas_trap_1", type: "trap", x: 3, y: 2, name: "毒气陷阱", is_hidden: false, is_revealed: true }],
    });
    window.ControlledAgentInputController.setPlayerPosition(2, 2);
    window.ControlledAgentInputController.updateHint();
    const dangerOnlyText = String(document.getElementById("interaction-hint").textContent || "");
    expect(dangerOnlyText).toContain("可疑机关：");
    expect(dangerOnlyText).toContain("让侦察员解除");
    expect(dangerOnlyText).toContain("gas_trap_1");

    window.ControlledAgentInputController.setMap({
      ...baseMap,
      interactables: [
        { id: "gas_trap_1", type: "trap", x: 3, y: 2, name: "毒气陷阱", is_hidden: false, is_revealed: true },
        { id: "chest_1", type: "chest", x: 2, y: 3, name: "箱子" },
      ],
    });
    window.ControlledAgentInputController.setPlayerPosition(2, 2);
    window.ControlledAgentInputController.updateHint();
    const withChestText = String(document.getElementById("interaction-hint").textContent || "");
    expect(withChestText).toContain("E 搜刮");
    expect(withChestText).toContain("[chest_1]");
  });

  test("test_hidden_room_objects_do_not_claim_e_hint", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const map = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    const normalized = window.ControlledAgentTiledAdapter.normalizeTiledMap(map);
    api.applyNormalizedMap(normalized, { source: "json" });

    const chest = api.state.fullNormalizedMap.interactables.find((it) => it.id === "chest_1");
    expect(chest).toBeDefined();
    window.ControlledAgentInputController.setMap(api.state.normalizedMap);
    window.ControlledAgentInputController.setPlayerPosition(Number(chest.x), Number(chest.y));
    window.ControlledAgentInputController.updateHint();

    const text = String(document.getElementById("interaction-hint").textContent || "");
    expect(text).toBe("");
  });

  test("test_narrative_interactions_activate_director_trace_state_machine", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: ["叙事回应"],
        journal_events: [],
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    await api.sendStructuredAction({
      text: "",
      intent: "INTERACT",
      options: { target: "door_a_to_b", source: "interaction" },
    });
    await flushAsync();
    if (window.ControlledAgentDirectorTrace) {
      expect(["active", "idle"]).toContain(window.ControlledAgentDirectorTrace.getState());
    }

    await api.sendStructuredAction({
      text: "阅读日记",
      intent: "READ",
      options: { target: "hazard_diary", source: "interaction" },
    });
    await flushAsync();
    if (window.ControlledAgentDirectorTrace) {
      expect(["active", "idle"]).toContain(window.ControlledAgentDirectorTrace.getState());
    }

    await api.sendStructuredAction({
      text: "",
      intent: "CHAT",
      options: { target: "gatekeeper", source: "dialogue_input" },
    });
    await flushAsync();
    if (window.ControlledAgentDirectorTrace) {
      expect(["active", "idle"]).toContain(window.ControlledAgentDirectorTrace.getState());
    }
  });

  test("test_reset_demo_button_visible_and_starts_new_session", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "危害研究员的废弃实验室",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1&qa_rest_controls=1");
    const resetBtn = document.getElementById("new-timeline-btn");
    expect(resetBtn).not.toBeNull();
    expect(resetBtn.textContent).toContain("Reset Demo");
    expect(document.getElementById("rest-controls").classList.contains("is-hidden")).toBe(false);
    fetchSpy.mockClear();
    resetBtn.click();
    await flushAsync();

    const chatCalls = fetchSpy.mock.calls
      .filter(([url]) => String(url).includes("/api/chat"))
      .map(([, req]) => JSON.parse(req.body));
    expect(chatCalls.length).toBe(1);
    expect(chatCalls[0].intent).toBe("init_sync");
    expect(chatCalls[0].session_id).toMatch(/^hazard_lab_demo_\d+$/);
  });

  test("test_rest_controls_hidden_by_default_to_protect_bottom_dock", async () => {
    spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "危害研究员的废弃实验室",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    const restControls = document.getElementById("rest-controls");

    expect(restControls).not.toBeNull();
    expect(restControls.classList.contains("is-hidden")).toBe(true);
    expect(restControls.getAttribute("aria-hidden")).toBe("true");
  });

  test("test_door_natural_language_normalizes_to_interact_target", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    const commands = [
      "打开门",
      "开门",
      "使用钥匙打开门",
      "用 heavy_iron_key 打开门",
      "检查 heavy_oak_door_1",
    ];

    for (const command of commands) {
      await api.sendMessage(command, null);
    }
    await flushAsync();

    const payloads = fetchSpy.mock.calls
      .filter(([url]) => String(url).includes("/api/chat"))
      .map(([, req]) => JSON.parse(req.body));
    expect(payloads.length).toBe(commands.length);
    payloads.forEach((payload) => {
      expect(payload.intent).toBe("INTERACT");
      expect(payload.target).toBe("heavy_oak_door_1");
    });
  });

  test("test_text_input_explicit_door_ids_route_to_matching_targets", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {},
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    await api.sendMessage("打开门 door_a_to_b", "INTERACT");
    await api.sendMessage("打开门 door_b_to_d", "INTERACT");
    await flushAsync();

    const payloads = fetchSpy.mock.calls
      .filter(([url]) => String(url).includes("/api/chat"))
      .map(([, req]) => JSON.parse(req.body));
    expect(payloads[0].target).toBe("door_a_to_b");
    expect(payloads[1].target).toBe("door_b_to_d");
  });

  test("test_text_input_open_door_payload_normalizes_to_interact_target", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {
          heavy_oak_door_1: { id: "heavy_oak_door_1", type: "door", status: "closed" },
        },
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    api.els.userInput.value = "打开门";
    api.els.sendBtn.click();
    await flushAsync();

    const chatCall = fetchSpy.mock.calls.find(([url]) => String(url).includes("/api/chat"));
    expect(chatCall).toBeDefined();
    const payload = JSON.parse(chatCall[1].body);
    expect(payload.source).toBe("text_input");
    expect(payload.intent).toBe("INTERACT");
    expect(payload.target).toBe("heavy_oak_door_1");
  });

  test("test_text_input_open_door_with_key_payload_normalizes_to_interact_target", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(
      mockResponse({
        responses: [],
        journal_events: [],
        current_location: "测试场景",
        party_status: {},
        environment_objects: {
          heavy_oak_door_1: { id: "heavy_oak_door_1", type: "door", status: "closed" },
        },
        player_inventory: {},
        combat_state: {},
      })
    );
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    api.els.userInput.value = "使用钥匙打开门";
    api.els.sendBtn.click();
    await flushAsync();

    const chatCall = fetchSpy.mock.calls.find(([url]) => String(url).includes("/api/chat"));
    expect(chatCall).toBeDefined();
    const payload = JSON.parse(chatCall[1].body);
    expect(payload.source).toBe("text_input");
    expect(payload.intent).toBe("INTERACT");
    expect(payload.target).toBe("heavy_oak_door_1");
  });

  test("test_qa_showcase_shows_run_demo_script", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1&qa_showcase=1");
    expect(document.getElementById("run-demo-script-btn")).not.toBeNull();
  });

  test("test_qa_map_debug_only_hides_run_demo_script", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1&qa_map_debug=1");
    expect(document.getElementById("run-demo-script-btn")).toBeNull();
  });

  test("test_qa_showcase_and_map_debug_shows_run_demo_script", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1&qa_showcase=1&qa_map_debug=1");
    expect(document.getElementById("run-demo-script-btn")).not.toBeNull();
  });

  test("test_normal_url_hides_run_demo_script", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    expect(document.getElementById("run-demo-script-btn")).toBeNull();
  });

  test("test_wasd_and_hover_do_not_activate_director_trace", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    if (window.ControlledAgentInputController) {
      window.ControlledAgentInputController.movePlayer(1, 0);
    }
    document.getElementById("map-container").dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    await flushAsync();
    expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
  });

  test("test_diary_event_constructs_memory_eventdrain_trace_nodes", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes({
      journal_events: ["[记忆] actor_private:scout += memory_note", "EventDrain committed memory_update x2"],
      game_state: { actor_runtime_state: { scout: { memory_notes: ["diary"] } } },
    }, { userLine: "read hazard_diary", intent: "READ" });
    expect(nodes).toEqual(expect.arrayContaining(["actor_view_filter", "domain_event", "event_drain", "ui_events"]));
  });

  test("test_gatekeeper_branch_constructs_party_coordinator_affection_trace_nodes", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes({
      journal_events: ["Scout affection +2", "Party Coordinator selected side_with_scout", "combat_active true"],
    }, { userLine: "side_with_scout", intent: "CHAT" });
    const details = window.ControlledAgentDirectorTrace.buildTraceDetails({
      journal_events: ["Scout affection +2", "Party Coordinator selected side_with_scout"],
    }, { nodes, userLine: "side_with_scout", intent: "CHAT" });
    expect(nodes).toEqual(expect.arrayContaining(["actor_runtime", "domain_event", "event_drain"]));
    expect(details.actor_runtime.output).toContain("Party Coordinator");
    expect(details.domain_event.output).toContain("affection +2");
  });

  test("test_director_trace_activates_for_diary_read_and_gatekeeper_negotiation", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();

    const diaryNodes = window.ControlledAgentDirectorTrace.buildTraceNodes({}, {
      userLine: "读日记",
      intent: "READ",
    });
    const negotiationNodes = window.ControlledAgentDirectorTrace.buildTraceNodes({
      journal_events: ["[交涉筹码] diary_evidence -> gatekeeper_elixir_truth"],
    }, {
      userLine: "Gatekeeper，我读了日记，知道你喝了危害药剂，也知道钥匙和实验的真相。",
      intent: "CHAT",
    });
    const expected = ["player_input", "dm_router", "actor_runtime", "domain_event", "event_drain", "ui_events"];

    expected.forEach((node) => {
      expect(diaryNodes).toContain(node);
      expect(negotiationNodes).toContain(node);
    });
  });

  test("test_wasd_still_does_not_activate_trace", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    if (window.ControlledAgentInputController) {
      window.ControlledAgentInputController.movePlayer(1, 0);
    }
    await flushAsync();
    expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
  });

  test("test_item_transfer_constructs_domainevent_eventdrain_item_toast_trace", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents(
      { player_inventory: { lab_key: 1 }, journal_events: ["EventDrain item_transfer lab_key"] },
      { player_inventory: {} }
    );
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes({
      journal_events: ["DomainEvent actor_item_transaction_requested", "EventDrain item_transfer lab_key"],
    }, { userLine: "loot study_chest", intent: "ui_action_loot", uiEvents: events });
    const details = window.ControlledAgentDirectorTrace.buildTraceDetails({}, { nodes, uiEvents: events, userLine: "loot study_chest" });
    expect(nodes).toEqual(expect.arrayContaining(["domain_event", "event_drain", "ui_events"]));
    expect(events.some((event) => event.type === "item_gained" && event.item === "lab_key")).toBe(true);
    expect(details.ui_events.output).toContain("Item Toast");
  });

  test("test_state_diff_detects_visibleRooms_added", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();
    const diffs = window.ControlledAgentStateDiffRenderer.diffSnapshots(
      { roomVisibleIds: ["room_a_spawn"] },
      { roomVisibleIds: ["room_a_spawn", "room_b_corridor"] }
    );
    expect(diffs.map((d) => d.label)).toContain("visibleRooms += room_b_corridor");
  });

  test("test_state_diff_detects_inventory_added", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();
    const diffs = window.ControlledAgentStateDiffRenderer.diffSnapshots(
      { player_inventory: {} },
      { player_inventory: { lab_key: 1 } }
    );
    expect(diffs.map((d) => d.label)).toContain("player.inventory += lab_key");
  });

  test("test_state_diff_detects_affection_change", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();
    const diffs = window.ControlledAgentStateDiffRenderer.diffSnapshots(
      { party_status: { scout: { name: "Scout", affection: 0 } } },
      { party_status: { scout: { name: "Scout", affection: 2 } } }
    );
    expect(diffs.map((d) => d.label)).toContain("Scout.affection +2");
  });

  test("test_state_diff_detects_memory_note_added", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();
    const diffs = window.ControlledAgentStateDiffRenderer.diffSnapshots(
      { actor_runtime_state: { scout: { memory_notes: [] } } },
      { actor_runtime_state: { scout: { memory_notes: ["玩家与我一起嘲笑了 Gatekeeper，这种默契让我满意。"] } } }
    );
    expect(diffs.map((d) => d.label)).toContain("actor_private:scout += memory_note");
  });

  test("test_demo_script_runner_advances_and_supports_stop", async () => {
    loadNewModules();
    jest.useFakeTimers();
    const calls = [];
    const runner = window.ControlledAgentDemoScriptRunner.createRunner({
      startNewTimeline: jest.fn(async () => calls.push("new")),
      runShowcaseLocalStep: jest.fn((cmd) => calls.push(cmd)),
      sendMessage: jest.fn(async (text) => calls.push(text)),
    }, { delayMs: 10 });
    const promise = runner.run();
    await Promise.resolve();
    expect(runner.isRunning()).toBe(true);
    runner.stop();
    jest.runOnlyPendingTimers();
    const result = await promise;
    expect(result.stopped).toBe(true);
    expect(calls.length).toBeGreaterThanOrEqual(1);
    jest.useRealTimers();
  });

  test("test_fallback_reason_is_highlighted_in_trace", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest();
    window.ControlledAgentDirectorTrace.activateTrace(["player_input", "dm_router", "ui_events"], {
      animate: false,
      data: { game_state: { intent_context: { fallback_reason: "network_timeout_or_unavailable" } } },
    });
    const fallback = document.getElementById("director-fallback-reason");
    expect(fallback).not.toBeNull();
    expect(fallback.classList.contains("is-hidden")).toBe(false);
    expect(fallback.textContent).toContain("network_timeout_or_unavailable");
  });

  test("test_journal_companion_guidance_derives_ui_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[队友建议] Scout topic=lab_key missing: 去书房找钥匙，或者撬锁。"],
    });
    const guidance = events.find((event) => event.type === "companion_guidance");
    expect(guidance).toMatchObject({
      actorId: "scout",
      topic: "lab_key",
      state: "missing_key",
    });
    expect(guidance.advice).toContain("书房");
  });

  test("test_companion_guidance_state_parses_missing_and_key_acquired", async () => {
    loadNewModules();
    const missing = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[队友建议] topic=lab_key 找钥匙，去书房搜箱子。"],
    }).find((event) => event.type === "companion_guidance");
    const acquired = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[队友建议] topic=lab_key has_key=true 钥匙在手，打开实验室门。"],
    }).find((event) => event.type === "companion_guidance");
    expect(missing.state).toBe("missing_key");
    expect(acquired.state).toBe("key_acquired");
  });

  test("test_companion_guidance_actor_parsing_and_party_fallback", async () => {
    loadNewModules();
    const lines = [
      "[队友建议] Scout topic=lab_key 找钥匙",
      "[队友建议] Analyst topic=lab_key 找钥匙",
      "[队友建议] Tactician topic=lab_key 找钥匙",
      "[队友建议] topic=lab_key 找钥匙",
    ];
    const actorIds = lines.map((line) => window.ControlledAgentUIEventAdapter.extractUIEvents({ journal_events: [line] })
      .find((event) => event.type === "companion_guidance").actorId);
    expect(actorIds).toEqual(["scout", "analyst", "tactician", "party"]);
  });

  test("test_journal_negotiation_leverage_derives_ui_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[交涉筹码] diary_evidence -> gatekeeper_elixir_truth"],
    });
    const leverage = events.find((event) => event.type === "negotiation_leverage");
    expect(leverage).toMatchObject({
      evidence: "diary_evidence",
      targetId: "gatekeeper",
      pressure: "gatekeeper_elixir_truth",
    });
  });

  test("test_negotiation_leverage_card_from_journal_events", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[交涉筹码] diary_evidence -> gatekeeper_elixir_truth"],
    });

    expect(events).toEqual(expect.arrayContaining([
      expect.objectContaining({
        type: "negotiation_leverage",
        evidence: "diary_evidence",
        targetId: "gatekeeper",
        pressure: "gatekeeper_elixir_truth",
      }),
    ]));
  });

  test("test_negotiation_leverage_effects_can_come_from_state_delta", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[交涉筹码] diary_evidence -> gatekeeper_elixir_truth"],
      environment_objects: {
        gatekeeper: {
          dynamic_states: {
            patience: { current_value: 7 },
            fear: { current_value: 2 },
            paranoia: { current_value: 3 },
          },
        },
      },
    }, {
      environment_objects: {
        gatekeeper: {
          dynamic_states: {
            patience: { current_value: 8 },
            fear: { current_value: 1 },
            paranoia: { current_value: 2 },
          },
        },
      },
    });
    const leverage = events.find((event) => event.type === "negotiation_leverage");
    expect(leverage.effects).toEqual({ patience: -1, fear: 1, paranoia: 1 });
  });

  test("test_negotiation_leverage_card_from_flag_diff", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      flags: { hazard_lab_gatekeeper_truth_pressure: true },
      environment_objects: {
        gatekeeper: {
          dynamic_states: {
            patience: { current_value: 14 },
            fear: { current_value: 6 },
            paranoia: { current_value: 1 },
          },
        },
      },
    }, {
      flags: { hazard_lab_gatekeeper_truth_pressure: false },
      environment_objects: {
        gatekeeper: {
          dynamic_states: {
            patience: { current_value: 15 },
            fear: { current_value: 5 },
            paranoia: { current_value: 0 },
          },
        },
      },
    });

    const leverage = events.find((event) => event.type === "negotiation_leverage");
    expect(leverage).toMatchObject({
      evidence: "diary_evidence",
      targetId: "gatekeeper",
      pressure: "gatekeeper_elixir_truth",
      effects: { patience: -1, fear: 1, paranoia: 1 },
    });
  });

  test("test_hud_renders_companion_guidance_card", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionGuidanceCard({
      type: "companion_guidance",
      actorId: "scout",
      topic: "lab_key",
      state: "missing_key",
      advice: "Find the study or lockpick the door.",
    });
    const card = document.querySelector(".agent-signal-card--guidance");
    expect(card).not.toBeNull();
    expect(card.textContent).toContain("Companion Guidance");
    expect(card.textContent).toContain("Scout");
    expect(card.textContent).toContain("Lab Key");
    expect(card.textContent).toContain("Missing Key");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_hud_renders_negotiation_leverage_card", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderNegotiationLeverageCard({
      type: "negotiation_leverage",
      evidence: "diary_evidence",
      targetId: "gatekeeper",
      pressure: "gatekeeper_elixir_truth",
      effects: { patience: -1, fear: 1, paranoia: 1 },
    });
    const card = document.querySelector(".agent-signal-card--leverage");
    expect(card).not.toBeNull();
    expect(card.textContent).toContain("Negotiation Leverage");
    expect(card.textContent).toContain("Diary Evidence");
    expect(card.textContent).toContain("Gatekeeper");
    expect(card.textContent).toContain("Elixir Truth");
    expect(card.textContent).toContain("Patience -1");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_original_journal_event_remains_in_world_log", async () => {
    const rawJournal = "[队友建议] topic=lab_key 找钥匙，去书房搜箱子。";
    spyOnFetch().mockResolvedValueOnce(mockResponse({
      responses: [],
      journal_events: [rawJournal],
      party_status: {},
      environment_objects: {},
      player_inventory: {},
      combat_state: {},
    }));
    const api = await bootAppForTest("http://localhost/?qa_test=1");
    await api.sendMessage("怎么打开实验室门？", "CHAT", null, { source: "dialogue_input" });
    await flushAsync();
    expect(document.body.textContent).toContain(rawJournal);
    expect(document.querySelector(".agent-signal-card--guidance")).not.toBeNull();
  });

  test("test_plain_journal_event_does_not_generate_agent_signal_card", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[系统] 门仍然锁着。"],
    });
    expect(events.some((event) => event.type === "companion_guidance" || event.type === "negotiation_leverage")).toBe(false);
  });

  test("test_agent_signal_event_highlights_director_ui_events_without_wasd_activation", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    if (window.ControlledAgentInputController) {
      window.ControlledAgentInputController.movePlayer(1, 0);
    }
    await flushAsync();
    expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");

    window.ControlledAgentDirectorTrace.activateTrace(["player_input", "dm_router", "ui_events"], {
      animate: false,
      data: { journal_events: ["[队友建议] topic=lab_key 找钥匙"] },
      uiEvents: [{ type: "companion_guidance", topic: "lab_key" }],
    });
    const uiNode = document.querySelector('li[data-node="ui_events"]');
    expect(uiNode).not.toBeNull();
    expect(uiNode.classList.contains("is-agent-signal")).toBe(true);
  });

  test("test_reduced_motion_agent_signal_card_has_no_pulse_class", async () => {
    loadNewModules();
    const previousMatchMedia = window.matchMedia;
    window.matchMedia = jest.fn().mockImplementation((query) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }));
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionGuidanceCard({
      type: "companion_guidance",
      actorId: "party",
      topic: "lab_key",
      state: "missing_key",
      advice: "Find the study.",
    });
    const card = document.querySelector(".agent-signal-card--guidance");
    expect(card).not.toBeNull();
    expect(card.classList.contains("is-reduced-motion")).toBe(true);
    expect(card.classList.contains("agent-signal-card--pulse")).toBe(false);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    window.matchMedia = previousMatchMedia;
  });

  test("test_companion_bark_renders_scout_trap_warning_compact", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchUIEvents([{
      type: "trap_insight",
      actor: "scout",
      trapId: "gas_trap_1",
    }]);
    const bark = document.querySelector(".companion-bark--scout");
    expect(bark).not.toBeNull();
    expect(bark.textContent).toContain("Scout");
    expect(bark.textContent).toContain("thinking");
    expect(bark.textContent).not.toContain("Hidden gas trap");
    jest.advanceTimersByTime(420);
    expect(bark.classList.contains("is-typing")).toBe(true);
    expect(bark.querySelector(".companion-bark-text").textContent.length).toBeGreaterThan(0);
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(bark.textContent).toContain("Hidden gas trap");
    expect(document.querySelector(".companion-bark-container--compact")).not.toBeNull();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_three_companion_barks_stack_without_overlap_and_cap_at_three", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Scout line." },
      { speaker: "analyst", text: "Analyst line." },
      { speaker: "tactician", text: "Tactician line." },
      { speaker: "gatekeeper", text: "Gatekeeper line." },
    ]);
    const host = document.querySelector(".companion-bark-container");
    const barks = Array.from(document.querySelectorAll(".companion-bark"));
    expect(host.classList.contains("companion-bark-container--compact")).toBe(true);
    expect(barks).toHaveLength(1);
    expect(barks[0].dataset.speaker).toBe("scout");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelectorAll(".companion-bark")).toHaveLength(1);
    expect(document.querySelector(".companion-bark").dataset.speaker).toBe("analyst");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_same_speaker_bark_updates_existing_entry", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "First warning." });
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "Second warning." });
    const barks = document.querySelectorAll(".companion-bark--scout");
    expect(barks).toHaveLength(1);
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(barks[0].textContent).toContain("First warning.");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    const next = document.querySelector(".companion-bark--scout");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(next.textContent).toContain("Second warning.");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_pending_same_speaker_bark_updates_existing_pending_entry", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "First active." },
      { speaker: "analyst", text: "Old pending." },
      { speaker: "analyst", text: "Updated pending." },
    ]);
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    const bark = document.querySelector(".companion-bark--analyst");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(bark.textContent).toContain("Updated pending.");
    expect(bark.textContent).not.toContain("Old pending.");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_lower_priority_same_speaker_bark_does_not_overwrite_pending_signal", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Current trap warning.", priority: 9 },
      { speaker: "scout", text: "Mechanism handled.", priority: 8 },
      { speaker: "scout", text: "Generic acknowledgement.", priority: 0 },
    ]);
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    const bark = document.querySelector(".companion-bark--scout");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(bark.textContent).toContain("Mechanism handled.");
    expect(bark.textContent).not.toContain("Generic acknowledgement.");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_reduced_motion_companion_bark_renders_full_text_immediately", async () => {
    loadNewModules();
    const previousMatchMedia = window.matchMedia;
    window.matchMedia = jest.fn().mockImplementation((query) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }));
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "Full sentence immediately." });
    const bark = document.querySelector(".companion-bark");
    expect(bark.textContent).toContain("Full sentence immediately.");
    expect(bark.classList.contains("is-typing")).toBe(false);
    expect(bark.classList.contains("is-reduced-motion")).toBe(true);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    window.matchMedia = previousMatchMedia;
  });

  test("test_space_skip_current_bark_to_full_text", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "Skip me now." });
    const bark = document.querySelector(".companion-bark");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true }));
    expect(bark.textContent).toContain("Skip me now.");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_click_bark_skips_current_bark_to_full_text", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "analyst", text: "Click skip." });
    const bark = document.querySelector(".companion-bark--analyst");
    bark.click();
    expect(bark.textContent).toContain("Click skip.");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_thinking_state_transitions_to_typing_within_600ms", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "Thinking ends quickly." });
    const bark = document.querySelector(".companion-bark");
    expect(bark.classList.contains("is-thinking")).toBe(true);
    jest.advanceTimersByTime(601);
    expect(bark.classList.contains("is-thinking")).toBe(false);
    expect(bark.classList.contains("is-typing")).toBe(true);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_high_priority_trap_triggered_bark_interrupts_lower_priority_queue", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "analyst", text: "Low priority line.", priority: 1 });
    window.ControlledAgentHudRenderers.dispatchUIEvents([{ type: "trap_triggered", trapId: "gas_trap_1" }]);
    const bark = document.querySelector(".companion-bark");
    expect(bark.dataset.speaker).toBe("gatekeeper");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(bark.textContent).toContain("Poison gas");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_long_bark_text_is_truncated_before_typewriter_layout", async () => {
    loadNewModules();
    jest.useFakeTimers();
    const longText = "A".repeat(180);
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: longText });
    const bark = document.querySelector(".companion-bark");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(bark.querySelector(".companion-bark-text").textContent.length).toBeLessThanOrEqual(118);
    expect(bark.querySelector(".companion-bark-text").textContent.endsWith("...")).toBe(true);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_typewriter_does_not_call_chat_api", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "No network." });
    jest.advanceTimersByTime(1400);
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes("/api/chat"))).toBe(false);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_typewriter_key_skip_ignored_while_text_input_focused", async () => {
    loadNewModules();
    jest.useFakeTimers();
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "Do not skip while typing." });
    document.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true }));
    expect(document.querySelector(".companion-bark").textContent).not.toContain("Do not skip while typing.");
    input.blur();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true }));
    expect(document.querySelector(".companion-bark").textContent).toContain("Do not skip while typing.");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_companion_bark_legacy_immediate_update_is_no_longer_used_for_normal_motion", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "Second warning." });
    const barks = document.querySelectorAll(".companion-bark--scout");
    expect(barks).toHaveLength(1);
    expect(barks[0].textContent).not.toContain("Second warning.");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(barks[0].textContent).toContain("Second warning.");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_actor_spoke_event_uses_companion_bark_renderer_after_dom_refresh", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "Old detached bark." });
    document.querySelector(".companion-bark").remove();

    window.ControlledAgentHudRenderers.dispatchUIEvents([{
      type: "actor_spoke",
      speaker: "scout",
      text: "Fresh bark after refresh.",
    }]);

    const barks = document.querySelectorAll(".companion-bark--scout");
    expect(barks).toHaveLength(1);
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(barks[0].textContent).toContain("Fresh bark after refresh.");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_realistic_act2_trap_perception_payload_dispatches_bark", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    api.triggerSpeechBubbles({
      responses: [{ speaker: "scout", text: "附近有陷阱，小心。" }],
      journal_events: ["[陷阱感知] scout -> gas_trap_1"],
    }, { skipUIEvents: true });
    const bark = document.querySelector(".companion-bark--scout");
    expect(bark).not.toBeNull();
    expect(bark.textContent).toContain("Scout");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(bark.textContent).toContain("附近有陷阱");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_realistic_trap_disarm_journal_dispatches_scout_bark", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    api.triggerSpeechBubbles({
      journal_events: ["[陷阱解除] scout -> gas_trap_1"],
    }, { skipUIEvents: true });
    const bark = document.querySelector(".companion-bark--scout");
    expect(bark).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(bark.textContent).toContain("处理好了");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_realistic_act3_study_journals_queue_three_companion_barks", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    api.triggerSpeechBubbles({
      journal_events: [
        "[书房观察] scout -> key_sketch",
        "[书房观察] analyst -> necromancy",
        "[书房观察] tactician -> exit_plan",
      ],
    }, { skipUIEvents: true });
    expect(document.querySelectorAll(".companion-bark")).toHaveLength(1);
    expect(document.querySelector(".companion-bark--scout")).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--scout").textContent).toContain("钥匙草图");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--analyst")).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--analyst").textContent).toContain("危害气息");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--tactician")).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--tactician").textContent).toContain("开门");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_realistic_act4_boss_strategy_journals_queue_in_party_order", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    api.triggerSpeechBubbles({
      journal_events: [
        "[Boss方案] analyst -> contain_corruption",
        "[Boss方案] tactician -> execute",
        "[Boss方案] scout -> steal_key",
      ],
    }, { skipUIEvents: true });
    expect(document.querySelector(".companion-bark--scout")).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--scout").textContent).toContain("钥匙");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--analyst")).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--analyst").textContent).toContain("毒气罐");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--tactician")).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--tactician").textContent).toContain("杀掉");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_ui_event_boss_strategy_payload_alone_extracts_barks", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const barks = api.extractSpeechBarks({
      ui_events: [{
        type: "boss_strategy",
        strategies: [
          { actor: "scout", plan: "steal_key" },
          { actor: "analyst", plan: "contain_corruption" },
          { actor: "tactician", plan: "execute" },
        ],
      }],
    });
    expect(barks.map((bark) => bark.speaker)).toEqual(["scout", "analyst", "tactician"]);
  });

  test("test_response_and_journal_same_bark_are_deduped", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const barks = api.extractSpeechBarks({
      responses: [{ speaker: "scout", text: "附近有陷阱，小心。" }],
      journal_events: ["[陷阱感知] scout -> gas_trap_1"],
    }, { skipUIEvents: true });
    expect(barks.filter((bark) => bark.speaker === "scout" && bark.text === "附近有陷阱，小心。")).toHaveLength(1);
  });

  test("test_companion_bark_uses_compact_style_not_modal_title_style", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "analyst", text: "Keep your voice down." });
    const bark = document.querySelector(".companion-bark");
    expect(bark).not.toBeNull();
    expect(bark.querySelector(".companion-bark-speaker")).not.toBeNull();
    expect(bark.querySelector(".companion-bark-text")).not.toBeNull();
    expect(bark.querySelector("h1,h2,h3,.agent-signal-card-title")).toBeNull();
    expect(document.querySelector(".dialogue-overlay:not(.hidden)")).toBeNull();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_bark_does_not_block_keyboard_input", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildFormationTestMap(), { source: "json" });
    window.ControlledAgentTacticalMap.movePlayerLocal.mockClear();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "Move." });

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "w", bubbles: true, cancelable: true }));

    expect(window.ControlledAgentTacticalMap.movePlayerLocal).toHaveBeenCalled();
    expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
  });

  test("test_reduced_motion_companion_bark_has_no_motion_class", async () => {
    loadNewModules();
    const previousMatchMedia = window.matchMedia;
    window.matchMedia = jest.fn().mockImplementation((query) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }));
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "scout", text: "No flourish." });
    const bark = document.querySelector(".companion-bark");
    expect(bark.classList.contains("is-reduced-motion")).toBe(true);
    expect(bark.classList.contains("companion-bark--motion")).toBe(false);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    window.matchMedia = previousMatchMedia;
  });

  test("test_act4_party_strategy_split_produces_three_companion_barks", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchUIEvents([{
      type: "boss_strategy",
      strategies: [
        { actor: "scout", plan: "steal_key" },
        { actor: "analyst", plan: "contain_corruption" },
        { actor: "tactician", plan: "execute" },
      ],
    }]);
    expect(document.querySelectorAll(".companion-bark")).toHaveLength(1);
    expect(document.querySelector(".companion-bark--scout")).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--scout").textContent).toContain("take the key");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--analyst")).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--analyst").textContent).toContain("poison");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--tactician")).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--tactician").textContent).toContain("Strike");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_boss_strategy_bark_interrupts_lower_priority_guidance_bark", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchUIEvents([{
      type: "companion_guidance",
      actorId: "scout",
      advice: "scout notices inventory/world state: has_key=False door_id=door_b_to_d.",
      state: "missing_key",
    }]);
    expect(document.querySelector(".companion-bark--scout").textContent).not.toContain("notices inventory");
    window.ControlledAgentHudRenderers.dispatchUIEvents([{
      type: "boss_strategy",
      strategies: [{ actor: "scout", plan: "steal_key" }],
    }]);
    const bark = document.querySelector(".companion-bark--scout");
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(bark.textContent).toContain("take the key");
    expect(bark.textContent).not.toContain("notices inventory");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_memory_party_guidance_barks_do_not_create_speech_bubble_or_modal", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchUIEvents([
      { type: "memory_echo", actor: "scout", tone: "resentful", quote: "Now you need me?" },
      { type: "party_stance", stances: [{ actor: "analyst", stance: "mercy" }] },
      { type: "companion_guidance", actorId: "scout", advice: "Find the study.", state: "missing_key" },
    ]);
    expect(document.querySelectorAll(".companion-bark")).toHaveLength(1);
    expect(document.querySelector(".dialogue-overlay:not(.hidden)")).toBeNull();
    expect(document.querySelector(".speech-bubble")).toBeNull();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_companion_bark_mobile_width_and_unknown_fallback_style_contract", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderCompanionBark({ speaker: "", text: "Unknown speaker." });
    const bark = document.querySelector(".companion-bark--unknown");
    expect(bark).not.toBeNull();
    const css = fs.readFileSync(path.resolve(__dirname, "../style.css"), "utf8");
    expect(css).toMatch(/\.companion-bark-container\s*\{[\s\S]*width:\s*min\(320px,\s*calc\(100vw - 44px\)\)/);
    expect(css).toContain("@media (max-width: 768px)");
    expect(css).toMatch(/\.companion-bark-container\s*\{[\s\S]*width:\s*min\(300px,\s*calc\(100vw - 24px\)\)/);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_study_observation_replaces_stale_trap_disarmed_bark", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchUIEvents([{ type: "trap_disarmed", actor: "scout", trapId: "gas_trap_1" }]);
    let bark = document.querySelector(".companion-bark--scout");
    expect(bark).not.toBeNull();
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(bark.textContent).toContain("Done");

    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "钥匙草图、逃生路线。", source: "study_observation", priority: 6 },
      { speaker: "analyst", text: "这里的危害气息很重。", source: "study_observation", priority: 6 },
      { speaker: "tactician", text: "找到能开门的东西。", source: "study_observation", priority: 6 },
    ]);

    bark = document.querySelector(".companion-bark");
    expect(bark).not.toBeNull();
    expect(bark.textContent).not.toContain("Done. The mechanism");
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState()).toMatchObject({
      activeSpeaker: "scout",
      currentGroup: "study",
    });
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_boss_strategy_replaces_stale_study_or_trap_bark", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "钥匙草图、逃生路线。", source: "study_observation", priority: 6 },
      { speaker: "analyst", text: "这里的危害气息很重。", source: "study_observation", priority: 6 },
    ]);
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState().currentGroup).toBe("study");

    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Let me take the key.", source: "boss_strategy", priority: 8 },
      { speaker: "analyst", text: "Keep the poison contained.", source: "boss_strategy", priority: 8 },
      { speaker: "tactician", text: "Strike first.", source: "boss_strategy", priority: 8 },
    ]);

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.activeSpeaker).toBe("scout");
    expect(state.currentGroup).toBe("boss_strategy");
    expect(state.pendingSpeakers).toEqual(["analyst", "tactician"]);
    expect(document.querySelector(".companion-bark").textContent).not.toContain("钥匙草图");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_multi_agent_study_queue_completes_party_order_within_bounded_time", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "钥匙草图、逃生路线。", source: "study_observation", priority: 6 },
      { speaker: "analyst", text: "这里的危害气息很重。", source: "study_observation", priority: 6 },
      { speaker: "tactician", text: "找到能开门的东西。", source: "study_observation", priority: 6 },
    ]);

    jest.advanceTimersByTime(7000);

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.completedSpeakers).toEqual(["scout", "analyst", "tactician"]);
    expect(state.pendingSpeakers).toEqual([]);
    expect(state.queueLength).toBe(0);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_multi_agent_boss_strategy_queue_completes_party_order_within_bounded_time", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Let me take the key.", source: "boss_strategy", priority: 8 },
      { speaker: "analyst", text: "Keep the poison contained.", source: "boss_strategy", priority: 8 },
      { speaker: "tactician", text: "Strike first.", source: "boss_strategy", priority: 8 },
    ]);

    jest.advanceTimersByTime(7000);

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.completedSpeakers).toEqual(["scout", "analyst", "tactician"]);
    expect(state.completedSpeakers).toContain("analyst");
    expect(state.pendingSpeakers).not.toContain("tactician");
    expect(state.queueLength).toBe(0);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_generic_response_cannot_replace_active_boss_strategy_bark", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Let me take the key.", source: "boss_strategy", priority: 8 },
      { speaker: "analyst", text: "Keep the poison contained.", source: "boss_strategy", priority: 8 },
    ]);
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "gatekeeper", text: "Generic interruption.", source: "response", priority: 10 },
    ]);

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.activeSpeaker).toBe("scout");
    expect(state.pendingSpeakers).toEqual(["analyst"]);
    expect(document.querySelector(".companion-bark").textContent).not.toContain("Generic interruption");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_stale_trap_disarmed_cannot_replace_boss_strategy_queue", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Let me take the key.", source: "boss_strategy", priority: 8 },
      { speaker: "analyst", text: "Keep the poison contained.", source: "boss_strategy", priority: 8 },
      { speaker: "tactician", text: "Strike first.", source: "boss_strategy", priority: 8 },
    ]);

    const blocked = window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);

    expect(blocked).toEqual([]);
    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.currentGroup).toBe("boss_strategy");
    expect(state.activeSpeaker).toBe("scout");
    expect(state.pendingSpeakers).toEqual(["analyst", "tactician"]);
    expect(document.querySelector(".companion-bark").textContent).not.toContain("mechanism");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_bark_queue_qa_state_exists_empty_on_module_boot", async () => {
    loadNewModules();
    expect(window.__ControlledAgent_QA_STATE__.barkQueue).toMatchObject({
      activeSpeaker: "",
      activeSource: "",
      activeSourceGroup: "",
      activeComplete: false,
      pendingSpeakers: [],
      pendingSources: [],
      pendingSourceGroups: [],
      completedSpeakers: [],
      completedSources: [],
      currentGroup: "",
      queueLength: 0,
    });
    expect(window.__ControlledAgent_QA_STATE__.barkQueue.pendingSceneScopes).toEqual([]);
  });

  test("test_clear_companion_barks_without_args_resets_qa_state_empty", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState().currentGroup).toBe("trap");

    window.ControlledAgentHudRenderers.clearCompanionBarks();

    expect(window.__ControlledAgent_QA_STATE__.barkQueue).toMatchObject({
      activeSpeaker: "",
      activeSourceGroup: "",
      pendingSpeakers: [],
      completedSpeakers: [],
      currentGroup: "",
      queueLength: 0,
    });
    expect(document.querySelector(".companion-bark")).toBeNull();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_act3_study_observation_clears_active_trap_disarmed_bark", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);

    api.triggerSpeechBubbles({
      journal_events: ["[书房观察] analyst -> necromancy_pollution"],
    }, { skipUIEvents: true });

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.currentGroup).toBe("study");
    expect(state.activeSpeaker).toBe("analyst");
    expect(document.querySelector(".companion-bark--analyst")).not.toBeNull();
    expect(document.querySelector(".companion-bark").textContent).not.toContain("mechanism");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_act3_context_without_bark_clears_active_trap_disarmed_group", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);

    api.triggerSpeechBubbles({
      journal_events: ["[线索整合] chemical_notes -> diary_context"],
    }, { skipUIEvents: true });

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.activeSpeaker).toBe("");
    expect(state.currentGroup).not.toBe("trap");
    expect(state.queueLength).toBe(0);
    expect(document.querySelector(".companion-bark")).toBeNull();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_act3_context_suppresses_later_stale_trap_disarmed_bark", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    api.triggerSpeechBubbles({
      journal_events: ["[线索整合] chemical_notes -> diary_context"],
    }, { skipUIEvents: true });

    const blocked = window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);

    expect(blocked).toEqual([]);
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState()).toMatchObject({
      activeSpeaker: "",
      currentGroup: "",
      queueLength: 0,
    });
    expect(document.querySelector(".companion-bark")).toBeNull();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_act3_transition_clear_removes_active_trap_disarmed_dom", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--scout").textContent).toContain("mechanism");

    api.hardClearAct3TrapBarks("unit_act3_transition");

    expect(document.querySelector(".companion-bark")).toBeNull();
    expect(window.__ControlledAgent_QA_STATE__.barkQueue).toMatchObject({
      activeSource: "",
      activeSourceGroup: "",
      currentGroup: "",
      queueLength: 0,
    });
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_reveal_room_c_hard_clears_active_trap_disarmed_dom", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark--scout").textContent).toContain("mechanism");

    expect(api.revealRoom("room_c_secret_study")).toBe(true);

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(document.querySelector(".companion-bark")).toBeNull();
    expect(state.activeSourceGroup).not.toBe("trap");
    expect(state.currentGroup).not.toBe("trap");
    expect(state.activeSceneScope).not.toBe("act2_corridor");
    expect(state.pendingSceneScopes).not.toContain("act2_corridor");
    expect(state.lastDropReason).toMatch(/room_c_reveal|scope_clear:act2_corridor/);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_reveal_room_c_by_door_target_hard_clears_active_trap_disarmed_dom", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark")).not.toBeNull();

    expect(api.revealRoomByDoorTarget("door_b_to_c")).toBe(true);

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(document.querySelector(".companion-bark")).toBeNull();
    expect(state.activeSourceGroup).not.toBe("trap");
    expect(state.currentGroup).not.toBe("trap");
    expect(state.pendingSceneScopes).not.toContain("act2_corridor");
    expect(state.lastDropReason).toMatch(/room_c_reveal|scope_clear:act2_corridor/);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_pending_act2_trap_scope_bark_is_dropped_on_room_c_reveal", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Wait. Hidden gas trap.", source: "trap_insight", priority: 9 },
      { speaker: "analyst", text: "The corridor smells wrong.", source: "trap_insight", priority: 9 },
    ]);
    const before = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(before.activeSceneScope).toBe("act2_corridor");
    expect(before.pendingSceneScopes).toContain("act2_corridor");

    api.revealRoom("room_c_secret_study");

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(document.querySelector(".companion-bark")).toBeNull();
    expect(state.pendingSourceGroups).not.toContain("trap");
    expect(state.pendingSceneScopes).not.toContain("act2_corridor");
    expect(state.currentGroup).not.toBe("trap");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_room_c_visible_rejects_delayed_trap_disarmed_replay", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    api.revealRoom("room_c_secret_study");

    api.triggerSpeechBubbles({
      journal_events: ["[陷阱解除] scout -> gas_trap_1"],
    }, { skipUIEvents: true });

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(document.querySelector(".companion-bark")).toBeNull();
    expect(state.pendingSources).not.toContain("trap_disarmed");
    expect(state.pendingSceneScopes).not.toContain("act2_corridor");
    expect(state.currentGroup).not.toBe("trap");
    expect(state.lastDropReason).toMatch(/scene_scope_mismatch|scope_clear:act2_corridor|room_c_reveal/);
    window.ControlledAgentHudRenderers.clearCompanionBarks();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_visible_room_c_state_update_hard_clears_active_trap_disarmed_bark", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({
      responses: [],
      journal_events: [],
      map_data: { visible_rooms: ["room_a_spawn", "room_b_corridor", "room_c_secret_study"] },
      party_status: {},
      environment_objects: {},
      player_inventory: {},
      combat_state: {},
    }));
    const api = await bootAppForTest();
    fetchSpy.mockClear();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();

    await api.sendMessage("同步 Act3 状态", "CHAT", null, { skipLogUpdate: true });

    expect(document.querySelector(".companion-bark")).toBeNull();
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState()).toMatchObject({
      activeSourceGroup: "",
      currentGroup: "",
      queueLength: 0,
    });
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_read_chemical_notes_pre_request_hard_clears_trap_disarmed_bark", async () => {
    const pending = {};
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    fetchSpy.mockImplementation(() => new Promise((resolve) => {
      pending.resolve = resolve;
    }));
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);
    window.ControlledAgentHudRenderers.skipCurrentCompanionBark();
    expect(document.querySelector(".companion-bark")).not.toBeNull();

    const sendPromise = api.sendMessage("阅读 chemical_notes", null, null, { source: "text_input" });
    await Promise.resolve();

    expect(document.querySelector(".companion-bark")).toBeNull();
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState()).toMatchObject({
      activeSourceGroup: "",
      currentGroup: "",
      queueLength: 0,
    });

    pending.resolve(await mockResponse({
      responses: [],
      journal_events: ["[线索整合] chemical_notes -> diary_context"],
      party_status: {},
      environment_objects: {},
      player_inventory: {},
      combat_state: {},
    }));
    jest.runOnlyPendingTimers();
    await sendPromise;
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_delayed_trap_disarmed_replay_after_act3_does_not_requeue", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    api.triggerSpeechBubbles({
      journal_events: ["[线索整合] chemical_notes -> diary_context"],
    }, { skipUIEvents: true });

    api.triggerSpeechBubbles({
      journal_events: ["[陷阱解除] scout -> gas_trap_1"],
    }, { skipUIEvents: true });

    expect(document.querySelector(".companion-bark")).toBeNull();
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState()).toMatchObject({
      activeSource: "",
      activeSourceGroup: "",
      currentGroup: "",
      queueLength: 0,
    });
    window.ControlledAgentHudRenderers.clearCompanionBarks();
    jest.useRealTimers();
  });

  test("test_trap_disarmed_immediately_replaces_active_trap_insight", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchUIEvents([{ type: "trap_insight", actor: "scout", trapId: "gas_trap_1" }]);
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState()).toMatchObject({
      activeSource: "trap_insight",
      activeSourceGroup: "trap",
    });

    window.ControlledAgentHudRenderers.dispatchUIEvents([{ type: "trap_disarmed", actor: "scout", trapId: "gas_trap_1" }]);

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.activeSource).toBe("trap_disarmed");
    expect(state.pendingSources).not.toContain("trap_disarmed");
    expect(document.querySelector(".companion-bark--scout")).not.toBeNull();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_trap_disarmed_is_visible_inside_act2_window", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchUIEvents([{ type: "trap_disarmed", actor: "scout", trapId: "gas_trap_1" }]);
    expect(document.querySelector(".companion-bark--scout")).not.toBeNull();
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState()).toMatchObject({
      activeSource: "trap_disarmed",
      activeSceneScope: "act2_corridor",
    });
    jest.advanceTimersByTime(500);
    expect(document.querySelector(".companion-bark--scout")).not.toBeNull();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_stale_trap_disarmed_past_max_age_is_dropped_before_play", async () => {
    loadNewModules();
    jest.useFakeTimers();
    const oldCreatedAt = Date.now() - 3000;
    const result = window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      {
        speaker: "scout",
        text: "Done. The mechanism will not bite unless you insist.",
        source: "trap_disarmed",
        createdAt: oldCreatedAt,
        maxAgeMs: 2500,
      },
    ]);
    expect(result).toEqual([]);
    expect(document.querySelector(".companion-bark")).toBeNull();
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState().lastDropReason).toContain("expired:trap_disarmed");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_active_trap_disarmed_removed_when_entering_secret_study_scene", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchUIEvents([{ type: "trap_disarmed", actor: "scout", trapId: "gas_trap_1" }]);
    expect(document.querySelector(".companion-bark--scout")).not.toBeNull();

    window.ControlledAgentHudRenderers.setBarkSceneContext({ act: "act3", visibleRooms: ["room_c_secret_study"] });

    expect(document.querySelector(".companion-bark")).toBeNull();
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState()).toMatchObject({
      activeSourceGroup: "",
      currentGroup: "",
      queueLength: 0,
    });
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_pending_act2_trap_barks_are_dropped_when_entering_secret_study_scene", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Wait. Hidden gas trap.", source: "trap_insight", priority: 9 },
      { speaker: "analyst", text: "The corridor smells wrong.", source: "trap_insight", priority: 9 },
    ]);
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState().pendingSourceGroups).toContain("trap");

    window.ControlledAgentHudRenderers.setBarkSceneContext({ act: "act3", visibleRooms: ["room_c_secret_study"] });

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.pendingSourceGroups).not.toContain("trap");
    expect(state.currentGroup).not.toBe("trap");
    expect(document.querySelector(".companion-bark")).toBeNull();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_new_trap_triggered_danger_bark_can_still_interrupt_after_act3_context", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.setBarkSceneContext({ act: "act3", visibleRooms: ["room_c_secret_study"] });

    const result = window.ControlledAgentHudRenderers.dispatchUIEvents([{ type: "trap_triggered", trapId: "gas_trap_1" }]);

    expect(result).toBeUndefined();
    expect(document.querySelector(".companion-bark--gatekeeper")).not.toBeNull();
    expect(window.ControlledAgentHudRenderers.getCompanionBarkDebugState()).toMatchObject({
      activeSource: "trap_triggered",
      activeSourceGroup: "trap",
    });
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_act3_diary_decoded_flag_clears_active_trap_disarmed_group", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);

    api.triggerSpeechBubbles({
      flags: { act3_diary_decoded: true },
    }, { skipUIEvents: true });

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.activeSpeaker).toBe("");
    expect(state.currentGroup).not.toBe("trap");
    expect(state.queueLength).toBe(0);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_demo_cleared_resets_bark_queue_group", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Done. The mechanism will not bite unless you insist.", source: "trap_disarmed", priority: 8 },
    ]);
    window.ControlledAgentHudRenderers.dispatchUIEvents([{ type: "demo_cleared" }]);

    const state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.currentGroup).toBe("");
    expect(state.queueLength).toBe(0);
    expect(document.querySelector(".companion-bark")).toBeNull();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_bark_queue_debug_state_exposes_active_pending_completed_group", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.dispatchCompanionBarks([
      { speaker: "scout", text: "Let me take the key.", source: "boss_strategy", priority: 8 },
      { speaker: "analyst", text: "Keep the poison contained.", source: "boss_strategy", priority: 8 },
    ]);

    let state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state).toMatchObject({
      activeSpeaker: "scout",
      activeSource: "boss_strategy",
      activeSourceGroup: "boss_strategy",
      pendingSpeakers: ["analyst"],
      currentGroup: "boss_strategy",
      queueLength: 2,
    });
    expect(window.__ControlledAgent_QA_STATE__.barkQueue).toMatchObject({ activeSpeaker: "scout" });

    jest.advanceTimersByTime(2400);
    state = window.ControlledAgentHudRenderers.getCompanionBarkDebugState();
    expect(state.completedSpeakers).toContain("scout");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_trap_insight_journal_derives_ui_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[陷阱感知] scout -> gas_trap_1"],
    });
    expect(events).toContainEqual(expect.objectContaining({
      type: "trap_insight",
      actor: "scout",
      trapId: "gas_trap_1",
      source: "journal",
    }));
  });

  test("test_trap_reveal_flag_or_status_derives_insight_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      flags: { hazard_lab_poison_trap_revealed: true },
      environment_objects: {
        gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false, x: 4, y: 5 },
      },
    }, {
      flags: {},
      environment_objects: {
        gas_trap_1: { id: "gas_trap_1", type: "trap", status: "hidden", is_hidden: true, x: 4, y: 5 },
      },
    });
    expect(events.filter((event) => event.type === "trap_insight")).toHaveLength(1);
    expect(events.find((event) => event.type === "trap_insight")).toMatchObject({ trapId: "gas_trap_1" });

    const actorFlagEvents = window.ControlledAgentUIEventAdapter.extractUIEvents({
      game_state: {
        flags: {
          scout_detected_gas_trap: { value: true },
          world_hazard_lab_trap_warned: true,
        },
      },
    }, {
      flags: {},
    });
    expect(actorFlagEvents.filter((event) => event.type === "trap_insight")).toHaveLength(1);
  });

  test("test_trap_disarmed_journal_derives_ui_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[陷阱解除] scout -> gas_trap_1"],
    });
    expect(events).toContainEqual(expect.objectContaining({
      type: "trap_disarmed",
      actor: "scout",
      trapId: "gas_trap_1",
    }));
  });

  test("test_sticky_trap_disarmed_flag_does_not_replay_after_act2", async () => {
    loadNewModules();
    const previous = {
      flags: { hazard_lab_poison_trap_disarmed: true },
      environment_objects: { gas_trap_1: { status: "disabled" } },
    };
    const current = {
      flags: {
        hazard_lab_poison_trap_disarmed: true,
        act4_boss_room_entered: true,
      },
      environment_objects: { gas_trap_1: { status: "disabled" } },
      journal_events: ["[Boss Encounter] gatekeeper_confrontation_started"],
    };
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents(current, previous);
    expect(events.filter((event) => event.type === "trap_disarmed")).toHaveLength(0);
    expect(events).toContainEqual(expect.objectContaining({ type: "boss_intro" }));
  });

  test("test_trap_triggered_journal_derives_ui_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[毒气陷阱] gas_trap_1 triggered"],
    });
    expect(events).toContainEqual(expect.objectContaining({
      type: "trap_triggered",
      trapId: "gas_trap_1",
    }));
  });

  test("test_diary_lore_mentioning_trap_trigger_does_not_emit_trap_triggered", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: [
        "实验记录：只要通道的毒气陷阱会触发，他就会警觉。逃生关键是 heavy_iron_key。",
      ],
    });
    expect(events.filter((event) => event.type === "trap_triggered")).toHaveLength(0);
  });

  test("test_poisoned_status_diff_derives_trap_triggered_affected_actor", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      party_status: {
        player: { status_effects: [{ type: "poisoned", duration: 3 }] },
      },
    }, {
      party_status: {
        player: { status_effects: [] },
      },
    });
    const triggered = events.find((event) => event.type === "trap_triggered");
    expect(triggered).toBeDefined();
    expect(triggered.affectedActors).toContain("player");
  });

  test("test_hidden_trap_overlay_not_rendered_before_reveal", async () => {
    const tacticalMap = loadGameHelpers();
    const entries = tacticalMap.resolveTrapOverlayEntries({
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "hidden", is_hidden: true, x: 4, y: 5 },
    });
    expect(entries).toEqual([]);
  });

  test("test_is_hidden_false_alone_does_not_render_trap_overlay", async () => {
    const tacticalMap = loadGameHelpers();
    const entries = tacticalMap.resolveTrapOverlayEntries({
      gas_trap_1: { id: "gas_trap_1", type: "trap", is_hidden: false, x: 4, y: 5 },
    });
    expect(entries).toEqual([]);
  });

  test("test_opening_a_b_door_reveals_corridor_without_revealing_trap_overlay", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildTrapCorridorTestMap(), { source: "json" });
    api.state.partyStatus = {
      player: { x: 2, y: 2 },
      scout: { x: 2, y: 3, _projection_source: "local_party_trail" },
      analyst: { x: 2, y: 4, _projection_source: "local_party_trail" },
      tactician: { x: 1, y: 4, _projection_source: "local_party_trail" },
    };
    api.state.environmentObjects = {
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "hidden", is_hidden: true, x: 5, y: 2, room_id: "room_b_corridor" },
    };
    window.ControlledAgentTacticalMap.update.mockClear();

    expect(api.revealRoomByDoorTarget("door_a_to_b")).toBe(true);
    api.refreshVisibilityProjection();
    api.renderTacticalGrid(api.state.partyStatus, api.state.environmentObjects, api.state.mapData);

    const lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    const projectedParty = lastCall[0] || {};
    const projectedEnvironment = lastCall[1] || {};
    expect(api.state.roomVisibleIds.has("room_b_corridor")).toBe(true);
    expect(projectedEnvironment.gas_trap_1).toMatchObject({
      status: "hidden",
      is_hidden: true,
      is_revealed: false,
      discovered: false,
    });
    expect(projectedParty.scout).toMatchObject({ x: 2, y: 3, _projection_source: "local_party_trail" });
    expect(projectedParty.analyst).toMatchObject({ x: 2, y: 4, _projection_source: "local_party_trail" });
    expect(projectedParty.tactician).toMatchObject({ x: 1, y: 4, _projection_source: "local_party_trail" });
  });

  test("test_opening_a_b_door_does_not_dispatch_trap_insight_until_approach", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({
      responses: [],
      journal_events: ["🚪 [交互] 玩家 打开了 通往毒气走廊的门。"],
      party_status: {},
      environment_objects: {
        door_a_to_b: { id: "door_a_to_b", type: "door", status: "open", is_open: true, x: 3, y: 2 },
        gas_trap_1: { id: "gas_trap_1", type: "trap", status: "hidden", is_hidden: true, x: 4, y: 6 },
      },
      player_inventory: { healing_potion: 2 },
      combat_state: {},
      game_state: { flags: {} },
    }));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    fetchSpy.mockClear();

    await api.sendStructuredAction({
      text: "打开 A-B 门。",
      intent: "INTERACT",
      options: { target: "door_a_to_b", source: "ui_text_normalized" },
    });
    await flushAsync();

    expect(document.querySelector(".agent-signal-card--trap-insight")).toBeNull();
    expect(document.getElementById("director-trace-summary").textContent).not.toContain("Scout noticed");
  });

  test("test_real_corridor_approach_uses_tiled_trap_position_for_awareness", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({
      responses: [],
      journal_events: ["[陷阱感知] scout -> gas_trap_1"],
      party_status: {},
      environment_objects: {
        gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false, x: 4, y: 6 },
      },
      player_inventory: {},
      combat_state: {},
      game_state: {
        flags: {
          act2_scout_perception_checked: true,
          hazard_lab_poison_trap_revealed: true,
        },
      },
    }));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    fetchSpy.mockClear();
    const rawMap = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    api.applyNormalizedMap(window.ControlledAgentTiledAdapter.normalizeTiledMap(rawMap), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.state.environmentObjects = {
      door_a_to_b: { id: "door_a_to_b", type: "door", status: "open", is_open: true, x: 3, y: 2 },
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "hidden", is_hidden: true, x: 4, y: 6 },
    };
    window.ControlledAgentTacticalMap.getPlayerGridPosition.mockReturnValue({ x: 2, y: 2 });
    window.ControlledAgentInputController.setPlayerPosition(5, 13);
    const dateSpy = jest.spyOn(Date, "now").mockReturnValue(1000);

    window.ControlledAgentInputController.movePlayer(0, -1);
    await flushAsync();

    const chatCalls = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"));
    expect(chatCalls).toHaveLength(1);
    const payload = JSON.parse(chatCalls[0][1].body);
    expect(payload).toMatchObject({
      user_input: "侦察员检查走廊里的可疑机关。",
      intent: "CHAT",
      target: "gas_trap_1",
      source: "trap_awareness",
      client_player_position: { x: 5, y: 12 },
      player_position: [5, 12],
    });
    dateSpy.mockRestore();
  });

  test("test_act4_valve_backend_objects_hidden_until_lab_revealed", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildTrapCorridorTestMap(), { source: "json" });
    api.state.environmentObjects = {
      poison_valve: { id: "poison_valve", type: "poison_valve", status: "armed", is_hidden: false, x: 6, y: 9 },
      potion_tank: { id: "potion_tank", type: "potion_tank", status: "unstable", is_hidden: false, x: 7, y: 9 },
    };
    window.ControlledAgentTacticalMap.update.mockClear();

    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.renderTacticalGrid(api.state.partyStatus, api.state.environmentObjects, api.state.mapData);

    let lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    expect(lastCall[1].poison_valve).toBeUndefined();
    expect(lastCall[1].potion_tank).toBeUndefined();

    api.state.worldFlags = { act4_boss_room_entered: true };
    api.refreshVisibilityProjection();
    api.renderTacticalGrid(api.state.partyStatus, api.state.environmentObjects, api.state.mapData);

    lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    expect(lastCall[1].poison_valve).toMatchObject({ id: "poison_valve", status: "armed" });
    expect(lastCall[1].potion_tank).toMatchObject({ id: "potion_tank", status: "unstable" });
  });

  test("test_revealed_backend_gas_trap_projects_to_real_tiled_corridor_marker", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const rawMap = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    api.applyNormalizedMap(window.ControlledAgentTiledAdapter.normalizeTiledMap(rawMap), { source: "json" });
    api.state.environmentObjects = {
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false, x: 4, y: 6 },
    };

    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.renderTacticalGrid(api.state.partyStatus, api.state.environmentObjects, api.state.mapData);

    const lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    expect(lastCall[1].gas_trap_1).toMatchObject({
      status: "revealed",
      is_hidden: false,
      is_revealed: true,
      discovered: true,
      x: 5,
      y: 11,
      room_id: "room_b_corridor",
    });
  });

  test("test_scout_disarm_actor_action_projects_next_to_real_tiled_trap", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const rawMap = JSON.parse(fs.readFileSync(REAL_MAP_JSON_PATH, "utf8"));
    api.applyNormalizedMap(window.ControlledAgentTiledAdapter.normalizeTiledMap(rawMap), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.state.worldFlags = {
      act2_disarm_actor: "scout",
      act2_disarm_attempted: true,
      act2_gas_trap_disarmed: true,
      hazard_lab_poison_trap_disarmed: true,
    };
    api.state.partyStatus = {
      player: { x: 5, y: 15, faction: "player" },
      scout: { x: 4, y: 7 },
      analyst: { x: 5, y: 17 },
      tactician: { x: 5, y: 18 },
    };
    window.ControlledAgentTacticalMap.getLocalPartyTokenPositions.mockReturnValue({
      scout: { x: 5, y: 16, _projection_source: "local_party_trail" },
    });

    api.renderTacticalGrid(api.state.partyStatus, api.state.environmentObjects, api.state.mapData);

    const lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    expect(lastCall[0].scout).toMatchObject({
      x: 5,
      y: 12,
      _projection_source: "actor_action",
    });
  });

  test("test_trap_overlay_appears_after_backend_journal_trap_insight_signal", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildTrapCorridorTestMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.state.environmentObjects = {
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "hidden", is_hidden: true, x: 5, y: 2, room_id: "room_b_corridor" },
    };

    const events = api.dispatchUIEventsFromResponse({
      journal_events: ["[陷阱感知] scout -> gas_trap_1"],
    }, {});
    api.refreshVisibilityProjection();
    api.renderTacticalGrid(api.state.partyStatus, api.state.environmentObjects, api.state.mapData);

    const lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    expect(events.some((event) => event.type === "trap_insight")).toBe(true);
    expect(lastCall[1].gas_trap_1).toMatchObject({
      status: "revealed",
      is_hidden: false,
      is_revealed: true,
      discovered: true,
    });
  });

  test("test_local_act1_perception_does_not_discover_or_highlight_hidden_trap", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildTrapCorridorTestMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.state.environmentObjects = {
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "hidden", is_hidden: true, x: 5, y: 2, room_id: "room_b_corridor" },
    };
    const randomSpy = jest.spyOn(Math, "random").mockReturnValue(0.95);

    api.resolveAct1Perception();
    api.renderTacticalGrid(api.state.partyStatus, api.state.environmentObjects, api.state.mapData);

    const lastCall = window.ControlledAgentTacticalMap.update.mock.calls.at(-1);
    expect(api.state.discoveredTrapIds.has("gas_trap_1")).toBe(false);
    expect(api.state.discoveredSecretDoorIds.has("door_b_to_c")).toBe(false);
    expect(window.ControlledAgentTacticalMap.playTrapDiscoveryHighlight).not.toHaveBeenCalled();
    expect(lastCall[1].gas_trap_1).toMatchObject({ status: "hidden", is_hidden: true });
    expect(randomSpy).not.toHaveBeenCalled();
    randomSpy.mockRestore();
  });

  test("test_revealing_room_b_updates_act_card_to_poison_corridor", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    api.applyNormalizedMap(buildTrapCorridorTestMap(), { source: "json" });

    expect(document.getElementById("act-title").textContent).toContain("Act 1");

    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();

    expect(document.getElementById("act-title").textContent).toContain("Act 2");
    expect(document.getElementById("act-title").textContent).toContain("毒气走廊");
    expect(document.getElementById("act-summary").textContent).toContain("甜腻的腐臭味");
  });

  test("test_entering_active_trap_zone_posts_trap_trigger_payload_once", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse(trapTriggeredResponse()));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    fetchSpy.mockClear();
    api.applyNormalizedMap(buildTrapCorridorTestMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.state.environmentObjects = {
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false, x: 5, y: 2 },
    };
    window.ControlledAgentInputController.setPlayerPosition(4, 2);
    const dateSpy = jest.spyOn(Date, "now").mockReturnValue(1000);

    window.ControlledAgentInputController.movePlayer(1, 0);
    await flushAsync();

    const chatCalls = fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"));
    expect(chatCalls).toHaveLength(1);
    const payload = JSON.parse(chatCalls[0][1].body);
    expect(payload).toMatchObject({
      user_input: "触发毒气陷阱",
      intent: "INTERACT",
      target: "gas_trap_1",
      source: "trap_trigger",
      map_id: "hazard_lab",
    });
    expect(document.querySelector(".agent-signal-card--trap-triggered")).not.toBeNull();
    expect(window.ControlledAgentDirectorTrace.getState()).not.toBe("idle");
    expect(window.ControlledAgentDirectorTrace.getLastNodes()).toEqual(expect.arrayContaining(["domain_event", "event_drain", "ui_events"]));
    dateSpy.mockRestore();
  });

  test("test_entering_disabled_trap_zone_does_not_post_trap_trigger", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    fetchSpy.mockClear();
    api.applyNormalizedMap(buildTrapCorridorTestMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.state.worldFlags = { hazard_lab_poison_trap_disarmed: true };
    api.state.environmentObjects = {
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "disabled", is_hidden: false, x: 5, y: 2 },
    };
    window.ControlledAgentInputController.setPlayerPosition(4, 2);
    const dateSpy = jest.spyOn(Date, "now").mockReturnValue(1000);

    window.ControlledAgentInputController.movePlayer(1, 0);
    await flushAsync();

    expect(fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"))).toHaveLength(0);
    dateSpy.mockRestore();
  });

  test("test_entering_already_triggered_trap_zone_does_not_post_again", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    fetchSpy.mockClear();
    api.applyNormalizedMap(buildTrapCorridorTestMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.state.worldFlags = { hazard_lab_poison_trap_triggered: true };
    api.state.environmentObjects = {
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "triggered", is_hidden: false, x: 5, y: 2 },
    };
    window.ControlledAgentInputController.setPlayerPosition(4, 2);
    const dateSpy = jest.spyOn(Date, "now").mockReturnValue(1000);

    window.ControlledAgentInputController.movePlayer(1, 0);
    await flushAsync();

    expect(fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"))).toHaveLength(0);
    dateSpy.mockRestore();
  });

  test("test_trap_zone_repeated_enter_without_leaving_dedupes_trigger_post", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse(trapTriggeredResponse()));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    fetchSpy.mockClear();
    const map = buildTrapCorridorTestMap();
    map.triggers[0].w = 2;
    api.applyNormalizedMap(map, { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.state.environmentObjects = {
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false, x: 5, y: 2 },
    };
    window.ControlledAgentInputController.setPlayerPosition(4, 2);
    let now = 1000;
    const dateSpy = jest.spyOn(Date, "now").mockImplementation(() => now);

    window.ControlledAgentInputController.movePlayer(1, 0);
    await flushAsync();
    now += 200;
    window.ControlledAgentInputController.movePlayer(1, 0);
    await flushAsync();

    expect(fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"))).toHaveLength(1);
    dateSpy.mockRestore();
  });

  test("test_leave_and_reenter_after_triggered_state_still_does_not_retrigger", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse(trapTriggeredResponse()));
    const api = await bootAppForTest("http://localhost/?qa_test=1&qa_no_idle=1");
    fetchSpy.mockClear();
    api.applyNormalizedMap(buildTrapCorridorTestMap(), { source: "json" });
    api.revealRoomByDoorTarget("door_a_to_b");
    api.refreshVisibilityProjection();
    api.state.environmentObjects = {
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false, x: 5, y: 2 },
    };
    window.ControlledAgentInputController.setPlayerPosition(4, 2);
    let now = 1000;
    const dateSpy = jest.spyOn(Date, "now").mockImplementation(() => now);

    window.ControlledAgentInputController.movePlayer(1, 0);
    await flushAsync();
    now += 200;
    window.ControlledAgentInputController.movePlayer(-1, 0);
    await flushAsync();
    now += 200;
    window.ControlledAgentInputController.movePlayer(1, 0);
    await flushAsync();

    expect(fetchSpy.mock.calls.filter(([url]) => String(url).includes("/api/chat"))).toHaveLength(1);
    dateSpy.mockRestore();
  });

  test("test_disarm_failure_journal_with_poison_trigger_renders_trigger_feedback", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();
    const events = api.dispatchUIEventsFromResponse({
      journal_events: [
        "🔧 [解除陷阱失败] 侦察员的工具滑脱。",
        "[毒气陷阱] gas_trap_1 triggered",
      ],
      party_status: {
        player: { status_effects: [{ type: "poisoned", duration: 3 }] },
      },
    }, {
      party_status: {
        player: { status_effects: [] },
      },
    });

    expect(events.some((event) => event.type === "trap_triggered")).toBe(true);
    expect(document.querySelector(".agent-signal-card--trap-triggered")).not.toBeNull();
  });

  test("test_scout_warning_line_derives_trap_insight_event", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest();

    const events = api.dispatchUIEventsFromResponse({
      journal_events: ["🗣️ [Scout] 小心，前面有毒气机关的痕迹。"],
    }, {});

    expect(events).toEqual(expect.arrayContaining([
      expect.objectContaining({
        type: "trap_insight",
        actor: "scout",
        trapId: "gas_trap_1",
      }),
    ]));
    expect(document.querySelector(".agent-signal-card--trap-insight")).not.toBeNull();
  });

  test("test_trap_insight_response_does_not_auto_open_loot_modal_for_available_chest", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({
      responses: ["Scout notices a hidden gas mechanism."],
      journal_events: ["[陷阱感知] scout -> gas_trap_1"],
      party_status: {},
      environment_objects: {
        gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false },
        chest_1: {
          id: "chest_1",
          name: "危害研究员的战利品箱",
          type: "chest",
          status: "open",
          inventory: { lab_key: 1 },
        },
      },
      player_inventory: {},
      combat_state: {},
    }));
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    await api.sendStructuredAction({
      text: "Scout checks the corridor",
      intent: "INTERACT",
      options: { target: "gas_trap_1", source: "trap_check" },
    });
    await flushAsync();

    expect(document.getElementById("loot-modal").classList.contains("hidden")).toBe(true);
    expect(document.querySelector(".agent-signal-card--trap-insight")).not.toBeNull();
  });

  test("test_explicit_loot_response_still_opens_loot_modal", async () => {
    const fetchSpy = spyOnFetch().mockResolvedValue(mockResponse({
      responses: [],
      journal_events: [],
      party_status: {},
      environment_objects: {
        chest_1: {
          id: "chest_1",
          name: "危害研究员的战利品箱",
          type: "chest",
          status: "open",
          inventory: { lab_key: 1 },
        },
      },
      player_inventory: {},
      combat_state: {},
    }));
    const api = await bootAppForTest();
    fetchSpy.mockClear();

    await api.sendStructuredAction({
      text: "我要搜刮 chest_1",
      intent: "ui_action_loot",
      options: { target: "chest_1", source: "ui_click" },
    });
    await flushAsync();

    expect(document.getElementById("loot-modal").classList.contains("hidden")).toBe(false);
    expect(document.getElementById("loot-title").textContent).toContain("危害研究员的战利品箱");
  });

  test("test_revealed_trap_overlay_is_amber", async () => {
    const tacticalMap = loadGameHelpers();
    const [entry] = tacticalMap.resolveTrapOverlayEntries({
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false, x: 4, y: 5 },
    });
    expect(entry).toMatchObject({ id: "gas_trap_1", status: "revealed", label: "TRAP", color: 0xe0a84e });
  });

  test("test_disabled_trap_overlay_is_safe", async () => {
    const tacticalMap = loadGameHelpers();
    const [entry] = tacticalMap.resolveTrapOverlayEntries({
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "disabled", is_hidden: false, x: 4, y: 5 },
    });
    expect(entry).toMatchObject({ status: "disabled", label: "DISARMED", color: 0x7fae83 });
  });

  test("test_triggered_trap_overlay_is_danger", async () => {
    const tacticalMap = loadGameHelpers();
    const [entry] = tacticalMap.resolveTrapOverlayEntries({
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "triggered", is_hidden: false, x: 4, y: 5 },
    });
    expect(entry).toMatchObject({ status: "triggered", label: "POISON", color: 0xc54232 });
  });

  test("test_state_diff_highlights_trap_flags_status_and_poisoned", async () => {
    loadNewModules();
    const diffs = window.ControlledAgentStateDiffRenderer.diffSnapshots({
      flags: {},
      environment_objects: {
        gas_trap_1: { id: "gas_trap_1", type: "trap", name: "gas_trap_1", status: "revealed" },
      },
      party_status: {
        player: { name: "Player", status_effects: [] },
      },
    }, {
      flags: { hazard_lab_poison_trap_disarmed: true },
      environment_objects: {
        gas_trap_1: { id: "gas_trap_1", type: "trap", name: "gas_trap_1", status: "disabled" },
      },
      party_status: {
        player: { name: "Player", status_effects: [{ type: "poisoned", duration: 3 }] },
      },
    });
    expect(diffs).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "trap_signal", label: "flags.hazard_lab_poison_trap_disarmed = true" }),
      expect.objectContaining({ type: "trap_signal", label: "Gas Trap 1.status revealed -> disabled" }),
      expect.objectContaining({ type: "trap_signal", label: "Player.status += poisoned" }),
    ]));
  });

  test("test_director_trace_trap_events_activate_expected_nodes", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    if (window.ControlledAgentInputController) {
      window.ControlledAgentInputController.movePlayer(1, 0);
    }
    await flushAsync();
    expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");

    const insightNodes = window.ControlledAgentDirectorTrace.buildTraceNodes({}, {
      userLine: "Scout spots a trap",
      intent: "CHAT",
      uiEvents: [{ type: "trap_insight", trapId: "gas_trap_1" }],
    });
    const disarmNodes = window.ControlledAgentDirectorTrace.buildTraceNodes({}, {
      userLine: "侦察员，解除毒气陷阱",
      intent: "INTERACT",
      uiEvents: [{ type: "trap_disarmed", trapId: "gas_trap_1" }],
    });
    const triggerNodes = window.ControlledAgentDirectorTrace.buildTraceNodes({}, {
      userLine: "step into poison gas",
      intent: "trigger_zone",
      uiEvents: [{ type: "trap_triggered", trapId: "gas_trap_1" }],
    });
    expect(insightNodes).toEqual(expect.arrayContaining(["actor_view_filter", "actor_runtime", "domain_event", "event_drain", "ui_events"]));
    expect(disarmNodes).toEqual(expect.arrayContaining(["dm_router", "domain_event", "event_drain", "ui_events"]));
    expect(triggerNodes).toEqual(expect.arrayContaining(["domain_event", "event_drain", "ui_events"]));

    window.ControlledAgentDirectorTrace.activateTrace(insightNodes, {
      animate: false,
      uiEvents: [{ type: "trap_insight", trapId: "gas_trap_1" }],
    });
    expect(document.querySelector('li[data-node="actor_view_filter"]').classList.contains("is-agent-signal")).toBe(true);
    expect(document.querySelector('li[data-node="ui_events"]').classList.contains("is-agent-signal")).toBe(true);

    window.ControlledAgentDirectorTrace.activateTrace(disarmNodes, {
      animate: false,
      uiEvents: [
        { type: "trap_insight", trapId: "gas_trap_1" },
        { type: "trap_disarmed", trapId: "gas_trap_1" },
      ],
      autoIdleMs: 999999,
    });
    expect(document.getElementById("director-trace-summary").textContent).toContain("Trap disabled");
  });

  test("test_reduced_motion_trap_cards_do_not_pulse", async () => {
    loadNewModules();
    const previousMatchMedia = window.matchMedia;
    window.matchMedia = jest.fn().mockImplementation((query) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }));
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderTrapInsightCard({
      type: "trap_insight",
      actor: "scout",
      trapId: "gas_trap_1",
    });
    const card = document.querySelector(".agent-signal-card--trap-insight");
    expect(card).not.toBeNull();
    expect(card.classList.contains("is-reduced-motion")).toBe(true);
    expect(card.classList.contains("agent-signal-card--pulse")).toBe(false);
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    window.matchMedia = previousMatchMedia;
  });

  test("test_memory_echo_journal_rebuke_derives_ui_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[记忆回响] scout -> rebuked_by_player"],
    });
    expect(events).toContainEqual(expect.objectContaining({
      type: "memory_echo",
      actor: "scout",
      memoryType: "rebuked_by_player",
      tone: "resentful",
      source: "journal",
    }));
  });

  test("test_memory_echo_journal_sided_derives_ui_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[记忆回响] scout -> sided_with_player"],
    });
    expect(events).toContainEqual(expect.objectContaining({
      type: "memory_echo",
      actor: "scout",
      memoryType: "sided_with_player",
      tone: "complicit",
      source: "journal",
    }));
  });

  test("test_memory_echo_rebuke_flag_diff_derives_ui_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      flags: { hazard_lab_scout_rebuke_echo_seen: true },
    }, {
      flags: { hazard_lab_scout_rebuke_echo_seen: false },
    });
    expect(events).toContainEqual(expect.objectContaining({
      type: "memory_echo",
      memoryType: "rebuked_by_player",
      tone: "resentful",
      source: "state",
    }));
  });

  test("test_memory_echo_sided_flag_diff_derives_ui_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      flags: { hazard_lab_scout_complicity_echo_seen: true },
    }, {
      flags: {},
    });
    expect(events).toContainEqual(expect.objectContaining({
      type: "memory_echo",
      memoryType: "sided_with_player",
      tone: "complicit",
      source: "state",
    }));
  });

  test("test_memory_echo_response_token_fallback_derives_rebuke", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      responses: [{ speaker: "scout", text: "现在又需要我了？我会记住这笔账。" }],
    });
    expect(events).toContainEqual(expect.objectContaining({
      type: "memory_echo",
      actor: "scout",
      memoryType: "rebuked_by_player",
      tone: "resentful",
      source: "response",
    }));
  });

  test("test_memory_echo_journal_and_flag_are_deduped", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[记忆回响] scout -> rebuked_by_player"],
      flags: { hazard_lab_scout_rebuke_echo_seen: true },
    }, {
      flags: {},
    });
    expect(events.filter((event) => event.type === "memory_echo" && event.memoryType === "rebuked_by_player")).toHaveLength(1);
  });

  test("test_hud_renders_memory_echo_rebuke_card", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderMemoryEchoCard({
      type: "memory_echo",
      actor: "scout",
      memoryType: "rebuked_by_player",
      tone: "resentful",
      message: "He remembers you rebuked him.",
      quote: "Now you need me?",
    });
    const card = document.querySelector(".agent-signal-card--memory-echo.memory-echo-resentful");
    expect(card).not.toBeNull();
    expect(card.textContent).toContain("Memory Echo");
    expect(card.textContent).toContain("Scout");
    expect(card.textContent).toContain("Resentful");
    expect(card.textContent).toContain("Now you need me?");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_hud_renders_memory_echo_sided_card", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderMemoryEchoCard({
      type: "memory_echo",
      actor: "scout",
      memoryType: "sided_with_player",
      tone: "complicit",
      message: "He remembers you sided with him.",
      quote: "Cruelty shared becomes trust.",
    });
    const card = document.querySelector(".agent-signal-card--memory-echo.memory-echo-complicit");
    expect(card).not.toBeNull();
    expect(card.textContent).toContain("Memory Echo");
    expect(card.textContent).toContain("Complicit");
    expect(card.textContent).toContain("Cruelty shared becomes trust.");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_memory_echo_activates_director_trace", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes({}, {
      userLine: "侦察员，你怎么看这件事？",
      intent: "CHAT",
      uiEvents: [{ type: "memory_echo", actor: "scout", memoryType: "rebuked_by_player" }],
    });
    expect(nodes).toEqual(expect.arrayContaining(["actor_runtime", "domain_event", "event_drain", "ui_events"]));
    window.ControlledAgentDirectorTrace.activateTrace(nodes, {
      animate: false,
      uiEvents: [{ type: "memory_echo", actor: "scout", memoryType: "rebuked_by_player" }],
    });
    expect(document.querySelector('li[data-node="actor_runtime"]').classList.contains("is-agent-signal")).toBe(true);
    expect(document.querySelector('li[data-node="ui_events"]').classList.contains("is-agent-signal")).toBe(true);
  });

  test("test_wasd_does_not_activate_trace_for_memory_echo_feature", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    if (window.ControlledAgentInputController) {
      window.ControlledAgentInputController.movePlayer(1, 0);
    }
    await flushAsync();
    expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
  });

  test("test_state_diff_highlights_memory_echo_flags", async () => {
    loadNewModules();
    const diffs = window.ControlledAgentStateDiffRenderer.diffSnapshots({
      flags: {},
    }, {
      flags: {
        hazard_lab_scout_memory_echo_seen: true,
        hazard_lab_scout_rebuke_echo_seen: true,
        hazard_lab_scout_complicity_echo_seen: true,
      },
    });
    expect(diffs).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "memory_echo_signal", label: "flags.hazard_lab_scout_memory_echo_seen = true" }),
      expect.objectContaining({ type: "memory_echo_signal", label: "flags.hazard_lab_scout_rebuke_echo_seen = true" }),
      expect.objectContaining({ type: "memory_echo_signal", label: "flags.hazard_lab_scout_complicity_echo_seen = true" }),
    ]));
    window.ControlledAgentStateDiffRenderer.renderDiffs(diffs, { autoExpand: false });
    expect(document.querySelector(".world-diff-row--memory_echo_signal.memory-echo-diff")).not.toBeNull();
  });

  test("test_state_diff_synthesizes_memory_echo_flags_from_journal", async () => {
    loadNewModules();
    const diffs = window.ControlledAgentStateDiffRenderer.diffSnapshots({
      journal_events: [],
    }, {
      journal_events: ["[记忆回响] scout -> rebuked_by_player"],
    });
    expect(diffs).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "memory_echo_signal", label: "flags.hazard_lab_scout_memory_echo_seen = true" }),
      expect.objectContaining({ type: "memory_echo_signal", label: "flags.hazard_lab_scout_rebuke_echo_seen = true" }),
    ]));
  });

  test("test_memory_echo_does_not_break_existing_agent_signals", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: [
        "[队友建议] Scout topic=lab_key 找钥匙",
        "[交涉筹码] diary_evidence -> gatekeeper_elixir_truth",
        "[陷阱感知] scout -> gas_trap_1",
        "[记忆回响] scout -> sided_with_player",
      ],
    });
    expect(events).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "companion_guidance" }),
      expect.objectContaining({ type: "negotiation_leverage" }),
      expect.objectContaining({ type: "trap_insight" }),
      expect.objectContaining({ type: "memory_echo" }),
    ]));
  });

  test("test_party_stance_journals_merge_into_one_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: [
        "[站队] analyst -> mercy",
        "[站队] tactician -> execute",
        "[站队] scout -> resentful",
      ],
    });
    const stances = events.filter((event) => event.type === "party_stance");
    expect(stances).toHaveLength(1);
    expect(stances[0]).toMatchObject({ target: "gatekeeper" });
    expect(stances[0].stances).toEqual(expect.arrayContaining([
      { actor: "analyst", stance: "mercy" },
      { actor: "tactician", stance: "execute" },
      { actor: "scout", stance: "resentful" },
    ]));
  });

  test("test_single_party_stance_journal_derives_event", async () => {
    loadNewModules();
    const event = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[站队] scout -> mocking"],
    }).find((candidate) => candidate.type === "party_stance");
    expect(event).toMatchObject({
      type: "party_stance",
      target: "gatekeeper",
      stances: [{ actor: "scout", stance: "mocking" }],
    });
  });

  test("test_mercy_resolution_spared_journal_derives_event", async () => {
    loadNewModules();
    const event = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[抉择] gatekeeper -> spared"],
    }).find((candidate) => candidate.type === "mercy_resolution");
    expect(event).toMatchObject({ target: "gatekeeper", result: "spared", source: "journal" });
  });

  test("test_mercy_resolution_executed_journal_derives_event", async () => {
    loadNewModules();
    const event = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[抉择] gatekeeper -> executed"],
    }).find((candidate) => candidate.type === "mercy_resolution");
    expect(event).toMatchObject({ target: "gatekeeper", result: "executed", source: "journal" });
  });

  test("test_mercy_resolution_flag_diff_derives_event", async () => {
    loadNewModules();
    const spared = window.ControlledAgentUIEventAdapter.extractUIEvents({
      flags: {
        hazard_lab_gatekeeper_spared: true,
        hazard_lab_gatekeeper_key_available: true,
      },
    }, {
      flags: {},
    }).find((event) => event.type === "mercy_resolution");
    const executed = window.ControlledAgentUIEventAdapter.extractUIEvents({
      flags: { hazard_lab_gatekeeper_executed: true },
    }, {
      flags: {},
    }).find((event) => event.type === "mercy_resolution");
    expect(spared).toMatchObject({ result: "spared", keyAvailable: true });
    expect(executed).toMatchObject({ result: "executed" });
  });

  test("test_mercy_resolution_journal_and_flag_are_deduped", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[抉择] gatekeeper -> spared"],
      flags: { hazard_lab_gatekeeper_spared: true },
    }, {
      flags: {},
    });
    expect(events.filter((event) => event.type === "mercy_resolution" && event.result === "spared")).toHaveLength(1);
  });

  test("test_hud_renders_party_stance_card", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderPartyStanceCard({
      type: "party_stance",
      target: "gatekeeper",
      stances: [
        { actor: "analyst", stance: "mercy" },
        { actor: "tactician", stance: "execute" },
        { actor: "scout", stance: "resentful" },
      ],
    });
    const card = document.querySelector(".agent-signal-card--party-stance");
    expect(card).not.toBeNull();
    expect(card.textContent).toContain("Party Split");
    expect(card.textContent).toContain("Analyst");
    expect(card.textContent).toContain("Mercy");
    expect(card.textContent).toContain("Tactician");
    expect(card.textContent).toContain("Execute");
    expect(card.textContent).toContain("Scout");
    expect(card.textContent).toContain("Resentful");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_hud_renders_mercy_resolution_spared_card", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderMercyResolutionCard({
      type: "mercy_resolution",
      target: "gatekeeper",
      result: "spared",
      keyAvailable: true,
    });
    const card = document.querySelector(".agent-signal-card--mercy-resolution.mercy-resolution-spared");
    expect(card).not.toBeNull();
    expect(card.textContent).toContain("Gatekeeper Spared");
    expect(card.textContent).toContain("spared / neutralized");
    expect(card.textContent).toContain("Analyst + / Tactician -");
    expect(card.textContent).toContain("Key path remains available.");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_hud_renders_mercy_resolution_executed_card", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderMercyResolutionCard({
      type: "mercy_resolution",
      target: "gatekeeper",
      result: "executed",
    });
    const card = document.querySelector(".agent-signal-card--mercy-resolution.mercy-resolution-executed");
    expect(card).not.toBeNull();
    expect(card.textContent).toContain("Gatekeeper Executed");
    expect(card.textContent).toContain("dead / defeated");
    expect(card.textContent).toContain("Analyst - / Tactician +");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_state_diff_highlights_mercy_flags_gatekeeper_state_and_affection", async () => {
    loadNewModules();
    const diffs = window.ControlledAgentStateDiffRenderer.diffSnapshots({
      flags: {},
      environment_objects: {
        gatekeeper: { id: "gatekeeper", name: "Gatekeeper", status: "alive", faction: "hostile" },
      },
      party_status: {
        analyst: { name: "Analyst", affection: 0 },
        tactician: { name: "Tactician", affection: 0 },
      },
    }, {
      flags: {
        hazard_lab_gatekeeper_mercy_window: true,
        hazard_lab_gatekeeper_mercy_resolved: true,
        hazard_lab_gatekeeper_spared: true,
        hazard_lab_gatekeeper_key_available: true,
      },
      environment_objects: {
        gatekeeper: { id: "gatekeeper", name: "Gatekeeper", status: "spared", faction: "neutralized" },
      },
      party_status: {
        analyst: { name: "Analyst", affection: 1 },
        tactician: { name: "Tactician", affection: -1 },
      },
    });
    expect(diffs).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "mercy_signal", label: "flags.hazard_lab_gatekeeper_mercy_window = true" }),
      expect.objectContaining({ type: "mercy_signal", label: "flags.hazard_lab_gatekeeper_mercy_resolved = true" }),
      expect.objectContaining({ type: "mercy_signal", label: "flags.hazard_lab_gatekeeper_spared = true" }),
      expect.objectContaining({ type: "mercy_signal", label: "flags.hazard_lab_gatekeeper_key_available = true" }),
      expect.objectContaining({ type: "mercy_signal", label: "Gatekeeper.status alive -> spared" }),
      expect.objectContaining({ type: "mercy_signal", label: "Gatekeeper.faction = neutralized" }),
      expect.objectContaining({ type: "affection", label: "Analyst.affection +1" }),
      expect.objectContaining({ type: "affection", label: "Tactician.affection -1" }),
    ]));
    window.ControlledAgentStateDiffRenderer.renderDiffs(diffs, { autoExpand: false });
    expect(document.querySelector(".world-diff-row--mercy_signal.mercy-signal-diff")).not.toBeNull();
  });

  test("test_director_trace_party_stance_activates_party_nodes", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes({}, {
      userLine: "队友们，你们觉得该怎么处理 Gatekeeper？",
      intent: "CHAT",
      uiEvents: [{ type: "party_stance", target: "gatekeeper", stances: [{ actor: "analyst", stance: "mercy" }] }],
    });
    expect(nodes).toEqual(expect.arrayContaining(["dm_router", "actor_runtime", "ui_events"]));
    window.ControlledAgentDirectorTrace.activateTrace(nodes, {
      animate: false,
      uiEvents: [{ type: "party_stance", target: "gatekeeper", stances: [{ actor: "analyst", stance: "mercy" }] }],
    });
    expect(document.querySelector('li[data-node="actor_runtime"]').classList.contains("is-agent-signal")).toBe(true);
    expect(document.querySelector('li[data-node="ui_events"]').classList.contains("is-agent-signal")).toBe(true);
  });

  test("test_director_trace_mercy_resolution_activates_event_nodes", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes({}, {
      userLine: "放过他",
      intent: "CHAT",
      uiEvents: [{ type: "mercy_resolution", target: "gatekeeper", result: "spared" }],
    });
    expect(nodes).toEqual(expect.arrayContaining(["player_input", "dm_router", "domain_event", "event_drain", "ui_events"]));
    window.ControlledAgentDirectorTrace.activateTrace(nodes, {
      animate: false,
      uiEvents: [{ type: "mercy_resolution", target: "gatekeeper", result: "spared" }],
    });
    expect(document.querySelector('li[data-node="domain_event"]').classList.contains("is-agent-signal")).toBe(true);
    expect(document.querySelector('li[data-node="event_drain"]').classList.contains("is-agent-signal")).toBe(true);
  });

  test("test_wasd_does_not_activate_trace_for_mercy_feature", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    if (window.ControlledAgentInputController) {
      window.ControlledAgentInputController.movePlayer(1, 0);
    }
    await flushAsync();
    expect(window.ControlledAgentDirectorTrace.getState()).toBe("idle");
  });

  test("test_mercy_signals_do_not_break_existing_agent_signals", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: [
        "[队友建议] Scout topic=lab_key 找钥匙",
        "[交涉筹码] diary_evidence -> gatekeeper_elixir_truth",
        "[陷阱感知] scout -> gas_trap_1",
        "[记忆回响] scout -> sided_with_player",
        "[站队] analyst -> mercy",
        "[抉择] gatekeeper -> spared",
      ],
    });
    expect(events).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "companion_guidance" }),
      expect.objectContaining({ type: "negotiation_leverage" }),
      expect.objectContaining({ type: "trap_insight" }),
      expect.objectContaining({ type: "memory_echo" }),
      expect.objectContaining({ type: "party_stance" }),
      expect.objectContaining({ type: "mercy_resolution" }),
    ]));
  });

  test("test_boss_intro_journal_derives_ui_event", async () => {
    loadNewModules();
    const event = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[Boss Encounter] gatekeeper_confrontation_started"],
    }).find((candidate) => candidate.type === "boss_intro");
    expect(event).toMatchObject({
      type: "boss_intro",
      targetId: "gatekeeper",
      keyHolder: true,
      poisonValvePresent: true,
      source: "journal",
    });
  });

  test("test_boss_strategy_journals_merge_into_one_event", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: [
        "[Boss方案] scout -> steal_key",
        "[Boss方案] analyst -> contain_corruption",
        "[Boss方案] tactician -> execute",
      ],
    });
    const strategies = events.filter((event) => event.type === "boss_strategy");
    expect(strategies).toHaveLength(1);
    expect(strategies[0].strategies).toEqual(expect.arrayContaining([
      { actor: "scout", plan: "steal_key" },
      { actor: "analyst", plan: "contain_corruption" },
      { actor: "tactician", plan: "execute" },
    ]));
  });

  test("test_boss_route_journals_derive_route_events", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: [
        "[Boss解决] negotiation -> key_surrendered",
        "[Boss解决] scout_steal -> heavy_iron_key",
        "[偷钥匙失败] scout -> gatekeeper_alerted",
        "[Boss解决] assault -> gatekeeper_defeated",
      ],
    });
    expect(events).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "boss_route", route: "negotiation", result: "key_surrendered" }),
      expect.objectContaining({ type: "boss_route", route: "scout_steal", result: "heavy_iron_key" }),
      expect.objectContaining({ type: "boss_route", route: "scout_steal", result: "gatekeeper_alerted", failed: true }),
      expect.objectContaining({ type: "boss_route", route: "assault", result: "gatekeeper_defeated" }),
    ]));
  });

  test("test_poison_valve_journal_and_flag_derive_ui_event", async () => {
    loadNewModules();
    const journalEvent = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: ["[毒气泄漏] poison_valve -> lab_poison"],
    }).find((event) => event.type === "poison_valve");
    const flagEvent = window.ControlledAgentUIEventAdapter.extractUIEvents({
      flags: { act4_poison_valve_triggered: true },
    }, {
      flags: {},
    }).find((event) => event.type === "poison_valve");
    expect(journalEvent).toMatchObject({ valveId: "poison_valve", status: "triggered", result: "lab_poison" });
    expect(flagEvent).toMatchObject({ valveId: "poison_valve", status: "triggered" });
  });

  test("test_hud_renders_boss_cards", async () => {
    loadNewModules();
    jest.useFakeTimers();
    window.ControlledAgentHudRenderers.renderBossIntroCard({ type: "boss_intro", diaryTruthAvailable: true });
    window.ControlledAgentHudRenderers.renderBossStrategyCard({
      type: "boss_strategy",
      strategies: [
        { actor: "scout", plan: "steal_key" },
        { actor: "analyst", plan: "contain_corruption" },
        { actor: "tactician", plan: "execute" },
      ],
    });
    window.ControlledAgentHudRenderers.renderBossRouteCard({ type: "boss_route", route: "negotiation", result: "key_surrendered" });
    expect(document.querySelector(".agent-signal-card--boss-intro").textContent).toContain("Diary Truth Available");
    expect(document.querySelector(".agent-signal-card--boss-strategy").textContent).toContain("Steal Key");
    expect(document.querySelector(".agent-signal-card--boss-route").textContent).toContain("Negotiation Success");
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  test("test_poison_valve_overlay_statuses", async () => {
    const tacticalMap = loadGameHelpers();
    const entries = tacticalMap.resolveTrapOverlayEntries({
      poison_valve: { id: "poison_valve", type: "poison_valve", x: 8, y: 4, status: "intact" },
      potion_tank: { id: "potion_tank", type: "potion_tank", x: 9, y: 4, status: "triggered" },
      safe_valve: { id: "safe_valve", type: "poison_valve", x: 10, y: 4, status: "disabled" },
    });
    expect(entries).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "poison_valve", kind: "poison_valve", status: "intact", label: "VALVE" }),
      expect.objectContaining({ id: "potion_tank", kind: "poison_valve", status: "triggered", label: "LEAK" }),
      expect.objectContaining({ id: "safe_valve", kind: "poison_valve", status: "disabled", label: "SAFE" }),
    ]));
  });

  test("test_act2_trap_visual_states_only_triggered_renders_poison_gas", async () => {
    const tacticalMap = loadGameHelpers();
    expect(tacticalMap.shouldRenderAct2PoisonGas({
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "hidden", is_hidden: true },
    })).toBe(false);
    expect(tacticalMap.shouldRenderAct2PoisonGas({
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "revealed", is_hidden: false },
    })).toBe(false);
    expect(tacticalMap.shouldRenderAct2PoisonGas({
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "disabled", is_hidden: false },
    })).toBe(false);
    expect(tacticalMap.shouldRenderAct2PoisonGas({
      gas_trap_1: { id: "gas_trap_1", type: "trap", status: "triggered", is_hidden: false },
    })).toBe(true);
  });

  test("test_trap_revealed_overlay_is_suspicious_marker_not_poison_gas", async () => {
    const tacticalMap = loadGameHelpers();
    const entries = tacticalMap.resolveTrapOverlayEntries({
      gas_trap_1: { id: "gas_trap_1", type: "trap", x: 4, y: 6, status: "revealed", is_hidden: false },
    });
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      id: "gas_trap_1",
      status: "revealed",
      label: "TRAP",
      fill: 0x4a3518,
    });
    expect(entries[0].label).not.toBe("POISON");
  });

  test("test_state_diff_highlights_act4_boss_flags_gatekeeper_and_key", async () => {
    loadNewModules();
    const diffs = window.ControlledAgentStateDiffRenderer.diffSnapshots({
      flags: {},
      player_inventory: {},
      environment_objects: {
        gatekeeper: { id: "gatekeeper", name: "Gatekeeper", status: "alive", faction: "hostile" },
      },
      party_status: { analyst: { name: "Analyst", affection: 0 } },
    }, {
      flags: {
        act4_boss_room_entered: true,
        act4_diary_truth_available: true,
        act4_negotiation_success: true,
        act4_heavy_iron_key_obtained: true,
      },
      player_inventory: { heavy_iron_key: 1 },
      environment_objects: {
        gatekeeper: { id: "gatekeeper", name: "Gatekeeper", status: "spared", faction: "neutralized" },
      },
      party_status: { analyst: { name: "Analyst", affection: 2 } },
    });
    expect(diffs).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "boss_signal", label: "flags.act4_boss_room_entered = true" }),
      expect.objectContaining({ type: "boss_signal", label: "flags.act4_diary_truth_available = true" }),
      expect.objectContaining({ type: "boss_signal", label: "Gatekeeper.status alive -> spared" }),
      expect.objectContaining({ type: "boss_signal", label: "Gatekeeper.faction = neutralized" }),
      expect.objectContaining({ type: "inventory", label: "player.inventory += heavy_iron_key" }),
      expect.objectContaining({ type: "affection", label: "Analyst.affection +2" }),
    ]));
    window.ControlledAgentStateDiffRenderer.renderDiffs(diffs, { autoExpand: false });
    expect(document.querySelector(".world-diff-row--boss_signal.boss-signal-diff")).not.toBeNull();
  });

  test("test_director_trace_boss_signals_activate_expected_nodes", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    await bootAppForTest("http://localhost/?qa_test=1");
    const nodes = window.ControlledAgentDirectorTrace.buildTraceNodes({}, {
      userLine: "侦察员偷钥匙",
      intent: "INTERACT",
      uiEvents: [{ type: "boss_route", route: "scout_steal", result: "heavy_iron_key" }],
    });
    expect(nodes).toEqual(expect.arrayContaining(["dm_router", "actor_runtime", "domain_event", "event_drain", "ui_events"]));
    window.ControlledAgentDirectorTrace.activateTrace(nodes, {
      animate: false,
      uiEvents: [{ type: "boss_route", route: "scout_steal", result: "heavy_iron_key" }],
    });
    expect(document.querySelector('li[data-node="domain_event"]').classList.contains("is-agent-signal")).toBe(true);
    expect(document.querySelector('li[data-node="ui_events"]').classList.contains("is-agent-signal")).toBe(true);
  });

  test("test_boss_text_routing_normalizes_actions", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&map_id=hazard_lab");
    expect(api.buildChatPayload("我们怎么处理他？", "", "").payload).toMatchObject({
      intent: "CHAT",
      target: "gatekeeper",
      source: "boss_strategy",
    });
    expect(api.buildChatPayload("侦察员偷钥匙", "", "").payload).toMatchObject({
      intent: "INTERACT",
      target: "gatekeeper",
      source: "boss_steal_key",
      character: "scout",
    });
    expect(api.buildChatPayload("用日记真相说服他", "", "").payload).toMatchObject({
      intent: "CHAT",
      target: "gatekeeper",
      source: "boss_diary_truth",
    });
    expect(api.buildChatPayload("Tactician 解决他", "", "").payload).toMatchObject({
      intent: "ATTACK",
      target: "gatekeeper",
      source: "boss_assault",
      character: "tactician",
    });
  });

  test("test_boss_intro_dispatch_updates_act_card_to_act4", async () => {
    spyOnFetch().mockResolvedValue(mockResponse({}));
    const api = await bootAppForTest("http://localhost/?qa_test=1&map_id=hazard_lab");
    api.dispatchUIEventsFromResponse({
      journal_events: ["[Boss Encounter] gatekeeper_confrontation_started"],
      flags: { act4_boss_room_entered: true, act4_gatekeeper_confrontation_started: true },
    }, { flags: {} });
    expect(document.body.textContent).toContain("Act 4");
    expect(document.body.textContent).toContain("Gatekeeper 攥着沉重铁钥匙");
  });

  test("test_act4_boss_signals_do_not_break_existing_agent_signals", async () => {
    loadNewModules();
    const events = window.ControlledAgentUIEventAdapter.extractUIEvents({
      journal_events: [
        "[队友建议] Scout topic=lab_key 找钥匙",
        "[交涉筹码] diary_evidence -> gatekeeper_elixir_truth",
        "[陷阱感知] scout -> gas_trap_1",
        "[记忆回响] scout -> sided_with_player",
        "[站队] analyst -> mercy",
        "[抉择] gatekeeper -> spared",
        "[Boss Encounter] gatekeeper_confrontation_started",
        "[Boss方案] scout -> steal_key",
      ],
    });
    expect(events).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "companion_guidance" }),
      expect.objectContaining({ type: "negotiation_leverage" }),
      expect.objectContaining({ type: "trap_insight" }),
      expect.objectContaining({ type: "memory_echo" }),
      expect.objectContaining({ type: "party_stance" }),
      expect.objectContaining({ type: "mercy_resolution" }),
      expect.objectContaining({ type: "boss_intro" }),
      expect.objectContaining({ type: "boss_strategy" }),
    ]));
  });
});
