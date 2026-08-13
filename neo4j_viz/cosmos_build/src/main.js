import { Graph } from "@cosmos.gl/graph";

const PALETTE = {
  Paper: "#ff6b6b",
  Concept: "#4dabf7",
  Theorem: "#ffd43b",
  Algorithm: "#69db7c",
  CodeSnippet: "#da77f2",
  Diagram: "#ff922b",
};
const DEFAULT_COLOR = "#adb5bd";

const SIZE = {
  Paper: 9,
  Concept: 5,
  Theorem: 5,
  Algorithm: 5,
  CodeSnippet: 4,
  Diagram: 4,
};
const DEFAULT_SIZE = 3;

function hexToRgba(hex, alpha = 1) {
  const n = parseInt(hex.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255, alpha];
}

function buildLegend(container) {
  const rows = Object.entries(PALETTE)
    .map(
      ([type, color]) =>
        `<div class="legend-row"><span class="swatch" style="background:${color}"></span>${type}</div>`
    )
    .join("");
  container.innerHTML = rows;
}

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

// Per-type (label, propKey, {pre: renderAsMonospaceBlock}) field lists for the
// detail modal. Fields not listed here (besides the excluded ones) fall back
// to a generic "other properties" dump so nothing stored on the node is hidden.
const DETAIL_FIELDS = {
  Paper: [
    ["Motivation & Problem", "motivation"],
    ["Methodology", "methodology"],
    ["Key Contributions", "contributions"],
    ["Limitations", "limitations"],
    ["Significance", "significance"],
    ["Extras", "extras"],
    ["Page Count", "page_count"],
    ["Processed At", "processed_at"],
    ["Source PDF Path", "pdf_path"],
  ],
  Concept: [["Definition", "definition"]],
  Theorem: [["Statement", "statement"]],
  Algorithm: [
    ["Pseudocode", "pseudocode", { pre: true }],
    ["Invariant", "invariant"],
  ],
  CodeSnippet: [
    ["Language", "language"],
    ["Code", "code", { pre: true }],
  ],
  Diagram: [
    ["Graphviz Source", "dot_src", { pre: true }],
    ["SVG Path (relative to its dataset's _processed/)", "svg_path"],
  ],
};
const DETAIL_EXCLUDE = new Set(["fx", "fy", "name", "title"]);

function renderNodeDetail(type, props, id) {
  const heading = escapeHtml(props.name ?? props.title ?? `${type} #?`);
  const fields = DETAIL_FIELDS[type] || [];
  const shown = new Set(fields.map(([, key]) => key));

  let body = fields
    .map(([label, key, opts]) => {
      const value = props[key];
      if (value === undefined || value === null || value === "") return "";
      const html = opts?.pre
        ? `<pre>${escapeHtml(value)}</pre>`
        : `<p>${escapeHtml(value)}</p>`;
      return `<div class="detail-field"><h3>${escapeHtml(label)}</h3>${html}</div>`;
    })
    .join("");

  const rest = Object.entries(props).filter(
    ([key]) => !DETAIL_EXCLUDE.has(key) && !shown.has(key)
  );
  if (rest.length) {
    body += rest
      .map(
        ([key, value]) =>
          `<div class="detail-field"><h3>${escapeHtml(key)}</h3><p>${escapeHtml(value)}</p></div>`
      )
      .join("");
  }

  let pdfButton = "";
  if (type === "Paper") {
    pdfButton = `<a class="pdf-button" href="/pdf/${id}" target="_blank" rel="noopener">Open source PDF ↗</a>`;
  }

  return `
    <div class="detail-header">
      <span class="detail-type" style="color:${PALETTE[type] || DEFAULT_COLOR}">${escapeHtml(type)}</span>
      <h2>${heading}</h2>
      ${pdfButton}
    </div>
    <div class="detail-body">${body || "<p>No additional properties stored.</p>"}</div>
  `;
}

const DOUBLE_CLICK_MS = 400;

