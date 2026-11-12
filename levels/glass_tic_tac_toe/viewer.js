export const supportsSight = true;
export const supportsMotion = true;

const mint = "#8df0d7";
const amber = "#f8b279";
const ink = "#07111d";

// Rocket, rover, diamond, dome, walker, glider, shield and twin pod.
const silhouettes = [
  "M0 -24 9 -16 13 -5 13 7 18 13 18 19 8 14 5 21H-5L-8 14-18 19V13L-13 7V-5L-9 -16Z",
  "M-20 -9H-13V-16H13V-9H20V14L15 18H-15L-20 14Z",
  "M0 -23 20 -3V3L0 23-20 3V-3Z",
  "M-21 -10H-14Q0 -28 14 -10H21V-1H16Q19 12 9 18H-9Q-19 12 -16 -1H-21Z",
  "M-3 -24H3V-18H10L14 -12V13H9V22H3V14H-3V22H-9V13H-14V-12L-10 -18H-3Z",
  "M-22 -18 -8 -10 0 -17 8 -10 22 -18V3L13 12H5L0 22-5 12H-13L-22 3Z",
  "M-19 -15H7L18 -22V8L11 16 0 22-11 16-19 7Z",
  "M-21 -18H-10L-5 -12H5L10 -18H21V1H16V14H10V20H-10V14H-16V1H-21Z",
].map(path => new Path2D(path));

const numbers = [
  ["010", "110", "010", "010", "111"],
  ["111", "001", "111", "100", "111"],
  ["111", "001", "111", "001", "111"],
  ["101", "101", "111", "001", "001"],
  ["111", "100", "111", "001", "111"],
  ["111", "100", "111", "101", "111"],
  ["111", "001", "010", "010", "010"],
  ["111", "101", "111", "101", "111"],
];

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

function sprite(ctx, id, x, y, color, active, stride = 0) {
  const index = Number(id) - 1;
  ctx.save();
  ctx.translate(Math.round(x), Math.round(y));
  ctx.fillStyle = ink;
  ctx.beginPath();
  ctx.ellipse(0, 20, 17, 5, 0, 0, Math.PI * 2);
  ctx.fill();
  if (active) {
    ctx.beginPath();
    for (const dx of [-1, 1]) {
      for (const dy of [-1, 1]) {
        ctx.moveTo(dx * 19, dy * 27);
        ctx.lineTo(dx * 27, dy * 27);
        ctx.lineTo(dx * 27, dy * 19);
      }
    }
    ctx.lineJoin = "round";
    ctx.strokeStyle = ink;
    ctx.lineWidth = 5;
    ctx.stroke();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2.5;
    ctx.stroke();
  }

  ctx.translate(0, -Math.round(2 * Math.sin(stride * Math.PI)));
  ctx.lineJoin = "round";
  ctx.strokeStyle = ink;
  ctx.lineWidth = 4;
  const body = silhouettes[index];
  ctx.stroke(body);
  ctx.fillStyle = color;
  ctx.fill(body);
  ctx.fillStyle = ink;
  ctx.fillRect(-7, -11, 4, 3);
  ctx.fillRect(3, -11, 4, 3);
  ctx.fillRect(-8, -5, 16, 20);
  ctx.fillStyle = "#fff";
  numbers[index].forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      if (row[x] === "1") ctx.fillRect(-4 + x * 3, -2 + y * 3, 3, 3);
    }
  });
  ctx.restore();
}

export function drawAgent(canvas, id, color) {
  const ctx = canvas.getContext("2d");
  sprite(ctx, id, canvas.width / 2, canvas.height / 2, color, false);
}

export function drawWorld(canvas, world, {color, focus, sight, actor, move}) {
  const ctx = canvas.getContext("2d");
  const size = canvas.width;
  const inset = 29;
  const step = (size - inset * 2) / world.size;
  const end = size - inset;
  const edge = value => inset + value * step;
  const center = value => edge(value + 0.5);
  const players = world.players.map(player => {
    if (!move || player.id !== actor) return player;
    const ease = 1 - (1 - move.progress) ** 2;
    return {...player, x: move.from.x + (player.x - move.from.x) * ease,
      y: move.from.y + (player.y - move.from.y) * ease};
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
  for (const player of ordered) {
    const group = players.filter(other => other.x === player.x && other.y === player.y);
    const onPiece = world.pieces.some(piece => piece.x === player.x && piece.y === player.y);
    const angle = group.indexOf(player) * (2 * Math.PI / group.length) - Math.PI / 4;
    const shift = group.length > 1 ? Math.max(onPiece ? 34 : 0, 19 / Math.sin(Math.PI / group.length))
      : onPiece ? 33 : 0;
    const baseX = center(player.x), baseY = center(player.y);
    const x = keepVisible(baseX, 30 + shift) + shift * Math.cos(angle);
    const y = keepVisible(baseY, 30 + shift) + shift * Math.sin(angle);
    if (shift) {
      ctx.beginPath();
      ctx.strokeStyle = color(player.id);
      ctx.lineWidth = 1.5;
      ctx.moveTo(baseX, baseY);
      ctx.lineTo(x, y);
      ctx.stroke();
    }
    sprite(ctx, player.id, x, y, color(player.id), actor === player.id,
      actor === player.id ? move?.progress : 0);
    if (player.carrying) {
      ctx.fillStyle = "#09131f";
      ctx.beginPath();
      ctx.arc(x + 19, y - 15, 8, 0, Math.PI * 2);
      ctx.fill();
      glyph(ctx, player.carrying, x + 19, y - 15, 3,
        player.carrying === "X" ? mint : amber, 1.8);
    }
  }
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
}
