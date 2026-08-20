import base64
import datetime as dt
import glob
import hashlib
import io
import json
import os
import platform
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request

try:
    import pystray
except Exception:
    pystray = None
from PIL import Image, ImageDraw

PEAK_COLOR = "#F44336"
VALLEY_COLOR = "#4CAF50"
WARN_COLOR = "#FFB300"

PEAK_LABEL = "梁文峰"
VALLEY_LABEL = "梁文谷"

BEIJING_OFFSET = dt.timedelta(hours=8)
PEAK_PERIODS = ((9, 12), (14, 18))
NEAR_END = dt.timedelta(minutes=5)

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

BALANCE_URL = "https://api.deepseek.com/user/balance"
REFRESH_OPTIONS = ((30, "30秒"), (60, "1分钟"), (300, "5分钟"))
CURRENCY_SYMBOL = {"CNY": "¥", "USD": "$", "EUR": "€", "JPY": "¥", "HKD": "HK$"}
BALANCE_RETRY_SECS = 30
WINDOW_W, WINDOW_H = 340, 236
EDGE_MARGIN = 24


def beijing_now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + BEIJING_OFFSET


def next_peak_start(now):
    day = now.date()
    hm = now.hour * 60 + now.minute
    if hm < 9 * 60:
        return dt.datetime.combine(day, dt.time(9, 0))
    if 12 * 60 <= hm < 14 * 60:
        return dt.datetime.combine(day, dt.time(14, 0))
    d = day + dt.timedelta(days=1)
    return dt.datetime.combine(d, dt.time(9, 0))


def get_status(now):
    hm = now.hour * 60 + now.minute
    for start_h, end_h in PEAK_PERIODS:
        if start_h * 60 <= hm < end_h * 60:
            end = dt.datetime.combine(now.date(), dt.time(end_h))
            return True, end - now
    start = next_peak_start(now)
    return False, start - now


