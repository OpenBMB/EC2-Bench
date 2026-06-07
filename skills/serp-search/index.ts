import { Type } from "@sinclair/typebox";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { jsonResult, readStringParam } from "openclaw/plugin-sdk";

const SERP_HK_ENDPOINT = "https://api.serp.hk/serp/google/search/advanced";
const SERP_GLOBAL_ENDPOINT = "https://api.serp.global/serp/google/search/advanced";
const SERPER_ENDPOINT = "https://google.serper.dev/search"; 

type SearchProvider = "serp" | "serper";

function resolveProvider(api: OpenClawPluginApi): SearchProvider {
  const fromConfig =
    api.pluginConfig && typeof api.pluginConfig.provider === "string"
      ? api.pluginConfig.provider.trim().toLowerCase()
      : "";
  const fromEnv = process.env.SEARCH_PROVIDER?.trim().toLowerCase() || "";
  const provider = fromConfig || fromEnv;
  return provider === "serper" ? "serper" : "serp";
}

function resolveApiKey(api: OpenClawPluginApi, provider: SearchProvider): string | undefined {
  const fromConfig =
    api.pluginConfig && typeof api.pluginConfig.apiKey === "string"
      ? api.pluginConfig.apiKey.trim()
      : "";
  if (fromConfig) return fromConfig;
  if (provider === "serper") {
    return process.env.SERPER_API_KEY?.trim() || process.env.SERP_API_KEY?.trim() || undefined;
  }
  return process.env.SERP_API_KEY?.trim() || undefined;
}

function resolveEndpoint(api: OpenClawPluginApi, provider: SearchProvider): string {
  if (provider === "serper") return SERPER_ENDPOINT;
  const region =
    api.pluginConfig && typeof api.pluginConfig.region === "string"
      ? api.pluginConfig.region.trim().toLowerCase()
      : "cn";
  return region === "global" ? SERP_GLOBAL_ENDPOINT : SERP_HK_ENDPOINT;
}

const SerpSearchSchema = Type.Object(
  {
    query: Type.String({ description: "The search query to look up on the web." }),
    gl: Type.Optional(
      Type.String({
        description:
          'Country code for localized results (default "CN"). Use "US" for English results.',
      }),
    ),
  },
);

function createSerpSearchTool(
  api: OpenClawPluginApi,
  apiKey: string,
  endpoint: string,
  provider: SearchProvider,
) {
  const isServer = provider === "serper";
  return {
    name: "web_search", 
    label: isServer ? "Web Search (serper.dev)" : "Web Search (serp.hk)",
    description:
      "Search the web. Returns organic results, knowledge graph, answer boxes, and related queries.",
    parameters: SerpSearchSchema,
    execute: async (_toolCallId: string, rawArgs: Record<string, unknown>) => {
      const query = readStringParam(rawArgs, "query", { required: true }); 
      if (!query) {
        return jsonResult({ error: "missing query" });
      }

      const body: Record<string, string> = { q: query }; 
      const gl = readStringParam(rawArgs, "gl");
      if (gl) body.gl = isServer ? gl.toLowerCase() : gl; // serper.dev 用小写国家码

      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (isServer) headers["X-API-KEY"] = apiKey;
      else headers.Authorization = `Bearer ${apiKey}`;

      const res = await fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(30_000),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        const label = isServer ? "serper.dev" : "serp.hk";
        return jsonResult({ error: `${label} API error (${res.status})`, detail: text });
      }

      const data = (await res.json()) as Record<string, unknown>;

      if (!isServer && typeof data.code === "number" && data.code !== 0) {
        return jsonResult({ error: data.msg || "serp.hk error", code: data.code });
      }

      // serp.hk 把结果包在 data.result 里；serper.dev 直接放顶层
      const result = (isServer ? data : (data.result ?? data)) as Record<string, unknown>;

      const organic = Array.isArray(result.organic)
        ? (result.organic as Array<Record<string, unknown>>).slice(0, 8).map((r) => ({
            title: r.title,
            link: r.link,
            snippet: r.snippet,
            source: r.source, 
          }))
        : [];

      const output: Record<string, unknown> = { query, organic };

      const knowledgeGraph = isServer ? result.knowledgeGraph : result.knowledge_graph;
      const answerBox = isServer ? result.answerBox : result.answer_box;
      const topStories = isServer ? result.topStories : result.top_stories;

      if (knowledgeGraph) output.knowledge_graph = knowledgeGraph;
      if (answerBox) output.answer_box = answerBox;
      if (Array.isArray(topStories) && topStories.length > 0) {
        output.top_stories = (topStories as Array<Record<string, unknown>>).slice(0, 5);
      }

      return jsonResult(output);
    },
  };
}

const plugin = {
  id: "serp-search",
  name: "SERP Search Plugin",
  description: "Google search via serp.hk proxy (works in China without VPN)",
  register(api: OpenClawPluginApi) {
    const provider = resolveProvider(api); 
    const apiKey = resolveApiKey(api, provider);
    if (!apiKey) {
      const keyHint = provider === "serper" ? "SERPER_API_KEY" : "SERP_API_KEY";
      api.logger.warn(
        `serp-search: no API key. Set plugins.entries.serp-search.config.apiKey or ${keyHint} env var.`,
      );
      return;
    }
    const endpoint = resolveEndpoint(api, provider);
    api.logger.info(
      `serp_search: registered (provider=${provider}, endpoint=${new URL(endpoint).hostname})`,
    );
    api.registerTool(createSerpSearchTool(api, apiKey, endpoint, provider));
  },
};

export default plugin;
