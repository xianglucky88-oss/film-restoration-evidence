from __future__ import annotations

from json import dumps as json_dumps
from typing import Any

from .contract import Row, apply_rows, flag, index_rows, integer, read_rows, span, text

MATERIAL_KINDS = {"scan", "paint", "freeze", "placeholder"}
EXTENDED_KEYS = {"versions", "scans", "materials", "correspondences"}
DIRECTIONS = {"forward", "reverse"}


def _stable(value: Any) -> str:
    return json_dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# --------------------------------------------------------------------------- 旧版口径
def _legacy_transform(row: Row) -> Row:
    """按导出清单中的明确引用追查逐段原扫描、补绘或替代素材。"""
    sources = index_rows(read_rows(row, "sources"), "ref")
    result = []
    for key, item in index_rows(read_rows(row, "spans"), "span").items():
        ref = text(item, "ref")
        source = sources.get(ref)
        kind = text(source, "kind") if source is not None else "unresolved"
        if source is not None and kind not in {"scan", "paint", "placeholder"}:
            raise ValueError("unknown material kind")
        result.append({"span": key, "ref": ref, "kind": kind})
    return {"materials": result}


# --------------------------------------------------------------------------- 扩展解析
def _parse_versions(row: Row) -> dict[str, dict[str, Any]]:
    versions: dict[str, dict[str, Any]] = {}
    for version in read_rows(row, "versions"):
        label = text(version, "version")
        if label in versions:
            raise ValueError(f"duplicate version: {label}")
        shots = []
        counts: dict[str, int] = {}
        cursor = 0
        for shot in read_rows(version, "shots"):
            tag = text(shot, "shot")
            counts[tag] = counts.get(tag, 0) + 1
            occurrence = counts[tag]
            end = cursor + integer(shot, "frames", minimum=1)
            shots.append({"shot": tag, "occurrence": occurrence, "start": cursor, "end": end})
            cursor = end
        versions[label] = {"total_frames": cursor, "shots": shots}
    if not versions:
        raise ValueError("versions must not be empty")
    return versions


def _parse_scans(row: Row) -> dict[str, dict[str, Any]]:
    scans: dict[str, dict[str, Any]] = {}
    for scan in read_rows(row, "scans"):
        ref = text(scan, "ref")
        if ref in scans:
            raise ValueError(f"duplicate scan ref: {ref}")
        reel = text(scan, "reel")
        batch = text(scan, "batch")
        origin = integer(scan, "source_origin")
        length = integer(scan, "frame_count", minimum=1)
        direction = text(scan, "direction")
        if direction not in DIRECTIONS:
            raise ValueError("unknown scan direction")
        indices = []
        for value in scan["captured_indices"]:
            indices.append(integer({"captured_index": value}, "captured_index"))
        if len(set(indices)) != len(indices) or any(i >= length for i in indices):
            raise ValueError("duplicate or out-of-range scan index")
        mapping = {
            i: origin + (i if direction == "forward" else length - i - 1)
            for i in range(length)
        }
        captured = frozenset(mapping[i] for i in indices)
        scans[ref] = {
            "reel": reel,
            "batch": batch,
            "direction": direction,
            "captured": captured,
            "missing": frozenset(mapping.values()) - captured,
        }
    return scans


def _parse_materials(row: Row) -> dict[str, dict[str, Any]]:
    materials: dict[str, dict[str, Any]] = {}
    for material in read_rows(row, "materials"):
        ref = text(material, "ref")
        if ref in materials:
            raise ValueError(f"duplicate material ref: {ref}")
        kind = text(material, "kind")
        if kind not in MATERIAL_KINDS:
            raise ValueError("unknown material kind")
        parsed: dict[str, Any] = {"ref": ref, "kind": kind}
        if kind == "scan":
            parsed["scan_ref"] = text(material, "scan_ref")
            parsed["reel"] = text(material, "reel")
            parsed["batch"] = text(material, "batch")
        elif kind == "freeze":
            parsed["reel"] = text(material, "reel")
            parsed["batch"] = text(material, "batch")
            parsed["frozen_frame"] = integer(material, "frozen_frame")
            parsed["scan_ref"] = text(material, "scan_ref")
        elif kind == "paint":
            if "base_frame" in material and material["base_frame"] is not None:
                parsed["base_frame"] = integer(material, "base_frame")
                parsed["base_scan_ref"] = text(material, "base_scan_ref")
            else:
                parsed["base_frame"] = None
                parsed["base_scan_ref"] = None
            parsed["base_frame_verified"] = flag(material, "base_frame_verified")
        materials[ref] = parsed
    return materials


