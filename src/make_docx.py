# -*- coding: utf-8 -*-
"""把 Markdown 报告渲染成 Word，并（可选）转成 PDF。

用法：
    python src/make_docx.py out/reports/2026-10-12.md
    python src/make_docx.py out/reports/2026-10-12.md --pdf
    python src/make_docx.py out/reports/2026-10-12.md --pdf --no-docx

为什么自己做而不是用 pandoc：
    这台机器上只有 DSH 自带的 LibreOffice Kit 可以转 PDF，没有 pandoc。
    报告的结构是固定的（标题/段落/列表/表格/引用），自己解析比引入
    一个重依赖更可控，中文字体也能显式指定。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

# DSH 自带的 LibreOffice Kit（随应用分发，最稳）。
# 不要去找 PATH 里的 soffice，也不要用系统装的 LibreOffice。
# 本机默认路径写在这里，但允许用环境变量覆盖 —— 换机器/换用户名时
# 只要设这两个变量，不用改代码。
LO_NODE = os.environ.get("AF_LO_NODE") or (
    r"C:\Users\谢安豪\AppData\Local\Programs\DeepSeek Harness\resources"
    r"\runtime\primary-runtime\dependencies\node\bin\node.exe")
LO_CLI = os.environ.get("AF_LO_CLI") or (
    r"C:\Users\谢安豪\AppData\Local\Programs\DeepSeek Harness\resources"
    r"\app.asar.unpacked\dsh\node_modules\@deepseek-ai\libreoffice-kit\lib\cli.js")

# 中文字体：必须同时设 ascii 和 eastAsia，
# 只设 run.font.name 的话中文会回退成默认字体（python-docx 的已知坑）。
LATIN_FONT = "Segoe UI"
CJK_FONT = "微软雅黑"
MONO_FONT = "Consolas"


# ------------------------------------------------------------------ 行内解析

INLINE_RE = re.compile(
    r"(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\*[^*]+\*)")


def add_runs(paragraph, text, base_bold=False):
    """把 **粗体** `代码` [链接](url) *斜体* 渲染成 runs。"""
    from docx.shared import Pt, RGBColor

    for part in INLINE_RE.split(text):
        if not part:
            continue
        bold = base_bold
        italic = False
        mono = False
        color = None
        body = part
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            body, bold = part[2:-2], True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            body, mono = part[1:-1], True
        elif part.startswith("[") and "](" in part:
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part)
            if m:
                body, color = m.group(1), RGBColor(0x05, 0x63, 0xC1)
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            body, italic = part[1:-1], True

        run = paragraph.add_run(body.replace("\\|", "|"))
        run.bold = bold
        run.italic = italic
        if mono:
            run.font.name = MONO_FONT
            run.font.size = Pt(9.5)
        else:
            run.font.name = LATIN_FONT
            rpr = run._element.get_or_add_rPr()
            rf = rpr.find(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rFonts")
            if rf is None:
                from docx.oxml.ns import qn
                rf = rpr.makeelement(qn("w:rFonts"), {})
                rpr.append(rf)
            from docx.oxml.ns import qn
            rf.set(qn("w:eastAsia"), CJK_FONT)
        if color is not None:
            run.font.color.rgb = color


def md_to_docx(md_path: Path, docx_path: Path):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    md_path = Path(md_path)
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    doc = Document()
    # 全局默认字体
    style = doc.styles["Normal"]
    style.font.name = LATIN_FONT
    style.font.size = Pt(10.5)
    from docx.oxml.ns import qn
    style.element.rPr.rFonts.set(qn("w:eastAsia"), CJK_FONT)

    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Pt(54)
        sec.top_margin = sec.bottom_margin = Pt(54)

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 分隔线
        if re.fullmatch(r"-{3,}", stripped):
            i += 1
            continue

        # 表格
        if stripped.startswith("|") and i + 1 < n and re.match(
                r"^\|[\s:\-|]+\|$", lines[i + 1].strip()):
            block = []
            while i < n and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            _add_table(doc, block)
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            if level == 1:
                p = doc.add_heading("", level=0)
                add_runs(p, m.group(2))
            else:
                p = doc.add_heading("", level=min(level - 1, 4))
                add_runs(p, m.group(2))
                for r in p.runs:
                    r.font.color.rgb = RGBColor(0x1F, 0x38, 0x64)
            i += 1
            continue

        # 引用块
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            p.paragraph_format.space_after = Pt(4)
            add_runs(p, " ".join(x for x in buf if x))
            for r in p.runs:
                r.font.size = Pt(9.5)
                r.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
            continue

        # 无序列表
        m = re.match(r"^([-*+])\s+(.*)$", stripped)
        if m:
            p = doc.add_paragraph(style="List Bullet")
            add_runs(p, m.group(2))
            p.paragraph_format.space_after = Pt(2)
            i += 1
            continue

        # 有序列表
        m = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if m:
            p = doc.add_paragraph(style="List Number")
            add_runs(p, m.group(2))
            p.paragraph_format.space_after = Pt(2)
            i += 1
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        # 普通段落
        p = doc.add_paragraph()
        add_runs(p, stripped)
        p.paragraph_format.space_after = Pt(5)
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        i += 1

    docx_path = Path(docx_path)
    docx_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(docx_path))
    return docx_path


def _add_table(doc, block):
    from docx.oxml.ns import qn
    from docx.shared import Pt

    rows = []
    for raw in block:
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        rows.append(cells)
    if len(rows) < 2:
        return
    header, body = rows[0], rows[2:]     # rows[1] 是 |---|---| 分隔行
    ncols = len(header)
    table = doc.add_table(rows=1, cols=ncols)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0]
    for j, htext in enumerate(header):
        cell = hdr.cells[j]
        # 必须先把空段落彻底清掉：直接往 cell.text="" 的段落里 add_run，
        # 前面会残留一个空 run，标题就看不见了（实测踩过这个坑）。
        cell.text = ""
        para = cell.paragraphs[0]
        for r in list(para.runs):
            r._element.getparent().remove(r._element)
        add_runs(para, htext, base_bold=True)
    # 表头重复出现在每一页
    trPr = hdr._tr.get_or_add_trPr()
    tblHeader = trPr.makeelement(qn("w:tblHeader"), {})
    trPr.append(tblHeader)

    for row in body:
        cells = table.add_row().cells
        for j in range(ncols):
            val = row[j] if j < len(row) else ""
            cells[j].text = ""
            para = cells[j].paragraphs[0]
            for r in list(para.runs):
                r._element.getparent().remove(r._element)
            add_runs(para, val)
            for r in para.runs:
                r.font.size = Pt(8.5)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def docx_to_pdf(docx_path: Path, out_dir: Path | None = None):
    """用 DSH 自带的 LibreOffice Kit 转 PDF。"""
    node, cli = Path(LO_NODE), Path(LO_CLI)
    if not node.exists() or not cli.exists():
        raise RuntimeError(
            f"找不到 DSH 自带的 LibreOffice Kit：\n  node={node}\n  cli={cli}\n"
            "不要去找系统装的 soffice —— 报告说清失败原因即可。")
    out_dir = Path(out_dir or docx_path.parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / (docx_path.stem + ".pdf")
    # LibreOffice Kit 的 convert 不会覆盖已存在的文件：它直接报
    # "EEXIST: file already exists" 并以 exit=1 失败。
    # 日报每天会重跑同名文件，所以这里必须先删掉旧产物。
    if pdf.exists():
        pdf.unlink()
    cmd = [str(node), str(cli), "convert",
           "--input", str(docx_path),
           "--output", str(pdf)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    if proc.returncode != 0 or not pdf.exists():
        raise RuntimeError(
            f"PDF 转换失败（exit={proc.returncode}）\n"
            f"stdout: {proc.stdout[-800:]}\nstderr: {proc.stderr[-800:]}")
    return pdf


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Markdown 报告 → Word / PDF")
    ap.add_argument("md", help="Markdown 报告路径")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认与 md 同目录）")
    ap.add_argument("--docx", action="store_true", help="生成 Word")
    ap.add_argument("--pdf", action="store_true", help="生成 PDF")
    ap.add_argument("--no-docx", action="store_true", help="不生成 Word")
    args = ap.parse_args()

    md_path = Path(args.md).resolve()
    if not md_path.exists():
        print(f"找不到文件：{md_path}")
        return 1
    out_dir = Path(args.out_dir).resolve() if args.out_dir else md_path.parent
    do_docx = args.docx or not (args.pdf and args.no_docx)
    do_docx = do_docx and not args.no_docx

    results = []
    docx_path = None
    if do_docx:
        docx_path = md_to_docx(md_path, out_dir / (md_path.stem + ".docx"))
        print(f"[docx] {docx_path}  ({docx_path.stat().st_size} bytes)")
        results.append(docx_path)
    if args.pdf:
        if docx_path is None:
            docx_path = md_to_docx(md_path, out_dir / (md_path.stem + ".docx"))
        pdf = docx_to_pdf(docx_path, out_dir)
        print(f"[pdf ] {pdf}  ({pdf.stat().st_size} bytes)")
        results.append(pdf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
