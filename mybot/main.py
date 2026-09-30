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
OPP = [2, 3, 0, 1]                              # opposite of direction k
DIR_INDEX = {d: k for k, d in enumerate(DIRS)}  # Direction -> 0..3
STEP = [(0, -1), (1, 0), (0, 1), (-1, 0)]       # (dx, dy) for N, E, S, W

# Memory for this dragon's whole life. Each dragon is its own process, so this is per
# dragon; a split child starts empty. Kelp and portals never change, so anything we've
# seen once stays true.
OPEN_EDGE, KELP_EDGE = -1, -2
EDGES = {}      # (x, y) -> [N, E, S, W] edge codes: OPEN_EDGE, KELP_EDGE, or portal id (>= 0)
PORTALS = {}    # portal id -> {(x, y, k)}: cell (x, y) has that portal on its side k
TRAIL = []      # our head position each turn, oldest first. Segment s sits at TRAIL[-1 - s].


def remember(w, tiles):
    """Store this turn's edges and our head position."""
    for i in range(49):
        p = w.pos[i]
        cell = (p.x, p.y)
        if cell in EDGES:
            continue
        codes = []
        for k in range(4):
            t = w.edge[i][k]
            if t == EdgeType.EMPTY:
                codes.append(OPEN_EDGE)
            elif t == EdgeType.KELP:
                codes.append(KELP_EDGE)
            else:
                pid = tiles[i].get_edge(DIRS[k]).get_portal_id()
                codes.append(pid)
                PORTALS.setdefault(pid, set()).add((p.x, p.y, k))
        EDGES[cell] = codes
    head = (w.pos[HEAD].x, w.pos[HEAD].y)
    if not TRAIL or TRAIL[-1] != head:          # a split turn doesn't move the head
        TRAIL.append(head)
        if len(TRAIL) > 600:
            del TRAIL[:200]


def step(cell, k):
    dx, dy = STEP[k]
    return (cell[0] + dx) % game.width, (cell[1] + dy) % game.height


def cross(cell, k):
    """
    Where we land stepping out of `cell` in direction k, as (cell, None),
    or (None, why) with why = "kelp" | "portal" (exit never seen) | "unseen" (we don't know this cell).
    Portals: partner shares the id and orientation and we keep moving in direction k, so we
    come out on the cell whose OPP[k] side holds the partner.
    """
    codes = EDGES.get(cell)
    if codes is None:
        return None, "unseen"
    c = codes[k]
    nxt = step(cell, k)
    if c == OPEN_EDGE:
        return nxt, None
    if c == KELP_EDGE:
        return None, "kelp"
    for x, y, side in PORTALS[c]:
        if side == OPP[k] and (x, y) != nxt:   # (nxt, OPP[k]) is this same edge seen from the far side
            return (x, y), None
    return None, "portal"


def view_index(head, cell):
    """Window index 0..48 of an absolute cell, or -1 if it's outside this turn's view."""
    col = (cell[0] - head[0] + 3) % game.width
    row = (cell[1] - head[1] + 3) % game.height
    return row * SIZE + col if col < SIZE and row < SIZE else -1


def own_body(w, head):
    """
    {cell: s} for our own segments, s = steps back from the head (head = 0, tail = length-1).
    Two sources:
      - the window: a body segment faces the segment in front of it, so walk back from the head.
        This works on turn 1, before we have a trail.
      - our trail: segment s is where our head was s moves ago. Stops at any gap (a portal
        crossing or a sprint), since past that point the positions don't line up.
    """
    me = ct.get_id()
    behind = {}                                  # window cell -> window cell one segment further back
    for i in range(49):
        p = w.part[i]
        if p is None or p.is_head() or p.get_id() != me:
            continue
        k = DIR_INDEX[p.get_dir()]
        if w.edge[i][k] != EdgeType.EMPTY:
            continue
        t = neighbour(i, k)
        if t != -1:
            behind[t] = i
    body = {head: 0}
    cur = HEAD
    while cur in behind:
        cur = behind[cur]
        cell = (w.pos[cur].x, w.pos[cur].y)
        if cell in body:                         # safety net, a real body never loops
            break
        body[cell] = len(body)

    t = len(TRAIL) - 1
    for s in range(1, min(w.length, len(TRAIL))):
        t -= 1
        if TRAIL[t + 1] not in (step(TRAIL[t], 0), step(TRAIL[t], 1), step(TRAIL[t], 2), step(TRAIL[t], 3)):
            break
        body.setdefault(TRAIL[t], s)
    return body


