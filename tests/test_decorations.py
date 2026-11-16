"""Regression tests for semantic quotation handling and moving text rules."""
import tempfile
from pathlib import Path
import unittest
from test_translation import t, args, quiet, fitz


class Decorations(unittest.TestCase):
    def test_complete_build_records_migration_and_rebuilds_link(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);pdf=root/'source.pdf';d,p,_,_=self.fixture()
            p.insert_link({'kind':2,'from':fitz.Rect(40,70,130,83),'uri':'https://example.org'})
            d.save(pdf);d.close();work=root/'job'
            quiet(t.prepare,args(pdf=str(pdf),key=None,work=str(work)))
            source=t.load(work/'pages/0001.source.json');ids=[s['id'] for b in source['blocks'] for s in b['spans']]
            text='对应的中文标题。其他说明。'
            item={'source_ids':ids,'text':text,
                  'underlines':[{'source':'u1','text':'中文标题','start':3,'end':7}],
                  'runs':[{'kind':'hyperlink','text':'中文标题','start':3,'end':7,'link':{'kind':2,'uri':'https://example.org'}}]}
            t.save(work/'pages/0001.translation.json',{'page':1,'items':[item],'keep':[],'protected_regions':[]})
            quiet(t.build,args(work=str(work)))
            quality=t.load(work/'quality.json');self.assertEqual(quality['underline_migration'][0]['removed_source_underlines'],1)
            self.assertGreater(quality['underline_migration'][0]['translated_underline_fragments'],0)
            with fitz.open(work/'中文全文译文.pdf') as out:
                self.assertTrue(all(link['from'].y0<100 for link in out[0].get_links()))
                self.assertEqual(len(out[0].get_links()),1)
                self.assertTrue(all(abs(line['rect'].y0-81.5)>.1 for line in out[0].get_drawings()))

    def fixture(self):
        d=fitz.open();p=d.new_page()
        p.insert_text((40,80),'Underlined heading. Other text.',fontsize=11)
        x=40+fitz.get_text_length('Underlined heading.',fontsize=11)
        p.draw_line((40,81.5),(x,81.5),width=.4)
        p.draw_line((30,100),(350,100),width=.5) # table rule
        source=t.page_source(p);spans=[s for b in source['blocks'] for s in b['spans']]
        item={'source_ids':[s['id'] for s in spans],'source_spans':spans,'text':'对应的中文标题。其他说明。'}
        return d,p,source,item

    def test_missing_mapping_blocks_and_table_rule_survives(self):
        d,p,_,item=self.fixture()
        found=t.decoration_api('detect',p)
        self.assertEqual([u['text'] for u in found],['Underlined heading.'])
        with self.assertRaisesRegex(ValueError,'缺少对应译文'):t.decoration_api('checked',p,[item])
        item['underlines']=[{'source':'u1','start':0,'end':8,'text':item['text'][:8]}]
        found=t.decoration_api('checked',p,[item])
        before=p.get_text();t.decoration_api('remove',p,found)
        self.assertEqual(p.get_text(),before)
        self.assertEqual(len(p.get_drawings()),1)
        self.assertEqual(p.get_drawings()[0]['rect'].y0,100)
        d.close()

    def test_underlines_follow_new_wrapped_text_and_link_ranges(self):
        d,p,source,item=self.fixture()
        item['text']='迁移后的中文标题跨行显示以及文献引用。'
        item['underlines']=[{'source':'u1','start':0,'end':len(item['text']),'text':item['text']}]
        start=item['text'].index('文献引用')
        item['runs']=[{'start':start,'end':start+4,'text':'文献引用','kind':'citation','link':{'kind':2,'uri':'https://example.org'}}]
        found=t.decoration_api('checked',p,[item]);t.decoration_api('remove',p,found)
        t.guarded_remove_text(p,[item],source)
        colors={};markup=t.decoration_api('markup',item,colors)
        spare,scale=p.insert_htmlbox((40,300,135,440),markup)
        self.assertGreaterEqual(spare,0)
        seen,count=t.decoration_api('paint',p,colors)
        self.assertEqual(seen,set(colors));self.assertGreater(count,1)
        self.assertTrue(any(v['target'] for v in colors.values()))
        rules=p.get_drawings();self.assertEqual(len(rules),count+1)
        self.assertTrue(all(dr['rect'].y0>300 for dr in rules[1:]))
        d.close()

    def test_quote_punctuation_does_not_create_preserved_examples(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td);pdf=path/'input.pdf'
            d=fitz.open();p=d.new_page()
            p.insert_htmlbox((30,40,500,100),'We call this “change what is requested, preserve the rest”.')
            d.save(pdf);d.close()
            work=path/'job';quiet(t.prepare,args(pdf=str(pdf),key=None,work=str(work)))
            catalog=t.load(work/'segments.source.json');block=catalog['pages'][0]['blocks'][0]
            self.assertIn('change what is requested',block['text'])
            self.assertFalse(any(v['type']=='e' for v in block['tokens'].values()))
            # Old quote placeholders must be explicitly translated or classified.
            block['tokens']={'e1':{'type':'e','text':'“change what is requested, preserve the rest”','bbox':[30,40,300,60],'glyphs':[]}}
            t.save(work/'segments.source.json',catalog)
            translated=t.load(work/'segments.translation.json');edit=translated['pages'][0]['blocks'][0]
            edit.update(mode='translate',text='这一原则是{{e1}}。')
            t.save(work/'segments.translation.json',translated)
            with self.assertRaisesRegex(ValueError,'重新分类'):t.planning(str(work),'compile')
            edit['token_translations']={'e1':'“修改所要求的部分，保留其余部分”'}
            t.save(work/'segments.translation.json',translated);t.planning(str(work),'compile')
            plan=t.load(work/'pages/0001.translation.json')
            self.assertIn('修改所要求的部分',plan['items'][0]['text']);self.assertEqual(plan['items'][0]['math'],[])

    def test_old_quoted_bitmap_is_not_silently_reused(self):
        item={'text':'{{math:e1}}','math':[{'id':'e1','glyphs':[{'c':c} for c in '“ordinary prose”']} ]}
        with self.assertRaisesRegex(ValueError,'普通引语应翻译'):
            t.paragraph_markup(item,{},None,0,None,'source')


if __name__=='__main__':unittest.main()
