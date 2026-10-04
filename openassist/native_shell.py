"""Mirally's desktop presentation of native OpenClaw sessions."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from urllib.parse import urlsplit
import webbrowser
from uuid import uuid4

from openassist.branding import APP_NAME, apply_window_icon, brand_photo, prepare_process_identity
from openassist.edge_companion import EdgeCompanion
from openassist.gateway_transport import GatewayTransport
from openassist.presentation import DEFAULT_THEME, THEMES, choose_ui_font, public_text
from openassist.session_list import SessionList
from openassist.settings_view import SettingsView
from openassist.startup import StartupReveal
from openassist.widgets import ButtonStyles
from openassist.window_session import WindowSession, enable_dpi_awareness


TOOL_TITLES = {
    'read': '读取文件', 'write': '写入文件', 'edit': '修改文件',
    'exec': '执行后台命令', 'process': '查看后台进程',
    'web_fetch': '读取网页', 'web_search': '搜索网络', 'browser': '使用独立浏览器',
    'pdf': '分析 PDF', 'image': '分析图片', 'memory_search': '检索记忆',
    'memory_get': '读取记忆', 'sessions_spawn': '委派任务',
}


def native_text(message):
    content = message.get('content', [])
    if isinstance(content, str):
        return content
    return '\n'.join(str(part.get('text', '')) for part in content
                     if isinstance(part, dict) and part.get('type') == 'text') if isinstance(content, list) else ''


class NativeShell:
    def __init__(self, product_root: Path, *, startup=True, transport=None, visible=True):
        self.product_root = product_root
        self.demo = False
        self.connected = False
        self.can_stop = False
        self._closing = False
        self._phase = 'idle'
        self._key = None
        self._selecting_key = None
        self._initial_selection = True
        self._snapshot = {}
        self._sessions = []
        self._drafts = {}
        self._pending = {}
        self._retry_sends = {}
        self._rendered_chunks = []
        self._drawer = None
        self._wide = False
        self._started = None
        self._last_layout = None
        self.preference_path = product_root / 'config/openassist/openclaw/mirally-client/interface.json'
        try:
            self.preferences = json.loads(self.preference_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.preferences = {}
        if not isinstance(self.preferences, dict):
            self.preferences = {}
        self.theme_key = self.preferences.get('theme', DEFAULT_THEME)
        if self.theme_key not in THEMES:
            self.theme_key = DEFAULT_THEME
        self.theme = THEMES[self.theme_key]
        enable_dpi_awareness()
        prepare_process_identity()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        apply_window_icon(self.root)
        self.body_font = choose_ui_font(self.root)
        self.scale = max(1.0, self.root.winfo_fpixels('1i') / 96)
        # Reuse HWND geometry/taskbar helpers only: no input monitor, hotkeys,
        # capture handshake, edit leases, passthrough, or foreground restoration.
        self.session = WindowSession(self.root)
        self._topmost = tk.BooleanVar(value=bool(self.preferences.get('topmost', True)))
        self._reduced = tk.BooleanVar(value=bool(self.preferences.get('reduced_motion', False)))
        self._theme_name = tk.StringVar(value=self.theme.name)
        self.status_var = tk.StringVar(value='正在连接')
        self.task_var = tk.StringVar(value='一起想清楚，也一起把事情做好。')
        self.time_var = tk.StringVar()
        self.session_title = tk.StringVar(value='会话空间')
        self.hint_var = tk.StringVar(value='Ctrl + Enter 发送 · 工作中也可以随时补充')
        self.connection_var = tk.StringVar(value='连接 OpenClaw…')
        self._frames, self._labels, self._buttons = [], [], []
        self.button_styles = ButtonStyles(self.root, self.scale)
        self.transport = transport or GatewayTransport(product_root)
        self.startup = None
        self._build()
        self.edge = EdgeCompanion(self, background=True)
        self._apply_theme()
        self._pin()
        width = min(round(600 * self.scale), self.root.winfo_screenwidth() - 50)
        height = min(round(800 * self.scale), self.root.winfo_screenheight() - 90)
        self.root.geometry(f'{width}x{height}+{max(0,(self.root.winfo_screenwidth()-width)//2)}+{max(0,(self.root.winfo_screenheight()-height)//2)}')
        self.root.minsize(min(width, round(490 * self.scale)), min(height, round(580 * self.scale)))
        self.root.protocol('WM_DELETE_WINDOW', self._close)
        self.root.bind('<Control-Return>', self._submit)
        self.root.bind('<Escape>', self._escape)
        self.root.bind('<Map>', self._mapped, add='+')
        if visible:
            self.root.deiconify()
        if startup:
            self.startup = StartupReveal(self.root, self.theme, scale=self.scale,
                                         body_font=self.body_font, reduced_motion=self._reduced.get())
            self.root.after_idle(self.startup.start)
        self.transport.start()
        self._poll_job = self.root.after(30, self._poll)

    def _frame(self, parent, *, surface=False, **kwargs):
        frame = tk.Frame(parent, borderwidth=0, **kwargs)
        self._frames.append((frame, surface))
        return frame

    def _label(self, parent, *, muted=False, surface=False, **kwargs):
        kwargs.setdefault('font', (self.body_font, 10))
        label = tk.Label(parent, anchor='w', borderwidth=0, **kwargs)
        self._labels.append((label, muted, surface))
        return label

    def _button(self, parent, text, command, *, role='quiet', **kwargs):
        button = ttk.Button(parent, text=text, command=command, cursor='hand2', **kwargs)
        self._buttons.append((button, role))
        return button

    def _text(self, parent, **kwargs):
        text = ScrolledText(parent, **kwargs)
        text.vbar.destroy()
        text.vbar = ttk.Scrollbar(text.frame, orient='vertical', command=text.yview,
                                  style='Mirally.Settings.Vertical.TScrollbar')
        text.vbar.pack(side='right', fill='y')
        text.configure(yscrollcommand=text.vbar.set)
        return text

    def _build(self):
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.main = self._frame(self.root)
        self.main.grid(row=0, column=0, sticky='nsew')
        self.main.rowconfigure(0, weight=1)
        self.main.columnconfigure(1, weight=1)
        self.sidebar = self._frame(self.main, surface=True, padx=20, pady=26)
        self.sidebar.pack_propagate(False)
        self._label(self.sidebar, text='会话空间', surface=True, font=(self.body_font, 17, 'bold')).pack(fill='x', pady=(0, 16))
        self.sidebar_new = self._button(self.sidebar, '＋ 新建会话', self._new_conversation, role='surface.outline')
        self.sidebar_new.pack(fill='x', pady=(0, 16))
        self._label(self.sidebar, textvariable=self.connection_var, surface=True, muted=True,
                    font=(self.body_font, 9)).pack(fill='x', side='bottom', pady=(12, 0))
        self.sidebar_list = SessionList(self.sidebar, self)
        self.sidebar_list.pack(fill='both', expand=True)

        self.content = self._frame(self.main, padx=22, pady=18)
        self.content.grid(row=0, column=1, sticky='nsew')
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(3, weight=1)
        header = self._frame(self.content)
        header.grid(row=0, column=0, sticky='ew', pady=(0, 16))
        self.mark = tk.Canvas(header, highlightthickness=0, width=round(34*self.scale), height=round(34*self.scale))
        self.mark.pack(side='left', padx=(0, 8))
        self._label(header, text=APP_NAME, font=('Segoe UI', 17, 'bold')).pack(side='left')
        for text, command in [('设置', lambda: self._show_drawer('settings')),
                              ('历史', lambda: self._show_drawer('history')),
                              ('浮窗', self._return_to_edge)]:
            self._button(header, text, command, width=-1).pack(side='right', padx=(5, 0))
        task = self._frame(self.content)
        task.grid(row=1, column=0, sticky='ew')
        self._label(task, textvariable=self.status_var, font=(self.body_font, 10, 'bold')).pack(side='left')
        self._label(task, textvariable=self.time_var, muted=True, font=('Segoe UI', 9)).pack(side='right')
        self.task_label = self._label(self.content, textvariable=self.task_var, muted=True)
        self.task_label.grid(row=2, column=0, sticky='ew', pady=(7, 12))
        self.task_label.bind('<Configure>', lambda event: self.task_label.configure(wraplength=max(100,event.width)))

        self.stack = self._frame(self.content)
        self.stack.grid(row=3, column=0, sticky='nsew')
        self.stack.rowconfigure(0, weight=1)
        self.stack.columnconfigure(0, weight=1)
        self.chat = self._frame(self.stack)
        self.chat_text = self._text(self.chat, wrap='word', state='disabled', borderwidth=0,
                                     highlightthickness=0, height=8, padx=12, pady=12,
                                     font=(self.body_font, 11), spacing3=8)
        self.chat_text.pack(fill='both', expand=True)
        self.chat.grid(row=0, column=0, sticky='nsew')
        self.history = self._frame(self.stack, surface=True, padx=12, pady=12)
        self._label(self.history, text='历史会话', surface=True, font=(self.body_font, 16, 'bold')).pack(fill='x', pady=(0, 10))
        self.history_new = self._button(self.history, '＋ 新建会话', self._new_conversation, role='surface.outline')
        self.history_new.pack(fill='x', pady=(0, 10))
        self.history_list = SessionList(self.history, self)
        self.history_list.pack(fill='both', expand=True)
        self.settings = SettingsView(self.stack, self)
        self.settings.note.configure(text='外观在这里调整。模型、记忆、工具、插件和权限通过原生界面管理。')
        self.records = self._frame(self.stack)
        self._label(self.records, text='工具记录', font=(self.body_font, 16, 'bold')).pack(fill='x', pady=(0, 10))
        self.record_text = self._text(self.records, wrap='word', state='disabled', borderwidth=0,
                                       highlightthickness=0, font=(self.body_font, 10))
        self.record_text.pack(fill='both', expand=True)

        self.interaction = self._frame(self.content, surface=True, padx=12, pady=10)
        self.interaction_label = self._label(self.interaction, surface=True, text='', justify='left')
        self.interaction_label.pack(fill='x', pady=(0, 6))
        self._button(self.interaction, '到原生界面处理 ↗', self._open_native, role='surface.outline').pack(anchor='w')

        bottom = self._frame(self.content)
        bottom.grid(row=5, column=0, sticky='ew', pady=(12, 0))
        toolbar = self._frame(bottom)
        toolbar.pack(fill='x', pady=(0, 10))
        self._button(toolbar, '工具记录', lambda: self._show_drawer('records'), width=-1).pack(side='left')
        self._button(toolbar, '原生界面 ↗', self._open_native, width=-1).pack(side='right')
        self.stop_button = self._button(toolbar, '■ 停止', self._stop, role='outline', width=-1)
        self.stop_button.pack(side='right', padx=(0, 8))
        compose = self._frame(bottom, surface=True, padx=12, pady=10)
        compose.pack(fill='x')
        self.composer = tk.Text(compose, height=3, width=1, relief='flat', borderwidth=0,
                                highlightthickness=0, wrap='word', font=(self.body_font, 11), undo=True)
        self.composer.pack(side='left', fill='both', expand=True)
        self.send_button = self._button(compose, '↑', self._submit, role='surface.primary', width=2)
        self.send_button.pack(side='right', padx=(10, 0))
        self.hint_label = self._label(bottom, textvariable=self.hint_var, muted=True, justify='left', font=(self.body_font, 8))
        self.hint_label.pack(fill='x', pady=(9, 0))
        self.main.bind('<Configure>', self._layout_workspace)
        self._render_chat()
        self._update_controls()

    def _save_preferences(self):
        value = {'theme': self.theme_key, 'topmost': self._topmost.get(),
                 'reduced_motion': self._reduced.get(), 'session': self._key or self.preferences.get('session')}
        try:
            self.preference_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.preference_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.preference_path)
        except OSError:
            self.hint_var.set('界面偏好暂时无法保存。')

    def _apply_theme(self):
        theme = self.theme
        self.button_styles.apply(self.theme_key, theme)
        self.root.configure(bg=theme.background)
        self.session.set_native_background(theme.background)
        for frame, surface in self._frames:
            frame.configure(bg=theme.surface if surface else theme.background)
        for label, muted, surface in self._labels:
            label.configure(bg=theme.surface if surface else theme.background, fg=theme.muted if muted else theme.text)
        for button, role in self._buttons:
            button.configure(style=f'{self.theme_key}.{role}.TButton')
        self.composer.configure(bg=theme.surface, fg=theme.text, insertbackground=theme.accent,
                                selectbackground=theme.tint, selectforeground=theme.text)
        for widget in (self.chat_text, self.record_text):
            widget.configure(bg=theme.background, fg=theme.text, selectbackground=theme.tint, selectforeground=theme.text)
        self.chat_text.tag_configure('role', foreground=theme.accent, font=(self.body_font, 9, 'bold'), spacing1=14, spacing3=6)
        self.chat_text.tag_configure('user', background=theme.tint, lmargin1=12, lmargin2=12, spacing1=8, spacing3=8)
        self.chat_text.tag_configure('muted', foreground=theme.muted, font=(self.body_font, 9))
        self.chat_text.tag_configure('tool', foreground=theme.muted, font=(self.body_font, 9), lmargin1=10, lmargin2=10)
        self.mark.configure(bg=theme.background)
        self.mark.delete('all')
        size = round(34*self.scale)
        self._brand_image = brand_photo(self.root, size, background=theme.background)
        self.mark.create_image(size//2, size//2, image=self._brand_image)
        self.sidebar_list.draw()
        self.history_list.draw()
        self.settings.apply_theme()
        if hasattr(self, 'edge'):
            self.edge.apply_theme()

    def _choose_theme(self, _event=None):
        self.theme_key = next(key for key, value in THEMES.items() if value.name == self._theme_name.get())
        self.theme = THEMES[self.theme_key]
        self._apply_theme()
        self._save_preferences()

    def _motion(self):
        if self._reduced.get():
            if self.startup:
                self.startup.finish()
            self.edge.settle()
        self._save_preferences()

    def _pin(self):
        self.session.topmost = self._topmost.get()
        self.root.attributes('-topmost', self._topmost.get())
        self._save_preferences()

    def _mapped(self, event):
        if event.widget is self.root and not self._closing:
            self.session.set_native_background(self.theme.background)

    def _layout_workspace(self, _event=None):
        if not hasattr(self, 'hint_label') or self._closing or (hasattr(self,'edge') and self.edge.mode != 'workspace'):
            return
        width, height = self.main.winfo_width(), self.main.winfo_height()
        if width < 20 or self._last_layout == (width, height):
            return
        self._last_layout = (width, height)
        self._wide = width >= round(980*self.scale)
        side_width = min(round(300*self.scale), width//4) if self._wide else 0
        if self._wide:
            self.sidebar.configure(width=side_width)
            self.sidebar.grid(row=0, column=0, sticky='nsew')
            if self._drawer == 'history':
                self._show_drawer(None)
        else:
            self.sidebar.grid_remove()
        padding = round((30 if self._wide else 18)*self.scale)
        self.content.configure(padx=padding)
        self.hint_label.configure(wraplength=max(100,width-side_width-2*padding))
        self.interaction_label.configure(wraplength=max(100,width-side_width-2*padding-24))
        self.chat_text.configure(font=(self.body_font,12 if self._wide else 11), padx=round((24 if self._wide else 8)*self.scale))

    def _show_drawer(self, name):
        if name == self._drawer:
            name = None
        self._drawer = name
        for view in (self.chat, self.history, self.settings, self.records):
            view.grid_remove()
        {'history': self.history, 'settings': self.settings, 'records': self.records}.get(name,self.chat).grid(row=0,column=0,sticky='nsew')
        if name == 'records':
            self._render_records()
        if name == 'history' and self._wide:
            self._show_drawer(None)
            self.sidebar_list.canvas.focus_set()

    def _return_to_edge(self):
        if self.startup:
            self.startup.finish()
        self.edge.dock()
        self.edge.set_ready(self.connected and not self._selecting_key)

    def _edge_history(self):
        self.edge.open_workspace()
        self._show_drawer('history')

    def _escape(self, _event=None):
        if self.startup and self.startup.active:
            self.startup.finish()
        elif self._drawer:
            self._show_drawer(None)
        elif self.edge.mode == 'workspace':
            self._return_to_edge()
        else:
            self.edge.collapse()
        return 'break'

    def _begin_composing(self):
        return self.connected and not self._selecting_key

    def _end_composing(self):
        pass  # Editing has no effect on a native background run.

    def _request(self, op, callback=None, **params):
        try:
            identifier = self.transport.request(op, **params)
            self._pending[identifier] = callback
            return identifier
        except RuntimeError as error:
            self.hint_var.set(str(error))
            return None

    def _save_draft(self):
        if self._key:
            editor = self.composer if self.edge.mode == 'workspace' else self.edge.composer
            self._drafts[self._key] = editor.get('1.0','end-1c')

    def _select_conversation(self, key):
        if key == self._key and not self._selecting_key:
            self._show_drawer(None)
            return
        self._save_draft()
        self._selecting_key = key
        self._update_controls()
        if not self._request('select', callback=self._selection_result, key=key):
            self._selecting_key = None
            self._update_controls()

    def _selection_result(self, event):
        if event.get('error'):
            self._selecting_key = None
            self._update_controls()

    def _new_conversation(self):
        if not self.connected:
            return
        self._save_draft()
        self._selecting_key = '<new>'
        self._update_controls()
        if not self._request('new', callback=self._selection_result):
            self._selecting_key = None
            self._update_controls()

    def _submit(self, _event=None):
        draft = self.composer.get('1.0','end-1c')
        if self._send_text(draft.strip()):
            self.composer.delete('1.0','end')
        return 'break'

    def _send_text(self, text):
        if not text or not self.connected or not self._key or self._selecting_key:
            return False
        key = self._key
        send_key = self._retry_sends.get((key,text)) or uuid4().hex
        def sent(event):
            if event.get('error'):
                self._retry_sends[(key,text)] = send_key
                # Preserve failed text even when the user switched sessions.
                existing = self._drafts.get(key, '')
                restored = text + ('\n' + existing if existing else '')
                self._drafts[key] = restored
                if self._key == key:
                    editor = self.composer if self.edge.mode == 'workspace' else self.edge.composer
                    fresh = editor.get('1.0','end-1c')
                    editor.configure(state='normal')
                    editor.delete('1.0','end')
                    editor.insert('1.0', text + ('\n'+fresh if fresh else ''))
            else:
                self._retry_sends.pop((key,text),None)
                self.hint_var.set('信息已发送。执行中发送的补充会按 OpenClaw 的原生机制处理。')
                self.edge.set_receipt('信息已发送', '后台工作继续进行；可在会话中查看消息和结果。')
        identifier = self._request('send', callback=sent, key=key, message=text, idempotencyKey=send_key)
        if identifier:
            self._drafts.pop(key, None)
            self.hint_var.set('正在发送…')
        return bool(identifier)

    def _stop(self):
        runs = self._snapshot.get('activeRunIds', [])
        if not self.can_stop or len(runs) != 1:
            self.hint_var.set('请到原生界面选择需要停止的执行回合。')
            return
        self._request('abort', key=self._key, runId=runs[0])
        self.hint_var.set('已请求停止本轮执行。独立后台进程的状态请查看工具记录。')

    def _open_native(self):
        def opened(event):
            if event.get('error'):
                return
            url = event.get('result',{}).get('url','')
            parsed = urlsplit(url)
            if parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1','localhost','::1'}:
                webbrowser.open(url)
            else:
                self.hint_var.set('原生界面地址不符合本机配置。请通过原生启动器打开。')
        self._request('dashboard', callback=opened)

    def _update_controls(self):
        ready = self.connected and bool(self._key) and not self._selecting_key
        self.send_button.configure(state='normal' if ready else 'disabled')
        self.can_stop = ready and len(self._snapshot.get('activeRunIds',[])) == 1
        self.stop_button.configure(state='normal' if self.can_stop else 'disabled')
        for button in (self.sidebar_new, self.history_new):
            button.configure(state='normal' if self.connected and not self._selecting_key else 'disabled')
        for view in (self.sidebar_list,self.history_list):
            view.set_enabled(self.connected and not self._selecting_key)
        if hasattr(self,'edge'):
            self.edge.set_ready(ready)
            self.edge.set_status(self._phase,self.task_var.get(),0)

    def _session_rows(self):
        rows = [(row['key'], str(row.get('derivedTitle') or row.get('displayName') or row.get('label') or row['key']))
                for row in self._sessions if row.get('key')]
        for view in (self.sidebar_list,self.history_list):
            view.set_sessions(rows,self._key)
        selected = next((title for key,title in rows if key == self._key),'新的会话')
        self.session_title.set(selected)

    def _render_chat(self):
        chunks = []
        entries = self._snapshot.get('entries',[])
        if not entries:
            chunks = [(APP_NAME+'\n','role'),('你好，有什么想一起聊聊的？\n\n可以问我问题，也可以交给我文件、命令或网络任务。\n','')]
        for entry in entries:
            message = entry.get('message',{})
            if not isinstance(message,dict):
                continue
            role = message.get('role')
            text = native_text(message)
            if role == 'user':
                chunks.extend([('你\n','role'),(text+'\n','user')])
                if entry.get('pending'):
                    chunks.append(('正在发送 / 等待进入原生记录\n','muted'))
            elif role == 'assistant':
                if text:
                    chunks.extend([(APP_NAME+'\n','role'),(text+'\n\n','')])
                content = message.get('content',[])
                for part in content if isinstance(content,list) else []:
                    if isinstance(part,dict) and part.get('type') == 'toolCall':
                        name = part.get('name','工具')
                        chunks.append(('  '+TOOL_TITLES.get(name,name)+'\n','tool'))
            elif role == 'toolResult':
                name = message.get('toolName','工具')
                chunks.append(('  '+TOOL_TITLES.get(name,name)+(' · 失败' if message.get('isError') else ' · 返回结果')+'\n','tool'))
        if self._snapshot.get('error'):
            chunks.append((self._snapshot['error']+'\n','muted'))
        if chunks == self._rendered_chunks:
            return
        at_bottom = self.chat_text.yview()[1] >= .98
        position = self.chat_text.yview()[0]
        common = 0
        for previous,current in zip(self._rendered_chunks,chunks):
            if previous != current:
                break
            common += 1
        self.chat_text.configure(state='normal')
        start = f'chunk_{common}' if common < len(self._rendered_chunks) else 'end-1c'
        self.chat_text.delete(start,'end-1c')
        for index,(text,tag) in enumerate(chunks[common:],common):
            self.chat_text.mark_set(f'chunk_{index}','end-1c')
            self.chat_text.mark_gravity(f'chunk_{index}','left')
            self.chat_text.insert('end-1c',text,tag)
        self.chat_text.configure(state='disabled')
        self._rendered_chunks = chunks
        if at_bottom:
            self.chat_text.see('end')
        else:
            self.chat_text.yview_moveto(position)

    def _render_records(self):
        self.record_text.configure(state='normal')
        self.record_text.delete('1.0','end')
        found = False
        for entry in self._snapshot.get('entries',[]):
            message = entry.get('message',{})
            if not isinstance(message,dict):
                continue
            content = message.get('content',[])
            for part in content if isinstance(content,list) else []:
                if isinstance(part,dict) and part.get('type') == 'toolCall':
                    found = True
                    self.record_text.insert('end',TOOL_TITLES.get(part.get('name'),part.get('name','工具'))+'\n')
                    self.record_text.insert('end',json.dumps(part.get('arguments',{}),ensure_ascii=False,indent=2)[:8000]+'\n\n')
            if message.get('role') == 'toolResult':
                found = True
                self.record_text.insert('end',native_text(message)[:12000]+'\n\n')
        if not found:
            self.record_text.insert('end','这个会话暂时没有工具记录。\n\n完整工具详情与交互内容也可以在原生界面查看。')
        self.record_text.configure(state='disabled')

    def _show_interactions(self, items):
        relevant = [item for item in items if (item.get('sessionKey') or item.get('request',{}).get('sessionKey')) == self._key]
        if relevant:
            question = next((item for item in relevant if item.get('questions')),None)
            if question:
                text = '\n'.join(str(q.get('question') or q.get('prompt') or '需要补充信息') for q in question['questions'])
            else:
                text = '这段会话有待处理的原生审批。'
            self.interaction_label.configure(text=public_text(text,400))
            self.interaction.grid(row=4,column=0,sticky='ew',pady=(10,0))
            self.edge.set_receipt('需要你处理', '打开会话后，到原生界面回答问题或处理审批。')
        else:
            self.interaction.grid_remove()

    def _event(self,event):
        kind = event.get('type')
        if kind == 'connection':
            self.connected = bool(event.get('connected'))
            self.connection_var.set('OpenClaw · 已连接' if self.connected else 'OpenClaw · 未连接')
            if not self.connected:
                self.status_var.set('连接中断')
                self.hint_var.set(event.get('message','正在重新连接。'))
            self._update_controls()
        elif kind == 'sessions':
            self._sessions = event.get('sessions',[])
            self._session_rows()
            if self._initial_selection and self.connected:
                self._initial_selection = False
                keys = [row['key'] for row in self._sessions if row.get('key')]
                preferred = self.preferences.get('session')
                if keys:
                    self._select_conversation(preferred if preferred in keys else keys[0])
                else:
                    self._new_conversation()
        elif kind == 'selecting':
            self._selecting_key = event.get('key')
            self._update_controls()
        elif kind == 'session':
            key = event.get('key')
            if self._selecting_key and self._selecting_key not in {key,'<new>'}:
                return
            changed_key = self._key != key
            if changed_key:
                self._key = key
                self._started = None
                for editor in (self.composer,self.edge.composer):
                    editor.configure(state='normal')
                    editor.delete('1.0','end')
                self.composer.insert('1.0',self._drafts.get(key,''))
                self._rendered_chunks = []
                self.chat_text.configure(state='normal')
                self.chat_text.delete('1.0','end')
                self.chat_text.configure(state='disabled')
                self.edge.reset_receipt()
                self._show_drawer(None)
            self._selecting_key = None
            self._snapshot = event
            active = bool(event.get('activeRunIds') or event.get('activeUnknown'))
            activity = event.get('activity') or {}
            name = activity.get('name')
            self._phase = 'executing' if active and name else ('thinking' if active else 'idle')
            self.status_var.set('后台工作中' if active else '准备就绪')
            self.task_var.set(TOOL_TITLES.get(name,name) if active and name else ('正在思考与处理你的请求' if active else '一起想清楚，也一起把事情做好。'))
            if event.get('error') and event.get('errorType') == 'run' and not active:
                self._phase = 'failed'
                self.status_var.set('需要处理')
                self.task_var.set('本轮遇到问题，详情见会话。')
            if active and self._started is None:
                self._started = time.monotonic()
            elif not active:
                self._started = None
                self.time_var.set('')
            self._session_rows()
            self._render_chat()
            if self._drawer == 'records':
                self._render_records()
            self._show_interactions(getattr(self,'_interactions',[]))
            self._update_controls()
            if changed_key:
                self._save_preferences()
        elif kind == 'interactions':
            self._interactions = event.get('items',[])
            self._show_interactions(self._interactions)
            if event.get('unavailable'):
                self.hint_var.set('部分原生交互状态暂不可读取；可通过原生界面处理。')
        elif kind == 'result':
            callback = self._pending.pop(event.get('id'),None)
            if event.get('error'):
                self.hint_var.set(event['error'])
            if callback:
                callback(event)
        elif kind == 'error':
            self.hint_var.set(event.get('message','连接暂时遇到问题。'))

    def _poll(self):
        if self._closing:
            return
        # Bound each batch so streaming cannot starve animation or typing.
        for _ in range(24):
            try:
                self._event(self.transport.events.get_nowait())
            except queue.Empty:
                break
        if self._started is not None:
            elapsed = int(time.monotonic()-self._started)
            self.time_var.set(f'{elapsed//60:02d}:{elapsed%60:02d}')
        self.edge.poll()
        self._poll_job = self.root.after(30,self._poll)

    def _close(self):
        if self._closing:
            return
        self._closing = True
        self.root.after_cancel(self._poll_job)
        self._save_preferences()
        self.transport.close()
        self.edge.close()
        if self.startup:
            self.startup.finish()
        self.session.close()
        self.root.update_idletasks()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
