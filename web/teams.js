import {agentColors, drawAgent, spriteNames} from "/agent-sprites.js";

const $ = id => document.getElementById(id);
const defaultNames = ["Ann", "Ben", "Cat", "Dug", "Egg", "Fax", "Guy", "Han"];
const el = (tag, text, className) => Object.assign(document.createElement(tag), {
  textContent: text ?? "", className: className ?? "",
});

function control(title, input) {
  const label = el("label", title, "control");
  label.append(input);
  return label;
}

function select(choices, selected) {
  const input = el("select");
  for (const [value, label] of choices) input.append(new Option(label, value));
  input.value = selected;
  return input;
}

function avatar(agent, index) {
  const canvas = el("canvas", "", "team-avatar");
  canvas.width = canvas.height = 56;
  canvas.setAttribute("aria-hidden", "true");
  drawAgent(canvas, String(index + 1), agentColors[index], agent);
  return canvas;
}

export function teamEditor({post, get, profiles, manageProfiles, changed}) {
  const dialog = $("team-dialog");
  const form = $("team-form");
  const roster = $("team-roster");
  let teams = [], editing = null, saving = false, revision = 0;
  const configuredProfiles = () => profiles().filter(profile => profile.enabled);

  function status(element, message) {
    element.hidden = !message;
    element.textContent = message ?? "";
  }

  function render() {
    const hasConfiguredProfiles = configuredProfiles().length > 0;
    $("new-team").disabled = !hasConfiguredProfiles;
    const models = new Map(profiles().map(profile => [profile.id, profile]));
    const cards = teams.map(team => {
      const card = el("article", "", "panel team-card");
      const heading = el("div", "", "team-card-header");
      const edit = el("button", "Edit", "button");
      edit.type = "button";
      edit.setAttribute("aria-label", "Edit " + team.name);
      edit.addEventListener("click", () => open(team));
      heading.append(el("h2", team.name), edit);
      const members = el("ul", "", "team-members");
      team.agents.forEach((agent, index) => {
        const row = el("li");
        const profile = models.get(agent.profile_id);
        const details = el("div");
        details.append(el("strong", agent.name), el("span",
          profile ? profile.name + (profile.enabled ? "" : " · Needs API key") : "Model profile removed", "quiet"));
        row.append(avatar(agent, index), details);
        members.append(row);
      });
      card.append(heading, members);
      return card;
    });
    if (!cards.length || !hasConfiguredProfiles) {
      const empty = el("div", "", "empty");
      empty.append(el("p", hasConfiguredProfiles
        ? "Create a team and choose a model profile for each agent."
        : "Save an API key in a model profile to add agents."));
      if (!hasConfiguredProfiles) {
        const button = el("button", "Open model profiles", "button");
        button.type = "button";
        button.addEventListener("click", manageProfiles);
        empty.append(button);
      }
      cards.push(empty);
    }
    $("teams-list").replaceChildren(...cards);
  }

  async function load() {
    const current = ++revision;
    try {
      const result = await get("/api/teams");
      if (current !== revision) return;
      teams = result;
      status($("teams-status"));
      render();
      changed(teams);
    } catch {
      if (current !== revision) return;
      status($("teams-status"), "Could not load team profiles.");
    }
  }

  function syncRows() {
    const rows = [...roster.children];
    $("add-team-agent").disabled = rows.length === agentColors.length || !configuredProfiles().length;
    rows.forEach((row, index) => {
      row.setAttribute("aria-label", "Agent " + (index + 1));
      row.querySelector("button").disabled = rows.length === 1;
      row.draw(index);
    });
  }

  function add(agent) {
    const index = roster.children.length;
    if (index >= agentColors.length) return;
    const badges = new Set([...roster.querySelectorAll('[name="badge"]')].map(input => input.value.toUpperCase()));
    const defaultName = defaultNames.find(name => !badges.has(name[0]));
    const name = el("input");
    name.name = "name";
    name.maxLength = 40;
    name.required = true;
    name.value = agent?.name ?? defaultName;
    const badge = el("input");
    badge.name = "badge";
    badge.maxLength = 1;
    badge.pattern = "[A-Za-z0-9]";
    badge.required = true;
    badge.value = agent?.badge ?? defaultName[0];
    const sprite = select(Object.entries(spriteNames), agent?.sprite ?? Object.keys(spriteNames)[index]);
    sprite.name = "sprite";
    const choices = configuredProfiles().map(profile => [profile.id, profile.name]);
    const previousId = roster.lastElementChild?.querySelector('[name="profile_id"]').value;
    const defaultId = choices.some(([id]) => id === previousId) ? previousId : choices[0]?.[0];
    const profile = select(choices, agent?.profile_id ?? defaultId);
    if (agent && !choices.some(([id]) => id === agent.profile_id)) {
      const saved = profiles().find(profile => profile.id === agent.profile_id);
      const unavailable = new Option(saved ? saved.name + " · Needs API key" : "Model profile removed", agent.profile_id);
      unavailable.disabled = unavailable.hidden = true;
      profile.prepend(unavailable);
      profile.value = agent.profile_id;
    }
    profile.name = "profile_id";
    profile.required = true;
    const row = el("div", "", "team-member-fields");
    row.setAttribute("role", "group");
    const preview = avatar({sprite: sprite.value, badge: badge.value}, index);
    row.draw = slot => drawAgent(preview, String(slot + 1), agentColors[slot], {
      sprite: sprite.value, badge: badge.value || String(slot + 1),
    });
    row.addEventListener("input", () => row.draw([...roster.children].indexOf(row)));
    const remove = el("button", "Remove", "button");
    remove.type = "button";
    remove.addEventListener("click", () => {
      row.remove();
      syncRows();
    });
    row.append(preview, control("Agent name", name), control("Character", badge),
      control("Sprite", sprite), control("Model profile", profile), remove);
    roster.append(row);
    syncRows();
  }

  function open(team) {
    editing = team ?? null;
    form.reset();
    roster.replaceChildren();
    $("team-heading").textContent = team ? "Edit team" : "New team";
    $("team-name").value = team?.name ?? "";
    $("team-rounds").value = team?.rounds ?? "";
    $("delete-team").hidden = !team;
    status($("team-error"));
    if (team) team.agents.forEach(add);
    else add();
    dialog.showModal();
  }

  async function update(action, data) {
    if (saving) return;
    saving = form.inert = true;
    status($("team-error"));
    try {
      const result = await post("teams/" + action, data, "Could not update the team.");
      ++revision;
      teams = teams.filter(team => team.id !== result.id);
      if (action === "save") teams.push(result);
      dialog.close();
      render();
      changed(teams, action === "save" ? result.id : null);
    } catch (error) {
      status($("team-error"), error.message);
    } finally {
      saving = form.inert = false;
    }
  }

  $("new-team").addEventListener("click", () => open());
  $("add-team-agent").addEventListener("click", () => add());
  $("cancel-team").addEventListener("click", () => dialog.close());
  dialog.addEventListener("cancel", event => { if (saving) event.preventDefault(); });
  $("delete-team").addEventListener("click", () => {
    if (editing && confirm("Delete the team “" + editing.name + "”?")) update("delete", {id: editing.id});
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    const data = {
      name: $("team-name").value,
      rounds: $("team-rounds").value === "" ? null : $("team-rounds").valueAsNumber,
      agents: [...roster.children].map(row => Object.fromEntries(
        [...row.querySelectorAll("input, select")].map(input => [input.name, input.value]))),
    };
    if (editing) data.id = editing.id;
    update("save", data);
  });
  return {load, render};
}
