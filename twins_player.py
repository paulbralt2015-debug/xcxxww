#!/usr/bin/env python3
"""Twins Video Player 2 : lecteur natif (PySide6 + libmpv), sans HTML."""
import os
import sys
import locale
import random
import threading
import time
import shutil
import subprocess
import tempfile

# libmpv (mpv-2.dll sous Windows) est cherchée à côté du programme
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
os.environ["PATH"] = BASE + os.pathsep + os.environ.get("PATH", "")
if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(BASE)

from PySide6.QtCore import Qt, QObject, Signal, QSettings, QTimer
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QTabWidget, QListWidget,
    QListWidgetItem, QPushButton, QSlider, QLabel, QComboBox, QCheckBox, QFormLayout,
    QFileDialog, QMessageBox, QInputDialog, QLineEdit, QAbstractItemView, QScrollArea, QFrame)

import filters as F
import shaders as S

URL_PREFIX = ("http://", "https://", "rtmp://", "rtsp://")
VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mpg", ".mpeg", ".m4v",
             ".ts", ".vob", ".divx", ".asf", ".ogv", ".mp3", ".flac", ".wav", ".ogg", ".m4a",
             ".opus", ".aac", ".ac3", ".dts", ".mid")
STYLE = """
QWidget{background:#14151b;color:#e9e8e4;font-size:13px}
QPushButton{background:#1d1f28;border:1px solid #2e3040;border-radius:6px;padding:6px 10px}
QPushButton:hover{border-color:#3fd0d4}
QPushButton#play{background:#3fd0d4;color:#0b0c10;font-weight:600;border-color:#3fd0d4;min-width:70px}
QListWidget,QLineEdit,QComboBox{background:#1d1f28;border:1px solid #2e3040;border-radius:6px;padding:4px}
QListWidget::item:selected{background:#2a2d3c;border-left:3px solid #f0558a}
QTabBar::tab{padding:8px 7px;color:#8d90a0;border-bottom:2px solid transparent}
QTabBar::tab:selected{color:#e9e8e4;border-bottom:2px solid #f0558a}
QTabWidget::pane{border:1px solid #2e3040}
QScrollArea{border:0}
QSlider::groove:horizontal{height:4px;background:#2e3040;border-radius:2px}
QSlider::sub-page:horizontal{background:#3fd0d4;border-radius:2px}
QSlider::handle:horizontal{width:12px;margin:-5px 0;border-radius:6px;background:#f0558a}
"""


def fmt_t(t):
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def fmt(s):
    try:
        s = float(s)
    except (TypeError, ValueError):
        return "0:00"
    if s <= 0:
        return "0:00"
    s = int(s)
    h, m, r = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{r:02d}" if h else f"{m}:{r:02d}"


class Bridge(QObject):
    """Ramène les rappels de libmpv (autre thread) vers le thread de l'interface."""
    call = Signal(object)


