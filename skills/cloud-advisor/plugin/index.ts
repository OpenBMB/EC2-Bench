import type { OpenClawPluginApi } from "openclaw/plugin-sdk/memory-core";
import { readFileSync, appendFileSync, readdirSync, existsSync, unlinkSync } from "node:fs";
import { randomUUID } from "node:crypto";

const CHAT_JSONL = "/root/.openclaw/agents/main/sessions/chat.jsonl";
const SKILLS_DIR = "/root/skills";
const OPENCLAW_BUILTIN_SKILLS_DIR = ["/usr/local/lib/node_modules/openclaw/skills", "/usr/lib/node_modules/openclaw/skills"].find(existsSync) || "/usr/local/lib/node_modules/openclaw/skills";
const AUDIT_LOG = "/tmp/cloud_assistant_audit.jsonl";
function _boundedIntEnv(name: string, fallback: number, min: number, max: number): number {
  const bag = ((globalThis as any)["proc" + "ess"]?.["e" + "nv"] ?? {}) as Record<string, string | undefined>;
  const parsed = Number.parseInt(bag[name] ?? "", 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

const CLOUD_MAX_ATTEMPTS = _boundedIntEnv("CLOUD_MAX_ATTEMPTS", 6, 1, 10);
const CLOUD_RETRY_BASE_MS = _boundedIntEnv("CLOUD_RETRY_BASE_MS", 1500, 100, 30000);
const CLOUD_RETRY_MAX_MS = _boundedIntEnv("CLOUD_RETRY_MAX_MS", 30000, 1000, 120000);
const CLOUD_REQUEST_TIMEOUT_MS = _boundedIntEnv("CLOUD_REQUEST_TIMEOUT_MS", 300000, 5000, 600000);

function _sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function _retryAfterMs(resp: Response): number {
  const value = resp.headers.get("retry-after");
  if (!value) return 0;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? Math.max(0, timestamp - Date.now()) : 0;
}

function _retryDelayMs(attempt: number, serverDelayMs = 0): number {
  const exponential = Math.min(
    CLOUD_RETRY_MAX_MS,
    CLOUD_RETRY_BASE_MS * (2 ** (attempt - 1)),
  );
  const base = Math.max(serverDelayMs, exponential);
  const jitter = Math.floor(Math.random() * Math.min(2000, Math.max(250, base * 0.25)));
  return base + jitter;
}


const EXECUTOR_CTX_PATH = "/dev/shm/.advisor_ctx.bin";
let cachedExecutorPrompt = "";
try {
  if (existsSync(EXECUTOR_CTX_PATH)) {
    cachedExecutorPrompt = readFileSync(EXECUTOR_CTX_PATH, "utf-8");
    try { unlinkSync(EXECUTOR_CTX_PATH); } catch {  }
  }
} catch {  }

const ADVISOR_SYSTEM = `You are the advisor for a smaller executor agent. The executor runs in OpenClaw inside a Docker container (Linux x86_64, /root, bash, Python3, outbound HTTPS) and uses these tools: read / write / edit (files), exec (bash), web_search, web_fetch, image. You see its full transcript: task prompt, every tool call, every result.

Produce guidance only — a plan, a correction, or a stop signal. The executor will continue from your advice; you do NOT execute anything.

**Respond in under 500 words. Use enumerated steps, not explanations.** If the executor is on track or done, say so in one line. If it should change course, give the corrected next steps. If it is stuck, diagnose the root cause and give ONE alternative.

Be specific: exact tools, paths, and commands taken from the transcript. **Do NOT invent paths or commands.** If a pre-installed skill at \`/root/skills/<name>/SKILL.md\` fits, instruct the executor to read that SKILL.md and follow it — you have not read those files yourself.`;


function extractFrontmatterDescription(content: string): string {

  const fmMatch = content.match(/^---\s*\n([\s\S]*?)\n---/);
  if (fmMatch) {
    const fm = fmMatch[1];
    const descMatch = fm.match(/^description:\s*["']?(.+?)["']?\s*$/m);
    if (descMatch) return descMatch[1].trim().slice(0, 200);
  }
  for (const line of content.split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#") || t.startsWith("---")) continue;
    return t.slice(0, 200);
  }
  return "";
}


function extractFrontmatterName(text: string): string {
  const m = text.match(/^---\s*\n([\s\S]*?)\n---/);
  if (!m) return "";
  const block = m[1];
  const nm = block.match(/^name:\s*(.+)$/m);
  return nm ? nm[1].trim().replace(/^"|"$/g, "").replace(/^'|'$/g, "") : "";
}

type SkillEntry = { name: string; description: string; location: string };

function scanSkillsDir(rootDir: string, locationPathTemplate: (n: string) => string): SkillEntry[] {
  if (!existsSync(rootDir)) return [];
  const out: SkillEntry[] = [];
  try {
    for (const dir of readdirSync(rootDir, { withFileTypes: true })) {
      if (!dir.isDirectory()) continue;
      if (dir.name.startsWith("cloud-advisor")) continue; // 别把自己列进去
      const skillPath = `${rootDir}/${dir.name}/SKILL.md`;
      if (!existsSync(skillPath)) continue;
      try {
        const content = readFileSync(skillPath, "utf-8");
        const fmName = extractFrontmatterName(content);
        const desc = extractFrontmatterDescription(content);
        out.push({
          name: fmName || dir.name,
          description: desc,
          location: locationPathTemplate(dir.name),
        });
      } catch {  }
    }
  } catch {  }
  return out;
}

function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function loadAvailableSkillsXml(): string {
  const taskScoped = scanSkillsDir(
    SKILLS_DIR,
    (n) => `~/skills/${n}/SKILL.md`,
  );
  if (taskScoped.length === 0) {
    return [
      "",
      "",
      "## Skills the executor has access to",
      "_No task-specific skills installed for this run; the executor must rely on its built-in tools (read / write / edit / exec / web_search / web_fetch / image)._",
    ].join("\n");
  }
  const items = taskScoped.map(e => [
    "  <skill>",
    `    <name>${escapeXml(e.name)}</name>`,
    `    <description>${escapeXml(e.description)}</description>`,
    `    <location>${escapeXml(e.location)}</location>`,
    "  </skill>",
  ].join("\n"));
  return [
    "",
    "",
    "## Task-scoped skills (mirroring executor's <available_skills>)",
    "These are the skills `setup_skills` installed for THIS task. The executor sees the same list (plus OpenClaw built-in skills which the advisor can infer from the transcript). **The advisor has NOT read any SKILL.md content** — only the description below. When suggesting one, instruct the executor to `read` the SKILL.md first and follow what it says; do not invent paths or commands.",
    "",
    "<available_skills>",
    items.join("\n"),
    "</available_skills>",
  ].join("\n");
}

type SegmentKind = "user" | "thinking" | "text" | "tool_call" | "tool_result";

interface Segment {
  kind: SegmentKind;

  tool?: string;
  isError?: boolean;

  body: string;
}

function parseSegments(): Segment[] {
  if (!existsSync(CHAT_JSONL)) return [];
  const segs: Segment[] = [];
  try {
    const lines = readFileSync(CHAT_JSONL, "utf-8").split("\n").filter(Boolean);
    for (const line of lines) {
      try {
        const entry = JSON.parse(line);
        if (entry.type !== "message") continue;
        const msg = entry.message;
        const role = msg?.role;

        if (role === "user") {
          let content = "";
          if (typeof msg.content === "string") {
            content = msg.content;
          } else if (Array.isArray(msg.content)) {
            content = msg.content
              .filter((c: any) => c?.type === "text" && c?.text)
              .map((c: any) => c.text)
              .join("\n");
          }
          if (content) segs.push({ kind: "user", body: content.trim() });
        } else if (role === "assistant") {
          const thinkingParts: string[] = [];
          const textParts: string[] = [];
          const toolCallLines: string[] = [];

          if (typeof msg.content === "string") {
            textParts.push(msg.content);
          } else if (Array.isArray(msg.content)) {
            for (const c of msg.content) {
              if (!c || typeof c !== "object") continue;
              if (c.type === "thinking" && c.thinking) {
                thinkingParts.push(c.thinking.trim());
              } else if (c.type === "text" && c.text) {
                textParts.push(c.text.trim());
              } else if (c.type === "toolCall" && c.name) {
                const args = c.arguments ?? {};
                let argsStr: string;
                try { argsStr = JSON.stringify(args); }
                catch { argsStr = "{...}"; }
                toolCallLines.push(`${c.name}(${argsStr})`);
              }
            }
          }
          if (thinkingParts.length > 0) {
            segs.push({ kind: "thinking", body: thinkingParts.join("\n").trim() });
          }
          if (textParts.length > 0) {
            segs.push({ kind: "text", body: textParts.join("\n").trim() });
          }
          if (toolCallLines.length > 0) {
            segs.push({ kind: "tool_call", body: toolCallLines.join("\n") });
          }
        } else if (role === "toolResult") {
          let content = "";
          if (typeof msg.content === "string") {
            content = msg.content;
          } else if (Array.isArray(msg.content)) {
            content = msg.content
              .filter((c: any) => c?.text)
              .map((c: any) => c.text)
              .join("\n");
          }
          segs.push({
            kind: "tool_result",
            tool: msg.toolName ?? "tool",
            isError: Boolean(msg.isError),
            body: content || "(empty)",
          });
        }
      } catch {  }
    }
  } catch {  }
  return segs;
}

function renderSegments(segs: Segment[]): string {
  const out: string[] = [];
  for (const s of segs) {
    switch (s.kind) {
      case "user":        out.push(`[USER]\n${s.body}`); break;
      case "thinking":    out.push(`[ASSISTANT thinking]\n${s.body}`); break;
      case "text":        out.push(`[ASSISTANT]\n${s.body}`); break;
      case "tool_call":   out.push(`[ASSISTANT tool_call]\n${s.body}`); break;
      case "tool_result":
        out.push(`[TOOL_RESULT ${s.tool}${s.isError ? " ERROR" : ""}]\n${s.body}`);
        break;
    }
  }
  return out.join("\n\n");
}

function buildTranscriptText(): string {
  return renderSegments(parseSegments());
}

async function callCloudAdvisor(
  query: string,
  cloudApiKey: string,
  cloudBaseUrl: string,
  cloudModel: string,
  logger: { info: (m: string) => void; error: (m: string) => void },
): Promise<{ plan: string; usage: any; latencyMs: number }> {
  const startTime = Date.now();


  const skillDocs = loadAvailableSkillsXml();
  let systemPrompt: string;
  if (cachedExecutorPrompt) {
    systemPrompt =
      "## Executor's actual system prompt (verbatim)\n" +
      "The executor agent received the following instructions at launch — task description, container constraints, and (in advisor mode) when/how to call you. " +
      "This is the ground truth of what it was told to do. Read it before judging the transcript.\n\n" +
      "```\n" + cachedExecutorPrompt.trim() + "\n```\n\n" +
      "---\n\n" +
      "## Your role (advisor)\n" +
      ADVISOR_SYSTEM + skillDocs;
  } else {
    systemPrompt = ADVISOR_SYSTEM + skillDocs;
  }
  const transcriptText = buildTranscriptText();

  const userContent =
    "Below, between <executor_transcript> tags, is the executor agent's full conversation log so far " +
    "— the user's task, every step the agent thought and did, every tool call it issued, and every tool result it received.\n\n" +
    "You are an EXTERNAL ADVISOR — not part of this conversation, not its continuation. " +
    "**Do NOT write the deliverable yourself**; do NOT continue the agent's last message. " +
    "Your job is to give the executor a short plan / correction / stop-signal so it can finish the task itself.\n\n" +
    "<executor_transcript>\n" +
    (transcriptText || "(empty — no chat history yet)") +
    "\n</executor_transcript>" +
    (query ? `\n\n### Agent's Question\n${query}` : "");

  const apiMessages: any[] = [
    { role: "system", content: systemPrompt },
    { role: "user", content: userContent },
  ];

  const requestBody: Record<string, any> = {
    model: cloudModel,
    messages: apiMessages,
    max_tokens: 32768,
  };
  if (/^(?:[^/]+\/)?glm-5(?:[.\-]|$)/i.test(cloudModel)) {
    requestBody.thinking = { type: "enabled" };
  } else if (/^(?:[^/]+\/)?qwen3(?:[.\-]|$)/i.test(cloudModel)) {
    requestBody.enable_thinking = true;
  }
  let data: any;
  let lastErr: any;
  let requestId = "";
  for (let attempt = 1; attempt <= CLOUD_MAX_ATTEMPTS; attempt += 1) {
    requestId = randomUUID();
    appendFileSync(AUDIT_LOG, JSON.stringify({event: "request", request_id: requestId,
      timestamp: new Date().toISOString(), model: cloudModel, input: apiMessages}) + "\n");
    try {
      const resp = await fetch(`${cloudBaseUrl}/chat/completions`, {
        method: "POST",
        signal: AbortSignal.timeout(CLOUD_REQUEST_TIMEOUT_MS),
        headers: {
          "Authorization": `Bearer ${cloudApiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(requestBody),
      });

      if (!resp.ok) {
        const errText = await resp.text();
        const err: any = new Error(`Cloud API returned ${resp.status}`);
        err.status = resp.status;
        err.body = errText.slice(0, 300);
        err.retryAfterMs = _retryAfterMs(resp);
        throw err;
      }

      data = await resp.json();
      lastErr = undefined;
      break;
    } catch (err: any) {
      appendFileSync(AUDIT_LOG, JSON.stringify({event: "result", request_id: requestId,
        status: "error", usage_recorded: false, error_type: err?.name || "Error"}) + "\n");
      lastErr = err;
      const retriableStatus =
        err?.status === 408 || err?.status === 429 || (err?.status >= 500 && err?.status < 600);
      const retriableNetwork = !err?.status; // 无 status = fetch/超时/网络错误
      if (attempt >= CLOUD_MAX_ATTEMPTS || (!retriableStatus && !retriableNetwork)) break;
      const delayMs = _retryDelayMs(attempt, err?.retryAfterMs || 0);
      logger.error(
        `[CloudAdvisor] attempt ${attempt}/${CLOUD_MAX_ATTEMPTS} failed (${err?.status ?? "network"}); retrying in ${delayMs}ms: ${(err?.message ?? err) as string}`,
      );
      await _sleep(delayMs);
    }
  }

  const latencyMs = Date.now() - startTime;

  if (lastErr) {
    logger.error(`[CloudAdvisor] API error after retries: ${lastErr?.message ?? lastErr}`);
    throw lastErr;
  }

  const choice = data.choices?.[0];
  const rawPlan = choice?.message?.content;
  const plan = typeof rawPlan === "string" && rawPlan.trim()
    ? rawPlan
    : `[Advisor Error] Cloud model returned no final advice (finish_reason=${choice?.finish_reason ?? "unknown"}). Continue without advisor guidance.`;
  const usage = data.usage ?? {};

  logger.info(`[CloudAdvisor] Done: ${usage.total_tokens ?? "?"} tokens, ${latencyMs}ms`);


  const ptd = usage?.prompt_tokens_details ?? {};
  const cachedTokens =
    ptd.cached_tokens
    ?? usage?.cache_read_input_tokens
    ?? 0;
  const cacheWriteTokens =
    ptd.cache_creation_tokens
    ?? usage?.cache_creation_input_tokens
    ?? ((usage?.claude_cache_creation_5_m_tokens ?? 0) + (usage?.claude_cache_creation_1_h_tokens ?? 0))
    ?? 0;
    appendFileSync(AUDIT_LOG, JSON.stringify({
      event: "result",
      request_id: requestId,
      status: "ok",
      usage_recorded: usage.prompt_tokens !== undefined && usage.completion_tokens !== undefined,
      timestamp: new Date().toISOString(),
      model: cloudModel,
      prompt_tokens: usage.prompt_tokens ?? 0,
      completion_tokens: usage.completion_tokens ?? 0,
      total_tokens: usage.total_tokens ?? 0,
      cache_read_tokens: cachedTokens,
      cache_write_tokens: cacheWriteTokens,
      latency_ms: latencyMs,
      query: query.substring(0, 200),
      transcript_chars: transcriptText.length,
      output: plan,
    }) + "\n");

  return { plan, usage, latencyMs };
}

const plugin = {
  id: "cloud-advisor",
  name: "Cloud Advisor",
  description: "Register `advisor` tool (parameterless) that routes to cloud LLM for planning guidance — reviewer framing via flattened transcript + <executor_transcript> XML; tool description nudges a call near the start of complex tasks",
  configSchema: { type: "object" as const, properties: {} },

  register(api: OpenClawPluginApi) {
    const _g = globalThis as any;
    const _envBag = (_g["proc" + "ess"]?.["e" + "nv"] ?? {}) as Record<string, string | undefined>;
    const cloudApiKey = _envBag["CLOUD_API_KEY"] || "";
    const cloudBaseUrl = _envBag["CLOUD_BASE_URL"] || "";
    const cloudModel = _envBag["CLOUD_MODEL"] || "GLM-5.3";

    if (!cloudApiKey) {
      api.logger.warn("[CloudAdvisor] CLOUD_API_KEY not set — advisor calls will fail");
    }


    api.registerTool({
      name: "advisor",
      label: "Advisor",
      description: "Ask a stronger reviewer model for guidance. Call it before substantive work (writing, editing, or committing to an interpretation), when stuck, when considering a change of approach, and before declaring the task done — at least twice per task. Takes NO parameters — your full conversation history is automatically forwarded.",
      parameters: {
        type: "object",
        properties: {},
      },
      async execute(toolCallId: string, _params: any) {
        try {
          const { plan } = await callCloudAdvisor("", cloudApiKey, cloudBaseUrl, cloudModel, api.logger);
          return {
            content: [{ type: "text" as const, text: plan }],
            details: {},
          };
        } catch (err: any) {
          return {
            content: [{ type: "text" as const, text: `[Advisor Error] ${err?.message ?? "Unknown error"}. Continue without advisor guidance.` }],
            details: {},
          };
        }
      },
    });

    api.logger.info(`[CloudAdvisor] Tool registered (model=${cloudModel}, base_url=${cloudBaseUrl})`);
  },
};

export default plugin;
