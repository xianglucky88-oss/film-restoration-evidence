from __future__ import annotations

from .contract import Row, apply_rows, integer, read_array, text


def transform(row: Row) -> Row:
    """对已知方向和原片起点的单段扫描列出对应帧及内部缺口。"""
    origin = integer(row, "source_origin")
    length = integer(row, "frame_count", minimum=1)
    direction = text(row, "direction")
    if direction not in {"forward", "reverse"}:
        raise ValueError("unknown scan direction")
    frames = [integer({"frame": x}, "frame") for x in read_array(row, "captured_indices")]
    if len(set(frames)) != len(frames) or any(x >= length for x in frames):
        raise ValueError("duplicate or out-of-range scan index")
    mapping = {i: origin + (i if direction == "forward" else length - i - 1) for i in range(length)}
    return {"source_frames": [mapping[i] for i in sorted(frames)],
            "missing_source_frames": sorted(mapping[i] for i in mapping.keys() - set(frames))}


def build_facts(data: Row) -> list[Row]:
    return apply_rows(data, transform)
