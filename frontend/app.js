/*
 * EasyCode frontend.
 *
 * Deliberately dependency-free: the whole UI is one script served by the same
 * FastAPI process as the API, so `docker compose up` is the only build step.
 */

const API = "/api";

const STAGES = [
  ["queued", "Queued"],
  ["cloning", "Cloning repository"],
  ["parsing", "Parsing source files"],
  ["generating_embeddings", "Generating embeddings"],
  ["building_graph", "Building the code graph"],
  ["indexing", "Writing the repository overview"],
  ["ready", "Ready"],
];

const EXAMPLE_QUESTIONS = [
  "What does this repository do?",
  "How is this application structured?",
  "Where is authentication handled?",
  "What happens when a request comes in?",
  "What is the entry point of this application?",
];

const state = {
  repo: null,
  files: [],
  poll: null,
  activeFile: null,
};

// --------------------------------------------------------------- utilities

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]
  );
}

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : detail;
    } catch (_) {
      /* response had no JSON body */
    }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

/** Minimal Markdown rendering: headings, lists, bold, inline code, paragraphs. */
function renderMarkdown(text) {
  const lines = String(text || "").split("\n");
  const out = [];
  let inList = false;

  const closeList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };

  const inline = (s) =>
    escapeHtml(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  for (const raw of lines) {
    const line = raw.trimEnd();
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    const bullet = line.match(/^[-*]\s+(.*)$/);
    const numbered = line.match(/^\d+\.\s+(.*)$/);

    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 1, 6);
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
    } else if (bullet || numbered) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return out.join("\n");
}

function show(element, visible) {
  element.classList.toggle("hidden", !visible);
}

// ------------------------------------------------------------------ health

async function loadHealth() {
  const container = $("service-health");
  try {
    const health = await api("/health");
    const llmLabel = health.llm_available
      ? `LLM: ${health.llm_model}`
      : "LLM: none (extractive answers)";
    container.innerHTML = [
      `<span class="pill ${health.qdrant ? "ok" : "bad"}">Qdrant</span>`,
      `<span class="pill ${health.neo4j ? "ok" : "bad"}">Neo4j</span>`,
      `<span class="pill ${health.llm_available ? "ok" : ""}">${escapeHtml(llmLabel)}</span>`,
    ].join("");
  } catch (_) {
    container.innerHTML = '<span class="pill bad">API unreachable</span>';
  }
}

// ------------------------------------------------------------ known repos

async function loadKnownRepositories() {
  let repos = [];
  try {
    repos = await api("/repositories");
  } catch (_) {
    return;
  }
  show($("known-repos"), repos.length > 0);
  $("repo-list").innerHTML = repos
    .map(
      (repo) => `
      <li>
        <div>
          <div class="repo-name">${escapeHtml(repo.name || repo.url)}</div>
          <div class="repo-meta">${escapeHtml(repo.status)} ·
            ${repo.file_count} files · ${repo.entity_count} entities</div>
        </div>
        <div class="row">
          <button data-open="${escapeHtml(repo.id)}"
            ${repo.status === "ready" ? "" : "disabled"}>Open</button>
          <button class="secondary" data-delete="${escapeHtml(repo.id)}">Delete</button>
        </div>
      </li>`
    )
    .join("");

  $("repo-list")
    .querySelectorAll("[data-open]")
    .forEach((button) =>
      button.addEventListener("click", () => openRepository(button.dataset.open))
    );
  $("repo-list")
    .querySelectorAll("[data-delete]")
    .forEach((button) =>
      button.addEventListener("click", async () => {
        button.disabled = true;
        await api(`/repositories/${button.dataset.delete}`, { method: "DELETE" });
        loadKnownRepositories();
      })
    );
}

// -------------------------------------------------------------- indexing

