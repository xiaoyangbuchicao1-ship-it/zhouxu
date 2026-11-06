"""Owned document templates for maritime evidence work; no invented case facts."""
import math
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

FONT = 'Noto Sans CJK SC'
NAVY = '20364B'
BLUE = '456B86'
INK = '202B38'
MUTED = '66758A'
PALE = 'F4F6F8'
PALE_BLUE = 'EAF1F6'
PALE_AMBER = 'FFF4DF'
PALE_RED = 'FCEBE7'
LINE = 'D5DDE5'
TIMELINE = ['时间','事件','来源','原文位置','确定程度']
GAPS = ['待证事实','已有证据','矛盾或缺口','补证动作']


def _rows(rows,columns):
    if not isinstance(rows,list) or not rows:
        raise ValueError('需要实际内容行，不用空表代替成果')
    for row in rows:
        if not isinstance(row,dict) or set(row)!=set(columns) or any(not isinstance(row[c],str) or not row[c].strip() for c in columns):
            raise ValueError('每行需要完整字段：'+ '、'.join(columns)+'；未知明确填待核')
    return rows


def _status_fill(value):
    value = value or ''
    if '未知' in value or '待核' in value:
        return PALE_RED, '9A4A3A'
    if '单方' in value or '声称' in value:
        return PALE_AMBER, '8C622E'
    return PALE_BLUE, NAVY


def _sheet(wb, title, columns, rows, widths, source, scope):
    ws = wb.create_sheet(title)
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 90
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.sheet_properties.tabColor = NAVY if title == '证据时间线' else BLUE
    ws.sheet_properties.pageSetUpPr.autoPageBreaks = False
    ws.column_dimensions['A'].width = 2.5
    ws.row_dimensions[1].height = 8
    ws.merge_cells(start_row=2, start_column=2, end_row=2, end_column=1 + len(columns))
    title_cell = ws.cell(2, 2, title)
    title_cell.font = Font(name=FONT, size=16, bold=True, color=INK)
    title_cell.alignment = Alignment(vertical='center')
    title_cell.border = Border(bottom=Side(style='medium', color=NAVY))
    ws.row_dimensions[2].height = 28
    ws.merge_cells(start_row=3, start_column=2, end_row=3, end_column=1 + len(columns))
    source_cell = ws.cell(3, 2, source)
    source_cell.font = Font(name=FONT, size=9, italic=True, color=MUTED)
    source_cell.alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[3].height = 22
    if scope:
        ws.merge_cells(start_row=4, start_column=2, end_row=4, end_column=1 + len(columns))
        scope_cell = ws.cell(4, 2, scope)
        scope_cell.font = Font(name=FONT, size=9, color=MUTED)
        scope_cell.alignment = Alignment(vertical='center', wrap_text=True)
        ws.row_dimensions[4].height = 22
    else:
        ws.row_dimensions[4].height = 8
    header_row = 6
    for col, width in enumerate(widths, 2):
        letter = ws.cell(header_row, col).column_letter
        ws.column_dimensions[letter].width = width
        cell = ws.cell(header_row, col, columns[col - 2])
        cell.font = Font(name=FONT, size=10, bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor=NAVY)
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(
            top=Side(style='medium', color=NAVY),
            bottom=Side(style='medium', color=NAVY),
            right=Side(style='thin', color='FFFFFF'),
        )
    ws.row_dimensions[header_row].height = 30
    for number, row in enumerate(rows, header_row + 1):
        maxlines = 1
        for col, key in enumerate(columns, 2):
            text = row[key]
            cell = ws.cell(number, col, text)
            cell.font = Font(name=FONT, size=10, color=INK)
            cell.fill = PatternFill('solid', fgColor='FFFFFF' if number % 2 else PALE)
            cell.alignment = Alignment(
                horizontal='center' if key in ('时间', '确定程度') else 'left',
                vertical='center',
                wrap_text=True,
                indent=1,
            )
            cell.border = Border(bottom=Side(style='thin', color=LINE))
            weighted = sum(1.8 if ord(c) > 127 else 1 for c in text)
            lines = max(1, math.ceil(weighted / max(6, widths[col - 2] - 3)))
            maxlines = max(maxlines, lines)
            if key == '确定程度':
                fill, color = _status_fill(text)
                cell.fill = PatternFill('solid', fgColor=fill)
                cell.font = Font(name=FONT, size=10, bold=True, color=color)
        ws.row_dimensions[number].height = max(38, min(112, maxlines * 16 + 14))
    last = header_row + len(rows)
    ws.freeze_panes = f'D{header_row + 1}' if title == '证据时间线' else f'C{header_row + 1}'
    ws.auto_filter.ref = f'B{header_row}:{ws.cell(last, len(columns) + 1).column_letter}{last}'
    ws.print_options.horizontalCentered = True
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    # Keep each compact working sheet on one readable printed page.
    ws.page_setup.fitToHeight = 1
    ws.print_title_rows = f'1:{header_row}'
    ws.print_area = f'B2:{ws.cell(last, len(columns) + 1).column_letter}{last}'
    ws.page_margins.left = 0.3
    ws.page_margins.right = 0.3
    ws.page_margins.top = 0.4
    ws.page_margins.bottom = 0.45
    ws.oddHeader.left.text = '舟序  /  海事工作底稿'
    ws.oddHeader.right.text = title
    ws.oddHeader.left.size = 9
    ws.oddHeader.right.size = 9
    ws.oddFooter.left.text = '仅按材料整理  ·  待专业复核'
    ws.oddFooter.right.text = '第 &P 页 / 共 &N 页'
    ws.oddFooter.left.size = 9
    ws.oddFooter.right.size = 9
    return ws


