"""文件抽取纯函数测试：不碰数据库、不碰模型。"""

from __future__ import annotations

import pytest

from app.infra.fileparse import (
    FileParseError,
    UnsupportedFileType,
    extract_text,
    is_supported,
)


def test_is_supported():
    assert is_supported(".pdf")
    assert is_supported(".DOCX")
    assert not is_supported(".exe")
    assert not is_supported("")


def test_text_utf8(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("你好，世界。\n\n\n第二段。", encoding="utf-8")
    text = extract_text(p, ".txt")
    assert "你好" in text and "第二段" in text
    # 连续空行被压缩
    assert "\n\n\n" not in text


def test_text_gbk(tmp_path):
    p = tmp_path / "b.txt"
    p.write_bytes("中文编码测试：检索增强生成".encode("gbk"))
    assert "中文编码测试" in extract_text(p, ".txt")


def test_html_strips_tags_and_scripts(tmp_path):
    p = tmp_path / "c.html"
    p.write_text(
        "<html><head><style>.x{}</style><script>evil()</script></head>"
        "<body><h1>大标题</h1><p>正文&nbsp;内容</p></body></html>",
        encoding="utf-8",
    )
    text = extract_text(p, ".html")
    assert "大标题" in text and "正文" in text
    assert "<h1>" not in text
    assert "evil()" not in text


def test_csv_kept_as_text(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("姓名,年龄\n张三,20\n", encoding="utf-8")
    text = extract_text(p, ".csv")
    assert "张三" in text and "年龄" in text


def test_docx(tmp_path):
    docx = pytest.importorskip("docx")
    p = tmp_path / "e.docx"
    doc = docx.Document()
    doc.add_paragraph("第一段内容")
    doc.add_paragraph("第二段内容")
    doc.save(str(p))

    text = extract_text(p, ".docx")
    assert "第一段内容" in text and "第二段内容" in text


def test_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    p = tmp_path / "f.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(["列A", "列B"])
    ws.append(["甲", "乙"])
    wb.save(str(p))

    text = extract_text(p, ".xlsx")
    assert "数据" in text
    assert "列A" in text and "甲" in text


def test_pptx(tmp_path):
    pptx = pytest.importorskip("pptx")
    p = tmp_path / "g.pptx"
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # 标题+内容
    slide.shapes.title.text = "幻灯片标题"
    slide.placeholders[1].text = "要点一"
    prs.save(str(p))

    text = extract_text(p, ".pptx")
    assert "幻灯片标题" in text and "要点一" in text


def test_unsupported_raises(tmp_path):
    p = tmp_path / "h.exe"
    p.write_bytes(b"\x00\x01\x02")
    with pytest.raises(UnsupportedFileType):
        extract_text(p, ".exe")


def test_truncates_long_text(tmp_path):
    p = tmp_path / "i.txt"
    p.write_text("啊" * 2000, encoding="utf-8")
    text = extract_text(p, ".txt", max_chars=100)
    assert "截断" in text
    assert len(text) < 200


def test_missing_file(tmp_path):
    with pytest.raises(FileParseError):
        extract_text(tmp_path / "nope.txt", ".txt")
