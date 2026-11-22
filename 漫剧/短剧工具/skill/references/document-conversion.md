# 文档转文本

使用 scripts/convert.py。DOCX 依赖 python-docx，PDF 依赖 pypdf，DOC 依赖系统 antiword；缺依赖明确报错，不自动安装。

```bash
python3 <技能目录>/scripts/convert.py 原著.docx --output 原著.txt
```

DOCX 按正文顺序提取段落及表格单元格；PDF按页面保留换行，避免自动拼接改变文本。不承诺完整提取文本框、脚注或复杂阅读顺序。扫描 PDF 无文字时提示 OCR，不交付空白文本冒充成功。
默认不覆盖既有文件，确认重做时用 --overwrite。输出 UTF-8；转换后抽查章节边界及文字顺序，再交给 [novel-reader-story](novel-reading.md)。
