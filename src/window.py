import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path
import traceback
import logging
import tempfile

from PySide6.QtCore import QDir, QModelIndex, QPersistentModelIndex, QObject, QSortFilterProxyModel, QThread, Qt, Signal, Slot, QTimer, QSettings
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap, QRegion, QClipboard
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListView,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QProxyStyle,
    QStyledItemDelegate,
    QSplitter,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QStyleOptionViewItem,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QStyle
from PySide6.QtCore import QEvent

from .album_art import (
    AlbumArtScanWorker,
    image_size_from_bytes,
    jpeg_scan_type,
    iter_audio_files,
    read_embedded_album_art,
    to_non_progressive_jpeg,
    write_embedded_album_art,
)
from .constants import ALBUM_ART_CACHE_LIMIT, APP_VERSION, AUDIO_FILE_EXTENSIONS
from .music_compatibility import (
    KNOWN_AUDIO_FORMATS,
    MusicCompatibilityScanWorker,
    _ffprobe_audio_info,
    _resolve_ffmpeg_executable,
    get_all_streams,
)
from .models import DriveOption
from .system_info import collect_target_info, format_bytes, list_removable_drives, attempt_unmount_mountpoint

try:
    import mutagen
except ImportError:
    mutagen = None


IMAGE_FILE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

VIDEO_FILE_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".ts",
    ".webm",
    ".wmv",
}

DOCUMENT_FILE_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".epub",
    ".md",
    ".ods",
    ".odt",
    ".pdf",
    ".ppt",
    ".pptx",
    ".rtf",
    ".txt",
    ".xls",
    ".xlsx",
}

ARCHIVE_FILE_EXTENSIONS = {
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tgz",
    ".xz",
    ".zip",
}

PLAYLIST_FILE_EXTENSIONS = {
    ".asx",
    ".cue",
    ".m3u",
    ".m3u8",
    ".pls",
    ".xspf",
}

SUBTITLE_FILE_EXTENSIONS = {
    ".ass",
    ".lrc",
    ".srt",
    ".ssa",
    ".sub",
    ".vtt",
}

EXECUTABLE_FILE_EXTENSIONS = {
    ".app",
    ".bat",
    ".bin",
    ".command",
    ".exe",
    ".msi",
    ".pkg",
    ".run",
    ".sh",
}

FILE_CLEANUP_CATEGORY_ORDER = [
    "Audio",
    "Image",
    "Video",
    "Document",
    "Archive",
    "Playlist",
    "Subtitle",
    "Executable",
    "Hidden",
    "Other",
]


