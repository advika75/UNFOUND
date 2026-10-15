from backend.catalog_discovery import inventory_report


def product(**extra):
    row = {"id": "p1", "brand_id": "b1", "product_name": "Black crop top", "description": "Fitted party crop top"}
    row.update(extra)
    return row


def test_inventory_report_counts_static_export_images_as_stable_when_configured(monkeypatch):
    monkeypatch.setattr("backend.catalog_discovery.client", lambda *_a, **_k: object())
    monkeypatch.setattr(
        "backend.catalog_discovery.fetch_all",
        lambda _db, table: [product(image_url="https://unfound-images.pages.dev/product-images/b1/p1.jpg")] if table == "products" else [],
    )
    monkeypatch.delenv("R2_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("IMAGE_PUBLIC_BASE_URL", "https://unfound-images.pages.dev")
    report = inventory_report()
    assert report["stable_images"] == 1

    monkeypatch.delenv("IMAGE_PUBLIC_BASE_URL", raising=False)
    report = inventory_report()
    assert report["stable_images"] == 0


def test_inventory_report_falls_back_to_r2_public_base_url_if_set(monkeypatch):
    monkeypatch.setattr("backend.catalog_discovery.client", lambda *_a, **_k: object())
    monkeypatch.setattr(
        "backend.catalog_discovery.fetch_all",
        lambda _db, table: [product(image_url="https://images.unfound.example/product-images/b1/p1.jpg")] if table == "products" else [],
    )
    monkeypatch.delenv("IMAGE_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://images.unfound.example")
    report = inventory_report()
    assert report["stable_images"] == 1