def space_after(w, start, occupant, grow, enough):
    """
    Time-aware flood fill from `start`, where our head will be after this move, over every cell
    we've ever seen. Our own segment s is gone after (length - s) of our moves, and collisions
    are checked before the tail moves, so we can step onto it on move d once
    d >= length - s + 1 (+1 more if we eat on this move).
    Stops early once it has found `enough` cells.
    Returns (cells reachable, status):
        "open"   found `enough` room, or ran into cells we've never seen (assume fine)
        "portal" closed except for portal(s) whose exit we've never seen
        "closed" fully enclosed, `cells` is its real size
    """
    L = w.length
    depth = {start: 1}
    queue = [start]
    status = "closed"
    for cell in queue:
        if len(depth) >= enough or cell not in EDGES:
            return len(depth), "open"
        d = depth[cell] + 1
        for k in range(4):
            n, why = cross(cell, k)
            if n is None:
                if why == "portal":
                    status = "portal"
                continue
            if n in depth:
                continue
            s = occupant(n)
            if s is not None and (s < 0 or d < L - s + 1 + grow):
                continue
            depth[n] = d
            queue.append(n)
    return len(depth), status


def head_threat(w, j):
    """Can another dragon's head (either team) step onto window cell j before our next turn?"""
    for h in w.other_heads:
        for k in range(4):
            if neighbour(h, k) == j and w.edge[h][k] == EdgeType.EMPTY:
                return True
    return False


def get_safe_moves(w):
    """
    Which directions won't kill us, and how safe is each?
    Returns {k: SAFE | RISKY | LAST_RESORT}. Certain-death directions are left out.

      [x] kelp edges            -> left out
      [x] any body segment      -> left out. Includes our own tail: collisions are checked
                                   before the tail moves, so stepping onto it kills us.
      [x] portals               -> exit ever seen: judged like a normal cell, but capped at
                                   RISKY if the exit is outside our view right now.
                                   exit never seen: RISKY
      [x] dead ends             -> flood fill over every cell we've seen (not just the 7x7):
                                   closed region smaller than our length = LAST_RESORT,
                                   smaller than 2x our length = RISKY,
                                   only way out is an unseen portal exit = RISKY
      [x] head-on risk          -> another head can also reach the cell = RISKY
      [x] tail cells            -> flood fill counts our tail cells as free once they
                                   have moved away (later turns, not this one)
    """
    tiles = ct.get_tiles()                       # helper caches these, no re-parse
    remember(w, tiles)
    me = ct.get_id()
    head = (w.pos[HEAD].x, w.pos[HEAD].y)
    back = OPP[DIR_INDEX[ct.get_dir()]]          # our neck is always directly behind the head
    body = own_body(w, head)
    L = w.length

    def occupant(cell):
        """None = free as far as we know; s >= 0 = our segment s; -1 = someone else's segment."""
        j = view_index(head, cell)
        if j != -1:
            if w.free[j]:
                return None
            if w.part[j].get_id() == me:
                return body.get(cell, -1)
            return -1
        return body.get(cell)                    # out of view: only our own trail is known

    safe = {}
    for k in range(4):
        through_portal = w.edge[HEAD][k] == EdgeType.PORTAL
        if through_portal and k == back:
            continue                             # back through a portal = into our own neck
        n, why = cross(head, k)
        if n is None:
            if why == "portal":
                safe[k] = RISKY                  # exit never seen: can't check it
            continue
        if occupant(n) is not None:
            continue                             # any segment right now is death, our tail included

        j = view_index(head, n)
        rating = SAFE
        if j == -1 or head_threat(w, j):         # can't see the exit cell, or a head can reach it too
            rating = RISKY

        grow = 1 if j != -1 and w.pearl[j] else 0
        cells, status = space_after(w, n, occupant, grow, 2 * L)
        if status == "closed" and cells < L:
            rating = LAST_RESORT                 # we run out of room before our tail clears
        elif status != "open":
            rating = min(rating, RISKY)          # tight, or only way out is a portal we can't see through

        safe[k] = rating
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
