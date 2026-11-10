"""Human-readable duration formatting for benchmark summary JSON."""
from __future__ import annotations

def format_duration_human(seconds: float | int | None) -> str | None:
    """Format seconds as Chinese duration, e.g. '1小时7分钟51秒'."""
    if seconds is None or not isinstance(seconds, (int, float)):
        return None
    total = int(round(float(seconds)))
    if total < 0:
        return None
    (hours, rem) = divmod(total, 3600)
    (minutes, secs) = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f'{hours}小时')
    if minutes:
        parts.append(f'{minutes}分钟')
    if secs or not parts:
        parts.append(f'{secs}秒')
    return ''.join(parts)

def format_duration_compact(seconds: float | int | None) -> str | None:
    """Compact duration for terminal progress, e.g. '9m12s' or '1h07m03s'."""
    if seconds is None or not isinstance(seconds, (int, float)):
        return None
    total = int(round(float(seconds)))
    if total < 0:
        return None
    (hours, rem) = divmod(total, 3600)
    (minutes, secs) = divmod(rem, 60)
    if hours:
        return f'{hours}h{minutes:02d}m{secs:02d}s'
    if minutes:
        return f'{minutes}m{secs:02d}s'
    return f'{secs}s'

def format_duration_short(seconds: float | int | None) -> str | None:
    """Clean running-total label for progress, e.g. '2h20min', '20min', '45s'."""
    if seconds is None or not isinstance(seconds, (int, float)):
        return None
    total = int(round(float(seconds)))
    if total < 0:
        return None
    (hours, rem) = divmod(total, 3600)
    (minutes, secs) = divmod(rem, 60)
    if hours:
        return f'{hours}h{minutes:02d}min'
    if minutes:
        return f'{minutes}min'
    return f'{secs}s'

def format_elapsed_progress(seconds: float | int | None) -> str | None:
    """Compact progress label, e.g. '9m12s (552.3s)'."""
    compact = format_duration_compact(seconds)
    if compact is None:
        return None
    return f'{compact} ({float(seconds):.1f}s)'

def build_time_block(*, wall_clock_seconds: float | None, total_task_seconds: float | None) -> dict:
    return {'wall_clock_seconds': round(wall_clock_seconds, 2) if isinstance(wall_clock_seconds, (int, float)) else None, 'wall_clock_human': format_duration_human(wall_clock_seconds), 'total_task_seconds': round(total_task_seconds, 2) if isinstance(total_task_seconds, (int, float)) else None, 'total_task_seconds_human': format_duration_human(total_task_seconds)}