$("repo-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = $("repo-url").value.trim();
  const button = $("submit-button");
  show($("submit-error"), false);
  button.disabled = true;

  try {
    const repo = await api("/repositories", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    startPolling(repo);
  } catch (error) {
    $("submit-error").textContent = error.message;
    show($("submit-error"), true);
  } finally {
    button.disabled = false;
  }
});

function renderStages(status, error) {
  const currentIndex = STAGES.findIndex(([key]) => key === status);
  $("stages").innerHTML = STAGES.map(([key, label], index) => {
    let className = "";
    if (status === "failed") {
      className = index === 0 ? "failed" : "";
    } else if (currentIndex >= 0 && index < currentIndex) {
      className = "done";
    } else if (index === currentIndex) {
      className = key === "ready" ? "done" : "active";
    }
    return `<li class="${className}"><span class="dot"></span>${label}</li>`;
  }).join("");

  show($("progress-error"), Boolean(error));
  if (error) $("progress-error").textContent = error;
}

function startPolling(repo) {
  state.repo = repo;
  show($("submit-panel"), false);
  show($("progress-panel"), true);
  show($("workspace"), false);
  $("progress-name").textContent = repo.name || repo.url;
  renderStages(repo.status, null);

  clearInterval(state.poll);
  state.poll = setInterval(async () => {
    let status;
    try {
      status = await api(`/repositories/${repo.id}/status`);
    } catch (error) {
      clearInterval(state.poll);
      renderStages("failed", error.message);
      return;
    }

    renderStages(status.status, status.error);
    $("progress-detail").textContent =
      status.entity_count > 0
        ? `${status.file_count} files · ${status.entity_count} entities · ` +
          `${status.chunk_count} chunks · ${status.graph_node_count} graph nodes`
        : "";

    if (status.status === "ready") {
      clearInterval(state.poll);
      openRepository(repo.id);
    } else if (status.status === "failed") {
      clearInterval(state.poll);
    }
  }, 1200);
}

// -------------------------------------------------------------- workspace

async function openRepository(repoId) {
  const repo = await api(`/repositories/${repoId}`);
  state.repo = repo;

  show($("submit-panel"), false);
  show($("progress-panel"), false);
  show($("workspace"), true);
  switchTab("overview");

  renderStats(repo);
  loadOverview(repoId);
  loadFiles(repoId);
  loadGraph(repoId);
  renderExamples();
  $("answer-area").innerHTML = "";
}

function renderStats(repo) {
  const stats = [
    [repo.file_count, "files"],
    [repo.entity_count, "entities"],
    [repo.chunk_count, "chunks"],
    [repo.graph_node_count, "graph nodes"],
    [repo.graph_relationship_count, "relationships"],
    [`${repo.indexing_seconds}s`, "indexing time"],
  ];
  $("repo-stats").innerHTML = stats
    .map(
      ([value, label]) =>
        `<div class="stat"><div class="value">${escapeHtml(value)}</div>
         <div class="label">${label}</div></div>`
    )
    .join("");
}

async function loadOverview(repoId) {
  const data = await api(`/repositories/${repoId}/overview`);
  const languages = data.languages.length
    ? `<p class="muted">Languages: ${escapeHtml(data.languages.join(", "))}` +
      (data.frameworks.length
        ? ` · Frameworks: ${escapeHtml(data.frameworks.join(", "))}`
        : "") +
      "</p>"
    : "";
  $("overview-text").innerHTML =
    languages +
    (data.overview
      ? renderMarkdown(data.overview)
      : "<p class='muted'>No overview was generated for this repository.</p>");
}

// -------------------------------------------------------------------- ask

function renderExamples() {
  $("examples").innerHTML = EXAMPLE_QUESTIONS.map(
    (question) => `<button class="example" type="button">${escapeHtml(question)}</button>`
  ).join("");
  $("examples")
    .querySelectorAll(".example")
    .forEach((button) =>
      button.addEventListener("click", () => {
        $("question").value = button.textContent;
        $("ask-form").requestSubmit();
      })
    );
}

