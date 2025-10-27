from PIL import Image, ImageDraw, ImageFont
import asyncio
import io
from pathlib import Path
import base64
from typing import Optional

async def load_font(font_size):
    # 尝试多路径加载
    font_paths = [
        Path(__file__).resolve().parent.parent/'resource'/'msyh.ttf',
        'msyh.ttf',  # 当前目录
        '/usr/share/fonts/zh_CN/msyh.ttf',  # Linux常见路径
        'C:/Windows/Fonts/msyh.ttc',  # Windows路径
        '/System/Library/Fonts/Supplemental/Songti.ttc'  # macOS路径
    ]
    
    for path in font_paths:
        try:
            return ImageFont.truetype(path, font_size)
        except OSError:
            continue
    
    # 全部失败时使用默认字体（添加中文支持）
    try:
        # 尝试加载PIL的默认中文字体
        return ImageFont.load_default().font_variant(size=font_size)
    except:
        return ImageFont.load_default()

# 在代码中替换字体加载部分
title_font = load_font(30)
text_font = load_font(20)
small_font = load_font(18)

async def fetch_icon(icon_base64: Optional[str] = None) -> Optional[Image.Image]:
    """处理Base64编码的服务器图标"""
    if not icon_base64:
        return None
    
    try:
        # 去除可能的Base64前缀
        if "," in icon_base64:
            icon_base64 = icon_base64.split(",", 1)[1]
        icon_data = base64.b64decode(icon_base64)
        return Image.open(io.BytesIO(icon_data)).convert("RGBA")
    except Exception as e:
        print(f"Base64图标解码失败: {str(e)}")
        return None

async def generate_server_info_image(
    players_list: list,
    latency: int,
    server_name: str,
    plays_max: int,
    plays_online: int,
    server_version: str,
    icon_base64: Optional[str] = None,
    host_address: Optional[str] = None
) -> str:
    """生成服务器信息图片并返回base64编码"""
    
    def measure(draw_ctx: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        try:
            # PIL>=8 提供 textlength，较为准确
            return int(draw_ctx.textlength(text, font=font))
        except Exception:
            # 退化方案
            bbox = draw_ctx.textbbox((0, 0), text, font=font)
            return bbox[2] - bbox[0]

    def wrap_text(draw_ctx: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        if not text:
            return []
        lines = []
        current = ""
        for ch in text:
            trial = current + ch
            if measure(draw_ctx, trial, font) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)
        return lines

    def wrap_players(draw_ctx: ImageDraw.ImageDraw, players: list[str], font: ImageFont.ImageFont, max_width: int) -> list[str]:
        if not players:
            return []
        lines = []
        current = ""
        sep = " • "
        for name in players:
            part = name if not current else current + sep + name
            if measure(draw_ctx, part, font) <= max_width:
                current = part
            else:
                if current:
                    lines.append(current)
                # 如果单个名字已经超过宽度，强制按字符折行
                if measure(draw_ctx, name, font) > max_width:
                    for chunk in wrap_text(draw_ctx, name, font, max_width):
                        lines.append(chunk)
                    current = ""
                else:
                    current = name
        if current:
            lines.append(current)
        return lines

    # 异步获取图标
    server_icon = await fetch_icon(icon_base64)
    
    # 配置参数
    BG_COLOR = (34, 34, 34)
    TEXT_COLOR = (255, 255, 255)
    ACCENT_COLOR = (85, 255, 85)
    WARNING_COLOR = (255, 170, 0)
    ERROR_COLOR = (255, 85, 85)
    
    # 字体配置
    try:
        title_font = await load_font(30)
        text_font = await load_font(20)
        small_font = await load_font(18)
    except IOError:
        title_font = ImageFont.load_default(30)
        text_font = ImageFont.load_default(20)
        small_font = ImageFont.load_default(18)
    
    # 计算布局参数
    icon_size = 64 if server_icon else 0
    base_y = 20
    text_x = 20 + icon_size + 20
    img_width = 600
    right_margin = 20
    left_margin = 20

    # 预先创建测量画布
    tmp_img = Image.new("RGB", (img_width, 10), color=BG_COLOR)
    tmp_draw = ImageDraw.Draw(tmp_img)

    # 顶部名称行高度（和原先一致）
    name_line_height = 40

    # 版本 + 地址 文本，左侧与延迟共享一行；左侧需要根据延迟文本宽度折行
    version_text = f"版本: {server_version}"
    addr_text = f"  地址: {host_address}" if host_address else ""
    version_addr_text = version_text + addr_text

    latency_color = ACCENT_COLOR if latency < 100 else WARNING_COLOR if latency < 200 else ERROR_COLOR
    latency_text = f"延迟: {latency}ms"

    # 左侧可用宽度：延迟不与本行共享，给版本+地址留出全部空间
    allowed_left_width = max(60, img_width - right_margin - text_x)
    version_addr_lines = wrap_text(tmp_draw, version_addr_text, text_font, allowed_left_width)

    # 在线玩家标题行
    online_title = f"在线玩家 ({plays_online}/{plays_max})"
    online_title_height = 40

    # 玩家列表折行
    players_area_max_width = img_width - right_margin - (text_x + 20)
    players_lines = wrap_players(tmp_draw, players_list or [], small_font, players_area_max_width)
    line_height = 30

    # 计算总高度
    calc_y = base_y
    calc_y += name_line_height
    calc_y += max(len(version_addr_lines), 1) * 40
    calc_y += online_title_height  # 延迟与在线玩家同一行
    calc_y += max(len(players_lines), 1) * line_height
    img_height = calc_y + 30  # 底部留白

    # 创建画布
    img = Image.new("RGB", (img_width, img_height), color=BG_COLOR)
    draw = ImageDraw.Draw(img)
    
    # 绘制服务器图标
    if server_icon:
        icon_mask = Image.new("L", (64, 64), 0)
        mask_draw = ImageDraw.Draw(icon_mask)
        mask_draw.rounded_rectangle((0, 0, 64, 64), radius=10, fill=255)
        server_icon.thumbnail((64, 64))
        img.paste(server_icon, (20, base_y), icon_mask)
    
    # 服务器信息绘制（保持原有绘制逻辑不变）
    draw.text((text_x, base_y), server_name, font=title_font, fill=ACCENT_COLOR)
    base_y += 40
    
    # 绘制版本 + 地址（折行）
    for i, line in enumerate(version_addr_lines):
        draw.text((text_x, base_y), line, font=text_font, fill=TEXT_COLOR)
        base_y += 40
    
    # 在线玩家（左） + 延迟（右对齐）同一行
    draw.text((text_x, base_y), online_title, font=text_font, fill=ACCENT_COLOR)
    lat_w = measure(draw, latency_text, text_font)
    draw.text((img_width - right_margin - lat_w, base_y), latency_text, font=text_font, fill=latency_color)
    base_y += 40

    if players_lines:
        for line in players_lines:
            draw.text((text_x + 20, base_y), line, font=small_font, fill=TEXT_COLOR)
            base_y += line_height
    else:
        draw.text((text_x + 20, base_y), "暂无玩家在线", font=small_font, fill=TEXT_COLOR)
        base_y += line_height
    
    draw.rounded_rectangle([10, 10, img.width-10, img.height-10], radius=10, outline=ACCENT_COLOR, width=2)
    
    # 转换为base64
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    # 返回base64 bytes
    return img_base64
