import { useEffect, useMemo, useRef, useState } from "react";
import "./index.css";
import "./overrides.css";
import { supabase } from "./lib/supabase";
import { requireEnv } from "./lib/env";
import AuthModal from "./components/AuthModal";

const API_BASE_URL = requireEnv("VITE_API_BASE_URL");
const DOLU_API_URL =
  import.meta.env.VITE_DOLU_API_URL || "http://127.0.0.1:8001";
// The DOLU chat service is not part of this deployment; the chat entry point stays hidden unless VITE_ENABLE_CHAT=true.
const CHAT_ENABLED = import.meta.env.VITE_ENABLE_CHAT === "true";
const STORAGE_KEY = "unfound_guest_state_v1";
const PROFILE_KEY = "unfound_profile_id_v1";
const PROFILE_ID = (() => {
  const existing = localStorage.getItem(PROFILE_KEY);
  if (existing) return existing;
  const created = crypto.randomUUID();
  localStorage.setItem(PROFILE_KEY, created);
  return created;
})();

// Personalization (preferences, saved items, moodboards) requires a real signed-in
// Supabase Auth user: the backing tables have a foreign key to auth.users, so a
// client-generated guest id (PROFILE_ID, used elsewhere for anonymous tracking)
// cannot be used here. Rejects locally with no network call when signed out.
async function personalizationRequest(path = "", options = {}) {
  const { data: { session } } = await supabase.auth.getSession();
  if (!session) throw new Error("Sign in required.");
  const response = await fetch(
    `${API_BASE_URL}/api/personalization/me${path}`,
    {
      ...options,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.access_token}`,
        ...(options.headers || {}),
      },
    },
  );
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Personalization unavailable");
  return response.json();
}

// Identity for search/discovery personalization comes only from the verified access token
// (never from a client-supplied id); signed-out callers just get unpersonalized results.
async function optionalAuthHeaders() {
  const { data: { session } } = await supabase.auth.getSession();
  return session ? { Authorization: `Bearer ${session.access_token}` } : {};
}

// Real Supabase Auth error strings, confirmed live against this project -- never
// surface these (or any other raw Supabase message) to a visitor directly.
const MIN_SIGNUP_PASSWORD_LENGTH = 8;
function mapAuthError(error) {
  const message = (error?.message || "").toLowerCase();
  if (message.includes("invalid login credentials")) return "That email or password is incorrect.";
  if (error?.code === "weak_password" || message.includes("password should be at least"))
    return `Choose a stronger password (at least ${MIN_SIGNUP_PASSWORD_LENGTH} characters).`;
  if (message.includes("email not confirmed")) return "Confirm your email first -- check your inbox for the link we sent.";
  if (message.includes("rate limit")) return "Too many attempts. Wait a moment and try again.";
  return "Something went wrong. Please try again.";
}
function isNetworkError(error) {
  return error instanceof TypeError || /failed to fetch|networkerror/i.test(error?.message || "");
}
const NETWORK_ERROR_MESSAGE = "Couldn't reach the server. Check your connection and try again.";
const GENERIC_ERROR_MESSAGE = "Something went wrong. Please try again.";

function useSupabaseAuth() {
  const [user, setUser] = useState(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setUser(data.session?.user || null);
    });
    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user || null);
    });
    return () => listener.subscription.unsubscribe();
  }, []);

  const onSignIn = async ({ email, password }) => {
    try {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) return { error: mapAuthError(error) };
      return {};
    } catch (error) {
      return { error: isNetworkError(error) ? NETWORK_ERROR_MESSAGE : GENERIC_ERROR_MESSAGE };
    }
  };

  const onSignUp = async ({ email, password }) => {
    try {
      const { data, error } = await supabase.auth.signUp({ email, password });
      if (error) return { error: mapAuthError(error) };
      // Supabase deliberately does not return an error for an already-registered
      // email (anti-enumeration): it returns 200 with a synthetic user whose
      // `identities` array is empty -- confirmed live against this project, the
      // only signal available that no new account was actually created.
      if (data?.user && Array.isArray(data.user.identities) && data.user.identities.length === 0) {
        return { error: "An account with that email already exists -- try signing in instead." };
      }
      if (!data?.session) return { notice: "Check your email to confirm your account." };
      return {};
    } catch (error) {
      return { error: isNetworkError(error) ? NETWORK_ERROR_MESSAGE : GENERIC_ERROR_MESSAGE };
    }
  };

  const onSignOut = async () => {
    await supabase.auth.signOut();
  };

  return { user, onSignIn, onSignUp, onSignOut };
}

const CATEGORY_TREE = [
  {
    name: "Women",
    tone: "sage",
    subs: [
      "Jeans",
      "Tops",
      "Gym Wear",
      "Kurtis",
      "Formal Wear",
      "Party Wear",
      "Co-ord Sets",
      "Skirts & Shorts",
      "Lingerie",
    ],
  },
  {
    name: "Men",
    tone: "lavender",
    subs: [
      "Jeans",
      "T-Shirts",
      "Shirts",
      "Gym Wear",
      "Underwear",
      "Jackets",
      "Formal Wear",
    ],
  },
  {
    name: "Unisex",
    tone: "neutral",
    subs: ["Socks", "Hoodies", "Sneakers", "Bags", "Streetwear", "Accessories"],
  },
  {
    name: "Gifting",
    tone: "blush",
    subs: [
      "Gifts for Her",
      "Gifts for Him",
      "Birthday",
      "Anniversary",
      "Under ₹1000",
      "Premium Gifts",
    ],
  },
  {
    name: "Home Decor",
    tone: "sage",
    subs: [
      "Furniture",
      "Lighting",
      "Wall Decor",
      "Vases",
      "Bedroom",
      "Desk Decor",
    ],
  },
  {
    name: "Gadgets",
    tone: "neutral",
    subs: [
      "Tech Accessories",
      "Audio",
      "Desk Gadgets",
      "Smart Devices",
      "Lifestyle Tech",
    ],
  },
  {
    name: "Watches",
    tone: "lavender",
    subs: ["Minimal", "Luxury", "Vintage", "Smart Watches"],
  },
  {
    name: "Accessories",
    tone: "blush",
    subs: [
      "Jewellery",
      "Bags",
      "Sunglasses",
      "Belts",
      "Hair Accessories",
      "Wallets",
    ],
  },
];

const CATEGORY_IDS = {
  "Women / Jeans": "women-jeans",
  "Women / Tops": "women-tops",
  "Men / Jeans": "men-jeans",
  "Men / Shirts": "men-shirts",
  "Accessories / Jewellery": "accessories-jewellery",
  "Accessories / Bags": "accessories-bags",
  "Home Decor": "home-decor",
};

// Verified against the live catalog (not guessed) -- each returns a healthy
// spread of real results, so they're safe to suggest from a zero-result state.
const EXAMPLE_QUERIES = ["black oversized top", "floral midi dress", "denim jacket", "ethnic kurti"];

// A discovery rail with only 1-2 items reads as broken, not curated -- hide it
// entirely rather than show a near-empty row. Search results are exempt: a
// direct match count of 1-2 is a legitimate answer, not a rail to pad out.
const MIN_RAIL_ITEMS = 3;
const MIN_BRAND_RAIL_ITEMS = 3;

// Backend HTTPException details are already human-written ("Brands are
// temporarily unavailable."). A raw fetch/network failure throws browser-generated
// text instead ("Failed to fetch", "NetworkError when attempting to fetch
// resource.") -- catch that shape specifically and swap in the same copy the
// rest of the app already uses, rather than ever surfacing it verbatim.
function humanizeFetchError(message) {
  const text = String(message || "").trim();
  if (!text || /failed to fetch|networkerror|load failed/i.test(text)) return NETWORK_ERROR_MESSAGE;
  return text;
}
function formatNumber(value) {
  return Number(value || 0).toLocaleString("en-IN");
}
function hasPrice(value) {
  return Boolean(value) && Number(value) > 0;
}
function formatPrice(value) {
  return `₹${Number(value).toLocaleString("en-IN")}`;
}
function percent(value) {
  return value === null || value === undefined
    ? "—"
    : `${Math.round(Number(value) * 100)}%`;
}
// Discovery-feed products (gem-score ranked, never run through any similarity
// computation) carry similarity_score: 0.0 as a placeholder default, not a real
// "0% match" -- never render that. Below ~5% a real similarity score is noise too.
const MEANINGFUL_MATCH_THRESHOLD = 0.05;
function hasMeaningfulMatchScore(value) {
  return value !== null && value !== undefined && Number(value) > MEANINGFUL_MATCH_THRESHOLD;
}
// Search results carry a real similarity_score (0-1). Discovery-feed products instead
// carry personalized_discovery_score (0-100, gem_score-based) -- but that number is
// only genuinely "personalized" when personalization_matched is true; otherwise it's
// plain gem_score with nothing user-specific behind it (true for every signed-out
// visitor, and for signed-in users with no saved-preference overlap), and badging it
// as "X% match" would just move the placeholder-precision problem to a different
// field instead of fixing it. Returns null when there's nothing meaningful to show.
function matchBadgeText(product) {
  if (product.personalization_matched && product.personalized_discovery_score != null) {
    return `${Math.round(product.personalized_discovery_score)}% match`;
  }
  if (hasMeaningfulMatchScore(product.similarity_score)) {
    return `${percent(product.similarity_score)} match`;
  }
  return null;
}
function gemScore(item) {
  return item?.gem_score === null || item?.gem_score === undefined
    ? null
    : Math.round(Number(item.gem_score));
}

function normalizeBrand(row) {
  const name =
    row.name || row.brand_name || row.instagram_username || "Unknown brand";
  return {
    ...row,
    id: row.id || row.brand_id || name,
    name,
    category: row.category || "Independent label",
    followers: Number(row.followers || row.follower_count || 0),
    niche_score: Number(row.niche_score || row.brand_niche_score || 0.65),
    avatar_url: row.image_url || row.profile_picture_url || row.brand_profile_picture_url || "",
    profile_url:
      row.instagram_profile_url ||
      row.profile_url ||
      `https://instagram.com/${name}`,
  };
}