$("ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.repo) return;

  const question = $("question").value.trim();
  const mode = document.querySelector('input[name="mode"]:checked').value;
  const button = $("ask-button");
  button.disabled = true;
  $("answer-area").innerHTML =
    `<div class="answer-card"><span class="spinner"></span> Retrieving context and ` +
    `generating an answer…</div>`;

  try {
    const result = await api(`/repositories/${state.repo.id}/query`, {
      method: "POST",
      body: JSON.stringify({ query: question, mode, generate: true }),
    });
    renderAnswer(result);
  } catch (error) {
    $("answer-area").innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
  }
});

function renderAnswer(result) {
  const tags = [
    `<span class="tag ${escapeHtml(result.question_type)}">${escapeHtml(
      result.question_type
    )}</span>`,
    `<span class="tag">${result.vector_hits} vector hits</span>`,
    `<span class="tag">${result.graph_hits} graph hits</span>`,
    `<span class="tag">${escapeHtml(result.model || "no model")}</span>`,
    `<span class="tag">${result.latency_seconds.toFixed(1)}s</span>`,
  ];
  if (!result.grounded) {
    tags.push(
      `<span class="tag warn">${result.unverified_citations.length} unverified ` +
        `citation(s) removed</span>`
    );
  }

  const facts = result.graph_facts.length
    ? `<div class="facts"><h4>Structural facts from the code graph</h4><ul>${result.graph_facts
        .map((fact) => `<li>${escapeHtml(fact)}</li>`)
        .join("")}</ul></div>`
    : "";

  const sources = result.sources.length
    ? `<div class="sources"><h4>Retrieved sources</h4>${result.sources
        .map(
          (source, index) => `
        <div class="source-item" data-index="${index}">
          <div class="source-head">
            <span class="badge ${escapeHtml(source.source)}">${escapeHtml(source.source)}</span>
            <span class="cite">${escapeHtml(source.file_path)}:${source.start_line}-${
            source.end_line
          }</span>
            <span class="name">${escapeHtml(source.entity_type)} ${escapeHtml(
            source.entity_name
          )}</span>
            ${source.relationship ? `<span class="rel">${escapeHtml(source.relationship)}</span>` : ""}
          </div>
          <div class="source-body"><pre><code>${escapeHtml(source.content)}</code></pre></div>
        </div>`
        )
        .join("")}</div>`
    : "";

  $("answer-area").innerHTML = `
    <div class="answer-card">
      <div class="answer-meta">${tags.join("")}</div>
      <div class="prose" style="border:none;padding:0;background:none">
        ${renderMarkdown(result.answer)}
      </div>
      ${facts}
      ${sources}
    </div>`;

  // Clicking a source expands its code; clicking the citation opens the file.
  $("answer-area")
    .querySelectorAll(".source-item")
    .forEach((item) => {
      item.querySelector(".source-head").addEventListener("click", (event) => {
        if (event.target.classList.contains("cite")) {
          const source = result.sources[Number(item.dataset.index)];
          switchTab("files");
          openFile(source.file_path, source.start_line, source.end_line);
          return;
        }
        item.classList.toggle("open");
      });
    });
}

// ------------------------------------------------------------------ files

async function loadFiles(repoId) {
  const data = await api(`/repositories/${repoId}/files`);
  state.files = data.files;
  renderFileList("");
}

function renderFileList(filter) {
  const term = filter.toLowerCase();
  const matches = state.files.filter((file) =>
    file.file_path.toLowerCase().includes(term)
  );
  $("file-list").innerHTML = matches
    .map(
      (file) =>
        `<li data-path="${escapeHtml(file.file_path)}"${
          file.file_path === state.activeFile ? ' class="active"' : ""
        }>${escapeHtml(file.file_path)}</li>`
    )
    .join("");
  $("file-list")
    .querySelectorAll("li")
    .forEach((item) =>
      item.addEventListener("click", () => openFile(item.dataset.path))
    );
}

