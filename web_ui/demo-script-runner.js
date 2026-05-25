/**
 * demo-script-runner.js
 * Showcase-only scripted path through hazard_lab.
 * Exposed on window.ControlledAgentDemoScriptRunner.
 */
(() => {
  "use strict";

  const DEFAULT_DELAY_MS = 1050;

  function safeObj(value) {
    return value && typeof value === "object" ? value : {};
  }

  function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, Math.max(0, Number(ms) || 0)));
  }

  function createRunner(api, options = {}) {
    const app = safeObj(api);
    const opts = safeObj(options);
    let running = false;
    let stopped = false;
    let index = 0;
    let currentPromise = null;
    const delayMs = Number(opts.delayMs) || DEFAULT_DELAY_MS;

    const steps = [
      {
        id: "new_session",
        title: "Act 0 — Fresh Session",
        summary: "Initialize a clean hazard_lab timeline.",
        run: async () => {
          if (typeof app.startNewTimeline === "function") await app.startNewTimeline();
        },
      },
      {
        id: "reveal_b",
        title: "Act 1 — Room Reveal",
        summary: "Open A-B door and expose visibleRooms diff.",
        run: async () => {
          if (typeof app.runShowcaseLocalStep === "function") {
            app.runShowcaseLocalStep("qa_open door_a_to_b", { title: "Act 1 — Room Reveal" });
          }
        },
      },
      {
        id: "act1_perception",
        title: "Act 1 — Trap Sense",
        summary: "Trigger perception, dice card, trap highlight, and Director Trace.",
        run: async () => {
          if (typeof app.runShowcaseLocalStep === "function") {
            app.runShowcaseLocalStep("qa_perception", { title: "Act 1 — Trap Sense" });
          }
          if (typeof app.sendMessage === "function") {
            await app.sendMessage("我谨慎进入毒气走廊，观察地面与墙缝。", "trigger_zone", null, {
              target: "act1_corridor_approach",
              source: "trigger_zone",
              skipIdleReset: true,
            });
          }
        },
      },
      {
        id: "secret_study",
        title: "Act 1.5 — Secret Study",
        summary: "Discover and open B-C secret door.",
        run: async () => {
          if (typeof app.runShowcaseLocalStep === "function") {
            app.runShowcaseLocalStep("qa_open door_b_to_c", { title: "Act 1.5 — Secret Study" });
          }
        },
      },
      {
        id: "read_diary",
        title: "Act 2 — ActorView Memory",
        summary: "Read hazard_diary and show memory isolation plus diff.",
        run: async () => {
          if (typeof app.sendMessage === "function") {
            await app.sendMessage("用奥术知识阅读 hazard_diary。", "READ", null, {
              target: "hazard_diary",
              source: "interaction",
              skipIdleReset: true,
            });
          }
        },
      },
      {
        id: "loot_study_chest",
        title: "Act 2.5 — Study Chest Loot",
        summary: "Loot study_chest and show item transfer through EventDrain.",
        run: async () => {
          if (typeof app.sendMessage === "function") {
            await app.sendMessage("我要搜刮 study_chest", "ui_action_loot", "player", {
              target: "chest_1",
              source: "ui_click",
              skipIdleReset: true,
            });
          }
        },
      },
      {
        id: "open_lab",
        title: "Act 3 — Lab Door",
        summary: "Open B-D door with lab_key and reveal Gatekeeper chamber.",
        run: async () => {
          if (typeof app.runShowcaseLocalStep === "function") {
            app.runShowcaseLocalStep("qa_open door_b_to_d", { title: "Act 3 — Lab Door" });
          }
        },
      },
      {
        id: "gatekeeper_start",
        title: "Act 3 — Party Turn Coordinator",
        summary: "Start Gatekeeper dialogue.",
        run: async () => {
          if (typeof app.sendMessage === "function") {
            await app.sendMessage("我想和 Gatekeeper 谈谈。", "CHAT", null, {
              target: "gatekeeper",
              source: "interaction",
              skipIdleReset: true,
            });
          }
        },
      },
      {
        id: "side_scout",
        title: "Act 3 — Scout Branch",
        summary: "Choose side_with_scout and expose affection/combat/hostility diffs.",
        run: async () => {
          if (typeof app.sendMessage === "function") {
            await app.sendMessage("side_with_scout：侦察员说得对，我们一起嘲笑 Gatekeeper。", "CHAT", null, {
              target: "gatekeeper",
              source: "dialogue_input",
              skipIdleReset: true,
            });
          }
        },
      },
      {
        id: "loot_gatekeeper_key",
        title: "Act 4 — EventDrain Key Transfer",
        summary: "Loot heavy_iron_key from Gatekeeper.",
        run: async () => {
          if (typeof app.sendMessage === "function") {
            await app.sendMessage("我确认 heavy_iron_key 已经入包，准备撤离。", "chat", null, {
              source: "text_input",
              skipIdleReset: true,
            });
          }
        },
      },
      {
        id: "exit",
        title: "Act 4 — Demo Cleared",
        summary: "Open exit_door and show completion banner.",
        run: async () => {
          if (typeof app.sendMessage === "function") {
            await app.sendMessage("移动到 17,4", "MOVE", null, {
              target: "17,4",
              source: "text_input",
              skipIdleReset: true,
            });
            await app.sendMessage("用 heavy_iron_key 打开 heavy_oak_door_1。", "INTERACT", null, {
              target: "heavy_oak_door_1",
              source: "interaction",
              skipIdleReset: true,
            });
            if (typeof app.completeShowcaseLocally === "function") {
              app.completeShowcaseLocally("exit_door_showcase");
            }
          }
        },
      },
    ];

    function setAct(step) {
      if (window.ControlledAgentHudRenderers && typeof window.ControlledAgentHudRenderers.updateActProgress === "function") {
        const actMatch = String(step.title || "").match(/Act\s+(\d+)/i);
        window.ControlledAgentHudRenderers.updateActProgress(actMatch ? Number(actMatch[1]) : 1, step.summary || step.title || "");
      }
      if (typeof opts.onStep === "function") opts.onStep(step, index);
    }

    async function run() {
      if (running) return currentPromise;
      running = true;
      stopped = false;
      index = 0;
      currentPromise = (async () => {
        try {
          while (index < steps.length && !stopped) {
            const step = steps[index];
            setAct(step);
            await step.run();
            index += 1;
            if (!stopped && index < steps.length) await wait(delayMs);
          }
        } finally {
          running = false;
          if (typeof opts.onDone === "function") opts.onDone({ stopped, index });
        }
        return { stopped, index };
      })();
      return currentPromise;
    }

    function stop() {
      stopped = true;
      running = false;
      if (typeof opts.onStop === "function") opts.onStop({ index });
    }

    function isRunning() {
      return running && !stopped;
    }

    return Object.freeze({ run, stop, isRunning, steps, getIndex: () => index });
  }

  window.ControlledAgentDemoScriptRunner = Object.freeze({ createRunner, wait });
})();
