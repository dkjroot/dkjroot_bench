import random
import re
from dataclasses import dataclass

from dojo.level import tool

TITLE = "Word Jumble"
VERSION = 4
GOAL = "Submit the correct answer."
DEFAULT_AGENTS = FIXED_AGENTS = 1
USE_COMMUNICATION = False
MAX_CALLS_PER_TURN = 1

TOOLS = (
    tool("read", "Read the puzzle."),
    tool("submit", "Submit an answer.",
         answer={"type": "string", "minLength": 1, "maxLength": 500}),
)

CRYPTIC_CLUES = (
    ("Bird from crowd with its tail cut off.", "crow",
     "Cut the last letter off CROWD to get CROW, a bird."),
    ("Quiet when listen is scrambled.", "silent",
     "Scramble LISTEN to get SILENT, meaning quiet."),
    ("Metal found in bright inlay.", "tin",
     "TIN is hidden across the join in brighT INlay."),
    ("A river edge or a place for money.", "bank",
     "A BANK is the edge of a river and a place to keep money."),
    ("Tense when desserts are served backwards.", "stressed",
     "Reverse DESSERTS to get STRESSED, meaning tense."),
    ("A unit of language from a sword with its head cut off.", "word",
     "Take the first letter off SWORD to get WORD."),
    ("An ingredient in bread that sounds like a flower.", "flour",
     "FLOUR is an ingredient in bread and sounds like FLOWER."),
    ("Game with a foot followed by a dance.", "football",
     "Put FOOT before BALL, a formal dance, to get FOOTBALL."),
    ("Fruit initially available past private lane entrances.", "apple",
     "Take the initials of Available Past Private Lane Entrances."),
    ("Timepieces from lock held by the edges of cupboards.", "clocks",
     "Put LOCK between C and S, the outside letters of CUPBOARDS."),
)


@dataclass(frozen=True)
class Stage:
    clue: str
    answer: str
    words: tuple[str, ...]
    explanation: str


def shuffle_stage(rng, clue, answer, explanation):
    ordered = re.findall(r"[a-z]+", clue.lower())
    shuffled = ordered.copy()
    rng.shuffle(shuffled)
    if shuffled == ordered:
        shuffled[0], shuffled[1] = shuffled[1], shuffled[0]
    return Stage(clue, answer, tuple(shuffled), explanation)


class Puzzle:
    tools = TOOLS

    def __init__(self, seed, agents):
        rng = random.Random(seed)
        self.stages = tuple(shuffle_stage(rng, *clue) for clue in CRYPTIC_CLUES)
        self.completed = 0

    @property
    def current(self):
        return self.stages[min(self.completed, len(self.stages) - 1)]

    def act(self, agent, name, arguments):
        if self.completed == len(self.stages):
            return {"error": "All puzzles are solved."}
        if name == "read":
            return {"words": list(self.current.words)}
        if name != "submit":
            return {"error": "Unknown action."}
        answer = arguments["answer"].strip().lower()
        expected = self.current.answer
        if answer == expected:
            self.completed += 1
            feedback = ("Success. All ten puzzles are solved." if self.completed == len(self.stages)
                        else "Success. Next puzzle is available.")
            return {"correct": True, "feedback": feedback}
        return {"correct": False, "feedback": "Incorrect."}

    def evaluate(self):
        total = len(self.stages)
        return self.completed == total, self.completed / total

    def snapshot(self):
        total = len(self.stages)
        solution = None
        if self.completed:
            solved = self.stages[self.completed - 1]
            solution = {
                "stage": self.completed, "sentence": solved.clue, "answer": solved.answer,
                "explanation": solved.explanation,
            }
        return {
            "stage": min(self.completed + 1, total), "total": total, "completed": self.completed,
            "words": self.current.words, "solution": solution,
        }


def demo(puzzle):
    actions = []
    for index, stage in enumerate(puzzle.stages):
        actions.append(("read", {}))
        if index == 0:
            actions.append(("submit", {"answer": "crowd"}))
        actions.append(("submit", {"answer": stage.answer}))
    return {"1": actions}
