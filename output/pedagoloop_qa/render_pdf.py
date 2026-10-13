import pypdfium2 as pdfium
from pathlib import Path
p=Path(r'C:\Users\13548\Desktop\NoviceTeacher-AI\output\pedagoloop_qa')
doc=pdfium.PdfDocument(str(p/'preview.pdf'))
print('Pages:', len(doc))
for i,page in enumerate(doc):
 page.render(scale=1.4).to_pil().save(p/f'page-{i+1}.png')
 print(i+1, page.get_textpage().get_text_range()[-120:])
