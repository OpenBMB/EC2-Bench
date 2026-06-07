# cloud-advisor (OpenClaw plugin)

> Internal documentation. NOT a Skill — this directory ships as an OpenClaw
> plugin. The runtime never reads this file; it is here for human reference only.
>
> This file is intentionally **not named `SKILL.md`** so that OpenClaw's skill
> auto-discovery (which scans `/root/skills/**/SKILL.md`) does not list this
> plugin in `<available_skills>`. The plugin is installed via
> `openclaw plugins install` and registered through `plugin/openclaw.plugin.json`.

## What it does

Registers a parameterless `advisor` tool. When the executor calls `advisor()`,
the plugin reads the persisted conversation transcript at
`/root/.openclaw/agents/main/sessions/chat.jsonl`, packages the head + tail
slice with the advisor system prompt, and POSTs to a cloud LLM (configured via
`CLOUD_BASE_URL` / `CLOUD_API_KEY` / `CLOUD_MODEL`).

Result is returned as the tool's output — the executor sees a short plan,
correction, or stop signal.

The advisor system prompt has no mode branching: the advisor sees the
transcript and decides whether to give a plan, a correction, or a stop signal.
Output is kept short (under ~100 words, enumerated steps, no explanations) to
avoid the executor treating the advice as a verbatim checklist to execute.

## Behavior contract (for humans reading this)

The full guidance the executor receives is defined in two places:

1. `plugin/index.ts` — the `registerTool({description: ...})` block (what the
   executor sees in its tool schema).
2. `eval/run_batch.py` — the `advisor` registry entry's `prompts.default`
   (prepended to the executor's user message).

The cloud advisor's own system prompt (`ADVISOR_SYSTEM`) lives in
`plugin/index.ts`.

## Why this isn't a Skill

OpenClaw separates two concepts:

- **Skill**: a directory under `/root/skills/<name>/` containing a `SKILL.md`.
  The runtime auto-discovers these and injects their frontmatter
  (`name` + `description`) into the executor's system prompt under
  `<available_skills>`. The body is loaded only when the executor decides to
  `read_file` it.
- **Plugin**: a node module installed via `openclaw plugins install <path>`,
  declared through `openclaw.plugin.json`. It runs as trusted code inside the
  OpenClaw process, can register tools / hooks via `OpenClawPluginApi`, and
  can read the on-disk session transcript directly.

`cloud-advisor` is the second kind. The benchmark's plugin install path
(`run_batch.py` → `setup_plugin_source` + `install_openclaw_plugin`) copies
this directory's `plugin/` subtree to `/root/openclaw_plugins/cloud-advisor/`
rather than `/root/skills/`. As a result this `README.md` never enters the
executor's prompt; it is purely for maintainers.
