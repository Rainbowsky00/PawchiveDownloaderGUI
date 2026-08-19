from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import FileItem, PostItem


def build_content_text(post: PostItem, files: list[FileItem], external_links: list[str]) -> str:
    lines: list[str] = [
        f"标题: {post.title}",
        f"发布时间: {post.day}",
        f"原帖链接: {post.url}",
        "",
        "=" * 30,
        "",
    ]

    if post.content_html:
        soup = BeautifulSoup(post.content_html, "html.parser")
        body = soup.get_text(separator="\n", strip=True)
        lines.append("正文:")
        lines.append(body or "正文为空。")
        lines.append("")

        image_links = [
            urljoin(post.url, str(img.get("src") or img.get("data-src")))
            for img in soup.find_all("img")
            if img.get("src") or img.get("data-src")
        ]
        if image_links:
            lines.append("正文内图片链接:")
            lines.extend(f"- {url}" for url in dict.fromkeys(image_links))
            lines.append("")

        html_links = [
            urljoin(post.url, str(a.get("href")))
            for a in soup.find_all("a")
            if a.get("href") and not str(a.get("href")).startswith(("#", "javascript:"))
        ]
        if html_links:
            lines.append("正文内超链接:")
            lines.extend(f"- {url}" for url in dict.fromkeys(html_links))
            lines.append("")
    else:
        lines.append("正文为空。")
        lines.append("")

    if files:
        lines.append("附件:")
        lines.extend(f"- {item.name}: {item.url}" for item in files if item.kind != "text")
        lines.append("")

    if external_links:
        lines.append("外链:")
        lines.extend(f"- {url}" for url in external_links)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"

