from decimal import Decimal

import pytest

from scraper.shopify_catalog import parse_shopify_products


def test_parse_shopify_products_extracts_title_type_tags_images_and_prices() -> None:
    products = parse_shopify_products(
        {
            "products": [
                {
                    "title": "Cotton Shirt",
                    "product_type": "Men's Shirt",
                    "tags": "cotton, menswear",
                    "featured_image": "https://cdn.example/shirt.jpg",
                    "images": [
                        {"src": "https://cdn.example/shirt.jpg"},
                        {"src": "https://cdn.example/shirt-back.jpg"},
                    ],
                    "variants": [{"price": "1299.00"}, {"price": "1499"}],
                }
            ]
        }
    )

    assert len(products) == 1
    product = products[0]
    assert product.title == "Cotton Shirt"
    assert product.product_type == "Men's Shirt"
    assert product.tags == ("cotton", "menswear")
    assert product.image_urls == (
        "https://cdn.example/shirt.jpg",
        "https://cdn.example/shirt-back.jpg",
    )
    assert product.prices == (Decimal("1299.00"), Decimal("1499"))


def test_parse_shopify_products_handles_empty_and_malformed_optional_fields() -> None:
    products = parse_shopify_products(
        {
            "products": [
                {"title": "No image", "images": [None, "relative.jpg"], "variants": [{"price": "N/A"}]},
                None,
            ]
        }
    )

    assert len(products) == 1
    assert products[0].image_urls == ()
    assert products[0].prices == ()
    assert products[0].tags == ()


def test_parse_shopify_products_requires_products_list() -> None:
    with pytest.raises(ValueError, match="products list"):
        parse_shopify_products({"products": {}})
