# dkroot bench

Give a handful of AI agents a puzzle, a few tools and no explanation of what
they're looking at. Then watch them try to work it out together.

I'm interested in what happens when several copies of the same model have to
explore a place, pass on what they've found, and do something with it. Each
agent starts with its own conversation. It gets a goal and a list of things it
can do, then has to find out what the tools and the other agents can tell it.
We get to watch the whole thing from above.

## Free to use. Please help keep it that way.

I develop this project to be freely available to everyone, supported, if at all,
by voluntary contributions. I do not want other people to profit from my work
by selling copies, putting it behind a paywall, or building a paid application
or hosted service around it. I ask you to respect that intention. Please keep
this work freely available, credit its origins, and direct people to
[my official GitHub repository](https://github.com/dkjroot/dkroot_bench) for the software.

If you want to help fund my development, please contribute **directly to me**
through [Buy Me a Coffee](https://buymeacoffee.com/dkjroot). Paying a distributor
or hosting provider does not fund my work unless I explicitly say otherwise.
There is no obligation to contribute.

**This is a personal request.** The
[GNU General Public License v3.0](LICENSE) permits commercial use and
redistribution, including forks, subject to its terms. Those legal permissions
remain unchanged by this request.

**[Support my work on Buy Me a Coffee](https://buymeacoffee.com/dkjroot)**

## Watch a demo

You'll need Python 3.10 or newer and a browser. There are no extra Python
packages to install. From the project directory, run:

```sh
python3 bench.py --serve
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765), pick a puzzle and click
**Run demo**. You can switch puzzles and pick a saved recording from the same
screen. The demos run without internet access or an API key. To start one
straight from the terminal, use `python3 bench.py --demo --level word_jumble`.

The viewer highlights the agent acting now and shows what they did. Use the
arrow buttons to go one action at a time, or press **Play** and pick a speed.
You can also jump to any action in the current round or use the slider to
move further back. Clicking an agent's name highlights its sight range.

The first level is Glass Tic-Tac-Toe. Four agents start at random spots and
can see a small circle directly below them. There's a board painted on the
floor, loose Xs and Os, and a sign that tells them what to do. They have to
find the sign and leave a position in which X has legally won. A move is
one, two or three spaces along one axis, and each agent gets one tool call
per turn. The cards beside the play area show each agent's latest view
and the messages they've sent and received.

Word Jumble gives a single agent a set of shuffled words. It can read the list
and submit an answer. The initials of the words in the original sentence spell
the answer, which scores 1 when submitted. The viewer shows each attempt and
its feedback, then reveals the sentence when the agent gets it right.

## Try it with a model

Choose a tool-capable model ID from [OpenRouter's model catalogue](https://openrouter.ai/models)
and set `OPENROUTER_API_KEY` in your shell. Then configure and start a run:

```sh
./configure --model provider/model-id --seed 42 --agents 4 --rounds 60
python3 bench.py
```

Use the actual model ID in place of `provider/model-id`. The script writes
`bench.json`, which you can edit or replace between runs. `./configure --help`
shows the options, including the maximum completion tokens per request and an
optional provider and reasoning effort. You can start with fewer rounds to
see how a model gets on. Four agents and 60 rounds allow up to 240 requests
using your OpenRouter account.

With a model configured and the key set, the viewer also has a **Run** button
labelled with that model. It uses the saved settings for the selected puzzle.
When you switch puzzles, the new puzzle's default agent count applies.

Each agent starts with a short goal and the tool definitions. The
spectator view and saved recording carry the level name. Each agent sees its
own tool results and receives other agents' messages through its mailbox.
The runner makes one request per active agent each round, waits for the
replies, then applies actions in agent number order. A model reply has to
contain a named tool call with JSON arguments matching the tool's fields,
types and limits to change anything. The viewer runs on localhost. Once a
run starts, the browser controls only the playback.

The runner asks OpenRouter to disable web search and response healing. Use a
key from an account without enforced plugin overrides, which can take
precedence over those request settings. See the
[OpenRouter plugin documentation](https://openrouter.ai/docs/guides/features/plugins/overview).

Use `python3 bench.py --no-viewer` to run once and exit. Press Ctrl+C to
stop the viewer.

## Look back through the runs

Each run gets a summary and a replay in `runs/`. The summary records the seed,
model and provider, elapsed time, time to solve if it did, and token counts and
USD cost reported by OpenRouter. If a request comes back without usage for
a metric, the viewer leaves that total blank. It includes requests already sent
during the winning round in the totals.

Glass Tic-Tac-Toe scores from 0 to 1. Each wrong or missing square relative to
the closest legal X win costs 0.2, down to zero. A square holding more than
one piece needs fixing too. A score of 1 ends the run.

In **Compare runs**, pick a level, run type, seed and metric to graph
individual runs. Scripted demos show up for score, time and tool calls.
Their token and cost fields are blank. The table shows the setup and lets
you open any of those replays.
For a fair comparison, use the same seeds, number of agents and run limits.
The seed fixes the initial layout. Model replies and API timings can still vary.

## Make a level

The first puzzle lives in `levels/glass_tic_tac_toe/`. A level exports its
title, version, goal, default agent count and a `Puzzle(seed, agents)` class.
The class supplies the tool definitions, applies an action, scores the
current state and produces a spectator snapshot. The common runner handles
the agent contexts, mailboxes, calls to the model and recordings. The viewer
can display snapshots as JSON, or a level can supply its own `viewer.js`.
Glass Tic-Tac-Toe draws a board on a canvas, while Word Jumble renders
words and submission history as text. A level can also supply a scripted demo.

Run the offline checks with `python3 -m unittest discover -s tests -v`.

## Licence

This project uses the [GNU General Public License v3.0](LICENSE).
