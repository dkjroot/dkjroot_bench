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
python3 bench.py --serve --demo
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765), pick a puzzle and click
**Run demo**. You can switch puzzles and pick a saved recording from the same
screen. The demos run without internet access or an API key. To start one
straight from the terminal, use `python3 bench.py --demo --level word_jumble`.
The **Run demo** button is available when the viewer starts with `--demo`.

The viewer highlights the agent acting now and shows what they did. Use the
arrow buttons to go one action at a time, or press **Play** and pick a speed.
You can also jump to any action in the current round or use the slider to
move further back. Clicking an agent's name highlights its sight range.

The first level is Glass Tic-Tac-Toe. Four agents start at random spots and
can see a small circle directly below them. There's a board painted on the
floor, loose Xs and Os, and a sign that tells them what to do. They have to
find the sign and leave a position in which X has legally won. They can only
read the part of the sign inside their view. A move is
one, two or three spaces along one axis, and each agent gets one tool call
per turn. Agents can see teammates within their sight circle. The cards
beside the play area show each agent's latest view.
Messages appear below the game, newest first. A tick beside a recipient
shows when the message reached their conversation, including as you step through
a replay. During playback, the agents glide between recorded positions and
short speech bubbles show what they just said. When a provider supplies
readable reasoning, the viewer can show a short **Agent thinking** bubble.

Word Jumble gives one agent ten shuffled cryptic crossword clues. Each
correct answer unlocks the next puzzle and adds 0.1 to the score. A wrong
guess gets the same reply each time. The viewer puts the latest guess at
the top and reveals each solved clue and how it works.

In Ball Machine, everyone starts in the top left. Their job is to get three
balls into the bin before time runs out. A look gives them a small grayscale
picture of the whole field. They can try the controls and see what changes.
The viewer shows the same picture below the replay controls, so you can see
what the agent was working with.

## Try it with a model

You can run a puzzle with a Gemini API key from Google AI Studio or with an
OpenRouter API key. Use a model that can call tools.

### Gemini directly

