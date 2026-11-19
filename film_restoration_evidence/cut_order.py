from __future__ import annotations

from .contract import Row, apply_rows, integer, read_rows, text


def transform(row: Row) -> Row:
    """为单版已确认镜头顺序计算片上帧位和各镜头的出现序号。"""
    shots = []
    counts = {}
    cursor = 0
    for shot in read_rows(row, "shots"):
        tag = text(shot, "shot")
        counts[tag] = counts.get(tag, 0) + 1
        end = cursor + integer(shot, "frames", minimum=1)
        shots.append({"shot": tag, "occurrence": counts[tag], "start": cursor, "end": end})
        cursor = end
    return {"total_frames": cursor, "shots": shots}


def build_facts(data: Row) -> list[Row]:
    return apply_rows(data, transform)
