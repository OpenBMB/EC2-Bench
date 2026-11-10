from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import yaml
load_dotenv()
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get('EC2_DATA_ROOT', str(ROOT_DIR))).expanduser().resolve()

def parse_task_md(task_file: Path) -> dict:
    """Extract task_id, prompt, workspace_path, and automated_checks from task.md."""
    content = task_file.read_text(encoding='utf-8')
    fm_match = re.match('^---\\s*\\n(.*?)\\n---\\s*\\n(.*)', content, re.DOTALL)
    if not fm_match:
        raise ValueError(f'YAML frontmatter not found: {task_file}')
    metadata = yaml.safe_load(fm_match.group(1))
    body = fm_match.group(2)
    sections: dict[str, str] = {}
    current_section: Optional[str] = None
    lines: list[str] = []
    in_code_block = False
    for line in body.split('\n'):
        if re.match('^```', line):
            in_code_block = not in_code_block
        header = re.match('^##\\s+(.+)$', line)
        if header and (not in_code_block):
            if current_section is not None:
                sections[current_section] = '\n'.join(lines).strip()
            current_section = header.group(1)
            lines = []
        else:
            lines.append(line)
    if current_section is not None:
        sections[current_section] = '\n'.join(lines).strip()

    def strip_codeblock(raw: str) -> str:
        s = re.sub('^```[^\\n]*\\n?', '', raw.strip())
        s = re.sub('\\n?```$', '', s).strip()
        return s
    prompt = sections.get('Prompt', '').strip()
    raw_workspace = sections.get('Workspace Path', '').strip()
    workspace_path = strip_codeblock(raw_workspace)
    if not workspace_path:
        raise ValueError(f'Missing ## Workspace Path in task.md: {task_file}')
    skills_path = os.environ.get('ACEBENCH_SKILLS_PATH', str(DATA_ROOT / 'skills'))
    automated_checks = strip_codeblock(sections.get('Automated Checks', ''))
    env = strip_codeblock(sections.get('Env', ''))
    skills = strip_codeblock(sections.get('Skills', ''))
    warmup = strip_codeblock(sections.get('Warmup', ''))
    task_id = metadata.get('id', task_file.stem)
    timeout_seconds = int(metadata.get('timeout_seconds', 120))
    wp = Path(workspace_path)
    if not wp.is_absolute():
        wp = (DATA_ROOT / wp).resolve()
    workspace_path = str(wp)
    sp = Path(skills_path)
    if not sp.is_absolute():
        sp = (ROOT_DIR / sp).resolve()
    skills_path = str(sp)
    return {'task_id': task_id, 'prompt': prompt, 'workspace_path': workspace_path, 'skills_path': skills_path, 'automated_checks': automated_checks, 'env': env, 'skills': skills, 'warmup': warmup, 'timeout_seconds': timeout_seconds, 'file_path': str(task_file.resolve()), 'category': metadata.get('category') or task_file.parent.name}
