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

export default function ProductCard({
  product,
  onLike,
  isLiking,
  variant = "default",
}) {
  const [liked, setLiked] = useState(false);
  const [added, setAdded] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);

  const {
    name,
    brand,
    price,
    niche_score: nicheScore,
    image_url: imageUrl,
  } = product;

  const score = Number(nicheScore) || 0;
  const isCompact = variant === "compact";

  function handleLikeClick() {
    setLiked(true);
    onLike(product);
  }

  return (
    <article className="group overflow-hidden rounded-[2rem] border border-white/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.06),rgba(183,148,244,0.08))] shadow-[0_16px_64px_rgba(0,0,0,0.24)] backdrop-blur-[15px] transition duration-300 hover:-translate-y-1 hover:border-[#00F2FF]/30">
      <div className={`relative overflow-hidden ${isCompact ? "h-44" : "h-60"} bg-black/20`}>
        {imageUrl && !imageFailed ? (
          <img
            src={imageUrl}
            alt={name}
            loading="lazy"
            onError={() => setImageFailed(true)}
            className="h-full w-full object-cover transition duration-500 group-hover:scale-105"
          />
        ) : (
          <div className="flex h-full items-center justify-center bg-[radial-gradient(circle_at_top,rgba(0,242,255,0.18),transparent_45%),linear-gradient(180deg,#171B21,#0E1014)] text-center text-sm uppercase tracking-[0.3em] text-white/54">
            No image available
          </div>
        )}

        <div className="absolute inset-x-0 top-0 flex items-start justify-between p-4">
          <span className="rounded-full border border-white/10 bg-black/25 px-3 py-1 text-[11px] uppercase tracking-[0.24em] text-white/72 backdrop-blur-md">
            {brand}
          </span>
          <button
            type="button"
            aria-label="Like product"
            onClick={handleLikeClick}
            disabled={isLiking}
            className={`inline-flex h-10 w-10 items-center justify-center rounded-2xl border transition ${
              liked
                ? "scale-110 border-[#FF4FD8]/40 bg-[#FF4FD8]/18 text-[#FF4FD8]"
                : "border-white/10 bg-black/25 text-white/72 hover:border-[#FF4FD8]/35 hover:text-[#FF4FD8]"
            }`}
          >
            <HeartIcon filled={liked} />
          </button>
        </div>

        <div className="absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-[#101217] to-transparent" />
      </div>

      <div className={`space-y-5 ${isCompact ? "p-4" : "p-5"}`}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className={`${isCompact ? "text-lg" : "text-xl"} font-semibold text-white`}>
              {name}
            </h3>
            <p className="mt-2 text-sm text-white/54">
              Compare positioning from {brand}
            </p>
          </div>
          <span className="rounded-full border border-[#00F2FF]/20 bg-[#00F2FF]/10 px-3 py-1 text-sm font-semibold text-[#7BF7FF]">
            ${Number(price).toFixed(2)}
          </span>
        </div>

        <div className="rounded-[1.5rem] border border-white/8 bg-black/18 p-4">
          <div className="flex items-center justify-between text-sm text-white/58">
            <span>Niche Score</span>
            <span className="font-semibold text-white">{score.toFixed(2)}</span>
          </div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/8">
            <div
              className="h-full rounded-full bg-[linear-gradient(90deg,#B794F4,#00F2FF)]"
              style={{ width: `${Math.min(score * 100, 100)}%` }}
            />
          </div>
        </div>

        <button
          type="button"
          onClick={() => setAdded(true)}
          className={`w-full rounded-full px-4 py-3 text-sm font-semibold transition ${
            added
              ? "bg-emerald-400/18 text-emerald-200"
              : "bg-[#00F2FF] text-[#071018] hover:shadow-[0_0_28px_rgba(0,242,255,0.42)]"
          }`}
        >
          {isLiking ? "Saving..." : added ? "Added! ✓" : "Add to Cart"}
        </button>
      </div>
    </article>
  );
}
