import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import AuthPanel from "../components/AuthPanel";
import BrandCard from "../components/BrandCard";
import ProductCard from "../components/ProductCard";

const CATEGORY_PILLS = [
  "All",
  "Clothing",
  "House & Furnishing",
  "Accessories",
  "Tech",
];

const CATEGORY_FEATURES = [
  {
    name: "Clothing",
    label: "Soft-tailored labels",
    image:
      "https://images.unsplash.com/photo-1529139574466-a303027c1d8b?auto=format&fit=crop&w=1200&q=80",
  },
  {
    name: "House & Furnishing",
    label: "Objects with spatial identity",
    image:
      "https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?auto=format&fit=crop&w=1200&q=80",
  },
  {
    name: "Accessories",
    label: "Small-format cult favorites",
    image:
      "https://images.unsplash.com/photo-1523170335258-f5ed11844a49?auto=format&fit=crop&w=1200&q=80",
  },
  {
    name: "Tech",
    label: "Precision objects and audio tools",
    image:
      "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1200&q=80",
  },
];

const staggerContainer = {
  hidden: {},
  show: {
    transition: {
      staggerChildren: 0.08,
    },
  },
};

const fadeItem = {
  hidden: { opacity: 0, y: 18 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.45, ease: "easeOut" },
  },
};

const IMAGE_FEATURE_SIZE = 24;
const CLIP_MODEL_ID = "Xenova/clip-vit-base-patch32";
const CLIP_TOP_MATCHES = 5;
const CLIP_CANDIDATE_LABELS = [
  "dress",
  "gown",
  "top",
  "shirt",
  "kurta",
  "ethnic wear",
  "jacket",
  "blazer",
  "skirt",
  "pants",
  "jeans",
  "bag",
  "handbag",
  "jewelry",
  "earrings",
  "necklace",
  "shoes",
  "heels",
  "sandals",
  "home decor",
  "furnishing",
  "lamp",
  "cushion",
  "tech accessory",
  "audio device",
  "fashion clothing",
];
const FASHION_SEARCH_GROUPS = {
  dress: [
    "dress",
    "dresses",
    "gown",
    "frock",
    "sundress",
    "slipdress",
    "slip",
    "maxi",
    "midi",
    "mini",
  ],
  top: [
    "top",
    "tops",
    "blouse",
    "blouses",
    "camisole",
    "tank",
    "tee",
    "tshirt",
    "shirt",
    "shirts",
  ],
  kurta: [
    "kurta",
    "kurtas",
    "kurti",
    "kurti",
    "tunic",
    "tunics",
    "ethnic",
  ],
  jacket: [
    "jacket",
    "jackets",
    "blazer",
    "coat",
    "outerwear",
    "cardigan",
    "hoodie",
    "sweater",
  ],
  bottoms: [
    "jeans",
    "pants",
    "trousers",
    "denim",
    "skirt",
    "skirts",
    "shorts",
    "bottoms",
  ],
  bag: [
    "bag",
    "bags",
    "handbag",
    "purse",
    "tote",
    "satchel",
    "clutch",
    "wallet",
  ],
  jewelry: [
    "jewelry",
    "jewellery",
    "earring",
    "earrings",
    "necklace",
    "bracelet",
    "ring",
    "rings",
  ],
  shoes: [
    "shoes",
    "sneaker",
    "sneakers",
    "loafer",
    "loafers",
    "heel",
    "heels",
    "sandals",
  ],
  home: [
    "decor",
    "home",
    "furnishing",
    "interior",
    "cushion",
    "lamp",
    "table",
  ],
  tech: [
    "tech",
    "audio",
    "speaker",
    "headphones",
    "gadget",
    "electronics",
  ],
};
const CATEGORY_KEYWORDS = {
  Clothing: [
    "clothing",
    "dress",
    "dresses",
    "wear",
    "fashion",
    "apparel",
    "outfit",
    "outfits",
    "shirt",
    "shirts",
    "tee",
    "tshirt",
    "tops",
    "top",
    "kurta",
    "kurtas",
    "coord",
    "co-ord",
    "jacket",
    "skirts",
    "skirt",
    "pants",
    "jeans",
  ],
  "House & Furnishing": [
    "home",
    "decor",
    "furnishing",
    "furnishings",
    "living",
    "space",
    "room",
    "interior",
    "cushion",
    "table",
    "lamp",
  ],
  Accessories: [
    "accessories",
    "accessory",
    "jewelry",
    "jewellery",
    "ring",
    "rings",
    "bag",
    "bags",
    "wallet",
    "scarf",
    "belt",
    "beauty",
  ],
  Tech: [
    "tech",
    "audio",
    "speaker",
    "headphone",
    "headphones",
    "electronic",
    "electronics",
    "gadget",
    "gadgets",
  ],
};

function SearchIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-5 w-5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
    >
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l5 5" />
    </svg>
  );
}

function SparkleLogo() {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-white/15 bg-white/10 shadow-[0_0_30px_rgba(0,242,255,0.18)] backdrop-blur-md">
        <div className="h-4 w-4 rounded-full bg-[#00F2FF] shadow-[0_0_20px_rgba(0,242,255,0.65)]" />
      </div>
      <div>
        <p className="text-[11px] uppercase tracking-[0.35em] text-white/45">
          Hidden Gems
        </p>
        <p className="text-sm font-medium text-white/88">Brand Radar</p>
      </div>
    </div>
  );
}

