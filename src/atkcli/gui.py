"""atkcli 图形界面。除脚本页显式执行外，其余页面只读。"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .errors import SessionError
from .files import json_text, read_text, write_json_new
from .reports import check_cadence, parse_report
from .scripting import COMMANDS, TEMPLATES, parse_script, run_script
from .session import Config, Session, default_home, doctor, seconds, status, watch
from .xmlcheck import compare_xml

ENCODINGS = ("auto", "utf-8-sig", "utf-16", "gb18030")
ABOUT = """ATK 会话工具 {version}

用途
  · 接管本机已打开的 ATK，持有只读 Connect 连接和协作锁
  · 软件无响应时区分“原进程还在”与“原进程已消失”
  · 离线核对已导出的报表、采样间隔与 XML 显示参数
  · 脚本页逐条发送 Connect 命令，经确认后修改场景

边界
  · 实例、报表、XML 页只读；只有脚本页的“执行脚本”会修改场景
  · 脚本不可撤销：New/Load/Unload/Save/SaveAs 会改变或覆盖内容，请先备份
  · 不启动 GUI（除非勾选“无实例时启动空 GUI”并确认）
  · 不能证明仿真完成或 ATK 的关闭原因

实时接管需要
  · Windows x64、CPython 3.12（打包版本已内置）
  · 本机 ATK 安装目录，默认取 ATK_HOME 或 D:\\ATK-4.2.0-rc.2
  · 厂商 Connect SDK 由安装目录加载，exe 内不包含 SDK/DLL/许可证

退出码
  0 成功   2 参数或输入错误   10 等待超时   20 原实例消失
  21 未发现实例   31 数据不足/未通过   40 锁被占用
  41 实例或场景冲突   42 SDK 或权限失败   130 用户中断

锁
  所有客户端共用 %TEMP%\\atkcli.lock；同一实例同一时间只能被一个客户端接管。
  关闭本窗口只会断开客户端并释放锁，不会关闭 ATK。