$("file-filter").addEventListener("input", (event) => renderFileList(event.target.value));

async function openFile(path, startLine, endLine) {
  if (!state.repo) return;
  state.activeFile = path;
  renderFileList($("file-filter").value);
  $("file-view-header").textContent = path;

  let data;
  try {
    data = await api(
      `/repositories/${state.repo.id}/file?file_path=${encodeURIComponent(path)}`
    );
  } catch (error) {
    $("file-content").innerHTML = `<code class="muted">${escapeHtml(error.message)}</code>`;
    return;
  }

  const lines = data.content.split("\n");
  $("file-content").innerHTML = `<code>${lines
    .map((line, index) => {
      const number = index + 1;
      const highlighted =
        startLine && number >= startLine && number <= (endLine || startLine);
      return `<span class="line${highlighted ? " highlight" : ""}"><span class="line-no">${number}</span>${escapeHtml(
        line
      )}</span>`;
    })
    .join("\n")}</code>`;

  if (startLine) {
    const target = $("file-content").querySelector(".line.highlight");
    if (target) target.scrollIntoView({ block: "center" });
  } else {
    $("file-content").parentElement.scrollTop = 0;
  }
}

// ------------------------------------------------------------------ graph

async function loadGraph(repoId) {
  const data = await api(`/repositories/${repoId}/graph`);

  const nodes = data.statistics.nodes || {};
  const relationships = data.statistics.relationships || {};
  $("graph-stats").innerHTML =
    '<div class="kv">' +
    Object.entries(nodes)
      .map(([k, v]) => `<span class="k">${escapeHtml(k)}</span><span class="v">${v}</span>`)
      .join("") +
    Object.entries(relationships)
      .map(
        ([k, v]) =>
          `<span class="k">${escapeHtml(k.toLowerCase().replace(/_/g, " "))}</span>` +
          `<span class="v">${v}</span>`
      )
      .join("") +
    "</div>";

  $("central-files").innerHTML = data.central_files
    .map(
      (row) =>
        `<li><span>${escapeHtml(row.file_path)}</span><span class="n">${row.importers}</span></li>`
    )
    .join("");

  $("entry-points").innerHTML = data.entry_points
    .map(
      (row) =>
        `<li><span>${escapeHtml(row.file_path)}</span><span class="n">${row.dependencies}</span></li>`
    )
    .join("");

  drawGraph();
}

// ------------------------------------------------- graph visualisation

/*
 * A small force-directed layout, written out rather than pulled from d3, to
 * keep the frontend build-free. Three forces: repulsion between every pair,
 * spring attraction along edges, and a weak pull toward the centre. Cooling
 * alpha stops it once it settles.
 */
const SVG_NS = "http://www.w3.org/2000/svg";

const LANGUAGE_COLORS = {
  python: "#5fd3a0",
  javascript: "#e5c07b",
  typescript: "#6ea8fe",
  markdown: "#b98cff",
  java: "#e06c75",
};
const DEFAULT_COLOR = "#7f8aa3";
const CALL_COLORS = { function: "#6ea8fe", method: "#b98cff" };

const graph = {
  nodes: [],
  links: [],
  selected: null,
  frame: null,
  transform: { x: 0, y: 0, k: 1 },
};

function nodeColor(node) {
  if (graph.scope === "calls") {
    return node.focus ? "#ffffff" : CALL_COLORS[node.entity_type] || DEFAULT_COLOR;
  }
  return LANGUAGE_COLORS[node.language] || DEFAULT_COLOR;
}

function nodeRadius(node) {
  if (graph.scope === "calls") return node.focus ? 11 : 7;
  // Area grows with how many files depend on this one.
  return Math.min(5 + Math.sqrt(node.importers || 0) * 3.5, 20);
}

