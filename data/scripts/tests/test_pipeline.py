import unittest
import pipeline as p

class PipelineTests(unittest.TestCase):
 def source(self, **kw):
  return dict(parent_asin='B000000001',asin='B000000002',timestamp=1609459200000,rating=2.0,title='Battery',text='Battery failed after several weeks of daily use. '*4,helpful_vote=1,verified_purchase=True,**kw)
 def test_aliases_and_units(self):
  a=self.source();b=dict(a);b['sort_timestamp']=b.pop('timestamp');b['helpful_votes']=b.pop('helpful_vote')
  self.assertEqual(p.normalize(a,1,'rev'),p.normalize(b,1,'rev'))
  b['sort_timestamp']=1609459200
  self.assertEqual(p.normalize(a,1,'rev')['_id'],p.normalize(b,1,'rev')['_id'])
 def test_conflicting_aliases_rejected(self):
  a=self.source();a['sort_timestamp']=1700000000000
  with self.assertRaises(ValueError):p.normalize(a,1,'rev')
 def test_identity_ignores_reviewer_and_source_line(self):
  a=p.normalize(self.source(user_id='private'),1,'rev');b=p.normalize(self.source(user_id='other'),2,'rev')
  self.assertEqual(a['_id'],b['_id']);self.assertNotIn('user_id',str(a))
  self.assertEqual(len(p.deduplicate([a,b])[0]),1)
 def test_collision_rejected(self):
  a=p.normalize(self.source(),1,'rev');b=dict(a,text='different')
  with self.assertRaises(ValueError):p.deduplicate([a,b])
 def test_global_chronology_and_gate(self):
  rows=[]
  for i in range(15):
   a=self.source();a['timestamp']+=i//2*86400000;a['title']=str(i)
   rows.append(p.normalize(a,i+1,'rev'))
  batches=p.assign_batches(rows)
  self.assertLess(max(x['timestamp'] for x in rows if x['batch']=='A'),min(x['timestamp'] for x in rows if x['batch']=='B'))
  self.assertLess(max(x['timestamp'] for x in rows if x['batch']=='B'),min(x['timestamp'] for x in rows if x['batch']=='C'))
  self.assertTrue(all(x['held_out'] for x in rows if x['batch']=='C'))
  with self.assertRaises(ValueError):p.review_filter('B000000001','C')
  self.assertTrue(p.review_filter('B000000001','C',evaluation=True)['held_out'])
 def test_bad_timestamp(self):
  a=self.source();a['timestamp']=999
  with self.assertRaises(ValueError):p.normalize(a,1,'rev')

if __name__=='__main__':unittest.main()

class ShortReviewTests(unittest.TestCase):
 def test_expansion_accepts_specific_concise_feedback_explicitly(self):
  source=dict(parent_asin='B000000001',asin='B000000002',timestamp=1609459200000,rating=2.0,title='Stopped working',text='Stopped working after only one month.')
  with self.assertRaises(ValueError):p.normalize(source,1,'revision')
  self.assertEqual(p.normalize(source,1,'revision',min_words=5)['text'],source['text'])
