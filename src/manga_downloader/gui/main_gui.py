import os
import sys
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple, Union

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar, QFileDialog, QMessageBox, QGroupBox, QStackedWidget,
    QRadioButton, QButtonGroup, QSpinBox, QScrollArea, QFrame, QDialog, QDialogButtonBox,
    QAbstractItemView
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap

from manga_downloader.downloaders import (
    load_config,
    MangaDownloader,
    OlympusScanDownloader,
    MangaTVDownloader,
    LectorKnightDownloader,
    ZonaTMODownloader,
    TomosMangaDownloader,
    save_metadata_inventario as save_metadata,
    save_metadata_olympus,
    save_metadata_mangatv,
    save_metadata_lectorknight,
    save_metadata_zonatmo,
    save_metadata_tomosmanga,
)
from manga_downloader.utils import CBRGenerator, group_cbrs_by_manga, create_zip_from_cbrs, create_zip_name, extract_manga_title_and_tomo
from manga_downloader.paths import get_project_root


DownloaderType = Union[
    MangaDownloader,
    OlympusScanDownloader,
    MangaTVDownloader,
    LectorKnightDownloader,
    ZonaTMODownloader,
    TomosMangaDownloader,
]


def _build_downloader(url, website_id, config) -> DownloaderType:
    if website_id == 'olympus_scan':
        return OlympusScanDownloader(url, config)
    if website_id == 'mangatv':
        return MangaTVDownloader(url, config)
    if website_id == 'lectorknight':
        return LectorKnightDownloader(url, config)
    if website_id == 'zonatmo':
        return ZonaTMODownloader(url, config)
    if website_id == 'tomosmanga':
        return TomosMangaDownloader(url, config)
    return MangaDownloader(url, config)


def _get_series_info(downloader: DownloaderType, html_content: str) -> Tuple[str, str]:
    if isinstance(downloader, OlympusScanDownloader):
        return downloader.get_manhwa_title(html_content), "manhwa"
    if isinstance(downloader, (MangaTVDownloader, ZonaTMODownloader)):
        return downloader.get_manga_title(html_content), downloader.get_manga_type(html_content)
    if isinstance(downloader, LectorKnightDownloader):
        return downloader.get_manga_title(html_content), "manhwa"
    return downloader.get_manga_title(html_content), "manga"


class LoadVolumesWorker(QObject):
    finished = Signal(object, object)
    error = Signal(str)
    log_msg = Signal(str)

    def __init__(self, url, website_id, config):
        super().__init__()
        self.url = url
        self.website_id = website_id
        self.config = config
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                self.error.emit("Cancelado")
                return
            downloader = _build_downloader(self.url, self.website_id, self.config)
            if self._cancelled:
                self.error.emit("Cancelado")
                return
            html_content = downloader.get_page(self.url, use_selenium=True)
            if self._cancelled:
                self.error.emit("Cancelado")
                return
            if not html_content:
                self.error.emit("No se pudo cargar la página")
                return
            manga_title, manga_type = _get_series_info(downloader, html_content)
            if self._cancelled:
                self.error.emit("Cancelado")
                return
            parse_result = downloader.parse_volumes(html_content, debug=False)
            if self._cancelled:
                self.error.emit("Cancelado")
                return
            if self.website_id in ('mangatv', 'zonatmo') and parse_result and isinstance(parse_result, dict) and 'volumes' in parse_result:
                volumes = parse_result['volumes']
                if not volumes:
                    self.error.emit("No se encontraron volúmenes")
                    return
                common_scanlations = parse_result.get('common_scanlations', {})
                volumes_with_options = [v for v in volumes if v.get('options')]
                if volumes_with_options and common_scanlations:
                    scanlation_list = list(common_scanlations.keys())
                    self.finished.emit(downloader, {'scanlation_dialog': (scanlation_list, common_scanlations, volumes_with_options, volumes, parse_result, manga_title, manga_type)})
                    return
                if volumes_with_options:
                    self.finished.emit(downloader, {'scanlation_per_chapter': (volumes_with_options, volumes, parse_result, manga_title, manga_type)})
                    return
                sorted_volumes = sorted(volumes, key=lambda v: float(downloader.extract_chapter_numbers(v['name'])))
                if manga_type == 'manga':
                    self.finished.emit(downloader, {'tomos_dialog': (sorted_volumes, manga_title, manga_type)})
                    return
                self.finished.emit(downloader, (sorted_volumes, manga_title, manga_type))
                return
            volumes = parse_result if isinstance(parse_result, list) else (parse_result.get('volumes', []) if isinstance(parse_result, dict) else [])
            if not volumes:
                self.error.emit("No se encontraron volúmenes/capítulos")
                return
            if self.website_id not in ('olympus_scan', 'mangatv', 'lectorknight', 'tomosmanga'):
                volumes = list(reversed(volumes))
            self.finished.emit(downloader, (volumes, manga_title, manga_type))
        except Exception as e:
            self.error.emit(str(e))


