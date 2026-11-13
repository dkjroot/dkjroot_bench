import {agentColors as colors, drawAgent} from "/agent-sprites.js";
import {squadPicker} from "/squad.js";
import {teamEditor} from "/teams.js";

const $ = id => document.getElementById(id);
const integer = new Intl.NumberFormat();
const dollars = new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 6,
});
const dollarsToCents = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
// https://ai.google.dev/gemini-api/docs/pricing
// Standard paid USD per million tokens for this benchmark's text and image requests.
const geminiRates = new Map([
  ["gemini-3.8-flash", [
    {since: "2027-01-01", input: 1.50, cached: 0.15, output: 7.50},
    {since: "", input: 0.75, cached: 0.075, output: 3.75},
  ]],
  ["gemini-3.1-flash-lite", [{since: "", input: 0.25, cached: 0.025, output: 1.50}]],
  ["gemini-3.5-flash-lite", [{since: "", input: 0.30, cached: 0.03, output: 2.50}]],
  ["gemini-2.5-flash", [{since: "", input: 0.30, cached: 0.03, output: 2.50}]],
]);
const estimatedCostNote = "Estimated using Google's published standard paid rates at the start of the run. "
  + "Cached input is discounted when recorded. Actual billing may differ.";
const labels = {
  running: "Running", paused: "Paused", solved: "Solved", round_limit: "Round limit",
  agents_stopped: "Agents stopped", interrupted: "Interrupted", error: "Error",
};
let runs = [], selected = "", frames = [], offset = 0, cursor = 0;
let catalog = {levels: []}, selectedPuzzle = "", requestedRun = null, launching = false, stoppingRun = null;
let changingPause = false, roundInputRun = null, nextRounds = "";
let pacingRun = null, deletingUnpublished = false;
let follow = true, streaming = false, replay = false, playbackFrame = null;
let playback = null, activeBubble = null, bubbleTimer = null;
let spritePulse = 0;
let preview = null, managing = false;
let modelProfiles = [], editingProfile = null, savingProfile = false;
let openRouterModels = new Map(), catalogLoading = false;
let savedTeams = [], teamPage = false;
let profileLoad = 0;
const apiNames = {
  openrouter: "OpenRouter", gemini: "Google Gemini Direct", openai: "OpenAI Direct",
  anthropic: "Anthropic Direct", xai: "xAI Direct", deepseek: "DeepSeek Direct",
};
const familyNames = new Map([
  ["google", "Gemini"], ["openai", "OpenAI GPT"], ["anthropic", "Claude"],
  ["x-ai", "Grok"], ["deepseek", "DeepSeek"], ["qwen", "Qwen"],
]);
const directFamilies = {gemini: "google", xai: "x-ai"};
function profileFamily(profile) {
  if (profile.backend !== "openrouter") return directFamilies[profile.backend] ?? profile.backend;
  const [author, model] = profile.model.split("/");
  return author === "google" && !model.startsWith("gemini-") ? "google-other" : author;
}
const hiddenProfileApis = new Set();
const hiddenProfileFamilies = new Set();
const profileRanges = {
  "profile-input-cap": ["input_usd", (value, limit) => value <= limit],
  "profile-output-cap": ["output_usd", (value, limit) => value <= limit],
  "profile-context-min": ["context", (value, limit) => value >= limit],
  "profile-context-max": ["context", (value, limit) => value <= limit],
};
const profileFlags = {"profiles-tools": "tools", "profiles-vision": "image", "profiles-free": "free"};
const rateFormat = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 6});
const recordingUpdates = new Set();
let commentRun = null;
let renderer = null, focus = null, comparing = false;
let animation = null, progress = 1;
let selection = 0;
let listing = "";
let messageCursor = -1;
const cards = new Map();
const messageRows = new Map();
const roundActions = new Map();
const lastImageFrame = [];
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

const identity = id => {
  const run = chosen();
  return run?.team?.agents?.[id] ?? run?.characters?.[id];
};
const agentName = id => identity(id)?.name ?? "Agent " + id;
const agentBadge = id => identity(id)?.badge ?? id;
const runName = run => run.team?.name ?? run.profile_name
  ?? (run.settings.model === "demo" ? "Scripted demo" : run.settings.model);

function localRecording() {
  const run = chosen();
  return run?.public_id ? runs.find(local => local.id === run.public_id && local.same_public_replay) : run;
}

function updateRecordings(preferred, fromStart) {
  const duplicates = new Map(runs.filter(run => run.settings.level === selectedPuzzle
      && run.same_public_replay && run.id !== catalog.active_run)
    .map(run => [run.id, "git/" + run.id]));
  const recordings = runs.filter(run => run.settings.level === selectedPuzzle && !duplicates.has(run.id));
  const next = [preferred, selected]
    .map(id => duplicates.get(id) ?? id)
    .find(id => recordings.some(run => run.id === id)) ?? "";
  const select = $("runs");
  options(select, [["", recordings.length ? "Choose a recording" : "No recordings"],
    ...recordings.map(run => [run.id, runLabel(run)])]);
  select.value = next;
  select.disabled = !recordings.length;
  if (next !== selected) selectRun(next, fromStart);
}

function selectPuzzle(id, preferred, fromStart = false) {
  const changed = selectedPuzzle !== id;
  const previous = selected;
  selectedPuzzle = id;
  $("puzzle").value = id;
  updateRecordings(preferred, fromStart);
  if (changed && !selected && !previous) loadPreview(id);
  renderLaunchControls();
  render();
}

function renderLaunchControls() {
  const level = catalog.levels.find(level => level.id === selectedPuzzle);
  const active = catalog.active_run ?? null;
  const busy = launching || Boolean(requestedRun || active);
  const stopping = Boolean(active && (catalog.stopping || stoppingRun === active));
  const paused = Boolean(active && catalog.pause_reason && !stopping);
  const exhausted = paused && catalog.paused_round >= catalog.max_rounds;
  const canDemo = catalog.can_demo && level?.demo;
  const picker = $("model-profile");
  const teamChoices = savedTeams
    .map(team => ["team:" + team.id, team.name + " · " + team.agents.length + (team.agents.length === 1 ? " agent" : " agents")]);
  const profiles = modelProfiles.filter(profile => profile.enabled).map(profile => [profile.id, profile.name]);
  if (catalog.model) profiles.push([":configured", catalog.model + " · Terminal settings"]);
  const launchChoices = [...teamChoices, ...profiles];
  const choices = launchChoices.length ? launchChoices : [["", "Add a model profile"]];
  if (picker.dataset.choices !== JSON.stringify(choices)) {
    options(picker, choices, choices[0][0]);
    picker.dataset.choices = JSON.stringify(choices);
  }
  const profile = modelProfiles.find(profile => profile.id === picker.value && profile.enabled);
  const team = savedTeams.find(team => "team:" + team.id === picker.value);
  squad.show(team, level, busy);
  const canModel = catalog.can_launch && level && picker.value && (!team || squad.valid());
  $("launch-rpm-control").hidden = Boolean(active) || !picker.value || !level;
  $("launch-rpm").disabled = busy;
  $("model-profile-control").hidden = !(catalog.can_manage_profiles || catalog.model);
  picker.disabled = busy || !launchChoices.length;
  const recording = localRecording();
  const saved = catalog.can_pause && recording?.resumable && !active
    && recording.harness_version === catalog.harness_version && recording.level_version === level?.version
    && (recording.settings.model !== "demo" || catalog.can_demo) ? recording : null;
  const defaults = catalog.round_defaults;
  const pacingId = paused && catalog.model_run ? active : saved && saved.settings.model !== "demo" ? saved.id : null;
  const rpm = $("request-rpm");
  if (pacingRun !== pacingId) {
    pacingRun = pacingId;
    rpm.value = (active ? catalog.request_rpm : saved?.request_rpm) ?? "";
  }
  $("request-pacing").hidden = !pacingId;
  rpm.disabled = changingPause || launching || exhausted;
  const modelRounds = team || profile ? (team ?? profile).rounds ?? level?.model_rounds : defaults?.model;
  const input = $("round-limit");
  if (roundInputRun !== active) {
    if (!roundInputRun) nextRounds = input.value;
    roundInputRun = active;
    input.value = active ? (catalog.round_limit ?? "") : nextRounds;
  }
  if (active && !paused) input.value = catalog.round_limit ?? "";
  $("round-limit-control").hidden = !(active || canDemo || canModel || saved);
  input.disabled = active ? !paused || changingPause || exhausted : busy || !(canDemo || canModel || saved);
  input.min = paused ? catalog.paused_round + 1 : 1;
  input.max = catalog.max_rounds ?? 1000;
  input.placeholder = (active ? catalog.round_limit : saved?.settings.rounds ?? (canModel ? modelRounds : defaults?.demo)) ?? "Default";
  input.title = paused ? "Set the total round limit for this run." : "Leave blank for the default for each run.";
  $("run-demo").hidden = !canDemo;
  $("run-demo").disabled = busy;
  $("run-demo").title = defaults ? "Default demo limit: " + defaults.demo + " rounds" : "";
  $("run-model").hidden = !(catalog.can_manage_profiles || catalog.model);
  $("run-model").disabled = busy || !canModel;
  $("run-model").textContent = team ? "Run squad" : "Run model";
  $("run-model").title = canModel ? "Default round limit: " + modelRounds
    : team ? "Pick a squad that fits the puzzle." : "Add a key under Model profiles.";
  $("continue-run").hidden = !saved;
  $("continue-run").disabled = busy;
  $("pause-run").hidden = !active || !catalog.can_pause;
  $("pause-run").disabled = stopping || Boolean(catalog.pausing) || changingPause || exhausted;
  $("pause-run").textContent = paused ? "Resume run" : catalog.pausing ? "Pausing run…" : "Pause run";
  $("stop-run").hidden = !active;
  $("stop-run").disabled = stopping;
  $("stop-run").textContent = stopping ? "Stopping run…" : "Stop run";
  const notice = $("run-pause-status");
  notice.hidden = !paused || !["error", "round_limit"].includes(catalog.pause_reason);
  if (!notice.hidden) {
    notice.textContent = catalog.pause_reason === "error" ? "Paused on a model error in round " + (catalog.paused_round + 1) + ". Resume to retry the failed calls."
      : exhausted ? "Reached the maximum of " + catalog.max_rounds + " rounds. Stop the run to save the replay."
      : "Paused at round " + catalog.paused_round + ". Increase Max rounds to resume.";
  }
}

