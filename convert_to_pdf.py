"""用 Word COM 将 docx 转为 PDF"""
import os
import sys
import win32com.client

def docx_to_pdf(input_path, output_path):
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    try:
        doc = word.Documents.Open(os.path.abspath(input_path))
        doc.SaveAs(os.path.abspath(output_path), FileFormat=17)  # 17 = PDF
        doc.Close()
        print(f"  ✅ {os.path.basename(input_path)} → {os.path.basename(output_path)}")
    finally:
        word.Quit()

base = r"C:\Users\0937\Documents\work\simple_rag\data"

conversions = [
    (os.path.join(base, "docx", "v1", "IT运维管理规范.docx"),
     os.path.join(base, "pdf", "v1", "IT运维管理规范.pdf")),
    (os.path.join(base, "docx", "v2", "IT运维管理规范.docx"),
     os.path.join(base, "pdf", "v2", "IT运维管理规范.pdf")),
]

for src, dst in conversions:
    print(f"Converting: {src}")
    docx_to_pdf(src, dst)

print("\nDone!")
