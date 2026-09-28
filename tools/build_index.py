"""由 cases_manifest.json 生成 index.html 的定性结果段落,注回 index.html。

为什么用生成而不是手写:27 个 case × 2 条视频 = 54 个 <video>,手写既容易漏
也容易和磁盘上的实际文件名对不上。本脚本只替换两个标记之间的内容:

    <!-- BEGIN:CASES --> … <!-- END:CASES -->

标记之外的一切(hero / abstract / method / 页尾的 carousel 脚本)都不动,
所以可以反复跑。

⚠ 排版是**按真机/仿真两组 + 每组一个左右滑页**(2026-09-26 用户要求;此前是逐数据集
  分成 5 组)。54 条宽幅拼带竖着平铺要滚很久,分组滑页后一次只看一条;分组粗了之后
  每条 slide 的 caption 底下要标出它来自哪个数据集,否则读者无从判断。
  行为由 index.html 页尾的脚本驱动,靠 ``data-carousel`` 属性认领;**改这里的
  类名/结构就必须同步改那段脚本**,两边对不上的表现是"箭头点了没反应",不报错。

用法:
    python tools/collect_cases.py     # 先拷资源 + 出清单
    python tools/build_index.py       # 再生成段落
"""

from __future__ import annotations

import html
import json
import os
import subprocess

BEGIN = "<!-- BEGIN:CASES -->"
END = "<!-- END:CASES -->"

# 两类拼带的列标题(顺序 = 视频里从左到右的列序)
# 依据:cosmos-rbs test_dino_flow_runner.py:729-740(tracks_static)与
#      test_dino_runner._save_pca_videos 的 docstring(pca_single, 4 列分支)
# ⚠ tracks 用的是 **_tracks_static**:第 1 列是真实视频(会动),第 2/3 列的轨迹画在
#   **冻结首帧**上、全程累积并用颜色编码时间(GT 与预测共用同一套配色 → 可直接比形状)。
# ⚠ pca 源是 4 列,collect_cases.py 已裁掉第 3 列(DINO-VAE 往返重建)→ 这里只剩 3 列。
#   两处必须同步:改了 --drop-pca-col 就要改这张表,否则标题会与画面错位而**不报错**。
COLS = {
    "tracks": ["Input video", "GT object flow", "Predicted object flow"],
    "pca": ["Input RGB", "GT DINO", "Predicted DINO"],
}

# 分组:真机在前,仿真在后。(源名, slide 上标的短名),组内顺序即此处顺序。
# ⚠ 这里**写死**了源名清单,而不是从 cases_manifest 的标签里解析 "Real-world (...)"
#   前缀 —— 解析法在标签改个写法时会静默把某个源归错组或整组丢掉。写死 + 下面对
#   清单外的源硬报错,是为了让"新加了源但忘了分组"变成一个响的错误。
DOMAINS = [
    ("Real-world", [("demo5", "Tabletop"), ("agibot", "AgiBot")]),
    ("Simulation", [("robotwin", "RoboTwin"), ("maniskill", "ManiSkill"),
                    ("metaworld", "MetaWorld")]),
]

# 每段的标题与导语(id 同时是顶部导航的锚点)
SECTIONS = [
    ("object-flow", "tracks", "3D Object Flow",
     "Each slide is one sample. The first column is the real video; the other two draw the full "
     "trajectories on the frozen first frame, coloured by time, so the whole motion is visible at "
     "once instead of scrolling past. Ground truth and prediction share one colour map, so a good "
     "prediction reproduces both the shape of the bundle and the timing along it."),
    ("dino-prediction", "pca", "Future DINO Representation",
     "The same samples in representation space. The ground-truth and predicted DINO features share "
     "a single PCA basis fitted over all of their patches, so colours are directly comparable "
     "between the two columns: matching colours mean matching representations, and the layout of "
     "the colour regions reflects how the model places objects and structure in the predicted "
     "future."),
]

_AR_CACHE: dict[str, float | None] = {}


def _aspect(repo: str, rel: str) -> float | None:
    """拼带的宽高比,用来给懒加载的 <video> **先占好位**。

    视频是 ``preload="none"`` + ``data-src`` 懒加载的,不预留高度的话每次挂载都会从
    0 跳到真实高度,整页随之抖一下。

    ⚠ 必须**逐条**探而不是逐组探:真机组里 demo5 是 3072×576(5.33)、agibot 是
      1920×480(4.0),差 33% 的高度 —— 按组取一个值会让其中一种被拉扁或压扁。
      仿真三源都是 2496×480,同组内一致。

    探不到(没装 ffprobe、文件缺失)就返回 None,CSS 侧退回默认值 —— 只是占位不准,
    不该因此让整页生成失败。
    """
    path = os.path.join(repo, rel.lstrip("./").replace("/", os.sep))
    if path in _AR_CACHE:
        return _AR_CACHE[path]
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True).stdout.strip()
        w, h = (int(x) for x in out.split(","))
        ar = round(w / h, 4) if h else None
    except Exception as exc:                            # noqa: BLE001
        print(f"    ⚠ 探测宽高比失败({rel}):{exc};该条退回 CSS 默认占位")
        ar = None
    _AR_CACHE[path] = ar
    return ar


