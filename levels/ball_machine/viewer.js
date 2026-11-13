import {drawBubble, drawSprite} from "/agent-sprites.js";
export {drawAgent} from "/agent-sprites.js";

export const worldTitle = "Control corridor & mechanism";
export const canvasSize = [760, 520];
export const supportsMotion = true;
export const supportsTickMotion = true;
export const sharedObservation = true;

const ink = "#e5f2fa";
const muted = "#a1b8c8";
const steel = "#6f8a9c";
const stationColors = {
  hoist: "#55c9f2", jaws: "#ffbc6c", slide: "#93d6ac", tilt: "#c5a7ff", lid: "#4be4cf",
};
const phases = {
  held: "in the gripper", falling: "falling", rolling: "on the plank",
  skidding: "skidding on the plank", to_goal: "rolling towards the bucket",
  in_bucket: "inside the bucket", spill: "falling to the floor",
};
const mix = (a, b, progress) => a + (b - a) * progress;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const point = (x, y) => [44 + x * 48, 41 + y * 40];
const corridor = {left: 18, top: 18, right: 742, bottom: 143};

function line(ctx, points, color, width = 1) {
  ctx.beginPath();
  ctx.moveTo(...points[0]);
  points.slice(1).forEach(([x, y]) => ctx.lineTo(x, y));
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.stroke();
}

function box(ctx, x, y, w, h, radius, fill, outline) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, radius);
  ctx.fillStyle = fill;
  ctx.fill();
  if (outline) {
    ctx.lineWidth = 1.3;
    ctx.strokeStyle = outline;
    ctx.stroke();
  }
}

function disc(ctx, x, y, radius, fill, outline, width = 1.4) {
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, 2 * Math.PI);
  ctx.fillStyle = fill;
  ctx.fill();
  if (outline) {
    ctx.strokeStyle = outline;
    ctx.lineWidth = width;
    ctx.stroke();
  }
}

function label(ctx, text, x, y, {color = ink, size = 13, align = "center", weight = 600} = {}) {
  ctx.font = `${weight} ${size}px "IBM Plex Mono", ui-monospace, Menlo, monospace`;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.fillText(text, x, y);
}

function ball(ctx, x, y, radius = 10, neutral = false) {
  const light = ctx.createRadialGradient(x - radius * 0.3, y - radius * 0.35, 0, x, y, radius);
  light.addColorStop(0, neutral ? "#f9fcfd" : "#ffedb9");
  light.addColorStop(0.45, neutral ? "#d6e2e8" : "#ffc56b");
  light.addColorStop(1, neutral ? "#879ba7" : "#dd7740");
  disc(ctx, x, y, radius, light, neutral ? "#c7d5dd" : "#ffe1a2", 1.2);
}

function drawCorridor(ctx, world, controls, neutral) {
  const {left, top, right, bottom} = corridor;
  box(ctx, left, top, right - left, bottom - top, 11, "#152735", "#33566a");
  const {rows, columns} = world.corridor;
  for (let y = 0; y < rows; y++) {
    line(ctx, [point(0, y), point(columns - 1, y)], "#263f50");
  }
  for (let x = 0; x < columns; x++) {
    line(ctx, [point(x, 0), point(x, rows - 1)], "#263f50");
    for (let y = 0; y < rows; y++) disc(ctx, ...point(x, y), 2.3, "#406078");
  }
  if (!neutral) {
    const [startX, startY] = point(0, 0);
    box(ctx, startX - 19, startY - 16, 38, 32, 5, "#203746", "#557389");
    label(ctx, "START", startX, 10, {color: muted, size: 10});
  }
  for (const station of world.stations) {
    const [x, y] = point(station.x, station.y);
    if (neutral) {
      box(ctx, x - 19, y - 16, 38, 32, 5, "#203746", "#657d8c");
      continue;
    }
    const color = stationColors[station.id];
    const amount = controls[station.id];
    box(ctx, x - 19, y - 16, 38, 32, 5, amount == null ? "#233849" : color + "50", color);
    box(ctx, x - 15, y + 10, 30, 3, 1.5, "#11212b");
    if (amount > 0) box(ctx, x - 15, y + 10, 30 * amount, 3, 1.5, color);
    label(ctx, station.label, x, 154, {color, size: 14});
  }
  line(ctx, [[18, 170], [742, 170]], "#385061", 2);
}

