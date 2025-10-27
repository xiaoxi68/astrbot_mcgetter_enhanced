from typing import List, Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont
import io
import base64
from datetime import datetime


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


def generate_trend_image(history: List[Dict[str, Any]], server_name: str, width: int = 800, height: int = 380) -> str:
    """Draw a simple 24h trend bar chart and return base64 PNG.

    history: list of {"ts": int, "count": int}, ascending by time, up to 24.
    """
    # canvas - enhanced colors
    bg = (28, 28, 32)
    fg = (240, 240, 245)
    accent = (85, 255, 170)
    accent_light = (120, 255, 190)
    grid = (70, 70, 75)
    grid_light = (50, 50, 55)
    stat_color = (180, 180, 190)
    
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # Dashed line helper (compat: Pillow < 10 has no dash kw)
    def dashed_line(p0: tuple[float, float], p1: tuple[float, float], *, fill, width: int = 1, dash: tuple[int, int] = (5, 3)):
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

    # layout - increased margins for better spacing
    l = 65
    r = 30
    t = 70
    b = 50

    title_font = _load_font(22)
    axis_font = _load_font(11)
    stat_font = _load_font(10)

    # title
    title = f"{server_name} - 24小时在线人数趋势"
    draw.text((l, 15), title, fill=fg, font=title_font)

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

    counts = [int(d.get("count", 0)) for d in history]
    max_c = max(counts) if counts else 1
    min_c = 0
    n = len(counts)
    
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
        """Calculate x position for bar i, with proper spacing"""
        if n <= 1:
            return x0 + plot_w / 2
        # Use uniform spacing across the plot width
        spacing = plot_w / n
        return x0 + spacing * (i + 0.5)

    def y_at(c: int) -> float:
        rng = max(1, max_c - min_c)
        norm = (c - min_c) / rng
        return y1 - norm * plot_h

    # Enhanced grid - more horizontal lines for better readability
    num_grid_lines = 5
    for i in range(num_grid_lines + 1):
        frac = i / num_grid_lines
        y = y1 - frac * plot_h
        line_color = grid if i in [0, num_grid_lines] else grid_light
        draw.line([(x0, y), (x1, y)], fill=line_color, width=1)
        
        # Y-axis labels
        val = int(min_c + (max_c - min_c) * frac)
        text = str(val)
        tw, th = text_size(text, axis_font)
        draw.text((x0 - 12 - tw, y - th/2), text, fill=fg, font=axis_font)
    
    # Draw average line
    if avg_c > 0:
        avg_y = y_at(avg_c)
        dashed_line((x0, avg_y), (x1, avg_y), fill=(255, 200, 100), width=1, dash=(5, 3))
        avg_label = f"平均: {avg_c}"
        tw, th = text_size(avg_label, stat_font)
        draw.text((x1 - tw - 5, avg_y - th - 3), avg_label, fill=(255, 220, 120), font=stat_font)

    # X-axis ticks - improved time label display
    # Show labels at strategic points: start, middle points, and end
    if n <= 6:
        indices = list(range(n))
    else:
        step = max(1, n // 6)
        indices = list(range(0, n, step))
        if (n - 1) not in indices:
            indices.append(n - 1)
    
    for i in indices:
        x = x_at(i)
        draw.line([(x, y1), (x, y1 + 5)], fill=grid, width=1)
        ts = int(history[i].get("ts", 0))
        lab = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""
        tw, th = text_size(lab, axis_font)
        draw.text((x - tw/2, y1 + 8), lab, fill=fg, font=axis_font)

    # bars with enhanced visual effects
    xs = [x_at(i) for i in range(n)]
    spacing = plot_w / n if n > 0 else plot_w
    bar_w = max(8, min(35, spacing * 0.7))
    
    for i, c in enumerate(counts):
        cx = xs[i]
        left = cx - bar_w / 2
        right = cx + bar_w / 2
        top = y_at(c)
        
        # Draw shadow/depth effect
        shadow_offset = 2
        draw.rectangle(
            [left + shadow_offset, top + shadow_offset, right + shadow_offset, y1 + shadow_offset],
            fill=(20, 20, 22)
        )
        
        # Gradient effect - draw multiple rectangles with varying opacity
        bar_height = y1 - top
        if bar_height > 10:
            # Top part - lighter
            mid_y = top + bar_height * 0.3
            draw.rectangle([left, top, right, mid_y], fill=accent_light)
            # Bottom part - standard color
            draw.rectangle([left, mid_y, right, y1], fill=accent)
        else:
            # For short bars, use single color
            draw.rectangle([left, top, right, y1], fill=accent)
        
        # Border for bars
        draw.rectangle([left, top, right, y1], outline=(100, 255, 200), width=1)
        
        # Highlight maximum value
        if c == max_c and c > 0:
            draw.rectangle([left, top, right, y1], outline=(255, 220, 100), width=2)
        
        # Value label with better positioning logic
        label = str(c)
        tw, th = text_size(label, axis_font)
        
        # Only show labels if there's enough space (avoid overlap)
        show_label = True
        if i > 0 and n > 12:
            # For dense charts, only show every other label or important ones
            show_label = (i % 2 == 0) or (c == max_c) or (i == n - 1) or (i == 0)
        
        if show_label:
            label_y = max(y0 + 2, top - th - 3)
            # Add text background for better readability
            padding = 2
            draw.rectangle(
                [cx - tw/2 - padding, label_y - padding,
                 cx + tw/2 + padding, label_y + th + padding],
                fill=(40, 40, 45)
            )
            draw.text((cx - tw/2, label_y), label, fill=accent_light, font=axis_font)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")
