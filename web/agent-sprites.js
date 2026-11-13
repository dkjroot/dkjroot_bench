const ink = "#07111d";

export const spriteNames = {
  rocket: "Rocket", rover: "Rover", diamond: "Diamond", dome: "Dome",
  walker: "Walker", glider: "Glider", shield: "Shield", "twin-pod": "Twin pod",
};
export const agentColors = ["#19daff", "#ffc02b", "#f875ee", "#adfa45", "#ff7355", "#91a7ff", "#fafaff", "#24e3ab"];
const spriteIds = Object.keys(spriteNames);
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

export function drawSprite(ctx, id, x, y, color,
    {active = false, stride = 0, scale = 1, character, pulse = 0} = {}) {
  const index = Number(id) - 1;
  const badge = character?.badge ?? id;
  const sprite = silhouettes[spriteIds.indexOf(character?.sprite)] ?? silhouettes[index];
  ctx.save();
  ctx.translate(Math.round(x), Math.round(y));
  ctx.scale(scale, scale);
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

  const hop = Math.sin(stride * Math.PI) * 2 + Math.sin(pulse * Math.PI) * 4;
  ctx.translate(0, -Math.round(hop));
  ctx.lineJoin = "round";
  ctx.strokeStyle = ink;
  ctx.lineWidth = 4;
  ctx.stroke(sprite);
  ctx.fillStyle = color;
  ctx.fill(sprite);
  ctx.fillStyle = ink;
  ctx.fillRect(-7, -11, 4, 3);
  ctx.fillRect(3, -11, 4, 3);
  ctx.fillRect(-8, -5, 16, 20);
  ctx.fillStyle = "#fff";
  const pixels = numbers[Number(badge) - 1];
  if (pixels) {
    pixels.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        if (row[x] === "1") ctx.fillRect(-4 + x * 3, -2 + y * 3, 3, 3);
      }
    });
  } else {
    ctx.font = "bold 16px monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(badge, 0, 6, 13);
  }
  ctx.restore();
}

export function drawAgent(canvas, id, color, character) {
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawSprite(ctx, id, canvas.width / 2, canvas.height / 2, color, {character});
}

export function drawBubble(ctx, x, y, bubble, color) {
  if (typeof bubble.text !== "string" || !bubble.text.trim()) return;
  const characters = Array.from(bubble.text.trim().replace(/\s+/g, " "));
  const text = characters.slice(0, 96).join("") + (characters.length > 96 ? "…" : "");
  ctx.save();
  ctx.font = "20px system-ui, sans-serif";
  const lines = [];
  let line = "";
  for (const word of text.split(" ")) {
    const next = line ? line + " " + word : word;
    if (line && ctx.measureText(next).width > 290) {
      lines.push(line);
      line = word;
    } else {
      line = next;
    }
  }
  lines.push(line);
  if (lines.length > 3) lines.splice(2, lines.length - 2, lines[2] + "…");
  const width = Math.min(314, Math.max(bubble.thinking ? 130 : 42, ...lines.map(part => ctx.measureText(part).width + 24)));
  const height = lines.length * 23 + (bubble.thinking ? 36 : 18);
  const left = Math.max(7, Math.min(ctx.canvas.width - width - 7, x - width / 2));
  const above = y - height - 12 >= 6;
  const top = above ? y - height - 12 : Math.min(ctx.canvas.height - height - 6, y + 36);
  const tail = Math.max(left + 14, Math.min(left + width - 14, x));
  const edge = above ? top + height : top;
  ctx.fillStyle = "#101f2e";
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.roundRect(left, top, width, height, 10);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(tail - 7, edge);
  ctx.lineTo(x, above ? y : top - 11);
  ctx.lineTo(tail + 7, edge);
  ctx.fill();
  ctx.stroke();
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  if (bubble.thinking) {
    ctx.fillStyle = color;
    ctx.font = "bold 14px system-ui, sans-serif";
    ctx.fillText("Agent thinking", left + 12, top + 14);
    ctx.font = "20px system-ui, sans-serif";
  }
  ctx.fillStyle = "#f2f7fb";
  lines.forEach((part, index) => ctx.fillText(part, left + 12, top + (bubble.thinking ? 36 : 20) + 23 * index, width - 24));
  ctx.restore();
}
