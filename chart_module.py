# -*- coding: utf-8 -*-
"""
图表生成模块 - matplotlib 可视化
支持：柱状图、折线图、饼图、横向柱状图
"""
import os, uuid, io, base64
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# 设置中文字体
plt.rcParams["axes.unicode_minus"] = False

# 尝试加载中文字体
_chinese_font = None
_font_candidates = [
    "Microsoft YaHei", "SimHei", "KaiTi", "FangSong",
    "Noto Sans CJK SC", "WenQuanYi Micro Hei", "Arial Unicode MS"
]
for fn in _font_candidates:
    try:
        fm.findfont(fn, fallback_to_default=False)
        _chinese_font = fn
        break
    except:
        pass

if _chinese_font:
    plt.rcParams["font.sans-serif"] = [_chinese_font, "DejaVu Sans"]
else:
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    print("[Chart] Warning: No Chinese font found, labels may not display correctly")

# 输出目录
CHART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "charts")
os.makedirs(CHART_DIR, exist_ok=True)

# 配色方案
COLORS = ["#2563eb", "#7c3aed", "#059669", "#d97706", "#dc2626",
          "#0891b2", "#4f46e5", "#ea580c", "#16a34a", "#b91c1c"]


def _save_and_encode(fig):
    """保存图表并返回 base64 编码"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    plt.close(fig)
    return img_base64


def bar_chart(labels, values, title="", xlabel="", ylabel="", top_k=None):
    """柱状图 — 排名、对比"""
    if top_k:
        labels = labels[:top_k]
        values = values[:top_k]

    fig, ax = plt.subplots(figsize=(10, 5))
    colors = [COLORS[i % len(COLORS)] for i in range(len(labels))]
    bars = ax.bar(range(len(labels)), values, color=colors, edgecolor="white", linewidth=0.5)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=10)
    ax.set_ylabel(ylabel or "万元", fontsize=11)
    ax.set_title(title or "财务数据对比", fontsize=14, fontweight="bold", pad=15)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.3)

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values) * 0.01,
                f"{val:,.0f}", ha="center", va="bottom", fontsize=9)

    return _save_and_encode(fig)


def line_chart(x_labels, values, title="", xlabel="", ylabel="", marker="o"):
    """折线图 — 趋势分析"""
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(x_labels, values, marker=marker, color=COLORS[0], linewidth=2.5,
            markersize=8, markerfacecolor="white", markeredgewidth=2, markeredgecolor=COLORS[0])

    for i, (x, y) in enumerate(zip(x_labels, values)):
        ax.annotate(f"{y:,.0f}", (x, y), textcoords="offset points",
                    xytext=(0, 14), ha="center", fontsize=10, fontweight="bold", color=COLORS[0])

    ax.fill_between(range(len(x_labels)), values, alpha=0.1, color=COLORS[0])
    ax.set_xticks(range(len(x_labels)))
    ax.set_xticklabels(x_labels, fontsize=10)
    ax.set_ylabel(ylabel or "万元", fontsize=11)
    ax.set_title(title or "趋势分析", fontsize=14, fontweight="bold", pad=15)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.3)

    return _save_and_encode(fig)


def hbar_chart(labels, values, title="", xlabel="", top_k=None):
    """横向柱状图 — 适合长标签"""
    if top_k:
        labels = labels[:top_k]
        values = values[:top_k]

    fig, ax = plt.subplots(figsize=(10, max(4, len(labels) * 0.5)))
    colors = [COLORS[i % len(COLORS)] for i in range(len(labels))]
    bars = ax.barh(range(len(labels)), values, color=colors, edgecolor="white", linewidth=0.5)

    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel(xlabel or "万元", fontsize=11)
    ax.set_title(title or "财务数据对比", fontsize=14, fontweight="bold", pad=15)
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", alpha=0.3)

    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + max(values) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{val:,.0f}", va="center", fontsize=9)

    return _save_and_encode(fig)


def pie_chart(labels, values, title=""):
    """饼图 — 占比分析"""
    fig, ax = plt.subplots(figsize=(8, 6))
    colors = COLORS[:len(labels)]
    wedges, texts, autotexts = ax.pie(
        values, labels=labels, autopct="%1.1f%%",
        colors=colors, startangle=90,
        textprops={"fontsize": 10},
        pctdistance=0.75
    )
    for at in autotexts:
        at.set_fontweight("bold")
        at.set_fontsize(11)

    ax.set_title(title or "占比分析", fontsize=14, fontweight="bold", pad=15)

    return _save_and_encode(fig)


# ==================== 测试入口 ====================
if __name__ == "__main__":
    print("Testing chart generation...")

    img1 = bar_chart(
        ["华润三九", "白云山", "同仁堂", "太极集团", "云南白药"],
        [89501, 76230, 52180, 41050, 38920],
        title="2024年营收排名 Top 5", ylabel="万元"
    )
    print(f"Bar chart: {len(img1)} chars base64")

    img2 = line_chart(
        ["2022", "2023", "2024"],
        [67230, 75810, 89501],
        title="华润三九主营业务收入趋势", ylabel="万元"
    )
    print(f"Line chart: {len(img2)} chars base64")

    img3 = pie_chart(
        ["中药", "化药", "生物药", "器械"],
        [45, 30, 15, 10],
        title="产品结构占比"
    )
    print(f"Pie chart: {len(img3)} chars base64")

    img4 = hbar_chart(
        ["金花企业(集团)股份有限公司", "华润三九医药股份有限公司",
         "广州白云山医药集团", "北京同仁堂股份有限公司"],
        [89501, 76230, 52180, 41050],
        title="研发投入对比", xlabel="万元"
    )
    print(f"H-Bar chart: {len(img4)} chars base64")

    print("All chart types OK!")