async function drawGraph() {
  if (!state.repo) return;

  const scope = document.querySelector('input[name="graph-scope"]:checked').value;
  const focus = $("graph-focus").value.trim();
  const svg = $("graph-canvas");
  const empty = $("graph-empty");

  if (graph.frame) cancelAnimationFrame(graph.frame);
  svg.innerHTML = "";
  empty.classList.remove("show");
  $("graph-detail").textContent = "Loading…";

  if (scope === "calls" && !focus) {
    showGraphEmpty(
      "Enter a function or method name above, then press Draw, to see what calls it and what it calls."
    );
    return;
  }

  let data;
  try {
    const params = new URLSearchParams({ scope, limit: "150" });
    if (scope === "calls") params.set("focus", focus);
    data = await api(`/repositories/${state.repo.id}/graph/network?${params}`);
  } catch (error) {
    showGraphEmpty(error.message);
    return;
  }

  graph.scope = data.scope;

  if (!data.nodes.length) {
    showGraphEmpty(
      scope === "calls"
        ? `No call relationships found for “${escapeHtml(focus)}”. ` +
            `Try a name from the Files tab, or switch to Imports.`
        : "This repository has no import relationships to draw. " +
          "Structural parsing currently covers Python only, so a repository in " +
          "another language will have an empty graph."
    );
    return;
  }

  const rect = svg.getBoundingClientRect();
  const width = rect.width || 800;
  const height = rect.height || 480;

  graph.nodes = data.nodes.map((node, index) => ({
    ...node,
    // Seed on a circle so the layout unfolds predictably instead of exploding.
    x: width / 2 + Math.cos((index / data.nodes.length) * 2 * Math.PI) * Math.min(width, height) * 0.3,
    y: height / 2 + Math.sin((index / data.nodes.length) * 2 * Math.PI) * Math.min(width, height) * 0.3,
    vx: 0,
    vy: 0,
  }));

  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  graph.links = data.edges
    .map((edge) => ({ source: byId.get(edge.source), target: byId.get(edge.target) }))
    .filter((link) => link.source && link.target && link.source !== link.target);

  renderGraph(svg, width, height);
  runSimulation(width, height);

  const label =
    data.scope === "calls"
      ? `Call graph around <strong>${escapeHtml(data.focus)}</strong>: ` +
        `${data.nodes.length} nodes, ${data.edges.length} calls.`
      : `Import graph: <strong>${data.nodes.length}</strong> files, ` +
        `${data.edges.length} import edges` +
        (data.truncated
          ? `, showing the ${data.nodes.length} most connected of ${data.total_nodes}`
          : "") +
        ". Node size = how many files import it.";
  $("graph-detail").innerHTML = `${label} Click a node to inspect it.`;
}

function showGraphEmpty(message) {
  const empty = $("graph-empty");
  empty.innerHTML = message;
  empty.classList.add("show");
  $("graph-legend").innerHTML = "";
  $("graph-detail").textContent = "";
}

function renderGraph(svg, width, height) {
  const root = document.createElementNS(SVG_NS, "g");
  root.setAttribute("id", "graph-root");
  svg.appendChild(root);

  const linkLayer = document.createElementNS(SVG_NS, "g");
  const nodeLayer = document.createElementNS(SVG_NS, "g");
  root.append(linkLayer, nodeLayer);

  for (const link of graph.links) {
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("class", "link");
    link.el = line;
    linkLayer.appendChild(line);
  }

  for (const node of graph.nodes) {
    const group = document.createElementNS(SVG_NS, "g");
    group.setAttribute("class", "node");

    const circle = document.createElementNS(SVG_NS, "circle");
    circle.setAttribute("r", nodeRadius(node));
    circle.setAttribute("fill", nodeColor(node));
    circle.setAttribute("stroke", "#10131b");
    circle.setAttribute("stroke-width", "1.5");

    const text = document.createElementNS(SVG_NS, "text");
    text.setAttribute("dy", nodeRadius(node) + 11);
    text.textContent = node.label;

    const title = document.createElementNS(SVG_NS, "title");
    title.textContent =
      graph.scope === "calls"
        ? `${node.entity_type} ${node.label}\n${node.file_path}:${node.start_line}-${node.end_line}`
        : `${node.id}\n${node.importers} importers, ${node.imports} imports, ` +
          `${node.definitions} definitions`;

    group.append(circle, text, title);
    node.el = group;
    nodeLayer.appendChild(group);

    group.addEventListener("mousedown", (event) => startNodeDrag(event, node, svg));
    group.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!node.moved) selectNode(node);
    });
  }

  svg.addEventListener("click", () => selectNode(null));
  attachPanZoom(svg, root);
  renderLegend();
}