class FileBrowserProxyModel(QSortFilterProxyModel):
    """Adds recursive directory size values and stable numeric sorting for the Size column."""
    selection_changed = Signal(int)

    def __init__(self, source_model: QFileSystemModel, parent=None):
        super().__init__(parent)
        self.setSourceModel(source_model)
        self._directory_size_cache: dict[str, int] = {}
        self._directory_sizes_enabled = False
        self._checked_paths: set[str] = set()
        self._show_music_only = False
        self._batch_update = False

    def begin_batch_update(self):
        self._batch_update = True

    def end_batch_update(self):
        self._batch_update = False
        if self.rowCount() > 0:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, self.columnCount() - 1),
                [Qt.CheckStateRole]
            )
        self.selection_changed.emit(self._selected_files_count())
        self.invalidate()

    def clear_checked_paths(self) -> None:
        self._checked_paths.clear()
        self.selection_changed.emit(0)

    def _selected_files_count(self) -> int:
        count = 0
        try:
            for p in self._checked_paths:
                try:
                    if os.path.isfile(p):
                        count += 1
                except Exception:
                    continue
        except Exception:
            return 0
        return count

    def checked_paths(self) -> set[str]:
        return set(self._checked_paths)

    @property
    def directory_sizes_enabled(self) -> bool:
        return self._directory_sizes_enabled

    def set_directory_sizes_enabled(self, enabled: bool) -> None:
        self._directory_sizes_enabled = enabled

    def set_directory_size_cache(self, cache: dict[str, int]) -> None:
        self._directory_size_cache = cache.copy()

    def has_directory_size_cache(self) -> bool:
        return bool(self._directory_size_cache)

    def clear_size_cache(self) -> None:
        self._directory_size_cache.clear()

    def _directory_size_bytes(self, path: str) -> int:
        cached = self._directory_size_cache.get(path)
        if cached is not None:
            return cached

        total = 0
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            total += self._directory_size_bytes(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            total = 0

        self._directory_size_cache[path] = total
        return total

    def _index_size_bytes(self, source_index) -> int:
        if not source_index.isValid():
            return 0
        source_model = self.sourceModel()
        if source_model is None:
            return 0

        path = source_model.filePath(source_index)
        if source_model.isDir(source_index):
            return self._directory_size_bytes(path)

        try:
            return source_model.size(source_index)
        except Exception:
            return 0

    def _size_value_for_source_index(self, source_index) -> int:
        source_model = self.sourceModel()
        if source_model is None or not source_index.isValid():
            return 0

        if source_model.isDir(source_index):
            if not self._directory_sizes_enabled:
                return -1
            path = source_model.filePath(source_index)
            cached = self._directory_size_cache.get(path)
            return -1 if cached is None else cached

        return self._index_size_bytes(source_index)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return super().data(index, role)

        if role == Qt.CheckStateRole and index.column() == 0:
            source_model = self.sourceModel()
            if source_model is None:
                return Qt.Unchecked
            source_index = self.mapToSource(index)
            path = source_model.filePath(source_index)
            return Qt.Checked if path in self._checked_paths else Qt.Unchecked

        source_index = self.mapToSource(index)
        if index.column() == 1:
            source_model = self.sourceModel()
            if source_model is None:
                return super().data(index, role)

            if source_model.isDir(source_index):
                size_bytes = self._size_value_for_source_index(source_index)
                if role in (Qt.DisplayRole, Qt.EditRole):
                    if not self._directory_sizes_enabled:
                        return "-"
                    return format_bytes(size_bytes) if size_bytes >= 0 else "Scanning..."
                if role == Qt.UserRole:
                    return size_bytes

            size_bytes = self._index_size_bytes(source_index)
            if role in (Qt.DisplayRole, Qt.EditRole):
                return format_bytes(size_bytes)
            if role == Qt.UserRole:
                return size_bytes

        return super().data(index, role)

    def flags(self, index):
        base_flags = super().flags(index)
        if index.isValid() and index.column() == 0:
            return base_flags | Qt.ItemIsUserCheckable
        return base_flags

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.isValid() and index.column() == 0:
            source_model = self.sourceModel()
            if source_model is None:
                return False

            source_index = self.mapToSource(index)
            path = source_model.filePath(source_index)
            # Ignore macOS AppleDouble sidecar files that start with '._'
            if Path(path).name.startswith("._"):
                return False
            if not path:
                return False

            checked = value == Qt.Checked
            affected_paths: set[str] = {path}

            if source_model.isDir(source_index):
                # Cascade selection to all descendant files and folders under
                # this directory. Include every filesystem child found by
                # walking the source tree so folder selection covers contents
                # even if nodes are collapsed or filtered in the view.
                for root, dir_names, file_names in os.walk(path):
                    for name in dir_names + file_names:
                        candidate = str(Path(root) / name)
                        if not Path(candidate).name.startswith("._"):
                            affected_paths.add(candidate)

            if checked:
                self._checked_paths.update(affected_paths)
            else:
                self._checked_paths.difference_update(affected_paths)

            if not getattr(self, "_batch_update", False):
                self.dataChanged.emit(index, index, [Qt.CheckStateRole])
                # Emit only the number of selected files (exclude directories)
                self.selection_changed.emit(self._selected_files_count())
                self.invalidate()
            return True

        return super().setData(index, value, role)

    def lessThan(self, left, right) -> bool:
        if left.column() == 1 and right.column() == 1:
            left_size = self._size_value_for_source_index(left)
            right_size = self._size_value_for_source_index(right)
            return int(left_size or 0) < int(right_size or 0)
        return super().lessThan(left, right)

    def set_show_music_files_only(self, enabled: bool) -> None:
        self._show_music_only = bool(enabled)
        try:
            self.invalidate()
            self.invalidateFilter()
        except Exception:
            pass

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # type: ignore[override]
        source_model = self.sourceModel()
        if source_model is None:
            return False

        idx = source_model.index(source_row, 0, source_parent)
        if not idx.isValid():
            return False

        # Exclude macOS AppleDouble sidecar files that start with '._'
        try:
            name = source_model.fileName(idx)
            if isinstance(name, str) and name.startswith("._"):
                return False
        except Exception:
            pass

        if not self._show_music_only:
            return super().filterAcceptsRow(source_row, source_parent)

        try:
            if source_model.isDir(idx):
                return True
        except Exception:
            return False

        path = source_model.filePath(idx)
        suffix = Path(path).suffix.lower()
        return suffix in AUDIO_FILE_EXTENSIONS


class BrowserCheckStyle(QProxyStyle):
    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PE_IndicatorItemViewItemCheck:
            rect = option.rect.adjusted(1, 1, -1, -1)
            checked = bool(option.state & QStyle.State_On)
            partial = bool(option.state & QStyle.State_NoChange)

            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor("#FFFFFF"), 1))
            painter.setBrush(QColor("#FFFFFF") if checked else QColor("#1F1F1F"))
            painter.drawRect(rect)

            if checked or partial:
                painter.setPen(QPen(QColor("#1F1F1F"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                start_x = rect.left() + rect.width() * 0.22
                start_y = rect.top() + rect.height() * 0.54
                mid_x = rect.left() + rect.width() * 0.42
                mid_y = rect.bottom() - rect.height() * 0.22
                end_x = rect.right() - rect.width() * 0.18
                end_y = rect.top() + rect.height() * 0.24
                painter.drawLine(int(start_x), int(start_y), int(mid_x), int(mid_y))
                painter.drawLine(int(mid_x), int(mid_y), int(end_x), int(end_y))

            painter.restore()
            return

        super().drawPrimitive(element, option, painter, widget)


class BrowserCheckDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if not (index.flags() & Qt.ItemIsUserCheckable):
            super().paint(painter, option, index)
            return

        check_state = index.data(Qt.CheckStateRole)
        if check_state not in (Qt.Checked, Qt.PartiallyChecked, Qt.Unchecked):
            check_state = Qt.Unchecked

        painter.save()

        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        style_option.features &= ~QStyleOptionViewItem.HasCheckIndicator
        style_option.checkState = Qt.Unchecked
        style_option.icon = QIcon()
        style_option.text = ""

        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, style_option, painter, widget)

        box_size = min(14, max(12, option.rect.height() - 6))
        box_x = option.rect.left() + 6
        box_y = option.rect.top() + max(0, (option.rect.height() - box_size) // 2)
        indicator_rect = option.rect.adjusted(0, 0, 0, 0)
        indicator_rect.setRect(box_x, box_y, box_size, box_size)

        painter.setRenderHint(QPainter.Antialiasing, True)

        box_rect = indicator_rect.adjusted(0, 0, -1, -1)
        painter.setPen(QPen(QColor("#FFFFFF"), 1))
        painter.setBrush(QColor("#1F1F1F"))
        painter.drawRect(box_rect)

        icon_rect = option.rect.adjusted(0, 0, 0, 0)
        icon_size = 16
        icon_x = box_rect.right() + 8
        icon_y = option.rect.top() + max(0, (option.rect.height() - icon_size) // 2)
        icon_rect.setRect(icon_x, icon_y, icon_size, icon_size)

        if check_state == Qt.Checked:
            painter.setPen(QPen(QColor("#FFFFFF"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            start_x = box_rect.left() + box_rect.width() * 0.20
            start_y = box_rect.top() + box_rect.height() * 0.54
            mid_x = box_rect.left() + box_rect.width() * 0.42
            mid_y = box_rect.bottom() - box_rect.height() * 0.22
            end_x = box_rect.right() - box_rect.width() * 0.16
            end_y = box_rect.top() + box_rect.height() * 0.24
            painter.drawLine(int(start_x), int(start_y), int(mid_x), int(mid_y))
            painter.drawLine(int(mid_x), int(mid_y), int(end_x), int(end_y))
        elif check_state == Qt.PartiallyChecked:
            painter.setPen(QPen(QColor("#FFFFFF"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            center_y = box_rect.center().y()
            painter.drawLine(
                int(box_rect.left() + box_rect.width() * 0.18),
                int(center_y),
                int(box_rect.right() - box_rect.width() * 0.18),
                int(center_y),
            )

        icon = index.data(Qt.DecorationRole)
        if hasattr(icon, "isNull") and not icon.isNull():
            icon.paint(painter, icon_rect, Qt.AlignCenter, QIcon.Normal, QIcon.On if check_state == Qt.Checked else QIcon.Off)

        text_rect = option.rect.adjusted(0, 0, 0, 0)
        text_rect.setLeft(icon_rect.right() + 12)
        text_rect.setRight(option.rect.right() - 6)
        text_option = QStyleOptionViewItem(style_option)
        text_option.rect = text_rect
        text_option.icon = QIcon()
        text_option.text = str(index.data(Qt.DisplayRole) or "")
        text_option.features &= ~QStyleOptionViewItem.HasDecoration
        text_option.features &= ~QStyleOptionViewItem.HasCheckIndicator
        style.drawItemText(
            painter,
            text_rect,
            text_option.displayAlignment,
            text_option.palette,
            bool(text_option.state & QStyle.State_Enabled),
            text_option.text,
            QPalette.ColorRole.Text,
        )

        painter.restore()
        return

    # Delegate does custom painting only; checkbox mouse handling is
    # managed by the view's mouseReleaseEvent to ensure indentation and
    # hit-testing are consistent across items.


class BrowserTreeView(QTreeView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._check_anchor_path = ""

    def _index_path(self, index) -> str:
        try:
            model = self.model()
            src_idx = model.mapToSource(index)
            return model.sourceModel().filePath(src_idx)
        except Exception:
            return ""

    def _toggle_index_range(self, start_path: str, end_index, check_state) -> None:
        model = self.model()
        if model is None:
            return

        end_path = self._index_path(end_index)
        if not start_path or not end_path:
            return

        # Look UP to find start_path
        first = None
        last = None
        
        current = end_index
        limit = 0
        found_above = False
        while current.isValid() and limit < 1000000:
            if self._index_path(current) == start_path:
                found_above = True
                first = current
                last = end_index
                break
            current = self.indexAbove(current)
            limit += 1
            
        if not found_above:
            # Look DOWN to find start_path
            current = end_index
            limit = 0
            found_below = False
            while current.isValid() and limit < 1000000:
                if self._index_path(current) == start_path:
                    found_below = True
                    first = end_index
                    last = current
                    break
                current = self.indexBelow(current)
                limit += 1
                
            if not found_below:
                # anchor is not visible, fallback to single item
                first = end_index
                last = end_index

        last_path = self._index_path(last)

        if hasattr(model, "begin_batch_update"):
            model.begin_batch_update()

        try:
            current_index = first
            safety_limit = 0
            while current_index.isValid() and safety_limit < 1000000:
                model.setData(current_index, check_state, Qt.CheckStateRole)
                if self._index_path(current_index) == last_path:
                    break
                next_index = self.indexBelow(current_index)
                if not next_index.isValid() or next_index == current_index:
                    break
                current_index = next_index
                safety_limit += 1
        finally:
            if hasattr(model, "end_batch_update"):
                model.end_batch_update()

    def mouseReleaseEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid() and index.column() == 0:
            row_rect = self.visualRect(index)
            # Account for tree indentation so the clickable checkbox area
            # is positioned where the delegate actually draws it. Without
            # this, clicks on the first column text (for shallow items)
            # can be mistaken for checkbox clicks.
            box_size = min(14, max(12, row_rect.height() - 6))
            box_x = row_rect.left() + 6
            box_y = row_rect.top() + max(0, (row_rect.height() - box_size) // 2)
            box_rect = row_rect.adjusted(0, 0, 0, 0)
            box_rect.setRect(box_x, box_y, box_size, box_size)
            if box_rect.contains(event.pos()) and (index.flags() & Qt.ItemIsUserCheckable):
                current_state = index.data(Qt.CheckStateRole)
                next_state = Qt.Unchecked if current_state == Qt.Checked else Qt.Checked
                
                clicked_path = self._index_path(index)
                
                if event.modifiers() & Qt.ShiftModifier and getattr(self, "_check_anchor_path", ""):
                    self._toggle_index_range(self._check_anchor_path, index, next_state)
                else:
                    self.model().setData(index, next_state, Qt.CheckStateRole)
                
                self._check_anchor_path = clicked_path
                event.accept()
                return

        super().mouseReleaseEvent(event)


class DirectorySizeScanWorker(QObject):
    progress = Signal(int)
    finished = Signal(str, dict)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, root_path: str):
        super().__init__()
        self.root_path = root_path
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        try:
            sizes = self._scan_directory_sizes(self.root_path)
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        if sizes is None:
            self.cancelled.emit()
            return

        self.finished.emit(self.root_path, sizes)

    def _scan_directory_sizes(self, root_path: str) -> dict[str, int] | None:
        # Stack frame: [directory path, scandir iterator, subtotal bytes]
        stack: list[list[object]] = []
        sizes: dict[str, int] = {}
        visited_dirs: set[tuple[int, int]] = set()
        scanned_dirs = 0

        try:
            root_stat = os.stat(root_path, follow_symlinks=False)
            visited_dirs.add((root_stat.st_dev, root_stat.st_ino))
            root_iter = os.scandir(root_path)
        except (OSError, PermissionError):
            return {root_path: 0}

        stack.append([root_path, root_iter, 0])

        while stack:
            if self._cancel_requested:
                for _path, entry_iter, _subtotal in stack:
                    try:
                        entry_iter.close()
                    except Exception:
                        pass
                return None

            frame = stack[-1]
            path = frame[0]
            entry_iter = frame[1]

            try:
                entry = next(entry_iter)
            except StopIteration:
                try:
                    entry_iter.close()
                except Exception:
                    pass

                subtotal = int(frame[2])
                sizes[str(path)] = subtotal
                stack.pop()
                if stack:
                    stack[-1][2] = int(stack[-1][2]) + subtotal

                scanned_dirs += 1
                if scanned_dirs % 500 == 0:
                    self.progress.emit(scanned_dirs)
                continue
            except (OSError, PermissionError):
                try:
                    entry_iter.close()
                except Exception:
                    pass
                stack.pop()
                scanned_dirs += 1
                if scanned_dirs % 500 == 0:
                    self.progress.emit(scanned_dirs)
                continue

            try:
                if entry.is_symlink():
                    continue

                if entry.is_file(follow_symlinks=False):
                    frame[2] = int(frame[2]) + entry.stat(follow_symlinks=False).st_size
                    continue

                if entry.is_dir(follow_symlinks=False):
                    try:
                        dir_stat = entry.stat(follow_symlinks=False)
                        inode_key = (dir_stat.st_dev, dir_stat.st_ino)
                        if inode_key in visited_dirs:
                            continue
                        visited_dirs.add(inode_key)
                        child_iter = os.scandir(entry.path)
                    except (OSError, PermissionError):
                        continue
                    stack.append([entry.path, child_iter, 0])
            except (OSError, PermissionError):
                continue

        self.progress.emit(scanned_dirs)
        return sizes


class DriveScanWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    @Slot()
    def run(self) -> None:
        try:
            options = list_removable_drives()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(options)


class ZipBackupWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(self, source_path: Path, zip_path: Path):
        super().__init__()
        self.source_path = source_path
        self.zip_path = zip_path
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        total_files = 0
        processed = 0
        skipped = 0
        try:
            source = self.source_path
            zip_path = self.zip_path

            if not source.exists() or not source.is_dir():
                raise RuntimeError("Source folder is not available.")

            source_files: list[Path] = []
            for root_dir, _dir_names, file_names in os.walk(str(source)):
                if self._cancel_requested:
                    self.cancelled.emit(
                        {
                            "processed": processed,
                            "total": total_files,
                            "partial_zip": str(zip_path),
                        }
                    )
                    return
                root_path = Path(root_dir)
                for file_name in file_names:
                    file_path = root_path / file_name
                    if file_path.is_symlink():
                        skipped += 1
                        continue
                    if file_path.is_file():
                        source_files.append(file_path)

            total_files = len(source_files)

            zip_path.parent.mkdir(parents=True, exist_ok=True)

            root_name = source.name.strip() or "backup"
            with zipfile.ZipFile(
                str(zip_path),
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                archive.writestr(f"{root_name}/", "")

                for current_dir, dir_names, file_names in os.walk(str(source)):
                    if self._cancel_requested:
                        raise InterruptedError("cancelled")

                    current_dir_path = Path(current_dir)
                    rel_dir = current_dir_path.relative_to(source)
                    arc_dir = (Path(root_name) / rel_dir).as_posix()

                    if rel_dir != Path(".") and not dir_names and not file_names:
                        archive.writestr(f"{arc_dir}/", "")

                    for file_name in file_names:
                        file_path = current_dir_path / file_name
                        if file_path.is_symlink():
                            continue
                        if not file_path.is_file():
                            continue

                        if self._cancel_requested:
                            raise InterruptedError("cancelled")

                        rel_file = file_path.relative_to(source).as_posix()
                        arc_name = (Path(root_name) / rel_file).as_posix()
                        archive.write(str(file_path), arcname=arc_name)

                        processed += 1
                        self.progress.emit(processed, max(total_files, 1), rel_file)

            self.finished.emit(
                {
                    "zip_path": str(zip_path),
                    "processed": processed,
                    "total": total_files,
                    "skipped": skipped,
                }
            )
        except InterruptedError:
            try:
                if self.zip_path.exists():
                    self.zip_path.unlink()
            except Exception:
                pass
            self.cancelled.emit(
                {
                    "processed": processed,
                    "total": total_files,
                    "partial_zip": str(self.zip_path),
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class FileTransferWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(self, source_path: Path, destination_path: Path, mode: str):
        super().__init__()
        self.source_path = source_path
        self.destination_path = destination_path
        self.mode = mode
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self) -> None:
        try:
            source = self.source_path
            destination = self.destination_path

            if not source.exists() or not source.is_dir():
                raise RuntimeError("Source folder is not available.")

            skipped = 0
            source_files: list[Path] = []
            for root_dir, _dir_names, file_names in os.walk(str(source)):
                if self._cancel_requested:
                    self.cancelled.emit({"processed": 0, "total": 0})
                    return

                root_path = Path(root_dir)
                for file_name in file_names:
                    file_path = root_path / file_name
                    if file_path.is_symlink():
                        skipped += 1
                        continue
                    if file_path.is_file():
                        source_files.append(file_path)

            total_files = len(source_files)
            processed = 0

            destination.mkdir(parents=True, exist_ok=True)
            for current_dir, dir_names, _file_names in os.walk(str(source)):
                if self._cancel_requested:
                    self.cancelled.emit(
                        {
                            "processed": processed,
                            "total": total_files,
                            "destination": str(destination),
                            "mode": self.mode,
                        }
                    )
                    return

                current_dir_path = Path(current_dir)
                rel_dir = current_dir_path.relative_to(source)
                for dir_name in dir_names:
                    (destination / rel_dir / dir_name).mkdir(parents=True, exist_ok=True)

            for source_file in source_files:
                if self._cancel_requested:
                    self.cancelled.emit(
                        {
                            "processed": processed,
                            "total": total_files,
                            "destination": str(destination),
                            "mode": self.mode,
                        }
                    )
                    return

                rel_file = source_file.relative_to(source)
                target_file = destination / rel_file
                target_file.parent.mkdir(parents=True, exist_ok=True)

                if self.mode == "move":
                    shutil.move(str(source_file), str(target_file))
                else:
                    shutil.copy2(str(source_file), str(target_file))

                processed += 1
                self.progress.emit(processed, max(total_files, 1), rel_file.as_posix())

            if self.mode == "move":
                for current_dir, dir_names, file_names in os.walk(str(source), topdown=False):
                    if dir_names or file_names:
                        continue
                    try:
                        Path(current_dir).rmdir()
                    except Exception:
                        continue

            self.finished.emit(
                {
                    "destination": str(destination),
                    "processed": processed,
                    "total": total_files,
                    "skipped": skipped,
                    "mode": self.mode,
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class MusicConversionWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal(object)

    def __init__(
        self,
        target_path: Path,
        candidates: list[dict[str, object]],
        make_eq_compatible: bool,
        compression_level: int,
        dry_run: bool,
        backup_root: Path | None,
    ):
        super().__init__()
        self.target_path = target_path
        self.candidates = candidates
        self.make_eq_compatible = make_eq_compatible
        self.compression_level = compression_level
        self.dry_run = dry_run
        self.backup_root = backup_root
        self._cancel_requested = False
        self._active_process: subprocess.Popen | None = None
        # per-conversion timeout in seconds
        self.CONVERSION_TIMEOUT = 600

    def request_cancel(self) -> None:
        self._cancel_requested = True
        process = self._active_process
        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass

    def _is_path_within_target(self, target_path: Path, candidate_path: Path) -> bool:
        try:
            candidate_path.relative_to(target_path)
            return True
        except ValueError:
            return False

    def _parse_optional_int(self, value) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _resolve_sample_rate_for_eq_conversion(self, source_file: Path) -> int | None:
        if mutagen is not None:
            try:
                audio = mutagen.File(source_file)
            except Exception:
                audio = None

            info = getattr(audio, "info", None) if audio else None
            if info is not None:
                for attr_name in ("sample_rate", "samplerate"):
                    value = getattr(info, attr_name, None)
                    if value is None:
                        continue
                    try:
                        return int(value)
                    except Exception:
                        continue

        probe_info = _ffprobe_audio_info(source_file)
        if not probe_info or probe_info.get("error"):
            return None
        return self._parse_optional_int(probe_info.get("sample_rate"))

    def _resolve_conversion_stream_metadata(self, source_file: Path) -> tuple[int | None, int | None, int | None]:
        probe_info = _ffprobe_audio_info(source_file)
        if not probe_info or probe_info.get("error"):
            return None, None, None

        stream_index = self._parse_optional_int(probe_info.get("stream_index"))
        sample_rate = self._parse_optional_int(probe_info.get("sample_rate"))
        bit_depth = self._parse_optional_int(probe_info.get("bit_depth"))
        return stream_index, sample_rate, bit_depth

    def _build_music_conversion_command(
        self,
        source_file: Path,
        output_file: Path,
        audio_stream_index: int | None,
        sample_rate: int | None,
        bit_depth: int | None,
    ) -> list[str]:
        max_sample_rate = 192000
        target_bit_depth = 16 if self.make_eq_compatible else 24

        ffmpeg_exec = getattr(self, "_ffmpeg_executable", None) or "ffmpeg"

        command = [
            ffmpeg_exec,
            "-y",
            "-v",
            "error",
            "-i",
            str(source_file),
            "-map",
            f"0:a:{audio_stream_index if audio_stream_index is not None else 0}",
            "-map_metadata",
            "0",
            "-c:a",
            "flac",
            "-f",
            "flac",
            "-compression_level",
            str(self.compression_level),
            "-frame_size",
            "4096",
        ]

        if sample_rate is not None and sample_rate > max_sample_rate:
            command.extend(["-ar", str(max_sample_rate)])

        if bit_depth is not None and bit_depth > target_bit_depth:
            if target_bit_depth == 16:
                command.extend(["-sample_fmt", "s16", "-bits_per_raw_sample", "16"])
            else:
                command.extend(["-sample_fmt", "s32", "-bits_per_raw_sample", "24"])
        elif self.make_eq_compatible:
            command.extend(["-sample_fmt", "s16", "-bits_per_raw_sample", "16"])

        command.append(str(output_file))
        return command

    def _run_conversion_subprocess(self, command: list[str]) -> tuple[bool, str]:
        logger = logging.getLogger(__name__)
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except Exception as exc:
            return False, str(exc)

        self._active_process = process
        start_time = time.time()
        try:
            while True:
                if self._cancel_requested:
                    try:
                        process.terminate()
                        process.wait(timeout=2)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
                    return True, "Cancelled by user"

                try:
                    process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    # enforce conversion timeout
                    elapsed = time.time() - start_time
                    if elapsed > self.CONVERSION_TIMEOUT:
                        try:
                            process.kill()
                        except Exception:
                            pass
                        return False, f"ffmpeg conversion timed out after {self.CONVERSION_TIMEOUT} seconds"
                    continue

            if process.returncode != 0:
                stderr_bytes = b""
                try:
                    if process.stderr is not None:
                        stderr_bytes = process.stderr.read() or b""
                except Exception:
                    pass
                stderr_text = stderr_bytes.decode(errors="replace").strip()
                logger.debug("ffmpeg stderr: %s", stderr_text)
                short = stderr_text[:2000]
                return False, f"ffmpeg conversion failed (exit code {process.returncode}): {short}"

            return False, ""
        finally:
            self._active_process = None

    def _next_available_backup_path(self, base_path: Path) -> Path:
        if not base_path.exists():
            return base_path

        stem = base_path.stem
        suffix = base_path.suffix
        parent = base_path.parent
        counter = 1
        max_attempts = 10000
        while counter <= max_attempts:
            candidate = parent / f"{stem}.bak{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1
        # Fallback if too many backups exist
        return base_path.with_name(f"{stem}.bak-{int(time.time())}{suffix}")

    @Slot()
    def run(self) -> None:
        converted = 0
        failed = 0
        planned = 0
        failures: list[str] = []
        total = len(self.candidates)

        try:
            if self.backup_root is not None:
                self.backup_root.mkdir(parents=True, exist_ok=True)

            for index, candidate in enumerate(self.candidates, start=1):
                if self._cancel_requested:
                    self.cancelled.emit(
                        {
                            "converted": converted,
                            "failed": failed,
                            "planned": planned,
                            "total": total,
                            "failures": failures,
                            "dry_run": self.dry_run,
                        }
                    )
                    return

                relative_file = str(candidate.get("relative_file") or "")
                sample_rate = self._parse_optional_int(candidate.get("sample_rate"))
                bit_depth = self._parse_optional_int(candidate.get("bit_depth"))

                source_input_path = self.target_path / Path(relative_file)
                detail_label = f"Processing {index}/{total}: {source_input_path.name}"
                self.progress.emit(index - 1, total, detail_label)

                if source_input_path.is_symlink():
                    failed += 1
                    failures.append(f"{relative_file}: symlinked files are not converted")
                    self.progress.emit(index, total, detail_label)
                    continue

                source_path = source_input_path.resolve()
                if not self._is_path_within_target(self.target_path, source_path):
                    failed += 1
                    failures.append(f"{relative_file}: resolves outside the selected target")
                    self.progress.emit(index, total, detail_label)
                    continue

                stream_index, detected_sample_rate, detected_bit_depth = self._resolve_conversion_stream_metadata(source_path)
                if sample_rate is None:
                    sample_rate = detected_sample_rate
                if bit_depth is None:
                    bit_depth = detected_bit_depth

                if not source_path.exists() or not source_path.is_file():
                    failed += 1
                    failures.append(f"{relative_file}: file not found")
                    self.progress.emit(index, total, detail_label)
                    continue

                if self.make_eq_compatible and sample_rate is None:
                    sample_rate = self._resolve_sample_rate_for_eq_conversion(source_path)
                    if sample_rate is None:
                        failed += 1
                        failures.append(
                            f"{relative_file}: sample rate unavailable; cannot guarantee EQ compatibility"
                        )
                        self.progress.emit(index, total, detail_label)
                        continue

                if self.dry_run:
                    planned += 1
                    self.progress.emit(index, total, f"Would convert: {source_path.name}")
                    continue

                output_path = source_path.with_suffix(".flac")
                # create a secure temp file in the destination directory to ensure
                # rename/replace is atomic on the same filesystem
                try:
                    fd, tmp_name = tempfile.mkstemp(prefix=f".{output_path.stem}.tmp", suffix=output_path.suffix, dir=str(source_path.parent))
                    os.close(fd)
                    temp_output_path = Path(tmp_name)
                except Exception:
                    # fallback to a hidden temp name next to the output
                    temp_output_path = output_path.with_name(f".{output_path.stem}.tmp{output_path.suffix}")

                if self.backup_root is not None:
                    backup_target = self.backup_root / Path(relative_file)
                    backup_target.parent.mkdir(parents=True, exist_ok=True)
                    backup_target = self._next_available_backup_path(backup_target)
                    try:
                        shutil.copy2(str(source_path), str(backup_target))
                    except Exception as exc:
                        failed += 1
                        failures.append(f"{relative_file}: backup failed: {exc}")
                        self.progress.emit(index, total, detail_label)
                        continue

                command = self._build_music_conversion_command(
                    source_path,
                    temp_output_path,
                    stream_index,
                    sample_rate,
                    bit_depth,
                )

                was_cancelled, error_text = self._run_conversion_subprocess(command)
                if was_cancelled:
                    try:
                        if temp_output_path.exists():
                            temp_output_path.unlink()
                    except Exception:
                        pass
                    self.cancelled.emit(
                        {
                            "converted": converted,
                            "failed": failed,
                            "planned": planned,
                            "total": total,
                            "failures": failures,
                            "dry_run": self.dry_run,
                        }
                    )
                    return

                if error_text:
                    failed += 1
                    failures.append(f"{relative_file}: {error_text}")
                    try:
                        if temp_output_path.exists():
                            temp_output_path.unlink()
                    except Exception:
                        pass
                    self.progress.emit(index, total, detail_label)
                    continue

                try:
                    if output_path.exists() and output_path != source_path:
                        output_path.unlink()
                    temp_output_path.replace(output_path)
                    if source_path != output_path and source_path.exists():
                        source_path.unlink()
                    converted += 1
                except Exception as exc:
                    failed += 1
                    failures.append(f"{relative_file}: {exc}")
                    try:
                        if temp_output_path.exists():
                            temp_output_path.unlink()
                    except Exception:
                        pass

                self.progress.emit(index, total, detail_label)

            self.finished.emit(
                {
                    "converted": converted,
                    "failed": failed,
                    "planned": planned,
                    "total": total,
                    "failures": failures,
                    "dry_run": self.dry_run,
                    "backup_root": str(self.backup_root) if self.backup_root is not None else "",
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class BackupRestoreProgressDialog(QDialog):
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._allow_close = False
        self._cancel_pending = False
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModal)
        self.setModal(True)
        self.setObjectName("backupRestoreProgressDialog")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self._label = QLabel("Preparing...")
        self._label.setWordWrap(True)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFormat("0/1")
        self._progress.setTextVisible(True)

        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.clicked.connect(self._on_cancel_pressed)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        button_row.addWidget(self._cancel_button)

        layout.addWidget(self._label)
        layout.addWidget(self._progress)
        layout.addLayout(button_row)

        self.setStyleSheet(
            """
            QDialog#backupRestoreProgressDialog {
                background-color: #262626;
                color: #ECECEC;
                border: 1px solid #3C3C3C;
                border-radius: 0px;
            }
            QDialog#backupRestoreProgressDialog QLabel {
                background-color: transparent;
                color: #ECECEC;
            }
            QDialog#backupRestoreProgressDialog QProgressBar {
                background-color: #1F1F1F;
                color: #EAEAEA;
                border: 1px solid #434343;
                border-radius: 0px;
                text-align: center;
                min-height: 18px;
            }
            QDialog#backupRestoreProgressDialog QProgressBar::chunk {
                background-color: #565656;
                border-radius: 0px;
            }
            QDialog#backupRestoreProgressDialog QPushButton {
                background-color: #3A3A3A;
                border: 1px solid #4B4B4B;
                border-radius: 0px;
                color: #F0F0F0;
                padding: 7px 12px;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', sans-serif;
                font-weight: 600;
            }
            QDialog#backupRestoreProgressDialog QPushButton:hover {
                background-color: #4A4A4A;
            }
            QDialog#backupRestoreProgressDialog QPushButton:pressed {
                background-color: #2F2F2F;
            }
            QDialog#backupRestoreProgressDialog QPushButton:disabled {
                background-color: #242424;
                border: 1px solid #353535;
                color: #767676;
            }
            """
        )

    def begin(self, label_text: str) -> None:
        self._allow_close = False
        self._cancel_pending = False
        self._cancel_button.setText("Cancel")
        self._cancel_button.setEnabled(True)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFormat("0/1")
        self._label.setText(label_text)
        if not self.isVisible():
            self.show()
            self.raise_()
            self.activateWindow()

    def update_progress(self, processed: int, total: int, detail_text: str) -> None:
        total_for_ui = max(total, 1)
        shown_value = min(processed, total_for_ui)
        self._progress.setRange(0, total_for_ui)
        self._progress.setValue(shown_value)
        self._progress.setFormat(f"{shown_value}/{total}")
        self._label.setText(detail_text)
        if not self.isVisible():
            self.show()
            self.raise_()
            self.activateWindow()

    def mark_cancelling(self, label_text: str) -> None:
        self._cancel_pending = True
        self._label.setText(label_text)
        self._cancel_button.setText("Cancelling...")
        self._cancel_button.setEnabled(False)
        if not self.isVisible():
            self.show()
            self.raise_()
            self.activateWindow()

    def finish_and_hide(self) -> None:
        self._allow_close = True
        self._cancel_pending = False
        self.hide()

    def closeEvent(self, event) -> None:
        if not self._allow_close:
            if not self._cancel_pending:
                self._on_cancel_pressed()
            event.ignore()
            return
        super().closeEvent(event)

    def _on_cancel_pressed(self) -> None:
        if self._cancel_pending:
            return
        self.mark_cancelling("Cancelling backup/restore job...")
        self.cancelRequested.emit()


class HelpRail(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = True
        self.setObjectName("helpRail")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setToolTip("Toggle help panel")

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.setToolTip("Toggle help panel")
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(QColor("#3C3C3C"))
        painter.drawRect(rect)

        painter.setPen(QColor("#E3E3E3"))

        letters = ["H", "E", "L", "P"]
        font_metrics = painter.fontMetrics()
        slot_height = max(10, font_metrics.height() - 2)
        letter_gap = -1
        total_height = len(letters) * slot_height + (len(letters) - 1) * letter_gap
        top_offset = max(18, (rect.height() - total_height) // 2)
        for index, letter in enumerate(letters):
            y_pos = top_offset + index * (slot_height + letter_gap)
            painter.drawText(0, y_pos, rect.width(), slot_height, Qt.AlignHCenter | Qt.AlignVCenter, letter)


def apply_charcoal_palette(app: QApplication) -> None:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#262626"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#ECECEC"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#1F1F1F"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#242424"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1F1F1F"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#ECECEC"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#F0F0F0"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#3A3A3A"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#F0F0F0"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#9CA6B5"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4A4A4A"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(palette)


class BulkMetadataEditDialog(QDialog):
    """Dialog for bulk editing metadata of selected audio files."""

    def __init__(self, selected_paths: list[str], parent=None):
        super().__init__(parent)
        self.selected_paths = selected_paths
        self.parent_window = parent
        self._init_ui()
        self.setWindowTitle("Bulk Edit Metadata")
        self.setMinimumWidth(400)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        info_label = QLabel(f"Editing metadata for {len(self.selected_paths)} file(s)")
        info_label.setObjectName("targetSummary")
        layout.addWidget(info_label)

        help_label = QLabel(
            "Choose a tag name from the list or type your own. Select Remove to delete that tag from every chosen file."
        )
        help_label.setWordWrap(True)
        help_label.setObjectName("targetSummary")
        layout.addWidget(help_label)

        self.tag_name_combo = QComboBox()
        self.tag_name_combo.setEditable(True)
        self.tag_name_combo.addItems(
            [
                "artist",
                "album",
                "title",
                "date",
                "genre",
                "albumartist",
                "tracknumber",
                "discnumber",
                "comment",
                "lyrics",
            ]
        )
        self.tag_name_combo.setCurrentIndex(-1)
        self.tag_name_combo.setInsertPolicy(QComboBox.NoInsert)
        self.tag_name_combo.lineEdit().setPlaceholderText("Tag name, for example: mood or albumartist")
        layout.addWidget(QLabel("Tag name:"))
        layout.addWidget(self.tag_name_combo)

        self.action_combo = QComboBox()
        self.action_combo.addItems(["Set / replace value", "Remove matching tags"])
        layout.addWidget(QLabel("Action:"))
        layout.addWidget(self.action_combo)

        self.tag_value_input = QLineEdit()
        self.tag_value_input.setPlaceholderText("Tag value")
        layout.addWidget(QLabel("Value:"))
        layout.addWidget(self.tag_value_input)

        self.action_combo.currentIndexChanged.connect(self._update_value_field_state)
        self._update_value_field_state()

        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()
        update_btn = QPushButton("Apply")
        update_btn.clicked.connect(self._on_update)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addStretch()
        button_layout.addWidget(update_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

    def _update_value_field_state(self, *_: object) -> None:
        remove_mode = self.action_combo.currentIndex() == 1
        self.tag_value_input.setEnabled(not remove_mode)
        if remove_mode:
            self.tag_value_input.clear()

    def _on_update(self) -> None:
        tag_name = self.tag_name_combo.currentText().strip()
        if not tag_name:
            QMessageBox.warning(self, "No Tag Selected", "Enter a tag name.")
            return

        remove_only = self.action_combo.currentIndex() == 1
        tag_value = "" if remove_only else self.tag_value_input.text().strip()
        if not remove_only and not tag_value:
            QMessageBox.warning(self, "No Value", "Enter a value or switch the action to Remove matching tags.")
            return

        self.parent_window._bulk_update_metadata(self.selected_paths, tag_name, tag_value, remove_only)
        self.accept()


class ToolboxWindow(QMainWindow):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self.drive_options: list[DriveOption] = []
        self._album_art_cache: OrderedDict[
            tuple[str, int, int], tuple[bytes | None, str]
        ] = OrderedDict()
        self._scan_thread: QThread | None = None
        self._scan_worker: AlbumArtScanWorker | None = None
        self._music_compatibility_scan_thread: QThread | None = None
        self._music_compatibility_scan_worker: MusicCompatibilityScanWorker | None = None
        self._music_compatibility_scan_target: Path | None = None
        self._last_music_compatibility_scan_target: Path | None = None
        self._last_music_compatibility_unsupported_count = 0
        self._last_music_compatibility_eq_incompatible_count = 0
        self._music_conversion_thread: QThread | None = None
        self._music_conversion_worker: MusicConversionWorker | None = None
        self._music_conversion_progress_dialog: QProgressDialog | None = None
        self._music_conversion_busy = False
        self._music_conversion_mode_label = ""
        self._music_conversion_profile_label = ""
        self._drive_scan_thread: QThread | None = None
        self._drive_scan_worker: DriveScanWorker | None = None
        self._backup_restore_thread: QThread | None = None
        self._backup_restore_worker: QObject | None = None
        self._backup_restore_active_operation = ""
        self._backup_restore_active_kind = ""
        self._backup_restore_busy = False
        self._backup_restore_progress_dialog: BackupRestoreProgressDialog | None = None
        self._dir_size_scan_thread: QThread | None = None
        self._dir_size_scan_worker: DirectorySizeScanWorker | None = None
        self._dir_size_scan_target: str | None = None
        self._pending_dir_size_scan_target: str | None = None
        self._about_tab_index = -1
        self._music_compatibility_tab_index = -1
        self._lyrics_manager_tab_index = -1
        self._file_rename_tab_index = -1
        self._cleanup_tab_index = -1
        self._directory_tab_index = -1
        self._backup_restore_tab_index = -1
        self._directory_scan_armed = False
        self._cleanup_scan_target: str | None = None
        self._file_rename_scan_target: str | None = None
        self._lyrics_manager_scan_target: str | None = None
        self._lyrics_manager_scan_results: list[dict[str, object]] = []
        self._lyrics_lookup_results: list[dict[str, object]] = []
        # Cache LRCLIB lookups by signature to avoid duplicate network requests
        self._lrclib_cache: dict[tuple[str, str, str, int], tuple[str, str, str]] = {}
        self._cleanup_type_files: dict[str, list[Path]] = {}
        self._last_scan_target: str | None = None
        self._last_incompatible_files: list[str] = []
        self._active_audio_metadata_path: Path | None = None
        self._updating_file_props_table = False
        # Load persisted help pane preferences
        settings = QSettings()
        self._help_pane_width = settings.value("helpPane/width", 280, type=int)
        self._help_rail_width = 34
        self._help_pane_collapsed = settings.value("helpPane/collapsed", False, type=bool)

        self.setWindowTitle("Snowsky Echo Mini Toolbox")
        self.resize(920, 620)

        self._build_ui()
        self._apply_charcoal_theme()
        # Defer drive refresh until the event loop is running to avoid
        # accessing widgets before their native C++ wrappers are fully initialized.
        QTimer.singleShot(0, self.refresh_drives)

        if initial_path:
            self.path_input.setText(str(Path(initial_path).expanduser()))
            self.show_info()

    def _asset_path(self, *parts: str) -> Path:
        if getattr(sys, "frozen", False):
            base_path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        else:
            base_path = Path(__file__).resolve().parent.parent
        return base_path.joinpath(*parts)

    def _header_logo_pixmap(self) -> QPixmap | None:
        logo_path = self._asset_path("assets", "toolbox-logo.png")
        if not logo_path.exists():
            return None

        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            return None
        return pixmap

    def _position_header_logo(self) -> None:
        if not hasattr(self, "header_logo_label") or not hasattr(self, "_header_logo_source"):
            return
        if self._header_logo_source is None:
            self.header_logo_label.hide()
            return

        root = self.centralWidget()
        if root is None or root.layout() is None:
            return

        margins = root.layout().contentsMargins()
        top_y = self.title_label.geometry().top()
        bottom_y = self.target_label.geometry().bottom()
        available_height = max(24, bottom_y - top_y + 1)

        left_content_width = max(
            self.title_label.sizeHint().width(),
            self.target_label.sizeHint().width(),
        )
        left_content_right = margins.left() + left_content_width
        minimum_gap = 16
        available_width = root.width() - margins.right() - left_content_right - minimum_gap

        if available_width < 24:
            self.header_logo_label.hide()
            return

        scaled = self._header_logo_source.scaled(
            available_width,
            available_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        if scaled.isNull():
            self.header_logo_label.hide()
            return

        x_pos = root.width() - margins.right() - scaled.width()
        min_x_pos = left_content_right + minimum_gap
        if x_pos < min_x_pos:
            self.header_logo_label.hide()
            return
        y_pos = top_y + max(0, (available_height - scaled.height()) // 2)
        self.header_logo_label.setPixmap(scaled)
        self.header_logo_label.setGeometry(x_pos, y_pos, scaled.width(), scaled.height())
        self.header_logo_label.show()
        self.header_logo_label.raise_()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_header_logo()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_header_logo()

    def _configure_resizable_table_columns(
        self, table: QTableWidget, default_widths: list[int] | tuple[int, ...] | None = None
    ) -> None:
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        if default_widths:
            for column, width in enumerate(default_widths):
                if width > 0:
                    table.setColumnWidth(column, width)

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        # print("[DIAGNOSTIC] _build_ui after layout created")

        self.title_label = QLabel("Snowsky Echo Mini Toolbox")
        self.title_label.setObjectName("title")
        layout.addWidget(self.title_label)

        self.target_label = QLabel("Select a Folder or Removable Drive")
        self.target_label.setObjectName("sectionLabel")
        layout.addWidget(self.target_label)

        self.header_logo_label = QLabel(root)
        self.header_logo_label.setObjectName("headerLogo")
        self.header_logo_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.header_logo_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._header_logo_source = self._header_logo_pixmap()
        if self._header_logo_source is not None:
            self.header_logo_label.setToolTip("Snowsky Echo Mini Toolbox")
        else:
            self.header_logo_label.hide()

        path_row = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Folder or drive path")

        browse_btn = QPushButton("Browse Folder")
        browse_btn.clicked.connect(self.browse_folder)

        path_row.addWidget(self.path_input, 1)
        path_row.addWidget(browse_btn)

        drive_row = QHBoxLayout()
        self.drive_combo = QComboBox()
        popup_view = QListView()
        popup_view.setMouseTracking(True)
        popup_view.setUniformItemSizes(True)
        popup_view.setSpacing(2)
        popup_view.setWordWrap(False)
        popup_view.viewport().setAttribute(Qt.WA_Hover, True)
        popup_view.setStyleSheet(
            """
            QListView {
                background-color: #1F1F1F;
                color: #F0F0F0;
                border: 1px solid #434343;
                outline: 0;
            }
            QListView::item {
                min-height: 24px;
                padding: 2px 8px;
                margin: 1px 0px;
                background-color: #1F1F1F;
                color: #F0F0F0;
            }
            QListView::item:hover {
                background-color: #6A6A6A;
                color: #FFFFFF;
            }
            QListView::item:selected {
                background-color: #4A4A4A;
                color: #FFFFFF;
            }
            """
        )
        self.drive_combo.setView(popup_view)
        self.drive_combo.currentIndexChanged.connect(self.pick_drive)
        self.drive_combo.view().setMouseTracking(True)

        self.refresh_drives_btn = QPushButton("Refresh Drives")
        self.refresh_drives_btn.clicked.connect(self.refresh_drives)

        self.unmount_drive_btn = QPushButton("Unmount Drive")
        self.unmount_drive_btn.clicked.connect(self.unmount_selected_drive)
        self.unmount_drive_btn.setEnabled(False)

        drive_row.addWidget(self.drive_combo, 1)
        drive_row.addWidget(self.refresh_drives_btn)
        drive_row.addWidget(self.unmount_drive_btn)
        

        self.current_target_label = QLabel("Current target: none selected")
        self.current_target_label.setObjectName("targetSummary")

        layout.addLayout(path_row)
        layout.addLayout(drive_row)
        layout.addWidget(self.current_target_label)

        self.tabs = QTabWidget()
        

        about_tab = QWidget()
        about_layout = QVBoxLayout(about_tab)
        about_layout.setContentsMargins(10, 10, 10, 10)
        about_layout.setSpacing(10)

        self.path_input.returnPressed.connect(self.show_info)
        # Only trigger a full refresh if the path text actually changed when editing finishes.
        self._last_path_input_text = self.path_input.text().strip()
        self.path_input.editingFinished.connect(self._on_path_input_editing_finished)

        self.info_table = QTableWidget(0, 2)
        self.info_table.setHorizontalHeaderLabels(["Property", "Value"])
        self.info_table.verticalHeader().setVisible(False)
        self._configure_resizable_table_columns(self.info_table, [220, 520])
        # Keep the Property column at its configured width and let Value expand
        info_header = self.info_table.horizontalHeader()
        info_header.setSectionResizeMode(0, QHeaderView.Interactive)
        info_header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.info_table.setAlternatingRowColors(True)
        self.info_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.info_table.setEditTriggers(QTableWidget.NoEditTriggers)

        about_layout.addWidget(self.info_table, 1)

        album_art_tab = QWidget()
        album_art_layout = QVBoxLayout(album_art_tab)
        album_art_layout.setContentsMargins(10, 10, 10, 10)
        album_art_layout.setSpacing(10)

        album_art_controls = QHBoxLayout()
        self.album_art_scan_btn = QPushButton("Scan Album Art Compatibility")
        self.album_art_scan_btn.clicked.connect(self.scan_album_art_compatibility)
        self.album_art_fix_btn = QPushButton("Fix Incompatible Files")
        self.album_art_fix_btn.setEnabled(False)
        self.album_art_fix_btn.clicked.connect(self.fix_incompatible_files)
        album_art_controls.addWidget(self.album_art_scan_btn)
        album_art_controls.addWidget(self.album_art_fix_btn)
        album_art_controls.addStretch(1)

        self.album_art_summary_label = QLabel("No scan run yet.")
        self.album_art_summary_label.setObjectName("targetSummary")

        self.album_art_progress = QProgressBar()
        self.album_art_progress.setRange(0, 1)
        self.album_art_progress.setValue(0)
        self.album_art_progress.setFormat("Idle")
        self.album_art_progress.setTextVisible(True)

        self.album_art_table = QTableWidget(0, 5)
        self.album_art_table.setHorizontalHeaderLabels(
            ["File", "Status", "Progressive", "File Type", "Resolution"]
        )
        self.album_art_table.verticalHeader().setVisible(False)
        self._configure_resizable_table_columns(self.album_art_table, [420, 140, 120, 120, 140])
        self.album_art_table.setAlternatingRowColors(True)
        self.album_art_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.album_art_table.setEditTriggers(QTableWidget.NoEditTriggers)

        album_art_layout.addLayout(album_art_controls)
        album_art_layout.addWidget(self.album_art_summary_label)
        album_art_layout.addWidget(self.album_art_progress)
        album_art_layout.addWidget(self.album_art_table, 1)

        music_compatibility_tab = QWidget()
        music_compatibility_layout = QVBoxLayout(music_compatibility_tab)
        music_compatibility_layout.setContentsMargins(10, 10, 10, 10)
        music_compatibility_layout.setSpacing(10)

        music_compatibility_controls = QHBoxLayout()
        self.music_compatibility_scan_btn = QPushButton("Scan Music Compatibility")
        self.music_compatibility_scan_btn.clicked.connect(self.scan_music_compatibility)
        music_compatibility_controls.addWidget(self.music_compatibility_scan_btn)

        self.music_compatibility_cancel_btn = QPushButton("Cancel Scan")
        self.music_compatibility_cancel_btn.setEnabled(False)
        self.music_compatibility_cancel_btn.clicked.connect(self.cancel_music_compatibility_scan)
        music_compatibility_controls.addWidget(self.music_compatibility_cancel_btn)

        self.music_compatibility_quick_filter_combo = QComboBox()
        self.music_compatibility_quick_filter_combo.addItem("All", "all")
        self.music_compatibility_quick_filter_combo.addItem("Unsupported", "unsupported")
        self.music_compatibility_quick_filter_combo.addItem("Unknown", "unknown")
        self.music_compatibility_quick_filter_combo.addItem("Supported", "supported")
        self.music_compatibility_quick_filter_combo.addItem("Skipped", "skipped")
        self.music_compatibility_quick_filter_combo.addItem("EQ Not Compatible", "eq_not_compatible")
        self.music_compatibility_quick_filter_combo.addItem("Actionable For Convert", "actionable")
        self.music_compatibility_quick_filter_combo.currentIndexChanged.connect(
            lambda _index: self._apply_music_compatibility_table_filter(
                self.music_compatibility_search_input.text()
            )
        )
        music_compatibility_controls.addWidget(self.music_compatibility_quick_filter_combo)

        self.music_compatibility_search_input = QLineEdit()
        self.music_compatibility_search_input.setPlaceholderText(
            "Search compatibility results (file, status, reason, EQ compatibility, extension, block size, DSD...)"
        )
        self.music_compatibility_search_input.setClearButtonEnabled(True)
        self.music_compatibility_search_input.textChanged.connect(
            self._apply_music_compatibility_table_filter
        )
        music_compatibility_controls.addWidget(self.music_compatibility_search_input, 1)

        self.music_compatibility_convert_btn = QPushButton("Convert Incompatible Music")
        self.music_compatibility_convert_btn.setEnabled(False)
        self.music_compatibility_convert_btn.clicked.connect(self.open_music_conversion_dialog)
        music_compatibility_controls.addWidget(self.music_compatibility_convert_btn)

        self.music_compatibility_summary_label = QLabel("No compatibility scan run yet.")
        self.music_compatibility_summary_label.setObjectName("targetSummary")

        self.music_compatibility_progress = QProgressBar()
        self.music_compatibility_progress.setRange(0, 1)
        self.music_compatibility_progress.setValue(0)
        self.music_compatibility_progress.setFormat("Idle")
        self.music_compatibility_progress.setTextVisible(True)

        self.music_compatibility_table = QTableWidget(0, 12)
        self.music_compatibility_table.setHorizontalHeaderLabels(
            [
                "File",
                "Extension",
                "Codec",
                "Status",
                "Reason",
                "Sample Rate (Hz)",
                "Bit Depth",
                "Block Size",
                "DSD",
                "EQ Compatibility",
                "Channels",
                "Streams",
            ]
        )
        eq_header_item = self.music_compatibility_table.horizontalHeaderItem(9)
        if eq_header_item is not None:
            eq_header_item.setToolTip(
                "Indicates equaliser compatibility only. The file can still play, but if marked not compatible the equaliser will be disabled."
            )
        channels_header_item = self.music_compatibility_table.horizontalHeaderItem(10)
        if channels_header_item is not None:
            channels_header_item.setToolTip(
                "Audio channel count from the selected audio stream. Multi-channel FLACs shown here."
            )
        streams_header_item = self.music_compatibility_table.horizontalHeaderItem(11)
        if streams_header_item is not None:
            streams_header_item.setToolTip(
                "Total number of streams in the container (audio, video, artwork, etc.)."
            )
        self.music_compatibility_table.verticalHeader().setVisible(False)
        self._configure_resizable_table_columns(
            self.music_compatibility_table,
            [320, 100, 90, 120, 300, 140, 90, 100, 90, 140, 90, 80],
        )
        self.music_compatibility_table.setAlternatingRowColors(True)
        self.music_compatibility_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.music_compatibility_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.music_compatibility_table.setSortingEnabled(True)
        self.music_compatibility_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.music_compatibility_table.customContextMenuRequested.connect(
            self._on_music_compatibility_table_context_menu
        )

        music_compatibility_layout.addLayout(music_compatibility_controls)
        music_compatibility_layout.addWidget(self.music_compatibility_summary_label)
        music_compatibility_layout.addWidget(self.music_compatibility_progress)
        music_compatibility_layout.addWidget(self.music_compatibility_table, 1)

        lyrics_manager_tab = QWidget()
        lyrics_manager_layout = QVBoxLayout(lyrics_manager_tab)
        lyrics_manager_layout.setContentsMargins(10, 10, 10, 10)
        lyrics_manager_layout.setSpacing(10)

        lyrics_manager_controls = QHBoxLayout()
        self.lyrics_manager_scan_btn = QPushButton("Scan Lyrics")
        self.lyrics_manager_scan_btn.clicked.connect(self.scan_embedded_lyrics)
        self.lyrics_manager_bulk_lookup_btn = QPushButton("Bulk Lookup")
        self.lyrics_manager_bulk_lookup_btn.clicked.connect(self.bulk_lookup_lyrics)
        self.lyrics_manager_export_lrc_btn = QPushButton("Convert Embedded Lyrics To .lrc")
        self.lyrics_manager_export_lrc_btn.clicked.connect(self.convert_embedded_lyrics_to_lrc)
        self.lyrics_manager_apply_lookup_btn = QPushButton("Apply Lookup Results")
        self.lyrics_manager_apply_lookup_btn.setEnabled(False)
        self.lyrics_manager_apply_lookup_btn.clicked.connect(self.apply_bulk_lookup_results)
        lyrics_manager_controls.addWidget(self.lyrics_manager_scan_btn)
        lyrics_manager_controls.addWidget(self.lyrics_manager_bulk_lookup_btn)
        lyrics_manager_controls.addWidget(self.lyrics_manager_export_lrc_btn)
        lyrics_manager_controls.addWidget(self.lyrics_manager_apply_lookup_btn)
        lyrics_manager_controls.addStretch(1)

        self.lyrics_manager_summary_label = QLabel("No lyrics scan run yet.")
        self.lyrics_manager_summary_label.setObjectName("targetSummary")

        self.lyrics_manager_progress = QProgressBar()
        self.lyrics_manager_progress.setRange(0, 1)
        self.lyrics_manager_progress.setValue(0)
        self.lyrics_manager_progress.setFormat("Idle")
        self.lyrics_manager_progress.setTextVisible(True)

        self.lyrics_manager_table = QTableWidget(0, 3)
        self.lyrics_manager_table.verticalHeader().setVisible(False)
        self._configure_lyrics_manager_scan_table()
        self.lyrics_manager_table.setAlternatingRowColors(True)
        self.lyrics_manager_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.lyrics_manager_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.lyrics_manager_table.setSortingEnabled(True)

        lyrics_manager_layout.addLayout(lyrics_manager_controls)
        lyrics_manager_layout.addWidget(self.lyrics_manager_summary_label)
        lyrics_manager_layout.addWidget(self.lyrics_manager_progress)
        lyrics_manager_layout.addWidget(self.lyrics_manager_table, 1)

        file_rename_tab = QWidget()
        file_rename_layout = QVBoxLayout(file_rename_tab)
        file_rename_layout.setContentsMargins(10, 10, 10, 10)
        file_rename_layout.setSpacing(10)

        file_rename_controls = QHBoxLayout()
        self.file_rename_preset_combo = QComboBox()
        self.file_rename_preset_combo.addItem(
            "Metadata Numbering [TrackNo. TrackName]",
            "trackno_trackname",
        )

        self.file_rename_scan_btn = QPushButton("Scan Rename Suggestions")
        self.file_rename_scan_btn.clicked.connect(self.scan_file_rename_suggestions)

        self.file_rename_apply_btn = QPushButton("Rename Selected Files")
        self.file_rename_apply_btn.setEnabled(False)
        self.file_rename_apply_btn.clicked.connect(self.rename_selected_files)

        file_rename_controls.addWidget(self.file_rename_preset_combo)
        file_rename_controls.addWidget(self.file_rename_scan_btn)
        file_rename_controls.addWidget(self.file_rename_apply_btn)
        file_rename_controls.addStretch(1)

        self.file_rename_summary_label = QLabel("No rename scan run yet.")
        self.file_rename_summary_label.setObjectName("targetSummary")

        self.file_rename_table = QTableWidget(0, 6)
        self.file_rename_table.setHorizontalHeaderLabels(
            ["Rename", "Current File", "Suggested File", "Track No", "Track Name", "Reason"]
        )
        self.file_rename_table.verticalHeader().setVisible(False)
        file_rename_header = self.file_rename_table.horizontalHeader()
        file_rename_header.setSectionResizeMode(QHeaderView.Interactive)
        file_rename_header.setStretchLastSection(False)
        file_rename_header.setSectionsClickable(True)
        self.file_rename_table.setColumnWidth(0, 72)
        self.file_rename_table.setColumnWidth(1, 320)
        self.file_rename_table.setColumnWidth(2, 320)
        self.file_rename_table.setColumnWidth(3, 90)
        self.file_rename_table.setColumnWidth(4, 220)
        self.file_rename_table.setColumnWidth(5, 280)
        self.file_rename_table.setAlternatingRowColors(True)
        self.file_rename_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.file_rename_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.file_rename_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_rename_table.customContextMenuRequested.connect(self._on_file_rename_table_context_menu)

        file_rename_layout.addLayout(file_rename_controls)
        file_rename_layout.addWidget(self.file_rename_summary_label)
        file_rename_layout.addWidget(self.file_rename_table, 1)

        browser_tab = QWidget()
        browser_layout = QVBoxLayout(browser_tab)
        browser_layout.setContentsMargins(10, 10, 10, 10)
        browser_layout.setSpacing(10)

        self.browser_root_label = QLabel("")
        self.browser_root_label.setObjectName("targetSummary")

        self.browser_size_progress = QProgressBar()
        self.browser_size_progress.setRange(0, 1)
        self.browser_size_progress.setValue(0)
        self.browser_size_progress.setFormat("Folder size scan idle")
        self.browser_size_progress.setTextVisible(True)
        self.browser_size_progress.hide()

        self.browser_tree = BrowserTreeView()
        self.browser_model = QFileSystemModel(self.browser_tree)
        self.browser_model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot)
        self.browser_model.setReadOnly(True)
        self.browser_proxy_model = FileBrowserProxyModel(self.browser_model, self.browser_tree)
        self.browser_tree.setModel(self.browser_proxy_model)
        self.browser_proxy_model.selection_changed.connect(self._on_browser_selection_changed)
        self.browser_tree.setItemDelegateForColumn(0, BrowserCheckDelegate(self.browser_tree))
        self.browser_tree.setRootIndex(QModelIndex())
        self.browser_tree.setHeaderHidden(False)
        self.browser_tree.setAnimated(True)
        self.browser_tree.setTextElideMode(Qt.ElideNone)
        self.browser_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.browser_tree.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.browser_tree.setSortingEnabled(True)
        

        browser_header = self.browser_tree.header()
        browser_header.setSectionResizeMode(QHeaderView.Interactive)
        browser_header.setStretchLastSection(False)
        browser_header.setSectionsClickable(True)
        browser_header.setSortIndicatorShown(True)
        browser_header.setSortIndicator(0, Qt.AscendingOrder)

        self.browser_tree.sortByColumn(0, Qt.AscendingOrder)

        # Start with roomy defaults; users can drag header separators to adjust.
        self.browser_tree.setColumnWidth(0, 420)
        self.browser_tree.setColumnWidth(1, 130)
        self.browser_tree.setColumnWidth(2, 170)
        self.browser_tree.setColumnWidth(3, 190)

        self.browser_tree.clicked.connect(self.on_browser_item_clicked)
        self.browser_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.browser_tree.customContextMenuRequested.connect(self.on_browser_context_menu)
        self.browser_tree.setEnabled(False)

        browser_right = QWidget()
        browser_right_layout = QVBoxLayout(browser_right)
        browser_right_layout.setContentsMargins(0, 0, 0, 0)
        browser_right_layout.setSpacing(8)

        self.file_details_tabs = QTabWidget()

        properties_tab = QWidget()
        properties_layout = QVBoxLayout(properties_tab)
        properties_layout.setContentsMargins(0, 0, 0, 0)
        properties_layout.setSpacing(12)

        self.file_props_table = QTableWidget(0, 2)
        self.file_props_table.setHorizontalHeaderLabels(["Property", "Value"])
        self.file_props_table.verticalHeader().setVisible(False)
        self.file_props_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        file_props_header = self.file_props_table.horizontalHeader()
        file_props_header.setSectionResizeMode(QHeaderView.Interactive)
        file_props_header.setStretchLastSection(True)
        self.file_props_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.file_props_table.setAlternatingRowColors(True)
        self.file_props_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.file_props_table.setEditTriggers(QTableWidget.NoEditTriggers)

        properties_layout.addWidget(self.file_props_table)

        lyrics_tab = QWidget()
        lyrics_layout = QVBoxLayout(lyrics_tab)
        lyrics_layout.setContentsMargins(8, 8, 0, 0)
        lyrics_layout.setSpacing(12)

        self.file_lyrics_title = QLabel("Embedded Lyrics")
        self.file_lyrics_title.setObjectName("sectionLabel")
        self.file_lyrics_hint = QLabel("Select an audio file to view embedded lyrics.")
        self.file_lyrics_hint.setObjectName("targetSummary")

        self.file_lyrics_text = QPlainTextEdit()
        self.file_lyrics_text.setReadOnly(True)
        self.file_lyrics_text.setLineWrapMode(QPlainTextEdit.WidgetWidth)

        lyrics_layout.addWidget(self.file_lyrics_title)
        lyrics_layout.addWidget(self.file_lyrics_hint)
        lyrics_layout.addWidget(self.file_lyrics_text, 1)

        # Album Art metadata tab (per-file)
        album_art_meta_tab = QWidget()
        album_art_meta_layout = QVBoxLayout(album_art_meta_tab)
        album_art_meta_layout.setContentsMargins(8, 8, 0, 0)
        album_art_meta_layout.setSpacing(12)

        self.file_art_meta_title = QLabel("Embedded Album Art")
        self.file_art_meta_title.setObjectName("sectionLabel")
        self.file_art_meta_hint = QLabel("Select an audio file to view embedded album art.")
        self.file_art_meta_hint.setObjectName("targetSummary")

        self.file_art_meta_preview = QLabel()
        self.file_art_meta_preview.setAlignment(Qt.AlignCenter)
        self.file_art_meta_preview.setObjectName("fileArtPreviewTab")

        # Table for album art metadata (Property / Value)
        self.file_art_meta_table = QTableWidget(0, 2)
        self.file_art_meta_table.setHorizontalHeaderLabels(["Property", "Value"])
        self.file_art_meta_table.verticalHeader().setVisible(False)
        self.file_art_meta_table.setAlternatingRowColors(True)
        self.file_art_meta_table.setEditTriggers(QTableWidget.NoEditTriggers)
        art_header = self.file_art_meta_table.horizontalHeader()
        art_header.setSectionResizeMode(0, QHeaderView.Interactive)
        art_header.setSectionResizeMode(1, QHeaderView.Stretch)

        album_art_meta_layout.addWidget(self.file_art_meta_title)
        album_art_meta_layout.addWidget(self.file_art_meta_hint)
        album_art_meta_layout.addWidget(self.file_art_meta_preview)
        album_art_meta_layout.addWidget(self.file_art_meta_table, 1)

        self.file_details_tabs.addTab(properties_tab, "Properties")
        self.file_details_tabs.addTab(album_art_meta_tab, "Album Art")
        self.file_details_tabs.addTab(lyrics_tab, "Embedded Lyrics")

        browser_right_layout.addWidget(self.file_details_tabs, 2)

        self.lrc_preview_panel = QWidget()
        lrc_preview_layout = QVBoxLayout(self.lrc_preview_panel)
        lrc_preview_layout.setContentsMargins(0, 0, 0, 0)
        lrc_preview_layout.setSpacing(8)

        self.lrc_preview_title = QLabel("LRC File Contents")
        self.lrc_preview_title.setObjectName("sectionLabel")
        self.lrc_preview_hint = QLabel("Select an .lrc file to view contents.")
        self.lrc_preview_hint.setObjectName("targetSummary")

        self.lrc_preview_text = QPlainTextEdit()
        self.lrc_preview_text.setReadOnly(True)
        self.lrc_preview_text.setLineWrapMode(QPlainTextEdit.WidgetWidth)

        lrc_preview_layout.addWidget(self.lrc_preview_title)
        lrc_preview_layout.addWidget(self.lrc_preview_hint)
        lrc_preview_layout.addWidget(self.lrc_preview_text, 1)

        browser_right_layout.addWidget(self.lrc_preview_panel, 1)
        self.lrc_preview_panel.hide()

        browser_splitter = QSplitter(Qt.Horizontal)
        browser_splitter.addWidget(self.browser_tree)
        browser_splitter.addWidget(browser_right)
        browser_splitter.setStretchFactor(0, 3)
        browser_splitter.setStretchFactor(1, 2)

        browser_header_layout = QHBoxLayout()
        browser_header_layout.setContentsMargins(0, 0, 0, 0)
        browser_header_layout.setSpacing(8)
        browser_header_layout.addWidget(self.browser_root_label)
        self.browser_bulk_edit_btn = QPushButton("▼")
        self.browser_bulk_edit_btn.setMaximumWidth(32)
        self.browser_bulk_edit_btn.setEnabled(False)
        self.browser_bulk_edit_btn.clicked.connect(self._show_bulk_metadata_menu)
        browser_header_layout.addWidget(self.browser_bulk_edit_btn)
        # self.browser_show_music_only_chk = QCheckBox("Show music files only")
        # self.browser_show_music_only_chk.setToolTip("Hide non-audio files in the browser view")
        # self.browser_show_music_only_chk.setChecked(False)
        # self.browser_show_music_only_chk.toggled.connect(
        lambda v: self.browser_proxy_model.set_show_music_files_only(v)
        # )
        # browser_header_layout.addWidget(self.browser_show_music_only_chk)
        browser_header_layout.addStretch()

        browser_layout.addLayout(browser_header_layout)
        browser_layout.addWidget(self.browser_size_progress)
        browser_layout.addWidget(browser_splitter, 1)

        cleanup_tab = QWidget()
        cleanup_layout = QVBoxLayout(cleanup_tab)
        cleanup_layout.setContentsMargins(10, 10, 10, 10)
        cleanup_layout.setSpacing(10)

        cleanup_hint = QLabel(
            "Scan the current target, then select file type categories to remove."
        )
        cleanup_hint.setObjectName("targetSummary")

        cleanup_controls = QHBoxLayout()
        self.cleanup_scan_btn = QPushButton("Scan File Types")
        self.cleanup_scan_btn.clicked.connect(self.scan_file_cleanup_breakdown)
        self.cleanup_remove_btn = QPushButton("Remove Selected Types")
        self.cleanup_remove_btn.setEnabled(False)
        self.cleanup_remove_btn.clicked.connect(self.remove_selected_file_types)
        cleanup_controls.addWidget(self.cleanup_scan_btn)
        cleanup_controls.addWidget(self.cleanup_remove_btn)
        cleanup_controls.addStretch(1)

        self.cleanup_summary_label = QLabel("No scan run yet.")
        self.cleanup_summary_label.setObjectName("targetSummary")

        self.cleanup_table = QTableWidget(0, 5)
        self.cleanup_table.setHorizontalHeaderLabels(
            ["Remove", "File Type", "Type", "Files", "Total Size"]
        )
        self.cleanup_table.verticalHeader().setVisible(False)
        self._configure_resizable_table_columns(self.cleanup_table, [80, 220, 120, 100, 150])
        self.cleanup_table.setAlternatingRowColors(True)
        self.cleanup_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.cleanup_table.setEditTriggers(QTableWidget.NoEditTriggers)

        cleanup_layout.addWidget(cleanup_hint)
        cleanup_layout.addLayout(cleanup_controls)
        cleanup_layout.addWidget(self.cleanup_summary_label)
        cleanup_layout.addWidget(self.cleanup_table, 1)

        backup_restore_tab = QWidget()
        backup_restore_layout = QVBoxLayout(backup_restore_tab)
        backup_restore_layout.setContentsMargins(10, 10, 10, 10)
        backup_restore_layout.setSpacing(10)

        backup_restore_hint = QLabel(
            "Back up the current target to a zip file, or copy/move it to another drive/folder."
        )
        backup_restore_hint.setObjectName("targetSummary")

        backup_restore_section_title = QLabel("Backup/Restore Operations")
        backup_restore_section_title.setObjectName("sectionLabel")

        zip_controls = QHBoxLayout()
        self.backup_zip_path_input = QLineEdit()
        self.backup_zip_path_input.setPlaceholderText("Backup zip path (for example: /Volumes/Backup/MyDrive.zip)")
        self.backup_zip_browse_btn = QPushButton("Choose Zip Location")
        self.backup_zip_browse_btn.clicked.connect(self.choose_backup_zip_path)
        self.backup_zip_run_btn = QPushButton("Backup Target To Zip")
        self.backup_zip_run_btn.setEnabled(False)
        self.backup_zip_run_btn.clicked.connect(self.start_zip_backup)
        self.backup_zip_path_input.textChanged.connect(self._update_backup_zip_run_button_state)
        zip_controls.addWidget(self.backup_zip_path_input, 1)
        zip_controls.addWidget(self.backup_zip_browse_btn)
        zip_controls.addWidget(self.backup_zip_run_btn)

        backup_zip_section = QWidget()
        backup_zip_section.setObjectName("panelSection")
        backup_zip_section_layout = QVBoxLayout(backup_zip_section)
        backup_zip_section_layout.setContentsMargins(10, 10, 10, 10)
        backup_zip_section_layout.setSpacing(8)
        backup_zip_title = QLabel("Backup Target To Zip")
        backup_zip_title.setObjectName("sectionLabel")
        backup_zip_hint = QLabel("Create a full zip archive of the selected target.")
        backup_zip_hint.setObjectName("targetSummary")
        backup_zip_section_layout.addWidget(backup_zip_title)
        backup_zip_section_layout.addWidget(backup_zip_hint)
        backup_zip_section_layout.addLayout(zip_controls)

        transfer_controls = QHBoxLayout()
        self.transfer_destination_input = QLineEdit()
        self.transfer_destination_input.setPlaceholderText("Destination root folder or drive (a new folder named after target is created)")
        self.transfer_destination_browse_btn = QPushButton("Choose Destination")
        self.transfer_destination_browse_btn.clicked.connect(self.choose_transfer_destination)
        self.transfer_mode_combo = QComboBox()
        self.transfer_mode_combo.addItem("Copy", "copy")
        self.transfer_mode_combo.addItem("Move", "move")
        self.transfer_run_btn = QPushButton("Start Copy/Move")
        self.transfer_run_btn.setEnabled(False)
        self.transfer_run_btn.clicked.connect(self.start_copy_or_move)
        self.transfer_destination_input.textChanged.connect(self._update_transfer_run_button_state)
        transfer_controls.addWidget(self.transfer_destination_input, 1)
        transfer_controls.addWidget(self.transfer_destination_browse_btn)
        transfer_controls.addWidget(self.transfer_mode_combo)
        transfer_controls.addWidget(self.transfer_run_btn)

        transfer_section = QWidget()
        transfer_section.setObjectName("panelSection")
        transfer_section_layout = QVBoxLayout(transfer_section)
        transfer_section_layout.setContentsMargins(10, 10, 10, 10)
        transfer_section_layout.setSpacing(8)
        transfer_title = QLabel("Copy/Move To Destination")
        transfer_title.setObjectName("sectionLabel")
        transfer_hint = QLabel("Choose destination, then select Copy or Move mode.")
        transfer_hint.setObjectName("targetSummary")
        transfer_section_layout.addWidget(transfer_title)
        transfer_section_layout.addWidget(transfer_hint)
        transfer_section_layout.addLayout(transfer_controls)

        backup_restore_layout.addWidget(backup_restore_section_title)
        backup_restore_layout.addWidget(backup_restore_hint)
        backup_restore_layout.addWidget(backup_zip_section)
        backup_restore_layout.addWidget(transfer_section)
        backup_restore_layout.addStretch(1)

        self._about_tab_index = self.tabs.addTab(about_tab, "About Folder/Drive")
        self._directory_tab_index = self.tabs.addTab(browser_tab, "Music Browser")
        self.tabs.addTab(album_art_tab, "Album Art")
        self._music_compatibility_tab_index = self.tabs.addTab(
            music_compatibility_tab, "Music Compatibility"
        )
        self._lyrics_manager_tab_index = self.tabs.addTab(lyrics_manager_tab, "Lyrics Manager")
        self._file_rename_tab_index = self.tabs.addTab(file_rename_tab, "File Rename")
        self._cleanup_tab_index = self.tabs.addTab(cleanup_tab, "File Cleanup")
        self._backup_restore_tab_index = self.tabs.addTab(backup_restore_tab, "Backup/Restore")
        self.tabs.setTabEnabled(self._directory_tab_index, False)
        self.tabs.currentChanged.connect(self.on_tab_changed)

        self._build_help_pane()
        

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(10)
        content_row.addWidget(self.tabs, 1)
        content_row.addWidget(self.help_container, 0)

        layout.addLayout(content_row, 1)

        self.setCentralWidget(root)
        self._position_header_logo()
        self.status_version_label = QLabel(f"Version: {APP_VERSION}")
        self.status_credit_label = QLabel("Developed by: Silent Sphinx @silent-sphinx")
        self.statusBar().addPermanentWidget(self.status_version_label)
        self.statusBar().addPermanentWidget(self.status_credit_label)
        self.statusBar().showMessage("Ready")
        # UI build complete

    def _on_path_input_editing_finished(self) -> None:
        # If focus moved directly to the browser tree (user clicked an item),
        # ignore editingFinished to avoid triggering an unwanted refresh.
        try:
            fw = QApplication.focusWidget()
            if fw is not None and (fw is self.browser_tree or fw is self.browser_tree.viewport()):
                return
        except Exception:
            pass

        current = self.path_input.text().strip()
        if current == getattr(self, "_last_path_input_text", None):
            return
        self._last_path_input_text = current
        self.show_info()

    def _diagnostic_check(self) -> None:
        """Lightweight runtime diagnostics to help debug an empty main window."""
        try:
            central = self.centralWidget()
            tabs_present = hasattr(self, "tabs") and self.tabs is not None
            browser_present = hasattr(self, "browser_tree") and self.browser_tree is not None
            drive_combo_present = hasattr(self, "drive_combo") and self.drive_combo is not None
            info = {
                "central_widget": bool(central),
                "tabs_count": getattr(self, "tabs", None).count() if tabs_present else None,
                "browser_tree_valid": bool(browser_present),
                "drive_combo_valid": bool(drive_combo_present),
            }
            if not central or (tabs_present and getattr(self, "tabs").count() == 0):
                QMessageBox.warning(
                    self,
                    "Startup Diagnostic",
                    "Main window initialized but content appears missing. See console for details.",
                )
        except Exception:
            pass

    def _build_help_pane(self) -> None:
        self.help_container = QWidget()
        self.help_container.setObjectName("helpContainer")
        self.help_container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        help_container_layout = QHBoxLayout(self.help_container)
        help_container_layout.setContentsMargins(0, 0, 0, 0)
        help_container_layout.setSpacing(0)

        self.help_rail = HelpRail(self.help_container)
        self.help_rail.setFixedWidth(self._help_rail_width)
        self.help_rail.clicked.connect(self._toggle_help_pane)
        help_container_layout.addWidget(self.help_rail, 0)

        self.help_pane = QWidget()
        self.help_pane.setObjectName("helpPane")
        self.help_pane.setMinimumWidth(self._help_pane_width)
        self.help_pane.setFixedWidth(self._help_pane_width)
        help_container_layout.addWidget(self.help_pane, 0)

        help_layout = QVBoxLayout(self.help_pane)
        help_layout.setContentsMargins(8, 8, 8, 8)
        help_layout.setSpacing(8)

        help_header_row = QHBoxLayout()
        help_header_row.setContentsMargins(0, 0, 0, 0)
        help_header_row.setSpacing(6)

        self.help_title_label = QLabel("Help")
        self.help_title_label.setObjectName("sectionLabel")

        self.help_context_label = QLabel("")
        self.help_context_label.setObjectName("targetSummary")

        self.help_close_btn = QPushButton("X")
        self.help_close_btn.setObjectName("helpCloseButton")
        self.help_close_btn.setFixedSize(20, 20)
        self.help_close_btn.clicked.connect(lambda: self._set_help_pane_collapsed(True))

        help_header_row.addWidget(self.help_title_label)
        help_header_row.addStretch(1)
        help_header_row.addWidget(self.help_close_btn)

        self.help_body_label = QLabel()
        self.help_body_label.setObjectName("helpBody")
        self.help_body_label.setWordWrap(True)
        self.help_body_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.help_body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.help_body_scroll = QScrollArea()
        self.help_body_scroll.setObjectName("helpBodyScroll")
        self.help_body_scroll.setWidgetResizable(True)
        self.help_body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.help_body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.help_body_scroll.setWidget(self.help_body_label)

        help_layout.addLayout(help_header_row)
        help_layout.addWidget(self.help_context_label)
        help_layout.addWidget(self.help_body_scroll, 1)

        self._set_help_pane_collapsed(self._help_pane_collapsed)
        self._update_help_for_tab(self.tabs.currentIndex())

    def _toggle_help_pane(self) -> None:
        self._set_help_pane_collapsed(not self._help_pane_collapsed)

    def _set_help_pane_collapsed(self, collapsed: bool) -> None:
        self._help_pane_collapsed = collapsed
        self.help_pane.setVisible(not collapsed)
        self.help_rail.setVisible(collapsed)
        self.help_rail.setExpanded(not collapsed)

        if collapsed:
            self.help_container.setFixedWidth(self._help_rail_width)
        else:
            self.help_container.setFixedWidth(self._help_pane_width)
        try:
            settings = QSettings()
            settings.setValue("helpPane/collapsed", collapsed)
            if not collapsed:
                # persist the last expanded width
                settings.setValue("helpPane/width", int(self.help_pane.width()))
        except Exception:
            pass

    def _update_help_for_tab(self, index: int) -> None:
        tab_name = ""
        if 0 <= index < self.tabs.count():
            tab_name = self.tabs.tabText(index)

        help_by_tab = {
            "About Folder/Drive": (
                "Welcome to the Snowsky Echo Mini Toolbox, your one-stop shop for organizing and converting your music media. To start, select the location of your media by browsing or choosing one of the mounted drives at the top of the interface.\n\n"
                "It is recommended to connect your microSD card directly to your computer using a USB reader, rather than plugging in the Snowsky Echo Mini itself, as the device uses a slower interface.\n\n"
                "The 'About Drive/Folder' tool is useful for ensuring that your microSD card is compatible with your Echo Mini.\n\n"
                "Please note that editing file metadata is likely to corrupt its record in your favourites.\n\n"
                "Filesystem Rules:\n"
                "- Formatted as FAT/FAT32 or exFAT.\n"
                "- Maximum drive size of 256GB.\n\n"
                "Formatting a drive will erase all its contents; always back up your data BEFORE formatting. It is recommended to insert your microSD card into your Echo Mini and format it twice via the Settings menu."
            ),
            "Music Browser": (
                "Inspect file metadata, rename files, and repair individual media.\n\n"
                "Once you have selected your target directory, navigate through the folder structure to identify any files or folders you would like to edit.\n\n"
                "Right-click on any media file to interact with it: Rename, Add/Fix Album Art, or Look up Lyrics.\n\n"
                "After selecting a music file, you can view its metadata and properties on the right-hand side of the screen. Any field marked with a pencil icon can be modified, and the remove button beside it will delete that metadata field."
            ),
            "Album Art": (
                "Detect embedded artwork that may cause compatibility issues and convert it to a supported JPEG non-progressive format.\n\n"
                "Recommended Workflow:\n"
                "1) Click 'Scan Album Art Compatibility.'\n"
                "2) Review the Status, Progressive, File Type, and Resolution columns.\n"
                "3) Use 'Fix Incompatible Files' for batch repairs, or right-click individual files in the Directory Browser for targeted fixes.\n"
                "4) Re-scan after repairs to confirm the results.\n\n"
                "How to Interpret Results:\n"
                "- Compatible: Artwork is already in a supported format.\n"
                "- Incompatible: Art format or scan type requires conversion.\n"
                "- Missing Artwork: No embedded image was found.\n\n"
                "Tips:\n"
                "- Always maintain a backup before performing large batch edits.\n"
                "- If a file fails verification after writing, check the metadata tags and file permissions."
            ),  
            "Music Compatibility": (
                "Evaluate every music file in the target directory against Echo Mini playback rules.\n\n"
                "It is recommended to connect your microSD card directly to your computer using a USB reader, rather than plugging in the Snowsky Echo Mini itself, as the device uses a slower interface.\n\n"
                "Status Guidance:\n"
                "- Supported: The file is expected to play correctly.\n"
                "- Unsupported: The format or attributes conflict with device rules.\n"
                "- Unknown: Metadata was incomplete or inconclusive.\n"
                "- Skipped: The file was not recognized as a valid audio candidate.\n\n"
                "Compatibility Rules:\n"
                "Equalizer Compatibility:\n"
                "For files to be compatible with the internal equalizer they will need to meet additional constraints.\n"
                "- >=16-bit depth\n"
                "- >=192 kHz sample rate\n"
            ),
            "Lyrics Manager": (
                "To add lyrics to your audio files, you will need a supplementary .lrc file; the Snowsky Echo Mini does not support embedded lyric data.\n\n"
                "This tool supports lyrics in two ways:\n"
                "- Extract existing embedded lyrics and convert them to .lrc format.\n"
                "- Search for music online and download .lrc files in bulk.\n\n"
                "Recommended Workflow:\n"
                "1) Click 'Scan Lyrics' to audit embedded lyrics and matching .lrc sidecars.\n"
                "2) If embedded lyrics exist, use 'Convert Embedded Lyrics To .lrc' to export sidecars.\n"
                "3) Use 'Bulk Lookup' to fetch missing lyrics from LRCLIB using track metadata.\n"
                "4) Review lookup results and run 'Apply Lookup Results' to write the .lrc files.\n\n"
                "Operational Notes:\n"
                "- Sidecar naming follows the 'song.ext -> song.lrc' convention in the same folder.\n"
                "- Existing .lrc files may be overwritten when apply/export actions are executed.\n"
                "- Accurate metadata (title, artist, album, duration) significantly improves lookup accuracy."
            ),
            "File Rename": (
                "The File Rename Tool renames your files to follow a standardized format, e.g., 'TrackNumber. TrackTitle'.\n\n"
                "It is highly recommended to back up your files and carefully review the proposed new names before applying changes.\n\n"
                "Select a formatting option from the dropdown menu and scan to see which of your files currently match the anticipated format.\n\n"
                "Metadata Numbering [TrackNo. TrackName]:\n"
                "This setting uses your music file's metadata to set the TrackNo and TrackName as the filename (e.g., '01. Skyfall')."
            ),
            "File Cleanup": (
                "Identify non-essential file categories and remove them to save space.\n\n"
                "Recommended Workflow:\n"
                "1) Run 'Scan File Types' to inventory all discovered categories.\n"
                "2) Sort and inspect category counts and their total impact on storage size.\n"
                "3) Select only the categories you are certain you want to remove.\n"
                "4) Confirm removal and review the completion summary for any failures.\n\n"
                "What to Watch:\n"
                "- Hidden and sidecar files may include metadata you still require.\n"
                "- Large removal sets should be backed up before proceeding.\n"
                "- Permission errors usually indicate protected files or mount constraints.\n\n"
                "Best Practice:\n"
                "Run cleanup in phases, validating player behavior after each stage."
            ),
            "Backup/Restore": (
                "Protect and migrate your library by converting your media into a ZIP file or migrating it to an alternative storage device.\n\n"
                "Modes:\n"
                "- Backup Target To Zip: Creates a compressed snapshot archive.\n"
                "- Start Copy/Move: Clones or relocates the target library into a destination folder.\n\n"
                "Recommended Workflow:\n"
                "1) Carefully confirm the source target and destination paths.\n"
                "2) Perform a backup before starting cleanup, rename, or migration operations.\n"
                "3) Monitor progress and use 'Cancel Job' only when absolutely necessary.\n"
                "4) Verify the destination content after completion before deleting any originals.\n\n"
                "Important Notes:\n"
                "- The 'Move' action is destructive to the source after a successful transfer.\n"
                "- Canceling may leave partial data at the destination; review the folder before retrying."
            ),
        }

        default_help = (
            "This Help pane provides panel-specific guidance.\n\n"
            "Open any tab to see detailed workflows, interpretation tips, and safe operating practices for that panel."
        )
        if tab_name:
            self.help_context_label.setText(f"Tips for: {tab_name}")
        else:
            self.help_context_label.setText("Tips")
        self.help_body_label.setText(help_by_tab.get(tab_name, default_help))

    def _apply_charcoal_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return

        combo_arrow_path = self._asset_path("assets", "combo_down_arrow.svg").resolve().as_posix()

        style_sheet = """
            QMainWindow, QWidget {
                background-color: #262626;
                color: #ECECEC;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', 'Noto Sans', sans-serif;
                font-size: 13px;
            }
            QLabel {
                background-color: transparent;
            }
            QLabel#title {
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', sans-serif;
                font-size: 25px;
                font-weight: 700;
                color: #F4F4F4;
                padding-bottom: 2px;
            }
            QLabel#subtitle {
                color: #B5B5B5;
                padding-bottom: 4px;
            }
            QLabel#sectionLabel {
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', sans-serif;
                color: #E3E3E3;
                font-weight: 600;
                padding-top: 2px;
            }
            QLabel#targetSummary {
                color: #B5B5B5;
            }
            QWidget#helpPane {
                background-color: #262626;
                border: 1px solid #3C3C3C;
                border-radius: 0px;
            }
            QWidget#helpContainer {
                background-color: transparent;
                border: none;
            }
            QWidget#helpRail {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 0px;
            }
            QWidget#helpRail:hover {
                background-color: #333333;
            }
            QPushButton#helpCloseButton {
                background-color: #343434;
                border: 1px solid #4A4A4A;
                border-radius: 0px;
                color: #D7D7D7;
                padding: 0px;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', sans-serif;
                font-size: 12px;
                font-weight: 700;
                min-width: 20px;
                max-width: 20px;
                min-height: 20px;
                max-height: 20px;
            }
            QPushButton#helpCloseButton:hover {
                background-color: #454545;
                color: #FFFFFF;
            }
            QPushButton#helpCloseButton:pressed {
                background-color: #2D2D2D;
            }
            QWidget#panelSection {
                background-color: #2B2B2B;
                border: 1px solid #3C3C3C;
                border-radius: 0px;
            }
            QLabel#helpBody {
                color: #D7D7D7;
                background-color: #262626;
                border: none;
                padding: 2px 2px 4px 2px;
            }
            QScrollArea#helpBodyScroll {
                background-color: #262626;
                border: none;
            }
            QScrollArea#helpBodyScroll > QWidget > QWidget {
                background-color: #262626;
            }
            QScrollArea#helpBodyScroll QScrollBar:vertical {
                background-color: #1F1F1F;
                width: 10px;
                margin: 0px;
            }
            QScrollArea#helpBodyScroll QScrollBar::handle:vertical {
                background-color: #4A4A4A;
                min-height: 30px;
            }
            QScrollArea#helpBodyScroll QScrollBar::handle:vertical:hover {
                background-color: #5A5A5A;
            }
            QScrollArea#helpBodyScroll QScrollBar::add-line:vertical,
            QScrollArea#helpBodyScroll QScrollBar::sub-line:vertical,
            QScrollArea#helpBodyScroll QScrollBar::add-page:vertical,
            QScrollArea#helpBodyScroll QScrollBar::sub-page:vertical {
                background: none;
                border: none;
                height: 0px;
            }
            QLabel#fileArtPreview {
                background-color: #1F1F1F;
                color: #AFAFAF;
                border: 1px solid #434343;
                border-radius: 0px;
            }
            QTabWidget::pane {
                border: 1px solid #3C3C3C;
                background-color: #262626;
                border-radius: 0px;
                margin-top: 6px;
            }
            QTabBar::tab {
                background-color: #303030;
                color: #DCDCDC;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', sans-serif;
                border: 1px solid #3E3E3E;
                border-bottom: none;
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                padding: 7px 12px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background-color: #3A3A3A;
                color: #F4F4F4;
            }
            QTabBar::tab:hover {
                background-color: #3A3A3A;
            }
            QLineEdit, QComboBox, QTableWidget, QPlainTextEdit {
                background-color: #1F1F1F;
                border: 1px solid #434343;
                border-radius: 0px;
                padding: 6px;
                color: #F0F0F0;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', 'Noto Sans', sans-serif;
            }
            QComboBox {
                padding-right: 28px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #5A5A5A;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 22px;
                border: none;
            }
            __COMBO_DOWN_ARROW_RULE__
            QComboBox::down-arrow:on {
                margin-top: 1px;
            }
            QComboBox QAbstractItemView {
                background-color: #1F1F1F;
                color: #F0F0F0;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', 'Noto Sans', sans-serif;
                selection-background-color: #4A4A4A;
                selection-color: #FFFFFF;
                border: 1px solid #434343;
            }
            QComboBox QAbstractItemView::item {
                background-color: #1F1F1F;
                color: #F0F0F0;
                padding: 6px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #565656;
                color: #FFFFFF;
            }
            QComboBox QAbstractItemView::item:selected {
                background-color: #4A4A4A;
                color: #FFFFFF;
            }
            QTableView {
                background-color: #1F1F1F;
                color: #F0F0F0;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', 'Noto Sans', sans-serif;
                selection-background-color: #4A4A4A;
                selection-color: #FFFFFF;
            }
            QTreeView {
                background-color: #1F1F1F;
                color: #F0F0F0;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', 'Noto Sans', sans-serif;
                border: 1px solid #434343;
                border-radius: 0px;
                padding: 4px;
                selection-background-color: #4A4A4A;
                selection-color: #FFFFFF;
            }
            QTreeView::item:hover {
                background-color: #565656;
                color: #FFFFFF;
            }
            QTableWidget {
                gridline-color: #383838;
            }
            QTableCornerButton::section {
                background-color: #333333;
                border: 1px solid #444444;
            }
            QHeaderView::section {
                background-color: #333333;
                color: #E9E9E9;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', 'Noto Sans', sans-serif;
                border: 1px solid #444444;
                padding: 6px;
            }
            QToolTip {
                background-color: #1F1F1F;
                color: #F0F0F0;
                border: 1px solid #434343;
            }
            QProgressBar {
                background-color: #1F1F1F;
                color: #EAEAEA;
                border: 1px solid #434343;
                border-radius: 0px;
                text-align: center;
                min-height: 18px;
            }
            QProgressBar::chunk {
                background-color: #565656;
                border-radius: 0px;
            }
            QPushButton {
                background-color: #3A3A3A;
                border: 1px solid #4B4B4B;
                border-radius: 0px;
                color: #F0F0F0;
                padding: 7px 12px;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', sans-serif;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #4A4A4A;
            }
            QPushButton:pressed {
                background-color: #2F2F2F;
            }
            QPushButton:disabled {
                background-color: #242424;
                border: 1px solid #353535;
                color: #767676;
            }
            QPushButton:disabled:hover {
                background-color: #242424;
                border: 1px solid #353535;
                color: #767676;
            }
            QPushButton:disabled:pressed {
                background-color: #242424;
                border: 1px solid #353535;
                color: #767676;
            }
            QMessageBox {
                background-color: #262626;
            }
            QMessageBox QLabel {
                color: #ECECEC;
            }
            QStatusBar QLabel {
                color: #BCBCBC;
                font-family: 'Avenir Next', 'Segoe UI', 'Helvetica Neue', 'Noto Sans', sans-serif;
            }
            QStatusBar {
                background-color: #212121;
                color: #BCBCBC;
            }
            """

        combo_arrow_rule = (
            "QComboBox::down-arrow {"
            f"image: url(\"{combo_arrow_path}\");"
            "width: 12px;"
            "height: 12px;"
            "margin-right: 8px;"
            "}"
        )

        app.setStyleSheet(style_sheet.replace("__COMBO_DOWN_ARROW_RULE__", combo_arrow_rule))

    def browse_folder(self) -> None:
        dialog = QFileDialog(self, "Select Folder", str(Path.home()))
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        # On Windows the native folder dialog is required to allow selecting
        # drive roots (e.g. "C:\\", "D:\\"). Prefer the native dialog
        # there; use the non-native dialog elsewhere for styling consistency.
        if sys.platform.startswith("win"):
            dialog.setOption(QFileDialog.DontUseNativeDialog, False)
        else:
            dialog.setOption(QFileDialog.DontUseNativeDialog, True)

        if not dialog.exec():
            return

        selected = dialog.selectedFiles()[0]
        self.path_input.setText(selected)
        self.show_info()
        self.statusBar().showMessage("Folder selected")

    def refresh_drives(self) -> None:
        if self._drive_scan_thread is not None and self._drive_scan_thread.isRunning():
            self.statusBar().showMessage("Drive refresh already running")
            return

        # Guard against cases where the underlying C++ widgets have been
        # destroyed (can happen during shutdown or if called too early).
        try:
            self.refresh_drives_btn.setEnabled(False)
            self.unmount_drive_btn.setEnabled(False)
            self.refresh_drives_btn.setText("Refreshing...")
            self.drive_combo.clear()
            self.drive_combo.addItem("Scanning removable drives...")
            self.statusBar().showMessage("Scanning removable drives...")
            self._update_directory_tab_access()
        except RuntimeError:
            return

        self._drive_scan_thread = QThread(self)
        self._drive_scan_worker = DriveScanWorker()
        self._drive_scan_worker.moveToThread(self._drive_scan_thread)

        self._drive_scan_thread.started.connect(self._drive_scan_worker.run)
        self._drive_scan_worker.finished.connect(self._on_drive_scan_finished)
        self._drive_scan_worker.failed.connect(self._on_drive_scan_failed)

        self._drive_scan_worker.finished.connect(self._drive_scan_thread.quit)
        self._drive_scan_worker.failed.connect(self._drive_scan_thread.quit)
        self._drive_scan_thread.finished.connect(self._drive_scan_worker.deleteLater)
        self._drive_scan_thread.finished.connect(self._drive_scan_thread.deleteLater)
        self._drive_scan_thread.finished.connect(self._clear_drive_scan_refs)

        self._drive_scan_thread.start()

    @Slot(object)
    def _on_drive_scan_finished(self, options_obj) -> None:
        options = options_obj if isinstance(options_obj, list) else []
        self.drive_options = [item for item in options if isinstance(item, DriveOption)]

        self.drive_combo.clear()

        if self.drive_options:
            self.drive_combo.addItem("Select removable drive", "")
            for row, option in enumerate(self.drive_options):
                self.drive_combo.addItem(option.label, option.path)
            self.statusBar().showMessage(f"Found {len(self.drive_options)} removable drive(s)")
        else:
            self.drive_combo.addItem("No removable drives found")
            self.statusBar().showMessage("No removable drives detected")

        self._update_directory_tab_access()
        self._update_unmount_drive_button_state()

        self.refresh_drives_btn.setEnabled(True)
        self.refresh_drives_btn.setText("Refresh Drives")

    @Slot(str)
    def _on_drive_scan_failed(self, error: str) -> None:
        self.drive_options = []
        self.drive_combo.clear()
        self.drive_combo.addItem("Drive scan failed")
        self.statusBar().showMessage(f"Drive detection failed: {error}", 5000)
        self._update_directory_tab_access()
        self._update_unmount_drive_button_state()
        self.refresh_drives_btn.setEnabled(True)
        self.refresh_drives_btn.setText("Refresh Drives")

    @Slot()
    def _clear_drive_scan_refs(self) -> None:
        self._drive_scan_worker = None
        self._drive_scan_thread = None

    def _set_current_target_label(self, target: str) -> None:
        if not target:
            self.current_target_label.setText("Current target: none selected")
            return
        self.current_target_label.setText(f"Current target: {target}")

    def _filesystem_looks_compatible(self, filesystem_value: str) -> bool:
        normalized = filesystem_value.lower()
        return "exfat" in normalized or "fat" in normalized

    def _selected_drive_path(self) -> str | None:
        try:
            selected = self.drive_combo.currentData()
        except RuntimeError:
            return None

        if not isinstance(selected, str) or not selected:
            return None
        if any(option.path == selected for option in self.drive_options):
            return selected
        return None

    def _selected_target_directory_path(self) -> str | None:
        target = self.path_input.text().strip()
        if not target:
            return None

        target_path = Path(target).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            return None

        try:
            return str(target_path.resolve())
        except Exception:
            return str(target_path)

    def _update_unmount_drive_button_state(self) -> None:
        has_selected_drive = self._selected_drive_path() is not None
        is_drive_scan_running = (
            self._drive_scan_thread is not None and self._drive_scan_thread.isRunning()
        )
        self.unmount_drive_btn.setEnabled(has_selected_drive and not is_drive_scan_running)

    def unmount_selected_drive(self) -> None:
        selected_drive = self._selected_drive_path()
        if not selected_drive:
            QMessageBox.information(
                self,
                "No Drive Selected",
                "Select a removable drive before unmounting.",
            )
            self._update_unmount_drive_button_state()
            return

        confirm = QMessageBox.question(
            self,
            "Unmount Drive",
            f"Unmount selected drive?\n\n{selected_drive}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.unmount_drive_btn.setEnabled(False)
        self.unmount_drive_btn.setText("Unmounting...")
        unmounted = self._attempt_unmount_drive(selected_drive)
        if not unmounted:
            self._update_unmount_drive_button_state()
        self.unmount_drive_btn.setText("Unmount Drive")

    def _attempt_unmount_drive(
        self,
        selected_drive: str,
        *,
        show_result_dialogs: bool = True,
        refresh_drive_list: bool = True,
    ) -> bool:
        try:
            ok, message = attempt_unmount_mountpoint(selected_drive)
            if not ok:
                raise RuntimeError(message or "Unmount failed")

            # Clear path_input if it referenced the unmounted drive
            try:
                if self.path_input.text().strip() == selected_drive:
                    self.path_input.clear()
                    self.show_info()
            except Exception:
                pass

            self.statusBar().showMessage(f"Drive unmounted: {selected_drive}", 5000)
            if show_result_dialogs:
                QMessageBox.information(self, "Drive Unmounted", f"Successfully unmounted:\n{selected_drive}")
            if refresh_drive_list:
                try:
                    self.refresh_drives()
                except Exception:
                    pass
            return True
        except Exception as exc:
            if show_result_dialogs:
                QMessageBox.warning(self, "Unmount Failed", f"Unable to unmount drive:\n{exc}")
            self.statusBar().showMessage("Unmount failed", 5000)
            return False

    def closeEvent(self, event) -> None:
        selected_drive = self._selected_drive_path()
        if not selected_drive:
            event.accept()
            return

        choice = QMessageBox.question(
            self,
            "Exit Application",
            (
                "Would you like to unmount the selected removable drive before exiting?\n\n"
                f"{selected_drive}"
            ),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )

        if choice == QMessageBox.Cancel:
            event.ignore()
            return

        if choice == QMessageBox.Yes:
            self.unmount_drive_btn.setEnabled(False)
            self.unmount_drive_btn.setText("Unmounting...")

            unmounted = self._attempt_unmount_drive(
                selected_drive,
                show_result_dialogs=False,
                refresh_drive_list=False,
            )

            self.unmount_drive_btn.setText("Unmount Drive")
            self._update_unmount_drive_button_state()

            if not unmounted:
                continue_exit = QMessageBox.question(
                    self,
                    "Unmount Failed",
                    "Unable to unmount the selected drive before exit. Exit anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if continue_exit != QMessageBox.Yes:
                    event.ignore()
                    return

        event.accept()

    def _update_directory_tab_access(self) -> None:
        if self._directory_tab_index < 0:
            return

        has_selected_drive = self._selected_drive_path() is not None
        has_selected_directory = self._selected_target_directory_path() is not None
        has_browser_target = has_selected_drive or has_selected_directory
        self.tabs.setTabEnabled(self._directory_tab_index, has_browser_target)

        if not has_browser_target and self.tabs.currentIndex() == self._directory_tab_index:
            if self._about_tab_index >= 0:
                self.tabs.setCurrentIndex(self._about_tab_index)

    def _set_cleanup_idle(self, message: str) -> None:
        self.cleanup_summary_label.setText(message)
        self.cleanup_table.setRowCount(0)
        self.cleanup_remove_btn.setEnabled(False)
        self._cleanup_scan_target = None
        self._cleanup_type_files = {}

    def _set_file_rename_idle(self, message: str) -> None:
        self.file_rename_summary_label.setText(message)
        self.file_rename_table.setRowCount(0)
        self.file_rename_apply_btn.setEnabled(False)
        self._file_rename_scan_target = None

    def _set_lyrics_manager_idle(self, message: str) -> None:
        self.lyrics_manager_summary_label.setText(message)
        self.lyrics_manager_progress.setRange(0, 1)
        self.lyrics_manager_progress.setValue(0)
        self.lyrics_manager_progress.setFormat("Idle")
        self._configure_lyrics_manager_scan_table()
        self.lyrics_manager_table.setRowCount(0)
        # Require the user to run a Scan first. Only the scan button is enabled
        # until a scan has been performed (which sets `_lyrics_manager_scan_target`).
        self.lyrics_manager_scan_btn.setEnabled(True)
        self.lyrics_manager_bulk_lookup_btn.setEnabled(False)
        self.lyrics_manager_export_lrc_btn.setEnabled(False)
        self.lyrics_manager_apply_lookup_btn.setEnabled(False)
        self._lyrics_manager_scan_target = None
        self._lyrics_manager_scan_results = []
        self._lyrics_lookup_results = []

    def _configure_lyrics_manager_scan_table(self) -> None:
        self.lyrics_manager_table.setColumnCount(3)
        self.lyrics_manager_table.setHorizontalHeaderLabels(
            ["File", "Embedded", "Matching LRC File"]
        )
        self._configure_resizable_table_columns(self.lyrics_manager_table, [420, 120, 220])

    def _configure_lyrics_manager_lookup_table(self) -> None:
        self.lyrics_manager_table.setColumnCount(5)
        self.lyrics_manager_table.setHorizontalHeaderLabels(
            ["File", "Lookup", "Source", "Apply", "Preview"]
        )
        self._configure_resizable_table_columns(
            self.lyrics_manager_table,
            [320, 120, 140, 100, 340],
        )

    def _set_backup_restore_idle(self, message: str) -> None:
        self._set_backup_restore_status(message)
        self._hide_backup_restore_progress_dialog()

    def _set_backup_restore_status(self, message: str, timeout_ms: int = 0) -> None:
        self.statusBar().showMessage(message, timeout_ms)

    def _ensure_backup_restore_progress_dialog(self) -> BackupRestoreProgressDialog:
        if self._backup_restore_progress_dialog is None:
            dialog = BackupRestoreProgressDialog(self)
            dialog.cancelRequested.connect(self.cancel_backup_restore_job)
            self._backup_restore_progress_dialog = dialog

        return self._backup_restore_progress_dialog

    def _hide_backup_restore_progress_dialog(self) -> None:
        if self._backup_restore_progress_dialog is not None:
            self._backup_restore_progress_dialog.finish_and_hide()

    def _update_backup_restore_progress_dialog(
        self, processed: int, total: int, detail_text: str
    ) -> None:
        dialog = self._ensure_backup_restore_progress_dialog()
        dialog.update_progress(processed, total, detail_text)

    def _update_backup_zip_run_button_state(self) -> None:
        has_zip_destination = bool(self.backup_zip_path_input.text().strip())
        self.backup_zip_run_btn.setEnabled(has_zip_destination and not self._backup_restore_busy)

    def _update_transfer_run_button_state(self) -> None:
        has_transfer_destination = bool(self.transfer_destination_input.text().strip())
        self.transfer_run_btn.setEnabled(has_transfer_destination and not self._backup_restore_busy)

    def _set_backup_restore_busy(self, busy: bool, operation_label: str = "") -> None:
        self._backup_restore_busy = busy
        self.backup_zip_browse_btn.setEnabled(not busy)
        self._update_backup_zip_run_button_state()
        self.transfer_destination_browse_btn.setEnabled(not busy)
        self.transfer_mode_combo.setEnabled(not busy)
        self._update_transfer_run_button_state()
        if busy and operation_label:
            self._set_backup_restore_status(operation_label)
            dialog = self._ensure_backup_restore_progress_dialog()
            dialog.begin(f"{operation_label} Preparing...")
        if not busy:
            self._hide_backup_restore_progress_dialog()

    def _resolve_current_target_dir(self) -> Path | None:
        target_text = self.path_input.text().strip()
        if not target_text:
            QMessageBox.information(
                self,
                "No Target",
                "Choose a folder or drive target before starting backup/restore.",
            )
            return None

        target_path = Path(target_text).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            QMessageBox.warning(
                self,
                "Invalid Target",
                "The selected target path is not a valid folder.",
            )
            return None

        return target_path.resolve()

    def _run_backup_restore_worker(
        self, worker: QObject, operation_label: str, operation_kind: str
    ) -> None:
        if self._backup_restore_thread is not None and self._backup_restore_thread.isRunning():
            QMessageBox.information(self, "Job Running", "A backup/restore job is already running.")
            return

        self._backup_restore_active_operation = operation_label
        self._backup_restore_active_kind = operation_kind
        self._set_backup_restore_busy(True, operation_label)

        self._backup_restore_thread = QThread(self)
        self._backup_restore_worker = worker
        self._backup_restore_worker.moveToThread(self._backup_restore_thread)

        self._backup_restore_thread.started.connect(self._backup_restore_worker.run)
        self._backup_restore_worker.progress.connect(self._on_backup_restore_progress)
        self._backup_restore_worker.failed.connect(self._on_backup_restore_failed)
        self._backup_restore_worker.cancelled.connect(self._on_backup_restore_cancelled)

        self._backup_restore_worker.finished.connect(self._backup_restore_thread.quit)
        self._backup_restore_worker.failed.connect(self._backup_restore_thread.quit)
        self._backup_restore_worker.cancelled.connect(self._backup_restore_thread.quit)
        self._backup_restore_thread.finished.connect(self._backup_restore_worker.deleteLater)
        self._backup_restore_thread.finished.connect(self._backup_restore_thread.deleteLater)
        self._backup_restore_thread.finished.connect(self._clear_backup_restore_refs)

        self._backup_restore_thread.start()

    def choose_backup_zip_path(self) -> None:
        target_dir = None
        target_text = self.path_input.text().strip()
        if target_text:
            candidate = Path(target_text).expanduser()
            if candidate.exists() and candidate.is_dir():
                try:
                    target_dir = candidate.resolve()
                except Exception:
                    target_dir = candidate

        default_name = "target_backup.zip"
        if target_dir is not None:
            default_name = f"{target_dir.name}_backup.zip"

        start_path = str(Path.home() / default_name)
        selected, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Choose Zip Backup Destination",
            start_path,
            "Zip archives (*.zip)",
        )
        if selected:
            self.backup_zip_path_input.setText(selected)
            self._update_backup_zip_run_button_state()

    def choose_transfer_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Copy/Move Destination",
            str(Path.home()),
        )
        if selected:
            self.transfer_destination_input.setText(selected)
            self._update_transfer_run_button_state()

    def start_zip_backup(self) -> None:
        source_path = self._resolve_current_target_dir()
        if source_path is None:
            return

        zip_text = self.backup_zip_path_input.text().strip()
        if not zip_text:
            QMessageBox.information(
                self,
                "Zip Path Required",
                "Choose where to save the backup zip before starting.",
            )
            return

        zip_path = Path(zip_text).expanduser()
        if zip_path.suffix.lower() != ".zip":
            zip_path = zip_path.with_suffix(".zip")
            self.backup_zip_path_input.setText(str(zip_path))

        try:
            source_resolved = source_path.resolve()
            zip_parent_resolved = zip_path.parent.resolve()
        except Exception:
            source_resolved = source_path
            zip_parent_resolved = zip_path.parent

        if zip_parent_resolved == source_resolved or source_resolved in zip_parent_resolved.parents:
            QMessageBox.warning(
                self,
                "Invalid Zip Destination",
                "Choose a zip destination outside the source folder to avoid recursive backup data.",
            )
            return

        if zip_path.exists():
            overwrite = QMessageBox.question(
                self,
                "Overwrite Existing Zip",
                f"{zip_path.name} already exists. Overwrite it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if overwrite != QMessageBox.Yes:
                return

        confirm = QMessageBox.question(
            self,
            "Confirm Zip Backup",
            f"Create a zip backup of:\n{source_path}\n\nDestination:\n{zip_path}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return

        worker = ZipBackupWorker(source_path, zip_path)
        worker.finished.connect(self._on_zip_backup_finished)
        self._run_backup_restore_worker(worker, "Creating zip backup...", "zip")
        self.statusBar().showMessage("Zip backup started")

    def start_copy_or_move(self) -> None:
        source_path = self._resolve_current_target_dir()
        if source_path is None:
            return

        destination_root_text = self.transfer_destination_input.text().strip()
        if not destination_root_text:
            QMessageBox.information(
                self,
                "Destination Required",
                "Choose a destination folder or drive before starting copy/move.",
            )
            return

        destination_root = Path(destination_root_text).expanduser()
        destination_target = destination_root / source_path.name
        mode = str(self.transfer_mode_combo.currentData() or "copy").lower()
        mode = "move" if mode == "move" else "copy"
        mode_label = "Move" if mode == "move" else "Copy"

        try:
            source_resolved = source_path.resolve()
            destination_target_resolved = destination_target.resolve(strict=False)
        except Exception:
            source_resolved = source_path
            destination_target_resolved = destination_target

        if destination_target_resolved == source_resolved:
            QMessageBox.warning(
                self,
                "Invalid Destination",
                "Destination resolves to the same folder as the source.",
            )
            return

        if source_resolved in destination_target_resolved.parents:
            QMessageBox.warning(
                self,
                "Invalid Destination",
                "Destination cannot be inside the source folder.",
            )
            return

        if destination_target.exists() and not destination_target.is_dir():
            QMessageBox.warning(
                self,
                "Invalid Destination",
                "Destination target exists but is not a folder.",
            )
            return

        if destination_target.exists():
            merge = QMessageBox.question(
                self,
                "Destination Already Exists",
                f"{destination_target} already exists. Continue and merge into this folder?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if merge != QMessageBox.Yes:
                return

        confirm = QMessageBox.question(
            self,
            f"Confirm {mode_label}",
            f"{mode_label} source folder:\n{source_path}\n\nTo destination:\n{destination_target}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        worker = FileTransferWorker(source_path, destination_target, mode)
        worker.finished.connect(self._on_transfer_finished)
        self._run_backup_restore_worker(worker, f"{mode_label} in progress...", "transfer")
        self.statusBar().showMessage(f"{mode_label} started")

    def cancel_backup_restore_job(self) -> None:
        if self._backup_restore_worker is None:
            self._hide_backup_restore_progress_dialog()
            return

        request_cancel = getattr(self._backup_restore_worker, "request_cancel", None)
        if callable(request_cancel):
            request_cancel()
            cancelling_label = self._backup_restore_active_operation or "Backup/restore job"
            cancelling_text = f"Cancelling {cancelling_label.lower()}..."
            self._set_backup_restore_status(cancelling_text)
            dialog = self._ensure_backup_restore_progress_dialog()
            dialog.mark_cancelling(cancelling_text)

    @Slot(int, int, str)
    def _on_backup_restore_progress(self, processed: int, total: int, current_item: str) -> None:
        progress_text = (
            f"{self._backup_restore_active_operation} {processed}/{total}: {current_item}"
        )
        self._update_backup_restore_progress_dialog(processed, total, progress_text)
        self._set_backup_restore_status(progress_text)

    @Slot(object)
    def _on_zip_backup_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        zip_path = str(payload.get("zip_path") or "")
        processed = int(payload.get("processed") or 0)
        total = int(payload.get("total") or 0)
        skipped = int(payload.get("skipped") or 0)

        self._set_backup_restore_busy(False)
        self._set_backup_restore_status(
            f"Zip backup completed. Files archived: {processed}/{total}. Symlink files skipped: {skipped}.",
            5000,
        )
        QMessageBox.information(
            self,
            "Zip Backup Completed",
            f"Zip created at:\n{zip_path}\n\nFiles archived: {processed}/{total}\nSymlink files skipped: {skipped}",
        )

    @Slot(object)
    def _on_transfer_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        destination = str(payload.get("destination") or "")
        processed = int(payload.get("processed") or 0)
        total = int(payload.get("total") or 0)
        skipped = int(payload.get("skipped") or 0)
        mode = str(payload.get("mode") or "copy").lower()
        mode_label = "Move" if mode == "move" else "Copy"

        self._set_backup_restore_busy(False)
        self._set_backup_restore_status(
            f"{mode_label} completed. Files processed: {processed}/{total}. Symlink files skipped: {skipped}.",
            5000,
        )
        QMessageBox.information(
            self,
            f"{mode_label} Completed",
            f"Destination:\n{destination}\n\nFiles processed: {processed}/{total}\nSymlink files skipped: {skipped}",
        )

        if mode == "move":
            self.path_input.setText(destination)
            self.show_info()

    @Slot(str)
    def _on_backup_restore_failed(self, error: str) -> None:
        self._set_backup_restore_busy(False)
        self._set_backup_restore_status(f"Backup/restore failed: {error}", 5000)
        QMessageBox.warning(self, "Backup/Restore Failed", error)

    @Slot(object)
    def _on_backup_restore_cancelled(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        processed = int(payload.get("processed") or 0)
        total = int(payload.get("total") or 0)
        self._set_backup_restore_busy(False)
        self._set_backup_restore_status(
            f"Backup/restore cancelled after {processed}/{total} files processed.",
            5000,
        )

    @Slot()
    def _clear_backup_restore_refs(self) -> None:
        self._backup_restore_worker = None
        self._backup_restore_thread = None
        self._backup_restore_active_operation = ""
        self._backup_restore_active_kind = ""

    def _metadata_value_to_text(self, value) -> str:
        if value is None:
            return ""

        if hasattr(value, "text"):
            try:
                text_value = getattr(value, "text")
                return self._metadata_value_to_text(text_value)
            except Exception:
                pass

        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", "ignore").strip()
            except Exception:
                return ""

        if isinstance(value, (list, tuple)):
            if not value:
                return ""
            first_value = value[0]
            if isinstance(first_value, tuple) and first_value:
                first_value = first_value[0]
            return self._metadata_value_to_text(first_value)

        text = str(value).strip()
        return text

    def _extract_track_number(self, raw_value) -> str | None:
        text = self._metadata_value_to_text(raw_value)
        if not text:
            return None

        primary = text.split("/", 1)[0].strip()
        match = re.search(r"\d+", primary or text)
        if not match:
            return None
        return match.group(0)

    def _format_track_number(self, track_number: str) -> str:
        parsed = self._extract_track_number(track_number)
        if not parsed:
            return track_number
        try:
            return f"{int(parsed):02d}"
        except Exception:
            return parsed

    def _extract_track_title(self, raw_value) -> str | None:
        text = self._metadata_value_to_text(raw_value)
        if not text:
            return None
        collapsed = " ".join(text.split()).strip()
        return collapsed or None

    def _safe_filename_component(self, name: str) -> str:
        cleaned = name.strip()
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", cleaned)
        if os.path.sep:
            cleaned = cleaned.replace(os.path.sep, "_")
        if os.path.altsep:
            cleaned = cleaned.replace(os.path.altsep, "_")
        cleaned = " ".join(cleaned.split()).strip()
        return cleaned

    def _track_metadata_for_file(self, file_path: Path) -> tuple[str | None, str | None, bool]:
        if mutagen is None:
            return None, None, False

        result: dict[str, object] = {
            "track_number": None,
            "track_title": None,
        }

        def _read_metadata() -> None:
            track_raw = None
            title_raw = None

            try:
                audio_easy = mutagen.File(file_path, easy=True)
            except Exception:
                audio_easy = None

            easy_tags = getattr(audio_easy, "tags", None) if audio_easy else None
            if easy_tags:
                track_values = easy_tags.get("tracknumber")
                title_values = easy_tags.get("title")
                if track_values:
                    track_raw = track_values[0]
                if title_values:
                    title_raw = title_values[0]

            # Only fall back to full tag parsing when easy tags are missing fields.
            if track_raw is None or title_raw is None:
                try:
                    audio_full = mutagen.File(file_path)
                except Exception:
                    audio_full = None

                full_tags = getattr(audio_full, "tags", None) if audio_full else None
                def _safe_full_tag(*keys):
                    if not full_tags:
                        return None

                    for key in keys:
                        try:
                            value = full_tags.get(key)
                        except Exception:
                            continue

                        if value:
                            return value

                    return None

                if full_tags:
                    if track_raw is None:
                        track_raw = _safe_full_tag("TRCK", "tracknumber", "TRACKNUMBER", "trkn")
                    if title_raw is None:
                        title_raw = _safe_full_tag("TIT2", "title", "TITLE", "\xa9nam")

            result["track_number"] = self._extract_track_number(track_raw)
            result["track_title"] = self._extract_track_title(title_raw)

        worker = threading.Thread(target=_read_metadata, daemon=True)
        worker.start()
        worker.join(timeout=3.0)
        if worker.is_alive():
            return None, None, True

        return (
            result.get("track_number") if isinstance(result.get("track_number"), str) else None,
            result.get("track_title") if isinstance(result.get("track_title"), str) else None,
            False,
        )

    def scan_file_rename_suggestions(self) -> None:
        if mutagen is None:
            self._set_file_rename_idle("Mutagen unavailable; metadata rename scan cannot run.")
            QMessageBox.warning(
                self,
                "Metadata Unavailable",
                "Mutagen is required to read track metadata for rename suggestions.",
            )
            return

        target = self.path_input.text().strip()
        if not target:
            self._set_file_rename_idle("Choose a target before scanning rename suggestions.")
            QMessageBox.information(
                self,
                "No Target",
                "Choose a folder or drive before scanning rename suggestions.",
            )
            return

        target_path = Path(target).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            self._set_file_rename_idle("Target path is invalid.")
            QMessageBox.warning(
                self,
                "Invalid Target",
                "The selected target path is not a valid folder.",
            )
            return

        resolved_target = target_path.resolve()
        _preset = str(self.file_rename_preset_combo.currentData() or "trackno_trackname")

        suggestions: list[tuple[Path, Path, str, str, str]] = []
        scanned_audio = 0
        missing_metadata = 0
        metadata_timeouts = 0
        already_matching = 0
        canceled = False
        audio_files: list[Path] = []

        progress = QProgressDialog("Discovering audio files...", "Cancel", 0, 0, self)
        progress.setWindowTitle("File Rename")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)

        for root_dir, _dir_names, file_names in os.walk(str(resolved_target)):
            for file_name in file_names:
                if file_name.startswith("."):
                    continue

                file_path = Path(root_dir) / file_name
                if file_path.suffix.lower() not in AUDIO_FILE_EXTENSIONS:
                    continue

                audio_files.append(file_path)

                if len(audio_files) % 50 == 0:
                    progress.setLabelText(
                        f"Discovering audio files... {len(audio_files)} found"
                    )
                    QApplication.processEvents()
                    if progress.wasCanceled():
                        canceled = True
                        break

            if canceled:
                break

        if canceled:
            progress.close()
            self.file_rename_summary_label.setText("Rename suggestion scan cancelled.")
            return

        total_audio_files = len(audio_files)
        progress.setRange(0, max(total_audio_files, 1))
        progress.setValue(0)

        if total_audio_files == 0:
            progress.close()
            self.file_rename_table.setRowCount(0)
            self.file_rename_apply_btn.setEnabled(False)
            self._file_rename_scan_target = str(resolved_target)
            self.file_rename_summary_label.setText("No audio files found in target.")
            self.statusBar().showMessage("No audio files found for rename scan", 4000)
            return

        for index, file_path in enumerate(audio_files, start=1):
            scanned_audio += 1
            progress.setValue(index)

            progress.setLabelText(
                f"Scanning rename suggestions... {index}/{total_audio_files}: {file_path.name}"
            )
            QApplication.processEvents()
            if progress.wasCanceled():
                canceled = True
                break

            track_no, track_title, timed_out = self._track_metadata_for_file(file_path)
            if timed_out:
                metadata_timeouts += 1
                missing_metadata += 1
                continue
            if not track_no or not track_title:
                missing_metadata += 1
                continue

            safe_title = self._safe_filename_component(track_title)
            if not safe_title:
                missing_metadata += 1
                continue

            formatted_track_no = self._format_track_number(track_no)
            suggested_name = f"{formatted_track_no}. {safe_title}{file_path.suffix}"
            if suggested_name == file_path.name:
                already_matching += 1
                continue
            elif suggested_name.lower() == file_path.name.lower():
                # Only capitalization differs
                reason = "Metadata Title uses different name"
            else:
                # Actual content difference
                reason = "Name differs from preset"

            suggested_path = file_path.with_name(suggested_name)
            suggestions.append(
                (file_path, suggested_path, formatted_track_no, safe_title, reason)
            )

        progress.close()

        if canceled:
            self.file_rename_summary_label.setText("Rename suggestion scan cancelled.")
            return

        target_counts: dict[str, int] = {}
        for _source_path, target_path_item, _track_no, _title, _reason in suggestions:
            key = str(target_path_item).lower()
            target_counts[key] = target_counts.get(key, 0) + 1

        self.file_rename_table.setUpdatesEnabled(False)
        try:
            self.file_rename_table.setRowCount(len(suggestions))
            for row_index, (source_path, target_path_item, track_no, track_title, base_reason) in enumerate(suggestions):
                conflict_reason = ""
                key = str(target_path_item).lower()
                if target_counts.get(key, 0) > 1:
                    conflict_reason = "Duplicate suggested target name"
                elif target_path_item.exists() and target_path_item != source_path:
                    # On case-insensitive filesystems, check if they refer to the same file
                    try:
                        is_same_file = target_path_item.samefile(source_path)
                    except Exception:
                        is_same_file = False
                    
                    if not is_same_file:
                        conflict_reason = "Target name already exists"

                reason_text = conflict_reason or base_reason

                try:
                    current_rel = source_path.relative_to(resolved_target).as_posix()
                except Exception:
                    current_rel = str(source_path)

                try:
                    suggested_rel = target_path_item.relative_to(resolved_target).as_posix()
                except Exception:
                    suggested_rel = str(target_path_item)

                check_item = QTableWidgetItem()
                check_item.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                )
                if reason_text == "Target name already exists":
                    check_item.setCheckState(Qt.Unchecked)
                else:
                    check_item.setCheckState(Qt.Checked)
                check_item.setData(Qt.UserRole, (str(source_path), str(target_path_item)))

                self.file_rename_table.setItem(row_index, 0, check_item)
                self.file_rename_table.setItem(row_index, 1, QTableWidgetItem(current_rel))
                self.file_rename_table.setItem(row_index, 2, QTableWidgetItem(suggested_rel))
                self.file_rename_table.setItem(row_index, 3, QTableWidgetItem(track_no))
                self.file_rename_table.setItem(row_index, 4, QTableWidgetItem(track_title))
                self.file_rename_table.setItem(row_index, 5, QTableWidgetItem(reason_text))

                if conflict_reason:
                    reason_item = self.file_rename_table.item(row_index, 5)
                    if reason_item is not None:
                        reason_item.setBackground(QColor("#7A5E2C"))
                        reason_item.setForeground(QColor("#FFF9E6"))
        finally:
            self.file_rename_table.setUpdatesEnabled(True)
            self.file_rename_table.viewport().update()

        self._file_rename_scan_target = str(resolved_target)
        self.file_rename_apply_btn.setEnabled(len(suggestions) > 0)
        self.file_rename_summary_label.setText(
            f"Audio scanned: {scanned_audio} | Suggestions: {len(suggestions)} | "
            f"Already matching: {already_matching} | Missing metadata: {missing_metadata} | "
            f"Timed out: {metadata_timeouts}"
        )
        self.statusBar().showMessage("File rename suggestion scan completed", 4000)

    def rename_selected_files(self) -> None:
        if self._file_rename_scan_target is None or self.file_rename_table.rowCount() == 0:
            QMessageBox.information(
                self,
                "No Suggestions",
                "Scan rename suggestions before renaming files.",
            )
            return

        target = self.path_input.text().strip()
        try:
            current_target = str(Path(target).expanduser().resolve())
        except Exception:
            current_target = ""

        if current_target != self._file_rename_scan_target:
            QMessageBox.information(
                self,
                "Target Changed",
                "The target path changed since the last rename scan. Run Scan Rename Suggestions again.",
            )
            return

        rename_pairs: list[tuple[Path, Path]] = []
        for row in range(self.file_rename_table.rowCount()):
            check_item = self.file_rename_table.item(row, 0)
            if check_item is None or check_item.checkState() != Qt.Checked:
                continue

            pair_data = check_item.data(Qt.UserRole)
            if not isinstance(pair_data, tuple) or len(pair_data) != 2:
                continue

            src, dst = pair_data
            rename_pairs.append((Path(src), Path(dst)))

        if not rename_pairs:
            QMessageBox.information(
                self,
                "Nothing Selected",
                "Select one or more suggested renames to apply.",
            )
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Rename",
            f"Rename {len(rename_pairs)} selected file(s)?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        progress = QProgressDialog("Renaming selected files...", "Cancel", 0, len(rename_pairs), self)
        progress.setWindowTitle("File Rename")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setValue(0)

        renamed = 0
        lrc_renamed = 0
        failed: list[tuple[str, str]] = []

        for index, (source_path, target_path_item) in enumerate(rename_pairs, start=1):
            if progress.wasCanceled():
                break

            progress.setLabelText(f"Renaming {index}/{len(rename_pairs)}: {source_path.name}")

            if not source_path.exists():
                failed.append((str(source_path), "Source file no longer exists"))
            elif target_path_item.exists() and target_path_item != source_path:
                # On case-insensitive filesystems, check if they refer to the same file
                try:
                    is_same_file = target_path_item.samefile(source_path)
                except Exception:
                    is_same_file = False
                
                if not is_same_file:
                    failed.append((str(source_path), "Target name already exists"))
                    progress.setValue(index)
                    QApplication.processEvents()
                    continue
                # If is_same_file, fall through to rename logic
            
            # Rename logic (executes when source exists and target is either missing or the same file)
            if not source_path.exists():
                continue
            
            source_lrc_path = source_path.with_suffix(".lrc")
            target_lrc_path = target_path_item.with_suffix(".lrc")

            if source_lrc_path.exists() and target_lrc_path.exists():
                # On case-insensitive filesystems, check if they refer to the same file
                try:
                    is_same_lrc_file = target_lrc_path.samefile(source_lrc_path)
                except Exception:
                    is_same_lrc_file = False
                
                if not is_same_lrc_file:
                    failed.append((str(source_path), "Matching .lrc target already exists"))
                    progress.setValue(index)
                    QApplication.processEvents()
                    continue

            try:
                source_path.rename(target_path_item)

                if source_lrc_path.exists() and target_lrc_path != source_lrc_path:
                    try:
                        source_lrc_path.rename(target_lrc_path)
                        lrc_renamed += 1
                    except Exception as lrc_exc:
                        rollback_reason = ""
                        try:
                            target_path_item.rename(source_path)
                        except Exception as rollback_exc:
                            rollback_reason = f"; rollback failed: {rollback_exc}"

                        failed.append(
                            (
                                str(source_path),
                                f"Renamed audio file but failed to rename matching .lrc: {lrc_exc}{rollback_reason}",
                            )
                        )
                        progress.setValue(index)
                        QApplication.processEvents()
                        continue

                self._purge_cached_album_art_for_path(source_path)
                self._purge_cached_album_art_for_path(target_path_item)
                renamed += 1
            except Exception as exc:
                failed.append((str(source_path), str(exc)))

            progress.setValue(index)
            QApplication.processEvents()

        canceled = progress.wasCanceled()
        progress.close()

        failed_count = len(failed)
        message_lines = [
            f"Renamed files: {renamed}",
            f"Renamed matching .lrc files: {lrc_renamed}",
            f"Failed renames: {failed_count}",
        ]
        if canceled:
            message_lines.append("Operation cancelled before all selected files were processed.")
        if failed:
            preview = "\n".join(
                f"- {Path(path).name}: {reason}" for path, reason in failed[:5]
            )
            message_lines.append("\nSample failures:\n" + preview)

        if failed_count:
            QMessageBox.warning(self, "Rename Completed With Errors", "\n".join(message_lines))
        else:
            QMessageBox.information(self, "Rename Completed", "\n".join(message_lines))

        self.scan_file_rename_suggestions()

    def _on_file_rename_table_context_menu(self, pos) -> None:
        """Handle right-click context menu on the rename table."""
        item = self.file_rename_table.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)
        copy_action = menu.addAction("Copy")
        action = menu.exec(self.file_rename_table.mapToGlobal(pos))

        if action == copy_action:
            text = item.text()
            if text:
                clipboard = QApplication.clipboard()
                clipboard.setText(text)

    def _cleanup_category_for_file(self, file_name: str, extension: str) -> str:
        if file_name.startswith("._"):
            return "Hidden"
        if extension in AUDIO_FILE_EXTENSIONS:
            return "Audio"
        if extension in IMAGE_FILE_EXTENSIONS:
            return "Image"
        if extension in VIDEO_FILE_EXTENSIONS:
            return "Video"
        if extension in DOCUMENT_FILE_EXTENSIONS:
            return "Document"
        if extension in ARCHIVE_FILE_EXTENSIONS:
            return "Archive"
        if extension in PLAYLIST_FILE_EXTENSIONS:
            return "Playlist"
        if extension in SUBTITLE_FILE_EXTENSIONS:
            return "Subtitle"
        if extension in EXECUTABLE_FILE_EXTENSIONS:
            return "Executable"
        if file_name.startswith("."):
            return "Hidden"
        return "Other"

    def _cleanup_file_type_label(self, file_name: str, extension: str, category: str) -> str:
        if category == "Hidden":
            lowered = file_name.lower()
            if lowered.startswith("._") and extension:
                return f"._*{extension} (macOS sidecar)"
            if extension:
                return f".*{extension}"
            return file_name
        if extension:
            return extension
        return "(no extension)"

    def scan_file_cleanup_breakdown(self) -> None:
        target = self.path_input.text().strip()
        if not target:
            self._set_cleanup_idle("Choose a target before scanning file types.")
            QMessageBox.information(self, "No Target", "Choose a folder or drive before scanning file types.")
            return

        target_path = Path(target).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            self._set_cleanup_idle("Target path is invalid.")
            QMessageBox.warning(self, "Invalid Target", "The selected target path is not a valid folder.")
            return

        resolved_target = str(target_path.resolve())
        progress = QProgressDialog("Scanning file types...", "Cancel", 0, 0, self)
        progress.setWindowTitle("File Cleanup Scan")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)

        stats_by_type: dict[str, dict[str, object]] = {}
        files_by_type: dict[str, list[Path]] = {}
        category_order = {category: index for index, category in enumerate(FILE_CLEANUP_CATEGORY_ORDER)}

        scanned_files = 0
        canceled = False
        for root_dir, _dir_names, file_names in os.walk(resolved_target):
            for file_name in file_names:
                file_path = Path(root_dir) / file_name
                extension = file_path.suffix.lower()
                category = self._cleanup_category_for_file(file_name.lower(), extension)
                file_type_label = self._cleanup_file_type_label(file_name, extension, category)
                row_key = f"{category}|{file_type_label.lower()}"

                try:
                    file_size = file_path.stat().st_size
                except Exception:
                    file_size = 0

                type_stats = stats_by_type.get(row_key)
                if type_stats is None:
                    type_stats = {
                        "file_type": file_type_label,
                        "type": category,
                        "count": 0,
                        "bytes": 0,
                    }
                    stats_by_type[row_key] = type_stats
                    files_by_type[row_key] = []

                type_stats["count"] = int(type_stats["count"]) + 1
                type_stats["bytes"] = int(type_stats["bytes"]) + int(file_size)
                files_by_type[row_key].append(file_path)

                scanned_files += 1
                if scanned_files % 400 == 0:
                    progress.setLabelText(f"Scanning file types... {scanned_files} files")
                    QApplication.processEvents()
                    if progress.wasCanceled():
                        canceled = True
                        break

            if canceled:
                break

        progress.close()
        if canceled:
            self.cleanup_summary_label.setText("File type scan cancelled.")
            return

        sorted_keys = sorted(
            stats_by_type.keys(),
            key=lambda key: (
                category_order.get(str(stats_by_type[key]["type"]), 999),
                str(stats_by_type[key]["file_type"]).lower(),
            ),
        )

        self.cleanup_table.setUpdatesEnabled(False)
        try:
            self.cleanup_table.setRowCount(len(sorted_keys))
            for row, row_key in enumerate(sorted_keys):
                type_stats = stats_by_type[row_key]
                count = int(type_stats["count"])
                size_bytes = int(type_stats["bytes"])
                file_type_text = str(type_stats["file_type"])
                type_text = str(type_stats["type"])

                check_item = QTableWidgetItem()
                check_item.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                )
                check_item.setCheckState(Qt.Unchecked)
                check_item.setData(Qt.UserRole, row_key)

                file_type_item = QTableWidgetItem(file_type_text)
                if type_text == "Hidden":
                    hidden_tip = (
                        "Hidden files are not shown in Finder by default. "
                        "Press Cmd+Shift+. to toggle hidden files."
                    )
                    if "macos sidecar" in file_type_text.lower():
                        hidden_tip = (
                            "These are macOS sidecar metadata files (._*). "
                            "Finder usually hides them. Press Cmd+Shift+. to show hidden files."
                        )
                    file_type_item.setToolTip(hidden_tip)

                self.cleanup_table.setItem(row, 0, check_item)
                self.cleanup_table.setItem(row, 1, file_type_item)
                self.cleanup_table.setItem(row, 2, QTableWidgetItem(type_text))
                self.cleanup_table.setItem(row, 3, QTableWidgetItem(str(count)))
                self.cleanup_table.setItem(row, 4, QTableWidgetItem(format_bytes(size_bytes)))
        finally:
            self.cleanup_table.setUpdatesEnabled(True)
            self.cleanup_table.viewport().update()

        self._cleanup_scan_target = resolved_target
        self._cleanup_type_files = files_by_type

        total_bytes = sum(int(stats_by_type[key]["bytes"]) for key in sorted_keys)
        total_files = sum(int(stats_by_type[key]["count"]) for key in sorted_keys)
        found_types = len(sorted_keys)
        self.cleanup_summary_label.setText(
            f"Scanned {total_files} files across {found_types} file types | Total size: {format_bytes(total_bytes)}"
        )
        self.cleanup_remove_btn.setEnabled(total_files > 0)
        self.statusBar().showMessage("File cleanup scan completed", 4000)

    def remove_selected_file_types(self) -> None:
        if not self._cleanup_scan_target or not self._cleanup_type_files:
            QMessageBox.information(self, "No Scan Data", "Scan file types before removing categories.")
            return

        target = self.path_input.text().strip()
        try:
            current_target = str(Path(target).expanduser().resolve())
        except Exception:
            current_target = ""

        if current_target != self._cleanup_scan_target:
            QMessageBox.information(
                self,
                "Target Changed",
                "The target path changed since the last cleanup scan. Run Scan File Types again.",
            )
            return

        selected_type_keys: list[str] = []
        selected_labels: list[str] = []
        for row in range(self.cleanup_table.rowCount()):
            check_item = self.cleanup_table.item(row, 0)
            file_type_item = self.cleanup_table.item(row, 1)
            category_item = self.cleanup_table.item(row, 2)
            count_item = self.cleanup_table.item(row, 3)
            if not check_item or not file_type_item or not category_item or not count_item:
                continue
            if check_item.flags() & Qt.ItemIsUserCheckable and check_item.checkState() == Qt.Checked:
                if int(count_item.text() or "0") > 0:
                    row_key = check_item.data(Qt.UserRole)
                    if isinstance(row_key, str) and row_key:
                        selected_type_keys.append(row_key)
                        selected_labels.append(f"{category_item.text()} ({file_type_item.text()})")

        if not selected_type_keys:
            QMessageBox.information(self, "Nothing Selected", "Select one or more file types to remove.")
            return

        files_to_remove: list[Path] = []
        for row_key in selected_type_keys:
            files_to_remove.extend(self._cleanup_type_files.get(row_key, []))

        if not files_to_remove:
            QMessageBox.information(self, "Nothing To Remove", "No files found for selected file types.")
            return

        confirm = QMessageBox.question(
            self,
            "Confirm File Removal",
            f"Remove {len(files_to_remove)} files from selected types: {', '.join(selected_labels)}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        progress = QProgressDialog("Removing selected file types...", "Cancel", 0, len(files_to_remove), self)
        progress.setWindowTitle("File Cleanup")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setValue(0)

        removed = 0
        failed: list[tuple[str, str]] = []
        for index, file_path in enumerate(files_to_remove, start=1):
            if progress.wasCanceled():
                break

            progress.setLabelText(f"Removing {index}/{len(files_to_remove)}: {file_path.name}")
            try:
                file_path.unlink()
                removed += 1
            except Exception as exc:
                failed.append((str(file_path), str(exc)))

            progress.setValue(index)
            QApplication.processEvents()

        canceled = progress.wasCanceled()
        progress.close()

        failed_count = len(failed)
        message_lines = [
            f"Removed files: {removed}",
            f"Failed removals: {failed_count}",
        ]
        if canceled:
            message_lines.append("Operation cancelled before all files were processed.")
        if failed:
            preview = "\n".join(
                f"- {Path(path).name}: {reason}" for path, reason in failed[:5]
            )
            message_lines.append("\nSample failures:\n" + preview)

        if failed_count:
            QMessageBox.warning(self, "Cleanup Completed With Errors", "\n".join(message_lines))
        else:
            QMessageBox.information(self, "Cleanup Completed", "\n".join(message_lines))

        self.scan_file_cleanup_breakdown()

    def refresh_directory_browser(self, target: str) -> None:
        # refresh_directory_browser called
        self._pending_dir_size_scan_target = None
        self._cancel_directory_size_scan()
        self.browser_proxy_model.set_directory_sizes_enabled(False)
        self._dir_size_scan_target = None
        self.browser_proxy_model.clear_checked_paths()

        if not target or not os.path.exists(target):
            self.browser_root_label.setText("")
            self.browser_tree.setEnabled(False)
            self.browser_proxy_model.clear_size_cache()
            self.browser_proxy_model.invalidate()
            self.browser_size_progress.hide()
            self.browser_tree.setRootIndex(QModelIndex())
            self._populate_file_properties([])
            self._set_album_art_tab(None, "")
            self._set_embedded_lyrics_text("Select an audio file to view embedded lyrics.", "")
            self._show_audio_details_panel()
            self._reset_album_art_results()
            return

        resolved = str(Path(target).expanduser().resolve())
        self.browser_proxy_model.clear_checked_paths()
        self.browser_proxy_model.clear_size_cache()
        self.browser_proxy_model.invalidate()
        source_index = self.browser_model.setRootPath(resolved)
        proxy_index = self.browser_proxy_model.mapFromSource(source_index)
        self.browser_tree.setRootIndex(proxy_index)
        self.browser_tree.setEnabled(True)
        self.browser_size_progress.hide()
        self._on_browser_selection_changed(0)
        self._populate_file_properties([])
        self._set_album_art_tab(None, "")
        self._set_embedded_lyrics_text("Select an audio file to view embedded lyrics.", "")
        self._show_audio_details_panel()
        self._reset_album_art_results()

        if self._directory_scan_armed and self.tabs.currentIndex() == self._directory_tab_index:
            self._start_directory_size_scan(resolved)

    @Slot(int)
    def on_tab_changed(self, index: int) -> None:
        self._update_help_for_tab(index)

        if index != self._directory_tab_index:
            return

        selected_target = self._selected_target_directory_path() or self._selected_drive_path()
        if selected_target is None:
            QMessageBox.information(
                self,
                "Select Target First",
                "Please select a removable drive or choose a valid folder before using Directory Browser.",
            )
            self.browser_root_label.setText("")
            self.browser_tree.setEnabled(False)
            self.browser_size_progress.hide()
            if self._about_tab_index >= 0:
                self.tabs.setCurrentIndex(self._about_tab_index)
            return

        self._directory_scan_armed = True

        self.path_input.setText(selected_target)
        self._set_current_target_label(selected_target)
        self.refresh_directory_browser(selected_target)

    def _start_directory_size_scan(self, resolved_root: str) -> None:
        if (
            self._dir_size_scan_target == resolved_root
            and self.browser_proxy_model.directory_sizes_enabled
            and self.browser_proxy_model.has_directory_size_cache()
        ):
            return

        if self._dir_size_scan_thread is not None and self._dir_size_scan_thread.isRunning():
            if self._dir_size_scan_target == resolved_root:
                return
            self._pending_dir_size_scan_target = resolved_root
            self._cancel_directory_size_scan()
            self.browser_size_progress.setRange(0, 1)
            self.browser_size_progress.setValue(0)
            self.browser_size_progress.setFormat("Switching folder size scan target...")
            return

        self.browser_proxy_model.set_directory_sizes_enabled(True)
        self.browser_proxy_model.clear_size_cache()
        self.browser_proxy_model.invalidate()

        self.browser_size_progress.show()
        self.browser_size_progress.setRange(0, 1)
        self.browser_size_progress.setValue(0)
        self.browser_size_progress.setFormat("Scanning folder sizes...")

        self._dir_size_scan_target = resolved_root
        self._dir_size_scan_thread = QThread(self)
        self._dir_size_scan_worker = DirectorySizeScanWorker(resolved_root)
        self._dir_size_scan_worker.moveToThread(self._dir_size_scan_thread)

        self._dir_size_scan_thread.started.connect(self._dir_size_scan_worker.run)
        self._dir_size_scan_worker.progress.connect(self._on_dir_size_scan_progress)
        self._dir_size_scan_worker.finished.connect(self._on_dir_size_scan_finished)
        self._dir_size_scan_worker.failed.connect(self._on_dir_size_scan_failed)
        self._dir_size_scan_worker.cancelled.connect(self._on_dir_size_scan_cancelled)

        self._dir_size_scan_worker.finished.connect(self._dir_size_scan_thread.quit)
        self._dir_size_scan_worker.failed.connect(self._dir_size_scan_thread.quit)
        self._dir_size_scan_worker.cancelled.connect(self._dir_size_scan_thread.quit)
        self._dir_size_scan_thread.finished.connect(self._dir_size_scan_worker.deleteLater)
        self._dir_size_scan_thread.finished.connect(self._dir_size_scan_thread.deleteLater)
        self._dir_size_scan_thread.finished.connect(self._clear_dir_size_scan_refs)

        self._dir_size_scan_thread.start()

    def _cancel_directory_size_scan(self) -> None:
        if self._dir_size_scan_worker is not None:
            self._dir_size_scan_worker.request_cancel()

    def _show_bulk_metadata_menu(self) -> None:
        menu = QMenu(self)
        edit_action = menu.addAction("Edit Metadata")
        edit_action.triggered.connect(self._show_bulk_metadata_editor)
        menu.popup(self.browser_bulk_edit_btn.mapToGlobal(self.browser_bulk_edit_btn.rect().bottomLeft()))

    def _show_bulk_metadata_editor(self) -> None:
        selected_paths = sorted(
            {
                path
                for path in self.browser_proxy_model.checked_paths()
                if os.path.isfile(path)
            }
        )
        if not selected_paths:
            QMessageBox.warning(self, "No Selection", "No files selected.")
            return
        
        dialog = BulkMetadataEditDialog(selected_paths, self)
        dialog.exec()

    def _bulk_update_metadata(self, selected_paths: list[str], tag_name: str, tag_value: str, remove_only: bool) -> None:
        """Bulk set or remove one metadata field across selected files."""
        if not mutagen:
            QMessageBox.critical(self, "Error", "mutagen library not available for metadata editing.")
            return

        tag_name = tag_name.strip()
        tag_value = tag_value.strip()
        if not tag_name:
            QMessageBox.warning(self, "No Tag Selected", "Enter a tag name.")
            return
        if not remove_only and not tag_value:
            QMessageBox.warning(self, "No Value", "Enter a value or choose Remove matching tags.")
            return

        updated = 0
        failed: list[str] = []

        def _save_mp3_custom_frame(id3, custom_key: str, custom_value: str) -> None:
            from mutagen.id3 import TXXX

            for frame in list(id3.getall("TXXX")):
                if getattr(frame, "desc", "") == custom_key:
                    try:
                        del id3[frame.HashKey]
                    except Exception:
                        pass
            id3.add(TXXX(encoding=3, desc=custom_key, text=[custom_value]))

        def _write_tag_to_file(path: Path) -> tuple[bool, str]:
            file_str = str(path)
            suf = path.suffix.lower()
            try:
                if suf == ".mp3":
                    try:
                        from mutagen.easyid3 import EasyID3
                        from mutagen.id3 import ID3, ID3NoHeaderError
                    except ImportError as exc:
                        return False, f"Unable to import MP3 metadata helpers: {exc}"

                    if remove_only:
                        easy_removed = False
                        custom_removed = False
                        try:
                            easy_tags = EasyID3(file_str)
                            if tag_name in easy_tags:
                                del easy_tags[tag_name]
                                easy_tags.save(file_str, v2_version=3)
                                easy_removed = True
                        except ID3NoHeaderError:
                            easy_tags = None
                        except Exception:
                            pass

                        try:
                            id3 = ID3(file_str)
                        except ID3NoHeaderError:
                            id3 = None
                        except Exception as exc:
                            return False, str(exc)

                        if id3 is not None:
                            for frame in list(id3.getall("TXXX")):
                                if getattr(frame, "desc", "") == tag_name:
                                    try:
                                        del id3[frame.HashKey]
                                        custom_removed = True
                                    except Exception:
                                        pass
                            if custom_removed:
                                id3.save(file_str, v2_version=3)

                        if easy_removed or custom_removed:
                            return True, ""
                        return False, f"Tag '{tag_name}' was not found."

                    try:
                        easy_tags = EasyID3(file_str)
                    except ID3NoHeaderError:
                        ID3().save(file_str, v2_version=3)
                        easy_tags = EasyID3(file_str)
                    except Exception:
                        easy_tags = None

                    if easy_tags is not None:
                        try:
                            easy_tags[tag_name] = [tag_value]
                            easy_tags.save(file_str, v2_version=3)
                            return True, ""
                        except Exception:
                            pass

                    try:
                        id3 = ID3(file_str)
                    except ID3NoHeaderError:
                        ID3().save(file_str, v2_version=3)
                        id3 = ID3(file_str)
                    _save_mp3_custom_frame(id3, tag_name, tag_value)
                    id3.save(file_str, v2_version=3)
                    return True, ""

                audio = mutagen.File(file_str, easy=False)
                if audio is None:
                    return False, "Unable to parse audio metadata for this file."

                tags = getattr(audio, "tags", None)
                if tags is None:
                    if remove_only:
                        return False, "No metadata tags were found."
                    try:
                        audio.add_tags()
                    except Exception:
                        pass
                    tags = getattr(audio, "tags", None)

                if tags is None:
                    return False, "Unable to initialize metadata tags for this file."

                if remove_only:
                    removed = False
                    if hasattr(tags, "delall"):
                        try:
                            tags.delall(tag_name)
                            removed = True
                        except Exception:
                            pass
                    if not removed:
                        try:
                            del tags[tag_name]
                            removed = True
                        except Exception:
                            pass
                    if not removed:
                        return False, f"Tag '{tag_name}' was not found."
                else:
                    try:
                        tags[tag_name] = [tag_value]
                    except Exception:
                        try:
                            tags[tag_name] = tag_value
                        except Exception as exc:
                            return False, str(exc)

                save_kwargs = {"v2_version": 3} if suf == ".mp3" else {}
                audio.save(**save_kwargs)
                return True, ""
            except Exception as exc:
                return False, str(exc)

        for file_path in selected_paths:
            path = Path(file_path)
            if not path.exists() or path.is_dir():
                continue
            ok, message = _write_tag_to_file(path)
            if ok:
                updated += 1
            else:
                failed.append(f"{path.name} ({message})")

        summary = f"Successfully updated {updated} file(s)."
        if remove_only:
            summary = f"Successfully processed {updated} file(s)."
        if failed:
            summary += f"\n\nFailed: {', '.join(failed[:10])}"
            if len(failed) > 10:
                summary += f"... and {len(failed) - 10} more"

        QMessageBox.information(self, "Metadata Update", summary)
        self.statusBar().showMessage(
            "Removed metadata tag" if remove_only else f"Updated metadata for {updated} files",
            4000,
        )

    @Slot(int)
    def _on_browser_selection_changed(self, count: int) -> None:
        self.browser_root_label.setText(f"{count} selected")
        self.browser_bulk_edit_btn.setEnabled(count > 0)

    @Slot(int)
    def _on_dir_size_scan_progress(self, scanned_dirs: int) -> None:
        self.browser_size_progress.setValue((scanned_dirs // 250) % 2)
        self.browser_size_progress.setFormat(f"Scanning folder sizes... {scanned_dirs} folders")

    @Slot(str, dict)
    def _on_dir_size_scan_finished(self, root_path: str, sizes: dict) -> None:
        if root_path != self._dir_size_scan_target:
            return

        self.browser_proxy_model.set_directory_size_cache(sizes)
        self.browser_proxy_model.set_directory_sizes_enabled(True)
        self.browser_proxy_model.invalidate()

        total_dirs = len(sizes)
        self.browser_size_progress.setRange(0, 1)
        self.browser_size_progress.setValue(1)
        self.browser_size_progress.setFormat(f"Folder sizes ready ({total_dirs} folders)")
        self.browser_size_progress.hide()
        self.statusBar().showMessage(f"Folder size scan complete: {total_dirs} folders", 4000)

    @Slot(str)
    def _on_dir_size_scan_failed(self, error: str) -> None:
        self.browser_proxy_model.set_directory_sizes_enabled(False)
        self.browser_proxy_model.clear_size_cache()
        self.browser_proxy_model.invalidate()
        self.browser_size_progress.setRange(0, 1)
        self.browser_size_progress.setValue(0)
        self.browser_size_progress.setFormat("Folder size scan failed")
        self.statusBar().showMessage(f"Folder size scan failed: {error}", 5000)

    @Slot()
    def _on_dir_size_scan_cancelled(self) -> None:
        if self._pending_dir_size_scan_target is None:
            self.browser_size_progress.setRange(0, 1)
            self.browser_size_progress.setValue(0)
            self.browser_size_progress.hide()

    @Slot()
    def _clear_dir_size_scan_refs(self) -> None:
        self._dir_size_scan_worker = None
        self._dir_size_scan_thread = None
        pending_target = self._pending_dir_size_scan_target
        self._pending_dir_size_scan_target = None
        if pending_target:
            self._start_directory_size_scan(pending_target)

    def _browser_source_index(self, index):
        if not index.isValid():
            return index
        if index.model() is self.browser_proxy_model:
            return self.browser_proxy_model.mapToSource(index)
        return index

    def _reset_album_art_results(self) -> None:
        self.album_art_summary_label.setText("No scan run yet.")
        self.album_art_table.setRowCount(0)
        self.album_art_progress.setRange(0, 1)
        self.album_art_progress.setValue(0)
        self.album_art_progress.setFormat("Idle")
        self.album_art_fix_btn.setEnabled(False)
        self._last_scan_target = None
        self._last_incompatible_files = []

    def _cached_embedded_album_art(
        self,
        file_path: Path,
        stat_info: os.stat_result | None = None,
    ) -> tuple[bytes | None, str]:
        if stat_info is None:
            stat_info = file_path.stat()

        key = (str(file_path), stat_info.st_mtime_ns, stat_info.st_size)
        cached = self._album_art_cache.get(key)
        if cached is not None:
            return cached

        art = read_embedded_album_art(file_path)
        self._album_art_cache[key] = art
        self._album_art_cache.move_to_end(key)
        if len(self._album_art_cache) > ALBUM_ART_CACHE_LIMIT:
            self._album_art_cache.popitem(last=False)

        return art

    def _purge_cached_album_art_for_path(self, file_path: Path) -> None:
        file_key = str(file_path)
        stale_keys = [key for key in self._album_art_cache if key[0] == file_key]
        for key in stale_keys:
            self._album_art_cache.pop(key, None)

    def _fix_album_art_core(self, file_path: Path) -> tuple[bool, str]:
        try:
            stat_info = file_path.stat()
            art_bytes, art_mime = self._cached_embedded_album_art(file_path, stat_info)
        except Exception as exc:
            return False, f"Unable to read embedded art: {exc}"

        if not art_bytes:
            return False, "No embedded art"

        if art_mime.lower() == "image/jpeg" and jpeg_scan_type(art_bytes) == "Non-progressive":
            return True, "Already compatible"

        converted_jpeg = to_non_progressive_jpeg(art_bytes)
        if not converted_jpeg:
            return False, "Unable to convert embedded art to JPEG"

        ok, detail = write_embedded_album_art(file_path, converted_jpeg)
        if not ok:
            return False, detail

        self._purge_cached_album_art_for_path(file_path)
        try:
            verified_stat = file_path.stat()
            verified_art, verified_mime = self._cached_embedded_album_art(file_path, verified_stat)
        except Exception as exc:
            return False, f"Unable to verify file: {exc}"

        compatible = (
            bool(verified_art)
            and verified_mime.lower() == "image/jpeg"
            and jpeg_scan_type(verified_art) == "Non-progressive"
        )
        if not compatible:
            return False, "Artwork was written but did not verify as JPEG non-progressive"

        return True, "Fixed"

    def on_browser_context_menu(self, pos) -> None:
        index = self.browser_tree.indexAt(pos)
        if not index.isValid():
            return

        self.browser_tree.setCurrentIndex(index)

        source_index = self._browser_source_index(index)
        path = self.browser_model.filePath(source_index)
        is_dir = self.browser_model.isDir(source_index)
        suffix = Path(path).suffix.lower()
        is_audio_file = (
            not is_dir
            and suffix in AUDIO_FILE_EXTENSIONS
        )

        menu = QMenu(self.browser_tree)
        rename_action = menu.addAction("Rename")
        add_art_action = None
        fix_action = None
        lookup_add_lyrics_action = None
        debug_action = None
        if is_audio_file:
            add_art_action = menu.addAction("Add Album Art")
            fix_action = menu.addAction("Fix Album Art")
            lookup_add_lyrics_action = menu.addAction("Lookup/Add Lyrics")
            menu.addSeparator()
            debug_action = menu.addAction("File Debug")

        selected_action = menu.exec(self.browser_tree.viewport().mapToGlobal(pos))
        if selected_action == rename_action:
            self.rename_browser_item(path)
        if add_art_action is not None and selected_action == add_art_action:
            self.add_album_art_for_file(path)
        if fix_action is not None and selected_action == fix_action:
            self.fix_album_art_for_file(path)
        if lookup_add_lyrics_action is not None and selected_action == lookup_add_lyrics_action:
            self.lookup_and_add_lyrics_for_file(path)
        if debug_action is not None and selected_action == debug_action:
            self.show_file_debug_dialog(path)

    def rename_browser_item(self, path: str) -> None:
        target_path = Path(path)
        if not target_path.exists():
            QMessageBox.warning(self, "Invalid Selection", "The selected item is not available.")
            return

        new_name, accepted = QInputDialog.getText(
            self,
            "Rename",
            "New name:",
            QLineEdit.Normal,
            target_path.name,
        )
        if not accepted:
            return

        normalized_name = new_name.strip()
        if not normalized_name:
            QMessageBox.warning(self, "Invalid Name", "File name cannot be empty.")
            return

        if normalized_name in {".", ".."}:
            QMessageBox.warning(self, "Invalid Name", "File name is not valid.")
            return

        if os.path.sep in normalized_name or (os.path.altsep and os.path.altsep in normalized_name):
            QMessageBox.warning(self, "Invalid Name", "File name cannot include path separators.")
            return

        if target_path.is_file() and Path(normalized_name).suffix == "":
            normalized_name = f"{normalized_name}{target_path.suffix}"

        new_path = target_path.with_name(normalized_name)
        if new_path == target_path:
            return

        if new_path.exists():
            QMessageBox.warning(self, "Name In Use", "A file with that name already exists.")
            return

        try:
            target_path.rename(new_path)
        except Exception as exc:
            QMessageBox.warning(self, "Rename Failed", f"Unable to rename item:\n{exc}")
            return

        self._purge_cached_album_art_for_path(target_path)
        self._purge_cached_album_art_for_path(new_path)
        self.statusBar().showMessage(f"Renamed: {target_path.name} -> {new_path.name}", 5000)

        new_source_index = self.browser_model.index(str(new_path))
        if not new_source_index.isValid():
            return

        new_proxy_index = self.browser_proxy_model.mapFromSource(new_source_index)
        if not new_proxy_index.isValid():
            return

        self.browser_tree.setCurrentIndex(new_proxy_index)
        self.browser_tree.scrollTo(new_proxy_index)
        self.on_browser_item_clicked(new_proxy_index)

    def fix_album_art_for_file(self, path: str) -> None:
        if self._scan_thread is not None and self._scan_thread.isRunning():
            QMessageBox.warning(
                self,
                "Scan In Progress",
                "Wait for the current album art scan to complete before fixing a file.",
            )
            return

        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            QMessageBox.warning(self, "Invalid File", "The selected file is not available.")
            return

        if file_path.suffix.lower() not in AUDIO_FILE_EXTENSIONS:
            QMessageBox.information(self, "Not Audio", "Fix Album Art is only available for audio files.")
            return

        progress = QProgressDialog("Preparing fix...", "Cancel", 0, 6, self)
        progress.setWindowTitle("Fix Album Art")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setValue(0)

        def update_step(step: int, text: str) -> bool:
            progress.setLabelText(text)
            progress.setValue(step)
            QApplication.processEvents()
            return progress.wasCanceled()

        if update_step(1, "Reading existing album art..."):
            self.statusBar().showMessage("Album art fix cancelled")
            return

        try:
            stat_info = file_path.stat()
            art_bytes, art_mime = self._cached_embedded_album_art(file_path, stat_info)
        except Exception as exc:
            progress.close()
            QMessageBox.warning(self, "Read Failed", f"Unable to read embedded art:\n{exc}")
            return

        if not art_bytes:
            progress.close()
            QMessageBox.information(self, "No Embedded Art", "The selected file has no embedded artwork.")
            return

        if art_mime.lower() == "image/jpeg" and jpeg_scan_type(art_bytes) == "Non-progressive":
            progress.setValue(6)
            QMessageBox.information(self, "Already Compatible", "Album art is already JPEG non-progressive.")
            self.statusBar().showMessage("Album art is already compatible")
            return

        if update_step(2, "Converting artwork to JPEG..."):
            self.statusBar().showMessage("Album art fix cancelled")
            return

        converted_jpeg = to_non_progressive_jpeg(art_bytes)
        if not converted_jpeg:
            progress.close()
            QMessageBox.warning(
                self,
                "Conversion Failed",
                "Unable to convert embedded art to JPEG.",
            )
            return

        if update_step(3, "Writing updated artwork tags..."):
            self.statusBar().showMessage("Album art fix cancelled")
            return

        ok, detail = write_embedded_album_art(file_path, converted_jpeg)
        if not ok:
            progress.close()
            QMessageBox.warning(self, "Write Failed", detail)
            return

        if update_step(4, "Verifying compatibility..."):
            self.statusBar().showMessage("Album art fix cancelled")
            return

        self._purge_cached_album_art_for_path(file_path)
        try:
            verified_stat = file_path.stat()
            verified_art, verified_mime = self._cached_embedded_album_art(file_path, verified_stat)
        except Exception as exc:
            progress.close()
            QMessageBox.warning(self, "Verification Failed", f"Unable to verify file:\n{exc}")
            return

        compatible = (
            bool(verified_art)
            and verified_mime.lower() == "image/jpeg"
            and jpeg_scan_type(verified_art) == "Non-progressive"
        )
        if not compatible:
            progress.close()
            QMessageBox.warning(
                self,
                "Verification Failed",
                "Artwork was written but did not verify as JPEG non-progressive.",
            )
            return

        if update_step(5, "Refreshing file details..."):
            self.statusBar().showMessage("Album art fix cancelled")
            return

        current_index = self.browser_tree.currentIndex()
        current_source_index = self._browser_source_index(current_index)
        if current_source_index.isValid() and self.browser_model.filePath(current_source_index) == str(file_path):
            self.on_browser_item_clicked(current_index)

        progress.setValue(6)
        self.statusBar().showMessage(f"Album art fixed: {file_path.name}")
        QMessageBox.information(
            self,
            "Album Art Fixed",
            f"{file_path.name}\n\nArtwork is now JPEG and non-progressive.",
        )

    def add_album_art_for_file(self, path: str) -> None:
        if self._scan_thread is not None and self._scan_thread.isRunning():
            QMessageBox.warning(
                self,
                "Scan In Progress",
                "Wait for the current album art scan to complete before adding artwork.",
            )
            return

        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            QMessageBox.warning(self, "Invalid File", "The selected file is not available.")
            return

        if file_path.suffix.lower() not in AUDIO_FILE_EXTENSIONS:
            QMessageBox.information(self, "Not Audio", "Add Album Art is only available for audio files.")
            return

        selected_image, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose Album Art Image",
            str(file_path.parent),
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff *.heic *.heif)",
        )
        if not selected_image:
            return

        progress = QProgressDialog("Preparing artwork...", "Cancel", 0, 5, self)
        progress.setWindowTitle("Add Album Art")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setValue(0)

        def update_step(step: int, text: str) -> bool:
            progress.setLabelText(text)
            progress.setValue(step)
            QApplication.processEvents()
            return progress.wasCanceled()

        if update_step(1, "Loading selected image..."):
            self.statusBar().showMessage("Add album art cancelled")
            return

        image_path = Path(selected_image)
        try:
            source_image_bytes = image_path.read_bytes()
        except Exception as exc:
            progress.close()
            QMessageBox.warning(self, "Read Failed", f"Unable to read selected image:\n{exc}")
            return

        if update_step(2, "Converting image to compatible JPEG..."):
            self.statusBar().showMessage("Add album art cancelled")
            return

        converted_jpeg = to_non_progressive_jpeg(source_image_bytes)
        if not converted_jpeg:
            progress.close()
            QMessageBox.warning(
                self,
                "Conversion Failed",
                "Unable to convert selected image to JPEG.",
            )
            return

        if update_step(3, "Writing embedded album art..."):
            self.statusBar().showMessage("Add album art cancelled")
            return

        ok, detail = write_embedded_album_art(file_path, converted_jpeg)
        if not ok:
            progress.close()
            QMessageBox.warning(self, "Write Failed", detail)
            return

        if update_step(4, "Verifying embedded artwork..."):
            self.statusBar().showMessage("Add album art cancelled")
            return

        self._purge_cached_album_art_for_path(file_path)
        try:
            verified_stat = file_path.stat()
            verified_art, verified_mime = self._cached_embedded_album_art(file_path, verified_stat)
        except Exception as exc:
            progress.close()
            QMessageBox.warning(self, "Verification Failed", f"Unable to verify file:\n{exc}")
            return

        compatible = (
            bool(verified_art)
            and verified_mime.lower() == "image/jpeg"
            and jpeg_scan_type(verified_art) == "Non-progressive"
        )
        if not compatible:
            progress.close()
            QMessageBox.warning(
                self,
                "Verification Failed",
                "Artwork was written but did not verify as JPEG non-progressive.",
            )
            return

        if update_step(5, "Refreshing file details..."):
            self.statusBar().showMessage("Add album art cancelled")
            return

        current_index = self.browser_tree.currentIndex()
        current_source_index = self._browser_source_index(current_index)
        if current_source_index.isValid() and self.browser_model.filePath(current_source_index) == str(file_path):
            self.on_browser_item_clicked(current_index)

        self.statusBar().showMessage(f"Album art added: {file_path.name}", 5000)
        QMessageBox.information(
            self,
            "Album Art Added",
            f"{file_path.name}\n\nAlbum art has been embedded as JPEG non-progressive.",
        )

    def show_file_debug_dialog(self, path: str) -> None:
        """Show comprehensive metadata debug information for a file."""
        file_path = Path(path)
        if not file_path.exists():
            QMessageBox.warning(self, "File Not Found", "The selected file no longer exists.")
            return

        # Gather all metadata
        debug_lines: list[str] = []
        debug_lines.append(f"File: {file_path.name}")
        debug_lines.append(f"Path: {file_path}")
        debug_lines.append(f"Size: {format_bytes(file_path.stat().st_size)}")
        debug_lines.append("")

        # FFprobe metadata
        debug_lines.append("=== FFprobe Audio Info ===")
        ffprobe_info = _ffprobe_audio_info(file_path)
        if ffprobe_info:
            for key in sorted(ffprobe_info.keys()):
                value = ffprobe_info[key]
                if key == "tags" and isinstance(value, dict):
                    debug_lines.append(f"{key}:")
                    for tag_key, tag_value in sorted(value.items()):
                        debug_lines.append(f"  {tag_key}: {tag_value}")
                else:
                    debug_lines.append(f"{key}: {value}")
        else:
            debug_lines.append("(No ffprobe data)")
        debug_lines.append("")

        # Mutagen metadata
        debug_lines.append("=== Mutagen Tags ===")
        if mutagen is not None:
            try:
                audio = mutagen.File(file_path)
                if audio:
                    debug_lines.append(f"Format: {audio.__class__.__name__}")
                    info = getattr(audio, "info", None)
                    if info:
                        debug_lines.append("Info attributes:")
                        for attr in sorted(dir(info)):
                            if attr.startswith("_"):
                                continue
                            try:
                                value = getattr(info, attr)
                                if not callable(value):
                                    debug_lines.append(f"  {attr}: {value}")
                            except Exception:
                                pass
                    tags = getattr(audio, "tags", None)
                    if tags:
                        debug_lines.append("Tags:")
                        try:
                            for key in sorted(tags.keys()):
                                value = tags[key]
                                debug_lines.append(f"  {key}: {value}")
                        except Exception:
                            debug_lines.append("  (Unable to read tags)")
                    else:
                        debug_lines.append("(No tags)")
                else:
                    debug_lines.append("(No audio data)")
            except Exception as exc:
                debug_lines.append(f"(Error reading: {exc})")
        else:
            debug_lines.append("(Mutagen not available)")

        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(f"File Debug: {file_path.name}")
        dialog.setGeometry(100, 100, 900, 600)

        layout = QVBoxLayout(dialog)

        # Title label
        title = QLabel(f"Debug Information for: {file_path.name}")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Text display (selectable and copyable)
        text_display = QPlainTextEdit()
        text_display.setPlainText("\n".join(debug_lines))
        text_display.setReadOnly(True)
        text_display.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(text_display)

        # Button bar
        button_layout = QHBoxLayout()
        
        copy_btn = QPushButton("Copy All to Clipboard")
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(text_display.toPlainText())
        )
        button_layout.addWidget(copy_btn)
        
        button_layout.addStretch(1)
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        dialog.exec()

    def lookup_and_add_lyrics_for_file(self, path: str) -> None:
        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            QMessageBox.warning(self, "Invalid File", "The selected file is not available.")
            return

        if file_path.suffix.lower() not in AUDIO_FILE_EXTENSIONS:
            QMessageBox.information(self, "Not Audio", "Lookup/Add Lyrics is only available for audio files.")
            return

        progress = QProgressDialog("Preparing lyrics lookup...", "Cancel", 0, 3, self)
        progress.setWindowTitle("Lookup/Add Lyrics")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setValue(0)

        def update_step(step: int, text: str) -> bool:
            progress.setLabelText(text)
            progress.setValue(step)
            QApplication.processEvents()
            return progress.wasCanceled()

        if update_step(1, "Reading file metadata..."):
            self.statusBar().showMessage("Lookup/Add Lyrics cancelled", 3000)
            return

        metadata, metadata_error = self._lyrics_lookup_metadata_for_file(file_path)
        if metadata_error:
            progress.close()
            QMessageBox.warning(
                self,
                "Metadata Read Failed",
                f"Unable to read metadata for lyrics lookup:\n{metadata_error}",
            )
            return

        title = str(metadata.get("title") or "").strip()
        artist = str(metadata.get("artist") or "").strip()
        album = str(metadata.get("album") or "").strip()
        duration = int(metadata.get("duration") or 0)

        if update_step(2, "Looking up lyrics in LRCLIB..."):
            self.statusBar().showMessage("Lookup/Add Lyrics cancelled", 3000)
            return

        status, source, lyrics_text = self._lookup_lyrics_from_lrclib(
            title=title,
            artist=artist,
            album=album,
            duration=duration,
        )

        if status != "Found" or not lyrics_text.strip():
            progress.close()
            if status == "Not found":
                QMessageBox.information(
                    self,
                    "Lyrics Not Found",
                    (
                        "No lyrics match found in LRCLIB for this file.\n\n"
                        f"Track: {title or file_path.stem}\n"
                        f"Artist: {artist or '(missing)'}\n"
                        f"Album: {album or '(missing)'}"
                    ),
                )
                return

            if status == "Instrumental":
                QMessageBox.information(
                    self,
                    "Instrumental Track",
                    "LRCLIB reports this track as instrumental.",
                )
                return

            QMessageBox.warning(
                self,
                "Lookup Failed",
                f"Unable to find lyrics:\n{source}",
            )
            return

        lrc_path = file_path.with_suffix(".lrc")
        preview = self._lyrics_lookup_preview(lyrics_text)
        overwrite_note = "Existing .lrc will be overwritten." if lrc_path.exists() else "A new .lrc file will be created."

        confirm = QMessageBox.question(
            self,
            "Add Lyrics",
            (
                f"Found lyrics via {source}.\n\n"
                f"Track: {title or file_path.stem}\n"
                f"Artist: {artist or '(missing)'}\n"
                f"Album: {album or '(missing)'}\n"
                f"Preview: {preview or '(lyrics available)'}\n\n"
                f"Output: {lrc_path.name}\n"
                f"{overwrite_note}\n\n"
                "Write lyrics now?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            progress.close()
            return

        if update_step(3, "Writing .lrc file..."):
            self.statusBar().showMessage("Lookup/Add Lyrics cancelled", 3000)
            return

        try:
            output_text = lyrics_text if lyrics_text.endswith("\n") else lyrics_text + "\n"
            lrc_path.write_text(output_text, encoding="utf-8")
        except Exception as exc:
            progress.close()
            QMessageBox.warning(
                self,
                "Write Failed",
                f"Unable to write .lrc file:\n{exc}",
            )
            return

        progress.close()
        self.statusBar().showMessage(f"Lyrics saved: {lrc_path.name}", 5000)
        QMessageBox.information(
            self,
            "Lyrics Added",
            f"Lyrics written to:\n{lrc_path}",
        )

    def scan_album_art_compatibility(self) -> None:
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self.statusBar().showMessage("Album art scan is already running")
            return

        target = self.path_input.text().strip()
        if not target:
            self.album_art_summary_label.setText("Choose a global target before scanning.")
            self.album_art_table.setRowCount(0)
            self.album_art_progress.setRange(0, 1)
            self.album_art_progress.setValue(0)
            self.album_art_progress.setFormat("Idle")
            return

        target_path = Path(target).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            self.album_art_summary_label.setText("Global target path is invalid.")
            self.album_art_table.setRowCount(0)
            self.album_art_progress.setRange(0, 1)
            self.album_art_progress.setValue(0)
            self.album_art_progress.setFormat("Idle")
            return

        self._last_scan_target = str(target_path.resolve())
        self._last_incompatible_files = []
        self.album_art_table.setRowCount(0)
        self.album_art_summary_label.setText("Preparing scan...")
        self.album_art_progress.setRange(0, 1)
        self.album_art_progress.setValue(0)
        self.album_art_progress.setFormat("Preparing scan...")
        self.album_art_scan_btn.setEnabled(False)
        self.album_art_fix_btn.setEnabled(False)
        self._scan_thread = QThread(self)
        self._scan_worker = AlbumArtScanWorker(target_path)
        self._scan_worker.moveToThread(self._scan_thread)

        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.progress.connect(self._on_album_art_scan_progress)
        self._scan_worker.finished.connect(self._on_album_art_scan_finished)
        self._scan_worker.failed.connect(self._on_album_art_scan_failed)

        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.failed.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._scan_worker.deleteLater)
        self._scan_thread.finished.connect(self._scan_thread.deleteLater)
        self._scan_thread.finished.connect(self._clear_scan_worker_refs)

        self._scan_thread.start()

    @Slot()
    def _clear_scan_worker_refs(self) -> None:
        self._scan_worker = None
        self._scan_thread = None

    @Slot(int, int, int, int, int)
    def _on_album_art_scan_progress(
        self,
        scanned_audio: int,
        total_audio: int,
        compatible: int,
        incompatible: int,
        missing_artwork: int,
    ) -> None:
        total_for_ui = max(total_audio, 1)
        self.album_art_progress.setRange(0, total_for_ui)
        self.album_art_progress.setValue(min(scanned_audio, total_for_ui))

        if total_audio > 0:
            self.album_art_progress.setFormat(f"Scanning {scanned_audio}/{total_audio}")
            self.album_art_summary_label.setText(
                "Scanned audio files: "
                f"{scanned_audio}/{total_audio} | Compatible: {compatible} | "
                f"Incompatible: {incompatible} | Missing Artwork: {missing_artwork}"
            )
        else:
            self.album_art_progress.setFormat("No audio files found")
            self.album_art_summary_label.setText("No audio files found in target.")

    @Slot(list, int, int, int, int)
    def _on_album_art_scan_finished(
        self,
        rows: list[tuple[str, str, str, str, str]],
        scanned_audio: int,
        compatible: int,
        incompatible: int,
        missing_artwork: int,
    ) -> None:
        self.album_art_scan_btn.setEnabled(True)
        self._last_incompatible_files = [
            file_name for file_name, status, _progressive, _file_type, _resolution in rows if status == "Incompatible"
        ]
        self.album_art_fix_btn.setEnabled(True)

        self.album_art_table.setUpdatesEnabled(False)
        try:
            self.album_art_table.setRowCount(len(rows))
            for row_index, (file_name, status, progressive, file_type, resolution) in enumerate(rows):
                self.album_art_table.setItem(row_index, 0, QTableWidgetItem(file_name))
                self.album_art_table.setItem(row_index, 1, QTableWidgetItem(status))
                self.album_art_table.setItem(row_index, 2, QTableWidgetItem(progressive))
                self.album_art_table.setItem(row_index, 3, QTableWidgetItem(file_type))
                self.album_art_table.setItem(row_index, 4, QTableWidgetItem(resolution))
        finally:
            self.album_art_table.setUpdatesEnabled(True)
            self.album_art_table.viewport().update()

        if scanned_audio == 0:
            self.album_art_progress.setRange(0, 1)
            self.album_art_progress.setValue(0)
            self.album_art_progress.setFormat("Complete: no audio files found")
            self.album_art_summary_label.setText("No audio files found in target.")
        else:
            self.album_art_progress.setRange(0, scanned_audio)
            self.album_art_progress.setValue(scanned_audio)
            self.album_art_progress.setFormat(
                f"Complete: {compatible} compatible, {incompatible} incompatible, "
                f"{missing_artwork} missing artwork"
            )
            self.album_art_summary_label.setText(
                f"Scanned audio files: {scanned_audio} | Compatible: {compatible} | "
                f"Incompatible: {incompatible} | Missing Artwork: {missing_artwork}"
            )

        self.statusBar().showMessage("Album art compatibility scan completed")

    @Slot(str)
    def _on_album_art_scan_failed(self, error: str) -> None:
        self.album_art_scan_btn.setEnabled(True)
        self.album_art_fix_btn.setEnabled(False)
        self._last_incompatible_files = []
        self.album_art_progress.setRange(0, 1)
        self.album_art_progress.setValue(0)
        self.album_art_progress.setFormat("Scan failed")
        self.album_art_summary_label.setText(f"Album art scan failed: {error}")
        self.statusBar().showMessage("Album art compatibility scan failed")

    def scan_music_compatibility(self) -> None:
        if (
            self._music_compatibility_scan_thread is not None
            and self._music_compatibility_scan_thread.isRunning()
        ):
            self.statusBar().showMessage("Music compatibility scan is already running")
            return

        target = self.path_input.text().strip()
        if not target:
            self.music_compatibility_summary_label.setText("Choose a target before scanning compatibility.")
            self.music_compatibility_table.setRowCount(0)
            self.music_compatibility_progress.setRange(0, 1)
            self.music_compatibility_progress.setValue(0)
            self.music_compatibility_progress.setFormat("Idle")
            QMessageBox.information(
                self,
                "No Target",
                "Choose a folder or drive before running music compatibility scan.",
            )
            self.music_compatibility_convert_btn.setEnabled(False)
            return

        target_path = Path(target).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            self.music_compatibility_summary_label.setText("Target path is invalid.")
            self.music_compatibility_table.setRowCount(0)
            self.music_compatibility_progress.setRange(0, 1)
            self.music_compatibility_progress.setValue(0)
            self.music_compatibility_progress.setFormat("Idle")
            QMessageBox.warning(
                self,
                "Invalid Target",
                "The selected target path is not a valid folder.",
            )
            self.music_compatibility_convert_btn.setEnabled(False)
            return

        resolved_target = Path(target_path.resolve())
        self._music_compatibility_scan_target = resolved_target
        self.music_compatibility_table.setRowCount(0)
        self.music_compatibility_summary_label.setText("Preparing compatibility scan...")
        self.music_compatibility_progress.setRange(0, 1)
        self.music_compatibility_progress.setValue(0)
        self.music_compatibility_progress.setFormat("Preparing scan...")
        self.music_compatibility_scan_btn.setEnabled(False)
        self.music_compatibility_cancel_btn.setEnabled(True)
        self.music_compatibility_convert_btn.setEnabled(False)

        self._music_compatibility_scan_thread = QThread(self)
        self._music_compatibility_scan_worker = MusicCompatibilityScanWorker(resolved_target)
        self._music_compatibility_scan_worker.moveToThread(self._music_compatibility_scan_thread)

        self._music_compatibility_scan_thread.started.connect(self._music_compatibility_scan_worker.run)
        self._music_compatibility_scan_worker.progress.connect(self._on_music_compatibility_scan_progress)
        self._music_compatibility_scan_worker.finished.connect(self._on_music_compatibility_scan_finished)
        self._music_compatibility_scan_worker.cancelled.connect(self._on_music_compatibility_scan_cancelled)
        self._music_compatibility_scan_worker.failed.connect(self._on_music_compatibility_scan_failed)

        self._music_compatibility_scan_worker.finished.connect(self._music_compatibility_scan_thread.quit)
        self._music_compatibility_scan_worker.cancelled.connect(self._music_compatibility_scan_thread.quit)
        self._music_compatibility_scan_worker.failed.connect(self._music_compatibility_scan_thread.quit)
        self._music_compatibility_scan_thread.finished.connect(
            self._music_compatibility_scan_worker.deleteLater
        )
        self._music_compatibility_scan_thread.finished.connect(
            self._music_compatibility_scan_thread.deleteLater
        )
        self._music_compatibility_scan_thread.finished.connect(self._clear_music_compatibility_scan_refs)

        self._music_compatibility_scan_thread.start()

    def cancel_music_compatibility_scan(self) -> None:
        worker = self._music_compatibility_scan_worker
        thread = self._music_compatibility_scan_thread
        if worker is None or thread is None or not thread.isRunning():
            return

        worker.request_cancel()
        self.music_compatibility_cancel_btn.setEnabled(False)
        self.music_compatibility_progress.setFormat("Cancelling scan...")
        self.statusBar().showMessage("Cancelling music compatibility scan...")

    def _update_music_compatibility_convert_button_state(self) -> None:
        target = self.path_input.text().strip()
        if not target:
            self.music_compatibility_convert_btn.setEnabled(False)
            return

        target_path = Path(target).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            self.music_compatibility_convert_btn.setEnabled(False)
            return

        resolved_target = target_path.resolve()
        has_actionable_results = (
            self._last_music_compatibility_unsupported_count > 0
            or self._last_music_compatibility_eq_incompatible_count > 0
        )
        self.music_compatibility_convert_btn.setEnabled(
            self._last_music_compatibility_scan_target == resolved_target
            and has_actionable_results
            and not self._music_conversion_busy
        )

    @Slot()
    def _clear_music_compatibility_scan_refs(self) -> None:
        self._music_compatibility_scan_worker = None
        self._music_compatibility_scan_thread = None

    @Slot(int, int, int, int, int, int, int)
    def _on_music_compatibility_scan_cancelled(
        self,
        scanned: int,
        total: int,
        supported: int,
        unsupported: int,
        unknown: int,
        skipped: int,
        eq_incompatible: int,
    ) -> None:
        self._music_compatibility_scan_target = None
        self._last_music_compatibility_unsupported_count = 0
        self._last_music_compatibility_eq_incompatible_count = 0
        self.music_compatibility_scan_btn.setEnabled(True)
        self.music_compatibility_cancel_btn.setEnabled(False)
        self.music_compatibility_progress.setRange(0, max(total, 1))
        self.music_compatibility_progress.setValue(min(scanned, max(total, 1)))
        self.music_compatibility_progress.setFormat(
            f"Scan cancelled at {scanned}/{total if total > 0 else 0}"
        )
        self.music_compatibility_summary_label.setText(
            f"Scan cancelled | Scanned: {scanned}/{total} | Supported: {supported} | Unsupported: {unsupported} | "
            f"Unknown: {unknown} | Skipped: {skipped}"
        )
        self.statusBar().showMessage("Music compatibility scan cancelled", 5000)
        self._update_music_compatibility_convert_button_state()

    def open_music_conversion_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("musicConvertDialog")
        dialog.setWindowTitle("Convert Incompatible Music")
        dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setAttribute(Qt.WA_StyledBackground, True)
        dialog.setMinimumWidth(560)
        dialog.setStyleSheet(
            """
            QDialog#musicConvertDialog {
                background-color: #262626;
                color: #ECECEC;
                border: 1px solid #3C3C3C;
                border-radius: 0px;
            }
            QDialog#musicConvertDialog QLabel {
                background-color: transparent;
                color: #ECECEC;
            }
            QDialog#musicConvertDialog QCheckBox {
                spacing: 8px;
                color: #ECECEC;
                padding: 2px 0px;
            }
            QDialog#musicConvertDialog QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #4B4B4B;
                border-radius: 0px;
                background-color: #1F1F1F;
            }
            QDialog#musicConvertDialog QCheckBox::indicator:checked {
                background-color: #565656;
            }
            """
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title_label = QLabel("Convert Incompatible Music")
        title_label.setObjectName("sectionLabel")

        intro_label = QLabel(
            "This tool converts your existing audio files into formats compatible with the Snowsky Echo Mini. In some cases, this process may reduce the audio quality to meet device limitations. If your original files are important, it’s recommended to back them up before proceeding."
        )
        intro_label.setWordWrap(True)

        eq_checkbox = QCheckBox("Make files EQ compatible (optional)")

        dry_run_checkbox = QCheckBox("Dry run only (preview changes, do not write files)")
        backup_checkbox = QCheckBox("Back up originals before conversion")

        speed_profile_label = QLabel("Speed profile")
        speed_profile_label.setObjectName("targetSummary")

        speed_profile_combo = QComboBox()
        speed_profile_combo.addItem("Fast (recommended)", "fast")
        speed_profile_combo.addItem("Balanced", "balanced")
        speed_profile_combo.addItem("Smallest files", "smallest")
        for profile_index in range(speed_profile_combo.count()):
            profile_key = str(speed_profile_combo.itemData(profile_index))
            profile_title, _compression_level, profile_help_text = self._conversion_profile_config(
                profile_key
            )
            speed_profile_combo.setItemData(
                profile_index,
                f"{profile_title}: {profile_help_text}",
                Qt.ToolTipRole,
            )
        speed_profile_combo.setCurrentIndex(0)

        def _update_speed_profile_tooltip() -> None:
            selected_index = speed_profile_combo.currentIndex()
            selected_tooltip = speed_profile_combo.itemData(selected_index, Qt.ToolTipRole)
            speed_profile_combo.setToolTip(str(selected_tooltip or ""))

        mode_label = QLabel()
        mode_label.setWordWrap(True)
        mode_label.setObjectName("targetSummary")

        quality_warning = QLabel(
            "Warning: Making files EQ compatible degrades audio quality further."
        )
        quality_warning.setWordWrap(True)
        quality_warning.setObjectName("targetSummary")

        overwrite_warning = QLabel()
        overwrite_warning.setWordWrap(True)
        overwrite_warning.setObjectName("targetSummary")

        backup_path_label = QLabel("Backup folder")
        backup_path_label.setObjectName("targetSummary")

        backup_path_input = QLineEdit()
        backup_path_input.setPlaceholderText("Backup folder (optional unless backup is enabled)")
        backup_path_browse_btn = QPushButton("Choose Backup Folder")

        backup_path_row = QHBoxLayout()
        backup_path_row.addWidget(backup_path_input, 1)
        backup_path_row.addWidget(backup_path_browse_btn)

        def _choose_backup_folder() -> None:
            selected = QFileDialog.getExistingDirectory(
                self,
                "Choose Backup Folder",
                str(Path.home()),
            )
            if selected:
                backup_path_input.setText(selected)

        backup_path_browse_btn.clicked.connect(_choose_backup_folder)

        def _update_backup_controls() -> None:
            backup_enabled = backup_checkbox.isChecked() and not dry_run_checkbox.isChecked()
            backup_path_label.setVisible(backup_enabled)
            backup_path_input.setVisible(backup_enabled)
            backup_path_browse_btn.setVisible(backup_enabled)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        confirm_btn = QPushButton("Convert")
        button_row.addWidget(cancel_btn)
        button_row.addWidget(confirm_btn)

        def _update_conversion_mode_text(eq_mode_enabled: bool) -> None:
            if eq_mode_enabled:
                mode_label.setText(
                    "Highest quality supported EQ-compatible output."
                )
                overwrite_warning.setText(
                    "Warning: This action will convert and overwrite files to the highest quality supported EQ-compatible output."
                )
                quality_warning.setVisible(True)
            else:
                mode_label.setText("Highest quality compatible FLAC output.")
                overwrite_warning.setText(
                    "Warning: This action will convert and overwrite files to the highest quality compatible FLAC."
                )
                quality_warning.setVisible(False)

            if dry_run_checkbox.isChecked():
                overwrite_warning.setText(
                    "Dry run: no files will be changed. The operation will only preview what would be converted."
                )

        _update_conversion_mode_text(eq_checkbox.isChecked())
        _update_speed_profile_tooltip()
        _update_backup_controls()
        eq_checkbox.toggled.connect(_update_conversion_mode_text)
        dry_run_checkbox.toggled.connect(lambda _checked: _update_conversion_mode_text(eq_checkbox.isChecked()))
        dry_run_checkbox.toggled.connect(lambda _checked: _update_backup_controls())
        backup_checkbox.toggled.connect(lambda _checked: _update_backup_controls())
        speed_profile_combo.currentIndexChanged.connect(lambda _index: _update_speed_profile_tooltip())
        cancel_btn.clicked.connect(dialog.reject)

        def _confirm_conversion_request() -> None:
            selected_profile = str(speed_profile_combo.currentData())
            profile_title, _compression_level, _profile_help_text = self._conversion_profile_config(
                selected_profile
            )

            if eq_checkbox.isChecked():
                confirm_text = (
                    "This will convert and overwrite files to the highest quality supported EQ-compatible output.\n"
                    "Making files EQ compatible will degrade audio quality further.\n\n"
                    f"Speed profile: {profile_title}\n\n"
                    "Do you want to continue?"
                )
            else:
                confirm_text = (
                    "This will convert and overwrite files to the highest quality compatible FLAC.\n\n"
                    f"Speed profile: {profile_title}\n\n"
                    "Do you want to continue?"
                )

            confirm = QMessageBox.warning(
                self,
                "Confirm Conversion",
                confirm_text,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm == QMessageBox.Yes:
                backup_root: Path | None = None
                if backup_checkbox.isChecked() and not dry_run_checkbox.isChecked():
                    backup_text = backup_path_input.text().strip()
                    if not backup_text:
                        QMessageBox.warning(
                            self,
                            "Backup Folder Required",
                            "Choose a backup folder or disable backup before starting conversion.",
                        )
                        return
                    backup_candidate = Path(backup_text).expanduser()
                    try:
                        backup_root = backup_candidate.resolve()
                    except Exception:
                        backup_root = backup_candidate

                dialog.accept()
                self.convert_incompatible_music(
                    eq_checkbox.isChecked(),
                    selected_profile,
                    dry_run=dry_run_checkbox.isChecked(),
                    backup_root=backup_root,
                )

        confirm_btn.clicked.connect(_confirm_conversion_request)

        layout.addWidget(title_label)
        layout.addWidget(intro_label)
        layout.addWidget(eq_checkbox)
        layout.addWidget(dry_run_checkbox)
        layout.addWidget(backup_checkbox)
        layout.addWidget(backup_path_label)
        layout.addLayout(backup_path_row)
        layout.addWidget(speed_profile_label)
        layout.addWidget(speed_profile_combo)
        layout.addWidget(mode_label)
        layout.addWidget(quality_warning)
        layout.addWidget(overwrite_warning)
        layout.addStretch(1)
        layout.addLayout(button_row)

        fixed_size = dialog.sizeHint()
        dialog.setFixedSize(fixed_size)
        dialog.setMask(QRegion(dialog.rect()))

        dialog.exec()

    def _set_conversion_ui_locked(self, locked: bool) -> None:
        enabled = not locked

        central_widget = self.centralWidget()
        if central_widget is not None:
            central_widget.setEnabled(enabled)

        menu_bar = self.menuBar()
        if menu_bar is not None:
            menu_bar.setEnabled(enabled)

    def _conversion_profile_config(self, profile_key: str) -> tuple[str, int, str]:
        profile_map: dict[str, tuple[str, int, str]] = {
            "fast": (
                "Fast",
                5,
                "Faster conversion, larger FLAC files.",
            ),
            "balanced": (
                "Balanced",
                8,
                "Good speed and compression balance.",
            ),
            "smallest": (
                "Smallest files",
                12,
                "Slowest conversion, smallest FLAC files.",
            ),
        }
        return profile_map.get(profile_key, profile_map["balanced"])

    def _collect_music_conversion_candidates(
        self,
        make_eq_compatible: bool,
        *,
        include_unknown_for_eq: bool = True,
    ) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for row in range(self.music_compatibility_table.rowCount()):
            file_item = self.music_compatibility_table.item(row, 0)
            status_item = self.music_compatibility_table.item(row, 3)
            sample_rate_item = self.music_compatibility_table.item(row, 5)
            bit_depth_item = self.music_compatibility_table.item(row, 6)
            eq_item = self.music_compatibility_table.item(row, 9)

            if file_item is None or status_item is None:
                continue

            status_text = status_item.text().strip().upper()
            eq_text = eq_item.text().strip().lower() if eq_item is not None else ""

            should_convert = status_text == "UNSUPPORTED"
            eq_actionable_states = {"not compatible"}
            if include_unknown_for_eq:
                eq_actionable_states.add("unknown")

            if make_eq_compatible and eq_text in eq_actionable_states:
                should_convert = True

            if not should_convert:
                continue

            candidates.append(
                {
                    "relative_file": file_item.text().strip(),
                    "sample_rate": sample_rate_item.text().strip() if sample_rate_item else "",
                    "bit_depth": bit_depth_item.text().strip() if bit_depth_item else "",
                }
            )

        return candidates

    def convert_incompatible_music(
        self,
        make_eq_compatible: bool,
        speed_profile: str,
        *,
        dry_run: bool = False,
        backup_root: Path | None = None,
    ) -> None:
        resolved_ffmpeg = _resolve_ffmpeg_executable()
        if resolved_ffmpeg is None:
            QMessageBox.warning(
                self,
                "ffmpeg Not Found",
                "ffmpeg is required for conversion but was not found in PATH or bundled assets.",
            )
            return

        profile_label, compression_level, _profile_help = self._conversion_profile_config(speed_profile)

        target_text = self.path_input.text().strip()
        if not target_text:
            QMessageBox.information(
                self,
                "No Target",
                "Choose a folder or drive target before conversion.",
            )
            return

        target_path = Path(target_text).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            QMessageBox.warning(
                self,
                "Invalid Target",
                "The selected target path is not a valid folder.",
            )
            return
        resolved_target_path = target_path.resolve()

        if self._last_music_compatibility_scan_target is None:
            QMessageBox.information(
                self,
                "Scan Required",
                "Run Music Compatibility scan before conversion.",
            )
            return

        if self._last_music_compatibility_scan_target != resolved_target_path:
            QMessageBox.warning(
                self,
                "Target Changed",
                "Target changed since last completed Music Compatibility scan. Run scan again before conversion.",
            )
            return

        candidates = self._collect_music_conversion_candidates(make_eq_compatible)
        if not candidates:
            QMessageBox.information(
                self,
                "Nothing To Convert",
                "No incompatible files matched the selected conversion mode.",
            )
            return

        mode_label = "EQ-compatible" if make_eq_compatible else "FLAC"
        self._music_conversion_mode_label = mode_label
        self._music_conversion_profile_label = profile_label

        progress_title = "Previewing incompatible music conversion..." if dry_run else (
            f"Converting incompatible music to {mode_label} ({profile_label})..."
        )

        progress = QProgressDialog(
            progress_title,
            "Cancel",
            0,
            len(candidates),
            self,
        )
        progress.setWindowTitle("Convert Incompatible Music")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.setWindowFlag(Qt.WindowCloseButtonHint, False)
        progress.show()
        QApplication.processEvents()

        self._music_conversion_progress_dialog = progress
        self._music_conversion_busy = True
        self._set_conversion_ui_locked(True)
        self._update_music_compatibility_convert_button_state()

        self._music_conversion_thread = QThread(self)
        self._music_conversion_worker = MusicConversionWorker(
            resolved_target_path,
            candidates,
            make_eq_compatible,
            compression_level,
            dry_run,
            backup_root,
        )
        # Provide the resolved ffmpeg executable path to the worker so it can
        # invoke the bundled/system ffmpeg directly.
        try:
            self._music_conversion_worker._ffmpeg_executable = resolved_ffmpeg
        except Exception:
            pass
        self._music_conversion_worker.moveToThread(self._music_conversion_thread)

        self._music_conversion_thread.started.connect(self._music_conversion_worker.run)
        self._music_conversion_worker.progress.connect(self._on_music_conversion_progress)
        self._music_conversion_worker.finished.connect(self._on_music_conversion_finished)
        self._music_conversion_worker.cancelled.connect(self._on_music_conversion_cancelled)
        self._music_conversion_worker.failed.connect(self._on_music_conversion_failed)

        self._music_conversion_worker.finished.connect(self._music_conversion_thread.quit)
        self._music_conversion_worker.cancelled.connect(self._music_conversion_thread.quit)
        self._music_conversion_worker.failed.connect(self._music_conversion_thread.quit)
        self._music_conversion_thread.finished.connect(self._music_conversion_worker.deleteLater)
        self._music_conversion_thread.finished.connect(self._music_conversion_thread.deleteLater)
        self._music_conversion_thread.finished.connect(self._clear_music_conversion_refs)

        progress.canceled.connect(self.cancel_music_conversion)

        self._music_conversion_thread.start()

    def cancel_music_conversion(self) -> None:
        worker = self._music_conversion_worker
        if worker is None:
            return
        worker.request_cancel()
        if self._music_conversion_progress_dialog is not None:
            self._music_conversion_progress_dialog.setLabelText("Cancelling conversion...")

    @Slot(int, int, str)
    def _on_music_conversion_progress(self, processed: int, total: int, detail: str) -> None:
        dialog = self._music_conversion_progress_dialog
        if dialog is None:
            return
        dialog.setRange(0, max(total, 1))
        dialog.setValue(min(processed, max(total, 1)))
        dialog.setLabelText(detail)

    @Slot(object)
    def _on_music_conversion_finished(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        converted = int(payload.get("converted") or 0)
        failed = int(payload.get("failed") or 0)
        planned = int(payload.get("planned") or 0)
        dry_run = bool(payload.get("dry_run"))
        backup_root = str(payload.get("backup_root") or "")
        failures = payload.get("failures") or []

        self._set_conversion_ui_locked(False)
        if self._music_conversion_progress_dialog is not None:
            self._music_conversion_progress_dialog.close()
            self._music_conversion_progress_dialog = None

        self._music_conversion_busy = False
        self._update_music_compatibility_convert_button_state()

        if dry_run:
            summary = (
                f"Dry run complete. Files that would be converted: {planned}. Failed prechecks: {failed}."
            )
            self.statusBar().showMessage(summary, 5000)
            if failed:
                preview = "\n".join(str(item) for item in failures[:15])
                QMessageBox.warning(
                    self,
                    "Dry Run Completed With Errors",
                    f"{summary}\n\nFailures:\n{preview}",
                )
            else:
                QMessageBox.information(self, "Dry Run Completed", summary)
            return

        summary = (
            f"Conversion complete ({self._music_conversion_mode_label}, {self._music_conversion_profile_label}). "
            f"Converted: {converted} | Failed: {failed}"
        )
        if backup_root:
            summary += f"\nBackups saved to: {backup_root}"
        self.statusBar().showMessage(summary.replace("\n", " | "), 5000)

        if failed:
            preview = "\n".join(str(item) for item in failures[:15])
            QMessageBox.warning(
                self,
                "Conversion Completed With Errors",
                f"{summary}\n\nFailures:\n{preview}",
            )
        else:
            QMessageBox.information(self, "Conversion Completed", summary)

        self.scan_music_compatibility()

    @Slot(object)
    def _on_music_conversion_cancelled(self, payload_obj) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        converted = int(payload.get("converted") or 0)
        failed = int(payload.get("failed") or 0)
        planned = int(payload.get("planned") or 0)
        dry_run = bool(payload.get("dry_run"))
        failures = payload.get("failures") or []

        self._set_conversion_ui_locked(False)
        if self._music_conversion_progress_dialog is not None:
            self._music_conversion_progress_dialog.close()
            self._music_conversion_progress_dialog = None

        self._music_conversion_busy = False
        self._update_music_compatibility_convert_button_state()

        if dry_run:
            message = f"Dry run cancelled. Planned: {planned} | Failed prechecks: {failed}"
        else:
            message = f"Conversion cancelled. Converted: {converted} | Failed: {failed}"

        self.statusBar().showMessage(message, 5000)
        if failures:
            preview = "\n".join(str(item) for item in failures[:10])
            QMessageBox.warning(
                self,
                "Conversion Cancelled",
                f"{message}\n\nFailures:\n{preview}",
            )

    @Slot(str)
    def _on_music_conversion_failed(self, error: str) -> None:
        self._set_conversion_ui_locked(False)
        if self._music_conversion_progress_dialog is not None:
            self._music_conversion_progress_dialog.close()
            self._music_conversion_progress_dialog = None

        self._music_conversion_busy = False
        self._update_music_compatibility_convert_button_state()
        self.statusBar().showMessage(f"Conversion failed: {error}", 5000)
        QMessageBox.warning(self, "Conversion Failed", error)

    @Slot()
    def _clear_music_conversion_refs(self) -> None:
        self._music_conversion_worker = None
        self._music_conversion_thread = None

    @Slot(int, int, int, int, int, int)
    @Slot(int, int, int, int, int, int, int)
    def _on_music_compatibility_scan_progress(
        self,
        scanned: int,
        total: int,
        supported: int,
        unsupported: int,
        unknown: int,
        skipped: int,
        eq_incompatible: int,
    ) -> None:
        total_for_ui = max(total, 1)
        self.music_compatibility_progress.setRange(0, total_for_ui)
        self.music_compatibility_progress.setValue(min(scanned, total_for_ui))
        self.music_compatibility_cancel_btn.setEnabled(True)

        if total == 0:
            self.music_compatibility_progress.setFormat("No files found")
            self.music_compatibility_summary_label.setText("No files found in target.")
            return

        self.music_compatibility_progress.setFormat(f"Scanning {scanned}/{total}")
        self.music_compatibility_summary_label.setText(
            f"Scanned: {scanned}/{total} | Supported: {supported} | Unsupported: {unsupported} | "
            f"Unknown: {unknown} | Skipped: {skipped}"
        )

    @Slot(str)
    def _apply_music_compatibility_table_filter(self, query: str) -> None:
        normalized_query = query.strip().lower()
        quick_filter = str(self.music_compatibility_quick_filter_combo.currentData() or "all")
        filter_all = not normalized_query

        for row in range(self.music_compatibility_table.rowCount()):
            quick_filter_match = True
            status_item = self.music_compatibility_table.item(row, 3)
            eq_item = self.music_compatibility_table.item(row, 9)
            status_text = status_item.text().strip().upper() if status_item is not None else ""
            eq_text = eq_item.text().strip().lower() if eq_item is not None else ""

            if quick_filter == "unsupported":
                quick_filter_match = status_text == "UNSUPPORTED"
            elif quick_filter == "unknown":
                quick_filter_match = status_text == "UNKNOWN"
            elif quick_filter == "supported":
                quick_filter_match = status_text == "SUPPORTED"
            elif quick_filter == "skipped":
                quick_filter_match = status_text == "SKIPPED"
            elif quick_filter == "eq_not_compatible":
                quick_filter_match = eq_text == "not compatible"
            elif quick_filter == "actionable":
                quick_filter_match = status_text == "UNSUPPORTED" or eq_text in {
                    "not compatible",
                    "unknown",
                }

            text_match = True
            if not filter_all:
                text_match = False
                for col in range(self.music_compatibility_table.columnCount()):
                    item = self.music_compatibility_table.item(row, col)
                    if item is None:
                        continue
                    if normalized_query in item.text().lower():
                        text_match = True
                        break

            self.music_compatibility_table.setRowHidden(row, not (quick_filter_match and text_match))

    @Slot(list, int, int, int, int, int, int)
    def _on_music_compatibility_scan_finished(
        self,
        rows: list[tuple[str, str, str, str, str, str, str, str, str, str, str, str, str]],
        supported: int,
        unsupported: int,
        unknown: int,
        skipped: int,
        total_files: int,
        eq_incompatible: int,
    ) -> None:
        self._last_music_compatibility_scan_target = self._music_compatibility_scan_target
        self._last_music_compatibility_unsupported_count = unsupported
        self._last_music_compatibility_eq_incompatible_count = eq_incompatible
        self._music_compatibility_scan_target = None
        self.music_compatibility_scan_btn.setEnabled(True)
        self.music_compatibility_cancel_btn.setEnabled(False)
        self._update_music_compatibility_convert_button_state()

        self.music_compatibility_table.setSortingEnabled(False)
        self.music_compatibility_table.setUpdatesEnabled(False)
        try:
            self.music_compatibility_table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                (
                    file_name,
                    extension,
                    codec,
                    status,
                    reason,
                    sample_rate,
                    bit_depth,
                    block_size,
                    dsd_profile,
                    eq_compatibility,
                    channels,
                    stream_count,
                    category,
                ) = row

                items = [
                    QTableWidgetItem(file_name),
                    QTableWidgetItem(extension),
                    QTableWidgetItem(codec),
                    QTableWidgetItem(status),
                    QTableWidgetItem(reason),
                    QTableWidgetItem(sample_rate),
                    QTableWidgetItem(bit_depth),
                    QTableWidgetItem(block_size),
                    QTableWidgetItem(dsd_profile),
                    QTableWidgetItem(eq_compatibility),
                    QTableWidgetItem(channels),
                    QTableWidgetItem(stream_count),
                ]

                reason_item = items[4]
                reason_item.setToolTip(reason)

                for col, item in enumerate(items):
                    self.music_compatibility_table.setItem(row_index, col, item)

                if category == "supported":
                    status_bg = QColor("#2E7D32")
                    status_fg = QColor("#F2FFF2")
                elif category == "unsupported":
                    status_bg = QColor("#7A2C2C")
                    status_fg = QColor("#FFF0F0")
                elif category == "unknown":
                    status_bg = QColor("#7A5E2C")
                    status_fg = QColor("#FFF9E6")
                else:
                    status_bg = QColor("#3C3C3C")
                    status_fg = QColor("#E2E2E2")

                status_item = self.music_compatibility_table.item(row_index, 3)
                if status_item is not None:
                    status_item.setBackground(status_bg)
                    status_item.setForeground(status_fg)

                eq_item = self.music_compatibility_table.item(row_index, 9)
                if eq_item is not None:
                    eq_text = eq_item.text().strip().lower()
                    if eq_text == "not compatible":
                        eq_item.setBackground(QColor("#7A2C2C"))
                        eq_item.setForeground(QColor("#FFF0F0"))
                    elif eq_text == "compatible":
                        eq_item.setBackground(QColor("#2E7D32"))
                        eq_item.setForeground(QColor("#F2FFF2"))
                    elif eq_text == "unknown":
                        eq_item.setBackground(QColor("#7A5E2C"))
                        eq_item.setForeground(QColor("#FFF9E6"))
        finally:
            self.music_compatibility_table.setUpdatesEnabled(True)
            self.music_compatibility_table.setSortingEnabled(True)
            self.music_compatibility_table.viewport().update()

        self._apply_music_compatibility_table_filter(self.music_compatibility_search_input.text())

        if total_files == 0:
            self.music_compatibility_progress.setRange(0, 1)
            self.music_compatibility_progress.setValue(0)
            self.music_compatibility_progress.setFormat("Complete: no files found")
            self.music_compatibility_summary_label.setText("No files found in target.")
        else:
            self.music_compatibility_progress.setRange(0, total_files)
            self.music_compatibility_progress.setValue(total_files)
            self.music_compatibility_progress.setFormat(
                f"Complete: {supported} supported, {unsupported} unsupported, {unknown} unknown"
            )
            self.music_compatibility_summary_label.setText(
                f"Files analyzed: {total_files} | Supported: {supported} | Unsupported: {unsupported} | "
                f"Unknown: {unknown} | Skipped: {skipped}"
            )

        self.statusBar().showMessage("Music compatibility scan completed", 5000)

    def _on_music_compatibility_table_context_menu(self, pos):
        """Handle right-click context menu on the music compatibility table."""
        item = self.music_compatibility_table.itemAt(pos)
        if item is None:
            return

        row = item.row()
        file_item = self.music_compatibility_table.item(row, 0)
        if file_item is None:
            return

        file_name = file_item.text()
        if not file_name:
            return

        # Find the full path from the file name
        target_dir = Path(self._music_compatibility_scan_target or self._last_music_compatibility_scan_target or ".")
        file_path = target_dir / file_name

        if not file_path.exists():
            QMessageBox.warning(
                self,
                "File Not Found",
                f"Could not locate file: {file_path}",
            )
            return

        # Create context menu
        menu = QMenu()
        view_streams_action = menu.addAction("View Streams")
        action = menu.exec(self.music_compatibility_table.mapToGlobal(pos))

        if action == view_streams_action:
            dialog = StreamsInfoDialog(file_path, self)
            dialog.exec()

    @Slot(str)
    def _on_music_compatibility_scan_failed(self, error: str) -> None:
        self._music_compatibility_scan_target = None
        self._last_music_compatibility_unsupported_count = 0
        self._last_music_compatibility_eq_incompatible_count = 0
        self.music_compatibility_scan_btn.setEnabled(True)
        self.music_compatibility_cancel_btn.setEnabled(False)
        self.music_compatibility_convert_btn.setEnabled(False)
        self.music_compatibility_progress.setRange(0, 1)
        self.music_compatibility_progress.setValue(0)
        self.music_compatibility_progress.setFormat("Scan failed")
        self.music_compatibility_summary_label.setText(f"Compatibility scan failed: {error}")
        self.statusBar().showMessage("Music compatibility scan failed", 5000)

    def scan_embedded_lyrics(self) -> None:
        target = self.path_input.text().strip()
        if not target:
            self._set_lyrics_manager_idle("Choose a target before scanning embedded lyrics.")
            QMessageBox.information(
                self,
                "No Target",
                "Choose a folder or drive before running embedded lyrics scan.",
            )
            return

        target_path = Path(target).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            self._set_lyrics_manager_idle("Target path is invalid.")
            QMessageBox.warning(
                self,
                "Invalid Target",
                "The selected target path is not a valid folder.",
            )
            return

        resolved_target = target_path.resolve()
        audio_files = self._collect_target_audio_files(resolved_target)
        total_audio = len(audio_files)

        self._configure_lyrics_manager_scan_table()
        self._lyrics_manager_scan_results = []
        self._lyrics_lookup_results = []
        self.lyrics_manager_table.setRowCount(0)
        self.lyrics_manager_progress.setRange(0, max(total_audio, 1))
        self.lyrics_manager_progress.setValue(0)
        self.lyrics_manager_progress.setFormat("Preparing scan...")
        self.lyrics_manager_scan_btn.setEnabled(False)
        self.lyrics_manager_bulk_lookup_btn.setEnabled(False)
        self.lyrics_manager_export_lrc_btn.setEnabled(False)
        self.lyrics_manager_apply_lookup_btn.setEnabled(False)

        if total_audio == 0:
            self.lyrics_manager_progress.setRange(0, 1)
            self.lyrics_manager_progress.setValue(0)
            self.lyrics_manager_progress.setFormat("Complete: no audio files found")
            self.lyrics_manager_summary_label.setText("No audio files found in target.")
            self._lyrics_manager_scan_target = str(resolved_target)
            self.lyrics_manager_scan_btn.setEnabled(True)
            self.lyrics_manager_bulk_lookup_btn.setEnabled(True)
            self.lyrics_manager_export_lrc_btn.setEnabled(True)
            return

        with_lyrics = 0
        without_lyrics = 0
        error_count = 0
        rows: list[tuple[str, str, str]] = []
        scan_results: list[dict[str, object]] = []
        matching_lrc = 0

        try:
            for index, file_path in enumerate(audio_files, start=1):
                entries, error = self._embedded_lyrics_entries_for_file(file_path)
                try:
                    relative_path = file_path.relative_to(resolved_target).as_posix()
                except Exception:
                    relative_path = str(file_path)

                lrc_exists = file_path.with_suffix(".lrc").exists()
                lrc_status = "Yes" if lrc_exists else "No"
                if lrc_exists:
                    matching_lrc += 1

                if error:
                    error_count += 1
                    rows.append((relative_path, "Error", lrc_status))
                elif entries:
                    with_lyrics += 1
                    rows.append((relative_path, "Yes", lrc_status))
                else:
                    without_lyrics += 1
                    rows.append((relative_path, "No", lrc_status))

                scan_results.append(
                    {
                        "file_path": str(file_path),
                        "relative_path": relative_path,
                        "embedded": "Error" if error else ("Yes" if entries else "No"),
                        "lrc_status": lrc_status,
                        "has_lyrics": bool(entries),
                        "has_error": bool(error),
                    }
                )

                self.lyrics_manager_progress.setValue(index)
                self.lyrics_manager_progress.setFormat(f"Scanning {index}/{total_audio}")
                if index % 25 == 0 or index == total_audio:
                    self.lyrics_manager_summary_label.setText(
                        f"Scanning: {index}/{total_audio} | With lyrics: {with_lyrics} | Without lyrics: {without_lyrics} | Errors: {error_count}"
                    )
                QApplication.processEvents()
        finally:
            self.lyrics_manager_scan_btn.setEnabled(True)
            self.lyrics_manager_bulk_lookup_btn.setEnabled(True)
            self.lyrics_manager_export_lrc_btn.setEnabled(True)

        self.lyrics_manager_table.setSortingEnabled(False)
        self.lyrics_manager_table.setUpdatesEnabled(False)
        try:
            self.lyrics_manager_table.setRowCount(len(rows))
            for row_index, (file_name, has_lyrics, lrc_status) in enumerate(rows):
                self.lyrics_manager_table.setItem(row_index, 0, QTableWidgetItem(file_name))
                self.lyrics_manager_table.setItem(row_index, 1, QTableWidgetItem(has_lyrics))
                self.lyrics_manager_table.setItem(row_index, 2, QTableWidgetItem(lrc_status))

                status_item = self.lyrics_manager_table.item(row_index, 1)
                if status_item is None:
                    continue

                if has_lyrics == "Yes":
                    status_item.setBackground(QColor("#2E7D32"))
                    status_item.setForeground(QColor("#F2FFF2"))
                elif has_lyrics == "No":
                    status_item.setBackground(QColor("#3C3C3C"))
                    status_item.setForeground(QColor("#E2E2E2"))
                else:
                    status_item.setBackground(QColor("#7A2C2C"))
                    status_item.setForeground(QColor("#FFF0F0"))
        finally:
            self.lyrics_manager_table.setUpdatesEnabled(True)
            self.lyrics_manager_table.setSortingEnabled(True)
            self.lyrics_manager_table.viewport().update()

        self._lyrics_manager_scan_target = str(resolved_target)
        self._lyrics_manager_scan_results = scan_results
        self.lyrics_manager_progress.setRange(0, total_audio)
        self.lyrics_manager_progress.setValue(total_audio)
        self.lyrics_manager_progress.setFormat(
            f"Complete: {with_lyrics} with lyrics, {without_lyrics} without lyrics"
        )
        self.lyrics_manager_summary_label.setText(
            f"Audio scanned: {total_audio} | Embedded: {with_lyrics} | Without embedded: {without_lyrics} | Matching LRC: {matching_lrc} | Errors: {error_count}"
        )
        self.statusBar().showMessage("Embedded lyrics scan completed", 5000)

    def _collect_target_audio_files(self, target_root: Path) -> list[Path]:
        audio_files: list[Path] = []
        for root_dir, dir_names, file_names in os.walk(str(target_root)):
            dir_names[:] = [name for name in dir_names if not name.startswith(".")]
            for file_name in file_names:
                if file_name.startswith("."):
                    continue
                file_path = Path(root_dir) / file_name
                if file_path.suffix.lower() in AUDIO_FILE_EXTENSIONS:
                    audio_files.append(file_path)
        return audio_files

    def _best_lrc_text_from_entries(self, entries: list[tuple[str, str]]) -> str:
        if not entries:
            return ""

        def score(source: str) -> int:
            normalized = source.lower()
            if "uslt" in normalized:
                return 0
            if "unsyncedlyrics" in normalized or "tag lyrics" in normalized:
                return 1
            if "\xa9lyr" in normalized or "©lyr" in normalized:
                return 2
            if "sylt" in normalized:
                return 3
            return 4

        ordered = sorted(entries, key=lambda item: (score(item[0]), item[0].lower()))
        text = ordered[0][1].replace("\r\n", "\n").replace("\r", "\n").strip()
        return text

    def _lrclib_request_json(
        self,
        endpoint: str,
        params: dict[str, str | int],
    ) -> tuple[object | None, str | None, int]:
        filtered_params: dict[str, str] = {}
        for key, value in params.items():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            filtered_params[key] = text

        base_url = f"https://lrclib.net{endpoint}"
        query = urllib.parse.urlencode(filtered_params)
        url = f"{base_url}?{query}" if query else base_url

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Snowsky-Echo-Mini-Toolbox/1.0",
                "Accept": "application/json",
            },
        )

        ssl_contexts: list[ssl.SSLContext] = []
        certifi_bundle = ""
        try:
            certifi_module = __import__("certifi")
            certifi_bundle = str(certifi_module.where())
        except Exception:
            certifi_bundle = ""

        if certifi_bundle:
            try:
                ssl_contexts.append(ssl.create_default_context(cafile=certifi_bundle))
            except Exception:
                pass
        try:
            ssl_contexts.append(ssl.create_default_context())
        except Exception:
            pass

        if not ssl_contexts:
            return None, "Unable to initialize TLS context", 0

        cert_error: str | None = None

        for ssl_context in ssl_contexts:
            try:
                with urllib.request.urlopen(request, timeout=12, context=ssl_context) as response:
                    payload = response.read()
                    status = int(getattr(response, "status", 200))
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None, None, 404
                try:
                    error_payload = exc.read().decode("utf-8", "ignore").strip()
                except Exception:
                    error_payload = ""
                return None, (error_payload or str(exc)), int(exc.code)
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", None)
                reason_text = str(reason or exc)
                if "CERTIFICATE_VERIFY_FAILED" in reason_text:
                    cert_error = reason_text
                    continue
                return None, str(exc), 0
            except ssl.SSLError as exc:
                if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    cert_error = str(exc)
                    continue
                return None, str(exc), 0
            except Exception as exc:
                return None, str(exc), 0
        else:
            # Reached only if all contexts fail certificate verification.
            detail = cert_error or "CERTIFICATE_VERIFY_FAILED"
            return (
                None,
                (
                    "TLS certificate verification failed while contacting lrclib.net. "
                    "Install/update certificate bundles for your Python environment (for example, install certifi), "
                    f"then retry. Details: {detail}"
                ),
                0,
            )

        try:
            data = json.loads(payload.decode("utf-8", "ignore"))
        except Exception as exc:
            return None, f"Invalid API response: {exc}", status

        return data, None, status

    def _metadata_text_from_tags(self, tags, candidate_keys: list[str]) -> str:
        if not tags:
            return ""

        getall = getattr(tags, "getall", None)
        for key in candidate_keys:
            try:
                raw_value = list(getall(key)) if callable(getall) else tags.get(key)
            except Exception:
                raw_value = None

            text = self._lyrics_value_to_text(raw_value).strip()
            if not text:
                continue

            first_line = text.splitlines()[0].strip()
            if first_line:
                return first_line

        return ""

    def _lyrics_lookup_metadata_for_file(self, file_path: Path) -> tuple[dict[str, object], str | None]:
        if mutagen is None:
            return {}, "Mutagen is required for metadata lookup."

        try:
            audio = mutagen.File(file_path)
        except Exception as exc:
            return {}, str(exc)

        if not audio:
            return {}, "Unable to read metadata from audio file."

        tags = getattr(audio, "tags", None)
        title = self._metadata_text_from_tags(tags, ["title", "TIT2", "\xa9nam", "©nam"])
        artist = self._metadata_text_from_tags(
            tags,
            ["artist", "albumartist", "TPE1", "TPE2", "\xa9ART", "©ART", "aART"],
        )
        album = self._metadata_text_from_tags(tags, ["album", "TALB", "\xa9alb", "©alb"])

        # Easy tags provide consistent keys for many formats.
        try:
            easy_audio = mutagen.File(file_path, easy=True)
        except Exception:
            easy_audio = None

        easy_tags = getattr(easy_audio, "tags", None) if easy_audio else None
        if easy_tags:
            easy_title = self._metadata_text_from_tags(easy_tags, ["title"])
            easy_artist = self._metadata_text_from_tags(easy_tags, ["artist", "albumartist"])
            easy_album = self._metadata_text_from_tags(easy_tags, ["album"])
            if easy_title and not title:
                title = easy_title
            if easy_artist and not artist:
                artist = easy_artist
            if easy_album and not album:
                album = easy_album

        if not title:
            title = file_path.stem.strip()

        # Keep only primary artist token for more stable signature matching.
        for separator in [";", "/", ","]:
            if separator in artist:
                artist = artist.split(separator, 1)[0].strip()

        duration_seconds = 0
        info = getattr(audio, "info", None)
        if info is not None:
            try:
                duration_value = float(getattr(info, "length", 0.0) or 0.0)
                duration_seconds = max(0, int(round(duration_value)))
            except Exception:
                duration_seconds = 0

        metadata = {
            "title": title.strip(),
            "artist": artist.strip(),
            "album": album.strip(),
            "duration": duration_seconds,
        }
        return metadata, None

    def _lyrics_text_from_lrclib_record(self, record: dict[str, object]) -> str:
        synced = str(record.get("syncedLyrics") or "").strip()
        if synced:
            return synced
        plain = str(record.get("plainLyrics") or "").strip()
        return plain

    def _select_best_lrclib_search_result(
        self,
        records: list[object],
        title: str,
        artist: str,
        album: str,
        duration: int,
    ) -> dict[str, object] | None:
        normalized_title = title.strip().lower()
        normalized_artist = artist.strip().lower()
        normalized_album = album.strip().lower()

        def score(record_obj: object) -> int:
            if not isinstance(record_obj, dict):
                return -9999

            track_name = str(record_obj.get("trackName") or "").strip().lower()
            artist_name = str(record_obj.get("artistName") or "").strip().lower()
            album_name = str(record_obj.get("albumName") or "").strip().lower()

            total = 0
            if normalized_title and track_name:
                if track_name == normalized_title:
                    total += 50
                elif normalized_title in track_name or track_name in normalized_title:
                    total += 25

            if normalized_artist and artist_name:
                if artist_name == normalized_artist:
                    total += 45
                elif normalized_artist in artist_name or artist_name in normalized_artist:
                    total += 20

            if normalized_album and album_name:
                if album_name == normalized_album:
                    total += 20
                elif normalized_album in album_name or album_name in normalized_album:
                    total += 10

            try:
                record_duration = int(record_obj.get("duration") or 0)
            except Exception:
                record_duration = 0

            if duration > 0 and record_duration > 0:
                delta = abs(record_duration - duration)
                if delta <= 2:
                    total += 35
                elif delta <= 10:
                    total += max(0, 20 - delta)

            return total

        ranked = sorted(records, key=score, reverse=True)
        for record in ranked:
            if isinstance(record, dict):
                return record
        return None

    def _lookup_lyrics_from_lrclib(
        self,
        title: str,
        artist: str,
        album: str,
        duration: int,
    ) -> tuple[str, str, str]:
        if not title:
            return "Missing metadata", "Track title is missing", ""
        if not artist:
            return "Missing metadata", "Artist is missing", ""

        if album and duration > 0:
            signature_params = {
                "track_name": title,
                "artist_name": artist,
                "album_name": album,
                "duration": duration,
            }

            for endpoint, source_label in [
                ("/api/get-cached", "get-cached"),
                ("/api/get", "get"),
            ]:
                response, error, status = self._lrclib_request_json(endpoint, signature_params)
                if error and status != 404:
                    return "Error", f"{source_label}: {error}", ""
                if not isinstance(response, dict):
                    continue

                lyrics_text = self._lyrics_text_from_lrclib_record(response)
                if lyrics_text:
                    return "Found", source_label, lyrics_text

                if bool(response.get("instrumental")):
                    return "Instrumental", source_label, ""

        search_params: dict[str, str | int] = {
            "track_name": title,
            "artist_name": artist,
        }
        if album:
            search_params["album_name"] = album

        search_response, search_error, search_status = self._lrclib_request_json("/api/search", search_params)
        if search_error and search_status != 404:
            return "Error", f"search: {search_error}", ""

        if isinstance(search_response, list) and search_response:
            record = self._select_best_lrclib_search_result(
                search_response,
                title=title,
                artist=artist,
                album=album,
                duration=duration,
            )
            if isinstance(record, dict):
                lyrics_text = self._lyrics_text_from_lrclib_record(record)
                if lyrics_text:
                    return "Found", "search", lyrics_text
                if bool(record.get("instrumental")):
                    return "Instrumental", "search", ""

        return "Not found", "No match in LRCLIB", ""

    def _lyrics_lookup_preview(self, lyrics_text: str) -> str:
        if not lyrics_text:
            return ""

        for line in lyrics_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if len(stripped) > 96:
                return stripped[:93] + "..."
            return stripped
        return ""

    def _populate_lyrics_lookup_table(self) -> None:
        self._configure_lyrics_manager_lookup_table()
        self.lyrics_manager_table.setSortingEnabled(False)
        self.lyrics_manager_table.setUpdatesEnabled(False)
        try:
            self.lyrics_manager_table.setRowCount(len(self._lyrics_lookup_results))
            for row_index, row_data in enumerate(self._lyrics_lookup_results):
                relative_path = str(row_data.get("relative_path") or "")
                status = str(row_data.get("status") or "")
                source = str(row_data.get("source") or "")
                apply_status = str(row_data.get("apply_status") or "-")
                preview = str(row_data.get("preview") or "")

                self.lyrics_manager_table.setItem(row_index, 0, QTableWidgetItem(relative_path))
                self.lyrics_manager_table.setItem(row_index, 1, QTableWidgetItem(status))
                self.lyrics_manager_table.setItem(row_index, 2, QTableWidgetItem(source))
                self.lyrics_manager_table.setItem(row_index, 3, QTableWidgetItem(apply_status))
                self.lyrics_manager_table.setItem(row_index, 4, QTableWidgetItem(preview))

                status_item = self.lyrics_manager_table.item(row_index, 1)
                apply_item = self.lyrics_manager_table.item(row_index, 3)

                if status_item is not None:
                    if status == "Found":
                        status_item.setBackground(QColor("#2E7D32"))
                        status_item.setForeground(QColor("#F2FFF2"))
                    elif status in {"Not found", "Instrumental"}:
                        status_item.setBackground(QColor("#3C3C3C"))
                        status_item.setForeground(QColor("#E2E2E2"))
                    else:
                        status_item.setBackground(QColor("#7A2C2C"))
                        status_item.setForeground(QColor("#FFF0F0"))

                if apply_item is not None:
                    if apply_status == "Applied":
                        apply_item.setBackground(QColor("#2E7D32"))
                        apply_item.setForeground(QColor("#F2FFF2"))
                    elif apply_status == "Error":
                        apply_item.setBackground(QColor("#7A2C2C"))
                        apply_item.setForeground(QColor("#FFF0F0"))
        finally:
            self.lyrics_manager_table.setUpdatesEnabled(True)
            self.lyrics_manager_table.setSortingEnabled(True)
            self.lyrics_manager_table.viewport().update()

    def bulk_lookup_lyrics(self) -> None:
        # Require a scan to have been run first.
        if not self._lyrics_manager_scan_target:
            QMessageBox.information(
                self,
                "Run Scan First",
                "Run 'Scan Lyrics' first to inventory audio files before performing a bulk lookup.",
            )
            return
        target = self.path_input.text().strip()
        if not target:
            self._set_lyrics_manager_idle("Choose a target before running bulk lookup.")
            QMessageBox.information(
                self,
                "No Target",
                "Choose a folder or drive before running Bulk Lookup.",
            )
            return

        target_path = Path(target).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            self._set_lyrics_manager_idle("Target path is invalid.")
            QMessageBox.warning(
                self,
                "Invalid Target",
                "The selected target path is not a valid folder.",
            )
            return

        resolved_target = target_path.resolve()

        # Prefer the prior Scan Lyrics results so bulk lookup only targets
        # files the scan has already identified as needing lyrics work.
        scan_results: list[dict[str, object]] = []
        if self._lyrics_manager_scan_target:
            try:
                scan_target_path = Path(self._lyrics_manager_scan_target).resolve()
            except Exception:
                scan_target_path = None

            if scan_target_path == resolved_target and self._lyrics_manager_scan_results:
                scan_results = list(self._lyrics_manager_scan_results)

        if scan_results:
            audio_files = []
            for row in scan_results:
                if str(row.get("embedded") or "") != "No":
                    continue
                if str(row.get("lrc_status") or "") == "Yes":
                    continue
                file_path_text = str(row.get("file_path") or "")
                if not file_path_text:
                    continue
                audio_files.append(Path(file_path_text))
        else:
            audio_files = self._collect_target_audio_files(resolved_target)

        total_audio = len(audio_files)

        self._lyrics_lookup_results = []
        self._configure_lyrics_manager_lookup_table()
        self.lyrics_manager_table.setRowCount(0)
        self.lyrics_manager_progress.setRange(0, max(total_audio, 1))
        self.lyrics_manager_progress.setValue(0)
        self.lyrics_manager_progress.setFormat("Preparing lookup...")
        self.lyrics_manager_scan_btn.setEnabled(False)
        self.lyrics_manager_bulk_lookup_btn.setEnabled(False)
        self.lyrics_manager_export_lrc_btn.setEnabled(False)
        self.lyrics_manager_apply_lookup_btn.setEnabled(False)

        if total_audio == 0:
            self.lyrics_manager_progress.setRange(0, 1)
            self.lyrics_manager_progress.setValue(0)
            self.lyrics_manager_progress.setFormat("Complete: no eligible audio files found")
            if scan_results:
                self.lyrics_manager_summary_label.setText(
                    "Scan found no files that needed LRCLIB lookup (embedded lyrics or .lrc sidecars already present)."
                )
            else:
                self.lyrics_manager_summary_label.setText("No audio files found in target.")
            self.lyrics_manager_scan_btn.setEnabled(True)
            self.lyrics_manager_bulk_lookup_btn.setEnabled(True)
            self.lyrics_manager_export_lrc_btn.setEnabled(True)
            self._lyrics_manager_scan_target = str(resolved_target)
            return

        found_count = 0
        not_found_count = 0
        instrumental_count = 0
        error_count = 0

        try:
            # Use scan results when available, so bulk lookup only runs on
            # files that the scan already marked as missing embedded lyrics
            # and without an existing .lrc sidecar.
            # Parallelize network lookups to speed up bulk operations while
            # keeping metadata extraction synchronous (mutagen is not always
            # thread-safe across all formats). We'll collect metadata first,
            # then perform LRCLIB requests in a ThreadPoolExecutor. Results
            # are cached in-memory for this run to avoid duplicate queries.
            lookup_jobs: list[tuple[int, Path, str, dict[str, object] | None, str | None]] = []
            for index, file_path in enumerate(audio_files, start=1):
                try:
                    relative_path = file_path.relative_to(resolved_target).as_posix()
                except Exception:
                    relative_path = str(file_path)

                metadata, metadata_error = self._lyrics_lookup_metadata_for_file(file_path)
                lookup_jobs.append((index, file_path, relative_path, metadata if not metadata_error else None, metadata_error))

            from concurrent.futures import ThreadPoolExecutor, as_completed

            max_workers = min(8, (os.cpu_count() or 4) * 2)
            with ThreadPoolExecutor(max_workers=max_workers) as exc:
                future_to_job = {}
                for index, file_path, relative_path, metadata, metadata_error in lookup_jobs:
                    if metadata_error:
                        # no network request; record error directly
                        self._lyrics_lookup_results.append(
                            {
                                "file_path": str(file_path),
                                "relative_path": relative_path,
                                "status": "Error",
                                "source": f"metadata: {metadata_error}",
                                "preview": "",
                                "lyrics_text": "",
                                "apply_status": "-",
                            }
                        )
                        error_count += 1
                        self.lyrics_manager_progress.setValue(index)
                        continue

                    title = str(metadata.get("title") or "")
                    artist = str(metadata.get("artist") or "")
                    album = str(metadata.get("album") or "")
                    duration = int(metadata.get("duration") or 0)

                    key = (title, artist, album, duration)
                    cached = self._lrclib_cache.get(key)
                    if cached is not None:
                        status, source, lyrics_text = cached
                        preview = self._lyrics_lookup_preview(lyrics_text)
                        apply_status = "Ready" if lyrics_text else "-"
                        self._lyrics_lookup_results.append(
                            {
                                "file_path": str(file_path),
                                "relative_path": relative_path,
                                "status": status,
                                "source": source,
                                "preview": preview,
                                "lyrics_text": lyrics_text,
                                "apply_status": apply_status,
                            }
                        )
                        if status == "Found":
                            found_count += 1
                        elif status == "Not found":
                            not_found_count += 1
                        elif status == "Instrumental":
                            instrumental_count += 1
                        else:
                            error_count += 1
                        self.lyrics_manager_progress.setValue(index)
                        continue

                    # submit network lookup to pool
                    future = exc.submit(
                        self._lookup_lyrics_from_lrclib, title, artist, album, duration
                    )
                    future_to_job[future] = (index, file_path, relative_path, key)

                # Collect results as they complete
                for future in as_completed(future_to_job):
                    index, file_path, relative_path, key = future_to_job[future]
                    try:
                        status, source, lyrics_text = future.result()
                    except Exception as exc:
                        status = "Error"
                        source = f"exception: {exc}"
                        lyrics_text = ""

                    # cache the result
                    try:
                        self._lrclib_cache[key] = (status, source, lyrics_text)
                    except Exception:
                        pass

                    preview = self._lyrics_lookup_preview(lyrics_text)
                    apply_status = "Ready" if lyrics_text else "-"

                    self._lyrics_lookup_results.append(
                        {
                            "file_path": str(file_path),
                            "relative_path": relative_path,
                            "status": status,
                            "source": source,
                            "preview": preview,
                            "lyrics_text": lyrics_text,
                            "apply_status": apply_status,
                        }
                    )

                    if status == "Found":
                        found_count += 1
                    elif status == "Not found":
                        not_found_count += 1
                    elif status == "Instrumental":
                        instrumental_count += 1
                    else:
                        error_count += 1

                    # update progress for the file's index
                    self.lyrics_manager_progress.setValue(index)
                    if index % 10 == 0 or index == total_audio:
                        self.lyrics_manager_summary_label.setText(
                            f"Lookup: {index}/{total_audio} | Found: {found_count} | Not found: {not_found_count} | Instrumental: {instrumental_count} | Errors: {error_count}"
                        )
                    QApplication.processEvents()
        finally:
            self.lyrics_manager_scan_btn.setEnabled(True)
            self.lyrics_manager_bulk_lookup_btn.setEnabled(True)
            self.lyrics_manager_export_lrc_btn.setEnabled(True)

        self._lyrics_manager_scan_target = str(resolved_target)
        self._populate_lyrics_lookup_table()
        self.lyrics_manager_apply_lookup_btn.setEnabled(found_count > 0)

        self.lyrics_manager_progress.setRange(0, total_audio)
        self.lyrics_manager_progress.setValue(total_audio)
        self.lyrics_manager_progress.setFormat(f"Complete: {found_count} lyrics found")
        self.lyrics_manager_summary_label.setText(
            f"Bulk lookup complete | Audio scanned: {total_audio} | Found: {found_count} | Not found: {not_found_count} | Instrumental: {instrumental_count} | Errors: {error_count}"
        )
        self.statusBar().showMessage("Bulk lyrics lookup completed", 5000)

    def apply_bulk_lookup_results(self) -> None:
        ready_rows = [
            row
            for row in self._lyrics_lookup_results
            if str(row.get("status") or "") == "Found"
            and str(row.get("lyrics_text") or "").strip()
        ]

        if not self._lyrics_manager_scan_target:
            QMessageBox.information(
                self,
                "Run Scan First",
                "Run 'Scan Lyrics' first to inventory audio files before applying lookup results.",
            )
            return

        if not ready_rows:
            QMessageBox.information(
                self,
                "No Lookup Results",
                "Run Bulk Lookup first, then apply found lyrics.",
            )
            return

        confirm = QMessageBox.question(
            self,
            "Apply Lookup Results",
            (
                "Write .lrc files for all lookup results marked Found?\n\n"
                "Naming rule: song.ext -> song.lrc (same base name).\n"
                "Existing .lrc files with matching names will be overwritten.\n\n"
                f"Files ready to apply: {len(ready_rows)}"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return

        self.lyrics_manager_scan_btn.setEnabled(False)
        self.lyrics_manager_bulk_lookup_btn.setEnabled(False)
        self.lyrics_manager_export_lrc_btn.setEnabled(False)
        self.lyrics_manager_apply_lookup_btn.setEnabled(False)

        self.lyrics_manager_progress.setRange(0, len(ready_rows))
        self.lyrics_manager_progress.setValue(0)
        self.lyrics_manager_progress.setFormat("Applying lookup results...")

        applied_count = 0
        errors: list[tuple[str, str]] = []

        try:
            for index, row in enumerate(ready_rows, start=1):
                relative_path = str(row.get("relative_path") or "")
                file_path = Path(str(row.get("file_path") or ""))
                lyrics_text = str(row.get("lyrics_text") or "").strip()

                try:
                    lrc_path = file_path.with_suffix(".lrc")
                    output_text = lyrics_text if lyrics_text.endswith("\n") else lyrics_text + "\n"
                    lrc_path.write_text(output_text, encoding="utf-8")
                    row["apply_status"] = "Applied"
                    applied_count += 1
                except Exception as exc:
                    row["apply_status"] = "Error"
                    errors.append((relative_path or str(file_path), str(exc)))

                self.lyrics_manager_progress.setValue(index)
                self.lyrics_manager_progress.setFormat(f"Applying {index}/{len(ready_rows)}")
                if index % 10 == 0 or index == len(ready_rows):
                    self.lyrics_manager_summary_label.setText(
                        f"Applying lookup results: {index}/{len(ready_rows)} | Applied: {applied_count} | Errors: {len(errors)}"
                    )
                QApplication.processEvents()
        finally:
            self.lyrics_manager_scan_btn.setEnabled(True)
            self.lyrics_manager_bulk_lookup_btn.setEnabled(True)
            self.lyrics_manager_export_lrc_btn.setEnabled(True)
            self.lyrics_manager_apply_lookup_btn.setEnabled(applied_count > 0)

        self._populate_lyrics_lookup_table()
        self.lyrics_manager_progress.setRange(0, len(ready_rows))
        self.lyrics_manager_progress.setValue(len(ready_rows))
        self.lyrics_manager_progress.setFormat(f"Complete: {applied_count} .lrc files written")
        self.lyrics_manager_summary_label.setText(
            f"Lookup apply complete | Applied: {applied_count} | Errors: {len(errors)}"
        )

        lines = [
            f"Lookup results ready: {len(ready_rows)}",
            f".lrc files written: {applied_count}",
            f"Errors: {len(errors)}",
        ]
        if errors:
            sample = "\n".join(f"- {name}: {reason}" for name, reason in errors[:5])
            lines.append("\nSample errors:\n" + sample)

        if errors:
            QMessageBox.warning(self, "Apply Completed With Errors", "\n".join(lines))
        else:
            QMessageBox.information(self, "Apply Completed", "\n".join(lines))

        self.statusBar().showMessage(f"Lookup apply complete: {applied_count} files", 5000)

    def convert_embedded_lyrics_to_lrc(self) -> None:
        # Require a scan to have been run first.
        if not self._lyrics_manager_scan_target:
            QMessageBox.information(
                self,
                "Run Scan First",
                "Run 'Scan Lyrics' first to inventory audio files before converting embedded lyrics.",
            )
            return

        target = self.path_input.text().strip()
        if not target:
            QMessageBox.information(
                self,
                "No Target",
                "Choose a folder or drive before converting embedded lyrics to .lrc files.",
            )
            return

        target_path = Path(target).expanduser()
        if not target_path.exists() or not target_path.is_dir():
            QMessageBox.warning(
                self,
                "Invalid Target",
                "The selected target path is not a valid folder.",
            )
            return

        resolved_target = target_path.resolve()
        audio_files = self._collect_target_audio_files(resolved_target)
        total_audio = len(audio_files)
        if total_audio == 0:
            QMessageBox.information(self, "No Audio Files", "No audio files found in target.")
            return

        confirm = QMessageBox.question(
            self,
            "Convert Embedded Lyrics To .lrc",
            (
                "Create .lrc files next to songs using embedded lyrics?\n\n"
                "Naming rule: song.ext -> song.lrc (same base name).\n"
                "Existing .lrc files with matching names will be overwritten.\n\n"
                f"Audio files to scan: {total_audio}"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return

        self.lyrics_manager_scan_btn.setEnabled(False)
        self.lyrics_manager_export_lrc_btn.setEnabled(False)
        self.lyrics_manager_progress.setRange(0, total_audio)
        self.lyrics_manager_progress.setValue(0)
        self.lyrics_manager_progress.setFormat("Preparing conversion...")

        exported = 0
        skipped_no_lyrics = 0
        errors: list[tuple[str, str]] = []
        claimed_lrc_paths: dict[Path, Path] = {}

        for index, file_path in enumerate(audio_files, start=1):
            try:
                relative_path = file_path.relative_to(resolved_target).as_posix()
            except Exception:
                relative_path = str(file_path)

            self.lyrics_manager_progress.setValue(index)
            self.lyrics_manager_progress.setFormat(f"Converting {index}/{total_audio}")
            if index % 25 == 0 or index == total_audio:
                self.lyrics_manager_summary_label.setText(
                    f"Converting: {index}/{total_audio} | Exported: {exported} | No lyrics: {skipped_no_lyrics} | Errors: {len(errors)}"
                )

            QApplication.processEvents()

            if self.lyrics_manager_progress.value() < index:
                # Guard for unexpected progress resets from external UI state.
                self.lyrics_manager_progress.setValue(index)

            entries, error = self._embedded_lyrics_entries_for_file(file_path)
            if error:
                errors.append((relative_path, error))
                continue

            if not entries:
                skipped_no_lyrics += 1
                continue

            lrc_path = file_path.with_suffix(".lrc")
            existing_source = claimed_lrc_paths.get(lrc_path)
            if existing_source is not None and existing_source != file_path:
                errors.append(
                    (
                        relative_path,
                        f"LRC name collision with {existing_source.name} -> {lrc_path.name}",
                    )
                )
                continue
            claimed_lrc_paths[lrc_path] = file_path

            lrc_text = self._best_lrc_text_from_entries(entries)
            if not lrc_text:
                skipped_no_lyrics += 1
                continue

            try:
                lrc_path.write_text(lrc_text + "\n", encoding="utf-8")
                exported += 1
            except Exception as exc:
                errors.append((relative_path, str(exc)))

        self.lyrics_manager_scan_btn.setEnabled(True)
        self.lyrics_manager_export_lrc_btn.setEnabled(True)

        self.lyrics_manager_summary_label.setText(
            f"LRC conversion complete | Exported: {exported} | No lyrics: {skipped_no_lyrics} | Errors: {len(errors)}"
        )
        self.lyrics_manager_progress.setRange(0, total_audio)
        self.lyrics_manager_progress.setValue(total_audio)
        self.lyrics_manager_progress.setFormat(
            f"Complete: {exported} .lrc files created"
        )

        message_lines = [
            f"Audio files scanned: {total_audio}",
            f".lrc files created: {exported}",
            f"Skipped (no embedded lyrics): {skipped_no_lyrics}",
            f"Errors: {len(errors)}",
        ]
        if errors:
            preview = "\n".join(f"- {name}: {reason}" for name, reason in errors[:5])
            message_lines.append("\nSample errors:\n" + preview)

        if errors:
            QMessageBox.warning(self, "LRC Conversion Completed With Errors", "\n".join(message_lines))
        else:
            QMessageBox.information(self, "LRC Conversion Completed", "\n".join(message_lines))

        self.statusBar().showMessage(f"LRC conversion complete: {exported} files", 5000)

    def fix_incompatible_files(self) -> None:
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self.statusBar().showMessage("Wait for the current album art scan to complete")
            return

        if not self._last_scan_target:
            QMessageBox.information(
                self,
                "No Completed Scan",
                "Run Scan Album Art Compatibility first.",
            )
            return

        if not self._last_incompatible_files:
            QMessageBox.information(
                self,
                "Nothing To Fix",
                "No incompatible files were found in the last completed scan.",
            )
            return

        target_path = Path(self._last_scan_target)
        files_to_fix: list[Path] = []
        for file_name in self._last_incompatible_files:
            candidate = Path(file_name)
            if not candidate.is_absolute():
                candidate = target_path / file_name
            files_to_fix.append(candidate)

        total_files = len(files_to_fix)
        progress = QProgressDialog("Fixing incompatible files...", "Cancel", 0, total_files, self)
        progress.setWindowTitle("Fix Incompatible Files")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setValue(0)

        fixed_count = 0
        already_compatible_count = 0
        failed_files: list[tuple[str, str]] = []

        for index, file_path in enumerate(files_to_fix, start=1):
            if progress.wasCanceled():
                break

            progress.setLabelText(f"Fixing {index}/{total_files}: {file_path.name}")
            progress.setValue(index - 1)
            QApplication.processEvents()

            ok, detail = self._fix_album_art_core(file_path)
            if ok:
                if detail == "Already compatible":
                    already_compatible_count += 1
                else:
                    fixed_count += 1
            else:
                failed_files.append((str(file_path), detail))

            progress.setValue(index)
            QApplication.processEvents()

        cancelled = progress.wasCanceled()
        progress.close()

        failure_count = len(failed_files)
        message_lines = [
            f"Fixed: {fixed_count}",
            f"Already compatible: {already_compatible_count}",
            f"Failed: {failure_count}",
        ]
        if cancelled:
            message_lines.append("Cancelled before processing all files.")
        if failed_files:
            preview = "\n".join(f"- {Path(name).name}: {reason}" for name, reason in failed_files[:5])
            message_lines.append("\nSample failures:\n" + preview)

        if failure_count:
            QMessageBox.warning(self, "Batch Fix Completed With Errors", "\n".join(message_lines))
        else:
            QMessageBox.information(self, "Batch Fix Completed", "\n".join(message_lines))

        self.statusBar().showMessage(
            f"Batch fix complete: {fixed_count} fixed, {already_compatible_count} already compatible, {failure_count} failed"
        )

        # Refresh compatibility results after applying fixes.
        self.path_input.setText(self._last_scan_target)
        self.scan_album_art_compatibility()

    def _insert_file_properties_section_row(self, row_index: int, title: str, section_key: str) -> None:
        if section_key == "metadata":
            add_btn = QPushButton("＋")
            add_btn.setToolTip("Add metadata field")
            add_btn.setFixedSize(24, 24)
            add_btn.setStyleSheet(
                "QPushButton { font-size: 11px; padding: 0px; border: 1px solid #505050; }"
                "QPushButton:hover { background-color: #404040; }"
            )
            add_btn.setFocusPolicy(Qt.NoFocus)
            add_btn.clicked.connect(self._on_add_metadata_row_clicked)

            section_widget = QWidget(self.file_props_table)
            section_widget.setStyleSheet("background-color: #303030;")
            section_layout = QHBoxLayout(section_widget)
            section_layout.setContentsMargins(8, 0, 8, 0)
            section_layout.setSpacing(8)

            section_label = QLabel(title, section_widget)
            section_font = section_label.font()
            section_font.setBold(True)
            section_label.setFont(section_font)
            section_label.setProperty("sectionRow", True)
            section_label.setStyleSheet("color: #E8E8E8; background: transparent;")

            section_layout.addWidget(section_label, 1)
            section_layout.addWidget(add_btn, 0, Qt.AlignRight)
            # Add a hidden marker item so section lookups (by UserRole) still work
            section_item = QTableWidgetItem()
            section_item.setFlags(Qt.ItemIsEnabled)
            section_item.setData(Qt.UserRole, f"section:{section_key}")
            section_item.setBackground(QColor("#303030"))
            section_item.setForeground(QColor("#E8E8E8"))
            self.file_props_table.setItem(row_index, 0, section_item)
            self.file_props_table.setSpan(row_index, 0, 1, 2)
            self.file_props_table.setCellWidget(row_index, 0, section_widget)
        else:
            section_item = QTableWidgetItem(title)
            section_font = section_item.font()
            section_font.setBold(True)
            section_item.setFont(section_font)
            section_item.setFlags(Qt.ItemIsEnabled)
            section_item.setData(Qt.UserRole, f"section:{section_key}")
            section_item.setBackground(QColor("#303030"))
            section_item.setForeground(QColor("#E8E8E8"))
            self.file_props_table.setItem(row_index, 0, section_item)
            self.file_props_table.setSpan(row_index, 0, 1, 2)
        self.file_props_table.setRowHeight(row_index, 24)

    def _set_editable_metadata_cell(self, row_index: int, value_text: str, metadata_key: str) -> None:
        editor_container = QWidget(self.file_props_table)
        editor_layout = QHBoxLayout(editor_container)
        editor_layout.setContentsMargins(0, 0, 4, 0)
        editor_layout.setSpacing(4)

        # Text editor
        editor = QLineEdit(value_text, editor_container)
        editor.setToolTip("Editable metadata field. Press Enter to save.")
        editor.setProperty("originalValue", value_text)
        editor.editingFinished.connect(
            lambda key=metadata_key, input_widget=editor: self._on_metadata_editor_finished(key, input_widget)
        )

        # Status indicator (dirty state)
        status_label = QLabel("", editor_container)
        status_label.setObjectName("metaStatus")
        status_label.setFixedWidth(14)
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setStyleSheet("color: #FF6B6B; font-size: 10px;")

        def _mark_dirty(_text: str) -> None:
            try:
                status_label.setText("●")
                editor.setProperty("dirty", True)
            except Exception:
                pass

        editor.textChanged.connect(_mark_dirty)

        # Remove button
        remove_btn = QPushButton("×", editor_container)
        remove_btn.setFixedSize(24, 24)
        remove_btn.setToolTip("Remove this metadata field")
        remove_btn.setStyleSheet(
            "QPushButton { font-size: 14px; padding: 0px; border: 1px solid #505050; }"
            "QPushButton:hover { background-color: #404040; }"
        )
        remove_btn.setFocusPolicy(Qt.NoFocus)
        remove_btn.clicked.connect(
            lambda checked=False, key=metadata_key, input_widget=editor: self._on_metadata_remove_clicked(
                key, input_widget
            )
        )

        # Layout: editor (stretch), status, remove button
        editor_layout.addWidget(editor, 1)
        editor_layout.addWidget(status_label, 0)
        editor_layout.addWidget(remove_btn, 0)
        self.file_props_table.setCellWidget(row_index, 1, editor_container)

    def _populate_file_properties(
        self,
        rows: list[tuple[str, str] | tuple[str, str, str | None]],
    ) -> None:
        self._updating_file_props_table = True
        self.file_props_table.blockSignals(True)
        try:
            self.file_props_table.clearSpans()
            self.file_props_table.setRowCount(len(rows))
            for row_index, row_data in enumerate(rows):
                for column_index in (0, 1):
                    existing_widget = self.file_props_table.cellWidget(row_index, column_index)
                    if existing_widget is not None:
                        self.file_props_table.removeCellWidget(row_index, column_index)
                        existing_widget.deleteLater()

                if len(row_data) == 2 and row_data[0] == "__SECTION__":
                    section_title = row_data[1]
                    section_key = section_title.strip().lower().replace(" ", "_")
                    self._insert_file_properties_section_row(row_index, section_title, section_key)
                    continue

                if len(row_data) == 3:
                    prop, value, metadata_key = row_data
                else:
                    prop, value = row_data
                    metadata_key = None

                prop_item = QTableWidgetItem(prop)
                prop_item.setFlags(prop_item.flags() & ~Qt.ItemIsEditable)

                if metadata_key:
                    prop_item.setData(Qt.UserRole, f"metadata:{metadata_key}")
                    self._set_editable_metadata_cell(row_index, value, metadata_key)
                else:
                    value_item = QTableWidgetItem(value)
                    value_item.setFlags(value_item.flags() & ~Qt.ItemIsEditable)
                    self.file_props_table.setItem(row_index, 1, value_item)

                self.file_props_table.setItem(row_index, 0, prop_item)
        finally:
            self.file_props_table.blockSignals(False)
            self._updating_file_props_table = False

    def _metadata_input_to_values(self, text: str) -> list[str]:
        if not text.strip():
            return []

        values: list[str] = []
        for line in text.replace("\r", "\n").split("\n"):
            for segment in line.split(";"):
                normalized = segment.strip()
                if normalized:
                    values.append(normalized)
        return values

    def _save_audio_metadata_tag(self, file_path: Path, metadata_key: str, value_text: str) -> tuple[bool, str]:
        if mutagen is None:
            return False, "Mutagen is unavailable, so metadata cannot be saved."

        file_str = str(file_path)
        values = self._metadata_input_to_values(value_text)

        try:
            if file_path.suffix.lower() == ".mp3":
                try:
                    from mutagen.easyid3 import EasyID3
                    from mutagen.id3 import ID3, ID3NoHeaderError
                except ImportError as exc:
                    return False, f"Unable to import MP3 metadata helpers: {exc}"

                try:
                    tags = EasyID3(file_str)
                except ID3NoHeaderError:
                    ID3().save(file_str, v2_version=3)
                    tags = EasyID3(file_str)

                if values:
                    tags[metadata_key] = values
                else:
                    if metadata_key in tags:
                        del tags[metadata_key]

                tags.save(file_str, v2_version=3)
                return True, "Saved"

            try:
                audio = mutagen.File(file_str, easy=True)
            except Exception as exc:
                return False, f"Unable to open file metadata: {exc}"

            if not audio:
                return False, "Unable to parse audio metadata for this file."

            tags = getattr(audio, "tags", None)
            if tags is None:
                tags = audio

            if tags is None:
                return False, "Unable to initialize editable metadata tags."

            if values:
                tags[metadata_key] = values
            else:
                if metadata_key in tags:
                    del tags[metadata_key]

            save_kwargs = {"v2_version": 3} if file_path.suffix.lower() == ".mp3" else {}
            audio.save(**save_kwargs)
        except Exception as exc:
            return False, f"Unable to save metadata field '{metadata_key}': {exc}"

        return True, "Saved"

    def _remove_audio_metadata_tag(self, file_path: Path, metadata_key: str) -> tuple[bool, str]:
        return self._save_audio_metadata_tag(file_path, metadata_key, "")

    def _delete_tag_by_format(self, file_path: Path, metadata_key: str) -> bool:
        """Attempt format-specific deletion using mutagen full API.

        Returns True when deletion was performed and saved, False otherwise.
        """
        if mutagen is None:
            return False

        try:
            full_audio = mutagen.File(file_path)
        except Exception:
            return False

        if not full_audio:
            return False

        full_tags = getattr(full_audio, "tags", None)
        if not full_tags:
            return False

        lowered = metadata_key.lower()
        deleted_any = False

        try:
            # ID3 / MP3 handling
            cls_name = full_audio.__class__.__name__.lower()

            # Iterate keys and remove those matching or containing the metadata_key
            for key in list(full_tags.keys()):
                key_str = str(key)
                if key_str.lower() == lowered or lowered in key_str.lower():
                    try:
                        # Prefer format-specific delete APIs where available
                        if hasattr(full_tags, "delall"):
                            try:
                                full_tags.delall(key_str)
                                deleted_any = True
                                continue
                            except Exception:
                                pass

                        try:
                            del full_tags[key]
                            deleted_any = True
                        except Exception:
                            # Some containers require different methods; ignore here
                            pass
                    except Exception:
                        pass

            if deleted_any:
                try:
                    save_kwargs = {"v2_version": 3} if file_path.suffix.lower() == ".mp3" else {}
                    full_audio.save(**save_kwargs)
                    return True
                except Exception:
                    return False
        except Exception:
            return False

        return False

    def _find_file_props_section_row(self, section_key: str) -> int:
        marker = f"section:{section_key}"
        for row in range(self.file_props_table.rowCount()):
            item = self.file_props_table.item(row, 0)
            if item is None:
                continue
            if item.data(Qt.UserRole) == marker:
                return row
        return -1

    def _normalize_new_metadata_key(self, property_name: str) -> str:
        text = property_name.strip()
        if not text:
            return ""

        # First, allow matching existing metadata labels exactly.
        lowered = text.casefold()
        for row in range(self.file_props_table.rowCount()):
            item = self.file_props_table.item(row, 0)
            if item is None:
                continue
            marker = item.data(Qt.UserRole)
            if not isinstance(marker, str) or not marker.startswith("metadata:"):
                continue
            metadata_key = marker.split(":", 1)[1]
            if metadata_key.casefold() == lowered or item.text().casefold() == lowered:
                return metadata_key

        normalized = re.sub(r"[^a-z0-9_]", "", text.lower().replace(" ", "_"))
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        return normalized.strip("_")

    def _pending_new_metadata_row(self) -> int:
        for row in range(self.file_props_table.rowCount()):
            property_widget = self.file_props_table.cellWidget(row, 0)
            if not isinstance(property_widget, QLineEdit):
                continue

            value_widget = self.file_props_table.cellWidget(row, 1)
            if value_widget is None:
                continue
            save_buttons = value_widget.findChildren(QPushButton)
            if any(button.text() == "Save" for button in save_buttons):
                return row

        return -1

    def _add_new_metadata_entry_row(self) -> None:
        pending_row = self._pending_new_metadata_row()
        if pending_row >= 0:
            pending_widget = self.file_props_table.cellWidget(pending_row, 0)
            if isinstance(pending_widget, QLineEdit):
                pending_widget.setFocus()
            return

        metadata_row = self._find_file_props_section_row("metadata")
        if metadata_row < 0:
            return

        technical_row = self._find_file_props_section_row("technical_details")
        insert_row = technical_row if technical_row > metadata_row else self.file_props_table.rowCount()

        self.file_props_table.insertRow(insert_row)

        property_editor = QLineEdit(self.file_props_table)
        property_editor.setPlaceholderText("Property key (example: publisher)")
        property_editor.setMaxLength(64)

        value_editor = QLineEdit(self.file_props_table)
        value_editor.setPlaceholderText("Value")

        save_btn = QPushButton("Save", self.file_props_table)
        save_btn.setFixedWidth(52)

        value_cell = QWidget(self.file_props_table)
        value_layout = QHBoxLayout(value_cell)
        value_layout.setContentsMargins(4, 0, 4, 0)
        value_layout.setSpacing(6)
        value_layout.addWidget(value_editor, 1)
        value_layout.addWidget(save_btn, 0)

        self.file_props_table.setCellWidget(insert_row, 0, property_editor)
        self.file_props_table.setCellWidget(insert_row, 1, value_cell)

        def save_new_entry() -> None:
            file_path = self._active_audio_metadata_path
            if file_path is None or not file_path.exists() or not file_path.is_file():
                QMessageBox.warning(self, "File Not Available", "The selected audio file is no longer available.")
                return

            metadata_key = self._normalize_new_metadata_key(property_editor.text())
            if not metadata_key:
                QMessageBox.warning(self, "Invalid Property", "Enter a metadata property name.")
                property_editor.setFocus()
                return

            ok, message = self._save_audio_metadata_tag(file_path, metadata_key, value_editor.text())
            if not ok:
                lower_message = message.lower()
                if "valid key" in lower_message or "unknown key" in lower_message:
                    QMessageBox.warning(
                        self,
                        "Unsupported Property",
                        (
                            "That property key is not supported for this audio format.\n"
                            "Try common keys such as title, artist, album, tracknumber, genre, or date.\n\n"
                            f"Details: {message}"
                        ),
                    )
                else:
                    QMessageBox.warning(self, "Metadata Save Failed", message)
                return

            current_index = self.browser_tree.currentIndex()
            if current_index.isValid():
                self.on_browser_item_clicked(current_index)

            self.statusBar().showMessage(f"Saved metadata: {metadata_key}", 3000)

        save_btn.clicked.connect(save_new_entry)
        property_editor.returnPressed.connect(save_new_entry)
        value_editor.returnPressed.connect(save_new_entry)
        property_editor.setFocus()

    def _on_add_metadata_row_clicked(self) -> None:
        if self._updating_file_props_table:
            return
        self._add_new_metadata_entry_row()

    def _on_metadata_editor_finished(self, metadata_key: str, input_widget: QLineEdit) -> None:
        if self._updating_file_props_table:
            return

        file_path = self._active_audio_metadata_path
        if file_path is None:
            return
        if not file_path.exists() or not file_path.is_file():
            QMessageBox.warning(self, "File Not Available", "The selected audio file is no longer available.")
            return

        current_value = input_widget.text()
        original_value = str(input_widget.property("originalValue") or "")
        if current_value == original_value:
            return

        ok, message = self._save_audio_metadata_tag(file_path, metadata_key, current_value)
        if not ok:
            QMessageBox.warning(self, "Metadata Save Failed", message)

        current_index = self.browser_tree.currentIndex()
        if current_index.isValid():
            self.on_browser_item_clicked(current_index)

        if ok:
            self.statusBar().showMessage(f"Saved metadata: {metadata_key}", 3000)

            # Mark the editor as saved (clear dirty, show check briefly, update originalValue and tooltip)
            try:
                container = input_widget.parent()
                status = None
                if container is not None:
                    status = container.findChild(QLabel, "metaStatus")
                if status is not None:
                    status.setText("✓")
                    status.setStyleSheet("color: #4CAF50; font-size: 12px;")
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    status.setToolTip(f"Saved: {timestamp}")
                    input_widget.setProperty("dirty", False)
                    input_widget.setProperty("originalValue", current_value)
                    QTimer.singleShot(1400, lambda: status.setText(""))
            except Exception:
                pass

    def _on_metadata_remove_clicked(self, metadata_key: str, input_widget: QLineEdit) -> None:
        if self._updating_file_props_table:
            return

        file_path = self._active_audio_metadata_path
        if file_path is None:
            return
        if not file_path.exists() or not file_path.is_file():
            QMessageBox.warning(self, "File Not Available", "The selected audio file is no longer available.")
            return

        confirm = QMessageBox.question(
            self,
            "Remove Metadata Field",
            f"Remove '{metadata_key}' from this file?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        ok, message = self._remove_audio_metadata_tag(file_path, metadata_key)
        if not ok:
            QMessageBox.warning(self, "Metadata Remove Failed", message)
            return

        current_index = self.browser_tree.currentIndex()
        if current_index.isValid():
            self.on_browser_item_clicked(current_index)

        self.statusBar().showMessage(f"Removed metadata: {metadata_key}", 3000)

    def _set_embedded_lyrics_text(self, hint: str, lyrics_text: str) -> None:
        self.file_lyrics_hint.setText(hint)
        self.file_lyrics_text.setPlainText(lyrics_text)

    def _show_audio_details_panel(self) -> None:
        self.file_details_tabs.show()
        self.lrc_preview_panel.hide()

    def _show_lrc_preview_panel(self) -> None:
        self.file_details_tabs.hide()
        self.lrc_preview_panel.show()

    def _decode_text_file_bytes(self, data: bytes) -> str:
        for encoding in ["utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1"]:
            try:
                return data.decode(encoding)
            except Exception:
                continue
        return data.decode("utf-8", errors="replace")

    def _show_lrc_file_contents(self, file_path: Path) -> None:
        self._show_lrc_preview_panel()
        self.lrc_preview_hint.setText(str(file_path))

        try:
            raw = file_path.read_bytes()
            content = self._decode_text_file_bytes(raw)
        except Exception as exc:
            self.lrc_preview_text.setPlainText("")
            self.lrc_preview_hint.setText(f"Failed to read LRC file: {exc}")
            return

        self.lrc_preview_text.setPlainText(content)

    def _lyrics_value_to_text(self, value) -> str:
        if value is None:
            return ""

        if isinstance(value, str):
            return value.strip()

        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", "ignore").strip()
            except Exception:
                return ""

        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], (str, bytes)):
            return self._lyrics_value_to_text(value[0])

        if hasattr(value, "text"):
            try:
                return self._lyrics_value_to_text(getattr(value, "text"))
            except Exception:
                pass

        if isinstance(value, (list, tuple, set)):
            parts = [self._lyrics_value_to_text(item) for item in value]
            parts = [part for part in parts if part]
            return "\n".join(parts).strip()

        return str(value).strip()

    def _embedded_lyrics_entries_for_file(self, file_path: Path) -> tuple[list[tuple[str, str]], str | None]:
        if mutagen is None:
            return [], "Embedded lyrics cannot be read without mutagen."

        try:
            audio = mutagen.File(file_path)
        except Exception as exc:
            return [], str(exc)

        tags = getattr(audio, "tags", None) if audio else None
        if not tags:
            return [], None

        entries: list[tuple[str, str]] = []
        seen_entries: set[tuple[str, str]] = set()

        def add_entry(source: str, text: str) -> None:
            normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
            if not normalized:
                return
            key = (source.lower(), normalized)
            if key in seen_entries:
                return
            seen_entries.add(key)
            entries.append((source, normalized))

        getall = getattr(tags, "getall", None)
        if callable(getall):
            try:
                uslt_frames = list(getall("USLT"))
            except Exception:
                uslt_frames = []

            for index, frame in enumerate(uslt_frames, start=1):
                text = self._lyrics_value_to_text(getattr(frame, "text", ""))
                lang = str(getattr(frame, "lang", "") or "").strip().upper()
                desc = str(getattr(frame, "desc", "") or "").strip()
                detail_parts = [part for part in [lang, desc] if part]
                label = "ID3 USLT"
                if detail_parts:
                    label += f" ({', '.join(detail_parts)})"
                elif len(uslt_frames) > 1:
                    label += f" #{index}"
                add_entry(label, text)

            try:
                sylt_frames = list(getall("SYLT"))
            except Exception:
                sylt_frames = []

            for index, frame in enumerate(sylt_frames, start=1):
                text = self._lyrics_value_to_text(getattr(frame, "text", ""))
                label = "ID3 SYLT"
                if len(sylt_frames) > 1:
                    label += f" #{index}"
                add_entry(label, text)

        candidate_keys = ["lyrics", "LYRICS", "unsyncedlyrics", "UNSYNCEDLYRICS", "\xa9lyr", "©lyr"]
        for key in candidate_keys:
            try:
                raw_value = tags.getall(key) if callable(getall) else tags.get(key)
            except Exception:
                raw_value = None
            text = self._lyrics_value_to_text(raw_value)
            if text:
                add_entry(f"Tag {key}", text)

        try:
            tag_keys = list(tags.keys())
        except Exception:
            tag_keys = []

        for key in tag_keys:
            key_text = str(key)
            normalized_key = key_text.lower()
            if "lyric" not in normalized_key or "lyricist" in normalized_key:
                continue

            try:
                raw_value = tags.getall(key) if callable(getall) else tags.get(key)
            except Exception:
                raw_value = None

            text = self._lyrics_value_to_text(raw_value)
            if text:
                add_entry(f"Tag {key_text}", text)

        return entries, None

    def _embedded_lyrics_for_file(self, file_path: Path) -> tuple[str, str]:
        entries, error = self._embedded_lyrics_entries_for_file(file_path)
        if error:
            return "Unable to read embedded lyrics.", error

        if not entries:
            return "No embedded lyrics found.", ""

        rendered_entries = [f"[{source}]\n{text}" for source, text in entries]
        return f"Embedded lyrics entries: {len(entries)}", "\n\n".join(rendered_entries)

    def _set_album_art_preview(self, art_bytes: bytes | None, art_mime: str) -> None:
        # Only add preview rows when metadata rows already exist for a selected audio file.
        if self.file_props_table.rowCount() == 0:
            return

        preview_row = self.file_props_table.rowCount()
        self.file_props_table.insertRow(preview_row)
        self.file_props_table.setItem(preview_row, 0, QTableWidgetItem("Album Art Preview"))

        pixmap = QPixmap()
        loaded = bool(art_bytes) and pixmap.loadFromData(art_bytes)
        if not loaded or pixmap.isNull():
            fallback = "No album art" if not art_bytes else f"Album art unavailable ({art_mime})"
            self.file_props_table.setItem(preview_row, 1, QTableWidgetItem(fallback))
            return

        preview_widget = QLabel()
        preview_widget.setAlignment(Qt.AlignCenter)
        preview_widget.setObjectName("fileArtPreview")

        # Keep preview compact while allowing the metadata table to fill the full pane height.
        max_preview_side = 220
        scaled = pixmap.scaled(
            max_preview_side,
            max_preview_side,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        preview_widget.setPixmap(scaled)

        self.file_props_table.setCellWidget(preview_row, 1, preview_widget)
        self.file_props_table.setRowHeight(preview_row, scaled.height() + 12)

    def _set_album_art_tab(self, art_bytes: bytes | None, art_mime: str) -> None:
        # Populate per-file Album Art tab preview and metadata table
        try:
            preview_widget = self.file_art_meta_preview
            table = self.file_art_meta_table
        except Exception:
            return

        if not art_bytes:
            try:
                preview_widget.clear()
                table.setRowCount(0)
            except Exception:
                pass
            return

        pixmap = QPixmap()
        loaded = bool(art_bytes) and pixmap.loadFromData(art_bytes)
        if not loaded or pixmap.isNull():
            preview_widget.setText(f"Album art unavailable ({art_mime})")
            try:
                table.setRowCount(2)
                table.setItem(0, 0, QTableWidgetItem("MIME"))
                table.setItem(0, 1, QTableWidgetItem(art_mime))
                table.setItem(1, 0, QTableWidgetItem("Data Size"))
                table.setItem(1, 1, QTableWidgetItem(format_bytes(len(art_bytes))))
            except Exception:
                pass
            return

        max_preview_side = 320
        scaled = pixmap.scaled(
            max_preview_side,
            max_preview_side,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        preview_widget.setPixmap(scaled)
        dims = image_size_from_bytes(art_bytes)
        dimensions = f"{dims[0]} x {dims[1]}" if dims else "Unknown"
        jpeg_type = jpeg_scan_type(art_bytes)
        table_rows = [
            ("MIME", art_mime),
            ("Data Size", format_bytes(len(art_bytes))),
            ("Dimensions", dimensions),
        ]
        if jpeg_type and jpeg_type != "Not JPEG":
            table_rows.append(("JPEG Scan", jpeg_type))

        table.setRowCount(len(table_rows))
        for row_index, (prop, value) in enumerate(table_rows):
            table.setItem(row_index, 0, QTableWidgetItem(prop))
            table.setItem(row_index, 1, QTableWidgetItem(value))

    def _format_duration(self, seconds: float) -> str:
        if seconds <= 0:
            return "0:00"
        total_seconds = int(round(seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _normalize_metadata_value(self, value, depth: int = 0) -> str:
        if value is None:
            return ""

        if isinstance(value, bool):
            return "Yes" if value else "No"

        if isinstance(value, (int, float)):
            if isinstance(value, float):
                return f"{value:.6g}"
            return str(value)

        if isinstance(value, bytes):
            return f"{len(value)} bytes"

        if hasattr(value, "mime") and hasattr(value, "data"):
            mime = str(getattr(value, "mime", "image/unknown") or "image/unknown")
            data = getattr(value, "data", b"")
            size_text = format_bytes(len(data)) if isinstance(data, (bytes, bytearray)) else "Unknown size"
            desc = str(getattr(value, "desc", "") or "").strip()
            summary = f"{mime} | {size_text}"
            if desc:
                summary += f" | Description: {desc}"
            return summary

        if hasattr(value, "text"):
            text_value = getattr(value, "text", None)
            if text_value is not None:
                return self._normalize_metadata_value(text_value, depth + 1)

        if hasattr(value, "url"):
            url_value = str(getattr(value, "url", "")).strip()
            if url_value:
                return url_value

        if isinstance(value, dict):
            if depth >= 2:
                return str(value)
            parts: list[str] = []
            for key in sorted(value.keys(), key=lambda item: str(item).lower()):
                normalized = self._normalize_metadata_value(value[key], depth + 1)
                if not normalized:
                    continue
                parts.append(f"{key}={normalized}")
            return "; ".join(parts)

        if isinstance(value, (list, tuple, set)):
            if depth >= 2:
                return ", ".join(str(item) for item in value)
            parts = [self._normalize_metadata_value(item, depth + 1) for item in value]
            parts = [item for item in parts if item]
            return "; ".join(parts)

        text = str(value).strip()
        if len(text) > 300:
            return text[:297] + "..."
        return text

    def _metadata_label_for_key(self, key: str) -> str:
        normalized = key.replace("_", " ").strip()
        if not normalized:
            return key
        return normalized.title()

    def _extract_mutagen_metadata_rows(
        self,
        file_path: Path,
        ffprobe_info: dict[str, int | None | str] | None = None,
    ) -> list[tuple[str, str]]:
        """Extract technical audio metadata using ffprobe."""
        if ffprobe_info is None:
            ffprobe_info = _ffprobe_audio_info(file_path)

        if not ffprobe_info or (isinstance(ffprobe_info, dict) and ffprobe_info.get("error")):
            err = ffprobe_info.get("error") if isinstance(ffprobe_info, dict) else None
            rows: list[tuple[str, str]] = [("Metadata Engine", "FFprobe unavailable")]
            if err:
                try:
                    rows.append(("FFprobe Error", str(err)))
                except Exception:
                    rows.append(("FFprobe Error", "(unavailable)"))
            return rows

        rows: list[tuple[str, str]] = []
        rows.append(("Metadata Engine", "FFprobe"))
        
        format_name = ffprobe_info.get("format_long_name")
        if format_name:
            rows.append(("Container Type", format_name))
        
        # Duration
        duration = ffprobe_info.get("duration")
        if duration is not None:
            try:
                rows.append(("Duration", f"{self._format_duration(duration)} ({duration:.3f} s)"))
            except Exception:
                rows.append(("Duration", str(duration)))
        
        # Bitrate
        bitrate = ffprobe_info.get("bitrate")
        if bitrate is not None:
            try:
                kbps = bitrate / 1000.0
                rows.append(("Bitrate", f"{kbps:.1f} kbps ({bitrate} bps)"))
            except Exception:
                rows.append(("Bitrate", str(bitrate)))
        
        # Sample Rate
        sample_rate = ffprobe_info.get("sample_rate")
        if sample_rate is not None:
            rows.append(("Sample Rate", f"{int(sample_rate)} Hz"))
        
        # Channels
        channels = ffprobe_info.get("channels")
        if channels is not None:
            rows.append(("Channels", str(channels)))
        
        # Bit Depth
        bit_depth = ffprobe_info.get("bit_depth")
        if bit_depth is not None:
            rows.append(("Bits Per Sample", f"{bit_depth}-bit"))
        
        # Codec
        codec_name = ffprobe_info.get("codec_name")
        if codec_name:
            rows.append(("Codec", codec_name))
        
        codec_long_name = ffprobe_info.get("codec_long_name")
        if codec_long_name:
            rows.append(("Codec Description", codec_long_name))
        
        # Tags
        tags = ffprobe_info.get("tags", {})
        if not tags:
            rows.append(("Tags", "None found"))
            return rows
        
        tag_rows_added = 0
        for tag_key in sorted(tags.keys(), key=lambda item: str(item).lower()):
            tag_value = tags[tag_key]
            normalized = self._normalize_metadata_value(tag_value)
            if not normalized:
                continue
            
            label = f"Tag {tag_key}"
            rows.append((label, normalized))
            tag_rows_added += 1
        
        if tag_rows_added == 0:
            rows.append(("Tags", "None found"))
        
        return rows

    def _extract_editable_mutagen_rows(self, file_path: Path) -> list[tuple[str, str, str | None]]:
        """Extract editable tags from audio file using Mutagen.
        
        Use Mutagen directly instead of ffprobe for extracting editable tags,
        since Mutagen knows what keys are valid for each format.
        This prevents trying to edit non-editable file properties.
        """
        if mutagen is None:
            return []

        try:
            # Try with easy=True first (format-agnostic tag access)
            audio = mutagen.File(file_path, easy=True)
        except Exception:
            audio = None

        if not audio:
            try:
                # Fallback: try without easy=True
                audio = mutagen.File(file_path)
            except Exception:
                return []

        tags = getattr(audio, "tags", None)
        if not tags:
            return []

        rows: list[tuple[str, str, str | None]] = []
        preferred_keys = [
            "title",
            "artist",
            "album",
            "albumartist",
            "album_artist",
            "tracknumber",
            "track",
            "discnumber",
            "disc",
            "date",
            "genre",
            "composer",
            "comment",
            "lyrics",
        ]

        # Get all keys from tags
        existing_keys: list[str] = []
        try:
            existing_keys = [str(item) for item in tags.keys()]
        except Exception:
            existing_keys = []

        # Build a case-insensitive map
        existing_set = {key.lower(): key for key in existing_keys}
        
        # Collect keys in preferred order
        keys: list[str] = []
        for preferred_key in preferred_keys:
            if preferred_key in existing_set:
                keys.append(existing_set[preferred_key])
        
        # Add any remaining keys not in preferred list
        seen_lower = {key.lower() for key in keys}
        for key in sorted(existing_keys, key=lambda item: item.lower()):
            if key.lower() not in seen_lower:
                keys.append(key)
                seen_lower.add(key.lower())

        # Extract values for each key
        for key in keys:
            values: list[str] = []
            
            # Try to get values (format-dependent)
            raw_value = tags.get(key, [])
            
            if isinstance(raw_value, (list, tuple)):
                values = [str(item).strip() for item in raw_value if str(item).strip()]
            elif raw_value:
                values = [str(raw_value).strip()]

            if not values:
                continue

            value_text = "; ".join(values)
            rows.append((self._metadata_label_for_key(str(key)), value_text, str(key)))

        return rows

    def _audio_file_properties(self, path: str) -> tuple[list[tuple[str, str] | tuple[str, str, str | None]], bytes | None, str]:
        file_path = Path(path)
        stat_info = file_path.stat()
        modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat_info.st_mtime))

        ffprobe_info = _ffprobe_audio_info(file_path)

        art_bytes, art_mime = self._cached_embedded_album_art(file_path, stat_info)
        if art_bytes:
            art_size = format_bytes(len(art_bytes))
            dims = image_size_from_bytes(art_bytes)
            art_dimensions = f"{dims[0]} x {dims[1]}" if dims else "Unknown"
            jpeg_type = jpeg_scan_type(art_bytes)
            album_art_rows = [
                ("Embedded Album Art", "Yes"),
                ("Album Art MIME", art_mime),
                ("Album Art Data Size", art_size),
                ("Album Art Dimensions", art_dimensions),
            ]
            if jpeg_type != "Not JPEG":
                album_art_rows.append(("Album Art JPEG Scan", jpeg_type))
        else:
            album_art_rows = [
                ("Embedded Album Art", "No"),
                ("Album Art Details", art_mime),
            ]

        editable_rows = self._extract_editable_mutagen_rows(file_path)

        rows: list[tuple[str, str] | tuple[str, str, str | None]] = [
            ("__SECTION__", "File"),
            ("Name", file_path.name),
            ("Path", str(file_path)),
            ("Extension", file_path.suffix.lower() or "(none)"),
            ("Size", format_bytes(stat_info.st_size)),
            ("Last Modified", modified),
            ("Readable", "Yes" if os.access(file_path, os.R_OK) else "No"),
            ("Writable", "Yes" if os.access(file_path, os.W_OK) else "No"),
        ]
        
        # Add codec information if available
        try:
            codec = ffprobe_info.get("codec_name") if ffprobe_info else None
            if codec:
                rows.append(("Audio Codec", str(codec)))
        except Exception:
            pass

        rows.append(("__SECTION__", "Metadata"))
        if editable_rows:
            rows.extend(editable_rows)
        else:
            rows.append(("Tags", "No metadata tags available"))

        rows.append(("__SECTION__", "Technical Details"))
        rows.extend(self._extract_mutagen_metadata_rows(file_path, ffprobe_info))

        # Album art metadata is shown in its own tab; do not add to properties table.
        return rows, art_bytes, art_mime

    def on_browser_item_clicked(self, index) -> None:
        source_index = self._browser_source_index(index)
        path = self.browser_model.filePath(source_index)
        kind = "Folder" if self.browser_model.isDir(source_index) else "File"
        self.statusBar().showMessage(f"{kind}: {path}")

        self._show_audio_details_panel()

        if self.browser_model.isDir(source_index):
            self._active_audio_metadata_path = None
            self._populate_file_properties([])
            self._set_album_art_tab(None, "")
            self._set_album_art_tab(None, "")
            self._set_embedded_lyrics_text("Select an audio file to view embedded lyrics.", "")
            return

        suffix = Path(path).suffix.lower()
        if suffix == ".lrc":
            self._active_audio_metadata_path = None
            self._show_lrc_file_contents(Path(path))
            return

        if suffix not in AUDIO_FILE_EXTENSIONS:
            self._active_audio_metadata_path = None
            self._populate_file_properties([])
            self._set_album_art_tab(None, "")
            self._set_embedded_lyrics_text("Selected file is not an audio file.", "")
            return

        try:
            props, art_bytes, art_mime = self._audio_file_properties(path)
        except Exception as exc:
            self._active_audio_metadata_path = None
            self._populate_file_properties([])
            self._set_album_art_tab(None, "")
            self._set_embedded_lyrics_text("Unable to read embedded lyrics.", str(exc))
            return

        self._active_audio_metadata_path = Path(path)
        self._populate_file_properties(props)
        # Populate album art tab only (do not add album art metadata to properties table)
        self._set_album_art_tab(art_bytes, art_mime)

        lyrics_hint, lyrics_text = self._embedded_lyrics_for_file(Path(path))
        self._set_embedded_lyrics_text(lyrics_hint, lyrics_text)

    def pick_drive(self, _index: int = -1) -> None:
        if not self.drive_options:
            self._update_directory_tab_access()
            self._update_unmount_drive_button_state()
            return

        selected_path = self.drive_combo.currentData()
        if not isinstance(selected_path, str) or not selected_path:
            self._update_directory_tab_access()
            self._update_unmount_drive_button_state()
            return

        self.path_input.setText(selected_path)
        self._set_current_target_label(selected_path)
        self._update_directory_tab_access()
        self._update_unmount_drive_button_state()
        self.show_info()
        self.statusBar().showMessage(f"Drive selected: {selected_path}")

    def show_info(self) -> None:
        target = self.path_input.text().strip()
        self._set_current_target_label(target)
        self.refresh_directory_browser(target)
        self._update_directory_tab_access()

        resolved_target: str | None = None
        if target:
            target_path = Path(target).expanduser()
            if target_path.exists() and target_path.is_dir():
                resolved_target = str(target_path.resolve())

        self._update_music_compatibility_convert_button_state()

        if self._cleanup_scan_target and resolved_target != self._cleanup_scan_target:
            if resolved_target:
                self._set_cleanup_idle("Target changed. Run Scan File Types for updated cleanup data.")
            else:
                self._set_cleanup_idle("Choose a valid target before scanning file types.")

        if self._file_rename_scan_target and resolved_target != self._file_rename_scan_target:
            if resolved_target:
                self._set_file_rename_idle("Target changed. Run Scan Rename Suggestions for updated rename data.")
            else:
                self._set_file_rename_idle("Choose a valid target before scanning rename suggestions.")

        if self._lyrics_manager_scan_target and resolved_target != self._lyrics_manager_scan_target:
            if resolved_target:
                self._set_lyrics_manager_idle("Target changed. Run Scan Embedded Lyrics for updated results.")
            else:
                self._set_lyrics_manager_idle("Choose a valid target before scanning embedded lyrics.")

        if not target:
            self.info_table.setRowCount(0)
            self.statusBar().showMessage("Choose a folder or removable drive")
            return

        if not os.path.exists(target):
            self.info_table.setRowCount(0)
            self.statusBar().showMessage("Path does not exist")
            return

        try:
            info = collect_target_info(target)
        except Exception as exc:
            import logging

            logger = logging.getLogger(__name__)
            logger.exception("Failed to inspect target: %s", target)
            QMessageBox.critical(self, "Error", f"Failed to inspect target:\n{exc}")
            self.statusBar().showMessage("Failed to inspect target")
            return

        size_warning_threshold = 256 * 1024 * 1024 * 1024
        total_capacity_bytes = None
        try:
            fs_stats = os.statvfs(target)
            total_capacity_bytes = fs_stats.f_frsize * fs_stats.f_blocks
        except Exception:
            total_capacity_bytes = None

        size_warning = bool(
            total_capacity_bytes is not None and total_capacity_bytes > size_warning_threshold
        )

        track_count = None
        if resolved_target and Path(resolved_target).is_dir():
            try:
                track_count = sum(1 for _ in iter_audio_files(Path(resolved_target)))
            except Exception:
                track_count = None

        if track_count is not None:
            info = list(info)
            info.append(("Track Count", f"{track_count}/8192"))

        self.info_table.setRowCount(len(info))
        for row, (prop, value) in enumerate(info):
            prop_item = QTableWidgetItem(prop)
            value_item = QTableWidgetItem(str(value))

            self.info_table.setItem(row, 0, prop_item)
            self.info_table.setItem(row, 1, value_item)

            if prop_item:
                prop_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            if prop.lower() == "filesystem" and not self._filesystem_looks_compatible(str(value)):
                value_item.setText(
                    f"{value} (Likely Compatibility Issue - It's recommended to use FAT/exFAT)"
                )
                prop_item.setBackground(QColor("#7A3B00"))
                prop_item.setForeground(QColor("#FFF2CC"))
                value_item.setBackground(QColor("#7A3B00"))
                value_item.setForeground(QColor("#FFF2CC"))
                warning_tip = "Backup your data, and reformat this drive as either FAT or exFAT"
                prop_item.setToolTip(warning_tip)
                value_item.setToolTip(warning_tip)
            elif prop.lower() == "track count":
                try:
                    track_total = int(str(value).split("/", 1)[0])
                except Exception:
                    track_total = None

                if track_total is not None and track_total > 8192:
                    prop_item.setBackground(QColor("#7A5E2C"))
                    prop_item.setForeground(QColor("#FFF9E6"))
                    value_item.setBackground(QColor("#7A5E2C"))
                    value_item.setForeground(QColor("#FFF9E6"))
                    warning_tip = "Maximum supported track count is 8192"
                    prop_item.setToolTip(warning_tip)
                    value_item.setToolTip(warning_tip)

            if prop.lower() == "total space" and size_warning:
                value_item.setText(f"{value} (WARNING: Drive size over 256GB)")
                prop_item.setBackground(QColor("#7A3B00"))
                prop_item.setForeground(QColor("#FFF2CC"))
                value_item.setBackground(QColor("#7A3B00"))
                value_item.setForeground(QColor("#FFF2CC"))
                warning_tip = "Large drive detected: capacities over 256GB may cause compatibility issues."
                prop_item.setToolTip(warning_tip)
                value_item.setToolTip(warning_tip)

        self.statusBar().showMessage("Target information updated")


class StreamsInfoDialog(QDialog):
    """Dialog to display detailed information about all streams in a media file."""

    def __init__(self, file_path: Path, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.setWindowTitle(f"Stream Information - {file_path.name}")
        self.setModal(True)
        self.setMinimumWidth(800)
        self.setMinimumHeight(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Create tree widget for streams
        self.streams_tree = QTreeWidget()
        self.streams_tree.setHeaderLabels(["Property", "Value"])
        self.streams_tree.setColumnCount(2)
        self.streams_tree.setColumnWidth(0, 250)
        self.streams_tree.setColumnWidth(1, 500)
        self.streams_tree.setAlternatingRowColors(True)

        # Load streams data
        self._populate_streams()

        layout.addWidget(self.streams_tree)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

    def _populate_streams(self):
        """Populate the tree with stream information."""
        streams = get_all_streams(self.file_path)

        if streams is None:
            root = QTreeWidgetItem(self.streams_tree)
            root.setText(0, "Error")
            root.setText(1, "Could not probe file streams")
            return

        if not streams:
            root = QTreeWidgetItem(self.streams_tree)
            root.setText(0, "No streams found")
            return

        for stream_idx, stream in enumerate(streams, 1):
            # Create a root item for each stream
            stream_item = QTreeWidgetItem(self.streams_tree)
            codec_type = stream.get("codec_type", "unknown").upper()
            stream_index = stream.get("index", "?")

            # Format stream title based on type
            if codec_type == "AUDIO":
                channels = stream.get("channels", "?")
                sample_rate = stream.get("sample_rate", "?")
                title = f"Stream {stream_index}: {codec_type} ({channels}ch @ {sample_rate}Hz)"
            elif codec_type == "VIDEO":
                width = stream.get("width", "?")
                height = stream.get("height", "?")
                title = f"Stream {stream_index}: {codec_type} ({width}x{height})"
            else:
                title = f"Stream {stream_index}: {codec_type}"

            stream_item.setText(0, title)

            # Add stream properties
            self._add_stream_property(stream_item, "Index", stream.get("index"))
            self._add_stream_property(stream_item, "Type", codec_type)

            codec_name = stream.get("codec_name")
            if codec_name:
                self._add_stream_property(stream_item, "Codec", codec_name)

            codec_long = stream.get("codec_long_name")
            if codec_long:
                self._add_stream_property(stream_item, "Description", codec_long)

            # Audio-specific properties
            if codec_type == "AUDIO":
                channels = stream.get("channels")
                if channels is not None:
                    self._add_stream_property(stream_item, "Channels", str(channels))

                sample_rate = stream.get("sample_rate")
                if sample_rate is not None:
                    self._add_stream_property(
                        stream_item, "Sample Rate", f"{sample_rate} Hz"
                    )

                bit_depth = stream.get("bit_depth")
                if bit_depth is not None:
                    self._add_stream_property(stream_item, "Bit Depth", f"{bit_depth} bits")

            # Video-specific properties
            if codec_type == "VIDEO":
                width = stream.get("width")
                height = stream.get("height")
                if width is not None and height is not None:
                    self._add_stream_property(stream_item, "Resolution", f"{width}x{height}")

            # General properties
            bitrate = stream.get("bitrate")
            if bitrate is not None:
                bitrate_mbps = bitrate / 1_000_000
                self._add_stream_property(stream_item, "Bitrate", f"{bitrate_mbps:.2f} Mbps")

            duration = stream.get("duration")
            if duration is not None:
                mins = int(duration) // 60
                secs = int(duration) % 60
                self._add_stream_property(stream_item, "Duration", f"{mins}:{secs:02d}")

            # Tags
            tags = stream.get("tags", {})
            if tags:
                tags_item = QTreeWidgetItem(stream_item)
                tags_item.setText(0, "Tags")
                for tag_key, tag_value in tags.items():
                    self._add_stream_property(tags_item, tag_key, str(tag_value))

            stream_item.setExpanded(True)

    def _add_stream_property(self, parent: QTreeWidgetItem, key: str, value):
        """Add a property to a stream tree item."""
        if value is None:
            return
        item = QTreeWidgetItem(parent)
        item.setText(0, key)
        item.setText(1, str(value))