def _font(style,size,bold=False):
    style.font.name = FONT
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor(0, 0, 0)
    rfonts = style.element.get_or_add_rPr().get_or_add_rFonts()
    rfonts.set(qn('w:ascii'), FONT)
    rfonts.set(qn('w:hAnsi'), FONT)
    rfonts.set(qn('w:eastAsia'), FONT)


def _table(doc, headers, rows, widths):
    table = doc.add_table(rows=1, cols=len(headers))
    table.autofit = False
    table.allow_autofit = False
    props = table._tbl.tblPr
    table_width = OxmlElement('w:tblW')
    table_width.set(qn('w:w'), str(int(sum(widths) * 1440)))
    table_width.set(qn('w:type'), 'dxa')
    props.append(table_width)
    layout = OxmlElement('w:tblLayout')
    layout.set(qn('w:type'), 'fixed')
    props.append(layout)
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        node = OxmlElement('w:' + edge)
        node.set(qn('w:val'), 'single')
        node.set(qn('w:sz'), '4')
        node.set(qn('w:color'), LINE)
        borders.append(node)
    props.append(borders)
    margins = OxmlElement('w:tblCellMar')
    for edge in ('top', 'left', 'bottom', 'right'):
        node = OxmlElement('w:' + edge)
        node.set(qn('w:w'), '105')
        node.set(qn('w:type'), 'dxa')
        margins.append(node)
    props.append(margins)
    repeat = OxmlElement('w:tblHeader')
    table.rows[0]._tr.get_or_add_trPr().append(repeat)
    for i, text in enumerate(headers):
        table.rows[0].cells[i].text = text
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = value
    for number, row in enumerate(table.rows):
        no_split = OxmlElement('w:cantSplit')
        row._tr.get_or_add_trPr().append(no_split)
        for i, cell in enumerate(row.cells):
            cell.width = Inches(widths[i])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            shading = OxmlElement('w:shd')
            shading.set(qn('w:fill'), NAVY if number == 0 else ('FFFFFF' if number % 2 else PALE))
            cell._tc.get_or_add_tcPr().append(shading)
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(3)
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.line_spacing = 1.15
                for run in p.runs:
                    _set_run_font(run, 9.5, bold=number == 0, color='FFFFFF' if number == 0 else INK)
    return table


def _set_run_font(run, size, bold=False, color=INK):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
    rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    rfonts.set(qn('w:ascii'), FONT)
    rfonts.set(qn('w:hAnsi'), FONT)
    rfonts.set(qn('w:eastAsia'), FONT)


