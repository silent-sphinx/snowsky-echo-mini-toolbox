import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import certifi
from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    import mutagen
except ImportError:
    mutagen = None

logger = logging.getLogger(__name__)

USER_AGENT = "SnowskyEchoMiniToolbox/1.0 ( https://github.com/snowsky-echo-mini-toolbox )"


class AlbumArtDownloadFetchWorker(QObject):
    progress = Signal(int, int, str)  # current, total, message
    # list of dicts: {"path": Path, "artist": str, "album": str, "mbid": str, "image_data": bytes, "error": str}
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, selected_files: list[tuple[Path, str]]):
        super().__init__()
        self.selected_files = selected_files

    @Slot()
    def run(self) -> None:
        try:
            results = []
            total = len(self.selected_files)
            ssl_context = ssl.create_default_context(cafile=certifi.where())

            for i, (file_path, search_term) in enumerate(self.selected_files, start=1):
                self.progress.emit(i, total, f"Reading tags for {file_path.name}...")
                
                if mutagen is None:
                    results.append({
                        "path": file_path, "artist": "Unknown", "album": "Unknown",
                        "mbid": None, "images": [], "error": "Mutagen unavailable"
                    })
                    continue

                try:
                    audio = mutagen.File(file_path, easy=True)
                except Exception as exc:
                    results.append({
                        "path": file_path, "artist": "Unknown", "album": "Unknown",
                        "mbid": None, "images": [], "error": f"Tag read error: {exc}"
                    })
                    continue

                if not audio:
                    results.append({
                        "path": file_path, "artist": "Unknown", "album": "Unknown",
                        "mbid": None, "images": [], "error": "Unsupported audio format"
                    })
                    continue

                artist = audio.get("artist", [""])[0]
                album = audio.get("album", [""])[0]

                if search_term:
                    self.progress.emit(i, total, f"Searching MusicBrainz for custom term '{search_term}'...")
                    query = search_term
                else:
                    if not artist or not album:
                        results.append({
                            "path": file_path, "artist": artist or "Unknown", "album": album or "Unknown",
                            "mbid": None, "images": [], "error": "Missing artist or album tag"
                        })
                        continue

                    self.progress.emit(i, total, f"Searching MusicBrainz for '{album}' by '{artist}'...")
                    # Clean up album name by removing bracketed text like [Explicit] or (Deluxe Edition)
                    # which often prevents exact phrase matches on MusicBrainz.
                    clean_album = re.sub(r'\[.*?\]|\(.*?\)', '', album).strip()
                    if not clean_album:
                        clean_album = album # Fallback if the whole name was in brackets
                    query = f'artist:"{artist}" AND release:"{clean_album}"'

                encoded_query = urllib.parse.quote(query)
                mb_url = f"https://musicbrainz.org/ws/2/release/?query={encoded_query}&fmt=json"
                req = urllib.request.Request(mb_url, headers={"User-Agent": USER_AGENT})
                
                mb_data = None
                max_retries = 3
                backoff = 2.0
                
                for attempt in range(max_retries):
                    try:
                        with urllib.request.urlopen(req, context=ssl_context, timeout=10) as response:
                            mb_data = json.loads(response.read())
                            # Keep baseline 1s delay per MusicBrainz guidelines, but we now handle 
                            # rate limits gracefully if we still hit them
                            time.sleep(1.0) 
                            break
                    except urllib.error.HTTPError as exc:
                        if exc.code in (503, 429) and attempt < max_retries - 1:
                            self.progress.emit(i, total, f"Rate limited by MusicBrainz! Retrying in {backoff}s...")
                            time.sleep(backoff)
                            backoff *= 2
                        else:
                            mb_data = exc
                            break
                    except Exception as exc:
                        mb_data = exc
                        break
                        
                if isinstance(mb_data, Exception):
                    results.append({
                        "path": file_path, "artist": artist, "album": album,
                        "mbid": None, "images": [], "error": f"MusicBrainz error: {mb_data}"
                    })
                    continue

                releases = mb_data.get("releases", [])
                if not releases:
                    results.append({
                        "path": file_path, "artist": artist, "album": album,
                        "mbid": None, "images": [], "error": "No release found on MusicBrainz"
                    })
                    continue

                fetched_images = []
                last_error = "No cover art available"
                
                # Fetch up to 6 releases
                for release in releases[:6]:
                    mbid = release["id"]
                    self.progress.emit(i, total, f"Fetching Cover Art for '{album}' (Release {len(fetched_images)+1})...")
                    
                    caa_url = f"https://coverartarchive.org/release/{mbid}/front"
                    caa_req = urllib.request.Request(caa_url, headers={"User-Agent": USER_AGENT})
                    try:
                        with urllib.request.urlopen(caa_req, context=ssl_context, timeout=10) as response:
                            image_data = response.read()
                            
                            # Verify it's an image
                            img = QImage()
                            if img.loadFromData(image_data):
                                fetched_images.append(image_data)
                    except urllib.error.HTTPError as exc:
                        if exc.code == 404:
                            last_error = "No cover art available for this release"
                        else:
                            last_error = f"Cover Art error: {exc.code}"
                    except Exception as exc:
                        last_error = f"Download failed: {exc}"

                if fetched_images:
                    results.append({
                        "path": file_path, "artist": artist, "album": album,
                        "mbid": releases[0]["id"], "images": fetched_images, "error": None
                    })
                else:
                    results.append({
                        "path": file_path, "artist": artist, "album": album,
                        "mbid": releases[0]["id"], "images": [], "error": last_error
                    })

            self.finished.emit(results)
        except Exception as exc:
            logger.error("Album art download failed", exc_info=True)
            self.failed.emit(str(exc))


