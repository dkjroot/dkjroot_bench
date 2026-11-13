import {agentColors, drawAgent} from "/agent-sprites.js";

const $ = id => document.getElementById(id);

export function squadPicker(onChange) {
  const panel = $("squad-picker");
  const size = $("squad-size");
  const playing = $("squad-selected");
  const bench = $("squad-available");
  const picks = new Map();
  let team, level, key, signature, chosen = [], drag = null;

  const available = () => team.available_agents;
  const capacity = () => Math.min(level.max_agents, available().length);
  const valid = () => Boolean(team && level && chosen.length >= level.min_agents && chosen.length <= capacity());

  function button(row, text, action, label, disabled = false) {
    const control = document.createElement("button");
    control.className = "button";
    control.type = "button";
    control.textContent = text;
    control.setAttribute("aria-label", label);
    control.dataset.action = action;
    control.disabled = disabled;
    control.addEventListener("click", () => {
      const index = Number(row.dataset.agent);
      const position = chosen.indexOf(index);
      if (action === "add") move(index);
      else if (action === "remove") move(index, null, true);
      else {
        const adjacent = position + (action === "up" ? -1 : 1);
        [chosen[adjacent], chosen[position]] = [chosen[position], chosen[adjacent]];
        update(index, action);
      }
    });
    return control;
  }

  function rowFor(index, position = -1) {
    const agent = team.agents[index];
    const ready = available().includes(index);
    const row = document.createElement("li");
    row.dataset.agent = index;
    row.draggable = ready;
    const sprite = document.createElement("canvas");
    sprite.width = sprite.height = 56;
    sprite.className = "team-avatar";
    sprite.setAttribute("aria-hidden", "true");
    drawAgent(sprite, String(index + 1), agentColors[position >= 0 ? position : index], agent);
    const name = document.createElement("span");
    name.className = "squad-agent-name";
    name.textContent = agent.name;
    row.append(sprite, name);
    if (position >= 0) {
      row.append(button(row, "↑", "up", "Move " + agent.name + " earlier", position === 0),
        button(row, "↓", "down", "Move " + agent.name + " later", position === chosen.length - 1),
        button(row, "Bench", "remove", "Bench " + agent.name));
    } else if (ready) {
      row.append(button(row, "Add", "add", "Add " + agent.name + " to squad", chosen.length >= capacity()));
    } else {
      const note = document.createElement("span");
      note.className = "quiet";
      note.textContent = "Model profile unavailable";
      row.append(note);
    }
    return row;
  }

  function render() {
    size.replaceChildren();
    if (!valid()) size.append(new Option("Choose size", ""));
    for (let count = level.min_agents; count <= capacity(); count++) size.append(new Option(count, count));
    size.disabled = capacity() < level.min_agents;
    size.value = valid() ? String(chosen.length) : "";
    $("squad-limits").textContent = "Puzzle minimum " + level.min_agents + " · Maximum " + level.max_agents
      + " · " + chosen.length + " selected";
    playing.replaceChildren(...chosen.map(rowFor));
    bench.replaceChildren(...team.agents.flatMap((_, index) => chosen.includes(index) ? [] : [rowFor(index)]));
    $("squad-help").textContent = capacity() < level.min_agents
      ? "This team needs " + (level.min_agents - capacity()) + " more agents with available model profiles."
      : !valid() ? "Add agents or set a squad size to reach the puzzle minimum."
      : "Drag to reorder or swap agents. You can also use the buttons.";
  }

  function update(index, action) {
    picks.set(key, {signature, agents: chosen.slice()});
    render();
    if (index != null) {
      const row = panel.querySelector('[data-agent="' + index + '"]');
      (row?.querySelector('[data-action="' + action + '"]:enabled')
        ?? row?.querySelector("button:enabled") ?? size).focus();
    }
    onChange();
  }

  function move(index, before = null, toBench = false) {
    const from = chosen.indexOf(index);
    const target = chosen.indexOf(before);
    if (toBench) {
      if (from < 0) return;
      chosen.splice(from, 1);
    } else if (from >= 0) {
      const end = target < 0 ? chosen.length - 1 : target;
      if (from === end) return;
      chosen.splice(from, 1);
      chosen.splice(end, 0, index);
    } else if (target >= 0) {
      chosen[target] = index;
    } else if (chosen.length < capacity()) {
      chosen.push(index);
    } else return;
    update(index, toBench ? "add" : "remove");
  }

  size.addEventListener("change", () => {
    const count = Number(size.value);
    if (!count) return;
    chosen = chosen.concat(available().filter(index => !chosen.includes(index))).slice(0, count);
    update();
  });

  panel.addEventListener("dragstart", event => {
    const row = event.target.closest("li[data-agent]");
    if (!row?.draggable) return;
    drag = Number(row.dataset.agent);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", row.textContent);
    row.dataset.dragging = "true";
  });
  function dropZone(event) {
    if (drag === null) return null;
    const column = event.target.closest(".squad-column");
    if (!column || (column.contains(bench) && !chosen.includes(drag))) return null;
    return column;
  }
  panel.addEventListener("dragover", event => {
    const column = dropZone(event);
    if (!column) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const target = event.target.closest("li[data-agent]") ?? column;
    panel.querySelector("[data-drop]")?.removeAttribute("data-drop");
    target.dataset.drop = "true";
  });
  panel.addEventListener("drop", event => {
    const column = dropZone(event);
    if (!column) return;
    event.preventDefault();
    const row = event.target.closest("li[data-agent]");
    move(drag, row && Number(row.dataset.agent), column.contains(bench));
    endDrag();
  });
  function endDrag() {
    drag = null;
    panel.querySelector("[data-drop]")?.removeAttribute("data-drop");
    panel.querySelector("[data-dragging]")?.removeAttribute("data-dragging");
  }
  panel.addEventListener("dragend", endDrag);
  panel.addEventListener("dragleave", event => {
    if (!panel.contains(event.relatedTarget)) panel.querySelector("[data-drop]")?.removeAttribute("data-drop");
  });

  return {
    show(nextTeam, nextLevel, busy) {
      team = nextTeam;
      level = nextLevel;
      panel.hidden = !team || !level || busy;
      if (!team || !level) return;
      const nextKey = team.id + "/" + level.id;
      const nextSignature = JSON.stringify([team.revision, team.available_agents,
        level.min_agents, level.max_agents, level.default_agents]);
      if (key === nextKey && signature === nextSignature) return;
      endDrag();
      key = nextKey;
      signature = nextSignature;
      const stored = picks.get(key);
      const startingSize = Math.min(capacity(), Math.max(level.min_agents, level.default_agents));
      chosen = stored?.signature === signature ? stored.agents.slice() : available().slice(0, startingSize);
      render();
    },
    valid,
    selected: () => chosen.slice(),
  };
}
