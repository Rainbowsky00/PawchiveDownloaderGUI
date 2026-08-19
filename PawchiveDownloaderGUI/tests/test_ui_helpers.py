from __future__ import annotations

import unittest

from kemono_downloader.app.ui_helpers import (
    FileProgressState,
    choose_displayed_file,
    format_speed,
    grid_columns,
    group_posts_by_year,
    update_file_progress,
)


class UiHelpersTests(unittest.TestCase):
    def test_fastest_file_requires_speed_advantage_to_switch(self) -> None:
        active = {
            "first": FileProgressState("first", 100, 1000, speed=100.0),
            "second": FileProgressState("second", 100, None, speed=115.0),
        }
        self.assertEqual(choose_displayed_file(active, "first"), "first")
        active["second"].speed = 121.0
        self.assertEqual(choose_displayed_file(active, "first"), "second")

    def test_completed_displayed_file_switches_to_remaining_file(self) -> None:
        active = {"second": FileProgressState("second", 100, None, speed=10.0)}
        self.assertEqual(choose_displayed_file(active, "first"), "second")

    def test_progress_speed_is_smoothed_from_byte_samples(self) -> None:
        state = update_file_progress(None, "file", 0, None, 1.0)
        state = update_file_progress(state, "file", 100, None, 2.0)
        self.assertEqual(state.speed, 100.0)
        state = update_file_progress(state, "file", 300, None, 3.0)
        self.assertEqual(state.speed, 130.0)

    def test_speed_formatting_and_download_restart(self) -> None:
        self.assertEqual(format_speed(0), "0 B/s")
        self.assertEqual(format_speed(1023), "1023 B/s")
        self.assertEqual(format_speed(1024), "1.0 KB/s")
        self.assertEqual(format_speed(1.5 * 1024 * 1024), "1.5 MB/s")
        self.assertEqual(format_speed(1024 * 1024 * 1024), "1.0 GB/s")

        state = FileProgressState("file", 200, None, speed=100.0, sample_bytes=200, sample_time=1.0)
        state = update_file_progress(state, "file", 50, None, 2.0)
        self.assertEqual(state.speed, 0.0)
        state = update_file_progress(state, "file", 150, None, 3.0)
        self.assertEqual(state.speed, 100.0)

    def test_groups_years_and_calculates_grid_columns(self) -> None:
        posts = [("2025-01-01", "a"), ("2026-01-01", "b"), ("unknown", "c")]
        groups = group_posts_by_year(posts, lambda post: post[0])
        self.assertEqual([year for year, _posts in groups], ["2026", "2025", "未知年份"])
        self.assertEqual(grid_columns(560), 3)
        self.assertEqual(grid_columns(10), 1)


if __name__ == "__main__":
    unittest.main()