class AlbumArtDownloadDialog(QDialog):
    def __init__(self, results: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Verify Downloaded Album Art")
        self.setMinimumSize(800, 600)
        self.results = results
        self.verified_items = []

        layout = QVBoxLayout(self)

        instruction = QLabel(
            "Review the downloaded artwork below. Tick the checkboxes for the ones you want to apply."
        )
        instruction.setStyleSheet("font-weight: bold;")
        layout.addWidget(instruction)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Apply", "File", "Metadata", "Preview", "Results"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(120)
        self.table.setSelectionMode(QTableWidget.NoSelection)

        layout.addWidget(self.table)

        self._populate_table()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        self.apply_btn = QPushButton("Apply Selected")
        self.apply_btn.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold;")
        self.apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(self.apply_btn)

        layout.addLayout(btn_layout)

    def _populate_table(self):
        for res in self.results:
            row = self.table.rowCount()
            self.table.insertRow(row)

            images = res.get("images", [])

            # Apply Checkbox
            checkbox = QCheckBox()
            if images:
                checkbox.setChecked(True)
            else:
                checkbox.setEnabled(False)
            
            # center checkbox
            cb_widget = QHBoxLayout()
            cb_widget.setAlignment(Qt.AlignCenter)
            cb_widget.setContentsMargins(0, 0, 0, 0)
            cb_widget.addWidget(checkbox)
            cb_container = QLabel()
            cb_container.setLayout(cb_widget)
            self.table.setCellWidget(row, 0, cb_container)
            
            # Store the checkbox reference so we can read it later
            checkbox.setProperty("result_data", res)
            
            # File
            file_item = QTableWidgetItem(res["path"].name)
            file_item.setFlags(file_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, file_item)

            # Metadata or Error
            if images:
                meta_text = f"Artist: {res['artist']}\nAlbum: {res['album']}"
            else:
                meta_text = f"Artist: {res['artist']}\nAlbum: {res['album']}\n\nError: {res['error']}"
            meta_item = QTableWidgetItem(meta_text)
            meta_item.setFlags(meta_item.flags() & ~Qt.ItemIsEditable)
            if res["error"] and not images:
                meta_item.setForeground(Qt.red)
            self.table.setItem(row, 2, meta_item)

            # Preview
            preview_lbl = QLabel()
            preview_lbl.setAlignment(Qt.AlignCenter)
            
            # Results Dropdown
            results_container = QWidget()
            results_layout = QVBoxLayout(results_container)
            results_layout.setContentsMargins(5, 5, 5, 5)
            results_layout.setAlignment(Qt.AlignCenter)

            if images:
                combo = QComboBox()
                pixmaps = []
                for i, img_data in enumerate(images):
                    img = QImage()
                    if img.loadFromData(img_data):
                        pm = QPixmap.fromImage(img)
                        pixmaps.append(pm)
                        combo.addItem(QIcon(pm), f"Result {i+1}", userData=img_data)
                
                if pixmaps:
                    preview_lbl.setPixmap(pixmaps[0].scaled(90, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    
                    def on_combo_changed(idx, lbl=preview_lbl, pms=pixmaps):
                        if idx < 0 or idx >= len(pms):
                            return
                        lbl.setPixmap(pms[idx].scaled(90, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))

                    combo.currentIndexChanged.connect(on_combo_changed)
                    
                    if len(images) > 1:
                        results_layout.addWidget(combo)
                    
                    checkbox.setProperty("combo_box", combo)
            else:
                preview_lbl.setText("No Image")

            self.table.setCellWidget(row, 3, preview_lbl)
            self.table.setCellWidget(row, 4, results_container)

    def _on_apply(self):
        self.verified_items = []
        for row in range(self.table.rowCount()):
            cb_container = self.table.cellWidget(row, 0)
            checkbox = cb_container.layout().itemAt(0).widget()
            if checkbox.isChecked():
                res = checkbox.property("result_data")
                combo = checkbox.property("combo_box")
                if combo is not None:
                    # Modify res directly so window.py doesn't need to change
                    res["image_data"] = combo.currentData()
                self.verified_items.append(res)
        self.accept()