function playerSprites(world) {
  const groups = new Map();
  for (const player of world.players) {
    const key = player.x + "/" + player.y;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(player);
  }
  const sprites = [];
  for (const group of groups.values()) {
    const shared = group.length > 1;
    const scale = shared ? 0.46 : 0.65;
    const spread = shared ? 12 / Math.sin(Math.PI / group.length) : 0;
    const margin = spread + 14;
    group.forEach((player, index) => {
      const [cx, cy] = point(player.x, player.y);
      let px = cx, py = cy;
      if (shared) {
        const angle = index / group.length * 2 * Math.PI - Math.PI / 2;
        px = clamp(cx, corridor.left + margin, corridor.right - margin) + Math.cos(angle) * spread;
        py = clamp(cy, corridor.top + margin, corridor.bottom - margin) + Math.sin(angle) * spread;
      }
      sprites.push({id: player.id, px, py, cx, cy, scale});
    });
  }
  return sprites;
}

function drawPlayers(ctx, world, {color, actor, focus, move, bubble, character, pulse}) {
  const sprites = playerSprites(world);
  const previous = new Map(move ? playerSprites(move.world).map(sprite => [sprite.id, sprite]) : []);
  const active = id => id === actor || id === focus;
  sprites.sort((a, b) => Number(active(a.id)) - Number(active(b.id)));
  let speaker;
  for (const sprite of sprites) {
    const {id} = sprite;
    const from = previous.get(id) ?? sprite;
    const progress = move?.progress ?? 1;
    const [px, py, cx, cy, scale] = ["px", "py", "cx", "cy", "scale"].map(key => mix(from[key], sprite[key], progress));
    const tint = color(id);
    if (px !== cx || py !== cy) line(ctx, [[cx, cy], [px, py]], tint + "88");
    drawSprite(ctx, id, px, py, tint, {active: active(id), stride: id === actor ? progress : 0, scale,
      character: character?.(id), pulse: id === actor ? pulse : 0});
    if (bubble?.agent === id) speaker = [px, py - 27 * scale];
  }
  return speaker;
}

function drawWiring(ctx, world, controls, layout) {
  const {rail, plank, goal} = layout;
  const endpoints = {
    hoist: [[rail.left, 198]],
    jaws: [[rail.left + 74, 198]],
    slide: [[rail.right, 198]],
    tilt: [[524, 289], [plank.x, 289], [plank.x, plank.y]],
    lid: [[668, goal.top], [goal.x + goal.half_width, goal.top]],
  };
  for (const station of world.stations) {
    const [x] = point(station.x, station.y);
    const active = controls[station.id] != null;
    const tint = stationColors[station.id];
    const path = [[x, 165], [x, 180], ...endpoints[station.id]];
    line(ctx, path, "#10202c", 5);
    line(ctx, path, tint + (active ? "b8" : "48"), active ? 2 : 1.4);
  }
}

function drawBucket(ctx, shape, scored, {supply = false, neutral = false} = {}) {
  const {x, half_width: half, top, bottom} = shape;
  const walls = [[x - half, top], [x - half + 11, bottom],
    [x + half - 11, bottom], [x + half, top]];
  ctx.beginPath();
  ctx.moveTo(...walls[0]);
  walls.slice(1).forEach(point => ctx.lineTo(...point));
  ctx.closePath();
  ctx.fillStyle = neutral ? "#263644" : supply ? "#483327" : "#123637";
  ctx.fill();
  if (supply) {
    for (const [dx, dy] of [[-31, -13], [-10, -13], [12, -13], [32, -13],
                            [-23, -32], [-2, -32], [20, -32], [10, -50]]) {
      ball(ctx, x + dx, bottom + dy, 9, neutral);
    }
  } else {
    for (let index = 0; index < scored; index++) {
      ball(ctx, x + 29 - index * 22, bottom - 13, 9, neutral);
    }
  }
  const accent = neutral ? "#91a7b4" : supply ? "#bc8a65" : "#4b998e";
  line(ctx, walls, "#0b151d", 12);
  line(ctx, walls, accent, 7);
  line(ctx, [[x - half - 5, top], [x - half + 9, top]], accent, 6);
  line(ctx, [[x + half - 9, top], [x + half + 5, top]], accent, 6);
}