def _parse_spans(
    row: Row,
    versions: dict[str, dict[str, Any]],
    materials: dict[str, dict[str, Any]],
    scans: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    spans_by_version: dict[str, list[dict[str, Any]]] = {}
    for item in read_rows(row, "spans"):
        label = text(item, "version")
        if label not in versions:
            raise ValueError(f"span references unknown version: {label}")
        start, end = span(item, "start", "end")
        owner = next(
            (
                shot
                for shot in versions[label]["shots"]
                if shot["start"] <= start and end <= shot["end"]
            ),
            None,
        )
        if owner is None:
            raise ValueError("span must stay inside one shot occurrence")
        ref = text(item, "ref")
        material = materials.get(ref)
        if material is None:
            raise ValueError(f"span references unknown material: {ref}")
        frames: list[int] | None = None
        if material["kind"] == "scan":
            scan = scans[material["scan_ref"]]
            frames = [integer({"source_frame": value}, "source_frame") for value in item["source_frames"]]
            if len(frames) != end - start or len(set(frames)) != len(frames):
                raise ValueError("scan span must declare one source frame per delivered frame")
            if any(frame not in scan["captured"] and frame not in scan["missing"] for frame in frames):
                raise ValueError(f"span {item['span']} cites a frame outside its declared scan range")
        entry = {
            "version": label,
            "span": text(item, "span"),
            "start": start,
            "end": end,
            "ref": ref,
            "kind": material["kind"],
            "shot": owner["shot"],
            "occurrence": owner["occurrence"],
            "source_frames": frames,
        }
        spans_by_version.setdefault(label, []).append(entry)

    for label, version in versions.items():
        spans = sorted(spans_by_version.get(label, []), key=lambda s: (s["start"], s["end"]))
        names = [s["span"] for s in spans]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate span name in version {label}")
        cursor = 0
        for item in spans:
            if item["start"] != cursor:
                raise ValueError(f"spans in version {label} must tile every delivered frame")
            cursor = item["end"]
        if cursor != version["total_frames"]:
            raise ValueError(f"spans in version {label} must tile every delivered frame")
        spans_by_version[label] = spans
    return spans_by_version


def _scan_descriptor(material: Row, source_frame: int, scans: dict[str, dict[str, Any]]) -> Row:
    scan = scans[material["scan_ref"]]
    return {
        "kind": "scan",
        "material_ref": material["ref"],
        "scan_ref": material["scan_ref"],
        "reel": material["reel"],
        "batch": material["batch"],
        "source_frame": source_frame,
        "scan_status": "captured" if source_frame in scan["captured"] else "missing",
    }


def _build_segments(
    spans_by_version: dict[str, list[dict[str, Any]]],
    materials: dict[str, dict[str, Any]],
    scans: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    segments_by_version: dict[str, list[dict[str, Any]]] = {}
    for label, spans in spans_by_version.items():
        segments: list[dict[str, Any]] = []
        for item in spans:
            material = materials[item["ref"]]
            kind = material["kind"]
            if kind == "scan":
                run_start = item["start"]
                run_verdict: str | None = None
                for offset, source_frame in enumerate(item["source_frames"]):
                    descriptor = _scan_descriptor(material, source_frame, scans)
                    verdict = "scan_found" if descriptor["scan_status"] == "captured" else "scan_missing"
                    delivered = item["start"] + offset
                    if run_verdict is None:
                        run_verdict = verdict
                    elif verdict != run_verdict:
                        segments.append(
                            _scan_segment(item, run_start, delivered, item["source_frames"],
                                          run_verdict, materials, scans)
                        )
                        run_start = delivered
                        run_verdict = verdict
                segments.append(
                    _scan_segment(item, run_start, item["end"], item["source_frames"],
                                  run_verdict, materials, scans)
                )
            elif kind == "freeze":
                scan = scans[material["scan_ref"]]
                frozen = material["frozen_frame"]
                if frozen not in scan["captured"] and frozen not in scan["missing"]:
                    raise ValueError(f"freeze frame outside declared scan range: {material['ref']}")
                segments.append({
                    "version": label,
                    "start": item["start"],
                    "end": item["end"],
                    "shot": item["shot"],
                    "occurrence": item["occurrence"],
                    "verdict": "freeze_extended",
                    "material_ref": material["ref"],
                    "reel": material["reel"],
                    "batch": material["batch"],
                    "frozen_frame": frozen,
                    "frozen_frame_scan_status": (
                        "captured" if frozen in scan["captured"] else "missing"
                    ),
                })
            elif kind == "paint":
                base_scan_ref = material["base_scan_ref"]
                base_status = None
                if base_scan_ref is not None:
                    scan = scans[base_scan_ref]
                    base_frame = material["base_frame"]
                    if base_frame not in scan["captured"] and base_frame not in scan["missing"]:
                        raise ValueError(f"paint base frame outside declared scan range: {material['ref']}")
                    base_status = "captured" if base_frame in scan["captured"] else "missing"
                segments.append({
                    "version": label,
                    "start": item["start"],
                    "end": item["end"],
                    "shot": item["shot"],
                    "occurrence": item["occurrence"],
                    "verdict": "paint_delivered",
                    "material_ref": material["ref"],
                    "base_frame": material["base_frame"],
                    "base_scan_ref": base_scan_ref,
                    "base_frame_scan_status": base_status,
                    "base_frame_verified": material["base_frame_verified"],
                })
            else:
                segments.append({
                    "version": label,
                    "start": item["start"],
                    "end": item["end"],
                    "shot": item["shot"],
                    "occurrence": item["occurrence"],
                    "verdict": "placeholder",
                    "material_ref": material["ref"],
                })
        segments_by_version[label] = segments
    return segments_by_version


def _scan_segment(
    span: Row,
    start: int,
    end: int,
    source_frames: list[int],
    verdict: str,
    materials: dict[str, dict[str, Any]],
    scans: dict[str, dict[str, Any]],
) -> Row:
    material = materials[span["ref"]]
    return {
        "version": span["version"],
        "start": start,
        "end": end,
        "shot": span["shot"],
        "occurrence": span["occurrence"],
        "verdict": verdict,
        "material_ref": material["ref"],
        "scan_ref": material["scan_ref"],
        "reel": material["reel"],
        "batch": material["batch"],
        "source_frames": source_frames[start - span["start"]:end - span["start"]],
    }


def _material_claims(materials: dict[str, dict[str, Any]],
                     scans: dict[str, dict[str, Any]]) -> list[Row]:
    claims = []
    for ref in sorted(materials):
        material = materials[ref]
        kind = material["kind"]
        claim: Row = {"ref": ref, "kind": kind}
        if kind == "scan":
            claim.update({
                "scan_ref": material["scan_ref"],
                "reel": material["reel"],
                "batch": material["batch"],
            })
        elif kind == "freeze":
            claim.update({
                "reel": material["reel"],
                "batch": material["batch"],
                "frozen_frame": material["frozen_frame"],
                "scan_ref": material["scan_ref"],
                "frozen_frame_scan_status": (
                    "captured"
                    if material["frozen_frame"] in scans[material["scan_ref"]]["captured"]
                    else "missing"
                ),
            })
        elif kind == "paint":
            claim.update({
                "base_frame": material["base_frame"],
                "base_scan_ref": material["base_scan_ref"],
                "base_frame_verified": material["base_frame_verified"],
            })
            if material["base_scan_ref"] is not None:
                base_frame = material["base_frame"]
                claim["base_frame_scan_status"] = (
                    "captured"
                    if base_frame in scans[material["base_scan_ref"]]["captured"]
                    else "missing"
                )
        claims.append(claim)
    return claims


def _segment_sources(segment: Row) -> list[Row]:
    """段内各交付帧的来源描述；连续段去重后仍与逐帧查询结论一致。"""
    verdict = segment["verdict"]
    if verdict in {"scan_found", "scan_missing"}:
        return [
            {
                "kind": "scan",
                "material_ref": segment["material_ref"],
                "scan_ref": segment["scan_ref"],
                "reel": segment["reel"],
                "batch": segment["batch"],
                "source_frame": source_frame,
                "scan_status": "captured" if verdict == "scan_found" else "missing",
            }
            for source_frame in segment["source_frames"]
        ]
    if verdict == "freeze_extended":
        return [{
            "kind": "freeze",
            "material_ref": segment["material_ref"],
            "reel": segment["reel"],
            "batch": segment["batch"],
            "frozen_frame": segment["frozen_frame"],
            "frozen_frame_scan_status": segment["frozen_frame_scan_status"],
        }]
    if verdict == "paint_delivered":
        descriptor = {
            "kind": "paint",
            "material_ref": segment["material_ref"],
            "base_frame": segment["base_frame"],
            "base_scan_ref": segment["base_scan_ref"],
            "base_frame_verified": segment["base_frame_verified"],
        }
        if segment["base_frame_scan_status"] is not None:
            descriptor["base_frame_scan_status"] = segment["base_frame_scan_status"]
        return [descriptor]
    return [{"kind": "placeholder", "material_ref": segment["material_ref"]}]


def _parse_correspondences(row: Row, versions: dict[str, dict[str, Any]]) -> list[Row]:
    links = []
    seen: set[tuple[str, str, int, str, int]] = set()
    for link in read_rows(row, "correspondences"):
        old_version = text(link, "old_version")
        new_version = text(link, "new_version")
        if old_version not in versions or new_version not in versions:
            raise ValueError("correspondence references unknown version")
        if old_version == new_version:
            raise ValueError("correspondence must link two different versions")
        old_shot = text(link, "old_shot")
        old_occ = integer(link, "old_occurrence", minimum=1)
        new_shot = text(link, "new_shot")
        new_occ = integer(link, "new_occurrence", minimum=1)
        old_key = (old_version, old_shot, old_occ)
        new_key = (new_version, new_shot, new_occ)
        if old_key in seen or new_key in seen:
            raise ValueError("shot occurrence appears in more than one correspondence")
        seen.add(old_key)
        seen.add(new_key)
        links.append({
            "old_version": old_version,
            "old_shot": old_shot,
            "old_occurrence": old_occ,
            "new_version": new_version,
            "new_shot": new_shot,
            "new_occurrence": new_occ,
        })
    return links


def _occurrence_segments(segments: list[Row], shot: str, occurrence: int) -> list[Row]:
    return [
        segment
        for segment in segments
        if segment["shot"] == shot and segment["occurrence"] == occurrence
    ]


def _occurrence_sources(segments: list[Row], shot: str, occurrence: int) -> list[Row]:
    ordered = sorted(_occurrence_segments(segments, shot, occurrence), key=lambda s: s["start"])
    descriptors: list[Row] = []
    for segment in ordered:
        for descriptor in _segment_sources(segment):
            if not descriptors or _stable(descriptors[-1]) != _stable(descriptor):
                descriptors.append(descriptor)
    return descriptors


def _occurrence_position(version: Row, shot: str, occurrence: int) -> Row:
    owner = next(
        (item for item in version["shots"]
         if item["shot"] == shot and item["occurrence"] == occurrence),
        None,
    )
    if owner is None:
        raise ValueError("correspondence references unknown shot occurrence")
    return {"start": owner["start"], "end": owner["end"]}


def _identity(descriptors: list[Row]) -> list[str]:
    """来源身份：只看卷、批、帧与素材性质，不看清单内部的素材声明编号。"""
    return [_stable({key: value for key, value in item.items() if key != "material_ref"})
            for item in descriptors]


def _build_comparison(
    row: Row,
    versions: dict[str, dict[str, Any]],
    segments_by_version: dict[str, list[Row]],
) -> Row:
    links = _parse_correspondences(row, versions)
    linked: set[tuple[str, str, int]] = set()
    source_changed = []
    position_only = []
    for link in links:
        old_version, new_version = versions[link["old_version"]], versions[link["new_version"]]
        old_segments = segments_by_version[link["old_version"]]
        new_segments = segments_by_version[link["new_version"]]
        old_sources = _occurrence_sources(old_segments, link["old_shot"], link["old_occurrence"])
        new_sources = _occurrence_sources(new_segments, link["new_shot"], link["new_occurrence"])
        entry = {
            "old": {
                "version": link["old_version"],
                "shot": link["old_shot"],
                "occurrence": link["old_occurrence"],
                "position": _occurrence_position(old_version, link["old_shot"], link["old_occurrence"]),
                "sources": old_sources,
            },
            "new": {
                "version": link["new_version"],
                "shot": link["new_shot"],
                "occurrence": link["new_occurrence"],
                "position": _occurrence_position(new_version, link["new_shot"], link["new_occurrence"]),
                "sources": new_sources,
            },
        }
        # 剪短或放长只改变片位：重叠序位上的来源身份一致即不算换料。
        old_identity = _identity(old_sources)
        new_identity = _identity(new_sources)
        overlap = min(len(old_identity), len(new_identity))
        if old_identity[:overlap] == new_identity[:overlap]:
            position_only.append(entry)
        else:
            source_changed.append(entry)
        linked.add((link["old_version"], link["old_shot"], link["old_occurrence"]))
        linked.add((link["new_version"], link["new_shot"], link["new_occurrence"]))

    unmatched = []
    for label in sorted(versions):
        for shot in versions[label]["shots"]:
            key = (label, shot["shot"], shot["occurrence"])
            if key in linked:
                continue
            unmatched.append({
                "version": label,
                "shot": shot["shot"],
                "occurrence": shot["occurrence"],
                "position": {"start": shot["start"], "end": shot["end"]},
                "sources": _occurrence_sources(segments_by_version[label], shot["shot"], shot["occurrence"]),
            })

    order = lambda entry: _stable(entry)
    return {
        "source_changed": sorted(source_changed, key=order),
        "position_only": sorted(position_only, key=order),
        "unmatched": sorted(unmatched, key=order),
    }


def _extended_transform(row: Row) -> Row:
    versions = _parse_versions(row)
    scans = _parse_scans(row)
    materials = _parse_materials(row)
    for material in materials.values():
        for field in ("scan_ref", "base_scan_ref"):
            ref = material.get(field)
            if ref is not None and ref not in scans:
                raise ValueError(f"material references unknown scan: {ref}")
    spans_by_version = _parse_spans(row, versions, materials, scans)
    segments_by_version = _build_segments(spans_by_version, materials, scans)
    comparison = _build_comparison(row, versions, segments_by_version)
    return {
        "versions": [
            {"version": label, "total_frames": versions[label]["total_frames"],
             "shots": versions[label]["shots"]}
            for label in sorted(versions)
        ],
        "material_claims": _material_claims(materials, scans),
        "segments": [
            segment
            for label in sorted(segments_by_version)
            for segment in segments_by_version[label]
        ],
        "comparison": comparison,
    }


def transform(row: Row) -> Row:
    """追查成片每一交付帧的实际来源，并区分换料、停格、补绘与占位。"""
    if any(key in row for key in EXTENDED_KEYS):
        if "versions" not in row or "materials" not in row or "spans" not in row:
            raise ValueError("extended render-lineage records require versions, materials and spans")
        if "sources" in row:
            raise ValueError("extended render-lineage records use materials, not sources")
        return _extended_transform(row)
    return _legacy_transform(row)


def build_facts(data: Row) -> list[Row]:
    return apply_rows(data, transform)


# --------------------------------------------------------------------------- 任意帧复核
def lookup_frame(facts: list[Row], fact_id: str, version: str, delivered_frame: int) -> Row:
    """选取任意一段交付帧，返回它所在的同质来源段及该帧结论。"""
    fact = next((item for item in facts if item["fact_id"] == fact_id), None)
    if fact is None or "segments" not in fact:
        raise ValueError("unknown extended render-lineage fact")
    for segment in fact["segments"]:
        if segment["version"] == version and segment["start"] <= delivered_frame < segment["end"]:
            return {"delivered_frame": delivered_frame, "segment": segment}
    raise ValueError("delivered frame outside declared versions")


def lookup_range(facts: list[Row], fact_id: str, version: str, start: int, end: int) -> Row:
    """查整段：连续同质段覆盖的范围，结论与逐帧查询一致。"""
    if end <= start:
        raise ValueError("range must be left-closed and right-open")
    touched = []
    for frame in range(start, end):
        touched.append(lookup_frame(facts, fact_id, version, frame)["segment"])
    unique: list[Row] = []
    seen: set[tuple[int, int]] = set()
    for segment in touched:
        key = (segment["start"], segment["end"])
        if key not in seen:
            seen.add(key)
            unique.append(segment)
    return {
        "start": start,
        "end": end,
        "uniform": len(seen) == 1,
        "segments": unique,
    }
