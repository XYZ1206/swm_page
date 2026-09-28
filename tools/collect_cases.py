"""把 GoodCase 的 tracks / pca_single 视频拷进 static/video/cases/,并按英文名归档。

源目录用的是中文 case 名(``GoodCase-mutisource/<source>/<中文名>/``),直接进 URL 会被
百分号编码、在不同平台上还可能因文件系统归一化(NFC/NFD)出现路径对不上。故这里做一次
中文 → 英文 slug 的映射,映射表写死在本文件里,便于核对与手改。

一个 case 产出两个文件:
    <slug>_tracks_static.mp4   3 列:RGB 视频 | GT 轨迹 | 预测轨迹
    <slug>_pca.mp4             3 列:RGB | GT DINO | Predicted DINO

⚠ 源 ``__pca_single.mp4`` 是**4 列**(RGB | GT DINO | DINO-VAE 往返重建 | 生成),
  第 3 列(VAE 往返)按用户 2026-09-26 的要求**裁掉**。它是预渲染的拼带,没法用 CSS
  隐藏中间一列,只能 ffmpeg 裁剪重编码:留 [0, 2·cw) 与 [3·cw, 4·cw) 两段再 hstack。
  ⚠ 这会引入**二次 H.264 编码**(源本身已是 H.264)。PCA 图是块状平滑色块,
    CRF 18 下肉眼无损;若日后要做像素级比较,应回到源文件而不是用这里的产物。

⚠ 输出名**保留 ``_static`` 后缀**(2026-09-26):一是在磁盘上自证用的是哪个变体,
  二是换变体时 URL 跟着变 → 破掉浏览器缓存。此前输出名固定为 ``_tracks.mp4``,
  把内容从普通版换成 static 版后 URL 没变,浏览器继续用缓存里的旧视频,
  页面上看着像"根本没换"。
(列语义见 cosmos-rbs 的 test_dino_runner._save_pca_videos / test_dino_flow_runner)

⚠ tracks 取的是 ``_tracks_static.mp4`` 而**不是** ``_tracks.mp4``(2026-09-26 用户改):
  两者都是 3 列同尺寸,区别在第 2/3 列 ——
    _tracks.mp4         轨迹画在**逐帧变化的** RGB 上,带渐隐拖尾(只看得到近几帧)
    _tracks_static.mp4  轨迹画在**冻结首帧**上,全程累积保留 + 颜色编码时间
  后者更适合网页上静态浏览:整条轨迹一眼看全,不被背景运动干扰,GT/预测两列
  共用同一套时间配色因而可直接对比形状。第 1 列仍是真实 GT 视频(会动)。

用法:
    python tools/collect_cases.py            # 拷贝 + 打印 index.html 用的清单
    python tools/collect_cases.py --dry-run  # 只看要拷什么
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess

SRC_ROOT = "/mnt/home/lijuelin/latent-wm/cosmos-rbs/GoodCase/GoodCase-mutisource"
DST_REL = os.path.join("static", "video", "cases")
# 源 __pca_single.mp4 的列数(dino_vae_latent 模式 = 4 列;single_layer_dino = 3 列)
PCA_SRC_COLS = 4

# 源名 → 展示名(页面上的小标题)
SOURCE_LABEL = {
    "demo5": "Real-world (Tabletop)",
    "agibot": "Real-world (AgiBot)",
    "robotwin": "Simulation (RoboTwin)",
    "maniskill": "Simulation (ManiSkill)",
    "metaworld": "Simulation (MetaWorld)",
}
# 页面上的源顺序:真机在前(更有说服力),仿真在后
SOURCE_ORDER = ["demo5", "agibot", "robotwin", "maniskill", "metaworld"]

# 中文 case 名 → (英文 slug, 页面 caption)
CASES: dict[str, tuple[str, str]] = {
    # ---- demo5(真机桌面)----
    "丢垃圾": ("throw_away_trash", "Throw away the trash"),
    "关抽屉": ("close_drawer", "Close the drawer"),
    "关洗衣机": ("close_washing_machine", "Close the washing machine"),
    "取出鞋子": ("take_out_shoes", "Take the shoes out"),
    "把小熊放到红圈内": ("put_bear_in_red_circle", "Put the bear inside the red circle"),
    "把玉米放到框中": ("put_corn_in_basket", "Put the corn into the basket"),
    "把纸巾放到抽屉中": ("put_tissue_in_drawer", "Put the tissue into the drawer"),
    "把罐子放到盒子上": ("put_can_on_box", "Put the can on the box"),
    "拉开抽屉": ("open_drawer", "Open the drawer"),
    "移动书本": ("move_book", "Move the book"),
    "移动勺子": ("move_spoon", "Move the spoon"),
    "移动发卡": ("move_hairpin", "Move the hairpin"),
    "移动本子": ("move_notebook", "Move the notebook"),
    "移动盒子": ("move_box", "Move the box"),
    "移动遥控器": ("move_remote", "Move the remote control"),
    # ---- agibot(真机双臂)----
    "放纸巾到抽屉": ("agibot_put_tissue_in_drawer", "Put the tissue into the drawer"),
    "盘子放到微波炉里": ("put_plate_in_microwave", "Put the plate into the microwave"),
    # ---- robotwin(仿真)----
    "把杯子放到盘子上": ("put_cup_on_plate", "Put the cup on the plate"),
    "摇瓶子": ("shake_bottle", "Shake the bottle"),
    "旋转二维码牌": ("rotate_qr_board", "Rotate the QR-code board"),
    "调整瓶子": ("adjust_bottle", "Adjust the bottle"),
    # ---- maniskill(仿真)----
    "插插座": ("plug_charger", "Plug in the charger"),
    "插销": ("insert_peg", "Insert the peg"),
    # ---- metaworld(仿真)----
    "开柜门": ("open_cabinet_door", "Open the cabinet door"),
    "旋转开关": ("rotate_dial", "Rotate the dial"),
    "盖盖子": ("put_on_lid", "Put on the lid"),
}


def _probe_wh(path: str) -> tuple[int, int]:
    """mp4 → (width, height)。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = (int(x) for x in out.split(","))
    return w, h


