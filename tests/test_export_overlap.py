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
            self.assertIn('id="cfg-popover" hidden', study_html)
            self.assertIn('id="toggle-fullscreen" aria-pressed="false"', study_html)
            self.assertIn('id="stage-dock" hidden', study_html)
            self.assertIn('id="stage-splitter" hidden', study_html)
            self.assertIn('id="stage-float-layer"', study_html)
            self.assertIn('id="open-layout-menu"', study_html)
            self.assertIn('id="lanes-stage"', study_html)
            self.assertIn('class="lanes-stage timer-bottom"', study_html)
            self.assertIn('id="lanes-live-clock"', study_html)
            # Vertical order: player-wrap (1st) -> viz-stage (2nd) -> transcript-panel (3rd)
            self.assertLess(study_html.index('id="player-wrap"'), study_html.index('id="viz-stage"'))
            self.assertLess(study_html.index('id="viz-stage"'), study_html.index('id="transcript-panel"'))
            self.assertIn('<option value="10" selected>10s</option>', study_html)
            study_tr = json.loads((out_dir_study / "transcript.json").read_text(encoding="utf-8"))
            self.assertEqual(study_tr["language"], "ja")
            self.assertEqual(len(study_tr["vocab"]), 4)
            self.assertRegex(study_tr["avatars"]["Speaker A"], r"^avatars/a\.jpg\?v=[0-9a-f]{10}$")
            self.assertEqual(study_tr["segments"][0]["en"], "Let's head out today.")
            self.assertEqual(study_tr["segments"][0]["tk"][0][4], "today")
            self.assertEqual(len(study_tr["overlaps"]), 1)
            self.assertEqual(len(study_tr["pulses"]), 1)

            # With --prosody: exports prosody.json and unhides #toggle-tone.
            prosody_src = Path(tmp) / "prosody.json"
            prosody_src.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "video_id": vid,
                        "dt": 0.06,
                        "speakers": {
                            "Speaker A": {"median_f0_hz": 120.0, "p10_st": -3.0, "p90_st": 5.0, "median_db": -25.0, "median_sps": 4.0},
                            "Speaker B": {"median_f0_hz": 180.0, "p10_st": -2.5, "p90_st": 4.5, "median_db": -26.0, "median_sps": 3.5},
                        },
                        "segments": [
                            {"i": 0, "p": [0, 12, 45, 110, 20], "d": [0, 2, 6, 12, 2], "pk": 11.0, "mn": 0.0, "md": 2.0, "sl": 1.2, "db": 2.0, "pdb": 6.0, "rt": 1.1, "tg": ["falsetto"]},
                            {"i": 1, "p": [-40, -42, -38], "d": [-12, -14, -12], "pk": -3.8, "mn": -4.2, "md": -4.0, "sl": -0.2, "db": -6.0, "pdb": -6.0, "rt": 0.9, "tg": ["low-drop", "quiet"]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            out_dir_pr = Path(tmp) / "out_prosody"
            proc_pr = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    vid,
                    str(out_dir_pr),
                    "--data",
                    str(data_dir),
                    "--prosody",
                    str(prosody_src),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            summary_pr = json.loads(proc_pr.stdout)
            self.assertEqual(summary_pr["prosody"], 2)
            self.assertTrue((out_dir_pr / "prosody.json").is_file())
            pr_html = (out_dir_pr / "index.html").read_text(encoding="utf-8")
            self.assertRegex(pr_html, r'data-prosody="prosody\.json\?v=[0-9a-f]{10}"')
            self.assertIn('id="toggle-tone" aria-pressed="true" title=', pr_html)
            self.assertIn('id="open-labels-menu" aria-pressed="true" title=', pr_html)
            self.assertIn('id="tone-legend" hidden', pr_html)
            self.assertIn('id="open-rows-menu"', pr_html)

            # With --tracks: exports custom translation/emotion/sarcasm/mood/status tracks.
            tracks_src = Path(tmp) / "tracks.json"
            tracks_src.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "tracks": [
                            {"id": "translation", "label": "Alt Translation", "short": "Trans", "type": "text", "color": "#a9d6ff"},
                            {"id": "emotion", "label": "Emotion", "short": "Emo", "type": "tag", "color": "#ff85a2", "defaultOn": True},
                            {"id": "sarcasm", "label": "Sarcasm", "short": "Sarc", "type": "tag", "color": "#ffd240"},
                            {"id": "mood", "label": "Mood", "short": "Mood", "type": "tag", "color": "#66e3d8"},
                            {"id": "status", "label": "Status (High/Low)", "short": "Status", "type": "meter", "color": "#d2a8ff", "defaultOn": True},
                        ],
                        "segments": {
                            "0": {
                                "translation": "We might take the high route.",
                                "emotion": {"label": "curious", "score": 0.8},
                                "sarcasm": "none",
                                "mood": "analytical",
                                "status": {"value": 0.55, "label": "high (+0.55)"},
                            },
                            "1": {
                                "emotion": "warm",
                                "status": {"value": -0.35, "label": "yielding (-0.35)"},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            out_dir_trk = Path(tmp) / "out_tracks"
            proc_trk = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    vid,
                    str(out_dir_trk),
                    "--data",
                    str(data_dir),
                    "--prosody",
                    str(prosody_src),
                    "--annotations",
                    str(ann_src),
                    "--tracks",
                    str(tracks_src),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            summary_trk = json.loads(proc_trk.stdout)
            self.assertEqual(summary_trk["tracks"], 5)
            trk_tr = json.loads((out_dir_trk / "transcript.json").read_text(encoding="utf-8"))
            self.assertEqual([t["id"] for t in trk_tr["tracks"]], ["translation", "emotion", "sarcasm", "mood", "status"])
            self.assertEqual(trk_tr["segments"][0]["f"]["status"]["value"], 0.55)
            self.assertEqual(trk_tr["segments"][1]["f"]["emotion"], "warm")

            # Malformed tracks file fails loudly.
            bad_trk = Path(tmp) / "bad_tracks.json"
            bad_trk.write_text('{"tracks": [{"id": "x", "type": "invalid"}]}', encoding="utf-8")
            bad_trk_proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    vid,
                    str(Path(tmp) / "out_bad_trk"),
                    "--data",
                    str(data_dir),
                    "--tracks",
                    str(bad_trk),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(bad_trk_proc.returncode, 0)

            # With --clip-start / --clip-end and --overlaps + Spanish study mode (es-std).
            es_study = Path(tmp) / "es_study.json"
            es_study.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "language": "es",
                        "dialect": "ar",
                        "segments": [
                            {
                                "i": 0,
                                "es": "Vos sabés cómo es.",
                                "std": "Tú sabes cómo es.",
                                "en": "You know how it is.",
                                "tokens": [
                                    {"s": 0.5, "e": 1.0, "w": "Vos", "g": "you(vos)", "v": 0},
                                    {"s": 1.1, "e": 1.8, "w": "sabés", "g": "know", "v": 0},
                                ],
                            }
                        ],
                        "vocab": [
                            {"id": 0, "base": "sabés", "kana": "sabes", "diff": "ar", "gloss": "you know", "role": "voseo verb", "note": "Rioplatense voseo", "count": 1, "times": [0.5]},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            ov_file = Path(tmp) / "overlaps.json"
            ov_file.write_text(
                json.dumps({"acoustic_overlaps": [{"start": 2.4, "end": 3.5, "speakers": ["Speaker A", "Speaker B"]}]}),
                encoding="utf-8",
            )
            out_dir_es = Path(tmp) / "out_es"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    vid,
                    str(out_dir_es),
                    "--data",
                    str(data_dir),
                    "--study",
                    str(es_study),
                    "--overlaps",
                    str(ov_file),
                    "--clip-start",
                    "1.0",
                    "--clip-end",
                    "10.0",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            es_tr = json.loads((out_dir_es / "transcript.json").read_text(encoding="utf-8"))
            self.assertEqual(es_tr["language"], "es")
            self.assertEqual(es_tr["dialect"], "ar")
            self.assertEqual(es_tr["clip_start"], 1.0)
            self.assertEqual(es_tr["clip_end"], 10.0)
            self.assertEqual(es_tr["segments"][0]["std"], "Tú sabes cómo es.")
            self.assertEqual(len(es_tr["overlaps"]), 1)

    def test_extract_prosody_yin_tracks_pitch_and_loudness(self):
        import importlib.util
        import numpy as np

        spec = importlib.util.spec_from_file_location("extract_prosody", ROOT / "scripts" / "extract_prosody.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        sr = mod.SR
        t = np.arange(int(sr * 1.6), dtype=np.float32) / sr
        sig = np.zeros_like(t)
        # First 0.8s: 120 Hz modal tone at 0.2 amplitude; next 0.8s: 240 Hz (+12 st) at 0.6 amplitude (+9.5 dB).
        m1 = t < 0.8
        m2 = ~m1
        sig[m1] = 0.2 * np.sin(2.0 * np.pi * 120.0 * t[m1])
        sig[m2] = 0.6 * np.sin(2.0 * np.pi * 240.0 * t[m2])
        f0, db, _ = mod.extract_raw_tracks(sig)
        # Evaluate interior steady frames away from the 0.8s step edge
        f0_lo = f0[8:32]
        f0_hi = f0[48:72]
        self.assertAlmostEqual(float(np.nanmedian(f0_lo)), 120.0, delta=1.5)
        self.assertAlmostEqual(float(np.nanmedian(f0_hi)), 240.0, delta=2.5)
        st_jump = 12.0 * np.log2(float(np.nanmedian(f0_hi)) / float(np.nanmedian(f0_lo)))
        self.assertAlmostEqual(st_jump, 12.0, delta=0.2)
        db_jump = float(np.median(db[48:72]) - np.median(db[8:32]))
        self.assertAlmostEqual(db_jump, 9.54, delta=0.5)


if __name__ == "__main__":
    unittest.main()
