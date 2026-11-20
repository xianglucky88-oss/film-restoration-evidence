from __future__ import annotations

from json import dumps as json_dumps

from .contract import (
    Row,
    apply_rows,
    choice,
    integer,
    merge_spans,
    number,
    read_array,
    read_rows,
    rectangle,
)
from .geometry import WeightGrid, contains, inter_area, subtract, union_cells

EPS = 1e-9

# Screening-frame records declare a device and a viewing table. The older export-crop
# records use picture/export; one record may not mix the two schemas.
SCREENING_KEYS = {"device", "views"}
EXPORT_KEYS = {"picture", "export"}

DAMAGE_STATES = {"suspected", "confirmed", "excluded"}


def transform(row: Row) -> Row:
    """以操作员标注的矩形画面核对导出裁切，或评估固定放映框。"""
    if SCREENING_KEYS & set(row):
        if EXPORT_KEYS & set(row):
            raise ValueError("crop-review record mixes export crop and screening-frame fields")
        return _screening_transform(row)
    return _export_transform(row)


def _export_transform(row: Row) -> Row:
    from .contract import area, intersection_area

    picture = rectangle(row, "picture")
    export = rectangle(row, "export")
    shared = intersection_area(picture, export)
    return {"lost_picture_px": area(picture) - shared, "retained_border_px": area(export) - shared}


def build_facts(data: Row) -> list[Row]:
    return apply_rows(data, transform)


# --------------------------------------------------------------------------- 输入解析


def _clean(value: float) -> int | float:
    rounded = round(float(value), 6)
    return int(rounded) if rounded.is_integer() else rounded


def _clean_rect(rect: tuple[float, float, float, float]) -> list[int | float]:
    return [_clean(value) for value in rect]


def _clean_rects(rects: list[tuple[float, float, float, float]]) -> list[list[int | float]]:
    return [_clean_rect(rect) for rect in sorted(set(rects))]


