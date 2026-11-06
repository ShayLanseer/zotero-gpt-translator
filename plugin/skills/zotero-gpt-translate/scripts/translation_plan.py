"""Compact block editing: deterministic PDF mapping, translation stays with GPT."""
import json,re


def draft(work, api):
    work, manifest, sources, _ = api.read_work(work)
    catalog_path, translation_path = work/'segments.source.json', work/'segments.translation.json'
    if catalog_path.exists() or translation_path.exists():
        raise ValueError('分段稿已经存在；继续编辑 segments.translation.json，不覆盖已有译稿。')
    catalog={'source_sha256':manifest['source_sha256'],'pages':[]}
    translations={'pages':[]};readable=[]
    with api.fitz.open(work/'source.pdf') as doc:
        for source in sources:
            pno=source['page'];raw=doc[pno-1].get_text('rawdict')
            links=[json.loads(value) for value in source['links']]
            for link in links:
                link['from']=list(api.fitz.Rect(link['from'])*doc[pno-1].derotation_matrix)
            link_index=api.RectIndex((n,l['from']) for n,l in enumerate(links))
            page={'page':pno,'blocks':[]};target={'page':pno,'blocks':[]}
            readable.append(f'\n=== PAGE {pno} ===')
            for block in source['blocks']:
                key=block['spans'][0]['id'].split('-l')[0];bi=int(key.split('-b')[1]);original=raw['blocks'][bi]
                chars=[];text=''
                base=max(s['size'] for s in block['spans'])
                for li,line in enumerate(original['lines']):
                    if text:text+='\n';chars.append(None)
                    for si,span in enumerate(line['spans']):
                        st=''.join(c['c'] for c in span['chars'])
                        for c in span['chars']:
                            text+=c['c'];chars.append({**c,'font':span['font'],'size':span['size'],'flags':span['flags'],'span_text':st,'line':li})
                types=[None]*len(text)
                for pos,c in enumerate(chars):
                    if c is None or not c['c'].strip():continue
                    matches=[links[n] for n,r in link_index.candidates(c['bbox']) if api.intersection(r,c['bbox'])>api.fitz.Rect(c['bbox']).get_area()*.45]
                    if matches:
                        l=max(matches,key=lambda v:api.intersection(v['from'],c['bbox']))
                        kind='citation' if l.get('nameddest','').startswith('cite.') else 'hyperlink' if l['kind']==2 else 'crossref'
                        link={k:l[k] for k in ['kind','page','to','zoom','uri','nameddest','name','file'] if k in l}
                        types[pos]=('r',json.dumps(link,sort_keys=True),link,kind)
                    elif re.search(r'CMMI|CMSY|CMEX|MSAM|MSBM|BBOLD|Math|CMR',c['font'],re.I) and (not re.search('CMR',c['font'],re.I) or re.fullmatch(r'[\d\W]+',c['span_text'].strip())) or ((c['flags']&2) and re.fullmatch(r'[A-Za-z]',c['span_text'].strip())):
                        types[pos]=('m','math',None,None)
                # Include adjacent lower-case Roman subscripts in an identified
                # formula; do not classify ordinary smaller headings as math.
                for pos,c in enumerate(chars):
                    if c is None or types[pos] or not c['c'].strip():continue
                    if c['size']>=base*.82:continue
                    nearby=[j for j in range(max(0,pos-8),min(len(chars),pos+9)) if chars[j] and types[j] and types[j][0]=='m' and chars[j]['line']==c['line']]
                    if any(abs(c['bbox'][0]-chars[j]['bbox'][2])<12 or abs(chars[j]['bbox'][0]-c['bbox'][2])<12 for j in nearby):types[pos]=('m','math',None,None)
                for quote in re.finditer('“[^”]+”',text):
                    if not any(types[j] and types[j][0]=='r' for j in range(quote.start(),quote.end())):
                        for j in range(quote.start(),quote.end()):
                            if chars[j] and chars[j]['c'].strip():types[j]=('e','example',None,None)
                regions=[]
                for pos,kind in enumerate(types):
                    if not kind:continue
                    c=chars[pos];merge=False
                    if regions and regions[-1]['identity']==kind[:2] and re.fullmatch(r'\s*',text[regions[-1]['end']:pos]):
                        last=regions[-1];prev=last['chars'][-1]
                        merge=c['line']==prev['line'] or kind[0]=='r'
                        if not merge and kind[0]=='m':
                            whole=api.union([{'bbox':v['bbox']} for v in last['chars']]);cur=api.fitz.Rect(c['bbox'])
                            merge=min(whole.x1,cur.x1)>max(whole.x0,cur.x0) and abs(cur.y0-whole.y0)<max(c['size'],prev['size'])*1.5
                    if merge:regions[-1]['end']=pos+1;regions[-1]['chars'].append(c)
                    else:regions.append({'start':pos,'end':pos+1,'identity':kind[:2],'link':kind[2],'kind':kind[3],'chars':[c]})
                pieces=[];cursor=0;tokens={};counts={'r':0,'m':0,'e':0}
                for region in regions:
                    kind=region['identity'][0];counts[kind]+=1;name=kind+str(counts[kind])
                    pieces.extend([text[cursor:region['start']],'{{'+name+'}}']);cursor=region['end']
                    token={'type':kind,'text':re.sub(r'\s+',' ',text[region['start']:region['end']]).strip()}
                    if kind=='r':token.update(kind=region['kind'],link=region['link'])
                    else:token.update(bbox=list(api.union([{'bbox':c['bbox']} for c in region['chars']])),glyphs=[{'c':c['c'],'origin':list(c['origin'])} for c in region['chars']])
                    tokens[name]=token
                pieces.append(text[cursor:]);marked=''.join(pieces)
                entry={'id':key,'source_ids':[s['id'] for s in block['spans']],'bbox':block['bbox'],'text':marked,'tokens':tokens}
                page['blocks'].append(entry);target['blocks'].append({'id':key,'mode':None,'role':'body','text':None})
                readable.append(key+' '+marked.replace('\n',' '))
                if tokens:readable.append('  '+ '; '.join(k+'='+v['text'] for k,v in tokens.items()))
            catalog['pages'].append(page);translations['pages'].append(target)
    api.save(catalog_path,catalog);api.save(translation_path,translations)
    (work/'reading.txt').write_text('\n'.join(readable),encoding='utf8')
    return {'reading':str(work/'reading.txt'),'edit':str(translation_path),'catalog':str(catalog_path),'note':'公式识别是候选，须核对原页；填写 mode=translate/keep 和忠实译文后 compile。'}


