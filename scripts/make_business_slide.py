"""Generate docs/business_slide.pptx — a single business-friendly overview slide."""

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

DARK = RGBColor(0x1F, 0x2A, 0x44)
ACCENT = RGBColor(0x2E, 0x6D, 0xB4)
LIGHT = RGBColor(0xEE, 0xF3, 0xFA)
GREY = RGBColor(0x55, 0x5F, 0x6E)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank


def textbox(left, top, width, height, lines, size=14, color=DARK, bold_first=False,
            align=PP_ALIGN.LEFT, fill=None, line_spacing=1.15):
    box = slide.shapes.add_textbox(left, top, width, height)
    if fill is not None:
        box.fill.solid()
        box.fill.fore_color.rgb = fill
    tf = box.text_frame
    tf.word_wrap = True
    for i, (text, bold) in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_spacing
        run = p.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.color.rgb = color
        run.font.bold = bold or (bold_first and i == 0)
        run.font.name = "Calibri"
    return tf


def bullets(left, top, width, height, items, size=14, color=DARK):
    tf = textbox(left, top, width, height, [(t, False) for t in items], size=size, color=color)
    for p in tf.paragraphs:
        p.text = p.text  # keep text
    return tf


def header(left, top, text, width=Inches(4)):
    textbox(left, top, width, Inches(0.35), [(text, True)], size=16, color=ACCENT)


# Title
textbox(Inches(0.5), Inches(0.25), Inches(12.3), Inches(0.7),
        [("Wealth Research Copilot — AI Assistant for Analysts", True)],
        size=30, color=DARK)

# Section 1: Problem (left)
header(Inches(0.5), Inches(1.15), "The Problem Today")
textbox(Inches(0.5), Inches(1.55), Inches(5.9), Inches(1.9), [
    ("• Analysts spend hours digging through company filings and reports for answers", False),
    ("• Market prices and research documents sit in different systems", False),
    ("• Answers are hard to trace back to their source", False),
], size=14, color=GREY)

# Section 2: Solution (right)
header(Inches(6.8), Inches(1.15), "The Solution")
textbox(Inches(6.8), Inches(1.55), Inches(6.0), Inches(1.9), [
    ("• One simple chat screen — ask a question in plain English, get a clear answer", False),
    ("• Every answer shows where it came from, so it can be verified and trusted", False),
    ("• Today: running on safe sample data · Coming next: live market prices", False),
], size=14, color=GREY)

# Section 3: How It Works — 4 step boxes with arrows
header(Inches(0.5), Inches(3.55), "How It Works")
steps = [
    "1. Analyst asks\na question",
    "2. Copilot finds the\nright documents & prices",
    "3. AI writes a clear\nanswer with sources",
    "4. Analyst reviews\nand shares",
]
box_w, box_h, gap = Inches(2.8), Inches(1.0), Inches(0.32)
x = Inches(0.5)
top = Inches(4.0)
for i, label in enumerate(steps):
    shape = slide.shapes.add_shape(5, x, top, box_w, box_h)  # rounded rect
    shape.fill.solid()
    shape.fill.fore_color.rgb = LIGHT
    shape.line.color.rgb = ACCENT
    shape.line.width = Pt(1.25)
    tf = shape.text_frame
    tf.word_wrap = True
    for j, line_txt in enumerate(label.split("\n")):
        p = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = line_txt
        run.font.size = Pt(13)
        run.font.bold = j == 0
        run.font.color.rgb = DARK
    if i < 3:
        arrow = slide.shapes.add_shape(29, x + box_w + Emu(20000), top + Inches(0.32),
                                       gap - Emu(40000), Inches(0.36))  # right arrow
        arrow.fill.solid()
        arrow.fill.fore_color.rgb = ACCENT
        arrow.line.fill.background()
    x += box_w + gap

# Section 4: Business Value (bottom strip)
strip = slide.shapes.add_shape(1, Inches(0.5), Inches(5.6), Inches(12.3), Inches(1.5))
strip.fill.solid()
strip.fill.fore_color.rgb = LIGHT
strip.line.color.rgb = ACCENT
strip.line.width = Pt(1.0)
tf = strip.text_frame
tf.word_wrap = True
lines = [
    ("Business Value", True),
    ("• Answers in seconds, not hours", False),
    ("• Every claim is backed by a visible source", False),
    ("• Same interface scales to real market data when connected", False),
]
for i, (t, b) in enumerate(lines):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    run = p.add_run()
    run.text = t
    run.font.size = Pt(16 if b else 14)
    run.font.bold = b
    run.font.color.rgb = ACCENT if b else DARK

prs.save("docs/business_slide.pptx")
print("saved docs/business_slide.pptx")
