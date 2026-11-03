import os, sys, tempfile

sys.path.insert(0, os.getcwd())
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication
from slidebuilder.gui.app import make_app
app = make_app()
from slidebuilder.gui.main_window import MainWindow
w = MainWindow(); w.resize(1280, 720); w.show()
def pump(ms=300):
    import time; t=time.time()
    while time.time()-t < ms/1000: app.processEvents()
def wait(cond, t=60):
    import time; t0=time.time()
    while not cond() and time.time()-t0 < t: app.processEvents()
    assert cond(), "timeout"
out = os.path.join(os.getcwd(), "demo_data_shots", "shots"); os.makedirs(out, exist_ok=True)
def shot(n): pump(); w.grab().save(os.path.join(out, n + ".png"))
st = w.state
w.steps[0].start_scan(os.path.abspath("demo_data/images"))
wait(lambda: st.scan is not None); pump(1500); shot("1_folder")
w.go(1); w.steps[1].load(os.path.abspath("demo_data/treatments.csv")); shot("2_csv")
w.go(2); L = w.steps[2]
L.pixel.setText("0.65"); pump(600)
for pid in list(L.cards):
    L.pick_preset(pid); pump(900)
    print(pid, L.pos.text(), L.cards[pid].count); shot("3_" + pid)
L.pick_preset("chan_by_rep"); pump(900)
w.save_project(path=os.path.join(out, "p.json"))
w.go(3); E = w.steps[3]
E.out.setText(os.path.join(tempfile.gettempdir(), "gui_test.pptx")); E.start(confirm=False)
wait(lambda: E.worker is None, 180); shot("4_export"); print(E.status.text())
print("pptx size", os.path.getsize(E.out.text()))
w.load_project(os.path.join(out, "p.json")); wait(lambda: w.stack.currentIndex()==2); pump(900); print("restored", st.preset, L.pos.text()); shot("5_restored")
w.close()
