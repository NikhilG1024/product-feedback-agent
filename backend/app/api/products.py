"""Bounded authenticated product browsing."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from app.api.auth import require_principal
from app.api.reviews import service

router=APIRouter(prefix='/api/v1',dependencies=[Depends(require_principal)])

class ProductResponse(BaseModel):
    id: str
    title: str
    product_type: str | None = None

class ProductPage(BaseModel):
    items: list[ProductResponse]
    next_cursor: str | None

@router.get('/products',response_model=ProductPage)
def products(cursor: str | None=Query(None,max_length=200),limit: int=Query(20,ge=1,le=100),reviews=Depends(service)):
    return reviews.products(cursor,limit)

@router.get('/products/{product_id}',response_model=ProductResponse)
def product(product_id: str,reviews=Depends(service)):
    return reviews.product(product_id)
