from backend.pilot_qa import build_pilot_report, compare_inventory, embedding_status, feed_isolation


def test_pilot_embedding_and_stable_image_verification():
    vector = [1 / (512 ** .5)] * 512
    assert embedding_status(vector)["valid"] is True
    report = build_pilot_report(
        [{"id":"p1","brand_id":"b1","image_url":"https://project.supabase.co/storage/v1/object/public/product-images/b1/p1.jpg","embedding":vector,"normalized_subcategory":"women-tops","classifier_confidence":.9}],
        [{"id":"b1","instagram_username":"pilot"}], supabase_url="https://project.supabase.co", handles={"pilot"},
    )
    assert report["pilot_products"] == 1 and report["stable_image_coverage_percent"] == 100 and report["valid_embeddings"] == 1


def test_stylish_tops_and_modern_ethnic_feed_isolation():
    tops = feed_isolation([{"id":"1","product_name":"Black crop top"}], {"tops","shirts"})
    ethnic = feed_isolation([{"id":"2","product_name":"Cotton kurta"},{"id":"3","product_name":"Western mini dress"}], {"ethnic-upperwear","sets"})
    assert tops["precision"] == 1
    assert ethnic["precision"] == .5 and ethnic["mismatches"][0]["id"] == "3"


def test_inventory_before_after_comparison():
    before={"inventory":[{"type":"kurta","products":2,"brands":1}]}; after={"inventory":[{"type":"kurta","products":7,"brands":2,"status":"NEED_MORE","family":"Ethnic"}]}
    row=compare_inventory(before,after)[0]
    assert row["product_change"] == 5 and row["brand_change"] == 1