def build_evidence_bundle(*,title,timeline,gaps,notes=None,scope='',output_dir='.'):
    """All facts/status/source locations must come from the caller's actual evidence."""
    _rows(timeline,TIMELINE);_rows(gaps,GAPS)
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
    names=list(dict.fromkeys(row['来源'] for row in timeline))
    source='来源：'+'；'.join(names)
    wb=Workbook();wb.remove(wb.active)
    _sheet(wb,'证据时间线',TIMELINE,timeline,[16,45,25,19,27],source,scope)
    _sheet(wb,'待证事实与缺口',GAPS,gaps,[30,32,36,44],source,scope)
    wb.properties.title = title
    wb.properties.subject = '海事证据整理工作底稿'
    wb.properties.creator = '舟序'
    xlsx=output/'证据时间线.xlsx';wb.save(xlsx)
    doc=Document();section=doc.sections[0]
    section.page_width=Inches(8.27);section.page_height=Inches(11.69)
    section.top_margin=Inches(.72);section.bottom_margin=Inches(.68)
    section.left_margin=Inches(.78);section.right_margin=Inches(.78)
    section.header_distance=Inches(.28);section.footer_distance=Inches(.3)
    _font(doc.styles['Normal'],10.5)
    doc.styles['Normal'].paragraph_format.line_spacing=1.28
    doc.styles['Normal'].paragraph_format.space_after=Pt(6)
    for key,size in [('Title',22),('Heading 1',14),('Heading 2',11)]:
        _font(doc.styles[key],size,True)
        doc.styles[key].paragraph_format.keep_with_next=True
        doc.styles[key].paragraph_format.space_before=Pt(15 if key!='Title' else 0)
        doc.styles[key].paragraph_format.space_after=Pt(7)
    section.header.paragraphs[0].text='舟序   /   海事证据整理'
    for run in section.header.paragraphs[0].runs:
        _set_run_font(run, 8.5, color=MUTED)
    title_para = doc.add_paragraph(title, style='Title')
    title_para.paragraph_format.space_after = Pt(4)
    p=doc.add_paragraph(scope or '证据整理工作底稿   ·   待专业复核')
    p.paragraph_format.space_after=Pt(12)
    for run in p.runs:
        _set_run_font(run, 10, color=MUTED)
    p=doc.add_paragraph(source)
    p.paragraph_format.space_after=Pt(5)
    for run in p.runs:
        _set_run_font(run, 9.5, color=MUTED)
    p=doc.add_paragraph('本稿只按授权材料整理。材料记载、单方陈述和未知信息分别保留，供核对原件、确认事实并安排后续取证。')
    p.paragraph_format.space_after=Pt(13)
    doc.add_heading('事件时间线',level=1)
    table_rows=[[r['时间'],r['事件'],r['原文位置'],r['确定程度']] for r in timeline]
    _table(doc,['时间','材料记载的事件','原文位置','证据状态'],table_rows,[.82,3.72,.9,1.34])
    doc.add_page_break()
    doc.add_heading('补证清单',level=1)
    p=doc.add_paragraph('按事实缺口组织下一步材料请求。每一项都保留现有依据和需要核对的边界。')
    p.paragraph_format.space_after=Pt(8)
    for number,row in enumerate(gaps,1):
        doc.add_heading(f'{number:02d}  {row["待证事实"]}',level=2)
        detail_rows = [('已有材料','已有证据'),('尚缺什么','矛盾或缺口'),('补证动作','补证动作')]
        for detail_index, (label,key) in enumerate(detail_rows):
            p=doc.add_paragraph()
            p.paragraph_format.left_indent=Inches(.18)
            p.paragraph_format.first_line_indent=Inches(-.18)
            p.paragraph_format.keep_with_next = detail_index < len(detail_rows) - 1
            lead = p.add_run(label+'  ')
            _set_run_font(lead, 10.2, bold=True, color=NAVY)
            body = p.add_run(row[key])
            _set_run_font(body, 10.2, color=INK)
    if notes:
        doc.add_heading('范围与待核事项',level=1)
        for note in notes:
            p = doc.add_paragraph(note)
            p.paragraph_format.left_indent = Inches(.18)
            p.paragraph_format.first_line_indent = Inches(-.18)
    footer=section.footer.paragraphs[0];footer.text='舟序   /   工作底稿'
    footer.paragraph_format.space_before=Pt(4)
    run=footer.add_run(' '*8+'第 ')
    field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)
    footer.add_run(' 页')
    for run in footer.runs:_set_run_font(run, 8.5, color=MUTED)
    docx=output/'补证清单.docx';doc.save(docx)
    return {'xlsx':str(xlsx),'docx':str(docx)}
