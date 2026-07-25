"""
md2pdf.py — 将播客追踪器输出的 Markdown 文档转换为 PDF
使用 reportlab + markdown 库，支持中文排版
"""

import re
import sys
import hashlib
import tempfile
import unicodedata
from pathlib import Path
from urllib.request import urlretrieve, Request, urlopen
from markdown import markdown
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image as RLImage,
    PageBreak, HRFlowable, ListFlowable, ListItem, Table, TableStyle
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 注册中文字体（跨平台自动检测）──
def _find_font_dir() -> Path:
    """Detect system font directory."""
    if sys.platform == 'win32':
        return Path("C:/Windows/Fonts")
    elif sys.platform == 'darwin':
        return Path("/System/Library/Fonts")
    else:
        # Linux common paths
        for d in ["/usr/share/fonts", "/usr/local/share/fonts"]:
            if Path(d).exists():
                return Path(d)
        return Path("/usr/share/fonts")  # fallback

FONT_DIR = _find_font_dir()

# 注册字体（缺失时跳过，PDF 将使用默认字体）
_expected = {
    "SimSun": ("simsun.ttc", 0),   # subfontIndex for .ttc
    "SimHei": ("simhei.ttf", None),
    "SimKai": ("simkai.ttf", None),
    "SimFang": ("simfang.ttf", None),
}
_registered = 0
for name, (fname, idx) in _expected.items():
    path = FONT_DIR / fname
    if path.exists():
        try:
            kwargs = {"subfontIndex": idx} if idx is not None else {}
            pdfmetrics.registerFont(TTFont(name, str(path), **kwargs))
            _registered += 1
        except Exception:
            pass
    # 备选：macOS 字体
    elif sys.platform == 'darwin':
        mac_fonts = {"SimSun": "Songti.ttc", "SimHei": "Heiti.ttc",
                     "SimKai": "Kaiti.ttc", "SimFang": None}
        alt = mac_fonts.get(name)
        if alt and (FONT_DIR / alt).exists():
            try:
                pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / alt)))
                _registered += 1
            except Exception:
                pass

if _registered == 0:
    print("⚠️  未找到中文字体，PDF 可能无法正确渲染中文。"
          "请安装 SimSun/SimHei 字体或设置 XZ_FONT_DIR 环境变量指向字体目录。")

# ── 颜色常量 ──
C_PRIMARY = HexColor("#1a1a2e")
C_ACCENT = HexColor("#0f3460")
C_LIGHT = HexColor("#666666")
C_BORDER = HexColor("#cccccc")
C_BG_TOPIC = HexColor("#f5f5f5")
C_QUOTE_BG = HexColor("#eef3fb")  # 淡蓝背景，用于段落摘要卡片
C_LINK = HexColor("#0366d6")

