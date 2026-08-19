from __future__ import annotations

import importlib.util
import unittest

DEPENDENCIES_AVAILABLE = all(importlib.util.find_spec(name) for name in ("aiohttp", "bs4"))

if DEPENDENCIES_AVAILABLE:
    from kemono_downloader.api.pawchive import (
        ImageSource,
        build_posts_url,
        normalize_creator_path,
        parse_post_detail,
        parse_post_summaries,
    )
    from kemono_downloader.downloader.models import DateNamingMode, PostItem
    from kemono_downloader.downloader.naming import post_folder_name


LISTING_HTML = """
<main>
  <article class="post-card">
    <a href="/patreon/user/12831133/post/167050871">
      <span class="post__title">First post</span>
      <time datetime="2026-08-18 20:58:46"></time>
    </a>
  </article>
  <article class="post-card">
    <a href="/patreon/user/12831133/post/167050870">
      <span class="post__title">Second post</span>
      <time datetime="2026-08-17 20:58:46"></time>
    </a>
  </article>
</main>
"""

DETAIL_HTML = """
<section id="page" data-service="patreon" data-user="12831133" data-id="167050871">
  <div class="post__content">
    <p>Hello <a href="/artists">artist</a>.</p>
    <img data-src="/images/embedded.png">
  </div>
  <div class="post__files">
    <a class="fileThumb" href="https://file.pawchive.pw/data/a.png?f=first.png" download="first.png">
      <img src="https://img.pawchive.pw/thumbnail/data/a.png">
    </a>
    <a class="fileThumb" href="/data/b.webp?f=second.webp" download="second.webp">
      <img data-src="/thumbnail/data/b.webp">
    </a>
    <a class="fileThumb" href="/data/archive.zip?f=archive.zip" download="archive.zip"></a>
    <a class="fileThumb" href="/data/b.webp?f=second.webp" download="second.webp">
      <img data-src="/thumbnail/data/b.webp">
    </a>
  </div>
</section>
"""


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, "install requirements.txt to run Pawchive parser tests")
class PawchiveApiTests(unittest.TestCase):
    def test_normalizes_creator_url(self) -> None:
        self.assertEqual(
            normalize_creator_path("https://pawchive.pw/patreon/user/12831133/?tag=WIP"),
            "patreon/user/12831133",
        )
        self.assertEqual(
            build_posts_url("patreon/user/12831133", 50),
            "https://pawchive.pw/patreon/user/12831133?o=50",
        )
        with self.assertRaises(ValueError):
            normalize_creator_path("https://kemono.cr/patreon/user/12831133")

    def test_parses_post_listing(self) -> None:
        posts = parse_post_summaries(
            LISTING_HTML,
            "https://pawchive.pw/patreon/user/12831133",
        )
        self.assertEqual([post.id for post in posts], ["167050871", "167050870"])
        self.assertEqual(posts[0].creator_id, "12831133")
        self.assertEqual(posts[0].title, "First post")
        self.assertEqual(posts[0].day, "2026-08-18")

    def test_parses_preview_images_and_deduplicates(self) -> None:
        detail = parse_post_detail(
            DETAIL_HTML,
            "https://pawchive.pw/patreon/user/12831133/post/167050871",
        )
        self.assertIn("Hello", detail.content_html)
        self.assertEqual([item.name for item in detail.files], ["first.png", "second.webp", "archive.zip"])
        self.assertEqual(
            detail.files[0].url,
            "https://file.pawchive.pw/data/a.png?f=first.png",
        )
        self.assertEqual(
            detail.files[1].url,
            "https://pawchive.pw/data/b.webp?f=second.webp",
        )

    def test_parses_original_files_when_selected(self) -> None:
        detail = parse_post_detail(
            DETAIL_HTML,
            "https://pawchive.pw/patreon/user/12831133/post/167050871",
            image_source=ImageSource.ORIGINAL,
            include_other_attachments=True,
        )
        self.assertEqual([item.name for item in detail.files], ["first.png", "second.webp", "archive.zip"])
        self.assertEqual(
            detail.files[1].url,
            "https://pawchive.pw/data/b.webp?f=second.webp",
        )

    def test_uses_safe_date_prefix_for_pawchive_timestamp(self) -> None:
        post = PostItem(
            id="167050871",
            title="A title",
            published="2026-08-18 20:58:46",
            url="https://pawchive.pw/patreon/user/12831133/post/167050871",
            creator_id="12831133",
            service="patreon",
        )
        self.assertEqual(post_folder_name(post, DateNamingMode.PREFIX), "2026-08-18_A title")


if __name__ == "__main__":
    unittest.main()
