import fitz
from pathlib import Path
p=Path(r'C:\Users\13548\Desktop\NoviceTeacher-AI\output\pedagoloop_qa')
doc=fitz.open(p/'preview.pdf')
print('Pages:', len(doc))
for i,page in enumerate(doc):
 page.get_pixmap(matrix=fitz.Matrix(1.4,1.4)).save(p/f'page-{i+1}.png')
 print(i+1, page.get_text()[:100], '...',page.get_text()[-100:])
