import json
from pathlib import Path
import unittest

from film_restoration_evidence import process
from film_restoration_evidence.render_lineage import lookup_frame, lookup_range

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = json.loads((ROOT / "examples" / "render-lineage.json").read_text(encoding="utf-8"))


def extended_fact():
    facts = process("render-lineage", EXAMPLE)
    return next(fact for fact in facts if fact["fact_id"] == "render-lineage-02")


class RenderLineageExtendedTests(unittest.TestCase):
    def test_legacy_record_keeps_original_material_manifest(self):
        facts = process("render-lineage", EXAMPLE)
        legacy = next(fact for fact in facts if fact["fact_id"] == "render-lineage-01")
        self.assertEqual(
            legacy,
            {
                "fact_id": "render-lineage-01",
                "materials": [
                    {"span": "opener", "ref": "scan-A", "kind": "scan"},
                    {"span": "join", "ref": "paint-2", "kind": "paint"},
                    {"span": "tail", "ref": "lost-id", "kind": "unresolved"},
                ],
            },
        )

    def test_reverse_scan_gap_remains_missing_even_when_manifest_cites_it(self):
        fact = extended_fact()
        verdict_by_frame = {
            frame: lookup_frame([fact], "render-lineage-02", "archive-cut", frame)["segment"]
            for frame in range(12)
        }
        # 倒扫漏扫的 301 被交付清单引用，但不能算已找到原扫描。
        missing = verdict_by_frame[10]
        self.assertEqual(missing["verdict"], "scan_missing")
        self.assertEqual(missing["source_frames"], [301])
        self.assertEqual(missing["reel"], "R3")
        self.assertEqual(missing["batch"], "B-2026-11-03")
        self.assertEqual(verdict_by_frame[9]["verdict"], "scan_found")
        self.assertEqual(verdict_by_frame[11]["verdict"], "scan_found")

    def test_freeze_names_the_single_extended_frame_and_its_scan_state(self):
        fact = extended_fact()
        for frame in (8, 9):
            segment = lookup_frame([fact], "render-lineage-02", "screening-cut", frame)["segment"]
            self.assertEqual(segment["verdict"], "freeze_extended")
            self.assertEqual(segment["frozen_frame"], 302)
            self.assertEqual(segment["frozen_frame_scan_status"], "captured")

    def test_paint_delivery_and_unverified_base_frame_are_both_queryable(self):
        fact = extended_fact()
        segment = lookup_frame([fact], "render-lineage-02", "screening-cut", 11)["segment"]
        self.assertEqual(segment["verdict"], "paint_delivered")
        self.assertFalse(segment["base_frame_verified"])
        self.assertEqual(segment["base_frame"], 302)
        self.assertEqual(segment["base_frame_scan_status"], "captured")
        # 补绘没有底帧声明时，待核状态与缺底帧都查得到。
        no_base = lookup_frame([fact], "render-lineage-02", "screening-cut", 13)["segment"]
        self.assertEqual(no_base["verdict"], "paint_delivered")
        self.assertIsNone(no_base["base_frame"])
        self.assertIsNone(no_base["base_frame_scan_status"])
        self.assertFalse(no_base["base_frame_verified"])

    def test_same_shot_two_occurrences_can_use_different_copies(self):
        fact = extended_fact()
        first = lookup_frame([fact], "render-lineage-02", "archive-cut", 2)["segment"]
        second = lookup_frame([fact], "render-lineage-02", "archive-cut", 8)["segment"]
        self.assertEqual((first["shot"], first["occurrence"]), ("A", 1))
        self.assertEqual((second["shot"], second["occurrence"]), ("A", 2))
        self.assertEqual(first["reel"], "R1")
        self.assertEqual(second["reel"], "R4")

    def test_range_query_matches_per_frame_query_for_continuous_segments(self):
        fact = extended_fact()
        per_frame = [
            lookup_frame([fact], "render-lineage-02", "archive-cut", frame)["segment"]
            for frame in range(12)
        ]
        whole = lookup_range([fact], "render-lineage-02", "archive-cut", 0, 12)
        # 全卷范围按同质段合并，逐帧查询落在同一段上时结论完全一致。
        for frame, segment in zip(range(12), per_frame):
            self.assertIn(segment, whole["segments"])
        self.assertEqual(
            [s["verdict"] for s in whole["segments"]],
            ["scan_found", "scan_found", "placeholder", "scan_found",
             "scan_found", "scan_missing", "scan_found"],
        )
        # 全卷连续段查其中任意一帧，结论与查整段一致。
        uniform = lookup_range([fact], "render-lineage-02", "archive-cut", 0, 4)
        self.assertTrue(uniform["uniform"])
        for frame in range(4):
            single = lookup_frame([fact], "render-lineage-02", "archive-cut", frame)["segment"]
            self.assertEqual(single, uniform["segments"][0])
        # 跨过漏扫帧的范围不是同质段。
        split = lookup_range([fact], "render-lineage-02", "archive-cut", 9, 12)
        self.assertFalse(split["uniform"])
        self.assertEqual([s["verdict"] for s in split["segments"]],
                         ["scan_found", "scan_missing", "scan_found"])

    def test_comparison_keeps_source_change_position_only_and_unmatched(self):
        fact = extended_fact()
        comparison = fact["comparison"]
        changed = [(e["old"]["shot"], e["old"]["occurrence"]) for e in comparison["source_changed"]]
        moved = [(e["old"]["shot"], e["old"]["occurrence"]) for e in comparison["position_only"]]
        unmatched = [(e["version"], e["shot"], e["occurrence"]) for e in comparison["unmatched"]]
        self.assertEqual(changed, [("C", 1)])
        self.assertEqual(sorted(moved), [("A", 1), ("A", 2), ("B", 1)])
        self.assertEqual(unmatched, [("screening-cut", "D", 1)])
        # 位置确实因剪短挪动，但来源身份未变。
        b1 = next(e for e in comparison["position_only"] if e["old"]["shot"] == "B")
        self.assertEqual(b1["old"]["position"], {"start": 4, "end": 6})
        self.assertEqual(b1["new"]["position"], {"start": 3, "end": 5})

    def test_validation_errors(self):
        def mutate(fn):
            data = json.loads(json.dumps(EXAMPLE))
            record = next(r for r in data["records"] if r["id"] == "render-lineage-02")
            fn(record)
            return data

        # 倒扫引用了未实际捕获的帧：通过（标记 missing），但引用扫描范围外帧必须报错。
        def out_of_range(record):
            arch_c = next(s for s in record["spans"] if s["span"] == "arch-c1")
            arch_c["source_frames"] = [302, 301, 999]
        with self.assertRaisesRegex(ValueError, "outside its declared scan range"):
            process("render-lineage", mutate(out_of_range))

        def span_crosses_shot(record):
            record["spans"][0]["end"] = 5
        with self.assertRaisesRegex(ValueError, "inside one shot"):
            process("render-lineage", mutate(span_crosses_shot))

        def span_gap(record):
            first = record["spans"][0]
            first["end"] = 3
            first["source_frames"] = [100, 101, 102]
        with self.assertRaisesRegex(ValueError, "tile every delivered frame"):
            process("render-lineage", mutate(span_gap))

        def wrong_frame_count(record):
            record["spans"][0]["source_frames"] = [100, 101, 102]
        with self.assertRaisesRegex(ValueError, "one source frame per delivered frame"):
            process("render-lineage", mutate(wrong_frame_count))

        def unknown_scan(record):
            scan_material = next(m for m in record["materials"] if m["ref"] == "mat-r1")
            scan_material["scan_ref"] = "does-not-exist"
        with self.assertRaisesRegex(ValueError, "unknown scan"):
            process("render-lineage", mutate(unknown_scan))

        def duplicate_occurrence_in_links(record):
            record["correspondences"][1]["new_shot"] = "A"
            record["correspondences"][1]["new_occurrence"] = 1
        with self.assertRaisesRegex(ValueError, "more than one correspondence"):
            process("render-lineage", mutate(duplicate_occurrence_in_links))

        def unknown_shot_in_link(record):
            record["correspondences"][0]["old_shot"] = "Z"
        with self.assertRaisesRegex(ValueError, "unknown shot occurrence"):
            process("render-lineage", mutate(unknown_shot_in_link))

    def test_physical_source_swap_is_detected_beyond_material_rename(self):
        data = json.loads(json.dumps(EXAMPLE))
        record = next(r for r in data["records"] if r["id"] == "render-lineage-02")
        # B 在放映版改用 R4 的同序位帧：卷不同，属于真实换料。
        b_span = next(s for s in record["spans"] if s["span"] == "screen-b1")
        b_span["ref"] = "mat-r4"
        b_span["source_frames"] = [400, 401]
        fact = next(f for f in process("render-lineage", data) if f["fact_id"] == "render-lineage-02")
        changed = [(e["old"]["shot"], e["old"]["occurrence"])
                   for e in fact["comparison"]["source_changed"]]
        self.assertIn(("B", 1), changed)

    def test_lookup_rejects_unknown_fact_version_and_frame(self):
        with self.assertRaisesRegex(ValueError, "unknown extended"):
            lookup_frame([], "missing", "archive-cut", 0)
        fact = extended_fact()
        with self.assertRaisesRegex(ValueError, "outside declared versions"):
            lookup_frame([fact], "render-lineage-02", "archive-cut", 99)
        with self.assertRaisesRegex(ValueError, "left-closed"):
            lookup_range([fact], "render-lineage-02", "archive-cut", 5, 5)


if __name__ == "__main__":
    unittest.main()
