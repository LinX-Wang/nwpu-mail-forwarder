"""Build a local gallery and provenance-preserving archive of downloaded NWPU assets."""
from pathlib import Path
import hashlib
import html
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'resources' / 'nwpu-official'
references = json.loads((ASSETS / 'reference-manifest.json').read_text(encoding='utf-8'))
originals = [
    dict(file='originals/20180109.zip', category='standard-identity', format='zip', kind='official-source-archive', source_page='https://news.nwpu.edu.cn/xcb/index/xxsbxt.htm', source_url='https://news.nwpu.edu.cn/20180109.zip'),
    dict(file='originals/new-vi.zip', category='new-vi', format='zip', kind='official-source-archive', source_page='https://news.nwpu.edu.cn/info/1210/74474.htm', source_url='https://news.nwpu.edu.cn/system/_content/download.jsp?urltype=news.DownloadAttachUrl&owner=867808034&wbfileid=6BB69DC86837DAF0F909E6343B47EEE4'),
    dict(file='originals/standard-identity.cdr', category='standard-identity', format='cdr', kind='official-source-extracted', extracted_from='originals/20180109.zip'),
    dict(file='originals/new-vi-source.cdr', category='new-vi', format='cdr', kind='official-source-extracted', extracted_from='originals/new-vi.zip'),
]
for entry in originals + references:
    content = (ASSETS / entry['file']).read_bytes()
    entry.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), downloaded_on='2026-11-16')
(ASSETS / 'manifest.json').write_text(json.dumps(dict(collection='NWPU official source collection', license='No open-source redistribution license verified; university branding excluded from project MIT license.', assets=originals + references), ensure_ascii=False, indent=2), encoding='utf-8')
names = {'new-vi': '新版 VI', 'emblem': '校徽', 'spirit': '校风', 'motto': '校训', 'flag': '校旗规范'}
cards = []
for e in references:
    label = f"{names[e['category']]} · {Path(e['file']).stem.split('-')[-1]}"
    cards.append(f'<article><a href="{html.escape(e["file"])}"><img loading="eager" src="{html.escape(e["file"])}" alt="{label}"></a><div><b>{label}</b><span>{e["format"].upper()} · {e["width"]} × {e["height"]}</span></div><a class="source" href="{html.escape(e["source_page"])}" rel="noopener noreferrer">官方来源 ↗</a></article>')
page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NWPU 官方视觉素材库</title><style>
*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:#14212e;font:15px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}main{max-width:1600px;margin:auto;padding:40px}header{margin-bottom:28px}h1{font-size:30px;margin:4px 0 8px}p{margin:8px 0;color:#546578}.eyebrow{font-size:12px;letter-spacing:2px;color:#197d78}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px}article{background:white;border:1px solid #dde5ec;border-radius:12px;overflow:hidden}article img{width:100%;height:190px;object-fit:contain;display:block;padding:8px;background:white}article div{padding:14px 16px 0}b{display:block;font-size:15px}span{font-size:13px;color:#64748b}.source{display:block;padding:6px 16px 14px;color:#187c76;text-decoration:none;font-size:13px}.note{border-left:3px solid #197d78;padding:10px 16px;background:#edf5f4;margin:20px 0}.downloads{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}.downloads a{padding:10px 16px;background:white;border:1px solid #d5e0e7;border-radius:8px;color:#14212e;text-decoration:none}footer{margin-top:28px;color:#64748b;font-size:13px}@media(max-width:1000px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:700px){main{padding:20px}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}article img{height:140px}}@media(max-width:400px){.grid{grid-template-columns:1fr}}
</style><main><header><div class="eyebrow">NWPU / VISUAL SOURCE LIBRARY</div><h1>西工大官方视觉素材库</h1><p>2 套官方 CDR 源文件 · 31 张官方示例与规范图片 · 保留原始文件及完整出处</p><div class="note">CDR 是可编辑设计源文件；下方预览均为学校发布的位图。建筑剪影尚未找到可核实的官方矢量原稿。公开下载不等于获得开源再分发授权。</div><div class="downloads"><a href="originals/standard-identity.cdr">校标 / 校名标准字 CDR</a><a href="originals/new-vi-source.cdr">新版 VI 源文件 CDR</a><a href="README.md">使用说明</a><a href="manifest.json">来源与校验清单</a></div></header><section class="grid">''' + ''.join(cards) + '</section><footer>仅作素材整理与设计参考，不代表西北工业大学官方产品。学校品牌权利不包含在邮件工具的 MIT 许可中。</footer></main></html>'
(ASSETS / 'gallery.html').write_text(page, encoding='utf-8')
destination = ROOT / 'dist' / 'nwpu-official-visual-assets-20261116.zip'
with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for f in sorted(ASSETS.rglob('*')):
        if f.is_file():
            archive.write(f, Path('nwpu-official') / f.relative_to(ASSETS))
print(destination)
print(f'{len(originals)} source files including archives; {len(references)} official raster references')
print(hashlib.sha256(destination.read_bytes()).hexdigest())
