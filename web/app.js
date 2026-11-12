const $ = id => document.getElementById(id);
const colors = ["#19daff", "#ffc02b", "#f875ee", "#adfa45", "#ff7355", "#91a7ff", "#fafaff", "#24e3ab"];
const integer = new Intl.NumberFormat();
const dollars = new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 6,
});
const labels = {
  running: "Running", solved: "Solved", round_limit: "Round limit",
  agents_stopped: "Agents stopped", interrupted: "Interrupted", error: "Error",
};
let runs = [], selected = "", frames = [], offset = 0, cursor = 0;
let catalog = {levels: []}, selectedPuzzle = "", requestedRun = null, launching = false;
let follow = true, streaming = false, replay = false, playbackTimer = null;
let renderer = null, focus = null, comparing = false;
let animation = null, progress = 1;
let selection = 0;
let listing = "";
const cards = new Map();
const roundActions = new Map();
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function options(select, choices, fallback = "") {
  const old = select.value;
  select.replaceChildren(...choices.map(([value, label]) => {
    const option = el("option", "", label);
    option.value = value;
    return option;
  }));
  select.value = choices.some(([value]) => value === old) ? old : fallback;
}

function chosen() {
  return runs.find(run => run.id === selected);
}

function updateRecordings(preferred, fromStart) {
  const recordings = runs.filter(run => run.settings.level === selectedPuzzle);
  const next = [preferred, selected, recordings[0]?.id]
    .find(id => recordings.some(run => run.id === id)) ?? "";
  const select = $("runs");
  options(select, recordings.length ? recordings.map(run => [run.id, runLabel(run)]) : [["", "No recordings"]]);
  select.value = next;
  select.disabled = !recordings.length;
  if (next !== selected) selectRun(next, fromStart);
}

function selectPuzzle(id, preferred, fromStart = false) {
  selectedPuzzle = id;
  $("puzzle").value = id;
  updateRecordings(preferred, fromStart);
  renderLaunchControls();
  render();
}

function renderLaunchControls() {
  const level = catalog.levels.find(level => level.id === selectedPuzzle);
  const busy = launching || Boolean(requestedRun) || Boolean(catalog.active_run);
  $("run-demo").hidden = !catalog.can_launch || !level?.demo;
  $("run-demo").disabled = busy;
  $("run-model").hidden = !catalog.can_launch || !level || !catalog.model;
  $("run-model").disabled = busy;
  $("run-model").textContent = catalog.model ? "Run " + catalog.model : "";
}