async function postRun(path, body, failure) {
  const response = await fetch("/api/" + path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error ?? failure);
  if (data.run_state && body.id === catalog.active_run && data.run_state.active_run === body.id) {
    Object.assign(catalog, data.run_state);
  }
  return data;
}

function showRunError(error) {
  $("launch-status").textContent = error.message;
  $("launch-status").hidden = false;
}

async function launch(mode) {
  const input = $("round-limit");
  if (!input.reportValidity()) return;
  const level = selectedPuzzle;
  const continuing = mode === "continue";
  const request = continuing ? {id: localRecording().id} : {level, mode};
  if (continuing && !addPacing(request)) return;
  if (mode === "model" && !addPacing(request, $("launch-rpm"))) return;
  const profile = $("model-profile").value;
  if (mode === "model" && profile.startsWith("team:")) {
    if (!squad.valid()) return;
    request.mode = "team";
    request.team_id = profile.slice(5);
    request.squad = squad.selected();
    request.team_revision = savedTeams.find(team => team.id === request.team_id).revision;
  } else if (mode === "model" && profile !== ":configured") {
    request.profile_id = profile;
  }
  if (input.value !== "") request.rounds = input.valueAsNumber;
  launching = true;
  $("launch-status").hidden = true;
  renderLaunchControls();
  try {
    const data = await postRun(continuing ? "continue" : "launch", request, "Could not start the run.");
    if (selectedPuzzle === level) {
      if (continuing && localRecording()?.id === data.id) {
        if (selected !== data.id) selectRun(data.id);
        stopReplay();
        follow = true;
        streaming = false;
        if (frames.length) stepTo(frames.length - 1);
      } else {
        requestedRun = data.id;
      }
    }
  } catch (error) {
    showRunError(error);
    if (request.mode === "team") teams.load();
  } finally {
    launching = false;
    renderLaunchControls();
  }
}

function addPacing(request, input = $("request-rpm")) {
  if (input === $("request-rpm") && $("request-pacing").hidden) return true;
  if (!input.reportValidity()) return false;
  request.pacing = {rpm: input.value === "" ? null : input.valueAsNumber};
  return true;
}

async function toggleRunPause() {
  const id = catalog.active_run;
  const resuming = Boolean(catalog.pause_reason);
  const input = $("round-limit");
  if (resuming && !input.reportValidity()) return;
  const body = {id};
  if (resuming && !addPacing(body)) return;
  if (resuming && input.value !== "") body.rounds = input.valueAsNumber;
  changingPause = true;
  $("launch-status").hidden = true;
  renderLaunchControls();
  try {
    const action = resuming ? "resume" : "pause";
    await postRun(action, body, "Could not " + action + " the run.");
  } catch (error) {
    showRunError(error);
  } finally {
    changingPause = false;
    renderLaunchControls();
  }
}

async function stopRun() {
  const id = catalog.active_run;
  if (!id || catalog.stopping || stoppingRun === id) return;
  stoppingRun = id;
  $("launch-status").hidden = true;
  renderLaunchControls();
  try {
    await postRun("stop", {id}, "Could not stop the run.");
  } catch (error) {
    if (stoppingRun === id) stoppingRun = null;
    showRunError(error);
    renderLaunchControls();
  }
}

function selectRun(id, fromStart = false) {
  const revision = ++selection;
  selected = id;
  $("runs").value = id;
  frames = [];
  $("world-content").replaceChildren();
  messageCursor = -1;
  messageRows.clear();
  $("communications-list").replaceChildren();
  roundActions.clear();
  lastImageFrame.length = 0;
  $("agent-observation").hidden = true;
  offset = cursor = 0;
  focus = renderer = null;
  stopReplay();
  playback = null;
  clearBubble();
  const run = chosen();
  follow = fromStart || Boolean(run && run.id === catalog.active_run);
  streaming = fromStart || run?.status === "running";
  renderLaunchControls();
  progress = 1;
  render();
  if (!run) {
    loadPreview(selectedPuzzle);
    return;
  }
  import("/levels/" + encodeURIComponent(run.settings.level) + "/viewer.js")
    .then(module => {
      if (selection !== revision) return;
      renderer = module;
      render();
    }).catch(() => {});
}

function loadPreview(level) {
  preview = renderer = null;
  const revision = ++selection;
  if (!catalog.levels.some(item => item.id === level)) return;
  $("world-content").replaceChildren();
  Promise.all([get("/api/preview?level=" + encodeURIComponent(level)),
    import("/levels/" + encodeURIComponent(level) + "/viewer.js")])
    .then(([frame, module]) => {
      if (selection !== revision || selected) return;
      preview = frame;
      renderer = module;
      render();
    }).catch(() => {});
}

function runLabel(run) {
  const model = runName(run);
  const stamp = new Date(run.started_at).toLocaleString([], {dateStyle: "short", timeStyle: "short"});
  return model + " · " + stamp + " · " + run.settings.seed + (run.public_id ? " · Public" : "");
}

async function get(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.json();
}

function renderProfileFilters(id, choices, excluded) {
  const group = $(id);
  const signature = JSON.stringify(choices);
  if (group.dataset.choices !== signature) {
    group.dataset.choices = signature;
    const all = el("label", "toggle", "All");
    const master = el("input");
    master.type = "checkbox";
    master.setAttribute("aria-label", id === "profile-api-filters" ? "All API providers" : "All model families");
    master.addEventListener("change", () => {
      for (const [value] of choices) {
        if (master.checked) excluded.delete(value);
        else excluded.add(value);
      }
      renderProfiles();
    });
    all.prepend(master);
    group.replaceChildren(group.querySelector("legend"), all, ...choices.map(([value, text]) => {
      const label = el("label", "toggle", text);
      const input = el("input");
      input.type = "checkbox";
      input.value = value;
      input.addEventListener("change", () => {
        if (input.checked) excluded.delete(value);
        else excluded.add(value);
        renderProfiles();
      });
      label.prepend(input);
      return label;
    }));
  }
  const [master, ...inputs] = group.querySelectorAll("input");
  for (const input of inputs) input.checked = !excluded.has(input.value);
  master.checked = inputs.every(input => input.checked);
  master.indeterminate = !master.checked && inputs.some(input => input.checked);
}

