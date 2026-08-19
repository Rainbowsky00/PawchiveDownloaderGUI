from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, TypeVar


T = TypeVar("T")


CARD_SIZE = 180
CARD_SPACING = 10


@dataclass(slots=True)
class FileProgressState:
    name: str
    current: int
    total: int | None
    speed: float = 0.0
    sample_bytes: int = 0
    sample_time: float = 0.0


def update_file_progress(
    state: FileProgressState | None,
    name: str,
    current: int,
    total: int | None,
    now: float,
) -> FileProgressState:
    if state is None:
        return FileProgressState(name, current, total, sample_bytes=current, sample_time=now)

    elapsed = now - state.sample_time
    delta = current - state.sample_bytes
    if current < state.sample_bytes:
        state.speed = 0.0
        state.sample_bytes = current
        state.sample_time = now
    elif elapsed >= 0.1 and delta > 0:
        instant_speed = delta / elapsed
        state.speed = instant_speed if state.speed <= 0 else state.speed * 0.7 + instant_speed * 0.3
        state.sample_bytes = current
        state.sample_time = now
    state.name = name
    state.current = current
    state.total = total
    return state


def choose_displayed_file(
    active: dict[str, FileProgressState],
    displayed_key: str | None,
    threshold: float = 1.2,
) -> str | None:
    if not active:
        return None
    if displayed_key not in active:
        return max(active, key=lambda key: (active[key].speed, active[key].current))

    current = active[displayed_key]
    candidate_key = max(active, key=lambda key: (active[key].speed, active[key].current))
    candidate = active[candidate_key]
    if candidate_key == displayed_key or candidate.speed <= 0:
        return displayed_key
    if current.speed <= 0 or candidate.speed >= current.speed * threshold:
        return candidate_key
    return displayed_key


def group_posts_by_year(posts: Iterable[T], day_getter: Callable[[T], str]) -> list[tuple[str, list[T]]]:
    groups: dict[str, list[T]] = {}
    for post in posts:
        day = day_getter(post)
        year = day[:4] if day[:4].isdigit() else "未知年份"
        groups.setdefault(year, []).append(post)
    known_years = sorted((year for year in groups if year != "未知年份"), reverse=True)
    if "未知年份" in groups:
        known_years.append("未知年份")
    return [(year, groups[year]) for year in known_years]


def grid_columns(available_width: int) -> int:
    return max(1, (max(0, available_width) + CARD_SPACING) // (CARD_SIZE + CARD_SPACING))


def format_speed(bytes_per_second: float) -> str:
    speed = max(0.0, float(bytes_per_second))
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if speed < 1024 or unit == "GB/s":
            return f"{speed:.1f} {unit}" if unit != "B/s" else f"{int(speed)} B/s"
        speed /= 1024
    return f"{int(bytes_per_second)} B/s"
