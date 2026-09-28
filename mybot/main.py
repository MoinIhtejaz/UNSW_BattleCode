"""
Ocean_prime bot.

Work split:
  get_safe_moves()  -> Maheer   (survival: walls/kelp, own body, other dragons)
  choose_action()   -> Vandanaa (pearl collection, growth, splitting)
  execute_turn()    -> glue, edit together

Contract between the two:
  get_safe_moves(w) returns {direction_index: safety}
      direction_index: 0=N, 1=E, 2=S, 3=W
      safety: 2 = safe, 1 = risky, 0 = last resort (probably dies)
      Certain death (kelp, any body segment) -> leave the direction OUT.
  choose_action(w, safe) returns one of:
      ("MOVE", k)   with k in safe
      ("SPLIT", n)  child gets the last n segments (check ct.can_split(n))
"""
import helper as unswbc
from helper import Direction, EdgeType
import random

ct: unswbc.Controller
game: unswbc.Game

random.seed(0)

SIZE = 7            # vision window is 7x7
HEAD = 24           # our head is the middle cell (row 3, col 3)
DIRS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

SAFE, RISKY, LAST_RESORT = 2, 1, 0


# ---------------------------------------------------------------- shared helpers
# Both of you use these. Read the window ONCE per turn: helper calls are
# expensive in the judge (100M CPU points per turn, v1 blew it on turn 1).

class Window:
    """This turn's 7x7 view as flat lists, indexed 0..48 row by row (index = row*7 + col)."""
    def __init__(self):
        tiles = ct.get_tiles()
        me = ct.get_id()
        self.length = ct.get_length()
        self.round = game.get_round_num()
        self.free = [True] * 49          # no dragon part on this cell
        self.pearl = [False] * 49        # pearl on this cell now
        self.countdown = [-1] * 49       # rounds until pearl spawns (-1 = never)
        self.edge = [[EdgeType.EMPTY] * 4 for _ in range(49)]  # edge type out of cell i in dir k
        self.part = [None] * 49          # DragonPart or None
        self.other_heads = []            # cells holding a head that isn't ours
        self.pos = [None] * 49           # absolute Position of each cell

        for i, tile in enumerate(tiles):
            self.pos[i] = tile.get_position()
            part = tile.get_dragon()
            if part is not None:
                self.free[i] = False
                self.part[i] = part
                if part.is_head() and part.get_id() != me:
                    self.other_heads.append(i)
            self.pearl[i] = tile.has_pearl()
            self.countdown[i] = tile.get_pearl_time()
            for k, d in enumerate(DIRS):
                self.edge[i][k] = tile.get_edge(d).get_edge_type()

    def open(self, i, k):
        """Can we walk across edge k out of cell i without dying on the edge itself?"""
        return self.edge[i][k] == EdgeType.EMPTY


def neighbour(i, k):
    """Index of the cell next to i in direction k (0=N,1=E,2=S,3=W), or -1 if off the window."""
    r, c = divmod(i, SIZE)
    if k == 0:
        return i - SIZE if r > 0 else -1
    if k == 1:
        return i + 1 if c < SIZE - 1 else -1
    if k == 2:
        return i + SIZE if r < SIZE - 1 else -1
    return i - 1 if c > 0 else -1


def bfs(w, start):
    """Distances from start to every reachable free cell in the window. {cell: steps}"""
    dist = {start: 0}
    queue = [start]
    for i in queue:
        for k in range(4):
            if not w.open(i, k):
                continue
            n = neighbour(i, k)
            if n != -1 and w.free[n] and n not in dist:
                dist[n] = dist[i] + 1
                queue.append(n)
    return dist


# ---------------------------------------------------------------- MAHEER: survival
def get_safe_moves(w):
    """
    Which directions won't kill us, and how safe is each?
    Returns {k: SAFE | RISKY | LAST_RESORT}. Leave out certain-death directions.

    TODO (Maheer):
      [ ] kelp edges            -> leave out            (placeholder does this)
      [ ] any body segment      -> leave out            (placeholder does this)
      [ ] portals               -> exit is unseen; decide how to rate
      [ ] dead ends             -> flood fill smaller than our length = RISKY/LAST_RESORT
      [ ] head-on risk          -> cell another head can also reach = RISKY
      [ ] tail cells            -> our own tail moves away next turn (unless we eat)
    """
    safe = {}
    for k in range(4):
        if not w.open(HEAD, k):
            continue
        n = neighbour(HEAD, k)
        if w.free[n]:
            safe[k] = SAFE
    return safe


# ---------------------------------------------------------------- VANDANAA: pearls, growth, splitting
def choose_action(w, safe):
    """
    Pick what to do this turn, using only directions in `safe`.
    Returns ("MOVE", k) or ("SPLIT", n).

    TODO (Vandanaa):
      [ ] target by time-to-eat = max(distance, countdown); skip countdown -1
      [ ] prefer SAFE over RISKY directions unless the pearl is worth it
      [ ] remember countdowns seen on earlier turns (global dict keyed by position)
      [ ] mirror tiles share a countdown (map is symmetric)
      [ ] split policy: length, round number, space; stop splitting late game
    """
    best = max(safe.values())
    options = [k for k in safe if safe[k] == best]
    return ("MOVE", random.choice(options))


# ---------------------------------------------------------------- glue (edit together)
def execute_turn() -> None:
    w = Window()
    safe = get_safe_moves(w)

    if not safe:
        ct.set_indicator_string("no safe move")
        ct.make_move(ct.get_dir())       # every option kills us; still output an action
        return

    # A crash or bad answer in choose_action would mean "no valid action" = death,
    # so check it and fall back to the safest move.
    try:
        action = choose_action(w, safe)
    except Exception as e:
        ct.output_log("choose_action crashed:", repr(e))
        action = None

    if action and action[0] == "SPLIT" and ct.can_split(action[1]):
        ct.set_indicator_string(f"split {action[1]}")
        ct.do_split(action[1])
        return

    if action and action[0] == "MOVE" and action[1] in safe:
        k = action[1]
    else:
        k = max(safe, key=safe.get)

    ct.set_indicator_string(f"{DIRS[k].value} safety={safe[k]}")
    ct.make_move(DIRS[k])


def main() -> None:
    global ct, game
    ct, game = unswbc.init()

    while unswbc.update(ct, game):
        execute_turn()
        unswbc.end_turn()


if __name__ == "__main__":
    main()
