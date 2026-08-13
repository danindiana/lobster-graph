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

async function main() {
  const statusEl = document.getElementById("status");
  const legendEl = document.getElementById("legend");
  const tooltipEl = document.getElementById("tooltip");
  const canvasHost = document.getElementById("graph");

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
