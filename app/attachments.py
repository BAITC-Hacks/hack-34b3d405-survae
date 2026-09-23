import base64
import io
import re
import zipfile
from pathlib import Path

MAX_BYTES=8*1024*1024
MAX_TEXT=16000


def tabular_items(rows):
    """Lossless two-column article/quantity tables; other layouts stay with AI."""
    rows = [[str(c).strip() if c is not None else "" for c in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return None
    items = []
    for index, row in enumerate(rows):
        while row and not row[-1]:
            row.pop()
        if index == 0 and len(row) == 2 and re.search(r"артикул|товар|наименование|название", row[0], re.I) and re.search(r"кол|qty|quantity", row[1], re.I):
            continue
        if not row or len(row) > 2 or not row[0]:
            return None
        quantity = row[1].replace(",", ".") if len(row) == 2 else ""
        numeric = re.fullmatch(r"[+-]?\d+(?:\.\d+)?", quantity)
        items.append({"query":row[0], "quantity":float(quantity) if numeric else None})
    return items or None


def extract(filename, body):
    rows = None
    if not body or len(body)>MAX_BYTES:
        raise ValueError("Файл должен быть непустым и не больше 8 МБ.")
    extension=Path(filename).suffix.lower()
    if extension in {".xlsx",".docx"}:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            if sum(i.file_size for i in archive.infolist())>30*1024*1024 or len(archive.infolist())>3000:
                raise ValueError("Слишком большой документ после распаковки.")
    if extension in {".jpg",".jpeg",".png"}:
        from PIL import Image
        image=Image.open(io.BytesIO(body))
        image.thumbnail((1600,1600))
        output=io.BytesIO(); image.convert("RGB").save(output,format="JPEG",quality=85)
        return {"name":filename,"image":"data:image/jpeg;base64,"+base64.b64encode(output.getvalue()).decode(),"text":""}
    if extension==".pdf":
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(body))
        if reader.is_encrypted or len(reader.pages)>30:
            raise ValueError("Поддерживаются незашифрованные PDF до 30 страниц.")
        text="\n".join(p.extract_text() or "" for p in reader.pages)
        if not text.strip():
            raise ValueError("В PDF нет текстового слоя. Прикрепите нужную страницу как JPEG.")
    elif extension==".docx":
        from docx import Document
        doc=Document(io.BytesIO(body))
        text="\n".join([p.text for p in doc.paragraphs]+[" | ".join(c.text for c in row.cells) for t in doc.tables for row in t.rows])
    elif extension==".xlsx":
        from openpyxl import load_workbook
        workbook=load_workbook(io.BytesIO(body),read_only=True,data_only=True)
        try:
            if len(workbook.worksheets) > 5:
                raise ValueError("Поддерживаются таблицы до 5 листов; разделите документ.")
            rows = []
            for sheet in workbook:
                if (sheet.max_row or 0) > 150 or (sheet.max_column or 0) > 16:
                    raise ValueError("Поддерживаются листы до 150 строк и 16 столбцов; разделите документ.")
                rows.extend(sheet.iter_rows(max_row=150, max_col=16, values_only=True))
            text="\n".join(" | ".join(str(c) if c is not None else "" for c in row).rstrip(" |") for row in rows if any(c is not None for c in row))
        finally:
            workbook.close()
    elif extension==".xls":
        import xlrd
        workbook=xlrd.open_workbook(file_contents=body)
        if workbook.nsheets > 5 or any(sheet.nrows > 150 or sheet.ncols > 16 for sheet in workbook.sheets()):
            raise ValueError("Поддерживаются таблицы до 5 листов, 150 строк и 16 столбцов; разделите документ.")
        rows = [sheet.row_values(i) for sheet in workbook.sheets() for i in range(sheet.nrows)]
        text="\n".join(" | ".join(str(c) for c in row) for row in rows)
    elif extension in {".txt",".csv"}:
        text=body.decode("utf-8-sig")
    else:
        raise ValueError("Поддерживаются PDF, Word (.docx), Excel (.xlsx/.xls), JPEG, PNG, TXT и CSV.")
    if not text.strip():
        raise ValueError("В документе не найден текст. Прикрепите заполненный файл.")
    if len(text) > MAX_TEXT:
        raise ValueError("Документ содержит больше 16 000 символов. Разделите его: строки не будут обрезаться автоматически.")
    result = {"name":filename,"text":text,"image":None}
    if rows is not None:
        result["structured_items"] = tabular_items(rows)
    return result
