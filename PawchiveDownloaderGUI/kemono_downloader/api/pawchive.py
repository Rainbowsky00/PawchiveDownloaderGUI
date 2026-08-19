from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup, Tag

from kemono_downloader.config import INCLUDE_OTHER_ATTACHMENTS
from kemono_downloader.downloader.models import FileItem, PostItem
from kemono_downloader.downloader.naming import is_image_name, sanitize_filename


BASE = "https://pawchive.pw"
PAGE_SIZE = 50
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": f"{BASE}/",
}
POST_PATH_RE = re.compile(r"^/([^/]+)/user/([^/]+)/post/([^/?#]+)$")
CREATOR_PATH_RE = re.compile(r"^/([^/]+)/user/([^/?#]+)$")


class ImageSource(Enum):
    ORIGINAL = "original"
    PREVIEW = "preview"

    @classmethod
    def parse(cls, value: "ImageSource | str") -> "ImageSource":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError as exc:
            raise ValueError(f"未知图片源模式: {value}") from exc


@dataclass(slots=True)
class _PostDetail:
    content_html: str
    files: list[FileItem]


class PawchiveApi:
    def __init__(
        self,
        timeout: int = 30,
        image_source: ImageSource | str = ImageSource.ORIGINAL,
        include_other_attachments: bool = INCLUDE_OTHER_ATTACHMENTS,
    ):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.image_source = ImageSource.parse(image_source)
        self.include_other_attachments = include_other_attachments
        self._detail_cache: dict[str, _PostDetail] = {}

    async def list_posts(self, creator_url: str) -> list[PostItem]:
        creator_path = normalize_creator_path(creator_url)
        posts: list[PostItem] = []
        seen_post_ids: set[str] = set()

        async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=self.timeout) as session:
            for offset in range(0, 1_000_000, PAGE_SIZE):
                url = build_posts_url(creator_path, offset)
                status, html = await self._get_html(session, url)
                if offset and status == 404:
                    break
                if status != 200:
                    raise RuntimeError(f"获取 Pawchive 帖子列表失败: HTTP {status} {url}")

                page_posts = parse_post_summaries(html, url)
                if not page_posts:
                    if offset == 0:
                        raise RuntimeError(f"未能从 Pawchive 创作者页面识别帖子: {url}")
                    break

                new_posts = [post for post in page_posts if post.id not in seen_post_ids]
                if not new_posts:
                    break
                posts.extend(new_posts)
                seen_post_ids.update(post.id for post in new_posts)
                if len(page_posts) < PAGE_SIZE:
                    break

        return posts

    async def get_post_files(self, post: PostItem) -> list[FileItem]:
        return (await self._get_detail(post)).files

    async def populate_file_counts(self, posts: list[PostItem], concurrency: int = 6) -> None:
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def populate(post: PostItem) -> None:
            async with semaphore:
                files = await self.get_post_files(post)
            post.image_count = sum(item.kind == "image" for item in files)
            post.attachment_count = sum(item.kind == "file" for item in files)

        await asyncio.gather(*(populate(post) for post in posts))

    async def get_post_content(self, post: PostItem) -> str:
        return (await self._get_detail(post)).content_html

    async def _get_detail(self, post: PostItem) -> _PostDetail:
        key = f"{post.service}:{post.creator_id}:{post.id}"
        if key not in self._detail_cache:
            async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=self.timeout) as session:
                status, html = await self._get_html(session, post.url)
            if status != 200:
                raise RuntimeError(f"获取 Pawchive 帖子详情失败: HTTP {status} {post.title}")
            self._detail_cache[key] = parse_post_detail(
                html,
                post.url,
                self.image_source,
                self.include_other_attachments,
            )
        return self._detail_cache[key]

    async def _get_html(self, session: aiohttp.ClientSession, url: str) -> tuple[int, str]:
        try:
            async with session.get(url) as response:
                return response.status, await response.text(errors="replace")
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"请求 Pawchive 页面失败: {url} ({exc})") from exc


