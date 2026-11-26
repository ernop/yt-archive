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
            self.assertIn('id="toggle-furigana" aria-pressed="true" hidden', index_html)
            self.assertIn('id="vocab-panel" hidden', index_html)
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

            # With --study: merges furigana tokens, glosses, English, vocab, and acoustic overlaps.
            study_src = Path(tmp) / "study.json"
            study_src.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "language": "ja",
                        "segments": [
                            {
                                "i": 0,
                                "ja": "今日は出発しよう。",
                                "en": "Let's head out today.",
                                "tokens": [
                                    {"s": 0.5, "e": 1.4, "ja": "今日", "ruby": [["今日", "きょう"]], "g": "today", "v": 0},
                                    {"s": 1.4, "e": 1.7, "ja": "は", "ruby": "", "g": "topic", "v": 1},
                                    {"s": 1.7, "e": 3.8, "ja": "出発しよう。", "ruby": [["出発", "しゅっぱつ"], ["しよう。", ""]], "g": "let's depart", "v": 2},
                                ],
                            },
                            {
                                "i": 1,
                                "ja": "賛成。",
                                "en": "Agreed.",
                                "tokens": [
                                    {"s": 2.4, "e": 3.5, "ja": "賛成。", "ruby": [["賛成", "さんせい"], ["。", ""]], "g": "agreed", "v": 3},
                                ],
                            },
                        ],
                        "vocab": [
                            {"id": 0, "base": "今日", "ruby": [["今日", "きょう"]], "kana": "きょう", "romaji": "kyō", "diff": "basic", "gloss": "today", "role": "noun", "note": "", "count": 1, "times": [0.5]},
                            {"id": 1, "base": "は", "ruby": "", "kana": "", "romaji": "wa", "diff": "basic", "gloss": "topic", "role": "particle: topic", "note": "", "count": 1, "times": [1.4]},
                            {"id": 2, "base": "出発", "ruby": [["出発", "しゅっぱつ"]], "kana": "しゅっぱつ", "romaji": "shuppatsu", "diff": "n3", "gloss": "departure", "role": "noun", "note": "", "count": 1, "times": [1.7]},
                            {"id": 3, "base": "賛成", "ruby": [["賛成", "さんせい"]], "kana": "さんせい", "romaji": "sansei", "diff": "n3", "gloss": "agreement", "role": "noun", "note": "", "count": 1, "times": [2.4]},
                        ],
                        "acoustic_overlaps": [{"start": 2.4, "end": 3.5, "speakers": ["Speaker A", "Speaker B"]}],
                        "vocal_pulses": [{"start": 2.4, "end": 3.0, "speaker": "Speaker B"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            out_dir_study = Path(tmp) / "out_study"
            av_dir = Path(tmp) / "avatars_src"
            av_dir.mkdir()
            (av_dir / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-a")
            (av_dir / "b.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-b")
            (av_dir / "avatars.json").write_text(
                json.dumps({"Speaker A": "a.jpg", "Speaker B": "b.jpg"}), encoding="utf-8"
            )
            proc_study = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    vid,
                    str(out_dir_study),
                    "--data",
                    str(data_dir),
                    "--study",
                    str(study_src),
                    "--avatars",
                    str(av_dir),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            summary_study = json.loads(proc_study.stdout)
            self.assertEqual(summary_study["vocab"], 4)
            self.assertEqual(summary_study["avatars"], 2)
            self.assertTrue((out_dir_study / "avatars" / "a.jpg").is_file())
            self.assertTrue((out_dir_study / "avatars" / "b.jpg").is_file())
            study_html = (out_dir_study / "index.html").read_text(encoding="utf-8")
            self.assertIn('id="toggle-furigana" aria-pressed="true" title=', study_html)
            self.assertIn('id="toggle-gloss" aria-pressed="true" title=', study_html)
            self.assertIn('id="toggle-english" aria-pressed="true" title=', study_html)
            self.assertIn('id="stream-ctx-menu" hidden', study_html)
            self.assertIn('id="toggle-fullscreen" aria-pressed="false"', study_html)
            self.assertIn('id="ov-drag-handle"', study_html)
            self.assertIn('id="ov-fs-btn"', study_html)
            self.assertIn('<option value="10" selected>10s</option>', study_html)
            study_tr = json.loads((out_dir_study / "transcript.json").read_text(encoding="utf-8"))
            self.assertEqual(study_tr["language"], "ja")
            self.assertEqual(len(study_tr["vocab"]), 4)
            self.assertRegex(study_tr["avatars"]["Speaker A"], r"^avatars/a\.jpg\?v=[0-9a-f]{10}$")
            self.assertEqual(study_tr["segments"][0]["en"], "Let's head out today.")
            self.assertEqual(study_tr["segments"][0]["tk"][0][4], "today")
            self.assertEqual(len(study_tr["overlaps"]), 1)
            self.assertEqual(len(study_tr["pulses"]), 1)


if __name__ == "__main__":
    unittest.main()
