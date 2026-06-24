from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class JobType(StrEnum):
    BRAND_LISTING = "brand_listing"
    PRODUCT = "product"
    REVIEWS = "reviews"
    AVATAR = "avatar"
    NLP = "nlp"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    BLOCKED = "blocked"


class BrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=120)
    listing_url: HttpUrl
    enabled: bool = True


class BrandRead(OrmModel):
    id: int
    name: str
    slug: str
    listing_url: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ListingProduct(BaseModel):
    emag_product_id: int | None = None
    offer_id: int | None = None
    family_id: int | None = None
    pnk: str
    title: str
    url: HttpUrl


class ListingPage(BaseModel):
    products: list[ListingProduct]
    next_url: HttpUrl | None = None


class ProductFamilyCreate(BaseModel):
    brand_id: int
    emag_family_id: int
    name: str = Field(min_length=1)
    description: str | None = None
    aggregate_rating: Decimal | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    url: HttpUrl
    scraped_at: datetime | None = None


class VariantCreate(BaseModel):
    family_id: int
    emag_product_id: int | None = None
    pnk: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1)
    storage: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=120)
    price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)
    seller: str | None = Field(default=None, max_length=200)
    available: bool | None = None
    url: HttpUrl


class ParsedVariant(BaseModel):
    emag_product_id: int | None = None
    pnk: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1)
    storage: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=120)
    price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)
    available: bool | None = None
    url: HttpUrl


class ParsedProductPage(BaseModel):
    emag_family_id: int
    family_name: str = Field(min_length=1)
    description: str | None = None
    aggregate_rating: Decimal | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    url: HttpUrl
    variants: list[ParsedVariant] = Field(min_length=1)


class ParsedReview(BaseModel):
    emag_review_id: int
    pnk: str | None = Field(default=None, max_length=40)
    title: str | None = None
    content: str
    rating: int = Field(ge=1, le=5)
    votes: int = Field(default=0, ge=0)
    verified_purchase: bool
    reviewer_name: str | None = Field(default=None, max_length=300)
    reviewer_hash: str | None = Field(default=None, max_length=160)
    review_created_at: datetime | None = None
    published_at: datetime | None = None
    storage: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=120)
    avatar_metadata: dict[str, Any] | None = None


class ParsedReviewPage(BaseModel):
    total_count: int = Field(ge=0)
    reviews: list[ParsedReview]


class TfidfTerm(BaseModel):
    term: str = Field(min_length=1)
    score: float = Field(ge=0)


class NlpResultCreate(BaseModel):
    review_id: int
    cleaned_text: str = Field(min_length=1)
    language: str | None = Field(default=None, max_length=20)
    sentiment_label: str | None = Field(default=None, max_length=20)
    sentiment_score: float | None = Field(default=None, ge=0, le=1)
    rating_mismatch: bool | None = None
    token_count: int = Field(default=0, ge=0)
    unique_token_count: int = Field(default=0, ge=0)
    tfidf_terms: list[TfidfTerm] = Field(default_factory=list)
    model_name: str | None = Field(default=None, max_length=200)


class ReviewCreate(BaseModel):
    emag_review_id: int
    family_id: int
    variant_id: int | None = None
    title: str | None = None
    content: str
    rating: int = Field(ge=1, le=5)
    votes: int = Field(default=0, ge=0)
    verified_purchase: bool
    reviewer_name: str | None = Field(default=None, max_length=300)
    reviewer_hash: str | None = Field(default=None, max_length=160)
    review_created_at: datetime | None = None
    published_at: datetime | None = None
    storage: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=120)
    avatar_metadata: dict[str, Any] | None = None


class JobCreate(BaseModel):
    job_type: JobType
    brand_id: int | None = None
    family_id: int | None = None
    target_url: HttpUrl | None = None
    current_offset: int = Field(default=0, ge=0)
    total_expected: int | None = Field(default=None, ge=0)
    priority: int = 100


class JobRead(OrmModel):
    id: int
    job_type: JobType
    status: JobStatus
    brand_id: int | None
    family_id: int | None
    target_url: str | None
    current_offset: int
    total_expected: int | None
    attempts: int
    priority: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
