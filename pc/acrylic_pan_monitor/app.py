"""Tk monitor for serial capture, waveform/FFT display, and session recording."""

from __future__ import annotations

from pathlib import Path
import queue
import tkinter as tk
from tkinter import filedialog, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib import font_manager, rcParams

from .protocol import EventData, Frame, MessageType, decode_event
from .recorder import Recorder, RecordingError, ReceiveStats, make_demo_event
from .serial_link import SerialLink, available_ports
from .signal_processing import prepare_plot_data


JAPANESE_FONT_CANDIDATES = (
    "Yu Gothic", "Yu Gothic UI", "Meiryo", "MS Gothic", "Noto Sans CJK JP",
)


def configure_matplotlib_font(available_names: set[str] | None = None) -> str | None:
    """Select an installed Japanese font and configure Matplotlib globally."""
    if available_names is None:
        available_names = {font.name for font in font_manager.fontManager.ttflist}
    selected = next((name for name in JAPANESE_FONT_CANDIDATES if name in available_names), None)
    if selected is not None:
        rcParams["font.family"] = selected
    rcParams["axes.unicode_minus"] = False
    return selected


class MonitorApp:
    BAUDRATE = 115_200

    def __init__(self, root: tk.Tk) -> None:
        configure_matplotlib_font()
        self.root = root
        self.root.title("Acrylic Pan - 振動収録モニター")
        self.root.geometry("1220x820")
        self._messages: queue.Queue[Frame | Exception] = queue.Queue()
        self.link = SerialLink(self._messages.put, self._messages.put)
        self.stats = ReceiveStats()
        self.recorder: Recorder | None = None
        self.output_root = Path("data/raw/sessions").resolve()
        self._last_identity = ""

        self.port_var = tk.StringVar()
        self.status_var = tk.StringVar(value="未接続")
        self.info_var = tk.StringVar(value="衝撃イベントを待っています")
        self.stats_var = tk.StringVar(value="受信 0 / 保存 0 / 欠落 0 / CRC等 0")
        self.output_var = tk.StringVar(value=str(self.output_root))
        self.class_var = tk.StringVar()
        self.autosave_var = tk.BooleanVar(value=True)
        self._build_ui()
        self._refresh_ports()
        self.root.after(20, self._poll_messages)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        connection = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        connection.pack(fill=tk.X)
        ttk.Label(connection, text="COMポート").pack(side=tk.LEFT)
        self.port_box = ttk.Combobox(connection, textvariable=self.port_var, width=15, state="readonly")
        self.port_box.pack(side=tk.LEFT, padx=(6, 4))
        ttk.Button(connection, text="更新", command=self._refresh_ports).pack(side=tk.LEFT)
        self.connect_button = ttk.Button(connection, text="接続", command=self._toggle_connection)
        self.connect_button.pack(side=tk.LEFT, padx=8)
        ttk.Button(connection, text="デモ波形", command=self._show_demo).pack(side=tk.LEFT, padx=8)
        ttk.Label(connection, textvariable=self.status_var).pack(side=tk.RIGHT)

        recording = ttk.Frame(self.root, padding=(8, 4, 8, 4))
        recording.pack(fill=tk.X)
        ttk.Checkbutton(recording, text="イベントを自動保存", variable=self.autosave_var).pack(side=tk.LEFT)
        ttk.Label(recording, text="クラスID（任意）").pack(side=tk.LEFT, padx=(18, 4))
        ttk.Entry(recording, textvariable=self.class_var, width=6).pack(side=tk.LEFT)
        ttk.Button(recording, text="保存先…", command=self._choose_output).pack(side=tk.LEFT, padx=(18, 4))
        ttk.Button(recording, text="新規セッション", command=self._new_session).pack(side=tk.LEFT, padx=4)
        ttk.Label(recording, textvariable=self.output_var).pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        summary = ttk.Frame(self.root, padding=(10, 3))
        summary.pack(fill=tk.X)
        ttk.Label(summary, textvariable=self.info_var, font=("Yu Gothic UI", 10)).pack(side=tk.LEFT)
        ttk.Label(summary, textvariable=self.stats_var).pack(side=tk.RIGHT)

        figure = Figure(figsize=(10, 6), dpi=100, layout="constrained")
        self.wave_ax = figure.add_subplot(211)
        self.fft_ax = figure.add_subplot(212)
        self._style_empty_axes()
        self.canvas = FigureCanvasTkAgg(figure, master=self.root)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def _style_empty_axes(self) -> None:
        self.wave_ax.set_title("振動波形")
        self.wave_ax.set_xlabel("時間 [ms]")
        self.wave_ax.set_ylabel("加速度 [raw LSB]")
        self.wave_ax.grid(True, alpha=0.3)
        self.fft_ax.set_title("FFTスペクトル（DC除去・Hann窓）")
        self.fft_ax.set_xlabel("周波数 [Hz]")
        self.fft_ax.set_ylabel("振幅 [dB re. 1 LSB]")
        self.fft_ax.grid(True, alpha=0.3)

    def _refresh_ports(self) -> None:
        ports = available_ports()
        self.port_box["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def _toggle_connection(self) -> None:
        if self.link.connected:
            self.link.disconnect()
            self.status_var.set("未接続")
            self.connect_button.configure(text="接続")
            return
        port = self.port_var.get()
        if not port:
            self.status_var.set("COMポートを選択してください")
            return
        try:
            self.link.connect(port, self.BAUDRATE)
        except Exception as error:
            self.status_var.set(f"接続失敗: {error}")
            return
        self.status_var.set(f"接続中: {port} / {self.BAUDRATE:,} bps")
        self.connect_button.configure(text="切断")

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_root, title="セッション保存先")
        if not selected:
            return
        self._finish_session()
        self.output_root = Path(selected).resolve()
        self.output_var.set(str(self.output_root))

    def _new_session(self) -> None:
        self._finish_session()
        try:
            session = self._ensure_session()
        except RecordingError as error:
            self.status_var.set(f"セッション作成失敗: {error}")
            return
        self.status_var.set(f"新規セッション: {session.name}")

    def _ensure_session(self) -> Path:
        if self.recorder is None or not self.recorder.active:
            self.recorder = Recorder(self.output_root)
            try:
                session = self.recorder.begin_session({
                    "application": "acrylic_pan_monitor",
                    "serial_port": self.port_var.get() or None,
                    "baudrate": self.BAUDRATE,
                    "device_identity": self._last_identity or None,
                    "class_id_note": "-1 in NPZ means unlabeled; manifests use an empty value",
                })
            except Exception as error:
                self.recorder = None
                raise RecordingError(str(error)) from error
            self.output_var.set(str(session))
        return self.recorder.session_dir  # type: ignore[return-value]

    def _poll_messages(self) -> None:
        while True:
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break
            if isinstance(message, Exception):
                self.status_var.set(f"通信エラー: {message}")
                self.link.disconnect()
                self.connect_button.configure(text="接続")
                continue
            self.stats.observe_frame()
            if message.message_type == MessageType.EVENT_DATA:
                try:
                    event = decode_event(message)
                    self.stats.observe_event(event.sequence)
                    self._display_event(event)
                    if self.autosave_var.get():
                        self._save_event(event, source="serial")
                except Exception as error:
                    self.status_var.set(f"イベント処理エラー: {error}")
            elif message.message_type == MessageType.HELLO:
                self._last_identity = message.payload.decode("ascii", errors="replace")
                self.status_var.set(f"接続済み: {self._last_identity}")
        self.stats.decoder_errors = self.link.decoder_error_count
        self._update_stats_text()
        self.root.after(20, self._poll_messages)

    def _display_event(self, event: EventData) -> None:
        plot = prepare_plot_data(event)
        self.wave_ax.clear()
        self.wave_ax.plot(plot.time_ms, plot.samples, color="#1777c8", linewidth=1.0)
        self.wave_ax.axvline(plot.trigger_time_ms, color="#e23b2e", linestyle="--", label="trigger")
        self.wave_ax.set_title(f"振動波形 - sequence {event.sequence}")
        self.wave_ax.set_xlabel("時間 [ms]")
        self.wave_ax.set_ylabel("加速度 [raw LSB]")
        self.wave_ax.grid(True, alpha=0.3)
        self.wave_ax.legend(loc="upper right")

        self.fft_ax.clear()
        self.fft_ax.plot(plot.frequency_hz, plot.magnitude_db, color="#dd7b16", linewidth=1.0)
        self.fft_ax.set_xlim(0, event.sample_rate_hz / 2)
        self.fft_ax.set_title("FFTスペクトル（DC除去・Hann窓）")
        self.fft_ax.set_xlabel("周波数 [Hz]")
        self.fft_ax.set_ylabel("振幅 [dB re. 1 LSB]")
        self.fft_ax.grid(True, alpha=0.3)
        self.canvas.draw_idle()
        self.info_var.set(
            f"{event.sample_rate_hz:,} Hz / {len(event.samples)} samples / "
            f"peak {event.peak_abs:,} LSB / trigger {plot.trigger_time_ms:.2f} ms"
        )

    def _class_id(self) -> int | None:
        value = self.class_var.get().strip()
        if not value:
            return None
        class_id = int(value)
        if class_id < 0:
            raise ValueError("クラスIDは0以上にしてください")
        return class_id

    def _save_event(self, event: EventData, *, source: str) -> None:
        try:
            class_id = self._class_id()
            self._ensure_session()
            assert self.recorder is not None
            recorded = self.recorder.record_event(event, class_id=class_id, annotations={"source": source})
            self.stats.events_saved += 1
            self.status_var.set(f"保存: {recorded.path.name}")
        except (ValueError, RecordingError, OSError) as error:
            self.stats.save_errors += 1
            self.status_var.set(f"保存エラー: {error}")

    def _show_demo(self) -> None:
        sequence = self.stats.events_received + 1
        event = make_demo_event(sequence)
        self._display_event(event)
        if self.autosave_var.get():
            self._save_event(event, source="demo")
        self._update_stats_text()

    def _update_stats_text(self) -> None:
        self.stats_var.set(
            f"受信 {self.stats.events_received} / 保存 {self.stats.events_saved} / "
            f"欠落 {self.stats.missing_sequences} / 重複 {self.stats.duplicate_sequences} / "
            f"順序逆転 {self.stats.out_of_order_sequences} / CRC等 {self.stats.decoder_errors} / "
            f"保存失敗 {self.stats.save_errors}"
        )

    def _finish_session(self) -> None:
        if self.recorder is not None:
            try:
                self.recorder.close()
            except OSError as error:
                self.status_var.set(f"セッション終了処理エラー: {error}")
        self.recorder = None
        self.output_var.set(str(self.output_root))

    def _close(self) -> None:
        self.link.disconnect()
        self._finish_session()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()
