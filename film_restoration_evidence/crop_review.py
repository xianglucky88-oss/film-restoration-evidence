from __future__ import annotations

from .contract import Row, apply_rows, area, intersection_area, rectangle


def transform(row: Row) -> Row:
    """以操作员标注的矩形画面核对导出裁切的有效画面损失和片边保留。"""
    picture = rectangle(row, "picture")
    export = rectangle(row, "export")
    shared = intersection_area(picture, export)
    return {"lost_picture_px": area(picture) - shared, "retained_border_px": area(export) - shared}


def build_facts(data: Row) -> list[Row]:
    return apply_rows(data, transform)
