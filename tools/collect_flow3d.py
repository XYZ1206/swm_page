"""把 3D flow 动画(GT 轨迹在首帧点云上逐帧生长)拷进 static/,并出海报图。

与 collect_cases.py 的区别:那边是**结果**(GT vs 预测并排的拼带),这边是**标注**
—— 轨迹全部来自 obj_tgt,是真值,不是模型输出(源目录 README.json 明确写了
"轨迹来源: obj_tgt = GT 未来 3D 点轨迹。不是模型生成结果")。所以页面上这一段
必须放在结果之外、并在文案里说清是 ground truth,否则等于把真值当成绩展示。

⚠ 海报图不是可有可无:视频是 ``preload="none"`` + 滚到视口里才加载的,没有海报
  就会先显示一块灰底。海报取**第 0 帧** —— 按 README,第 0 帧正好是"点云 + 青色
  查询点、还没有任何轨迹",天然就是这段动画的"开始状态"。

用法:
    python tools/collect_flow3d.py            # 拷视频 + 生成海报
    python tools/collect_flow3d.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess

SRC_ROOT = "/mnt/home/lijuelin/latent-wm/paper_figs/video3d_20"
VID_REL = os.path.join("static", "video", "flow3d")
IMG_REL = os.path.join("static", "images", "flow3d")
POSTER_W = 900          # 海报只为占位/首屏观感,不需要原始 1600+ 宽

# (episode stem, 英文 caption)。caption 译自各 case json 的 ``prompt`` 字段
# —— 那是原始采集时人给的自然语言指令,页面是英文站,故此处翻译后写死,
# 并在下面校验 stem 仍存在(源目录若换了 case,这里要报错而不是静默少放一条)。
CASES = [
    ("episode_1395_20260725_201904", "Put the red chilli into the stainless-steel bowl"),
    ("episode_0039_20260802_104111", "Put the Hello Kitty charm right of the car diffuser"),
    ("episode_0152_20260813_174316", "Put the mug to the left of the blue tray"),
    ("episode_0350_20260808_145449", "Put the green tin right of the vending-machine toy"),
]


def _probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,nb_frames", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h, n = out.split(",")
    return {"w": int(w), "h": int(h), "frames": int(n), "ar": round(int(w) / int(h), 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows, total = [], 0

    for stem, caption in CASES:
        src = os.path.join(a.src, stem, f"{stem}__flow3d.mp4")
        if not os.path.exists(src):
            raise SystemExit(f"ERROR: 找不到 {src} —— 源目录的 case 变了?")
        meta = _probe(src)
        # 每条的 prompt/fps 等元信息就在旁边的 json 里,顺手读出来核对
        side = os.path.join(a.src, stem, f"{stem}__flow3d.json")
        info = json.load(open(side, encoding="utf-8")) if os.path.exists(side) else {}
        sz = os.path.getsize(src)
        total += sz

        vid_dst = os.path.join(repo, VID_REL, f"{stem}__flow3d.mp4")
        img_dst = os.path.join(repo, IMG_REL, f"{stem}__flow3d.jpg")
        print(f"  {stem}  {meta['w']}x{meta['h']} ar={meta['ar']} "
              f"{meta['frames']}f @{info.get('fps', '?')}fps  {sz/1e6:.1f} MB")
        print(f"      prompt: {info.get('prompt', '(无)')}")
        print(f"      → {caption}")

        if not a.dry_run:
            os.makedirs(os.path.dirname(vid_dst), exist_ok=True)
            os.makedirs(os.path.dirname(img_dst), exist_ok=True)
            shutil.copy2(src, vid_dst)
            # 第 0 帧 = 点云 + 查询点,尚无轨迹
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", src, "-vframes", "1",
                 "-vf", f"scale={POSTER_W}:-2", "-q:v", "4", img_dst], check=True)

        rows.append({"stem": stem, "caption": caption, "ar": meta["ar"],
                     "prompt": info.get("prompt", ""),
                     "video": f"./{VID_REL}/{stem}__flow3d.mp4".replace(os.sep, "/"),
                     "poster": f"./{IMG_REL}/{stem}__flow3d.jpg".replace(os.sep, "/")})

    print(f"\n>>> {'(dry-run) ' if a.dry_run else ''}{len(rows)} 条,视频合计 {total/1e6:.1f} MB")
    if not a.dry_run:
        mp = os.path.join(repo, "tools", "flow3d_manifest.json")
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print(f">>> 清单 → {mp}")
        print(">>> 提示:index.html 里这一段是**手写**的(4 条固定 case),"
              "改 CASES 后需要手动同步那段 HTML")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