Set `GEMINI_API_KEY` in your shell using your key from Google AI Studio.
This runs up to three turns of Word Jumble, which uses one agent. Use a model ID
from the Gemini API catalogue, such as `gemini-3.1-flash-lite`.
[Google AI Studio API keys](https://aistudio.google.com/apikey) · [Gemini models](https://ai.google.dev/gemini-api/docs/models)

```sh
./configure --backend gemini --model gemini-3.1-flash-lite --level word_jumble --seed 42 --rounds 3
python3 bench.py --no-viewer
```

Gemini requests are paced together across the agents in a run. TPM is the
number of input tokens per minute and RPM is the number of requests per
minute. For a Gemini 3.8 Flash project with a two-million input TPM quota,
I'd start at `--gemini-input-tpm 1500000 --gemini-rpm 60`, provided the
project allows at least 60 RPM. Use a lower cap if the project's quota is
lower or other apps are using the same project. Gemini 3.8 Flash starts with
a two-million input TPM budget if you omit the flag. The runner can learn a
lower limit from a quota error and retry after Google's suggested wait.
Use `--gemini-thought-summaries` if you want Gemini to return thought
summaries for the replay. [Google AI Studio rate limits](https://aistudio.google.com/rate-limit)

### OpenRouter

Set `OPENROUTER_API_KEY` in your shell and choose a model ID from
[OpenRouter's model catalogue](https://openrouter.ai/models).

```sh
./configure --backend openrouter --model provider/model-id --seed 42 --agents 4 --rounds 60
python3 bench.py
```

Use the actual model ID in place of `provider/model-id`. Four agents and 60
rounds allow up to 240 requests. OpenRouter is the default backend. You can
also set `--provider` and `--reasoning-effort` for OpenRouter runs.

The runner asks OpenRouter to disable web search and response healing. Use a
key from an account without enforced plugin overrides, which can take
precedence over those request settings.
[OpenRouter plugin documentation](https://openrouter.ai/docs/guides/features/plugins/overview).

### How I prefer to run it

I set my API key in the terminal, configure the model I want to try, then
open the viewer and start the run from there. For Gemini 3.8 Flash, I'd
start with these settings.

```sh
./configure --backend gemini --model gemini-3.8-flash \
  --gemini-input-tpm 1500000 --gemini-rpm 60
python3 bench.py --serve
```

I open [the local viewer](http://127.0.0.1:8765), pick a puzzle, and set
**Max rounds** if I want a different limit. Then I click **Run**. I can also pick a
saved recording and click **Play replay**. When I want to try a different
model, I stop the viewer with Ctrl+C, run `./configure` again, and restart
it. Each configure command replaces `bench.json`. The runs and replays are
saved separately under `runs/`.

When I open the viewer in serve mode, I get a round-zero preview of the
selected puzzle. If I leave the seed unset, the preview uses a sample seed
and each new run draws its own seed.

### Run settings and the viewer

`./configure` writes `bench.json`, which you can edit or replace between
runs. To save another set of settings alongside it, use `./configure
--output another.json` with your model and level options and start it with
`python3 bench.py --config another.json`. Keep API keys in your shell.
Run `./configure --help` to see the available settings, including the maximum
completion tokens per request.

With a model configured and the key set, the viewer also has a **Run** button
labelled with that model. It uses the saved settings for the selected puzzle.
Set **Max rounds** before you launch to give the next run a different limit.
Clear the field to go back to the configured model limit or the demo default.
When you switch puzzles, the new puzzle's default agent count applies.
Ball Machine is set up for five agents by default and needs at least two.
Choose a model that accepts image inputs and can call tools. If you configure
Ball Machine without a round limit, it gets 400 rounds for a model run.
With five agents, that allows up to 2,000 requests.

Each agent starts with a short goal and the tool definitions. The
spectator view and saved recording carry the level name. Each agent sees its
own tool results. Messages sent in one round appear automatically in each
recipient's conversation before they act in the next round.
The runner makes one request per active agent each round, waits for the
replies, then applies actions in agent number order. A model reply has to
contain a named tool call with JSON arguments matching the tool's fields,
types and limits to change anything. The viewer runs on localhost.
If a reply contains no tool call, the runner counts it as a halt and prompts
the agent to act on its next turn. The agent gets up to three follow-up
turns in a row. A tool call resets that allowance.

Click **Pause run** to let the current round finish and wait. The viewer
also pauses automatically when it reaches **Max rounds**. While paused,
you can change that limit, then click **Resume run** to carry on with the
same game. At the round limit, enter a larger total before resuming.

Click **Stop run** if you've seen enough. Requests already sent can still
finish and use tokens. The runner saves the partial replay and you can
start another run from the same page.

If a model request fails and the runner cannot recover automatically, the
viewer pauses. **Resume run** retries the failed calls in that round and
uses the successful replies it already has. A paused or stopped run has
a checkpoint. After restarting the viewer, select its recording and click
**Resume saved run** to continue with the same model and agent histories.
If it stopped at the round limit, enter a higher **Max rounds** first.
Keep `checkpoint.json` private if you share a replay. It contains the agents'
full histories and the replies needed to resume the game.

Use `python3 bench.py --no-viewer` to run from the terminal and exit at the
configured round limit. Press Ctrl+C to stop the viewer.

## Look back through the runs

Each run gets a `summary.json` and a `frames.jsonl` replay in its own folder
under `runs/`. Start `python3 bench.py --serve`, choose a recording and click
**Play replay** to watch it from the beginning. Keep a copy of `runs/` if you
want to move the recordings to another machine. The summary records the seed, backend, model,
provider, elapsed time, time to solve and reported token counts. OpenRouter
also reports the cost in USD. The viewer leaves the cost blank for direct
Gemini runs and leaves any other usage total blank if a request came back
without that metric. Requests already sent during the winning round count
towards the totals.

Glass Tic-Tac-Toe scores from 0 to 1. Each wrong or missing square relative to
the closest legal X win costs 0.2, down to zero. A square holding more than
one piece needs fixing too. A score of 1 ends the run.

In **Compare runs**, choose **Published locally**, **Local and public**, or
**Public**, then pick a level, seed and metric. The level and version list
comes from the published model recordings in that view. A run published
both ways appears once in the combined view. You can watch saved demos from
**Recordings**. The graphs are for model runs. The **Halts** metric counts
replies without a tool call, including those the agent recovered from.
For a fair comparison, use the same seeds, number of agents and run limits.
The seed fixes the initial layout. Model replies and API timings can still vary.

## Submit a recording

Open **Recordings** in the viewer. **Publish locally** adds a finished run
to your own graphs. You can remove it from those graphs at any time.
**Publish to Git** copies the summary and replay into a new folder under
`recordings/`. You can do both for the same run. The Git copy stays as it was
when you published it, even if you later resume the local run.
Use **Delete** to remove a finished local recording that hasn't been
published to Git. The viewer asks for confirmation first.

Use **Add comment** or **Edit comment** to leave a note on a recording.
Comments appear in **Recordings** and **Compare runs**. A Git-published run
keeps its comment in a separate `comment.txt` file. Commit that file to share
an edit to a published comment.

Review the files in `recordings/<run-id>/` before sharing them. The replay
can include messages and recorded thinking. The private checkpoint stays
under `runs/`. Add the new folder to Git, commit it on a branch, and open a
merge request. Once the request is merged, the recording appears in the
**Public** graphs in checkouts that have the new data.

I only accept recording merge requests that add new recordings or update
their separate comment files. Please put code changes in a separate merge
request and leave existing summaries and replays exactly as they are.

Please, please only submit recordings of runs you actually made. Faked data
makes the graphs useless for everyone who's taken the time to run the benchmark.

## Make a level

The first puzzle lives in `levels/glass_tic_tac_toe/`. A level exports its
title, version, goal, default agent count and a `Puzzle(seed, agents)` class.
The class supplies the tool definitions, applies an action, scores the
current state and produces a spectator snapshot. The common runner handles
the agent contexts, mailboxes, calls to the model and recordings. The viewer
can display snapshots as JSON, or a level can supply its own `viewer.js`.
Glass Tic-Tac-Toe and Ball Machine draw their playfields on a canvas, while
Word Jumble renders words and submission history as text. A level can also
supply a scripted demo. Ball Machine advances its mechanism once after all
agents have acted in a round, and that motion is saved in the replay.

Run the offline checks with `python3 -m unittest discover -s tests -v`.

## Licence

This project uses the [GNU General Public License v3.0](LICENSE).
