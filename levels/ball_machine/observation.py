"""Render the field as pixels, using the visible parts of the simulation."""
from math import cos, isqrt, pi, sin
from struct import pack
from zlib import compress, crc32

SCALE = 2
BACKGROUND, CHAMBER, CORRIDOR, GRID, EDGE, METAL, LIGHT, WHITE = (
    22, 35, 51, 71, 105, 168, 222, 255,
)
DIGITS = (
    ("010", "110", "010", "010", "111"),
    ("111", "001", "111", "100", "111"),
    ("111", "001", "111", "001", "111"),
    ("101", "101", "111", "001", "001"),
    ("111", "100", "111", "001", "111"),
    ("111", "100", "111", "101", "111"),
    ("111", "001", "010", "010", "010"),
    ("111", "101", "111", "101", "111"),
)


class Bitmap:
    def __init__(self, size):
        self.width, self.height = (dimension // SCALE for dimension in size)
        self.pixels = bytearray([BACKGROUND]) * (self.width * self.height)

    def span(self, y, left, right, shade):
        left, right = max(0, left), min(self.width, right)
        if 0 <= y < self.height and left < right:
            offset = y * self.width
            self.pixels[offset + left:offset + right] = bytes([shade]) * (right - left)

    def rectangle(self, left, top, right, bottom, shade):
        left, top, right, bottom = (round(value / SCALE) for value in (left, top, right, bottom))
        for y in range(top, bottom):
            self.span(y, left, right, shade)

    def circle(self, x, y, radius, shade):
        x, y, radius = (round(value / SCALE) for value in (x, y, radius))
        for dy in range(-radius, radius + 1):
            half = isqrt(radius * radius - dy * dy)
            self.span(y + dy, x - half, x + half + 1, shade)

    def line(self, start, end, shade, width):
        x, y, end_x, end_y = (round(value / SCALE) for value in (*start, *end))
        steps = max(abs(end_x - x), abs(end_y - y), 1)
        half = (width // SCALE) // 2
        for step in range(steps + 1):
            px = round(x + (end_x - x) * step / steps)
            py = round(y + (end_y - y) * step / steps)
            for row in range(py - half, py + half + 1):
                self.span(row, px - half, px + half + 1, shade)

    def path(self, points, shade, width=2):
        for start, end in zip(points, points[1:]):
            self.line(start, end, shade, width)

    def png(self):
        def chunk(name, data):
            return pack(">I", len(data)) + name + data + pack(">I", crc32(name + data))

        rows = (b"\x00" + self.pixels[start:start + self.width]
                for start in range(0, len(self.pixels), self.width))
        header = pack(">IIBBBBB", self.width, self.height, 8, 0, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                + chunk(b"IDAT", compress(b"".join(rows))) + chunk(b"IEND", b""))


def point(x, y):
    return 44 + 48 * x, 41 + 40 * y


def draw_corridor(image, cells, columns, rows):
    image.rectangle(18, 18, 742, 143, EDGE)
    image.rectangle(20, 20, 740, 141, CORRIDOR)
    for y in range(rows):
        image.path((point(0, y), point(columns - 1, y)), GRID)
    for x in range(columns):
        image.path((point(x, 0), point(x, rows - 1)), GRID)
        for y in range(rows):
            image.circle(*point(x, y), 2, EDGE)
    for cell in cells:
        x, y = point(*cell)
        image.rectangle(x - 18, y - 16, x + 18, y + 16, EDGE)
        image.rectangle(x - 14, y - 12, x + 14, y + 12, CORRIDOR)


def draw_agents(image, positions, observer):
    groups = {}
    for agent, cell in positions.items():
        groups.setdefault(cell, []).append(agent)
    markers = []
    for cell, group in groups.items():
        x, y = point(*cell)
        shared = len(group) > 1
        spread = 14 / sin(pi / len(group)) if shared else 0
        for index, agent in enumerate(group):
            angle = index * 2 * pi / len(group) - pi / 2
            px, py = x, y
            if shared:
                px = min(725 - spread, max(35 + spread, x)) + spread * cos(angle)
                py = min(126 - spread, max(35 + spread, y)) + spread * sin(angle)
                image.path(((x, y), (px, py)), EDGE)
            markers.append((agent, px, py))
    for agent, x, y in markers:
        if agent == observer:
            image.rectangle(x - 13, y - 15, x + 13, y + 15, WHITE)
        image.rectangle(x - 10, y - 12, x + 10, y + 12, BACKGROUND)
        for row, digits in enumerate(DIGITS[int(agent) - 1]):
            for column, pixel in enumerate(digits):
                if pixel == "1":
                    left, top = x - 6 + 4 * column, y - 10 + 4 * row
                    image.rectangle(left, top, left + 4, top + 4, LIGHT)


def draw_bucket(image, shape):
    x, half = shape["x"], shape["half_width"]
    top, bottom = shape["top"], shape["bottom"]
    image.path(((x - half, top), (x - half + 11, bottom),
                (x + half - 11, bottom), (x + half, top)), METAL, 7)
    for side in (-1, 1):
        rim = x + side * half
        image.path(((rim - 5, top), (rim + 5, top)), LIGHT, 5)


def draw_ball(image, x, y):
    image.circle(x, y, 10, LIGHT)
    image.circle(x - 3, y - 3, 3, WHITE)


def render_observation(puzzle, observer, station_cells, columns, rows):
    layout = puzzle.mechanism_layout()
    image = Bitmap(layout["size"])
    draw_corridor(image, station_cells, columns, rows)
    draw_agents(image, puzzle.positions, observer)
    floor = layout["floor"]
    image.rectangle(18, 169, 742, floor, CHAMBER)
    image.path(((18, 169), (742, 169)), EDGE)
    image.path(((18, floor), (742, floor)), EDGE)

    source, plank, goal = (layout[key] for key in ("source", "plank", "goal"))
    draw_bucket(image, source)
    for dx, dy in ((-31, -13), (-10, -13), (12, -13), (32, -13),
                   (-23, -32), (-2, -32), (20, -32), (10, -50)):
        draw_ball(image, source["x"] + dx, source["bottom"] + dy)
    draw_bucket(image, goal)
    for index in range(puzzle.goals):
        draw_ball(image, goal["x"] + 29 - index * 22, goal["bottom"] - 13)
    hinge = goal["x"] + goal["half_width"]
    top = goal["top"]
    length = 2 * goal["half_width"]
    lid_end = (hinge, top - length) if puzzle.lid_closes_at is not None else (hinge - length, top)
    image.path(((hinge, top), lid_end), METAL, 7)
    image.circle(hinge, top, 6, LIGHT)

    x, y, half = plank["x"], plank["y"], plank["half_width"]
    rise = half * puzzle.slope
    image.path(((x - 35, 469), (x, y), (x + 35, 469), (x - 35, 469)), EDGE, 5)
    image.path(((x - half, y - rise), (x + half, y + rise)), METAL, 8)
    image.circle(x, y, 7, LIGHT)

    rail = layout["rail"]
    x, y = puzzle.grip_x, puzzle.grip_y
    image.path(((rail["left"] - 25, 207), (rail["right"] + 25, 207)), EDGE, 14)
    image.path(((x, 209), (x, y)), METAL, 6)
    image.rectangle(x - 16, 197, x + 16, 218, METAL)
    image.rectangle(x - 12, y - 6, x + 12, y + 7, METAL)
    spread = 21 - puzzle.jaws * 10
    for side in (-1, 1):
        image.path(((x + side * 7, y), (x + side * spread, y + 13),
                    (x + side * (spread - 3), y + 25)), LIGHT, 4)
    for ball in puzzle.balls:
        draw_ball(image, ball.x, ball.y)
    return image.png()
