from dataclasses import dataclass

from dojo.level import ImageObservation, tool

from .demo import demo
from .observation import render_observation

TITLE = "Ball Machine"
VERSION = 3
GOAL = "Get 3 balls into the bin before the time runs out."
DEFAULT_AGENTS = 5
MIN_AGENTS = 2
DEFAULT_MODEL_ROUNDS = 400
MAX_CALLS_PER_TURN = 1

COLUMNS, ROWS = 15, 3
START = (0, 0)
STRIDE = 3
STEPS = {"type": "integer", "minimum": -STRIDE, "maximum": STRIDE,
         "enum": [-3, -2, -1, 1, 2, 3]}
TOOLS = (
    tool("moveX", "Move horizontally. Positive is right.", steps=STEPS),
    tool("moveY", "Move vertically. Positive is down.", steps=STEPS),
    tool("look", "Look around."),
    tool("actuate", "Actuate with the given amount.",
         amount={"type": "number", "minimum": 0, "maximum": 1}),
)

STATIONS = {
    "hoist": (2, 2),
    "jaws": (4, 2),
    "slide": (7, 2),
    "tilt": (10, 2),
    "lid": (13, 2),
}
STATION_AT = {cell: key for key, cell in STATIONS.items()}

FIELD = (760, 520)
SOURCE_X, SOURCE_HALF, SOURCE_TOP, SOURCE_BOTTOM = 145, 52, 350, 430
RAIL_END = 372
GRIP_TOP, GRIP_TRAVEL = 235, 124
PLANK_X, PLANK_Y, PLANK_HALF = 393, 359, 92
GOAL_X, GOAL_HALF, GOAL_TOP, GOAL_BOTTOM = 577, 62, 437, 489
FLOOR = 500
RADIUS = 10
TARGET = 3
GRAVITY = 10
MAX_SLOPE = 0.42
LANDING_TOLERANCE = 0.12
ROLLING_RESISTANCE = 0.25 * MAX_SLOPE
LID_DURATION = 2


@dataclass
class Ball:
    id: int
    x: float
    y: float
    phase: str = "held"
    vx: float = 0.0
    vy: float = 0.0

    def visible(self):
        return {"id": self.id, "x": self.x, "y": self.y, "phase": self.phase}


def approach(current, target, step):
    return current + max(-step, min(step, target - current))


