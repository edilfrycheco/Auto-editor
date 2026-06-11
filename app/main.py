import sys
import os
import webbrowser
import yaml
import time

log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "launcher_error.log")

try:
    from PySide6.QtWidgets import (QApplication, QMainWindow, QLabel, QVBoxLayout, 
                                   QWidget, QComboBox, QPushButton, QFileDialog, 
                                   QTextEdit, QProgressBar, QHBoxLayout, QMessageBox,
                                   QSlider, QCheckBox, QGroupBox, QFormLayout, QTabWidget,
                                   QScrollArea, QRadioButton, QButtonGroup)
    from PySide6.QtCore import Qt, QThread, Signal, QTimer
    import json
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from core.resolve_io import ResolveIO
    from core.transcribe import Transcriber
    from core.align import AlignmentEngine
    from core.html_report import generate_html_report
    from core.segments import SegmentsBuilder
except Exception as e:
    import traceback
    with open(log_path, "a") as f:
        f.write(f"Error importando modulos: {e}\n{traceback.format_exc()}\n")
    sys.exit(1)

class AnalysisWorker(QThread):
    log_msg = Signal(str)
    progress_val = Signal(int)
    analysis_finished = Signal(dict, list)
    error = Signal(str)

    def __init__(self, video_path, script_path):
        super().__init__()
        self.video_path = video_path
        self.script_path = script_path

    def run(self):
        try:
            self.log_msg.emit("Cargando configuración para análisis...")
            self.progress_val.emit(5)
            
            config = {}
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    
            self.log_msg.emit(f"1. Preparando transcripción del video...")
            transcriber = Transcriber(config_path)
            
            def log_progress(msg):
                self.log_msg.emit(f" > {msg}")
                
            transcript_data = transcriber.transcribe(self.video_path, progress_callback=log_progress)
            self.progress_val.emit(60)
            
            transcript_words = []
            for segment in transcript_data.get("segments", []):
                for word in segment.get("words", []):
                    if "word" in word:
                        transcript_words.append(word)
                        
            if not transcript_words:
                self.error.emit("No se detectaron palabras en la transcripción.")
                return
                
            self.log_msg.emit("2. Leyendo guion...")
            self.progress_val.emit(65)
            with open(self.script_path, "r", encoding="utf-8") as f:
                script_text = f.read()
                
            self.log_msg.emit(f"3. Alineando guion vs audio ({len(transcript_words)} palabras)...")
            engine = AlignmentEngine(config)
            align_results = engine.align(script_text, transcript_words, progress_callback=log_progress)
            self.progress_val.emit(100)
            
            self.log_msg.emit(f"   - Tomas duplicadas (Retakes): {len(align_results.get('retake_groups', []))}")
            self.log_msg.emit(f"   - Tramos fuera de guion: {len(align_results.get('unmatched_segments', []))}")
            self.log_msg.emit("¡Análisis completado en memoria!")
            
            self.analysis_finished.emit(align_results, transcript_words)
            
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            self.log_msg.emit(f"ERROR CRITICO:\n{err}")
            self.error.emit(str(e))

class AutoEditorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Auto Editor V3 - RESOLVE INTEGRATION")
        self.resize(750, 650)
        self.setStyleSheet("background-color: #1a1a1a; color: white;")
        
        self.align_results = None
        self.transcript_words = None
        self.script_path = None
        
        main_widget = QWidget()
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        
        # Header
        title = QLabel("Auto Editor")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #4da6ff; margin-bottom: 5px;")
        main_layout.addWidget(title)
        
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabWidget::pane { border: 1px solid #444; } QTabBar::tab { background: #333; padding: 8px; margin-right: 2px; } QTabBar::tab:selected { background: #0056b3; font-weight: bold; }")
        main_layout.addWidget(self.tabs)
        
        self.init_cortes_tab()
        self.init_zooms_tab()
        
        # Log Area shared
        main_layout.addWidget(QLabel("Logs:"))
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet("background-color: #000; color: #0f0; font-family: monospace;")
        main_layout.addWidget(self.log_area)
        
        self.check_dependencies()
        self.load_resolve_clips()

    def init_cortes_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        self.tabs.addTab(tab, "Cortes y Ensamblaje")
        
        # File selection
        layout.addWidget(QLabel("Paso 1: Archivos"))
        file_layout = QFormLayout()
        self.clip_combo = QComboBox()
        self.clip_combo.setStyleSheet("padding: 5px; background-color: #2d2d30;")
        file_layout.addRow("Clip de Video:", self.clip_combo)
        
        script_layout = QHBoxLayout()
        self.script_lbl = QLabel("Ningún archivo seleccionado")
        self.script_lbl.setStyleSheet("color: #aaa;")
        script_btn = QPushButton("Cargar guion...")
        script_btn.setStyleSheet("background-color: #444; padding: 5px;")
        script_btn.clicked.connect(self.load_script)
        script_layout.addWidget(self.script_lbl)
        script_layout.addWidget(script_btn)
        file_layout.addRow("Guion (.txt):", script_layout)
        layout.addLayout(file_layout)
        
        self.btn_analyze = QPushButton("1. Analizar")
        self.btn_analyze.setStyleSheet("background-color: #d39e00; color: white; font-weight: bold; padding: 10px; margin-top: 10px;")
        self.btn_analyze.clicked.connect(self.run_analyze)
        layout.addWidget(self.btn_analyze)
        
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setStyleSheet("QProgressBar { border: 1px solid #444; border-radius: 5px; text-align: center; } QProgressBar::chunk { background-color: #4da6ff; }")
        layout.addWidget(self.progress)
        
        self.current_segments = None
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(600)
        self.preview_timer.timeout.connect(self._auto_preview)
        
        # Opciones de Corte
        options_group = QGroupBox("Paso 2: Opciones de Corte y Previsualización")
        options_group.setStyleSheet("QGroupBox { border: 1px solid #444; margin-top: 5px; padding-top: 15px; }")
        opt_layout = QVBoxLayout()
        options_group.setLayout(opt_layout)
        
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
        config_data = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f)
        padding_cfg = config_data.get("padding", {})
        
        form_layout = QFormLayout()
        def create_slider(label_text, min_val, max_val, default_val, tooltip=""):
            slider = QSlider(Qt.Horizontal)
            slider.setRange(min_val, max_val)
            slider.setValue(default_val)
            if tooltip:
                slider.setToolTip(tooltip)
            val_lbl = QLabel(f"{default_val} ms")
            val_lbl.setFixedWidth(60)
            
            def on_change(v, l=val_lbl):
                l.setText(f"{v} ms")
                if self.btn_preview.isEnabled():
                    self.preview_timer.start()
                    
            slider.valueChanged.connect(on_change)
            
            row_layout = QHBoxLayout()
            row_layout.addWidget(slider)
            row_layout.addWidget(val_lbl)
            
            lbl = QLabel(label_text)
            if tooltip:
                lbl.setToolTip(tooltip)
                
            form_layout.addRow(lbl, row_layout)
            return slider
            
        self.slider_pre = create_slider("Inicio del corte:", 0, 1000, padding_cfg.get("pre_head_ms", 120), "Cuánto audio se conserva ANTES de cada palabra inicial (más ms = el corte empieza más lejos de la palabra)")
        self.slider_post = create_slider("Final del corte:", 0, 1000, padding_cfg.get("post_tail_ms", 180), "Cuánto audio se conserva DESPUÉS de la última palabra")
        self.slider_max_silence = create_slider("Silencio Máximo:", 0, 3000, padding_cfg.get("max_silence_ms", 1000))
        self.slider_rem_silence = create_slider("Silencio Restante:", 0, 1000, padding_cfg.get("remaining_silence_ms", 300))
        opt_layout.addLayout(form_layout)
        
        btn_restore = QPushButton("Restaurar Valores por Defecto")
        btn_restore.setStyleSheet("background-color: #6c757d; color: white; padding: 5px; margin-bottom: 10px;")
        
        def restore_defaults():
            self.slider_pre.setValue(120)
            self.slider_post.setValue(180)
            self.slider_max_silence.setValue(1000)
            self.slider_rem_silence.setValue(300)
            
        btn_restore.clicked.connect(restore_defaults)
        opt_layout.addWidget(btn_restore)
        
        self.btn_preview = QPushButton("Actualizar Previsualización en Resolve")
        self.btn_preview.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 10px; margin-top: 5px;")
        self.btn_preview.clicked.connect(self.run_preview)
        self.btn_preview.setEnabled(False)
        opt_layout.addWidget(self.btn_preview)
        
        layout.addWidget(options_group)
        
        # Review Takes
        self.review_group = QGroupBox("Paso 2.5: Revisión de Tomas")
        self.review_group.setStyleSheet("QGroupBox { border: 1px solid #444; padding-top: 15px; }")
        review_layout = QVBoxLayout()
        self.review_group.setLayout(review_layout)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout()
        self.scroll_content.setLayout(self.scroll_layout)
        self.scroll_area.setWidget(self.scroll_content)
        review_layout.addWidget(self.scroll_area)
        
        # Panel inferior para debug temporal
        debug_layout = QHBoxLayout()
        self.btn_test_precision = QPushButton("Test de precisión (Debug)")
        self.btn_test_precision.clicked.connect(self.run_precision_test)
        debug_layout.addWidget(self.btn_test_precision)
        
        review_layout.addLayout(debug_layout)
        
        layout.addWidget(self.review_group)
        
        # Apply
        apply_group = QGroupBox("Paso 3: Finalizar")
        apply_group.setStyleSheet("QGroupBox { border: 1px solid #444; padding-top: 15px; }")
        apply_layout = QHBoxLayout()
        apply_group.setLayout(apply_layout)
        
        self.btn_report = QPushButton("Generar Reporte HTML")
        self.btn_report.setStyleSheet("background-color: #0056b3; color: white; padding: 10px;")
        self.btn_report.clicked.connect(self.run_report)
        self.btn_report.setEnabled(False)
        apply_layout.addWidget(self.btn_report)
        
        self.btn_cut = QPushButton("Aplicar Cortes (Crear Timeline)")
        self.btn_cut.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 10px;")
        self.btn_cut.clicked.connect(self.run_cut)
        self.btn_cut.setEnabled(False)
        apply_layout.addWidget(self.btn_cut)
        
        layout.addWidget(apply_group)

    def init_zooms_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        self.tabs.addTab(tab, "Zooms")
        
        info = QLabel("Esta herramienta opera directamente sobre la **Timeline Activa** en DaVinci Resolve, aplicándose a todos los clips en la pista V1.\n\nNo depende del análisis de cortes.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #ccc; margin-bottom: 20px;")
        layout.addWidget(info)
        
        self.overwrite_zooms_cb = QCheckBox("Sobrescribir zooms existentes (ZoomX/Y != 1.0)")
        self.overwrite_zooms_cb.setChecked(False)
        layout.addWidget(self.overwrite_zooms_cb)
        
        btn_apply_zooms = QPushButton("Aplicar Zooms Alternados (1.0x -> 1.15x)")
        btn_apply_zooms.setStyleSheet("background-color: #6f42c1; color: white; font-weight: bold; padding: 12px; margin-top: 10px;")
        btn_apply_zooms.clicked.connect(self.run_apply_zooms)
        layout.addWidget(btn_apply_zooms)
        
        btn_remove_zooms = QPushButton("Quitar Zooms (Restaurar a 1.0x)")
        btn_remove_zooms.setStyleSheet("background-color: #dc3545; color: white; padding: 12px; margin-top: 10px;")
        btn_remove_zooms.clicked.connect(self.run_remove_zooms)
        layout.addWidget(btn_remove_zooms)
        
        layout.addStretch()

    def check_dependencies(self):
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
            t = Transcriber(config_path)
            self.log_area.append(f"[OK] ffmpeg encontrado en: {t.ffmpeg_path}")
        except Exception as e:
            self.log_area.append(f"[ERROR] Dependencias: {e}")
            self.btn_analyze.setEnabled(False)

    def load_resolve_clips(self):
        try:
            self.log_area.append("Conectando a DaVinci Resolve...")
            resolve_io = ResolveIO()
            clips = resolve_io.get_video_clips()
            if not clips:
                self.log_area.append("No se encontraron clips de video en V1 o en el Media Pool.")
                self.clip_combo.addItem("No hay clips disponibles", None)
            else:
                for c in clips:
                    self.clip_combo.addItem(f"{c['name']} ({os.path.basename(c['path'])})", c['path'])
                self.log_area.append(f"Se cargaron {len(clips)} clips desde Resolve.")
        except Exception as e:
            self.log_area.append(f"Error conectando a Resolve: {e}")

    def load_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "Seleccionar Guion", "", "Text Files (*.txt);;All Files (*)")
        if path:
            self.script_path = path
            self.script_lbl.setText(os.path.basename(path))

    def _save_config(self):
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
        config_data = {}
        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = yaml.safe_load(f) or {}
                
                with open(config_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    
                new_lines = []
                in_padding = False
                for line in lines:
                    stripped = line.strip()
                    if line.startswith("padding:"):
                        in_padding = True
                        new_lines.append(line)
                        continue
                    if in_padding and line and not line.startswith(" ") and not line.startswith("\t"):
                        in_padding = False
                        
                    if in_padding:
                        if stripped.startswith("pre_head_ms:"):
                            line = f"  pre_head_ms: {self.slider_pre.value()}\n"
                        elif stripped.startswith("post_tail_ms:"):
                            line = f"  post_tail_ms: {self.slider_post.value()}\n"
                        elif stripped.startswith("max_silence_ms:"):
                            line = f"  max_silence_ms: {self.slider_max_silence.value()}\n"
                        elif stripped.startswith("remaining_silence_ms:"):
                            line = f"  remaining_silence_ms: {self.slider_rem_silence.value()}\n"
                    new_lines.append(line)
                    
                with open(config_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
        except Exception as e:
            self.log_area.append(f"[ERROR] Guardando config.yaml: {e}")
            
        return config_data

    def run_analyze(self):
        video_path = self.clip_combo.currentData()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self, "Error", "Debes seleccionar un clip de video válido.")
            return
            
        if not self.script_path or not os.path.exists(self.script_path):
            QMessageBox.warning(self, "Error", "Debes cargar un archivo de guion (.txt).")
            return
            
        self.btn_analyze.setEnabled(False)
        self.btn_preview.setEnabled(False)
        self.btn_cut.setEnabled(False)
        self.btn_report.setEnabled(False)
        self.progress.setValue(0)
        self.log_area.clear()
        
        self.align_results = None
        self.transcript_words = None
        
        self.worker = AnalysisWorker(video_path, self.script_path)
        self.worker.log_msg.connect(self.log_area.append)
        self.worker.progress_val.connect(self.progress.setValue)
        self.worker.error.connect(self.on_analysis_error)
        self.worker.analysis_finished.connect(self.on_analysis_finished)
        self.worker.start()

    def on_analysis_error(self, err_msg):
        self.btn_analyze.setEnabled(True)
        QMessageBox.critical(self, "Error", f"Ocurrió un error en el análisis:\n{err_msg}")

    def on_analysis_finished(self, align_results, transcript_words):
        self.btn_analyze.setEnabled(True)
        self.align_results = align_results
        self.transcript_words = transcript_words
        self.btn_preview.setEnabled(True)
        self.btn_cut.setEnabled(True)
        self.btn_report.setEnabled(True)
        self.populate_review_panel()
        QMessageBox.information(self, "Éxito", "Análisis completado. Puedes previsualizar o aplicar los cortes.")

    def populate_review_panel(self):
        while self.scroll_layout.count():
            child = self.scroll_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
                
        if not hasattr(self, "manual_decisions"):
            self.manual_decisions = {}
            
        has_reviewable = False
        rio = ResolveIO()
        fps = rio.get_timeline_framerate()
        
        # timecode conversion is now in resolve_io
            
        for g_idx, group in enumerate(self.align_results.get("retake_groups", [])):
            high_score_takes = [t for t in group["takes"] if t["score"] >= 95]
            if len(high_score_takes) >= 2 or group.get("s_idx", -1) == -1:
                has_reviewable = True
                
                gb = QGroupBox(f"Oración: {group['script_sentence']}")
                gb_layout = QVBoxLayout()
                gb.setLayout(gb_layout)
                
                bg = QButtonGroup(gb)
                current_selected_idx = self.manual_decisions.get(g_idx)
                
                for t_idx, t in enumerate(group["takes"]):
                    row = QHBoxLayout()
                    
                    rb = QRadioButton(f"Toma {t_idx+1}")
                    if current_selected_idx is not None:
                        if t_idx == current_selected_idx:
                            rb.setChecked(True)
                    else:
                        if t == group["best_take"]:
                            rb.setChecked(True)
                            
                    def on_toggled(checked, g=group, t=t, g_i=g_idx, t_i=t_idx):
                        if checked:
                            g["best_take"] = t
                            self.manual_decisions[g_i] = t_i
                            self.preview_timer.start()
                            
                    rb.toggled.connect(on_toggled)
                    bg.addButton(rb)
                    
                    lbl_info = QLabel(f"Score: {t['score']} | Duración: {t['end_time'] - t['start_time']:.1f}s")
                    btn_listen = QPushButton("🔊 Escuchar")
                    
                    def listen(checked=False, t_start=t["start_time"]):
                        video_path = self.clip_combo.currentData()
                        tl_items = rio.get_timeline_items_for_path(video_path)
                        if not tl_items: return
                        
                        absolute_f = rio.source_sec_to_frame_timeline(tl_items, t_start, fps)
                        tc = rio.absolute_frame_to_timecode(absolute_f, fps)
                        tl = rio.project.GetCurrentTimeline()
                        tl.SetCurrentTimecode(tc)
                        
                    btn_listen.clicked.connect(listen)
                    
                    row.addWidget(rb)
                    row.addWidget(lbl_info)
                    row.addWidget(btn_listen)
                    row.addStretch()
                    gb_layout.addLayout(row)
                    
                self.scroll_layout.addWidget(gb)
                
        if not has_reviewable:
            self.scroll_layout.addWidget(QLabel("No hay tomas conflictivas para revisión manual."))
            
        self.scroll_layout.addStretch()

    def _auto_preview(self):
        self.run_preview(silent=True)

    def run_preview(self, silent=False):
        if not self.align_results or not self.transcript_words: return
        
        if not silent:
            self.log_area.append("\nGenerando previsualización en la timeline actual...")
        config_data = self._save_config()
        
        rio = ResolveIO()
        fps = rio.get_timeline_framerate()
        
        # Remove old markers
        rio.delete_markers_by_custom_data("autoeditor")
        if not silent:
            self.log_area.append(" > Borrados marcadores de AutoEditor de la timeline.")
        
        video_path = self.clip_combo.currentData()
        tl_items = rio.get_timeline_items_for_path(video_path)
        if not tl_items:
            self.log_area.append("[ERROR] No se pudo encontrar el clip en la timeline activa para dibujar marcadores.")
            return
            
            
        sb = SegmentsBuilder(fps=fps, config=config_data)
        final_segments = sb.build_keep_segments(self.align_results, self.transcript_words)
        
        total_source_frames = int(round(self.transcript_words[-1].get("end", 0) * fps)) if self.transcript_words else 0
        
        red_intervals = []
        current_f = 0
        for seg in final_segments:
            sf = seg["start_frame"]
            if sf > current_f:
                red_intervals.append((current_f, sf))
            current_f = seg["end_frame"]
            
        if current_f < total_source_frames:
            red_intervals.append((current_f, total_source_frames))
            
        # SANITY CHECKS
        total_duration_s = total_source_frames / fps if fps else 0
        kept_duration_s = sum([(seg["end_frame"] - seg["start_frame"]) for seg in final_segments]) / fps if fps else 0
        
        coverage = kept_duration_s / total_duration_s if total_duration_s > 0 else 1.0
        max_red_s = max([(ef - sf) / fps for sf, ef in red_intervals]) if red_intervals else 0
        
        if total_duration_s > 0 and (coverage < 0.25 or max_red_s > 600):
            err_msg = f"Advertencia de Sanidad: Cobertura={coverage:.1%}, Max Rojo={max_red_s:.1f}s. Revisa si es intencional o un fallo."
            self.log_area.append(f"[ADVERTENCIA] {err_msg}")
            # No abortar, permitir la visualización para poder depurar
            # if not silent:
            #     QMessageBox.warning(self, "Advertencia de Sanidad", err_msg)
            
        self.current_segments = final_segments
        
        debug_preview_data = {
            "sliders": {
                "pre_head_ms": self.slider_pre.value(),
                "post_tail_ms": self.slider_post.value(),
                "max_silence_ms": self.slider_max_silence.value(),
                "remaining_silence_ms": self.slider_rem_silence.value()
            },
            "manual_decisions": getattr(self, "manual_decisions", {}),
            "final_segments": final_segments,
            "red_intervals": red_intervals,
            "virtual_groups": [g for g in self.align_results.get("retake_groups", []) if g.get("s_idx", -1) == -1]
        }
        try:
            with open("debug_preview.json", "w", encoding="utf-8") as f:
                json.dump(debug_preview_data, f, indent=2)
        except Exception as e:
            self.log_area.append(f"[ERROR] Escribiendo debug_preview.json: {e}")
        
        if not silent:
            self.log_area.append(f" > Sliders: Pre={self.slider_pre.value()}ms, Post={self.slider_post.value()}ms")
            self.log_area.append(f" > Resultan {len(final_segments)} tramos vigentes y {len(red_intervals)} tramos cortados (ROJO).")
            
        def draw_markers(sec_intervals, color, name, note):
            m_added = 0
            for idx, interval in enumerate(sec_intervals):
                sf_sec = interval[0]
                ef_sec = interval[1]
                start_target_f = round(sf_sec * fps)
                end_target_f = round(ef_sec * fps)
                
                for item in tl_items:
                    start_src = item.GetLeftOffset()
                    end_src = start_src + item.GetDuration()
                    
                    overlap_start = max(start_target_f, start_src)
                    overlap_end = min(end_target_f, end_src)
                    
                    if overlap_start < overlap_end:
                        abs_start = item.GetStart() + (overlap_start - start_src)
                        dur = max(1, overlap_end - overlap_start)
                        tl_sf_marker = rio.absolute_frame_to_marker_frame(abs_start)
                        rio.add_marker_to_current_timeline(tl_sf_marker, color, name, note, dur, "autoeditor")
                        m_added += 1
                        
                        if not silent and color == "Red" and idx < 3 and m_added <= 3:
                            tc = rio.absolute_frame_to_timecode(abs_start, fps)
                            self.log_area.append(f"   - Marker Rojo {m_added}: {tc} (dur: {dur} frames)")
            return m_added

        markers_added = 0
        markers_added += draw_markers(red_intervals, "Red", "AutoEditor: Corte", "")
        markers_added += draw_markers([(s["start_s"], s["end_s"]) for s in final_segments], "Green", "AutoEditor: Conservado", "")
        
        yellow_intervals = []
        for u in self.align_results.get("unmatched_segments", []):
            if u.get("duplicate_action") == "DECISIÓN MANUAL":
                yellow_intervals.append((u["start_time"], u["end_time"]))
        markers_added += draw_markers(yellow_intervals, "Yellow", "AutoEditor: Revisar", "Toma válida fuera de guion")
                
        if silent:
            self.log_area.append(f" > Auto-Preview: {len(final_segments)} vigentes, {len(red_intervals)} cortados.")
        else:
            self.log_area.append(f" > Añadidos {markers_added} marcadores a la Timeline.")

    def run_precision_test(self):
        rio = ResolveIO()
        tl = rio.project.GetCurrentTimeline()
        if not tl: return
        
        fps = rio.get_timeline_framerate()
        video_path = self.clip_combo.currentData()
        tl_items = rio.get_timeline_items_for_path(video_path)
        if not tl_items:
            self.log_area.append("[DEBUG] Clip no encontrado para el test.")
            return
            
        clip_fps = float(tl_items[0].GetMediaPoolItem().GetClipProperty("FPS") or 0)
        self.log_area.append(f"\n[DIAGNÓSTICO] Timeline FPS: {fps} | Clip FPS: {clip_fps}")
        
        current_tc = tl.GetCurrentTimecode()
        # Convertir TC actual a frame de timeline
        h, m, s, f = map(int, current_tc.split(':'))
        tl_f = int((h * 3600 + m * 60 + s) * fps + f)
        
        start_f = tl.GetStartFrame()
        
        expected_s = rio.frame_timeline_to_source_sec(tl_items, tl_f, fps)
        expected_source_f = round(expected_s * fps)
        
        self.log_area.append(f"  > Playhead TC: {current_tc} (frame timeline {tl_f})")
        self.log_area.append(f"  > Según la matemática, corresponde al frame {expected_source_f} del archivo fuente ({expected_s:.2f}s).")

    def run_cut(self):
        if not self.align_results or not self.transcript_words: return
        
        if getattr(self, "current_segments", None) is None:
            self.log_area.append("\nGenerando segmentos antes de cortar...")
            self.run_preview(silent=True)
            
        final_segments = self.current_segments
        if not final_segments:
            self.log_area.append("[ERROR] No hay segmentos calculados para cortar.")
            return
            
        self.log_area.append(f"\nAplicando cortes en una nueva timeline ({len(final_segments)} segmentos)...")
        
        debug_apply_data = {
            "segments_applied": final_segments
        }
        try:
            with open("debug_apply.json", "w", encoding="utf-8") as f:
                json.dump(debug_apply_data, f, indent=2)
        except:
            pass
            
        rio = ResolveIO()
        fps = rio.get_timeline_framerate()
        
        # Limpiar marcadores de la timeline actual (la de previsualización)
        rio.delete_markers_by_custom_data("autoeditor")
        
        video_path = self.clip_combo.currentData()
        vid_name = os.path.splitext(os.path.basename(video_path))[0]
        ts = time.strftime("%H%M%S")
        tl_name = f"{vid_name} _AUTOCUT_{ts}"
        
        clips = rio.get_video_clips()
        media_item = None
        for c in clips:
            if c["path"] == video_path:
                media_item = c.get("item")
                break
                
        if not media_item:
            self.log_area.append("[ERROR] No se pudo encontrar el MediaPoolItem original para cortar.")
            return
            
        tl = rio.create_timeline(tl_name)
        if not tl:
            self.log_area.append("[ERROR] No se pudo crear la nueva línea de tiempo.")
            return
            
        append_data = []
        for seg in final_segments:
            append_data.append({
                "mediaPoolItem": media_item,
                "startFrame": round(seg["start_s"] * fps),
                "endFrame": round(seg["end_s"] * fps)
            })
            
        if append_data:
            new_items = rio.append_to_timeline(append_data)
            self.log_area.append(f" > Creada timeline '{tl_name}' con {len(new_items)} fragmentos.")
            
            self.log_area.append(" > Verificando timeline post-corte...")
            items_in_track = tl.GetItemListInTrack("video", 1)
            if items_in_track:
                discrepancies = []
                for idx, item in enumerate(items_in_track):
                    if idx >= len(final_segments):
                        break
                    expected_sf = round(final_segments[idx]["start_s"] * fps)
                    real_source_f = item.GetLeftOffset()
                    diff = abs(real_source_f - expected_sf)
                    if diff > 2:
                        discrepancies.append(f"Seg {idx}: Exp {expected_sf} | Real {real_source_f} | Diff {diff}")
                        
                if discrepancies:
                    self.log_area.append("[WARNING] Discrepancias encontradas:")
                    for d in discrepancies:
                        self.log_area.append(f"   {d}")
                else:
                    self.log_area.append("[OK] Verificación post-corte impecable. 0 discrepancias encontradas.")
            else:
                self.log_area.append("[ERROR] No se pudo leer la timeline recién creada para verificar.")
        else:
            self.log_area.append(" > No hay fragmentos para insertar.")
            
        QMessageBox.information(self, "Éxito", f"Timeline '{tl_name}' creada con éxito.")

    def run_report(self):
        if not self.align_results or not self.transcript_words: return
        
        out_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reporte_cortes.html")
        out_path = generate_html_report(self.align_results, self.transcript_words, out_file)
        self.log_area.append(f"\nReporte generado: {out_path}")
        webbrowser.open(f"file://{os.path.abspath(out_path)}")

    def run_apply_zooms(self):
        rio = ResolveIO()
        overwrite = self.overwrite_zooms_cb.isChecked()
        res = rio.apply_zooms_to_current_timeline(overwrite_zooms=overwrite)
        if res:
            self.log_area.append(f"[ZOOMS] Zooms alternados aplicados a la timeline actual (Sobrescribir={overwrite}).")
            QMessageBox.information(self, "Zooms Aplicados", "Se aplicaron los zooms a V1 de la timeline activa.")
        else:
            self.log_area.append("[ZOOMS ERROR] No se pudo aplicar zooms. Verifica que haya una timeline activa con clips en V1.")

    def run_remove_zooms(self):
        rio = ResolveIO()
        res = rio.remove_zooms_from_current_timeline()
        if res:
            self.log_area.append("[ZOOMS] Se han restaurado los zooms a 1.0 en la timeline actual.")
            QMessageBox.information(self, "Zooms Restaurados", "Todos los clips en V1 ahora tienen ZoomX/Y en 1.0.")
        else:
            self.log_area.append("[ZOOMS ERROR] No se pudo quitar zooms. Verifica que haya una timeline activa.")

def main():
    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        window = AutoEditorApp()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        import traceback
        with open(log_path, "a") as f:
            f.write(f"Error en main(): {e}\n{traceback.format_exc()}\n")

if __name__ == "__main__":
    main()
