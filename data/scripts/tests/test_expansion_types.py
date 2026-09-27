import unittest
from expansion_types import product_type
class TypeTests(unittest.TestCase):
 def test_original_beats_empty_taxonomy_retained(self):
  self.assertEqual(product_type({'parent_asin':'B0C338S8M7','categories':[]}), 'Headphones')
 def test_earpads_not_headphones(self):
  self.assertIsNone(product_type({'categories':['Electronics','Headphones, Earbuds & Accessories','Earpads']}))
 def test_leaf_categories_are_disjoint(self):
  self.assertEqual(product_type({'categories':['Electronics','Computers','Keyboards']}),'Keyboards')
