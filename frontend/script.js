const API_BASE_URL = "http://127.0.0.1:8000";

let activeCategory = null;
let selectedFile = null;

const categoryTree = [
  {
    label: "Clothing",
    children: [
      {
        label: "Men's",
        children: [
          { label: "Jeans", id: 4 },
          { label: "Tops", id: 6 },
        ],
      },
      {
        label: "Women's",
        children: [
          { label: "Jeans", id: 5 },
          { label: "Dresses", id: 7 },
        ],
      },
    ],
  },
  {
    label: "Accessories",
    children: [
      { label: "Jewelry", id: 9 },
      { label: "Bags", id: 10 },
    ],
  },
  {
    label: "Home Decor",
    children: [{ label: "Decor", id: 11 }],
  },
];

const megaMenu = document.querySelector("#megaMenu");
const searchForm = document.querySelector("#searchForm");
const textQuery = document.querySelector("#textQuery");
const imageInput = document.querySelector("#imageInput");
const cameraButton = document.querySelector("#cameraButton");
const statusLine = document.querySelector("#statusLine");
const resultsGrid = document.querySelector("#resultsGrid");

function createCategoryButton(item) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = item.label;
  button.className = `category-button${activeCategory === item.id ? " active" : ""}`;
  button.addEventListener("click", () => {
    activeCategory = item.id;
    renderMenu();
    discover();
  });
  return button;
}

function renderMenu() {
  megaMenu.innerHTML = "";

  const resetButton = document.createElement("button");
  resetButton.type = "button";
  resetButton.textContent = "All";
  resetButton.className = `reset-button${activeCategory === null ? " active" : ""}`;
  resetButton.addEventListener("click", () => {
    activeCategory = null;
    renderMenu();
    discover();
  });
  megaMenu.appendChild(resetButton);

  categoryTree.forEach((group) => {
    const groupSection = document.createElement("section");
    groupSection.className = "menu-group";
    groupSection.innerHTML = `<h2>${group.label}</h2>`;

    const columns = document.createElement("div");
    columns.className = "menu-columns";

    group.children.forEach((child) => {
      if (child.children) {
        const column = document.createElement("div");
        column.className = "menu-column";
        column.innerHTML = `<span>${child.label}</span>`;
        child.children.forEach((item) => column.appendChild(createCategoryButton(item)));
        columns.appendChild(column);
      } else {
        columns.appendChild(createCategoryButton(child));
      }
    });

    groupSection.appendChild(columns);
    megaMenu.appendChild(groupSection);
  });
}

async function discover() {
  if (!textQuery.value.trim() && !selectedFile) {
    statusLine.textContent = "Enter a search phrase or upload an image first.";
    return;
  }

  const formData = new FormData();
  if (selectedFile) {
    formData.append("image_file", selectedFile);
  } else {
    formData.append("text_query", textQuery.value.trim());
  }

  if (activeCategory !== null) {
    formData.append("category_id", String(activeCategory));
  }

  statusLine.textContent = "Discovering visually similar pieces...";

  try {
    const response = await fetch(`${API_BASE_URL}/api/discover`, {
      method: "POST",
      body: formData,
    });
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(payload.detail || "Discovery request failed.");
    }

    renderResults(payload.results || []);
  } catch (error) {
    resultsGrid.innerHTML = "";
    statusLine.textContent = error.message || "Unable to run discovery right now.";
  }
}

function renderResults(products) {
  resultsGrid.innerHTML = "";

  products.forEach((product) => {
    const card = document.createElement("article");
    card.className = "product-card";

    const similarity = (Number(product.similarity || 0) * 100).toFixed(1);
    const imageMarkup = product.image_url
      ? `<img src="${product.image_url}" alt="${product.item_name || "Product"}" />`
      : `<div class="image-placeholder" aria-hidden="true"></div>`;

    card.innerHTML = `
      ${imageMarkup}
      <div class="product-body">
        <div class="product-meta">
          <span>${product.brand_name || "Unknown brand"}</span>
          <strong>${similarity}%</strong>
        </div>
        <h2>${product.item_name || "Untitled item"}</h2>
        <p>${product.description || "No description available."}</p>
      </div>
    `;

    resultsGrid.appendChild(card);
  });

  statusLine.textContent = `${products.length} matching product(s) found.`;
}

cameraButton.addEventListener("click", () => imageInput.click());

imageInput.addEventListener("change", () => {
  selectedFile = imageInput.files[0] || null;
  if (selectedFile) {
    textQuery.value = "";
    discover();
  }
});

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  selectedFile = null;
  imageInput.value = "";
  discover();
});

renderMenu();
