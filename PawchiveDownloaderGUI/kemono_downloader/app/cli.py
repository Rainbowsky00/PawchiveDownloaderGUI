from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

from kemono_downloader.api.pawchive import PawchiveApi, normalize_creator_path
from kemono_downloader.config import DEFAULT_CONCURRENCY
from kemono_downloader.downloader.engine import DownloadEngine
from kemono_downloader.downloader.models import DateNamingMode, DownloadEvent, DownloadTask
from kemono_downloader.downloader.service import DownloadService
from kemono_downloader.downloader.state import StateStore


FAILED_EXPORT_NAME = "failed_downloads.txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pawchive Downloader")
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list", help="列出创作者帖子")
    list_cmd.add_argument("url", help="Pawchive 创作者 URL")
    list_cmd.add_argument("--limit", type=int, default=0, help="只显示前 N 条")

    download_cmd = sub.add_parser("download", help="下载创作者帖子")
    download_cmd.add_argument("url", help="Pawchive 创作者 URL")
    download_cmd.add_argument("-o", "--output", required=True, help="保存目录")
    download_cmd.add_argument("-c", "--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="并发数")
    download_cmd.add_argument("--ids", default="", help="只下载指定帖子 ID，逗号分隔")
    download_cmd.add_argument("--limit", type=int, default=0, help="只下载前 N 条，用于测试")
    download_cmd.add_argument("-y", "--yes", action="store_true", help="兼容旧命令；扫描后会自动开始下载")
    download_cmd.add_argument("--keep-image-names", action="store_true", help="保留图片原始文件名")
    download_cmd.add_argument("--single-folder", action="store_true", help="所有文件直接保存到输出目录")
    return parser


async def main_async(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "download" and args.concurrency <= 0:
        print("并发数必须大于 0")
        return 1

    api = PawchiveApi()
    service = DownloadService(api)
    posts = await service.list_posts(args.url)
    if args.limit:
        posts = posts[: args.limit]

    if args.command == "list":
        print(f"正在扫描 {len(posts)} 条帖子中的图片和附件...")
        await api.populate_file_counts(posts)
        for post in posts:
            print(f"{post.id}\t{post.day}\t图片 {post.image_count}\t附件 {post.attachment_count}\t{post.title}")
        print(f"共 {len(posts)} 条")
        return 0

    selected_ids = parse_ids(args.ids)
    if selected_ids:
        posts = [post for post in posts if post.id in selected_ids]
    if not posts:
        print("没有可下载的帖子")
        return 1

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"正在扫描 {len(posts)} 条帖子并生成下载清单...")
    tasks = await service.build_tasks(
        posts,
        output_dir,
        DateNamingMode.PREFIX,
        rename_images_enabled=not args.keep_image_names,
        single_folder=args.single_folder,
    )
    if not tasks:
        print("未发现可下载的文件")
        return 1

    print_task_plan(tasks)

    failed_export_path = output_dir / FAILED_EXPORT_NAME
    if failed_export_path.exists():
        failed_export_path.unlink()
    state = StateStore(f"pawchive_{normalize_creator_path(args.url)}")
    engine = DownloadEngine(concurrency=args.concurrency)

    def on_event(event: DownloadEvent) -> None:
        if event.type == "started":
            print(f"开始下载，共 {event.total or 0} 个文件任务")
        elif event.type == "progress":
            print(f"\r进度 {event.done or 0}/{event.total or 0} 失败 {event.failed or 0}", end="")
        elif event.type == "file_failed":
            append_failed_export(failed_export_path, event)
            print(f"\n失败: {event.file_name} - {event.message}")
        elif event.type == "finished":
            print(f"\n{event.message}")
            if failed_export_path.exists():
                print(f"失败记录已导出: {failed_export_path}")
        elif event.type == "cancelled":
            print(f"\n{event.message}")

    await engine.run(tasks, state, on_event)
    return 0


def parse_ids(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def print_task_plan(tasks: list[DownloadTask]) -> None:
    by_post: dict[str, list[DownloadTask]] = defaultdict(list)
    for task in tasks:
        by_post[task.post.id].append(task)

    image_count = sum(task.file.kind == "image" for task in tasks)
    attachment_count = sum(task.file.kind == "file" for task in tasks)
    content_count = sum(task.file.kind == "text" for task in tasks)
    print(
        f"扫描完成：{len(by_post)} 篇帖子，图片 {image_count}，"
        f"附件 {attachment_count}，内容文件 {content_count}，共 {len(tasks)} 项。"
    )
    for post_tasks in by_post.values():
        post = post_tasks[0].post
        print(f"\n[{post.day}] {post.title}")
        for task in post_tasks:
            print(f"  - {task.file.name} -> {task.save_path}")


def append_failed_export(path: Path, event: DownloadEvent) -> None:
    data = event.data or {}
    target_path = data.get("target_path") or str(event.path or "")
    target_dir = data.get("target_dir") or str(Path(target_path).parent if target_path else "")
    first_write = not path.exists()
    with path.open("a", encoding="utf-8") as file:
        if first_write:
            file.write("# Pawchive Downloader failed files\n\n")
        file.write(
            "\n".join(
                [
                    f"Post: {event.post_id or ''}",
                    f"File: {event.file_name or ''}",
                    f"URL: {data.get('url') or ''}",
                    f"Target: {target_path}",
                    f"Folder: {target_dir}",
                    f"Error: {event.message}",
                    "",
                ]
            )
        )


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
