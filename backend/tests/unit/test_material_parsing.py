from __future__ import annotations

import csv
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

pytest.importorskip("docx")
pytest.importorskip("openpyxl")
pytest.importorskip("PIL")
pytest.importorskip("pypdf")

from docx import Document
from openpyxl import Workbook
from PIL import Image
from pypdf import PdfWriter

from backend.config.settings import load_settings
from backend.contracts.modules.materials import MaterialInput, MaterialReadInput
from backend.infrastructure.parsing.catalog import MaterialCatalog
from backend.infrastructure.parsing.registry import MaterialParserRegistry


def test_docx_without_optional_numbering_keeps_body(tmp_path):
    path = tmp_path / "plain.docx"
    original = tmp_path / "numbered-package.docx"
    document = Document()
    document.add_paragraph("第一项：参考答案 A")
    document.add_paragraph("第二项：参考答案 B")
    document.save(original)
    # Some valid producers omit the optional numbering part altogether.
    with ZipFile(original) as source, ZipFile(path, "w", ZIP_DEFLATED) as target:
        for name in source.namelist():
            if name == "word/numbering.xml":
                continue
            body = source.read(name)
            if name == "word/_rels/document.xml.rels":
                root = ET.fromstring(body)
                for item in list(root):
                    if item.get("Type", "").endswith("/numbering"):
                        root.remove(item)
                body = ET.tostring(root, encoding="utf-8")
            elif name == "[Content_Types].xml":
                root = ET.fromstring(body)
                for item in list(root):
                    if item.get("PartName") == "/word/numbering.xml":
                        root.remove(item)
                body = ET.tostring(root, encoding="utf-8")
            target.writestr(name, body)
    settings = load_settings().materials.model_copy(update={"cache_root": str(tmp_path / "cache")})
    parsed = MaterialParserRegistry(settings).parse(MaterialInput(uri=str(path), material_id="plain"))
    assert parsed.manifest.status == "available"
    assert [u.text for u in parsed.units] == ["第一项：参考答案 A", "第二项：参考答案 B"]


def make_samples(tmp_path: Path) -> dict[str, Path]:
    text = tmp_path / "timeline.txt"
    text.write_text("09:00 装货\n10:30 离港\n", encoding="utf-8")

    docx = tmp_path / "contract.docx"
    document = Document()
    document.add_heading("租船合同", level=1)
    document.add_paragraph("第8条 延迟交付由承租人承担。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "负责人"
    table.cell(0, 1).text = "船长"
    table.cell(1, 0).text = "截止日"
    table.cell(1, 1).text = ""
    document.save(docx)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "费用"
    sheet.append(["月份", "到港费", "备注"])
    sheet.append(["2026-01", 100, "正常"])
    sheet.append(["2026-02", None, "待核实"])
    xlsx = tmp_path / "fees.xlsx"
    workbook.save(xlsx)

    csv_path = tmp_path / "cargo.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream).writerows([["货物", "数量"], ["钢材", "10"]])

    pdf = tmp_path / "report.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    page.insert_text if False else None  # blank page exercises partial status
    with pdf.open("wb") as stream:
        writer.write(stream)

    image = tmp_path / "ship.png"
    Image.new("RGB", (120, 80), "white").save(image)
    return {"text": text, "docx": docx, "xlsx": xlsx, "csv": csv_path, "pdf": pdf, "image": image}


def test_registry_parses_common_local_formats(tmp_path):
    samples = make_samples(tmp_path)
    registry = MaterialParserRegistry(load_settings().materials)
    parsed = {
        name: registry.parse(MaterialInput(uri=str(path), material_id=name))
        for name, path in samples.items()
    }

    assert parsed["text"].manifest.status == "available"
    assert parsed["docx"].manifest.status == "available"
    assert parsed["xlsx"].manifest.status == "available"
    assert parsed["csv"].manifest.status == "available"
    assert parsed["pdf"].manifest.status == "partial"
    assert parsed["image"].manifest.status == "partial"
    assert any("第8条" in unit.text for unit in parsed["docx"].units)
    assert any("到港费" in unit.text for unit in parsed["xlsx"].units)


async def test_catalog_reads_a_specific_unit_and_keeps_provenance(tmp_path):
    sample = make_samples(tmp_path)["docx"]
    settings = load_settings()
    catalog = MaterialCatalog(settings)
    candidates, required = await catalog.ingest(
        [MaterialInput(uri=str(sample), material_id="contract")], run_id="read-run"
    )
    # The complete short text is available as a context candidate; a reader
    # remains available for source inspection without guessing a unit ID.
    assert required == []
    unit = catalog.read(
        MaterialReadInput(
            material_id="contract",
            unit_id=candidates[0].body["units"][1]["unit_id"],
        ), run_id="read-run", limit=2000,
    ).units[0]
    assert candidates[0].body["material_id"] == "contract"
    assert "uri" not in candidates[0].body
    assert unit.locator.value == "2"
    assert "延迟交付" in unit.text


async def test_image_is_only_inlined_when_read_on_demand(tmp_path):
    image = make_samples(tmp_path)["image"]
    catalog = MaterialCatalog(load_settings())
    candidates, _ = await catalog.ingest([MaterialInput(uri=str(image), material_id="photo")], run_id="image-run")
    assert "image_data_url" not in candidates[0].body
    denied = catalog.read(
        MaterialReadInput(material_id="photo", include_image=True),
        run_id="image-run", limit=2000, image_allowed=False,
    )
    assert denied.image_data_url is None
    assert denied.notices
    result = catalog.read(
        MaterialReadInput(
            material_id="photo",
            include_image=True,
        ), run_id="image-run", limit=2000, image_allowed=True,
    )
    assert result.image_data_url is not None
    assert result.image_data_url.startswith("data:image/png;base64,")


async def test_full_candidate_and_bounded_continuous_read_use_versioned_cursor(tmp_path):
    path = tmp_path / "long.docx"
    document = Document()
    for index in range(3):
        document.add_paragraph(f"延迟交付条款 {index}")
    document.save(path)

    settings = load_settings()
    settings.materials.overview_units = 1
    catalog = MaterialCatalog(settings)
    candidates, required = await catalog.ingest(
        [MaterialInput(uri=str(path), material_id="long")], run_id="paging"
    )

    assert required == []
    manifest = candidates[0].body
    assert "uri" not in manifest
    assert len(candidates[0].body["units"]) == 3
    result = catalog.read(MaterialReadInput(material_id="long"), run_id="paging", limit=5)
    assert len(result.units) == 1
    assert "延迟交付" in result.units[0].text
    assert result.next_cursor
    assert "uri" not in result.manifest.model_dump(mode="json")
    page = catalog.read(
        MaterialReadInput(
            material_id="long",
            cursor=result.next_cursor,
        ), run_id="paging", limit=10,
    )
    assert page.units and page.coverage[0]["char_start"] == result.coverage[-1]["char_end"]


def test_web_parser_reads_a_local_http_page(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = "<html><head><title>公告</title></head><body><nav>导航</nav><article>截止日为 2026-05-01。</article></body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        parsed = MaterialParserRegistry(load_settings().materials).parse(
            MaterialInput(uri=f"http://127.0.0.1:{server.server_port}/notice")
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert parsed.manifest.status == "available"
    assert "2026-05-01" in parsed.units[0].text