function renderLegend() {
  const entries =
    graph.scope === "calls"
      ? [
          ["#ffffff", "focus"],
          [CALL_COLORS.function, "function"],
          [CALL_COLORS.method, "method"],
        ]
      : [...new Set(graph.nodes.map((n) => n.language))]
          .filter(Boolean)
          .map((language) => [LANGUAGE_COLORS[language] || DEFAULT_COLOR, language]);

  $("graph-legend").innerHTML = entries
    .map(
      ([color, label]) =>
        `<span><span class="swatch" style="background:${color}"></span>${escapeHtml(label)}</span>`
    )
    .join("");
}

function runSimulation(width, height) {
  const REPULSION = 5200;
  const SPRING = 0.014;
  const SPRING_LENGTH = 90;
  const CENTER_PULL = 0.0016;
  const DAMPING = 0.86;

  let alpha = 1;

  function step() {
    alpha *= 0.985;

    for (let i = 0; i < graph.nodes.length; i++) {
      const a = graph.nodes[i];
      for (let j = i + 1; j < graph.nodes.length; j++) {
        const b = graph.nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let distanceSq = dx * dx + dy * dy;
        if (distanceSq < 1) {
          // Coincident nodes would divide by zero; nudge them apart.
          dx = Math.random() - 0.5;
          dy = Math.random() - 0.5;
          distanceSq = 1;
        }
        const distance = Math.sqrt(distanceSq);
        const force = REPULSION / distanceSq;
        const fx = (dx / distance) * force;
        const fy = (dy / distance) * force;
        if (!a.fixed) { a.vx -= fx; a.vy -= fy; }
        if (!b.fixed) { b.vx += fx; b.vy += fy; }
      }
    }

    for (const link of graph.links) {
      const dx = link.target.x - link.source.x;
      const dy = link.target.y - link.source.y;
      const distance = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = (distance - SPRING_LENGTH) * SPRING;
      const fx = (dx / distance) * force;
      const fy = (dy / distance) * force;
      if (!link.source.fixed) { link.source.vx += fx; link.source.vy += fy; }
      if (!link.target.fixed) { link.target.vx -= fx; link.target.vy -= fy; }
    }

    for (const node of graph.nodes) {
      if (node.fixed) continue;
      node.vx += (width / 2 - node.x) * CENTER_PULL;
      node.vy += (height / 2 - node.y) * CENTER_PULL;
      node.vx *= DAMPING;
      node.vy *= DAMPING;
      node.x += node.vx * alpha;
      node.y += node.vy * alpha;
    }

    paint();
    if (alpha > 0.006) graph.frame = requestAnimationFrame(step);
  }

  graph.frame = requestAnimationFrame(step);
}

function paint() {
  for (const link of graph.links) {
    link.el.setAttribute("x1", link.source.x);
    link.el.setAttribute("y1", link.source.y);
    link.el.setAttribute("x2", link.target.x);
    link.el.setAttribute("y2", link.target.y);
  }
  for (const node of graph.nodes) {
    node.el.setAttribute("transform", `translate(${node.x},${node.y})`);
  }
}

