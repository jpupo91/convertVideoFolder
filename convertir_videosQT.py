#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import subprocess
import threading
import time
import re
import json
from pathlib import Path
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QLineEdit, 
                             QFileDialog, QSpinBox, QProgressBar, QTextEdit,
                             QGroupBox, QGridLayout, QMessageBox, QComboBox,
                             QSlider, QTableWidget, QTableWidgetItem, QHeaderView,
                             QAbstractItemView, QStyledItemDelegate, QStyle, QSizePolicy)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QRect, QSize
from PyQt5.QtGui import QFont, QColor, QPainter


class EmojiDelegate(QStyledItemDelegate):
    """Delegate para renderizar texto correctamente en la columna de estado"""
    
    def paint(self, painter, option, index):
        text = index.data(Qt.DisplayRole)
        if not text:
            return
        
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            text_color = option.palette.highlightedText().color()
        else:
            text_color = option.palette.text().color()
        
        font = QFont("Arial", 11)
        painter.setFont(font)
        
        foreground = index.data(Qt.ForegroundRole)
        if foreground is not None:
            if isinstance(foreground, QColor):
                text_color = foreground
            elif hasattr(foreground, 'color'):
                text_color = foreground.color()
        
        painter.setPen(text_color)
        painter.drawText(option.rect, Qt.AlignCenter | Qt.AlignVCenter, text)
        painter.restore()
    
    def sizeHint(self, option, index):
        return QSize(130, 34)


class ConversionWorker(QThread):
    progress_updated = pyqtSignal(int)
    file_progress = pyqtSignal(str, int)
    file_completed = pyqtSignal(str, str, str)
    log_message = pyqtSignal(str)
    stats_updated = pyqtSignal(int, int, int, int, int)
    status_message = pyqtSignal(str)
    finished_signal = pyqtSignal(int, int, int, int, int)
    
    def __init__(self, archivos_info, output_dir, num_jobs, hw_key, crf, 
                 resolution, audio_bitrate, subtitle_map):
        super().__init__()
        self.archivos_info = archivos_info
        self.output_dir = output_dir
        self.num_jobs = num_jobs
        self.hw_key = hw_key
        self.crf = crf
        self.resolution = resolution
        self.audio_bitrate = audio_bitrate
        self.subtitle_map = subtitle_map
        self.running = True
        self.active_processes = []
        self.process_lock = threading.Lock()
        self.progress_lock = threading.Lock()
        self.file_progress_dict = {}
        
    def stop(self):
        self.running = False
        with self.process_lock:
            for proc in self.active_processes:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                except:
                    pass
            time.sleep(0.5)
            for proc in self.active_processes:
                try:
                    if proc.poll() is None:
                        proc.kill()
                except:
                    pass
            self.active_processes.clear()
    
    def get_video_duration(self, file_path):
        try:
            cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
                   "-show_format", str(file_path)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return float(data['format']['duration'])
        except:
            pass
        return None
    
    def convertir_archivo(self, file_path, subtitle_file):
        filename = Path(file_path).name
        output_file = Path(self.output_dir) / f"{Path(file_path).stem}_tv.mp4"
        
        if output_file.exists():
            return "SALTADO", filename, None
        
        duration = self.get_video_duration(file_path)
        
        hw_params = {
            "nvenc": ["-c:v", "h264_nvenc", "-preset", "fast", "-rc", "vbr", 
                     "-cq", str(self.crf), "-b:v", "0"],
            "amf": ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp",
                   "-qp_i", str(self.crf), "-qp_p", str(self.crf), "-qp_b", str(self.crf)],
            "qsv": ["-c:v", "h264_qsv", "-preset", "fast", "-global_quality", str(self.crf),
                   "-look_ahead", "0"],
            "videotoolbox": ["-c:v", "h264_videotoolbox", "-q:v", str(self.crf)],
        }
        
        def execute_ffmpeg(cmd):
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, 
                                   stderr=subprocess.PIPE, universal_newlines=True)
            
            with self.process_lock:
                self.active_processes.append(proc)
            
            time_pattern = re.compile(r'time=(\d+):(\d+):(\d+\.?\d*)')
            
            for line in proc.stderr:
                if not self.running:
                    break
                
                match = time_pattern.search(line)
                if match and duration:
                    hours = int(match.group(1))
                    minutes = int(match.group(2))
                    seconds = float(match.group(3))
                    current_time = hours * 3600 + minutes * 60 + seconds
                    file_percent = min(int((current_time / duration) * 100), 100)
                    
                    self.file_progress.emit(filename, file_percent)
                    
                    with self.progress_lock:
                        self.file_progress_dict[filename] = file_percent
                        total_progress = sum(self.file_progress_dict.values())
                        total_files = len(self.file_progress_dict)
                        if total_files > 0:
                            global_percent = int(total_progress / total_files)
                            self.progress_updated.emit(min(global_percent, 100))
            
            proc.wait()
            
            with self.process_lock:
                if proc in self.active_processes:
                    self.active_processes.remove(proc)
            
            return proc.returncode == 0
        
        if self.hw_key in hw_params:
            cmd = ["ffmpeg", "-i", str(file_path)]
            if subtitle_file:
                cmd.extend(["-i", str(subtitle_file)])
            if self.resolution:
                cmd.extend(["-vf", f"scale={self.resolution}"])
            cmd.extend(hw_params[self.hw_key])
            cmd.extend(["-c:a", "aac", "-b:a", f"{self.audio_bitrate}k"])
            if subtitle_file:
                cmd.extend(["-c:s", "mov_text", "-metadata:s:s:0", "language=spa"])
            cmd.extend(["-movflags", "+faststart", "-y", str(output_file), 
                       "-loglevel", "warning", "-stats"])
            
            if execute_ffmpeg(cmd):
                self.file_progress.emit(filename, 100)
                return "OK", filename, Path(subtitle_file).name if subtitle_file else None
        
        cmd = ["ffmpeg", "-i", str(file_path)]
        if subtitle_file:
            cmd.extend(["-i", str(subtitle_file)])
        if self.resolution:
            cmd.extend(["-vf", f"scale={self.resolution}"])
        cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", str(self.crf),
                   "-c:a", "aac", "-b:a", f"{self.audio_bitrate}k"])
        if subtitle_file:
            cmd.extend(["-c:s", "mov_text", "-metadata:s:s:0", "language=spa"])
        cmd.extend(["-movflags", "+faststart", "-y", str(output_file), 
                   "-loglevel", "warning", "-stats"])
        
        if execute_ffmpeg(cmd):
            self.file_progress.emit(filename, 100)
            return "OK", filename, Path(subtitle_file).name if subtitle_file else None
            
        return "ERROR", filename, None
    
    def buscar_subtitulos_auto(self, video_file):
        name = Path(video_file).stem
        parent = Path(video_file).parent
        for sub_ext in ['srt', 'ass', 'ssa', 'sub', 'vtt']:
            for pattern in [f"{name}.{sub_ext}", f"{name}.es.{sub_ext}", 
                           f"{name}.esp.{sub_ext}", f"{name}.spanish.{sub_ext}"]:
                sub_path = parent / pattern
                if sub_path.exists():
                    return str(sub_path)
        return None
    
    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        total = len(self.archivos_info)
        self.log_message.emit(f"Encontrados {total} archivos")
        
        converted = 0
        with_subs = 0
        skipped = 0
        errors = 0
        processed = 0
        start_time = time.time()
        
        with self.progress_lock:
            for info in self.archivos_info:
                self.file_progress_dict[Path(info['path']).name] = 0
        
        with ThreadPoolExecutor(max_workers=self.num_jobs) as executor:
            futures = {}
            for info in self.archivos_info:
                subtitle = self.subtitle_map.get(info['path'])
                if subtitle is None:
                    subtitle = self.buscar_subtitulos_auto(info['path'])
                
                future = executor.submit(self.convertir_archivo, info['path'], subtitle)
                futures[future] = info
            
            for future in as_completed(futures):
                if not self.running:
                    break
                
                status, filename, subtitle = future.result()
                processed += 1
                
                self.file_completed.emit(filename, status, subtitle or "")
                
                if status == "OK":
                    converted += 1
                    if subtitle:
                        with_subs += 1
                        self.log_message.emit(f"[OK] {filename} (con subtitulos: {subtitle})")
                    else:
                        self.log_message.emit(f"[OK] {filename}")
                elif status == "SALTADO":
                    skipped += 1
                    self.log_message.emit(f"[--] {filename} (ya existia)")
                else:
                    errors += 1
                    self.log_message.emit(f"[ERR] {filename} (error)")
                
                elapsed = int(time.time() - start_time)
                eta = 0
                if processed > 0 and processed < total:
                    restantes = total - processed
                    eta = int(restantes * (elapsed / processed) / self.num_jobs)
                
                self.status_message.emit(
                    f"[{processed}/{total}] {filename} | {elapsed}s | ETA: {eta}s")
                self.stats_updated.emit(total, converted, with_subs, skipped, errors)
        
        self.finished_signal.emit(total, converted, with_subs, skipped, errors)


class VideoConverterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Conversor de Videos para TV JTC")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)
        self.worker = None
        self.available_hw = {}
        self.archivos_info = []
        self.subtitle_map = {}
        self.progress_bars = {}
        self.init_ui()
        self.detect_hardware()
        
    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        title = QLabel("Conversor de Videos para TV JTC")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #2196F3; margin-bottom: 5px;")
        main_layout.addWidget(title)
        
        # ========== CONFIGURACIÓN ==========
        config_group = QGroupBox("Configuracion")
        config_layout = QGridLayout()
        config_layout.setSpacing(8)
        config_layout.setColumnStretch(1, 1)
        
        config_layout.addWidget(QLabel("Carpeta:"), 0, 0)
        self.input_dir = QLineEdit()
        self.input_dir.setPlaceholderText("Selecciona la carpeta con los videos...")
        config_layout.addWidget(self.input_dir, 0, 1)
        btn_in = QPushButton("Examinar")
        btn_in.clicked.connect(self.browse_input)
        btn_in.setStyleSheet("background-color: #4CAF50; color: white; padding: 6px; border-radius: 4px;")
        config_layout.addWidget(btn_in, 0, 2)
        
        config_layout.addWidget(QLabel("Salida:"), 1, 0)
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("Vacio = 'convertidos_tv'")
        config_layout.addWidget(self.output_dir, 1, 1)
        btn_out = QPushButton("Examinar")
        btn_out.clicked.connect(self.browse_output)
        btn_out.setStyleSheet("background-color: #4CAF50; color: white; padding: 6px; border-radius: 4px;")
        config_layout.addWidget(btn_out, 1, 2)
        
        config_layout.addWidget(QLabel("Paralelo:"), 2, 0)
        self.num_jobs = QSpinBox()
        self.num_jobs.setRange(1, 16)
        self.num_jobs.setValue(2)
        config_layout.addWidget(self.num_jobs, 2, 1)
        
        config_layout.addWidget(QLabel("Aceleracion:"), 3, 0)
        self.hardware_combo = QComboBox()
        config_layout.addWidget(self.hardware_combo, 3, 1)
        
        config_layout.addWidget(QLabel("Calidad:"), 4, 0)
        quality_widget = QWidget()
        quality_layout = QVBoxLayout(quality_widget)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.setSpacing(2)
        
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(18, 32)
        self.quality_slider.setValue(28)
        self.quality_slider.setTickPosition(QSlider.TicksBelow)
        self.quality_slider.setTickInterval(2)
        self.quality_slider.valueChanged.connect(self.update_quality_label)
        self.quality_slider.valueChanged.connect(self.recalculate_estimated_sizes)
        self.quality_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #999; height: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #f44336, stop:0.5 #ff9800, stop:1 #4CAF50);
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #2196F3; border: 1px solid #1976D2;
                width: 18px; margin: -5px 0; border-radius: 9px;
            }
        """)
        quality_layout.addWidget(self.quality_slider)
        
        slider_labels = QHBoxLayout()
        self.quality_label = QLabel("CRF 28 - Optima (recomendado para TV)")
        self.quality_label.setStyleSheet("font-weight: bold; color: #ff9800; font-size: 11px;")
        slider_labels.addWidget(self.quality_label)
        slider_labels.addStretch()
        range_label = QLabel("Alta <--> Baja")
        range_label.setStyleSheet("color: #666; font-size: 10px;")
        slider_labels.addWidget(range_label)
        quality_layout.addLayout(slider_labels)
        config_layout.addWidget(quality_widget, 4, 1)
        
        config_layout.addWidget(QLabel("Resolucion:"), 5, 0)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems([
            "Original (mantener)", "1080p (1920x1080)", 
            "720p (1280x720)", "480p (854x480)"
        ])
        self.resolution_combo.currentIndexChanged.connect(self.recalculate_estimated_sizes)
        config_layout.addWidget(self.resolution_combo, 5, 1)
        
        config_layout.addWidget(QLabel("Audio:"), 6, 0)
        self.audio_combo = QComboBox()
        self.audio_combo.addItems([
            "64 kbps (minimo)", "96 kbps (estandar TV)",
            "128 kbps (buena calidad)", "192 kbps (alta calidad)",
            "256 kbps (maxima calidad)"
        ])
        self.audio_combo.setCurrentIndex(1)
        self.audio_combo.currentIndexChanged.connect(self.recalculate_estimated_sizes)
        config_layout.addWidget(self.audio_combo, 6, 1)
        
        config_group.setLayout(config_layout)
        config_group.setStyleSheet(self.groupbox_style())
        main_layout.addWidget(config_group)
        
        # ========== ESTADO DE ACELERACIÓN ==========
        hw_status_group = QGroupBox("Aceleracion Detectada")
        hw_status_layout = QHBoxLayout()
        hw_status_layout.setSpacing(10)
        
        self.hw_status_labels = {}
        for key, name in [('nvenc', 'NVIDIA NVENC'), ('amf', 'AMD AMF/VCE'),
                         ('qsv', 'Intel QSV'), ('videotoolbox', 'VideoToolbox'),
                         ('cpu', 'CPU (libx264)')]:
            lbl = QLabel(f"[...] {name}")
            lbl.setStyleSheet("color: #999; font-size: 10px; padding: 4px 8px; "
                            "background-color: #f0f0f0; border-radius: 4px;")
            lbl.setAlignment(Qt.AlignCenter)
            hw_status_layout.addWidget(lbl)
            self.hw_status_labels[key] = lbl
        
        hw_status_group.setLayout(hw_status_layout)
        hw_status_group.setStyleSheet(self.groupbox_style("#4CAF50"))
        main_layout.addWidget(hw_status_group)
        
        # ========== TABLA DE ARCHIVOS ==========
        files_group = QGroupBox("Archivos a Convertir")
        files_layout = QVBoxLayout()
        
        table_buttons = QHBoxLayout()
        
        self.files_count_label = QLabel("0 archivos encontrados")
        self.files_count_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        table_buttons.addWidget(self.files_count_label)
        
        self.total_estimated_label = QLabel("Tamaño estimado total: 0 MB")
        self.total_estimated_label.setStyleSheet("font-weight: bold; color: #ff9800;")
        table_buttons.addWidget(self.total_estimated_label)
        
        table_buttons.addStretch()
        
        btn_auto_subs = QPushButton("Detectar subtitulos automaticamente")
        btn_auto_subs.clicked.connect(self.auto_detect_subtitles)
        btn_auto_subs.setStyleSheet("background-color: #2196F3; color: white; padding: 6px 12px; border-radius: 4px;")
        table_buttons.addWidget(btn_auto_subs)
        
        btn_clear_subs = QPushButton("Quitar todos los subtitulos")
        btn_clear_subs.clicked.connect(self.clear_all_subtitles)
        btn_clear_subs.setStyleSheet("background-color: #ff9800; color: white; padding: 6px 12px; border-radius: 4px;")
        table_buttons.addWidget(btn_clear_subs)
        
        files_layout.addLayout(table_buttons)
        
        self.files_table = QTableWidget()
        self.files_table.setColumnCount(8)
        self.files_table.setHorizontalHeaderLabels([
            "Archivo", "Tamano", "Duracion", "Subtitulo", "Tam. Estimado", "Acciones", "Progreso", "Estado"
        ])
        
        # Columnas flexibles que absorben el espacio sobrante
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)      # Archivo
        self.files_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)      # Subtitulo
        
        # Columnas con tamaño automático al contenido
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Tamano
        self.files_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Duracion
        self.files_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Tam. Estimado
        
        # Columnas con ancho fijo
        self.files_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)        # Acciones
        self.files_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Fixed)        # Progreso
        self.files_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Fixed)        # Estado
        
        self.files_table.setColumnWidth(5, 95)   # Acciones
        self.files_table.setColumnWidth(6, 150)  # Progreso
        self.files_table.setColumnWidth(7, 130)  # Estado
        
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        self.files_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.files_table.setMinimumHeight(150)
        self.files_table.verticalHeader().setDefaultSectionSize(42)
        
        self.files_table.horizontalHeader().setStretchLastSection(False)
        
        self.emoji_delegate = EmojiDelegate()
        self.files_table.setItemDelegateForColumn(7, self.emoji_delegate)
        
        self.files_table.setStyleSheet("""
            QTableWidget { 
                border: 1px solid #ccc; 
                gridline-color: #e0e0e0;
                font-size: 11px;
            }
            QTableWidget::item { 
                padding: 8px 6px;
            }
            QHeaderView::section { 
                background-color: #2196F3; 
                color: white; 
                padding: 8px 6px;
                font-weight: bold;
                border: none;
            }
        """)
        files_layout.addWidget(self.files_table)
        
        files_group.setLayout(files_layout)
        files_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        files_group.setStyleSheet(self.groupbox_style())
        
        main_layout.addWidget(files_group, stretch=1)
        
        # ========== PROGRESO GLOBAL ==========
        progress_group = QGroupBox("Progreso Global")
        progress_layout = QVBoxLayout()
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumHeight(28)
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 2px solid #2196F3; border-radius: 5px; 
                          text-align: center; font-weight: bold; }
            QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #4CAF50, stop:1 #66BB6A); border-radius: 5px; }
        """)
        progress_layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("Listo para iniciar")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 11px; color: #666;")
        progress_layout.addWidget(self.status_label)
        
        stats_layout = QHBoxLayout()
        self.stats_labels = {}
        for key, text in [('total', 'Total: 0'), ('converted', 'Convertidos: 0'),
                         ('subs', 'Con subtitulos: 0'), ('skipped', 'Saltados: 0'),
                         ('errors', 'Errores: 0')]:
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 10px; padding: 4px; background-color: #f5f5f5; border-radius: 3px;")
            stats_layout.addWidget(lbl)
            self.stats_labels[key] = lbl
        
        progress_layout.addLayout(stats_layout)
        progress_group.setLayout(progress_layout)
        progress_group.setStyleSheet(self.groupbox_style())
        main_layout.addWidget(progress_group)
        
        # ========== LOG ==========
        log_group = QGroupBox("Registro")
        log_layout = QVBoxLayout()
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setStyleSheet("""
            QTextEdit { border: 2px solid #ccc; border-radius: 5px; padding: 5px;
                       font-family: 'Courier New', monospace; font-size: 10px;
                       background-color: #fafafa; }
        """)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        log_group.setStyleSheet(self.groupbox_style())
        main_layout.addWidget(log_group)
        
        # ========== BOTONES ==========
        button_layout = QHBoxLayout()
        
        self.btn_start = QPushButton("Iniciar Conversion")
        self.btn_start.clicked.connect(self.start_conversion)
        self.btn_start.setMinimumHeight(45)
        self.btn_start.setStyleSheet("""
            QPushButton { background-color: #2196F3; color: white; font-weight: bold;
                         padding: 12px 24px; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background-color: #1976D2; }
            QPushButton:disabled { background-color: #ccc; }
        """)
        button_layout.addWidget(self.btn_start)
        
        self.btn_stop = QPushButton("Detener")
        self.btn_stop.clicked.connect(self.stop_conversion)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setMinimumHeight(45)
        self.btn_stop.setStyleSheet("""
            QPushButton { background-color: #f44336; color: white; font-weight: bold;
                         padding: 12px 24px; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background-color: #d32f2f; }
            QPushButton:disabled { background-color: #ccc; }
        """)
        button_layout.addWidget(self.btn_stop)
        
        main_layout.addLayout(button_layout)
    
    def groupbox_style(self, color="#2196F3"):
        return f"""
            QGroupBox {{ font-weight: bold; border: 2px solid {color}; 
                       border-radius: 8px; margin-top: 10px; padding-top: 12px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; 
                              padding: 0 5px; color: {color}; }}
        """
    
    def estimate_bitrate(self, crf, resolution_text):
        """Estima el bitrate de video basado en CRF y resolución"""
        # Bitrates base para 1080p
        base_bitrates = {
            18: 10000,  # 10 Mbps
            20: 8000,   # 8 Mbps
            22: 6000,   # 6 Mbps
            24: 4500,   # 4.5 Mbps
            26: 3500,   # 3.5 Mbps
            28: 2500,   # 2.5 Mbps
            30: 1800,   # 1.8 Mbps
            32: 1200    # 1.2 Mbps
        }
        
        # Interpolar para valores intermedios
        if crf in base_bitrates:
            base_bitrate = base_bitrates[crf]
        else:
            # Encontrar los dos valores más cercanos
            lower_crf = max([k for k in base_bitrates.keys() if k <= crf])
            upper_crf = min([k for k in base_bitrates.keys() if k >= crf])
            
            if lower_crf == upper_crf:
                base_bitrate = base_bitrates[lower_crf]
            else:
                # Interpolación lineal
                ratio = (crf - lower_crf) / (upper_crf - lower_crf)
                base_bitrate = int(base_bitrates[lower_crf] + 
                                  (base_bitrates[upper_crf] - base_bitrates[lower_crf]) * ratio)
        
        # Ajustar según resolución
        if "720p" in resolution_text:
            base_bitrate = int(base_bitrate * 0.6)  # 720p usa ~60% del bitrate de 1080p
        elif "480p" in resolution_text:
            base_bitrate = int(base_bitrate * 0.35) # 480p usa ~35% del bitrate de 1080p
        
        return base_bitrate
    
    def estimate_file_size(self, duration, crf, resolution_text, audio_bitrate_kbps):
        """Estima el tamaño del archivo en bytes"""
        if duration is None or duration <= 0:
            return 0
        
        # Bitrate de video en kbps
        video_bitrate_kbps = self.estimate_bitrate(crf, resolution_text)
        
        # Bitrate total = video + audio
        total_bitrate_kbps = video_bitrate_kbps + audio_bitrate_kbps
        
        # Tamaño en bytes = (bitrate * duración) / 8
        # bitrate está en kbps, duración en segundos
        size_bytes = (total_bitrate_kbps * 1000 * duration) / 8
        
        return int(size_bytes)
    
    def recalculate_estimated_sizes(self):
        """Recalcula los tamaños estimados cuando cambian los parámetros"""
        if not self.archivos_info:
            return
        
        crf = self.quality_slider.value()
        resolution_text = self.resolution_combo.currentText()
        audio_text = self.audio_combo.currentText()
        audio_bitrate = int(audio_text.split()[0])
        
        total_estimated = 0
        
        for row, info in enumerate(self.archivos_info):
            estimated_size = self.estimate_file_size(
                info['duration'], crf, resolution_text, audio_bitrate
            )
            info['estimated_size'] = estimated_size
            total_estimated += estimated_size
            
            # Actualizar la tabla
            size_item = QTableWidgetItem(self.format_size(estimated_size))
            size_item.setTextAlignment(Qt.AlignCenter)
            size_item.setForeground(QColor("#ff9800"))
            self.files_table.setItem(row, 4, size_item)
        
        # Actualizar el label de total estimado
        self.total_estimated_label.setText(f"Tamaño estimado total: {self.format_size(total_estimated)}")
    
    def update_quality_label(self, value):
        descriptions = {
            (18, 20): ("Alta (archivos grandes)", "#4CAF50"),
            (21, 23): ("Media-Alta (buena calidad)", "#8BC34A"),
            (24, 26): ("Media (balance calidad/tamano)", "#CDDC39"),
            (27, 29): ("Optima (recomendado para TV)", "#ff9800"),
            (30, 32): ("Baja (archivos pequenos)", "#f44336")
        }
        for (min_v, max_v), (desc, color) in descriptions.items():
            if min_v <= value <= max_v:
                self.quality_label.setText(f"CRF {value} - {desc}")
                self.quality_label.setStyleSheet(f"font-weight: bold; color: {color}; font-size: 11px;")
                break
    
    def format_size(self, size_bytes):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"
    
    def format_duration(self, seconds):
        if seconds is None:
            return "--:--:--"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
    
    def get_video_duration(self, file_path):
        try:
            cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
                   "-show_format", str(file_path)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return float(data['format']['duration'])
        except:
            pass
        return None
    
    def detect_hardware(self):
        self.hardware_combo.clear()
        self.available_hw = {}
        
        for key, lbl in self.hw_status_labels.items():
            name = lbl.text().split(' ', 1)[1] if ' ' in lbl.text() else key
            lbl.setText(f"[...] {name}")
            lbl.setStyleSheet("color: #999; font-size: 10px; padding: 4px 8px; "
                            "background-color: #f0f0f0; border-radius: 4px;")
        
        try:
            result = subprocess.run(["ffmpeg", "-encoders"], 
                                  capture_output=True, text=True, timeout=5)
            output = result.stdout
            
            checks = {
                'nvenc': ('h264_nvenc', 'NVIDIA NVENC'),
                'amf': ('h264_amf', 'AMD AMF/VCE'),
                'qsv': ('h264_qsv', 'Intel QSV'),
                'videotoolbox': ('h264_videotoolbox', 'VideoToolbox'),
            }
            
            for key, (encoder_name, display_name) in checks.items():
                if encoder_name in output:
                    self.available_hw[key] = display_name
                    self.hw_status_labels[key].setText(f"[OK] {display_name}")
                    self.hw_status_labels[key].setStyleSheet(
                        "color: white; font-size: 10px; padding: 4px 8px; "
                        "background-color: #4CAF50; border-radius: 4px; font-weight: bold;")
                    self.hardware_combo.addItem(f"{display_name} (GPU)", key)
                else:
                    self.hw_status_labels[key].setText(f"[--] {display_name}")
                    self.hw_status_labels[key].setStyleSheet(
                        "color: #666; font-size: 10px; padding: 4px 8px; "
                        "background-color: #ffebee; border-radius: 4px;")
            
            self.hw_status_labels['cpu'].setText("[OK] CPU (libx264)")
            self.hw_status_labels['cpu'].setStyleSheet(
                "color: white; font-size: 10px; padding: 4px 8px; "
                "background-color: #2196F3; border-radius: 4px; font-weight: bold;")
            
        except Exception as e:
            self.hw_status_labels['cpu'].setText("[OK] CPU (libx264)")
            self.hw_status_labels['cpu'].setStyleSheet(
                "color: white; font-size: 10px; padding: 4px 8px; "
                "background-color: #2196F3; border-radius: 4px; font-weight: bold;")
        
        self.hardware_combo.addItem("CPU (libx264)", "cpu")
        if self.available_hw:
            self.hardware_combo.setCurrentIndex(0)
        else:
            self.hardware_combo.setCurrentIndex(self.hardware_combo.count() - 1)
    
    def browse_input(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de entrada")
        if folder:
            self.input_dir.setText(folder)
            if not self.output_dir.text():
                self.output_dir.setText(os.path.join(folder, "convertidos_tv"))
            self.load_files(folder)
    
    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de salida")
        if folder:
            self.output_dir.setText(folder)
    
    def load_files(self, folder):
        self.files_table.setRowCount(0)
        self.archivos_info = []
        self.subtitle_map = {}
        self.progress_bars = {}
        
        extensions = ['*.mp4', '*.avi', '*.mkv', '*.mov', '*.wmv', '*.flv', 
                     '*.webm', '*.m4v', '*.mpg', '*.mpeg', '*.3gp', '*.ts']
        
        archivos = []
        folder_path = Path(folder)
        for ext in extensions:
            archivos.extend(folder_path.glob(ext))
            archivos.extend(folder_path.glob(ext.upper()))
        archivos = sorted(list(set(archivos)))
        
        self.log_text.clear()
        self.add_log(f"Cargando archivos desde: {folder}")
        
        for file_path in archivos:
            size = file_path.stat().st_size
            duration = self.get_video_duration(file_path)
            auto_subtitle = self.buscar_subtitulos_auto(file_path)
            
            info = {
                'path': str(file_path),
                'name': file_path.name,
                'size': size,
                'duration': duration,
                'auto_subtitle': auto_subtitle,
                'estimated_size': 0
            }
            self.archivos_info.append(info)
            
            if auto_subtitle:
                self.subtitle_map[str(file_path)] = auto_subtitle
        
        self.populate_table()
        self.recalculate_estimated_sizes()
        self.files_count_label.setText(f"{len(archivos)} archivos encontrados")
        self.add_log(f"{len(archivos)} archivos cargados")
    
    def populate_table(self):
        self.files_table.setRowCount(len(self.archivos_info))
        
        for row, info in enumerate(self.archivos_info):
            name_item = QTableWidgetItem(info['name'])
            name_item.setToolTip(info['path'])
            self.files_table.setItem(row, 0, name_item)
            
            size_item = QTableWidgetItem(self.format_size(info['size']))
            size_item.setTextAlignment(Qt.AlignCenter)
            self.files_table.setItem(row, 1, size_item)
            
            duration_item = QTableWidgetItem(self.format_duration(info['duration']))
            duration_item.setTextAlignment(Qt.AlignCenter)
            self.files_table.setItem(row, 2, duration_item)
            
            subtitle = self.subtitle_map.get(info['path'])
            if subtitle:
                sub_item = QTableWidgetItem(Path(subtitle).name)
                sub_item.setForeground(QColor("#4CAF50"))
                sub_item.setToolTip(subtitle)
            else:
                sub_item = QTableWidgetItem("-- Sin subtitulo --")
                sub_item.setForeground(QColor("#999"))
            self.files_table.setItem(row, 3, sub_item)
            
            # Columna 4: Tamaño estimado (se llena en recalculate_estimated_sizes)
            est_item = QTableWidgetItem("--")
            est_item.setTextAlignment(Qt.AlignCenter)
            est_item.setForeground(QColor("#ff9800"))
            self.files_table.setItem(row, 4, est_item)
            
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(6, 4, 6, 4)
            actions_layout.setSpacing(6)
            
            btn_edit = QPushButton("✎")
            btn_edit.setToolTip("Seleccionar subtitulo manualmente")
            btn_edit.setFixedSize(36, 34)
            btn_edit.setFont(QFont("DejaVu Sans", 16))
            btn_edit.setCursor(Qt.PointingHandCursor)
            btn_edit.setStyleSheet("""
                QPushButton { 
                    background-color: #2196F3; 
                    color: white; 
                    border-radius: 4px; 
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #1976D2; }
                QPushButton:pressed { background-color: #0D47A1; }
            """)
            btn_edit.clicked.connect(lambda checked, r=row: self.edit_subtitle(r))
            actions_layout.addWidget(btn_edit)
            
            btn_clear = QPushButton("✕")
            btn_clear.setToolTip("Quitar subtitulo asignado")
            btn_clear.setFixedSize(36, 34)
            btn_clear.setFont(QFont("DejaVu Sans", 16))
            btn_clear.setCursor(Qt.PointingHandCursor)
            btn_clear.setStyleSheet("""
                QPushButton { 
                    background-color: #f44336; 
                    color: white; 
                    border-radius: 4px; 
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #d32f2f; }
                QPushButton:pressed { background-color: #b71c1c; }
            """)
            btn_clear.clicked.connect(lambda checked, r=row: self.clear_subtitle(r))
            actions_layout.addWidget(btn_clear)
            
            self.files_table.setCellWidget(row, 5, actions_widget)
            
            progress_bar = QProgressBar()
            progress_bar.setRange(0, 100)
            progress_bar.setValue(0)
            progress_bar.setTextVisible(True)
            progress_bar.setFixedHeight(24)
            progress_bar.setStyleSheet("""
                QProgressBar { 
                    border: 1px solid #ccc; 
                    border-radius: 3px; 
                    text-align: center;
                    font-size: 10px;
                    font-weight: bold;
                }
                QProgressBar::chunk { 
                    background-color: #2196F3;
                    border-radius: 3px;
                }
            """)
            self.files_table.setCellWidget(row, 6, progress_bar)
            self.progress_bars[info['name']] = progress_bar
            
            status_item = QTableWidgetItem("[...] Pendiente")
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setForeground(QColor("#666"))
            self.files_table.setItem(row, 7, status_item)
    
    def edit_subtitle(self, row):
        info = self.archivos_info[row]
        file_path = Path(info['path'])
        
        subtitle_file, _ = QFileDialog.getOpenFileName(
            self,
            f"Seleccionar subtitulo para: {file_path.name}",
            str(file_path.parent),
            "Archivos de subtitulos (*.srt *.ass *.ssa *.sub *.vtt);;Todos los archivos (*)"
        )
        
        if subtitle_file:
            self.subtitle_map[info['path']] = subtitle_file
            sub_item = QTableWidgetItem(Path(subtitle_file).name)
            sub_item.setForeground(QColor("#4CAF50"))
            sub_item.setToolTip(subtitle_file)
            self.files_table.setItem(row, 3, sub_item)
            self.add_log(f"{file_path.name} -> {Path(subtitle_file).name}")
    
    def clear_subtitle(self, row):
        info = self.archivos_info[row]
        if info['path'] in self.subtitle_map:
            del self.subtitle_map[info['path']]
            sub_item = QTableWidgetItem("-- Sin subtitulo --")
            sub_item.setForeground(QColor("#999"))
            self.files_table.setItem(row, 3, sub_item)
            self.add_log(f"Subtitulo removido de {Path(info['path']).name}")
    
    def auto_detect_subtitles(self):
        count = 0
        for row, info in enumerate(self.archivos_info):
            auto_sub = self.buscar_subtitulos_auto(Path(info['path']))
            if auto_sub:
                self.subtitle_map[info['path']] = auto_sub
                sub_item = QTableWidgetItem(Path(auto_sub).name)
                sub_item.setForeground(QColor("#4CAF50"))
                sub_item.setToolTip(auto_sub)
                self.files_table.setItem(row, 3, sub_item)
                count += 1
        
        self.add_log(f"Detectados {count} subtitulos automaticamente")
        QMessageBox.information(self, "Deteccion completa", 
                               f"Se encontraron {count} subtitulos emparejados.")
    
    def clear_all_subtitles(self):
        self.subtitle_map.clear()
        for row in range(len(self.archivos_info)):
            sub_item = QTableWidgetItem("-- Sin subtitulo --")
            sub_item.setForeground(QColor("#999"))
            self.files_table.setItem(row, 3, sub_item)
        self.add_log("Todos los subtitulos removidos")
    
    def buscar_subtitulos_auto(self, video_file):
        name = Path(video_file).stem
        parent = Path(video_file).parent
        for sub_ext in ['srt', 'ass', 'ssa', 'sub', 'vtt']:
            for pattern in [f"{name}.{sub_ext}", f"{name}.es.{sub_ext}", 
                           f"{name}.esp.{sub_ext}", f"{name}.spanish.{sub_ext}"]:
                sub_path = parent / pattern
                if sub_path.exists():
                    return str(sub_path)
        return None
    
    def add_log(self, msg):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())
    
    def start_conversion(self):
        if not self.archivos_info:
            QMessageBox.warning(self, "Sin archivos", "Primero selecciona una carpeta con videos")
            return
        
        input_dir = self.input_dir.text()
        output_dir = self.output_dir.text()
        
        if not output_dir:
            output_dir = os.path.join(input_dir, "convertidos_tv")
        os.makedirs(output_dir, exist_ok=True)
        
        num_jobs = self.num_jobs.value()
        crf = self.quality_slider.value()
        hw_key = self.hardware_combo.currentData()
        hw_name = self.hardware_combo.currentText()
        
        res_text = self.resolution_combo.currentText()
        if "1080p" in res_text:
            resolution = "1920:1080"
        elif "720p" in res_text:
            resolution = "1280:720"
        elif "480p" in res_text:
            resolution = "854:480"
        else:
            resolution = None
        
        audio_text = self.audio_combo.currentText()
        audio_bitrate = audio_text.split()[0]
        
        self.log_text.clear()
        self.add_log(f"Iniciando conversion...")
        self.add_log(f"Entrada: {input_dir}")
        self.add_log(f"Salida: {output_dir}")
        self.add_log(f"Aceleracion: {hw_name}")
        self.add_log(f"Calidad: CRF {crf}")
        self.add_log(f"Resolucion: {res_text}")
        self.add_log(f"Audio: {audio_text}")
        self.add_log(f"Paralelo: {num_jobs} trabajo(s)")
        self.add_log(f"Archivos: {len(self.archivos_info)}")
        self.add_log("")
        
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        
        for row in range(len(self.archivos_info)):
            progress_bar = self.files_table.cellWidget(row, 6)
            progress_bar.setValue(0)
            progress_bar.setStyleSheet("""
                QProgressBar { 
                    border: 1px solid #ccc; 
                    border-radius: 3px; 
                    text-align: center;
                    font-size: 10px;
                    font-weight: bold;
                }
                QProgressBar::chunk { 
                    background-color: #2196F3;
                    border-radius: 3px;
                }
            """)
            
            status_item = QTableWidgetItem("[...] Procesando...")
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setForeground(QColor("#2196F3"))
            self.files_table.setItem(row, 7, status_item)
        
        self.worker = ConversionWorker(
            self.archivos_info, output_dir, num_jobs, hw_key, crf,
            resolution, audio_bitrate, self.subtitle_map.copy()
        )
        
        self.worker.progress_updated.connect(self.progress_bar.setValue)
        self.worker.file_progress.connect(self.update_file_progress)
        self.worker.file_completed.connect(self.on_file_completed)
        self.worker.log_message.connect(self.add_log)
        self.worker.stats_updated.connect(self.update_stats)
        self.worker.status_message.connect(self.status_label.setText)
        self.worker.finished_signal.connect(self.conversion_finished)
        
        self.worker.start()
    
    def stop_conversion(self):
        if self.worker:
            self.add_log("\nDeteniendo conversion...")
            self.btn_stop.setEnabled(False)
            self.worker.stop()
    
    def update_file_progress(self, filename, percent):
        if filename in self.progress_bars:
            progress_bar = self.progress_bars[filename]
            progress_bar.setValue(percent)
            
            if percent < 30:
                color = "#2196F3"
            elif percent < 70:
                color = "#ff9800"
            else:
                color = "#4CAF50"
            
            progress_bar.setStyleSheet(f"""
                QProgressBar {{ 
                    border: 1px solid #ccc; 
                    border-radius: 3px; 
                    text-align: center;
                    font-size: 10px;
                    font-weight: bold;
                }}
                QProgressBar::chunk {{ 
                    background-color: {color};
                    border-radius: 3px;
                }}
            """)
    
    def on_file_completed(self, filename, status, subtitle):
        for row in range(self.files_table.rowCount()):
            item = self.files_table.item(row, 0)
            if item and item.text() == filename:
                progress_bar = self.files_table.cellWidget(row, 6)
                
                if status == "OK":
                    progress_bar.setValue(100)
                    progress_bar.setStyleSheet("""
                        QProgressBar { 
                            border: 1px solid #4CAF50; 
                            border-radius: 3px; 
                            text-align: center;
                            font-size: 10px;
                            font-weight: bold;
                        }
                        QProgressBar::chunk { 
                            background-color: #4CAF50;
                            border-radius: 3px;
                        }
                    """)
                    
                    if subtitle:
                        status_item = QTableWidgetItem("[OK] Con subtitulos")
                        status_item.setForeground(QColor("#4CAF50"))
                    else:
                        status_item = QTableWidgetItem("[OK] OK")
                        status_item.setForeground(QColor("#4CAF50"))
                elif status == "SALTADO":
                    progress_bar.setValue(100)
                    progress_bar.setStyleSheet("""
                        QProgressBar { 
                            border: 1px solid #ff9800; 
                            border-radius: 3px; 
                            text-align: center;
                            font-size: 10px;
                            font-weight: bold;
                        }
                        QProgressBar::chunk { 
                            background-color: #ff9800;
                            border-radius: 3px;
                        }
                    """)
                    status_item = QTableWidgetItem("[--] Saltado")
                    status_item.setForeground(QColor("#ff9800"))
                else:
                    progress_bar.setStyleSheet("""
                        QProgressBar { 
                            border: 1px solid #f44336; 
                            border-radius: 3px; 
                            text-align: center;
                            font-size: 10px;
                            font-weight: bold;
                        }
                        QProgressBar::chunk { 
                            background-color: #f44336;
                            border-radius: 3px;
                        }
                    """)
                    status_item = QTableWidgetItem("[ERR] Error")
                    status_item.setForeground(QColor("#f44336"))
                
                status_item.setTextAlignment(Qt.AlignCenter)
                self.files_table.setItem(row, 7, status_item)
                break
    
    def update_stats(self, total, converted, subs, skipped, errors):
        self.stats_labels['total'].setText(f'Total: {total}')
        self.stats_labels['converted'].setText(f'Convertidos: {converted}')
        self.stats_labels['subs'].setText(f'Con subtitulos: {subs}')
        self.stats_labels['skipped'].setText(f'Saltados: {skipped}')
        self.stats_labels['errors'].setText(f'Errores: {errors}')
    
    def conversion_finished(self, total, converted, subs, skipped, errors):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setValue(100)
        
        self.add_log("")
        self.add_log("=" * 50)
        self.add_log("CONVERSION COMPLETADA")
        self.add_log("=" * 50)
        self.add_log(f"Total: {total}")
        self.add_log(f"Convertidos: {converted}")
        self.add_log(f"  Con subtitulos: {subs}")
        self.add_log(f"Saltados: {skipped}")
        self.add_log(f"Errores: {errors}")
        
        QMessageBox.information(self, "Conversion completada",
                              f"Conversion completada\n\n"
                              f"Total: {total}\n"
                              f"Convertidos: {converted}\n"
                              f"Con subtitulos: {subs}\n"
                              f"Saltados: {skipped}\n"
                              f"Errores: {errors}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = VideoConverterApp()
    window.show()
    sys.exit(app.exec_())
