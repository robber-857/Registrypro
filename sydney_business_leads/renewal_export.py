"""Small dependency-free XLSX export with explicit date acceptance status."""
import io
import zipfile
from xml.sax.saxutils import escape

COLUMNS=[('name','商家名称'),('business_name','Business Name'),('business_name_renewal_date','Renewal date'),('asic_status','ASIC Status'),('abn','ABN'),('acn','ACN'),('match_status','确认状态'),('browser_last_result','本轮结果'),('auto_reason','地址与匹配说明'),('asic_observed_records','本轮观察记录'),('renewal_source','日期来源')]

def confirmed(row):
    return bool(row.get('business_name') and row.get('business_name_renewal_date') and row.get('asic_status')=='Registered' and row.get('match_status') in ('商号及续期日已确认','自动确认'))

def xlsx(rows):
    sheets=[('已确认续期日',[r for r in rows if confirmed(r)]),('注销候选及未完成',[r for r in rows if not confirmed(r)])]
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in (1,2))+'</Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,_) in enumerate(sheets,1))+'</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in (1,2))+'</Relationships>')
        for i,(_,items) in enumerate(sheets,1):
            values=[[label for _,label in COLUMNS]]+[[str(r.get(key,'') or '') for key,_ in COLUMNS] for r in items]
            xml=f'<worksheet xmlns="{ns}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="11" width="24" customWidth="1"/></cols><sheetData>'
            for number,values_row in enumerate(values,1):
                xml+=f'<row r="{number}">'+''.join(f'<c r="{chr(65+j)}{number}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>' for j,value in enumerate(values_row))+'</row>'
            z.writestr(f'xl/worksheets/sheet{i}.xml',xml+f'</sheetData><autoFilter ref="A1:K{len(values)}"/></worksheet>')
    return output.getvalue()