function selectNode(node) {
  graph.selected = node;

  if (!node) {
    graph.nodes.forEach((n) => n.el.classList.remove("dimmed", "selected"));
    graph.links.forEach((l) => l.el.classList.remove("dimmed", "highlight"));
    $("graph-detail").innerHTML = "Click a node to inspect it.";
    return;
  }

  const connected = new Set([node]);
  for (const link of graph.links) {
    if (link.source === node) connected.add(link.target);
    if (link.target === node) connected.add(link.source);
  }

  for (const other of graph.nodes) {
    other.el.classList.toggle("dimmed", !connected.has(other));
    other.el.classList.toggle("selected", other === node);
  }
  for (const link of graph.links) {
    const touches = link.source === node || link.target === node;
    link.el.classList.toggle("highlight", touches);
    link.el.classList.toggle("dimmed", !touches);
  }

  renderNodeDetail(node);
}

function renderNodeDetail(node) {
  const detail = $("graph-detail");

  if (graph.scope === "calls") {
    const callers = graph.links.filter((l) => l.target === node).map((l) => l.source.label);
    const callees = graph.links.filter((l) => l.source === node).map((l) => l.target.label);
    detail.innerHTML =
      `<strong>${escapeHtml(node.entity_type)} ${escapeHtml(node.label)}</strong> — ` +
      `<span class="cite" data-path="${escapeHtml(node.file_path)}" ` +
      `data-start="${node.start_line}" data-end="${node.end_line}">` +
      `${escapeHtml(node.file_path)}:${node.start_line}-${node.end_line}</span>` +
      (callers.length ? `<br>Called by: ${escapeHtml(callers.join(", "))}` : "") +
      (callees.length ? `<br>Calls: ${escapeHtml(callees.join(", "))}` : "");
  } else {
    const importers = graph.links.filter((l) => l.target === node).map((l) => l.source.label);
    const imports = graph.links.filter((l) => l.source === node).map((l) => l.target.label);
    detail.innerHTML =
      `<strong><span class="cite" data-path="${escapeHtml(node.id)}">` +
      `${escapeHtml(node.id)}</span></strong> — ${node.definitions} definitions` +
      (importers.length ? `<br>Imported by: ${escapeHtml(importers.join(", "))}` : "") +
      (imports.length ? `<br>Imports: ${escapeHtml(imports.join(", "))}` : "");
  }

  detail.querySelectorAll(".cite").forEach((el) =>
    el.addEventListener("click", () => {
      switchTab("files");
      openFile(
        el.dataset.path,
        el.dataset.start ? Number(el.dataset.start) : undefined,
        el.dataset.end ? Number(el.dataset.end) : undefined
      );
    })
  );
}

function startNodeDrag(event, node, svg) {
  event.preventDefault();
  event.stopPropagation();
  node.moved = false;
  node.fixed = true;

  const move = (moveEvent) => {
    node.moved = true;
    const point = toGraphCoords(svg, moveEvent);
    node.x = point.x;
    node.y = point.y;
    node.vx = 0;
    node.vy = 0;
    paint();
  };
  const up = () => {
    node.fixed = false;
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
}

function toGraphCoords(svg, event) {
  const rect = svg.getBoundingClientRect();
  const { x, y, k } = graph.transform;
  return {
    x: (event.clientX - rect.left - x) / k,
    y: (event.clientY - rect.top - y) / k,
  };
}

function attachPanZoom(svg, root) {
  graph.transform = { x: 0, y: 0, k: 1 };

  const apply = () => {
    const { x, y, k } = graph.transform;
    root.setAttribute("transform", `translate(${x},${y}) scale(${k})`);
  };

  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = svg.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    const k = Math.max(0.2, Math.min(4, graph.transform.k * factor));
    // Keep the point under the cursor stationary while zooming.
    graph.transform.x = mx - ((mx - graph.transform.x) * k) / graph.transform.k;
    graph.transform.y = my - ((my - graph.transform.y) * k) / graph.transform.k;
    graph.transform.k = k;
    apply();
  }, { passive: false });

  svg.addEventListener("mousedown", (event) => {
    if (event.target.closest(".node")) return;
    svg.classList.add("dragging");
    const startX = event.clientX - graph.transform.x;
    const startY = event.clientY - graph.transform.y;

    const move = (moveEvent) => {
      graph.transform.x = moveEvent.clientX - startX;
      graph.transform.y = moveEvent.clientY - startY;
      apply();
    };
    const up = () => {
      svg.classList.remove("dragging");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });
}

