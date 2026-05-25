/**
 * hazard-meta.js
 * ───────────────────────────────────────────────────────
 * Hazard Lab demo-specific metadata.
 * Loaded before app.js so the main orchestrator can merge these
 * into its global registries.
 *
 * Exposed on window.ControlledAgentHazardMeta for cross-module access.
 */
(() => {
  "use strict";

  /* ── map_id 配置（URL 参数优先，默认 hazard_lab） ── */
  const MAP_ID =
    new URLSearchParams(window.location.search).get("map_id") ||
    "hazard_lab";

  /* ── 追加角色 Speaker 元数据 ── */
  const SPEAKER_META_EXTENSIONS = {
    gatekeeper: { name: "守门人", color: "#8bc34a", sigil: "🐸" },
  };

  /* ── 追加物品元数据 ── */
  const ITEM_META_EXTENSIONS = {
    heavy_iron_key: { label: "沉重铁钥匙", icon: "🗝" },
    lab_key: { label: "实验室钥匙", icon: "🗝" },
    hazard_diary: { label: "危害研究员日记", icon: "📓" },
    antidote_formula: { label: "解毒配方残页", icon: "📜" },
  };

  /* ── 追加位置标签 ── */
  const LOCATION_LABEL_EXTENSIONS = {
    hazard_lab: "危害研究员的废弃实验室",
  };

  /* ── 场景对象友好标签 ── */
  const OBJECT_LABELS = {
    gas_trap_1: "毒气陷阱",
    poison_trap_1: "毒气陷阱 I",
    poison_trap_2: "毒气陷阱 II",
    door_a_to_b: "A 区通往毒气走廊",
    door_b_to_c: "隐藏书房侧门",
    door_b_to_d: "实验室重门",
    exit_door: "出口门",
    heavy_oak_door_1: "通往地表的沉重大门",
    hazard_diary: "血污日记",
    chemical_notes: "化学残页",
    iron_key_sketch: "重铁钥匙草图",
    study_chest: "旧木箱",
    chest_1: "危害研究员的战利品箱",
  };

  /* ── 四幕目标 ── */
  const ACT_OBJECTIVES = Object.freeze([
    {
      act: 1,
      title: "安全屋",
      summary: "从危害研究员的废弃实验室醒来，确认队伍状态并寻找出口。",
      keywords: ["room_a_spawn", "安全屋", "入口", "醒来"],
    },
    {
      act: 2,
      title: "毒气走廊",
      summary: "侦察员在前方停下脚步。空气里有甜腻的腐臭味，墙缝间隐约传来气压声。",
      keywords: [
        "gas_trap",
        "毒气",
        "trap",
        "陷阱感知",
        "room_b_corridor",
      ],
    },
    {
      act: 3,
      title: "秘密书房",
      summary: "墙后露出一间狭窄书房，日记与残页把 Gatekeeper、钥匙和毒气真相串在一起。",
      keywords: ["秘密书房", "room_c_secret_study", "act3_secret_study", "cracked_wall", "diary"],
    },
    {
      act: 4,
      title: "Gatekeeper Lab",
      summary: "Gatekeeper 攥着沉重铁钥匙，身后的毒气罐低声翻滚。",
      keywords: [
        "act4_boss_room_entered",
        "gatekeeper_confrontation_started",
        "Boss Encounter",
        "Boss方案",
        "heavy_iron_key",
        "poison_valve",
        "heavy_oak_door",
        "开门",
        "escape",
        "cleared",
      ],
    },
  ]);

  /* ── 推断当前幕数（基于 journal_events / flags） ── */
  function inferCurrentAct(journalEvents, flags) {
    const events = Array.isArray(journalEvents) ? journalEvents : [];
    const f = flags && typeof flags === "object" ? flags : {};
    const text = events.join(" ").toLowerCase();

    if (
      f.hazard_lab_escape_complete ||
      f.demo_cleared ||
      /demo.*cleared|通关/i.test(text)
    ) {
      return 4;
    }
    if (
      f.act4_boss_room_entered ||
      f.act4_gatekeeper_confrontation_started ||
      f.act4_heavy_iron_key_obtained ||
      f.act4_final_exit_opened ||
      /\[boss encounter\]|\[boss方案\]|\[boss解决\]|\[偷钥匙失败\]|\[毒气泄漏\]|act4_/i.test(text)
    ) {
      return 4;
    }
    if (
      f.world_hazard_lab_gatekeeper_defeated ||
      f.hazard_lab_gatekeeper_combat_triggered ||
      /gatekeeper.*hostile|gatekeeper.*战斗/i.test(text)
    ) {
      return 4;
    }
    if (
      f.act3_secret_study_entered ||
      f.act3_secret_study_discovered ||
      f.act3_diary_read ||
      /\[秘密书房\]|room_c_secret_study|cracked_wall/i.test(text)
    ) {
      return 3;
    }
    if (
      f.hazard_lab_gatekeeper_negotiation_started ||
      /gatekeeper.*交涉|gatekeeper.*对话/i.test(text)
    ) {
      return 3;
    }
    if (
      f.hazard_lab_diary_read ||
      /日记|diary|arcana|investigation/i.test(text)
    ) {
      return 2;
    }
    if (
      f.world_hazard_lab_trap_warned ||
      /毒气|gas_trap|陷阱感知/i.test(text)
    ) {
      return 1;
    }
    return 1;
  }

  /* ── Public API ── */
  window.ControlledAgentHazardMeta = Object.freeze({
    MAP_ID,
    SPEAKER_META_EXTENSIONS,
    ITEM_META_EXTENSIONS,
    LOCATION_LABEL_EXTENSIONS,
    OBJECT_LABELS,
    ACT_OBJECTIVES,
    inferCurrentAct,
  });
})();
