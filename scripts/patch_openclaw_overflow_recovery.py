"""Normalize OpenAI-compatible errors and preserve bounded compaction recovery."""
from __future__ import annotations
import os
import pathlib
import sys
MARKER = '__LLM_OVERFLOW_RECOVERY_SIGNAL_PATCH__'
SEARCH_ROOTS = (pathlib.Path('/usr/local/lib/node_modules'), pathlib.Path('/usr/lib/node_modules'), pathlib.Path('/opt/node_modules'), pathlib.Path('/root/.local/share/openclaw'), pathlib.Path('/app'))

def _find_provider() -> pathlib.Path | None:
    override = os.environ.get('OPENCLAW_PROVIDER_FILE')
    if override:
        return pathlib.Path(override)
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        hits = list(root.rglob('providers/openai-completions.js'))
        if hits:
            return hits[0]
    return None

def main() -> int:
    target = _find_provider()
    if target is None or not target.is_file():
        print('[patch-overflow-recovery] openai-completions.js not found', file=sys.stderr)
        return 4
    source = target.read_text(encoding='utf-8')
    if MARKER in source:
        print(f'[patch-overflow-recovery] already patched: {target}', file=sys.stderr)
        return 0
    import_anchor = 'import { transformMessages } from "./transform-messages.js";'
    import_inject = import_anchor + '\n/* __LLM_OVERFLOW_RECOVERY_SIGNAL_PATCH__ */\nlet __llmOverflowRecovery = null;\nconst __llmIsContextOverflow = (status, body) => {\n    if (status !== 400 && status !== 413) return false;\n    const text = typeof body === "string" ? body : JSON.stringify(body ?? "");\n    return /(?:maximum\\s+context\\s+length|context\\s+(?:length|window).*(?:exceed|overflow)|prompt\\s+too\\s+large)/i.test(text);\n};\nconst __llmArmOverflowRecovery = (model, signal) => {\n    if (!signal) return;\n    __llmOverflowRecovery = { signal, modelId: model?.id ?? null, baseUrl: model?.baseUrl ?? null, remaining: 6, expiresAt: Date.now() + 30000 };\n};\nconst __llmConsumeOverflowRecoverySignal = (model, signal) => {\n    const recovery = __llmOverflowRecovery;\n    if (!signal?.aborted || !recovery || recovery.signal !== signal || recovery.modelId !== (model?.id ?? null) || recovery.baseUrl !== (model?.baseUrl ?? null) || Date.now() >= recovery.expiresAt) return false;\n    recovery.remaining -= 1;\n    if (recovery.remaining <= 0) __llmOverflowRecovery = null;\n    return true;\n};'
    client_anchor = '            const client = createClient(model, context, apiKey, options?.headers);'
    client_inject = '            const client = createClient(model, context, apiKey, options?.headers, (data) => { if (__llmIsContextOverflow(data && data.status, data && data.body)) __llmArmOverflowRecovery(model, options?.signal); });'
    signature_anchor = 'function createClient(model, context, apiKey, optionsHeaders) {'
    signature_inject = 'function createClient(model, context, apiKey, optionsHeaders, __llmOverflowSink) {'
    headers_anchor = '        defaultHeaders: headers,\n    });'
    headers_inject = '        defaultHeaders: headers,\n        ...(__llmOverflowSink ? { fetch: async (input, init) => {\n            const response = await globalThis.fetch(input, init);\n            let sdkResponse = response;\n            if (!response.ok) {\n                try {\n                    const body = await response.clone().text();\n                    let errorObject = null;\n                    try { errorObject = JSON.parse(body); } catch (e) { }\n                    if (__llmIsContextOverflow(response.status, body)) __llmOverflowSink({ status: response.status, body });\n                    if (errorObject && typeof errorObject === "object" && !Array.isArray(errorObject) && !Object.prototype.hasOwnProperty.call(errorObject, "error")) {\n                        const normalizedHeaders = new Headers(response.headers);\n                        normalizedHeaders.delete("content-length");\n                        normalizedHeaders.delete("content-encoding");\n                        normalizedHeaders.set("content-type", "application/json");\n                        sdkResponse = new Response(JSON.stringify({ error: errorObject }), { status: response.status, statusText: response.statusText, headers: normalizedHeaders });\n                    }\n                } catch (e) { }\n            }\n            return sdkResponse;\n        } } : {}),\n    });'
    create_candidates = ('            const openaiStream = await client.chat.completions.create(params, { signal: options?.signal });', '            let openaiStream = await client.chat.completions.create(params, { signal: options?.signal });')
    create_anchors = [anchor for anchor in create_candidates if source.count(anchor) == 1]
    if len(create_anchors) != 1:
        print(f'[patch-overflow-recovery] create anchor count is {len(create_anchors)}', file=sys.stderr)
        return 4
    create_anchor = create_anchors[0]
    create_inject = '            if (__llmConsumeOverflowRecoverySignal(model, options?.signal)) options = { ...options, signal: undefined };\n' + create_anchor
    replacements = (('import', import_anchor, import_inject), ('client', client_anchor, client_inject), ('client-signature', signature_anchor, signature_inject), ('client-fetch', headers_anchor, headers_inject), ('create', create_anchor, create_inject))
    for (name, anchor, replacement) in replacements:
        count = source.count(anchor)
        if count != 1:
            print(f'[patch-overflow-recovery] anchor {name} not unique ({count})', file=sys.stderr)
            return 4
        source = source.replace(anchor, replacement, 1)
    target.write_text(source, encoding='utf-8')
    print(f'[patch-overflow-recovery] patched {target}', file=sys.stderr)
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