def compile_plan(work, api):
    work,manifest,sources,_=api.read_work(work)
    catalog=api.load(work/'segments.source.json');translated=api.load(work/'segments.translation.json')
    if catalog['source_sha256']!=manifest['source_sha256']:raise ValueError('分段稿与当前原 PDF 不匹配。')
    if len(catalog['pages'])!=len(sources) or len(translated['pages'])!=len(sources):raise ValueError('分段稿页数不完整。')
    plans=[]
    for source,cp,tp in zip(sources,catalog['pages'],translated['pages']):
        if source['page']!=cp['page'] or source['page']!=tp['page']:raise ValueError('分段页码不一致。')
        expected={b['spans'][0]['id'].split('-l')[0]:[s['id'] for s in b['spans']] for b in source['blocks']}
        originals={b['id']:b for b in cp['blocks']};edits={b['id']:b for b in tp['blocks']}
        if len(originals)!=len(cp['blocks']) or len(edits)!=len(tp['blocks']) or set(originals)!=set(expected) or set(edits)!=set(expected):raise ValueError('分段条目缺失、重复或未知。')
        plan={'page':source['page'],'items':[],'keep':[],'protected_regions':tp.get('protected_regions',[]),'blank_page_confirmed':tp.get('blank_page_confirmed',False)}
        for key in expected:
            b,edit=originals[key],edits[key]
            if b['source_ids']!=expected[key]:raise ValueError('分段稿的源 span 标识已变化。')
            if edit['mode']=='keep':
                plan['keep'].append({'source_ids':expected[key],'kind':edit['kind'],'reason':edit['reason']});continue
            if edit['mode']!='translate' or not isinstance(edit.get('text'),str):raise ValueError(key+' 尚未完成翻译或保留分类。')
            text=edit['text'];parts=[];runs=[];maths=[];used=set();last=0;length=0
            for match in re.finditer(r'\{\{(\w+)\}\}',text):
                plain=text[last:match.start()];parts.append(plain);length+=len(plain);name=match[1]
                if name not in b['tokens'] or name in used:raise ValueError(key+' 的标记未知或重复：'+name)
                token=b['tokens'][name];used.add(name)
                if token['type']=='r':
                    value=token['text'];start=length
                    if token['kind']=='crossref':
                        prefix=re.search(r'(?:Figures?|Figs?\.|Tables?|Tabs?\.|Algorithms?|Algs?\.|Sections?|Secs?\.|Appendix|Equations?|Eqs?\.|§)\s*$',''.join(parts))
                        if prefix:start-=len(prefix[0])
                    runs.append({'start':start,'end':length+len(value),'kind':token['kind'],'link':token['link']})
                else:
                    value='{{math:'+name+'}}';maths.append({k:token[k] for k in ['bbox','glyphs']});maths[-1]['id']=name
                parts.append(value);length+=len(value);last=match.end()
            parts.append(text[last:]);text=''.join(parts)
            omitted=[name for name,t in b['tokens'].items() if name not in used and (t['type']=='r' or edit.get('role','body') not in ['heading','metadata'])]
            if omitted:raise ValueError(key+' 漏掉引用/公式/示例标记：'+', '.join(omitted))
            for run in runs:run['text']=text[run['start']:run['end']]
            role=edit.get('role','body');height=b['bbox'][3]-b['bbox'][1]
            item={'source_ids':expected[key],'text':text,'math':maths,'runs':runs,'typography':{'family':'song','line_height':1.05 if role=='heading' else 1.15 if role=='caption' else 1.2 if height<30 else 1.45,'indent_em':0 if role in ['heading','caption'] else 2,'weight':'normal','align':'left'}}
            for field in ['bbox','font_size','typography','image_overlap_review']:
                if field in edit:item[field]=edit[field]
            plan['items'].append(item)
        plans.append(plan)
    # Only emit after every page is semantically classified. Existing geometry
    # and link checks remain in validate/build; this does not authorize upload.
    for plan in plans:api.save(work/'pages'/f"{plan['page']:04}.translation.json",plan)
    return {'compiled_pages':len(plans),'next':'validate/build；核对数学候选、保留区域和图表，之后 render/review。'}
