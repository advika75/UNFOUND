from __future__ import annotations

import pytest
from selenium.common.exceptions import TimeoutException

from scraper import run_category_batch as batch


class FakeDriver:
    def __init__(self, *, url: str = "https://www.instagram.com/", body: str = "") -> None:
        self.current_url = url
        self.body = body
        self.stopped = False
        self.get_error: Exception | None = None
        self.script_results: list[object] = []
        self.script_calls = 0

    def get(self, url: str) -> None:
        self.current_url = url
        if self.get_error:
            raise self.get_error

    def execute_script(self, script: str) -> str:
        if script == "window.stop()":
            self.stopped = True
            return ""
        if script.startswith("return document.body"):
            return self.body
        self.script_calls += 1
        if self.script_results:
            result = self.script_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result  # type: ignore[return-value]
        return self.body


def test_navigation_timeout_stops_load_then_waits_for_required_elements(monkeypatch) -> None:
    driver = FakeDriver()
    driver.get_error = TimeoutException("page load timed out")
    waits: list[tuple[str, ...]] = []

    def wait_for(_driver, selectors, _timeout):
        waits.append(tuple(selectors))
        return True

    monkeypatch.setattr(batch, "wait_for_selectors", wait_for)
    timed_out = batch.safe_navigate(
        driver,
        "https://www.instagram.com/explore/tags/example/",
        batch.POST_LINK_SELECTORS,
    )

    assert timed_out is True
    assert driver.stopped is True
    assert waits == [batch.POST_LINK_SELECTORS]


def test_post_script_timeout_stops_then_waits_for_image_and_caption(monkeypatch) -> None:
    driver = FakeDriver()
    driver.script_results = [
        TimeoutException("script timed out"),
        {"description": "Product caption", "primary": "https://images.example/item.jpg", "images": []},
    ]
    waits: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        batch,
        "wait_for_selectors",
        lambda _driver, selectors, _timeout: waits.append(tuple(selectors)) or True,
    )

    result = batch.extract_post_page(driver)

    assert driver.stopped is True
    assert waits == [batch.POST_DATA_SELECTORS]
    assert result["description"] == "Product caption"
    assert result["image_url"] == "https://images.example/item.jpg"


@pytest.mark.parametrize(
    ("url", "body", "expected"),
    [
        ("https://www.instagram.com/accounts/login/", "", "login redirect"),
        ("https://www.instagram.com/challenge/", "", "challenge page"),
        ("https://www.instagram.com/", "429 Too Many Requests", "HTTP 429"),
        ("https://www.instagram.com/", "Please try again later", "try again later"),
    ],
)
def test_abort_conditions_are_terminal(url: str, body: str, expected: str) -> None:
    reason = batch.abort_reason(FakeDriver(url=url, body=body))
    assert reason is not None
    assert expected in reason


def test_safe_navigation_raises_abort_before_waiting(monkeypatch) -> None:
    driver = FakeDriver(url="https://www.instagram.com/accounts/login/")
    monkeypatch.setattr(batch, "wait_for_selectors", lambda *_args: pytest.fail("must abort before waiting"))

    with pytest.raises(batch.ScrapeAbort, match="login redirect"):
        batch.safe_navigate(driver, driver.current_url, batch.POST_LINK_SELECTORS)
