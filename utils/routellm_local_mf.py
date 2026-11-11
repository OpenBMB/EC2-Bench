"""Local inference for the OPEA E5-backed RouteLLM MF checkpoint."""
from __future__ import annotations

import json
from pathlib import Path


def score_tasks(tasks, checkpoint, encoder, scoring, *, batch_size=16, device='cpu'):
    import torch
    from huggingface_hub import snapshot_download
    from safetensors import safe_open
    from transformers import AutoConfig, AutoModel, AutoTokenizer

    tasks = list(tasks)
    if not tasks:
        return []
    checkpoint_dir = Path(checkpoint).expanduser()
    if not checkpoint_dir.is_dir():
        checkpoint_dir = Path(snapshot_download(
            repo_id=str(checkpoint),
            revision='622280cc39863fca8d25fc79625cfcadc9468f9c' if str(checkpoint) == 'OPEA/routellm-e5-base-v2' else None,
            allow_patterns=['config.json', 'model.safetensors']))
    config = json.loads((checkpoint_dir / 'config.json').read_text())
    if config.get('use_openai_embeddings', False) or config['text_dim'] != 768:
        raise ValueError('Expected an E5-backed MF checkpoint with 768-dimensional text embeddings')

    with safe_open(checkpoint_dir / 'model.safetensors', framework='pt', device='cpu') as weights:
        keys = set(weights.keys())

        def tensor(*names):
            for name in names:
                if name in keys:
                    return weights.get_tensor(name).to(device)
            raise KeyError(f'Missing checkpoint tensor: {names}')

        model_embeddings = tensor('P.weight')
        projection = tensor('text_proj.weight', 'text_proj.0.weight')
        classifier = tensor('classifier.weight', 'classifier.0.weight')
        encoder_state = {name.removeprefix('embedding_model.'): weights.get_tensor(name)
                         for name in keys if name.startswith('embedding_model.')}
    if not encoder_state:
        raise ValueError('The MF checkpoint must contain embedding_model.* weights')
    if tuple(model_embeddings.shape) != (config['num_models'], config['dim']):
        raise ValueError('P.weight shape does not match the MF configuration')
    if tuple(projection.shape) != (config['dim'], config['text_dim']):
        raise ValueError('Text projection shape does not match the MF configuration')

    encoder_path = str(Path(encoder).expanduser()) if Path(encoder).expanduser().is_dir() else encoder
    encoder_revision = 'f52bf8ec8c7124536f0efb74aca902b2995e5bcd' if encoder_path == 'intfloat/e5-base-v2' else None
    encoder_model = AutoModel.from_config(AutoConfig.from_pretrained(encoder_path, revision=encoder_revision))
    missing, unexpected = encoder_model.load_state_dict(encoder_state, strict=False)
    if any(not name.startswith('pooler.') for name in missing) or unexpected:
        raise ValueError(f'Encoder checkpoint mismatch: missing={missing}, unexpected={unexpected}')
    encoder_model.eval().to(device)
    tokenizer = AutoTokenizer.from_pretrained(encoder_path, revision=encoder_revision)
    ids = [scoring['strong_model_id'], scoring['weak_model_id']]
    paired_embeddings = torch.nn.functional.normalize(model_embeddings[ids], p=2, dim=1)
    scores = []
    with torch.no_grad():
        for start in range(0, len(tasks), batch_size):
            chunk = tasks[start:start + batch_size]
            inputs = tokenizer([prompt for _, prompt in chunk], padding=True, truncation=True,
                               max_length=scoring['max_length'], return_tensors='pt').to(device)
            hidden = encoder_model(**inputs).last_hidden_state
            mask = inputs['attention_mask'].unsqueeze(-1).float()
            prompt_embeddings = (hidden * mask).sum(1) / mask.sum(1)
            projected = prompt_embeddings @ projection.T
            logits = (paired_embeddings.unsqueeze(1) * projected.unsqueeze(0)) @ classifier.squeeze(0)
            scores.extend(torch.sigmoid(logits[0] - logits[1]).cpu().tolist())
    return [(task_id, score) for (task_id, _), score in zip(tasks, scores)]