function normalizeCategory(value = "") {
  const category = value.toLowerCase();

  if (
    category.includes("fashion") ||
    category.includes("clothing") ||
    category.includes("apparel")
  ) {
    return "Clothing";
  }

  if (
    category.includes("home") ||
    category.includes("furnishing") ||
    category.includes("decor")
  ) {
    return "House & Furnishing";
  }

  if (
    category.includes("accessories") ||
    category.includes("beauty") ||
    category.includes("jewellery") ||
    category.includes("jewelry") ||
    category.includes("instagram discovery")
  ) {
    return "Accessories";
  }

  if (category.includes("tech") || category.includes("electronics") || category.includes("audio")) {
    return "Tech";
  }

  return "Accessories";
}

function getBrandKey(brand) {
  return (brand?.name || "").toLowerCase().trim();
}

function normalizeSearchText(value = "") {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function getQueryTokens(value = "") {
  return normalizeSearchText(value).split(/\s+/).filter(Boolean);
}

function buildBrandSearchBlob(brand) {
  const categoryKeywords = CATEGORY_KEYWORDS[brand.display_category] || [];

  return normalizeSearchText(
    [
      brand.name,
      brand.category,
      brand.display_category,
      categoryKeywords.join(" "),
      brand.profile_url,
      brand.post_url,
    ]
      .filter(Boolean)
      .join(" ")
  );
}

function buildProductSearchBlob(product, productCategory) {
  const categoryKeywords = CATEGORY_KEYWORDS[productCategory] || [];

  return normalizeSearchText(
    [product.name, product.brand, productCategory, categoryKeywords.join(" "), product.image_url]
      .filter(Boolean)
      .join(" ")
  );
}

function matchesTokens(searchBlob, tokens) {
  if (tokens.length === 0) {
    return true;
  }

  return tokens.every((token) => searchBlob.includes(token));
}

function extractSearchHintFromFileName(fileName = "") {
  const normalized = normalizeSearchText(fileName.replace(/\.[a-z0-9]+$/i, ""));
  const blacklist = new Set([
    "img",
    "image",
    "photo",
    "pic",
    "screenshot",
    "jpeg",
    "jpg",
    "png",
    "webp",
    "heic",
    "screenshot",
    "screen",
    "shot",
    "capture",
    "whatsapp",
    "edited",
    "copy",
    "photo",
    "from",
    "camera",
    "pm",
    "am",
  ]);

  const meaningfulTokens = normalized
    .split(/\s+/)
    .filter((token) => token.length > 2 && !blacklist.has(token))
    .filter((token) => !/^\d+$/.test(token))
    .filter((token) => !/^\d{1,2}[-:]\d{1,2}([:-]\d{1,2})?$/.test(token))
    .filter((token) => !/^\d{4}-\d{2}-\d{2}$/.test(token));

  if (meaningfulTokens.length === 0) {
    return "";
  }

  return meaningfulTokens.slice(0, 4).join(" ");
}

function predictionsToSearchHint(predictions) {
  const synonymMap = {
    gown: ["dress", "clothing"],
    "ethnic wear": ["kurta", "clothing"],
    blazer: ["jacket", "clothing"],
    handbag: ["bag", "accessories"],
    earrings: ["jewelry", "accessories"],
    necklace: ["jewelry", "accessories"],
    heels: ["shoes", "accessories"],
    sandals: ["shoes", "accessories"],
    "home decor": ["home", "decor"],
    furnishing: ["home", "furnishing"],
    lamp: ["home", "decor"],
    cushion: ["home", "decor"],
    "tech accessory": ["tech", "gadget"],
    "audio device": ["tech", "audio"],
    "fashion clothing": ["clothing", "fashion"],
  };

  const normalizedTokens = [
    ...new Set(
      predictions
        .filter((prediction) => prediction.score >= 0.08)
        .flatMap((prediction) => synonymMap[prediction.label] || [prediction.label])
        .flatMap((label) => normalizeSearchText(label).split(/\s+/))
        .filter(Boolean)
    ),
  ];

  const matchedGroups = Object.entries(FASHION_SEARCH_GROUPS)
    .filter(([, groupTokens]) => normalizedTokens.some((token) => groupTokens.includes(token)))
    .flatMap(([, groupTokens]) => groupTokens.slice(0, 4));

  return [...new Set([...normalizedTokens, ...matchedGroups])].slice(0, 8).join(" ");
}

function clamp01(value) {
  return Math.max(0, Math.min(1, value));
}

function rgbToHue(red, green, blue) {
  const r = red / 255;
  const g = green / 255;
  const b = blue / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;

  if (!delta) {
    return 0;
  }

  let hue;

  if (max === r) {
    hue = ((g - b) / delta) % 6;
  } else if (max === g) {
    hue = (b - r) / delta + 2;
  } else {
    hue = (r - g) / delta + 4;
  }

  return ((hue * 60 + 360) % 360) / 360;
}

function createImageFeatureFromData(imageData) {
  const { data } = imageData;
  let redSum = 0;
  let greenSum = 0;
  let blueSum = 0;
  let brightnessSum = 0;
  let saturationSum = 0;
  let count = 0;

  for (let index = 0; index < data.length; index += 4) {
    const alpha = data[index + 3];

    if (!alpha) {
      continue;
    }

    const red = data[index];
    const green = data[index + 1];
    const blue = data[index + 2];
    const max = Math.max(red, green, blue);
    const min = Math.min(red, green, blue);

    redSum += red;
    greenSum += green;
    blueSum += blue;
    brightnessSum += (red + green + blue) / 3;
    saturationSum += max === 0 ? 0 : (max - min) / max;
    count += 1;
  }

  if (!count) {
    return null;
  }

  const avgRed = redSum / count;
  const avgGreen = greenSum / count;
  const avgBlue = blueSum / count;

  return {
    red: avgRed / 255,
    green: avgGreen / 255,
    blue: avgBlue / 255,
    brightness: brightnessSum / count / 255,
    saturation: saturationSum / count,
    hue: rgbToHue(avgRed, avgGreen, avgBlue),
  };
}

function loadImageFeature(src) {
  return new Promise((resolve) => {
    if (!src) {
      resolve(null);
      return;
    }

    const image = new Image();
    image.crossOrigin = "anonymous";

    image.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = IMAGE_FEATURE_SIZE;
        canvas.height = IMAGE_FEATURE_SIZE;

        const context = canvas.getContext("2d", { willReadFrequently: true });
        if (!context) {
          resolve(null);
          return;
        }

        context.drawImage(image, 0, 0, IMAGE_FEATURE_SIZE, IMAGE_FEATURE_SIZE);
        resolve(
          createImageFeatureFromData(
            context.getImageData(0, 0, IMAGE_FEATURE_SIZE, IMAGE_FEATURE_SIZE)
          )
        );
      } catch {
        resolve(null);
      }
    };

    image.onerror = () => resolve(null);
    image.src = src;
  });
}