def _canonical(value: object) -> str:
    return json_dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _dedupe(
    rects: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    seen: dict[str, tuple[float, float, float, float]] = {}
    for rect in rects:
        seen.setdefault(_canonical(_clean_rect(rect)), rect)
    return [seen[key] for key in sorted(seen)]


def _frame_range(value: object, total: int, field: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field} must be a [start, end) pair")
    start = integer({"value": value[0]}, "value")
    end = integer({"value": value[1]}, "value", minimum=start + 1)
    if end > total:
        raise ValueError(f"{field} must stay inside 0..{total}")
    return start, end


def _rect_list(row: Row, field: str) -> list[tuple[float, float, float, float]]:
    value = row.get(field, [])
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array of rectangles")
    return [rectangle({"rect": item}, "rect") for item in value]


def _aspect(device: Row) -> float:
    value = device["aspect"]
    if isinstance(value, list):
        if len(value) != 2:
            raise ValueError("aspect must be a number or a [width, height] pair")
        width = number({"value": value[0]}, "value", minimum=0)
        height = number({"value": value[1]}, "value", minimum=0)
        if width <= 0 or height <= 0:
            raise ValueError("aspect ratio must be positive")
        return width / height
    ratio = number(device, "aspect", minimum=0)
    if ratio <= 0:
        raise ValueError("aspect must be positive")
    return ratio


def _minimum_size(device: Row) -> tuple[float, float]:
    if "min_size" in device:
        if "min_width" in device or "min_height" in device:
            raise ValueError("use either min_size or min_width/min_height, not both")
        pair = read_array(device, "min_size")
        if len(pair) != 2:
            raise ValueError("min_size must be [width, height]")
        return (
            number({"value": pair[0]}, "value", minimum=EPS),
            number({"value": pair[1]}, "value", minimum=EPS),
        )
    if "min_width" not in device or "min_height" not in device:
        raise ValueError("device must declare min_width and min_height (or min_size)")
    return (
        number(device, "min_width", minimum=EPS),
        number(device, "min_height", minimum=EPS),
    )


def _parse_views(row: Row, total: int) -> list[Row]:
    views = []
    for view in read_rows(row, "views"):
        start = integer(view, "start")
        end = integer(view, "end", minimum=start + 1)
        if end > total:
            raise ValueError("view frames must stay inside the film")
        raw_marks = view.get("damage", [])
        if not isinstance(raw_marks, list) or any(not isinstance(mark, dict) for mark in raw_marks):
            raise ValueError("damage must contain JSON objects")
        marks = []
        for mark in raw_marks:
            if "frames" in mark:
                mark_start, mark_end = _frame_range(mark["frames"], total, "frames")
            else:
                mark_start, mark_end = start, end
            if mark_end <= start or mark_start >= end:
                raise ValueError("damage mark frames must overlap their view")
            marks.append(
                {
                    "rect": rectangle(mark, "rect"),
                    "state": choice(mark, "state", DAMAGE_STATES),
                    "start": max(start, mark_start),
                    "end": min(end, mark_end),
                }
            )
        views.append(
            {
                "start": start,
                "end": end,
                "picture": rectangle(view, "picture"),
                "keep": _rect_list(view, "keep"),
                "marks": marks,
            }
        )
    return views


# --------------------------------------------------------------------------- 同质帧段


def _segment(views: list[Row], start: int, end: int) -> Row:
    """一段帧共同的画面、必留区域与损伤状态。

    一条记录可覆盖多帧；多条记录可覆盖同一帧（重复看片）。这里按帧区间汇总，
    同一帧的重叠画面取并集，重复记录不会把同一块画面算两遍。
    """
    active = [view for view in views if view["start"] <= start and end <= view["end"]]
    keep = _dedupe([rect for view in active for rect in view["keep"]])
    marks = [
        mark
        for view in active
        for mark in view["marks"]
        if mark["start"] <= start and end <= mark["end"]
    ]
    confirmed = union_cells([mark["rect"] for mark in marks if mark["state"] == "confirmed"])
    excluded = union_cells([mark["rect"] for mark in marks if mark["state"] == "excluded"])
    suspected = union_cells([mark["rect"] for mark in marks if mark["state"] == "suspected"])
    # A recheck either confirms or excludes a suspected scratch. Excluded means clean;
    # a confirmed mark is never silently overridden by an excluded one — that
    # disagreement is surfaced as a review conflict instead.
    suspected = subtract(subtract(suspected, confirmed), excluded)
    # Retained picture is the viewed picture inside the frame. Confirmed damage never
    # enters the frame (it is avoided outright); a pending scratch is not treated as
    # confirmed damage, so it does not shrink the counted picture — it instead keeps the
    # result from being finalized until it is rechecked.
    picture = union_cells([view["picture"] for view in active])
    return {
        "start": start,
        "end": end,
        "viewed": bool(active),
        "picture": picture,
        "keep": keep,
        "confirmed": confirmed,
        "excluded": excluded,
        "suspected": suspected,
        "confirmed_marks": _dedupe(
            [mark["rect"] for mark in marks if mark["state"] == "confirmed"]
        ),
    }


def _segments(views: list[Row], total: int) -> list[Row]:
    points = {0, total}
    for view in views:
        points.add(view["start"])
        points.add(view["end"])
        for mark in view["marks"]:
            points.add(mark["start"])
            points.add(mark["end"])
    ordered = sorted(points)
    merged: list[Row] = []
    for start, end in zip(ordered, ordered[1:]):
        if start >= end:
            continue
        segment = _segment(views, start, end)
        if merged:
            previous = {
                key: value
                for key, value in merged[-1].items()
                if key not in {"start", "end"}
            }
            current = {
                key: value
                for key, value in segment.items()
                if key not in {"start", "end"}
            }
            if _canonical(previous) == _canonical(current):
                merged[-1]["end"] = end
                continue
        merged.append(segment)
    return merged


# --------------------------------------------------------------------------- 约束冲突


def _merge_by_span(entries: list[Row]) -> list[Row]:
    """把结论相同且首尾相接的条目合并，长段不逐帧重复。"""
    merged: list[Row] = []
    for entry in entries:
        if merged:
            head = {k: v for k, v in merged[-1].items() if k not in {"start", "end"}}
            tail = {k: v for k, v in entry.items() if k not in {"start", "end"}}
            if head == tail and merged[-1]["end"] == entry["start"]:
                merged[-1]["end"] = entry["end"]
                continue
        merged.append(dict(entry))
    return merged


def _conflicts(segments: list[Row]) -> list[Row]:
    """必留区域与已确认损伤无法兼顾之处，以及复检记录自相矛盾的区域。

    放映框全片固定：某一帧的必留区域与另一帧的已确认损伤只要在空间上重叠，
    就无法既保住必留区域又避开损伤——两帧同时出现在同一块固定框内。每条
    冲突给出必留区域、阻挡它的损伤，以及各自出现的帧段。
    """
    all_damage = [
        (cell, segment) for segment in segments for cell in segment["confirmed"]
    ]
    conflicts: list[Row] = []
    for segment in segments:
        for keep in segment["keep"]:
            hits = [
                (cell, other)
                for cell, other in all_damage
                if inter_area(keep, cell) > EPS
            ]
            if not hits:
                continue
            keep_frames = _merge_by_span(
                [
                    {"start": other["start"], "end": other["end"]}
                    for other in segments
                    if any(
                        _canonical(_clean_rect(rect)) == _canonical(_clean_rect(keep))
                        for rect in other["keep"]
                    )
                ]
            )
            damage_frames = _merge_by_span(
                [{"start": other["start"], "end": other["end"]} for _, other in hits]
            )
            conflicts.append(
                {
                    "start": min(keep_frames[0]["start"], damage_frames[0]["start"]),
                    "end": max(keep_frames[-1]["end"], damage_frames[-1]["end"]),
                    "reason": "keep_damage",
                    "keep": _clean_rect(keep),
                    "keep_frames": [[item["start"], item["end"]] for item in keep_frames],
                    "damage": _clean_rects(_dedupe([cell for cell, _ in hits])),
                    "damage_frames": [
                        [item["start"], item["end"]] for item in damage_frames
                    ],
                }
            )
        for mark in segment["confirmed_marks"]:
            hits = [cell for cell in segment["excluded"] if inter_area(mark, cell) > EPS]
            if hits:
                conflicts.append(
                    {
                        "start": segment["start"],
                        "end": segment["end"],
                        "reason": "review_conflict",
                        "confirmed": _clean_rect(mark),
                        "excluded": _clean_rects(hits),
                    }
                )
    return _merge_by_span(conflicts)


# --------------------------------------------------------------------------- 放映框候选


def _bbox(
    rects: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    if not rects:
        return None
    return (
        min(rect[0] for rect in rects),
        min(rect[1] for rect in rects),
        max(rect[2] for rect in rects),
        max(rect[3] for rect in rects),
    )


def _critical_heights(
    viewport: tuple[float, float, float, float],
    damage: list[tuple[float, float, float, float]],
    keep_bbox: tuple[float, float, float, float] | None,
    aspect: float,
    minimum: float,
    maximum: float,
) -> list[float]:
    """必须尝试的框高。

    保留面积只会随框包含更多画面而增加。因此最优框总可以取成“再放大就会被
    取景范围或已确认损伤挡住”的形式：某条边贴住取景范围边界或损伤边。这样的
    框高必为取景范围与损伤纵向边之间的某个间距，或为横向间距除以画幅比；
    再加上最小尺寸、必留区域给出的下限与取景上限。
    """
    vx0, vy0, vx1, vy1 = viewport
    stops_y = [vy0, vy1] + [value for rect in damage for value in (rect[1], rect[3])]
    stops_x = [vx0, vx1] + [value for rect in damage for value in (rect[0], rect[2])]
    if keep_bbox is not None:
        stops_y += [keep_bbox[1], keep_bbox[3]]
        stops_x += [keep_bbox[0], keep_bbox[2]]
    candidates = {minimum, maximum}
    for distance in _differences(stops_y):
        candidates.add(distance)
    for distance in _differences(stops_x):
        candidates.add(distance / aspect)
    return sorted(
        round(value, 9) for value in candidates if minimum - EPS <= value <= maximum + EPS
    )


def _critical_xs(
    viewport: tuple[float, float, float, float],
    damage: list[tuple[float, float, float, float]],
    keep_bbox: tuple[float, float, float, float] | None,
    weight_xs: list[float],
) -> list[float]:
    vx0, _, vx1, _ = viewport
    values = [vx0, vx1] + weight_xs + [value for rect in damage for value in (rect[0], rect[2])]
    if keep_bbox is not None:
        values += [keep_bbox[0], keep_bbox[2]]
    return sorted(set(values))


def _critical_ys(
    viewport: tuple[float, float, float, float],
    damage: list[tuple[float, float, float, float]],
    keep_bbox: tuple[float, float, float, float] | None,
    weight_ys: list[float],
) -> list[float]:
    _, vy0, _, vy1 = viewport
    values = [vy0, vy1] + weight_ys + [value for rect in damage for value in (rect[1], rect[3])]
    if keep_bbox is not None:
        values += [keep_bbox[1], keep_bbox[3]]
    return sorted(set(values))


def _starts(
    critical: list[float], size: float, low: float, high: float
) -> list[float]:
    """一个轴向上的候选起点：框边贴住关键边，或贴住取景范围边界。"""
    last = high - size
    candidates = {low, last}
    candidates.update(value for value in critical if low - EPS <= value <= last + EPS)
    candidates.update(
        value - size
        for value in critical
        if low - EPS <= value - size <= last + EPS
    )
    return sorted(round(value, 9) for value in candidates)


def _differences(values: list[float]) -> set[float]:
    ordered = sorted(set(values))
    return {
        ordered[j] - ordered[i]
        for i in range(len(ordered))
        for j in range(i + 1, len(ordered))
    }


def _weighted_picture(
    segments: list[Row],
) -> tuple[list[tuple[tuple[float, float, float, float], int]], list[float], list[float]]:
    """每块有效画面按覆盖它的已看片帧数计一次；重复看片与同帧重叠画面都不叠加。

    一条记录可覆盖多帧，同一帧也可能被多条记录重复看片；这里按区域取并集，
    不会把同一块画面算两次。重叠但不同来源的画面，按覆盖该区域的段帧数计。
    """
    viewed = [segment for segment in segments if segment["viewed"]]
    rects = [rect for segment in viewed for rect in segment["picture"]]
    if not rects:
        return [], [], []
    xs = sorted({value for rect in rects for value in (rect[0], rect[2])})
    ys = sorted({value for rect in rects for value in (rect[1], rect[3])})
    weighted = []
    for i in range(len(xs) - 1):
        cx = (xs[i] + xs[i + 1]) / 2
        for j in range(len(ys) - 1):
            cy = (ys[j] + ys[j + 1]) / 2
            frames = sum(
                segment["end"] - segment["start"]
                for segment in viewed
                if any(
                    rect[0] < cx < rect[2] and rect[1] < cy < rect[3]
                    for rect in segment["picture"]
                )
            )
            if frames:
                weighted.append(((xs[i], ys[j], xs[i + 1], ys[j + 1]), frames))
    return weighted, xs, ys


def _weighted_choice(
    viewport: tuple[float, float, float, float],
    aspect: float,
    low: float,
    high: float,
    keep_bbox: tuple[float, float, float, float] | None,
    damage: list[tuple[float, float, float, float]],
    weighted: list[tuple[tuple[float, float, float, float], int]],
    weight_xs: list[float],
    weight_ys: list[float],
) -> tuple[float, tuple[float, float, float, float]] | None:
    """在候选框位中找保留画面最多（同分取更小）的框。

    候选位置有限：框边贴住取景范围、损伤、必留区域或画面分界。保留面积
    对这些位置是分段线性的，最优解必在候选点上。
    """
    vx0, vy0, vx1, vy1 = viewport
    xs = _critical_xs(viewport, damage, keep_bbox, weight_xs)
    ys = _critical_ys(viewport, damage, keep_bbox, weight_ys)
    grid = WeightGrid(weighted)
    blocked = WeightGrid([(cell, 1) for cell in damage])
    ceiling = max(0.0, grid.integrate(viewport))

    best_score = -1.0
    best_frame: tuple[float, float, float, float] | None = None
    best_order = (0.0, 0.0, 0.0)
    for height in _critical_heights(viewport, damage, keep_bbox, aspect, low, high):
        width = aspect * height
        if width > vx1 - vx0 + EPS:
            continue
        xs_starts = _starts(xs, width, vx0, vx1)
        ys_starts = _starts(ys, height, vy0, vy1)
        area = width * height
        for x in xs_starts:
            for y in ys_starts:
                frame = (x, y, x + width, y + height)
                if keep_bbox is not None and not contains(frame, keep_bbox):
                    continue
                if blocked.occupied(frame):
                    continue
                score = grid.integrate(frame)
                order = (area, -x, -y)
                if best_frame is None or score > best_score + 1e-9 or (
                    abs(score - best_score) <= 1e-9 and order < best_order
                ):
                    best_score, best_frame, best_order = score, frame, order
                if best_score >= ceiling - EPS:
                    return best_score, best_frame
    return None if best_frame is None else (best_score, best_frame)


def _placement(
    viewport: tuple[float, float, float, float],
    aspect: float,
    min_width: float,
    min_height: float,
    keep_bbox: tuple[float, float, float, float] | None,
    damage: list[tuple[float, float, float, float]],
    weighted: list[tuple[tuple[float, float, float, float], int]] | None = None,
    weight_edges: tuple[list[float], list[float]] | None = None,
) -> tuple[float, tuple[float, float, float, float]] | None:
    """在候选尺寸与位置中找最优框；无权重时只判断是否放得下。"""
    low = max(min_height, min_width / aspect)
    high = min(viewport[3] - viewport[1], (viewport[2] - viewport[0]) / aspect)
    if low > high + EPS:
        return None
    if weighted:
        return _weighted_choice(
            viewport,
            aspect,
            low,
            high,
            keep_bbox,
            damage,
            weighted,
            *(weight_edges or ([], [])),
        )
    return _fits(viewport, aspect, low, high, keep_bbox, damage)


def _fits(
    viewport: tuple[float, float, float, float],
    aspect: float,
    low: float,
    high: float,
    keep_bbox: tuple[float, float, float, float] | None,
    damage: list[tuple[float, float, float, float]],
) -> tuple[float, tuple[float, float, float, float]] | None:
    """找一个不碰损伤、容纳必留区域的框（不优化保留面积）。"""
    vx0, vy0, vx1, vy1 = viewport
    xs = [vx0, vx1] + [value for rect in damage for value in (rect[0], rect[2])]
    ys = [vy0, vy1] + [value for rect in damage for value in (rect[1], rect[3])]
    if keep_bbox is not None:
        xs += [keep_bbox[0], keep_bbox[2]]
        ys += [keep_bbox[1], keep_bbox[3]]
    grid = WeightGrid([(cell, 1) for cell in damage])
    for height in _critical_heights(viewport, damage, keep_bbox, aspect, low, high):
        width = aspect * height
        if width > vx1 - vx0 + EPS:
            continue
        for x in _starts(xs, width, vx0, vx1):
            for y in _starts(ys, height, vy0, vy1):
                frame = (x, y, x + width, y + height)
                if keep_bbox is not None and not contains(frame, keep_bbox):
                    continue
                if grid.occupied(frame):
                    continue
                return 0.0, frame
    return None


def _choose_frame(
    segments: list[Row],
    viewport: tuple[float, float, float, float],
    aspect: float,
    min_width: float,
    min_height: float,
    keep_bbox: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    """兼顾必留区域与已确认损伤，并使已看片帧内保留有效画面最多的框。

    同分时取较小的框：没有额外画面可保时，没必要放大框去容纳更多空白。
    """
    damage = [cell for segment in segments for cell in segment["confirmed"]]
    weighted, weight_xs, weight_ys = _weighted_picture(segments)
    result = _placement(
        viewport,
        aspect,
        min_width,
        min_height,
        keep_bbox,
        damage,
        weighted=weighted,
        weight_edges=(weight_xs, weight_ys),
    )
    return None if result is None else result[1]


def _placeable(
    viewport: tuple[float, float, float, float],
    aspect: float,
    min_width: float,
    min_height: float,
    keep_bbox: tuple[float, float, float, float] | None,
    damage: list[tuple[float, float, float, float]],
) -> bool:
    return (
        _placement(viewport, aspect, min_width, min_height, keep_bbox, damage)
        is not None
    )


def _blocked_frames(
    segments: list[Row],
    viewport: tuple[float, float, float, float],
    aspect: float,
    min_width: float,
    min_height: float,
    keep_bbox: tuple[float, float, float, float] | None,
) -> list[Row]:
    """找不到可行框时，说明是设备条件无解，还是哪些损伤挡住了所有位置。"""
    total = segments[-1]["end"] if segments else 0
    if not _placeable(viewport, aspect, min_width, min_height, keep_bbox, []):
        return [
            {
                "start": segments[0]["start"] if segments else 0,
                "end": total,
                "reason": "device",
                "keep": _clean_rects(
                    [rect for segment in segments for rect in segment["keep"]]
                ),
                "detail": (
                    "取景范围、画幅比例与最小尺寸无法同时容纳必留区域"
                    if keep_bbox is not None
                    else "取景范围、画幅比例与最小尺寸无法形成任何框"
                ),
            }
        ]
    all_damage = [cell for segment in segments for cell in segment["confirmed"]]
    blockers: list[Row] = []
    dropped: set[str] = set()
    for segment in segments:
        if not segment["confirmed"]:
            continue
        keys = {_canonical(_clean_rect(cell)) for cell in segment["confirmed"]}
        if keys <= dropped:
            # Already known to be sufficient on its own; don't repeat it.
            continue
        remaining = [
            cell
            for cell in all_damage
            if _canonical(_clean_rect(cell)) not in keys
        ]
        if _placeable(viewport, aspect, min_width, min_height, keep_bbox, remaining):
            dropped |= keys
            blockers.append(
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "reason": "keep_damage" if keep_bbox is not None else "damage_blocks_frame",
                    "keep": _clean_rects(segment["keep"]),
                    "damage": _clean_rects(segment["confirmed"]),
                }
            )
    return _merge_by_span(blockers) or [
        {
            "start": segments[0]["start"] if segments else 0,
            "end": total,
            "reason": "keep_damage" if keep_bbox is not None else "damage_blocks_frame",
            "keep": _clean_rects(
                [rect for segment in segments for rect in segment["keep"]]
            ),
            "damage": _clean_rects(all_damage),
            "detail": "已确认损伤使取景范围、画幅比例与最小尺寸下无法形成符合要求的框",
        }
    ]


# --------------------------------------------------------------------------- 输出事实


def _pending(
    segments: list[Row], frame: tuple[float, float, float, float] | None
) -> list[Row]:
    """待复检划伤：既不是已确认损伤，也不是干净画面。

    全部列出，并标出哪些落在所选框内——框内的必须复核才能定案，框外的不影响
    这个框。框尚未定下时 ``inside_frame`` 为空，表示无从判断。
    """
    pending = []
    for segment in segments:
        if not segment["viewed"] or not segment["suspected"]:
            continue
        inside = (
            [cell for cell in segment["suspected"] if inter_area(cell, frame) > EPS]
            if frame is not None
            else []
        )
        pending.append(
            {
                "start": segment["start"],
                "end": segment["end"],
                "suspected": _clean_rects(segment["suspected"]),
                "inside_frame": _clean_rects(inside),
            }
        )
    return _merge_by_span(pending)


def _unviewed_ranges(segments: list[Row]) -> list[list[int]]:
    spans = [(segment["start"], segment["end"]) for segment in segments if not segment["viewed"]]
    return [[start, end] for start, end in merge_spans(spans)]


def _statuses(
    segments: list[Row],
    conflicts: list[Row],
    pending: list[Row],
    frame: tuple[float, float, float, float] | None,
) -> list[Row]:
    """按帧段给出结论；相邻同结论、同保留面积的段合并，长片不逐帧重复。"""
    conflict_spans = [(item["start"], item["end"]) for item in conflicts]
    pending_spans = [(item["start"], item["end"]) for item in pending]
    rows: list[Row] = []
    for segment in segments:
        start, end = segment["start"], segment["end"]
        if not segment["viewed"]:
            status = "unviewed"
        elif any(a <= start and end <= b for a, b in conflict_spans):
            status = "conflict"
        elif any(a <= start and end <= b for a, b in pending_spans):
            status = "pending_review"
        else:
            status = "decided"
        kept = 0.0
        if segment["viewed"] and frame is not None:
            kept = sum(inter_area(cell, frame) for cell in segment["picture"])
        row = {
            "start": start,
            "end": end,
            "status": status,
            "frames": end - start,
            "retained_picture_px": _clean(kept * (end - start)),
        }
        if (
            rows
            and rows[-1]["status"] == status
            and rows[-1]["retained_picture_px"] == row["retained_picture_px"]
            and rows[-1]["end"] == start
        ):
            rows[-1]["end"] = end
            rows[-1]["frames"] = rows[-1]["end"] - rows[-1]["start"]
        else:
            rows.append(row)
    return rows


def _screening_transform(row: Row) -> Row:
    device = row["device"]
    if not isinstance(device, dict):
        raise ValueError("device must be a JSON object")
    viewport = rectangle(device, "viewport")
    aspect = _aspect(device)
    min_width, min_height = _minimum_size(device)
    total = integer(row, "frame_count", minimum=1)
    views = _parse_views(row, total)
    if not views:
        raise ValueError("screening-frame records require at least one viewed range")
    segments = _segments(views, total)

    conflicts = _conflicts(segments)
    keep_bbox = _bbox([rect for segment in segments for rect in segment["keep"]])
    if keep_bbox is not None and not contains(viewport, keep_bbox):
        conflicts = _merge_by_span(
            conflicts
            + [
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "reason": "keep_outside_viewport",
                    "keep": _clean_rects(
                        [rect for rect in segment["keep"] if not contains(viewport, rect)]
                    ),
                    "detail": "必留区域不在取景范围内",
                }
                for segment in segments
                if any(not contains(viewport, rect) for rect in segment["keep"])
            ]
        )
    frame: tuple[float, float, float, float] | None = None
    if not conflicts:
        frame = _choose_frame(segments, viewport, aspect, min_width, min_height, keep_bbox)
        if frame is None:
            conflicts.extend(
                _blocked_frames(segments, viewport, aspect, min_width, min_height, keep_bbox)
            )

    pending = _pending(segments, frame)
    unviewed = _unviewed_ranges(segments)
    statuses = _statuses(segments, conflicts, pending, frame)
    viewed_frames = sum(
        segment["end"] - segment["start"] for segment in segments if segment["viewed"]
    )
    retained_total = sum(
        item["retained_picture_px"] for item in statuses if item["status"] != "unviewed"
    )
    return {
        "device": {
            "viewport": [_clean(value) for value in viewport],
            "aspect": _clean(aspect),
            "min_width": _clean(min_width),
            "min_height": _clean(min_height),
        },
        "frame": _clean_rect(frame) if frame is not None else None,
        "frame_size": (
            {"width": _clean(frame[2] - frame[0]), "height": _clean(frame[3] - frame[1])}
            if frame is not None
            else None
        ),
        "retained_picture_px": _clean(retained_total),
        "viewed_frames": viewed_frames,
        "unviewed_frames": total - viewed_frames,
        "segments": statuses,
        "unviewed_ranges": unviewed,
        "pending_review": pending,
        "conflicts": conflicts,
        "finalized": (
            frame is not None
            and not conflicts
            and not unviewed
            and not any(item["inside_frame"] for item in pending)
        ),
    }


# --------------------------------------------------------------------------- 任意帧复核


def lookup_frame(facts: list[Row], fact_id: str, frame: int) -> Row:
    """查询某一帧所在的同质结果段；长段无需逐帧列出。"""
    fact = next((item for item in facts if item["fact_id"] == fact_id), None)
    if fact is None or "segments" not in fact:
        raise ValueError("unknown screening-frame fact")
    for segment in fact["segments"]:
        if segment["start"] <= frame < segment["end"]:
            return {"frame": frame, "segment": segment}
    raise ValueError("frame outside the film")


def lookup_range(facts: list[Row], fact_id: str, start: int, end: int) -> Row:
    """查询一段帧；uniform 为真时整段结论相同。"""
    if end <= start:
        raise ValueError("range must be left-closed and right-open")
    touched: list[Row] = []
    for frame in range(start, end):
        segment = lookup_frame(facts, fact_id, frame)["segment"]
        if not touched or touched[-1] != segment:
            touched.append(segment)
    return {"start": start, "end": end, "uniform": len(touched) == 1, "segments": touched}
