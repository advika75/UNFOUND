import { useEffect, useState } from "react";
import { requireEnv } from "../src/lib/env";

const API_BASE_URL = requireEnv("VITE_API_BASE_URL");
const ADMIN_KEY_STORAGE_KEY = "unfound_admin_key_v1";

// This is a read-only internal tool -- every fetch below is a GET, and
// nothing here ever writes, deletes or mutates catalog/search/ingestion data.

function useAdminKey() {
  const [key, setKey] = useState(() => {
    try {
      return sessionStorage.getItem(ADMIN_KEY_STORAGE_KEY) || "";
    } catch {
      return "";
    }
  });
  const save = (value) => {
    setKey(value);
    try {
      if (value) sessionStorage.setItem(ADMIN_KEY_STORAGE_KEY, value);
      else sessionStorage.removeItem(ADMIN_KEY_STORAGE_KEY);
    } catch {
      // Session storage can throw in a private window -- the key still works
      // for this page load via component state, it just won't survive a reload.
    }
  };
  return [key, save];
}

function EmptyState({ title, note, children }) {
  return (
    <div className="accurate-empty">
      <h2>{title}</h2>
      {note && <p>{note}</p>}
      {children}
    </div>
  );
}

function KeyGate({ onSubmit, error }) {
  const [value, setValue] = useState("");
  return (
    <div className="page-shell admin-gate-shell">
      <p className="kicker">UNFOUND / INTERNAL</p>
      <h1>Quality dashboard</h1>
      <p className="admin-gate-note">
        Enter the admin API key (the same <code>ADMIN_API_KEY</code> the backend already uses for
        the other <code>/api/admin/*</code> endpoints) to view catalog, search and relevance health.
      </p>
      <form
        className="admin-gate-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (value.trim()) onSubmit(value.trim());
        }}
      >
        <input
          type="password"
          autoComplete="off"
          placeholder="Admin API key"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <button type="submit">Unlock</button>
      </form>
      {error && <p className="error-line">{error}</p>}
    </div>
  );
}

function StatCard({ label, value, sub }) {
  return (
    <div className="admin-stat-card">
      <p className="kicker">{label}</p>
      <strong>{value}</strong>
      {sub && <span>{sub}</span>}
    </div>
  );
}

function percent(value) {
  return value === null || value === undefined ? "—" : `${value}%`;
}

function ConfidenceBars({ distribution }) {
  const entries = Object.entries(distribution.buckets);
  const max = Math.max(1, ...entries.map(([, count]) => count), distribution.missing_confidence);
  return (
    <div className="admin-bars">
      {entries.map(([label, count]) => (
        <div className="admin-bar" key={label}>
          <div className="admin-bar-track">
            <div className="admin-bar-fill" style={{ height: `${(count / max) * 100}%` }} />
          </div>
          <small>{label}</small>
          <strong>{count}</strong>
        </div>
      ))}
      <div className="admin-bar">
        <div className="admin-bar-track">
          <div
            className="admin-bar-fill is-missing"
            style={{ height: `${(distribution.missing_confidence / max) * 100}%` }}
          />
        </div>
        <small>missing</small>
        <strong>{distribution.missing_confidence}</strong>
      </div>
    </div>
  );
}

function CatalogHealthSection({ health, confidenceDistribution, captionLikeNames, categoryAudience }) {
  return (
    <section className="admin-section">
      <p className="kicker">1 · CATALOG HEALTH</p>
      <h2>What's actually in the catalog</h2>
      <div className="admin-grid">
        <StatCard label="Products" value={health.total_products} />
        <StatCard label="Brands" value={health.total_brands} sub={`${health.brands_with_products} with products`} />
        <StatCard label="Image coverage" value={percent(health.image_coverage_percent)} />
        <StatCard label="Embedding coverage" value={percent(health.embedding_coverage_percent)} />
        <StatCard label="Category coverage" value={percent(health.category_coverage_percent)} />
        <StatCard label="Price coverage" value={percent(health.price_coverage_percent)} />
        <StatCard label="Possible duplicates" value={health.possible_duplicates} />
        <StatCard label="Caption-like names" value={captionLikeNames.count} sub="bare category word, no real title" />
      </div>
      <div className="admin-two-col">
        <div>
          <h3>Category-confidence distribution</h3>
          <ConfidenceBars distribution={confidenceDistribution} />
        </div>
        <div>
          <h3>Category &times; audience (top 12)</h3>
          {categoryAudience.length ? (
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Category</th>
                  <th>Audience</th>
                  <th>Products</th>
                </tr>
              </thead>
              <tbody>
                {categoryAudience.slice(0, 12).map((row) => (
                  <tr key={`${row.category}-${row.audience}`}>
                    <td>{row.category}</td>
                    <td>{row.audience}</td>
                    <td>{row.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState title="No products yet." note="This fills in once the catalog has products." />
          )}
        </div>
      </div>
    </section>
  );
}

function SearchAnalyticsSection({ analytics }) {
  return (
    <section className="admin-section">
      <p className="kicker">2 · SEARCH ANALYTICS</p>
      <h2>What people are actually searching</h2>
      {analytics.total_queries === 0 ? (
        <EmptyState
          title="No search queries logged yet."
          note="Query logging just started with this dashboard -- every text search from now on records its query, result count and latency (never who searched). This panel fills in as real traffic comes through."
        />
      ) : (
        <>
          <div className="admin-grid">
            <StatCard label="Logged queries" value={analytics.total_queries} />
            <StatCard label="p50 latency" value={analytics.p50_latency_ms != null ? `${Math.round(analytics.p50_latency_ms)} ms` : "—"} />
            <StatCard label="p95 latency" value={analytics.p95_latency_ms != null ? `${Math.round(analytics.p95_latency_ms)} ms` : "—"} />
            <StatCard
              label="Cache hit rate"
              value={analytics.cache_hit_rate != null ? `${Math.round(analytics.cache_hit_rate * 100)}%` : "—"}
            />
          </div>
          <div className="admin-two-col">
            <div>
              <h3>Top queries</h3>
              {analytics.top_queries.length ? (
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Query</th>
                      <th>Count</th>
                    </tr>
                  </thead>
                  <tbody>
                    {analytics.top_queries.map((row) => (
                      <tr key={row.query}>
                        <td>{row.query}</td>
                        <td>{row.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState title="Nothing yet." />
              )}
            </div>
            <div>
              <h3>Zero-result queries</h3>
              {analytics.zero_result_queries.length ? (
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Query</th>
                      <th>Count</th>
                    </tr>
                  </thead>
                  <tbody>
                    {analytics.zero_result_queries.map((row) => (
                      <tr key={row.query}>
                        <td>{row.query}</td>
                        <td>{row.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState title="None logged." note="No search in this log returned zero results." />
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}

const METRIC_LINES = [
  { key: "mean_ndcg_at_10", label: "nDCG@10", color: "#7b955f" },
  { key: "mean_recall_at_20", label: "Recall@20", color: "#a3729a" },
  { key: "mrr", label: "MRR", color: "#171717" },
];
const CHART_WIDTH = 640;
const CHART_HEIGHT = 220;
const CHART_PADDING = 28;

function RelevanceChart({ runs }) {
  if (runs.length === 0) {
    return (
      <EmptyState
        title="No relevance runs recorded yet."
        note="Run backend/eval/cli.py against a query set and its JSON output (backend/eval/runs/<timestamp>.json) will appear here automatically."
      />
    );
  }
  if (runs.length === 1) {
    const run = runs[0];
    return (
      <EmptyState
        title="Only one run so far -- not enough for a trend line."
        note={`${run.file}: nDCG@10 ${run.mean_ndcg_at_10?.toFixed(3)}, Recall@20 ${run.mean_recall_at_20?.toFixed(3)}, MRR ${run.mrr?.toFixed(3)}. Run the harness again later to see this become a real trend.`}
      />
    );
  }
  const innerWidth = CHART_WIDTH - CHART_PADDING * 2;
  const innerHeight = CHART_HEIGHT - CHART_PADDING * 2;
  const xFor = (index) => CHART_PADDING + (index / (runs.length - 1)) * innerWidth;
  const yFor = (value) => CHART_PADDING + (1 - Math.max(0, Math.min(1, value ?? 0))) * innerHeight;
  return (
    <div className="admin-chart-wrap">
      <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} className="admin-chart" role="img" aria-label="Relevance metrics over time">
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <line
            key={tick}
            x1={CHART_PADDING}
            x2={CHART_WIDTH - CHART_PADDING}
            y1={yFor(tick)}
            y2={yFor(tick)}
            stroke="var(--line)"
          />
        ))}
        {METRIC_LINES.map((metric) => (
          <polyline
            key={metric.key}
            fill="none"
            stroke={metric.color}
            strokeWidth="2"
            points={runs.map((run, index) => `${xFor(index)},${yFor(run[metric.key])}`).join(" ")}
          />
        ))}
        {METRIC_LINES.map((metric) =>
          runs.map((run, index) => (
            <circle key={`${metric.key}-${index}`} cx={xFor(index)} cy={yFor(run[metric.key])} r="3" fill={metric.color}>
              <title>
                {run.file} · {metric.label} {run[metric.key]?.toFixed(3)}
                {run.change_note ? ` (${run.change_note})` : ""}
              </title>
            </circle>
          ))
        )}
      </svg>
      <div className="admin-chart-legend">
        {METRIC_LINES.map((metric) => (
          <span key={metric.key}>
            <i style={{ background: metric.color }} /> {metric.label}
          </span>
        ))}
      </div>
      <table className="admin-table admin-runs-table">
        <thead>
          <tr>
            <th>Run</th>
            <th>Generated</th>
            <th>nDCG@10</th>
            <th>Recall@20</th>
            <th>MRR</th>
            <th>Zero-result rate</th>
            <th>Change</th>
          </tr>
        </thead>
        <tbody>
          {[...runs].reverse().map((run) => (
            <tr key={run.file}>
              <td>{run.file}</td>
              <td>{run.generated_at}</td>
              <td>{run.mean_ndcg_at_10?.toFixed(3)}</td>
              <td>{run.mean_recall_at_20?.toFixed(3)}</td>
              <td>{run.mrr?.toFixed(3)}</td>
              <td>{run.zero_result_rate?.toFixed(3)}</td>
              <td>{run.change_note || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function IngestionSection({ runs }) {
  return (
    <section className="admin-section">
      <p className="kicker">4 · INGESTION</p>
      <h2>Recent ingestion runs</h2>
      {runs.length === 0 ? (
        <EmptyState
          title="No ingestion runs recorded yet."
          note="Runs appear here once training/scraper_pipeline.py's ingest-brand-batch command writes to data/ingestion_audit/."
        />
      ) : (
        <table className="admin-table">
          <thead>
            <tr>
              <th>Batch</th>
              <th>Started</th>
              <th>Finished</th>
              <th>Products added</th>
              <th>Failures</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run, index) => (
              <tr key={run.batch_id} className={index === 0 ? "is-latest" : ""}>
                <td>
                  {run.batch_id}
                  {index === 0 && <span className="admin-badge">Last run</span>}
                </td>
                <td>{run.started_at}</td>
                <td>{run.finished_at}</td>
                <td>{run.products_added}</td>
                <td>{run.failures}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function Dashboard() {
  const [adminKey, setAdminKey] = useAdminKey();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    if (!adminKey) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetch(`${API_BASE_URL}/api/admin/dashboard`, {
      headers: { "x-admin-key": adminKey },
      signal: controller.signal,
    })
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (response.status === 401) {
          setAdminKey("");
          throw new Error("That admin key was rejected. Enter it again.");
        }
        if (response.status === 503) {
          throw new Error("ADMIN_API_KEY isn't configured on the backend, so admin access is disabled.");
        }
        if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
        return body;
      })
      .then(setData)
      .catch((requestError) => {
        if (requestError.name !== "AbortError") setError(requestError.message || "Couldn't reach the server. Check your connection and try again.");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [adminKey, retryKey]);

  if (!adminKey) return <KeyGate onSubmit={setAdminKey} error={error} />;

  return (
    <div className="page-shell admin-shell">
      <header className="admin-header">
        <div>
          <p className="kicker">UNFOUND / INTERNAL</p>
          <h1>Quality dashboard</h1>
          <p className="admin-gate-note">Read-only. Reflects the live catalog, search log and eval runs.</p>
        </div>
        <button type="button" className="retry-button" onClick={() => setAdminKey("")}>
          Lock
        </button>
      </header>
      {loading && <div className="loading-block">Loading dashboard…</div>}
      {!loading && error && (
        <EmptyState title="Dashboard unavailable" note={error}>
          <button type="button" className="retry-button" onClick={() => setRetryKey((key) => key + 1)}>
            Try again
          </button>
        </EmptyState>
      )}
      {!loading && !error && data && (
        <>
          <CatalogHealthSection
            health={data.catalog_health}
            confidenceDistribution={data.confidence_distribution}
            captionLikeNames={data.caption_like_names}
            categoryAudience={data.category_audience_breakdown}
          />
          <SearchAnalyticsSection analytics={data.search_analytics} />
          <section className="admin-section">
            <p className="kicker">3 · RELEVANCE</p>
            <h2>Search quality over time</h2>
            <RelevanceChart runs={data.relevance_runs} />
          </section>
          <IngestionSection runs={data.ingestion_runs} />
        </>
      )}
    </div>
  );
}