async function fetchJson(path, signal, headers) {
  const response = await fetch(`${API_BASE_URL}${path}`, { signal, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

async function fetchAllBrands(signal) {
  const pageSize = 100;
  const first = await fetchJson(
    `/api/brands?limit=${pageSize}&offset=0`,
    signal,
  );
  const rows = [...(first.brands || [])];
  for (
    let offset = pageSize;
    offset < Number(first.total || rows.length);
    offset += pageSize
  ) {
    const page = await fetchJson(
      `/api/brands?limit=${pageSize}&offset=${offset}`,
      signal,
    );
    rows.push(...(page.brands || []));
  }
  return rows;
}

function normalizeProduct(row) {
  const unusable =
    [
      "BROKEN",
      "EXPIRED_OR_FORBIDDEN",
      "INVALID_URL",
      "RECOVERY_REQUIRED",
      "UNRECOVERABLE",
      "MISSING",
    ].includes(row.image_status) ||
    (row.source === "instagram" &&
      String(row.image_url || "").includes("unsplash.com"));
  return {
    ...row,
    image_url: unusable ? null : row.image_url,
    id: row.id || row.product_url,
    product_name:
      row.product_name || row.item_name || row.name || "Found piece",
    // Server-computed display-layer fallback for a caption-like stored name (see
    // backend/product_titles.py) -- falls back to product_name itself for
    // locally-built rows (e.g. a moodboard image upload) that never went through
    // the API's format_product().
    display_name: row.display_name || row.product_name || row.item_name || row.name || "Found piece",
    name_is_caption_like: Boolean(row.name_is_caption_like),
    caption_preview: row.caption_preview || null,
    brand_name: row.brand_name || row.brand || "Independent label",
    category: row.category || row.category_name || row.subcategory || "Fashion",
    price: Number(row.price) > 0 ? Number(row.price) : null,
    niche_score: Number(row.niche_score || 0.65),
    likes_count: Number(row.likes_count || 0),
    comments_count: Number(row.comments_count || 0),
    similarity_score: row.similarity_score ?? row.similarity ?? null,
    scraped_at: row.scraped_at || row.created_at || "",
  };
}

function useGuestState(signedInUserId) {
  const [state, setState] = useState(() => {
    try {
      return (
        JSON.parse(localStorage.getItem(STORAGE_KEY)) || {
          saved: [],
          boards: [],
          interactions: [],
        }
      );
    } catch {
      return { saved: [], boards: [], interactions: [] };
    }
  });
  useEffect(
    () => localStorage.setItem(STORAGE_KEY, JSON.stringify(state)),
    [state],
  );
  useEffect(() => {
    if (!signedInUserId) return;
    personalizationRequest()
      .then((remote) => setState((current) => ({
        ...current,
        savedProductIds: remote.saved_product_ids || [],
        savedBrandIds: remote.saved_brand_ids || [],
        preferences: remote.preferences || {},
        remoteBoards: remote.moodboards || [],
      })))
      .catch(() => {});
  }, [signedInUserId]);
  const track = (type, target) =>
    setState((current) => ({
      ...current,
      interactions: [
        ...current.interactions.slice(-199),
        {
          type,
          target_id: target?.id,
          category: target?.category,
          at: new Date().toISOString(),
        },
      ],
    }));
  const toggleSave = (product) => {
    const removing = state.saved.some((item) => item.id === product.id);
    setState((current) => ({
      ...current,
      saved: current.saved.some((item) => item.id === product.id)
        ? current.saved.filter((item) => item.id !== product.id)
        : [...current.saved, product],
      interactions: [
        ...current.interactions,
        {
          type: "save",
          target_id: product.id,
          category: product.category,
          at: new Date().toISOString(),
        },
      ],
    }));
    personalizationRequest(`/saved-products/${product.id}`, { method: removing ? "DELETE" : "PUT" }).catch(() => {});
  };
  return [state, setState, track, toggleSave];
}

function Icon({ name }) {
  const paths = {
    search: "M21 21l-4.4-4.4m2.4-5.1a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z",
    heart:
      "M12 20s-8-4.8-8-11a4.5 4.5 0 0 1 8-2.8A4.5 4.5 0 0 1 20 9c0 6.2-8 11-8 11Z",
    camera:
      "M4 8h3l1.5-2h7L17 8h3v11H4V8Zm8 8a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z",
    plus: "M12 5v14M5 12h14",
    close: "m6 6 12 12M18 6 6 18",
  };
  return (
    <svg className="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d={paths[name]} />
    </svg>
  );
}

function SafeImage({ src, alt, kind = "product", entityId = null, ...props }) {
  const [failed, setFailed] = useState(!src);
  useEffect(() => setFailed(!src), [src]);
  if (failed)
    return (
      <span className={`image-fallback ${kind}`}>
        {kind === "brand" ? "UN" : "No image available"}
      </span>
    );
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => {
        setFailed(true);
        if (import.meta.env.DEV)
          console.warn("[UNFOUND IMAGE]", {
            alt,
            entity_id: entityId,
            original_url: src,
            normalized_url: src,
            status: "BROKEN",
          });
      }}
      {...props}
    />
  );
}

function Nav({ page, go, savedCount, user, onSignInClick, onSignOut }) {
  const items = [
    ["discover", "Discover"],
    ["brands", "Brands"],
    ["categories", "Categories"],
    ["moodboards", "Moodboards"],
    ["search", "Search"],
    ["dolu", "Ask DOLU"],
    ["about", "About"],
  ].filter(([id]) => id !== "dolu" || CHAT_ENABLED);
  return (
    <>
      <header className="unfound-nav">
        <button className="wordmark" onClick={() => go("discover")}>
          UNFOUND<span>✦</span>
        </button>
        <nav>
          {items.map(([id, label]) => (
            <button
              key={id}
              className={page === id ? "active" : ""}
              onClick={() => go(id)}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="nav-actions">
          <button onClick={() => go("saved")}>
            ♡ Saved <small>{savedCount}</small>
          </button>
          {user ? (
            <div className="nav-account">
              <button className="profile-dot" onClick={() => go("profile")} title={user.email}>
                {(user.email || "?")[0].toUpperCase()}
              </button>
              <button className="nav-signout" onClick={onSignOut}>Sign out</button>
            </div>
          ) : (
            <button className="nav-signin" onClick={onSignInClick}>Sign in</button>
          )}
        </div>
      </header>
      <nav className="mobile-nav">
        {[
          ["discover", "Discover"],
          ["brands", "Brands"],
          ["search", "Search"],
          ["moodboards", "Boards"],
          ["profile", "Profile"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={page === id ? "active" : ""}
            onClick={() => go(id)}
          >
            {label}
          </button>
        ))}
      </nav>
    </>
  );
}

function Section({ kicker, title, note, children, action }) {
  return (
    <section className="editorial-section">
      <header>
        <div>
          {kicker && <p className="kicker">{kicker}</p>}
          <h2>{title}</h2>
          {note && <p>{note}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

function ProductCard({ product, saved, onSave, onOpen, onBoard }) {
  return (
    <article className="unfound-product">
      <button className="product-image" onClick={() => onOpen(product)}>
        <SafeImage src={product.image_url} alt={product.product_name} entityId={product.id} />
        <i>
          {matchBadgeText(product) || "UNFOUND pick"}
        </i>
      </button>
      <div className="product-copy">
        <p>{product.brand_name}</p>
        <button className="product-title" onClick={() => onOpen(product)}>
          {product.display_name}
        </button>
        {product.name_is_caption_like && product.caption_preview && (
          <p className="product-caption-preview">{product.caption_preview}</p>
        )}
        <div className="product-line">
          {hasPrice(product.price) && <strong>{formatPrice(product.price)}</strong>}
          {gemScore(product) !== null && <span>💎 {gemScore(product)}{product.gem_label ? ` · ${product.gem_label}` : ""}</span>}
        </div>
        <div className="product-tags">
          <span>{product.category}</span>
          <button
            aria-label="Add to moodboard"
            onClick={() => onBoard(product)}
          >
            ＋
          </button>
          <button
            className={saved ? "saved" : ""}
            aria-label="Save"
            onClick={() => onSave(product)}
          >
            {saved ? "♥" : "♡"}
          </button>
        </div>
      </div>
    </article>
  );
}

function ProductRail({ products, state, actions }) {
  return (
    <div className="product-rail">
      {products.map((product) => (
        <ProductCard
          key={product.id}
          product={product}
          saved={state.saved.some((item) => item.id === product.id)}
          onSave={actions.toggleSave}
          onOpen={actions.openProduct}
          onBoard={actions.addToBoard}
        />
      ))}
    </div>
  );
}

// Shared visual language for every "nothing to show" and "something went wrong"
// state in the app, built on the existing .accurate-empty card so new states
// read as part of the same editorial system rather than a bootstrap alert.
function EmptyState({ kicker, title, note, children, tone = "empty" }) {
  return (
    <div className={`accurate-empty${tone === "error" ? " is-error" : ""}`}>
      {kicker && <p className="kicker">{kicker}</p>}
      <h2>{title}</h2>
      {note && <p>{note}</p>}
      {children}
    </div>
  );
}

// .empty-board (dashed border) is the site's established "start something new"
// pattern (first-run saves/boards) -- kept visually distinct from EmptyState's
// solid-border .accurate-empty, which reads as "we looked and found nothing."
function StartState({ glyph, title, note, children }) {
  return (
    <div className="empty-board">
      {glyph && <span>{glyph}</span>}
      <h2>{title}</h2>
      {note && <p>{note}</p>}
      {children}
    </div>
  );
}

function RetryButton({ onRetry, label = "Try again" }) {
  return (
    <button type="button" className="retry-button" onClick={onRetry}>
      {label}
    </button>
  );
}

// A discovery/brand rail with fewer than `min` items vanishes entirely (heading
// included) instead of rendering a near-empty row -- applied uniformly so every
// rail behaves the same way rather than each call site guessing.
function RailSection({ kicker, title, note, products, state, actions, min = MIN_RAIL_ITEMS, action }) {
  if (products.length < min) return null;
  return (
    <Section kicker={kicker} title={title} note={note} action={action}>
      <ProductRail products={products} state={state} actions={actions} />
    </Section>
  );
}

function BrandRailSection({ kicker, title, note, brands, min = MIN_BRAND_RAIL_ITEMS, openBrand }) {
  if (brands.length < min) return null;
  return (
    <Section kicker={kicker} title={title} note={note}>
      <div className="brand-row">
        {brands.slice(0, 5).map((item) => (
          <BrandCard key={item.id} brand={normalizeBrand(item)} count={item.eligible_product_count} onOpen={openBrand} />
        ))}
      </div>
    </Section>
  );
}

function BrandCard({ brand, count = 0, onOpen }) {
  const open = () => onOpen(brand);
  return (
    <article
      className="unfound-brand"
      role="button"
      tabIndex="0"
      onClick={open}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") open();
      }}
    >
      <span className="brand-mark">
        <SafeImage
          src={brand.avatar_url}
          alt={`${brand.name} logo`}
          kind="brand"
          entityId={brand.id}
        />
      </span>
      <span>
        <small>{brand.category}</small>
        <strong>{brand.name}</strong>
        <em>
          {brand.instagram_username ? `@${brand.instagram_username} · ` : ""}
          {formatNumber(brand.followers)} followers · {count} pieces
        </em>
        {brand.profile_url && (
          <a
            href={brand.profile_url}
            target="_blank"
            rel="noreferrer"
            onClick={(event) => event.stopPropagation()}
          >
            Instagram ↗
          </a>
        )}
      </span>
      {gemScore(brand) !== null && <b>💎 {gemScore(brand)}</b>}
    </article>
  );
}

function HeroSearch({ go, setSeedQuery }) {
  const [query, setQuery] = useState("");
  const file = useRef(null);
  const submit = (event) => {
    event.preventDefault();
    setSeedQuery(query);
    go("search");
  };
  return (
    <section className="unfound-hero">
      <div className="hero-editorial">
        <p className="kicker">AI-POWERED DISCOVERY · ISSUE 001</p>
        <h1>
          Find what you
          <br />
          <em>weren’t</em> looking for.
        </h1>
        <p>
          Discover independent brands, hidden gems and pieces that match your
          style.
        </p>
        <form onSubmit={submit}>
          <Icon name="search" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search for anything..."
          />
          <button>Explore</button>
        </form>
        <button className="image-search" onClick={() => file.current?.click()}>
          <Icon name="camera" /> Search with an image
        </button>
        <input
          ref={file}
          hidden
          type="file"
          accept="image/png,image/jpeg"
          onChange={(e) => {
            if (e.target.files?.[0]) {
              window.__unfoundImage = e.target.files[0];
              go("search");
            }
          }}
        />
      </div>
      <div className="hero-paper">
        <div className="paper-image one" />
        <div className="paper-image two" />
        <span className="tape" />
        <p>
          Things worth
          <br />
          stumbling into →
        </p>
        <i>✦</i>
      </div>
    </section>
  );
}

function DiscoverPage({
  brands,
  products,
  loading,
  error,
  onRetry,
  go,
  setSeedQuery,
  state,
  actions,
}) {
  const [discoveryFeeds, setDiscoveryFeeds] = useState({ fresh_drops: [], hidden_gems: [], trending: [], missed: [], new_discoveries: [], stylish_tops: [], modern_ethnic: [], trending_brands: [], emerging_brands: [] });
  const [feedsLoading, setFeedsLoading] = useState(true);
  const [feedsError, setFeedsError] = useState("");
  const [feedsRetryKey, setFeedsRetryKey] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setFeedsLoading(true);
    setFeedsError("");
    optionalAuthHeaders()
      // session_seed: the same per-browser guest id already used for anonymous
      // tracking, sent here purely as a determinism seed (never for a data lookup)
      // so the discover feed is stable across reloads but varies between visitors.
      .then((headers) => fetchJson(`/api/discovery/feeds?session_seed=${encodeURIComponent(PROFILE_ID)}`, controller.signal, headers))
      .then((data) => setDiscoveryFeeds({
        fresh_drops: (data.fresh_drops || []).map(normalizeProduct),
        hidden_gems: (data.hidden_gems || []).map(normalizeProduct),
        trending: (data.trending || []).map(normalizeProduct),
        missed: (data.missed || []).map(normalizeProduct),
        new_discoveries: (data.new_discoveries || []).map(normalizeProduct),
        stylish_tops: (data.stylish_tops || []).map(normalizeProduct),
        modern_ethnic: (data.modern_ethnic || []).map(normalizeProduct),
        trending_brands: data.trending_brands || [],
        emerging_brands: data.emerging_brands || [],
      }))
      .catch((requestError) => {
        if (requestError.name !== "AbortError") setFeedsError(requestError.message || NETWORK_ERROR_MESSAGE);
      })
      .finally(() => setFeedsLoading(false));
    return () => controller.abort();
  }, [feedsRetryKey]);
  const trending = discoveryFeeds.trending;
  const hidden = discoveryFeeds.hidden_gems;
  const preferred = state.interactions
    .map((i) => i.category)
    .filter(Boolean)
    .at(-1);
  // Exact category match only -- a substring-of-last-word match (e.g. "Shirts") would
  // cross genders ("Men Shirts" matching a "Women Shirts" product too), and padding
  // with unfiltered products when the match set was small used to show completely
  // unrelated items (a rail titled "Because you explored Men Shirts" showing a food
  // photo). Never pad with anything that wouldn't itself belong under this label --
  // fall back to the same curated hidden-gems set the no-history case already uses.
  const preferredMatches = preferred
    ? products.filter((p) => p.category === preferred)
    : [];
  const forYou = preferredMatches.length > 0 ? preferredMatches.slice(0, 8) : hidden.slice(0, 8);
  const forYouLabel = preferredMatches.length > 0 ? preferred : null;
  return (
    <div className="page-shell">
      <HeroSearch go={go} setSeedQuery={setSeedQuery} />
      {error && (
        <EmptyState tone="error" title="Your picks couldn't load." note={humanizeFetchError(error)}>
          <RetryButton onRetry={onRetry} />
        </EmptyState>
      )}
      {loading ? (
        <Section kicker="CURATED BY UNFOUND" title="UNFOUND PICKS" note="A small edit of things we think are worth seeing.">
          <div className="loading-block">Curating the edit…</div>
        </Section>
      ) : (
        <RailSection kicker="CURATED BY UNFOUND" title="UNFOUND PICKS" note="A small edit of things we think are worth seeing." products={products.slice(0, 8)} state={state} actions={actions} />
      )}
      {feedsLoading ? (
        <div className="loading-block">Curating your feed…</div>
      ) : feedsError ? (
        <EmptyState tone="error" title="This feed couldn't load." note={humanizeFetchError(feedsError)}>
          <RetryButton onRetry={() => setFeedsRetryKey((key) => key + 1)} />
        </EmptyState>
      ) : (
        <>
          <RailSection kicker="FRESH DROPS" title="Just landed" note="Recent, quality-checked products with stable imagery and trusted metadata." products={discoveryFeeds.fresh_drops.slice(0, 8)} state={state} actions={actions} />
          {hidden.length >= MIN_RAIL_ITEMS && (
            <section className="discovery-split">
              <div>
                <p className="kicker">💎 GEM SPOTLIGHT · HIDDEN GEMS</p>
                <h2>
                  Small labels.
                  <br />
                  Big point of view.
                </h2>
                <p>Independent names making pieces that refuse to blend in.</p>
                <button onClick={() => go("brands")}>Meet the brands →</button>
              </div>
              <ProductRail products={hidden.slice(0, 4)} state={state} actions={actions} />
            </section>
          )}
          <RailSection kicker="🔥 TRENDING NOW" title="Currently in rotation" note="What the UNFOUND crowd keeps coming back to." products={trending.slice(0, 8)} state={state} actions={actions} />
          <RailSection
            kicker="✨ FOR YOU"
            title={forYouLabel ? `Because you explored ${forYouLabel}` : "Start shaping your edit"}
            note="Your feed quietly learns from every save, view and search."
            products={forYou}
            state={state}
            actions={actions}
          />
          <RailSection kicker="THE TOPS EDIT" title="Stylish Tops" note="Going-out, cropped, fitted and less-obvious tops from independent labels." products={discoveryFeeds.stylish_tops.slice(0, 8)} state={state} actions={actions} />
          <RailSection kicker="CONTEMPORARY CRAFT" title="Modern Ethnic" note="Kurtis, co-ords and contemporary ethnic pieces selected from the live catalog." products={discoveryFeeds.modern_ethnic.slice(0, 8)} state={state} actions={actions} />
          <RailSection kicker="👀 YOU MIGHT HAVE MISSED" title="Quietly worth your attention" note="Strong gems receiving less exposure across discovery." products={discoveryFeeds.missed.slice(0, 8)} state={state} actions={actions} />
          <BrandRailSection kicker="✨ EMERGING BRANDS" title="Independent names to know" note="Promising labels ranked by catalog quality, distinctiveness and recent activity." brands={discoveryFeeds.emerging_brands} openBrand={actions.openBrand} />
          <BrandRailSection kicker="TRENDING BRANDS" title="Names gaining attention" note="Ranked by freshness, engagement, catalog depth and niche relevance—not followers alone." brands={discoveryFeeds.trending_brands} openBrand={actions.openBrand} />
        </>
      )}
      <CategoryStrip go={go} />
      {!feedsLoading && !feedsError && (
        <RailSection kicker="🆕 NEW DISCOVERIES" title="Freshly found labels and pieces" note="Recent, quality-checked additions from independent labels." products={discoveryFeeds.new_discoveries.slice(0, 8)} state={state} actions={actions} />
      )}
    </div>
  );
}

function CategoryStrip({ go }) {
  return (
    <Section
      kicker="BROWSE THE INDEX"
      title="Categories"
      note="Pick a lane, then wander."
    >
      <div className="category-index">
        {CATEGORY_TREE.map((cat, index) => (
          <button
            key={cat.name}
            className={cat.tone}
            onClick={() => go("category", { main: cat.name, sub: "" })}
          >
            <small>0{index + 1}</small>
            <strong>{cat.name}</strong>
            <span>{cat.subs.slice(0, 3).join(" · ")}</span>
          </button>
        ))}
      </div>
    </Section>
  );
}

function CategoriesPage({ go }) {
  return (
    <div className="page-shell">
      <header className="page-intro">
        <p className="kicker">THE UNFOUND INDEX</p>
        <h1>Everything, loosely organised.</h1>
        <p>Start specific or follow a category somewhere unexpected.</p>
      </header>
      <div className="category-directory">
        {CATEGORY_TREE.map((cat, i) => (
          <section key={cat.name} className={cat.tone}>
            <small>0{i + 1}</small>
            <h2>{cat.name}</h2>
            <div>
              {cat.subs.map((sub) => (
                <button
                  key={sub}
                  onClick={() => go("category", { main: cat.name, sub })}
                >
                  {sub} <span>↗</span>
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function categorySlug(selection) {
  const exact = {
    "Women / Jeans": "women-jeans",
    "Women / Tops": "women-tops",
    "Women / Kurtis": "women-kurtis",
    "Women / Formal Wear": "women-formal-wear",
    "Women / Co-ord Sets": "women-coord-sets",
    "Women / Lingerie": "women-lingerie",
    "Men / Jeans": "men-jeans",
    "Men / T-Shirts": "men-t-shirts",
    "Men / Shirts": "men-shirts",
    "Men / Jackets": "men-jackets",
    "Men / Formal Wear": "men-formal-wear",
    "Unisex / Sneakers": "unisex-sneakers",
    "Unisex / Streetwear": "unisex-streetwear",
    "Accessories / Jewellery": "accessories-jewellery",
    "Accessories / Bags": "accessories-bags",
    "Accessories / Wallets": "accessories-wallets",
    "Watches / Minimal": "watches-minimal",
  };
  return (
    exact[`${selection.main} / ${selection.sub}`] ||
    selection.main.toLowerCase().replace(/\s+/g, "-")
  );
}
function CategoryPage({
  selection,
  products,
  brands,
  state,
  actions,
  openBrand,
}) {
  const [sort, setSort] = useState("similarity"),
    [visible, setVisible] = useState(16),
    [categoryProducts, setCategoryProducts] = useState([]),
    [categoryBrands, setCategoryBrands] = useState([]),
    [categoryTypes, setCategoryTypes] = useState([]),
    [selectedType, setSelectedType] = useState(""),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [retryKey, setRetryKey] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const slug = categorySlug(selection);
    Promise.all([
      fetch(`${API_BASE_URL}/api/categories/${slug}?limit=100`),
      fetch(`${API_BASE_URL}/api/categories/${slug}/types`),
    ])
      .then(async ([catalogResponse, typesResponse]) => {
        const data = await catalogResponse.json();
        if (!catalogResponse.ok)
          throw new Error(data.detail || "Unable to load this category");
        const typeData = typesResponse.ok ? await typesResponse.json() : { types: [] };
        if (active) {
          setCategoryProducts((data.products || []).map(normalizeProduct));
          setCategoryBrands((data.brands || []).map(normalizeBrand));
          setCategoryTypes(typeData.types || []);
          setSelectedType("");
        }
      })
      .catch((e) => active && setError(e.message || NETWORK_ERROR_MESSAGE))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [selection.main, selection.sub, retryKey]);
  const selectedTypeData = categoryTypes.find((type) => type.slug === selectedType);
  const typeTerms = selectedTypeData?.terms || [selectedType.replaceAll("-", " ")];
  const filtered = categoryProducts.filter((product) => !selectedType ||
    typeTerms.some((term) => `${product.product_name} ${product.description || ""} ${product.subcategory || ""}`.toLowerCase().includes(term))
  ).sort(
    sort === "newest"
      ? (a, b) => String(b.scraped_at).localeCompare(String(a.scraped_at))
      : sort === "popularity"
        ? (a, b) => b.likes_count - a.likes_count
        : (a, b) => gemScore(b) - gemScore(a),
  );
  return (
    <div className="page-shell">
      <header className="category-hero">
        <p>
          {selection.main.toUpperCase()}{selection.sub ? ` / ${selection.sub.toUpperCase()}` : ""}
        </p>
        <h1>{selection.sub || selection.main}</h1>
        <span>Selected pieces, independent labels and less obvious finds.</span>
      </header>
      <div className="filter-bar">
        <button>Price⌄</button>
        <button>Brand⌄</button>
        <button>Niche Score⌄</button>
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="similarity">Similarity</option>
          <option value="popularity">Popularity</option>
          <option value="newest">Newest</option>
        </select>
      </div>
      {categoryTypes.length > 0 && (
        <Section kicker="VISUAL INDEX" title="Shop by Type" note="Real products chosen from this category.">
          <div className="category-type-tiles">
            {categoryTypes.map((type) => (
              <button key={type.slug} className={selectedType === type.slug ? "active" : ""} onClick={() => { setSelectedType(selectedType === type.slug ? "" : type.slug); setVisible(16); }}>
                <span><SafeImage src={type.image_url} alt={type.name} /></span>
                <strong>{type.name}</strong>
                <small>{type.product_count} pieces</small>
              </button>
            ))}
          </div>
        </Section>
      )}
      {loading ? (
        <div className="loading-block">Checking the category archive…</div>
      ) : error ? (
        <EmptyState tone="error" title="Category unavailable" note={humanizeFetchError(error)}>
          <RetryButton onRetry={() => setRetryKey((key) => key + 1)} />
        </EmptyState>
      ) : !filtered.length ? (
        <EmptyState kicker="STILL DISCOVERING" title="No products discovered here yet." note="More gems are being discovered for this category." />
      ) : (
        <>
          <Section
            kicker="FEATURED BRANDS"
            title={`Names to know in ${selection.sub || selection.main}`}
          >
            <div className="brand-row">
              {categoryBrands.map((b) => (
                <BrandCard
                  key={b.id}
                  brand={b}
                  count={filtered.filter((p) => p.brand_id === b.id).length}
                  onOpen={openBrand}
                />
              ))}
            </div>
          </Section>
          <Section kicker="TRENDING PRODUCTS" title="The edit">
            <div className="product-grid-new">
              {filtered.slice(0, visible).map((p) => (
                <ProductCard
                  key={p.id}
                  product={p}
                  saved={state.saved.some((x) => x.id === p.id)}
                  onSave={actions.toggleSave}
                  onOpen={actions.openProduct}
                  onBoard={actions.addToBoard}
                />
              ))}
            </div>
            {visible < filtered.length && (
              <button
                className="load-more"
                onClick={() => setVisible((v) => v + 16)}
              >
                Load more discoveries
              </button>
            )}
          </Section>
        </>
      )}
    </div>
  );
}

function BrandsPage({ brands, products, loading, error, onRetry, openBrand }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const [visible, setVisible] = useState(48);
  const shown = brands.filter(
    (b) =>
      (category === "All" ||
        String(b.category).toLowerCase().includes(category.toLowerCase())) &&
      `${b.name} ${b.instagram_username || ""}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  useEffect(() => setVisible(48), [query, category]);
  return (
    <div className="page-shell">
      <header className="page-intro brands-intro">
        <div>
          <p className="kicker">
            {brands.length
              ? `${brands.length} INDEPENDENT NAMES`
              : "INDEPENDENT NAMES"}
          </p>
          <h1>Brands with something to say.</h1>
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find a brand…"
        />
      </header>
      <div className="brand-filters">
        {["All", "Women", "Men", "Accessories", "Home"].map((c) => (
          <button
            className={category === c ? "active" : ""}
            onClick={() => setCategory(c)}
            key={c}
          >
            {c}
          </button>
        ))}
      </div>
      {loading ? (
        <div className="loading-block">Loading the brand directory…</div>
      ) : error ? (
        <EmptyState tone="error" title="Brands unavailable" note={humanizeFetchError(error)}>
          <RetryButton onRetry={onRetry} />
        </EmptyState>
      ) : !shown.length ? (
        <EmptyState title="No brands found." note="Try another name or category." />
      ) : (
        <>
          <div className="brand-directory">
            {shown.slice(0, visible).map((brand) => (
              <BrandCard
                key={brand.id}
                brand={brand}
                count={products.filter((p) => p.brand_id === brand.id).length}
                onOpen={openBrand}
              />
            ))}
          </div>
          {visible < shown.length && (
            <button
              className="load-more"
              onClick={() => setVisible((current) => current + 48)}
            >
              Load more brands
            </button>
          )}
        </>
      )}
    </div>
  );
}

function BrandProfile({ brand, products, brands, actions, state, back }) {
  const [remote, setRemote] = useState([]);
  const [similar, setSimilar] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);
  useEffect(() => {
    if (!brand?.id) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    Promise.all([
      fetchJson(`/api/brands/${brand.id}/products`, controller.signal),
      fetchJson(`/api/brands/${brand.id}/similar`, controller.signal),
    ])
      .then(([productData, similarData]) => {
        setRemote((productData.products || []).map(normalizeProduct));
        setSimilar((similarData.brands || []).map(normalizeBrand));
      })
      .catch((requestError) => {
        if (requestError.name !== "AbortError") setError(requestError.message || NETWORK_ERROR_MESSAGE);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [brand?.id, retryKey]);
  const pieces = remote;
  const cats = [...new Set(pieces.map((p) => p.category).filter(Boolean))];
  return (
    <div className="page-shell">
      <button className="back-link" onClick={back}>
        ← All brands
      </button>
      <header className="brand-profile-head">
        <span className="brand-mark large">
          <SafeImage
            src={brand.avatar_url}
            alt={`${brand.name} logo`}
            kind="brand"
          />
        </span>
        <div>
          <p className="kicker">UNFOUND BRAND PROFILE</p>
          <h1>{brand.name}</h1>
          <p>
            Independent label with a distinct point of view and pieces worth
            discovering slowly.
          </p>
          <div className="profile-facts">
            {gemScore(brand) !== null && <b>💎 {gemScore(brand)} {brand.gem_label || ""}</b>}
            <span>{formatNumber(brand.followers)} followers</span>
            <button type="button" onClick={() => actions.toggleBrandSave(brand)}>
              {(state.savedBrandIds || []).includes(brand.id) ? "♥ Saved brand" : "♡ Save brand"}
            </button>
            {brand.profile_url && (
              <a href={brand.profile_url} target="_blank" rel="noreferrer">
                Visit Instagram ↗
              </a>
            )}
          </div>
        </div>
      </header>
      <Section
        title="Products"
        note={
          loading ? "Loading real products…" : `${pieces.length} pieces found`
        }
      >
        {loading ? (
          <div className="loading-block">Loading this brand’s products…</div>
        ) : error ? (
          <EmptyState tone="error" title="Products unavailable" note={humanizeFetchError(error)}>
            <RetryButton onRetry={() => setRetryKey((key) => key + 1)} />
          </EmptyState>
        ) : pieces.length ? (
          <ProductRail
            products={pieces.slice(0, 12)}
            state={state}
            actions={actions}
          />
        ) : (
          <EmptyState title="No products stored for this brand yet." note="The catalog returned no products linked to this brand ID." />
        )}
      </Section>
      <Section kicker="SHOP BY CATEGORY" title="Their world">
        <div className="text-chips">
          {cats.length ? (
            cats.map((c) => <span key={c}>{c}</span>)
          ) : (
            <span>{brand.category}</span>
          )}
        </div>
      </Section>
      <section className="why-unfound">
        <p className="kicker">WHY UNFOUND RECOMMENDS THIS BRAND</p>
        <h2>
          “A smaller independent label with a strong visual identity and high
          relevance to your emerging style profile.”
        </h2>
      </section>
      <Section title="Similar brands">
        <div className="brand-row">
          {similar.slice(0, 5).map((b) => (
            <BrandCard key={b.id} brand={b} onOpen={actions.openBrand} />
          ))}
        </div>
      </Section>
    </div>
  );
}

// Used to work out which single filter is responsible for a zero-result search
// (probed by re-running the query with just that one filter removed) and to
// power the "clear filters" action -- key must match the matching useState name.
const SEARCH_FILTER_FIELDS = [
  { key: "category", formKey: "category_slug", label: "The category filter" },
  { key: "gender", formKey: "gender", label: "The gender filter" },
  { key: "minPrice", formKey: "min_price", label: "The minimum price filter" },
  { key: "maxPrice", formKey: "max_price", label: "The maximum price filter" },
  { key: "minNiche", formKey: "min_niche_score", label: "The minimum niche-score filter" },
];

function SearchPage({ seedQuery, state, actions, track }) {
  const [query, setQuery] = useState(seedQuery || "");
  const [image, setImage] = useState(() => window.__unfoundImage || null);
  const [results, setResults] = useState([]);
  const [bestMatches, setBestMatches] = useState([]);
  const [moreLikeThis, setMoreLikeThis] = useState([]);
  const [brandResults, setBrandResults] = useState([]);
  const [moreLikeLabel, setMoreLikeLabel] = useState("More Like This");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [blockingFilter, setBlockingFilter] = useState(null);
  const [searchedWithImage, setSearchedWithImage] = useState(false);
  const [category, setCategory] = useState("");
  const [gender, setGender] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [minNiche, setMinNiche] = useState("");
  const [sort, setSort] = useState("");
  const file = useRef(null);
  const request = useRef(null);
  const filterValues = { category, gender, minPrice, maxPrice, minNiche };
  const activeFilters = SEARCH_FILTER_FIELDS.filter((f) => filterValues[f.key]);
  function buildSearchForm(queryText, imageFile, values, overrideKey) {
    const body = new FormData();
    if (imageFile) body.append("image_file", imageFile);
    else body.append("text_query", (queryText || "").trim());
    for (const f of SEARCH_FILTER_FIELDS) {
      if (f.key !== overrideKey && values[f.key]) body.append(f.formKey, values[f.key]);
    }
    if (sort) body.append("sort_by", sort);
    return body;
  }
  async function runDiscover(body, signal) {
    const response = await fetch(`${API_BASE_URL}/api/discover`, {
      method: "POST",
      headers: await optionalAuthHeaders(),
      body,
      signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Search failed");
    return data;
  }
  // Runs a search using explicit values rather than reading component state,
  // so a caller that just called setState (clearFilters, an example-query chip)
  // doesn't race React's async state update and search on stale filters/query.
  async function runSearch(queryText, imageFile, values) {
    if (!String(queryText || "").trim() && !imageFile) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError("");
    setBlockingFilter(null);
    setSearchedWithImage(Boolean(imageFile));
    const activeNow = SEARCH_FILTER_FIELDS.filter((f) => values[f.key]);
    try {
      const data = await runDiscover(buildSearchForm(queryText, imageFile, values), controller.signal);
      const resultRows = data.results || [];
      setResults(resultRows.map(normalizeProduct));
      setBestMatches(
        (data.best_matches || resultRows.slice(0, 8)).map(normalizeProduct),
      );
      setMoreLikeThis(
        (data.more_like_this || resultRows.slice(8)).map(normalizeProduct),
      );
      setMoreLikeLabel(data.more_like_this_label || "More Like This");
      setBrandResults((data.similar_brands || []).map(normalizeBrand));
      track("search", { id: queryText || "image", category: values.category });
      // Zero results with active filters: find which single filter, if removed,
      // would unblock matches -- so the empty state can name it instead of
      // just suggesting "remove a filter" and leaving the guessing to the user.
      if (!resultRows.length && activeNow.length) {
        for (const f of activeNow) {
          try {
            const probe = await runDiscover(buildSearchForm(queryText, imageFile, values, f.key), controller.signal);
            if ((probe.results || []).length > 0) {
              setBlockingFilter(f.label);
              break;
            }
          } catch {
            // A probe failure just means we can't name the filter -- not worth
            // surfacing as a second error on top of a successful main search.
          }
        }
      }
    } catch (searchError) {
      if (searchError.name !== "AbortError") setError(searchError.message || NETWORK_ERROR_MESSAGE);
    } finally {
      if (request.current === controller) setLoading(false);
    }
  }
  function search(event) {
    event?.preventDefault();
    runSearch(query, image, filterValues);
  }
  function clearFilters() {
    const cleared = { category: "", gender: "", minPrice: "", maxPrice: "", minNiche: "" };
    setCategory("");
    setGender("");
    setMinPrice("");
    setMaxPrice("");
    setMinNiche("");
    runSearch(query, image, cleared);
  }
  function runExampleQuery(example) {
    setQuery(example);
    setImage(null);
    runSearch(example, null, filterValues);
  }
  useEffect(() => {
    if (seedQuery || image) search();
    return () => request.current?.abort();
  }, []);
  return (
    <div className="page-shell">
      <header className="page-intro">
        <p className="kicker">MULTIMODAL SEARCH</p>
        <h1>Describe it. Show it. Find it.</h1>
        <p>
          Try “quiet luxury under ₹5000” or upload something from your camera
          roll.
        </p>
      </header>
      <form className="unfound-search" onSubmit={search}>
        <Icon name="search" />
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setImage(null);
          }}
          placeholder="Search by name, keyword or description..."
        />
        <button type="button" onClick={() => file.current?.click()}>
          <Icon name="camera" />
          {image ? image.name : "Image"}
        </button>
        <input
          ref={file}
          hidden
          type="file"
          accept="image/png,image/jpeg"
          onChange={(e) => {
            setImage(e.target.files?.[0] || null);
            setQuery("");
          }}
        />
        <button disabled={loading}>{loading ? "Looking…" : "Search"}</button>
      </form>
      <div className="search-filters">
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {Object.entries(CATEGORY_IDS).map(([label, id]) => (
            <option value={id} key={label}>
              {label}
            </option>
          ))}
        </select>
        <select value={gender} onChange={(e) => setGender(e.target.value)}>
          <option value="">All genders</option>
          <option value="women">Women</option>
          <option value="men">Men</option>
          <option value="unisex">Unisex</option>
        </select>
        <input
          type="number"
          placeholder="Min ₹"
          value={minPrice}
          onChange={(e) => setMinPrice(e.target.value)}
        />
        <input
          type="number"
          placeholder="Max ₹"
          value={maxPrice}
          onChange={(e) => setMaxPrice(e.target.value)}
        />
        <input
          type="number"
          min="0"
          max="1"
          step="0.05"
          placeholder="Min niche"
          value={minNiche}
          onChange={(e) => setMinNiche(e.target.value)}
        />
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="">Similarity</option>
          <option value="price_asc">Price: low to high</option>
          <option value="price_desc">Price: high to low</option>
          <option value="niche_score">Niche score</option>
        </select>
        <button type="button" onClick={search}>
          Apply filters
        </button>
      </div>
      <Section
        kicker="PRODUCTS"
        title={
          loading
            ? "Searching the archive…"
            : results.length
              ? `${results.length} things found`
              : error
                ? "Search unavailable"
                : (query || image)
                  ? "No matches found"
                  : "Your results will live here"
        }
      >
        {!loading && error && (
          <EmptyState tone="error" title="Search unavailable" note={humanizeFetchError(error)}>
            <RetryButton onRetry={() => search()} />
          </EmptyState>
        )}
        {!loading && !error && !results.length && (query || image) && (
          <EmptyState
            title={searchedWithImage ? "Nothing looked similar enough to that image." : "No matching products found."}
            note={
              blockingFilter
                ? `${blockingFilter} is narrowing this down to zero — clear it to see matches.`
                : searchedWithImage
                  ? "Try a clearer or more zoomed-in photo, or describe the piece in words instead."
                  : activeFilters.length
                    ? "Try a broader description, or clear your filters."
                    : "Try a broader description, or one of these:"
            }
          >
            {activeFilters.length > 0 && <RetryButton onRetry={clearFilters} label="Clear filters" />}
            {!searchedWithImage && !blockingFilter && !activeFilters.length && (
              <div className="example-query-chips">
                {EXAMPLE_QUERIES.map((example) => (
                  <button key={example} type="button" onClick={() => runExampleQuery(example)}>
                    {example}
                  </button>
                ))}
              </div>
            )}
          </EmptyState>
        )}
      </Section>
      {bestMatches.length > 0 && (
        <Section kicker="HIGHEST CONFIDENCE" title="Best Matches">
          <div className="product-grid-new">
            {bestMatches.map((p) => (
              <ProductCard
                key={p.id}
                product={p}
                saved={state.saved.some((x) => x.id === p.id)}
                onSave={actions.toggleSave}
                onOpen={actions.openProduct}
                onBoard={actions.addToBoard}
              />
            ))}
          </div>
        </Section>
      )}
      {moreLikeThis.length > 0 && (
        <Section kicker="BROADER DISCOVERIES" title={moreLikeLabel}>
          <div className="product-grid-new">
            {moreLikeThis.map((p) => (
              <ProductCard
                key={p.id}
                product={p}
                saved={state.saved.some((x) => x.id === p.id)}
                onSave={actions.toggleSave}
                onOpen={actions.openProduct}
                onBoard={actions.addToBoard}
              />
            ))}
          </div>
        </Section>
      )}
      {brandResults.length > 0 && (
        <Section kicker="SIMILAR BRANDS" title="Names in your orbit">
          <div className="brand-row">
            {brandResults.slice(0, 5).map((b) => (
              <BrandCard key={b.id} brand={b} onOpen={actions.openBrand} />
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

function DoluPage({ state, actions }) {
  const [messages, setMessages] = useState([
    { role: "dolu", text: "What are we finding today? Give me a product, an occasion, or a mood." },
  ]);
  const [input, setInput] = useState("");
  const [image, setImage] = useState(null);
  const [loading, setLoading] = useState(false);
  const upload = useRef(null);
  const send = async (event, suggestion) => {
    event?.preventDefault();
    const text = (suggestion || input).trim();
    if ((!text && !image) || loading) return;
    setMessages((rows) => [...rows, { role: "user", text: text || "Find something like this image" }]);
    setInput(""); setLoading(true);
    try {
      let image_base64 = null;
      if (image) image_base64 = await new Promise((resolve, reject) => {
        const reader = new FileReader(); reader.onerror = reject;
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.readAsDataURL(image);
      });
      const response = await fetch(`${DOLU_API_URL}/api/dolu/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text || "Find something like this image", profile_id: PROFILE_ID, image_base64, image_type: image?.type || "image/jpeg" }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "DOLU is unavailable");
      setMessages((rows) => [...rows, {
        role: "dolu", text: data.message,
        products: (data.products || []).map(normalizeProduct),
        brands: (data.brands || []).map(normalizeBrand),
      }]);
      setImage(null);
    } catch (error) {
      setMessages((rows) => [...rows, { role: "dolu", text: error.message }]);
    } finally { setLoading(false); }
  };
  const suggestions = ["Find minimalist silver jewellery under ₹2000", "Style me for a college party", "Show niche Indian jewellery brands", "Create a summer moodboard"];
  return <div className="page-shell dolu-page">
    <header className="page-intro"><div><p className="kicker">YOUR DISCOVERY STYLIST</p><h1>Ask DOLU ✦</h1><p>Real pieces, real independent brands, shaped around what you save.</p></div></header>
    <div className="dolu-suggestions">{suggestions.map((text) => <button key={text} onClick={() => send(null, text)}>{text}</button>)}</div>
    <section className="dolu-thread">
      {messages.map((message, index) => <article key={index} className={`dolu-bubble ${message.role}`}>
        <p>{message.text}</p>
        {!!message.products?.length && <div className="product-grid-new">{message.products.slice(0, 6).map((product) => <ProductCard key={product.id} product={product} saved={state.saved.some((item) => item.id === product.id)} onSave={actions.toggleSave} onOpen={actions.openProduct} onBoard={actions.addToBoard} />)}</div>}
        {!!message.brands?.length && <div className="brand-row">{message.brands.slice(0, 5).map((brand) => <BrandCard key={brand.id} brand={brand} onOpen={actions.openBrand} />)}</div>}
      </article>)}
      {loading && <article className="dolu-bubble dolu"><p>DOLU is looking through UNFOUND…</p></article>}
    </section>
    <form className="dolu-composer" onSubmit={send}>
      <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="Ask DOLU anything…" />
      <button type="button" onClick={() => upload.current?.click()}>{image ? image.name : "Image"}</button>
      <input hidden ref={upload} type="file" accept="image/png,image/jpeg" onChange={(event) => setImage(event.target.files?.[0] || null)} />
      <button disabled={loading}>Send ↑</button>
    </form>
  </div>;
}

function MoodboardsPage({ state, setState, products, actions }) {
  const [title, setTitle] = useState("");
  const [active, setActive] = useState(null);
  const upload = useRef(null);
  const board = state.boards.find((b) => b.id === active);
  const create = () => {
    if (!title.trim()) return;
    const next = {
      id: crypto.randomUUID(),
      title: title.trim(),
      items: [],
      created_at: new Date().toISOString(),
    };
    setState((s) => ({ ...s, boards: [...s.boards, next] }));
    personalizationRequest("/moodboards", {
      method: "POST",
      body: JSON.stringify({ id: next.id, name: next.title, description: "" }),
    }).catch(() => {});
    setTitle("");
    setActive(next.id);
  };
  const removeBoard = (id) => {
    setState((s) => ({ ...s, boards: s.boards.filter((b) => b.id !== id) }));
    setActive(null);
    personalizationRequest(`/moodboards/${id}`, { method: "DELETE" }).catch(() => {});
  };
  const removeItem = (id) => {
    setState((s) => ({
      ...s,
      boards: s.boards.map((b) =>
        b.id === active
          ? { ...b, items: b.items.filter((p) => p.id !== id) }
          : b,
      ),
    }));
    personalizationRequest(`/moodboards/${active}/products`, {
      method: "DELETE",
      body: JSON.stringify({ item_id: id }),
    }).catch(() => {});
  };
  const setCover = (id) => {
    setState((s) => ({
      ...s,
      boards: s.boards.map((b) =>
        b.id === active ? { ...b, cover_id: id } : b,
      ),
    }));
    const product = board?.items.find((item) => item.id === id);
    personalizationRequest(`/moodboards/${active}`, {
      method: "PATCH",
      body: JSON.stringify({ cover_image_url: product?.image_url || null }),
    }).catch(() => {});
  };
  const addUpload = (file) => {
    if (!file || !active) return;
    const reader = new FileReader();
    reader.onload = () =>
      setState((s) => ({
        ...s,
        boards: s.boards.map((b) =>
          b.id === active
            ? {
                ...b,
                items: [
                  ...b.items,
                  {
                    id: crypto.randomUUID(),
                    product_name: "Visual inspiration",
                    brand_name: "Your upload",
                    category: "Mood reference",
                    image_url: reader.result,
                    uploaded: true,
                  },
                ],
              }
            : b,
        ),
      }));
    reader.readAsDataURL(file);
  };
  const mood = board?.items.some((p) =>
    /black|silver|minimal/i.test(`${p.product_name} ${p.category}`),
  )
    ? "Minimal / Monochrome / Smart Casual"
    : "Independent / Eclectic / Everyday";
  return (
    <div className="page-shell">
      <header className="page-intro mood-intro">
        <div>
          <p className="kicker">VISUAL COLLECTIONS</p>
          <h1>Moodboards</h1>
          <p>Collect a feeling before you know what to call it.</p>
        </div>
        <div className="create-board">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Summer in Goa"
          />
          <button onClick={create}>Create board</button>
        </div>
      </header>
      {!state.boards.length ? (
        <StartState
          glyph="✦"
          title="Your first mood starts here."
          note="Moodboards collect pieces you're drawn to into one visual collection. Save products from anywhere in UNFOUND, or create a board above, then arrange the feeling."
        />
      ) : (
        <div className="mood-layout">
          <aside>
            {state.boards.map((b) => (
              <button
                className={active === b.id ? "active" : ""}
                onClick={() => setActive(b.id)}
                key={b.id}
              >
                <span>
                  {[...b.items]
                    .sort((a) => (a.id === b.cover_id ? -1 : 1))
                    .slice(0, 4)
                    .map((p) => (
                      <img key={p.id} src={p.image_url} alt="" />
                    ))}
                </span>
                <strong>{b.title}</strong>
                <small>{b.items.length} finds</small>
              </button>
            ))}
          </aside>
          {board && (
            <section className="board-detail">
              <header>
                <div>
                  <p className="kicker">YOUR MOODBOARD</p>
                  <input
                    value={board.title}
                    onChange={(e) =>
                      setState((s) => ({
                        ...s,
                        boards: s.boards.map((b) =>
                          b.id === board.id
                            ? { ...b, title: e.target.value }
                            : b,
                        ),
                      }))
                    }
                    onBlur={(e) =>
                      personalizationRequest(`/moodboards/${board.id}`, {
                        method: "PATCH",
                        body: JSON.stringify({ name: e.target.value.trim() || "Untitled mood" }),
                      }).catch(() => {})
                    }
                  />
                </div>
                <div>
                  <button onClick={() => upload.current?.click()}>
                    Upload image
                  </button>
                  <input
                    ref={upload}
                    hidden
                    type="file"
                    accept="image/*"
                    onChange={(e) => addUpload(e.target.files?.[0])}
                  />
                  <button
                    onClick={() =>
                      navigator.clipboard?.writeText(location.href)
                    }
                  >
                    Share
                  </button>
                  <button onClick={() => removeBoard(board.id)}>Delete</button>
                </div>
              </header>
              {board.items.length === 0 ? (
                <StartState
                  glyph="✦"
                  title="This board is empty."
                  note="Add the heart on any product, or use “+” from a product card, to start collecting the feeling here."
                />
              ) : (
                <>
                  <div className="board-collage">
                    {board.items.map((p) => (
                      <article
                        className={p.id === board.cover_id ? "cover-item" : ""}
                        key={p.id}
                      >
                        <img src={p.image_url} alt={p.product_name} />
                        <button onClick={() => removeItem(p.id)}>×</button>
                        <button
                          className="cover-button"
                          onClick={() => setCover(p.id)}
                        >
                          {p.id === board.cover_id ? "Cover" : "Set cover"}
                        </button>
                      </article>
                    ))}
                  </div>
                  <div className="mood-detected">
                    <small>MOOD DETECTED</small>
                    <h3>{mood}</h3>
                    <p>
                      UNFOUND reads the common visual language across your saved
                      pieces.
                    </p>
                  </div>
                </>
              )}
              <Section kicker="COMPLETE THE MOOD" title="A few things to add">
                <ProductRail
                  products={products
                    .filter((p) => !board.items.some((x) => x.id === p.id))
                    .slice(0, 6)}
                  state={state}
                  actions={actions}
                />
              </Section>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function ProductDetail({ product, products, state, actions, close }) {
  const related = products
    .filter(
      (p) =>
        p.id !== product.id &&
        (p.category === product.category || p.brand_id === product.brand_id),
    )
    .slice(0, 6);
  return (
    <div
      className="detail-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <article className="product-detail">
        <button className="detail-close" onClick={close}>
          <Icon name="close" />
        </button>
        <div className="detail-image">
          {product.image_url && (
            <img src={product.image_url} alt={product.product_name} />
          )}
        </div>
        <div className="detail-copy">
          <p className="kicker">UNFOUND PRODUCT</p>
          <h1>{product.display_name}</h1>
          {product.name_is_caption_like && product.caption_preview && (
            <p className="product-caption-preview">{product.caption_preview}</p>
          )}
          <h3>{product.brand_name}</h3>
          <div className="score-row">
            {gemScore(product) !== null && <b>💎 {gemScore(product)} {product.gem_label || ""}</b>}
            {matchBadgeText(product) && <span>{matchBadgeText(product)}</span>}
          </div>
          <p>
            {product.description ||
              "A distinct find from an independent label in the UNFOUND archive."}
          </p>
          <dl>
            {hasPrice(product.price) && (
              <div>
                <dt>Price</dt>
                <dd>{formatPrice(product.price)}</dd>
              </div>
            )}
            <div>
              <dt>Category</dt>
              <dd>{product.category}</dd>
            </div>
          </dl>
          <div className="detail-actions">
            <a href={product.product_url} target="_blank" rel="noreferrer">
              Visit brand / shop ↗
            </a>
            <button onClick={() => actions.toggleSave(product)}>
              {state.saved.some((p) => p.id === product.id)
                ? "Saved ♥"
                : "Save ♡"}
            </button>
            <button onClick={() => actions.addToBoard(product)}>
              Add to moodboard ＋
            </button>
          </div>
        </div>
        <section>
          <Section kicker="YOU MAY ALSO LIKE" title="Keep looking">
            <ProductRail products={related} state={state} actions={actions} />
          </Section>
        </section>
      </article>
    </div>
  );
}

function SavedPage({ state, actions }) {
  return (
    <div className="page-shell">
      <header className="page-intro">
        <p className="kicker">YOUR ARCHIVE</p>
        <h1>Saved, for later.</h1>
        <p>The pieces you wanted to find again.</p>
      </header>
      <div className="product-grid-new">
        {state.saved.map((p) => (
          <ProductCard
            key={p.id}
            product={p}
            saved
            onSave={actions.toggleSave}
            onOpen={actions.openProduct}
            onBoard={actions.addToBoard}
          />
        ))}
      </div>
      {!state.saved.length && (
        <StartState
          glyph="♡"
          title="Nothing saved yet."
          note="Tap the ♡ on any product across search, discover or a brand page, and it collects here so you can find it again."
        />
      )}
    </div>
  );
}

function ProfilePage({ state, auth, openAuthModal }) {
  const counts = state.interactions.reduce((a, i) => {
    if (i.category) a[i.category] = (a[i.category] || 0) + 1;
    return a;
  }, {});
  const styles = [
    ["Minimal", 82],
    ["Streetwear", 61],
    ["Elegant", 74],
    ["Vintage", 42],
    ["Experimental", 58],
  ];
  return (
    <div className="page-shell">
      <div className="account-strip">
        {auth.user ? (
          <>
            <div>
              <p className="kicker">SIGNED IN</p>
              <h2>{auth.user.email}</h2>
            </div>
            <button type="button" onClick={auth.onSignOut}>Sign out</button>
          </>
        ) : (
          <>
            <div>
              <p className="kicker">GUEST BROWSING</p>
              <h2>Sign in to see your saved edit.</h2>
            </div>
            <button type="button" onClick={() => openAuthModal()}>Sign in</button>
          </>
        )}
      </div>
      <header className="page-intro">
        <p className="kicker">YOUR UNFOUND STYLE</p>
        <h1>A profile built quietly.</h1>
        <p>No quiz. Just the things you look at, love and save.</p>
      </header>
      <div className="style-profile">
        {styles.map(([name, value]) => (
          <div key={name}>
            <span>{name}</span>
            <i>
              <b style={{ width: `${value}%` }} />
            </i>
            <strong>{value}%</strong>
          </div>
        ))}
      </div>
      <Section
        title="Your signals"
        note={`${state.interactions.length} interactions shaping your edit`}
      >
        {state.interactions.length ? (
          <div className="text-chips">
            {Object.entries(counts)
              .slice(0, 8)
              .map(([name, count]) => (
                <span key={name}>
                  {name} · {count}
                </span>
              ))}
          </div>
        ) : (
          <StartState
            glyph="✦"
            title="No signals yet."
            note="Browse, search and save pieces you like -- this quietly fills in from there."
          />
        )}
      </Section>
    </div>
  );
}

function AboutPage() {
  return (
    <div className="page-shell">
      <section className="about-unfound">
        <p className="kicker">ABOUT UNFOUND</p>
        <h1>Not another marketplace.</h1>
        <p>
          UNFOUND is an AI-powered fashion and lifestyle magazine you can
          search. We use visual intelligence to connect you with independent
          brands, unexpected objects and pieces that feel like you—even before
          you know the words.
        </p>
        <blockquote>Find what you weren’t looking for.</blockquote>
      </section>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState("discover"),
    [payload, setPayload] = useState(null),
    [brands, setBrands] = useState([]),
    [products, setProducts] = useState([]),
    [loading, setLoading] = useState(true),
    [catalogError, setCatalogError] = useState(""),
    [catalogRetryKey, setCatalogRetryKey] = useState(0),
    [selectedProduct, setSelectedProduct] = useState(null),
    [seedQuery, setSeedQuery] = useState(""),
    [authModalOpen, setAuthModalOpen] = useState(false),
    [pendingAuthAction, setPendingAuthAction] = useState(null);
  const auth = useSupabaseAuth();
  const [state, setState, track, toggleSave] = useGuestState(auth.user?.id);
  const wasSignedIn = useRef(Boolean(auth.user));
  // Runs whichever action (e.g. a save) triggered the sign-in prompt, once the user
  // actually becomes signed in -- and only then, never on the initial session check.
  useEffect(() => {
    if (auth.user && !wasSignedIn.current) {
      setAuthModalOpen(false);
      pendingAuthAction?.();
      setPendingAuthAction(null);
    }
    wasSignedIn.current = Boolean(auth.user);
  }, [auth.user, pendingAuthAction]);
  const openAuthModal = (action = null) => {
    setPendingAuthAction(() => action);
    setAuthModalOpen(true);
  };
  const closeAuthModal = () => {
    setAuthModalOpen(false);
    setPendingAuthAction(null);
  };
  const requireAuth = (fn) => (...args) => {
    if (!auth.user) {
      openAuthModal(() => fn(...args));
      return;
    }
    return fn(...args);
  };
  useEffect(() => {
    if (!products.length) return;
    setState((current) => {
      const savedIds = new Set(current.savedProductIds || []);
      const remoteBoards = current.remoteBoards || [];
      if (!savedIds.size && !remoteBoards.length) return current;
      const productsById = new Map(products.map((product) => [product.id, product]));
      return {
        ...current,
        saved: savedIds.size
          ? [...savedIds].map((id) => productsById.get(id)).filter(Boolean)
          : current.saved,
        boards: remoteBoards.length
          ? remoteBoards.map((board) => ({
              id: board.id,
              title: board.title,
              cover_image_url: board.cover_image_url,
              items: (board.items || []).map((item) => productsById.get(item.product_id)).filter(Boolean),
              created_at: board.created_at,
            }))
          : current.boards,
        remoteBoards: [],
      };
    });
  }, [products, setState]);
  useEffect(() => {
    const handleImageError = (event) => {
      if (event.target?.tagName !== "IMG") return;
      event.target.style.visibility = "hidden";
      if (import.meta.env.DEV)
        console.warn("[UNFOUND IMAGE]", {
          original_url: event.target.currentSrc || event.target.src,
          status: "BROKEN",
        });
    };
    document.addEventListener("error", handleImageError, true);
    return () => document.removeEventListener("error", handleImageError, true);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setCatalogError("");
    Promise.all([
      fetchAllBrands(controller.signal),
      fetchJson("/api/products?limit=64&offset=0", controller.signal),
    ])
      .then(([brandRows, productData]) => {
        setBrands(brandRows.map(normalizeBrand));
        setProducts((productData.products || []).map(normalizeProduct));
      })
      .catch((requestError) => {
        if (requestError.name !== "AbortError")
          setCatalogError(requestError.message || NETWORK_ERROR_MESSAGE);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [catalogRetryKey]);
  const retryCatalog = () => setCatalogRetryKey((key) => key + 1);
  const go = (next, data = null) => {
    setPage(next);
    setPayload(data);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const openBrand = (brand) => {
    track("brand_view", brand);
    go("brand", brand);
  };
  const addToBoard = (product) => {
    if (!state.boards.length) {
      const boardId = crypto.randomUUID();
      setState((s) => ({
        ...s,
        boards: [
          {
            id: boardId,
            title: "My first mood",
            items: [product],
            created_at: new Date().toISOString(),
          },
        ],
      }));
      personalizationRequest("/moodboards", {
        method: "POST",
        body: JSON.stringify({ id: boardId, name: "My first mood", description: "" }),
      })
        .then(() => personalizationRequest(`/moodboards/${boardId}/products`, {
          method: "PUT",
          body: JSON.stringify({ item_id: product.id }),
        }))
        .catch(() => {});
      go("moodboards");
      return;
    }
    const id = state.boards[0].id;
    setState((s) => ({
      ...s,
      boards: s.boards.map((b) =>
        b.id === id && !b.items.some((p) => p.id === product.id)
          ? { ...b, items: [...b.items, product] }
          : b,
      ),
    }));
    personalizationRequest(`/moodboards/${id}/products`, {
      method: "PUT",
      body: JSON.stringify({ item_id: product.id }),
    }).catch(() => {});
  };
  const toggleBrandSave = (brand) => {
    const removing = (state.savedBrandIds || []).includes(brand.id);
    setState((current) => ({
      ...current,
      savedBrandIds: removing
        ? (current.savedBrandIds || []).filter((id) => id !== brand.id)
        : [...(current.savedBrandIds || []), brand.id],
    }));
    personalizationRequest(`/saved-brands/${brand.id}`, {
      method: removing ? "DELETE" : "PUT",
    }).catch(() => {});
  };
  const actions = {
    // Saving is a signed-in feature (it has to persist server-side to mean
    // anything) -- clicking save while signed out opens the sign-in modal instead
    // of silently no-op'ing, then replays the same action once sign-in succeeds.
    toggleSave: requireAuth(toggleSave),
    toggleBrandSave: requireAuth(toggleBrandSave),
    addToBoard: requireAuth(addToBoard),
    openBrand,
    openProduct: (p) => {
      track("product_view", p);
      setSelectedProduct(p);
    },
  };
  const content = useMemo(() => {
    if (page === "brands")
      return (
        <BrandsPage
          brands={brands}
          products={products}
          loading={loading}
          error={catalogError}
          onRetry={retryCatalog}
          openBrand={openBrand}
        />
      );
    if (page === "brand")
      return (
        <BrandProfile
          brand={payload}
          products={products}
          brands={brands}
          actions={actions}
          state={state}
          back={() => go("brands")}
        />
      );
    if (page === "categories") return <CategoriesPage go={go} />;
    if (page === "category")
      return (
        <CategoryPage
          selection={payload}
          products={products}
          brands={brands}
          state={state}
          actions={actions}
          openBrand={openBrand}
        />
      );
    if (page === "search")
      return (
        <SearchPage
          seedQuery={seedQuery}
          state={state}
          actions={actions}
          track={track}
        />
      );
    if (page === "dolu" && CHAT_ENABLED) return <DoluPage state={state} actions={actions} />;
    if (page === "moodboards")
      return (
        <MoodboardsPage
          state={state}
          setState={setState}
          products={products}
          actions={actions}
        />
      );
    if (page === "saved") return <SavedPage state={state} actions={actions} />;
    if (page === "profile") return <ProfilePage state={state} auth={auth} openAuthModal={openAuthModal} />;
    if (page === "about") return <AboutPage />;
    return (
      <DiscoverPage
        brands={brands}
        products={products}
        loading={loading}
        error={catalogError}
        onRetry={retryCatalog}
        go={go}
        setSeedQuery={setSeedQuery}
        state={state}
        actions={actions}
      />
    );
  }, [
    page,
    payload,
    brands,
    products,
    loading,
    catalogError,
    catalogRetryKey,
    state,
    seedQuery,
  ]);
  return (
    <main>
      <Nav
        page={page}
        go={go}
        savedCount={state.saved.length}
        user={auth.user}
        onSignInClick={() => openAuthModal()}
        onSignOut={auth.onSignOut}
      />
      <div className="page-transition" key={page}>
        {content}
      </div>
      {selectedProduct && (
        <ProductDetail
          product={selectedProduct}
          products={products}
          state={state}
          actions={actions}
          close={() => setSelectedProduct(null)}
        />
      )}
      <footer>
        <strong>UNFOUND✦</strong>
        <p>Find what you weren’t looking for.</p>
        <span>Independent by design · AI-powered discovery</span>
      </footer>
      <AuthModal
        open={authModalOpen}
        onClose={closeAuthModal}
        onSignIn={auth.onSignIn}
        onSignUp={auth.onSignUp}
      />
    </main>
  );
}
