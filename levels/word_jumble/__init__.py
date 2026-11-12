import random
import re

from dojo.level import tool

TITLE = "Word Jumble"
VERSION = 1
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


def initials(words):
    return "".join(word[0] for word in words)


class Puzzle:
    tools = TOOLS

    def __init__(self, seed, agents):
        rng = random.Random(seed)
        self.sentence = rng.choice(SENTENCES)
        ordered = re.findall(r"[a-z]+", self.sentence.lower())
        self.answer = initials(ordered)
        self.words = ordered.copy()
        rng.shuffle(self.words)
        if initials(self.words) == self.answer:
            self.words[0], self.words[1] = self.words[1], self.words[0]
        self.solved = False

    def act(self, agent, name, arguments):
        if name == "read":
            return {"words": self.words.copy()}
        if name != "submit":
            return {"error": "Unknown action."}
        answer = arguments["answer"].strip().lower()
        if answer == self.answer:
            self.solved = True
            return {"correct": True, "feedback": "Correct."}
        single_word = re.fullmatch(r"[a-z]+", answer) is not None
        if len(answer) > len(self.answer):
            feedback = "Too long." if single_word else "Too long. Submit a single word."
        elif not single_word:
            feedback = "Submit a single word."
        else:
            feedback = "Incorrect."
        return {"correct": False, "feedback": feedback}

    def evaluate(self):
        return self.solved, float(self.solved)

    def snapshot(self):
        solution = {"sentence": self.sentence, "answer": self.answer} if self.solved else None
        return {"words": self.words, "solution": solution}


def demo(puzzle):
    return {"1": [
        ("read", {}),
        ("submit", {"answer": puzzle.sentence}),
        ("submit", {"answer": initials(puzzle.words)}),
        ("submit", {"answer": puzzle.answer}),
    ]}
