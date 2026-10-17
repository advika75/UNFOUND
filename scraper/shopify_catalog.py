"""Parse the public products JSON exposed by Shopify storefronts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class ShopifyProduct:
    """Small, read-only view of the Shopify product fields used by discovery."""

    title: str
    product_type: str
    tags: tuple[str, ...]
    image_urls: tuple[str, ...]
    prices: tuple[Decimal, ...]


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _tags(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        values = []
    return tuple(text for item in values if (text := _text(item)))


def _image_urls(product: Mapping[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    images = product.get("images")
    if isinstance(images, list):
        for image in images:
            if isinstance(image, Mapping):
                value = _text(image.get("src"))
            else:
                value = _text(image)
            if value.startswith(("https://", "http://")) and value not in found:
                found.append(value)

    featured = product.get("featured_image")
    if isinstance(featured, Mapping):
        featured = featured.get("src")
    featured_url = _text(featured)
    if featured_url.startswith(("https://", "http://")) and featured_url not in found:
        found.insert(0, featured_url)
    return tuple(found)


def _prices(product: Mapping[str, Any]) -> tuple[Decimal, ...]:
    variants = product.get("variants")
    if not isinstance(variants, list):
        return ()
    prices: list[Decimal] = []
    for variant in variants:
        if not isinstance(variant, Mapping):
            continue
        raw_price = variant.get("price")
        try:
            price = Decimal(str(raw_price))
        except (InvalidOperation, ValueError):
            continue
        if price.is_finite() and price >= 0:
            prices.append(price)
    return tuple(prices)


def parse_shopify_products(payload: Mapping[str, Any]) -> list[ShopifyProduct]:
    """Parse ``/products.json`` and reject responses without a products array."""
    raw_products = payload.get("products")
    if not isinstance(raw_products, list):
        raise ValueError("Shopify response must contain a products list")

    products: list[ShopifyProduct] = []
    for raw_product in raw_products:
        if not isinstance(raw_product, Mapping):
            continue
        products.append(
            ShopifyProduct(
                title=_text(raw_product.get("title")),
                product_type=_text(raw_product.get("product_type")),
                tags=_tags(raw_product.get("tags")),
                image_urls=_image_urls(raw_product),
                prices=_prices(raw_product),
            )
        )
    return products