async function main() {
  const statusEl = document.getElementById("status");
  const legendEl = document.getElementById("legend");
  const tooltipEl = document.getElementById("tooltip");
  const canvasHost = document.getElementById("graph");
  const modalEl = document.getElementById("modal");
  const modalContentEl = document.getElementById("modal-content");
  const modalCloseEl = document.getElementById("modal-close");

  function closeModal() {
    modalEl.style.display = "none";
  }
  modalCloseEl.addEventListener("click", closeModal);
  modalEl.addEventListener("click", (e) => {
    if (e.target === modalEl) closeModal();
  });

  async function openNodeDetail(id, type, fallbackLabel) {
    // window.open must happen synchronously within the click handler (before
    // any await) or browsers treat it as a non-user-initiated popup and block it.
    if (type === "Paper") {
      window.open(`/pdf/${id}`, "_blank", "noopener");
    }

    modalEl.style.display = "flex";
    modalContentEl.innerHTML = `<div class="detail-header"><h2>${escapeHtml(fallbackLabel)}</h2></div><div class="detail-body"><p>Loading…</p></div>`;
    try {
      const res = await fetch(`/api/node/${id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { type: fetchedType, props } = await res.json();
      modalContentEl.innerHTML = renderNodeDetail(fetchedType || type, props, id);
    } catch (err) {
      modalContentEl.innerHTML = `<div class="detail-header"><h2>${escapeHtml(fallbackLabel)}</h2></div><div class="detail-body"><p>Failed to load details: ${escapeHtml(err.message)}</p></div>`;
    }
  }

  buildLegend(legendEl);
  statusEl.textContent = "Loading graph from Neo4j...";

  const res = await fetch("/api/graph");
  if (!res.ok) {
    statusEl.textContent = `Failed to load graph: ${res.status}`;
    return;
  }
  const { nodes, edges } = await res.json();

  const indexById = new Map();
  nodes.forEach((n, i) => indexById.set(n.id, i));

  const positions = new Float32Array(nodes.length * 2);
  const colors = new Float32Array(nodes.length * 4);
  const sizes = new Float32Array(nodes.length);

  nodes.forEach((n, i) => {
    positions[i * 2] = n.fx;
    positions[i * 2 + 1] = n.fy;
    const [r, g, b, a] = hexToRgba(PALETTE[n.type] || DEFAULT_COLOR, 1);
    colors[i * 4] = r;
    colors[i * 4 + 1] = g;
    colors[i * 4 + 2] = b;
    colors[i * 4 + 3] = a;
    sizes[i] = SIZE[n.type] ?? DEFAULT_SIZE;
  });

  const links = new Float32Array(edges.length * 2);
  let linkCount = 0;
  for (const e of edges) {
    const s = indexById.get(e.source);
    const t = indexById.get(e.target);
    if (s === undefined || t === undefined) continue;
    links[linkCount * 2] = s;
    links[linkCount * 2 + 1] = t;
    linkCount++;
  }
  const trimmedLinks = linkCount === edges.length ? links : links.slice(0, linkCount * 2);

  let lastClick = null; // { index, time } — cosmos.gl has no native dblclick event
  const handlePointClick = (index) => {
    const n = nodes[index];
    if (!n) return;
    const now = Date.now();
    if (lastClick && lastClick.index === index && now - lastClick.time < DOUBLE_CLICK_MS) {
      lastClick = null;
      openNodeDetail(n.id, n.type, n.label);
    } else {
      lastClick = { index, time: now };
    }
  };

  const graph = new Graph(canvasHost, {
    enableSimulation: false,
    backgroundColor: "#101014",
    pointDefaultSize: DEFAULT_SIZE,
    linkDefaultColor: "#666666",
    linkOpacity: 0.06,
    linkWidthScale: 0.5,
    renderHoveredPointRing: true,
    hoveredPointRingColor: "#ffffff",
    fitViewOnInit: true,
    fitViewPadding: 0.15,
    onPointMouseOver: (index, pointPosition) => {
      const n = nodes[index];
      if (!n) return;
      tooltipEl.style.display = "block";
      tooltipEl.textContent = `${n.type}: ${n.label}`;
    },
    onPointMouseOut: () => {
      tooltipEl.style.display = "none";
    },
    onPointClick: (index) => {
      if (index !== undefined) handlePointClick(index);
    },
    onMouseMove: (index, pointPosition, event) => {
      if (event) {
        tooltipEl.style.left = `${event.clientX + 12}px`;
        tooltipEl.style.top = `${event.clientY + 12}px`;
      }
    },
  });

  graph.setPointPositions(positions);
  graph.setPointColors(colors);
  graph.setPointSizes(sizes);
  graph.setLinks(trimmedLinks);
  graph.render();

  statusEl.textContent = `${nodes.length.toLocaleString()} nodes / ${linkCount.toLocaleString()} edges (static GPU-precomputed layout)`;
}

main().catch((err) => {
  document.getElementById("status").textContent = `Error: ${err.message}`;
  console.error(err);
});
