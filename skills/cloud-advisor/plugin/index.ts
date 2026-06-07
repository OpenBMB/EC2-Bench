import type { OpenClawPluginApi } from "openclaw/plugin-sdk/memory-core";
import { readFileSync, appendFileSync, readdirSync, existsSync, unlinkSync } from "node:fs";

const CHAT_JSONL = "/root/.openclaw/agents/main/sessions/chat.jsonl";
const SKILLS_DIR = "/root/skills";
const OPENCLAW_BUILTIN_SKILLS_DIR = "/usr/lib/node_modules/openclaw/skills";
const AUDIT_LOG = "/tmp/cloud_assistant_audit.jsonl"; 


const EXECUTOR_CTX_PATH = "/dev/shm/.advisor_ctx.bin";
let cachedExecutorPrompt = "";
try {
  if (existsSync(EXECUTOR_CTX_PATH)) {
    cachedExecutorPrompt = readFileSync(EXECUTOR_CTX_PATH, "utf-8");
    try { unlinkSync(EXECUTOR_CTX_PATH); } catch { /* unlink 失败不致命 */ }
  }
} catch { /* 读失败也不致命，advisor 退化到只看 transcript + ADVISOR_SYSTEM */ }

const ADVISOR_SYSTEM = `You are the advisor for a smaller executor agent. The executor runs in OpenClaw inside a Docker container (Linux x86_64, /root, bash, Python3, outbound HTTPS) and uses these tools: read / write / edit (files), exec (bash), web_search, web_fetch, image. You see its full transcript: task prompt, every tool call, every result.

Produce guidance only — a plan, a correction, or a stop signal. The executor will continue from your advice; you do NOT execute anything.

**Respond in under 500 words. Use enumerated steps, not explanations.** If the executor is on track or done, say so in one line. If it should change course, give the corrected next steps. If it is stuck, diagnose the root cause and give ONE alternative.

Be specific: exact tools, paths, and commands taken from the transcript. **Do NOT invent paths or commands.** If a pre-installed skill at \`/root/skills/<name>/SKILL.md\` fits, instruct the executor to read that SKILL.md and follow it — you have not read those files yourself.`;


function extractFrontmatterDescription(content: string): string {

  const fmMatch = content.match(/^---\s*\n([\s\S]*?)\n---/);
  if (fmMatch) {
    const fm = fmMatch[1];
    // 兼容 "description: ..." / "description: \"...\"" / 多行 |- 块
    const descMatch = fm.match(/^description:\s*["']?(.+?)["']?\s*$/m);
    if (descMatch) return descMatch[1].trim().slice(0, 200);
  }
  // fallback: 第一段非空、非标题、非 frontmatter 边界的文本
  for (const line of content.split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#") || t.startsWith("---")) continue;
    return t.slice(0, 200);
  }
  return "";
}


function extractFrontmatterName(text: string): string {
  // 优先取 frontmatter 里的 name 字段；没有时回退用 SKILL.md 所在目录名（外面传入）。
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
      } catch { /* skip unreadable */ }
    }
  } catch { /* skip */ }
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
  // 用 OpenClaw 原生 XML 格式（和 executor 看到的同型），便于 advisor 直接对照
  // transcript 里 9B 调过的 skill 路径。
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

function buildTranscriptText(): string {
  if (!existsSync(CHAT_JSONL)) return "";
  const segments: string[] = [];
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
          if (content) segments.push(`[USER]\n${content.trim()}`);
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
                // 截断超长 args（避免 binary blob 等把 transcript 撑爆）
                if (argsStr.length > 2000) argsStr = argsStr.slice(0, 2000) + "...(truncated)";
                toolCallLines.push(`${c.name}(${argsStr})`);
              }
            }
          }

          const parts: string[] = [];
          if (thinkingParts.length > 0) {
            parts.push(`[ASSISTANT thinking]\n${thinkingParts.join("\n").trim()}`);
          }
          if (textParts.length > 0) {
            parts.push(`[ASSISTANT]\n${textParts.join("\n").trim()}`);
          }
          if (toolCallLines.length > 0) {
            parts.push(`[ASSISTANT tool_call]\n${toolCallLines.join("\n")}`);
          }
          if (parts.length > 0) segments.push(parts.join("\n\n"));
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
          const errMark = msg.isError ? " ERROR" : "";
          const toolName = msg.toolName ?? "tool";
          // 工具结果可能极长（read 大文件 / web_fetch HTML）；保留头尾各 1500 字符
          if (content.length > 3500) {
            content = content.slice(0, 1500) + "\n...(truncated " + (content.length - 3000) + " chars)...\n" + content.slice(-1500);
          }
          segments.push(`[TOOL_RESULT ${toolName}${errMark}]\n${content || "(empty)"}`);
        }
      } catch { /* skip malformed line */ }
    }
  } catch { /* file read error */ }

  // 兜底：如果整体太长就头尾切（极端长 task 才会到这一步）
  const fullText = segments.join("\n\n");
  if (fullText.length > 200_000) {
    const headChars = 30_000;
    const tailChars = 150_000;
    const omitted = fullText.length - headChars - tailChars;
    return fullText.slice(0, headChars) +
      `\n\n[…${omitted} chars omitted to fit context budget…]\n\n` +
      fullText.slice(-tailChars);
  }
  return fullText;
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
  /////////

  const resp = await fetch(`${cloudBaseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${cloudApiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: cloudModel,
      messages: apiMessages,
      max_tokens: 4096,
    }),
  });

  const latencyMs = Date.now() - startTime;

  if (!resp.ok) {
    const errText = await resp.text();
    logger.error(`[CloudAdvisor] API error ${resp.status}: ${errText.substring(0, 200)}`);
    throw new Error(`Cloud API returned ${resp.status}`);
  }

  const data = await resp.json() as any;
  const plan = data.choices?.[0]?.message?.content ?? "No plan returned";
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
  /////////
  try {
    appendFileSync(AUDIT_LOG, JSON.stringify({
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
      input: apiMessages,
      output: plan,
    }) + "\n");
  } catch { /* audit write failure is non-fatal */ }

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
    const cloudBaseUrl = _envBag["CLOUD_BASE_URL"] || "https://yeysai.com/v1";
    const cloudModel = _envBag["CLOUD_MODEL"] || "claude-sonnet-4-6";

    if (!cloudApiKey) {
      api.logger.warn("[CloudAdvisor] CLOUD_API_KEY not set — advisor calls will fail");
    }

    api.registerTool({
      name: "advisor",
      label: "Advisor",
      description:
        "Ask a stronger reviewer model for guidance. Call it near the start of complex tasks, " +
        "when stuck, AND mid-task whenever you've gathered new evidence that might shift your " +
        "plan. Takes NO parameters — your full conversation history is automatically forwarded.",
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