def _slide(repo: str, item: dict, kind: str, i: int, n: int) -> str:
    """一张滑页。视频地址放 ``data-src``,由脚本按需挂到 ``src``。"""
    lis = "\n".join(f"                  <li>{html.escape(c)}</li>" for c in COLS[kind])
    ar = _aspect(repo, item[kind])
    style = f' style="--strip-ar: {ar}"' if ar else ""
    cap, short = html.escape(item["caption"]), html.escape(item["short"])
    return f"""              <figure class="carousel-slide" role="group"{style}
                aria-roledescription="slide" aria-label="{i} of {n}: {short} &ndash; {cap}">
                <video controls playsinline muted loop preload="metadata"
                  data-src="{html.escape(item[kind])}"></video>
                <ul class="strip-cols" data-cols="{len(COLS[kind])}" aria-hidden="true">
{lis}
                </ul>
                <figcaption>{cap}<span class="strip-meta">{short}</span></figcaption>
              </figure>"""


def _carousel(repo: str, domain: str, kind: str, items: list[dict], key: str) -> str:
    """一个分组的滑页组件(箭头 + 滑窗 + 圆点 + 计数)。"""
    n = len(items)
    slides = "\n".join(_slide(repo, it, kind, i + 1, n) for i, it in enumerate(items))
    dots = "\n".join(
        f'            <button class="carousel-dot{" is-active" if i == 0 else ""}" type="button"'
        f' role="tab" aria-selected="{"true" if i == 0 else "false"}"'
        f' aria-label="{html.escape(it["short"])} &ndash; {html.escape(it["caption"])}">'
        f'</button>'
        for i, it in enumerate(items))
    # 组里出现过的数据集,按出现顺序去重 —— 组标题旁列一下,读者才知道这组包含什么
    seen: list[str] = []
    for it in items:
        if it["short"] not in seen:
            seen.append(it["short"])
    # 只有一条时藏掉翻页控件:一颗圆点 + "1 / 1" 只是噪声
    nav = "" if n < 2 else f"""
          <button class="carousel-arrow carousel-prev" type="button"
            aria-label="Previous sample">&#10094;</button>
          <button class="carousel-arrow carousel-next" type="button"
            aria-label="Next sample">&#10095;</button>
          <div class="carousel-footer">
            <div class="carousel-dots" role="tablist" aria-label="Choose a sample">
{dots}
            </div>
            <span class="carousel-counter" aria-live="polite">1 / {n}</span>
          </div>"""
    return f"""        <div class="source-group">
          <div class="source-head">
            <h4>{html.escape(domain)}</h4>
            <span class="source-count">{html.escape(", ".join(seen))} &middot;
              {n} sample{"" if n == 1 else "s"}</span>
          </div>
          <div class="strip-carousel" data-carousel aria-roledescription="carousel"
            aria-label="{html.escape(domain)} &ndash; {html.escape(key)}">
            <div class="carousel-stage">
              <div class="carousel-window">
                <div class="carousel-track">
{slides}
                </div>
              </div>
            </div>{nav}
          </div>
        </div>"""


def main() -> int:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    man = json.load(open(os.path.join(repo, "tools", "cases_manifest.json"), encoding="utf-8"))
    cases = man["cases"]

    # 清单里有、DOMAINS 里没有的源 = 会被静默丢掉 → 直接报错
    known = {s for _, srcs in DOMAINS for s, _ in srcs}
    if unknown := sorted(set(cases) - known):
        raise SystemExit(f"ERROR: cases_manifest 里的源 {unknown} 未在 DOMAINS 中分组 —— "
                         f"补进 tools/build_index.py 的 DOMAINS,否则这些 case 不会上页")

    articles, tally = [], {}
    for anchor, kind, title, lede in SECTIONS:
        groups, n = [], []
        for domain, srcs in DOMAINS:
            items = [dict(r, short=short) for src, short in srcs
                     for r in (cases.get(src) or []) if r.get(kind)]
            if not items:
                continue
            groups.append(_carousel(repo, domain, kind, items, title))
            n.append(f"{domain} {len(items)}")
        tally[title] = " / ".join(n)
        articles.append(f"""      <article id="{anchor}" class="body-card subsection-card">
        <h3>{html.escape(title)}</h3>
        <p class="subsection-lede">{lede}</p>
{chr(10).join(groups)}
      </article>""")

    block = BEGIN + "\n" + "\n\n".join(articles) + "\n      " + END

    path = os.path.join(repo, "index.html")
    doc = open(path, encoding="utf-8").read()
    if BEGIN not in doc or END not in doc:
        raise SystemExit(f"ERROR: index.html 里找不到 {BEGIN} / {END} 标记")
    head, rest = doc.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    open(path, "w", encoding="utf-8").write(head + block + tail)
    print(f">>> 写入 {path}")
    for title, n in tally.items():
        print(f">>>   {title}:{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