"""
LOG_EVENTS = ("selected", "ready", "heartbeat", "error", "interrupted")


def _atomic_text(widget, text):
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text)
    widget.configure(state="disabled")


class App:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.stop_event = threading.Event()
        self.last_json = ""
        self.action_buttons = []
        self.stop_buttons = []
        self._ref_win = None
        root.title(f"ATK 会话工具 {__version__}")
        width = min(1120, root.winfo_screenwidth() - 80)
        height = min(920, root.winfo_screenheight() - 120)
        root.geometry(f"{width}x{height}")
        root.minsize(920, 760)
        root.protocol("WM_DELETE_WINDOW", self._closing)
        try:
            style = ttk.Style()
            style.theme_use("clam")
            style.configure("TNotebook.Tab", padding=(12, 5))
            style.map("TNotebook.Tab", background=[("selected", "#ffffff")])
        except tk.TclError:
            pass
        self.var_home = tk.StringVar(value=str(default_home()))
        self.var_port = tk.StringVar(value=os.environ.get("ATK_PORT", "6655"))
        self.var_pid = tk.StringVar()
        self.var_timeout = tk.StringVar(value="15")
        self.var_hold = tk.StringVar(value="0")
        self.var_wait_lock = tk.StringVar(value="0")
        self.var_scene = tk.StringVar()
        self.var_label = tk.StringVar()
        self.var_start = tk.BooleanVar(value=False)
        self.var_duration = tk.StringVar(value="60")
        self.var_state = tk.StringVar(value="就绪")
        self.var_report = tk.StringVar()
        self.var_encoding = tk.StringVar(value="auto")
        self.var_step = tk.StringVar()
        self.var_before = tk.StringVar()
        self.var_after = tk.StringVar()
        self.var_numeric = tk.BooleanVar(value=False)
        self.var_cmd_timeout = tk.StringVar(value="30")
        self.var_continue = tk.BooleanVar(value=False)
        self.var_ack = tk.BooleanVar(value=False)
        self._queue = queue.Queue()
        self._build()
        self.root.after(80, self._poll)

    # ---------- 界面 ----------
    def _build(self):
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="ATK 安装目录").pack(side="left")
        ttk.Entry(top, textvariable=self.var_home).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="浏览…", command=self._pick_home).pack(side="left")
        ttk.Label(top, text="端口").pack(side="left", padx=(12, 2))
        ttk.Entry(top, textvariable=self.var_port, width=8).pack(side="left")

        bottom = ttk.LabelFrame(self.root, text="结果", padding=6)
        bottom.pack(side="bottom", fill="x", padx=10, pady=(0, 8))
        bar = ttk.Frame(bottom)
        bar.pack(fill="x")
        self.state_label = ttk.Label(bar, textvariable=self.var_state, foreground="#1f4e79")
        self.state_label.pack(side="left")
        ttk.Button(bar, text="复制 JSON", command=self._copy).pack(side="right", padx=4)
        ttk.Button(bar, text="另存 JSON…", command=self._save).pack(side="right")
        self.out = tk.Text(bottom, height=8, wrap="none", font=("Consolas", 10), state="disabled")
        ys = ttk.Scrollbar(bottom, orient="vertical", command=self.out.yview)
        self.out.configure(yscrollcommand=ys.set)
        self.out.pack(side="left", fill="both", expand=True, pady=(6, 0))
        ys.pack(side="right", fill="y", pady=(6, 0))

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self._tab_instance()
        self._tab_reports()
        self._tab_xml()
        self._tab_script()
        self._tab_about()

    def _tab_instance(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="实例")

        box = ttk.LabelFrame(f, text="只读检查（不连接 ATK）", padding=10)
        box.pack(fill="x", pady=(0, 8))
        self._button(box, "SDK 自检", self.do_doctor).pack(side="left")
        self._button(box, "刷新状态", self.do_status).pack(side="left", padx=6)
        ttk.Label(box, text="自检只导入 SDK；刷新只读进程、端口与锁。").pack(side="left", padx=8)

        at = ttk.LabelFrame(f, text="接管 / 保持（只读 Connect）", padding=10)
        at.pack(fill="x", pady=(0, 8))
        row = ttk.Frame(at)
        row.pack(fill="x")
        self._field(row, "PID（空=自动）", self.var_pid, 10)
        self._field(row, "握手超时(s)", self.var_timeout, 7)
        self._field(row, "保持(s)", self.var_hold, 7)
        self._field(row, "等锁(s)", self.var_wait_lock, 7)
        row2 = ttk.Frame(at)
        row2.pack(fill="x", pady=(6, 0))
        self._field(row2, "期望场景名", self.var_scene, 22)
        self._field(row2, "占用说明", self.var_label, 30)
        ttk.Checkbutton(at, text="无实例时启动空 GUI（需要确认；已有实例一律复用）",
                        variable=self.var_start).pack(anchor="w", pady=(8, 0))
        bar = ttk.Frame(at)
        bar.pack(fill="x", pady=(8, 0))
        self._button(bar, "接管并保持", self.do_attach).pack(side="left")
        self._stop_button(bar, "停止并释放").pack(side="left", padx=6)
        ttk.Label(bar, text="退出本窗口只会断开客户端并释放锁，ATK 保持打开。").pack(side="left", padx=8)

        wa = ttk.LabelFrame(f, text="进程监控（不占锁、不连 TCP）", padding=10)
        wa.pack(fill="x", pady=(0, 8))
        row3 = ttk.Frame(wa)
        row3.pack(fill="x")
        self._field(row3, "监控时长(s)", self.var_duration, 8)
        self._button(row3, "开始监控", self.do_watch).pack(side="left", padx=(16, 0))
        ttk.Label(row3, text="按设定时长自动结束，期间每秒核对一次原进程。").pack(side="left", padx=8)

        ev = ttk.LabelFrame(f, text="事件", padding=6)
        ev.pack(fill="both", expand=True)
        self.events = tk.Text(ev, height=5, wrap="none", font=("Consolas", 9), state="disabled")
        evs = ttk.Scrollbar(ev, orient="vertical", command=self.events.yview)
        self.events.configure(yscrollcommand=evs.set)
        self.events.pack(side="left", fill="both", expand=True)
        evs.pack(side="right", fill="y")

    def _tab_reports(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="报表与采样")
        src = ttk.LabelFrame(f, text="已导出的本地文件", padding=10)
        src.pack(fill="x", pady=(0, 8))
        row = ttk.Frame(src)
        row.pack(fill="x")
        ttk.Label(row, text="输入文件").pack(side="left")
        ttk.Entry(row, textvariable=self.var_report).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="浏览…", command=self._pick_report).pack(side="left")
        row2 = ttk.Frame(src)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="编码").pack(side="left")
        ttk.Combobox(row2, textvariable=self.var_encoding, values=ENCODINGS, width=10,
                     state="readonly").pack(side="left", padx=6)
        ttk.Label(row2, text="要求步长(s，可空)").pack(side="left", padx=(16, 0))
        ttk.Entry(row2, textvariable=self.var_step, width=10).pack(side="left", padx=6)
        self._button(row2, "解析窗口表", self.do_parse).pack(side="left", padx=(16, 0))
        self._button(row2, "核验采样间隔", self.do_cadence).pack(side="left", padx=6)
        self.var_report_hint = tk.StringVar()
        ttk.Label(f, textvariable=self.var_report_hint, foreground="#555").pack(anchor="w")
        cols = ("n", "start", "end", "dur")
        self.rep_tree = ttk.Treeview(f, columns=cols, show="headings", height=12)
        for key, text, width in (("n", "#", 50), ("start", "开始 (UTC)", 260),
                                 ("end", "结束 (UTC)", 260), ("dur", "时长 (s)", 100)):
            self.rep_tree.heading(key, text=text)
            self.rep_tree.column(key, width=width, anchor="w" if key in ("start", "end") else "center")
        self.rep_tree.pack(fill="both", expand=True, pady=(6, 0))

    def _tab_xml(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="XML 比较")
        box = ttk.LabelFrame(f, text="两份已保存的文件", padding=10)
        box.pack(fill="x", pady=(0, 8))
        for text, var, picker in (("期望值文件", self.var_before, self._pick_before),
                                  ("另存后文件", self.var_after, self._pick_after)):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=text, width=12).pack(side="left")
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=6)
            ttk.Button(row, text="浏览…", command=picker).pack(side="left")
        mid = ttk.Frame(box)
        mid.pack(fill="x", pady=(6, 0))
        ttk.Label(mid, text="相对叶节点路径（每行一个，如 ./Display/Scale）").pack(side="left")
        self.var_xml_hint = tk.StringVar(value="")
        ttk.Label(mid, textvariable=self.var_xml_hint, foreground="#555").pack(side="left", padx=8)
        self.paths = tk.Text(box, height=4, font=("Consolas", 10))
        self.paths.pack(fill="x", pady=6)
        bar = ttk.Frame(box)
        bar.pack(fill="x")
        ttk.Checkbutton(bar, text="按数值比较（有限十进制/科学记数法）",
                        variable=self.var_numeric).pack(side="left")
        self._button(bar, "比较", self.do_compare).pack(side="right")
        cols = ("path", "ok", "before", "after")
        self.xml_tree = ttk.Treeview(f, columns=cols, show="headings", height=10)
        for key, text, width in (("path", "路径", 240), ("ok", "结果", 60),
                                 ("before", "期望值", 220), ("after", "回读值", 220)):
            self.xml_tree.heading(key, text=text)
            self.xml_tree.column(key, width=width)
        self.xml_tree.pack(fill="both", expand=True, pady=(4, 0))

    def _tab_script(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="脚本")
        ttk.Label(f, foreground="#a33",
                  text="脚本会修改 ATK 场景状态且不可撤销；New/Load/Unload/Save 前请先备份。"
                  ).pack(anchor="w")

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="模板").pack(side="left")
        self.var_template = tk.StringVar(value=TEMPLATES[0]["name"])
        self.template_box = ttk.Combobox(row, textvariable=self.var_template, state="readonly",
                                         values=[t["name"] for t in TEMPLATES], width=16)
        self.template_box.pack(side="left", padx=6)
        self.template_box.bind("<<ComboboxSelected>>", self._load_template)
        self.var_template_hint = tk.StringVar(value=TEMPLATES[0]["description"])
        ttk.Label(row, textvariable=self.var_template_hint, foreground="#555").pack(side="left", padx=6)
        ttk.Button(row, text="命令参考", command=self.show_reference).pack(side="right")

        ed = ttk.Frame(f)
        ed.pack(fill="both", expand=True, pady=(6, 0))
        ed.columnconfigure(0, weight=1)
        ed.rowconfigure(0, weight=1)
        self.script_editor = tk.Text(ed, height=9, wrap="none", font=("Consolas", 10), undo=True)
        ys = ttk.Scrollbar(ed, orient="vertical", command=self.script_editor.yview)
        xs = ttk.Scrollbar(ed, orient="horizontal", command=self.script_editor.xview)
        self.script_editor.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.script_editor.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self.script_editor.insert("1.0", TEMPLATES[0]["text"])

        opt = ttk.Frame(f)
        opt.pack(fill="x", pady=(6, 0))
        ttk.Label(opt, text="每命令超时(s)").pack(side="left")
        ttk.Entry(opt, textvariable=self.var_cmd_timeout, width=6).pack(side="left", padx=(4, 12))
        ttk.Checkbutton(opt, text="出错继续", variable=self.var_continue).pack(side="left")
        ttk.Checkbutton(opt, text="我了解会修改 ATK 场景", variable=self.var_ack).pack(side="left", padx=12)
        self._button(opt, "执行脚本", self.do_script).pack(side="right")
        self._stop_button(opt, "停止").pack(side="right", padx=6)

        self.var_script_hint = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.var_script_hint, foreground="#555").pack(anchor="w", pady=(6, 0))
        cols = ("i", "cmd", "arg", "ok", "elapsed", "reply")
        self.script_tree = ttk.Treeview(f, columns=cols, show="headings", height=5)
        for key, text, width in (("i", "#", 40), ("cmd", "命令", 170), ("arg", "参数", 260),
                                 ("ok", "结果", 55), ("elapsed", "耗时(s)", 70), ("reply", "返回摘要", 340)):
            self.script_tree.heading(key, text=text)
            self.script_tree.column(key, width=width, anchor="w" if key in ("cmd", "arg", "reply") else "center")
        self.script_tree.pack(fill="both", expand=True, pady=(4, 0))

    def _tab_about(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="说明")
        t = tk.Text(f, wrap="word", font=("Microsoft YaHei UI", 10), relief="flat")
        t.insert("1.0", ABOUT.format(version=__version__))
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)

    # ---------- 小部件 ----------
    def _button(self, parent, text, command):
        b = ttk.Button(parent, text=text, command=command)
        self.action_buttons.append(b)
        return b

    def _stop_button(self, parent, text):
        b = ttk.Button(parent, text=text, command=self.do_stop)
        b.state(["disabled"])
        self.stop_buttons.append(b)
        return b

    def _field(self, parent, text, var, width):
        ttk.Label(parent, text=text).pack(side="left")
        ttk.Entry(parent, textvariable=var, width=width).pack(side="left", padx=(4, 14))

    def _pick_home(self):
        path = filedialog.askdirectory(title="选择 ATK 安装目录", initialdir=self.var_home.get() or None)
        if path:
            self.var_home.set(path)

    def _pick_report(self):
        path = filedialog.askopenfilename(title="选择已导出的报表或采样文件",
                                          filetypes=[("文本/CSV", "*.txt *.csv *.log"), ("全部文件", "*.*")])
        if path:
            self.var_report.set(path)

    def _pick_before(self):
        path = filedialog.askopenfilename(title="选择期望值 XML",
                                          filetypes=[("XML", "*.xml"), ("全部文件", "*.*")])
        if path:
            self.var_before.set(path)

    def _pick_after(self):
        path = filedialog.askopenfilename(title="选择另存后的 XML",
                                          filetypes=[("XML", "*.xml"), ("全部文件", "*.*")])
        if path:
            self.var_after.set(path)

    # ---------- 公共流程 ----------
    @staticmethod
    def _number(text, name):
        try:
            return float(text)
        except (TypeError, ValueError):
            raise ValueError(f"{name} 必须是数字。") from None

    @staticmethod
    def _integer(text, name):
        try:
            return int(text)
        except (TypeError, ValueError):
            raise ValueError(f"{name} 必须是整数。") from None

    def _config(self, *, hold=0.0, wait_lock=0.0):
        text = self.var_home.get().strip()
        home = Path(text) if text else default_home()
        port_text = self.var_port.get().strip()
        port = self._integer(port_text, "端口") if port_text else int(os.environ.get("ATK_PORT", "6655"))
        pid_text = self.var_pid.get().strip()
        pid = self._integer(pid_text, "PID") if pid_text else None
        timeout = seconds(self._number(self.var_timeout.get(), "握手超时"), "握手超时", positive=True)
        hold = seconds(hold, "保持时长")
        wait_lock = seconds(wait_lock, "等锁时长")
        scene = self.var_scene.get().strip() or None
        label = self.var_label.get().strip()
        return Config(home=home, port=port, pid=pid, timeout=timeout, wait_lock=wait_lock,
                      start_if_missing=self.var_start.get(), label=label, expect_scene=scene,
                      expected_s=hold + timeout * (2 if hold else 1))

    def _run(self, title, fn, *, stoppable=False, after_show=None):
        if self.busy:
            return
        self.busy = True
        self.stop_event.clear()
        self._apply_busy(stoppable)
        self.var_state.set(f"{title}…")
        self._append_event(f"开始：{title}")

        def runner():
            try:
                result = fn()
            except SessionError as exc:
                result = exc.as_dict()
            except (ValueError, OSError) as exc:
                result = {"ok": False, "state": "INPUT_OR_IO_ERROR", "message": str(exc)}
            except Exception as exc:  # 不把后台线程异常带进界面事件循环
                result = {"ok": False, "state": "UNEXPECTED",
                          "message": f"{type(exc).__name__}: {exc}"}
            self._queue.put(("done", (result, after_show)))

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._write_event(payload)
                elif kind == "result":
                    self._add_script_row(payload)
                elif kind == "done":
                    result, after_show = payload
                    self._finish(result)
                    if after_show is not None:
                        after_show(result)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _finish(self, result):
        self.busy = False
        self._apply_busy(False)
        self._show(result)
        self._append_event("结束：" + str(result.get("state")))

    def _apply_busy(self, stoppable):
        for b in self.action_buttons:
            b.state(["disabled"] if self.busy else ["!disabled"])
        for b in self.stop_buttons:
            b.state(["!disabled"] if self.busy and stoppable else ["disabled"])

    def _show(self, result):
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        self.last_json = text
        _atomic_text(self.out, text)
        tag = result.get("state") or result.get("status") or "-"
        if result.get("ok") is True:
            self.var_state.set(f"成功  {tag}")
        else:
            code = result.get("exit_code")
            self.var_state.set(f"未通过  {tag}" + (f"  exit={code}" if code is not None else ""))

    def _append_event(self, line):
        self._queue.put(("log", line))

    def _write_event(self, line):
        self.events.configure(state="normal")
        self.events.insert("end", time.strftime("%H:%M:%S ") + line + "\n")
        self.events.see("end")
        self.events.configure(state="disabled")

    def _event(self, obj):
        if obj.get("event") in LOG_EVENTS:
            extra = {k: v for k, v in obj.items() if k in ("pid", "mode", "alive", "cpu_s", "elapsed_s")}
            self._append_event(f"{obj['event']}  {json_text(extra)}")

    def _copy(self):
        if not self.last_json:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_json)
        self.var_state.set("已复制 JSON 到剪贴板")

    def _save(self):
        if not self.last_json:
            messagebox.showinfo("另存 JSON", "当前没有可保存的结果。")
            return
        path = filedialog.asksaveasfilename(title="保存结果（必须是新文件）", defaultextension=".json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            write_json_new(Path(path), json.loads(self.last_json))
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.var_state.set(f"已保存：{path}")

    def _closing(self):
        if self.busy and not messagebox.askyesno("正在运行", "任务尚未结束，确定退出吗？"):
            return
        self.stop_event.set()
        self.root.destroy()

    # ---------- 实例 ----------
    def do_doctor(self):
        try:
            c = self._config()
        except (ValueError, OSError) as exc:
            self._show({"ok": False, "state": "INPUT_OR_IO_ERROR", "message": str(exc)})
            return
        self._run("SDK 自检", lambda: doctor(c))

    def do_status(self):
        try:
            c = self._config()
        except (ValueError, OSError) as exc:
            self._show({"ok": False, "state": "INPUT_OR_IO_ERROR", "message": str(exc)})
            return
        self._run("刷新状态", lambda: status(c))

    def do_attach(self):
        try:
            hold = seconds(self._number(self.var_hold.get() or 0, "保持时长"), "保持时长")
            wait_lock = seconds(self._number(self.var_wait_lock.get() or 0, "等锁时长"), "等锁时长")
            c = self._config(hold=hold, wait_lock=wait_lock)
        except (ValueError, OSError) as exc:
            self._show({"ok": False, "state": "INPUT_OR_IO_ERROR", "message": str(exc)})
            return
        if c.start_if_missing and not messagebox.askyesno(
                "启动空 GUI", "当前没有可核验的实例时，将启动一个新的空 ATK 窗口。\n确认已获准在该时段使用？"):
            return
        stop = self.stop_event
        self._run("接管", lambda: self._attach_work(c, hold, stop), stoppable=True)

    def _attach_work(self, c, hold, stop):
        t0 = time.monotonic()
        sample = None
        with Session(c, on_event=self._event) as sess:
            if hold:
                end = t0 + hold
                while not stop.is_set():
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    sample = sess.hold(min(0.5, left))
                if stop.is_set():
                    result = dict(sess.last)
                    result.update(hold_s=round(time.monotonic() - t0, 3), stopped=True)
                    return result
                result = sess.refresh()
                sample = sample or {}
            else:
                result = dict(sess.last)
        result.update(hold_s=hold, **sample)
        return result

    def do_watch(self):
        try:
            duration = seconds(self._number(self.var_duration.get(), "监控时长"), "监控时长")
            c = self._config()
        except (ValueError, OSError) as exc:
            self._show({"ok": False, "state": "INPUT_OR_IO_ERROR", "message": str(exc)})
            return
        self._run("进程监控", lambda: watch(c, duration=duration, on_event=self._event))

    def do_stop(self):
        self.stop_event.set()
        self.var_state.set("正在停止并释放…")

    # ---------- 报表 ----------
    def _report_input(self):
        path = Path(self.var_report.get().strip())
        if not self.var_report.get().strip():
            raise ValueError("请先选择输入文件。")
        encoding = self.var_encoding.get()
        text = read_text(path, encoding)
        return text

    def do_parse(self):
        try:
            text = self._report_input()
        except (ValueError, OSError) as exc:
            self._show({"ok": False, "state": "INPUT_OR_IO_ERROR", "message": str(exc)})
            return
        self._run("解析窗口表", lambda: parse_report(text), after_show=self._fill_windows)

    def do_cadence(self):
        try:
            text = self._report_input()
            step_text = self.var_step.get().strip()
            step = self._number(step_text, "要求步长") if step_text else None
        except (ValueError, OSError) as exc:
            self._show({"ok": False, "state": "INPUT_OR_IO_ERROR", "message": str(exc)})
            return

        def hint(result):
            if result.get("ok") is not None and "n" in result:
                self.var_report_hint.set(
                    f"n={result['n']}  最小={result['min_step_s']}s  最大={result['max_step_s']}s  "
                    f"异常间隔={result['bad_intervals']}")
            else:
                self.var_report_hint.set("")

        self._run("核验采样间隔", lambda: check_cadence(text, step), after_show=hint)

    def _fill_windows(self, result):
        self.rep_tree.delete(*self.rep_tree.get_children())
        if not result.get("ok"):
            self.var_report_hint.set("")
            return
        windows = result.get("windows") or []
        for i, row in enumerate(windows, 1):
            self.rep_tree.insert("", "end", values=(i, row["start_utc"], row["end_utc"], row["dur_s"]))
        stats = result.get("statistics") or {}
        self.var_report_hint.set(f"解析到 {len(windows)} 个窗口；统计项 {len(stats)} 项")

    # ---------- XML ----------
    def do_compare(self):
        try:
            before = Path(self.var_before.get().strip())
            after = Path(self.var_after.get().strip())
            if not self.var_before.get().strip() or not self.var_after.get().strip():
                raise ValueError("请选择两份 XML 文件。")
            paths = [line.strip() for line in self.paths.get("1.0", "end").splitlines() if line.strip()]
            if not paths:
                raise ValueError("请至少填写一条相对叶节点路径。")
            numeric = self.var_numeric.get()
        except (ValueError, OSError) as exc:
            self._show({"ok": False, "state": "INPUT_OR_IO_ERROR", "message": str(exc)})
            return
        self._run("比较 XML", lambda: compare_xml(before, after, paths, numeric), after_show=self._fill_checks)

    def _fill_checks(self, result):
        self.xml_tree.delete(*self.xml_tree.get_children())
        checks = result.get("checks") or []
        if not checks:
            self.var_xml_hint.set("")
            return
        for check in checks:
            self.xml_tree.insert("", "end", values=(
                check.get("path"), "一致" if check.get("ok") else "不一致",
                check.get("before"), check.get("after")))
        passed = sum(1 for c in checks if c.get("ok"))
        self.var_xml_hint.set(f"通过 {passed}/{len(checks)}")

    # ---------- 脚本 ----------
    def _load_template(self, event=None):
        for item in TEMPLATES:
            if item["name"] == self.var_template.get():
                if (self.script_editor.get("1.0", "end").strip()
                        and not messagebox.askyesno("载入模板", "将覆盖编辑器中的内容，确定吗？")):
                    return
                self.script_editor.delete("1.0", "end")
                self.script_editor.insert("1.0", item["text"])
                self.var_template_hint.set(item["description"])
                return

    def do_script(self):
        if not self.var_ack.get():
            messagebox.showwarning("需要确认", "脚本会修改 ATK 场景，请先勾选“我了解会修改 ATK 场景”。")
            return
        try:
            commands = parse_script(self.script_editor.get("1.0", "end"))
            timeout = seconds(self._number(self.var_cmd_timeout.get(), "每命令超时"), "每命令超时", positive=True)
            c = self._config()
        except (ValueError, OSError) as exc:
            self._show({"ok": False, "state": "INPUT_OR_IO_ERROR", "message": str(exc)})
            return
        if c.start_if_missing and not messagebox.askyesno(
                "启动空 GUI", "当前没有可核验的实例时，将启动一个新的空 ATK 窗口。\n确认已获准在该时段使用？"):
            return
        continue_on_error = self.var_continue.get()
        self.script_tree.delete(*self.script_tree.get_children())
        self.var_ack.set(False)
        self.var_script_hint.set(f"准备执行 {len(commands)} 条命令…")
        self._run("执行脚本",
                  lambda: run_script(c, commands, timeout=timeout, continue_on_error=continue_on_error,
                                     should_stop=self.stop_event.is_set,
                                     on_result=self._script_result, on_event=self._event),
                  stoppable=True, after_show=self._after_script)

    def _script_result(self, record):
        self._queue.put(("result", record))

    def _add_script_row(self, record):
        reply = " ".join(str(record.get("reply") or "").split())
        if len(reply) > 240:
            reply = reply[:240] + "…"
        command = record.get("command") or "sleep"
        argument = record.get("argument") or ""
        self.script_tree.insert("", "end", values=(
            (record.get("index") or 0) + 1, command, argument,
            "成功" if record.get("ok") else "失败",
            record.get("elapsed_s", ""), reply))

    def _after_script(self, result):
        executed = result.get("executed")
        if executed is None:
            self.var_script_hint.set("")
            return
        self.var_script_hint.set(
            f"执行 {executed} 条，失败 {result.get('failed', 0)} 条，用时 {result.get('elapsed_s')} s")
        rows = result.get("results") or []
        if len(self.script_tree.get_children()) < len(rows):
            self.script_tree.delete(*self.script_tree.get_children())
            for record in rows:
                self._add_script_row(record)

    def show_reference(self):
        if self._ref_win is not None and self._ref_win.winfo_exists():
            self._ref_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("Connect 命令参考")
        win.geometry("900x620")
        self._ref_win = win
        text = tk.Text(win, wrap="word", font=("Microsoft YaHei UI", 9), padx=10, pady=8)
        ys = ttk.Scrollbar(win, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=ys.set)
        text.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        lines = ["语法摘自本机 ATK 帮助（Connect 对象命令库）。参数含空格时用双引号括起，路径区分大小写。", ""]
        for name, syntax, example, note in COMMANDS:
            lines += [name, f"  语法：{syntax}", f"  示例：{example}", f"  说明：{note}", ""]
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--sdk-worker"]:
        from ._sdk_worker import main as worker_main
        return worker_main(args[1:])
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
