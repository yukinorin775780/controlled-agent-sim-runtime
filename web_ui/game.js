(() => {
  const DEFAULT_MAP_DATA = {
    id: "",
    width: 10,
    height: 10,
    obstacles: [],
    grid: [],
    collision: [],
    los_blockers: [],
    ground_types: [],
    rooms: [],
    visible_rooms: [],
  };

  const FALLBACK_STATE = {
    partyStatus: {
      player: { name: "玩家", faction: "player", x: 4, y: 5 },
    },
    environmentObjects: {
      drone_1: { name: "训练无人机", faction: "hostile", status: "alive", x: 6, y: 5 },
    },
    mapData: DEFAULT_MAP_DATA,
  };

  const SPRITE_KEYS = Object.freeze({
    tiles: "dungeon_tiles",
    actors: "dungeon_characters",
    scout: "showcase_actor_scout",
    analyst: "showcase_actor_analyst",
    tactician: "showcase_actor_tactician",
    labDoorClosed: "showcase_lab_door_closed",
    labDoorOpen: "showcase_lab_door_open",
  });

  const SPRITE_SHEETS = Object.freeze({
    tiles: {
      path: "assets/2D Pixel Dungeon Asset Pack/character and tileset/Dungeon_Tileset.png",
      frameWidth: 16,
      frameHeight: 16,
    },
    actors: {
      path: "assets/2D Pixel Dungeon Asset Pack/character and tileset/Dungeon_Character.png",
      frameWidth: 16,
      frameHeight: 16,
    },
  });

  const SPRITE_IMAGES = Object.freeze({
  });

  const CUSTOM_SPRITE_SHEETS = Object.freeze({
  });

  const PARTY_TEXTURES = Object.freeze({
  });

  const PARTY_TEXTURE_SCALES = Object.freeze({
  });

  const DEPTH_LAYERS = Object.freeze({
    floor: 0,
    ambience: 0.35,
    environment: 1,
    overlay: 1.6,
    actors: 2,
    interactFx: 3,
  });

  const FOG_OF_WAR = Object.freeze({
    color: 0x020509,
    alpha: 0.965,
    cellBleed: 1.04,
  });

  const FOLLOW_COMPANIONS = Object.freeze(["scout", "analyst", "tactician"]);
  const FOLLOW_PROJECTION_SOURCES = Object.freeze(new Set([
    "visual_party_formation",
    "local_party_follow",
    "local_party_trail",
  ]));
  const FOLLOW_TRAIL_MAX = 12;
  const FOLLOW_SPACING_STEPS = 1;
  const DEFAULT_FOLLOW_DIRECTION = Object.freeze({ x: 0, y: -1 });

  const TILE_FRAMES = Object.freeze({
    floor: [11, 12, 13, 21, 22, 23, 31, 32, 33, 61, 62, 63, 71, 72, 73],
    wall: [0, 1, 2, 3, 4, 10, 20, 30, 40, 41, 42, 43, 44, 50, 51, 52, 53, 55],
    rubble: [49, 59, 64, 68],
    campfire: [90, 91, 92, 93],
    prop: [80, 81, 83],
    trap: [65, 77],
    doorClosed: 39,
    doorOpen: 57,
    chestClosed: 84,
    chestOpen: 85,
    loot: [86, 87, 89, 97, 98],
    poison: 97,
    locked: 88,
  });

  const WALL_FRAME = 1;
  const FLOOR_FRAME = 11;

  const ACTOR_FRAMES = Object.freeze({
    player: [1, 4, 15, 18],
    hostile: [11, 12, 13, 25, 26, 27],
    neutral: [2, 3, 16, 17],
    object: [9, 10, 23, 24],
    partyById: {
      player: 1,
      scout: 12,
      analyst: 16,
      tactician: 18,
      gatekeeper: 27,
    },
    hostileById: {
      drone_1: 11,
    },
  });

  const ACTOR_TINTS = Object.freeze({
    player: 0xf4e1b8,
    scout: 0xd29f93,
    analyst: 0xc9b9f8,
    tactician: 0x9fdcb2,
    gatekeeper: 0xa7d8bf,
  });

  const GROUND_TYPE_TINTS = Object.freeze({
    default: 0xffffff,
    toxic: 0x79d89f,
  });

  const REGION_THEMES = Object.freeze([
    { key: "entrance_hall", x: 0, y: 14, w: 12, h: 11, color: 0x6f4f2e, alpha: 0.1 },
    { key: "poison_corridor", x: 1, y: 8, w: 8, h: 6, color: 0x4f4a3f, alpha: 0.08 },
    { key: "study", x: 18, y: 8, w: 7, h: 6, color: 0x5a4c80, alpha: 0.1 },
    { key: "surgery_exit", x: 9, y: 0, w: 8, h: 6, color: 0x4e6a82, alpha: 0.12 },
  ]);

  const controller = {
    game: null,
    scene: null,
    latestState: FALLBACK_STATE,
    localPartyTrail: [],
    lastMoveDirection: { ...DEFAULT_FOLLOW_DIRECTION },
    update(partyStatus, environmentObjects, mapData) {
      const nextMapData = normalizeMapData(mapData);
      const previousMapId = normalizeId(this.latestState && this.latestState.mapData && this.latestState.mapData.id);
      const nextMapId = normalizeId(nextMapData.id);
      const mapChanged = Boolean(previousMapId && nextMapId && previousMapId !== nextMapId);

      this.latestState = {
        partyStatus: safeObject(partyStatus),
        environmentObjects: safeObject(environmentObjects),
        mapData: nextMapData,
      };
      if (mapChanged) {
        this.resetLocalPartyTrail();
      }
      if (this.scene) {
        this.scene.syncState(this.latestState, { mapChanged });
      }
    },
    playProjectile(start, target, color) {
      if (!this.scene) return;
      this.scene.playProjectileBetweenCells(start, target, color);
    },
    playAoE(center) {
      if (!this.scene) return;
      this.scene.playAoEAtCell(center);
    },
    playKnockback(entityId, target, options = {}) {
      if (!this.scene) return;
      const point = safeObject(target);
      this.scene.playKnockbackAnimation(entityId, point.x, point.y, options);
    },
    playStatusDamage(entityId, label) {
      if (!this.scene) return;
      this.scene.playFloatingTextOverToken(entityId, label || "中毒", {
        color: "#76ff8a",
        stroke: "#062d10",
        yOffset: -0.72,
      });
    },
    playAdvantage(entityId) {
      if (!this.scene) return;
      this.scene.playFloatingTextOverToken(entityId, "ADVANTAGE!", {
        color: "#ffd86b",
        stroke: "#3c2500",
        yOffset: -0.92,
      });
    },
    playTrapDiscoveryHighlight(trapIds) {
      if (!this.scene || typeof this.scene.playTrapDiscoveryHighlight !== "function") return;
      this.scene.playTrapDiscoveryHighlight(trapIds);
    },
    playTrapHazardPulse(trigger) {
      if (!this.scene || typeof this.scene.playTrapHazardPulse !== "function") return;
      this.scene.playTrapHazardPulse(trigger);
    },
    playVictoryBanner() {
      if (!this.scene) return;
      this.scene.playVictoryBanner();
    },
    playSpeechBubble(entityId, text) {
      if (!this.scene) return;
      this.scene.playSpeechBubble(entityId, text);
    },
    playMapTransition() {
      if (!this.scene) return;
      this.scene.playMapTransition();
    },
    playShortRest() {
      if (!this.scene) return;
      this.scene.playShortRestTransition();
    },
    playLongRest() {
      if (!this.scene) return;
      this.scene.playLongRestTransition();
    },
    /** Move player token locally (no backend call). Called by input-controller. */
    movePlayerLocal(gridX, gridY) {
      if (!this.scene) return;
      const token = this.scene.tokens.get("player");
      if (!token) return;
      const prevX = Math.round(Number(token.entity.x));
      const prevY = Math.round(Number(token.entity.y));
      token.entity.x = gridX;
      token.entity.y = gridY;
      this.scene.moveToken(token, gridX, gridY, true);
      this.scene.updateCameraFollow();
      /* Also update latestState so sync doesn't snap back */
      if (this.latestState && this.latestState.partyStatus && this.latestState.partyStatus.player) {
        this.latestState.partyStatus.player.x = gridX;
        this.latestState.partyStatus.player.y = gridY;
      }
      this.recordLocalPartyTrailPoint(prevX, prevY, {
        x: Math.round(Number(gridX)) - prevX,
        y: Math.round(Number(gridY)) - prevY,
      });
      this.moveCompanionsLocalFormation(gridX, gridY);
    },
    resetLocalPartyTrail() {
      this.localPartyTrail = [];
      this.lastMoveDirection = { ...DEFAULT_FOLLOW_DIRECTION };
    },
    recordLocalPartyTrailPoint(x, y, direction = {}) {
      const point = { x: Math.round(Number(x)), y: Math.round(Number(y)) };
      if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) return;
      const dx = Math.sign(Math.round(Number(direction.x) || 0));
      const dy = Math.sign(Math.round(Number(direction.y) || 0));
      if (dx !== 0 || dy !== 0) {
        this.lastMoveDirection = { x: dx, y: dy };
      }
      const first = safeObject(this.localPartyTrail[0]);
      if (Math.round(Number(first.x)) !== point.x || Math.round(Number(first.y)) !== point.y) {
        this.localPartyTrail = [point, ...safeArray(this.localPartyTrail)]
          .slice(0, FOLLOW_TRAIL_MAX);
      }
    },
    /** Move projected companion tokens locally without backend sync. */
    moveCompanionsLocalFormation(playerX, playerY) {
      if (!this.scene || !this.latestState || !this.latestState.partyStatus) return;
      const nextParty = resolveLocalPartyFollowFormation(
        this.latestState.partyStatus,
        (this.latestState && this.latestState.mapData) || (this.scene && this.scene.mapData),
        { x: playerX, y: playerY },
        {
          trail: this.localPartyTrail,
          lastMoveDirection: this.lastMoveDirection,
        },
      );
      this.latestState.partyStatus = nextParty;
      FOLLOW_COMPANIONS.forEach((companionId) => {
        const token = this.scene.tokens && this.scene.tokens.get(companionId);
        const record = safeObject(nextParty[companionId]);
        if (!token || !Number.isFinite(Number(record.x)) || !Number.isFinite(Number(record.y))) return;
        if (normalizeId(record._projection_source) !== "local_party_trail") return;
        const x = Math.round(Number(record.x));
        const y = Math.round(Number(record.y));
        token.entity.x = x;
        token.entity.y = y;
        token.entity.data = {
          ...safeObject(token.entity.data),
          ...record,
          x,
          y,
          _projection_source: "local_party_trail",
        };
        this.scene.moveToken(token, x, y, true);
      });
    },
    /** Get current player grid position */
    getPlayerGridPosition() {
      if (!this.scene) return { x: 0, y: 0 };
      const token = this.scene.tokens.get("player");
      if (!token) return { x: 0, y: 0 };
      return { x: token.entity.x, y: token.entity.y };
    },
    getCameraFollowTarget() {
      if (!this.scene || !this.scene.cameras || !this.scene.cameras.main) return null;
      const followed = this.scene.cameras.main._follow;
      if (!followed) return null;
      return {
        x: Number(followed.x),
        y: Number(followed.y),
      };
    },
    /** Draw red overlay on LoS-blocked tiles */
    drawLoSBlockerOverlay(blockedTiles) {
      if (!this.scene || typeof this.scene.drawLoSOverlay !== "function") return;
      this.scene.drawLoSOverlay(blockedTiles);
    },
    /** Clear LoS overlay */
    clearLoSBlockerOverlay() {
      if (!this.scene || typeof this.scene.clearLoSOverlay !== "function") return;
      this.scene.clearLoSOverlay();
    },
    setInteractionFocus(interactable) {
      if (!this.scene || typeof this.scene.setInteractionFocus !== "function") return;
      this.scene.setInteractionFocus(interactable);
    },
    setTrapSenseMode(enabled) {
      if (!this.scene || typeof this.scene.setTrapSenseMode !== "function") return;
      this.scene.setTrapSenseMode(enabled);
    },
    refreshMapOnly(mapData, environmentObjects) {
      const nextMapData = normalizeMapData(mapData);
      this.latestState = {
        ...safeObject(this.latestState),
        environmentObjects: safeObject(environmentObjects),
        mapData: nextMapData,
      };
      if (this.scene && typeof this.scene.refreshMapOnly === "function") {
        this.scene.refreshMapOnly(nextMapData, safeObject(environmentObjects));
      }
    },
    getLocalPartyTokenPositions() {
      const out = {};
      if (!this.scene || !this.scene.tokens) return out;
      FOLLOW_COMPANIONS.forEach((id) => {
        const token = this.scene.tokens.get(id);
        const entity = safeObject(token && token.entity);
        const data = safeObject(entity.data);
        const source = normalizeId(data._projection_source || entity._projection_source);
        if (!["local_party_trail", "local_party_follow", "visual_party_formation"].includes(source)) return;
        const x = Math.round(Number(entity.x ?? data.x));
        const y = Math.round(Number(entity.y ?? data.y));
        if (!Number.isFinite(x) || !Number.isFinite(y)) return;
        out[id] = {
          ...data,
          x,
          y,
          _projection_source: source,
        };
      });
      return out;
    },
    resolveTrapOverlayEntries(environmentObjects) {
      return resolveTrapOverlayEntries(environmentObjects);
    },
    shouldRenderAct2PoisonGas(environmentObjects) {
      return shouldRenderAct2PoisonGas(environmentObjects);
    },
    resolveFogOfWarCells(mapData) {
      return resolveFogOfWarCells(mapData);
    },
    resolveLocalPartyFollowFormation(partyStatus, mapData, playerPosition, options) {
      return resolveLocalPartyFollowFormation(partyStatus, mapData, playerPosition, options);
    },
    resolveLocalPartyTrailFormation(partyStatus, mapData, playerPosition, options) {
      return resolveLocalPartyFollowFormation(partyStatus, mapData, playerPosition, options);
    },
    resize() {
      if (!this.game) return;
      const size = gameViewportSize();
      this.game.scale.resize(size.width, size.height);
      if (this.scene) {
        this.scene.handleResize(size);
      }
    },
  };

  window.ControlledAgentTacticalMap = controller;

  function safeObject(value) {
    return value && typeof value === "object" ? value : {};
  }

  function safeArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function normalizeId(id) {
    return String(id || "").trim().toLowerCase();
  }

  function hashString(value) {
    const text = String(value || "");
    let hash = 0;
    for (let i = 0; i < text.length; i += 1) {
      hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
    }
    return Math.abs(hash);
  }

  function pickFrame(frames, seed) {
    if (!Array.isArray(frames) || frames.length === 0) return 0;
    return frames[hashString(seed) % frames.length];
  }

  function isDialogueOverlayActive() {
    if (window.ControlledAgentDialogueActive === true) return true;
    const overlay = document.getElementById("dialogue-overlay");
    return Boolean(overlay && !overlay.classList.contains("hidden"));
  }

  function prefersReducedMotion() {
    return !!(
      window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  function gameViewportSize() {
    const host = document.getElementById("game-viewport") || document.getElementById("map-container");
    return {
      width: Math.max(320, Math.round((host && host.clientWidth) || window.innerWidth * 0.65)),
      height: Math.max(320, Math.round((host && host.clientHeight) || window.innerHeight)),
    };
  }

  function normalizeMapData(rawMapData) {
    const outer = safeObject(rawMapData);
    const data = outer.map_data && typeof outer.map_data === "object"
      ? safeObject(outer.map_data)
      : outer;
    const id = String(data.id || data.map_id || data.key || data.name || "").trim();
    const parsedGrid = normalizeGridData(
      data.grid || data.map_grid || data.layout || data.tiles || data.rows,
    );
    const parsedCollision = normalizeCollisionData(
      data.collision || data.collision_grid || data.blocked_movement_grid || [],
    );
    const parsedLosBlockers = normalizeCollisionData(
      data.los_blockers || data.losBlockers || data.los || [],
    );
    const parsedGroundTypes = normalizeNumericGridData(
      data.ground_types || data.groundTypes || data.ground || data.terrain || [],
    );
    const parsedRooms = safeArray(data.rooms).map((room) => {
      const record = safeObject(room);
      return {
        id: String(record.id || "").trim(),
        x: Math.max(0, Math.round(Number(record.x) || 0)),
        y: Math.max(0, Math.round(Number(record.y) || 0)),
        w: Math.max(1, Math.round(Number(record.w) || 1)),
        h: Math.max(1, Math.round(Number(record.h) || 1)),
      };
    });
    const parsedVisibleRooms = safeArray(data.visible_rooms || data.visibleRooms)
      .map((roomId) => String(roomId || "").trim())
      .filter(Boolean);
    const gridHeight = parsedGrid.length;
    const gridWidth = parsedGrid.reduce((max, row) => Math.max(max, row.length), 0);
    const collisionHeight = parsedCollision.length;
    const collisionWidth = parsedCollision.reduce((max, row) => Math.max(max, row.length), 0);
    const losHeight = parsedLosBlockers.length;
    const losWidth = parsedLosBlockers.reduce((max, row) => Math.max(max, row.length), 0);
    const groundHeight = parsedGroundTypes.length;
    const groundWidth = parsedGroundTypes.reduce((max, row) => Math.max(max, row.length), 0);
    const width = Math.max(
      1,
      Math.round(Number(data.width) || 0),
      losWidth || 0,
      groundWidth || 0,
      collisionWidth || 0,
      gridWidth || DEFAULT_MAP_DATA.width,
    );
    const height = Math.max(
      1,
      Math.round(Number(data.height) || 0),
      losHeight || 0,
      groundHeight || 0,
      collisionHeight || 0,
      gridHeight || DEFAULT_MAP_DATA.height,
    );
    const obstacles = Array.isArray(data.obstacles) ? data.obstacles : [];
    const collision = normalizeCollisionShape(parsedCollision, width, height);
    const losBlockers = normalizeCollisionShape(parsedLosBlockers, width, height);
    const groundTypes = normalizeNumericGridShape(parsedGroundTypes, width, height);
    const grid = parsedGrid.length
      ? normalizeGridShape(parsedGrid, width, height)
      : gridFromCollision(collision, width, height);
    return {
      id,
      width,
      height,
      obstacles,
      grid,
      collision,
      los_blockers: losBlockers,
      ground_types: groundTypes,
      rooms: parsedRooms,
      visible_rooms: parsedVisibleRooms,
    };
  }

  function cellInRoom(x, y, room) {
    const r = safeObject(room);
    const rx = Math.max(0, Math.round(Number(r.x) || 0));
    const ry = Math.max(0, Math.round(Number(r.y) || 0));
    const rw = Math.max(1, Math.round(Number(r.w) || 1));
    const rh = Math.max(1, Math.round(Number(r.h) || 1));
    return x >= rx && x < rx + rw && y >= ry && y < ry + rh;
  }

  function cellKey(x, y) {
    return Math.round(Number(x)) + "," + Math.round(Number(y));
  }

  function isWithinMapBounds(coord, mapLike) {
    const map = safeObject(mapLike);
    const x = Math.round(Number(safeObject(coord).x));
    const y = Math.round(Number(safeObject(coord).y));
    return (
      Number.isFinite(x)
      && Number.isFinite(y)
      && x >= 0
      && y >= 0
      && x < Math.max(1, Math.round(Number(map.width) || 1))
      && y < Math.max(1, Math.round(Number(map.height) || 1))
    );
  }

  function visibleRoomIdsForFollow(mapLike) {
    const map = safeObject(mapLike);
    return new Set(
      safeArray(map.visible_rooms || map.visibleRooms)
        .map((roomId) => normalizeId(roomId))
        .filter(Boolean),
    );
  }

  function isCoordInsideVisibleRoomsForFollow(coord, mapLike) {
    const map = safeObject(mapLike);
    const rooms = safeArray(map.rooms);
    if (!rooms.length) return true;
    const x = Math.round(Number(safeObject(coord).x));
    const y = Math.round(Number(safeObject(coord).y));
    const room = rooms.find((candidate) => cellInRoom(x, y, candidate));
    if (!room) return false;
    const visible = visibleRoomIdsForFollow(map);
    if (!visible.size) return true;
    return visible.has(normalizeId(safeObject(room).id));
  }

  function isWalkableFollowCell(mapLike, coord, occupiedCells) {
    const map = normalizeMapData(mapLike);
    const point = {
      x: Math.round(Number(safeObject(coord).x)),
      y: Math.round(Number(safeObject(coord).y)),
    };
    if (!isWithinMapBounds(point, map)) return false;
    if (occupiedCells && occupiedCells.has(cellKey(point.x, point.y))) return false;
    const collision = safeArray(map.collision);
    if (Boolean(safeArray(collision[point.y])[point.x])) return false;
    const grid = safeArray(map.grid);
    const cell = String(safeArray(grid[point.y])[point.x] || ".").toUpperCase();
    if (cell === "W" || cell === "#") return false;
    return isCoordInsideVisibleRoomsForFollow(point, map);
  }

  function followSearchCandidates(desiredCell) {
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

  function findNearestWalkableFollowCell(mapLike, desiredCell, occupiedCells) {
    const map = normalizeMapData(mapLike);
    return followSearchCandidates(desiredCell).find((candidate) => (
      isWalkableFollowCell(map, candidate, occupiedCells)
    )) || null;
  }

  function isCompanionFollowControlled(companion) {
    const record = safeObject(companion);
    const source = normalizeId(record._projection_source);
    const hasCoords = Number.isFinite(Number(record.x)) && Number.isFinite(Number(record.y));
    return !hasCoords || FOLLOW_PROJECTION_SOURCES.has(source);
  }

  function normalizeTrailPoint(point) {
    const record = safeObject(point);
    const x = Math.round(Number(record.x));
    const y = Math.round(Number(record.y));
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
  }

  function normalizeFollowDirection(direction) {
    const record = safeObject(direction);
    const x = Math.sign(Math.round(Number(record.x) || 0));
    const y = Math.sign(Math.round(Number(record.y) || 0));
    if (x === 0 && y === 0) return { ...DEFAULT_FOLLOW_DIRECTION };
    return { x, y };
  }

  function fallbackLineDesiredCells(playerX, playerY, companionIndex, lastMoveDirection) {
    const direction = normalizeFollowDirection(lastMoveDirection);
    const distance = (companionIndex + 1) * FOLLOW_SPACING_STEPS;
    return [
      /* Behind the player based on the last local movement. */
      { x: playerX - direction.x * distance, y: playerY - direction.y * distance },
      /* Initial no-direction fallback: a single file below the player. */
      { x: playerX, y: playerY + distance },
      /* Side lanes only preserve a vertical line; they are not surround formation. */
      { x: playerX - 1, y: playerY + companionIndex },
      { x: playerX + 1, y: playerY + companionIndex },
    ];
  }

  function findTrailTargetForCompanion(map, trail, occupied, companionIndex) {
    const preferredIndex = companionIndex * FOLLOW_SPACING_STEPS;
    for (let i = preferredIndex; i < trail.length; i += 1) {
      const point = normalizeTrailPoint(trail[i]);
      if (point && isWalkableFollowCell(map, point, occupied)) return point;
    }
    return null;
  }

  function findFallbackLineTarget(map, playerX, playerY, occupied, companionIndex, lastMoveDirection) {
    const desiredCells = fallbackLineDesiredCells(playerX, playerY, companionIndex, lastMoveDirection);
    for (const desired of desiredCells) {
      if (isWalkableFollowCell(map, desired, occupied)) return desired;
    }
    return null;
  }

  function resolveLocalPartyFollowFormation(partyStatus, mapData, playerPosition, options = {}) {
    const party = safeObject(partyStatus);
    const map = normalizeMapData(mapData);
    const player = safeObject(playerPosition);
    const playerX = Math.round(Number(player.x));
    const playerY = Math.round(Number(player.y));
    if (!Number.isFinite(playerX) || !Number.isFinite(playerY)) return { ...party };

    const next = { ...party };
    const occupied = new Set([cellKey(playerX, playerY)]);
    const trail = safeArray(safeObject(options).trail)
      .map((point) => normalizeTrailPoint(point))
      .filter(Boolean)
      .slice(0, FOLLOW_TRAIL_MAX);
    const lastMoveDirection = normalizeFollowDirection(safeObject(options).lastMoveDirection);

    FOLLOW_COMPANIONS.forEach((companionId) => {
      const record = safeObject(party[companionId]);
      if (!Object.keys(record).length) return;
      if (!isCompanionFollowControlled(record)) {
        if (Number.isFinite(Number(record.x)) && Number.isFinite(Number(record.y))) {
          occupied.add(cellKey(record.x, record.y));
        }
      }
    });

    FOLLOW_COMPANIONS.forEach((companionId) => {
      const record = safeObject(party[companionId]);
      if (!Object.keys(record).length || !isCompanionFollowControlled(record)) return;
      const companionIndex = FOLLOW_COMPANIONS.indexOf(companionId);

      let target = findTrailTargetForCompanion(map, trail, occupied, companionIndex);
      if (!target) {
        target = findFallbackLineTarget(map, playerX, playerY, occupied, companionIndex, lastMoveDirection);
      }
      if (!target) {
        const currentX = Math.round(Number(record.x));
        const currentY = Math.round(Number(record.y));
        if (Number.isFinite(currentX) && Number.isFinite(currentY)) {
          occupied.add(cellKey(currentX, currentY));
        }
        return;
      }

      occupied.add(cellKey(target.x, target.y));
      next[companionId] = {
        ...record,
        x: target.x,
        y: target.y,
        _projection_source: "local_party_trail",
      };
    });

    return next;
  }

  function resolveFogOfWarCells(rawMapData) {
    const map = normalizeMapData(rawMapData);
    const rooms = safeArray(map.rooms);
    if (!rooms.length) return [];
    const visibleRoomIds = new Set(
      safeArray(map.visible_rooms).map((roomId) => normalizeId(roomId)).filter(Boolean),
    );
    const visibleRooms = rooms.filter((room) => visibleRoomIds.has(normalizeId(safeObject(room).id)));
    const hidden = [];
    for (let y = 0; y < map.height; y += 1) {
      for (let x = 0; x < map.width; x += 1) {
        const visible = visibleRooms.some((room) => cellInRoom(x, y, room));
        if (!visible) hidden.push({ x, y });
      }
    }
    return hidden;
  }

  function normalizeGridData(rawGrid) {
    if (Array.isArray(rawGrid)) {
      if (rawGrid.every((row) => typeof row === "string")) {
        return rawGrid.map((row) => row.split(""));
      }
      if (rawGrid.every((row) => Array.isArray(row))) {
        return rawGrid.map((row) => row.map((cell) => String(cell || "").charAt(0)));
      }
    }
    if (typeof rawGrid === "string") {
      const rows = rawGrid
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
      return rows.map((row) => row.split(""));
    }
    return [];
  }

  function normalizeGridShape(parsedGrid, width, height) {
    if (!Array.isArray(parsedGrid) || parsedGrid.length === 0) return [];
    const out = [];
    for (let y = 0; y < height; y += 1) {
      const row = Array.isArray(parsedGrid[y]) ? parsedGrid[y] : [];
      const normalized = [];
      for (let x = 0; x < width; x += 1) {
        const value = String(row[x] || "").toUpperCase();
        normalized.push(value === "W" ? "W" : ".");
      }
      out.push(normalized);
    }
    return out;
  }

  function normalizeCollisionData(rawCollision) {
    if (!Array.isArray(rawCollision)) return [];
    if (rawCollision.every((row) => Array.isArray(row))) {
      return rawCollision.map((row) => row.map((cell) => Boolean(cell)));
    }
    return [];
  }

  function normalizeNumericGridData(rawGrid) {
    if (!Array.isArray(rawGrid)) return [];
    if (rawGrid.every((row) => Array.isArray(row))) {
      return rawGrid.map((row) => row.map((cell) => Number(cell) || 0));
    }
    return [];
  }

  function normalizeCollisionShape(parsedCollision, width, height) {
    if (!Array.isArray(parsedCollision) || parsedCollision.length === 0) {
      return [];
    }
    const out = [];
    for (let y = 0; y < height; y += 1) {
      const row = Array.isArray(parsedCollision[y]) ? parsedCollision[y] : [];
      const normalized = [];
      for (let x = 0; x < width; x += 1) {
        normalized.push(Boolean(row[x]));
      }
      out.push(normalized);
    }
    return out;
  }

  function normalizeNumericGridShape(parsedGrid, width, height) {
    if (!Array.isArray(parsedGrid) || parsedGrid.length === 0) {
      return [];
    }
    const out = [];
    for (let y = 0; y < height; y += 1) {
      const row = Array.isArray(parsedGrid[y]) ? parsedGrid[y] : [];
      const normalized = [];
      for (let x = 0; x < width; x += 1) {
        normalized.push(Number(row[x]) || 0);
      }
      out.push(normalized);
    }
    return out;
  }

  function gridFromCollision(collision, width, height) {
    const out = [];
    for (let y = 0; y < height; y += 1) {
      const row = Array.isArray(collision[y]) ? collision[y] : [];
      const next = [];
      for (let x = 0; x < width; x += 1) {
        next.push(Boolean(row[x]) ? "W" : ".");
      }
      out.push(next);
    }
    return out;
  }

  function gridCoord(value, fallback, max) {
    const num = Number(value);
    if (!Number.isFinite(num)) return fallback;
    return Math.max(0, Math.min(max - 1, Math.round(num)));
  }

  function visualCoord(value, fallback, max) {
    const num = Number(value);
    if (!Number.isFinite(num)) return fallback;
    return Math.max(0, Math.min(max - 1, num));
  }

  function tokenKind(id, data, source) {
    const entity = safeObject(data);
    if (isTrap(id, entity)) return "trap";
    if (isLootDrop(id, entity)) return "loot";
    if (isDoor(id, entity)) return "door";
    if (isChest(id, entity)) return "chest";
    if (normalizeId(id) === "player") return "player";
    if (normalizeId(entity.faction) === "hostile") return "hostile";
    if (source === "environment") return "object";
    return "neutral";
  }

  function isTrap(id, data) {
    const entity = safeObject(data);
    const key = normalizeId(id);
    const type = normalizeId(entity.type || entity.kind || entity.object_type || entity.category);
    return type === "trap" || key.includes("trap");
  }

  function isPoisonValve(id, data) {
    const entity = safeObject(data);
    const key = normalizeId(id);
    const type = normalizeId(entity.type || entity.kind || entity.object_type || entity.category);
    return key === "poison_valve"
      || key === "potion_tank"
      || key.includes("poison_valve")
      || key.includes("potion_tank")
      || key.includes("poison_tank")
      || type === "poison_valve"
      || type === "potion_tank"
      || type === "poison_tank";
  }

  function isEntityHidden(data) {
    const entity = safeObject(data);
    const hidden = entity.is_hidden ?? entity.hidden;
    if (typeof hidden === "boolean") return hidden;
    if (typeof hidden === "string") {
      return ["true", "yes", "hidden", "concealed"].includes(normalizeId(hidden));
    }
    return false;
  }

  function trapStatus(data) {
    const entity = safeObject(data);
    const status = normalizeId(entity.status || entity.state);
    if (["disabled", "disarmed"].includes(status)) return "disabled";
    if (["triggered", "active", "burst"].includes(status)) return "triggered";
    if (["revealed", "detected", "visible"].includes(status)) return "revealed";
    if (entity.is_revealed === true || entity.discovered === true || entity.is_discovered === true) return "revealed";
    return "";
  }

  function poisonValveStatus(data) {
    const entity = safeObject(data);
    const status = normalizeId(entity.status || entity.state);
    if (["disabled", "safe", "sealed"].includes(status)) return "disabled";
    if (["triggered", "leaking", "active", "burst"].includes(status)) return "triggered";
    return "intact";
  }

  function shouldRenderAct2PoisonGas(environmentObjects) {
    const env = safeObject(environmentObjects);
    const trap = safeObject(env.gas_trap_1 || env.poison_trap_1 || env.poison_trap_2);
    return trapStatus(trap) === "triggered";
  }

  function resolveTrapOverlayEntries(environmentObjects) {
    const entries = [];
    Object.entries(safeObject(environmentObjects)).forEach(([id, raw]) => {
      const entity = safeObject(raw);
      if (!isTrap(id, entity)) return;
      const status = trapStatus(entity);
      if (!status || isEntityHidden(entity)) return;
      const x = Number(entity.x);
      const y = Number(entity.y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const w = Math.max(1, Math.round(Number(entity.w ?? entity.width ?? entity.size_w ?? entity.tile_width) || 1));
      const h = Math.max(1, Math.round(Number(entity.h ?? entity.height ?? entity.size_h ?? entity.tile_height) || 1));
      const color = status === "disabled" ? 0x7fae83 : (status === "triggered" ? 0xc54232 : 0xe0a84e);
      const fill = status === "disabled" ? 0x24382a : (status === "triggered" ? 0x5d1714 : 0x4a3518);
      entries.push({
        id: normalizeId(id),
        x,
        y,
        w,
        h,
        status,
        color,
        fill,
        alpha: status === "triggered" ? 0.28 : 0.18,
        label: status === "disabled" ? "DISARMED" : (status === "triggered" ? "POISON" : "TRAP"),
      });
    });
    Object.entries(safeObject(environmentObjects)).forEach(([id, raw]) => {
      const entity = safeObject(raw);
      if (!isPoisonValve(id, entity)) return;
      if (isEntityHidden(entity)) return;
      const status = poisonValveStatus(entity);
      const x = Number(entity.x);
      const y = Number(entity.y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const w = Math.max(1, Math.round(Number(entity.w ?? entity.width ?? entity.size_w ?? entity.tile_width) || 1));
      const h = Math.max(1, Math.round(Number(entity.h ?? entity.height ?? entity.size_h ?? entity.tile_height) || 1));
      entries.push({
        id: normalizeId(id),
        kind: "poison_valve",
        x,
        y,
        w,
        h,
        status,
        color: status === "disabled" ? 0x8fa194 : (status === "triggered" ? 0xc84a35 : 0x66b875),
        fill: status === "disabled" ? 0x28302a : (status === "triggered" ? 0x4a1712 : 0x183821),
        alpha: status === "triggered" ? 0.22 : 0.12,
        label: status === "disabled" ? "SAFE" : (status === "triggered" ? "LEAK" : "VALVE"),
      });
    });
    return entries;
  }

  function isDoor(id, data) {
    const entity = safeObject(data);
    const key = normalizeId(id);
    const type = normalizeId(entity.type || entity.kind || entity.object_type || entity.category);
    return key.includes("door")
      || type === "door"
      || type === "gate"
      || type === "locked_door"
      || type.endsWith("_door");
  }

  function isDoorOpen(data) {
    const entity = safeObject(data);
    const explicit = entity.is_open ?? entity.open ?? entity.opened;
    if (typeof explicit === "boolean") return explicit;
    if (typeof explicit === "string") {
      const value = normalizeId(explicit);
      if (["true", "yes", "open", "opened"].includes(value)) return true;
      if (["false", "no", "closed", "locked"].includes(value)) return false;
    }

    const status = normalizeId(entity.status);
    if (["open", "opened"].includes(status)) return true;
    if (["closed", "locked", "sealed"].includes(status)) return false;
    return false;
  }

  function isChest(id, data) {
    const entity = safeObject(data);
    const key = normalizeId(id);
    const type = normalizeId(entity.type || entity.kind || entity.object_type || entity.category);
    return key.includes("chest") || type === "chest" || type === "locked_chest";
  }

  function isLocked(data) {
    const entity = safeObject(data);
    const explicit = entity.is_locked ?? entity.locked;
    if (typeof explicit === "boolean") return explicit;
    if (typeof explicit === "string") {
      const value = normalizeId(explicit);
      if (["true", "yes", "locked"].includes(value)) return true;
      if (["false", "no", "unlocked", "open", "opened"].includes(value)) return false;
    }

    const status = normalizeId(entity.status);
    if (["unlocked", "open", "opened"].includes(status)) return false;
    if (["locked", "sealed"].includes(status)) return true;

    const type = normalizeId(entity.type || entity.kind || entity.object_type || entity.category);
    return type === "locked_chest";
  }

  function isLootDrop(id, data) {
    const key = normalizeId(id);
    const type = normalizeId(safeObject(data).type);
    return key.includes("loot_drop")
      || type === "loot_drop"
      || type === "loot"
      || type === "treasure"
      || type === "drop";
  }

  function normalizeStatusEffects(data) {
    const effects = safeObject(data).status_effects;
    if (Array.isArray(effects)) {
      return effects.map((effect) => {
        if (typeof effect === "string") return normalizeId(effect);
        const record = safeObject(effect);
        return normalizeId(record.id || record.type || record.name);
      });
    }
    if (effects && typeof effects === "object") {
      return Object.entries(effects)
        .filter(([, enabled]) => Boolean(enabled))
        .map(([effect]) => normalizeId(effect));
    }
    return [];
  }

  function collectEntities(partyStatus, environmentObjects, mapData) {
    const entities = [];
    const map = normalizeMapData(mapData);

    Object.entries(safeObject(partyStatus)).forEach(([id, data]) => {
      const entity = safeObject(data);
      if (entity.x === undefined || entity.y === undefined) return;
      entities.push({
        id,
        data: entity,
        source: "party",
        kind: tokenKind(id, entity, "party"),
        x: gridCoord(entity.x, 0, map.width),
        y: gridCoord(entity.y, 0, map.height),
      });
    });

    Object.entries(safeObject(environmentObjects)).forEach(([id, data]) => {
      const entity = safeObject(data);
      if (entity.x === undefined || entity.y === undefined) return;
      if (isTrap(id, entity) && isEntityHidden(entity)) return;
      const kind = tokenKind(id, entity, "environment");
      const width = Math.max(1, Number(entity.w ?? entity.width ?? entity.size_w ?? entity.tile_width) || 1);
      const height = Math.max(1, Number(entity.h ?? entity.height ?? entity.size_h ?? entity.tile_height) || 1);
      const x = kind === "door" ? visualCoord(Number(entity.x) + (width - 1) / 2, 0, map.width) : gridCoord(entity.x, 0, map.width);
      const y = kind === "door" ? visualCoord(Number(entity.y) + (height - 1) / 2, 0, map.height) : gridCoord(entity.y, 0, map.height);
      entities.push({
        id,
        data: entity,
        source: "environment",
        kind,
        x,
        y,
      });
    });

    if (entities.length > 0) return entities;
    return collectEntities(FALLBACK_STATE.partyStatus, FALLBACK_STATE.environmentObjects, map);
  }

  function obstacleCoordinates(obstacle, mapData) {
    const map = normalizeMapData(mapData);
    const coords = Array.isArray(obstacle.coordinates) ? obstacle.coordinates : [];
    return coords
      .filter((coord) => Array.isArray(coord) && coord.length >= 2)
      .map(([x, y]) => ({ x: Math.round(Number(x)), y: Math.round(Number(y)) }))
      .filter(({ x, y }) => {
        return Number.isFinite(x) && Number.isFinite(y) && x >= 0 && y >= 0 && x < map.width && y < map.height;
      });
  }

  if (!window.Phaser) {
    console.warn("Phaser 未加载，战术地图 Canvas 暂不可用。");
    return;
  }

  class MainScene extends Phaser.Scene {
    constructor() {
      super("MainScene");
      this.floorLayer = null;
      this.ambienceLayer = null;
      this.environmentLayer = null;
      this.overlayLayer = null;
      this.entityLayer = null;
      this.floorSprites = [];
      this.ambientSprites = [];
      this.toxicFogSprites = [];
      this.losSprites = [];
      this.roomFogSprites = [];
      this.obstacleSprites = [];
      this.trapOverlaySprites = [];
      this.obstacleFxTweens = [];
      this.overlayTweens = [];
      this.trapOverlayTweens = [];
      this.interactionRing = null;
      this.highlightedInteractableId = "";
      this.trapSenseMode = false;
      this.mapData = DEFAULT_MAP_DATA;
      this.board = { x: 0, y: 0, width: 640, height: 640, cell: 64 };
      this.tokens = new Map();
      this.highlightTween = null;
      this.externalLosOverlaySprites = [];
      this.transitionOverlay = null;
      this.lastTransitionAt = -Infinity;
    }

    preload() {
      this.load.spritesheet(SPRITE_KEYS.tiles, SPRITE_SHEETS.tiles.path, {
        frameWidth: SPRITE_SHEETS.tiles.frameWidth,
        frameHeight: SPRITE_SHEETS.tiles.frameHeight,
      });
      this.load.spritesheet(SPRITE_KEYS.actors, SPRITE_SHEETS.actors.path, {
        frameWidth: SPRITE_SHEETS.actors.frameWidth,
        frameHeight: SPRITE_SHEETS.actors.frameHeight,
      });
      Object.entries(SPRITE_IMAGES).forEach(([key, assetPath]) => {
        this.load.image(SPRITE_KEYS[key], assetPath);
      });
      Object.entries(CUSTOM_SPRITE_SHEETS).forEach(([key, sheet]) => {
        this.load.spritesheet(SPRITE_KEYS[key], sheet.path, {
          frameWidth: sheet.frameWidth,
          frameHeight: sheet.frameHeight,
        });
      });
    }

    create() {
      this.floorLayer = this.add.layer().setDepth(DEPTH_LAYERS.floor);
      this.ambienceLayer = this.add.layer().setDepth(DEPTH_LAYERS.ambience);
      this.environmentLayer = this.add.layer().setDepth(DEPTH_LAYERS.environment);
      this.overlayLayer = this.add.layer().setDepth(DEPTH_LAYERS.overlay);
      this.entityLayer = this.add.layer().setDepth(DEPTH_LAYERS.actors);
      this.interactionRing = this.add.graphics().setDepth(DEPTH_LAYERS.interactFx);
      this.interactionRing.setVisible(false);
      this.transitionOverlay = this.add.rectangle(0, 0, 1, 1, 0x000000, 0)
        .setOrigin(0, 0)
        .setDepth(500)
        .setVisible(false);
      this.input.on("pointerdown", (pointer) => {
        if (!isDialogueOverlayActive()) return;
        if (pointer && pointer.event && typeof pointer.event.stopPropagation === "function") {
          pointer.event.stopPropagation();
        }
      });
      this.scale.on("resize", this.handleResize, this);
      controller.scene = this;
      this.syncState(controller.latestState);
      this.cameras.main.setZoom(2.5);
      this.updateCameraFollow();
    }

    syncState(nextState, options = {}) {
      const state = safeObject(nextState);
      this.mapData = normalizeMapData(state.mapData);
      this.handleResize({ width: this.scale.width, height: this.scale.height });

      const entities = collectEntities(state.partyStatus, state.environmentObjects, this.mapData);
      const active = new Set();

      entities.forEach((entity) => {
        active.add(normalizeId(entity.id));
        this.upsertToken(entity);
      });

      this.tokens.forEach((token, id) => {
        if (!active.has(id)) {
          this.destroyToken(token);
          this.tokens.delete(id);
        }
      });
      this.refreshInteractionHighlight();
      this.drawTrapOverlays();
      this.updateCameraFollow();

      if (options.mapChanged) {
        this.playMapTransition();
      }
    }

    refreshMapOnly(mapData, environmentObjects) {
      this.mapData = normalizeMapData(mapData);
      controller.latestState = {
        ...safeObject(controller.latestState),
        environmentObjects: safeObject(environmentObjects),
        mapData: this.mapData,
      };
      this.handleResize({ width: this.scale.width, height: this.scale.height });
      this.refreshInteractionHighlight();
      this.drawTrapOverlays();
      this.updateCameraBounds();
      this.updateCameraFollow();
    }

    handleResize(gameSize) {
      const width = gameSize.width || this.scale.width;
      const height = gameSize.height || this.scale.height;
      const map = normalizeMapData(this.mapData);
      const cell = Math.min(width * 0.86 / map.width, height * 0.86 / map.height);
      const boardWidth = cell * map.width;
      const boardHeight = cell * map.height;

      this.board = {
        x: 0,
        y: 0,
        width: boardWidth,
        height: boardHeight,
        cell,
      };

      this.drawFloorTiles();
      this.drawAmbienceLayers();
      this.drawRoomVisibilityMask();
      this.drawObstacleTiles();
      this.drawLosBlockers();
      this.drawTrapOverlays();
      this.tokens.forEach((token) => {
        this.updateTokenScale(token);
        this.updateIdleTween(token, true);
      });
      this.positionAllTokens(false);
      this.refreshInteractionHighlight();
      if (this.transitionOverlay) {
        this.transitionOverlay
          .setPosition(0, 0)
          .setSize(width, height)
          .setDisplaySize(width, height);
      }
      this.updateCameraBounds();
    }

    destroySpriteList(items) {
      items.forEach((item) => item.destroy());
      items.length = 0;
    }

    scaleForTile(ratio = 1) {
      return (this.board.cell / SPRITE_SHEETS.tiles.frameWidth) * ratio;
    }

    drawFloorTiles() {
      const map = normalizeMapData(this.mapData);
      this.destroySpriteList(this.floorSprites);
      const grid = Array.isArray(map.grid) ? map.grid : [];
      const hasGrid = grid.length > 0;
      const collision = Array.isArray(map.collision) ? map.collision : [];
      const groundTypes = Array.isArray(map.ground_types) ? map.ground_types : [];
      const poisonGasActive = shouldRenderAct2PoisonGas(controller.latestState.environmentObjects);

      for (let y = 0; y < map.height; y += 1) {
        for (let x = 0; x < map.width; x += 1) {
          const cell = hasGrid ? String((grid[y] && grid[y][x]) || ".").toUpperCase() : ".";
          const blocked = Boolean(collision[y] && collision[y][x]);
          const isWall = cell === "W" || blocked;
          const groundType = Number(groundTypes[y] && groundTypes[y][x]) || 0;
          const isToxic = poisonGasActive && groundType >= 2 && !isWall;
          const frame = isWall
            ? pickFrame(TILE_FRAMES.wall, `wall:${x}:${y}`)
            : pickFrame(TILE_FRAMES.floor, `floor:${x}:${y}`);
          const world = this.gridToWorld(x, y);
          const tile = this.add.image(world.x, world.y, SPRITE_KEYS.tiles, frame)
            .setScale(this.scaleForTile(1.02))
            .setDepth(isWall ? DEPTH_LAYERS.environment : DEPTH_LAYERS.floor);
          if (isWall) {
            tile.setTint(0xc9b8a0);
          } else if (isToxic) {
            tile.setTint(GROUND_TYPE_TINTS.toxic);
            tile.setAlpha(0.95);
          } else {
            tile.setTint(GROUND_TYPE_TINTS.default);
          }
          if (isWall) {
            this.environmentLayer.add(tile);
          } else {
            this.floorLayer.add(tile);
          }
          this.floorSprites.push(tile);
        }
      }
    }

    drawAmbienceLayers() {
      this.destroySpriteList(this.ambientSprites);
      this.destroySpriteList(this.toxicFogSprites);
      this.destroySpriteList(this.losSprites);
      this.overlayTweens.forEach((tween) => tween.stop());
      this.overlayTweens = [];

      const map = normalizeMapData(this.mapData);
      const poisonGasActive = shouldRenderAct2PoisonGas(controller.latestState.environmentObjects);
      REGION_THEMES.forEach((region) => {
        if (region.x >= map.width || region.y >= map.height) return;
        const width = Math.min(region.w, map.width - region.x) * this.board.cell;
        const height = Math.min(region.h, map.height - region.y) * this.board.cell;
        const center = this.gridToWorld(
          region.x + Math.min(region.w, map.width - region.x) / 2 - 0.5,
          region.y + Math.min(region.h, map.height - region.y) / 2 - 0.5,
        );
        const overlay = this.add.rectangle(center.x, center.y, width, height, region.color, region.alpha)
          .setDepth(DEPTH_LAYERS.ambience);
        this.ambienceLayer.add(overlay);
        this.ambientSprites.push(overlay);
      });

      const ground = Array.isArray(map.ground_types) ? map.ground_types : [];
      const collision = Array.isArray(map.collision) ? map.collision : [];
      for (let y = 0; y < map.height; y += 1) {
        for (let x = 0; x < map.width; x += 1) {
          const groundType = Number(ground[y] && ground[y][x]) || 0;
          if (!poisonGasActive || groundType < 2 || Boolean(collision[y] && collision[y][x])) continue;
          const world = this.gridToWorld(x, y);
          const fog = this.add.ellipse(world.x, world.y, this.board.cell * 0.72, this.board.cell * 0.52, 0x65cf87, 0.18)
            .setDepth(DEPTH_LAYERS.ambience);
          this.ambienceLayer.add(fog);
          this.toxicFogSprites.push(fog);
          this.overlayTweens.push(
            this.tweens.add({
              targets: fog,
              alpha: 0.34,
              scaleX: 1.1,
              scaleY: 1.1,
              duration: 1200 + ((x + y) % 4) * 140,
              yoyo: true,
              repeat: -1,
              ease: "Sine.easeInOut",
            }),
          );
        }
      }

      const vignette = this.add.rectangle(
        this.board.width * 0.5,
        this.board.height * 0.5,
        this.board.width,
        this.board.height,
        0x000000,
        0.12,
      ).setDepth(DEPTH_LAYERS.ambience);
      this.ambienceLayer.add(vignette);
      this.ambientSprites.push(vignette);

      const exitGlow = this.add.ellipse(
        this.board.width * 0.5,
        this.board.cell * 1.35,
        this.board.cell * 6.4,
        this.board.cell * 2.4,
        0x85a5be,
        0.24,
      ).setDepth(DEPTH_LAYERS.overlay);
      this.overlayLayer.add(exitGlow);
      this.ambientSprites.push(exitGlow);
      this.overlayTweens.push(
        this.tweens.add({
          targets: exitGlow,
          alpha: 0.42,
          duration: 1800,
          yoyo: true,
          repeat: -1,
          ease: "Sine.easeInOut",
        }),
      );

      if (this.trapSenseMode) {
        this.drawTrapSenseHints();
      }
    }

    drawRoomVisibilityMask() {
      this.destroySpriteList(this.roomFogSprites);
      const map = normalizeMapData(this.mapData);
      const rooms = Array.isArray(map.rooms) ? map.rooms : [];
      if (!rooms.length) return;
      const hiddenCells = resolveFogOfWarCells(map);
      if (!hiddenCells.length) return;
      const fog = this.add.graphics().setDepth(DEPTH_LAYERS.overlay + 0.02);
      fog.fillStyle(FOG_OF_WAR.color, FOG_OF_WAR.alpha);
      const size = this.board.cell * FOG_OF_WAR.cellBleed;
      const offset = size * 0.5;
      hiddenCells.forEach((cell) => {
        const world = this.gridToWorld(cell.x, cell.y);
        fog.fillRect(world.x - offset, world.y - offset, size, size);
      });
      this.overlayLayer.add(fog);
      this.roomFogSprites.push(fog);
    }

    drawTrapSenseHints() {
      const map = normalizeMapData(this.mapData);
      const ground = Array.isArray(map.ground_types) ? map.ground_types : [];
      for (let y = 0; y < map.height; y += 1) {
        for (let x = 0; x < map.width; x += 1) {
          const groundType = Number(ground[y] && ground[y][x]) || 0;
          if (groundType < 2) continue;
          const world = this.gridToWorld(x, y);
          const hint = this.add.text(world.x, world.y - this.board.cell * 0.16, "!", {
            fontFamily: "Georgia, serif",
            fontSize: Math.max(10, this.board.cell * 0.24) + "px",
            fontStyle: "bold",
            color: "#9ce2b0",
            stroke: "#0c1810",
            strokeThickness: 2,
          }).setOrigin(0.5).setDepth(DEPTH_LAYERS.overlay);
          hint.setAlpha(0.24);
          this.overlayLayer.add(hint);
          this.ambientSprites.push(hint);
          this.overlayTweens.push(
            this.tweens.add({
              targets: hint,
              alpha: 0.58,
              duration: 900 + ((x * 13 + y * 7) % 5) * 110,
              yoyo: true,
              repeat: -1,
              ease: "Sine.easeInOut",
            }),
          );
        }
      }
    }

    drawTrapOverlays() {
      this.destroySpriteList(this.trapOverlaySprites);
      this.trapOverlayTweens.forEach((tween) => tween.stop());
      this.trapOverlayTweens = [];

      const entries = resolveTrapOverlayEntries(safeObject(controller.latestState.environmentObjects));
      const reducedMotion = prefersReducedMotion();
      entries.forEach((entry) => {
        const center = this.gridToWorld(entry.x + entry.w / 2 - 0.5, entry.y + entry.h / 2 - 0.5);
        const isValve = entry.kind === "poison_valve";
        const width = entry.w * this.board.cell * (isValve ? 0.58 : 0.94);
        const height = entry.h * this.board.cell * (isValve ? 0.58 : 0.94);
        const fill = this.add.rectangle(center.x, center.y, width, height, entry.fill, entry.alpha)
          .setDepth(DEPTH_LAYERS.overlay + 0.24);
        const rim = this.add.rectangle(center.x, center.y, width, height)
          .setFillStyle(0x000000, 0)
          .setStrokeStyle(Math.max(2, this.board.cell * 0.045), entry.color, 0.92)
          .setDepth(DEPTH_LAYERS.overlay + 0.25);
        const label = this.add.text(center.x, center.y - height * 0.5 - Math.max(8, this.board.cell * 0.12), entry.label, {
          fontFamily: "SFMono-Regular, Cascadia Code, Fira Code, monospace",
          fontSize: Math.max(9, this.board.cell * 0.16) + "px",
          fontStyle: "bold",
          color: entry.status === "triggered" ? "#ffd7d0" : (entry.status === "disabled" ? "#d6f5cf" : "#ffe0a3"),
          stroke: "#120c05",
          strokeThickness: 3,
        }).setOrigin(0.5).setDepth(DEPTH_LAYERS.overlay + 0.26);

        this.overlayLayer.add(fill);
        this.overlayLayer.add(rim);
        this.overlayLayer.add(label);
        this.trapOverlaySprites.push(fill, rim, label);

        if (!reducedMotion && entry.status !== "disabled") {
          this.trapOverlayTweens.push(this.tweens.add({
            targets: [fill, rim],
            alpha: entry.status === "triggered" ? 0.62 : 0.42,
            duration: entry.status === "triggered" ? 420 : 900,
            yoyo: true,
            repeat: -1,
            ease: "Sine.easeInOut",
          }));
        }
      });
    }

    playTrapDiscoveryHighlight(trapIds) {
      const map = normalizeMapData(this.mapData);
      const ids = new Set((Array.isArray(trapIds) ? trapIds : []).map((id) => normalizeId(id)));
      const entities = safeObject(controller.latestState.environmentObjects);
      const points = [];
      Object.entries(entities).forEach(([id, raw]) => {
        const entity = safeObject(raw);
        const key = normalizeId(id || entity.id);
        const isTrap = normalizeId(entity.type || entity.kind).includes("trap") || key.includes("trap");
        if (!isTrap) return;
        if (ids.size && !ids.has(key) && !ids.has(normalizeId(entity.alias_id))) return;
        const x = Number(entity.x);
        const y = Number(entity.y);
        if (Number.isFinite(x) && Number.isFinite(y)) points.push({ x, y });
      });

      if (!points.length) {
        const ground = Array.isArray(map.ground_types) ? map.ground_types : [];
        for (let y = 0; y < map.height; y += 1) {
          for (let x = 0; x < map.width; x += 1) {
            const groundType = Number(ground[y] && ground[y][x]) || 0;
            if (groundType >= 2) points.push({ x, y });
          }
        }
      }

      points.forEach((point) => {
        const world = this.gridToWorld(point.x, point.y);
        const mark = this.add.rectangle(
          world.x,
          world.y,
          this.board.cell * 0.92,
          this.board.cell * 0.92,
          0x71cf83,
          0.18,
        ).setDepth(DEPTH_LAYERS.overlay + 0.28);
        this.overlayLayer.add(mark);
        this.overlayTweens.push(this.tweens.add({
          targets: mark,
          alpha: 0.58,
          duration: 360,
          yoyo: true,
          repeat: 1,
          ease: "Sine.easeInOut",
          onComplete: () => mark.destroy(),
        }));
      });
    }

    playTrapHazardPulse(trigger) {
      const t = safeObject(trigger);
      const x0 = Number(t.x);
      const y0 = Number(t.y);
      const w = Math.max(1, Number(t.w) || 1);
      const h = Math.max(1, Number(t.h) || 1);
      const points = [];
      for (let yy = 0; yy < h; yy += 1) {
        for (let xx = 0; xx < w; xx += 1) {
          points.push({ x: x0 + xx, y: y0 + yy });
        }
      }
      points.forEach((point) => {
        const world = this.gridToWorld(point.x, point.y);
        const mark = this.add.rectangle(
          world.x,
          world.y,
          this.board.cell * 0.96,
          this.board.cell * 0.96,
          0xaa2a2a,
          0.24,
        ).setDepth(DEPTH_LAYERS.overlay + 0.34);
        this.overlayLayer.add(mark);
        this.overlayTweens.push(this.tweens.add({
          targets: mark,
          alpha: 0.72,
          duration: 140,
          yoyo: true,
          repeat: 3,
          ease: "Sine.easeInOut",
          onComplete: () => mark.destroy(),
        }));
      });
    }

    drawLosBlockers() {
      const map = normalizeMapData(this.mapData);
      const blockers = Array.isArray(map.los_blockers) ? map.los_blockers : [];
      for (let y = 0; y < map.height; y += 1) {
        for (let x = 0; x < map.width; x += 1) {
          if (!Boolean(blockers[y] && blockers[y][x])) continue;
          const world = this.gridToWorld(x, y);
          const shadow = this.add.rectangle(
            world.x,
            world.y - this.board.cell * 0.18,
            this.board.cell * 0.9,
            this.board.cell * 0.34,
            0x0f1114,
            0.48,
          ).setDepth(DEPTH_LAYERS.environment + 0.05);
          const edge = this.add.rectangle(
            world.x,
            world.y - this.board.cell * 0.32,
            this.board.cell * 0.76,
            this.board.cell * 0.08,
            0x8da5b7,
            0.34,
          ).setDepth(DEPTH_LAYERS.environment + 0.06);
          this.environmentLayer.add(shadow);
          this.environmentLayer.add(edge);
          this.losSprites.push(shadow, edge);
        }
      }
    }

    setTrapSenseMode(enabled) {
      const nextValue = Boolean(enabled);
      if (this.trapSenseMode === nextValue) return;
      this.trapSenseMode = nextValue;
      this.drawAmbienceLayers();
      this.refreshInteractionHighlight();
    }

    setInteractionFocus(interactable) {
      const target = safeObject(interactable);
      this.highlightedInteractableId = normalizeId(target.id || "");
      this.refreshInteractionHighlight();
    }

    resolveFocusWorldPoint(id) {
      const key = normalizeId(id);
      if (!key) return null;
      const token = this.tokens.get(key);
      if (token && token.container) {
        return { x: token.container.x, y: token.container.y };
      }
      const entity = safeObject(
        safeObject(controller.latestState.environmentObjects)[key]
        || safeObject(controller.latestState.partyStatus)[key],
      );
      const x = Number(entity.x);
      const y = Number(entity.y);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        return this.gridToWorld(
          gridCoord(x, 0, this.mapData.width || 1),
          gridCoord(y, 0, this.mapData.height || 1),
        );
      }
      return null;
    }

    refreshInteractionHighlight() {
      if (!this.interactionRing) return;
      if (this.highlightTween) {
        this.highlightTween.stop();
        this.highlightTween = null;
      }
      this.interactionRing.clear();
      const point = this.resolveFocusWorldPoint(this.highlightedInteractableId);
      if (!point) {
        this.interactionRing.setVisible(false);
        return;
      }
      const radius = this.board.cell * 0.46;
      this.interactionRing
        .lineStyle(3, 0xf0ca7b, 0.88)
        .strokeCircle(point.x, point.y, radius)
        .lineStyle(1, 0x61c7bc, 0.6)
        .strokeCircle(point.x, point.y, radius * 0.72)
        .setVisible(true)
        .setAlpha(0.78);
      this.highlightTween = this.tweens.add({
        targets: this.interactionRing,
        alpha: 0.28,
        duration: 720,
        yoyo: true,
        repeat: -1,
        ease: "Sine.easeInOut",
      });
    }

    drawLoSOverlay(blockedTiles) {
      this.clearLoSOverlay();
      const tiles = Array.isArray(blockedTiles) ? blockedTiles : [];
      tiles.forEach((cell) => {
        const point = safeObject(cell);
        const x = Number(point.x);
        const y = Number(point.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return;
        const world = this.gridToWorld(gridCoord(x, 0, this.mapData.width || 1), gridCoord(y, 0, this.mapData.height || 1));
        const mark = this.add.rectangle(world.x, world.y, this.board.cell * 0.88, this.board.cell * 0.88, 0xa11616, 0.32)
          .setDepth(DEPTH_LAYERS.overlay + 0.2);
        this.overlayLayer.add(mark);
        this.externalLosOverlaySprites.push(mark);
      });
    }

    clearLoSOverlay() {
      this.externalLosOverlaySprites.forEach((sprite) => sprite.destroy());
      this.externalLosOverlaySprites = [];
    }

    updateCameraBounds() {
      const map = normalizeMapData(this.mapData);
      const mapTotalWidth = map.width * this.board.cell;
      const mapTotalHeight = map.height * this.board.cell;
      this.cameras.main.setBounds(0, 0, mapTotalWidth, mapTotalHeight);
    }

    updateCameraFollow() {
      const player = this.tokens.get("player")?.container;
      if (!player) return;
      this.cameras.main.startFollow(player, true, 0.16, 0.16);
      this.updateCameraBounds();
    }

    obstacleFrame(obstacle, x, y) {
      const entry = safeObject(obstacle);
      const kind = normalizeId(entry.type);
      if (kind.includes("campfire") || kind.includes("torch")) {
        return pickFrame(TILE_FRAMES.campfire, `${kind}:${x}:${y}`);
      }
      if (kind.includes("trap") || kind.includes("spike")) {
        return pickFrame(TILE_FRAMES.trap, `${kind}:${x}:${y}`);
      }
      if (entry.blocks_movement === true && entry.blocks_los === true) {
        return pickFrame(TILE_FRAMES.wall, `hard:${kind}:${x}:${y}`);
      }
      if (entry.blocks_movement === true) {
        return pickFrame(TILE_FRAMES.prop, `soft:${kind}:${x}:${y}`);
      }
      return pickFrame(TILE_FRAMES.rubble, `rubble:${kind}:${x}:${y}`);
    }

    drawObstacleTiles() {
      this.destroySpriteList(this.obstacleSprites);
      this.obstacleFxTweens.forEach((tween) => tween.stop());
      this.obstacleFxTweens = [];

      const obstacles = Array.isArray(this.mapData.obstacles) ? this.mapData.obstacles : [];
      obstacles.forEach((rawObstacle) => {
        const obstacle = safeObject(rawObstacle);
        const kind = normalizeId(obstacle.type);

        obstacleCoordinates(obstacle, this.mapData).forEach(({ x, y }) => {
          const world = this.gridToWorld(x, y);
          const sprite = this.add.image(world.x, world.y, SPRITE_KEYS.tiles, this.obstacleFrame(obstacle, x, y))
            .setScale(this.scaleForTile(0.95))
            .setDepth(DEPTH_LAYERS.environment);
          this.environmentLayer.add(sprite);
          this.obstacleSprites.push(sprite);

          if (kind.includes("campfire") || kind.includes("torch")) {
            const flicker = this.tweens.add({
              targets: sprite,
              alpha: 0.66,
              duration: 150,
              yoyo: true,
              repeat: -1,
              ease: "Sine.easeInOut",
            });
            this.obstacleFxTweens.push(flicker);
          }
        });
      });
    }

    playProjectileAnimation(startX, startY, targetX, targetY, color = 0x00ffff) {
      const radius = Math.max(5, this.board.cell * 0.09);
      const projectile = this.add.graphics().setDepth(180);

      projectile.fillStyle(color, 0.95);
      projectile.fillCircle(0, 0, radius);
      projectile.lineStyle(2, 0xffffff, 0.72);
      projectile.strokeCircle(0, 0, radius * 1.35);
      projectile.setPosition(startX, startY);

      this.tweens.add({
        targets: projectile,
        x: targetX,
        y: targetY,
        duration: 300,
        ease: "Quad.easeOut",
        onComplete: () => projectile.destroy(),
      });
    }

    playAoEAnimation(centerX, centerY) {
      const size = this.board.cell * 3;
      // 两次闪烁，总时长约 400ms：4 个半程 * 100ms（上升/回落 + repeat）
      const flashHalfStepMs = 100;
      const blast = this.add.graphics().setDepth(150);

      blast.fillStyle(0xff0000, 0.4);
      blast.fillRect(-size / 2, -size / 2, size, size);
      blast.lineStyle(2, 0xffb3a7, 0.78);
      blast.strokeRect(-size / 2, -size / 2, size, size);

      for (let i = -1; i <= 1; i += 1) {
        const offset = i * this.board.cell;
        blast.lineStyle(1, 0xffd2cc, 0.38);
        blast.lineBetween(offset, -size / 2, offset, size / 2);
        blast.lineBetween(-size / 2, offset, size / 2, offset);
      }

      blast.setPosition(centerX, centerY);
      blast.setAlpha(0.4);
      this.cameras.main.shake(120, 0.004);

      this.tweens.add({
        targets: blast,
        alpha: 0.8,
        duration: flashHalfStepMs,
        ease: "Sine.easeInOut",
        yoyo: true,
        repeat: 1,
        onComplete: () => blast.destroy(),
      });
    }

    playProjectileBetweenCells(start, target, color) {
      const map = normalizeMapData(this.mapData);
      const startCell = safeObject(start);
      const targetCell = safeObject(target);
      const startWorld = this.gridToWorld(
        gridCoord(startCell.x, 0, map.width),
        gridCoord(startCell.y, 0, map.height),
      );
      const targetWorld = this.gridToWorld(
        gridCoord(targetCell.x, 0, map.width),
        gridCoord(targetCell.y, 0, map.height),
      );
      this.playProjectileAnimation(startWorld.x, startWorld.y, targetWorld.x, targetWorld.y, color || 0x00ffff);
    }

    playAoEAtCell(center) {
      const map = normalizeMapData(this.mapData);
      const centerCell = safeObject(center);
      const centerWorld = this.gridToWorld(
        gridCoord(centerCell.x, 0, map.width),
        gridCoord(centerCell.y, 0, map.height),
      );
      this.playAoEAnimation(centerWorld.x, centerWorld.y);
    }

    playKnockbackAnimation(entityId, targetX, targetY, options = {}) {
      const id = normalizeId(entityId);
      const token = this.tokens.get(id);
      if (!token) return;

      const map = normalizeMapData(this.mapData);
      const gridX = gridCoord(targetX, token.entity.x, map.width);
      const gridY = gridCoord(targetY, token.entity.y, map.height);
      const target = this.gridToWorld(gridX, gridY);
      const originalDepth = token.container.depth;

      token.entity.x = gridX;
      token.entity.y = gridY;
      if (token.moveTween) {
        token.moveTween.stop();
        token.moveTween = null;
      }
      token.container.setDepth(Math.max(originalDepth, 175));

      token.moveTween = this.tweens.add({
        targets: token.container,
        x: target.x,
        y: target.y,
        duration: 200,
        ease: "Back.easeOut",
        onComplete: () => {
          token.moveTween = null;
          token.container.setDepth(originalDepth);
          if (options.terrainDamage) {
            this.playTerrainDamageFeedback(target.x, target.y, options.label || "火焰伤害");
          }
        },
      });
    }

    playTerrainDamageFeedback(x, y, label) {
      const burst = this.add.graphics().setDepth(205);
      burst.fillStyle(0xff7a1a, 0.34);
      burst.fillCircle(0, 0, this.board.cell * 0.42);
      burst.lineStyle(2, 0xffcf70, 0.78);
      burst.strokeCircle(0, 0, this.board.cell * 0.5);
      burst.setPosition(x, y);

      const text = this.add.text(x, y - this.board.cell * 0.28, label, {
        fontFamily: "Georgia, serif",
        fontSize: Math.max(14, this.board.cell * 0.24) + "px",
        fontStyle: "bold",
        color: "#ff9a2e",
        stroke: "#2a0800",
        strokeThickness: 4,
      }).setOrigin(0.5).setDepth(220);

      this.cameras.main.shake(100, 0.01);

      this.tweens.add({
        targets: burst,
        alpha: 0,
        scaleX: 1.45,
        scaleY: 1.45,
        duration: 220,
        ease: "Quad.easeOut",
        onComplete: () => burst.destroy(),
      });
      this.tweens.add({
        targets: text,
        y: text.y - this.board.cell * 0.55,
        alpha: 0,
        duration: 650,
        ease: "Cubic.easeOut",
        onComplete: () => text.destroy(),
      });
    }

    playFloatingTextOverToken(entityId, label, options = {}) {
      const token = this.tokens.get(normalizeId(entityId));
      if (!token) return;

      const yOffset = Number.isFinite(Number(options.yOffset)) ? Number(options.yOffset) : -0.78;
      const text = this.add.text(
        token.container.x,
        token.container.y + this.board.cell * yOffset,
        String(label || ""),
        {
          fontFamily: "Georgia, serif",
          fontSize: Math.max(14, this.board.cell * 0.22) + "px",
          fontStyle: "bold",
          color: options.color || "#ffffff",
          stroke: options.stroke || "#000000",
          strokeThickness: 4,
        },
      ).setOrigin(0.5).setDepth(240);

      this.tweens.add({
        targets: text,
        y: text.y - this.board.cell * 0.62,
        alpha: 0,
        duration: 600,
        ease: "Cubic.easeOut",
        onComplete: () => text.destroy(),
      });
    }

    playVictoryBanner() {
      const cx = this.scale.width / 2;
      const cy = this.scale.height * 0.28;
      const panel = this.add.graphics().setDepth(300);
      panel.fillStyle(0x080604, 0.72);
      panel.fillRoundedRect(-260, -46, 520, 92, 16);
      panel.lineStyle(2, 0xffd86b, 0.82);
      panel.strokeRoundedRect(-260, -46, 520, 92, 16);
      panel.setPosition(cx, cy);
      panel.setAlpha(0);

      const title = this.add.text(cx, cy - 10, "VICTORY", {
        fontFamily: "Georgia, serif",
        fontSize: "46px",
        fontStyle: "bold",
        color: "#ffd86b",
        stroke: "#2a1600",
        strokeThickness: 6,
      }).setOrigin(0.5).setDepth(301).setAlpha(0);

      const subtitle = this.add.text(cx, cy + 28, "战斗结束", {
        fontFamily: "Georgia, serif",
        fontSize: "20px",
        fontStyle: "bold",
        color: "#f4e0a2",
        stroke: "#100804",
        strokeThickness: 4,
      }).setOrigin(0.5).setDepth(301).setAlpha(0);

      this.tweens.add({
        targets: [panel, title, subtitle],
        alpha: 1,
        duration: 180,
        ease: "Quad.easeOut",
      });
      this.tweens.add({
        targets: [panel, title, subtitle],
        alpha: 0,
        delay: 1600,
        duration: 400,
        ease: "Quad.easeIn",
        onComplete: () => {
          panel.destroy();
          title.destroy();
          subtitle.destroy();
        },
      });
    }

    playMapTransition() {
      if (!this.transitionOverlay) return;
      const now = this.time ? this.time.now : Date.now();
      if (now - this.lastTransitionAt < 360) return;
      this.lastTransitionAt = now;

      this.tweens.killTweensOf(this.transitionOverlay);
      this.transitionOverlay
        .setVisible(true)
        .setAlpha(0)
        .setPosition(0, 0)
        .setSize(this.scale.width, this.scale.height)
        .setDisplaySize(this.scale.width, this.scale.height);

      this.tweens.add({
        targets: this.transitionOverlay,
        alpha: 1,
        duration: 250,
        ease: "Quad.easeIn",
        onComplete: () => {
          this.tweens.add({
            targets: this.transitionOverlay,
            alpha: 0,
            delay: 80,
            duration: 250,
            ease: "Quad.easeOut",
            onComplete: () => {
              this.transitionOverlay.setVisible(false);
            },
          });
        },
      });
    }

    playShortRestTransition() {
      const width = this.scale.width;
      const height = this.scale.height;
      const cx = width / 2;
      const cy = height * 0.34;
      const overlay = this.add.rectangle(0, 0, width, height, 0x07182f, 0)
        .setOrigin(0, 0)
        .setDepth(520);
      const clock = this.add.text(cx, cy - 28, "◷", {
        fontFamily: "Georgia, serif",
        fontSize: "54px",
        fontStyle: "bold",
        color: "#bfe7ff",
        stroke: "#07111f",
        strokeThickness: 5,
      }).setOrigin(0.5).setDepth(521).setAlpha(0);
      const label = this.add.text(cx, cy + 22, "1 Hour Later...", {
        fontFamily: "Georgia, serif",
        fontSize: "24px",
        fontStyle: "bold",
        color: "#d8ecff",
        stroke: "#07111f",
        strokeThickness: 4,
      }).setOrigin(0.5).setDepth(521).setAlpha(0);

      this.tweens.add({
        targets: overlay,
        alpha: 0.42,
        duration: 160,
        ease: "Quad.easeOut",
      });
      this.tweens.add({
        targets: clock,
        alpha: 1,
        angle: 720,
        duration: 800,
        ease: "Cubic.easeInOut",
      });
      this.tweens.add({
        targets: label,
        alpha: 1,
        y: label.y - 8,
        duration: 220,
        ease: "Quad.easeOut",
      });
      this.tweens.add({
        targets: [overlay, clock, label],
        alpha: 0,
        delay: 620,
        duration: 180,
        ease: "Quad.easeIn",
        onComplete: () => {
          overlay.destroy();
          clock.destroy();
          label.destroy();
        },
      });
    }

    playLongRestTransition() {
      const width = this.scale.width;
      const height = this.scale.height;
      const cx = width / 2;
      const cy = height * 0.36;
      const overlay = this.add.rectangle(0, 0, width, height, 0x000000, 0)
        .setOrigin(0, 0)
        .setDepth(530);
      const title = this.add.text(cx, cy - 10, "一夜过去", {
        fontFamily: "Georgia, serif",
        fontSize: "48px",
        fontStyle: "bold",
        color: "#f4e0a2",
        stroke: "#120c05",
        strokeThickness: 7,
      }).setOrigin(0.5).setDepth(531).setAlpha(0);
      const subtitle = this.add.text(cx, cy + 42, "The Next Day", {
        fontFamily: "Georgia, serif",
        fontSize: "22px",
        fontStyle: "bold",
        color: "#d0ab67",
        stroke: "#120c05",
        strokeThickness: 4,
      }).setOrigin(0.5).setDepth(531).setAlpha(0);

      this.tweens.add({
        targets: overlay,
        alpha: 1,
        duration: 360,
        ease: "Quad.easeIn",
      });
      this.tweens.add({
        targets: [title, subtitle],
        alpha: 1,
        delay: 260,
        duration: 240,
        ease: "Quad.easeOut",
      });
      this.tweens.add({
        targets: [overlay, title, subtitle],
        alpha: 0,
        delay: 1120,
        duration: 380,
        ease: "Quad.easeOut",
        onComplete: () => {
          overlay.destroy();
          title.destroy();
          subtitle.destroy();
        },
      });
    }

    playSpeechBubble(entityId, text) {
      const token = this.tokens.get(normalizeId(entityId));
      const line = String(text || "").trim();
      if (!token || !line) return;

      this.clearSpeechBubble(token);

      const maxWidth = Math.max(120, Math.min(260, this.board.cell * 3.4));
      const paddingX = 12;
      const paddingY = 9;
      const bubble = this.add.container(token.container.x, token.container.y - this.board.cell * 0.9).setDepth(260);
      const label = this.add.text(0, 0, line, {
        fontFamily: "Georgia, serif",
        fontSize: Math.max(13, this.board.cell * 0.18) + "px",
        color: "#1b1710",
        lineSpacing: 4,
        wordWrap: { width: maxWidth },
      });
      const width = Math.min(maxWidth, Math.max(80, label.width)) + paddingX * 2;
      const height = Math.max(34, label.height) + paddingY * 2;
      const background = this.add.graphics();

      background.fillStyle(0xfff5dc, 0.96);
      background.fillRoundedRect(-width / 2, -height, width, height, 12);
      background.fillTriangle(-10, 0, 10, 0, 0, 12);
      background.lineStyle(2, 0x2c2418, 0.24);
      background.strokeRoundedRect(-width / 2, -height, width, height, 12);
      label.setPosition(-width / 2 + paddingX, -height + paddingY);

      bubble.add([background, label]);
      bubble.setScale(0.82);
      bubble.setAlpha(0);
      token.speechBubble = bubble;

      this.tweens.add({
        targets: bubble,
        scaleX: 1,
        scaleY: 1,
        alpha: 1,
        duration: 200,
        ease: "Back.easeOut",
      });

      token.speechBubbleTimer = this.time.delayedCall(2400, () => {
        this.tweens.add({
          targets: bubble,
          y: bubble.y - 20,
          alpha: 0,
          duration: 300,
          ease: "Quad.easeIn",
          onComplete: () => {
            bubble.destroy();
            if (token.speechBubble === bubble) {
              token.speechBubble = null;
              token.speechBubbleTimer = null;
            }
          },
        });
      });
    }

    clearSpeechBubble(token) {
      if (token.speechBubbleTimer) {
        token.speechBubbleTimer.remove(false);
        token.speechBubbleTimer = null;
      }
      if (token.speechBubble) {
        token.speechBubble.destroy();
        token.speechBubble = null;
      }
    }

    upsertToken(entity) {
      const id = normalizeId(entity.id);
      let token = this.tokens.get(id);
      if (!token) {
        token = this.createToken(entity);
        this.tokens.set(id, token);
      }

      token.entity = entity;
      this.applyTokenStyle(token, entity.kind);
      this.applyTokenVisual(token, entity);
      this.moveToken(token, entity.x, entity.y, true);
    }

    tokenLayerForKind(kind) {
      return this.entityLayer;
    }

    syncTokenLayer(token, kind) {
      const layer = this.tokenLayerForKind(kind);
      if (!layer || token.renderLayer === layer) return;
      if (token.renderLayer && typeof token.renderLayer.remove === "function") {
        token.renderLayer.remove(token.container);
      }
      layer.add(token.container);
      token.renderLayer = layer;
    }

    kindDepth(kind) {
      const depths = {
        player: DEPTH_LAYERS.actors + 1,
        hostile: DEPTH_LAYERS.actors + 1,
        neutral: DEPTH_LAYERS.actors + 1,
        object: DEPTH_LAYERS.actors,
        loot: DEPTH_LAYERS.actors,
        door: DEPTH_LAYERS.actors - 0.05,
        trap: DEPTH_LAYERS.actors,
        chest: DEPTH_LAYERS.actors,
      };
      return depths[kind] || DEPTH_LAYERS.actors;
    }

    kindBaseScale(kind) {
      const scales = {
        player: 1.08,
        hostile: 1.06,
        neutral: 1.04,
        object: 0.94,
        loot: 0.84,
        door: 1,
        trap: 0.9,
        chest: 0.94,
      };
      return scales[kind] || 1;
    }

    updateTokenScale(token) {
      const baseScale = this.scaleForTile(token.baseScale || 1);
      token.baseWorldScale = baseScale;
      token.container.setScale(baseScale);
    }

    createToken(entity) {
      const container = this.add.container(0, 0);
      const doorFrame = this.add.graphics();
      doorFrame.setVisible(false);
      const actorPixel = this.add.graphics();
      actorPixel.setVisible(false);
      const sprite = this.add.image(0, 0, SPRITE_KEYS.actors, pickFrame(ACTOR_FRAMES.neutral, entity.id)).setOrigin(0.5);
      const poisonIcon = this.add.image(5, -6, SPRITE_KEYS.tiles, TILE_FRAMES.poison)
        .setOrigin(0.5)
        .setScale(0.52);
      poisonIcon.setVisible(false);
      const lockBadge = this.add.image(5, -6, SPRITE_KEYS.tiles, TILE_FRAMES.locked)
        .setOrigin(0.5)
        .setScale(0.52);
      lockBadge.setVisible(false);

      container.add([doorFrame, actorPixel, sprite, poisonIcon, lockBadge]);
      container.setDepth(this.kindDepth(entity.kind));
      return {
        container,
        sprite,
        poisonIcon,
        entity,
        pulseTween: null,
        moveTween: null,
        lootTween: null,
        trapTween: null,
        doorTween: null,
        doorOpenState: null,
        doorFrame,
        actorPixel,
        lockBadge,
        speechBubble: null,
        speechBubbleTimer: null,
        currentKind: null,
        baseScale: this.kindBaseScale(entity.kind),
        baseWorldScale: 1,
        hasSpawned: false,
        renderLayer: null,
      };
    }

    applyTokenStyle(token, kind) {
      this.syncTokenLayer(token, kind);
      token.baseScale = this.kindBaseScale(kind);
      token.container.setDepth(this.kindDepth(kind));
      this.updateTokenScale(token);
      if (token.currentKind !== kind) {
        token.currentKind = kind;
        this.updateIdleTween(token, true);
      }
    }

    updateIdleTween(token, forceReset = false) {
      if (forceReset && token.pulseTween) {
        token.pulseTween.stop();
        token.pulseTween = null;
      }
      if (forceReset && token.lootTween) {
        token.lootTween.stop();
        token.lootTween = null;
      }
      if (forceReset && token.trapTween) {
        token.trapTween.stop();
        token.trapTween = null;
      }

      token.container.setScale(token.baseWorldScale);
      token.sprite.setAlpha(1);

      if (token.currentKind === "player" && !token.pulseTween) {
        token.pulseTween = this.tweens.add({
          targets: token.container,
          scaleX: token.baseWorldScale * 1.06,
          scaleY: token.baseWorldScale * 1.06,
          duration: 900,
          yoyo: true,
          repeat: -1,
          ease: "Sine.easeInOut",
        });
      }

      if (token.currentKind !== "loot" && token.lootTween) {
        token.lootTween.stop();
        token.lootTween = null;
      }
      if (token.currentKind === "loot" && !token.lootTween) {
        token.lootTween = this.tweens.add({
          targets: token.sprite,
          alpha: 0.62,
          duration: 760,
          ease: "Sine.easeInOut",
          yoyo: true,
          repeat: -1,
        });
      }

      if (token.currentKind !== "trap" && token.trapTween) {
        token.trapTween.stop();
        token.trapTween = null;
      }
      if (token.currentKind === "trap" && !token.trapTween) {
        token.trapTween = this.tweens.add({
          targets: token.sprite,
          alpha: 0.52,
          duration: 660,
          ease: "Sine.easeInOut",
          yoyo: true,
          repeat: -1,
        });
      }
    }

    resetTokenSpriteTransform(token) {
      token.sprite.setScale(1, 1);
      token.sprite.setAngle(0);
      token.sprite.setAlpha(1);
      if (typeof token.sprite.clearTint === "function") token.sprite.clearTint();
    }

    resolveActorFrame(entity) {
      const kind = entity.kind;
      const id = normalizeId(entity.id);
      const data = safeObject(entity.data);
      const hint = normalizeId(data.name || data.type || data.kind || id);
      if (ACTOR_FRAMES.partyById[id] !== undefined) {
        return ACTOR_FRAMES.partyById[id];
      }
      if (kind === "player") {
        return pickFrame(ACTOR_FRAMES.player, id || hint);
      }
      if (kind === "hostile") {
        if (ACTOR_FRAMES.hostileById[id] !== undefined) return ACTOR_FRAMES.hostileById[id];
        if (hint.includes("drone")) return 11;
        if (hint.includes("skeleton")) return 13;
        if (hint.includes("scout")) return 12;
        return pickFrame(ACTOR_FRAMES.hostile, id || hint);
      }
      if (kind === "neutral") {
        return pickFrame(ACTOR_FRAMES.neutral, id || hint);
      }
      return pickFrame(ACTOR_FRAMES.object, id || hint);
    }

    applyTokenVisual(token, entity) {
      this.resetTokenSpriteTransform(token);
      token.sprite.setVisible(true);
      if (token.doorFrame) {
        token.doorFrame.clear();
        token.doorFrame.setVisible(false);
      }
      if (token.actorPixel) {
        token.actorPixel.clear();
        token.actorPixel.setVisible(false);
      }
      if (token.poisonIcon) token.poisonIcon.setVisible(false);
      if (token.lockBadge) token.lockBadge.setVisible(false);

      if (entity.kind === "door") {
        this.applyDoorVisual(token, entity.data);
        return;
      }
      if (entity.kind === "trap") {
        this.applyTrapVisual(token, entity);
        return;
      }
      if (entity.kind === "chest") {
        this.applyChestVisual(token, entity.data);
        return;
      }
      if (entity.kind === "loot") {
        token.sprite.setTexture(SPRITE_KEYS.tiles, pickFrame(TILE_FRAMES.loot, normalizeId(entity.id)));
        return;
      }
      if (entity.kind === "object") {
        token.sprite.setTexture(SPRITE_KEYS.tiles, pickFrame(TILE_FRAMES.prop, normalizeId(entity.id)));
        return;
      }

      const actorTexture = PARTY_TEXTURES[normalizeId(entity.id)];
      if (actorTexture) {
        token.sprite.setTexture(actorTexture);
      } else {
        token.sprite.setTexture(SPRITE_KEYS.actors, this.resolveActorFrame(entity));
      }
      this.applyStatusEffects(token, entity.data);
      this.applyPartyTextureScale(token, entity);
    }

    applyPartyTextureScale(token, entity) {
      const scale = PARTY_TEXTURE_SCALES[normalizeId(entity.id)];
      if (!scale) return;
      token.sprite.setScale(token.sprite.scaleX * scale, token.sprite.scaleY * scale);
    }

    applyTrapVisual(token, entity) {
      token.sprite.setTexture(SPRITE_KEYS.tiles, pickFrame(TILE_FRAMES.trap, normalizeId(entity.id)));
      token.sprite.setAlpha(0.96);
    }

    applyChestVisual(token, data) {
      const locked = isLocked(data);
      token.sprite.setTexture(SPRITE_KEYS.tiles, locked ? TILE_FRAMES.chestClosed : TILE_FRAMES.chestOpen);
      if (token.lockBadge) token.lockBadge.setVisible(locked);
    }

    applyDoorVisual(token, data) {
      const open = isDoorOpen(data);
      const changed = token.doorOpenState !== null && token.doorOpenState !== open;
      const width = Math.max(1, Number(data.w ?? data.width ?? data.size_w ?? data.tile_width) || 1);
      const height = Math.max(1, Number(data.h ?? data.height ?? data.size_h ?? data.tile_height) || 1);
      const vertical = height > width;
      const spanCells = Math.max(width, height);
      const thicknessCells = Math.min(width, height);
      const localWidth = Math.max(16, spanCells * 16);
      const localHeight = Math.max(10, thicknessCells * 16);
      const left = -localWidth / 2;
      const top = -localHeight / 2;
      if (token.doorFrame) {
        token.doorFrame.clear();
        token.doorFrame.setAlpha(1);
        token.doorFrame.setAngle(vertical ? 90 : 0);
        token.doorFrame.setVisible(true);
        if (open) {
          token.doorFrame.fillStyle(0x25140d, 0.7);
          token.doorFrame.fillRect(left, top + localHeight * 0.38, localWidth, localHeight * 0.24);
          token.doorFrame.fillStyle(0x7a3a1d, 1);
          token.doorFrame.fillRect(left, top, 4, localHeight);
          token.doorFrame.fillRect(left + localWidth - 4, top, 4, localHeight);
          token.doorFrame.fillStyle(0xb77032, 0.95);
          token.doorFrame.fillRect(left + 4, top + 1, localWidth - 8, 2);
          token.doorFrame.fillRect(left + 4, top + localHeight - 3, localWidth - 8, 2);
          token.doorFrame.fillStyle(0x4a2315, 0.9);
          token.doorFrame.fillRect(left - 4, top + 1, 5, localHeight - 2);
          token.doorFrame.fillRect(left + localWidth - 1, top + 1, 5, localHeight - 2);
        } else {
          token.doorFrame.fillStyle(0x522516, 1);
          token.doorFrame.fillRect(left, top, localWidth, localHeight);
          token.doorFrame.fillStyle(0x9a4c21, 1);
          token.doorFrame.fillRect(left + 3, top + 2, localWidth - 6, localHeight - 4);
          token.doorFrame.fillStyle(0x20120d, 1);
          token.doorFrame.fillRect(left + 7, top + 4, localWidth - 14, localHeight - 8);
          token.doorFrame.fillStyle(0xd0933e, 0.92);
          for (let x = left + 10; x < left + localWidth - 8; x += 12) {
            token.doorFrame.fillRect(x, top + 4, 2, localHeight - 8);
          }
          token.doorFrame.fillStyle(0xd7a64a, 1);
          token.doorFrame.fillRect(left + localWidth - 12, top + localHeight * 0.36, 6, 6);
        }
      }
      token.sprite.setVisible(false);
      token.sprite.setAngle(0);
      if (token.lockBadge) token.lockBadge.setVisible(isLocked(data));
      token.doorOpenState = open;
      if (token.doorTween) {
        token.doorTween.stop();
        token.doorTween = null;
      }
      if (!changed || !token.doorFrame || prefersReducedMotion()) {
        return;
      }
      token.doorTween = this.tweens.add({
        targets: token.doorFrame,
        alpha: open ? 0.45 : 0.55,
        duration: 100,
        ease: "Sine.easeInOut",
        onComplete: () => {
          token.doorTween = this.tweens.add({
            targets: token.doorFrame,
            alpha: 1,
            duration: 140,
            ease: "Sine.easeInOut",
            onComplete: () => {
              token.doorTween = null;
            },
          });
        },
      });
    }

    applyStatusEffects(token, data) {
      const actorId = normalizeId(safeObject(token.entity).id || "");
      const effects = normalizeStatusEffects(data);
      const isPoisoned = effects.includes("poisoned");
      const isProne = effects.includes("prone");

      if (token.poisonIcon) {
        token.poisonIcon.setVisible(isPoisoned);
      }
      token.sprite.setScale(isProne ? 1.16 : 1, isProne ? 0.68 : 1);
      token.sprite.setAngle(isProne ? 90 : 0);

      if (isPoisoned) {
        token.sprite.setAlpha(0.9);
        if (typeof token.sprite.setTint === "function") token.sprite.setTint(0xa2ff9e);
      } else {
        token.sprite.setAlpha(1);
        const baseTint = ACTOR_TINTS[actorId];
        if (baseTint !== undefined && typeof token.sprite.setTint === "function") {
          token.sprite.setTint(baseTint);
        } else if (typeof token.sprite.clearTint === "function") {
          token.sprite.clearTint();
        }
      }
    }

    moveToken(token, gridX, gridY, animate) {
      const target = this.gridToWorld(gridX, gridY);
      const samePosition = Math.abs(token.container.x - target.x) < 0.5 && Math.abs(token.container.y - target.y) < 0.5;
      if (token.moveTween) {
        token.moveTween.stop();
        token.moveTween = null;
      }
      if (!animate || !token.hasSpawned) {
        token.container.setPosition(target.x, target.y);
        token.hasSpawned = true;
        return;
      }
      if (samePosition) {
        return;
      }
      token.moveTween = this.tweens.add({
        targets: token.container,
        x: target.x,
        y: target.y,
        duration: 180,
        ease: "Sine.easeOut",
        onComplete: () => {
          token.moveTween = null;
          token.hasSpawned = true;
        },
      });
    }

    positionAllTokens(animate) {
      this.tokens.forEach((token) => {
        this.moveToken(token, token.entity.x, token.entity.y, animate);
      });
    }

    destroyToken(token) {
      if (token.moveTween) token.moveTween.stop();
      if (token.pulseTween) token.pulseTween.stop();
      if (token.lootTween) token.lootTween.stop();
      if (token.trapTween) token.trapTween.stop();
      if (token.doorTween) token.doorTween.stop();
      this.clearSpeechBubble(token);
      token.container.destroy();
    }

    gridToWorld(gridX, gridY) {
      return {
        x: this.board.x + (gridX + 0.5) * this.board.cell,
        y: this.board.y + (gridY + 0.5) * this.board.cell,
      };
    }
  }

  const initialViewport = gameViewportSize();
  const config = {
    type: Phaser.AUTO,
    parent: "game-viewport",
    backgroundColor: "rgba(0,0,0,0)",
    transparent: true,
    render: {
      pixelArt: true,
      roundPixels: true,
    },
    width: initialViewport.width,
    height: initialViewport.height,
    scale: {
      mode: Phaser.Scale.FIT,
      autoCenter: Phaser.Scale.CENTER_BOTH,
    },
    physics: {
      default: "arcade",
      arcade: { debug: false },
    },
    scene: [MainScene],
  };

  window.addEventListener("DOMContentLoaded", () => {
    if (!document.getElementById("game-viewport")) return;
    controller.game = new Phaser.Game(config);
    window.addEventListener("resize", () => controller.resize());
  });
})();
