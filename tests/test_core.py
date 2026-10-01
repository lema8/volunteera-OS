from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.ai_client import AIError, deterministic_plan, validate_plan_for_project
from backend.media import parse_fraction, parse_integer, parse_number
from backend.schemas import EditingPlan
from backend.storage import next_version_id, safe_filename, write_json


class EditingPlanTests(unittest.TestCase):
    def project_fixture(self, root: Path):
        clips = [
            {"id": "clip-a", "name": "A", "order": 0, "metadata": {"duration": 8}, "analysis_file": "analysis/clip-a/analysis.json"},
            {"id": "clip-b", "name": "B", "order": 1, "metadata": {"duration": 6}, "analysis_file": "analysis/clip-b/analysis.json"},
        ]
        write_json(root / "analysis/clip-a/analysis.json", {"duration": 8, "speech": [{"start": 1, "end": 3, "text": "Opening line"}], "scenes": [], "ocr": [], "events": []})
        write_json(root / "analysis/clip-b/analysis.json", {"duration": 6, "speech": [], "scenes": [], "ocr": [], "events": []})
        return {"id": "test", "name": "Test", "clips": clips, "assets": [], "versions": []}

    def test_local_plan_keeps_user_order_and_builds_captions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.project_fixture(root)
            plan = deterministic_plan(root, project, include_captions=True)
            self.assertEqual([item.clip_id for item in plan.timeline], ["clip-a", "clip-b"])
            self.assertEqual(plan.captions[0].text, "Opening line")
            validate_plan_for_project(plan, project)

    def test_reordered_ai_plan_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.project_fixture(root)
            plan = EditingPlan.model_validate({
                "timeline": [
                    {"clip_id": "clip-b", "start": 0, "end": 2},
                    {"clip_id": "clip-a", "start": 0, "end": 2},
                ]
            })
            with self.assertRaisesRegex(AIError, "clip order"):
                validate_plan_for_project(plan, project)

    def test_out_of_bounds_cut_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.project_fixture(root)
            plan = EditingPlan.model_validate({"timeline": [{"clip_id": "clip-a", "start": 0, "end": 20}]})
            with self.assertRaisesRegex(AIError, "duration"):
                validate_plan_for_project(plan, project)

    def test_visual_overlays_require_known_image_assets_and_output_times(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.project_fixture(root)
            project["assets"] = [{"id": "image-1", "category": "images"}, {"id": "audio-1", "category": "music"}]
            valid = EditingPlan.model_validate({
                "timeline": [{"clip_id": "clip-a", "start": 0, "end": 5}],
                "visual_overlays": [{"asset_id": "image-1", "start": 1, "end": 3, "mode": "fullscreen"}],
            })
            validate_plan_for_project(valid, project)
            wrong_type = EditingPlan.model_validate({
                "timeline": [{"clip_id": "clip-a", "start": 0, "end": 5}],
                "visual_overlays": [{"asset_id": "audio-1", "start": 1, "end": 3}],
            })
            with self.assertRaisesRegex(AIError, "image asset"):
                validate_plan_for_project(wrong_type, project)
            out_of_range = EditingPlan.model_validate({
                "timeline": [{"clip_id": "clip-a", "start": 0, "end": 5}],
                "visual_overlays": [{"asset_id": "image-1", "start": 4, "end": 7}],
            })
            with self.assertRaisesRegex(AIError, "beyond"):
                validate_plan_for_project(out_of_range, project)


class MediaParsingTests(unittest.TestCase):
    def test_ffmpeg_na_placeholders_do_not_raise(self):
        self.assertEqual(parse_integer("N/A"), 0)
        self.assertEqual(parse_integer(None, 42), 42)
        self.assertEqual(parse_number("nan", 7), 7)
        self.assertEqual(parse_fraction("N/A"), 0)
        self.assertEqual(parse_fraction("30000/1001"), 30000 / 1001)


class StorageTests(unittest.TestCase):
    def test_filename_removes_path_and_shell_characters(self):
        self.assertEqual(safe_filename("../../my; clip $(bad).MP4"), "my_ clip _bad_.mp4")

    def test_next_version_skips_interrupted_render_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "versions" / "v001" / "work").mkdir(parents=True)
            project = {"versions": []}
            self.assertEqual(next_version_id(project, root), "v002")
            (root / "versions" / "v004").mkdir()
            project["versions"] = [{"id": "v003"}]
            self.assertEqual(next_version_id(project, root), "v005")


if __name__ == "__main__":
    unittest.main()