# ── 样式定义 ──
STYLES = {
    "title": ParagraphStyle(
        "title", fontName="SimHei", fontSize=22, leading=30,
        textColor=C_PRIMARY, alignment=TA_CENTER,
        spaceAfter=6*mm, spaceBefore=10*mm,
    ),
    "meta": ParagraphStyle(
        "meta", fontName="SimSun", fontSize=9, leading=14,
        textColor=C_LIGHT, alignment=TA_CENTER,
        spaceAfter=4*mm,
    ),
    "h1": ParagraphStyle(
        "h1", fontName="SimHei", fontSize=22, leading=30,
        textColor=C_PRIMARY, alignment=TA_CENTER,
        spaceAfter=6*mm, spaceBefore=10*mm,
    ),
    "h2": ParagraphStyle(
        "h2", fontName="SimHei", fontSize=16, leading=22,
        textColor=C_ACCENT, spaceBefore=8*mm, spaceAfter=3*mm,
        borderWidth=0, borderPadding=0,
    ),
    "h3": ParagraphStyle(
        "h3", fontName="SimHei", fontSize=13, leading=18,
        textColor=C_PRIMARY, spaceBefore=5*mm, spaceAfter=2*mm,
    ),
    "h4": ParagraphStyle(
        "h4", fontName="SimHei", fontSize=11, leading=16,
        textColor=HexColor("#333333"), spaceBefore=3*mm, spaceAfter=2*mm,
    ),
    "h5": ParagraphStyle(
        "h5", fontName="SimHei", fontSize=10.5, leading=15,
        textColor=C_ACCENT, spaceBefore=4*mm, spaceAfter=1.5*mm,
    ),
    "h6": ParagraphStyle(
        "h6", fontName="SimHei", fontSize=10, leading=14,
        textColor=HexColor("#444444"), spaceBefore=3*mm, spaceAfter=1*mm,
    ),
    "body": ParagraphStyle(
        "body", fontName="SimSun", fontSize=10, leading=17,
        textColor=HexColor("#333333"), alignment=TA_JUSTIFY,
        spaceBefore=1.5*mm, spaceAfter=1.5*mm,
        firstLineIndent=20,
        wordWrap="CJK",
    ),
    "body_no_indent": ParagraphStyle(
        "body_no_indent", fontName="SimSun", fontSize=10, leading=17,
        textColor=HexColor("#333333"), alignment=TA_JUSTIFY,
        spaceBefore=1.5*mm, spaceAfter=1.5*mm,
        wordWrap="CJK",
    ),
    "quote": ParagraphStyle(
        "quote", fontName="SimKai", fontSize=9.5, leading=15,
        textColor=HexColor("#444444"), alignment=TA_JUSTIFY,
        leftIndent=0, rightIndent=0,
        spaceBefore=0, spaceAfter=0,
        wordWrap="CJK",
    ),
    "timestamp": ParagraphStyle(
        "timestamp", fontName="SimSun", fontSize=9, leading=14,
        textColor=C_LIGHT, spaceBefore=1*mm, spaceAfter=0.5*mm,
    ),
    "footer": ParagraphStyle(
        "footer", fontName="SimSun", fontSize=8, leading=12,
        textColor=C_LIGHT, alignment=TA_CENTER,
    ),
    "bullet": ParagraphStyle(
        "bullet", fontName="SimSun", fontSize=10, leading=17,
        textColor=HexColor("#333333"), alignment=TA_JUSTIFY,
        leftIndent=8*mm, bulletIndent=3*mm,
        spaceBefore=1*mm, spaceAfter=1*mm,
        wordWrap="CJK",
    ),
    "code_block": ParagraphStyle(
        "code_block", fontName="SimFang", fontSize=8.5, leading=13,
        textColor=HexColor("#333333"),
        leftIndent=5*mm, rightIndent=5*mm,
        spaceBefore=2*mm, spaceAfter=2*mm,
        backColor=C_BG_TOPIC,
    ),
    "continuation": ParagraphStyle(
        "continuation", fontName="SimSun", fontSize=10, leading=17,
        textColor=HexColor("#555555"), alignment=TA_JUSTIFY,
        leftIndent=12*mm, bulletIndent=3*mm,
        spaceBefore=0.5*mm, spaceAfter=1*mm,
        wordWrap="CJK",
    ),
    "sub_heading": ParagraphStyle(
        "sub_heading", fontName="SimHei", fontSize=10.5, leading=16,
        textColor=C_ACCENT,
        leftIndent=3*mm,
        spaceBefore=3*mm, spaceAfter=1.5*mm,
    ),
    "link": ParagraphStyle(
        "link", fontName="SimSun", fontSize=9, leading=14,
        textColor=C_LINK, alignment=TA_CENTER,
        spaceAfter=3*mm,
    ),
}


def _escape_xml(text: str) -> str:
    """转义 XML 特殊字符"""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _process_text(text: str) -> str:
    """先替换 emoji，再转义 XML（行内格式由外层 _parse_inline 处理）"""
    text = _replace_emoji(text)
    return _escape_xml(text)


# ── Emoji 替换表（中文字体不含 emoji 字形） ──
# 装饰性 emoji → Unicode 方块符号（中文字体可渲染）
# 语义性 emoji → 中文描述前缀
# ✅/❌ → ■ / □ 区分肯定/否定
_EMOJI_MAP = {
    "📋": "■ ",
    "📑": "■ ",
    "📍": "时间·",
    "🔗": "▸ ",
    "🔥": "热点·",
    "💡": "提示·",
    "📌": "标注·",
    "✅": "■ ",
    "❌": "□ ",
    "⚠️": "注意·",
    "💬": "评论·",
    "🎵": "音乐·",
    "▶️": "播放·",
    "📝": "笔记·",
    "🎯": "目标·",
    "🤔": "思考·",
    "📊": "数据·",
    "🌟": "亮点·",
}

