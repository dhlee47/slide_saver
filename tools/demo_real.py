"""Live demo on the real folder: opens the real window, steps through the wizard, takes screenshots."""
import os, sys, time
sys.path.insert(0, os.getcwd())
from PySide6.QtCore import Qt
from slidebuilder.gui.app import make_app
app = make_app()
from slidebuilder.gui.main_window import MainWindow

FOLDER = r"C:\Users\KLee\OneDrive - Neurocrine Biosciences Inc\Desktop\Image Edits"
CSV = os.path.abspath("examples/mockup_treatments.csv")
OUT = os.path.abspath("demo_shots"); os.makedirs(OUT, exist_ok=True)

w = MainWindow(); w.resize(1280, 760); w.show()
def pump(ms=800):
    t = time.time()
    while time.time() - t < ms / 1000: app.processEvents()
def wait(cond, t=120):
    t0 = time.time()
    while not cond() and time.time() - t0 < t: app.processEvents()
    assert cond(), "timeout"
def shot(n): pump(400); w.grab().save(os.path.join(OUT, n + ".png")); print("shot", n, flush=True)

st = w.state
w.steps[0].start_scan(FOLDER); wait(lambda: st.scan is not None); pump(4000); shot("1_folder")
w.go(1); w.steps[1].load(CSV); pump(1500); shot("2_csv")
w.go(2); L = w.steps[2]; pump(1500)
for pid in ("sample_panel", "chan_by_rep", "treat_by_rep", "chan_by_treat", "contact_sheet"):
    L.pick_preset(pid); pump(2500); print(pid, L.pos.text(), flush=True); shot("3_" + pid)
# turn off the 647 channel on the treatment x replicate layout, downsample to 1000 px
L.pick_preset("chan_by_rep"); pump(1500)
for i in range(L.chan_list.count()):
    if L.chan_list.item(i).text().startswith("647") or "647" in L.chan_list.item(i).text():
        L.chan_list.item(i).setCheckState(Qt.Unchecked)
L.maxpx.setValue(1000); pump(2500); print("no 647:", L.pos.text(), flush=True); shot("3_chan_by_rep_no647")
w.go(3); E = w.steps[3]; pump(1000)
E.out.setText(os.path.abspath("demo_export.pptx")); E.start(confirm=False)
wait(lambda: E.worker is None, 240); pump(800); shot("4_export"); print(E.status.text(), flush=True)
w.close()
