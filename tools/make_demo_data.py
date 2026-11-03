"""Generate a demo folder mimicking the real naming (55 sets, one missing 555, mixed sizes) + a CSV."""
import os
import sys
import numpy as np
from PIL import Image

out = sys.argv[1] if len(sys.argv) > 1 else "demo_data"
img = os.path.join(out, "images")
os.makedirs(img, exist_ok=True)
rng = np.random.default_rng(0)
COL = {"488": (0, 255, 0), "555": (255, 60, 0), "647": (255, 0, 255)}


def blobs(w, h, n):
    a = np.zeros((h, w), np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(n):
        cx, cy, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(8, 30)
        a += np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r))
    return np.clip(a, 0, 1)


def save(name, arr, size):
    rgba = np.dstack([arr, np.full(arr.shape[:2], 255, np.uint8)])
    Image.fromarray(rgba, "RGBA").save(os.path.join(img, name))


sets = [("", ".07"), ("", "3.6")] + [(f"_{i:02d}", s) for i in range(1, 27) for s in (".07", "3.6")] + [("_27", "")]
rows = []
for vid, sec in sets:
    w, h = (800, 460) if vid in ("_05", "_12") else (736, 424)  # a couple of different sizes
    planes = {c: blobs(w, h, rng.integers(10, 40)) for c in COL}
    merge = np.zeros((h, w, 3), np.float32)
    for c, p in planes.items():
        merge += p[..., None] * np.array(COL[c], np.float32)
    for c, p in planes.items():
        if (vid, sec) == ("_06", "3.6") and c == "555":
            continue
        save(f"Image{vid}{'_' + sec if sec else ''}_{c}_1x.tif", (p[..., None] * np.array(COL[c])).astype(np.uint8), (w, h))
    save(f"Image{vid}{'_' + sec if sec else ''}_merge_1x.tif", np.clip(merge, 0, 255).astype(np.uint8), (w, h))

with open(os.path.join(out, "treatments.csv"), "w", encoding="utf-8") as f:
    f.write("source_id,treatment,replicate,488,555,647,350,pixel_size_um,sex\n")
    f.write(",Vehicle,V0,GFP,NeuN,Iba1,,0.65,M\n")
    treats = ["Vehicle", "DrugA", "DrugB"]
    for i in range(1, 28):
        t = treats[(i - 1) % 3]
        f.write(f"Image_{i:02d},{t},{t[0]}{(i - 1) // 3 + 1},GFP,NeuN,Iba1,,{0.65 if i % 2 else 0.325},{'M' if i % 2 else 'F'}\n")
print("done", out)
