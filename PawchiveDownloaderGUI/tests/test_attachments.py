from __future__ import annotations

import asyncio
import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

DEPENDENCIES_AVAILABLE = all(importlib.util.find_spec(name) for name in ("aiohttp", "bs4"))

if DEPENDENCIES_AVAILABLE:
    from kemono_downloader.api.pawchive import ImageSource, parse_post_detail
    from kemono_downloader.downloader.models import DateNamingMode, FileItem, PostItem
    from kemono_downloader.downloader.service import DownloadService


DETAIL_HTML = """
<section id="page">
  <div class="post__content"><p>Hello</p></div>
  <a class="fileThumb" href="/data/a.png?f=first.png" download="first.png">
    <img src="/thumbnail/a.png">
  </a>
  <a class="post__attachment-link" href="https://file.pawchive.pw/data/archive.lpk?f=archive.lpk" download="%E6%B3%95%E5%B0%94%E4%BC%BD-%E9%86%89%E9%85%92%E3%80%90ENG%E3%80%911.4.lpk">
    Download archive.lpk
  </a>
</section>
"""


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, "install requirements.txt to run Pawchive parser tests")
class PawchiveAttachmentTests(unittest.TestCase):
    def test_parses_download_section_attachment(self) -> None:
        detail = parse_post_detail(
            DETAIL_HTML,
            "https://pawchive.pw/patreon/user/1/post/2",
            image_source=ImageSource.PREVIEW,
            include_other_attachments=True,
        )
        self.assertEqual([item.name for item in detail.files], ["first.png", "法尔伽-醉酒【ENG】1.4.lpk"])
        self.assertEqual(detail.files[0].url, "https://pawchive.pw/thumbnail/a.png")
        self.assertEqual(detail.files[1].url, "https://file.pawchive.pw/data/archive.lpk?f=archive.lpk")

    def test_build_tasks_keeps_images_attachments_and_content(self) -> None:
        class FakeApi:
            async def list_posts(self, creator_url: str) -> list[PostItem]:
                return []

            async def get_post_files(self, post: PostItem) -> list[FileItem]:
                return [
                    FileItem(url="https://img.pawchive.pw/a.png", name="first.png", kind="image"),
                    FileItem(url="https://file.pawchive.pw/archive.lpk", name="archive.lpk", kind="file"),
                ]

            async def get_post_content(self, post: PostItem) -> str:
                return "<p>Example</p>"

        post = PostItem(
            id="2",
            title="Example",
            published="2026-08-18 20:58:46",
            url="https://pawchive.pw/patreon/user/1/post/2",
            creator_id="1",
            service="patreon",
        )
        with TemporaryDirectory() as temporary_directory:
            tasks = asyncio.run(
                DownloadService(FakeApi()).build_tasks(
                    [post],
                    Path(temporary_directory),
                    DateNamingMode.PREFIX,
                )
            )
        self.assertEqual([task.file.kind for task in tasks], ["text", "image", "file"])
        self.assertEqual([task.file.name for task in tasks], ["content.txt", "01.png", "archive.lpk"])
    def test_single_folder_keeps_original_names_and_avoids_cross_post_collisions(self) -> None:
        class DuplicateApi:
            async def list_posts(self, creator_url: str) -> list[PostItem]:
                return []

            async def get_post_files(self, post: PostItem) -> list[FileItem]:
                return [FileItem(url=f"https://file.pawchive.pw/{post.id}.png", name="same.png", kind="image")]

            async def get_post_content(self, post: PostItem) -> str:
                return "<p>Example</p>"

        posts = [
            PostItem("1", "First", "2026-08-18", "https://pawchive.pw/patreon/user/1/post/1", "1", "patreon"),
            PostItem("2", "Second", "2026-08-17", "https://pawchive.pw/patreon/user/1/post/2", "1", "patreon"),
        ]
        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            tasks = asyncio.run(
                DownloadService(DuplicateApi()).build_tasks(
                    posts,
                    output_dir,
                    DateNamingMode.PREFIX,
                    rename_images_enabled=False,
                    single_folder=True,
                )
            )
        self.assertEqual([task.save_path.parent for task in tasks], [output_dir] * len(tasks))
        self.assertEqual([task.file.name for task in tasks if task.file.kind == "image"], ["same.png", "same.png"])
        self.assertEqual([task.save_path.name for task in tasks if task.file.kind == "image"], ["same.png", "same_1.png"])


if __name__ == "__main__":
    unittest.main()