$("graph-redraw").addEventListener("click", drawGraph);
$("graph-focus").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    document.querySelector('input[name="graph-scope"][value="calls"]').checked = true;
    drawGraph();
  }
});
document
  .querySelectorAll('input[name="graph-scope"]')
  .forEach((radio) => radio.addEventListener("change", drawGraph));

$("graph-lookup").addEventListener("click", async () => {
  const name = $("graph-query").value.trim();
  if (!name || !state.repo) return;

  const id = state.repo.id;
  const encoded = encodeURIComponent(name);
  const [callers, callees, classes] = await Promise.all([
    api(`/repositories/${id}/graph/callers?function_name=${encoded}`).catch(() => []),
    api(`/repositories/${id}/graph/callees?function_name=${encoded}`).catch(() => []),
    api(`/repositories/${id}/graph/class?class_name=${encoded}`).catch(() => []),
  ]);

  const groups = [];
  if (callers.length) {
    groups.push(
      group(
        `Callers of ${name}`,
        callers.map(
          (row) =>
            `${row.caller_type} ${row.caller_name} — ${row.caller_file}:${row.caller_start}-${row.caller_end}`
        )
      )
    );
  }
  if (callees.length) {
    groups.push(
      group(
        `${name} calls`,
        callees.map(
          (row) =>
            `${row.callee_type} ${row.callee_name} — ${row.callee_file}:${row.callee_start}-${row.callee_end}`
        )
      )
    );
  }
  for (const info of classes) {
    if (info.methods.length) {
      groups.push(
        group(
          `Methods of ${info.class_name}`,
          info.methods.map((m) => `${m.name} — ${m.file_path}:${m.start_line}-${m.end_line}`)
        )
      );
    }
    if (info.bases.length) {
      groups.push(group(`${info.class_name} inherits from`, info.bases.map((b) => `${b.name} — ${b.file_path}`)));
    }
    if (info.subclasses.length) {
      groups.push(group(`Subclasses of ${info.class_name}`, info.subclasses.map((s) => `${s.name} — ${s.file_path}`)));
    }
  }

  $("graph-results").innerHTML = groups.length
    ? groups.join("")
    : `<p class="hint">No relationships found for “${escapeHtml(name)}”. This box takes a
       single function, method or class <em>name</em> — not a file path or a phrase.
       Try one of the names listed in the Files tab, for example
       <code>${escapeHtml(exampleEntityName())}</code>.</p>`;
});

/** A real name from this repository, so the error message is actionable. */
function exampleEntityName() {
  const node = graph.nodes.find((n) => graph.scope === "calls" && n.label);
  return node ? node.label : "AuthService";
}

function group(title, entries) {
  return `<div class="rel-group"><h4>${escapeHtml(title)}</h4><ul>${entries
    .map((entry) => `<li>${escapeHtml(entry)}</li>`)
    .join("")}</ul></div>`;
}

// -------------------------------------------------------------------- tabs

function switchTab(name) {
  document
    .querySelectorAll(".tab[data-tab]")
    .forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  document
    .querySelectorAll(".tab-panel")
    .forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
}

document
  .querySelectorAll(".tab[data-tab]")
  .forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));

$("new-repo").addEventListener("click", () => {
  clearInterval(state.poll);
  state.repo = null;
  show($("workspace"), false);
  show($("progress-panel"), false);
  show($("submit-panel"), true);
  $("repo-url").value = "";
  loadKnownRepositories();
});

// ------------------------------------------------------------------- init

loadHealth();
loadKnownRepositories();
