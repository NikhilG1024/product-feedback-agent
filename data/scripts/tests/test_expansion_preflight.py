import copy,unittest
from datetime import datetime,timezone
from import_expansion300 import validate_existing_batches,SOURCE_MANIFEST
from import_mongodb import Conflict
from pipeline import DATASET
class BatchPreflightTests(unittest.TestCase):
 def fixture(self):
  dates=[datetime(2021+i,1,1,tzinfo=timezone.utc) for i in range(3)]
  batches=[dict(_id=DATASET+':'+b,dataset_id=DATASET,label=b,held_out=b=='C',review_count=1,product_counts={'p':1},start_at=d,end_at=d) for b,d in zip('ABC',dates)]
  stats=[{'_id':{'batch':b['_id'],'product':'p'},'count':1,'start':b['start_at'],'end':b['end_at']} for b in batches]
  class Reviews:
   def aggregate(self,pipeline):return stats
  class Batches:
   def find_one(self,q):return next(b for b in batches if b['_id']==q['_id'])
  class DB:
   reviews=Reviews();batches=Batches()
  return DB(),batches
 def test_valid_current_membership(self):
  db,batches=self.fixture();validate_existing_batches(db,batches)
 def test_bad_count_fails_before_writes(self):
  db,batches=self.fixture();batches[0]['review_count']=2
  with self.assertRaises(Conflict):validate_existing_batches(db,batches)
 def test_foreign_dataset_is_not_overwritten(self):
  db,batches=self.fixture();batches[0]['dataset_id']='another-dataset'
  with self.assertRaises(Conflict):validate_existing_batches(db,batches)
 def test_conflicting_provenance_is_not_overwritten(self):
  db,batches=self.fixture();batches[0]['source_manifest']={'revision':'different'}
  with self.assertRaises(Conflict):validate_existing_batches(db,batches)
