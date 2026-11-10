import { Type } from "@sinclair/typebox";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

const SERPER_ENDPOINT = "https://google.serper.dev/search";

function resolveApiKey(api: OpenClawPluginApi): string | undefined {
  const fromConfig =
    api.pluginConfig && typeof api.pluginConfig.apiKey === "string"
      ? api.pluginConfig.apiKey.trim()
      : "";
  return fromConfig || process.env.SERPER_API_KEY?.trim() || undefined;
}

function readStringArg(
  rawArgs: Record<string, unknown>,
  name: string,
  options: { required?: boolean } = {},
): string | undefined {
  const value = rawArgs[name];
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || undefined;
  }
  if (options.required) {
    return undefined;
  }
  return undefined;
}

function jsonResult(payload: Record<string, unknown>) {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, 2),
      },
    ],
    details: payload,
  };
}

const SerperSearchSchema = Type.Object(
  {
    q: Type.Optional(Type.String({ description: "Search query string." })),
    query: Type.Optional(
      Type.String({ description: "Alias for q, compatible with OpenClaw web_search." }),
    ),
    url: Type.Optional(
      Type.String({
        description:
          "Compatibility alias for a search URL. The q or query parameter is used as the search query.",
      }),
    ),
    maxChars: Type.Optional(
      Type.Integer({
        minimum: 1,
        description: "Accepted for web_search compatibility; the result count is bounded by the plugin.",
      }),
    ),
    gl: Type.Optional(
      Type.String({
        description:
          'Country code for localized results (for example "cn" or "us").',
      }),
    ),
  },
  { additionalProperties: false },
);

function queryFromUrl(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.searchParams.get("q")?.trim()
      || url.searchParams.get("query")?.trim()
      || undefined;
  } catch {
    return undefined;
  }
}

function createSerperSearchTool(
  apiKey: string,
  toolName: "web_search" | "serper_search",
) {
  return {
    name: toolName,
    label: "Google Search (Serper)",
    description:
      "Search Google via Serper. Returns organic results, knowledge graph, answer boxes, and related queries.",
    parameters: SerperSearchSchema,
    execute: async (_toolCallId: string, rawArgs: Record<string, unknown>) => {
      const q = readStringArg(rawArgs, "q")
        ?? readStringArg(rawArgs, "query")
        ?? queryFromUrl(readStringArg(rawArgs, "url"));
      if (!q) {
        return jsonResult({ error: "missing query" });
      }

      const body: Record<string, string> = { q };
      const gl = readStringArg(rawArgs, "gl");
      if (gl) body.gl = gl;

      const res = await fetch(SERPER_ENDPOINT, {
        method: "POST",
        headers: {
          "X-API-KEY": apiKey,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(30_000),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        return jsonResult({ error: `Serper API error (${res.status})`, detail: text });
      }

      const data = (await res.json()) as Record<string, unknown>;

      const organic = Array.isArray(data.organic)
        ? (data.organic as Array<Record<string, unknown>>).slice(0, 8).map((r) => ({
            title: r.title,
            link: r.link,
            snippet: r.snippet,
            position: r.position,
          }))
        : [];

      const output: Record<string, unknown> = { query: q, organic };

      if (data.knowledgeGraph) output.knowledgeGraph = data.knowledgeGraph;
      if (data.answerBox) output.answerBox = data.answerBox;
      if (Array.isArray(data.peopleAlsoAsk) && data.peopleAlsoAsk.length > 0) {
        output.peopleAlsoAsk = (data.peopleAlsoAsk as Array<Record<string, unknown>>).slice(0, 5);
      }
      if (Array.isArray(data.relatedSearches) && data.relatedSearches.length > 0) {
        output.relatedSearches = (data.relatedSearches as Array<Record<string, unknown>>).slice(0, 8);
      }
      if (Array.isArray(data.topStories) && data.topStories.length > 0) {
        output.topStories = (data.topStories as Array<Record<string, unknown>>).slice(0, 5);
      }

      return jsonResult(output);
    },
  };
}

const plugin = {
  id: "serper-search",
  name: "Serper Search Plugin",
  description: "Google search via Serper",
  register(api: OpenClawPluginApi) {
    const apiKey = resolveApiKey(api);
    if (!apiKey) {
      api.logger.warn(
        "serper-search: no API key. Set plugins.entries.serper-search.config.apiKey or SERPER_API_KEY env var.",
      );
      return;
    }
    api.logger.info("web_search/serper_search: registered (endpoint=google.serper.dev)");
    api.registerTool(createSerperSearchTool(apiKey, "web_search"));
    api.registerTool(createSerperSearchTool(apiKey, "serper_search"));
  },
};

export default plugin;
