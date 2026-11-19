from __future__ import annotations

from .contract import Row, apply_rows, index_rows, read_rows, span, text


def transform(row: Row) -> Row:
    """按导出清单中的明确引用追查逐段原扫描、补绘或替代素材。"""
    sources = index_rows(read_rows(row, "sources"), "ref")
    result = []
    for key, span in index_rows(read_rows(row, "spans"), "span").items():
        ref = text(span, "ref")
        source = sources.get(ref)
        kind = text(source, "kind") if source is not None else "unresolved"
        if source is not None and kind not in {"scan", "paint", "placeholder"}:
            raise ValueError("unknown material kind")
        result.append({"span": key, "ref": ref, "kind": kind})
    return {"materials": result}


def build_facts(data: Row) -> list[Row]:
    return apply_rows(data, transform)
