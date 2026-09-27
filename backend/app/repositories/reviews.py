"""Mongo persistence boundary, including durable idempotency and throttling."""
from app.repositories.capacity import CapacityGuard
from datetime import timedelta
from uuid import uuid4
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class ReviewRepository:
    def __init__(self, database, *, capacity=None):
        self.database=database
        self.capacity = capacity if capacity is not None else CapacityGuard(database)

    def replay(self, author_id, key):
        return self.database.reviews.find_one({'source':'user_submission','author_id':author_id,'idempotency_key':key})

    def product(self, product_id):
        return self.database.products.find_one({'_id':product_id})

    def products(self, cursor, limit):
        query={'_id':{'$gt':cursor}} if cursor else {}
        return list(self.database.products.find(query,{'title':1,'product_type':1}).sort('_id',1).limit(limit+1))

    def known_variants(self, product):
        known=set(product.get('asins',[])) | set(product.get('variant_asins',[]))
        for variant in product.get('variants',[]):
            if isinstance(variant,str): known.add(variant)
            elif isinstance(variant,dict) and isinstance(variant.get('asin'),str): known.add(variant['asin'])
        known.update(self.database.reviews.distinct('asin',{'parent_asin':product['_id']}))
        return known

    def get(self, review_id):
        return self.database.reviews.find_one({'_id':review_id})

    def list(self, product_id, author_id, source, batch_id, after, limit):
        query={'parent_asin':product_id}; clauses=[]
        if author_id is not None: query.update(author_id=author_id,source='user_submission')
        if source=='amazon_2023': clauses.append({'$or':[{'source':'amazon_2023'},{'source':{'$exists':False}}]})
        elif source is not None: clauses.append({'source':source})
        if batch_id is not None: query['batch_id']=batch_id
        if after:
            stamp,record_id=after
            clauses.append({'$or':[{'timestamp':{'$gt':stamp}},{'timestamp':stamp,'_id':{'$gt':record_id}}]})
        if clauses: query['$and']=clauses
        return list(self.database.reviews.find(query).sort([('timestamp',1),('_id',1)]).limit(limit+1))

    def create_submission(self, product_id, author_id, key, payload, digest, now):
        document={'_id':str(uuid4()),'source':'user_submission','parent_asin':product_id,'asin':payload.asin or product_id,
                  'title':payload.title,'text':payload.text,'rating':payload.rating,'timestamp':now,'timestamp_ms':int(now.timestamp()*1000),
                  'created_at':now,'author_id':author_id,'version':1,'provenance':{'kind':'user_submission'},
                  'idempotency_key':key,'payload_digest':digest,
                  'processing':{'status':'pending','attempts':0,'next_attempt_at':now,'memory_status':'pending','classification_status':'pending'}}
        return self.insert(document)

    def insert(self, document):
        self.capacity.check_documents([document])
        try:
            self.database.reviews.insert_one(document)
            return document
        except DuplicateKeyError:
            winner=self.replay(document['author_id'],document['idempotency_key'])
            if winner is None: raise
            return winner

    def consume(self, author_id, now, limit):
        self.capacity.check_write()
        window=int(now.timestamp())//60
        record_id=f'{author_id}:{window}'
        query={'_id':record_id}
        update={'$inc':{'count':1},'$setOnInsert':{'expires_at':now+timedelta(minutes=2)}}
        try:
            record=self.database.rate_limits.find_one_and_update(query,update,upsert=True,return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:
            record=self.database.rate_limits.find_one_and_update(query,{'$inc':{'count':1}},return_document=ReturnDocument.AFTER)
        return record['count']<=limit