function drawPlank(ctx, plank, slope, neutral) {
  const {x, y, half_width: half} = plank;
  const pedestal = [[x - 34, 467], [x, y + 1], [x + 34, 467]];
  ctx.beginPath();
  ctx.moveTo(...pedestal[0]);
  ctx.lineTo(...pedestal[1]);
  ctx.lineTo(...pedestal[2]);
  ctx.closePath();
  ctx.fillStyle = "#243648";
  ctx.fill();
  line(ctx, pedestal, neutral ? "#526775" : "#546482", 3);
  box(ctx, x - 43, 467, 86, 7, 3, neutral ? "#526775" : "#4a5375");
  const ends = [[x - half, y - half * slope], [x + half, y + half * slope]];
  const hardware = neutral ? "#263644" : "#29223e";
  const fastener = neutral ? "#d5e3eb" : "#d7bcff";
  line(ctx, ends, hardware, 17);
  line(ctx, ends, neutral ? "#a6bac7" : "#baa0ed", 9);
  for (const [ex, ey] of ends) disc(ctx, ex, ey, 3, fastener);
  disc(ctx, x, y, 10, hardware, fastener, 2);
  disc(ctx, x, y, 3, fastener);
  if (!neutral) label(ctx, "TEETER-TOTTER", x, 488, {color: "#cdb7ed", size: 13});
}

function drawGripper(ctx, rail, {x, y, jaws}) {
  const base = 210;
  box(ctx, rail.left - 25, base - 12, rail.right - rail.left + 50, 19, 5, "#344e60", "#6b889a");
  line(ctx, [[rail.left - 16, base - 3], [rail.right + 16, base - 3]], "#162b39", 4);
  disc(ctx, rail.left - 17, base - 3, 3, steel);
  disc(ctx, rail.right + 17, base - 3, 3, steel);
  line(ctx, [[x, base], [x, y]], "#233846", 12);
  line(ctx, [[x, base], [x, y]], "#b3c9d5", 5);
  box(ctx, x - 17, base - 14, 34, 23, 5, "#567788", "#b9d2de");
  box(ctx, x - 12, y - 7, 24, 14, 5, "#5f7b90", "#aec8d6");
  const spread = 21 - jaws * 10;
  for (const side of [-1, 1]) {
    const jaw = [[x + side * 7, y], [x + side * spread, y + 13],
      [x + side * (spread - 3), y + 25]];
    line(ctx, jaw, "#15212b", 8);
    line(ctx, jaw, "#c1d6e3", 4);
  }
}

function drawGoalLid(ctx, goal, openness, open, neutral) {
  const hinge = goal.x + goal.half_width;
  const angle = -openness * Math.PI / 2;
  const length = 2 * goal.half_width;
  const endpoint = [hinge - length * Math.cos(angle), goal.top + length * Math.sin(angle)];
  line(ctx, [[hinge, goal.top], endpoint], "#0b171f", 13);
  line(ctx, [[hinge, goal.top], endpoint], neutral ? "#a6bac7" : open ? "#5af1d5" : "#7c9b9f", 7);
  disc(ctx, hinge, goal.top, 7, neutral ? "#263644" : "#143b3c",
    neutral ? "#d5e3eb" : "#65ddc7", 2);
}