class DownloadWorker(QObject):
    log_msg = Signal(str)
    progress_status = Signal(str)
    finished_signal = Signal()

    def __init__(self, downloader, selected_volumes, output_dir, manga_title, manga_type, tomos_structure, website_id, config, save_metadata_fn):
        super().__init__()
        self.downloader = downloader
        self.selected_volumes = selected_volumes
        self.output_dir = output_dir
        self.manga_title = manga_title
        self.manga_type = manga_type
        self.tomos_structure = tomos_structure
        self.website_id = website_id
        self.config = config
        self.save_metadata_fn = save_metadata_fn

    def run(self):
        try:
            self.save_metadata_fn(self.manga_title, self.selected_volumes, self.output_dir)
            total_volumes = len(self.selected_volumes)
            is_olympus = self.website_id == 'olympus_scan'
            is_mangatv = self.website_id == 'mangatv'
            is_lectorknight = self.website_id == 'lectorknight'
            is_zonatmo = self.website_id == 'zonatmo'
            is_tomosmanga = self.website_id == 'tomosmanga'
            volume_label = "capítulos" if is_olympus or is_mangatv or is_lectorknight or is_zonatmo or is_tomosmanga else "tomos"
            self.log_msg.emit(f"Iniciando descarga de {total_volumes} {volume_label}...")
            if is_tomosmanga and self.downloader:
                def on_fireload_progress(current, total, chapter_name):
                    self.progress_status.emit(f"Obteniendo URLs ({current}/{total}): {chapter_name}")
                    self.log_msg.emit(f"Obteniendo URL: {chapter_name}")
                def on_download_start(chapter_name, file_size):
                    size_mb = (file_size / (1024 * 1024)) if file_size else 0
                    self.progress_status.emit(f"Descargando: {chapter_name} ({size_mb:.2f} MB)")
                    self.log_msg.emit(f"Iniciando descarga: {chapter_name} ({size_mb:.2f} MB)")
                def on_download_progress(chapter_name, percent, speed_mb, downloaded, total):
                    downloaded_mb = downloaded / (1024 * 1024)
                    total_mb = total / (1024 * 1024) if total else 0
                    speed_text = f" @ {speed_mb:.1f} MB/s" if speed_mb > 0 else ""
                    if percent > 0 and total_mb > 0:
                        self.progress_status.emit(f"Descargando: {chapter_name} - {percent:.1f}% ({downloaded_mb:.1f}/{total_mb:.1f} MB){speed_text}")
                    else:
                        self.progress_status.emit(f"Descargando: {chapter_name} - ({downloaded_mb:.1f} MB){speed_text}")
                def on_download_complete(chapter_name):
                    self.log_msg.emit(f"✓ {chapter_name} completado")
                self.downloader.set_callback('on_fireload_progress', on_fireload_progress)
                self.downloader.set_callback('on_download_start', on_download_start)
                self.downloader.set_callback('on_download_progress', on_download_progress)
                self.downloader.set_callback('on_download_complete', on_download_complete)
            parallel_tomos = self.config.get('parallel_tomos', 1)
            all_failed_chapters = []

            if is_tomosmanga and isinstance(self.downloader, TomosMangaDownloader):
                prefetch_size = self.config.get('tomosmanga_prefetch', min(2, max(1, parallel_tomos)))
                self.log_msg.emit(f"TomosManga: hasta {parallel_tomos} descargas activas, prefetch {prefetch_size}")
                pipeline_result = self.downloader.download_volumes_keep_filled(
                    self.selected_volumes,
                    self.manga_title,
                    self.output_dir,
                    max_downloads=parallel_tomos,
                    prefetch_size=prefetch_size,
                )
                all_failed_chapters.extend(pipeline_result.get('failed_chapters', []))
                if self.downloader and self.downloader.cancelled:
                    self.log_msg.emit("Descarga cancelada por el usuario")
                else:
                    completed_count = pipeline_result.get('completed_count', 0)
                    self.log_msg.emit(f"Descarga completada: {completed_count}/{total_volumes} {volume_label}")
                self.finished_signal.emit()
                return

            def download_single_tomo(volume, idx, total):
                if self.downloader and self.downloader.cancelled:
                    return None
                if is_olympus or is_mangatv or is_lectorknight or is_zonatmo:
                    volume_number = self.downloader.extract_chapter_numbers(volume['name'])
                    volume_label_single = "Capítulo"
                elif is_tomosmanga:
                    volume_label_single = "Tomo"
                    volume_number = self.downloader.extract_chapter_numbers(volume['name'])
                else:
                    volume_label_single = "Tomo"
                    volume_number = self.downloader.extract_tomo_number(volume['name'])
                self.log_msg.emit(f"Procesando {volume_label_single} {idx}/{total}: {volume_label_single} {volume_number}")
                is_tomo_structure = (is_mangatv or is_zonatmo) and self.manga_type == 'manga' and self.tomos_structure
                try:
                    if is_tomo_structure:
                        result = self.downloader.download_volume(volume, self.manga_title, self.output_dir, selected_option=None, is_tomo_structure=True) if (is_mangatv or is_zonatmo) else self.downloader.download_volume(volume, self.manga_title, self.output_dir, is_tomo_structure=True)
                    else:
                        result = self.downloader.download_volume(volume, self.manga_title, self.output_dir, selected_option=None) if (is_mangatv or is_zonatmo) else self.downloader.download_volume(volume, self.manga_title, self.output_dir)
                except Exception as e:
                    self.log_msg.emit(f"[ERROR] {e}")
                    return None
                if result and result.get('dir'):
                    self.log_msg.emit(f"{volume_label_single} {volume_number} descargado correctamente")
                    return {'dir': result['dir'], 'tomo_number': volume_number, 'failed_chapters': result.get('failed_chapters', [])}
                return None

            if parallel_tomos > 1 and total_volumes > 1:
                with ThreadPoolExecutor(max_workers=parallel_tomos) as executor:
                    futures = {executor.submit(download_single_tomo, volume, idx + 1, total_volumes): idx for idx, volume in enumerate(self.selected_volumes)}
                    for future in as_completed(futures):
                        if self.downloader and self.downloader.cancelled:
                            executor.shutdown(wait=False, cancel_futures=True)
                            self.log_msg.emit("Descarga cancelada por el usuario")
                            break
                        try:
                            result = future.result()
                            if result and isinstance(result, dict) and 'failed_chapters' in result:
                                all_failed_chapters.extend(result.get('failed_chapters', []))
                        except Exception as e:
                            self.log_msg.emit(f"Error: {str(e)}")
            else:
                for idx, volume in enumerate(self.selected_volumes, start=1):
                    if self.downloader and self.downloader.cancelled:
                        self.log_msg.emit("Descarga cancelada por el usuario")
                        break
                    result = download_single_tomo(volume, idx, total_volumes)
                    if result and isinstance(result, dict) and 'failed_chapters' in result:
                        all_failed_chapters.extend(result.get('failed_chapters', []))
            if self.downloader and not self.downloader.cancelled:
                self.log_msg.emit(f"Descarga completada: {total_volumes} {volume_label}")
            self.finished_signal.emit()
        except Exception as e:
            self.log_msg.emit(f"[ERROR] {e}")
            self.finished_signal.emit()


