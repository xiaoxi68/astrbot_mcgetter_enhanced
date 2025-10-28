from typing import List, Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont
import io
import base64
from datetime import datetime
import math

# ==========================
# Styling & Layout Constants
# ==========================
# Colors
BG_COLOR = (26, 27, 31)
FG_COLOR = (240, 240, 245)
ACCENT = (90, 250, 170)
ACCENT_LIGHT = (140, 255, 200)
GRID_COLOR = (70, 70, 78)
GRID_LIGHT_COLOR = (48, 48, 56)
STAT_COLOR = (185, 185, 196)

# Margins
MARGIN_LEFT = 70
MARGIN_RIGHT = 36
MARGIN_TOP = 70
MARGIN_BOTTOM = 52

# Fonts
TITLE_FONT_SIZE = 22
AXIS_FONT_SIZE = 12
STAT_FONT_SIZE = 11
TITLE_Y = 15

# Grid / Axis
GRID_LINES = 5
DASH_PATTERN = (5, 3)
Y_TICK_STEP = 5
MIN_Y_MAX = 5

# Bars
BAR_MIN_WIDTH = 10
BAR_MAX_WIDTH = 36
BAR_WIDTH_FACTOR = 0.68
BAR_RADIUS = 4
SHADOW_OFFSET = 2
LABEL_GAP = 8


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Load a CJK-capable font; prefer bundled Chinese font, then system CJK fonts, then DejaVu, last default."""
    from pathlib import Path
    candidates = [
        # Bundled font (preferred)
        Path(__file__).resolve().parent.parent / "resource" / "msyh.ttf",
        # Linux common CJK fonts
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/zh_CN/msyh.ttf",
        # Windows
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Songti.ttc",
        # DejaVu (non-CJK; last resort before default)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        try:
            path_str = str(p)
            return ImageFont.truetype(path_str, size)
        except Exception:
            continue
    return ImageFont.load_default()


def generate_bar_chart_image(history: List[Dict[str, Any]], server_name: str, hours: int = 24, width: int = 820, height: int = 400) -> str:
    """Render a polished hourly bar chart (柱状图) and return base64 PNG.

    history: list of {"ts": int, "count": int}, ascending by time. May have gaps.
    The renderer normalizes to an hourly timeline (fills gaps with 0) so bars align with time.
    """
    # canvas - enhanced colors
    bg = BG_COLOR
    fg = FG_COLOR
    accent = ACCENT
    accent_light = ACCENT_LIGHT
    grid = GRID_COLOR
    grid_light = GRID_LIGHT_COLOR
    stat_color = STAT_COLOR
    
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # Dashed line helper (compat: Pillow < 10 has no dash kw)
    def dashed_line(p0: tuple[float, float], p1: tuple[float, float], *, fill, width: int = 1, dash: tuple[int, int] = DASH_PATTERN):
        (x0, y0), (x1, y1) = p0, p1
        on, off = dash
        # Only implement axis-aligned dashes (horizontal/vertical), else fallback solid
        if abs(y0 - y1) < 1e-6:
            # horizontal
            x = x0
            while x < x1:
                x2 = min(x + on, x1)
                draw.line([(x, y0), (x2, y1)], fill=fill, width=width)
                x = x2 + off
            return
        if abs(x0 - x1) < 1e-6:
            # vertical
            y = y0
            while y < y1:
                y2 = min(y + on, y1)
                draw.line([(x0, y), (x1, y2)], fill=fill, width=width)
                y = y2 + off
            return
        # fallback
        draw.line([p0, p1], fill=fill, width=width)

    # layout - balanced margins
    l = MARGIN_LEFT
    r = MARGIN_RIGHT
    t = MARGIN_TOP
    b = MARGIN_BOTTOM

    title_font = _load_font(TITLE_FONT_SIZE)
    axis_font = _load_font(AXIS_FONT_SIZE)
    stat_font = _load_font(STAT_FONT_SIZE)

    # title with dynamic hours
    try:
        hrs = int(hours)
    except Exception:
        hrs = 24
    title = f"{server_name} · {hrs}小时在线人数"
    draw.text((l, TITLE_Y), title, fill=fg, font=title_font)

    # bounds
    plot_w = width - l - r
    plot_h = height - t - b
    x0, y0 = l, t
    x1, y1 = l + plot_w, t + plot_h
    draw.rectangle([x0, y0, x1, y1], outline=grid)

    # data
    if not history:
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    # Normalize to hourly timeline: ensure bars align with hour ticks
    def hour_bucket(ts: int) -> int:
        return int(ts // 3600 * 3600)

    # map original to bucket -> last value
    raw = {}
    for d in history:
        ts = int(d.get("ts", 0) or 0)
        cnt = int(d.get("count", 0) or 0)
        if ts:
            raw[hour_bucket(ts)] = cnt

    if not raw:
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    ts_sorted = sorted(raw.keys())
    start_ts = ts_sorted[0]
    end_ts = ts_sorted[-1]
    # cap to last N-hour window based on input hours
    window_hours = max(1, int(hrs))
    if end_ts - start_ts > (window_hours - 1) * 3600:
        start_ts = end_ts - (window_hours - 1) * 3600

    timeline: List[int] = []
    cur = start_ts
    while cur <= end_ts:
        timeline.append(cur)
        cur += 3600

    counts = [int(raw.get(ts, 0)) for ts in timeline]
    n = len(counts)
    max_c = max(counts) if counts else 1
    min_c = 0
    
    # helper for text size (Pillow兼容:优先使用textbbox) - 必须先定义
    def text_size(s: str, f: ImageFont.ImageFont) -> tuple[int, int]:
        try:
            bx = draw.textbbox((0, 0), s, font=f)
            return bx[2] - bx[0], bx[3] - bx[1]
        except Exception:
            # 极端退化
            try:
                return int(draw.textlength(s, font=f)), int(f.size)
            except Exception:
                return (len(s) * 8, 12)
    
    # Calculate statistics
    avg_c = sum(counts) // len(counts) if counts else 0
    
    # Draw statistics info
    stat_text = f"最大: {max_c}  平均: {avg_c}  数据点: {n}"
    tw, th = text_size(stat_text, stat_font)
    draw.text((width - r - tw, 18), stat_text, fill=stat_color, font=stat_font)

    def x_at(i: int) -> float:
        """Center of bar i over hourly ticks across the full width."""
        if n <= 1:
            return x0 + plot_w / 2
        spacing = plot_w / n
        return x0 + spacing * (i + 0.5)

    # Choose a nice Y max and draw horizontal grid
    # 保证顶部留白：最高柱值+1，再取到“好看”的5的倍数
    target_top = max(0, max_c + 1)
    y_max = int(math.ceil(target_top / float(Y_TICK_STEP)) * Y_TICK_STEP) if target_top > 0 else MIN_Y_MAX
    if y_max < MIN_Y_MAX:
        y_max = MIN_Y_MAX
    def y_at(c: int) -> float:
        norm = (c - min_c) / max(1, (y_max - min_c))
        return y1 - norm * plot_h

    num_grid_lines = GRID_LINES
    for i in range(num_grid_lines + 1):
        frac = i / num_grid_lines
        y = y1 - frac * plot_h
        line_color = grid if i in [0, num_grid_lines] else grid_light
        draw.line([(x0, y), (x1, y)], fill=line_color, width=1)
        val = int(round(min_c + (y_max - min_c) * frac))
        text = str(val)
        tw, th = text_size(text, axis_font)
        draw.text((x0 - 12 - tw, y - th/2), text, fill=fg, font=axis_font)
    
    # 平均值虚线省略（右上角已显示平均值）

    # X-axis ticks: label start, quarter points, and end
    if n <= 8:
        label_indices = list(range(n))
    else:
        label_indices = sorted(set([0, n//4, n//2, 3*n//4, n-1]))

    for i in label_indices:
        x = x_at(i)
        draw.line([(x, y1), (x, y1 + 5)], fill=grid, width=1)
        ts = timeline[i]
        lab = datetime.fromtimestamp(ts).strftime("%H:%M")
        tw, th = text_size(lab, axis_font)
        draw.text((x - tw/2, y1 + 8), lab, fill=fg, font=axis_font)

    # bars with enhanced visual effects
    xs = [x_at(i) for i in range(n)]
    spacing = plot_w / n if n > 0 else plot_w
    bar_w = max(BAR_MIN_WIDTH, min(BAR_MAX_WIDTH, spacing * BAR_WIDTH_FACTOR))
    
    for i, c in enumerate(counts):
        cx = xs[i]
        left = cx - bar_w / 2
        right = cx + bar_w / 2
        top = y_at(c)
        
        # Soft shadow
        shadow_offset = SHADOW_OFFSET
        draw.rectangle([left + shadow_offset, top + shadow_offset, right + shadow_offset, y1 + shadow_offset], fill=(20, 20, 22))
        
        # Single solid color bar (consistent top and bottom)
        bar_height = y1 - top
        radius = BAR_RADIUS
        draw.rounded_rectangle([left, top, right, y1], radius=radius, fill=accent)
        
        # No border/highlight for a cleaner flat style
        
        # Value label with better positioning logic
        label = str(c)
        tw, th = text_size(label, axis_font)
        
        # 始终绘制在柱顶上方；空间不足时贴紧上边界（不放入柱内）
        gap = LABEL_GAP
        label_y = max(y0 + 2, top - th - gap)
        label_x = max(x0 + 2, min(x1 - tw - 2, cx - tw/2))
        # 阴影 + 文字
        draw.text((label_x, label_y + 1), label, fill=(12, 12, 14), font=axis_font)
        draw.text((label_x, label_y), label, fill=ACCENT_LIGHT, font=axis_font)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")