def normalize_creator_path(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("Pawchive 创作者 URL 不能为空")

    parsed = urlparse(value if "://" in value else f"https://{value}")
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"pawchive.pw", "www.pawchive.pw"}:
        raise ValueError(f"无法识别 Pawchive 创作者 URL: {value}")
    match = CREATOR_PATH_RE.fullmatch(parsed.path.rstrip("/"))
    if not match:
        raise ValueError(f"无法识别 Pawchive 创作者 URL: {value}")
    return f"{match.group(1)}/user/{match.group(2)}"


def build_posts_url(creator_path: str, offset: int) -> str:
    url = f"{BASE}/{creator_path.strip('/')}"
    return url if offset <= 0 else f"{url}?o={offset}"


def parse_post_summaries(html: str, page_url: str) -> list[PostItem]:
    soup = BeautifulSoup(html, "html.parser")
    result: list[PostItem] = []
    seen_urls: set[str] = set()

    for link in soup.select('a[href*="/post/"]'):
        post_url = urljoin(page_url, str(link.get("href", "")))
        if post_url in seen_urls:
            continue
        parsed = urlparse(post_url)
        match = POST_PATH_RE.fullmatch(parsed.path.rstrip("/"))
        if not match or parsed.netloc.lower() not in {"pawchive.pw", "www.pawchive.pw"}:
            continue

        service, creator_id, post_id = match.groups()
        card = _post_container(link)
        title = _text_from_first(card, ".post__title, .post-card__title, .card__title") or link.get_text(" ", strip=True)
        published = _published_from_node(card) or _published_from_node(link)
        result.append(
            PostItem(
                id=post_id,
                title=title or "untitled",
                published=published,
                url=post_url,
                creator_id=creator_id,
                service=service,
            )
        )
        seen_urls.add(post_url)
    return result


def parse_post_detail(
    html: str,
    post_url: str,
    image_source: ImageSource | str = ImageSource.ORIGINAL,
    include_other_attachments: bool = INCLUDE_OTHER_ATTACHMENTS,
) -> _PostDetail:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one(".post__content")
    content_html = content.decode_contents() if content else ""
    source = ImageSource.parse(image_source)
    files: list[FileItem] = []
    seen_urls: set[str] = set()

    for link in soup.select("a.fileThumb[href], a.post__attachment-link[href]"):
        original_url = urljoin(post_url, str(link["href"]))
        image = link.select_one("img[src], img[data-src]")
        preview_value = image.get("src") or image.get("data-src") if image else ""
        preview_url = urljoin(post_url, str(preview_value)) if preview_value else ""
        name = attachment_name(link, original_url)
        kind = "image" if is_image_name(name) else "file"
        if kind == "file" and not include_other_attachments:
            continue
        url = preview_url if kind == "image" and source == ImageSource.PREVIEW and preview_url else original_url
        if url in seen_urls:
            continue
        seen_urls.add(url)
        files.append(FileItem(url=url, name=name, kind=kind))

    return _PostDetail(content_html=content_html, files=files)


def attachment_name(link: Tag, url: str) -> str:
    name = str(link.get("download") or "").strip()
    if not name:
        name = parse_qs(urlparse(url).query).get("f", [""])[0]
    if not name:
        name = PurePosixPath(urlparse(url).path).name
    return sanitize_filename(unquote(name or "file"))


def _post_container(link: Tag) -> Tag:
    for parent in [link, *link.parents]:
        if not isinstance(parent, Tag):
            continue
        classes = " ".join(parent.get("class", []))
        if parent.name in {"article", "li"} or "card" in classes or "post" in classes:
            return parent
    return link


def _text_from_first(node: Tag, selector: str) -> str:
    target = node.select_one(selector)
    return target.get_text(" ", strip=True) if target else ""


def _published_from_node(node: Tag) -> str:
    meta = node.select_one('meta[name="published"]')
    if meta and meta.get("content"):
        return str(meta["content"]).strip()
    time = node.select_one("time[datetime]")
    if time and time.get("datetime"):
        return str(time["datetime"]).strip()
    published = node.select_one(".post__published")
    return published.get_text(" ", strip=True).removeprefix("Published:").strip() if published else ""
