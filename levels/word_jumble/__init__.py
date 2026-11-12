import random
import re
from dataclasses import dataclass

from dojo.level import tool

TITLE = "Word Jumble"
VERSION = 2
GOAL = "Submit the correct answer."
DEFAULT_AGENTS = FIXED_AGENTS = 1
USE_COMMUNICATION = False
MAX_CALLS_PER_TURN = 1

TOOLS = (
    tool("read", "Read the puzzle."),
    tool("submit", "Submit an answer.",
         answer={"type": "string", "minLength": 1, "maxLength": 500}),
)

# Each sentence's initials spell an English word in its intended order.
SENTENCES = (
    "Ben rarely interrupts during group exercises.",
    "Bakers rarely eat all desserts.",
    "Cats are surprisingly tolerant little explorers.",
    "Cats rarely obey whispered names.",
    "Four old radios entertained some tourists.",
    "Fresh lemons add more excitement.",
    "Gardeners appreciate rich dirt every November.",
    "Lena always needs time, especially right now.",
    "Lucy enjoys making oat noodles.",
    "My aunt prefers lemon extract.",
    "Owls can easily avoid nets.",
    "People always prefer easier routes.",
    "People enjoy a relaxed lunch.",
    "Peter enjoys new challenges in life.",
    "Please leave a note explaining this.",
    "Sam has eaten leftover lasagne.",
    "Sam invited Lucy via email recently.",
    "Several people ran into nearby gardens.",
    "She thought Oliver needed encouragement.",
    "The rabbits are indoors now.",
)

CRYPTIC_CLUES = (
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


def initials(words):
    return "".join(word[0] for word in words)


@dataclass(frozen=True)
class Stage:
    clue: str
    answer: str
    words: tuple[str, ...]
    explanation: str | None


def shuffle_stage(rng, clue, answer=None, explanation=None):
    ordered = re.findall(r"[a-z]+", clue.lower())
    shuffled = ordered.copy()
    rng.shuffle(shuffled)
    if shuffled == ordered or (answer is None and initials(shuffled) == initials(ordered)):
        shuffled[0], shuffled[1] = shuffled[1], shuffled[0]
    return Stage(clue, answer or initials(ordered), tuple(shuffled), explanation)


class Puzzle:
    tools = TOOLS

    def __init__(self, seed, agents):
        rng = random.Random(seed)
        self.stages = (
            shuffle_stage(rng, rng.choice(SENTENCES)),
            *(shuffle_stage(rng, *clue) for clue in CRYPTIC_CLUES),
        )
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
        single_word = re.fullmatch(r"[a-z]+", answer) is not None
        if len(answer) > len(expected):
            feedback = "Too long." if single_word else "Too long. Submit a single word."
        elif not single_word:
            feedback = "Submit a single word."
        else:
            feedback = "Incorrect."
        return {"correct": False, "feedback": feedback}

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
            actions.append(("submit", {"answer": stage.clue}))
            actions.append(("submit", {"answer": initials(stage.words)}))
        actions.append(("submit", {"answer": stage.answer}))
    return {"1": actions}