def _drop_column(src: str, dst: str, n_cols: int, drop: int) -> None:
    """把 n_cols 列的拼带里第 ``drop`` 列(1-based)裁掉,其余列按原序拼回。

    做法:裁 drop 之前的一段 + 之后的一段,再 hstack。只有一段时直接 crop。
    ⚠ h264 要求宽高均为偶数 —— 各列等宽且源宽能被列数整除,故裁完必然仍是偶数
      (源宽偶数 ÷ 整数列 × 整数列数)。真出现奇数会被 ffmpeg 直接报错,不会静默。
    """
    w, h = _probe_wh(src)
    if w % n_cols:
        raise RuntimeError(f"{os.path.basename(src)}: 宽 {w} 不能被 {n_cols} 整除")
    cw = w // n_cols
    left_w = (drop - 1) * cw                      # drop 之前保留的宽度
    right_w = (n_cols - drop) * cw                # drop 之后保留的宽度
    if left_w and right_w:
        fc = (f"[0:v]crop={left_w}:{h}:0:0[a];"
              f"[0:v]crop={right_w}:{h}:{drop * cw}:0[b];"
              f"[a][b]hstack=inputs=2[out]")
        args = ["-filter_complex", fc, "-map", "[out]"]
    else:
        x = 0 if right_w else drop * cw
        args = ["-vf", f"crop={left_w or right_w}:{h}:{x}:0"]
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src, *args,
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", dst],
        check=True)


