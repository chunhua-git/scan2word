# -*- coding: utf-8 -*-
"""GUI 冒烟测试：离屏起界面，跑一次真实转换，验证信号链与输出。

不开真窗口、不需要人工点，用于每次改完代码后自检。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.append(_VENDOR)
sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.main import MainWindow

SAMPLES = os.path.join(ROOT, "samples")
OUT = os.path.join(ROOT, "out", "gui_smoke")


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    target = os.path.join(SAMPLES, "南宁青秀区收款账号确认书（新）空白.pdf")
    if not os.path.exists(target):
        sdir = SAMPLES
        cands = [os.path.join(sdir, f) for f in sorted(os.listdir(sdir))] if os.path.isdir(sdir) else []
        cands = [c for c in cands if c.lower().endswith(".pdf")]
        if not cands:
            print("跳过：samples/ 下没有 PDF。界面冒烟测试需要一个样本，放进 samples/ 再运行。")
            return 0
        target = cands[0]
    win.add_paths([target])
    assert win.list.count() == 1, "拖入后列表应有 1 个文件"
    win.edit_out.setText(OUT)
    win.chk_open.setChecked(False)

    state = {"done": False, "results": []}
    win.worker = None

    def on_all(results, elapsed):
        state["done"] = True
        state["results"] = results

    win.start()
    win.worker.finished_all.connect(on_all)

    def tick():
        if state["done"]:
            app.quit()

    QTimer.singleShot(300, tick)
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(200)
    QTimer.singleShot(120000, app.quit)          # 兜底，别挂死
    app.exec()

    r = state["results"][0] if state["results"] else None
    if not r:
        print("❌ GUI 未产出结果")
        return 1
    print("GUI 转换结果：", r.summary())
    print("  docx:", r.docx)
    ok = r.ok and r.docx and os.path.exists(r.docx)
    print("✅ GUI 冒烟测试通过" if ok else "❌ GUI 冒烟测试失败")
    print("日志：")
    print(win.log.toPlainText())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
