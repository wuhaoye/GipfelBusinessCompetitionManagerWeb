# -*- coding: utf-8 -*-
"""从品牌 logo 生成站点图标（favicon）到 frontend/public/。

用法（在任意目录均可执行）：
    python scripts/make_favicon.py      # 需要 Pillow，backend/.venv 里已有
    然后重新构建前端：cd frontend && npm run build

做什么：
  1) 用行方向墨迹分布，把「盾徽」与下方「Gipfel」字标分开（取第一块）
  2) 裁到盾徽外接框 + 少量留白 → 补成正方形（避免压扁）
  3) 输出 favicon.ico(16/32/48) + favicon-16/32.png + apple-touch-icon.png(180)

为什么只取盾徽：favicon 在标签页常为 16×16，「Gipfel」字样在该尺寸下不可读，
留着只会让盾徽更小更糊；32×32 下盾徽可辨识——这是本套美术的物理上限，不是处理问题。
"""
import os
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend", "src", "assets", "gipfel-logo.jpg")
OUT_DIR = os.path.join(ROOT, "frontend", "public")
INK = 240          # 灰度低于此值算「有内容」
MARGIN = 0.02      # 外接框四周留白比例（相对边长）

os.makedirs(OUT_DIR, exist_ok=True)
im = Image.open(SRC).convert("RGB")
w, h = im.size
gray = im.convert("L")
px = gray.load()

# ---- 1. 行墨迹分布 ----
rows = []
for y in range(h):
    ink = 0
    for x in range(0, w, 2):          # 隔列采样，够用且快
        if px[x, y] < INK:
            ink += 1
            if ink > 2:
                break
    rows.append(ink > 0)

blocks = []
y = 0
while y < h:
    if rows[y]:
        start = y
        while y < h and rows[y]:
            y += 1
        blocks.append((start, y - 1))
    else:
        y += 1

print(f"图像 {w}x{h}，检测到 {len(blocks)} 个内容块（行方向）：")
for i, (a, b) in enumerate(blocks):
    print(f"  块 {i}: y {a}..{b}  高 {b - a + 1}")
if not blocks:
    raise SystemExit("没检测到内容，检查阈值")

mark = blocks[0]
print(f"→ 取第一块作为盾徽：y {mark[0]}..{mark[1]}")

# ---- 2. 在盾徽行范围内求列外接框 ----
left, right = w - 1, 0
for y in range(mark[0], mark[1] + 1):
    for x in range(w):
        if px[x, y] < INK:
            if x < left:
                left = x
            if x > right:
                right = x
print(f"→ 盾徽列范围：x {left}..{right}")

box = (left, mark[0], right + 1, mark[1] + 1)
crop = im.crop(box)
print(f"→ 裁剪后尺寸：{crop.size}")

# ---- 3. 补成正方形（白底）----
cw, ch = crop.size
side = max(cw, ch)
pad = int(side * MARGIN)
side_pad = side + pad * 2
canvas = Image.new("RGB", (side_pad, side_pad), (255, 255, 255))
canvas.paste(crop, ((side_pad - cw) // 2, (side_pad - ch) // 2))
print(f"→ 正方形画布：{canvas.size}（含 {MARGIN:.0%} 留白）")

# ---- 4. 输出 ----
master = canvas.resize((512, 512), Image.LANCZOS)
ico_path = os.path.join(OUT_DIR, "favicon.ico")
master.save(ico_path, sizes=[(16, 16), (32, 32), (48, 48)])
print(f"已写 {ico_path}（16/32/48 多尺寸）")

for size, name in ((32, "favicon-32.png"), (16, "favicon-16.png"), (180, "apple-touch-icon.png")):
    p = os.path.join(OUT_DIR, name)
    canvas.resize((size, size), Image.LANCZOS).save(p, optimize=True)
    print(f"已写 {p}  ({size}x{size}, {os.path.getsize(p)} bytes)")

# ---- 5. 人工核对提示 ----
# 不写预览文件（避免在仓库根留临时产物）——直接看 frontend/public/ 下生成的结果即可：
#   favicon-32.png 是浏览器标签栏最常实际采用的尺寸，优先生成它来核对裁切是否合适。
print("\n核对建议：直接查看 frontend/public/favicon-32.png（标签栏实际尺寸）。")
print("原始 logo 换新后重跑本脚本即可；改完记得重新构建前端（cd frontend && npm run build）。")
