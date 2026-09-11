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
}

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
    : `<p class="hint">No relationships found for “${escapeHtml(name)}”.</p>`;
});

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
