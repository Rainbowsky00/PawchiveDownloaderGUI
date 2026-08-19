from __future__ import annotations

import asyncio
import ctypes
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QFontDatabase, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QApplication,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QScrollArea,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from kemono_downloader.app.ui_helpers import (
    CARD_SIZE,
    FileProgressState,
    choose_displayed_file,
    format_speed,
    grid_columns,
    group_posts_by_year,
    update_file_progress,
)
from kemono_downloader.api.pawchive import PawchiveApi, normalize_creator_path
from kemono_downloader.config import DEFAULT_CONCURRENCY
from kemono_downloader.downloader.engine import DownloadEngine
from kemono_downloader.downloader.models import DateNamingMode, DownloadEvent, DownloadTask, PostItem
from kemono_downloader.downloader.service import DownloadService
from kemono_downloader.downloader.state import StateStore


ASSET_DIR = Path(__file__).resolve().parents[2] / "assets"
BACKGROUND_PATH = ASSET_DIR / " "
FONT_PATH = ASSET_DIR / "YeZiGongChangTangYingHei-2.ttf"
ICON_PATH = ASSET_DIR / " "
FAILED_EXPORT_NAME = "failed_downloads.txt"
DEFAULT_FONT_FAMILY = "Microsoft YaHei UI"
WINDOWS_APP_ID = "PawchiveDownloader.PreviewImageDownloader"
ANNOUNCEMENT_TEXT = """📢 公告：
更新
github：https://github.com/slbidd/KemonoDownloaderGUI
项目开源免费，付费请举报。

2026年8月19日：
已正常将kemono项目迁移，包括更新，下载原图、下载附件等功能全部添加完毕。"""