class Puzzle:
    tools = TOOLS

    def __init__(self, seed, agents):
        self.positions = dict.fromkeys(agents, START)
        self.round = 0
        self.goals = 0
        self.depth = 0.0
        self.jaws = 1.0
        self.slide = self.slide_target = 0.0
        self.tilt = 0.0
        self.lid_closes_at = None
        self.lid_armed = True
        self.pending = {}
        self.balls = []
        self.held = None
        self.ball_id = 0

    @property
    def grip_x(self):
        return SOURCE_X + (RAIL_END - SOURCE_X) * self.slide

    @property
    def grip_y(self):
        return GRIP_TOP + GRIP_TRAVEL * self.depth

    @property
    def slope(self):
        return MAX_SLOPE * (2 * self.tilt - 1)

    def plank_height(self, x):
        return PLANK_Y + (x - PLANK_X) * self.slope - RADIUS

    def act(self, agent, name, arguments):
        x, y = self.positions[agent]
        if name in ("moveX", "moveY"):
            steps = arguments["steps"]
            if name == "moveX":
                self.positions[agent] = (max(0, min(COLUMNS - 1, x + steps)), y)
                moved = self.positions[agent][0] - x
            else:
                self.positions[agent] = (x, max(0, min(ROWS - 1, y + steps)))
                moved = self.positions[agent][1] - y
            return {"message": "Moved." if moved else "Blocked."}
        if name == "look":
            return ImageObservation.png(render_observation(self, agent, STATIONS.values(), COLUMNS, ROWS))
        if name == "actuate":
            station = STATION_AT.get((x, y))
            if station is None:
                return {"message": "This does nothing here."}
            if station in self.pending:
                return {"message": "Already actuated."}
            self.pending[station] = arguments["amount"]
            return {"message": "Actuated."}
        return {"error": "Unknown action."}

    def advance_round(self):
        self.round += 1
        messages = []
        controls, self.pending = self.pending, {}
        self.advance_lid(controls.get("lid", 0), messages)
        self.depth = approach(self.depth, controls.get("hoist", 0), 0.5)
        self.slide_target = controls.get("slide", self.slide_target)
        if self.depth <= 0.45:
            self.slide = approach(self.slide, self.slide_target, 0.5)
        self.tilt = controls.get("tilt", 0)
        self.advance_gripper(controls.get("jaws", self.jaws), messages)
        self.balls = [ball for ball in self.balls if self.advance_ball(ball, messages)]
        return messages

    def advance_lid(self, amount, messages):
        if self.lid_closes_at == self.round:
            self.lid_closes_at = None
            count = sum(ball.phase == "in_bucket" for ball in self.balls)
            self.balls = [ball for ball in self.balls if ball.phase != "in_bucket"]
            if count:
                self.goals = min(TARGET, self.goals + count)
                messages.append(f"The lid closed. Goal {self.goals} of {TARGET}.")
            else:
                messages.append("The lid closed.")
        if self.lid_closes_at is None:
            if amount < 0.5:
                self.lid_armed = True
            elif self.lid_armed:
                self.lid_armed = False
                self.lid_closes_at = self.round + LID_DURATION
                messages.append("The goal lid opened.")

    def advance_gripper(self, jaws, messages):
        was_closed = self.jaws >= 0.5
        closed = jaws >= 0.5
        self.jaws = jaws
        if self.held is not None:
            self.held.x, self.held.y = self.grip_x, self.grip_y + 16
            if not closed:
                self.held.phase = "falling"
                self.held = None
                messages.append("The gripper released a ball.")
        elif closed and not was_closed and self.depth >= 0.9 and self.slide <= 0.04:
            self.ball_id += 1
            self.held = Ball(self.ball_id, self.grip_x, self.grip_y + 16)
            self.balls.append(self.held)
            messages.append("The gripper picked up a ball.")

    def advance_ball(self, ball, messages):
        if ball.phase in ("held", "in_bucket"):
            return True
        if ball.phase in ("rolling", "skidding"):
            self.roll_ball(ball, messages)
            return True

        old_x, old_y = ball.x, ball.y
        ball.vy += GRAVITY
        ball.x += ball.vx
        ball.y += ball.vy
        if ball.phase == "falling":
            if (abs(ball.x - SOURCE_X) < SOURCE_HALF - RADIUS
                    and ball.y >= SOURCE_BOTTOM - RADIUS):
                messages.append("The ball fell back into the supply.")
                return False
            height = self.plank_height(ball.x)
            if abs(ball.x - PLANK_X) <= PLANK_HALF and old_y <= height <= ball.y:
                ball.y = height
                if abs(self.tilt - 0.5) <= LANDING_TOLERANCE:
                    ball.phase = "rolling"
                    messages.append("The ball settled on the plank.")
                else:
                    ball.phase = "skidding"
                    ball.vx = 50 if self.slope > 0 else -50
                    messages.append("The ball landed on a steep plank and skidded.")
        elif ball.phase == "to_goal" and old_y < GOAL_TOP - RADIUS <= ball.y:
            at_lid = (GOAL_TOP - RADIUS - old_y) / (ball.y - old_y)
            x = old_x + (ball.x - old_x) * at_lid
            if abs(x - GOAL_X) <= GOAL_HALF - RADIUS:
                ball.x = x
                if self.lid_closes_at is not None:
                    ball.phase = "in_bucket"
                    ball.y = GOAL_BOTTOM - RADIUS - 4
                    messages.append("The ball landed inside the open bucket.")
                else:
                    ball.phase = "spill"
                    ball.y = GOAL_TOP - RADIUS
                    ball.vx, ball.vy = max(18, ball.vx * 0.8), -19
                    messages.append("The ball bounced off the closed lid.")
        if ball.y >= FLOOR - RADIUS:
            messages.append("A ball fell onto the floor.")
            return False
        return True

    def roll_ball(self, ball, messages):
        if ball.phase == "rolling":
            drive = approach(self.slope, 0, ROLLING_RESISTANCE)
            ball.vx = max(-42, min(42, (ball.vx + drive * 48) * 0.86))
        ball.x += ball.vx
        left, right = PLANK_X - PLANK_HALF, PLANK_X + PLANK_HALF
        if left <= ball.x <= right:
            ball.y = self.plank_height(ball.x)
            return
        towards_goal = ball.x > right and ball.phase == "rolling"
        ball.x = right if ball.x > right else left
        ball.y = self.plank_height(ball.x)
        ball.vy = ball.vx * self.slope
        ball.phase = "to_goal" if towards_goal else "spill"
        messages.append("The ball rolled towards the goal bucket." if towards_goal
                        else "The ball rolled off the plank.")

    def evaluate(self):
        return self.goals >= TARGET, self.goals / TARGET

    def snapshot(self):
        return {
            "version": VERSION,
            "corridor": {"columns": COLUMNS, "rows": ROWS},
            "stations": [{"x": x, "y": y} for x, y in STATIONS.values()],
            "mechanism_layout": self.mechanism_layout(),
            "round": self.round,
            "goals": self.goals,
            "target": TARGET,
            "players": [{"id": agent, "x": x, "y": y}
                        for agent, (x, y) in self.positions.items()],
            "gripper": {"x": self.grip_x, "y": self.grip_y, "depth": self.depth,
                        "jaws": self.jaws, "slide": self.slide, "slide_target": self.slide_target,
                        "holding_ball": self.held is not None},
            "plank_tilt": self.tilt,
            "plank_slope": self.slope,
            "lid": {"open": self.lid_closes_at is not None, "armed": self.lid_armed,
                    "closes_at_round": self.lid_closes_at},
            "balls": [ball.visible() for ball in self.balls],
        }

    @staticmethod
    def mechanism_layout():
        return {
            "size": FIELD, "floor": FLOOR,
            "source": {"x": SOURCE_X, "top": SOURCE_TOP, "bottom": SOURCE_BOTTOM,
                       "half_width": SOURCE_HALF},
            "rail": {"left": SOURCE_X, "right": RAIL_END},
            "plank": {"x": PLANK_X, "y": PLANK_Y, "half_width": PLANK_HALF},
            "goal": {"x": GOAL_X, "top": GOAL_TOP, "bottom": GOAL_BOTTOM,
                     "half_width": GOAL_HALF},
        }
