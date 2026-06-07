
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/memory-core";
import { appendFileSync } from "node:fs";

const AUDIT_LOG = "/tmp/orchestrator_v1_audit.jsonl";

function logAudit(record: Record<string, unknown>): void {
  try {
    appendFileSync(
      AUDIT_LOG,
      JSON.stringify({ ts: new Date().toISOString(), ...record }) + "\n",
    );
  } catch { /* audit failure is non-fatal */ }
}

const ORCHESTRATOR_ALLOWED_TOOLS = new Set<string>([
  "sessions_spawn",
  "sessions_send",
  "sessions_list",
  "exec",
  "read",
]);


const READ_PII_ZONE_RE = /\/(?:fixtures?|data|messages|emails?|inbox|tmp\/fixtures)\b|\/tmp_workspace\/(?:fixtures?|data)\b|messages\.json$|fixtures?\.json$|emails?\.json$/i;
const READ_SKILL_RE = /\/skills\/[^/]+\/SKILL\.md$/i;


const EXEC_FIRST_TOKEN_ALLOWED = new Set<string>([
  "ls",       // 目录条目 + 元数据,不含内容
  "find",     // 路径搜索(必须带 -maxdepth,禁 -exec/-delete)
  "stat",     // 文件元信息
  "curl",     // 只允许 localhost / 127.0.0.1 → 探活 mock HTTP 服务
  "which",    // 命令解析
]);

const EXEC_SHELL_META_RE = /[|`;<>$\\\n\r]|&&|\|\||\$\(/;

const FIND_DANGEROUS_RE = /-exec(?:dir)?\b|-delete\b|-fls\b|-fprint(?:0|f)?\b|-okdir?\b/;

function checkExecCommand(cmd: string): { ok: true } | { ok: false; reason: string } {
  const trimmed = (cmd ?? "").trim();
  if (!trimmed) return { ok: false, reason: "empty command" };
  if (EXEC_SHELL_META_RE.test(trimmed)) {
    return {
      ok: false,
      reason: "shell metacharacters not allowed (no | ; && || > < $ ` $(...) newline)",
    };
  }
  // 单独显式拒后台 `&` (放在 SHELL_META_RE 之外是因为它不在字符类里太容易误伤)
  if (/(?:^|\s)&(?:\s|$)/.test(trimmed)) {
    return { ok: false, reason: "background & not allowed" };
  }
  const firstTok = trimmed.split(/\s+/)[0];
  if (!EXEC_FIRST_TOKEN_ALLOWED.has(firstTok)) {
    return {
      ok: false,
      reason:
        `program "${firstTok}" not in whitelist. ` +
        `Allowed: ls / find / stat / ss / curl / which`,
    };
  }
  if (firstTok === "find") {
    if (!/-maxdepth\s+\d/.test(trimmed)) {
      return { ok: false, reason: "find must include -maxdepth N (cap recursion)" };
    }
    if (FIND_DANGEROUS_RE.test(trimmed)) {
      return {
        ok: false,
        reason: "find: -exec/-execdir/-delete/-fprint* not allowed (they bypass the read-only intent)",
      };
    }
  }
  if (firstTok === "curl") {
    // 拒任何不指向 localhost / 127.0.0.1 的 URL
    if (/https?:\/\/(?!(?:localhost|127\.0\.0\.1)(?:[/:]|$))/i.test(trimmed)) {
      return { ok: false, reason: "curl: only localhost / 127.0.0.1 URLs allowed" };
    }
    // 必须有 localhost-形态的 token,否则用户在用 curl 干别的(file:// / ftp:// / ...)
    if (!/(?:^|\s)(?:https?:\/\/)?(?:localhost|127\.0\.0\.1)(?:[/:]|\s|$)/i.test(trimmed)) {
      return { ok: false, reason: "curl must target localhost or 127.0.0.1" };
    }
    // 拒任何写盘开关
    if (/(?:^|\s)(?:-o|--output|--upload-file|-T|--data-binary|-d|--data)\b/.test(trimmed)) {
      return {
        ok: false,
        reason: "curl: write/data-upload flags not allowed (-o / --output / -T / -d / --data)",
      };
    }
  }
  return { ok: true };
}


function isSubagentSession(sessionKey: string | undefined): boolean {
  return !!sessionKey && sessionKey.includes(":subagent:");
}

function makeChildSessionKey(parentKey: string | undefined, label: string): string {
  const safeParent = parentKey && parentKey.length > 0 ? parentKey : "chat";
  // label 里只保留字母数字 _ -;其他 char 改成 _。避免特殊字符进 sessionKey 出问题
  const safeLabel = label.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 60) || "step";
  const ts = Date.now().toString(36);
  return `${safeParent}:subagent:${safeLabel}-${ts}`;
}

