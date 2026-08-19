from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kemono_downloader.downloader.engine import DownloadEngine, response_total_size
from kemono_downloader.downloader.models import DownloadTask, FileItem, PostItem


class DownloadProgressTests(unittest.TestCase):
    def test_response_total_size_handles_ranges_and_lengths(self) -> None:
        class Response:
            def __init__(self, headers: dict[str, str]):
                self.headers = headers

        self.assertEqual(response_total_size(Response({"Content-Range": "bytes 10-19/100"}), 10), 100)
        self.assertEqual(response_total_size(Response({"Content-Length": "90"}), 10), 100)
        self.assertIsNone(response_total_size(Response({}), 0))

    def test_text_task_emits_byte_progress(self) -> None:
        post = PostItem(
            id="post",
            title="Title",
            published="2026-08-18 20:58:46",
            url="https://pawchive.pw/patreon/user/creator/post/post",
            creator_id="creator",
            service="patreon",
        )
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "content.txt"
            task = DownloadTask(
                post=post,
                file=FileItem(url=post.url, name="content.txt", kind="text", content="中文"),
                save_path=path,
                temp_path=Path(f"{path}.temp"),
            )
            events = []
            asyncio.run(DownloadEngine(concurrency=1).run([task], on_event=events.append))

            progress = [event for event in events if event.type == "file_progress"]
            self.assertEqual([(event.bytes_current, event.bytes_total) for event in progress], [(0, 6), (6, 6)])
            self.assertEqual(path.read_text(encoding="utf-8"), "中文")


if __name__ == "__main__":
    unittest.main()
