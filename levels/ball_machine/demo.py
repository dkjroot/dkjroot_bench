"""A scripted crew tests the lid timing, then makes three catches."""


def demo(puzzle):
    from . import STATIONS, STRIDE, TARGET

    ids = list(puzzle.positions)
    # With three agents, the jaw operator can reach Tilt during the drop.
    operator_indices = (0, 1, 2, 1, 2) if len(ids) == 3 else range(len(STATIONS))
    operators = {station: ids[index % len(ids)] for station, index in zip(STATIONS, operator_indices)}
    plans = {agent: [] for agent in ids}
    positions = dict(puzzle.positions)

    def turn(actions=None, *, observe=False):
        actions = dict(actions or {})
        if observe:
            observer = next((agent for agent in ids if agent not in actions), None)
            if observer is not None:
                actions[observer] = ("look", {})
        for agent, plan in plans.items():
            plan.append(actions.get(agent))

    def controls(*, observe=False, **amounts):
        turn({operators[station]: ("actuate", {"amount": amount})
              for station, amount in amounts.items()}, observe=observe)

    def stage(*stations, minimum_turns=0):
        walks = {}
        for station in stations:
            agent = operators[station]
            if agent in walks:
                continue
            target = STATIONS[station]
            moves = walks[agent] = []
            for name, start, end in zip(("moveX", "moveY"), positions[agent], target):
                remaining = end - start
                while remaining:
                    steps = max(-STRIDE, min(STRIDE, remaining))
                    moves.append((name, {"steps": steps}))
                    remaining -= steps
            positions[agent] = target
        for index in range(max([minimum_turns, *map(len, walks.values())])):
            turn({agent: moves[index] for agent, moves in walks.items() if index < len(moves)})

    turn({agent: ("look", {}) for agent in ids})
    controls(hoist=0.75)  # Everyone is still at the unmarked starting spot.
    turn({ids[0]: ("messageAllOtherAgents", {
        "message": "Actuating at the start did nothing. Let's try the square pads.",
    })})
    for attempt in range(TARGET + 1):
        stage("hoist", "jaws", "slide", "tilt", "lid")
        controls(hoist=1, jaws=0)
        controls(hoist=1, jaws=1)
        turn(observe=True)
        turn(observe=True)

        stage("slide", "jaws", "tilt", "lid")
        controls(slide=1)
        turn()
        stage("jaws", "tilt", "lid")

        # Three more rounds elapse between release and contact with the level plank.
        controls(jaws=0)
        stage("tilt", "lid", minimum_turns=2)
        controls(tilt=0.5, observe=True)
        if attempt == 0:
            # Open early and keep pressing through the bounce.
            for step in range(5):
                controls(tilt=1, lid=0.8, observe=step in (0, 1))
            controls(lid=0.8)
            controls(lid=0.8)
            turn({operators["lid"]: ("messageAllOtherAgents", {
                "message": "It shut while I was still pressing. I'll wait until the ball "
                           "rolls off the plank before opening it next time.",
            })}, observe=True)
        else:
            for step in range(5):
                controls(tilt=1, observe=step in (0, 4))
            controls(lid=0.8)
            turn(observe=True)
            turn(observe=True)  # Close the lid and register the catch.

        if attempt < TARGET:
            stage("slide", "hoist", "jaws", "tilt", "lid")
            controls(slide=0)
            turn()

    return plans
