"""胶片数字修复工作底稿"""

from . import scan_coverage, cut_order, damage_review, crop_review, render_lineage
from .contract import Row

PIPELINES = {
    "scan-coverage": scan_coverage.build_facts,
    "cut-order": cut_order.build_facts,
    "damage-review": damage_review.build_facts,
    "crop-review": crop_review.build_facts,
    "render-lineage": render_lineage.build_facts,
}

def process(pipeline: str, data: Row) -> list[Row]:
    """Validate and transform an independent input dataset into keyed facts."""
    if pipeline not in PIPELINES:
        raise ValueError(f"unknown pipeline: {pipeline}")
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    return PIPELINES[pipeline](data)