def format_remaining(d):
    total = max(0, int(d.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days:
        return f"{days}天 {hours}小时"
    if hours:
        return f"{hours}小时 {mins}分"
    if mins:
        return f"{mins}分 {secs}秒"
    return f"{secs}秒"


def format_balance(infos):
    texts = []
    for info in infos:
        currency = info.get("currency", "")
        symbol = CURRENCY_SYMBOL.get(currency, currency)
        texts.append(f"{symbol}{info.get('total_balance', '?')}")
    return " / ".join(texts)


def make_icon(color_hex, size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 2 - 3
    d.ellipse((size // 2 - r, size // 2 - r, size // 2 + r, size // 2 + r), fill=color_hex)
    return img


def make_key_icon(size=18, color="#7A7A7A", hole_color="#F0F0F0"):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 18.0
    cx, cy, r = 7 * s, 9 * s, 4.6 * s
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    d.ellipse((cx - r + 2.6 * s, cy - r + 2.6 * s, cx + r - 2.6 * s, cy + r - 2.6 * s), fill=hole_color)
    d.rectangle((11 * s, 4 * s, 13.5 * s, 14 * s), fill=color)
    d.rectangle((13.5 * s, 9 * s, 16.5 * s, 11 * s), fill=color)
    d.rectangle((13.5 * s, 12.5 * s, 15.5 * s, 13.5 * s), fill=color)
    return img


def machine_secret():
    parts = []
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p) as f:
                parts.append(f.read().strip())
                break
        except Exception:
            pass
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                parts.append(winreg.QueryValueEx(k, "MachineGuid")[0])
        except Exception:
            pass
    parts.append(os.path.expanduser("~"))
    return ":".join(parts)


def encryptor():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(machine_secret().encode()).digest())
    return Fernet(key)


class Config:
    def __init__(self, path=None):
        if path is None:
            path = os.path.join(os.path.expanduser("~"), ".dsprice", "config.json")
        self.path = path
        self.data = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except Exception:
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def get_refresh_secs(self):
        try:
            return int(self.data.get("refresh_secs", 60))
        except Exception:
            return 60

    def set_refresh_secs(self, secs):
        self.data["refresh_secs"] = int(secs)

    def get_api_key(self):
        enc = self.data.get("api_key_enc", "")
        if not enc:
            return ""
        f = encryptor()
        if f is None:
            return enc
        try:
            return f.decrypt(enc.encode()).decode()
        except Exception:
            return ""

    def set_api_key(self, key):
        f = encryptor()
        if f is None:
            print("警告: 未安装 cryptography，API Key 将以明文保存", file=sys.stderr)
            self.data["api_key_enc"] = key
            return
        self.data["api_key_enc"] = f.encrypt(key.encode()).decode()


def pil_to_photo(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"))


def ensure_display():
    if not os.environ.get("DISPLAY"):
        sockets = sorted(glob.glob("/tmp/.X11-unix/X*"))
        if sockets:
            os.environ["DISPLAY"] = ":" + sockets[0].rsplit("X", 1)[1]
    if not os.environ.get("WAYLAND_DISPLAY"):
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if runtime:
            wl = [p for p in glob.glob(os.path.join(runtime, "wayland-*")) if not p.endswith(".lock")]
            if wl:
                os.environ["WAYLAND_DISPLAY"] = os.path.basename(wl[0])
    if not os.environ.get("DISPLAY"):
        print("无法连接图形显示环境（DISPLAY 未设置）。请从桌面环境的终端中运行本程序。", file=sys.stderr)
        sys.exit(1)


class DSPriceApp:
    def __init__(self):
        self.cmd_q = queue.Queue()
        self.tray_icon = None
        self._tray_color = None
        self._has_tray = pystray is not None
        self._hidden = True
        self._fetching = False
        self._last_balance_at = -BALANCE_RETRY_SECS
        self.cfg = Config()
        self._api_key = self.cfg.get_api_key()
        self._cfg_win = None

        self.root = tk.Tk()
        self.root.title("DeepSeek 价格指示器")
        self.root.resizable(False, False)
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}")

        if platform.system() == "Windows":
            self.family = "Microsoft YaHei UI"
        else:
            self.family = "Noto Sans CJK SC"
        bg = self.root.cget("bg")

        self.time_var = tk.StringVar()
        self.remain_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.balance_var = tk.StringVar()

        top_bar = tk.Frame(self.root)
        top_bar.pack(fill="x", padx=8, pady=(10, 0))
        self.time_lbl = tk.Label(top_bar, textvariable=self.time_var, font=(self.family, 10), fg="#888888")
        self.time_lbl.pack(side="left", expand=True)
        self.key_img = pil_to_photo(make_key_icon(18, "#7A7A7A", bg))
        self.key_btn = tk.Button(top_bar, image=self.key_img, command=self.open_config, bd=0,
                                 relief="flat", bg=bg, activebackground=bg, highlightthickness=0, cursor="hand2")
        self.key_btn.pack(side="right")
        self._tooltip(self.key_btn, "配置 API Key")

        tk.Label(self.root, text="当前时段剩余", font=(self.family, 10), fg="#888888").pack(pady=(8, 0))
        tk.Label(self.root, textvariable=self.remain_var, font=(self.family, 18, "bold")).pack(pady=(2, 4))
        tk.Label(self.root, text="现在是", font=(self.family, 10), fg="#888888").pack()
        self.title_lbl = tk.Label(self.root, textvariable=self.title_var, font=(self.family, 36, "bold"),
                                  fg=VALLEY_COLOR)
        self.title_lbl.pack(pady=(4, 2))
        self.balance_lbl = tk.Label(self.root, textvariable=self.balance_var, font=(self.family, 10), fg="#555555")
        self.balance_lbl.pack()

        window_icon = pil_to_photo(make_icon(VALLEY_COLOR, 64))
        self.root.iconphoto(True, window_icon)
        self._window_icon = window_icon

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._place_bottom_right()
        self.root.bind("<Map>", self._on_map)
        if self._has_tray:
            self.root.withdraw()
        else:
            self._hidden = False

        if self._api_key:
            self.balance_var.set("余额: 获取中…")
        else:
            self.balance_var.set("未配置 API Key，余额不可用")

    def _tooltip(self, widget, text):
        tip = {"win": None}

        def on_enter(_):
            if tip["win"] is not None:
                return
            w = tk.Toplevel(widget)
            w.overrideredirect(True)
            w.geometry(f"+{widget.winfo_rootx() + 8}+{widget.winfo_rooty() + widget.winfo_height() + 6}")
            tk.Label(w, text=text, bg="#333333", fg="#ffffff", font=(self.family, 9), padx=6, pady=3).pack()
            w.attributes("-topmost", True)
            tip["win"] = w

        def on_leave(_):
            if tip["win"] is not None:
                tip["win"].destroy()
                tip["win"] = None

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def _on_close(self):
        if self._has_tray:
            self.hide_window()
        else:
            self.root.destroy()

    def start_tray(self):
        if not self._has_tray:
            return
        try:
            self.tray_icon = pystray.Icon(
                "dsprice",
                make_icon(VALLEY_COLOR),
                "DeepSeek 价格指示器",
                menu=pystray.Menu(
                    pystray.MenuItem("显示/隐藏", lambda: self.cmd_q.put("toggle"), default=True),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("刷新余额", lambda: self.cmd_q.put("refresh_balance")),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("配置 API Key", lambda: self.cmd_q.put("open_config")),
                    pystray.MenuItem("退出", lambda: self.cmd_q.put("quit")),
                ),
            )
        except Exception:
            self.tray_icon = None
            self._has_tray = False
            self._hidden = False
            return
        threading.Thread(target=self._run_tray, daemon=True).start()

    def _run_tray(self):
        try:
            self.tray_icon.run()
        except Exception:
            self.cmd_q.put("tray_failed")

    def update(self):
        now = beijing_now()
        is_peak, remaining = get_status(now)
        self.time_var.set(f"{now:%Y-%m-%d}  {WEEKDAYS[now.weekday()]}  {now:%H:%M:%S}")
        self.remain_var.set(format_remaining(remaining))

        if is_peak:
            label, color = PEAK_LABEL, PEAK_COLOR
        else:
            label, color = VALLEY_LABEL, VALLEY_COLOR
        if remaining <= NEAR_END:
            color = WARN_COLOR
        self.title_var.set(label)
        self.title_lbl.config(fg=color)

        if self.tray_icon is not None and color != self._tray_color:
            self._tray_color = color
            self.tray_icon.icon = make_icon(color)

        if self._api_key and not self._fetching and time.monotonic() - self._last_balance_at >= self.cfg.get_refresh_secs():
            self.fetch_balance()

        self.root.after(1000, self.update)

    def poll_commands(self):
        try:
            while True:
                cmd = self.cmd_q.get_nowait()
                if cmd == "toggle":
                    self.toggle_window()
                elif cmd == "quit":
                    if self.tray_icon is not None:
                        self.tray_icon.stop()
                    self.root.destroy()
                    return
                elif cmd == "tray_failed":
                    self.show_window()
                elif cmd == "refresh_balance":
                    self.on_refresh()
                elif cmd == "open_config":
                    self.open_config()
                elif isinstance(cmd, tuple) and cmd[0] == "balance":
                    ok, text = cmd[1]
                    self._fetching = False
                    if ok:
                        self.balance_var.set("余额: " + text)
                        self.balance_lbl.config(fg="#555555")
                    else:
                        self.balance_var.set("余额: " + text)
                        self.balance_lbl.config(fg="#B00020")
                        self._last_balance_at = time.monotonic() + BALANCE_RETRY_SECS - self.cfg.get_refresh_secs()
        except queue.Empty:
            pass
        self.root.after(100, self.poll_commands)

    def show_window(self):
        self._place_bottom_right()
        self.root.deiconify()
        self.root.after(60, self._place_bottom_right)
        self.root.after(250, self._place_bottom_right)
        self.root.lift()
        self.root.focus_force()
        self._hidden = False

    def _on_map(self, _event):
        self.root.after(60, self._place_bottom_right)

    def _place_bottom_right(self, win=None, w=None, h=None, margin=EDGE_MARGIN):
        if win is None:
            win = self.root
        win.update_idletasks()
        if w is None:
            w = win.winfo_width()
            if w <= 1:
                w = WINDOW_W
        if h is None:
            h = win.winfo_height()
            if h <= 1:
                h = WINDOW_H
        x = max(0, win.winfo_screenwidth() - w - margin)
        y = max(0, win.winfo_screenheight() - h - margin)
        win.geometry(f"+{x}+{y}")

    def hide_window(self):
        self.root.withdraw()
        self._hidden = True

    def toggle_window(self):
        if self._hidden:
            self.show_window()
        else:
            self.hide_window()

    def on_refresh(self):
        if not self._api_key:
            self.open_config()
        else:
            self.fetch_balance()

    def fetch_balance(self):
        if not self._api_key or self._fetching:
            return
        self._fetching = True
        self._last_balance_at = time.monotonic()
        self.balance_var.set("余额: 获取中…")
        threading.Thread(target=self._balance_worker, args=(self._api_key,), daemon=True).start()

    def _balance_worker(self, api_key):
        try:
            req = urllib.request.Request(
                BALANCE_URL,
                headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            infos = data.get("balance_infos") or []
            if not infos:
                self.cmd_q.put(("balance", (False, "数据为空")))
            else:
                self.cmd_q.put(("balance", (True, format_balance(infos))))
        except urllib.error.HTTPError as e:
            msg = f"请求失败 (HTTP {e.code})"
            if e.code == 401:
                msg = "API Key 无效 (HTTP 401)"
            self.cmd_q.put(("balance", (False, msg)))
        except Exception:
            self.cmd_q.put(("balance", (False, "网络错误")))

    def open_config(self):
        if self._cfg_win is not None and self._cfg_win.winfo_exists():
            self._cfg_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("API 配置")
        win.resizable(False, False)
        win.geometry("360x230")
        win.transient(self.root)
        self._place_bottom_right(win, w=360, h=230, margin=EDGE_MARGIN)
        win.bind("<Map>", lambda _e: win.after(60, lambda: self._place_bottom_right(win, w=360, h=230, margin=EDGE_MARGIN)))

        tk.Label(win, text="DeepSeek API Key:", font=(self.family, 10)).pack(anchor="w", padx=12, pady=(12, 4))
        key_entry = tk.Entry(win, show="*", width=44)
        key_entry.pack(padx=12)
        key_entry.insert(0, self._api_key)

        show_var = tk.BooleanVar(value=False)

        def toggle_show():
            key_entry.config(show="" if show_var.get() else "*")

        tk.Checkbutton(win, text="显示密钥", variable=show_var, command=toggle_show,
                       font=(self.family, 9)).pack(anchor="w", padx=12)

        tk.Label(win, text="余额刷新周期:", font=(self.family, 10)).pack(anchor="w", padx=12, pady=(8, 2))
        period = tk.IntVar(value=self.cfg.get_refresh_secs())
        if period.get() not in (30, 60, 300):
            period.set(60)
        row = tk.Frame(win)
        row.pack(anchor="w", padx=12)
        for val, label in REFRESH_OPTIONS:
            tk.Radiobutton(row, text=label, variable=period, value=val, font=(self.family, 9)).pack(side="left", padx=(0, 10))

        tk.Label(win, text="密钥将加密后保存在用户目录；未配置则余额功能不可用",
                 font=(self.family, 9), fg="#888888").pack(pady=(8, 0))

        btns = tk.Frame(win)
        btns.pack(pady=6)

        def open_github(_e=None):
            import webbrowser
            webbrowser.open("https://github.com/Linming-XHL/dsprice")

        github_lbl = tk.Label(win, text="GitHub: github.com/Linming-XHL/dsprice",
                              font=(self.family, 9), fg="#0366D6", cursor="hand2")
        github_lbl.pack(side="bottom", pady=(0, 10))
        github_lbl.bind("<Button-1>", open_github)

        def save():
            key = key_entry.get().strip()
            self.cfg.set_refresh_secs(period.get())
            if key:
                self.cfg.set_api_key(key)
            else:
                self.cfg.data.pop("api_key_enc", None)
            self.cfg.save()
            self._api_key = self.cfg.get_api_key()
            win.destroy()
            self._last_balance_at = -BALANCE_RETRY_SECS
            if self._api_key:
                self.fetch_balance()
            else:
                self.balance_var.set("未配置 API Key，余额不可用")
                self.balance_lbl.config(fg="#888888")

        tk.Button(btns, text="保存", width=8, command=save).pack(side="left", padx=8)
        tk.Button(btns, text="跳过", width=8, command=win.destroy).pack(side="left", padx=8)

        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.grab_set()
        self._cfg_win = win


def main():
    ensure_display()
    app = DSPriceApp()
    app.start_tray()
    app.update()
    app.poll_commands()
    if not app._api_key:
        app.root.after(600, app.open_config)
    app.root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