def _replace_emoji(text: str) -> str:
    """将 emoji 替换为中文描述，确保中文字体可渲染"""
    for emoji, replacement in _EMOJI_MAP.items():
        text = text.replace(emoji, replacement)
    # 兜底：处理不在表中的 emoji
    result = []
    for ch in text:
        cp = ord(ch)
        # 常见 emoji 范围（简易检测）
        if (0x1F300 <= cp <= 0x1F9FF or 0x2600 <= cp <= 0x26FF
                or 0x2700 <= cp <= 0x27BF or 0xFE00 <= cp <= 0xFE0F
                or 0x1FA00 <= cp <= 0x1FA6F or 0x1FA70 <= cp <= 0x1FAFF
                or 0x200D == cp or 0x20E3 == cp):
            # 尝试获取 Unicode 名称
            try:
                name = unicodedata.name(ch, "?")
                # 取第一个词
                word = name.split()[0].replace("_", "")
                result.append(f"[{word}]")
            except Exception:
                result.append("")
        else:
            result.append(ch)
    return "".join(result)


_IMG_CACHE_DIR = Path(tempfile.gettempdir()) / "podcast_transcriber_img_cache"
_IMG_CACHE_DIR.mkdir(exist_ok=True)


def _download_image(url: str, timeout: int = 15) -> "Path | None":
    """下载远程图片到临时缓存，返回本地路径；失败返回 None"""
    try:
        # 用 URL hash 做缓存文件名
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        suffix = ".jpg"
        for ext in (".png", ".jpeg", ".jpg", ".webp", ".gif"):
            if ext in url.lower().split("?")[0]:
                suffix = ext
                break
        cache_file = _IMG_CACHE_DIR / f"{url_hash}{suffix}"
        if cache_file.exists() and cache_file.stat().st_size > 0:
            return cache_file
        req = Request(url, headers={"User-Agent": "PodcastTranscriber/1.0 (personal-use)"})
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 100:
            return None
        cache_file.write_bytes(data)
        return cache_file
    except Exception:
        return None


def _embed_image(url: str, alt: str = ""):
    """下载远程图片并嵌入为 RLImage Flowable，失败则返回占位文字"""
    local = _download_image(url)
    if not local:
        return Paragraph(f"<i>[图片: {alt}]</i>", STYLES["meta"])
    try:
        from PIL import Image as PILImage
        from reportlab.lib.pagesizes import A4
        # 读取图片获取原始尺寸
        with PILImage.open(local) as pil_img:
            orig_w, orig_h = pil_img.size
        # 计算目标尺寸：最大宽度 170mm (A4内容区)，最大高度 100mm
        max_w = 170 * mm
        max_h = 100 * mm
        # 按比例缩放
        scale_w = max_w / orig_w
        scale_h = max_h / orig_h
        scale = min(scale_w, scale_h, 1.0)  # 不放大，只缩小
        target_w = orig_w * scale
        target_h = orig_h * scale
        img = RLImage(str(local), width=target_w, height=target_h)
        img.hAlign = "CENTER"
        return img
    except Exception:
        return Paragraph(f"<i>[图片: {alt}]</i>", STYLES["meta"])


def _parse_inline(text: str) -> str:
    """处理行内格式：粗体、斜体（星号+下划线）、行内代码、链接"""
    # 粗体 **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # 斜体 *text* (单星号，非粗体)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    # 斜体 _text_ (下划线)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<i>\1</i>', text)
    # 行内代码 `text`
    text = re.sub(r'`(.+?)`', r'<font face="SimFang" size="9">\1</font>', text)
    # 链接 [text](url) — 保留文本和 URL
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<font color="#0366d6">\1</font> <font size="8" color="#888888">\2</font>', text)
    return text


def _parse_table_row(line: str) -> list:
    """解析 Markdown 表格行，返回单元格文本列表"""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _build_table(table_lines: list) -> Table:
    """将 Markdown 表格行列表解析为 reportlab Table"""
    if len(table_lines) < 2:
        return None

    headers = _parse_table_row(table_lines[0])
    # 第二行是分隔行，跳过
    data_rows = [_parse_table_row(row) for row in table_lines[2:]]

    # 确定列数（取最大）
    col_count = len(headers)
    for row in data_rows:
        col_count = max(col_count, len(row))

    # 补齐短行
    def pad(cells):
        return cells + [""] * (col_count - len(cells))

    headers = pad(headers)
    data_rows = [pad(row) for row in data_rows]

    # 构建 cell 数据（Paragraph 支持富文本）
    cell_style = ParagraphStyle(
        "table_cell", fontName="SimSun", fontSize=9, leading=14,
        textColor=HexColor("#333333"), wordWrap="CJK",
        spaceBefore=0, spaceAfter=0,
    )
    header_style = ParagraphStyle(
        "table_header", fontName="SimHei", fontSize=9, leading=14,
        textColor=HexColor("#1a1a2e"), wordWrap="CJK",
        spaceBefore=0, spaceAfter=0,
    )

    table_data = []
    # 表头
    header_cells = []
    for h in headers:
        text = _parse_inline(_process_text(h))
        header_cells.append(Paragraph(f"<b>{text}</b>", header_style))
    table_data.append(header_cells)

    # 数据行
    for row in data_rows:
        row_cells = []
        for cell in row:
            text = _parse_inline(_process_text(cell))
            row_cells.append(Paragraph(text, cell_style))
        table_data.append(row_cells)

    # 列宽：均分可用宽度（A4 - 左右边距）
    avail_w = A4[0] - 40 * mm
    col_w = avail_w / col_count

    table = Table(table_data, colWidths=[col_w] * col_count)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "SimHei"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#f5f5f5")),
        ("TEXTCOLOR", (0, 0), (-1, -1), HexColor("#333333")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5 * mm),
    ]))
    return table


