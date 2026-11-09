"""Remove translated text without deleting neighbouring native math/table glyphs."""
from collections import Counter

def remove_text(page,items,source,api):
    f=api.fitz;state=f.TOOLS.set_small_glyph_heights()
    own={key for item in items for key in item['source_ids']}
    def extract():
        return [(f'p{source["page"]}-b{bi}-l{li}-s{si}',span['font'],c) for bi,b in enumerate(page.get_text('rawdict')['blocks']) if b['type']==0 for li,l in enumerate(b['lines']) for si,span in enumerate(l['spans']) for c in span['chars'] if c['c'].strip()]
    sig=lambda font,c:(font,c['c'],tuple(round(v,3) for v in c['origin']))
    try:
        # Source IDs were prepared using compact boxes. Full boxes can change
        # MuPDF's block grouping; match full bounds by glyph identity, not IDs.
        f.TOOLS.set_small_glyph_heights(True)
        compact=extract()
        f.TOOLS.set_small_glyph_heights(False)
        full={sig(font,c):c['bbox'] for _,font,c in extract()}
        before=[(key,font,{**c,'bbox':full[sig(font,c)]}) for key,font,c in compact]
        kept=[v for v in before if v[0] not in own];changed=[v for v in before if v[0] in own]
        wanted=Counter(sig(font,c) for _,font,c in changed);protected=Counter(sig(font,c) for _,font,c in kept)
        if wanted.keys()&protected.keys():raise ValueError('待替换文字与原生保留字形重合，需专门处理。')
        index=api.RectIndex((n,c['bbox']) for n,(_,_,c) in enumerate(kept))
        for item in items:
            ids=set(item['source_ids']);box=api.union(item['source_spans'])
            if not any(api.intersection(box,b)>0 for _,b in index.candidates(box)):
                page.add_redact_annot(box,fill=False,cross_out=False);continue
            for key,font,c in changed:
                if key not in ids:continue
                box=f.Rect(c['bbox']);chosen=None
                for fy in [.5,.2,.8,.05,.95]:
                    for fx in [.5,.2,.8,.05,.95]:
                        x,y=box.x0+box.width*fx,box.y0+box.height*fy;probe=f.Rect(x-.025,y-.025,x+.025,y+.025)
                        if not any(api.intersection(probe,b)>0 for _,b in index.candidates(probe)):
                            chosen=probe;break
                    if chosen is not None:break
                if chosen is None:raise ValueError('正文无法与原生公式/表格安全分离，停止生成。')
                page.add_redact_annot(chosen,fill=False,cross_out=False)
        if items:page.apply_redactions(images=0,graphics=0,text=0)
        after=Counter(sig(font,c) for _,font,c in extract())
        def near(a,b):
            return a[:2]==b[:2] and max(abs(a[2][k]-b[2][k]) for k in [0,1])<.005
        # Rewriting PDF text can round origins by <.001pt. Match with a tiny
        # coordinate tolerance while retaining multiplicity, font and Unicode.
        remaining=after.copy()
        for key,count in protected.items():
            for _ in range(count):
                match=next((k for k,n in remaining.items() if n and near(key,k)),None)
                if match is None:raise ValueError('正文替换误删原生保留字形，停止生成。')
                remaining[match]-=1
        if any(near(a,b) for a in wanted for b,n in remaining.items() if n):raise ValueError('原文未清除完整，停止生成以免中英叠加。')
    finally:f.TOOLS.set_small_glyph_heights(state)
