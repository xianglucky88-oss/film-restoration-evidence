from __future__ import annotations

from .contract import Row, apply_rows, flag, integer, read_rows


def transform(row: Row) -> Row:
    """把同一帧的已复检损伤与仅初检存疑的帧分别列出。"""
    suspected = set()
    confirmed = set()
    for mark in read_rows(row, "marks"):
        frame = integer(mark, "frame")
        if flag(mark, "review_confirmed"):
            confirmed.add(frame)
        else:
            suspected.add(frame)
    return {"confirmed_frames": sorted(confirmed), "review_queue": sorted(suspected - confirmed)}


def build_facts(data: Row) -> list[Row]:
    return apply_rows(data, transform)
