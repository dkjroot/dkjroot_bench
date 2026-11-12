export const worldTitle = "Puzzle and submissions";
export const compactAgents = true;

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

export function renderWorld(container, world, history) {
  const previous = container.querySelector(".jumble-attempts");
  const follow = !previous || previous.scrollHeight - previous.scrollTop - previous.clientHeight <= 6;

  const panel = element("div", "jumble");
  const prompt = element("section");
  const words = element("ul", "word-list");
  words.append(...world.words.map(word => element("li", "", word)));
  prompt.append(element("h3", "", "Shuffled words"), words);

  const submissions = element("section");
  const heading = element("h3", "", "Submission attempts");
  heading.id = "submission-heading";
  submissions.append(heading);
  const events = history.filter(event => event.kind === "tool" && event.name === "submit");
  const attempts = element("ol", "jumble-attempts");
  attempts.setAttribute("aria-labelledby", heading.id);
  if (events.length) {
    attempts.tabIndex = 0;
    for (const [index, {arguments: args, result}] of events.entries()) {
      const item = element("li", "jumble-attempt");
      item.dataset.correct = Boolean(result.correct);
      const content = element("div");
      content.append(element("span", "attempt-answer", args?.answer ?? "Rejected submission"),
        element("span", "attempt-feedback", result.feedback ?? result.error));
      item.append(element("span", "attempt-number", String(index + 1)), content);
      attempts.append(item);
    }
    submissions.append(attempts);
  } else {
    submissions.append(element("p", "quiet", "No submissions yet."));
  }
  panel.append(prompt, submissions);

  if (world.solution) {
    const solution = element("section", "jumble-solution");
    const sentence = element("p");
    const words = world.solution.sentence.split(" ");
    words.forEach((word, index) => {
      if (index) sentence.append(" ");
      sentence.append(element("strong", "", word[0]), word.slice(1));
    });
    solution.append(element("h3", "", "Solved · " + world.solution.answer.toUpperCase()), sentence);
    panel.append(solution);
  }

  container.replaceChildren(panel);
  if (events.length) attempts.scrollTop = follow ? attempts.scrollHeight : previous.scrollTop;
}
