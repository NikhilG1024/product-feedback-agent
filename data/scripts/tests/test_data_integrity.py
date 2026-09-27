"""Adversarial checks against a generated, clearly synthetic fixture (never imported)."""
import copy
import unittest
from pipeline import normalize, assign_batches, validate_dataset
from import_mongodb import check_document, Conflict

class IntegrityTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.products=[{'_id':str(i)} for i in range(3)]
  cls.rows=[]
  for i in range(600):
   cls.rows.append(normalize(dict(parent_asin=str(i%3),asin='variant',timestamp=1625097600000+i*86400000,
                                 rating=2.0,title=str(i),text='test words '*20),i+1,'fixture'))
  cls.batches=assign_batches(cls.rows)
 def test_valid_fixture(self):
  self.assertTrue(validate_dataset(self.products,self.rows,self.batches)['foreign_keys'])
 def test_bad_reference(self):
  rows=copy.deepcopy(self.rows);rows[0]['parent_asin']='missing'
  with self.assertRaises(ValueError):validate_dataset(self.products,rows,self.batches)
 def test_holdout_tampering(self):
  rows=copy.deepcopy(self.rows);rows[-1]['held_out']=False
  with self.assertRaises(ValueError):validate_dataset(self.products,rows,self.batches)
 def test_evidence_content_tampering(self):
  rows=copy.deepcopy(self.rows);rows[0]['text']='changed review'
  with self.assertRaises(ValueError):validate_dataset(self.products,rows,self.batches)
 def test_batch_count_tampering(self):
  batches=copy.deepcopy(self.batches);batches[0]['review_count']+=1
  with self.assertRaises(ValueError):validate_dataset(self.products,self.rows,batches)
 def test_no_overwrite(self):
  check_document(None,{'_id':'new'},'reviews')
  check_document({'_id':'same'},{'_id':'same'},'reviews')
  with self.assertRaises(Conflict):check_document({'_id':'same','text':'user data'},{'_id':'same','text':'new'},'reviews')

class CapacityTests(unittest.TestCase):
 def test_storage_ceiling(self):
  from import_mongodb import enforce_capacity
  enforce_capacity(392_000_000,8_000_000)
  with self.assertRaises(Conflict):enforce_capacity(392_000_001,8_000_000)
  with self.assertRaises(Conflict):enforce_capacity(400_000_001,0)

class ExpansionBoundsTests(unittest.TestCase):
 def test_expanded_limits_are_explicit(self):
  IntegrityTests.setUpClass()
  self.assertTrue(validate_dataset(IntegrityTests.products,IntegrityTests.rows,IntegrityTests.batches,max_products=50,max_reviews=50000)['foreign_keys'])

class AtlasQuotaTests(unittest.TestCase):
 def test_quota_uses_uncompressed_documents(self):
  from import_mongodb import storage_usage
  class DB:
   def command(self,*a,**kw):return {'dataSize':100,'storageSize':10,'indexSize':20}
  class Admin:
   def command(self,*a,**kw):return {'databases':[{'name':'app'}]}
  class Client:
   admin=Admin()
   def __getitem__(self,name):return DB()
  self.assertEqual(storage_usage(Client())['total_bytes'],120)
