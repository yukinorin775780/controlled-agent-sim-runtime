/**
 * tiled-adapter.js
 * ───────────────────────────────────────────────────────
 * Tiled JSON normalization layer.
 *
 * normalizeTiledMap(rawJson) → {
 *   id, width, height, tileLayers,
 *   collision[][], losBlockers[][], groundTypes[][],
 *   triggers[], interactables[], spawns[], rooms[]
 * }
 *
 * Currently uses a built-in HAZARD_LAB_FIXTURE.
 * Real Tiled JSON exports are loaded from web_ui/assets/maps/*.json.
 *
 * Exposed on window.ControlledAgentTiledAdapter.
 */
(() => {
  "use strict";

  /* ── helpers ── */
  function safeObj(v) {
    return v && typeof v === "object" ? v : {};
  }
  function safeArr(v) {
    return Array.isArray(v) ? v : [];
  }
  function normalizeKey(v) {
    return String(v || "").trim().toLowerCase();
  }

  function make2D(w, h, fill) {
    const grid = [];
    for (let y = 0; y < h; y++) {
      const row = [];
      for (let x = 0; x < w; x++) row.push(fill);
      grid.push(row);
    }
    return grid;
  }

  function normalizeEntityId(rawId, rawType, rawProps) {
    const id = String(rawId || "").trim();
    const type = normalizeKey(rawType);
    const props = safeObj(rawProps);
    const alias = String(props.alias_id || props.aliasId || "").trim();
    if (alias) {
      return {
        id: alias,
        source_id: id || alias,
      };
    }
    const key = normalizeKey(id);
    if (!id) return { id: "" };
    if (key === "heavy_oak_door") return { id: "heavy_oak_door_1", source_id: id };
    if (key === "loot_chest_1") return { id: "chest_1", source_id: id };
    if (key === "study_chest") return { id: "chest_1", source_id: id };
    if (key === "exit_door") return { id: "heavy_oak_door_1", source_id: id };
    if (key.startsWith("poison_trap") || (type === "trap" && key.includes("poison"))) {
      return { id, alias_id: "gas_trap_1" };
    }
    return { id };
  }

  /* ── Parse our YAML-style grid strings ── */
  function parseGridStrings(raw, w, h) {
    const collision = make2D(w, h, false);
    const rows = safeArr(raw);
    for (let y = 0; y < Math.min(rows.length, h); y++) {
      const cells =
        typeof rows[y] === "string"
          ? rows[y].trim().split(/\s+/)
          : safeArr(rows[y]);
      for (let x = 0; x < Math.min(cells.length, w); x++) {
        collision[y][x] = String(cells[x]).toUpperCase() === "W";
      }
    }
    return collision;
  }

  /* ── Parse obstacles into losBlockers grid ── */
  function buildLosGrid(obstacles, w, h) {
    const grid = make2D(w, h, false);
    safeArr(obstacles).forEach((obs) => {
      const o = safeObj(obs);
      if (!o.blocks_los) return;
      safeArr(o.coordinates).forEach((coord) => {
        if (!Array.isArray(coord) || coord.length < 2) return;
        const x = Math.round(Number(coord[0]));
        const y = Math.round(Number(coord[1]));
        if (x >= 0 && x < w && y >= 0 && y < h) grid[y][x] = true;
      });
    });
    return grid;
  }

  /* ── Mark obstacle collision into collision grid ── */
  function mergeObstacleCollision(collision, obstacles, w, h) {
    safeArr(obstacles).forEach((obs) => {
      const o = safeObj(obs);
      if (!o.blocks_movement) return;
      safeArr(o.coordinates).forEach((coord) => {
        if (!Array.isArray(coord) || coord.length < 2) return;
        const x = Math.round(Number(coord[0]));
        const y = Math.round(Number(coord[1]));
        if (x >= 0 && x < w && y >= 0 && y < h) collision[y][x] = true;
      });
    });
  }

  /* ── Extract triggers from obstacles ── */
  function extractTriggers(obstacles) {
    return safeArr(obstacles)
      .filter((o) => {
        const type = String(safeObj(o).type || "").toLowerCase();
        return (
          type === "transition_zone" ||
          type === "trigger" ||
          type === "narrative_trigger" ||
          type === "trap"
        );
      })
      .map((o) => {
        const obs = safeObj(o);
        const coords = safeArr(obs.coordinates);
        const first = safeArr(coords[0]);
        return {
          id: obs.entity_id || obs.id || obs.name || "trigger",
          x: Number(first[0]) || 0,
          y: Number(first[1]) || 0,
          w: 1,
          h: 1,
          type: obs.type || "trigger",
          data: obs,
        };
      });
  }

  /* ── Extract interactables from environment_objects ── */
  function extractInteractables(envObjects) {
    return safeArr(envObjects).map((raw) => {
      const o = safeObj(raw);
      const pos = safeArr(o.position);
      const status = String(o.status || (o.is_hidden ? "hidden" : "")).toLowerCase();
      return {
        id: o.id || "",
        x: Number(pos[0]) || Number(o.x) || 0,
        y: Number(pos[1]) || Number(o.y) || 0,
        type: o.type || "object",
        name: o.name || o.id || "",
        status,
        is_hidden: Boolean(o.is_hidden),
        is_revealed: Boolean(o.is_revealed),
        discovered: Boolean(o.discovered),
        label:
          (window.ControlledAgentHazardMeta &&
            window.ControlledAgentHazardMeta.OBJECT_LABELS[o.id]) ||
          o.name ||
          o.id ||
          "",
        data: o,
      };
    });
  }

  /* ── Extract spawns ── */
  function extractSpawns(rawSpawns) {
    return safeArr(rawSpawns).map((raw) => {
      const s = safeObj(raw);
      const pos = safeArr(s.position);
      return {
        id: s.instance_id || s.id || "",
        x: Number(pos[0]) || Number(s.x) || 0,
        y: Number(pos[1]) || Number(s.y) || 0,
        faction: s.faction || "neutral",
        prefab: s.prefab || "",
      };
    });
  }

  function extractSpawnInteractables(rawSpawns) {
    return safeArr(rawSpawns)
      .map((raw) => {
        const s = safeObj(raw);
        const pos = safeArr(s.position);
        const id = s.instance_id || s.id || "";
        if (!id) return null;
        return {
          id,
          x: Number(pos[0]) || Number(s.x) || 0,
          y: Number(pos[1]) || Number(s.y) || 0,
          type: "npc",
          name: id,
          label:
            (window.ControlledAgentHazardMeta &&
              window.ControlledAgentHazardMeta.OBJECT_LABELS[id]) ||
            id,
        };
      })
      .filter(Boolean);
  }

  /* ══════════════════════════════════════════════════════
   *  HAZARD_LAB_FIXTURE
   *  Fallback map data when JSON export not loaded
   * ══════════════════════════════════════════════════════ */
  const HAZARD_LAB_FIXTURE = Object.freeze({
    map_id: "hazard_lab",
    name: "危害研究员的废弃实验室",
    dimensions: [20, 14],
    player_start: [2, 2],
    grid: [
      "W W W W W W W W W W W W W W W W W W W W",
      "W . . . . . W W W W W . . . . . . . . W",
      "W . . . . . W W W W W . . . . . . . . W",
      "W . . . . . . . . . . . . . . . . . . W",
      "W . . . . . W W W W W . . . . . . . . W",
      "W W W W . W W W W W W W W W W . W W W W",
      "W W W W . W W W W W W W W W W . W W W W",
      "W . . . . . . . W W . . . . . . . . . W",
      "W . . . . . . . W W . . . . . . . . . W",
      "W . . . . . . . W W . . . . . . . . . W",
      "W W W W W W W W W W . W W W W W W W W W",
      "W . . . . . . . . . . . . . . W W W W W",
      "W . . . . . . . . . . W W W W W W W W W",
      "W W W W W W W W W W W W W W W W W W W W",
    ],
    environment_objects: [
      {
        id: "gas_trap_1",
        type: "trap",
        name: "毒气陷阱",
        position: [5, 11],
        is_hidden: true,
        detect_dc: 13,
        disarm_dc: 15,
        damage: "2d6",
        damage_type: "poison",
        save_dc: 13,
        trigger_radius: 0,
      },
      {
        id: "chest_1",
        type: "chest",
        name: "危害研究员的战利品箱",
        status: "open",
        description: "箱盖虚掩，里面散落着钥匙与杂物。",
        position: [16, 2],
        inventory: { heavy_iron_key: 1, gold_coin: 12 },
      },
      {
        id: "hazard_diary",
        type: "readable",
        name: "沾满血污的日记本",
        position: [15, 3],
        lore_id: "hazard_diary_1",
      },
      {
        id: "heavy_oak_door_1",
        type: "door",
        name: "通往地表的沉重大门",
        position: [18, 3],
        is_open: false,
        is_locked: true,
        is_exit: true,
        key_required: "heavy_iron_key",
        requires_flag: "world_hazard_lab_gatekeeper_defeated",
        room_id: "room_exit",
        connects_from: "room_d_lab",
        connects_to: "room_exit",
        alias_ids: ["exit_door"],
      },
    ],
    obstacles: [
      /* Act 1 — corridor approach trigger zone at rows 3-4, columns 1-5 */
      {
        type: "narrative_trigger",
        entity_id: "act1_corridor_approach",
        name: "走廊感知区",
        coordinates: [[1, 3], [2, 3], [3, 3], [4, 3], [5, 3]],
        blocks_movement: false,
        blocks_los: false,
      },
    ],
    spawns: [
      {
        prefab: "characters/gatekeeper.yaml",
        instance_id: "gatekeeper",
        position: [4, 9],
        faction: "neutral",
      },
    ],
  });

  /* ══════════════════════════════════════════════════════
   *  normalizeTiledMap(rawJson)
   *
   *  Accepts:
   *    - Our YAML-derived map format (with .grid, .dimensions, etc.)
   *    - Future: Tiled JSON export (with .layers, .tilesets, etc.)
   *    - null/undefined → returns hazard_lab fixture
   * ══════════════════════════════════════════════════════ */
  function normalizeTiledMap(rawJson) {
    const raw = safeObj(rawJson);

    /* No input / empty → default fixture */
    if (!rawJson || Object.keys(raw).length === 0) {
      return normalizeTiledMap(HAZARD_LAB_FIXTURE);
    }

    /* Tiled JSON format detection (has .layers array) */
    if (Array.isArray(raw.layers)) {
      return parseTiledJson(raw);
    }

    /* Our YAML-derived format */
    return parseYamlFormat(raw);
  }

  function parseYamlFormat(raw) {
    const dims = safeArr(raw.dimensions);
    const w = Number(dims[0]) || Number(raw.width) || 20;
    const h = Number(dims[1]) || Number(raw.height) || 14;
    const playerStart = safeArr(raw.player_start);

    const collision = parseGridStrings(raw.grid, w, h);
    mergeObstacleCollision(collision, raw.obstacles, w, h);
    const losBlockers = buildLosGrid(raw.obstacles, w, h);

    /* Merge wall cells into losBlockers too */
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (collision[y][x]) losBlockers[y][x] = true;
      }
    }

    return {
      id: raw.map_id || raw.id || "",
      name: raw.name || "",
      width: w,
      height: h,
      playerStart: {
        x: Number(playerStart[0]) || 0,
        y: Number(playerStart[1]) || 0,
      },
      tileLayers: [],
      collision,
      losBlockers,
      triggers: extractTriggers(raw.obstacles),
      interactables: extractInteractables(raw.environment_objects).concat(
        extractSpawnInteractables(raw.spawns)
      ),
      spawns: extractSpawns(raw.spawns),
    };
  }

  /* ══════════════════════════════════════════════════════
   *  parseTiledJson — full Tiled JSON parsing
   *  Handles:
   *    - tilelayer with name collision/walls → collision grid
   *    - tilelayer with name los_blockers/los → losBlockers grid
   *    - objectgroup layers → triggers, interactables, spawns,
   *      player_start, collision rects, los_blocker rects
   * ══════════════════════════════════════════════════════ */
  function parseTiledJson(raw) {
    const tw = Number(raw.tilewidth) || 32;
    const th = Number(raw.tileheight) || 32;
    const w = Number(raw.width) || 20;
    const h = Number(raw.height) || 14;
    const collision = make2D(w, h, false);
    const losBlockers = make2D(w, h, false);
    const groundTypes = make2D(w, h, 0);
    const triggers = [];
    const interactables = [];
    const spawns = [];
    const rooms = [];
    let playerStart = { x: 0, y: 0 };

    safeArr(raw.layers).forEach((layer) => {
      const l = safeObj(layer);
      const name = String(l.name || "").toLowerCase();
      const ltype = String(l.type || "").toLowerCase();

      /* ── Tile layers (gid-based collision / los) ── */
      if (ltype === "tilelayer" && Array.isArray(l.data)) {
        const data = l.data;
        if (name === "collision" || name === "walls") {
          data.forEach((gid, i) => {
            if (gid > 0) {
              const x = i % w;
              const y = Math.floor(i / w);
              if (x < w && y < h) collision[y][x] = true;
            }
          });
        }
        if (name === "los_blockers" || name === "los") {
          data.forEach((gid, i) => {
            if (gid > 0) {
              const x = i % w;
              const y = Math.floor(i / w);
              if (x < w && y < h) losBlockers[y][x] = true;
            }
          });
        }
        if (name === "ground_types" || name === "ground" || name === "terrain") {
          data.forEach((gid, i) => {
            const x = i % w;
            const y = Math.floor(i / w);
            if (x < w && y < h) groundTypes[y][x] = Number(gid) || 0;
          });
        }
        return;
      }

      /* ── Object group layers ── */
      if (ltype === "objectgroup" && Array.isArray(l.objects)) {
        l.objects.forEach((rawObj) => {
          const obj = safeObj(rawObj);
          parseObjectGroupItem(obj, name, tw, th, w, h,
            collision, losBlockers, triggers, interactables, spawns, rooms,
            (ps) => { playerStart = ps; });
        });
      }
    });

    assignRoomMembership(triggers, rooms);
    assignRoomMembership(interactables, rooms);
    assignRoomMembership(spawns, rooms);

    return {
      id: raw.map_id || raw.id || "",
      name: raw.name || "",
      width: w,
      height: h,
      playerStart,
      tileLayers: safeArr(raw.layers),
      collision,
      losBlockers,
      groundTypes,
      triggers,
      interactables,
      spawns,
      rooms,
    };
  }

  /**
   * Parse a single Tiled object from an objectgroup layer.
   *
   * Object classification priority:
   *   1. obj.type / obj.class (Tiled ≥ 1.9 uses "class" instead of "type")
   *   2. Layer name as fallback category
   *   3. Custom properties array → flattened to key/value map
   */
  function parseObjectGroupItem(
    obj, layerName, tw, th, mapW, mapH,
    collision, losBlockers, triggers, interactables, spawns, rooms,
    playerStartSetter
  ) {
    const objType = String(obj.type || obj["class"] || "").toLowerCase();
    const objName = String(obj.name || "").toLowerCase();
    const props = flattenProperties(obj.properties);

    /* Pixel → grid coordinates */
    const gx = Math.floor(Number(obj.x || 0) / tw);
    const gy = Math.floor(Number(obj.y || 0) / th);
    const gw = Math.max(1, Math.floor(Number(obj.width || tw) / tw));
    const gh = Math.max(1, Math.floor(Number(obj.height || th) / th));

    if (objType === "room" || layerName === "rooms") {
      rooms.push({
        id: String(obj.name || props.id || "room_" + gx + "_" + gy).trim(),
        x: gx,
        y: gy,
        w: gw,
        h: gh,
      });
      return;
    }

    /* ── player_start / spawn_point ── */
    if (
      objType === "player_start" || objType === "spawn_point" ||
      objName === "player_start" || objName === "player" ||
      layerName === "player_start"
    ) {
      playerStartSetter({ x: gx, y: gy });
      return;
    }

    /* ── collision rects ── */
    if (
      objType === "collision" || objType === "wall" ||
      layerName === "collision" || layerName === "walls"
    ) {
      markRect(collision, gx, gy, gw, gh, mapW, mapH);
      return;
    }

    /* ── los_blockers rects ── */
    if (
      objType === "los_blocker" || objType === "los" ||
      layerName === "los_blockers" || layerName === "los"
    ) {
      markRect(losBlockers, gx, gy, gw, gh, mapW, mapH);
      return;
    }

    /* ── triggers ── */
    if (
      objType === "trigger" || objType === "transition_zone" ||
      objType === "narrative_trigger" ||
      layerName === "triggers" || layerName === "trigger"
    ) {
      const preferredTriggerId =
        props.trigger_id
        || props.triggerId
        || obj.name
        || props.id
        || "trigger_" + gx + "_" + gy;
      const trapIdentity = normalizeEntityId(preferredTriggerId, objType || props.type, props);
      triggers.push({
        id: trapIdentity.id || preferredTriggerId,
        x: gx,
        y: gy,
        w: gw,
        h: gh,
        type: objType || "trigger",
        data: {
          ...props,
          name: obj.name || "",
          alias_id: trapIdentity.alias_id || "",
          source_id: trapIdentity.source_id || "",
        },
      });
      return;
    }

    /* ── spawns ── */
    if (
      objType === "spawn" || objType === "npc" || objType === "enemy" ||
      layerName === "spawns" || layerName === "spawn"
    ) {
      const spawnId = obj.name || props.instance_id || props.id || "";
      const normalizedSpawn = normalizeEntityId(spawnId, objType || props.type, props);
      spawns.push({
        id: normalizedSpawn.id || spawnId,
        x: gx,
        y: gy,
        faction: props.faction || "neutral",
        prefab: props.prefab || "",
        alias_id: normalizedSpawn.alias_id || "",
        source_id: normalizedSpawn.source_id || "",
        data: {
          ...props,
          name: obj.name || "",
          type: obj.type || "",
        },
      });
      if (spawnId) {
        interactables.push({
          id: normalizedSpawn.id || spawnId,
          x: gx,
          y: gy,
          type: "npc",
          name: normalizedSpawn.id || spawnId,
          label:
            (window.ControlledAgentHazardMeta &&
              window.ControlledAgentHazardMeta.OBJECT_LABELS[normalizedSpawn.id || spawnId]) ||
            (normalizedSpawn.id || spawnId),
          alias_id: normalizedSpawn.alias_id || "",
          source_id: normalizedSpawn.source_id || "",
          data: {
            ...props,
            name: obj.name || "",
            type: obj.type || "",
          },
        });
      }
      return;
    }

    /* ── interactables (default for named objects in interactable layers) ── */
    if (
      objType === "interactable" || objType === "chest" || objType === "door" ||
      objType === "readable" || objType === "trap" || objType === "object" ||
      layerName === "interactables" || layerName === "objects" ||
      layerName === "environment_objects"
    ) {
      const rawId = obj.name || props.id || "";
      const normalizedIdentity = normalizeEntityId(rawId, objType || props.type, props);
      interactables.push({
        id: normalizedIdentity.id || rawId,
        x: gx,
        y: gy,
        w: gw,
        h: gh,
        type: objType || "object",
        name: obj.name || props.name || "",
        label:
          (window.ControlledAgentHazardMeta &&
            window.ControlledAgentHazardMeta.OBJECT_LABELS[normalizedIdentity.id || rawId]) ||
          obj.name || props.name || "",
        alias_id: normalizedIdentity.alias_id || "",
        source_id: normalizedIdentity.source_id || "",
        data: {
          ...props,
          name: obj.name || props.name || "",
          type: objType || "object",
          w: gw,
          h: gh,
        },
      });
      return;
    }

    /* ── Fallback: treat named objects in unknown layers as interactables ── */
    if (obj.name) {
      const fallbackIdentity = normalizeEntityId(obj.name, objType || "", props);
      interactables.push({
        id: fallbackIdentity.id || obj.name,
        x: gx,
        y: gy,
        w: gw,
        h: gh,
        type: objType || "object",
        name: obj.name,
        label: obj.name,
        alias_id: fallbackIdentity.alias_id || "",
        source_id: fallbackIdentity.source_id || "",
        data: {
          ...props,
          name: obj.name,
          type: objType || "object",
          w: gw,
          h: gh,
        },
      });
    }
  }

  function pointInRoom(x, y, room) {
    const r = safeObj(room);
    return (
      Number(x) >= Number(r.x)
      && Number(x) < Number(r.x) + Number(r.w || 1)
      && Number(y) >= Number(r.y)
      && Number(y) < Number(r.y) + Number(r.h || 1)
    );
  }

  function assignRoomMembership(records, rooms) {
    const roomList = safeArr(rooms);
    safeArr(records).forEach((item) => {
      const rec = safeObj(item);
      const data = safeObj(rec.data);
      if (data.room_id || data.roomId) {
        rec.room_id = String(data.room_id || data.roomId);
        return;
      }
      if (data.connects_from || data.connects_to) {
        rec.connects_from = String(data.connects_from || "");
        rec.connects_to = String(data.connects_to || "");
      }
      const x = Number(rec.x);
      const y = Number(rec.y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const room = roomList.find((candidate) => pointInRoom(x, y, candidate));
      if (room) {
        rec.room_id = String(room.id || "");
      }
    });
  }

  /** Flatten Tiled custom properties [{name, value, type}] → {key: value} */
  function flattenProperties(props) {
    const out = {};
    safeArr(props).forEach((p) => {
      const o = safeObj(p);
      if (o.name) out[o.name] = o.value !== undefined ? o.value : true;
    });
    return out;
  }

  /** Mark a rectangle in a 2D boolean grid */
  function markRect(grid, gx, gy, gw, gh, mapW, mapH) {
    for (let dy = 0; dy < gh; dy++) {
      for (let dx = 0; dx < gw; dx++) {
        const y = gy + dy, x = gx + dx;
        if (x >= 0 && x < mapW && y >= 0 && y < mapH) grid[y][x] = true;
      }
    }
  }

  async function loadMapById(mapId, options = {}) {
    const targetMapId = String(mapId || "hazard_lab").trim() || "hazard_lab";
    const assetPath = String(options.assetPath || "/web_ui/assets/maps/" + targetMapId + ".json");
    const fallback = normalizeTiledMap(null);
    if (typeof fetch !== "function") {
      return { map: fallback, source: "fixture", reason: "fetch_unavailable", mapId: targetMapId, assetPath };
    }
    try {
      const response = await fetch(assetPath, { cache: "no-store" });
      if (!response.ok) {
        return {
          map: fallback,
          source: "fixture",
          reason: "http_" + response.status,
          mapId: targetMapId,
          assetPath,
        };
      }
      const payload = await response.json();
      const normalized = normalizeTiledMap(payload);
      return { map: normalized, source: "json", mapId: targetMapId, assetPath };
    } catch (error) {
      return {
        map: fallback,
        source: "fixture",
        reason: normalizeKey(error && error.name) || "load_error",
        mapId: targetMapId,
        assetPath,
      };
    }
  }

  /* ── Public API ── */
  window.ControlledAgentTiledAdapter = Object.freeze({
    normalizeTiledMap,
    loadMapById,
    HAZARD_LAB_FIXTURE,
  });
})();