class AnnouncementDialog(QDialog):
    def __init__(self, text: str, font_family: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("公告")
        self.setWindowIcon(load_app_icon())
        self.resize(560, 360)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("公告")
        title.setObjectName("announcementTitle")
        layout.addWidget(title)

        content = QPlainTextEdit()
        content.setObjectName("announcementContent")
        content.setReadOnly(True)
        content.setPlainText(text)
        layout.addWidget(content, stretch=1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        self.setStyleSheet(
            f"""
            QDialog {{
                background: rgba(38, 38, 64, 245);
                color: rgba(255, 255, 255, 235);
                font-family: "{font_family}", "Microsoft YaHei UI", "Segoe UI";
                font-size: 14px;
            }}
            QLabel#announcementTitle {{
                color: rgba(255, 255, 255, 245);
                font-size: 18px;
                font-weight: 700;
            }}
            QPlainTextEdit#announcementContent {{
                background: rgba(255, 255, 255, 28);
                border: 1px solid rgba(255, 255, 255, 80);
                border-radius: 8px;
                color: rgba(255, 255, 255, 235);
                padding: 8px;
                outline: none;
            }}
            QPushButton {{
                min-height: 32px;
                border: 1px solid rgba(255, 255, 255, 150);
                border-radius: 8px;
                background: rgba(255, 255, 255, 180);
                color: #25313d;
                padding: 5px 18px;
                outline: none;
            }}
            QPushButton:focus {{
                outline: none;
            }}
            """
        )


class PostCard(QWidget):
    selection_changed = pyqtSignal(bool)

    def __init__(self, post: PostItem):
        super().__init__()
        self.post = post
        self._selected = True
        self.setObjectName("postCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel(post.title)
        title.setObjectName("postCardTitle")
        title.setWordWrap(True)
        title.setMaximumHeight(74)
        title.setToolTip(post.title)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title, stretch=1)

        published = QLabel(post.day)
        published.setObjectName("postCardMeta")
        published.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(published)

        counts = QLabel(f"图片 {post.image_count} · 附件 {post.attachment_count}")
        counts.setObjectName("postCardMeta")
        counts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(counts)
        self._refresh_selection_style()

    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._refresh_selection_style()
        self.selection_changed.emit(selected)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_selected(not self._selected)
        super().mousePressEvent(event)

    def _refresh_selection_style(self) -> None:
        self.setProperty("selected", self._selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class PostLoader(QObject):
    posts_loaded = pyqtSignal(list)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.service = DownloadService(PawchiveApi())

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            posts = asyncio.run(self._main())
            self.posts_loaded.emit(posts)
        except Exception as exc:
            self.error.emit(str(exc))

    async def _main(self) -> list[PostItem]:
        self.log.emit("正在读取帖子列表...")
        posts = await self.service.list_posts(self.url)
        self.log.emit(f"正在扫描 {len(posts)} 条帖子的图片和附件...")
        await self.service.api.populate_file_counts(posts)
        self.log.emit(f"读取完成，共 {len(posts)} 条帖子")
        return posts


class DownloadWorker(QObject):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int, int)
    file_event = pyqtSignal(object)
    failed_file = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        creator_url: str,
        posts: list[PostItem],
        output_dir: Path,
        concurrency: int,
        service: DownloadService,
        rename_images_enabled: bool,
        single_folder: bool,
    ):
        super().__init__()
        self.creator_url = creator_url
        self.posts = posts
        self.output_dir = output_dir
        self.concurrency = concurrency
        self.service = service
        self.rename_images_enabled = rename_images_enabled
        self.single_folder = single_folder
        self.naming_mode = DateNamingMode.PREFIX
        self.failed_export_path = output_dir / FAILED_EXPORT_NAME
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self.pause_event.set()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.set()

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            self.error.emit(str(exc))

    async def _main(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log.emit(f"正在扫描 {len(self.posts)} 条帖子...")
        tasks: list[DownloadTask] = []
        for index, post in enumerate(self.posts, start=1):
            if self.stop_event.is_set():
                self.finished.emit("已取消下载")
                return
            batch = await self.service.build_post_tasks(
                post,
                self.output_dir,
                self.naming_mode,
                rename_images_enabled=self.rename_images_enabled,
                single_folder=self.single_folder,
            )
            tasks.extend(batch)
            self.log.emit(f"扫描 {index}/{len(self.posts)}: {post.title}（{len(batch)} 项）")
        if not tasks:
            raise RuntimeError("未发现可下载的文件")
        self._log_task_plan(tasks)

        if self.failed_export_path.exists():
            self.failed_export_path.unlink()

        state = StateStore(f"pawchive_{normalize_creator_path(self.creator_url)}")
        engine = DownloadEngine(
            concurrency=self.concurrency,
            pause_event=self.pause_event,
            stop_event=self.stop_event,
        )
        def on_event(event: DownloadEvent) -> None:
            if event.type == "started":
                self.progress.emit(0, event.total or 0, 0)
                self.log.emit(f"开始下载 {event.total or 0} 个文件任务")
            elif event.type in {"file_started", "file_progress", "file_finished", "file_skipped", "file_failed", "file_cancelled"}:
                self.file_event.emit(event)
                if event.type == "file_finished":
                    self.log.emit(f"完成: {event.file_name}")
                elif event.type == "file_skipped":
                    self.log.emit(f"跳过: {event.file_name}")
                elif event.type == "file_failed":
                    text = self._format_failed_event(event)
                    self._append_failed_export(text)
                    self.failed_file.emit(f"{event.file_name} | {event.path}")
                    self.log.emit(f"失败: {event.file_name} - {event.message}")
            elif event.type == "post_finished":
                if event.failed:
                    self.log.emit(f"帖子处理完成，有失败文件: {event.post_id}")
            elif event.type in {"finished", "cancelled"}:
                self.finished.emit(event.message)

        await engine.run(tasks, state, on_event)

    def _log_task_plan(self, tasks: list[DownloadTask]) -> None:
        image_count = sum(task.file.kind == "image" for task in tasks)
        attachment_count = sum(task.file.kind == "file" for task in tasks)
        self.log.emit(
            f"扫描完成：图片 {image_count}，附件 {attachment_count}，"
            f"内容文件 {len(tasks) - image_count - attachment_count}，共 {len(tasks)} 项。"
        )
        current_post = ""
        lines: list[str] = []
        for task in tasks:
            if task.post.id != current_post:
                if lines:
                    self.log.emit("\n".join(lines))
                current_post = task.post.id
                lines = [f"[{task.post.day}] {task.post.title}"]
            kind = {"image": "图片", "file": "附件", "text": "内容"}[task.file.kind]
            lines.append(f"  {kind}: {task.file.name} -> {task.save_path}")
        if lines:
            self.log.emit("\n".join(lines))

    def _format_failed_event(self, event: DownloadEvent) -> str:
        data = event.data or {}
        target_path = data.get("target_path") or str(event.path or "")
        target_dir = data.get("target_dir") or str(Path(target_path).parent if target_path else "")
        return "\n".join(
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

    def _append_failed_export(self, text: str) -> None:
        first_write = not self.failed_export_path.exists()
        with self.failed_export_path.open("a", encoding="utf-8") as file:
            if first_write:
                file.write("# Pawchive Downloader failed files\n\n")
            file.write(text)


class MainWindow(QMainWindow):
    def __init__(self, font_family: str = DEFAULT_FONT_FAMILY):
        super().__init__()
        self.font_family = font_family
        self.posts: list[PostItem] = []
        self.post_cards: dict[str, PostCard] = {}
        self.grouped_posts: list[tuple[str, list[PostItem]]] = []
        self.year_grids: list[tuple[list[PostCard], QGridLayout]] = []
        self.active_files: dict[str, FileProgressState] = {}
        self.displayed_file_key: str | None = None
        self.last_file_refresh = 0.0
        self.loader: PostLoader | None = None
        self.worker: DownloadWorker | None = None
        self.announcement_dialog: AnnouncementDialog | None = None
        self.background_ratio = load_background_aspect_ratio()
        self._ratio_resize_guard = False
        self.setWindowTitle("Pawchive Downloader")
        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(self._ratio_size_for_width(820))
        self.resize(self._ratio_size_for_width(1120))

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        title = QLabel("Pawchive Downloader")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        layout.addWidget(self._build_top_panel())
        layout.addWidget(self._build_body(), stretch=1)
        layout.addWidget(self._build_bottom_panel())

        self._apply_style()
        self._set_running(False)

    def _build_top_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("topPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        self.url_input = QLineEdit()
        self.url_input.setObjectName("overlayInput")
        self.url_input.setPlaceholderText("https://pawchive.pw/patreon/user/xxxx")
        self.path_input = QLineEdit()
        self.path_input.setObjectName("overlayInput")
        self.path_input.setPlaceholderText("选择保存目录")

        self.browse_button = QPushButton("浏览")
        self.load_button = QPushButton("加载帖子")
        self.load_button.setObjectName("primaryButton")
        self.browse_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.load_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.browse_button.clicked.connect(self._browse)
        self.load_button.clicked.connect(self._load_posts)
        self.rename_images_checkbox = QCheckBox("按序号重命名图片")
        self.rename_images_checkbox.setChecked(True)
        self.single_folder_checkbox = QCheckBox("全部文件保存到同一文件夹")
        self.single_folder_checkbox.setChecked(False)

        url_label = QLabel("创作者URL：")
        url_label.setObjectName("formLabel")
        path_label = QLabel("保存目录：")
        path_label.setObjectName("formLabel")

        grid.addWidget(url_label, 0, 0)
        grid.addWidget(self.url_input, 0, 1)
        grid.addWidget(self.load_button, 0, 2)
        grid.addWidget(path_label, 1, 0)
        grid.addWidget(self.path_input, 1, 1)
        grid.addWidget(self.browse_button, 1, 2)
        grid.addWidget(self.rename_images_checkbox, 2, 1)
        grid.addWidget(self.single_folder_checkbox, 3, 1)
        grid.setColumnStretch(1, 1)
        return panel

    def _build_body(self) -> QWidget:
        body = QWidget()
        body.setObjectName("bodyPanel")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)

        self.summary_label = QLabel("等待加载帖子")
        self.summary_label.setObjectName("summaryLabel")
        self.progress = QProgressBar()
        self.progress.setObjectName("fileProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.file_progress_label = QLabel("当前文件：未开始")
        self.file_progress_label.setObjectName("fileProgressLabel")
        self.file_progress = QProgressBar()
        self.file_progress.setObjectName("currentFileProgress")
        self.file_progress.setRange(0, 1)
        self.file_progress.setValue(0)
        body_layout.addWidget(self.summary_label)
        body_layout.addWidget(self.progress)
        body_layout.addWidget(self.file_progress_label)
        body_layout.addWidget(self.file_progress)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.start_button = QPushButton("开始下载")
        self.start_button.setObjectName("primaryButton")
        self.pause_button = QPushButton("暂停")
        self.resume_button = QPushButton("继续")
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("dangerButton")
        self.start_button.clicked.connect(self._start_download)
        self.pause_button.clicked.connect(self._pause)
        self.resume_button.clicked.connect(self._resume)
        self.stop_button.clicked.connect(self._stop)
        for button in [self.start_button, self.pause_button, self.resume_button, self.stop_button]:
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            buttons.addWidget(button)
        body_layout.addLayout(buttons)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title = QLabel("帖子列表")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.select_all_button = QPushButton("全选")
        self.invert_button = QPushButton("反选")
        self.select_all_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.invert_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.select_all_button.clicked.connect(self._select_all)
        self.invert_button.clicked.connect(self._invert_selection)
        title_row.addWidget(self.select_all_button)
        title_row.addWidget(self.invert_button)
        body_layout.addLayout(title_row)

        self.post_scroll = QScrollArea()
        self.post_scroll.setObjectName("postScroll")
        self.post_scroll.setWidgetResizable(True)
        self.post_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.post_container = QWidget()
        self.post_container.setObjectName("postContainer")
        self.post_groups_layout = QVBoxLayout(self.post_container)
        self.post_groups_layout.setContentsMargins(0, 0, 0, 0)
        self.post_groups_layout.setSpacing(16)
        self.post_scroll.setWidget(self.post_container)
        body_layout.addWidget(self.post_scroll, stretch=1)
        return body

    def _build_bottom_panel(self) -> QWidget:
        tabs = QTabWidget()
        tabs.setObjectName("tabs")
        tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.failed_list = QListWidget()
        self.failed_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tabs.addTab(self.log_view, "日志")
        tabs.addTab(self.failed_list, "失败文件")
        tabs.setMinimumHeight(120)
        return tabs

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if path:
            self.path_input.setText(path)

    def _load_posts(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请先填写创作者 URL")
            return

        self._clear_post_groups()
        self.posts = []
        self.load_button.setEnabled(False)
        self.summary_label.setText("正在加载帖子...")
        self._log("开始加载帖子列表")
        self.loader = PostLoader(url)
        self.loader.posts_loaded.connect(self._on_posts_loaded)
        self.loader.error.connect(self._on_loader_error)
        self.loader.log.connect(self._log)
        self.loader.start()

    def _on_posts_loaded(self, posts: list[PostItem]) -> None:
        self.posts = posts
        self.grouped_posts = group_posts_by_year(posts, lambda post: post.day)
        self._clear_post_groups()
        self.post_cards = {}
        self.year_grids = []

        for year, year_posts in self.grouped_posts:
            section = QWidget()
            section.setObjectName("yearSection")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(8)

            header = QLabel(f"────────  {year}年  {len(year_posts)}篇帖子  ────────")
            header.setObjectName("yearHeader")
            header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            section_layout.addWidget(header)

            grid_host = QWidget()
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(10)
            grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            cards: list[PostCard] = []
            for post in year_posts:
                card = PostCard(post)
                card.setFixedSize(CARD_SIZE, CARD_SIZE)
                card.selection_changed.connect(lambda _selected: self._update_selection_summary())
                self.post_cards[post.id] = card
                cards.append(card)
            self.year_grids.append((cards, grid))
            section_layout.addWidget(grid_host)
            self.post_groups_layout.addWidget(section)

        self.post_groups_layout.addStretch(1)
        self._relayout_post_grids()
        self.summary_label.setText(f"已加载 {len(posts)} 条帖子，默认全选")
        self.load_button.setEnabled(True)

    def _on_loader_error(self, message: str) -> None:
        self.load_button.setEnabled(True)
        self.summary_label.setText("加载失败")
        self._log(f"加载失败: {message}")
        QMessageBox.warning(self, "加载失败", message)

    def _start_download(self) -> None:
        selected = self._selected_posts()
        output = self.path_input.text().strip()
        url = self.url_input.text().strip()
        if not selected:
            QMessageBox.warning(self, "提示", "请至少选择一条帖子")
            return
        if not output:
            QMessageBox.warning(self, "提示", "请选择保存目录")
            return
        self.failed_list.clear()
        self.active_files = {}
        self.displayed_file_key = None
        self.last_file_refresh = 0.0
        self.progress.setValue(0)
        self.file_progress.setRange(0, 1)
        self.file_progress.setValue(0)
        self.file_progress_label.setText("当前文件：未开始")
        self.progress.setMaximum(0)
        self.summary_label.setText("正在准备下载任务...")
        self._set_running(True)

        self.worker = DownloadWorker(
            creator_url=url,
            posts=selected,
            output_dir=Path(output),
            concurrency=DEFAULT_CONCURRENCY,
            service=self.loader.service if self.loader else DownloadService(PawchiveApi()),
            rename_images_enabled=self.rename_images_checkbox.isChecked(),
            single_folder=self.single_folder_checkbox.isChecked(),
        )
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self._on_progress)
        self.worker.file_event.connect(self._on_file_event)
        self.worker.failed_file.connect(self._on_failed_file)
        self.worker.finished.connect(self._on_download_finished)
        self.worker.error.connect(self._on_download_error)
        self.worker.start()

    def _clear_post_groups(self) -> None:
        while self.post_groups_layout.count():
            item = self.post_groups_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.post_cards = {}
        self.year_grids = []

    def _relayout_post_grids(self) -> None:
        if not hasattr(self, "post_scroll"):
            return
        columns = grid_columns(self.post_scroll.viewport().width())
        for cards, grid in self.year_grids:
            for column in range(max(grid.columnCount(), columns) + 1):
                grid.setColumnStretch(column, 0)
            while grid.count():
                grid.takeAt(0)
            for index, card in enumerate(cards):
                grid.addWidget(card, index // columns, index % columns)
            grid.setColumnStretch(columns, 1)

    def _update_selection_summary(self) -> None:
        selected = sum(card.selected for card in self.post_cards.values())
        self.summary_label.setText(f"已选择 {selected}/{len(self.posts)} 条帖子")

    def _selected_posts(self) -> list[PostItem]:
        selected_ids = {post_id for post_id, card in self.post_cards.items() if card.selected}
        return [post for post in self.posts if post.id in selected_ids]

    def _select_all(self) -> None:
        for card in self.post_cards.values():
            card.set_selected(True)
        self._update_selection_summary()

    def _invert_selection(self) -> None:
        for card in self.post_cards.values():
            card.set_selected(not card.selected)
        self._update_selection_summary()

    def _pause(self) -> None:
        if self.worker:
            self.worker.pause()
            self.pause_button.setEnabled(False)
            self.resume_button.setEnabled(True)
            self._log("已暂停")

    def _resume(self) -> None:
        if self.worker:
            self.worker.resume()
            self.pause_button.setEnabled(True)
            self.resume_button.setEnabled(False)
            self._log("已继续")

    def _stop(self) -> None:
        if self.worker:
            self.worker.stop()
            self._log("正在停止...")

    def _on_file_event(self, event: DownloadEvent) -> None:
        key = str(event.path or event.file_name or "")
        if not key:
            return
        if event.type == "file_started":
            self.active_files.setdefault(
                key,
                FileProgressState(event.file_name or "", 0, None, sample_time=time.monotonic()),
            )
            return
        if event.type == "file_progress":
            now = time.monotonic()
            self.active_files[key] = update_file_progress(
                self.active_files.get(key),
                event.file_name or "",
                event.bytes_current or 0,
                event.bytes_total,
                now,
            )
            if now - self.last_file_refresh >= 0.25:
                self._refresh_displayed_file(now)
            return
        if event.type in {"file_finished", "file_skipped", "file_failed", "file_cancelled"}:
            self.active_files.pop(key, None)
            self._refresh_displayed_file(time.monotonic(), force=True)

    def _refresh_displayed_file(self, now: float, force: bool = False) -> None:
        if not force and now - self.last_file_refresh < 0.25:
            return
        self.last_file_refresh = now
        self.displayed_file_key = choose_displayed_file(self.active_files, self.displayed_file_key)
        if self.displayed_file_key is None:
            self.file_progress.setRange(0, 1)
            self.file_progress.setValue(0)
            self.file_progress_label.setText("当前文件：未开始")
            return

        state = self.active_files[self.displayed_file_key]
        speed_text = f" · {format_speed(state.speed)}" if state.speed > 0 else ""
        if state.total is None or state.total <= 0:
            self.file_progress.setRange(0, 0)
            self.file_progress_label.setText(
                f"当前文件：{state.name} · 已下载 {format_bytes(state.current)} · 大小未知{speed_text}"
            )
            return
        self.file_progress.setRange(0, state.total)
        self.file_progress.setValue(min(state.current, state.total))
        self.file_progress_label.setText(
            f"当前文件：{state.name} · {format_bytes(state.current)} / {format_bytes(state.total)}{speed_text}"
        )

    def _on_progress(self, done: int, total: int, failed: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.summary_label.setText(f"文件进度 {done}/{total}，失败 {failed}")

    def _on_failed_file(self, text: str) -> None:
        self.failed_list.addItem(text)

    def _on_download_finished(self, message: str) -> None:
        self.active_files = {}
        self.displayed_file_key = None
        self._refresh_displayed_file(time.monotonic(), force=True)
        self._log(message)
        if self.failed_list.count():
            failed_path = Path(self.path_input.text().strip()) / FAILED_EXPORT_NAME
            self._log(f"失败记录已导出: {failed_path}")
        self.summary_label.setText(message)
        self._set_running(False)

    def _on_download_error(self, message: str) -> None:
        self._log(f"下载出错: {message}")
        self.summary_label.setText("下载出错")
        self._set_running(False)
        QMessageBox.warning(self, "下载出错", message)

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.load_button.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(running)

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def start_announcement_loader(self) -> None:
        self._show_announcement(ANNOUNCEMENT_TEXT)

    def _show_announcement(self, text: str) -> None:
        dialog = AnnouncementDialog(text, self.font_family)
        dialog.finished.connect(lambda _result: setattr(self, "announcement_dialog", None))
        self.announcement_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def closeEvent(self, event) -> None:
        if self.worker:
            self.worker.stop()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout_post_grids()
        if self._ratio_resize_guard:
            return

        size = event.size()
        if size.height() <= 0:
            return
        if abs((size.width() / size.height()) - self.background_ratio) < 0.01:
            return

        old_size = event.oldSize()
        width_changed = old_size.isValid() and abs(size.width() - old_size.width()) >= abs(
            size.height() - old_size.height()
        )
        next_size = (
            self._ratio_size_for_width(size.width())
            if width_changed or not old_size.isValid()
            else self._ratio_size_for_height(size.height())
        )

        self._ratio_resize_guard = True
        self.resize(next_size)
        self._ratio_resize_guard = False

    def _ratio_size_for_width(self, width: int) -> QSize:
        return QSize(width, max(1, round(width / self.background_ratio)))

    def _ratio_size_for_height(self, height: int) -> QSize:
        return QSize(max(1, round(height * self.background_ratio)), height)

    def _apply_style(self) -> None:
        background_rule = "background: #000000;"
        if BACKGROUND_PATH.exists():
            background_rule = (
                f'border-image: url("{BACKGROUND_PATH.as_posix()}") '
                "0 0 0 0 stretch stretch;"
            )
        style = """
            QWidget#root {
                __BACKGROUND_RULE__
                color: rgba(255, 255, 255, 235);
                font-family: "__FONT_FAMILY__", "Microsoft YaHei UI", "Segoe UI";
                font-size: 14px;
            }
            QWidget#topPanel, QWidget#bodyPanel {
                background: transparent;
                border: none;
            }
            QLabel#titleLabel {
                font-size: 28px;
                font-weight: 700;
                color: rgba(255, 255, 255, 235);
                padding: 2px 0 4px 0;
            }
            QLabel#formLabel, QLabel#sectionTitle {
                color: rgba(255, 255, 255, 245);
                font-weight: 700;
            }
            QLabel#sectionTitle {
                font-size: 16px;
                padding: 6px 0 0 0;
            }
            QLabel#summaryLabel {
                color: rgba(255, 255, 255, 225);
                padding: 4px 0 0 0;
            }
            QLineEdit {
                min-height: 28px;
                border: 1px solid rgba(255, 255, 255, 92);
                border-radius: 8px;
                background: rgba(38, 38, 64, 120);
                color: rgba(255, 255, 255, 240);
                padding: 3px 8px;
                selection-background-color: #3478f6;
            }
            QLineEdit:focus {
                border: 1px solid rgba(255, 255, 255, 170);
                background: rgba(38, 38, 64, 150);
            }
            QLineEdit::placeholder {
                color: rgba(255, 255, 255, 145);
            }
            QPushButton, QTabBar::tab {
                outline: none;
            }
            QPushButton {
                min-height: 28px;
                border: 1px solid rgba(255, 255, 255, 150);
                border-radius: 8px;
                background: rgba(255, 255, 255, 180);
                color: #25313d;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 225);
                border-color: rgba(92, 150, 255, 190);
            }
            QPushButton:pressed {
                background: rgba(219, 234, 254, 220);
            }
            QPushButton:focus {
                outline: none;
                border: 1px solid rgba(255, 255, 255, 150);
            }
            QPushButton:disabled {
                color: rgba(95, 111, 127, 140);
                background: rgba(236, 240, 244, 135);
                border-color: rgba(255, 255, 255, 110);
            }
            QPushButton#primaryButton {
                background: rgba(52, 120, 246, 220);
                border-color: rgba(52, 120, 246, 235);
                color: white;
                font-weight: 600;
            }
            QPushButton#primaryButton:hover {
                background: rgba(37, 107, 232, 235);
            }
            QPushButton#dangerButton {
                background: rgba(255, 245, 245, 190);
                border-color: rgba(242, 184, 184, 190);
                color: #b42318;
            }
            QPushButton#dangerButton:hover {
                background: rgba(255, 231, 231, 230);
            }
            QScrollArea#postScroll, QWidget#postContainer, QPlainTextEdit {
                background: rgba(38, 38, 64, 120);
                border: 1px solid rgba(255, 255, 255, 92);
                border-radius: 8px;
                color: rgba(255, 255, 255, 232);
                padding: 6px;
            }
            QScrollArea#postScroll > QWidget > QWidget {
                background: transparent;
            }
            QWidget#yearSection {
                background: transparent;
            }
            QLabel#yearHeader {
                color: rgba(255, 255, 255, 240);
                font-size: 16px;
                font-weight: 700;
                padding: 4px 0;
            }
            QWidget#postCard {
                background: rgba(38, 38, 64, 150);
                border: 1px solid rgba(255, 255, 255, 72);
                border-radius: 10px;
            }
            QWidget#postCard[selected="true"] {
                background: rgba(52, 120, 246, 235);
                border: 3px solid rgba(255, 255, 255, 240);
            }
            QWidget#postCard:hover {
                border-color: rgba(92, 150, 255, 220);
            }
            QLabel#postCardTitle {
                color: rgba(255, 255, 255, 240);
                font-weight: 600;
            }
            QLabel#postCardMeta {
                color: rgba(255, 255, 255, 175);
            }
            QProgressBar {
                min-height: 14px;
                border: 1px solid rgba(255, 255, 255, 92);
                border-radius: 9px;
                background: rgba(38, 38, 64, 120);
                color: rgba(255, 255, 255, 235);
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 8px;
                background: rgba(52, 120, 246, 215);
            }
            QTabWidget#tabs {
                background: transparent;
                border: none;
            }
            QTabWidget::pane {
                border: none;
                background: transparent;
            }
            QTabBar::tab {
                min-height: 26px;
                padding: 4px 12px;
                border: 1px solid rgba(255, 255, 255, 80);
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                background: rgba(38, 38, 64, 120);
                color: rgba(255, 255, 255, 215);
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background: rgba(38, 38, 64, 170);
                color: rgba(255, 255, 255, 245);
                font-weight: 600;
            }
            """
        self.setStyleSheet(
            style.replace("__BACKGROUND_RULE__", background_rule).replace(
                "__FONT_FAMILY__",
                self.font_family,
            )
        )


def main() -> int:
    set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    font_family = load_app_font()
    app.setFont(QFont(font_family, 10))
    app.setWindowIcon(load_app_icon())
    window = MainWindow(font_family)
    window.show()
    window.start_announcement_loader()
    return app.exec()


def format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(max(0, value))
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{int(value)} B"


def load_app_font() -> str:
    if not FONT_PATH.exists():
        return DEFAULT_FONT_FAMILY

    font_id = QFontDatabase.addApplicationFont(str(FONT_PATH))
    if font_id < 0:
        return DEFAULT_FONT_FAMILY

    families = QFontDatabase.applicationFontFamilies(font_id)
    return families[0] if families else DEFAULT_FONT_FAMILY


def load_app_icon() -> QIcon:
    if not ICON_PATH.exists():
        return QIcon()
    return QIcon(str(ICON_PATH))


def set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except Exception:
        return


def load_background_aspect_ratio() -> float:
    pixmap = QPixmap(str(BACKGROUND_PATH))
    if pixmap.isNull() or pixmap.height() <= 0:
        return 16 / 9
    return pixmap.width() / pixmap.height()


if __name__ == "__main__":
    raise SystemExit(main())