function renderProfiles() {
  const savedRouterModels = new Set(modelProfiles.filter(profile => profile.backend === "openrouter")
    .map(profile => profile.model));
  const rows = [...modelProfiles, ...[...openRouterModels.values()]
    .filter(model => !savedRouterModels.has(model.model))
    .map(model => ({name: model.name, model: model.model, backend: "openrouter", enabled: false}))];
  const metadata = profile => profile.backend === "openrouter" ? openRouterModels.get(profile.model) : undefined;
  const families = [...new Set(rows.map(profileFamily))]
    .map(family => [family, family === "google-other" ? "Google (other models)" : familyNames.get(family) ?? family])
    .sort((a, b) => a[1].localeCompare(b[1]));
  renderProfileFilters("profile-api-filters", Object.entries(apiNames), hiddenProfileApis);
  renderProfileFilters("profile-family-filters", families, hiddenProfileFamilies);
  const configuredOnly = $("profiles-configured").checked;
  const search = $("profile-search").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const ranges = Object.entries(profileRanges).filter(([id]) => Number.isFinite($(id).valueAsNumber))
    .map(([id, [field, matches]]) => [field, matches, $(id).valueAsNumber]);
  const flags = Object.entries(profileFlags).filter(([id]) => $(id).checked).map(([, field]) => field);
  const sort = $("profile-sort").value;
  const field = sort.replace(/^-/, "");
  const named = (a, b) => a.name.localeCompare(b.name);
  const compare = (a, b) => {
    if (!sort) return Number(b.enabled) - Number(a.enabled) || named(a, b);
    if (sort === "name") return named(a, b);
    const left = metadata(a)?.[field], right = metadata(b)?.[field];
    if (left == null || right == null) return Number(left == null) - Number(right == null) || named(a, b);
    return (left - right) * (sort.startsWith("-") ? -1 : 1) || named(a, b);
  };
  const table = el("table", "profile-table");
  const header = table.createTHead().insertRow();
  for (const label of ["Profile", "API", "Model ID", "Capabilities", "Input $ / 1M", "Output $ / 1M",
    "Context tokens", "Max output tokens", "Status", ""]) {
    const cell = el("th", "", label);
    cell.scope = "col";
    header.append(cell);
  }
  const body = table.createTBody();
  const profiles = rows.filter(profile => {
    const details = metadata(profile);
    const text = [profile.name, profile.model, details?.name, apiNames[profile.backend]].join(" ").toLowerCase();
    return (!configuredOnly || profile.enabled) && !hiddenProfileApis.has(profile.backend)
      && !hiddenProfileFamilies.has(profileFamily(profile)) && search.every(term => text.includes(term))
      && flags.every(flag => details?.[flag])
      && ranges.every(([key, matches, limit]) => details?.[key] != null && matches(details[key], limit));
  }).sort(compare);
  $("profiles-count").textContent = integer.format(profiles.length) + " of " + integer.format(rows.length) + " profiles";
  for (const profile of profiles) {
    const details = metadata(profile);
    const row = body.insertRow();
    row.insertCell().textContent = profile.name;
    row.insertCell().textContent = apiNames[profile.backend] ?? profile.backend;
    const model = row.insertCell();
    model.className = "profile-model-id";
    model.textContent = profile.model;
    const capabilities = details ? [details.tools && "Tools", details.image && "Image input", details.free && "Free"]
      .filter(Boolean).join(" · ") || "Text" : "—";
    for (const [label, value] of [
      ["Capabilities", capabilities],
      ["Input $ / 1M", details?.input_usd != null ? rateFormat.format(details.input_usd) : "—"],
      ["Output $ / 1M", details?.output_usd != null ? rateFormat.format(details.output_usd) : "—"],
      ["Context", details?.context != null ? integer.format(details.context) : "—"],
      ["Max output", details?.max_output != null ? integer.format(details.max_output) : "—"],
    ]) {
      const cell = row.insertCell();
      cell.dataset.label = label;
      cell.textContent = value;
      if (details?.request_usd > 0 && label.endsWith("/ 1M")) {
        cell.title = "Plus " + rateFormat.format(details.request_usd) + " per request";
      }
    }
    const status = row.insertCell();
    status.className = "profile-status";
    status.dataset.ready = profile.enabled;
    status.textContent = profile.enabled ? "Ready" : "Needs API key";
    const edit = el("button", "button", profile.enabled ? "Edit" : "Set up");
    edit.type = "button";
    edit.setAttribute("aria-label", (profile.enabled ? "Edit " : "Set up ") + profile.name);
    edit.addEventListener("click", () => profile.id ? openProfile(profile) : openProfile(null, {
      ...profile, max_completion_tokens: Math.min(2048, details?.max_output ?? 2048),
    }));
    row.insertCell().append(edit);
  }
  $("profiles-list").replaceChildren(profiles.length ? table : el("p", "quiet",
    rows.length ? "No profiles match these filters." : "No model profiles yet."));
}

async function loadModelCatalog(refresh = false) {
  if (catalogLoading) return;
  catalogLoading = $("refresh-model-catalog").disabled = true;
  const status = $("model-catalog-status");
  status.textContent = "Loading OpenRouter catalog…";
  try {
    const data = await get("/api/openrouter-models" + (refresh ? "?refresh=1" : ""));
    openRouterModels = new Map(data.models.map(model => [model.model, model]));
    const updated = new Date(data.updated_at).toLocaleString();
    status.textContent = data.stale ? "OpenRouter unavailable. Showing catalog from " + updated + "."
      : "OpenRouter catalog updated " + updated + ".";
    renderProfiles();
  } catch {
    status.textContent = openRouterModels.size ? "Could not refresh OpenRouter. Showing the last catalog."
      : "OpenRouter catalog unavailable. Saved profiles and presets are available.";
  } finally {
    catalogLoading = $("refresh-model-catalog").disabled = false;
  }
}

async function loadProfiles() {
  const status = $("profiles-status");
  const revision = ++profileLoad;
  try {
    const profiles = await get("/api/profiles");
    if (profileLoad !== revision) return;
    modelProfiles = profiles;
    status.hidden = true;
    renderProfiles();
    renderLaunchControls();
    if (teamPage) teams.render();
  } catch {
    if (profileLoad !== revision) return;
    status.textContent = "Could not load model profiles.";
    status.hidden = false;
  }
}

function profileFields() {
  const fields = $("profile-form").elements;
  const backend = fields.backend.value;
  const efforts = backend === "deepseek" ? ["none", "low", "high", "max"]
    : ["openrouter", "openai"].includes(backend) ? ["none", "minimal", "low", "medium", "high", "xhigh"]
    : backend === "xai" ? ["none", "low", "medium", "high", "xhigh"] : [];
  const savedKey = editingProfile?.enabled;
  $("profile-provider").hidden = backend !== "openrouter";
  $("profile-gemini").hidden = backend !== "gemini";
  $("profile-reasoning").hidden = !efforts.length;
  options(fields.reasoning_effort, [["", "API default"], ...efforts.map(value => [value, value])]);
  $("remove-profile-key").hidden = !savedKey;
  fields.api_key.disabled = fields.remove_key.checked;
  $("profile-key-hint").textContent = savedKey && backend !== editingProfile.backend
    ? "Enter a new key or remove the saved key to change APIs."
    : savedKey ? "A key is saved. Leave this field blank to keep it." : "Save a key to make this profile available for runs.";
}

function openProfile(profile = null, defaults = {}) {
  editingProfile = profile;
  const form = $("profile-form");
  form.reset();
  const values = profile ?? {backend: "openrouter", max_completion_tokens: 2048, request_timeout: 90, ...defaults};
  for (const [name, value] of Object.entries(values)) {
    const field = form.elements.namedItem(name);
    if (!field) continue;
    if (field.type === "checkbox") field.checked = value;
    else field.value = value ?? "";
  }
  profileFields();
  form.elements.reasoning_effort.value = values.reasoning_effort ?? "";
  $("profile-heading").textContent = profile ? "Edit model profile" : "New model profile";
  $("delete-profile").hidden = !profile;
  $("profile-error").hidden = true;
  $("profile-dialog").showModal();
}

async function updateProfile(path, body) {
  if (savingProfile) return;
  savingProfile = true;
  $("profile-error").hidden = true;
  $("profile-form").inert = true;
  try {
    const data = await postRun("profiles/" + path, body, "Could not update the model profile.");
    ++profileLoad;
    if (path === "delete") modelProfiles = modelProfiles.filter(profile => profile.id !== data.id);
    else if (editingProfile) modelProfiles = modelProfiles.map(profile => profile.id === data.id ? data : profile);
    else modelProfiles.push(data);
    $("profile-dialog").close();
    renderProfiles();
    renderLaunchControls();
    if (catalog.can_manage_teams) teams.load();
    if (data.enabled) {
      $("model-profile").value = data.id;
      renderLaunchControls();
    }
  } catch (error) {
    $("profile-error").textContent = error.message;
    $("profile-error").hidden = false;
  } finally {
    savingProfile = false;
    $("profile-form").inert = false;
  }
}

