import { useState } from "react";

function HeartIcon({ filled }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-5 w-5"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.8"
    >
      <path d="M12 20.4l-1.2-1.08C5.4 14.46 2 11.4 2 7.62 2 4.56 4.42 2.2 7.46 2.2c1.74 0 3.4.82 4.54 2.1 1.14-1.28 2.8-2.1 4.54-2.1C19.58 2.2 22 4.56 22 7.62c0 3.78-3.4 6.84-8.8 11.7L12 20.4z" />
    </svg>
  );
}

function formatFollowers(value) {
  const followers = Number(value) || 0;
  return followers.toLocaleString();
}

export default function BrandCard({ brand }) {
  const [liked, setLiked] = useState(false);
  const [added, setAdded] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);

  const nicheScore = Number(brand.niche_score) || 0;
  const trendScore = Number(brand.trend_score) || 0;
  const tag = nicheScore > 0.8 ? "🔥 Hidden Gem" : "👍 Good Pick";
  const hasProfileUrl = Boolean(brand.profile_url);
  const hasPostUrl = Boolean(brand.post_url);

  return (
    <article className="group overflow-hidden rounded-[2rem] border border-white/10 bg-[linear-gradient(180deg,rgba(183,148,244,0.10),rgba(255,255,255,0.04))] p-5 shadow-[0_20px_80px_rgba(0,0,0,0.28)] backdrop-blur-[15px] transition duration-300 hover:-translate-y-1 hover:border-[#00F2FF]/30">
      <div className="flex items-start justify-between gap-4">
        <div className="h-16 w-16 shrink-0 overflow-hidden rounded-full bg-white/6">
          {brand.profile_picture_url && !imageFailed ? (
            <img src={brand.profile_picture_url} alt={`${brand.name} logo`} loading="lazy" onError={() => setImageFailed(true)} className="h-full w-full object-cover" />
          ) : (
            <span className="flex h-full items-center justify-center text-xs text-white/50">No image</span>
          )}
        </div>
        <div>
          <p className="text-xs uppercase tracking-[0.28em] text-white/42">Brand</p>
          <h3 className="mt-3 text-2xl font-semibold text-white">{brand.name}</h3>
          <p className="mt-2 text-sm text-white/58">{brand.category || "Unclassified"}</p>
        </div>

        <button
          type="button"
          aria-label={liked ? "Unlike brand" : "Like brand"}
          onClick={() => setLiked((current) => !current)}
          className={`inline-flex h-11 w-11 items-center justify-center rounded-2xl border transition ${
            liked
              ? "scale-110 border-[#FF4FD8]/40 bg-[#FF4FD8]/18 text-[#FF4FD8]"
              : "border-white/10 bg-white/6 text-white/70 hover:border-[#FF4FD8]/35 hover:text-[#FF4FD8]"
          }`}
        >
          <HeartIcon filled={liked} />
        </button>
      </div>

      <div className="mt-5 flex flex-wrap gap-3">
        <span className="rounded-full border border-[#00F2FF]/25 bg-[#00F2FF]/10 px-3 py-1 text-xs font-medium text-[#7BF7FF]">
          {tag}
        </span>
        <span className="rounded-full border border-white/10 bg-white/6 px-3 py-1 text-xs text-white/60">
          Trend {trendScore.toFixed(2)}
        </span>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <div className="rounded-[1.5rem] border border-white/8 bg-black/18 p-4">
          <p className="text-sm text-white/48">Followers</p>
          <p className="mt-2 text-2xl font-semibold text-white">
            {formatFollowers(brand.followers)}
          </p>
        </div>
        <div className="rounded-[1.5rem] border border-white/8 bg-[rgba(0,242,255,0.06)] p-4">
          <p className="text-sm text-white/48">Niche Score</p>
          <p className="mt-2 text-2xl font-semibold text-white">
            {nicheScore.toFixed(2)}
          </p>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap gap-3">
        {hasProfileUrl ? (
          <a
            href={brand.profile_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center rounded-full bg-[#00F2FF] px-4 py-2 text-sm font-semibold text-[#071018] transition hover:shadow-[0_0_28px_rgba(0,242,255,0.42)]"
          >
            Open Instagram Profile
          </a>
        ) : null}

        {hasPostUrl ? (
          <a
            href={brand.post_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center rounded-full border border-white/12 bg-white/5 px-4 py-2 text-sm font-semibold text-white/78 transition hover:border-white/26 hover:text-white"
          >
            View Source Post
          </a>
        ) : null}

        <button
          type="button"
          onClick={() => setAdded(true)}
          className={`inline-flex items-center rounded-full px-4 py-2 text-sm font-semibold transition ${
            added
              ? "bg-emerald-400/18 text-emerald-200"
              : "border border-white/12 bg-white/5 text-white/82 hover:border-[#00F2FF]/35 hover:text-[#00F2FF]"
          }`}
        >
          {added ? "Added! ✓" : "Add to Cart"}
        </button>
      </div>
    </article>
  );
}
