"""把简单 Markdown 转换为 docx。

支持：
- # 一级标题
- ## 二级标题
- ### 三级标题
- - 无序列表
- 普通段落
"""

import sys
from pathlib import Path

from docx import Document


def convert(md_path: Path, docx_path: Path) -> None:
    document = Document()
    lines = md_path.read_text(encoding="utf-8").splitlines()

    for line in lines:
        text = line.strip()
        if not text:
            continue

        if text.startswith("### "):
            document.add_heading(text[4:], level=2)
        elif text.startswith("## "):
            document.add_heading(text[3:], level=1)
        elif text.startswith("# "):
            document.add_heading(text[2:], level=0)
        elif text.startswith("- "):
            document.add_paragraph(text[2:], style="List Bullet")
        else:
            document.add_paragraph(text)

    document.save(docx_path)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python md_to_docx.py <input.md> <output.docx>")
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
    print("docx generated")