def _pick(case_dir: str, suffix: str) -> str | None:
    """case 目录里找唯一一个以 suffix 结尾的文件。

    ⚠ 后缀匹配靠 endswith,而 ``_tracks_static.mp4`` 与 ``_tracks.mp4`` 互不包含
      (前者末尾是 ``_static.mp4``),所以两个后缀都能精确命中,无需额外排除。
    """
    hits = sorted(f for f in os.listdir(case_dir) if f.endswith(suffix))
    if not hits:
        return None
    if len(hits) > 1:
        print(f"    ⚠ {os.path.basename(case_dir)} 有 {len(hits)} 个 *{suffix},取第一个")
    return os.path.join(case_dir, hits[0])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--drop-pca-col", type=int, default=3,
                    help="从 pca 拼带里裁掉第几列(1-based);0=不裁,原样拷贝。"
                         f"默认 3 = DINO-VAE 往返重建那一列(源共 {PCA_SRC_COLS} 列)")
    a = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dst_root = os.path.join(repo, DST_REL)

    manifest: dict[str, list[dict]] = {}
    n_copy = n_skip = 0
    total_bytes = 0

    for source in SOURCE_ORDER:
        sdir = os.path.join(a.src, source)
        if not os.path.isdir(sdir):
            print(f">>> ⚠ 源目录不存在,跳过:{sdir}")
            continue
        rows: list[dict] = []
        print(f">>> {source}")
        for zh in sorted(os.listdir(sdir)):
            cdir = os.path.join(sdir, zh)
            if not os.path.isdir(cdir):
                continue
            if zh not in CASES:
                print(f"    ⚠ 未登记的 case 名 {zh!r},跳过(请补进 CASES)")
                n_skip += 1
                continue
            slug, caption = CASES[zh]
            # demo5 与 robotwin 都有「丢垃圾」→ slug 加源前缀避免撞名
            if source != "demo5" and slug in {v[0] for k, v in CASES.items()
                                              if k in os.listdir(os.path.join(a.src, "demo5"))}:
                slug = f"{source}_{slug}"
            out: dict = {"slug": slug, "caption": caption, "zh": zh}
            for suffix, key in (("_tracks_static.mp4", "tracks"), ("__pca_single.mp4", "pca")):
                src_f = _pick(cdir, suffix)
                if not src_f:
                    print(f"    ⚠ {zh}: 缺 *{suffix}")
                    continue
                # 输出名带上**变体与列数**:磁盘上自证 + 换了口径 URL 就变 → 破浏览器缓存。
                # ⚠ 2026-09-26 两次踩坑:先是把 tracks 换成 static 版、后是把 pca 从 4 列裁成
                #   3 列,两次都**只换内容不换文件名** → URL 不变 → 浏览器继续放缓存里的旧片,
                #   页面上看着像"根本没改"。文件名里带上口径,这类问题从根上消失。
                if key == "tracks":
                    tag = "tracks_static"
                else:
                    n_out = PCA_SRC_COLS - (1 if a.drop_pca_col else 0)
                    tag = f"pca{n_out}col"
                dst_f = os.path.join(dst_root, source, f"{slug}_{tag}.mp4")
                out[key] = f"./{DST_REL}/{source}/{slug}_{tag}.mp4".replace(os.sep, "/")
                sz = os.path.getsize(src_f)
                total_bytes += sz
                if a.dry_run:
                    print(f"    {zh} → {source}/{slug}_{tag}.mp4  ({sz/1e6:.1f} MB)")
                else:
                    os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                    if key == "pca" and a.drop_pca_col:
                        # 4 列拼带 → 裁掉第 3 列(DINO-VAE 往返重建)
                        _drop_column(src_f, dst_f, PCA_SRC_COLS, a.drop_pca_col)
                    else:
                        shutil.copy2(src_f, dst_f)
                n_copy += 1
            rows.append(out)
        if rows:
            manifest[source] = rows

    print(f"\n>>> {'(dry-run) ' if a.dry_run else ''}文件 {n_copy} 个,"
          f"合计 {total_bytes/1e6:.1f} MB" + (f",跳过 {n_skip}" if n_skip else ""))
    mpath = os.path.join(repo, "tools", "cases_manifest.json")
    if not a.dry_run:
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump({"source_label": SOURCE_LABEL, "source_order": SOURCE_ORDER,
                       "cases": manifest}, f, ensure_ascii=False, indent=1)
        print(f">>> 清单 → {mpath}")
    for src, rows in manifest.items():
        print(f"    {src}: {len(rows)} case")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
