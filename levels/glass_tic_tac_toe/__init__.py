import random
from functools import lru_cache

from dojo.level import tool

TITLE = "Glass Tic-Tac-Toe"
VERSION = 6
GOAL = "Solve the puzzle."
DEFAULT_AGENTS = 4
MAX_CALLS_PER_TURN = 1

SIZE = 29
RADIUS = 6
MIN_BOARD, MAX_BOARD = 6, 22
LINES = (11, 17)
CENTERS = (8, 14, 20)
SIGN = "X must win. Leave a position that could occur in a game."
SIGN_Y = 26
SIGN_COLUMNS = range(9, 20)
SIGN_CELLS = {
    (x, SIGN_Y): SIGN[i * len(SIGN) // len(SIGN_COLUMNS):(i + 1) * len(SIGN) // len(SIGN_COLUMNS)]
    for i, x in enumerate(SIGN_COLUMNS)
}
STRIDE = 3
STEPS = {"type": "integer", "minimum": -STRIDE, "maximum": STRIDE,
         "enum": [step for step in range(-STRIDE, STRIDE + 1) if step]}
TOOLS = (
    tool("moveX", "Move one, two or three spaces horizontally. Positive is right. "
         "Returns the actual displacement. Call one tool at a time.",
         steps=STEPS),
    tool("moveY", "Move one, two or three spaces vertically. Positive is down. "
         "Returns the actual displacement. Call one tool at a time.",
         steps=STEPS),
    tool("look", "Read a circular grid centred under you. Row and column offsets are relative "
         "to your current spot, with positive offsets down and right. A space is outside your "
         "view, # is beyond the floor, and . is bare floor."),
    tool("lift", "Pick up the loose object directly under you. You can carry one object."),
    tool("drop", "Put the object you are carrying directly under you."),
)

WIN_LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6),
    (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6),
)


def wins(board, mark):
    return any(all(board[index] == mark for index in line) for line in WIN_LINES)


@lru_cache(maxsize=1)
def winning_positions():
    """Enumerate X wins from legal play, stopping each game at its first win."""
    visited = set()
    winning = set()

    def visit(board, mark):
        if board in visited:
            return
        visited.add(board)
        if wins(board, "X"):
            winning.add(board)
            return
        if wins(board, "O"):
            return
        for index, piece in enumerate(board):
            if piece == ".":
                visit(board[:index] + mark + board[index + 1:], "O" if mark == "X" else "X")

    visit("." * 9, "X")
    return winning


@lru_cache(maxsize=512)
def score_board(board):
    distance = min(sum(a != b for a, b in zip(board, target))
                   for target in winning_positions())
    return distance == 0, round(max(0, 1 - distance / 5), 2)


def inside(x, y):
    return 0 <= x < SIZE and 0 <= y < SIZE


def painted(x, y):
    return (MIN_BOARD <= x <= MAX_BOARD and MIN_BOARD <= y <= MAX_BOARD
            and (x in LINES or y in LINES))


def cell(x, y):
    if MIN_BOARD <= x <= MAX_BOARD and MIN_BOARD <= y <= MAX_BOARD and not painted(x, y):
        return 3 * sum(y > line for line in LINES) + sum(x > line for line in LINES)
    return None


class Puzzle:
    tools = TOOLS

    def __init__(self, seed, agents):
        rng = random.Random(seed)
        floor = [(x, y) for y in range(SIZE) for x in range(SIZE)]
        rim = [(x, y) for x, y in floor if x in (2, 26) or y in (2, 24)]
        self.pieces = dict(zip(rng.sample(rim, 9), "XXXXXOOOO"))
        self.positions = dict(zip(agents, rng.sample(floor, len(agents))))
        self.hands = dict.fromkeys(agents)

    def act(self, agent, name, arguments):
        x, y = position = self.positions[agent]
        if name in ("moveX", "moveY"):
            axis = 0 if name == "moveX" else 1
            before = position[axis]
            requested = arguments["steps"]
            if type(requested) is not int or requested not in STEPS["enum"]:
                return {"error": "Move one, two or three spaces."}
            after = max(0, min(SIZE - 1, before + requested))
            self.positions[agent] = (after, y) if axis == 0 else (x, after)
            return {"moved": after - before}
        if name == "look":
            rows = []
            visible_text = []
            for dy in range(-RADIUS, RADIUS + 1):
                row = []
                for dx in range(-RADIUS, RADIUS + 1):
                    point = (x + dx, y + dy)
                    if dx * dx + dy * dy > RADIUS * RADIUS:
                        row.append(" ")
                    elif not inside(*point):
                        row.append("#")
                    elif point in SIGN_CELLS:
                        row.append("!")
                        visible_text.append(SIGN_CELLS[point])
                    else:
                        row.append(self.pieces.get(point, "+" if painted(*point) else "."))
                rows.append("".join(row))
            visible_agents = [
                {"id": other, "dx": other_x - x, "dy": other_y - y}
                for other, (other_x, other_y) in self.positions.items()
                if other != agent and (other_x - x) ** 2 + (other_y - y) ** 2 <= RADIUS ** 2
            ]
            view = {"grid": rows, "carrying": self.hands[agent], "agents": visible_agents}
            if visible_text:
                view["inscriptions"] = ["".join(visible_text)]
            return view
        if name == "lift":
            if self.hands[agent] is not None:
                return {"error": "You are already carrying something."}
            if position not in self.pieces:
                return {"error": "There is nothing loose directly under you."}
            self.hands[agent] = self.pieces.pop(position)
            return {"lifted": self.hands[agent]}
        if name == "drop":
            mark = self.hands[agent]
            if mark is None:
                return {"error": "You are not carrying anything."}
            if position in self.pieces or position in SIGN_CELLS or painted(*position):
                return {"error": "This spot is occupied or marked."}
            self.pieces[position] = mark
            self.hands[agent] = None
            return {"dropped": mark}
        return {"error": "Unknown action."}

    def evaluate(self):
        cells = [[] for _ in range(9)]
        for position, mark in self.pieces.items():
            index = cell(*position)
            if index is not None:
                cells[index].append(mark)
        board = "".join("." if not marks else marks[0] if len(marks) == 1 else "?"
                        for marks in cells)
        return score_board(board)

    def snapshot(self):
        return {
            "size": SIZE,
            "radius": RADIUS,
            "board": [MIN_BOARD, MAX_BOARD],
            "lines": LINES,
            "sign": SIGN,
            "sign_y": SIGN_Y,
            "pieces": [{"x": x, "y": y, "mark": mark}
                       for (x, y), mark in self.pieces.items()],
            "players": [{"id": agent, "x": x, "y": y, "carrying": self.hands[agent]}
                        for agent, (x, y) in self.positions.items()],
        }