export function drawWorld(canvas, world, options) {
  const ctx = canvas.getContext("2d");
  const layout = world.mechanism_layout;
  const {source, plank, rail, goal, floor} = layout;
  const {from, to = world, progress} = options.tick ?? {from: world, progress: 1};
  const controls = {...world.applied, ...world.pending};
  const neutral = world.version >= 2;
  const background = ctx.createLinearGradient(0, 170, 0, floor);
  background.addColorStop(0, "#10202d");
  background.addColorStop(1, "#131f2b");
  ctx.fillStyle = "#0b1722";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  box(ctx, 18, 177, 724, floor - 177, 10, background, "#263b4b");
  for (let x = 32; x < 742; x += 16) {
    for (let y = 189; y < floor; y += 16) disc(ctx, x, y, 0.7, "#324454");
  }
  line(ctx, [[22, floor], [738, floor]], "#546b7b", 2);
  for (let x = 33; x < 740; x += 14) {
    line(ctx, [[x, floor + 3], [x - 5, floor + 10]], "#324655", 2);
  }

  if (!neutral) drawWiring(ctx, world, controls, layout);
  drawCorridor(ctx, world, controls, neutral);
  const speaker = drawPlayers(ctx, world, options);
  drawBucket(ctx, source, 0, {supply: true, neutral});
  drawPlank(ctx, plank, mix(from.plank_slope, to.plank_slope, progress), neutral);
  drawBucket(ctx, goal, world.goals, {neutral});

  const open = world.lid.open;
  drawGoalLid(ctx, goal, mix(Number(from.lid.open), Number(to.lid.open), progress), open, neutral);
  if (!neutral) {
    label(ctx, "BALL SUPPLY", source.x, 451, {color: "#e2bd9d", size: 13});
    label(ctx, "GRIPPER", 45, 208, {color: muted, align: "left", size: 12});
    label(ctx, "GOAL BUCKET", goal.x, 505, {color: "#8ce7d3", size: 12});
    box(ctx, 656, 342, 72, 64, 8, "#102d30", open ? "#57eacf" : "#3d6e6d");
    label(ctx, `${world.goals} / ${world.target}`, 692, 360, {size: 18});
    label(ctx, open ? "OPEN" : "SHUT", 692, 383, {color: open ? "#69ffdb" : "#abc8cc", size: 13});
    if (open) label(ctx, `${world.lid.closes_at_round - world.round} left`, 692, 397, {color: muted, size: 9});
    else if (!world.lid.armed) label(ctx, "RELEASE", 692, 397, {color: muted, size: 9});
  }

  const gripper = {};
  for (const field of ["x", "y", "jaws"]) {
    gripper[field] = mix(from.gripper[field], to.gripper[field], progress);
  }
  drawGripper(ctx, rail, gripper);
  for (const ballAtFrame of world.balls) {
    const previous = from.balls.find(ball => ball.id === ballAtFrame.id) ?? ballAtFrame;
    const next = to.balls.find(ball => ball.id === ballAtFrame.id) ?? ballAtFrame;
    ball(ctx, mix(previous.x, next.x, progress), mix(previous.y, next.y, progress), 10, neutral);
  }
  if (speaker) drawBubble(ctx, ...speaker, options.bubble, options.color(options.bubble.agent));
}

export function describeWorld(world) {
  const balls = world.balls.map(ball => phases[ball.phase]).join(", ");
  const tilt = world.plank_tilt < 0.38 ? "left" : world.plank_tilt > 0.62 ? "right" : "level";
  return `Round ${world.round}. ${world.goals} of ${world.target} goals. Plank ${tilt}. `
    + `Goal lid ${world.lid.open ? "open" : "shut"}.` + (balls ? " Ball " + balls + "." : "");
}

export function describeAction({kind, name, result, arguments: args}) {
  if (kind !== "tool") return;
  if (name === "look") return {title: "Surveyed the playfield", short: "Look"};
  if (["moveX", "moveY"].includes(name) && result.message) {
    const axis = name === "moveX" ? ["←", "→"] : ["↑", "↓"];
    return {title: result.message, short: result.message === "Moved."
      ? axis[Number(args.steps > 0)] + " " + Math.abs(args.steps) : "Wall"};
  }
  if (name === "actuate") {
    const {station, amount, message} = result;
    if (message === "Actuated.") return {title: "Actuated at " + args.amount, short: "Actuate " + args.amount};
    if (amount == null) return {title: message, short: station ?? "Actuate"};
    return {title: `${station} at ${amount}`, short: `${station} ${amount}`, detail: message};
  }
}

export function playbackDelay(event) {
  if (event.kind === "tick") return event.messages.length ? 440 : 165;
  if (event.kind === "tool" && ["moveX", "moveY", "actuate"].includes(event.name)) return 155;
  return 320;
}

