import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_overlap_page.py"


class ExportOverlapPageTests(unittest.TestCase):
    def test_exports_strict_csp_static_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            vid = "abcdefghijk"
            folder = data_dir / vid
            cond = folder / "_condensed"
            cond.mkdir(parents=True)
            (folder / "archive.json").write_text(
                json.dumps(
                    {
                        "id": vid,
                        "title": "Two Speakers Testing Overlap",
                        "channel": "Demo Channel",
                        "upload_date": "20260101",
                        "duration": 12.5,
                    }
                ),
                encoding="utf-8",
            )
            (folder / f"demo-{vid}.info.json").write_text(
                json.dumps(
                    {
                        "chapters": [
                            {"start_time": 0, "end_time": 6, "title": "Intro"},
                            {"start_time": 6, "end_time": 12.5, "title": "Discussion"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (cond / "transcript.json").write_text(
                json.dumps(
                    {
                        "duration": 12.5,
                        "engine": "test-engine",
                        "segments": [
                            {
                                "index": 0,
                                "start": 0.5,
                                "end": 4.0,
                                "speaker": "Speaker A",
                                "text": "We could take the upper path here.",
                                "words": [
                                    {"start": 0.5, "end": 1.0, "word": "We"},
                                    {"start": 1.1, "end": 1.6, "word": "could"},
                                    {"start": 1.7, "end": 2.2, "word": "take"},
                                    {"start": 2.3, "end": 2.8, "word": "the"},
                                    {"start": 2.9, "end": 3.4, "word": "upper"},
                                    {"start": 3.5, "end": 4.0, "word": "path."},
                                ],
                            },
                            {
                                "index": 1,
                                "start": 2.4,
                                "end": 3.6,
                                "speaker": "Speaker B",
                                "text": "Yeah, totally.",
                                "words": [
                                    {"start": 2.4, "end": 2.9, "word": "Yeah,"},
                                    {"start": 3.0, "end": 3.6, "word": "totally."},
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            out_dir = Path(tmp) / "out"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    vid,
                    str(out_dir),
                    "--data",
                    str(data_dir),
                    "--title",
                    "Custom Overlap Study Title",
                    "--back-url",
                    "https://example.test/",
                    "--back-label",
                    "example.test",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            summary = json.loads(proc.stdout)
            self.assertEqual(summary["video_id"], vid)
            self.assertEqual(summary["segments"], 2)
            self.assertEqual(summary["speakers"], ["Speaker A", "Speaker B"])
            self.assertEqual(summary["annotations"], 0)
            self.assertFalse((out_dir / "annotations.json").exists())

            index_html = (out_dir / "index.html").read_text(encoding="utf-8")
            app_js = (out_dir / "app.js").read_text(encoding="utf-8")
            app_css = (out_dir / "app.css").read_text(encoding="utf-8")
            tr = json.loads((out_dir / "transcript.json").read_text(encoding="utf-8"))

            # Strict CSP: only self-hosted deferred script, zero inline scripts/handlers.
            scripts = re.findall(r"<script\b[^>]*>", index_html, re.IGNORECASE)
            self.assertEqual(len(scripts), 1)
            self.assertIn('src="app.js?v=', scripts[0])
            self.assertNotIn("onclick=", index_html.lower())
            self.assertIn("https://example.test/", index_html)
            self.assertIn("Custom Overlap Study Title", index_html)
            self.assertEqual(tr["title"], "Custom Overlap Study Title")
            self.assertEqual(tr["video_title"], "Two Speakers Testing Overlap")
            self.assertIn("youtube-nocookie.com", app_js)
            self.assertNotIn("youtube.com/iframe_api", app_js)
            self.assertNotIn("Live dual-speaker captions", index_html + app_js)
            self.assertNotIn("Speaker overlap timeline", index_html + app_js)
            self.assertIn('id="lanes-minimap-wrap" hidden', index_html)
            self.assertIn('id="toggle-minimap" aria-pressed="false"', index_html)
            self.assertIn('id="toggle-badges" aria-pressed="false"', index_html)
            self.assertIn('id="toggle-annotations" aria-pressed="false" hidden', index_html)
            self.assertIn('id="ann-live" hidden', index_html)
            self.assertIn('id="ann-filter-bar" hidden', index_html)
            self.assertNotIn("data-annotations=", index_html)
            self.assertTrue(len(app_css) > 1000)

            self.assertEqual(tr["chapters"][1]["t"], "Discussion")
            self.assertEqual(tr["segments"][1]["k"], 1)
            self.assertEqual(tr["segments"][1]["w"][0][2], "Yeah,")

            if shutil.which("node"):
                subprocess.run(["node", "--check", str(out_dir / "app.js")], check=True)

            # With --annotations: annotations.json exported, toggle visible and OFF by default.
            ann_src = Path(tmp) / "seed.json"
            ann_src.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "video_id": vid,
                        "categories": [
                            {"id": "overlap", "label": "Cooperative overlap", "color": "#ffd240"},
                            {"id": "buy-in", "label": "Buy-in", "color": "#7ee787"},
                        ],
                        "items": [
                            {
                                "id": "a01",
                                "t": 2.4,
                                "end": 3.6,
                                "cat": "overlap",
                                "title": "Aligned continuer",
                                "note": "Speaker B affirms mid-turn.",
                                "source": "ai",
                            },
                            {
                                "id": "a02",
                                "t": 8.0,
                                "cat": "buy-in",
                                "title": "Confirming plan",
                                "note": "Both agree before moving.",
                                "source": "ai",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            out_dir_ann = Path(tmp) / "out_ann"
            proc_ann = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    vid,
                    str(out_dir_ann),
                    "--data",
                    str(data_dir),
                    "--annotations",
                    str(ann_src),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            summary_ann = json.loads(proc_ann.stdout)
            self.assertEqual(summary_ann["annotations"], 2)
            ann_out = json.loads((out_dir_ann / "annotations.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ann_out["items"]), 2)
            ann_html = (out_dir_ann / "index.html").read_text(encoding="utf-8")
            self.assertRegex(ann_html, r'data-annotations="annotations\.json\?v=[0-9a-f]{10}"')
            self.assertIn('id="toggle-annotations" aria-pressed="false" title=', ann_html)
            self.assertIn('id="ann-live" hidden', ann_html)
            self.assertIn('id="ann-filter-bar" hidden', ann_html)

            # Malformed annotations file fails loudly.
            bad_ann = Path(tmp) / "bad.json"
            bad_ann.write_text('{"items": [{"note": "missing t"}]}', encoding="utf-8")
            bad_proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    vid,
                    str(Path(tmp) / "out_bad"),
                    "--data",
                    str(data_dir),
                    "--annotations",
                    str(bad_ann),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(bad_proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