class Win(QMainWindow):
    def __init__(self, mpv_mod):
        super().__init__()
        self.mpv = mpv_mod
        self.setWindowTitle("Twins Video Player 2")
        self.resize(1320, 800)
        self.cfg = QSettings("Twins", "TwinsVideoPlayer2")
        self.cur = None
        self.dragging = False
        self.last_save = 0
        self.bridge = Bridge()
        self.bridge.call.connect(lambda fn: fn())
        self._build()
        self._start_mpv()
        self._keys()
        self.setAcceptDrops(True)

    # ---------- interface ----------
    def _build(self):
        self.video = QWidget()
        self.video.setAttribute(Qt.WA_NativeWindow)
        self.video.setStyleSheet("background:#000")
        self.video.setMinimumSize(480, 270)
        self.title = QLabel("Aucun fichier")
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderPressed.connect(lambda: setattr(self, "dragging", True))
        self.seek.sliderReleased.connect(self._seek_release)
        self.tcur, self.tdur = QLabel("0:00"), QLabel("0:00")

        def btn(text, fn, name=None, tip=""):
            b = QPushButton(text)
            b.clicked.connect(lambda *_: fn())
            b.setToolTip(tip)
            if name:
                b.setObjectName(name)
            return b
        self.bplay = btn("Lecture", self.toggle, "play", "Z / X / Espace")
        row = QHBoxLayout()
        for w in (btn("Préc.", self.prev, tip="V"), self.bplay, btn("Arrêt", self.stop, tip="C"),
                  btn("Suiv.", lambda: self.next(False), tip="B"), btn("−10 s", lambda: self.seek_by(-10)),
                  btn("+10 s", lambda: self.seek_by(10)), btn("A-B", self.ab, tip="L"),
                  btn("Capture", self.shot, tip="P")):
            row.addWidget(w)
        row.addStretch()
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 130)
        self.vol.setValue(100)
        self.vol.setFixedWidth(120)
        self.vol.valueChanged.connect(lambda v: setattr(self.mp, "volume", v))
        row.addWidget(QLabel("Vol."))
        row.addWidget(self.vol)
        row.addWidget(btn("Muet", self.mute, tip="M"))
        row.addWidget(btn("Plein écran", self.fullscreen, tip="F"))
        sk = QHBoxLayout()
        sk.addWidget(self.tcur)
        sk.addWidget(self.seek)
        sk.addWidget(self.tdur)
        self.ctrl = QWidget()
        cl = QVBoxLayout(self.ctrl)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.title)
        cl.addLayout(sk)
        cl.addLayout(row)
        left = QVBoxLayout()
        left.addWidget(self.video, 1)
        left.addWidget(self.ctrl)
        lw = QWidget()
        lw.setLayout(left)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_list(), "Liste")
        self.tabs.addTab(self.scroll(self._tab_audio()), "Audio")
        self.tabs.addTab(self.scroll(self._tab_video()), "Vidéo")
        self.tabs.addTab(self.scroll(self._tab_fx()), "Effets")
        self.tabs.addTab(self.scroll(self._tab_export()), "Export")
        self.tabs.addTab(self._tab_tracks(), "Pistes")
        self.tabs.addTab(self.scroll(self._tab_opt()), "Options")
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setFixedWidth(480)
        root = QWidget()
        h = QHBoxLayout(root)
        h.addWidget(lw, 1)
        h.addWidget(self.tabs)
        self.setCentralWidget(root)

    def scroll(self, w):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(w)
        return sa

    def _tab_list(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.lst = QListWidget()
        self.lst.setDragDropMode(QAbstractItemView.InternalMove)
        self.lst.itemDoubleClicked.connect(lambda it: self.load(it.data(Qt.UserRole)))
        r = QHBoxLayout()
        for t, fn in (("Fichiers", self.open_files), ("Dossier", self.open_dir), ("Sous-titres", self.add_subs),
                      ("Retirer", self.remove_sel), ("Vider", self.clear)):
            b = QPushButton(t)
            b.clicked.connect(lambda *_, f=fn: f())
            r.addWidget(b)
        v.addLayout(r)
        v.addWidget(self.lst, 3)
        v.addWidget(QLabel("Marque-pages"))
        v.addWidget(self._tab_bkm(), 2)
        return w

    def _tab_bkm(self):
        w = QWidget()
        v = QVBoxLayout(w)
        b = QPushButton("Marquer ce moment")
        b.clicked.connect(self.add_bm)
        self.bl = QListWidget()
        self.bl.itemDoubleClicked.connect(lambda it: self.mp.seek(it.data(Qt.UserRole), "absolute"))
        d = QPushButton("Supprimer")
        d.clicked.connect(self.del_bm)
        v.addWidget(b)
        v.addWidget(self.bl)
        v.addWidget(d)
        return w

    def _slider(self, lo, hi, val, cb):
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.valueChanged.connect(lambda *_: cb())
        return s

    def _tab_audio(self):
        w = QWidget()
        f = QFormLayout(w)
        self.amode = QComboBox()
        self.amode.addItems(list(F.AUDIO_MODES))
        self.aout = QComboBox()
        self.aout.addItems(["Casque (downmix)", "Enceintes 5.1"])
        self.eq = QComboBox()
        self.eq.addItems(list(F.EQ_PRESETS))
        self.night = QCheckBox("Mode nuit (dynamique réduite)")
        self.slev = self._slider(0, 300, 100, self.apply_audio)
        self.clev = self._slider(0, 200, 100, self.apply_audio)
        self.sdel = self._slider(0, 60, 15, self.apply_audio)
        self.bass = self._slider(-6, 12, 0, self.apply_audio)
        self.rev = self._slider(0, 100, 0, self.apply_audio)
        self.adel = self._slider(-500, 500, 0, lambda: setattr(self.mp, "audio_delay", self.adel.value() / 1000))
        for c in (self.amode, self.aout, self.eq):
            c.currentIndexChanged.connect(self.apply_audio)
        self.night.stateChanged.connect(self.apply_audio)
        for lab, wd in (("Décodage", self.amode), ("Sortie", self.aout), ("Égaliseur", self.eq),
                        ("Surround / largeur", self.slev), ("Centre", self.clev),
                        ("Retard surround (ms)", self.sdel), ("Basses (dB)", self.bass),
                        ("Salle", self.rev), ("Décalage son (ms)", self.adel)):
            f.addRow(lab, wd)
        f.addRow(self.night)
        n = QLabel("Le décodage Pro Logic s’applique à une bande son stéréo encodée Lt/Rt. "
                   "Les flux AC-3 et DTS 5.1 sont lus tels quels par mpv.")
        n.setWordWrap(True)
        n.setStyleSheet("color:#8d90a0")
        f.addRow(n)
        return w

    def _tab_video(self):
        w = QWidget()
        f = QFormLayout(w)
        self.std = QComboBox()
        self.std.addItems(["Original", "PAL", "SECAM", "NTSC"])
        self.dar = QComboBox()
        self.dar.addItems(["4:3", "16:9"])
        self.scan = QCheckBox("Lignes de balayage")
        self.light = QCheckBox("Aperçu allégé (moitié de définition, l’export reste complet)")
        self.light.setChecked(True)
        self.deint = QCheckBox("Désentrelacer (yadif)")
        self.den = QCheckBox("Réduire le bruit")
        self.flip = QCheckBox("Miroir horizontal")
        self.rot = QComboBox()
        self.rot.addItems(["0°", "90°", "180°", "270°"])
        self.shp = self._slider(0, 10, 0, self.apply_video)
        self.asp = QComboBox()
        self.asp.addItems(["Auto", "4:3", "16:9", "2.35:1"])
        self.asp.currentIndexChanged.connect(self.apply_aspect)
        self.gam = self._slider(-50, 50, 0, lambda: setattr(self.mp, "gamma", self.gam.value()))
        self.hue = self._slider(-50, 50, 0, lambda: setattr(self.mp, "hue", self.hue.value()))
        for c in (self.std, self.dar, self.rot):
            c.currentIndexChanged.connect(self.apply_video)
        for c in (self.scan, self.light, self.deint, self.den, self.flip):
            c.stateChanged.connect(self.apply_video)
        for lab, wd in (("Standard", self.std), ("Format TV", self.dar), ("Rotation", self.rot),
                        ("Netteté", self.shp), ("Format d’affichage", self.asp),
                        ("Gamma", self.gam), ("Teinte", self.hue)):
            f.addRow(lab, wd)
        for c in (self.scan, self.light, self.deint, self.den, self.flip):
            f.addRow(c)
        n = QLabel("PAL et SECAM : 25 i/s, 576 lignes. NTSC : 29,97 i/s, 480 lignes.")
        n.setWordWrap(True)
        n.setStyleSheet("color:#8d90a0")
        f.addRow(n)
        return w

    def _tab_fx(self):
        w = QWidget()
        f = QFormLayout(w)
        self.look = QComboBox()
        self.look.addItems(list(F.LOOKS))
        self.lookk = self._slider(0, 100, 70, self.apply_video)
        self.spk = self._slider(0, 100, 0, self.apply_video)
        self.rcp = self._slider(0, 100, 0, self.apply_video)
        self.pald = self._slider(0, 100, 0, self.apply_video)
        self.scr = QComboBox()
        self.scr.addItems(list(F.SCRAMBLERS))
        self.look.currentIndexChanged.connect(self.apply_video)
        self.scr.currentIndexChanged.connect(self.on_scrambler)
        self.pvmode = QComboBox()
        self.pvmode.addItems(["Shaders GPU (temps réel)", "Filtres FFmpeg (précis, lent)", "Aucun effet en direct"])
        self.pvmode.currentIndexChanged.connect(self.apply_video)
        f.addRow("Aperçu direct", self.pvmode)
        pv = QPushButton("Aperçu rendu (10 s, via FFmpeg)")
        pv.clicked.connect(self.preview)
        f.addRow(pv)
        for lab, wd in (("Rendu", self.look), ("Intensité du rendu", self.lookk),
                        ("Sparklies (quantité)", self.spk), ("Mauvaise réception", self.rcp),
                        ("Destruction couleurs PAL", self.pald), ("Cryptage", self.scr)):
            f.addRow(lab, wd)
        n = QLabel("En direct, les effets tournent sur la carte graphique (shaders) : c’est une approximation "
                   "rapide. L’aperçu rendu et l’export utilisent FFmpeg, plus fidèle, mais hors temps réel. "
                   "Le direct ne change ni la cadence ni la forme de l’image (pas de 25 i/s ni de bandes "
                   "noires). Les modes de cryptage sont des simulations visuelles : ils ne chiffrent rien. "
                   "Le choix d’un système règle aussi la norme TV d’origine.")
        n.setWordWrap(True)
        n.setStyleSheet("color:#8d90a0")
        f.addRow(n)
        return w

    def _tab_export(self):
        w = QWidget()
        f = QFormLayout(w)
        self.efmt = QComboBox()
        self.efmt.addItems(list(F.EXPORT_FORMATS))
        self.eq_q = self._slider(14, 32, 20, lambda: self.eq_lab.setText(f"CRF {self.eq_q.value()}"))
        self.eq_lab = QLabel("CRF 20")
        self.eall = QCheckBox("Exporter toute la liste (dans un dossier)")
        self.eavid = QCheckBox("Appliquer les effets vidéo")
        self.eavid.setChecked(True)
        self.eaaud = QCheckBox("Appliquer les effets audio")
        self.eaaud.setChecked(True)
        b = QPushButton("Exporter…")
        b.clicked.connect(self.export)
        f.addRow("Format", self.efmt)
        f.addRow("Qualité (bas = meilleur)", self.eq_q)
        f.addRow(self.eq_lab)
        f.addRow(self.eavid)
        f.addRow(self.eaaud)
        f.addRow(self.eall)
        f.addRow(b)
        n = QLabel("L’export refait la conversion complète, hors temps réel, en pleine définition, avec "
                   "tous les réglages de l’onglet Vidéo (standard, rotation, netteté…), de l’onglet Effets "
                   "et de l’onglet Audio. Les formats « Audio » ne gardent que le son.")
        n.setWordWrap(True)
        n.setStyleSheet("color:#8d90a0")
        f.addRow(n)
        return w

    def _tab_tracks(self):
        w = QWidget()
        f = QFormLayout(w)
        self.aud, self.sub = QComboBox(), QComboBox()
        self.aud.currentIndexChanged.connect(lambda *_: self.set_track("aid", self.aud))
        self.sub.currentIndexChanged.connect(lambda *_: self.set_track("sid", self.sub))
        self.sdl = self._slider(-100, 100, 0, lambda: setattr(self.mp, "sub_delay", self.sdl.value() / 10))
        self.ssc = self._slider(30, 250, 100, lambda: setattr(self.mp, "sub_scale", self.ssc.value() / 100))
        for lab, wd in (("Piste audio", self.aud), ("Sous-titres", self.sub),
                        ("Décalage (×0,1 s)", self.sdl), ("Taille (%)", self.ssc)):
            f.addRow(lab, wd)
        return w

    def _tab_opt(self):
        w = QWidget()
        f = QFormLayout(w)
        self.mode = QComboBox()
        self.mode.addItems(["Normal", "Répéter la liste", "Répéter le fichier", "Aléatoire"])
        self.rate = QComboBox()
        self.rate.addItems(["0.5", "0.75", "1", "1.25", "1.5", "2"])
        self.rate.setCurrentText("1")
        self.rate.currentTextChanged.connect(lambda t: setattr(self.mp, "speed", float(t)))
        self.fit = QComboBox()
        self.fit.addItems(["Ajustée", "Remplie", "Étirée"])
        self.fit.currentIndexChanged.connect(self.apply_fit)
        self.bri = self._slider(-50, 50, 0, lambda: setattr(self.mp, "brightness", self.bri.value()))
        self.con = self._slider(-50, 50, 0, lambda: setattr(self.mp, "contrast", self.con.value()))
        self.sat = self._slider(-100, 100, 0, lambda: setattr(self.mp, "saturation", self.sat.value()))
        self.blk = QLineEdit(str(self.cfg.value("blocked", "")))
        self.blk.setPlaceholderText("mot1, mot2")
        self.blk.editingFinished.connect(lambda: self.cfg.setValue("blocked", self.blk.text()))
        for lab, wd in (("Mode", self.mode), ("Vitesse", self.rate), ("Image", self.fit),
                        ("Luminosité", self.bri), ("Contraste", self.con), ("Saturation", self.sat),
                        ("Mots bloqués", self.blk)):
            f.addRow(lab, wd)
        self.sleep = QComboBox()
        self.sleep.addItems(["Désactivé", "15 min", "30 min", "45 min", "60 min", "90 min", "120 min"])
        self.sleep_quit = QCheckBox("Quitter au lieu de mettre en pause")
        self.sleep_timer = QTimer(self)
        self.sleep_timer.setSingleShot(True)
        self.sleep_timer.timeout.connect(self.on_sleep)
        self.sleep.currentIndexChanged.connect(self.set_sleep)
        self.restore = QCheckBox("Restaurer la liste au démarrage")
        self.restore.setChecked(str(self.cfg.value("restore", "true")).lower() == "true")
        self.restore.stateChanged.connect(lambda *_: self.cfg.setValue("restore", self.restore.isChecked()))
        f.addRow("Minuteur d’arrêt", self.sleep)
        f.addRow(self.sleep_quit)
        f.addRow(self.restore)
        for t, fn in (("Ouvrir une URL…", self.open_url), ("Exporter la liste (M3U)…", self.export_m3u),
                      ("Importer une liste (M3U)…", self.import_m3u)):
            b = QPushButton(t)
            b.clicked.connect(lambda *_, fn=fn: fn())
            f.addRow(b)
        k = QLabel("Z/X lecture · C arrêt · V/B précédent/suivant · +/− ±10 s · ←/→ ±5 s · T aller à · "
                   "F plein écran · L boucle A-B · P capture · ,/. image par image · M muet · "
                   "O fichiers · D dossier · I infos · Q marque-page · molette volume")
        k.setWordWrap(True)
        k.setStyleSheet("color:#8d90a0")
        f.addRow(k)
        return w

    # ---------- moteur mpv ----------
    def _start_mpv(self):
        gui = self.bridge.call.emit
        self.logfile = os.path.join(os.path.expanduser("~"), "twins_player.log")
        self.seen_err = set()

        def log(level, comp, text):
            if level in ("error", "fatal", "warn"):
                try:
                    with open(self.logfile, "a", encoding="utf-8") as fh:
                        fh.write(f"[{level}] {comp}: {text}")
                except OSError:
                    pass
                if level in ("error", "fatal") and text not in self.seen_err:
                    self.seen_err.add(text)
                    gui(lambda: self.statusBar().showMessage(f"mpv ({comp}) : {text.strip()}", 15000))
        # auto-copy : les filtres logiciels (effets) ont besoin d'images en mémoire système
        self.mp = self.mpv.MPV(wid=str(int(self.video.winId())), input_default_bindings=False,
                               input_vo_keyboard=True, osc=False, keep_open="yes", hwdec="auto-copy",
                               sub_auto="fuzzy", screenshot_format="png", log_handler=log, loglevel="warn")
        self.mp.observe_property("time-pos", lambda n, v: gui(lambda: self.on_pos(v)))
        self.mp.observe_property("duration", lambda n, v: gui(lambda: self.tdur.setText(fmt(v))))
        self.mp.observe_property("pause", lambda n, v: gui(lambda: self.bplay.setText("Lecture" if v else "Pause")))
        self.mp.observe_property("track-list", lambda n, v: gui(lambda: self.on_tracks(v)))
        self.mp.observe_property("eof-reached", lambda n, v: gui(lambda: self.on_eof()) if v else None)

    def _keys(self):
        T = [("Z", "z", self.toggle), ("X", "x", self.toggle), ("Space", "SPACE", self.toggle),
             ("C", "c", self.stop), ("V", "v", self.prev), ("B", "b", lambda: self.next(False)),
             ("+", "+", lambda: self.seek_by(10)), ("=", "=", lambda: self.seek_by(10)),
             ("-", "-", lambda: self.seek_by(-10)), ("Right", "RIGHT", lambda: self.seek_by(5)),
             ("Left", "LEFT", lambda: self.seek_by(-5)), ("T", "t", self.jump), ("F", "f", self.fullscreen),
             ("Esc", "ESC", self.leave_fs), ("L", "l", self.ab), ("P", "p", self.shot),
             (",", ",", lambda: self.mp.command("frame-back-step")), (".", ".", lambda: self.mp.command("frame-step")),
             ("M", "m", self.mute), ("O", "o", self.open_files), ("A", "a", self.open_files),
             ("D", "d", self.open_dir), ("I", "i", self.info), ("Q", "q", self.add_bm)]
        gui = self.bridge.call.emit
        for qk, mk, fn in T:
            sc = QShortcut(QKeySequence(qk), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(fn)
            try:
                self.mp.register_key_binding(
                    mk, lambda st, key, ch=None, f=fn: gui(f) if st[:1] in ("d", "p") else None)
            except Exception:
                pass
        try:
            for k, d in (("WHEEL_UP", 5), ("WHEEL_DOWN", -5)):
                self.mp.register_key_binding(k, lambda st, key, ch=None, d=d: gui(
                    lambda: self.vol.setValue(self.vol.value() + d)) if st[:1] in ("d", "p") else None)
            self.mp.register_key_binding("MBTN_LEFT_DBL", lambda st, key, ch=None: gui(self.fullscreen)
                                         if st[:1] in ("d", "p") else None)
        except Exception:
            pass

    def prop(self, name, default=None):
        try:
            v = self.mp[name]
            return default if v is None else v
        except Exception:
            return default

    # ---------- lecture ----------
    def paths(self):
        return [self.lst.item(i).data(Qt.UserRole) for i in range(self.lst.count())]

    def add_files(self, files, force=False, autoload=True):
        blocked = [w.strip().lower() for w in self.blk.text().split(",") if w.strip()]
        n = skipped = 0
        for p in files:
            if not force and not p.lower().endswith(VIDEO_EXT):
                continue
            if any(w in (os.path.basename(p) or p).lower() for w in blocked):
                skipped += 1
                continue
            it = QListWidgetItem(os.path.basename(p) or p)
            it.setData(Qt.UserRole, p)
            self.lst.addItem(it)
            n += 1
        if skipped:
            self.statusBar().showMessage(f"{skipped} fichier(s) bloqué(s) par vos mots bloqués", 4000)
        if n and self.cur is None and autoload:
            self.load(self.paths()[-n])

    def open_files(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Ouvrir des fichiers", "", "Médias (*" + " *".join(VIDEO_EXT) + ");;Tous (*)")
        self.add_files(fs)

    def open_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Ajouter un dossier")
        if d:
            self.add_files(sorted(os.path.join(r, f) for r, _, fl in os.walk(d) for f in fl))

    def add_subs(self):
        if not self.cur:
            return
        f, _ = QFileDialog.getOpenFileName(self, "Sous-titres", "", "Sous-titres (*.srt *.ass *.ssa *.sub *.vtt)")
        if f:
            self.mp.command("sub-add", f)

    def remove_sel(self):
        for it in self.lst.selectedItems():
            self.lst.takeItem(self.lst.row(it))

    def clear(self):
        self.lst.clear()
        self.stop()
        self.mp.command("stop")
        self.cur = None
        self.title.setText("Aucun fichier")
        self.refresh_bkm()

    def key_of(self, p):
        try:
            return f"{os.path.basename(p)}_{os.path.getsize(p)}".replace("/", "_").replace("\\", "_")
        except OSError:
            return os.path.basename(p)

    def load(self, path):
        if not path or not (path.startswith(URL_PREFIX) or os.path.exists(path)):
            self.statusBar().showMessage("Fichier introuvable", 4000)
            return
        self.cur = path
        if getattr(self, "preview_on", False):
            self.preview_on = False
            self.apply_video()
            self.apply_audio()
        t = float(self.cfg.value("pos/" + self.key_of(path), 0) or 0)
        self.mp["start"] = str(int(t)) if t > 8 else "none"
        self.mp.play(path)
        self.mp.pause = False
        self.title.setText(os.path.basename(path))
        self.setWindowTitle(os.path.basename(path) + " · Twins Video Player 2")
        for i in range(self.lst.count()):
            self.lst.item(i).setSelected(self.lst.item(i).data(Qt.UserRole) == path)
        self.refresh_bkm()
        if t > 8:
            self.statusBar().showMessage(f"Reprise à {fmt(t)}", 3000)

    def toggle(self):
        if self.cur is None:
            self.open_files()
        else:
            self.mp.pause = not self.mp.pause

    def stop(self):
        self.mp.pause = True
        self.mp.seek(0, "absolute")

    def next(self, auto):
        ps = self.paths()
        if not ps:
            return
        m = self.mode.currentIndex()
        i = ps.index(self.cur) if self.cur in ps else -1
        if m == 3:
            j = random.randrange(len(ps))
        else:
            j = i + 1
            if j >= len(ps):
                if m == 1 or not auto:
                    j = 0
                else:
                    return
        self.load(ps[j])

    def prev(self):
        ps = self.paths()
        if not ps:
            return
        if (self.prop('time-pos') or 0) > 3:
            self.mp.seek(0, "absolute")
            return
        i = ps.index(self.cur) if self.cur in ps else 0
        self.load(ps[(i - 1) % len(ps)])

    def on_eof(self):
        if self.cur:
            self.cfg.setValue("pos/" + self.key_of(self.cur), 0)
        if self.mode.currentIndex() == 2:
            self.mp.seek(0, "absolute")
            self.mp.pause = False
        else:
            self.next(True)

    def on_pos(self, v):
        if v is None:
            return
        self.tcur.setText(fmt(v))
        d = self.prop('duration')
        if d and not self.dragging:
            self.seek.setValue(int(v / d * 1000))
        if self.cur and time.time() - self.last_save > 4:
            self.last_save = time.time()
            self.cfg.setValue("pos/" + self.key_of(self.cur), v)

    def _seek_release(self):
        self.dragging = False
        d = self.prop('duration')
        if d:
            self.mp.seek(self.seek.value() / 1000 * d, "absolute")

    def seek_by(self, s):
        self.mp.seek(s, "relative")

    def jump(self):
        t, ok = QInputDialog.getText(self, "Aller à", "Temps (mm:ss ou hh:mm:ss) :", text=fmt(self.prop('time-pos')))
        if ok:
            try:
                sec = 0
                for p in t.split(":"):
                    sec = sec * 60 + float(p)
                self.mp.seek(sec, "absolute")
            except ValueError:
                self.statusBar().showMessage("Format invalide. Exemple : 1:25:30", 4000)

    def mute(self):
        self.mp.mute = not self.mp.mute

    def ab(self):
        a, b = self.prop('ab-loop-a'), self.prop('ab-loop-b')
        pos = self.prop('time-pos') or 0
        if a in (None, "no"):
            self.mp["ab-loop-a"] = pos
            self.statusBar().showMessage(f"Point A à {fmt(pos)}", 3000)
        elif b in (None, "no"):
            self.mp["ab-loop-b"] = pos
            self.statusBar().showMessage(f"Boucle {fmt(a)} → {fmt(pos)}", 3000)
        else:
            self.mp["ab-loop-a"] = "no"
            self.mp["ab-loop-b"] = "no"
            self.statusBar().showMessage("Boucle désactivée", 3000)

    def shot(self):
        if not self.cur:
            return
        d = os.path.join(os.path.expanduser("~"), "Pictures")
        os.makedirs(d, exist_ok=True)
        out = os.path.join(d, f"{os.path.splitext(os.path.basename(self.cur))[0]}_{int(self.prop('time-pos') or 0)}s.png")
        self.mp.command("screenshot-to-file", out, "window")
        self.statusBar().showMessage("Capture : " + out, 5000)

    def info(self):
        g = lambda n: self.prop(n, "?")
        QMessageBox.information(self, "Informations", (
            f"Fichier : {self.cur}\nDurée : {fmt(self.prop('duration'))}\n"
            f"Vidéo : {g('width')}×{g('height')} · {g('container-fps')} i/s · {g('video-format')}\n"
            f"Audio : {g('audio-codec-name')} · {g('audio-params')}"))

    def fullscreen(self):
        fs = not self.isFullScreen()
        self.showFullScreen() if fs else self.showNormal()
        self.tabs.setVisible(not fs)
        self.ctrl.setVisible(not fs)

    def leave_fs(self):
        if self.isFullScreen():
            self.fullscreen()

    # ---------- pistes, minuteur, listes ----------
    def on_tracks(self, tl):
        for combo, typ in ((self.aud, "audio"), (self.sub, "sub")):
            combo.blockSignals(True)
            combo.clear()
            if typ == "sub":
                combo.addItem("Aucun", "no")
            sel = 0
            for t in tl or []:
                if t.get("type") != typ:
                    continue
                lab = " · ".join(str(x) for x in (t.get("lang"), t.get("title"), t.get("codec")) if x)
                combo.addItem(f"{t['id']}. {lab or 'Piste'}", t["id"])
                if t.get("selected"):
                    sel = combo.count() - 1
            combo.setCurrentIndex(sel)
            combo.blockSignals(False)

    def set_track(self, prop, combo):
        d = combo.currentData()
        if d is not None:
            self.mp[prop] = d

    def set_sleep(self):
        i = self.sleep.currentIndex()
        self.sleep_timer.stop()
        if i:
            mins = int(self.sleep.currentText().split()[0])
            self.sleep_timer.start(mins * 60000)
            self.statusBar().showMessage(f"Arrêt dans {mins} min", 4000)

    def on_sleep(self):
        self.sleep.setCurrentIndex(0)
        if self.sleep_quit.isChecked():
            self.close()
        else:
            self.mp.pause = True
            self.statusBar().showMessage("Minuteur écoulé : lecture mise en pause", 8000)

    def open_url(self):
        u, ok = QInputDialog.getText(self, "Ouvrir une URL", "Adresse du flux ou de la vidéo :")
        if ok and u.strip().startswith(URL_PREFIX):
            self.add_files([u.strip()], force=True)

    def export_m3u(self):
        f, _ = QFileDialog.getSaveFileName(self, "Exporter la liste", os.path.expanduser("~/liste.m3u"), "M3U (*.m3u *.m3u8)")
        if f:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("#EXTM3U\n" + "\n".join(self.paths()) + "\n")

    def import_m3u(self):
        f, _ = QFileDialog.getOpenFileName(self, "Importer une liste", "", "M3U (*.m3u *.m3u8)")
        if not f:
            return
        base = os.path.dirname(f)
        with open(f, encoding="utf-8", errors="ignore") as fh:
            ps = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
        self.add_files([p if p.startswith(URL_PREFIX) or os.path.isabs(p) else os.path.join(base, p) for p in ps], force=True)

    # ---------- marque-pages ----------
    def refresh_bkm(self):
        self.bl.clear()
        if not self.cur:
            return
        for t in sorted(self.cfg.value("bkm/" + self.key_of(self.cur), []) or [], key=float):
            it = QListWidgetItem(fmt(float(t)))
            it.setData(Qt.UserRole, float(t))
            self.bl.addItem(it)

    def add_bm(self):
        if not self.cur:
            return
        k = "bkm/" + self.key_of(self.cur)
        v = list(self.cfg.value(k, []) or [])
        v.append(int(self.prop('time-pos') or 0))
        self.cfg.setValue(k, v)
        self.refresh_bkm()

    def del_bm(self):
        it = self.bl.currentItem()
        if it and self.cur:
            k = "bkm/" + self.key_of(self.cur)
            v = [x for x in (self.cfg.value(k, []) or []) if float(x) != it.data(Qt.UserRole)]
            self.cfg.setValue(k, v)
            self.refresh_bkm()

    # ---------- audio / vidéo ----------
    def audio_raw(self):
        mode = F.AUDIO_MODES[self.amode.currentText()]
        out51 = self.aout.currentIndex() == 1
        graph = F.audio_graph(mode, out51, self.slev.value() / 100, self.clev.value() / 100,
                              self.sdel.value(), self.bass.value(), self.rev.value(),
                              self.eq.currentText(), self.night.isChecked())
        if F.is_surround(mode) and out51:
            ch = "5.1"
        else:
            ch = "stereo" if graph else "auto-safe"
        return graph, ch

    def audio_cfg(self):
        g, ch = self.audio_raw()
        return F.mpv_wrap(g), ch

    def apply_audio(self):
        af, ch = self.audio_cfg()
        try:
            self.mp["audio-channels"] = ch
            self.mp["af"] = af
        except Exception as e:
            self.mp["af"] = ""
            self.statusBar().showMessage(f"Filtre audio refusé par mpv : {e}", 6000)

    def video_opts(self, light=False):
        return dict(deint=self.deint.isChecked(), rot=self.rot.currentIndex() * 90, flip=self.flip.isChecked(),
                    denoise=self.den.isChecked(), sharpen=self.shp.value(), scan=self.scan.isChecked(),
                    look=F.LOOKS[self.look.currentText()], look_k=self.lookk.value(), sparkle=self.spk.value(),
                    reception=self.rcp.value(), pal_destroy=self.pald.value(),
                    scrambler=F.SCRAMBLERS[self.scr.currentText()], light=light)

    def video_cfg(self, light=None):
        light = self.light.isChecked() if light is None else light
        return F.mpv_wrap(F.video_graph(self.std.currentText(), self.dar.currentText(), self.video_opts(light)))

    def on_scrambler(self):
        k = F.SCRAMBLERS[self.scr.currentText()]
        if k:
            self.std.setCurrentText(F.SCRAMBLER_STD[k])
        self.apply_video()

    def apply_aspect(self):
        self.mp["video-aspect-override"] = ["-1", "4:3", "16:9", "2.35:1"][self.asp.currentIndex()]

    def apply_video(self):
        mode = self.pvmode.currentIndex()               # 0 shaders, 1 FFmpeg (lavfi), 2 aucun
        try:
            if mode == 1:
                vf, shader = self.video_cfg(), ""
            else:                                       # seules les corrections de base passent par lavfi
                o = dict(self.video_opts(False), look=None, sparkle=0, reception=0, pal_destroy=0,
                         scrambler=None, scan=False)
                vf = F.mpv_wrap(F.video_graph("Original", self.dar.currentText(), o))
                shader = S.build_shader(self.std.currentText(), self.video_opts(False)) if mode == 0 else ""
            self.mp["vf"] = vf
            self.set_shader(shader)
        except Exception as e:
            self.mp["vf"] = ""
            msg = str(e)
            if msg not in self.seen_err:
                self.seen_err.add(msg)
                QMessageBox.warning(self, "Effet refusé par mpv",
                                    f"{msg}\n\nLes effets sont désactivés. Détails : {self.logfile}\n"
                                    "Essayez l'aperçu rendu (onglet Effets) ou l'export, qui utilisent FFmpeg.")

    def set_shader(self, text):
        old = getattr(self, "shader_file", None)
        if text:
            self.shader_n = getattr(self, "shader_n", 0) + 1
            path = os.path.join(tempfile.gettempdir(), f"twins_fx_{os.getpid()}_{self.shader_n}.glsl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.mp["glsl-shaders"] = [path]
            self.shader_file = path
        else:
            self.mp["glsl-shaders"] = []
            self.shader_file = None
        if old and os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass

    def apply_fit(self):
        i = self.fit.currentIndex()
        self.mp["keepaspect"] = "no" if i == 2 else "yes"
        self.mp["panscan"] = 1.0 if i == 1 else 0.0

    # ---------- FFmpeg (aperçu rendu et export) ----------
    def ffmpeg(self):
        for c in (os.path.join(BASE, "ffmpeg.exe"), os.path.join(BASE, "ffmpeg")):
            if os.path.isfile(c):
                return c
        return shutil.which("ffmpeg")

    def run_ffmpeg(self, cmd, label, on_line=None):
        """Lance FFmpeg ; retourne (ok, derniers messages)."""
        flags = 0x08000000 if os.name == "nt" else 0          # pas de console sous Windows
        pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="ignore", creationflags=flags)
        tail = []
        for line in pr.stdout:
            line = line.strip()
            if line.startswith("out_time_ms=") and on_line:
                try:
                    on_line(int(line.split("=")[1]) / 1e6)
                except ValueError:
                    pass
            elif "=" not in line or line.startswith("error"):
                tail = (tail + [line])[-8:]
        return pr.wait() == 0, "\n".join(tail)

    def preview(self):
        """Pré-rend 10 s avec FFmpeg puis les lit : montre les effets même si le direct échoue."""
        ff = self.ffmpeg()
        if not self.cur or self.cur.startswith(URL_PREFIX):
            self.statusBar().showMessage("Chargez d'abord un fichier local", 5000)
            return
        if not ff:
            QMessageBox.warning(self, "FFmpeg introuvable", "Placez ffmpeg à côté du programme ou dans le PATH.")
            return
        vf = F.video_graph(self.std.currentText(), self.dar.currentText(), self.video_opts(self.light.isChecked()))
        af, _ = self.audio_raw()
        out = os.path.join(tempfile.gettempdir(), "twins_preview.mp4")
        start, src, gui = self.prop("time-pos") or 0, self.cur, self.bridge.call.emit
        cmd = F.ffmpeg_cmd(ff, src, out, "MP4 (H.264 + AAC)", vf, af, 23, start=start, duration=10)
        cmd[cmd.index("medium")] = "ultrafast"
        self.statusBar().showMessage("Rendu de l'aperçu (10 s)…")

        def work():
            ok, msg = self.run_ffmpeg(cmd, "aperçu")
            gui(lambda: self.play_preview(out) if ok else QMessageBox.warning(self, "Aperçu impossible", msg))
        threading.Thread(target=work, daemon=True).start()

    def play_preview(self, out):
        self.preview_on = True
        for k, v in (("vf", ""), ("af", ""), ("glsl-shaders", []), ("start", "none")):
            try:
                self.mp[k] = v
            except Exception:
                pass
        self.mp.play(out)
        self.mp.pause = False
        self.statusBar().showMessage("Aperçu rendu par FFmpeg. Rechargez un fichier de la liste pour revenir au direct.", 10000)

    # ---------- export (conversion réelle) ----------
    def export(self):
        srcs = self.paths() if self.eall.isChecked() else ([self.cur] if self.cur else [])
        srcs = [s for s in srcs if s and not s.startswith(URL_PREFIX)]
        if not srcs:
            self.statusBar().showMessage("Rien à exporter : chargez un fichier local", 5000)
            return
        fmt = F.EXPORT_FORMATS[self.efmt.currentText()]
        tag = "_".join(x for x in (self.std.currentText().lower() if self.std.currentIndex() else "",
                                   F.LOOKS[self.look.currentText()] or "",
                                   F.SCRAMBLERS[self.scr.currentText()] or "") if x) or "export"
        if len(srcs) == 1:
            out, _ = QFileDialog.getSaveFileName(self, "Exporter", os.path.splitext(srcs[0])[0] + f"_{tag}." + fmt["ext"],
                                                 f"*.{fmt['ext']}")
            jobs = [(srcs[0], out)] if out else []
        else:
            d = QFileDialog.getExistingDirectory(self, "Dossier de destination")
            jobs = [(s, os.path.join(d, os.path.splitext(os.path.basename(s))[0] + f"_{tag}." + fmt["ext"]))
                    for s in srcs] if d else []
        if not jobs:
            return
        vf = self.video_cfg(light=False) if self.eavid.isChecked() and fmt["video"] else ""
        af, ch = self.audio_cfg() if self.eaaud.isChecked() else ("", "auto-safe")
        vopts = F.video_opts(self.efmt.currentText(), self.eq_q.value())
        mpv, gui = self.mpv, self.bridge.call.emit
        ff = self.ffmpeg()
        raw_v = F.video_graph(self.std.currentText(), self.dar.currentText(), self.video_opts(False)) \
            if self.eavid.isChecked() and fmt["video"] else ""
        raw_a = self.audio_raw()[0] if self.eaaud.isChecked() else ""
        fname, q = self.efmt.currentText(), self.eq_q.value()

        def work():
            for n, (src, out) in enumerate(jobs, 1):
                if ff:                                  # voie FFmpeg : mêmes graphes, testés hors lecteur
                    ok, msg = self.run_ffmpeg(
                        F.ffmpeg_cmd(ff, src, out, fname, raw_v, raw_a, q), "export",
                        lambda t, n=n: gui(lambda: self.statusBar().showMessage(f"Export {n}/{len(jobs)} : {fmt_t(t)}")))
                    if not ok:
                        gui(lambda msg=msg, src=src: QMessageBox.warning(
                            self, "Export impossible", f"{os.path.basename(src)} :\n{msg}"))
                    continue
                try:
                    kw = dict(o=out, oac=fmt["oac"], af=af, audio_channels="5.1" if ch == "5.1" else "stereo",
                              keep_open="no")
                    if fmt["video"]:
                        kw.update(ovc=fmt["ovc"], vf=vf)
                        if vopts:
                            kw["ovcopts"] = vopts
                    else:
                        kw["vid"] = "no"
                    m = mpv.MPV(**kw)
                    m.observe_property("percent-pos", lambda nm, v, n=n: gui(
                        lambda: self.statusBar().showMessage(f"Export {n}/{len(jobs)} : {v:.0f} %")) if v else None)
                    m.play(src)
                    m.wait_for_playback()
                    m.terminate()
                except Exception as e:
                    msg = f"{os.path.basename(src)} : {e}"
                    gui(lambda msg=msg: QMessageBox.warning(self, "Export impossible", msg))
            gui(lambda: self.statusBar().showMessage(f"Export terminé ({len(jobs)} fichier(s))", 10000))
        threading.Thread(target=work, daemon=True).start()
        self.statusBar().showMessage("Export en cours…")

    # ---------- divers ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_files([u.toLocalFile() for u in e.mimeData().urls()])

    def closeEvent(self, e):
        self.cfg.setValue("list", self.paths())
        if getattr(self, "shader_file", None) and os.path.exists(self.shader_file):
            try:
                os.remove(self.shader_file)
            except OSError:
                pass
        try:
            self.mp.terminate()
        except Exception:
            pass
        e.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    locale.setlocale(locale.LC_NUMERIC, "C")  # exigé par libmpv
    try:
        import mpv
    except Exception as e:
        QMessageBox.critical(None, "libmpv introuvable",
                             "Placez mpv-2.dll (Windows) à côté du programme, ou installez libmpv "
                             f"(Linux : libmpv2, macOS : brew install mpv).\n\n{e}")
        return 1
    w = Win(mpv)
    w.show()
    args = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if args:
        w.add_files(args)
    elif w.restore.isChecked():
        saved = w.cfg.value("list", []) or []
        w.add_files([saved] if isinstance(saved, str) else list(saved), force=True, autoload=False)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