function getVisualSimilarityScore(left, right) {
  if (!left || !right) {
    return null;
  }

  const hueDistance = Math.min(
    Math.abs(left.hue - right.hue),
    1 - Math.abs(left.hue - right.hue)
  );

  const distance =
    Math.abs(left.red - right.red) * 0.18 +
    Math.abs(left.green - right.green) * 0.18 +
    Math.abs(left.blue - right.blue) * 0.18 +
    Math.abs(left.brightness - right.brightness) * 0.22 +
    Math.abs(left.saturation - right.saturation) * 0.14 +
    hueDistance * 0.1;

  return clamp01(1 - distance);
}

function inferBrandCategory(brand) {
  const rawCategory = brand.category || "";
  const normalized = normalizeCategory(rawCategory);
  if (rawCategory && rawCategory !== "Instagram Discovery") {
    return normalized;
  }

  const searchableText = `${brand.name || ""} ${brand.category || ""}`.toLowerCase();

  if (
    searchableText.includes("wear") ||
    searchableText.includes("atelier") ||
    searchableText.includes("apparel") ||
    searchableText.includes("fashion")
  ) {
    return "Clothing";
  }

  if (
    searchableText.includes("home") ||
    searchableText.includes("furnish") ||
    searchableText.includes("decor") ||
    searchableText.includes("farm")
  ) {
    return "House & Furnishing";
  }

  if (
    searchableText.includes("tech") ||
    searchableText.includes("audio") ||
    searchableText.includes("electronic")
  ) {
    return "Tech";
  }

  return "Accessories";
}

