"""Optional real Windows input verification in a disposable, locally created window.

This changes foreground focus and moves the mouse. Run only when desktop testing is
authorized, serially, on an unlocked interactive desktop.
"""
import json
import os
import subprocess
import sys
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def invoke(kind, value=None, *, window):
    # SetForegroundWindow may need the destination thread to process messages.
    # Blocking Tk in subprocess.run tests an unresponsive target rather than
    # the interactive window used by the product.
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(subprocess.run,
            [sys.executable, '-m', 'mirally.framework.desktop_observation', kind],
            cwd=ROOT, input=json.dumps(value or {}), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            encoding='utf-8', env={**os.environ, 'PYTHONUTF8':'1'})
        while not future.done():
            window.update()
            time.sleep(.01)
        result = future.result()
    data = json.loads(result.stdout)
    if result.returncode:
        raise RuntimeError(data.get('error', 'desktop helper failed'))
    return data


def main():
    window = tk.Tk()
    window.title('Mirally desktop observation test')
    window.geometry('500x200+80+80')
    entry = tk.Entry(window)
    entry.pack(fill='x', padx=30, pady=50)
    window.lift()
    window.attributes('-topmost', True)
    window.focus_force()
    window.update()
    time.sleep(.2)
    window.update()
    try:
        matches = [w for w in invoke('windows', window=window)['windows'] if w['title'] == window.title()]
        assert len(matches) == 1, 'test window unavailable'
        record = matches[0]
        x = entry.winfo_rootx() + entry.winfo_width() // 2
        y = entry.winfo_rooty() + entry.winfo_height() // 2
        invoke('action', {'action':'click', 'window':record, 'x':x, 'y':y}, window=window)
        window.update()
        invoke('action', {'action':'type', 'window':record, 'text':'Mirally 测试'}, window=window)
        window.update()
        assert entry.get() == 'Mirally 测试', repr(entry.get())
        invalid = {**record, 'pid':record['pid'] + 1}
        try:
            invoke('action', {'action':'type', 'window':invalid, 'text':'blocked'}, window=window)
            raise AssertionError('changed identity should be rejected')
        except RuntimeError:
            pass
        window.update()
        assert entry.get() == 'Mirally 测试'
        print('Desktop click, Unicode input and changed-window rejection passed.')
    finally:
        window.destroy()


if __name__ == '__main__':
    main()
