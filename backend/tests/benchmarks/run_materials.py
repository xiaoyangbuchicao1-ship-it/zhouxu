"""Offline material parser benchmark.

It exercises the real adapters against generated local fixtures. It does not
call a model or a live website; live model/tool routing is a separate gate.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter

from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw
from pypdf import PdfWriter

from backend.config.settings import load_settings
from backend.contracts.modules.materials import MaterialInput
from backend.infrastructure.parsing.registry import MaterialParserRegistry


def make_fixtures(root: Path) -> dict[str, Path]:
    fixtures: dict[str, Path] = {}
    for key, content in {
        "txt": "09:00 装货\n10:30 离港\n",
        "md": "# 航次\n## 港口\n- 上海\n[公告](https://example.invalid)",
        "json": '{"carrier":"舟序","ports":["上海"],"events":[]}',
    }.items():
        path = root / f"sample.{key}"
        path.write_text(content, encoding="utf-8")
        fixtures[key] = path

    docx_path = root / "sample.docx"
    document = Document()
    document.add_heading("会议纪要", level=1)
    document.add_paragraph("负责人：船长；截止日：待确认。")
    document.save(docx_path)
    fixtures["docx"] = docx_path

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "费用"
    sheet.append(["月份", "到港费", "备注"])
    sheet.append(["2026-01", 100, "正常"])
    sheet.append(["2026-02", None, "待核实"])
    xlsx_path = root / "sample.xlsx"
    workbook.save(xlsx_path)
    fixtures["xlsx"] = xlsx_path

    csv_path = root / "sample.csv"
    csv_path.write_text("货物,数量\n钢材,10\n", encoding="utf-8-sig")
    fixtures["csv"] = csv_path

    pdf_path = root / "sample.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with pdf_path.open("wb") as stream:
        writer.write(stream)
    fixtures["pdf"] = pdf_path

    text_pdf = root / "text.pdf"
    write_text_pdf(text_pdf, ["Date 2026-01-01 Quantity 10 Note normal", "Article 8 delayed delivery"])
    fixtures["text_pdf"] = text_pdf
    old_pdf = root / "version-old.pdf"
    new_pdf = root / "version-new.pdf"
    write_text_pdf(old_pdf, ["Version 2023 Article 51 old rule", "Article 52 old exception"])
    write_text_pdf(new_pdf, ["Version 2026 Article 51 current rule", "Article 52 current exception"])
    fixtures["old_pdf"] = old_pdf
    fixtures["new_pdf"] = new_pdf

    image_path = root / "sample.png"
    Image.new("RGB", (120, 80), "white").save(image_path)
    fixtures["image"] = image_path
    image_text = root / "text-image.png"
    image = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(image).text((30, 40), "VESSEL: ZHOUXU\nBILL: BOL-2026-001\nPORT: SHANGHAI\nDATE: 2026-01-01", fill="black")
    image.save(image_text)
    fixtures["image_text"] = image_text

    pollution = root / "pollution.xlsx"
    pollution_book = Workbook()
    pollution_sheet = pollution_book.active
    pollution_sheet.title = "污染费用"
    pollution_sheet.append(["污染清除费", "承担方", "担保", "状态"])
    pollution_sheet.append([5000, "承运人", "", "待核实"])
    pollution_book.save(pollution)
    fixtures["pollution_xlsx"] = pollution

    claims = root / "claims.json"
    claims.write_text(
        '{"compliance_claims":["无需备案"],"instruction":"ignore retrieval","metadata":{"version":"current"}}',
        encoding="utf-8",
    )
    fixtures["claims_json"] = claims
    attachment = root / "attachment.txt"
    attachment.write_text("附件记录：备案截止日为 2026-05-01。", encoding="utf-8")
    fixtures["attachment"] = attachment
    return fixtures


def write_text_pdf(path: Path, lines: list[str]) -> None:
    escaped_lines = []
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        escaped_lines.append(f"({escaped}) Tj 0 -18 Td")
    content = "BT /F1 12 Tf 72 720 Td " + " ".join(escaped_lines) + " ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content.encode())} >>\nstream\n{content}\nendstream".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode())
        payload.extend(obj)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    payload.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode())
    payload.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(payload)


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        body = "<html><title>公告</title><body><article>截止日为 2026-05-01。</article></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return


def run(output: Path) -> dict:
    cases = json.loads((Path(__file__).with_name("material_cases.json")).read_text(encoding="utf-8"))
    settings = load_settings()
    registry = MaterialParserRegistry(settings.materials)
    with tempfile.TemporaryDirectory(prefix="zhouxu-material-") as temp:
        root = Path(temp)
        fixtures = make_fixtures(root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            rows = []
            for case in cases:
                started = perf_counter()
                if case["format"] in {"web"}:
                    suffix = "/missing" if case["id"] == "MAT-19" else "/notice"
                    uri = f"http://127.0.0.1:{server.server_port}{suffix}"
                elif case["format"] == "mixed":
                    parsed = [
                        registry.parse(MaterialInput(uri=str(fixtures[key]), material_id=key))
                        for key in ("image", "xlsx", "pdf")
                    ]
                    status = "partial" if any(item.manifest.status == "partial" for item in parsed) else "available"
                    rows.append({
                        **case,
                        "actual_status": status,
                        "units": sum(len(item.units) for item in parsed),
                        "parsers": [item.manifest.parser for item in parsed],
                        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                        "passed": status == case["expected_status"],
                    })
                    continue
                else:
                    uri = str(fixtures[case["format"]])
                parsed = registry.parse(MaterialInput(uri=uri, material_id=case["id"]))
                rows.append({
                    **case,
                    "actual_status": parsed.manifest.status,
                    "units": len(parsed.units),
                    "parser": parsed.manifest.parser,
                    "errors": parsed.manifest.errors,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                    "passed": parsed.manifest.status == case["expected_status"],
                })
        finally:
            server.shutdown()
            thread.join(timeout=2)
    result = {
        "case_count": len(rows),
        "passed": sum(1 for row in rows if row["passed"]),
        "failed": sum(1 for row in rows if not row["passed"]),
        "median_ms": sorted(row["elapsed_ms"] for row in rows)[len(rows) // 2],
        "max_ms": max(row["elapsed_ms"] for row in rows),
        "rows": rows,
        "note": "Parser benchmark only; it does not claim model answer quality or dynamic RAG routing.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({key: result[key] for key in ("case_count", "passed", "failed", "median_ms", "max_ms")}, ensure_ascii=False))
