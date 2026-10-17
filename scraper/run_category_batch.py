"""Dry-discover niche Indian Instagram brands; ingest only explicitly approved handles."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import certifi
    from dotenv import load_dotenv
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError as error:  # fail before opening Chrome
    print(f"[PREFLIGHT FAILED] Missing scraper dependency: {error}", file=sys.stderr)
    raise SystemExit(1) from error

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
os.environ["SELENIUM_PROFILE_DIR"] = str(ROOT / "scraper" / "chrome_profile")
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("TRANSFORMERS_NO_FLAX", "1")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")
load_dotenv(ROOT / "backend" / ".env", override=True)
load_dotenv(ROOT / "scraper" / ".env", override=True)
# Profile location and certificate roots are fixed after env loading; neither is read here.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
os.environ["SELENIUM_PROFILE_DIR"] = str(ROOT / "scraper" / "chrome_profile")

from training import scraper_pipeline as pipeline

MEN_HASHTAGS = ("MenswearIndia", "StreetwearIndia", "MensEthicWear")
WOMEN_HASHTAGS = ("IndieClothingIndia", "WomensFashionIndia", "HandloomSarees", "ArtisanJewelry")
GIFT_HASHTAGS = ("HomeDecorIndia", "GiftingEssentials", "IndieHomeDecor")
HASHTAGS: tuple[tuple[str, str], ...] = tuple(
    [(tag, "gifting essentials") for tag in GIFT_HASHTAGS]
    + [(tag, "men's clothing") for tag in MEN_HASHTAGS]
    + [(tag, "women's clothing and fashion") for tag in WOMEN_HASHTAGS]
)
HOME_SEEDS = ("nestasia.in", "phool.co", "dailyobjects_official", "doodlecollection")
MEN_SEEDS = ("bonkers.corner", "thesouledstore", "freakinsindia")
WOMEN_SEEDS = ("pipabella", "rubans.accessories", "jokerandwitch", "mokobara", "zouk.is.online")
SEEDS_BY_GROUP = {
    "gifting essentials": HOME_SEEDS,
    "men's clothing": MEN_SEEDS,
    "women's clothing and fashion": WOMEN_SEEDS,
}
GROUPS = (
    "men's clothing",
    "women's clothing and fashion",
    "bags",
    "gifting essentials",
)
POST_LINK_SELECTORS = ("a[href*='/p/']", "a[href*='/reel/']")
POST_DATA_SELECTORS = (
    "meta[property='og:image']",
    "meta[property='og:description']",
    "article img",
    "main article img",
)
MAX_POSTS_PER_TARGET = 5
PER_TARGET_SECONDS = 90.0
PAGE_TIMEOUT_SECONDS = 30.0
PAGE_DELAY_SECONDS = (4.0, 10.0)


class ScrapeAbort(RuntimeError):
    """An Instagram login/checkpoint/rate-limit state requiring an immediate stop."""


class TargetStalled(TimeoutError):
    """A target exceeded its hard time budget or did not expose required elements."""


@dataclass(frozen=True)
class Target:
    kind: str
    value: str
    group: str

    @property
    def label(self) -> str:
        prefix = "#" if self.kind == "hashtag" else "@"
        return f"{prefix}{self.value}"


@dataclass
class Candidate:
    handle: str
    groups: Counter[str] = field(default_factory=Counter)
    posts: dict[str, dict[str, Any]] = field(default_factory=dict)
    post_count_seen: int = 0
    posts_with_image_seen: int = 0
    public_profile: bool | None = None
    bio: str = ""
    followers: int | None = None
    source_tags: set[str] = field(default_factory=set)


def detect_chrome_major_version() -> int:
    candidates = [
        os.getenv("CHROME_BINARY", ""),
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for binary in dict.fromkeys(path for path in candidates if path):
        try:
            result = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        match = re.search(r"\b(\d+)\.", result.stdout + result.stderr)
        if result.returncode == 0 and match:
            return int(match.group(1))
    raise RuntimeError("Could not detect installed Chrome major version.")


def create_driver() -> Any:
    import undetected_chromedriver as uc

    options = uc.ChromeOptions()
    options.page_load_strategy = "eager"
    options.add_argument(f"--user-data-dir={ROOT / 'scraper' / 'chrome_profile'}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--start-maximized")
    major = detect_chrome_major_version()
    driver = uc.Chrome(version_main=major, options=options, use_subprocess=True)
    driver.set_page_load_timeout(PAGE_TIMEOUT_SECONDS)
    driver.set_script_timeout(PAGE_TIMEOUT_SECONDS)
    return driver


def abort_reason(driver: Any) -> str | None:
    url = str(getattr(driver, "current_url", "") or "").lower()
    if "/accounts/login" in url or "/login/" in url:
        return "Instagram login redirect"
    if any(path in url for path in ("/challenge/", "/checkpoint/")):
        return "Instagram checkpoint/challenge page"
    try:
        body = str(driver.execute_script("return document.body ? document.body.innerText : ''") or "")
    except TimeoutException:
        stop_page_load(driver)
        body = ""
    text = body.casefold()
    if any(term in text for term in ("log in to see photos", "log in to view this", "you must log in", "log in to continue")):
        return "Instagram login wall"
    if re.search(r"(?:http\s+)?429\b", text):
        return "Instagram HTTP 429 rate limit"
    if "429" in text and any(term in text for term in ("too many requests", "try again", "rate limit")):
        return "Instagram HTTP 429 rate limit"
    if "try again later" in text or "please wait a few minutes" in text:
        return "Instagram says try again later"
    if any(term in text for term in ("challenge_required", "confirm it's you", "security code")):
        return "Instagram checkpoint/challenge page"
    return None


def stop_page_load(driver: Any) -> None:
    try:
        driver.execute_script("window.stop()")
    except Exception:
        pass


def wait_for_selectors(driver: Any, selectors: Sequence[str], timeout: float) -> bool:
    deadline = time.monotonic() + max(timeout, 0.1)

    def has_required_elements(active_driver: Any) -> bool:
        return any(active_driver.find_elements(By.CSS_SELECTOR, selector) for selector in selectors)

    try:
        WebDriverWait(driver, max(0.1, deadline - time.monotonic()), poll_frequency=0.25).until(
            has_required_elements
        )
        return True
    except TimeoutException:
        stop_page_load(driver)
        return False


def safe_navigate(
    driver: Any,
    url: str,
    selectors: Sequence[str],
    *,
    timeout: float = PAGE_TIMEOUT_SECONDS,
) -> bool:
    """Navigate eagerly, stop stalled loads, then wait for the actual page elements."""
    timed_out = False
    try:
        driver.get(url)
    except TimeoutException:
        timed_out = True
        stop_page_load(driver)

    reason = abort_reason(driver)
    if reason:
        raise ScrapeAbort(reason)
    found = wait_for_selectors(driver, selectors, timeout)
    reason = abort_reason(driver)
    if reason:
        raise ScrapeAbort(reason)
    if not found:
        raise TargetStalled(f"required elements absent after navigation: {url}")
    # A navigation timeout is recoverable when the required elements appeared.
    return timed_out


def check_abort_or_stop(driver: Any) -> None:
    reason = abort_reason(driver)
    if reason:
        raise ScrapeAbort(reason)


def wait_between_visits(deadline: float, last_visit: list[float | None]) -> None:
    now = time.monotonic()
    if last_visit[0] is not None:
        delay = random.uniform(*PAGE_DELAY_SECONDS)
        if now + delay >= deadline:
            raise TargetStalled("target deadline reached before next page visit")
        time.sleep(delay)
    last_visit[0] = time.monotonic()


def body_text(driver: Any) -> str:
    try:
        return str(driver.execute_script("return document.body ? document.body.innerText : ''") or "")
    except TimeoutException:
        stop_page_load(driver)
        check_abort_or_stop(driver)
        return ""


def extract_post_page(driver: Any) -> dict[str, Any]:
    script = """
        const description = document.querySelector("meta[property='og:description'], meta[name='description']")?.content || '';
        const primary = document.querySelector("meta[property='og:image'], meta[name='twitter:image']")?.content || '';
        const images = Array.from(document.querySelectorAll('article img, main article img'))
          .map(node => node.currentSrc || node.src || '').filter(value => /^https?:\\/\\//.test(value));
        return {description, primary, images: [...new Set(images)]};
        """
    try:
        payload = driver.execute_script(script)
    except TimeoutException:
        stop_page_load(driver)
        check_abort_or_stop(driver)
        wait_for_selectors(driver, POST_DATA_SELECTORS, PAGE_TIMEOUT_SECONDS)
        payload = driver.execute_script(script)
    payload = payload or {}
    images = [str(value) for value in payload.get("images", []) if str(value).startswith(("http://", "https://"))]
    primary = str(payload.get("primary") or "")
    if primary and primary not in images:
        images.insert(0, primary)
    return {
        "description": str(payload.get("description") or "").strip(),
        "image_url": primary or (images[0] if images else ""),
    }


def handle_from_post(description: str, fallback_alt: str = "") -> str:
    identity = re.search(r"(?:Photo|Video|Reel)\s+(?:by|shared by)\s+@?([A-Za-z0-9._]+)", description, re.I)
    if not identity:
        identity = re.search(r"@([A-Za-z0-9._]{2,})", description)
    if not identity:
        identity = re.search(r"(?:Photo|Video|Reel)\s+(?:by|shared by)\s+@?([A-Za-z0-9._]+)", fallback_alt, re.I)
    return pipeline.normalize_instagram_username(identity.group(1)) if identity else ""


def group_for_post(description: str, source_group: str) -> str:
    lower = description.casefold()
    if re.search(r"\b(bag|bags|tote|handbag|clutch|backpack|luggage)\b", lower):
        return "bags"
    slug = pipeline.fallback_category(description).get("category_slug", "")
    if str(slug).startswith("men-") or any(token in lower for token in ("menswear", "men's", "for men")):
        return "men's clothing"
    if any(token in lower for token in ("gift", "gifting", "home decor", "homeware")) or slug == "home-decor":
        return "gifting essentials"
    if str(slug).startswith("women-"):
        return "women's clothing and fashion"
    return source_group


def make_post(
    *,
    handle: str,
    post_url: str,
    image_url: str,
    description: str,
    source: str,
) -> Any:
    username = pipeline.normalize_instagram_username(handle)
    profile_url = f"https://www.instagram.com/{username}/" if username else ""
    return pipeline.ScrapedInstagramPost(
        product_url=post_url,
        image_url=image_url,
        description=description,
        brand_name=username or "unknown_brand",
        instagram_username=username,
        instagram_profile_url=profile_url,
        source_hashtag=source,
        scraped_at=datetime.now(timezone.utc).isoformat(),
        instagram_post_id=pipeline.instagram_post_id(post_url),
        metadata={"category_group": group_for_post(description, "women's clothing and fashion")},
    )


def add_candidate_post(
    candidates: dict[str, Candidate],
    post: Any,
    *,
    source_group: str,
    source_tag: str,
) -> None:
    handle = pipeline.normalize_instagram_username(post.instagram_username)
    if not handle or handle == "unknown_brand":
        return
    candidate = candidates.setdefault(handle, Candidate(handle=handle))
    group = group_for_post(post.description, source_group)
    candidate.groups[group] += 1
    candidate.source_tags.add(source_tag)
    productish = pipeline.is_likely_product_post(post)[0]
    candidate.posts[post.product_url] = {
        "post_url": post.product_url,
        "image_url": post.image_url,
        "caption": post.description[:1000],
        "category_group": group,
        "product_signal": productish,
    }
    candidate.post_count_seen = len(candidate.posts)
    candidate.posts_with_image_seen = sum(bool(item["image_url"]) for item in candidate.posts.values())


def check_deadline(deadline: float, target: Target) -> None:
    if time.monotonic() >= deadline:
        raise TargetStalled(f"hard target timeout ({PER_TARGET_SECONDS:.0f}s)")


def target_visit(
    driver: Any,
    target: Target,
    url: str,
    selectors: Sequence[str],
    deadline: float,
    last_visit: list[float | None],
) -> None:
    check_deadline(deadline, target)
    wait_between_visits(deadline, last_visit)
    remaining = max(0.1, deadline - time.monotonic())
    driver.set_page_load_timeout(min(PAGE_TIMEOUT_SECONDS, remaining))
    driver.set_script_timeout(min(PAGE_TIMEOUT_SECONDS, remaining))
    safe_navigate(driver, url, selectors, timeout=min(PAGE_TIMEOUT_SECONDS, remaining))
    last_visit[0] = time.monotonic()
    check_abort_or_stop(driver)
    check_deadline(deadline, target)


def scan_target(
    driver: Any,
    target: Target,
    candidates: dict[str, Candidate],
    existing_handles: set[str],
    last_visit: list[float | None],
    run_deadline: float,
) -> None:
    started = time.monotonic()
    deadline = min(started + PER_TARGET_SECONDS, run_deadline)
    status = "ok"
    posts_added = 0
    try:
        if target.kind == "hashtag":
            target_visit(
                driver,
                target,
                f"https://www.instagram.com/explore/tags/{target.value}/",
                POST_LINK_SELECTORS,
                deadline,
                last_visit,
            )
            entries = pipeline.collect_post_candidates(driver)[:MAX_POSTS_PER_TARGET]
        else:
            target_visit(
                driver,
                target,
                f"https://www.instagram.com/{target.value}/",
                ("main", "header", "article"),
                deadline,
                last_visit,
            )
            page_text = body_text(driver).casefold()
            if "this account is private" in page_text or "this profile is private" in page_text:
                candidate = candidates.setdefault(target.value, Candidate(handle=target.value))
                candidate.public_profile = False
                status = "private profile"
                return
            candidate = candidates.setdefault(target.value, Candidate(handle=target.value))
            candidate.public_profile = True
            candidate.bio = pipeline.extract_profile_bio_from_page(driver)
            candidate.followers = pipeline.extract_followers_from_page(driver)
            entries_all = pipeline.collect_post_candidates(driver)
            candidate.post_count_seen = max(candidate.post_count_seen, len(entries_all))
            candidate.posts_with_image_seen = max(
                candidate.posts_with_image_seen,
                sum(bool(entry.get("img")) for entry in entries_all),
            )
            entries = entries_all[:MAX_POSTS_PER_TARGET]

        for entry in entries:
            check_deadline(deadline, target)
            post_url = str(entry.get("href") or "").split("?", 1)[0].rstrip("/")
            if "/p/" not in post_url and "/reel/" not in post_url:
                continue
            target_visit(
                driver,
                target,
                post_url,
                POST_DATA_SELECTORS,
                deadline,
                last_visit,
            )
            driver.set_script_timeout(min(PAGE_TIMEOUT_SECONDS, max(0.1, deadline - time.monotonic())))
            page = extract_post_page(driver)
            description = page["description"] or str(entry.get("alt") or "").strip()
            image_url = page["image_url"] or str(entry.get("img") or "")
            handle = target.value if target.kind == "profile" else handle_from_post(description, str(entry.get("alt") or ""))
            if not handle or not description or not image_url:
                continue
            post = make_post(
                handle=handle,
                post_url=post_url,
                image_url=image_url,
                description=description,
                source=target.value if target.kind == "hashtag" else "seed_profile",
            )
            if not pipeline.is_likely_product_post(post)[0]:
                # Keep non-product posts out of the eligibility evidence.
                continue
            add_candidate_post(candidates, post, source_group=target.group, source_tag=target.value)
            posts_added += 1
            if target.kind == "hashtag" and handle not in existing_handles:
                candidates.setdefault(handle, Candidate(handle=handle))
    except ScrapeAbort as error:
        status = f"aborted: {error}"
        raise
    except TargetStalled as error:
        status = f"skipped: {error}"
    except TimeoutException as error:
        stop_page_load(driver)
        status = f"skipped: Selenium timeout: {error.__class__.__name__}"
    finally:
        print(
            f"TARGET {target.label} | group={target.group} | posts={posts_added} | "
            f"seconds={time.monotonic() - started:.1f} | {status}",
            flush=True,
        )


def seed_targets() -> list[Target]:
    targets: list[Target] = []
    for group, handles in SEEDS_BY_GROUP.items():
        targets.extend(Target("profile", handle, group) for handle in handles)
    seed_file = ROOT / "data" / "scrape_seeds.txt"
    if seed_file.exists():
        for raw in seed_file.read_text(encoding="utf-8").splitlines():
            value = raw.strip()
            if not value or value.startswith("#"):
                continue
            try:
                handle, _ = pipeline.parse_instagram_identity(value)
            except ValueError:
                print("[SEED SKIPPED] Invalid Instagram handle/URL at data/scrape_seeds.txt line.", flush=True)
                continue
            targets.append(Target("profile", handle, "unassigned"))
    unique: dict[tuple[str, str], Target] = {}
    for target in targets:
        unique[(target.kind, target.value.casefold())] = target
    return list(unique.values())


def existing_catalog_handles(client: Any) -> set[str]:
    response = client.table("brands").select("instagram_username").execute()
    return {
        str(row.get("instagram_username") or "").strip().lstrip("@").casefold()
        for row in (response.data or [])
        if row.get("instagram_username")
    }


def eligibility(candidate: Candidate, existing: set[str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if candidate.handle.casefold() in existing:
        reasons.append("already in catalog")
    if candidate.public_profile is False:
        reasons.append("private profile")
    elif candidate.public_profile is None:
        reasons.append("public profile not verified")

    text = " ".join([candidate.bio, *[str(p["caption"]) for p in candidate.posts.values()]]).casefold()
    india_evidence = any(term in text for term in ("india", "indian", "inr", "₹")) or any(
        "india" in source.casefold() for source in candidate.source_tags
    )
    niche_terms = ("indie", "artisan", "handmade", "handcrafted", "handloom", "slow fashion", "small batch", "independent")
    reseller_terms = ("reseller", "multi-brand", "multibrand", "marketplace", "stockist", "authorized dealer")
    own_product_terms = ("our collection", "our products", "handmade", "handcrafted", "designed by", "shop now", "new drop", "made in")
    niche_signal = any(term in text for term in niche_terms)
    if not india_evidence:
        reasons.append("no Indian brand/location evidence")
    if candidate.followers is not None and candidate.followers > 100_000 and not niche_signal:
        reasons.append("not small/niche (over 100k followers, no niche signal)")
    elif candidate.followers is None and not niche_signal:
        reasons.append("small/niche status unverified")
    if any(term in text for term in reseller_terms):
        reasons.append("reseller/marketplace signals")
    elif not any(term in text for term in own_product_terms):
        reasons.append("own-product evidence not found")
    product_posts = sum(
        bool(post["image_url"]) and bool(post["product_signal"])
        for post in candidate.posts.values()
    )
    if product_posts < 6:
        reasons.append(f"only {product_posts} product posts with images observed; 6 required")
    return not reasons, reasons or ["all eligibility gates passed"]


def group_for_candidate(candidate: Candidate) -> str:
    return candidate.groups.most_common(1)[0][0] if candidate.groups else "unassigned"


def candidate_report(candidates: dict[str, Candidate], existing: set[str]) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for handle in sorted(candidates, key=str.casefold):
        candidate = candidates[handle]
        passed, reasons = eligibility(candidate, existing)
        row = {
            "handle": handle,
            "category_group": group_for_candidate(candidate),
            "post_count_seen": candidate.post_count_seen or len(candidate.posts),
            "product_posts_with_images": sum(
                bool(post["image_url"]) and bool(post["product_signal"])
                for post in candidate.posts.values()
            ),
            "pass": passed,
            "why": reasons,
            "public_profile": candidate.public_profile,
            "followers": candidate.followers,
            "bio": candidate.bio[:500],
            "source_tags": sorted(candidate.source_tags),
            "posts": list(candidate.posts.values()),
        }
        report.append(row)
    return report


def print_candidate_table(rows: list[dict[str, Any]]) -> None:
    print("\n| Handle | Category group | Post count seen | Pass/fail | Why |")
    print("|---|---|---:|---|---|")
    for row in rows:
        why = "; ".join(row["why"]).replace("|", "\\|")
        print(
            f"| @{row['handle']} | {row['category_group']} | {row['post_count_seen']} | "
            f"{'PASS' if row['pass'] else 'FAIL'} | {why} |"
        )
    for group in GROUPS:
        count = sum(row["pass"] and row["category_group"] == group for row in rows)
        if count < 10:
            print(f"[DISCOVERY GAP] {group}: found {count} eligible; target is about 10.")


def load_supabase() -> Any:
    # backend.db in this checkout exposes a psycopg pool, not a Supabase client.
    # Use the pipeline's service-role client, which is also used by ingestion helpers.
    return pipeline.get_supabase_client()


def preflight(client: Any) -> int:
    brands = client.table("brands").select("id").execute().data or []
    print(f"[DB CHECK OK] Connected to Supabase. Total brands in DB: {len(brands)}", flush=True)
    return len(brands)


def run_discovery(args: argparse.Namespace) -> None:
    client = load_supabase()
    preflight(client)
    existing = existing_catalog_handles(client)
    targets = seed_targets()
    max_targets = max(1, args.max_targets)
    run_deadline = time.monotonic() + max(1, args.max_minutes) * 60
    candidates: dict[str, Candidate] = {}
    dynamic_profiles: set[str] = set()
    driver = create_driver()
    try:
        targets_seen = 0
        last_visit: list[float | None] = [None]
        for target in targets:
            if targets_seen >= max_targets or time.monotonic() >= run_deadline:
                print("[RUN BUDGET] Target/minute budget reached; remaining targets skipped.", flush=True)
                break
            try:
                scan_target(driver, target, candidates, existing, last_visit, run_deadline)
            except ScrapeAbort as error:
                print(f"[RUN ABORTED] {error}. No further targets will be visited.", flush=True)
                break
            targets_seen += 1
            if target.kind == "hashtag":
                dynamic_profiles.update(
                    handle for handle in candidates if handle not in existing and handle not in {t.value for t in targets if t.kind == "profile"}
                )
        # Verify public status and collect more profile evidence for hashtag-discovered handles.
        for handle in sorted(dynamic_profiles, key=str.casefold):
            if targets_seen >= max_targets or time.monotonic() >= run_deadline:
                break
            target = Target("profile", handle, group_for_candidate(candidates[handle]))
            try:
                scan_target(driver, target, candidates, existing, last_visit, run_deadline)
            except ScrapeAbort as error:
                print(f"[RUN ABORTED] {error}. No further targets will be visited.", flush=True)
                break
            targets_seen += 1
    finally:
        driver.quit()

    rows = candidate_report(candidates, existing)
    output = ROOT / "data" / "scrape_candidates.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "candidates": rows}, indent=2), encoding="utf-8")
    print_candidate_table(rows)
    print(f"\n[DRY RUN COMPLETE] Wrote {len(rows)} candidate evaluations to data/scrape_candidates.json. No database writes were made.")
    print("STOP: review the table and approve the handles you want ingested.")


def run_login_only() -> None:
    """Open Instagram for manual login, then wait until the user closes Chrome."""
    driver = create_driver()
    try:
        try:
            driver.get("https://www.instagram.com/")
        except TimeoutException:
            stop_page_load(driver)
        print(
            "[LOGIN ONLY] Instagram is open in Chrome. Complete login manually, "
            "then close this Chrome window.",
            flush=True,
        )
        while True:
            time.sleep(1)
            try:
                _ = driver.current_url
            except Exception:
                print("[LOGIN ONLY] Chrome window closed.", flush=True)
                return
    except KeyboardInterrupt:
        print("[LOGIN ONLY] Interrupted.", flush=True)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-targets", type=int, default=60)
    parser.add_argument("--max-minutes", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument(
        "--login-only",
        action="store_true",
        help="Open Instagram for manual login and wait until Chrome is closed.",
    )
    parser.add_argument("--ingest-approved", nargs="*", default=None, metavar="HANDLE")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.login_only:
        run_login_only()
        return
    if args.ingest_approved is not None:
        raise SystemExit("Ingestion is gated on the user's explicit approval after the dry-run candidate table.")
    run_discovery(args)


if __name__ == "__main__":
    main()
