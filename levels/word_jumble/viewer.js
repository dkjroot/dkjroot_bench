export const worldTitle = "Puzzle and submissions";
export const compactAgents = true;
const views = new WeakMap();

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content != null) node.textContent = content;
  return node;
}

export function describeAction({name, arguments: args, result}) {
  if (name === "read") return {title: "Read the jumbled words", short: "Read"};
  if (name !== "submit") return null;
  return {
    title: result.correct ? "Submitted the correct answer" : "Submitted an answer",
    short: "Submit",
    detail: args.answer + " · " + result.feedback,
  };
}

function createView(container) {
  const panel = element("div", "jumble");
  const status = element("div", "jumble-progress");
  const label = element("h3");
  const progress = element("progress");
  const solved = element("span");
  progress.setAttribute("aria-label", "Puzzles solved");
  status.append(label, progress, solved);

  const prompt = element("section");
  const words = element("ul", "word-list");
  prompt.append(element("h3", "", "Shuffled words"), words);

  const submissions = element("section");
  const heading = element("h3", "", "Submission attempts");
  heading.id = "submission-heading";
  const attempts = element("ol", "jumble-attempts");
  attempts.setAttribute("aria-labelledby", heading.id);
  attempts.reversed = true;
  attempts.tabIndex = 0;
  const empty = element("p", "quiet", "No submissions yet.");
  submissions.append(heading, attempts, empty);

  const solution = element("section", "jumble-solution");
  solution.hidden = true;
  panel.append(status, prompt, submissions, solution);
  container.replaceChildren(panel);
  const view = {panel, status, label, progress, solved, words, attempts, empty, solution};
  views.set(container, view);
  return view;
}

function updateAttempts({attempts, empty}, events, world) {
  const count = attempts.childElementCount;
  attempts.hidden = events.length === 0;
  empty.hidden = events.length > 0;
  if (events.length === count) return;

  const top = attempts.scrollTop;
  const height = attempts.scrollHeight;
  for (let index = count; index > events.length; index--) attempts.firstElementChild.remove();
  if (events.length > count) {
    const added = document.createDocumentFragment();
    let stage = (world.completed ?? 0) + 1;
    for (let index = events.length - 1; index >= count; index--) {
      const {arguments: args, result} = events[index];
      if (result.correct) stage--;
      const item = element("li", "jumble-attempt");
      item.dataset.correct = Boolean(result.correct);
      const content = element("div");
      if (world.total > 1) content.append(element("span", "attempt-stage", "Puzzle " + stage));
      content.append(element("span", "attempt-answer", args?.answer ?? "Rejected submission"),
        element("span", "attempt-feedback", result.feedback ?? result.error));
      item.append(element("span", "attempt-number", String(index + 1)), content);
      added.append(item);
    }
    attempts.prepend(added);
  }
  // A reader at the top follows new guesses. Keep older guesses in place while browsing.
  attempts.scrollTop = top <= 6 ? 0 : top + attempts.scrollHeight - height;
}

function updateSolution(view, revealed) {
  if (view.solutionKey === revealed?.sentence) return;
  view.solutionKey = revealed?.sentence;
  const {solution} = view;
  solution.hidden = !revealed;
  if (!revealed) {
    solution.replaceChildren();
    return;
  }
  const {stage, answer, explanation} = revealed;
  const sentence = element("p");
  if (explanation) {
    sentence.textContent = revealed.sentence;
  } else {
    revealed.sentence.split(" ").forEach((word, index) => {
      if (index) sentence.append(" ");
      sentence.append(element("strong", "", word[0]), word.slice(1));
    });
  }
  const heading = stage ? "Puzzle " + stage + " solved" : "Solved";
  solution.replaceChildren(element("h3", "", heading + " · " + answer.toUpperCase()), sentence);
  if (explanation) solution.append(element("p", "jumble-explanation", explanation));
}

export function renderWorld(container, world, history) {
  let view = views.get(container);
  if (view?.panel.parentNode !== container) view = createView(container);
  view.status.hidden = !(world.total > 1);
  if (!view.status.hidden) {
    view.label.textContent = "Puzzle " + world.stage + " of " + world.total;
    view.progress.max = world.total;
    view.progress.value = world.completed;
    view.solved.textContent = world.completed + " solved";
  }
  const wordsKey = world.words.join(" ");
  if (wordsKey !== view.wordsKey) {
    view.wordsKey = wordsKey;
    view.words.replaceChildren(...world.words.map(word => element("li", "", word)));
  }
  updateAttempts(view, history.filter(event => event.kind === "tool" && event.name === "submit"), world);
  updateSolution(view, world.solution);
}