def md_to_flowables(md_text: str) -> list:
    """将 Markdown 文本解析为 reportlab Flowable 列表"""
    elements = []
    lines = md_text.split("\n")
    in_code_block = False
    code_buffer = []
    in_quote = False
    quote_buffer = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # ── 代码块 ──
        if line.strip().startswith("```"):
            if in_code_block:
                # 结束代码块
                code_text = "\n".join(code_buffer)
                # 检测是否为话题分段索引块（包含 ### 📍 模式）
                if re.search(r'^###\s*📍', code_text, re.MULTILINE):
                    # 将索引块解析为结构化内容
                    for cline in code_text.split("\n"):
                        cline_stripped = cline.strip()
                        if cline_stripped == "":
                            continue
                        # 索引标题行 ### 📍 HH:MM - HH:MM | 标题
                        idx_heading = re.match(
                            r'^###\s*(.+)$', cline_stripped
                        )
                        if idx_heading:
                            heading_text = idx_heading.group(1)
                            heading_text = _parse_inline(_process_text(heading_text))
                            elements.append(Paragraph(heading_text, STYLES["h4"]))
                            continue
                        # 索引描述行
                        desc_text = _parse_inline(_process_text(cline_stripped))
                        elements.append(Paragraph(desc_text, STYLES["body_no_indent"]))
                else:
                    code_text = _escape_xml(_replace_emoji(code_text))
                    # 将换行转为 <br/>，保留代码块内的分行
                    code_text = code_text.replace("\n", "<br/>")
                    elements.append(Paragraph(code_text, STYLES["code_block"]))
                code_buffer = []
                in_code_block = False
            else:
                # 刷新之前的引用缓冲
                _flush_quote(elements, quote_buffer)
                quote_buffer = []
                in_quote = False
                in_code_block = True
            i += 1
            continue

        if in_code_block:
            code_buffer.append(line)
            i += 1
            continue

        # ── 空行 ──
        if line.strip() == "":
            _flush_quote(elements, quote_buffer)
            quote_buffer = []
            in_quote = False
            i += 1
            continue

        # ── 引用块 ──
        if line.strip().startswith(">"):
            quote_line = re.sub(r'^>\s*', '', line)
            quote_buffer.append(quote_line)
            in_quote = True
            i += 1
            continue
        else:
            if in_quote:
                _flush_quote(elements, quote_buffer)
                quote_buffer = []
                in_quote = False

        # ── 分隔线 ──
        if re.match(r'^---+\s*$', line.strip()):
            elements.append(Spacer(1, 3*mm))
            elements.append(HRFlowable(width="90%", thickness=0.5, color=C_BORDER))
            elements.append(Spacer(1, 3*mm))
            i += 1
            continue

        # ── 标题（h1-h6） ──
        heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if heading_match:
            level = len(heading_match.group(1))
            text = _parse_inline(_process_text(heading_match.group(2)))
            style_key = f"h{min(level, 6)}"
            elements.append(Paragraph(text, STYLES[style_key]))
            i += 1
            continue

        # ── 列表项 ──
        list_match = re.match(r'^(\s*)[-*]\s+(.+)$', line)
        if list_match:
            text = _parse_inline(_process_text(list_match.group(2)))
            elements.append(Paragraph(f"• {text}", STYLES["bullet"]))
            i += 1
            continue

        # ── 有序列表 ──
        olist_match = re.match(r'^(\s*)\d+\.\s+(.+)$', line)
        if olist_match:
            text = _parse_inline(_process_text(olist_match.group(2)))
            num_match = re.match(r'\s*(\d+)\.', line)
            num = num_match.group(1) if num_match else "1"
            elements.append(Paragraph(f"{num}. {text}", STYLES["bullet"]))
            i += 1
            continue

        # ── 图片 ──
        img_match = re.match(r'^!\[(.+?)\]\((.+?)\)', line)
        if img_match:
            alt = img_match.group(1)
            url = img_match.group(2)
            elements.append(_embed_image(url, alt))
            i += 1
            continue

        # ── 箭头续行 →（缩进的解释行） ──
        arrow_match = re.match(r'^(\s+)→\s*(.+)$', line)
        if arrow_match:
            text = _parse_inline(_process_text(arrow_match.group(2)))
            elements.append(Paragraph(f"→ {text}", STYLES["continuation"]))
            i += 1
            continue

        # ── ✅/❌ 子标题行 ──
        subhead_match = re.match(r'^(✅|❌)\s+\*\*(.+?)\*\*\s*$', line)
        if subhead_match:
            emoji = _EMOJI_MAP.get(subhead_match.group(1), "")
            title = _parse_inline(_process_text(subhead_match.group(2)))
            # emoji 值已包含末尾空格，直接拼接
            prefix = emoji if emoji else ""
            elements.append(Paragraph(f"{prefix}<b>{title}</b>", STYLES["sub_heading"]))
            i += 1
            continue

        # ── 表格 ──
        if line.strip().startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            tbl = _build_table(table_lines)
            if tbl:
                elements.append(Spacer(1, 2 * mm))
                elements.append(tbl)
                elements.append(Spacer(1, 2 * mm))
            else:
                # 回退：按普通文本输出
                for tl in table_lines:
                    text = _parse_inline(_process_text(tl))
                    elements.append(Paragraph(text, STYLES["body_no_indent"]))
            continue

        # ── 普通段落 ──
        text = _parse_inline(_process_text(line.strip()))
        elements.append(Paragraph(text, STYLES["body_no_indent"]))
        i += 1

    # 刷新残留引用
    _flush_quote(elements, quote_buffer)

    return elements


