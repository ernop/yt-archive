from __future__ import annotations

import unittest

from yt_archive.framesheet import (
    MAX_SHEET_TILES,
    SINGLE_SHEET_TILES,
    _grid_crops,
    _keep_last,
    _sheet_chunk_sizes,
)


class FramesheetSizingTests(unittest.TestCase):
    def test_default_multi_sheet_cap_is_200(self):
        self.assertEqual(MAX_SHEET_TILES, 200)

    def test_up_to_250_frames_stay_on_one_sheet(self):
        self.assertEqual(SINGLE_SHEET_TILES, 250)
        self.assertEqual(_sheet_chunk_sizes(200), [200])
        self.assertEqual(_sheet_chunk_sizes(201), [201])
        self.assertEqual(_sheet_chunk_sizes(250), [250])

    def test_more_than_250_always_has_balanced_multiple_sheets(self):
        self.assertEqual(_sheet_chunk_sizes(251), [126, 125])
        self.assertEqual(_sheet_chunk_sizes(400), [200, 200])
        self.assertEqual(_sheet_chunk_sizes(401), [134, 134, 133])
        self.assertEqual(_sheet_chunk_sizes(601), [151, 150, 150, 150])

    def test_balanced_chunks_cover_every_frame_and_respect_cap(self):
        for total in range(251, 2001):
            chunks = _sheet_chunk_sizes(total)
            self.assertGreaterEqual(len(chunks), 2)
            self.assertEqual(sum(chunks), total)
            self.assertLessEqual(max(chunks), MAX_SHEET_TILES)
            self.assertLessEqual(max(chunks) - min(chunks), 1)


class DetailModeTests(unittest.TestCase):
    def test_a_small_region_change_fires_only_in_detail_mode(self):
        import torch

        whole = torch.tensor([[1.0, 0.0]] * 4)
        a, b = [1.0, 0.0], [0.0, 1.0]
        crops = torch.tensor([[a, a], [a, a], [a, b], [a, b]])
        self.assertEqual(_keep_last(whole, 0.85)[0], [0])
        self.assertEqual(_keep_last(whole, 0.85, crops, 0.85)[0], [0, 2])

    def test_three_by_three_crops_are_half_the_frame_each_way(self):
        from PIL import Image

        crops = _grid_crops(Image.new("RGB", (400, 800)), 3)
        self.assertEqual(len(crops), 9)
        self.assertEqual({c.size for c in crops}, {(200, 400)})


if __name__ == "__main__":
    unittest.main()