class CoverPickerDialog(QDialog):
    """Diálogo para elegir portada por tomo: preview de imagen y Siguiente/Anterior entre tomos con check."""
    PREVIEW_MAX_SIZE = (400, 550)

    def __init__(self, parent, volumes_with_images, selected_cover, refresh_callback, log_callback):
        super().__init__(parent)
        self.volumes = volumes_with_images
        self.selected_cover = selected_cover
        self.refresh_callback = refresh_callback
        self.log_callback = log_callback
        self.current_index = 0
        self.setWindowTitle("Elegir portada")
        self.setMinimumSize(700, 500)
        layout = QVBoxLayout(self)
        self.volume_label = QLabel()
        layout.addWidget(self.volume_label)
        content = QHBoxLayout()
        self.list_w = QListWidget()
        self.list_w.setMaximumWidth(320)
        self.list_w.currentRowChanged.connect(self._on_image_selected)
        content.addWidget(self.list_w)
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(380, 520)
        self.preview_label.setStyleSheet("background-color: #2d2d2d; border: 1px solid #555;")
        self.preview_label.setText("Selecciona una imagen")
        content.addWidget(self.preview_label, 1)
        layout.addLayout(content)
        nav = QHBoxLayout()
        self.prev_btn = QPushButton("← Anterior")
        self.prev_btn.clicked.connect(self._go_prev)
        self.next_btn = QPushButton("Siguiente →")
        self.next_btn.clicked.connect(self._go_next)
        nav.addWidget(self.prev_btn)
        nav.addStretch()
        self.use_cover_btn = QPushButton("Usar como portada")
        self.use_cover_btn.clicked.connect(self._set_cover)
        nav.addWidget(self.use_cover_btn)
        nav.addStretch()
        nav.addWidget(self.next_btn)
        layout.addLayout(nav)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)
        self._show_volume()

    def _current_volume(self):
        if 0 <= self.current_index < len(self.volumes):
            return self.volumes[self.current_index]
        return None

    def _show_volume(self):
        v = self._current_volume()
        if not v:
            return
        n, total = self.current_index + 1, len(self.volumes)
        self.volume_label.setText(f"Tomo {n} de {total}: {v['volume_name']}")
        self.prev_btn.setVisible(total > 1)
        self.next_btn.setVisible(total > 1)
        self.list_w.clear()
        self._image_paths = v['images']
        for path in self._image_paths:
            self.list_w.addItem(os.path.relpath(path, v['volume_path']))
        if self._image_paths:
            self.list_w.setCurrentRow(0)
            self._update_preview(self._image_paths[0])
        else:
            self.preview_label.setText("Sin imágenes")
            self.preview_label.setPixmap(QPixmap())

    def _on_image_selected(self, row):
        if not hasattr(self, '_image_paths') or row < 0 or row >= len(self._image_paths):
            return
        self._update_preview(self._image_paths[row])

    def _update_preview(self, path):
        try:
            pix = QPixmap(path)
            if pix.isNull():
                self.preview_label.setText("No se pudo cargar")
                return
            scaled = pix.scaled(
                self.PREVIEW_MAX_SIZE[0], self.PREVIEW_MAX_SIZE[1],
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.preview_label.setPixmap(scaled)
            self.preview_label.setText("")
        except Exception:
            self.preview_label.setText("Error al cargar")

    def _go_prev(self):
        if self.current_index > 0:
            self.current_index -= 1
            self._show_volume()

    def _go_next(self):
        if self.current_index < len(self.volumes) - 1:
            self.current_index += 1
            self._show_volume()

    def _set_cover(self):
        v = self._current_volume()
        if not v or not hasattr(self, '_image_paths'):
            return
        row = self.list_w.currentRow()
        if row < 0 or row >= len(self._image_paths):
            return
        chosen = self._image_paths[row]
        self.selected_cover[v['volume_path']] = chosen
        base = os.path.basename(chosen)
        if self.log_callback:
            self.log_callback(f"Portada para «{v['volume_name']}»: {base}")
        self.refresh_callback()
        if self.current_index < len(self.volumes) - 1:
            self.current_index += 1
            self._show_volume()
        else:
            self.accept()


class MangaDownloaderWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.website_names = {
            'Inventario Oculto': 'inventario_oculto',
            'Olympus Scan': 'olympus_scan',
            'MangaTV': 'mangatv',
            'Lector KNS': 'lectorknight',
            'ZonaTMO': 'zonatmo',
            'TomosManga': 'tomosmanga'
        }
        self.website_ids = {v: k for k, v in self.website_names.items()}
        self.downloader: Optional[DownloaderType] = None
        self.volumes = []
        self.manga_title = ""
        self.manga_type = "manga"
        self.tomos_structure = None
        self.load_thread = None
        self.load_worker = None
        self.download_thread = None
        self.download_worker = None
        self.is_downloading = False
        self.mangas_data = []
        self.rar_mangas_data = []
        self.cbr_checkboxes = []
        self.rar_checkboxes = []
        self.generator = CBRGenerator()
        self.selected_cover = {}
        self.setWindowTitle("Descargador de Manga - Generador de CBR")
        self.setMinimumSize(900, 700)
        icon_path = os.path.join(get_project_root(), 'logos', 'icono.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setup_ui()

    def get_volume_label(self):
        w = self.website_combo.currentData() or 'inventario_oculto'
        if w in ('olympus_scan', 'mangatv', 'lectorknight', 'zonatmo', 'tomosmanga'):
            return "Capítulos"
        return "Tomos"

    def update_website_logo(self):
        logo_files = {
            'inventario_oculto': 'logo_inventario_oculto.png',
            'olympus_scan': 'logo_olympus_scan.webp',
            'mangatv': 'logo_manga_tv.png',
            'lectorknight': 'logo_knight.png',
            'zonatmo': 'logo_zonatmo.png',
            'tomosmanga': 'logo_tomosmanga.png',
        }
        website_id = self.website_combo.currentData() or 'inventario_oculto'
        filename = logo_files.get(website_id)
        if not filename:
            self.logo_label.clear()
            return
        logo_path = os.path.join(get_project_root(), 'logos', filename)
        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                self.logo_label.setPixmap(pixmap)
                self.logo_label.setText("")
            else:
                self.logo_label.clear()
                self.logo_label.setText("(sin logo)")
        else:
            self.logo_label.clear()
            self.logo_label.setText("(sin logo)")

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        mode_group = QButtonGroup(self)
        mode_layout = QHBoxLayout()
        self.mode_download = QRadioButton("Descargar Manga")
        self.mode_download.setChecked(True)
        self.mode_cbr = QRadioButton("Crear CBR")
        self.mode_rar = QRadioButton("Crear RAR")
        mode_group.addButton(self.mode_download)
        mode_group.addButton(self.mode_cbr)
        mode_group.addButton(self.mode_rar)
        mode_layout.addWidget(self.mode_download)
        mode_layout.addWidget(self.mode_cbr)
        mode_layout.addWidget(self.mode_rar)
        main_layout.addLayout(mode_layout)
        self.stacked = QStackedWidget()
        self.download_page = QWidget()
        dl_layout = QVBoxLayout(self.download_page)
        web_layout = QHBoxLayout()
        web_layout.addWidget(QLabel("Sitio Web:"))
        self.website_combo = QComboBox()
        for name, wid in self.website_names.items():
            self.website_combo.addItem(name, wid)
        self.website_combo.currentIndexChanged.connect(self.on_website_change)
        web_layout.addWidget(self.website_combo)
        self.logo_label = QLabel()
        self.logo_label.setFixedSize(120, 80)
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setStyleSheet("background-color: #e0e0e0; border: 1px solid #999;")
        self.logo_label.setMinimumSize(120, 80)
        self.logo_label.setScaledContents(True)
        web_layout.addWidget(self.logo_label)
        self.update_website_logo()
        dl_layout.addLayout(web_layout)
        url_layout = QHBoxLayout()
        self.url_label = QLabel("URL del Manga:")
        url_layout.addWidget(self.url_label)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://...")
        self.url_edit.textChanged.connect(self.on_url_change)
        url_layout.addWidget(self.url_edit)
        self.load_btn = QPushButton("Cargar tomos")
        self.load_btn.clicked.connect(self.load_volumes)
        self.cancel_load_btn = QPushButton("Cancelar Carga")
        self.cancel_load_btn.setEnabled(False)
        self.cancel_load_btn.clicked.connect(self.cancel_load)
        url_layout.addWidget(self.load_btn)
        url_layout.addWidget(self.cancel_load_btn)
        dl_layout.addLayout(url_layout)
        dl_layout.addWidget(QLabel("Título:"))
        self.title_label = QLabel("")
        self.title_label.setStyleSheet("color: blue;")
        dl_layout.addWidget(self.title_label)
        dl_layout.addWidget(QLabel("Tomos/Capítulos disponibles:"))
        self.volumes_list = QListWidget()
        self.volumes_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        dl_layout.addWidget(self.volumes_list)
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("Seleccionar todos")
        select_all_btn.clicked.connect(self.select_all)
        deselect_all_btn = QPushButton("Deseleccionar todos")
        deselect_all_btn.clicked.connect(self.deselect_all)
        self.download_btn = QPushButton("Descargar seleccionados")
        self.download_btn.clicked.connect(self.start_download)
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_download)
        btn_layout.addWidget(select_all_btn)
        btn_layout.addWidget(deselect_all_btn)
        btn_layout.addWidget(self.download_btn)
        btn_layout.addWidget(self.cancel_btn)
        dl_layout.addLayout(btn_layout)
        self.stacked.addWidget(self.download_page)
        self.cbr_page = QWidget()
        cbr_layout = QVBoxLayout(self.cbr_page)
        cbr_layout.addWidget(QLabel("Manga:"))
        cbr_top = QHBoxLayout()
        self.manga_combo = QComboBox()
        self.manga_combo.setEditable(False)
        self.manga_combo.currentIndexChanged.connect(self.on_manga_selected)
        cbr_top.addWidget(self.manga_combo)
        load_mangas_btn = QPushButton("Cargar mangas")
        load_mangas_btn.clicked.connect(self.load_available_mangas)
        cbr_top.addWidget(load_mangas_btn)
        cbr_layout.addLayout(cbr_top)
        cbr_layout.addWidget(QLabel("Tomos:"))
        self.cbr_list = QListWidget()
        self.cbr_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        cbr_layout.addWidget(self.cbr_list)
        cbr_btn = QHBoxLayout()
        cbr_select_all = QPushButton("Seleccionar todos")
        cbr_select_all.clicked.connect(self.select_all_cbr)
        cbr_deselect = QPushButton("Deseleccionar todos")
        cbr_deselect.clicked.connect(self.deselect_all_cbr)
        self.choose_cover_btn = QPushButton("Elegir portada")
        self.choose_cover_btn.clicked.connect(self.choose_cover_cbr)
        self.clear_cover_btn = QPushButton("Quitar portada")
        self.clear_cover_btn.clicked.connect(self.clear_cover_cbr)
        self.generate_cbr_btn = QPushButton("Generar CBRs seleccionados")
        self.generate_cbr_btn.clicked.connect(self.generate_cbrs)
        cbr_btn.addWidget(cbr_select_all)
        cbr_btn.addWidget(cbr_deselect)
        cbr_btn.addWidget(self.choose_cover_btn)
        cbr_btn.addWidget(self.clear_cover_btn)
        cbr_btn.addWidget(self.generate_cbr_btn)
        cbr_layout.addLayout(cbr_btn)
        self.stacked.addWidget(self.cbr_page)
        self.rar_page = QWidget()
        rar_layout = QVBoxLayout(self.rar_page)
        rar_layout.addWidget(QLabel("Manga:"))
        rar_top = QHBoxLayout()
        self.rar_manga_combo = QComboBox()
        self.rar_manga_combo.currentIndexChanged.connect(self.on_rar_manga_selected)
        rar_top.addWidget(self.rar_manga_combo)
        load_rar_btn = QPushButton("Cargar mangas")
        load_rar_btn.clicked.connect(self.load_rar_mangas)
        rar_top.addWidget(load_rar_btn)
        rar_layout.addLayout(rar_top)
        rar_layout.addWidget(QLabel("Tomos por RAR:"))
        self.tomos_per_rar_spin = QSpinBox()
        self.tomos_per_rar_spin.setRange(1, 20)
        self.tomos_per_rar_spin.setValue(4)
        rar_layout.addWidget(self.tomos_per_rar_spin)
        rar_layout.addWidget(QLabel("CBRs:"))
        self.rar_list = QListWidget()
        self.rar_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        rar_layout.addWidget(self.rar_list)
        rar_btn = QHBoxLayout()
        rar_select_all = QPushButton("Seleccionar todos")
        rar_select_all.clicked.connect(self.select_all_rar)
        rar_deselect = QPushButton("Deseleccionar todos")
        rar_deselect.clicked.connect(self.deselect_all_rar)
        self.generate_rar_btn = QPushButton("Generar RARs")
        self.generate_rar_btn.clicked.connect(self.generate_rars)
        rar_btn.addWidget(rar_select_all)
        rar_btn.addWidget(rar_deselect)
        rar_btn.addWidget(self.generate_rar_btn)
        rar_layout.addLayout(rar_btn)
        self.stacked.addWidget(self.rar_page)
        main_layout.addWidget(self.stacked)
        self.mode_download.toggled.connect(lambda b: self.stacked.setCurrentIndex(0) if b else None)
        self.mode_cbr.toggled.connect(lambda b: self.stacked.setCurrentIndex(1) if b else None)
        self.mode_rar.toggled.connect(lambda b: self.stacked.setCurrentIndex(2) if b else None)
        main_layout.addWidget(QLabel("Progreso:"))
        self.progress_label = QLabel("Listo")
        main_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)
        main_layout.addWidget(QLabel("Logs:"))
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        main_layout.addWidget(self.log_text)
        self.on_website_change()

    def on_website_change(self):
        self.update_website_logo()
        vol_label = self.get_volume_label()
        self.load_btn.setText(f"Cargar {vol_label}")
        w = self.website_combo.currentData() or 'inventario_oculto'
        if w in ('olympus_scan', 'mangatv', 'lectorknight', 'zonatmo', 'tomosmanga'):
            self.url_label.setText("URL del Manga/Manhwa:")
        else:
            self.url_label.setText("URL del Manga:")

    def on_url_change(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        url_lower = url.lower()
        if 'lectorknight' in url_lower:
            idx = self.website_combo.findData('lectorknight')
            if idx >= 0:
                self.website_combo.setCurrentIndex(idx)
        elif 'zonatmo' in url_lower:
            idx = self.website_combo.findData('zonatmo')
            if idx >= 0:
                self.website_combo.setCurrentIndex(idx)
        elif 'mangatv' in url_lower:
            idx = self.website_combo.findData('mangatv')
            if idx >= 0:
                self.website_combo.setCurrentIndex(idx)
        elif 'olympusbiblioteca' in url_lower:
            idx = self.website_combo.findData('olympus_scan')
            if idx >= 0:
                self.website_combo.setCurrentIndex(idx)
        elif 'inventariooculto' in url_lower:
            idx = self.website_combo.findData('inventario_oculto')
            if idx >= 0:
                self.website_combo.setCurrentIndex(idx)
        elif 'tomosmanga' in url_lower:
            idx = self.website_combo.findData('tomosmanga')
            if idx >= 0:
                self.website_combo.setCurrentIndex(idx)

    def log(self, message):
        self.log_text.appendPlainText(message)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def load_volumes(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "Ingresa una URL")
            return
        website_id = self.website_combo.currentData() or 'inventario_oculto'
        self.load_btn.setEnabled(False)
        self.cancel_load_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText("Cargando...")
        self.log(f"Cargando información de: {url}")
        self.load_worker = LoadVolumesWorker(url, website_id, self.config)
        self.load_thread = QThread()
        self.load_worker.moveToThread(self.load_thread)
        self.load_thread.started.connect(self.load_worker.run)
        self.load_worker.finished.connect(self.on_load_finished)
        self.load_worker.error.connect(self.on_load_error)
        self.load_worker.log_msg.connect(self.log)
        self.load_thread.start()

    def cancel_load(self):
        if self.load_worker:
            self.load_worker.cancel()
        self.load_btn.setEnabled(True)
        self.cancel_load_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("Carga cancelada")
        self.log("Carga cancelada por el usuario")

    def on_load_finished(self, downloader, payload):
        if self.load_thread and self.load_thread.isRunning():
            self.load_thread.quit()
            self.load_thread.wait()
        self.load_btn.setEnabled(True)
        self.cancel_load_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("Listo")
        if isinstance(payload, dict):
            key = next(iter(payload))
            data = payload[key]
            self.downloader = downloader
            if key == 'scanlation_dialog':
                scanlation_list, common_scanlations, volumes_with_options, volumes, parse_result, self.manga_title, self.manga_type = data
                self.show_scanlation_dialog(scanlation_list, common_scanlations, volumes_with_options, volumes, parse_result)
                return
            if key == 'scanlation_per_chapter':
                self.show_scanlation_per_chapter(*data)
                return
            if key == 'tomos_dialog':
                sorted_volumes, self.manga_title, self.manga_type = data
                self.show_tomos_dialog(sorted_volumes)
                return
            return
        self.downloader = downloader
        volumes, manga_title, manga_type = payload
        self.volumes = volumes
        self.manga_title = manga_title or "Manga"
        self.manga_type = manga_type or "manga"
        self.update_volumes_list()
        vol_label = self.get_volume_label().lower()
        QMessageBox.information(self, "Éxito", f"Se encontraron {len(self.volumes)} {vol_label}")

    def on_load_error(self, msg):
        if self.load_thread and self.load_thread.isRunning():
            self.load_thread.quit()
            self.load_thread.wait()
        self.load_btn.setEnabled(True)
        self.cancel_load_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("Listo")
        QMessageBox.critical(self, "Error", msg)
        self.log(f"[ERROR] {msg}")

    def show_scanlation_dialog(self, scanlation_list, common_scanlations, volumes_with_options, volumes, parse_result):
        dialog = QDialog(self)
        dialog.setWindowTitle("Seleccionar Scanlation")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Elige una scanlation:"))
        list_w = QListWidget()
        for s in scanlation_list:
            count = common_scanlations.get(s, 0)
            list_w.addItem(f"{s} (presente en {count} capítulos)")
        layout.addWidget(list_w)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dialog.accept)
        bb.rejected.connect(dialog.reject)
        layout.addWidget(bb)
        if dialog.exec() == QDialog.DialogCode.Accepted and list_w.currentRow() >= 0:
            global_scanlation = scanlation_list[list_w.currentRow()]
            processed_volumes = []
            for volume in volumes_with_options:
                for option in volume.get('options', []):
                    if option['scanlation'] == global_scanlation:
                        volume = dict(volume)
                        volume['chapters'] = [{'name': option['name'], 'url': option['url']}]
                        volume['options'] = None
                        processed_volumes.append(volume)
                        break
            volumes_without = [v for v in volumes if not v.get('options') and v.get('single_scanlation') == global_scanlation]
            downloader = self.downloader
            if downloader is None:
                QMessageBox.critical(self, "Error", "No hay un downloader activo para ordenar los capÃ­tulos.")
                self.load_btn.setEnabled(True)
                return
            all_volumes = sorted(volumes_without + processed_volumes, key=lambda v: float(downloader.extract_chapter_numbers(v['name'])))
            if self.manga_type == 'manga':
                self.show_tomos_dialog(all_volumes)
            else:
                self.volumes = all_volumes
                self.update_volumes_list()
                QMessageBox.information(self, "Éxito", f"Se encontraron {len(self.volumes)} capítulos")
        else:
            self.load_btn.setEnabled(True)

    def show_scanlation_per_chapter(self, volumes_with_options, volumes, parse_result, manga_title, manga_type):
        self.manga_title = manga_title
        self.manga_type = manga_type
        self.volumes = volumes
        self.update_volumes_list()
        QMessageBox.information(self, "Éxito", f"Capítulos cargados: {len(self.volumes)}")

    def show_tomos_dialog(self, chapters):
        available = []
        for ch in chapters:
            if ch.get('chapters'):
                available.extend(ch['chapters'])
            else:
                available.append(ch)
        self.volumes = [{'name': f"Tomo 1", 'chapters': available}]
        self.update_volumes_list()
        QMessageBox.information(self, "Éxito", f"Se creó 1 tomo con {len(available)} capítulos")

    def update_volumes_list(self):
        self.volumes_list.clear()
        self.title_label.setText(self.manga_title)
        for idx, volume in enumerate(self.volumes, start=1):
            name = volume['name']
            ch_count = len(volume.get('chapters', []))
            if ch_count:
                text = f"{idx}. {name} ({ch_count} capítulos)"
            else:
                text = name
            item = QListWidgetItem(text)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, volume)
            self.volumes_list.addItem(item)
        self.log(f"Título: {self.manga_title}")
        self.log(f"Encontrados: {len(self.volumes)}")

    def select_all(self):
        for i in range(self.volumes_list.count()):
            self.volumes_list.item(i).setCheckState(Qt.CheckState.Checked)

    def deselect_all(self):
        for i in range(self.volumes_list.count()):
            self.volumes_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def get_selected_volumes(self):
        result = []
        for i in range(self.volumes_list.count()):
            item = self.volumes_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                v = item.data(Qt.ItemDataRole.UserRole)
                if v:
                    result.append(v)
        return result

    def start_download(self):
        if self.is_downloading:
            QMessageBox.warning(self, "Advertencia", "Ya hay una descarga en progreso")
            return
        selected = self.get_selected_volumes()
        if not selected:
            QMessageBox.warning(self, "Advertencia", "Selecciona al menos un tomo/capítulo")
            return
        downloader = self.downloader
        if downloader is None:
            QMessageBox.warning(self, "Error", "Primero carga los tomos/capÃ­tulos antes de descargar.")
            return
        output_dir = self.config['output_dir']
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(get_project_root(), output_dir)
        os.makedirs(output_dir, exist_ok=True)
        website_id = self.website_combo.currentData() or 'inventario_oculto'
        if website_id == 'mangatv':
            save_fn = lambda t, v, o: save_metadata_mangatv(t, v, o, manga_type=self.manga_type, tomos_structure=self.tomos_structure)
        elif website_id == 'olympus_scan':
            save_fn = save_metadata_olympus
        elif website_id == 'lectorknight':
            save_fn = save_metadata_lectorknight
        elif website_id == 'zonatmo':
            save_fn = lambda t, v, o: save_metadata_zonatmo(t, v, o, self.manga_type, self.tomos_structure)
        elif website_id == 'tomosmanga':
            save_fn = save_metadata_tomosmanga
        else:
            save_fn = save_metadata
        self.is_downloading = True
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.download_worker = DownloadWorker(
            downloader, selected, output_dir, self.manga_title, self.manga_type,
            self.tomos_structure, website_id, self.config, save_fn
        )
        self.download_thread = QThread()
        self.download_worker.moveToThread(self.download_thread)
        self.download_thread.started.connect(self.download_worker.run)
        self.download_worker.log_msg.connect(self.log)
        self.download_worker.progress_status.connect(self.progress_label.setText)
        self.download_worker.finished_signal.connect(self.download_complete)
        self.download_thread.start()

    def cancel_download(self):
        if self.downloader:
            self.downloader.cancel()
        self.log("Cancelando descarga...")
        self.progress_label.setText("Cancelando...")

    def download_complete(self):
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.quit()
            self.download_thread.wait()
        self.is_downloading = False
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("Listo")
        QMessageBox.information(self, "Listo", "Descarga finalizada")

    def detect_source_type_from_dir(self, directory):
        if not os.path.exists(directory):
            return None
        try:
            items = os.listdir(directory)
        except Exception:
            return None
        has_tomos = any('Tomo' in i or 'Volumen' in i for i in items)
        has_capitulos = any('Capítulo' in i or 'Capitulo' in i for i in items)
        if has_capitulos and not has_tomos:
            return 'olympus_scan'
        if has_tomos:
            return 'inventario_oculto'
        meta = self.generator.load_metadata(directory)
        if meta and meta.get('_source_type'):
            return meta['_source_type']
        if os.path.exists(os.path.join(directory, 'manhwa_metadata.json')):
            return 'olympus_scan'
        if os.path.exists(os.path.join(directory, 'manga_metadata.json')):
            return 'inventario_oculto'
        return None

    def get_title_from_directory(self, directory, metadata=None):
        if not os.path.exists(directory):
            return "Manga"
        return self.generator.get_title_from_metadata(directory, metadata or self.generator.load_metadata(directory))

    def load_content_list(self, output_dir):
        if not os.path.exists(output_dir):
            return []
        content_list = []
        try:
            items_found = os.listdir(output_dir)
        except Exception:
            return []
        root_metadata = self.generator.load_metadata(output_dir)
        if root_metadata:
            st = root_metadata.get('_source_type', 'inventario_oculto')
            title = root_metadata.get('manhwa_title') or root_metadata.get('manga_title', 'Manga')
            content_list.append({'title': title, 'folder': '.', 'source_type': st})
            return content_list
        for item in items_found:
            item_path = os.path.join(output_dir, item)
            if not os.path.isdir(item_path) or item.endswith('.cbr'):
                continue
            if item.lower() == 'tomosmanga':
                try:
                    tomosmanga_items = os.listdir(item_path)
                except Exception:
                    continue
                for manga_item in tomosmanga_items:
                    manga_item_path = os.path.join(item_path, manga_item)
                    if not os.path.isdir(manga_item_path) or manga_item.endswith('.cbr'):
                        continue
                    source_type = self.detect_source_type_from_dir(manga_item_path)
                    if source_type == 'tomosmanga':
                        title = self.get_title_from_directory(manga_item_path)
                        content_list.append({
                            'title': title,
                            'folder': os.path.join(item, manga_item),
                            'source_type': source_type
                        })
                continue
            source_type = self.detect_source_type_from_dir(item_path)
            if source_type:
                title = self.get_title_from_directory(item_path)
                content_list.append({'title': title, 'folder': item, 'source_type': source_type})
        return content_list

    def load_available_mangas(self):
        output_dir = self.config['output_dir']
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(get_project_root(), output_dir)
        self.log(f"Buscando contenido en: {output_dir}")
        if not os.path.exists(output_dir):
            QMessageBox.critical(self, "Error", f"El directorio no existe: {output_dir}")
            return
        content_list = self.load_content_list(output_dir)
        self.manga_combo.clear()
        self.mangas_data = content_list
        if content_list:
            for item in content_list:
                self.manga_combo.addItem(item['title'], item)
            self.log(f"Contenido encontrado: {len(content_list)}")
        else:
            self.log("No se encontró contenido")

    def on_manga_selected(self):
        selected_manga = self.manga_combo.currentData()
        if not selected_manga:
            return
        output_dir = self.config['output_dir']
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(get_project_root(), output_dir)
        manga_folder = output_dir if selected_manga['folder'] == '.' else os.path.join(output_dir, selected_manga['folder'])
        self.load_cbr_volumes(manga_folder)

    def load_cbr_volumes(self, manga_dir):
        self.cbr_list.clear()
        self.cbr_checkboxes = []
        if not os.path.exists(manga_dir):
            return
        items = [i for i in os.listdir(manga_dir) if os.path.isdir(os.path.join(manga_dir, i)) and not i.endswith('.cbr')]
        if not items:
            self.log("No se encontraron carpetas")
            return
        has_tomos = any('Tomo' in i or 'Volumen' in i for i in items)
        has_capitulos = any('Capítulo' in i or 'Capitulo' in i for i in items)
        source_type = self.detect_source_type_from_dir(manga_dir) or ('olympus_scan' if (has_capitulos and not has_tomos) else 'inventario_oculto')
        is_chapters = source_type in ('olympus_scan', 'mangatv', 'lectorknight', 'zonatmo')
        filtered = [i for i in items if (is_chapters and ('Capítulo' in i or 'Capitulo' in i or re.search(r'\d', i))) or (not is_chapters and ('Tomo' in i or 'Volumen' in i))]
        def sort_key(name):
            m = re.search(r'(\d+\.?\d*)', name)
            return float(m.group(1)) if m else 0.0
        filtered.sort(key=sort_key)
        for item_name in filtered:
            item_path = os.path.join(manga_dir, item_name)
            cover_path = self.selected_cover.get(item_path)
            if cover_path and os.path.exists(cover_path):
                label = f"{item_name}  (portada: {os.path.basename(cover_path)})"
            else:
                if item_path in self.selected_cover:
                    del self.selected_cover[item_path]
                label = item_name
            list_item = QListWidgetItem(label)
            list_item.setCheckState(Qt.CheckState.Checked)
            list_item.setData(Qt.ItemDataRole.UserRole, {'volume_name': item_name, 'volume_path': item_path, 'is_olympus': is_chapters})
            self.cbr_list.addItem(list_item)
            self.cbr_checkboxes.append(list_item)
        self.log(f"Tomos/Capítulos encontrados: {len(filtered)}")

    def select_all_cbr(self):
        for i in range(self.cbr_list.count()):
            self.cbr_list.item(i).setCheckState(Qt.CheckState.Checked)

    def deselect_all_cbr(self):
        for i in range(self.cbr_list.count()):
            self.cbr_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def generate_cbrs(self):
        selected = []
        for i in range(self.cbr_list.count()):
            item = self.cbr_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                data = item.data(Qt.ItemDataRole.UserRole)
                if data:
                    selected.append({'volume_name': data['volume_name'], 'volume_path': data['volume_path'], 'is_olympus': data.get('is_olympus', False)})
        if not selected:
            QMessageBox.warning(self, "Advertencia", "Selecciona al menos un tomo/capítulo")
            return
        title = self.manga_combo.currentText()
        if not title:
            QMessageBox.warning(self, "Advertencia", "Selecciona un manga")
            return
        output_dir = self.config['output_dir']
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(get_project_root(), output_dir)
        manga_data = next((m for m in self.mangas_data if m['title'] == title), None)
        if not manga_data:
            return
        manga_dir = output_dir if manga_data['folder'] == '.' else os.path.join(output_dir, manga_data['folder'])
        self.generate_cbr_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText("Generando CBRs...")
        def run():
            try:
                metadata = self.generator.load_metadata(manga_dir)
                tit = self.get_title_from_directory(manga_dir, metadata)
                generated = []
                for vol_data in selected:
                    cover = self.selected_cover.get(vol_data['volume_path'])
                    cbr_path = self.generator.generate_cbr_from_folder(vol_data['volume_path'], manga_dir, tit, metadata, cover)
                    if cbr_path:
                        generated.append(cbr_path)
                        self.log(f"[OK] CBR: {os.path.basename(cbr_path)}")
                QTimer.singleShot(0, lambda: self.generate_cbr_done(len(generated)))
            except Exception as e:
                QTimer.singleShot(0, lambda: self.log(f"[ERROR] {e}"))
                QTimer.singleShot(0, self.generate_cbr_done)
        threading.Thread(target=run, daemon=True).start()

    def generate_cbr_done(self, count=0):
        self.generate_cbr_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("Listo")
        if count:
            QMessageBox.information(self, "Éxito", f"Se generaron {count} archivos CBR")

    def _natural_sort_key(self, text):
        def convert(part):
            if part.isdigit():
                return (0, int(part))
            return (1, part.lower())
        parts = re.split(r'(\d+)', text)
        return [convert(p) for p in parts if p]

    def _list_images_in_folder(self, folder):
        """Lista todas las imágenes en una carpeta (y subcarpetas), orden: capítulo ascendente, luego imagen ascendente."""
        exts = ('.png', '.jpg', '.jpeg', '.webp', '.gif')
        paths = []
        for root, _dirs, files in os.walk(folder):
            _dirs.sort(key=lambda d: self._natural_sort_key(d))
            for f in sorted(files, key=self._natural_sort_key):
                if f.lower().startswith('temp_'):
                    continue
                if f.lower().endswith(exts):
                    paths.append(os.path.join(root, f))
        # Orden: primero por capítulo (carpeta relativa), luego por nombre de archivo (ej: Cap 93/003, 93/004, Cap 94/003...)
        def sort_key(p):
            rel = os.path.relpath(p, folder).replace('\\', '/')
            parts = rel.split('/')
            if len(parts) <= 1:
                dir_part, file_part = '', parts[0] if parts else ''
            else:
                dir_part = '/'.join(parts[:-1])
                file_part = parts[-1]
            return (self._natural_sort_key(dir_part), self._natural_sort_key(file_part))
        return sorted(paths, key=sort_key)

    def _get_checked_cbr_volumes(self):
        """Devuelve la lista de tomos con el check marcado (mismo criterio que generar CBR)."""
        out = []
        for i in range(self.cbr_list.count()):
            item = self.cbr_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                data = item.data(Qt.ItemDataRole.UserRole)
                if data:
                    out.append(data)
        return out

    def _refresh_cbr_list_cover_labels(self):
        """Actualiza el texto de cada ítem de la lista CBR según selected_cover."""
        for i in range(self.cbr_list.count()):
            item = self.cbr_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data:
                continue
            volume_name = data['volume_name']
            volume_path = data['volume_path']
            cover_path = self.selected_cover.get(volume_path)
            if cover_path and os.path.exists(cover_path):
                item.setText(f"{volume_name}  (portada: {os.path.basename(cover_path)})")
            else:
                item.setText(volume_name)

    def choose_cover_cbr(self):
        checked = self._get_checked_cbr_volumes()
        if not checked:
            QMessageBox.warning(self, "Portada", "Marca con el check los tomos/capítulos para los que quieres elegir portada.")
            return
        volumes_with_images = []
        for v in checked:
            volume_path = v['volume_path']
            if not os.path.isdir(volume_path):
                continue
            images = self._list_images_in_folder(volume_path)
            if images:
                volumes_with_images.append({**v, 'images': images})
        if not volumes_with_images:
            QMessageBox.warning(self, "Portada", "No se encontraron imágenes en los tomos seleccionados.")
            return
        dialog = CoverPickerDialog(self, volumes_with_images, self.selected_cover, self._refresh_cbr_list_cover_labels, self.log)
        dialog.exec()

    def clear_cover_cbr(self):
        checked = self._get_checked_cbr_volumes()
        if not checked:
            QMessageBox.warning(self, "Portada", "Marca con el check los tomos a los que quieres quitar la portada.")
            return
        for v in checked:
            volume_path = v['volume_path']
            volume_name = v['volume_name']
            if volume_path in self.selected_cover:
                del self.selected_cover[volume_path]
                self.log(f"Portada quitada para «{volume_name}»")
        self._refresh_cbr_list_cover_labels()

    def load_rar_mangas(self):
        output_dir = self.config['output_dir']
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(get_project_root(), output_dir)
        if not os.path.exists(output_dir):
            QMessageBox.critical(self, "Error", f"El directorio no existe: {output_dir}")
            return
        content_list = self.load_content_list(output_dir)
        self.rar_manga_combo.clear()
        self.rar_mangas_data = content_list
        if content_list:
            for item in content_list:
                self.rar_manga_combo.addItem(item['title'], item)
            self.log(f"Contenido encontrado: {len(content_list)}")
        else:
            QMessageBox.information(self, "Info", f"No se encontró contenido en:\n{output_dir}")

    def on_rar_manga_selected(self):
        selected_manga = self.rar_manga_combo.currentData()
        if not selected_manga:
            return
        output_dir = self.config['output_dir']
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(get_project_root(), output_dir)
        manga_dir = output_dir if selected_manga['folder'] == '.' else os.path.join(output_dir, selected_manga['folder'])
        cbr_list = []
        for root, dirs, files in os.walk(manga_dir):
            for f in files:
                if f.lower().endswith('.cbr'):
                    cbr_path = os.path.join(root, f)
                    manga_title, tomo_number = extract_manga_title_and_tomo(f)
                    cbr_list.append((tomo_number or 999, cbr_path, f))
        cbr_list.sort(key=lambda x: x[0])
        self.rar_list.clear()
        self.rar_checkboxes = []
        for tomo_number, cbr_path, cbr_filename in cbr_list:
            item = QListWidgetItem(f"Tomo {tomo_number:02d}: {cbr_filename}")
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, {'tomo_number': tomo_number, 'cbr_path': cbr_path, 'cbr_filename': cbr_filename})
            self.rar_list.addItem(item)
            self.rar_checkboxes.append(item)

    def select_all_rar(self):
        for i in range(self.rar_list.count()):
            self.rar_list.item(i).setCheckState(Qt.CheckState.Checked)

    def deselect_all_rar(self):
        for i in range(self.rar_list.count()):
            self.rar_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def generate_rars(self):
        selected = []
        for i in range(self.rar_list.count()):
            item = self.rar_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                data = item.data(Qt.ItemDataRole.UserRole)
                if data:
                    selected.append(data)
        if not selected:
            QMessageBox.warning(self, "Advertencia", "Selecciona al menos un CBR")
            return
        title = self.rar_manga_combo.currentText()
        if not title:
            QMessageBox.warning(self, "Advertencia", "Selecciona un manga")
            return
        tomo_range = self.tomos_per_rar_spin.value()
        output_dir = self.config['output_dir']
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(get_project_root(), output_dir)
        manga_data = next((m for m in self.rar_mangas_data if m['title'] == title), None)
        if not manga_data:
            return
        manga_dir = output_dir if manga_data['folder'] == '.' else os.path.join(output_dir, manga_data['folder'])
        self.generate_rar_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText("Generando RARs...")
        def run():
            try:
                selected_sorted = sorted(selected, key=lambda x: x['tomo_number'])
                created = []
                i = 0
                while i < len(selected_sorted):
                    group = selected_sorted[i:i + tomo_range]
                    first_tomo = group[0]['tomo_number']
                    last_tomo = group[-1]['tomo_number']
                    zip_name = create_zip_name(title, first_tomo, last_tomo)
                    zip_path = os.path.join(manga_dir, zip_name)
                    cbr_files = [cb['cbr_path'] for cb in group]
                    if create_zip_from_cbrs(cbr_files, zip_path):
                        created.append(zip_path)
                    i += tomo_range
                QTimer.singleShot(0, lambda: self.generate_rar_done(len(created)))
            except Exception as e:
                QTimer.singleShot(0, lambda: self.log(f"[ERROR] {e}"))
                QTimer.singleShot(0, self.generate_rar_done)
        threading.Thread(target=run, daemon=True).start()

    def generate_rar_done(self, count=0):
        self.generate_rar_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("Listo")
        if count:
            QMessageBox.information(self, "Completado", f"Se generaron {count} archivos RAR")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    icon_path = os.path.join(get_project_root(), 'logos', 'icono.ico')
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    win = MangaDownloaderWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