async function launch(mode) {
  launching = true;
  $("launch-status").hidden = true;
  renderLaunchControls();
  const level = selectedPuzzle;
  try {
    const response = await fetch("/api/launch", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({level, mode}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error ?? "Could not start the run.");
    if (selectedPuzzle === level) requestedRun = data.id;
  } catch (error) {
    $("launch-status").textContent = error.message;
    $("launch-status").hidden = false;
  } finally {
    launching = false;
    renderLaunchControls();
  }
}

function selectRun(id, fromStart = false) {
  const revision = ++selection;
  selected = id;
  $("runs").value = id;
  frames = [];
  $("world-content").replaceChildren();
  roundActions.clear();
  offset = cursor = 0;
  follow = true;
  focus = renderer = null;
  stopReplay();
  const run = chosen();
  streaming = fromStart || run?.status === "running";
  if (animation !== null) cancelAnimationFrame(animation);
  animation = null;
  progress = 1;
  render();
  if (!run) return;
  import("/levels/" + encodeURIComponent(run.settings.level) + "/viewer.js")
    .then(module => {
      if (selection !== revision) return;
      renderer = module;
      render();
    }).catch(() => {});
}

function runLabel(run) {
  const model = run.settings.model === "demo" ? "Scripted demo" : run.settings.model;
  const stamp = new Date(run.started_at).toLocaleString([], {dateStyle: "short", timeStyle: "short"});
  return model + " · " + stamp + " · " + run.settings.seed;
}

async function get(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.json();
}

async function poll() {
  try {
    const [nextRuns, nextCatalog] = await Promise.all([get("/api/runs"), get("/api/levels")]);
    const signature = JSON.stringify([nextRuns, nextCatalog]);
    const requested = nextRuns.find(run => run.id === requestedRun);
    if (signature !== listing || requested) {
      listing = signature;
      runs = nextRuns;
      catalog = nextCatalog;
      const puzzles = new Map(catalog.levels.map(level => [level.id, level.title]));
      for (const run of runs) if (!puzzles.has(run.settings.level)) puzzles.set(run.settings.level, run.level_title);
      const newest = runs.find(run => run.id === catalog.active_run) ?? runs[0];
      const initialPuzzle = selectedPuzzle || newest?.settings.level || catalog.levels[0]?.id || "";
      options($("puzzle"), [...puzzles], initialPuzzle);
      $("puzzle").disabled = !puzzles.size;
      if (requested) requestedRun = null;
      selectPuzzle(initialPuzzle, requested?.id ?? (!selectedPuzzle ? newest?.id : undefined), Boolean(requested));
      if (comparing) refreshFilters();
    }
    if (selected) {
      const id = selected;
      const revision = selection;
      const result = await get("/api/runs/" + id + "/frames?after=" + offset);
      if (selection === revision && result.frames.length) {
        offset = result.next_offset;
        for (const frame of result.frames) {
          // Keep provider bookkeeping in the recording and show actions in the replay.
          if (frame.event.kind === "request" && !frame.event.error) continue;
          if (frame.event.kind === "tool") {
            if (!roundActions.has(frame.round)) roundActions.set(frame.round, []);
            roundActions.get(frame.round).push(frames.length);
          }
          frames.push(frame);
        }
        if (follow && !streaming) cursor = Math.max(0, frames.length - 1);
        render();
        schedulePlayback();
      }
    }
    $("connection").hidden = true;
  } catch {
    $("connection").textContent = "Can’t reach the local viewer. Retrying…";
    $("connection").hidden = false;
  } finally {
    setTimeout(poll, 450);
  }
}

function format(metric, value) {
  if (value == null) return "—";
  if (metric === "score") return value.toFixed(2);
  if (metric === "cost_usd") return dollars.format(value);
  if (metric.endsWith("_seconds")) return value.toFixed(1) + " s";
  return integer.format(value);
}

function usage(metrics, field) {
  return metrics?.missing_usage?.[field] ? null : metrics?.[field];
}

function renderMetrics(run, frame) {
  const metrics = frame.metrics;
  const ending = frame.event.kind === "finish";
  const status = frame.won ? "solved" : ending ? frame.event.status : "running";
  const pairs = [
    ["Status", labels[status] ?? status],
    ["Score", format("score", frame.score)],
    ["Elapsed", format("elapsed_seconds", frame.elapsed_seconds)],
    ["Round", integer.format(frame.round)],
    ["Tokens", run.settings.model === "demo" ? "—" : format("total_tokens", usage(metrics, "total_tokens"))],
    ["API cost", run.settings.model === "demo" ? "—" : format("cost_usd", usage(metrics, "cost_usd"))],
  ];
  $("metrics").replaceChildren(...pairs.map(([key, value]) => {
    const item = el("div", "metric");
    const data = el("dd", "", value);
    data.dataset.success = key === "Status" && frame.won;
    item.append(el("dt", "", key), data);
    return item;
  }));
}

function renderWorld(frame) {
  const canvas = $("arena");
  const content = $("world-content");
  content.hidden = !renderer?.renderWorld;
  canvas.hidden = !content.hidden || !renderer?.drawWorld;
  $("world-data").hidden = !canvas.hidden || !content.hidden;
  $("vision-control").hidden = !renderer?.supportsSight;
  $("world-title").textContent = renderer?.worldTitle ?? "Play area";
  if (!content.hidden) {
    renderer.renderWorld(content, frame.world, frames.slice(0, cursor + 1).map(step => step.event));
  } else if (canvas.hidden) {
    $("world-data").textContent = JSON.stringify(frame.world, null, 2);
  } else {
    renderer.drawWorld(canvas, frame.world, {
      color: id => colors[frame.agents.findIndex(agent => agent.id === id) % colors.length],
      focus,
      sight: $("vision").checked,
      actor: frame.event.kind === "tool" ? frame.event.agent : null,
      move: renderer.supportsMotion ? motionFor(frame) : null,
    });
  }
}

function motionFor(frame) {
  const event = frame.event;
  if (event.kind !== "tool" || !["moveX", "moveY"].includes(event.name) || !event.result?.moved) return null;
  const from = frames[cursor - 1]?.world.players?.find(player => player.id === event.agent);
  return from ? {from, progress} : null;
}

function messages(list, sent) {
  if (!list.length) return [el("p", "quiet", "None yet")];
  return [...list].reverse().map(message => {
    const node = el("div", "message");
    const route = sent
      ? "To " + (message.to.length ? message.to.join(", ") : "no other agents")
      : "From " + message.from;
    node.append(el("b", "", route), document.createTextNode(message.text));
    return node;
  });
}

function createCard(id) {
  const card = el("article", "panel agent");
  const header = el("div", "panel-header agent-header");
  const selector = el("button", "agent-selector");
  selector.type = "button";
  const avatar = el("span", "agent-dot", id);
  avatar.setAttribute("aria-hidden", "true");
  selector.append(avatar, el("span", "", "Agent " + id));
  selector.addEventListener("click", () => {
    focus = focus === id ? null : id;
    render();
  });
  const status = el("span", "agent-status");
  header.append(selector, status);
  const look = el("div", "look");
  const bottom = el("div", "agent-bottom");
  const sent = el("div", "messages");
  const received = el("div", "messages");
  const receivedTitle = el("h3");
  bottom.append(el("h3", "", "Sent"), sent, receivedTitle, received);
  card.append(header, look, bottom);
  const view = {card, selector, avatar, status, look, bottom, sent, received, receivedTitle};
  cards.set(id, view);
  return view;
}

function renderAgents(agents, actor) {
  const midpoint = Math.ceil(agents.length / 2);
  const rails = [$("left-rail"), $("right-rail")];
  for (const [id, view] of cards) {
    if (!agents.some(agent => agent.id === id)) {
      view.card.remove();
      cards.delete(id);
    }
  }
  agents.forEach((agent, index) => {
    const view = cards.get(agent.id) ?? createCard(agent.id);
    const rail = rails[Number(index >= midpoint)];
    if (view.card.parentNode !== rail) rail.append(view.card);
    view.card.style.setProperty("--agent", colors[index]);
    view.card.dataset.acting = actor === agent.id;
    if (view.renderer !== renderer) {
      if (renderer?.drawAgent) {
        const canvas = el("canvas");
        canvas.width = canvas.height = 56;
        renderer.drawAgent(canvas, agent.id, colors[index]);
        view.avatar.replaceChildren(canvas);
      } else {
        view.avatar.textContent = agent.id;
      }
    }
    view.selector.setAttribute("aria-pressed", focus === agent.id);
    view.selector.disabled = Boolean(renderer?.compactAgents);
    view.look.hidden = view.bottom.hidden = Boolean(renderer?.compactAgents);
    view.status.textContent = agent.status === "ready" && agent.last_action
      ? agent.last_action : agent.status;
    view.receivedTitle.textContent = agent.unread ? "Received · " + agent.unread + " unread" : "Received";
    for (const key of ["sent", "received"]) {
      const signature = JSON.stringify(agent[key]);
      if (view[key].dataset.signature !== signature) {
        view[key].dataset.signature = signature;
        view[key].replaceChildren(...messages(agent[key], key === "sent"));
      }
    }
    const signature = JSON.stringify(agent.last_look);
    if (signature === view.look.dataset.signature && view.renderer === renderer) return;
    view.look.dataset.signature = signature;
    view.renderer = renderer;
    if (!agent.last_look) {
      view.look.replaceChildren(el("p", "", "Waiting for first look"));
    } else if (renderer?.drawLook) {
      const canvas = el("canvas");
      canvas.width = canvas.height = 156;
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", agent.last_look.grid?.join("\n") ?? "Last observation");
      renderer.drawLook(canvas, agent.last_look, colors[index]);
      const clue = agent.last_look.inscriptions?.join(" ");
      view.look.replaceChildren(canvas);
      if (clue) view.look.append(el("p", "look-inscription", clue));
    } else {
      view.look.replaceChildren(el("pre", "", JSON.stringify(agent.last_look, null, 2)));
    }
  });
}

function describe(event) {
  const {kind, name, result, arguments: args} = event;
  if (kind === "tool") {
    if (result?.error) return {title: name + " rejected", detail: result.error, short: name + " rejected"};
    const levelAction = renderer?.describeAction?.(event);
    if (levelAction) return levelAction;
    if (["moveX", "moveY"].includes(name) && Number.isInteger(result?.moved)) {
      const {moved} = result;
      const direction = name === "moveX" ? ["left", "right"] : ["up", "down"];
      const sign = (moved || args?.steps) < 0 ? 0 : 1;
      const amount = Math.abs(moved);
      const arrow = (name === "moveX" ? ["←", "→"] : ["↑", "↓"])[sign];
      const title = moved ? `Moved ${direction[sign]} ${amount} ${amount === 1 ? "space" : "spaces"}` : "Blocked at the wall";
      return {title, short: moved ? arrow + " " + amount : "Wall",
        detail: moved !== args?.steps && args?.steps ? `Tried to move ${direction[sign]} ${Math.abs(args.steps)}.` : ""};
    }
    if (name === "look") return {title: "Looked down", short: "Look", detail: result?.inscriptions?.join(" ")};
    if (name === "lift") return {title: "Picked up " + result?.lifted, short: "Lift " + result?.lifted};
    if (name === "drop") return {title: "Placed " + result?.dropped, short: "Drop " + result?.dropped};
    if (name === "messageOtherAgent" || name === "messageAllOtherAgents") {
      const recipients = result?.delivered_to ?? [];
      const title = recipients.length ? "Messaged " + (recipients.length === 1 ? "Agent " : "Agents ") + recipients.join(", ")
        : "Sent a message";
      return {title, short: "Message", detail: args?.message};
    }
    if (name === "readMyMessages") {
      const count = result?.messages?.length ?? 0;
      const senders = [...new Set(result?.messages?.map(message => message.from))];
      return {title: count ? `Read ${count} ${count === 1 ? "message" : "messages"}` : "Checked inbox",
        short: count ? "Read " + count : "Inbox",
        detail: count ? "From " + (senders.length === 1 ? "Agent " : "Agents ") + senders.join(", ") : ""};
    }
    if (name === "whoAmI") return {title: "Checked own ID", short: "Own ID"};
    if (name === "listAgents") return {title: "Listed agents", short: "List agents"};
    return {title: name, short: name};
  }
  if (kind === "request") return {title: "Model request failed", detail: event.error};
  if (kind === "round") return {title: "Waiting for model replies"};
  if (kind === "agent_stopped") return {title: "Agent stopped", detail: event.reason ?? "No tool call"};
  if (kind === "finish") return {title: labels[event.status] ?? event.status};
  return {title: "Run started"};
}

function renderAction(frame) {
  const {agent} = frame.event;
  const actor = $("event-agent");
  const description = describe(frame.event);
  const agentColor = id => colors[frame.agents.findIndex(candidate => candidate.id === id)] ?? colors[0];
  actor.hidden = !agent;
  if (agent) {
    actor.textContent = "Agent " + agent;
    actor.style.setProperty("--agent", agentColor(agent));
  }
  $("event").textContent = description.title;
  $("event-detail").textContent = description.detail ?? "";

  const entries = roundActions.get(frame.round) ?? [];
  $("round-flow").hidden = !entries.length;
  if (!entries.length) return;
  $("round-label").textContent = "Round " + frame.round;
  const track = $("round-actions");
  const signature = selected + "/" + frame.round + "/" + entries.length + "/" + Boolean(renderer);
  if (track.dataset.signature !== signature) {
    track.dataset.signature = signature;
    track.replaceChildren(...entries.map(index => {
      const event = frames[index].event;
      const label = describe(event);
      const button = el("button", "action-chip");
      button.type = "button";
      button.title = "Agent " + event.agent + " · " + label.title;
      button.style.setProperty("--agent", agentColor(event.agent));
      button.append(el("b", "", event.agent), document.createTextNode(label.short));
      button.addEventListener("click", () => seek(index));
      return button;
    }));
  }
  entries.forEach((index, slot) => {
    const button = track.children[slot];
    if (index === cursor) {
      button.setAttribute("aria-current", "step");
      const visible = track.getBoundingClientRect();
      const bounds = button.getBoundingClientRect();
      if (bounds.right > visible.right) track.scrollLeft += bounds.right - visible.right;
      if (bounds.left < visible.left) track.scrollLeft -= visible.left - bounds.left;
    } else {
      button.removeAttribute("aria-current");
    }
  });
}

function isPlaying() {
  return replay || (follow && streaming && (chosen()?.status === "running" || cursor < frames.length - 1));
}

function render() {
  const run = chosen();
  const frame = frames[cursor];
  $("title").textContent = run?.level_title ?? catalog.levels.find(level => level.id === selectedPuzzle)?.title
    ?? "Waiting for a run";
  $("run-caption").textContent = run
    ? (run.settings.model === "demo" ? "Scripted demo" : run.settings.model) + " · Seed " + run.settings.seed
    : "Spectator view";
  $("empty").hidden = Boolean(frame);
  $("empty").textContent = run ? "Waiting for the first recorded frame…" : "No recordings for this puzzle yet.";
  $("observation").hidden = !frame;
  if (!frame || comparing) return;
  renderMetrics(run, frame);
  renderWorld(frame);
  renderAgents(frame.agents, frame.event.kind === "tool" ? frame.event.agent : null);
  renderAction(frame);
  $("timeline").max = frames.length - 1;
  $("timeline").value = cursor;
  const playing = isPlaying();
  $("play").disabled = !playing && frames.length < 2;
  $("play").textContent = playing ? "Pause" : "Play";
  $("play").setAttribute("aria-label", playing ? "Pause replay" : "Play replay");
  $("previous").disabled = cursor === 0;
  $("next").disabled = cursor === frames.length - 1;
  $("latest").disabled = follow && cursor === frames.length - 1;
  $("position").textContent = (cursor + 1) + " / " + frames.length;
  $("record-details").textContent = [
    "Level v" + run.level_version, run.settings.agents + (run.settings.agents === 1 ? " agent" : " agents"),
    run.settings.rounds + " round limit", frame.metrics.tool_calls + " tool calls",
    ...Object.entries(run.errors ?? {}).map(([id, error]) => "Agent " + id + " · " + error),
  ].join(" · ");
}

function stopReplay() {
  clearTimeout(playbackTimer);
  playbackTimer = null;
  replay = false;
}

function stepTo(index, animate = false) {
  cursor = index;
  if (animation !== null) cancelAnimationFrame(animation);
  animation = null;
  progress = 1;
  const frame = frames[cursor];
  if (animate && !reducedMotion.matches && renderer?.supportsMotion && motionFor(frame)) {
    progress = 0;
    const start = performance.now();
    const duration = Math.min(360, 180 / Number($("speed").value));
    const paint = time => {
      progress = Math.min(1, (time - start) / duration);
      renderWorld(frame);
      animation = progress < 1 ? requestAnimationFrame(paint) : null;
    };
    animation = requestAnimationFrame(paint);
  }
  render();
}

function seek(index, animate = false) {
  stopReplay();
  follow = false;
  stepTo(index, animate);
}

function schedulePlayback() {
  if (playbackTimer !== null || comparing || !(replay || (follow && streaming))
      || cursor >= frames.length - 1) return;
  const event = frames[cursor].event;
  const linger = ["look", "read", "submit"].includes(event.name) || event.name?.startsWith("message");
  const duration = linger ? 650 : event.kind === "round" ? 140 : 310;
  playbackTimer = setTimeout(() => {
    playbackTimer = null;
    if (cursor + 1 === frames.length - 1) replay = false;
    stepTo(cursor + 1, true);
    schedulePlayback();
  }, duration / Number($("speed").value));
}

$("puzzle").addEventListener("change", event => {
  requestedRun = null;
  $("launch-status").hidden = true;
  selectPuzzle(event.target.value);
});
$("runs").addEventListener("change", event => selectRun(event.target.value));
$("run-demo").addEventListener("click", () => launch("demo"));
$("run-model").addEventListener("click", () => launch("model"));
$("vision").addEventListener("change", render);
$("timeline").addEventListener("input", event => seek(Number(event.target.value)));
$("previous").addEventListener("click", () => seek(cursor - 1));
$("next").addEventListener("click", () => seek(cursor + 1, true));
$("speed").addEventListener("change", () => {
  clearTimeout(playbackTimer);
  playbackTimer = null;
  schedulePlayback();
});
$("latest").addEventListener("click", () => {
  stopReplay();
  follow = true;
  stepTo(frames.length - 1);
});
$("play").addEventListener("click", () => {
  if (isPlaying()) {
    seek(cursor);
  } else {
    follow = false;
    replay = true;
    stepTo(cursor === frames.length - 1 ? 0 : cursor);
    schedulePlayback();
  }
});

function showCompare(value) {
  comparing = value;
  $("spectate").hidden = value;
  $("compare").hidden = !value;
  const active = $(value ? "compare-tab" : "spectate-tab");
  const inactive = $(value ? "spectate-tab" : "compare-tab");
  active.setAttribute("aria-current", "page");
  inactive.removeAttribute("aria-current");
  if (value) {
    stopReplay();
    refreshFilters();
  } else {
    render();
    schedulePlayback();
  }
}

const levelKey = run => [run.settings.level, run.level_version, run.harness_version].join("/");
const recordedRuns = () => {
  const type = $("run-type").value;
  return runs.filter(run => run.status !== "running"
    && (!type || (run.settings.model === "demo" ? "demo" : "model") === type));
};

function runMetric(run, field) {
  if (run.settings.model === "demo" && (field.endsWith("_tokens") || field === "cost_usd")) return null;
  return run[field] ?? usage(run.metrics, field);
}

function refreshFilters() {
  const levels = new Map(catalog.levels.map(level => [
    [level.id, level.version, catalog.harness_version].join("/"),
    level.title + " v" + level.version + " · Harness v" + catalog.harness_version,
  ]));
  for (const run of runs) {
    levels.set(levelKey(run), run.level_title + " v" + run.level_version + " · Harness v" + run.harness_version);
  }
  const preferred = [...levels.keys()].find(key => key.startsWith(selectedPuzzle + "/"));
  options($("level-filter"), levels.size ? [...levels] : [["", "No levels"]], preferred ?? levels.keys().next().value);
  const seeds = [...new Set(recordedRuns().filter(run => levelKey(run) === $("level-filter").value)
    .map(run => run.settings.seed))].sort((a, b) => a - b);
  options($("seed-filter"), [["", "All seeds"], ...seeds.map(seed => [String(seed), String(seed)])]);
  renderComparison();
}

function renderComparison() {
  const seed = $("seed-filter").value;
  const data = recordedRuns().filter(run => levelKey(run) === $("level-filter").value
    && (!seed || String(run.settings.seed) === seed));
  const metric = $("metric-filter").value;
  const samples = data.filter(run => !["error", "interrupted"].includes(run.status))
    .map(run => [run, runMetric(run, metric)])
    .filter(([, value]) => value != null)
    .sort(([a], [b]) => a.settings.model.localeCompare(b.settings.model) || a.id.localeCompare(b.id));
  $("chart-note").textContent = !data.length
    ? "No completed runs match these filters."
    : !samples.length ? "No results are available for this metric."
    : "Each bar is one run. The table includes the seed, outcome and run settings.";
  const max = metric === "score" ? 1 : Math.max(0, ...samples.map(([, value]) => value));
  const models = [...new Set(samples.map(([run]) => run.settings.model))];
  $("chart").replaceChildren(...samples.map(([run, value]) => {
    const row = el("div", "bar-row");
    const track = el("div", "bar-track");
    const bar = el("div", "bar");
    bar.style.width = (max ? 100 * value / max : 0) + "%";
    bar.style.setProperty("--agent", colors[models.indexOf(run.settings.model) % colors.length]);
    track.append(bar);
    row.title = runLabel(run);
    const model = run.settings.model === "demo" ? "Scripted demo" : run.settings.model;
    row.append(el("span", "", model + " · " + run.settings.seed), track,
      el("span", "bar-value", format(metric, value)));
    return row;
  }));

  const table = el("table");
  const head = table.createTHead().insertRow();
  ["Run", "Model", "Provider", "Seed", "Result", "Score", "Elapsed", "Tokens", "API cost", "Setup"]
    .forEach(value => head.append(el("th", "", value)));
  const body = table.createTBody();
  for (const run of data) {
    const settings = run.settings;
    const row = body.insertRow();
    const replayButton = el("button", "button", "Watch");
    replayButton.type = "button";
    replayButton.title = runLabel(run);
    replayButton.addEventListener("click", () => {
      selectPuzzle(run.settings.level, run.id);
      showCompare(false);
    });
    row.insertCell().append(replayButton);
    const resolved = run.models?.join(", ");
    const values = [
      settings.model === "demo" ? "Scripted demo"
        : resolved && resolved !== settings.model ? settings.model + " → " + resolved : settings.model,
      run.providers?.join(", ") || "—",
      settings.seed,
      labels[run.status] ?? run.status,
      format("score", run.score),
      format("elapsed_seconds", run.elapsed_seconds),
      format("total_tokens", runMetric(run, "total_tokens")),
      format("cost_usd", runMetric(run, "cost_usd")),
      settings.agents + (settings.agents === 1 ? " agent" : " agents") + " · " + settings.rounds + " rounds"
        + (settings.model === "demo" ? "" : " · " + settings.max_completion_tokens + " max reply tokens · "
          + (settings.reasoning_effort ?? "default reasoning")),
    ];
    values.forEach(value => row.append(el("td", "", value)));
  }
  $("history").replaceChildren(...(data.length ? [table] : []));
}

$("spectate-tab").addEventListener("click", () => showCompare(false));
$("compare-tab").addEventListener("click", () => showCompare(true));
$("level-filter").addEventListener("change", refreshFilters);
$("run-type").addEventListener("change", refreshFilters);
$("metric-filter").addEventListener("change", renderComparison);
$("seed-filter").addEventListener("change", renderComparison);
poll();