def demo(puzzle):
    """Script scouts who report observations before the team gets a plan."""
    ids = list(puzzle.positions)
    plans = {agent: [("whoAmI", {}), ("look", {})] for agent in ids}
    positions = dict(puzzle.positions)
    board_scout, sign_scout, x_scout, o_scout = (ids[i % len(ids)] for i in range(4))
    supply = {mark: [point for point, piece in puzzle.pieces.items() if piece == mark]
              for mark in "XO"}

    def add(agent, name, **arguments):
        plans[agent].append((name, arguments))

    def report(agent, message):
        if len(ids) > 1:
            add(agent, "messageAllOtherAgents", message=message)

    def wait_for_team():
        round_count = max(map(len, plans.values()))
        for plan in plans.values():
            plan.extend([None] * (round_count - len(plan)))

    def distance(start, end):
        return sum((abs(a - b) + STRIDE - 1) // STRIDE for a, b in zip(start, end))

    def go(agent, target, scout=False):
        total = distance(positions[agent], target)
        moves = 0
        for name, start, end in zip(("moveX", "moveY"), positions[agent], target):
            remaining = end - start
            while remaining:
                steps = max(-STRIDE, min(STRIDE, remaining))
                add(agent, name, steps=steps)
                remaining -= steps
                moves += 1
                if scout and moves % 3 == 0 and moves < total:
                    add(agent, "look")
        positions[agent] = target

    go(board_scout, (11, 11), scout=True)
    add(board_scout, "look")
    report(board_scout, "Two painted lines cross here. I'll move a little and see what connects to them.")
    go(board_scout, (14, 14))
    add(board_scout, "look")
    report(board_scout, "I can see two lines in each direction. It looks like a three-by-three grid.")

    go(sign_scout, (14, 24), scout=True)
    add(sign_scout, "look")
    report(sign_scout, f"I found an inscription down here. It says, “{SIGN}”")

    for agent, mark in ((x_scout, "X"), (o_scout, "O")):
        source = min(supply[mark], key=lambda point: distance(positions[agent], point))
        # Take a reading from alongside the loose mark.
        x, y = source
        go(agent, (x - 2 if x >= 2 else x + 2, y), scout=True)
        add(agent, "look")
        report(agent, f"There's a loose {mark} a couple of spaces from me. I'll remember where it is.")

    # Assign the five deliveries by estimated finish time from the scouts' new positions.
    targets = [
        ("X", (8, 8), "top left"), ("O", (8, 14), "middle left"),
        ("X", (14, 8), "top centre"), ("O", (14, 14), "centre"), ("X", (20, 8), "top right"),
    ]
    jobs = {agent: [] for agent in ids}
    locations = dict(positions)
    work = dict.fromkeys(ids, 0)
    for mark, target, square in targets:
        turns, agent, source = min(
            (work[agent] + distance(locations[agent], point) + distance(point, target) + 4,
             agent, point)
            for agent in ids for point in supply[mark]
        )
        supply[mark].remove(source)
        jobs[agent].append((mark, source, target, square))
        locations[agent] = target
        work[agent] = turns

    wait_for_team()
    if len(ids) > 1:
        assignments = "; ".join(
            f"{agent} takes " + " and ".join(f"{mark} to {square}" for mark, _, _, square in deliveries)
            for agent, deliveries in jobs.items() if deliveries
        )
        report(board_scout, "A three-by-three grid, loose Xs and Os, and a sign saying X must win. "
               "Sounds like tic-tac-toe. Let's put three Xs across the top and two Os below. "
               + assignments + ".")
        wait_for_team()

    for agent, deliveries in jobs.items():
        for mark, source, target, square in deliveries:
            go(agent, source)
            add(agent, "look")
            add(agent, "lift")
            go(agent, target)
            add(agent, "look")
            add(agent, "drop")
            report(agent, f"My {mark} is down at {square}.")
    return plans
