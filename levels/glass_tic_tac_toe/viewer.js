import {drawBubble, drawSprite} from "/agent-sprites.js";
export {drawAgent} from "/agent-sprites.js";

export const supportsSight = true;
export const supportsMotion = true;

const mint = "#8df0d7";
const amber = "#f8b279";

function glyph(ctx, mark, x, y, radius, color, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.beginPath();
  if (mark === "X") {
    ctx.moveTo(x - radius, y - radius);
    ctx.lineTo(x + radius, y + radius);
    ctx.moveTo(x + radius, y - radius);
    ctx.lineTo(x - radius, y + radius);
  } else {
    ctx.arc(x, y, radius * 1.12, 0, Math.PI * 2);
  }
  ctx.stroke();
}

export function drawWorld(canvas, world, {color, focus, sight, actor, move, bubble, character, pulse}) {
  const ctx = canvas.getContext("2d");
  const size = canvas.width;
  const inset = 29;
  const step = (size - inset * 2) / world.size;
  const end = size - inset;
  const edge = value => inset + value * step;
  const center = value => edge(value + 0.5);
  const players = world.players.map(player => {
    if (!move || player.id !== actor) return player;
    return {...player, x: move.from.x + (player.x - move.from.x) * move.progress,
      y: move.from.y + (player.y - move.from.y) * move.progress};
  });
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = "#09121d";
  ctx.fillRect(0, 0, size, size);
  ctx.fillStyle = "#122235";
  ctx.fillRect(inset, inset, end - inset, end - inset);
  ctx.save();
  ctx.beginPath();
  ctx.rect(inset, inset, end - inset, end - inset);
  ctx.clip();

  ctx.fillStyle = "#203449";
  for (let y = 0; y < world.size; y++) {
    for (let x = 0; x < world.size; x++) {
      ctx.fillRect(center(x) - 1, center(y) - 1, 2, 2);
    }
  }
  const [min, max] = world.board;
  ctx.fillStyle = "#1a3145";
  ctx.fillRect(edge(min), edge(min), (max - min + 1) * step, (max - min + 1) * step);
  ctx.strokeStyle = "#405a71";
  ctx.lineWidth = 1.5;
  ctx.strokeRect(edge(min), edge(min), (max - min + 1) * step, (max - min + 1) * step);
  ctx.strokeStyle = "#65839a";
  ctx.lineWidth = 3;
  for (const line of world.lines) {
    ctx.beginPath();
    ctx.moveTo(center(line), edge(min));
    ctx.lineTo(center(line), edge(max + 1));
    ctx.moveTo(edge(min), center(line));
    ctx.lineTo(edge(max + 1), center(line));
    ctx.stroke();
  }

  if (sight) {
    for (const player of players) {
      if (focus && player.id !== focus) continue;
      const x = center(player.x), y = center(player.y);
      const radius = (world.radius + 0.5) * step;
      const tint = color(player.id);
      const light = ctx.createRadialGradient(x, y, 0, x, y, radius);
      light.addColorStop(0, tint + "36");
      light.addColorStop(0.65, tint + "1c");
      light.addColorStop(1, tint + "08");
      ctx.fillStyle = light;
      ctx.strokeStyle = tint + "52";
      ctx.lineWidth = 1.3;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }

  if (move) {
    const player = world.players.find(candidate => candidate.id === actor);
    const startX = center(move.from.x), startY = center(move.from.y);
    ctx.save();
    ctx.strokeStyle = color(actor) + "a0";
    ctx.setLineDash([4, 5]);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(startX, startY);
    ctx.lineTo(center(player.x), center(player.y));
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.strokeRect(startX - 3, startY - 3, 6, 6);
    ctx.restore();
  }

  const signX = size / 2, signY = center(world.sign_y);
  ctx.fillStyle = "#101c29";
  ctx.fillRect(signX - 141, signY - 25, 282, 50);
  ctx.strokeStyle = "#597188";
  ctx.lineWidth = 1;
  ctx.strokeRect(signX - 141, signY - 25, 282, 50);
  ctx.fillStyle = "#f4e7d1";
  ctx.font = "bold 19px ui-monospace, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const [first, ...rest] = world.sign.split(". ");
  ctx.fillText(first, signX, signY - 8, 266);
  if (rest.length) {
    ctx.font = "12px ui-monospace, monospace";
    ctx.fillText(rest.join(". "), signX, signY + 12, 266);
  }

  for (const {x, y, mark} of world.pieces) {
    ctx.fillStyle = "#0c1925";
    ctx.beginPath();
    ctx.arc(center(x), center(y), 12, 0, Math.PI * 2);
    ctx.fill();
    glyph(ctx, mark, center(x), center(y), 6, mark === "X" ? mint : amber, 2.6);
  }
  ctx.restore();
  ctx.strokeStyle = "#4b647d";
  ctx.lineWidth = 2;
  ctx.strokeRect(inset, inset, end - inset, end - inset);

  const ordered = [...players].sort((a, b) => Number(a.id === actor) - Number(b.id === actor));
  const keepVisible = (coordinate, margin) => Math.max(margin, Math.min(size - margin, coordinate));
  function position(player, state) {
    const group = state.players.filter(other => other.x === player.x && other.y === player.y);
    const onPiece = state.pieces.some(piece => piece.x === player.x && piece.y === player.y);
    const angle = group.indexOf(player) * (2 * Math.PI / group.length) - Math.PI / 4;
    const shift = group.length > 1 ? Math.max(onPiece ? 34 : 0, 19 / Math.sin(Math.PI / group.length))
      : onPiece ? 33 : 0;
    return [keepVisible(center(player.x), 30 + shift) + shift * Math.cos(angle),
      keepVisible(center(player.y), 30 + shift) + shift * Math.sin(angle)];
  }
  let speaker;
  for (const player of ordered) {
    const endpoint = world.players.find(candidate => candidate.id === player.id);
    const baseX = center(player.x), baseY = center(player.y);
    let [x, y] = position(endpoint, world);
    if (move) {
      const start = move.world.players.find(candidate => candidate.id === player.id) ?? endpoint;
      const [startX, startY] = position(start, move.world);
      x = startX + (x - startX) * move.progress;
      y = startY + (y - startY) * move.progress;
    }
    if (Math.hypot(x - baseX, y - baseY) > 1) {
      ctx.beginPath();
      ctx.strokeStyle = color(player.id);
      ctx.lineWidth = 1.5;
      ctx.moveTo(baseX, baseY);
      ctx.lineTo(x, y);
      ctx.stroke();
    }
    drawSprite(ctx, player.id, x, y, color(player.id), {
      active: actor === player.id, stride: actor === player.id ? move?.progress : 0,
      character: character?.(player.id), pulse: actor === player.id ? pulse : 0,
    });
    if (bubble?.agent === player.id) speaker = [x, y - 26];
    if (player.carrying) {
      ctx.fillStyle = "#09131f";
      ctx.beginPath();
      ctx.arc(x + 19, y - 15, 8, 0, Math.PI * 2);
      ctx.fill();
      glyph(ctx, player.carrying, x + 19, y - 15, 3,
        player.carrying === "X" ? mint : amber, 1.8);
    }
  }
  if (speaker) drawBubble(ctx, ...speaker, bubble, color(bubble.agent));
}

export function drawLook(canvas, observation, color) {
  const ctx = canvas.getContext("2d");
  const n = observation.grid.length;
  const padding = 6;
  const step = (canvas.width - 2 * padding) / n;
  const mid = (n - 1) / 2;
  const tiles = {"#": "#0e1520", ".": "#315068", "+": "#9fb0c1", "!": "#f9d68a", X: mint, O: amber};
  observation.grid.forEach((row, y) => {
    [...row].forEach((mark, x) => {
      if (mark === " ") return;
      const left = padding + x * step, top = padding + y * step;
      const dim = Math.min(1, Math.hypot(x - mid, y - mid) / mid);
      ctx.globalAlpha = 1 - 0.4 * dim;
      ctx.fillStyle = mark === "X" || mark === "O" ? tiles["."] : tiles[mark];
      ctx.fillRect(left, top, step - 0.75, step - 0.75);
      if (mark === "X" || mark === "O") glyph(ctx, mark, left + step / 2, top + step / 2, 2.6, tiles[mark], 1.4);
    });
  });
  ctx.globalAlpha = 1;
  ctx.beginPath();
  ctx.arc(canvas.width / 2, canvas.height / 2, (mid + 0.43) * step, 0, 2 * Math.PI);
  ctx.strokeStyle = color + "42";
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.2;
  ctx.strokeRect(padding + mid * step - 0.5, padding + mid * step - 0.5, step, step);
  const markers = new Map();
  for (const {id, dx, dy} of observation.agents ?? []) {
    const key = dx + "," + dy;
    if (!markers.has(key)) markers.set(key, []);
    markers.get(key).push(id);
  }
  ctx.font = "bold 8px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const [key, ids] of markers) {
    const [dx, dy] = key.split(",").map(Number);
    const x = padding + (mid + dx + 0.5) * step;
    const y = padding + (mid + dy + 0.5) * step;
    ctx.fillStyle = "#10202d";
    ctx.beginPath();
    ctx.arc(x, y, step / 2, 0, 2 * Math.PI);
    ctx.fill();
    ctx.fillStyle = "#e7f3fb";
    ctx.fillText(ids.join(","), x, y, step - 1);
  }
}
