import type { OpenClawPluginApi } from "openclaw/plugin-sdk/memory-core";
import { appendFileSync } from "node:fs";

const AUDIT_LOG = "/tmp/cloud_assistant_audit.jsonl";

const ESCALATE_ACK =
  "Escalation acknowledged. The next single step will be handled by the stronger " +
  "cloud model, which sees your full conversation and tools. After that step you " +
  "will regain control — review its result and continue.";

const plugin = {
  id: "escalate-signal",
  name: "Escalate Signal",
  description:
    "Register a parameterless `escalate` tool. It performs no work itself; calling it " +
    "emits a signal that the cloud-router proxy (ROUTER_MODE=signal) uses to route the " +
    "NEXT round to the stronger cloud model, which then executes natively via OpenClaw.",
  configSchema: { type: "object" as const, properties: {} },

  register(api: OpenClawPluginApi) {
    api.registerTool({
      name: "escalate",
      label: "Escalate",
      description: "Ask a stronger model to perform your next single step. Call it before substantive work (writing, editing, or committing to an interpretation), when stuck, when considering a change of approach, and before declaring the task done — at least twice per task. Takes NO parameters — your full conversation history is automatically forwarded.",
      parameters: {
        type: "object",
        properties: {},
      },
      async execute(_toolCallId: string, _params: any) {
        try {
          appendFileSync(
            AUDIT_LOG,
            JSON.stringify({
              timestamp: new Date().toISOString(),
              tool: "escalate",
              event: "signal_emitted",
            }) + "\n",
          );
        } catch {  }
        return {
          content: [{ type: "text" as const, text: ESCALATE_ACK }],
          details: {},
        };
      },
    });

    api.logger.info("[Escalate] Tool registered: escalate (no-op signal for ROUTER_MODE=signal)");
  },
};

export default plugin;