async function poll() {
  try {
    const [recordings, nextCatalog] = await Promise.all([get("/api/recordings"), get("/api/levels")]);
    const nextRuns = [...recordings.local, ...recordings.public.map(run => ({...run,
      id: "git/" + run.id, public_id: run.id, resumable: false}))];
    const signature = JSON.stringify([nextRuns, nextCatalog]);
    const requested = nextRuns.find(run => run.id === requestedRun);
    if (signature !== listing || requested) {
      listing = signature;
      runs = nextRuns;
      const startedProfiles = nextCatalog.can_manage_profiles && !catalog.can_manage_profiles;
      const startedTeams = nextCatalog.can_manage_teams && !catalog.can_manage_teams;
      catalog = nextCatalog;
      $("profiles-tab").hidden = !catalog.can_manage_profiles;
      $("teams-tab").hidden = !catalog.can_manage_teams;
      if (startedProfiles) loadProfiles();
      if (startedTeams) teams.load();
      if (stoppingRun !== catalog.active_run) stoppingRun = null;
      const puzzles = new Map(catalog.levels.map(level => [level.id, level.title]));
      for (const run of runs) if (!puzzles.has(run.settings.level)) puzzles.set(run.settings.level, run.level_title);
      const live = runs.find(run => run.id === catalog.active_run);
      const initial = live ?? (catalog.autoplay_latest ? recordings.local[0] : null);
      const initialPuzzle = selectedPuzzle || initial?.settings.level || catalog.default_level || catalog.levels[0]?.id || "";
      options($("puzzle"), [...puzzles], initialPuzzle);
      $("puzzle").disabled = !puzzles.size;
      if (requested) requestedRun = null;
      const opening = requested ?? (!selectedPuzzle ? initial : null);
      selectPuzzle(initialPuzzle, opening?.id, Boolean(requested || (opening && opening !== live)));
      if (comparing) refreshFilters();
      if (managing) renderRecordings();
    }
    if (selected) {
      const id = selected;
      const revision = selection;
      const run = chosen();
      const path = run?.public_id ? "/api/public/" + run.public_id : "/api/runs/" + id;
      const result = await get(path + "/frames?after=" + offset);
      if (selection === revision && result.frames.length) {
        offset = result.next_offset;
        for (const frame of result.frames) {
          // Keep provider bookkeeping in the recording and show actions in the replay.
          if (frame.event.kind === "request" && !frame.event.error) continue;
          if (frame.event.kind === "tool") {
            if (!roundActions.has(frame.round)) roundActions.set(frame.round, []);
            roundActions.get(frame.round).push(frames.length);
          }
          const image = frame.event.image;
          const hasImage = frame.event.kind === "tool" && typeof image === "string"
            && image.startsWith("data:image/png;base64,");
          lastImageFrame.push(hasImage ? frames.length : lastImageFrame.at(-1));
          frames.push(frame);
        }
        if (follow && !streaming) cursor = Math.max(0, frames.length - 1);
        schedulePlayback();
        render();
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

function geminiCost(run, metrics, model = run.settings.model) {
  const rates = geminiRates.get(model)?.find(rate => run.started_at.slice(0, 10) >= rate.since);
  const input = usage(metrics, "input_tokens");
  if (!rates || !Number.isSafeInteger(input) || input < 0) return null;
  let output = usage(metrics, "output_tokens");
  // Older recordings may omit output counts on empty replies but still include total usage.
  if (output == null) {
    const total = usage(metrics, "total_tokens");
    if (!Number.isSafeInteger(total) || total - input < (metrics.output_tokens ?? 0)) return null;
    output = total - input;
  }
  if (!Number.isSafeInteger(output) || output < 0) return null;
  const cached = metrics.cached_input_tokens ?? 0;
  if (!Number.isSafeInteger(cached) || cached < 0 || cached > input) return null;
  return ((input - cached) * rates.input + cached * rates.cached + output * rates.output) / 1_000_000;
}

function runCost(run, metrics) {
  if (!run.team) return run.settings.backend === "gemini"
    ? geminiCost(run, metrics) : usage(metrics, "cost_usd");
  let total = 0;
  for (const [id, agent] of Object.entries(run.team.agents)) {
    const counts = metrics?.agent_usage?.[id];
    if (!counts) return null;
    if (!counts.requests) continue;
    const value = agent.settings.backend === "gemini"
      ? geminiCost(run, counts, agent.settings.model) : usage(counts, "cost_usd");
    if (value == null) return null;
    total += value;
  }
  return total;
}

const hasEstimatedCost = run => run.settings.backend === "gemini"
  || Object.values(run.team?.agents ?? {}).some(agent => agent.settings.backend === "gemini");

function costText(run, value, markEstimate = true) {
  const estimated = value != null && hasEstimatedCost(run);
  const price = estimated && value >= 1 ? dollarsToCents.format(value) : format("cost_usd", value);
  return price + (estimated && markEstimate ? " (est.)" : "");
}

function renderMetrics(run, frame) {
  const metrics = frame.metrics;
  const demo = run.settings.model === "demo";
  const estimated = !demo && hasEstimatedCost(run);
  const cost = demo ? null : runCost(run, metrics);
  const ending = frame.event.kind === "finish";
  const status = frame.won ? "solved" : ending ? frame.event.status
    : frame.event.kind === "pause" ? "paused" : "running";
  const pairs = [
    ["Status", labels[status] ?? status],
    ["Score", format("score", frame.score)],
    ["Elapsed", format("elapsed_seconds", frame.elapsed_seconds)],
    ["Round", integer.format(frame.round)],
    ["Tokens", demo ? "—" : format("total_tokens", usage(metrics, "total_tokens"))],
    [estimated ? "Estimated cost" : "API cost", costText(run, cost, false), estimated ? estimatedCostNote : null],
  ];
  $("metrics").replaceChildren(...pairs.map(([key, value, note]) => {
    const item = el("div", "metric");
    const data = el("dd", "", value);
    data.title = note ?? value;
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
    const [width, height] = renderer.canvasSize ?? [800, 800];
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      canvas.dataset.landscape = width > height;
    }
    canvas.setAttribute("aria-label", renderer.describeWorld?.(frame.world) ?? "Overhead view of the puzzle");
    renderer.drawWorld(canvas, frame.world, {
      color: id => colors[frame.agents.findIndex(agent => agent.id === id) % colors.length],
      focus,
      sight: $("vision").checked,
      actor: frame.event.kind === "tool" ? frame.event.agent : null,
      character: identity,
      pulse: spritePulse,
      bubble: activeBubble,
      move: renderer.supportsMotion ? motionFor(frame) : null,
      tick: renderer.supportsTickMotion ? tickMotionFor(frame) : null,
    });
  }
}

function tickMotionFor(frame) {
  if (playback?.tick) {
    const {from, to, elapsed, duration} = playback.tick;
    return {from, to, progress: (elapsed + playback.elapsed) / duration};
  }
  const from = frames[cursor - 1]?.world;
  return frame.event.kind === "tick" && from ? {from, progress: ease(progress)} : null;
}

const ease = value => value * value * (3 - 2 * value);

function bubbleFor(event) {
  if (event.kind === "thinking") return {agent: event.agent, text: event.text, thinking: true};
  if (event.kind === "tool" && ["messageOtherAgent", "messageAllOtherAgents"].includes(event.name)
      && event.result?.delivered_to?.length) {
    return {agent: event.agent, text: event.arguments?.message};
  }
  return null;
}

function clearBubble() {
  clearTimeout(bubbleTimer);
  activeBubble = null;
}

function showBubble(event) {
  const bubble = bubbleFor(event);
  if (!bubble) return;
  clearBubble();
  activeBubble = bubble;
  bubbleTimer = setTimeout(() => {
    activeBubble = null;
    if (!$("spectate").hidden && frames[cursor]) renderWorld(frames[cursor]);
  }, 1400);
}

function motionFor(frame, index = cursor) {
  const event = frame.event;
  if (event.kind !== "tool" || !["moveX", "moveY"].includes(event.name)) return null;
  const world = frames[index - 1]?.world;
  const from = world?.players?.find(player => player.id === event.agent);
  const to = frame.world.players?.find(player => player.id === event.agent);
  return from && to && (from.x !== to.x || from.y !== to.y) ? {from, world, progress: ease(progress)} : null;
}

function tweenDuration(index, continuousTicks = false) {
  if (reducedMotion.matches) return 0;
  const frame = frames[index];
  const move = renderer?.supportsMotion && motionFor(frame, index);
  if (move) return 320 + Math.min(200, Math.abs(frame.event.arguments?.steps ?? 1) * 30);
  return !continuousTicks && renderer?.supportsTickMotion && frame.event.kind === "tick" && index > 0
    ? 330 : 0;
}

function renderAgentObservation() {
  const frame = frames[lastImageFrame[cursor]];
  $("agent-observation").hidden = !frame;
  if (!frame) return;
  const {agent, image} = frame.event;
  const preview = $("agent-observation-image");
  if (preview.getAttribute("src") !== image) preview.src = image;
  preview.alt = "Field as seen by " + agentName(agent);
  $("agent-observation-caption").textContent = agentName(agent) + " · Round " + frame.round;
}

function markRead(receipt, agent, read, delivery = false) {
  const label = agentName(agent) + (delivery ? (read ? " · Delivered" : " · Pending delivery")
    : (read ? " · Read" : " · Unread"));
  receipt.dataset.read = read;
  receipt.title = label;
  receipt.setAttribute("aria-label", label);
}

function dialogueRow(sender, label, text) {
  const row = el("li", "message");
  row.style.setProperty("--sender", colors[Number(sender) - 1]);
  const content = el("p", "message-content");
  content.append(el("strong", "message-route", label + ": "), document.createTextNode(text));
  row.append(content);
  return row;
}

function messageRow(id, sender, recipients, text, autoMail) {
  const to = recipients.length > 1 && !identity(sender) ? "Agents " + recipients.join(", ")
    : recipients.length ? recipients.map(agentName).join(", ") : "no other agents";
  const row = dialogueRow(sender, agentName(sender) + " to " + to, text);
  row.dataset.messageId = id;

  const receipts = new Map();
  if (recipients.length) {
    const group = el("div", "message-receipts");
    for (const recipient of recipients) {
      const marker = el("span", "message-receipt", agentBadge(recipient));
      marker.setAttribute("role", "img");
      markRead(marker, recipient, false, autoMail);
      receipts.set(recipient, marker);
      group.append(marker);
    }
    row.append(group);
  }
  return {row, receipts};
}

function renderCommunications(agents) {
  const list = $("communications-list");
  const rewind = cursor < messageCursor;
  const first = rewind ? messageCursor : messageCursor + 1;
  const end = rewind ? cursor : cursor + 1;
  const direction = rewind ? -1 : 1;
  const autoMail = Boolean(chosen()?.automatic_mail);
  let top, height;
  for (let index = first; index !== end; index += direction) {
    const frame = frames[index];
    const {kind, name, agent, arguments: args, result} = frame.event;
    if (kind === "mail") {
      for (const [recipient, ids] of Object.entries(frame.event.deliveries)) {
        for (const id of ids) {
          const receipt = messageRows.get(id)?.receipts.get(recipient);
          if (receipt) markRead(receipt, recipient, !rewind, true);
        }
      }
    }
    if (kind === "tool" && name === "readMyMessages") {
      for (const message of result?.messages ?? []) {
        const receipt = messageRows.get(message.id)?.receipts.get(agent);
        if (receipt) markRead(receipt, agent, !rewind);
      }
    }
    const thinking = kind === "thinking" && typeof frame.event.text === "string" && frame.event.text.trim();
    const sent = kind === "tool" && (name === "messageOtherAgent" || name === "messageAllOtherAgents")
      && Array.isArray(result?.delivered_to);
    if (thinking || sent) {
      // The send frame includes the message ID even when later snapshots roll it out of view.
      const sender = sent && frame.agents.find(candidate => candidate.id === agent);
      const id = sender?.sent?.at(-1)?.id ?? "frame-" + frame.index;
      if (top === undefined) {
        top = list.scrollTop;
        height = list.scrollHeight;
      }
      if (rewind) {
        messageRows.get(id)?.row.remove();
        messageRows.delete(id);
      } else {
        const view = thinking
          ? {row: dialogueRow(agent, agentName(agent) + " thinking", frame.event.text), receipts: new Map()}
          : messageRow(id, agent, result.delivered_to, args?.message ?? "", autoMail);
        if (thinking) view.row.classList.add("message-thinking");
        messageRows.set(id, view);
        list.prepend(view.row);
      }
    }
  }
  messageCursor = cursor;
  const showThinking = $("show-thinking").checked;
  let visible = false;
  for (const {row} of messageRows.values()) {
    row.hidden = !showThinking && row.classList.contains("message-thinking");
    visible ||= !row.hidden;
  }
  list.hidden = !visible;
  $("communications-empty").hidden = visible;
  $("communications-empty").textContent = showThinking ? "No messages or thinking yet." : "No messages yet.";
  $("communications").hidden = agents.length < 2 && !messageRows.size;
  if (top !== undefined) list.scrollTop = top <= 6 ? 0 : top + list.scrollHeight - height;
}

function createCard(id) {
  const card = el("article", "panel agent");
  const header = el("div", "panel-header agent-header");
  const selector = el("button", "agent-selector");
  selector.type = "button";
  const avatar = el("span", "agent-dot", id);
  avatar.setAttribute("aria-hidden", "true");
  const name = el("span", "", "Agent " + id);
  const model = el("small", "agent-model");
  const label = el("span", "agent-identity");
  label.append(name, model);
  selector.append(avatar, label);
  selector.addEventListener("click", () => {
    focus = focus === id ? null : id;
    render();
  });
  const status = el("span", "agent-status");
  header.append(selector, status);
  const look = el("div", "look");
  card.append(header, look);
  const view = {card, selector, avatar, status, look, name, model};
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
    const character = identity(agent.id);
    view.card.dataset.character = Boolean(character);
    if (view.renderer !== renderer || view.character !== character) {
      view.character = character;
      view.name.textContent = agentName(agent.id);
      const settings = character?.settings ?? chosen()?.settings;
      view.model.textContent = character ? settings.model : "";
      view.model.hidden = !character;
      view.selector.title = character ? agentName(agent.id) + " · " + settings.model
        + " · " + (apiNames[settings.backend] ?? settings.backend) : "";
      if (renderer?.drawAgent || character) {
        const canvas = el("canvas");
        canvas.width = canvas.height = 56;
        drawAgent(canvas, agent.id, colors[index], character);
        view.avatar.replaceChildren(canvas);
      } else {
        view.avatar.textContent = agent.id;
      }
    }
    view.selector.setAttribute("aria-pressed", focus === agent.id);
    view.selector.disabled = Boolean(renderer?.compactAgents);
    view.look.hidden = Boolean(renderer?.compactAgents || renderer?.sharedObservation);
    view.status.textContent = agent.status === "ready" && agent.last_action
      ? agent.last_action : agent.status;
    const signature = JSON.stringify(agent.last_look);
    if (signature === view.look.dataset.signature && view.renderer === renderer) return;
    view.look.dataset.signature = signature;
    view.renderer = renderer;
    if (view.look.hidden) {
      view.look.replaceChildren();
    } else if (!agent.last_look) {
      view.look.replaceChildren(el("p", "", "Waiting for first look"));
    } else if (renderer?.drawLook) {
      const canvas = el("canvas");
      canvas.width = canvas.height = 156;
      canvas.setAttribute("role", "img");
      const visible = (agent.last_look.agents ?? []).map(({id, dx, dy}) => agentName(id) + " at offset " + dx + ", " + dy);
      canvas.setAttribute("aria-label", [agent.last_look.grid?.join("\n") ?? "Last observation", ...visible].join("\n"));
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
  if (kind === "tool" && result?.error) {
    return {title: name + " rejected", detail: result.error, short: name + " rejected"};
  }
  const levelAction = renderer?.describeAction?.(event);
  if (levelAction) return levelAction;
  if (kind === "tool") {
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
      const title = recipients.length ? "Messaged " + recipients.map(agentName).join(", ")
        : "Sent a message";
      return {title, short: "Message", detail: args?.message};
    }
    if (name === "readMyMessages") {
      const count = result?.messages?.length ?? 0;
      const senders = [...new Set(result?.messages?.map(message => message.from))];
      return {title: count ? `Read ${count} ${count === 1 ? "message" : "messages"}` : "Checked inbox",
        short: count ? "Read " + count : "Inbox",
        detail: count ? "From " + senders.map(agentName).join(", ") : ""};
    }
    if (name === "whoAmI") return {title: "Checked identity", short: "Identity", detail: result?.name};
    if (name === "listAgents") return {title: "Listed agents", short: "List agents"};
    return {title: name, short: name};
  }
  if (kind === "request") return {title: "Model request failed", detail: event.error};
  if (kind === "mail") return {title: "Messages delivered"};
  if (kind === "round") return {title: "Waiting for model replies"};
  if (kind === "tick") return {title: event.messages[0] ?? "Mechanism advanced", detail: event.messages.slice(1).join(" ")};
  if (kind === "agent_stopped") return {title: "Agent stopped", detail: event.reason ?? "No tool call"};
  if (kind === "agent_halted") return {title: "Agent returned no tool call",
    detail: event.retry == null ? "Follow-up limit reached." : "Follow-up " + event.retry + " of " + event.max_retries + " on the next turn."};
  if (kind === "thinking") return {title: "Agent thinking", detail: event.text};
  if (kind === "pause") return {title: event.reason === "round_limit" ? "Paused at the round limit"
    : event.reason === "error" ? "Paused on a model error" : "Run paused"};
  if (kind === "resume") return {title: "Run resumed"};
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
    actor.textContent = agentName(agent);
    actor.style.setProperty("--agent", agentColor(agent));
  }
  const title = $("event");
  title.textContent = title.title = description.title;
  const detail = $("event-detail");
  const nextDetail = description.detail ?? "";
  if (detail.textContent !== nextDetail) {
    detail.textContent = nextDetail;
    detail.scrollTop = 0;
  }

  const entries = roundActions.get(frame.round) ?? [];
  $("round-flow").inert = !entries.length;
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
      button.title = agentName(event.agent) + " · " + label.title;
      button.style.setProperty("--agent", agentColor(event.agent));
      button.append(el("b", "", agentBadge(event.agent)), document.createTextNode(label.short));
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
  const setup = !run && preview;
  $("title").textContent = run?.level_title ?? catalog.levels.find(level => level.id === selectedPuzzle)?.title
    ?? "Waiting for a run";
  $("run-caption").textContent = run
    ? runName(run) + " · Seed " + run.settings.seed
    : setup ? "Round 0 · Preview seed " + setup.seed : "Spectator view";
  const showing = Boolean(frame);
  $("empty").hidden = showing || Boolean(setup);
  $("empty-message").textContent = run ? "Waiting for the first recorded frame…"
    : runs.some(item => item.settings.level === selectedPuzzle) ? "Choose a recording or start a run."
    : "No recordings for this puzzle yet.";
  $("observation").hidden = !showing && !setup;
  if ($("spectate").hidden) return;
  for (const id of ["metrics", "current-action", "round-flow", "replay-controls", "record-details"]) {
    $(id).hidden = Boolean(setup);
  }
  if (setup) {
    $("agent-observation").hidden = $("communications").hidden = true;
    renderWorld(setup);
    renderAgents(setup.agents, null);
    return;
  }
  if (!showing) return;
  renderMetrics(run, frame);
  renderWorld(frame);
  renderAgentObservation();
  renderAgents(frame.agents, frame.event.kind === "tool" ? frame.event.agent : null);
  renderCommunications(frame.agents);
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
    (frame.round_limit ?? run.settings.rounds) + " round limit", frame.metrics.tool_calls + " tool calls",
    ...(frame.metrics.halts == null || run.settings.model === "demo" ? []
      : [frame.metrics.halts + (frame.metrics.halts === 1 ? " halt" : " halts")]),
    ...Object.entries(run.errors ?? {}).map(([id, error]) => agentName(id) + " · " + error),
  ].join(" · ");
}

function stopReplay() {
  if (playbackFrame !== null) cancelAnimationFrame(playbackFrame);
  if (animation !== null) cancelAnimationFrame(animation);
  playbackFrame = null;
  animation = null;
  spritePulse = 0;
  replay = false;
}

function stepTo(index, animate = false) {
  cursor = index;
  playback = null;
  if (animation !== null) cancelAnimationFrame(animation);
  animation = null;
  progress = 1;
  spritePulse = 0;
  const frame = frames[cursor];
  showBubble(frame.event);
  schedulePlayback();
  const acting = frame.event.kind === "tool" && identity(frame.event.agent) && !reducedMotion.matches;
  const motion = tweenDuration(index);
  const tween = animate && playbackFrame === null && Math.max(motion, acting ? 240 : 0);
  if (tween) {
    progress = motion ? 0 : 1;
    const start = performance.now();
    const duration = tween / Number($("speed").value);
    const paint = time => {
      const fraction = Math.min(1, (time - start) / duration);
      progress = motion ? Math.min(1, fraction * tween / motion) : 1;
      spritePulse = acting ? Math.min(1, fraction * tween / 240) : 0;
      renderWorld(frame);
      animation = fraction < 1 ? requestAnimationFrame(paint) : null;
    };
    animation = requestAnimationFrame(paint);
  }
  render();
}

function seek(index, animate = false) {
  stopReplay();
  follow = false;
  clearBubble();
  stepTo(index, animate);
}

function playbackDelay(index, tween) {
  const event = frames[index].event;
  const linger = ["look", "read", "submit"].includes(event.name);
  const delay = renderer?.playbackDelay?.(event) ?? (linger ? 650 : event.kind === "round" ? 140 : 310);
  return Math.max(delay, tween + 45);
}

function tickSpan() {
  // The event cursor still visits every action between the two physics snapshots.
  let start = cursor;
  while (start > 0 && frames[start].event.kind !== "tick") start--;
  let end = cursor + 1;
  while (end < frames.length && frames[end].event.kind !== "tick") end++;
  if (end === frames.length) return null;
  let elapsed = 0, duration = 0;
  for (let index = start; index < end; index++) {
    const delay = playbackDelay(index, tweenDuration(index, true));
    if (index < cursor) elapsed += delay;
    duration += delay;
  }
  return {from: frames[start].world, to: frames[end].world, elapsed, duration};
}

function schedulePlayback() {
  if (playbackFrame !== null || $("spectate").hidden || !(replay || (follow && streaming))
      || cursor >= frames.length - 1) return;
  if (!playback) {
    const continuousTicks = replay && renderer?.supportsTickMotion && !reducedMotion.matches;
    const tween = tweenDuration(cursor, continuousTicks);
    const elapsed = animation === null ? 0 : progress * tween;
    playback = {elapsed, tween, duration: playbackDelay(cursor, tween), tick: continuousTicks ? tickSpan() : null};
  }
  if (animation !== null) cancelAnimationFrame(animation);
  animation = null;
  progress = playback.tween ? Math.min(1, playback.elapsed / playback.tween) : 1;
  let previousTime = performance.now();
  const advance = time => {
    const elapsed = playback.elapsed;
    playback.elapsed += Math.max(0, time - previousTime) * Number($("speed").value);
    previousTime = time;
    if (playback.elapsed >= playback.duration) {
      playbackFrame = null;
      const next = cursor + 1;
      const live = !replay;
      if (next === frames.length - 1) replay = false;
      stepTo(next, live);
      return;
    }
    const moving = progress < 1;
    progress = playback.tween ? Math.min(1, playback.elapsed / playback.tween) : 1;
    const event = frames[cursor].event;
    const acting = !reducedMotion.matches && event.kind === "tool" && identity(event.agent);
    spritePulse = acting ? Math.min(1, playback.elapsed / 240) : 0;
    if (moving || playback.tick || (acting && elapsed < 240)) renderWorld(frames[cursor]);
    playbackFrame = requestAnimationFrame(advance);
  };
  playbackFrame = requestAnimationFrame(advance);
}

$("puzzle").addEventListener("change", event => {
  requestedRun = null;
  $("launch-status").hidden = true;
  selectPuzzle(event.target.value);
});
$("runs").addEventListener("change", event => selectRun(event.target.value));
$("run-demo").addEventListener("click", () => launch("demo"));
$("run-model").addEventListener("click", () => launch("model"));
$("continue-run").addEventListener("click", () => launch("continue"));
$("pause-run").addEventListener("click", toggleRunPause);
$("stop-run").addEventListener("click", stopRun);
$("vision").addEventListener("change", render);
$("timeline").addEventListener("input", event => seek(Number(event.target.value)));
$("previous").addEventListener("click", () => seek(cursor - 1));
$("next").addEventListener("click", () => seek(cursor + 1, true));
$("latest").addEventListener("click", () => {
  stopReplay();
  clearBubble();
  follow = true;
  stepTo(frames.length - 1);
});
$("play").addEventListener("click", () => {
  if (isPlaying()) {
    stopReplay();
    follow = false;
    render();
  } else {
    follow = false;
    replay = true;
    if (cursor === frames.length - 1) {
      clearBubble();
      stepTo(0);
    } else {
      schedulePlayback();
      render();
    }
  }
});

function showPage(page) {
  comparing = page === "compare";
  managing = page === "recordings";
  teamPage = page === "teams";
  for (const tab of ["spectate", "compare", "recordings", "profiles", "teams"]) {
    $(["recordings", "profiles", "teams"].includes(tab) ? tab + "-page" : tab).hidden = tab !== page;
    if (tab === page) $(tab + "-tab").setAttribute("aria-current", "page");
    else $(tab + "-tab").removeAttribute("aria-current");
  }
  if (page !== "spectate") {
    stopReplay();
    if (comparing) refreshFilters();
    if (managing) renderRecordings();
    if (page === "profiles") {
      loadProfiles();
      loadModelCatalog();
    }
    if (teamPage) {
      teams.render();
      teams.load();
    }
  } else {
    render();
    schedulePlayback();
  }
}

function watchRun(run) {
  selectPuzzle(run.settings.level, run.id);
  if (frames.length && selected !== catalog.active_run) seek(0);
  showPage("spectate");
}

function openComment(run) {
  commentRun = run.public_id ?? run.id;
  const input = $("recording-comment");
  input.maxLength = catalog.max_comment_length ?? 1000;
  input.value = run.comment ?? "";
  $("comment-error").hidden = true;
  $("comment-destination").hidden = !(run.public_id || run.published_to_git);
  $("comment-dialog").showModal();
}

function commentCell(cell, run) {
  cell.className = "recording-comment";
  if (run.comment) cell.append(el("p", "comment-text", run.comment));
  if (catalog.can_launch) {
    const edit = el("button", "button", run.comment ? "Edit comment" : "Add comment");
    edit.type = "button";
    edit.addEventListener("click", () => openComment(run));
    cell.append(edit);
  } else if (!run.comment) {
    cell.textContent = "—";
  }
}

$("comment-form").addEventListener("submit", async event => {
  event.preventDefault();
  if ($("save-comment").disabled) return;
  const id = commentRun;
  $("save-comment").disabled = $("cancel-comment").disabled = true;
  $("comment-error").hidden = true;
  try {
    const result = await postRun("comment", {id, comment: $("recording-comment").value}, "Could not save the comment.");
    for (const run of runs) {
      if ((run.public_id ?? run.id) === id) run.comment = result.comment;
    }
    $("comment-dialog").close();
    if (managing) renderRecordings();
    if (comparing) renderComparison();
  } catch (error) {
    $("comment-error").textContent = error.message;
    $("comment-error").hidden = false;
  } finally {
    $("save-comment").disabled = $("cancel-comment").disabled = false;
  }
});

$("cancel-comment").addEventListener("click", () => $("comment-dialog").close());
$("comment-dialog").addEventListener("cancel", event => {
  if ($("save-comment").disabled) event.preventDefault();
});

async function setPublication(run, destination, enabled) {
  if (recordingUpdates.has(run.id)) return;
  if (destination === "git" && !window.confirm(
    "Copy this recording into recordings/ for Git? The replay includes agent messages and any recorded thinking. "
    + "You can review the files before committing them.")) return;
  recordingUpdates.add(run.id);
  renderRecordings();
  const notice = $("publication-status");
  notice.hidden = false;
  notice.textContent = "Saving publication status…";
  try {
    await postRun("publish", {id: run.id, destination, enabled}, "Could not update publication.");
    const current = runs.find(item => item.id === run.id);
    if (current) current[destination === "git" ? "published_to_git" : "published_locally"] = enabled;
    notice.textContent = destination === "git" ? "Ready to review and commit in recordings/" + run.id + "/."
      : enabled ? "Published locally." : "Removed from local graphs.";
  } catch (error) {
    notice.textContent = error.message;
  } finally {
    recordingUpdates.delete(run.id);
    if (managing) renderRecordings();
  }
}

async function deleteRecording(run) {
  if (recordingUpdates.has(run.id) || !window.confirm("Are you sure you want to delete this recording?")) return;
  recordingUpdates.add(run.id);
  renderRecordings();
  const notice = $("publication-status");
  notice.hidden = false;
  notice.textContent = "Deleting recording…";
  try {
    await postRun("delete-recording", {id: run.id}, "Could not delete the recording.");
    runs = runs.filter(item => item.id !== run.id);
    updateRecordings();
    notice.textContent = "Recording deleted.";
  } catch (error) {
    notice.textContent = error.message;
  } finally {
    recordingUpdates.delete(run.id);
    if (managing) renderRecordings();
  }
}

function unpublishedRecordings() {
  return runs.filter(run => !run.public_id && !run.published_locally && !run.published_to_git
    && !["running", "paused"].includes(run.status) && !recordingUpdates.has(run.id));
}

async function removeUnpublished() {
  const ids = unpublishedRecordings().map(run => run.id);
  if (!ids.length || deletingUnpublished || !confirm(
    "Permanently delete " + ids.length + " unpublished recording" + (ids.length === 1 ? "" : "s") + "?"
  )) return;
  deletingUnpublished = true;
  renderRecordings();
  const status = $("publication-status");
  status.hidden = false;
  status.textContent = "Deleting unpublished recordings…";
  try {
    const result = await postRun("delete-unpublished", {ids}, "Could not delete unpublished recordings.");
    const removed = new Set(result.deleted);
    runs = runs.filter(run => !removed.has(run.id));
    updateRecordings();
    status.textContent = "Deleted " + removed.size + " unpublished recording" + (removed.size === 1 ? "" : "s") + "."
      + (result.skipped.length ? " Skipped " + result.skipped.length + " unavailable or protected." : "");
  } catch (error) {
    status.textContent = error.message;
  } finally {
    deletingUnpublished = false;
    if (managing) renderRecordings();
  }
}

function renderRecordings() {
  const bulk = $("delete-unpublished");
  bulk.hidden = !catalog.can_launch;
  bulk.disabled = deletingUnpublished || !unpublishedRecordings().length;
  const container = $("recording-management");
  const local = new Set(runs.filter(run => !run.public_id).map(run => run.id));
  const recordings = runs.filter(run => !local.has(run.public_id));
  if (!recordings.length) {
    container.replaceChildren(el("p", "quiet", "No recordings yet."));
    return;
  }
  const table = el("table");
  const head = table.createTHead().insertRow();
  for (const label of ["Recording", "Puzzle", "Result", "Comment", "Local graphs", "Git", "Actions"]) head.append(el("th", "", label));
  const body = table.createTBody();
  for (const run of recordings) {
    const row = body.insertRow();
    const watch = el("button", "button", runLabel(run));
    watch.addEventListener("click", () => watchRun(run));
    row.insertCell().append(watch);
    row.insertCell().textContent = run.level_title;
    row.insertCell().textContent = labels[run.status] ?? run.status;
    commentCell(row.insertCell(), run);
    const localCell = row.insertCell(), gitCell = row.insertCell();
    const disabled = !catalog.can_launch || deletingUnpublished || recordingUpdates.has(run.id)
      || ["running", "paused"].includes(run.status);
    const demo = run.settings.model === "demo";
    if (!run.public_id && (!demo || run.published_locally)) {
      const toggle = el("button", "button", run.published_locally ? "Unpublish locally" : "Publish locally");
      toggle.disabled = disabled;
      toggle.addEventListener("click", () => setPublication(run, "local", !run.published_locally));
      localCell.append(toggle);
    } else {
      localCell.textContent = "—";
    }
    if (run.public_id || run.published_to_git) {
      gitCell.textContent = "In Git collection";
    } else if (!demo) {
      const copy = el("button", "button", "Publish to Git");
      copy.disabled = disabled;
      copy.addEventListener("click", () => setPublication(run, "git", true));
      gitCell.append(copy);
    } else {
      gitCell.textContent = "—";
    }
    const actions = row.insertCell();
    if (!run.public_id && !run.published_to_git) {
      const remove = el("button", "button", "Delete");
      remove.disabled = disabled;
      remove.addEventListener("click", () => deleteRecording(run));
      actions.append(remove);
    }
  }
  container.replaceChildren(table);
}

const levelKey = run => [run.settings.level, run.level_version, run.harness_version].join("/");
const recordedRuns = () => {
  const source = $("graph-source").value;
  const visible = runs.filter(run => run.settings.model !== "demo" && run.status !== "running" && run.status !== "paused"
    && (run.public_id ? source !== "local" : source !== "public" && run.published_locally));
  const local = new Set(visible.filter(run => !run.public_id).map(run => run.id));
  return visible.filter(run => !local.has(run.public_id));
};

function runMetric(run, field) {
  const value = field === "cost_usd" ? run.cost_usd ?? runCost(run, run.metrics)
    : run[field] ?? usage(run.metrics, field);
  if (value != null || !partialUsage(run, field)) return value;
  return run.metrics[field];
}

function partialUsage(run, field) {
  const metrics = run.metrics;
  const missing = metrics?.missing_usage?.[field];
  return missing > 0 && missing < metrics.requests && Number.isFinite(metrics[field])
    && (field !== "cost_usd" || (!hasEstimatedCost(run) && runCost(run, metrics) == null));
}

function comparisonValue(run, field) {
  const value = runMetric(run, field);
  const formatted = field === "cost_usd" ? costText(run, value) : format(field, value);
  return partialUsage(run, field) ? formatted + " (partial)" : formatted;
}

function refreshFilters() {
  const recordings = recordedRuns();
  const levels = new Map();
  for (const run of recordings) {
    levels.set(levelKey(run), run.level_title + " v" + run.level_version + " · Harness v" + run.harness_version);
  }
  const preferred = [...levels.keys()].find(key => key.startsWith(selectedPuzzle + "/"));
  options($("level-filter"), levels.size ? [...levels] : [["", "No published recordings"]], preferred ?? levels.keys().next().value);
  $("level-filter").disabled = !levels.size;
  const seeds = [...new Set(recordings.filter(run => levelKey(run) === $("level-filter").value)
    .map(run => run.settings.seed))].sort((a, b) => a - b);
  options($("seed-filter"), [["", "All seeds"], ...seeds.map(seed => [String(seed), String(seed)])]);
  renderComparison();
}

function renderScatter(samples, x, y, xLabel, yLabel) {
  const plot = el("div", "scatter");
  const grid = el("div", "scatter-grid");
  const axis = el("div", "scatter-x", xLabel);
  plot.append(el("span", "scatter-y", yLabel), grid, axis);
  const xMax = x === "score" ? 1 : Math.max(0, ...samples.map(point => point[1])) || 1;
  const yMax = y === "score" ? 1 : Math.max(0, ...samples.map(point => point[2])) || 1;
  for (let i = 0; i <= 4; i++) {
    const line = el("div", "scatter-grid-line");
    line.style.top = 100 - i * 25 + "%";
    line.append(el("span", "scatter-tick", format(y, yMax * i / 4)));
    grid.append(line);
    const tick = el("span", "scatter-tick", format(x, xMax * i / 4));
    tick.style.left = i * 25 + "%";
    axis.append(tick);
  }
  const groups = new Map();
  for (const [run, xValue, yValue] of samples) {
    const key = xValue + "/" + yValue;
    if (!groups.has(key)) groups.set(key, {xValue, yValue, runs: []});
    groups.get(key).runs.push(run);
  }
  for (const {xValue, yValue, runs: matches} of groups.values()) {
    const point = el("button", "scatter-point", matches.length > 1 ? matches.length : "");
    point.type = "button";
    point.style.left = xValue / xMax * 100 + "%";
    point.style.top = (1 - yValue / yMax) * 100 + "%";
    const partial = matches.some(run => partialUsage(run, x) || partialUsage(run, y));
    point.dataset.partial = partial;
    const coordinates = xLabel + " " + format(x, xValue) + ", " + yLabel + " " + format(y, yValue);
    point.title = coordinates + (partial ? " · Includes partial usage" : "") + " · " + matches.map(runLabel).join("\n");
    point.setAttribute("aria-label", matches.length === 1 ? point.title : matches.length + " runs · " + coordinates);
    point.addEventListener("click", () => {
      if (matches.length === 1) {
        watchRun(matches[0]);
        return;
      }
      const details = $("point-runs");
      details.replaceChildren(el("span", "quiet", coordinates), ...matches.map(run => {
        const button = el("button", "button", runLabel(run));
        button.type = "button";
        button.addEventListener("click", () => watchRun(run));
        return button;
      }));
      details.hidden = false;
      details.querySelector("button").focus();
    });
    grid.append(point);
  }
  $("chart").replaceChildren(plot);
}

function renderComparison() {
  const seed = $("seed-filter").value;
  const data = recordedRuns().filter(run => levelKey(run) === $("level-filter").value
    && (!seed || String(run.settings.seed) === seed));
  const metric = $("metric-filter").value;
  const scatter = $("chart-type").value === "scatter";
  const x = $("x-metric").value;
  $("x-metric-control").hidden = !scatter;
  $("y-metric-label").textContent = scatter ? "Y axis" : "Metric";
  $("point-runs").hidden = true;
  const fields = scatter ? [x, metric] : [metric];
  const samples = data.map(run => [run, ...fields.map(field => runMetric(run, field))])
    .filter(([, ...values]) => values.every(Number.isFinite))
    .sort(([a], [b]) => runName(a).localeCompare(runName(b)) || a.id.localeCompare(b.id));
  $("chart-note").textContent = !data.length
    ? "No published runs match these filters."
    : !samples.length ? "No runs have values for the selected metrics."
    : (scatter ? "Select a point to watch the run. Numbered points contain overlapping runs. " : "Each bar is one run. ")
      + (samples.length < data.length ? (data.length - samples.length) + " runs are missing a selected metric. " : "")
      + (samples.some(([run]) => fields.some(field => partialUsage(run, field)))
        ? "Red values show reported usage from runs with incomplete usage data. " : "")
      + (fields.includes("cost_usd") && samples.some(([run]) => hasEstimatedCost(run)) ? estimatedCostNote : "");
  if (scatter && samples.length) {
    renderScatter(samples, x, metric, $("x-metric").selectedOptions[0].textContent,
      $("metric-filter").selectedOptions[0].textContent);
  } else {
    const max = metric === "score" ? 1 : Math.max(0, ...samples.map(([, value]) => value));
    const models = [...new Set(samples.map(([run]) => run.team?.name ?? run.settings.model))];
    $("chart").replaceChildren(...samples.map(([run, value]) => {
      const row = el("div", "bar-row");
      const track = el("div", "bar-track");
      const bar = el("div", "bar");
      bar.style.width = (max ? 100 * value / max : 0) + "%";
      bar.style.setProperty("--agent", colors[models.indexOf(run.team?.name ?? run.settings.model) % colors.length]);
      track.append(bar);
      row.title = [runLabel(run), run.comment].filter(Boolean).join("\n");
      const label = el("span", "", (run.team?.name ?? run.settings.model) + " · " + run.settings.seed);
      if (run.comment) label.append(el("small", "graph-comment", run.comment));
      const amount = el("span", "bar-value", comparisonValue(run, metric));
      amount.dataset.partial = partialUsage(run, metric);
      row.append(label, track, amount);
      return row;
    }));
  }

  const table = el("table");
  const head = table.createTHead().insertRow();
  ["Run", "Model", "Provider", "Seed", "Result", "Score", "Halts", "Elapsed", "Tokens", "Cost (USD)", "Setup", "Comment"]
    .forEach(value => head.append(el("th", "", value)));
  const body = table.createTBody();
  for (const run of data) {
    const settings = run.settings;
    const row = body.insertRow();
    const replayButton = el("button", "button", "Watch");
    replayButton.type = "button";
    replayButton.title = runLabel(run);
    replayButton.addEventListener("click", () => watchRun(run));
    row.insertCell().append(replayButton);
    const resolved = run.models?.join(", ");
    const teamModels = run.team && [...new Set(Object.values(run.team.agents).map(agent => agent.settings.model))];
    const values = [
      teamModels ? run.team.name + " · " + teamModels.join(", ")
        : resolved && resolved !== settings.model ? settings.model + " → " + resolved : settings.model,
      run.providers?.join(", ") || "—",
      settings.seed,
      labels[run.status] ?? run.status,
      format("score", run.score),
      format("halts", runMetric(run, "halts")),
      format("elapsed_seconds", run.elapsed_seconds),
      comparisonValue(run, "total_tokens"),
      comparisonValue(run, "cost_usd"),
      settings.agents + (settings.agents === 1 ? " agent" : " agents") + " · " + settings.rounds + " rounds"
        + (teamModels ? "" : " · " + settings.max_completion_tokens + " max reply tokens · "
          + (settings.reasoning_effort ?? "default reasoning")),
    ];
    values.forEach(value => row.append(el("td", "", value)));
    for (const [index, field] of [[9, "total_tokens"], [10, "cost_usd"]]) {
      if (partialUsage(run, field)) {
        row.cells[index].dataset.partial = true;
        row.cells[index].title = "Usage missing for " + run.metrics.missing_usage[field] + " requests.";
      }
    }
    commentCell(row.insertCell(), run);
  }
  $("history").replaceChildren(...(data.length ? [table] : []));
}

$("spectate-tab").addEventListener("click", () => showPage("spectate"));
$("compare-tab").addEventListener("click", () => showPage("compare"));
$("recordings-tab").addEventListener("click", () => showPage("recordings"));
$("show-thinking").addEventListener("change", () => {
  if (frames[cursor]) renderCommunications(frames[cursor].agents);
});
$("delete-unpublished").addEventListener("click", removeUnpublished);
$("profiles-tab").addEventListener("click", () => showPage("profiles"));
$("profiles-configured").addEventListener("change", renderProfiles);
$("refresh-model-catalog").addEventListener("click", () => loadModelCatalog(true));
$("profile-sort").addEventListener("change", renderProfiles);
for (const id of ["profile-search", ...Object.keys(profileRanges)]) $(id).addEventListener("input", renderProfiles);
for (const id of Object.keys(profileFlags)) $(id).addEventListener("change", renderProfiles);
$("reset-profile-filters").addEventListener("click", () => {
  hiddenProfileApis.clear();
  hiddenProfileFamilies.clear();
  $("profiles-configured").checked = false;
  for (const id of Object.keys(profileFlags)) $(id).checked = false;
  for (const id of ["profile-search", "profile-sort", ...Object.keys(profileRanges)]) $(id).value = "";
  renderProfiles();
});
$("teams-tab").addEventListener("click", () => showPage("teams"));
$("model-profile").addEventListener("change", renderLaunchControls);
$("new-profile").addEventListener("click", () => openProfile());
$("profile-backend").addEventListener("change", profileFields);
$("profile-form").elements.remove_key.addEventListener("change", profileFields);
$("cancel-profile").addEventListener("click", () => $("profile-dialog").close());
$("profile-dialog").addEventListener("cancel", event => { if (savingProfile) event.preventDefault(); });
$("profile-dialog").addEventListener("close", () => { $("profile-form").elements.api_key.value = ""; });
$("delete-profile").addEventListener("click", () => {
  if (editingProfile && confirm("Delete the profile “" + editingProfile.name + "”?")) {
    updateProfile("delete", {id: editingProfile.id});
  }
});
$("profile-form").addEventListener("submit", event => {
  event.preventDefault();
  const fields = event.currentTarget.elements;
  const number = name => fields[name].value === "" ? null : fields[name].valueAsNumber;
  const backend = fields.backend.value;
  const body = {name: fields.name.value, backend, model: fields.model.value.trim(), rounds: number("rounds"),
    max_completion_tokens: number("max_completion_tokens"), request_timeout: number("request_timeout")};
  if (editingProfile) body.id = editingProfile.id;
  if (fields.remove_key.checked) body.api_key = "";
  else if (fields.api_key.value.trim()) body.api_key = fields.api_key.value;
  if (backend === "openrouter") body.provider = fields.provider.value.trim() || null;
  if (backend === "gemini") {
    body.gemini_rpm = number("gemini_rpm");
    body.gemini_input_tpm = number("gemini_input_tpm");
    body.gemini_thought_summaries = fields.gemini_thought_summaries.checked;
  }
  if (!$("profile-reasoning").hidden) body.reasoning_effort = fields.reasoning_effort.value || null;
  updateProfile("save", body);
});
$("graph-source").addEventListener("change", refreshFilters);
$("level-filter").addEventListener("change", refreshFilters);
$("metric-filter").addEventListener("change", renderComparison);
$("x-metric").replaceChildren(...[...$("metric-filter").options].map(option => option.cloneNode(true)));
$("x-metric").value = "total_tokens";
$("x-metric").addEventListener("change", renderComparison);
$("chart-type").addEventListener("change", renderComparison);
$("seed-filter").addEventListener("change", renderComparison);
const squad = squadPicker(renderLaunchControls);
const teams = teamEditor({
  post: postRun, get, profiles: () => modelProfiles, manageProfiles: () => showPage("profiles"),
  changed(next, preferred) {
    savedTeams = next;
    renderLaunchControls();
    if (preferred && [...$("model-profile").options].some(option => option.value === "team:" + preferred)) {
      $("model-profile").value = "team:" + preferred;
      renderLaunchControls();
    }
  },
});
poll();