export default function Home({
  products,
  likedProducts,
  brands,
  isLoading,
  likesLoading,
  brandsLoading,
  error,
  brandsError,
  user,
  authLoading,
  authMessage,
  authError,
  showOnlyNiche,
  onToggleNiche,
  onLike,
  likingProductId,
  onSignIn,
  onSignUp,
  onSignOut,
}) {
  const uploadedImageRef = useRef(null);
  const [activeCategory, setActiveCategory] = useState("All");
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [uploadedAssetName, setUploadedAssetName] = useState("");
  const [uploadedAssetUrl, setUploadedAssetUrl] = useState("");
  const [uploadSearchHint, setUploadSearchHint] = useState("");
  const [mlSearchHint, setMlSearchHint] = useState("");
  const [mlPredictions, setMlPredictions] = useState([]);
  const [mlModel, setMlModel] = useState(null);
  const [mlStatus, setMlStatus] = useState("idle");
  const [uploadedImageReady, setUploadedImageReady] = useState(false);
  const [uploadedImageFeature, setUploadedImageFeature] = useState(null);
  const [productImageFeatures, setProductImageFeatures] = useState({});
  const [visualMatchStatus, setVisualMatchStatus] = useState("idle");
  const effectiveSearchTerm = searchTerm.trim() || mlSearchHint;
  const deferredSearchTerm = useDeferredValue(effectiveSearchTerm);
  const searchTokens = useMemo(
    () => getQueryTokens(deferredSearchTerm),
    [deferredSearchTerm]
  );

  useEffect(() => {
    return () => {
      if (uploadedAssetUrl) {
        URL.revokeObjectURL(uploadedAssetUrl);
      }
    };
  }, [uploadedAssetUrl]);

  useEffect(() => {
    let cancelled = false;

    async function loadModel() {
      setMlStatus("loading");

      try {
        const { pipeline, env } = await import("@huggingface/transformers");
        env.allowLocalModels = false;
        const nextModel = await pipeline(
          "zero-shot-image-classification",
          CLIP_MODEL_ID
        );
        if (!cancelled) {
          setMlModel(nextModel);
          setMlStatus("ready");
        }
      } catch {
        if (!cancelled) {
          setMlStatus("error");
        }
      }
    }

    loadModel();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function analyzeUploadedAsset() {
      if (!uploadedAssetUrl) {
        setUploadedImageFeature(null);
        setVisualMatchStatus("idle");
        return;
      }

      setVisualMatchStatus("analyzing");
      const feature = await loadImageFeature(uploadedAssetUrl);

      if (cancelled) {
        return;
      }

      setUploadedImageFeature(feature);
      setVisualMatchStatus(feature ? "ready" : "unavailable");
    }

    analyzeUploadedAsset();

    return () => {
      cancelled = true;
    };
  }, [uploadedAssetUrl]);

  useEffect(() => {
    let cancelled = false;

    async function classifyUploadedImage() {
      if (!uploadedAssetUrl) {
        setMlSearchHint("");
        setMlPredictions([]);
        return;
      }

      if (!mlModel || !uploadedImageRef.current || !uploadedImageReady) {
        return;
      }

      setMlStatus("classifying");

      try {
        const predictions = await mlModel(uploadedAssetUrl, CLIP_CANDIDATE_LABELS);

        if (cancelled) {
          return;
        }

        const rankedPredictions = predictions.slice(0, CLIP_TOP_MATCHES);
        const nextHint = predictionsToSearchHint(rankedPredictions);
        setMlPredictions(rankedPredictions);
        setMlSearchHint(nextHint);
        setMlStatus("ready");

        if (!searchTerm.trim() && nextHint) {
          setSearchTerm(nextHint);
        }
      } catch {
        if (!cancelled) {
          setMlPredictions([]);
          setMlSearchHint("");
          setMlStatus("error");
        }
      }
    }

    classifyUploadedImage();

    return () => {
      cancelled = true;
    };
  }, [mlModel, searchTerm, uploadedAssetUrl, uploadedImageReady]);

  useEffect(() => {
    let cancelled = false;

    async function analyzeProductImages() {
      const productsToAnalyze = products.filter(
        (product) => product.image_url && !productImageFeatures[product.id]
      );

      if (!productsToAnalyze.length) {
        return;
      }

      const updates = {};

      await Promise.all(
        productsToAnalyze.map(async (product) => {
          const feature = await loadImageFeature(product.image_url);
          updates[product.id] = feature || null;
        })
      );

      if (cancelled || Object.keys(updates).length === 0) {
        return;
      }

      setProductImageFeatures((current) => ({ ...current, ...updates }));
    }

    analyzeProductImages();

    return () => {
      cancelled = true;
    };
  }, [products, productImageFeatures]);

  const enhancedBrands = useMemo(
    () =>
      brands.map((brand) => ({
        ...brand,
        display_category: inferBrandCategory(brand),
      })),
    [brands]
  );

  const filteredBrands = useMemo(() => {
    return enhancedBrands.filter((brand) => {
      if (activeCategory !== "All" && brand.display_category !== activeCategory) {
        return false;
      }

      return matchesTokens(buildBrandSearchBlob(brand), searchTokens);
    });
  }, [activeCategory, enhancedBrands, searchTokens]);

  const filteredProducts = useMemo(() => {
    return products.filter((product) => {
      const matchedBrand = enhancedBrands.find(
        (brand) => getBrandKey(brand) === (product.brand || "").toLowerCase().trim()
      );
      const productCategory = matchedBrand?.display_category || "Accessories";

      if (activeCategory !== "All" && productCategory !== activeCategory) {
        return false;
      }

      return matchesTokens(buildProductSearchBlob(product, productCategory), searchTokens);
    });
  }, [activeCategory, enhancedBrands, products, searchTokens]);

  const previewResults = useMemo(() => {
    return [...filteredProducts]
      .map((product) => {
        const visualScore = getVisualSimilarityScore(
          uploadedImageFeature,
          productImageFeatures[product.id]
        );

        return {
          ...product,
          visual_match_score: visualScore,
        };
      })
      .sort((left, right) => {
        const rightScore = right.visual_match_score ?? -1;
        const leftScore = left.visual_match_score ?? -1;

        if (rightScore !== leftScore) {
          return rightScore - leftScore;
        }

        if (Boolean(right.image_url) !== Boolean(left.image_url)) {
          return Number(Boolean(right.image_url)) - Number(Boolean(left.image_url));
        }

        return (Number(right.niche_score) || 0) - (Number(left.niche_score) || 0);
      })
      .slice(0, 6);
  }, [filteredProducts, productImageFeatures, uploadedImageFeature]);

  const previewBrands = useMemo(
    () => filteredBrands.slice(0, 6),
    [filteredBrands]
  );

  function handleAssetChange(event) {
    const file = event.target.files?.[0];

    if (uploadedAssetUrl) {
      URL.revokeObjectURL(uploadedAssetUrl);
    }

    if (!file) {
      setUploadedAssetName("");
      setUploadedAssetUrl("");
      setUploadSearchHint("");
      return;
    }

    const nextUrl = URL.createObjectURL(file);
    const nextHint = extractSearchHintFromFileName(file.name);

    setUploadedAssetName(file.name);
    setUploadedAssetUrl(nextUrl);
    setUploadSearchHint(nextHint);
    setMlSearchHint("");
    setMlPredictions([]);
    setUploadedImageReady(false);
    setUploadedImageFeature(null);

    if (!searchTerm.trim() && nextHint) {
      setSearchTerm(nextHint);
    }
  }

  function handleClearUpload() {
    if (uploadedAssetUrl) {
      URL.revokeObjectURL(uploadedAssetUrl);
    }

    setUploadedAssetName("");
    setUploadedAssetUrl("");
    setUploadSearchHint("");
    setMlSearchHint("");
    setMlPredictions([]);
    setUploadedImageReady(false);
    setUploadedImageFeature(null);
    setVisualMatchStatus("idle");
  }

  return (
    <main className="min-h-screen bg-[#121417] px-4 py-6 text-white sm:px-6 lg:px-8">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute left-[-8rem] top-[-5rem] h-72 w-72 rounded-full bg-[rgba(183,148,244,0.14)] blur-3xl" />
        <div className="absolute right-[-6rem] top-12 h-80 w-80 rounded-full bg-[rgba(0,242,255,0.12)] blur-3xl" />
        <div className="absolute bottom-[-8rem] left-1/3 h-96 w-96 rounded-full bg-[rgba(120,255,214,0.08)] blur-3xl" />
      </div>

      <div className="relative mx-auto max-w-7xl space-y-8">
        <nav className="glass-cyber sticky top-4 z-20 rounded-3xl px-4 py-3 sm:px-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <SparkleLogo />

            <div className="relative flex flex-1 justify-center">
              <div className="relative flex flex-wrap justify-center gap-2 rounded-full border border-white/10 bg-white/5 p-1 backdrop-blur-md">
                {CATEGORY_PILLS.map((category) => {
                  const isActive = activeCategory === category;
                  return (
                    <button
                      key={category}
                      type="button"
                      onClick={() => setActiveCategory(category)}
                      className={`group relative overflow-hidden rounded-full px-4 py-2 text-sm transition ${
                        isActive ? "text-[#05131A]" : "text-white/72 hover:text-white"
                      }`}
                    >
                      <span
                        className={`absolute inset-0 rounded-full transition duration-300 ${
                          isActive
                            ? "bg-[#00F2FF] shadow-[0_0_28px_rgba(0,242,255,0.38)]"
                            : "translate-y-full bg-white/12 group-hover:translate-y-0"
                        }`}
                      />
                      <span className="relative">{category}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <button
              type="button"
              onClick={() => setSearchOpen(true)}
              className="inline-flex items-center justify-center rounded-2xl border border-white/10 bg-white/8 p-3 text-white/80 transition hover:border-[#00F2FF]/50 hover:text-[#00F2FF]"
            >
              <SearchIcon />
            </button>
          </div>
        </nav>

        <section className="glass-cyber overflow-hidden rounded-[2rem] px-6 py-8 sm:px-8">
          <div className="grid gap-10 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
            <div className="space-y-5">
              <p className="text-xs uppercase tracking-[0.4em] text-white/45">
                Cyber-Soft Discovery
              </p>
              <h1 className="max-w-3xl text-4xl font-semibold tracking-tight text-white sm:text-5xl lg:text-6xl">
                Discover cult-favorite brands and compare products in one electric feed.
              </h1>
              <p className="max-w-2xl text-base leading-7 text-white/62 sm:text-lg">
                Search visually, scan trend signals, and jump from brand cards to source
                posts without leaving the glassy control room.
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="rounded-3xl border border-white/10 bg-[rgba(183,148,244,0.10)] p-5 backdrop-blur-xl">
                <p className="text-xs uppercase tracking-[0.24em] text-white/45">Brands</p>
                <p className="mt-3 text-4xl font-semibold text-white">{filteredBrands.length}</p>
              </div>
              <div className="rounded-3xl border border-white/10 bg-white/6 p-5 backdrop-blur-xl">
                <p className="text-xs uppercase tracking-[0.24em] text-white/45">Products</p>
                <p className="mt-3 text-4xl font-semibold text-white">{filteredProducts.length}</p>
              </div>
              <div className="rounded-3xl border border-white/10 bg-[rgba(0,242,255,0.08)] p-5 backdrop-blur-xl">
                <p className="text-xs uppercase tracking-[0.24em] text-white/45">Saved</p>
                <p className="mt-3 text-4xl font-semibold text-white">{likedProducts.length}</p>
              </div>
            </div>
          </div>
        </section>

        <section className="space-y-5">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.34em] text-[#00F2FF]">
                Featured Categories
              </p>
              <h2 className="mt-2 text-3xl font-semibold text-white">
                Browse by visual mood and category
              </h2>
            </div>
            <p className="text-sm text-white/48">
              Tap a panel to retune the whole discovery feed
            </p>
          </div>

          <motion.div
            variants={staggerContainer}
            initial="hidden"
            animate="show"
            className="grid gap-4 md:grid-cols-2 xl:grid-cols-4"
          >
            {CATEGORY_FEATURES.map((category) => {
              const isActive = activeCategory === category.name;
              return (
                <motion.button
                  key={category.name}
                  type="button"
                  variants={fadeItem}
                  onClick={() => setActiveCategory(category.name)}
                  className={`group relative min-h-[240px] overflow-hidden rounded-[2rem] border text-left transition ${
                    isActive
                      ? "border-[#00F2FF]/50 shadow-[0_0_40px_rgba(0,242,255,0.22)]"
                      : "border-white/10"
                  }`}
                >
                  <img
                    src={category.image}
                    alt={category.name}
                    className="absolute inset-0 h-full w-full object-cover transition duration-500 group-hover:scale-105"
                  />
                  <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(8,10,14,0.08),rgba(8,10,14,0.82))]" />
                  <div
                    className={`absolute inset-0 ${
                      isActive ? "bg-[rgba(0,242,255,0.12)]" : "bg-[rgba(183,148,244,0.06)]"
                    }`}
                  />
                  <div className="relative flex h-full flex-col justify-end p-5">
                    <span className="w-fit rounded-full border border-white/12 bg-black/20 px-3 py-1 text-[11px] uppercase tracking-[0.28em] text-white/72 backdrop-blur-md">
                      {category.name}
                    </span>
                    <h3 className="mt-4 text-2xl font-semibold text-white">
                      {category.label}
                    </h3>
                    <p className="mt-2 text-sm text-white/62">
                      {isActive ? "Currently active in the discovery grid" : "Switch feed focus"}
                    </p>
                  </div>
                </motion.button>
              );
            })}
          </motion.div>
        </section>

        <AuthPanel
          user={user}
          authLoading={authLoading}
          authMessage={authMessage}
          authError={authError}
          onSignIn={onSignIn}
          onSignUp={onSignUp}
          onSignOut={onSignOut}
        />

        <section className="grid gap-8 xl:grid-cols-[1.15fr_0.85fr]">
          <div className="space-y-5">
            <div className="flex items-end justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-[0.34em] text-[#00F2FF]">
                  Brand Discovery
                </p>
                <h2 className="mt-2 text-3xl font-semibold text-white">
                  Interactive brand cards
                </h2>
              </div>
              <button
                type="button"
                onClick={() => setSearchOpen(true)}
                className="rounded-full border border-[#00F2FF]/30 bg-[#00F2FF]/10 px-4 py-2 text-sm font-medium text-[#00F2FF] transition hover:bg-[#00F2FF] hover:text-[#05131A]"
              >
                Open search lab
              </button>
            </div>

            {brandsLoading ? (
              <div className="glass-cyber rounded-[2rem] p-10 text-center text-white/60">
                Loading brand intelligence...
              </div>
            ) : null}

            {brandsError ? (
              <div className="rounded-[2rem] border border-rose-400/30 bg-rose-500/10 p-6 text-rose-100">
                {brandsError}
              </div>
            ) : null}

            {!brandsLoading && !brandsError && filteredBrands.length === 0 ? (
              <div className="glass-cyber rounded-[2rem] p-10 text-center text-white/60">
                No brands match this category or search yet.
              </div>
            ) : null}

            {!brandsLoading && !brandsError && filteredBrands.length > 0 ? (
              <motion.div
                key={`brands-${activeCategory}-${searchTerm}`}
                variants={staggerContainer}
                initial="hidden"
                animate="show"
                className="columns-1 gap-6 md:columns-2"
              >
                {filteredBrands.map((brand) => (
                  <motion.div
                    key={brand.id}
                    variants={fadeItem}
                    className="mb-6 break-inside-avoid"
                  >
                    <BrandCard brand={brand} />
                  </motion.div>
                ))}
              </motion.div>
            ) : null}
          </div>

          <div className="space-y-5">
            <div className="glass-cyber rounded-[2rem] p-6">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-xs uppercase tracking-[0.34em] text-[#00F2FF]">
                    Product Comparison
                  </p>
                  <h2 className="mt-2 text-3xl font-semibold text-white">
                    Compare standout picks
                  </h2>
                </div>
                <label className="flex items-center gap-3 rounded-full border border-white/10 bg-white/6 px-4 py-3 text-sm text-white/72">
                  <span>Only niche</span>
                  <input
                    type="checkbox"
                    checked={showOnlyNiche}
                    onChange={onToggleNiche}
                    className="h-4 w-4 accent-[#00F2FF]"
                  />
                </label>
              </div>

              <p className="mt-4 text-sm leading-6 text-white/58">
                Use the category pills to narrow the shelf, then like or add items as
                you compare brand positioning and product depth.
              </p>
            </div>

            {error ? (
              <div className="rounded-[2rem] border border-rose-400/30 bg-rose-500/10 p-6 text-rose-100">
                {error}
              </div>
            ) : null}

            {isLoading ? (
              <div className="glass-cyber rounded-[2rem] p-10 text-center text-white/60">
                Loading comparison set...
              </div>
            ) : null}

            {!isLoading && !error && filteredProducts.length === 0 ? (
              <div className="glass-cyber rounded-[2rem] p-10 text-center text-white/60">
                No products match this slice right now.
              </div>
            ) : null}

            {!isLoading && filteredProducts.length > 0 ? (
              <motion.div
                key={`products-${activeCategory}-${searchTerm}-${showOnlyNiche}`}
                variants={staggerContainer}
                initial="hidden"
                animate="show"
                className="columns-1 gap-6"
              >
                {filteredProducts.slice(0, 4).map((product) => (
                  <motion.div
                    key={product.id}
                    variants={fadeItem}
                    className="mb-6 break-inside-avoid"
                  >
                    <ProductCard
                      product={product}
                      onLike={onLike}
                      isLiking={likingProductId === product.id}
                    />
                  </motion.div>
                ))}
              </motion.div>
            ) : null}

            <div className="glass-cyber rounded-[2rem] p-6">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-xs uppercase tracking-[0.34em] text-[#00F2FF]">
                    Saved Picks
                  </p>
                  <h3 className="mt-2 text-2xl font-semibold text-white">
                    Your liked products
                  </h3>
                </div>
                {user ? (
                  <span className="rounded-full border border-white/10 bg-white/6 px-3 py-2 text-sm text-white/70">
                    {likedProducts.length} saved
                  </span>
                ) : null}
              </div>

              {!user ? (
                <p className="mt-4 text-sm text-white/56">
                  Sign in above to keep a persistent shortlist.
                </p>
              ) : null}

              {user && likesLoading ? (
                <div className="mt-5 space-y-3">
                  <div className="h-24 animate-pulse rounded-3xl bg-white/8" />
                  <div className="h-24 animate-pulse rounded-3xl bg-white/6" />
                </div>
              ) : null}

              {user && !likesLoading && likedProducts.length > 0 ? (
                <motion.div
                  variants={staggerContainer}
                  initial="hidden"
                  animate="show"
                  className="mt-5 grid gap-4"
                >
                  {likedProducts.slice(0, 3).map((product) => (
                    <motion.div key={`liked-${product.id}`} variants={fadeItem}>
                      <ProductCard
                        product={product}
                        onLike={onLike}
                        isLiking={likingProductId === product.id}
                        variant="compact"
                      />
                    </motion.div>
                  ))}
                </motion.div>
              ) : null}
            </div>
          </div>
        </section>
      </div>

      <AnimatePresence>
        {searchOpen ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-[rgba(8,10,14,0.82)] p-4 backdrop-blur-xl sm:p-8"
          >
            <motion.div
              initial={{ opacity: 0, y: 24, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 20, scale: 0.98 }}
              transition={{ duration: 0.28, ease: "easeOut" }}
              className="mx-auto flex h-full max-w-6xl flex-col overflow-hidden rounded-[2rem] border border-white/10 bg-[rgba(18,20,23,0.94)] shadow-[0_0_120px_rgba(0,242,255,0.08)]"
            >
              <div className="flex items-center justify-between border-b border-white/8 px-6 py-5">
                <div>
                  <p className="text-xs uppercase tracking-[0.34em] text-[#00F2FF]">
                    Search + Upload
                  </p>
                  <h2 className="mt-2 text-2xl font-semibold text-white">
                    Brand discovery overlay
                  </h2>
                </div>
                <button
                  type="button"
                  onClick={() => setSearchOpen(false)}
                  className="rounded-full border border-white/10 bg-white/6 px-4 py-2 text-sm text-white/75 transition hover:border-white/30 hover:text-white"
                >
                  Close
                </button>
              </div>

              <div className="grid flex-1 gap-8 overflow-y-auto px-6 py-6 lg:grid-cols-[0.95fr_1.05fr]">
                <div className="space-y-6">
                  <div className="gradient-outline rounded-[1.75rem] p-[1px]">
                    <div className="rounded-[1.7rem] bg-[#171A21] p-4">
                      <input
                        type="text"
                        value={searchTerm}
                        onChange={(event) => setSearchTerm(event.target.value)}
                        placeholder="Search brands, products, handles, or categories..."
                        className="w-full bg-transparent text-lg text-white outline-none placeholder:text-white/30"
                      />
                    </div>
                  </div>

                  <label className="pulse-zone flex min-h-[220px] cursor-pointer flex-col items-center justify-center rounded-[1.75rem] border border-dashed border-[#00F2FF]/45 bg-[rgba(0,242,255,0.05)] p-8 text-center">
                    <input type="file" accept="image/*" className="hidden" onChange={handleAssetChange} />
                    {uploadedAssetUrl ? (
                      <div className="w-full space-y-4">
                        <img
                          ref={uploadedImageRef}
                          src={uploadedAssetUrl}
                          alt={uploadedAssetName || "Uploaded reference"}
                          onLoad={() => setUploadedImageReady(true)}
                          className="mx-auto max-h-56 w-full max-w-sm rounded-[1.5rem] object-cover shadow-[0_18px_40px_rgba(0,0,0,0.32)]"
                        />
                        <div>
                          <p className="text-lg font-medium text-white">Reference image loaded</p>
                          <p className="mt-2 text-sm leading-6 text-white/52">
                            We use browser-side ML to extract visual labels from the image and
                            combine them with search hints and preview ranking.
                          </p>
                        </div>
                        <div className="flex flex-wrap justify-center gap-3">
                          <span className="rounded-full border border-white/10 bg-white/8 px-4 py-2 text-sm text-white/70">
                            {uploadedAssetName}
                          </span>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.preventDefault();
                              handleClearUpload();
                            }}
                            className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-sm text-white/78 transition hover:border-white/25 hover:text-white"
                          >
                            Remove image
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-full bg-[#00F2FF]/12 text-[#00F2FF] shadow-[0_0_28px_rgba(0,242,255,0.22)]">
                          <SearchIcon />
                        </div>
                        <p className="text-lg font-medium text-white">
                          Upload a reference image to refine product discovery
                        </p>
                        <p className="mt-2 max-w-sm text-sm leading-6 text-white/52">
                          We will preview the image here, pull search hints from its file name,
                          and show the strongest catalog matches on the right.
                        </p>
                        <span className="mt-5 rounded-full border border-white/10 bg-white/8 px-4 py-2 text-sm text-white/70">
                          No image selected yet
                        </span>
                      </>
                    )}
                  </label>

                  {mlSearchHint ? (
                    <div className="glass-cyber rounded-[1.75rem] p-5">
                      <p className="text-xs uppercase tracking-[0.3em] text-[#00F2FF]">
                        ML Assist
                      </p>
                      <p className="mt-3 text-sm leading-6 text-white/58">
                        Suggested query from the uploaded image:{" "}
                        <span className="font-medium text-white">{mlSearchHint}</span>
                      </p>
                      {mlPredictions.length > 0 ? (
                        <div className="mt-4 flex flex-wrap gap-3">
                          {mlPredictions.map((prediction) => (
                            <span key={prediction.label} className="soft-chip">
                              {prediction.label} {Math.round(prediction.score * 100)}%
                            </span>
                          ))}
                        </div>
                      ) : null}
                      <div className="mt-4 flex flex-wrap gap-3">
                        <button
                          type="button"
                          onClick={() => setSearchTerm(mlSearchHint)}
                          className="rounded-full bg-[#00F2FF] px-4 py-2 text-sm font-semibold text-[#071018] transition hover:shadow-[0_0_28px_rgba(0,242,255,0.42)]"
                        >
                          Use suggested query
                        </button>
                        <button
                          type="button"
                          onClick={() => setSearchTerm("")}
                          className="rounded-full border border-white/12 bg-white/5 px-4 py-2 text-sm font-semibold text-white/82 transition hover:border-white/25 hover:text-white"
                        >
                          Clear query
                        </button>
                      </div>
                    </div>
                  ) : uploadSearchHint ? (
                    <div className="glass-cyber rounded-[1.75rem] p-5">
                      <p className="text-xs uppercase tracking-[0.3em] text-[#00F2FF]">
                        Upload assist
                      </p>
                      <p className="mt-3 text-sm leading-6 text-white/58">
                        Backup hint from the file name:{" "}
                        <span className="font-medium text-white">{uploadSearchHint}</span>
                      </p>
                    </div>
                  ) : uploadedAssetName ? (
                    <div className="glass-cyber rounded-[1.75rem] p-5">
                      <p className="text-xs uppercase tracking-[0.3em] text-[#00F2FF]">
                        Upload assist
                      </p>
                      <p className="mt-3 text-sm leading-6 text-white/58">
                        No useful search hint was found in the file name, so the app kept your
                        search box unchanged.
                      </p>
                    </div>
                  ) : null}

                  <div className="glass-cyber rounded-[1.75rem] p-5">
                    <p className="text-xs uppercase tracking-[0.3em] text-[#00F2FF]">
                      Active filters
                    </p>
                    <div className="mt-4 flex flex-wrap gap-3">
                      <span className="soft-chip">{activeCategory}</span>
                      <span className="soft-chip">
                        {effectiveSearchTerm ? `Query: ${effectiveSearchTerm}` : "Broad scan"}
                      </span>
                      <span className="soft-chip">
                        {uploadedAssetName ? "Visual input loaded" : "Text-first mode"}
                      </span>
                      {uploadedAssetName ? (
                        <span className="soft-chip">
                          {visualMatchStatus === "ready"
                            ? "Visual similarity active"
                            : visualMatchStatus === "analyzing"
                              ? "Analyzing image"
                              : "Visual match unavailable"}
                        </span>
                      ) : null}
                      {uploadedAssetName ? (
                        <span className="soft-chip">
                          {mlStatus === "loading"
                            ? "Loading ML model"
                            : mlStatus === "classifying"
                              ? "Classifying image"
                              : mlStatus === "ready"
                                ? "ML assist ready"
                                : "ML assist unavailable"}
                        </span>
                      ) : null}
                    </div>
                  </div>
                </div>

                <div className="space-y-5">
                  <div className="flex items-end justify-between gap-4">
                    <div>
                      <p className="text-xs uppercase tracking-[0.3em] text-[#00F2FF]">
                        Results Preview
                      </p>
                      <h3 className="mt-2 text-2xl font-semibold text-white">
                        {previewResults.length > 0
                          ? "Product matches from different brands"
                          : "Brand matches from your discovery feed"}
                      </h3>
                    </div>
                    <p className="text-sm text-white/48">
                      {(previewResults.length || previewBrands.length)} result
                      {(previewResults.length || previewBrands.length) === 1 ? "" : "s"}
                    </p>
                  </div>

                  {uploadedAssetName ? (
                    <div className="glass-cyber rounded-[1.5rem] p-4 text-sm text-white/62">
                      {visualMatchStatus === "ready"
                        ? "Results are ranked by visual similarity first, then by image availability and niche score."
                        : visualMatchStatus === "analyzing"
                          ? "Analyzing the uploaded image and product thumbnails for visual similarity..."
                          : "Some remote product images cannot be analyzed by the browser, so those items fall back to text and catalog ranking."}
                    </div>
                  ) : null}

                  {!products.length ? (
                    <div className="glass-cyber rounded-[1.5rem] p-4 text-sm text-white/62">
                      Your project currently has brand data but no product rows in Supabase, so
                      this overlay is falling back to brand matches for now.
                    </div>
                  ) : null}

                  {previewResults.length === 0 && previewBrands.length === 0 ? (
                    <div className="glass-cyber rounded-[1.75rem] p-10 text-center text-white/55">
                      Nothing matched yet. Try another term or a different category pill.
                    </div>
                  ) : null}

                  {previewResults.length > 0 ? (
                    <motion.div
                      key={`preview-${activeCategory}-${searchTerm}`}
                      variants={staggerContainer}
                      initial="hidden"
                      animate="show"
                      className="columns-1 gap-5 md:columns-2"
                    >
                      {previewResults.map((product) => (
                        <motion.div
                          key={`preview-${product.id}`}
                          variants={fadeItem}
                          className="mb-5 break-inside-avoid"
                        >
                          {typeof product.visual_match_score === "number" ? (
                            <div className="mb-3 flex justify-end">
                              <span className="rounded-full border border-[#00F2FF]/25 bg-[#00F2FF]/10 px-3 py-1 text-xs font-medium text-[#7BF7FF]">
                                Visual match {Math.round(product.visual_match_score * 100)}%
                              </span>
                            </div>
                          ) : null}
                          <ProductCard
                            product={product}
                            onLike={onLike}
                            isLiking={likingProductId === product.id}
                            variant="compact"
                          />
                        </motion.div>
                      ))}
                    </motion.div>
                  ) : null}

                  {previewResults.length === 0 && previewBrands.length > 0 ? (
                    <motion.div
                      key={`brand-preview-${activeCategory}-${effectiveSearchTerm}`}
                      variants={staggerContainer}
                      initial="hidden"
                      animate="show"
                      className="columns-1 gap-5 md:columns-2"
                    >
                      {previewBrands.map((brand) => (
                        <motion.div
                          key={`brand-preview-${brand.id}`}
                          variants={fadeItem}
                          className="mb-5 break-inside-avoid"
                        >
                          <BrandCard brand={brand} />
                        </motion.div>
                      ))}
                    </motion.div>
                  ) : null}
                </div>
              </div>
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </main>
  );
}