def _flush_quote(elements, quote_buffer):
    """将引用缓冲区内容写入 elements，渲染为左侧竖线 + 淡色背景卡片"""
    if not quote_buffer:
        return
    # 构建段落列表
    paragraphs = []
    for qline in quote_buffer:
        text = _parse_inline(_process_text(qline))
        paragraphs.append(Paragraph(text, STYLES["quote"]))

    # 用 Table 实现左侧竖线 + 背景色卡片
    # 左列：3mm 宽的装饰色条；右列：段落内容 + 上下内边距
    accent_para = Paragraph("", STYLES["quote"])  # 空段落占位
    content_cell = [Spacer(1, 1.5*mm)] + paragraphs + [Spacer(1, 1.5*mm)]

    table = Table(
        [[accent_para, content_cell]],
        colWidths=[3*mm, None],
        style=TableStyle([
            # 左列：深蓝色竖条背景（模拟左侧 accent line）
            ("BACKGROUND", (0, 0), (0, -1), C_ACCENT),
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), 0),
            ("TOPPADDING", (0, 0), (0, -1), 0),
            ("BOTTOMPADDING", (0, 0), (0, -1), 0),
            # 右列：淡蓝背景卡片
            ("BACKGROUND", (1, 0), (1, -1), C_QUOTE_BG),
            ("LEFTPADDING", (1, 0), (1, -1), 3*mm),
            ("RIGHTPADDING", (1, 0), (1, -1), 4*mm),
            ("TOPPADDING", (1, 0), (1, -1), 0),
            ("BOTTOMPADDING", (1, 0), (1, -1), 0),
            # 左右列紧密相邻，无间隔
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )
    table.hAlign = "LEFT"

    # 在卡片上下各加一点间距
    elements.append(Spacer(1, 2*mm))
    elements.append(table)
    elements.append(Spacer(1, 1.5*mm))


def convert(md_path: str, pdf_path: str = None):
    """将 Markdown 文件转换为 PDF"""
    md_path = Path(md_path)
    if pdf_path is None:
        pdf_path = md_path.with_suffix(".pdf")

    md_text = md_path.read_text(encoding="utf-8")

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
        title=md_path.stem,
    )

    elements = md_to_flowables(md_text)
    doc.build(elements)
    print(f"✅ PDF 已生成: {pdf_path}")
    return pdf_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python md2pdf.py <input.md> [output.pdf]")
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
