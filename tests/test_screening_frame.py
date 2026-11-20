import json
from pathlib import Path
import unittest

from film_restoration_evidence import process
from film_restoration_evidence.crop_review import lookup_frame, lookup_range

ROOT = Path(__file__).resolve().parent.parent


def screening(records, *more):
    return process("crop-review", {"records": [records, *more]})


def base(**overrides):
    record = {
        "id": "screening",
        "frame_count": 100,
        "device": {
            "viewport": [0, 0, 200, 100],
            "aspect": 2,
            "min_size": [80, 40],
        },
        "views": [
            {
                "start": 0,
                "end": 100,
                "picture": [0, 0, 192, 100],
                "keep": [[40, 80, 150, 92]],
            }
        ],
    }
    record.update(overrides)
    return record


class ScreeningFrameExampleTests(unittest.TestCase):
    def test_example_matches_expected(self):
        source = json.loads((ROOT / "examples" / "crop-review.json").read_text(encoding="utf-8"))
        expected = json.loads((ROOT / "tests" / "expected" / "crop-review.json").read_text(encoding="utf-8"))
        self.assertEqual(process("crop-review", source), expected)


class ScreeningFrameRuleTests(unittest.TestCase):
    def test_frame_keeps_must_keep_and_avoids_confirmed_damage(self):
        row = base(
            views=[
                {
                    "start": 0,
                    "end": 100,
                    "picture": [0, 0, 192, 100],
                    "keep": [[40, 80, 150, 92]],
                    "damage": [
                        {"rect": [184, 0, 192, 100], "state": "confirmed"},
                        {"rect": [0, 0, 8, 100], "state": "confirmed"},
                    ],
                }
            ]
        )
        fact = screening(row)[0]
        frame = fact["frame"]
        self.assertIsNotNone(frame)
        self.assertLessEqual(frame[0], 40)
        self.assertLessEqual(frame[1], 80)
        self.assertGreaterEqual(frame[2], 150)
        self.assertGreaterEqual(frame[3], 92)
        self.assertLessEqual(frame[2], 184)
        self.assertGreaterEqual(frame[0], 8)
        self.assertAlmostEqual(frame[2] - frame[0], 2 * (frame[3] - frame[1]))

    def test_suspected_marks_are_neither_damage_nor_clean(self):
        confirmed_only = base(
            views=[
                {
                    "start": 0,
                    "end": 100,
                    "picture": [0, 0, 192, 100],
                    "keep": [[40, 80, 150, 92]],
                    "damage": [{"rect": [184, 0, 192, 100], "state": "confirmed"}],
                }
            ]
        )
        suspected = base(
            views=[
                {
                    "start": 0,
                    "end": 100,
                    "picture": [0, 0, 192, 100],
                    "keep": [[40, 80, 150, 92]],
                    "damage": [{"rect": [184, 0, 192, 100], "state": "suspected"}],
                }
            ]
        )
        decided = screening(confirmed_only)[0]
        pending = screening(suspected)[0]
        # An unconfirmed scratch is not damage: the frame is not narrowed to hide it.
        self.assertEqual(decided["frame"], [0, 8, 184, 100])
        self.assertEqual(pending["frame"], [0, 0, 200, 100])
        # Nor is it clean: it stays in the review queue and blocks final sign-off.
        self.assertEqual(pending["segments"][0]["status"], "pending_review")
        self.assertFalse(pending["finalized"])
        self.assertTrue(decided["finalized"])

    def test_unviewed_ranges_block_final_signoff(self):
        row = base(frame_count=100, views=[
            {"start": 0, "end": 60, "picture": [0, 0, 192, 100], "keep": [[40, 80, 150, 92]]}
        ])
        fact = screening(row)[0]
        self.assertEqual(fact["unviewed_ranges"], [[60, 100]])
        self.assertEqual(fact["unviewed_frames"], 40)
        self.assertFalse(fact["finalized"])
        self.assertEqual(fact["segments"][-1]["status"], "unviewed")

    def test_keep_damage_conflict_is_reported_not_hidden(self):
        row = base(
            views=[
                {
                    "start": 0,
                    "end": 40,
                    "picture": [0, 0, 192, 100],
                    "keep": [[40, 80, 150, 92]],
                },
                {
                    "start": 40,
                    "end": 100,
                    "picture": [0, 0, 192, 100],
                    "keep": [[40, 80, 150, 92]],
                    "damage": [{"rect": [60, 78, 66, 94], "state": "confirmed"}],
                },
            ]
        )
        fact = screening(row)[0]
        self.assertIsNone(fact["frame"])
        self.assertFalse(fact["finalized"])
        conflict = fact["conflicts"][0]
        self.assertEqual(conflict["reason"], "keep_damage")
        self.assertEqual(conflict["keep"], [40, 80, 150, 92])
        self.assertEqual(conflict["damage"], [[60, 78, 66, 94]])
        self.assertEqual(conflict["keep_frames"], [[0, 100]])
        self.assertEqual(conflict["damage_frames"], [[40, 100]])
        # The fixed frame spans the whole film, so both ranges are shown.
        self.assertEqual([conflict["start"], conflict["end"]], [0, 100])

    def test_recheck_can_exclude_only_part_of_the_scratch(self):
        views = [
            {
                "start": 0,
                "end": 100,
                "picture": [0, 0, 192, 100],
                "keep": [[40, 80, 150, 92]],
                "damage": [
                    {"rect": [184, 0, 192, 100], "state": "excluded", "frames": [20, 100]},
                    {"rect": [184, 0, 192, 100], "state": "suspected", "frames": [0, 20]},
                ],
            }
        ]
        fact = screening(base(views=views))[0]
        # The scratch was excluded for frames 20..99 but is still pending for 0..19.
        self.assertEqual(
            fact["pending_review"],
            [
                {
                    "start": 0,
                    "end": 20,
                    "suspected": [[184, 0, 192, 100]],
                    "inside_frame": [[184, 0, 192, 100]],
                }
            ],
        )
        self.assertEqual(fact["segments"][0]["status"], "pending_review")
        self.assertEqual(fact["segments"][0]["end"], 20)
        self.assertEqual(fact["segments"][1]["status"], "decided")
        self.assertFalse(fact["finalized"])

    def test_repeated_view_records_do_not_add_weight(self):
        single = {
            "start": 0,
            "end": 100,
            "picture": [0, 0, 192, 100],
            "keep": [[40, 80, 150, 92]],
            "damage": [{"rect": [184, 0, 192, 100], "state": "confirmed"}],
        }
        doubled = base(views=[single, dict(single)])
        once = screening(base(views=[single]))[0]
        twice = screening(doubled)[0]
        self.assertEqual(once["frame"], twice["frame"])
        self.assertEqual(once["retained_picture_px"], twice["retained_picture_px"])

    def test_overlapping_picture_in_one_frame_is_counted_once(self):
        views = [
            {"start": 0, "end": 100, "picture": [0, 0, 100, 100], "keep": [[10, 80, 90, 92]]},
            {"start": 0, "end": 100, "picture": [0, 0, 100, 100], "keep": [[10, 80, 90, 92]]},
        ]
        overlapping = screening(base(views=views))[0]
        single = screening(base(views=views[:1]))[0]
        self.assertEqual(overlapping["frame"], single["frame"])
        self.assertEqual(overlapping["retained_picture_px"], single["retained_picture_px"])

    def test_frame_maximises_retained_picture(self):
        views = [
            {
                "start": 0,
                "end": 40,
                "picture": [0, 0, 192, 100],
                "keep": [[40, 80, 150, 92]],
                "damage": [{"rect": [160, 0, 168, 100], "state": "confirmed"}],
            }
        ]
        fact = screening(base(views=views))[0]
        frame = fact["frame"]
        self.assertLessEqual(frame[2], 160)
        self.assertGreaterEqual(frame[2], 150)
        self.assertGreaterEqual(frame[3], 92)
        self.assertGreater(fact["retained_picture_px"], 0)

    def test_frame_respects_aspect_and_minimum_size(self):
        row = base(
            device={"viewport": [0, 0, 200, 100], "aspect": [3, 2], "min_width": 90, "min_height": 60}
        )
        fact = screening(row)[0]
        self.assertAlmostEqual(fact["device"]["aspect"], 1.5)
        frame = fact["frame"]
        width = frame[2] - frame[0]
        height = frame[3] - frame[1]
        self.assertAlmostEqual(width / height, 1.5, places=6)
        self.assertGreaterEqual(width + 1e-9, 90)
        self.assertGreaterEqual(height + 1e-9, 60)

    def test_device_impossible_is_reported(self):
        row = base(
            device={"viewport": [0, 0, 10, 10], "aspect": 1, "min_size": [100, 100]},
            views=[{"start": 0, "end": 100, "picture": [0, 0, 10, 10], "keep": []}],
        )
        fact = screening(row)[0]
        self.assertIsNone(fact["frame"])
        self.assertEqual(fact["conflicts"][0]["reason"], "device")

    def test_keep_outside_viewport_is_reported(self):
        row = base(
            views=[{"start": 0, "end": 100, "picture": [0, 0, 192, 100], "keep": [[150, 90, 260, 98]]}]
        )
        fact = screening(row)[0]
        self.assertIsNone(fact["frame"])
        self.assertEqual(fact["conflicts"][0]["reason"], "keep_outside_viewport")
        self.assertEqual(fact["conflicts"][0]["keep"], [[150, 90, 260, 98]])

    def test_label_lookup_matches_segment_conclusions(self):
        row = base(
            frame_count=200,
            views=[
                {"start": 0, "end": 50, "picture": [0, 0, 192, 100], "keep": [[40, 80, 150, 92]]},
                {
                    "start": 50,
                    "end": 120,
                    "picture": [0, 0, 192, 100],
                    "keep": [[40, 80, 150, 92]],
                    "damage": [{"rect": [0, 0, 6, 100], "state": "suspected"}],
                },
                {
                    "start": 120,
                    "end": 200,
                    "picture": [0, 0, 192, 100],
                    "keep": [[40, 80, 150, 92]],
                },
            ],
        )
        fact = screening(row)[0]
        self.assertEqual(fact["segments"][0]["start"], 0)
        self.assertEqual(lookup_frame([fact], "screening", 10)["segment"]["status"], "decided")
        self.assertEqual(
            lookup_frame([fact], "screening", 60)["segment"]["status"], "pending_review"
        )
        uniform = lookup_range([fact], "screening", 120, 200)
        self.assertTrue(uniform["uniform"])
        split = lookup_range([fact], "screening", 40, 130)
        self.assertFalse(split["uniform"])

    def test_conflict_without_keep_is_never_raised(self):
        row = base(
            views=[
                {
                    "start": 0,
                    "end": 100,
                    "picture": [0, 0, 192, 100],
                    "damage": [{"rect": [40, 40, 120, 70], "state": "confirmed"}],
                }
            ]
        )
        fact = screening(row)[0]
        self.assertEqual(fact["conflicts"], [])
        self.assertIsNotNone(fact["frame"])
        self.assertTrue(fact["finalized"])


if __name__ == "__main__":
    unittest.main()