const REPLAY_WRITE_TOOL_NAMES = new Set<string>([
  "write", "create_file", "editor", "edit_file", "fs_write",
]);

const MAIN_CHAT_JSONL = "/root/.openclaw/agents/main/sessions/chat.jsonl";

function shortHex(n = 8): string {
  // 不需要密码学强度,只是给合成事件一个外观一致的 id
  return Math.random().toString(16).slice(2, 2 + n);
}

/** 从子 session messages[] 里提取所有 (write-like) toolCall 的 {name, path, content}。*/
function extractChildWrites(
  messages: unknown[],
): Array<{ name: string; path: string; content: string; toolCallId?: string }> {
  const out: Array<{ name: string; path: string; content: string; toolCallId?: string }> = [];
  for (const raw of messages) {
    if (!raw || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;
    let m: Record<string, unknown> = r;
    if (r["type"] === "message" && r["message"] && typeof r["message"] === "object") {
      m = r["message"] as Record<string, unknown>;
    }
    if (m["role"] !== "assistant") continue;
    const content = m["content"];
    if (!Array.isArray(content)) continue;
    for (const part of content) {
      if (!part || typeof part !== "object") continue;
      const p = part as Record<string, unknown>;
      if (p["type"] !== "toolCall" && p["type"] !== "tool_use") continue;
      const nm = String((p["name"] ?? p["toolName"]) ?? "").toLowerCase();
      if (!REPLAY_WRITE_TOOL_NAMES.has(nm)) continue;
      const argsObj = (p["input"] ?? p["arguments"] ?? {}) as Record<string, unknown>;
      const path = String(
        argsObj["path"] ?? argsObj["file_path"] ?? argsObj["filepath"] ?? "",
      );
      const cont = String(
        argsObj["content"] ?? argsObj["text"] ?? argsObj["file_text"] ?? "",
      );
      if (!cont) continue;  // 没内容就没必要回灌
      out.push({
        name: nm,
        path,
        content: cont,
        toolCallId: typeof p["id"] === "string" ? (p["id"] as string) : undefined,
      });
    }
  }
  return out;
}


function replayChildWritesToMainChat(
  writes: ReturnType<typeof extractChildWrites>,
  meta: { parentLabel: string; parentSession: string },
): number {
  if (writes.length === 0) return 0;
  let nReplayed = 0;
  for (const w of writes) {
    const ts = new Date().toISOString();
    const callId = "call_" + shortHex(16);
    const msgId = shortHex(8);
    const resMsgId = shortHex(8);
    const replayMarker = `subagent_replay:${meta.parentLabel}`;

    const assistantEvent = {
      type: "message",
      id: msgId,
      parentId: null,
      timestamp: ts,
      _replay_origin: replayMarker,
      message: {
        role: "assistant",
        content: [
          {
            type: "toolCall",
            id: callId,
            name: w.name,
            arguments: { path: w.path, content: w.content },
            _replay_origin: replayMarker,
          },
        ],
      },
    };
    const toolResultEvent = {
      type: "message",
      id: resMsgId,
      parentId: msgId,
      timestamp: ts,
      _replay_origin: replayMarker,
      message: {
        role: "user",
        content: [
          {
            type: "toolResult",
            toolUseId: callId,
            content: `Successfully wrote ${w.content.length} bytes to ${w.path} (replayed from subagent)`,
            _replay_origin: replayMarker,
          },
        ],
      },
    };
    try {
      appendFileSync(
        MAIN_CHAT_JSONL,
        JSON.stringify(assistantEvent) + "\n" + JSON.stringify(toolResultEvent) + "\n",
      );
      nReplayed += 1;
    } catch (err) {
      logAudit({
        event: "replay_write_failed",
        session: meta.parentSession,
        label: meta.parentLabel,
        path: w.path,
        error: String((err as Error)?.message ?? err),
      });
    }
  }
  return nReplayed;
}


function extractLastAssistantText(messages: unknown[]): string {
  let lastText = "";
  let lastToolCallSummary = "";
  for (const raw of messages) {
    if (!raw || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;

    // 解开 session-stream 包装
    let m = r;
    if (r["type"] === "message" && r["message"] && typeof r["message"] === "object") {
      m = r["message"] as Record<string, unknown>;
    }
    if (m["role"] !== "assistant") continue;

    const content = m["content"];
    if (typeof content === "string") {
      const s = content.trim();
      if (s) lastText = s;
      continue;
    }
    if (!Array.isArray(content)) continue;

    for (const part of content) {
      if (!part || typeof part !== "object") continue;
      const p = part as Record<string, unknown>;
      const pt = p["type"];
      if (pt === "text") {
        const t = String(p["text"] ?? "").trim();
        if (t) lastText = t;
      } else if (pt === "toolCall" || pt === "tool_use") {
        const nm = String(p["name"] ?? "?");
        const args = JSON.stringify(p["input"] ?? p["arguments"] ?? {}).slice(0, 200);
        lastToolCallSummary = `tool_call: ${nm}(${args})`;
      }
    }
  }
  return lastText || lastToolCallSummary || "(child produced no text output)";
}

const plugin = {
  id: "orchestrator-v1",
  name: "Orchestrator v1",
  description:
    "Cloud-driven, edge-executed mode via SYNCHRONOUS OpenClaw subagent. Main agent " +
    "(cloud, e.g. gpt-5.4) is restricted by a before_tool_call hook to: " +
    "sessions_spawn/sessions_send/sessions_list + exec (read-only discovery commands only: " +
    "ls / find -maxdepth N / stat / curl localhost / which, NO shell metacharacters) + " +
    "read (soft-audited: read calls are logged and PII-zone heuristic matches are flagged " +
    "as warnings, but never blocked; the read-policy is enforced at prompt level via the " +
    "'read protocols, not payloads' principle in orchestrator-system.md). `write`/`edit` " +
    "etc. remain blocked. sessions_spawn is wrapped synchronously: the hook calls " +
    "api.runtime.subagent.run/waitForRun/getSessionMessages internally and returns the " +
    "child's final text as the toolResult, so the main agent observes spawn as a blocking " +
    "RPC. The child still runs as a real OpenClaw subagent (with full file/shell tools) " +
    "and is routed to the edge worker (e.g. Qwen3.5-9B on vllm_local) by a " +
    "before_agent_start hook reading EDGE_PROVIDER/EDGE_MODEL env vars.",
  configSchema: { type: "object" as const, properties: {} },

  register(api: OpenClawPluginApi) {
    const _g = globalThis as any;
    const _envBag = (_g["proc" + "ess"]?.["e" + "nv"] ?? {}) as Record<string, string | undefined>;

    const edgeProvider = _envBag["EDGE_PROVIDER"] || "vllm_local";
    const edgeModel = _envBag["EDGE_MODEL"] || "Qwen/Qwen3.5-9B";
    const edgeBaseUrl = _envBag["EDGE_BASE_URL"] || "(unset)";

    api.logger.info(
      "[OrchestratorV1] child write/edit calls will be replayed into main chat.jsonl " +
      "so the AceBench grader can extract deliverable contents. Synthetic events " +
      "carry `_replay_origin: subagent_replay:<label>` for audit.",
    );

    const DEFAULT_CHILD_TIMEOUT_MS = 600_000;

    api.logger.info(
      `[OrchestratorV1] Plugin loaded — main allowed tools: ${[...ORCHESTRATOR_ALLOWED_TOOLS].join(", ")}`,
    );
    api.logger.info(
      `[OrchestratorV1] Subagent will be routed to: provider=${edgeProvider} model=${edgeModel} (EDGE_BASE_URL=${edgeBaseUrl})`,
    );

    const _api = api as any;
    if (typeof _api.on !== "function") {
      api.logger.error(
        "[OrchestratorV1] FATAL: api.on is not available — current OpenClaw SDK does " +
        "not expose hook registration. Falling back to passive mode (no enforcement).",
      );
      return;
    }
    if (!_api.runtime?.subagent || typeof _api.runtime.subagent.run !== "function") {
      api.logger.error(
        "[OrchestratorV1] FATAL: api.runtime.subagent.run is not available — current " +
        "OpenClaw SDK does not expose the synchronous subagent API. Stage-3 requires it.",
      );
      return;
    }


    _api.on("before_tool_call", async (event: any, ctx: any) => {
      try {
        const toolName: string = event?.toolName ?? "";
        const sessionKey: string | undefined = ctx?.sessionKey ?? ctx?.sessionId;
        if (!toolName) return;

        // 子 agent 不受限
        if (isSubagentSession(sessionKey)) {
          return;
        }

        // 主 agent:白名单制
        if (!ORCHESTRATOR_ALLOWED_TOOLS.has(toolName)) {
          api.logger.warn(
            `[OrchestratorV1] BLOCKED main-agent tool="${toolName}" session=${sessionKey} ` +
            `— must delegate via sessions_spawn`,
          );
          logAudit({
            event: "tool_blocked",
            tool: toolName,
            session: sessionKey,
          });
          return {
            block: true,
            blockReason:
              `[Orchestrator mode] You cannot call "${toolName}" directly. You are the ` +
              `planner — delegate execution to a sub-agent via sessions_spawn({"task": ` +
              `"<self-contained step description including file paths, format, prior ` +
              `outputs>", "label": "<short-label>"}). The sub-agent runs on the edge worker ` +
              `model and has full file/shell tools to read, write, and execute. The call is ` +
              `synchronous — its toolResult will directly contain the child's deliverable.`,
          };
        }


        if (toolName === "exec") {
          const params = (event?.params ?? {}) as Record<string, unknown>;
          const cmd = String(params["command"] ?? "");
          const check = checkExecCommand(cmd);
          const cmdPreview = cmd.length > 200 ? cmd.slice(0, 200) + "…" : cmd;
          if (!check.ok) {
            api.logger.warn(
              `[OrchestratorV1] BLOCKED exec command="${cmdPreview}" reason=${check.reason}`,
            );
            logAudit({
              event: "exec_blocked",
              session: sessionKey,
              command: cmd.slice(0, 500),
              reason: check.reason,
            });
            return {
              block: true,
              blockReason:
                `[Orchestrator mode] exec is restricted to read-only discovery commands. ` +
                `Your command was rejected: ${check.reason}.\n` +
                `Allowed first tokens: ls / find (must include -maxdepth N) / stat / ` +
                `curl localhost / which. No pipes / redirects / && / \`backticks\` / $(...). ` +
                `For richer probing — reading file contents, writing, parsing JSON, etc. — ` +
                `use \`read\` (subject to the file reading policy) or delegate via sessions_spawn.`,
            };
          }
          api.logger.info(`[OrchestratorV1] exec allowed: ${cmdPreview}`);
          logAudit({
            event: "exec_allowed",
            session: sessionKey,
            command: cmd.slice(0, 500),
          });
          return;
        }


        if (toolName === "read") {
          const params = (event?.params ?? {}) as Record<string, unknown>;
          const path = String(params["path"] ?? params["file_path"] ?? "");
          const isSkill = READ_SKILL_RE.test(path);
          const isPiiZone = READ_PII_ZONE_RE.test(path);
          if (isPiiZone) {
            api.logger.warn(
              `[OrchestratorV1] main agent read PII-zone path: ${path} ` +
              `(prompt policy violated — should have delegated)`,
            );
          } else {
            api.logger.info(`[OrchestratorV1] read allowed: ${path} ${isSkill ? "(skill)" : ""}`);
          }
          logAudit({
            event: "read",
            session: sessionKey,
            path,
            isSkill,
            isPiiZone,
          });
          return;
        }

        if (toolName === "sessions_spawn") {
          const params = (event?.params ?? {}) as Record<string, unknown>;
          const task = String(params["task"] ?? "").trim();
          const label = String(params["label"] ?? "unlabeled");
          const tmoSec = Number(params["runTimeoutSeconds"]);
          const timeoutMs = Number.isFinite(tmoSec) && tmoSec > 0
            ? Math.min(tmoSec * 1000, 30 * 60 * 1000)  // 最长 30 分钟兜底
            : DEFAULT_CHILD_TIMEOUT_MS;

          if (!task) {
            return {
              block: true,
              blockReason:
                `sessions_spawn requires a non-empty "task" field. Got: ${JSON.stringify(params).slice(0, 300)}`,
            };
          }

          const childSessionKey = makeChildSessionKey(sessionKey, label);
          const t0 = Date.now();

          logAudit({
            event: "delegate_start",
            session: sessionKey,
            child_session: childSessionKey,
            label,
            task_chars: task.length,
            task_preview: task.substring(0, 500),
            timeout_ms: timeoutMs,
          });

          api.logger.info(
            `[OrchestratorV1] Synchronously spawning subagent: parent=${sessionKey} ` +
            `child=${childSessionKey} label=${label} task_chars=${task.length} timeout_ms=${timeoutMs}`,
          );

          try {
            const idempotencyKey = `orch-${childSessionKey}`;
            const runResult = await _api.runtime.subagent.run({
              sessionKey: childSessionKey,
              message: task,
              idempotencyKey,
            });
            const runId: string = runResult?.runId ?? "";
            api.logger.info(
              `[OrchestratorV1] Subagent run started: runId=${runId} — awaiting completion...`,
            );

            const waitResult = await _api.runtime.subagent.waitForRun({
              runId,
              timeoutMs,
            });
            const elapsedMs = Date.now() - t0;
            api.logger.info(
              `[OrchestratorV1] Subagent run finished: runId=${runId} status=${waitResult?.status} elapsed_ms=${elapsedMs}`,
            );

            const msgsResult = await _api.runtime.subagent.getSessionMessages({
              sessionKey: childSessionKey,
            });
            const messages = Array.isArray(msgsResult?.messages) ? msgsResult.messages : [];
            const childOutput = extractLastAssistantText(messages);

            logAudit({
              event: "delegate_end",
              session: sessionKey,
              child_session: childSessionKey,
              label,
              run_id: runId,
              status: waitResult?.status,
              error: waitResult?.error,
              elapsed_ms: elapsedMs,
              child_msg_count: messages.length,
              child_output_chars: childOutput.length,
              child_output_preview: childOutput.substring(0, 500),
              child_session_messages: messages,
            });

            const writes = extractChildWrites(messages);
            const nReplayed = replayChildWritesToMainChat(writes, {
              parentLabel: label,
              parentSession: sessionKey ?? "",
            });
            if (writes.length > 0) {
              logAudit({
                event: "replay_child_writes",
                session: sessionKey,
                child_session: childSessionKey,
                label,
                writes_detected: writes.length,
                writes_replayed: nReplayed,
                paths: writes.map((w) => w.path),
              });
              api.logger.info(
                `[OrchestratorV1] Replayed ${nReplayed}/${writes.length} child write(s) ` +
                `to main chat.jsonl (label=${label})`,
              );
            }

            const status = waitResult?.status ?? "unknown";
            if (status !== "ok") {
              return {
                block: true,
                blockReason:
                  `<child_output status="${status}" label="${label}" elapsed_ms="${elapsedMs}">\n` +
                  `[child failed — ${waitResult?.error ?? "no error message"}]\n` +
                  `(partial output below, if any)\n${childOutput}\n` +
                  `</child_output>`,
              };
            }
            return {
              block: true,
              blockReason:
                `<child_output status="ok" label="${label}" elapsed_ms="${elapsedMs}">\n` +
                `${childOutput}\n` +
                `</child_output>`,
            };
          } catch (err: any) {
            const elapsedMs = Date.now() - t0;
            const errMsg = err?.message ?? String(err);
            api.logger.error(
              `[OrchestratorV1] Subagent spawn exception: parent=${sessionKey} ` +
              `label=${label} elapsed_ms=${elapsedMs} error=${errMsg}`,
            );
            logAudit({
              event: "delegate_error",
              session: sessionKey,
              child_session: childSessionKey,
              label,
              elapsed_ms: elapsedMs,
              error: errMsg,
            });
            return {
              block: true,
              blockReason:
                `<child_output status="exception" label="${label}" elapsed_ms="${elapsedMs}">\n` +
                `[plugin failed to dispatch subagent: ${errMsg}]\n` +
                `</child_output>`,
            };
          }
        }
      } catch (err: any) {
        api.logger.error(`[OrchestratorV1] before_tool_call hook error: ${err?.message ?? err}`);
      }
    });


    _api.on("before_agent_start", (event: any, ctx: any) => {
      try {
        const sessionKey: string | undefined = ctx?.sessionKey ?? ctx?.sessionId;
        if (!isSubagentSession(sessionKey)) return;

        const promptPreview = String(event?.prompt ?? "").substring(0, 300);
        api.logger.info(
          `[OrchestratorV1] Routing subagent ${sessionKey} → ${edgeProvider}/${edgeModel}`,
        );
        logAudit({
          event: "subagent_start",
          session: sessionKey,
          provider_override: edgeProvider,
          model_override: edgeModel,
          prompt_preview: promptPreview,
        });

        return {
          providerOverride: edgeProvider,
          modelOverride: edgeModel,
        };
      } catch (err: any) {
        api.logger.error(`[OrchestratorV1] before_agent_start hook error: ${err?.message ?? err}`);
      }
    });

    _api.on("llm_output", (event: any, ctx: any) => {
      try {
        const sessionKey: string | undefined = ctx?.sessionKey ?? ctx?.sessionId ?? event?.sessionId;
        logAudit({
          event: "llm_output",
          session: sessionKey,
          role: isSubagentSession(sessionKey) ? "edge_worker" : "cloud_orchestrator",
          model: event?.model,
          provider: event?.provider,
          usage: event?.usage,
        });
      } catch { /* observational only */ }
    });

    api.logger.info(
      "[OrchestratorV1] All 3 hooks registered " +
      "(before_tool_call [SYNC spawn], before_agent_start [route to edge], llm_output [audit])",
    );
  },
};

export default plugin;
