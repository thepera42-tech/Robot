"""LCD display controller (16x2 I2C). Layout is 1:3 -> 4 columns cat, 12 columns stats.
Settings page: http://localhost:5005/display   (config saved in display_config.json)"""
import copy, datetime, json, os, random, re, threading, time
from flask import jsonify, request, send_from_directory

MOODS = ["neutral", "happy", "sad", "exhausted", "sleepy", "excited", "surprised", "love"]
BARS = {"blocks": (chr(255), "-"), "hash": ("#", "-"), "eq": ("=", ".")}
ALERT_PRIORITY = {"no_flow": 1, "flow_sensor": 2, "controller": 3, "estop": 4}

HEAD = [".##..............##.", ".####..........####.", ".##################."] + \
       ["#..................#"] * 9 + [".#................#.", "..################.."]
EYE = {"dot": [".#.", "###", ".#."], "up": [".#.", "#.#", "..."], "line": ["...", "###", "..."],
       "wide": ["###", "#.#", "###"], "heart": ["#.#", "###", ".#."], "lid": ["###", "###", "..."],
       "sadL": ["#..", "###", ".#."], "sadR": ["..#", "###", ".#."],
       "gt": ["#..", ".#.", "#.."], "lt": ["..#", ".#.", "..#"]}
MOUTH = {"flat": ["######"], "w": ["#.##.#", ".#..#."], "sad": [".####.", "#....#"],
         "open": [".####.", "#....#", ".####."], "tongue": ["######", ".####."]}
# mood: (left eye, right eye, mouth, extra, bounce)
LOOK = {"neutral": ("dot", "dot", "flat", None, 0), "happy": ("up", "up", "w", None, 0),
        "sad": ("sadL", "sadR", "sad", "tear", 0), "exhausted": ("lid", "lid", "tongue", "sweat", 0),
        "sleepy": ("line", "line", "flat", "z", 0), "excited": ("gt", "lt", "open", "spark", 1),
        "surprised": ("wide", "wide", "open", None, 0), "love": ("heart", "heart", "w", "spark", 1)}

DEFAULT = {
    "enabled": True, "backlight": True, "fps": 4, "scroll_ms": 350, "bar_style": "blocks",
    "water_pump": 9, "page_seconds": 4,
    "cat": {"enabled": True, "side": "left", "name": "Mochi", "whiskers": True, "auto": True,
            "manual_mood": "happy", "speed": 1.0, "blink_min": 2, "blink_max": 6},
    "moods": {"boot": "love", "pouring": "excited", "done": "happy", "empty": "exhausted",
              "low": "sad", "tired": "exhausted", "sleep": "sleepy", "idle": "neutral", "error": "surprised"},
    "rules": {"done_hold_s": 6, "low_pct": 20, "empty_pct": 5, "tired_pours": 15, "sleep_after_s": 120},
    "tanks": {str(i): {"enabled": True, "name": ("Sparkling" if i == 9 else "Flavor %d" % i),
                       "capacity": 1000 if i == 9 else 500, "level": 1000 if i == 9 else 500,
                       "flow": 25.0, "low": 20} for i in range(1, 10)},
    "pages": [
        {"name": "Water", "on": True, "when": "idle", "dur": 4,
         "l1": {"text": "Water {water}%", "align": "l", "scroll": False},
         "l2": {"text": "{bar:water:12}", "align": "l", "scroll": False}},
        {"name": "Pours", "on": True, "when": "idle", "dur": 4,
         "l1": {"text": "Pours {pours}", "align": "l", "scroll": False},
         "l2": {"text": "Today {ml_today}ml", "align": "l", "scroll": False}},
        {"name": "Clock", "on": True, "when": "idle", "dur": 3,
         "l1": {"text": "{time}", "align": "c", "scroll": False},
         "l2": {"text": "{date}", "align": "c", "scroll": False}},
        {"name": "Pouring", "on": True, "when": "pouring", "dur": 4,
         "l1": {"text": "Pouring...", "align": "c", "scroll": False},
         "l2": {"text": "{pour_s}s {active}p", "align": "c", "scroll": False}},
        {"name": "Low", "on": True, "when": "low", "dur": 4,
         "l1": {"text": "Low: {lowest}", "align": "l", "scroll": True},
         "l2": {"text": "{lowest_pct}% left", "align": "l", "scroll": False}},
        {"name": "Error", "on": True, "when": "error", "dur": 4,
         "l1": {"text": "No Arduino", "align": "c", "scroll": False},
         "l2": {"text": "check USB", "align": "c", "scroll": False}}],
}

S = {"cfg": copy.deepcopy(DEFAULT), "stats": {"date": "", "pours_today": 0, "ml_today": 0.0, "total": 0},
    "frame": {}, "slots": [None] * 8, "hw": "starting", "dirty": False, "sending": False,
    "alerts": {}, "alert": None}
ctx = {}
alert_lock = threading.Lock()


def is_sending():
    return S["sending"]


def set_alert(key, line1=None, line2=None):
    with alert_lock:
        if line1 is None or line2 is None:
            S["alerts"].pop(key, None)
        else:
            S["alerts"][key] = (line1[:16], line2[:16])
        S["alert"] = max(S["alerts"], key=lambda item: ALERT_PRIORITY[item]) if S["alerts"] else None


def merge(base, new):
    out = copy.deepcopy(base)
    for k, v in (new or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def save():
    with open(ctx["file"], "w") as f:
        json.dump({"config": S["cfg"], "stats": S["stats"]}, f, indent=1)
    S["dirty"] = False


def load():
    try:
        d = json.load(open(ctx["file"]))
        S["cfg"] = merge(DEFAULT, d.get("config"))
        S["stats"].update(d.get("stats", {}))
    except Exception:
        pass


# ---------------- cat ----------------
def blit(c, pat, x, y):
    for j, row in enumerate(pat):
        for i, ch in enumerate(row):
            if ch == "#" and 0 <= y + j < 16 and 0 <= x + i < 20:
                c[y + j][x + i] = 1


def draw_cat(mood, t, cat, blink):
    le, re_, mo, extra, bounce = LOOK.get(mood, LOOK["neutral"])
    c = [[0] * 20 for _ in range(16)]
    blit(c, HEAD, 0, 0)
    if blink and le in ("dot", "wide"):
        le = re_ = "line"
    blit(c, EYE[le], 3, 4)
    blit(c, EYE[re_], 14, 4)
    blit(c, ["##"], 9, 8)
    blit(c, MOUTH[mo], 7, 9)
    if cat.get("whiskers") and extra != "z":
        for x, y in [(1, 8), (2, 8), (1, 10), (2, 10), (17, 8), (18, 8), (17, 10), (18, 10)]:
            c[y][x] = 1
    if extra == "tear":
        c[7 + t % 4][4] = 1
    elif extra == "sweat":
        c[4 + t % 2][17] = c[5 + t % 2][17] = 1
    elif extra == "z":
        blit(c, ["###", ".#.", "###"], 15, 8 - (t // 2) % 2)
    elif extra == "spark":
        c[3 + t % 2][2] = c[3 + (t + 1) % 2][17] = 1
    if bounce and t % 2:
        c = [[0] * 20] + c[:15]
    return c


def tiles_of(c):
    return [tuple(sum(c[ty * 8 + r][tx * 5 + k] << (4 - k) for k in range(5)) for r in range(8))
            for ty in range(2) for tx in range(4)]


def alloc(tiles, slots):
    slots = list(slots)
    uniq = []
    for t in tiles:
        if any(t) and t not in uniq:
            uniq.append(t)
    keep = {slots.index(t) for t in uniq if t in slots}
    free = [i for i in range(8) if i not in keep]
    for t in uniq:
        if t not in slots:
            slots[free.pop(0)] = t
    return [slots.index(t) if any(t) else 32 for t in tiles], slots


# ---------------- stats / text ----------------
def pct(tk):
    return max(0, min(100, round(100.0 * tk["level"] / max(1, tk["capacity"]))))


def bar(p, w):
    f, e = BARS.get(S["cfg"]["bar_style"], BARS["blocks"])
    n = int(round(w * p / 100.0))
    return f * n + e * (w - n)


def tank_of(a):
    cfg = S["cfg"]
    return cfg["tanks"].get(str(cfg["water_pump"]) if a == "water" else a)


def lows(cfg):
    out = [(pct(t), t) for t in cfg["tanks"].values() if t["enabled"] and pct(t) <= t["low"]]
    return sorted(out, key=lambda x: x[0])


def expand(text, st):
    cfg = S["cfg"]

    def rep(m):
        name, arg = m.group(1), (m.group(2) or "").split(":")
        tk = tank_of(arg[0]) if arg and arg[0] else None
        try:
            if name == "bar":
                return bar(pct(tk), int(arg[1])) if tk else ""
            if name == "lvl":
                return str(pct(tk)) if tk else "?"
            if name == "ml":
                return str(int(tk["level"])) if tk else "?"
            if name == "name":
                return tk["name"] if tk else "?"
        except Exception:
            return "?"
        w = tank_of("water")
        low = lows(cfg)
        return str({
            "water": pct(w) if w else "?", "water_ml": int(w["level"]) if w else "?",
            "pours": S["stats"]["pours_today"], "total": S["stats"]["total"],
            "ml_today": int(S["stats"]["ml_today"]), "time": datetime.datetime.now().strftime("%H:%M"),
            "date": datetime.datetime.now().strftime("%d %b %Y"), "status": st["status"],
            "mood": st["mood"], "cat": cfg["cat"]["name"], "active": st["active"],
            "pour_s": st["pour_s"], "lowest": low[0][1]["name"] if low else "-",
            "lowest_pct": low[0][0] if low else 100}.get(name, m.group(0)))
    return re.sub(r"\{([a-z_]+)(?::([^}]*))?\}", rep, text)


def clean(s):
    return "".join(ch if 32 <= ord(ch) <= 125 or ord(ch) == 255 else "?" for ch in s)


def line_text(spec, width, st, now):
    s = clean(expand(spec.get("text", ""), st))
    if len(s) > width and spec.get("scroll"):
        p = s + "   "
        o = int(now * 1000 / max(80, S["cfg"]["scroll_ms"])) % len(p)
        s = (p + p)[o:o + width]
    s = s[:width]
    a = spec.get("align", "l")
    return s.center(width) if a == "c" else s.rjust(width) if a == "r" else s.ljust(width)


def pick_page(cfg, st, now):
    w = st["when"]
    c = [p for p in cfg["pages"] if p.get("on") and p.get("when") in w]
    if not c:
        return None
    total = sum(max(1, p["dur"]) for p in c)
    t, acc = now % total, 0
    for p in c:
        acc += max(1, p["dur"])
        if t < acc:
            return p
    return c[0]


def pick_mood(cfg, st, now):
    if not cfg["cat"]["auto"]:
        return cfg["cat"]["manual_mood"]
    m, r, stt = cfg["moods"], cfg["rules"], S["stats"]
    low = lows(cfg)
    if st["error"]:
        return m["error"]
    if now - st["boot"] < 5:
        return m["boot"]
    if st["active"]:
        return m["pouring"]
    if now - st["last_done"] < r["done_hold_s"]:
        return m["done"]
    if low and low[0][0] <= r["empty_pct"]:
        return m["empty"]
    if low and low[0][0] <= r["low_pct"]:
        return m["low"]
    if stt["pours_today"] >= r["tired_pours"]:
        return m["tired"]
    if now - st["last_act"] > r["sleep_after_s"]:
        return m["sleep"]
    return m["idle"]


# ---------------- frame + loop ----------------
def build(cfg, st, now):
    cat = cfg["cat"]
    fps = max(1, cfg["fps"])
    rows = [[], []]
    if cat["enabled"]:
        c = draw_cat(st["mood"], int(now * fps * cat["speed"]), cat, st["blink"])
        codes, S["slots"] = alloc(tiles_of(c), S["slots"])
    width = 12 if cat["enabled"] else 16
    page = pick_page(cfg, st, now)
    txt = [line_text(page["l1"], width, st, now), line_text(page["l2"], width, st, now)] if page \
        else [" " * width] * 2
    for r in range(2):
        t = [ord(ch) for ch in txt[r]]
        if cat["enabled"]:
            cc = codes[r * 4:r * 4 + 4]
            rows[r] = cc + t if cat["side"] == "left" else t + cc
        else:
            rows[r] = t
    return rows, (page or {}).get("name", "")


def loop():
    st = {"boot": time.time(), "last_done": 0, "last_act": time.time(), "blink": False,
          "blink_at": time.time() + 3, "pouring": False, "pour_start": 0, "error": False}
    hw = {"slots": [None] * 8, "rows": [None, None], "bl": None, "init": False, "full": 0, "retry": 0}
    last = time.time()
    last_ping = 0

    def tx(cmd):
        S["sending"] = True
        try:
            reply = ctx["send"](cmd)
        finally:
            S["sending"] = False
        if cmd == "PING" and reply.startswith("PONG"):
            return
        if not reply.startswith("OK"):
            raise IOError(reply or "no reply")

    while True:
        cfg, now = S["cfg"], time.time()
        dt, last = now - last, now
        try:
            if now - last_ping >= 1:
                tx("PING")
                last_ping = now
            with ctx["lock"]:
                live = ctx["live"]()
            day = datetime.date.today().isoformat()
            if S["stats"]["date"] != day:
                S["stats"].update(date=day, pours_today=0, ml_today=0.0)
            for p in live:
                tk = cfg["tanks"].get(str(p["pump"]))
                if p["active"] and tk:
                    used = dt * p["speed"] / 100.0 * tk["flow"]
                    tk["level"] = max(0.0, tk["level"] - used)
                    S["stats"]["ml_today"] += used
                    S["dirty"] = True
            act = sum(1 for p in live if p["active"])
            if act and not st["pouring"]:
                st["pour_start"] = now
            if not act and st["pouring"]:
                S["stats"]["pours_today"] += 1
                S["stats"]["total"] += 1
                st["last_done"] = now
                S["dirty"] = True
            st["pouring"] = bool(act)
            if act:
                st["last_act"] = now
            if now >= st["blink_at"]:
                st["blink"] = True
                if now >= st["blink_at"] + 0.3:
                    st["blink"] = False
                    st["blink_at"] = now + random.uniform(cfg["cat"]["blink_min"], cfg["cat"]["blink_max"])
            st.update(active=act, pour_s=int(now - st["pour_start"]) if act else 0,
                      status="POURING" if act else "READY")
            st["mood"] = pick_mood(cfg, st, now)
            when = {"always"}
            when.add("pouring" if act else "idle")
            if lows(cfg):
                when.add("low")
            if st["error"]:
                when = {"error"}
            st["when"] = when
            rows, page = build(cfg, st, now)
            with alert_lock:
                active_alert = S["alerts"].get(S["alert"]) if S["alert"] else None
                active_alert_name = S["alert"]
            if active_alert:
                alert_rows = active_alert
                rows = [[ord(char) for char in text.ljust(16)[:16]] for text in alert_rows]
                page = active_alert_name.upper()
            S["frame"] = {"rows": rows, "cgram": [list(s) if s else [0] * 8 for s in S["slots"]],
                          "mood": st["mood"], "page": page, "hw": S["hw"]}
            if S["dirty"] and int(now) % 5 == 0:
                save()

            if cfg["enabled"] and now >= hw["retry"]:
                try:
                    if not hw["init"] or now - hw["full"] > 30:
                        tx("LCDINIT")
                        hw.update(slots=[None] * 8, rows=[None, None], bl=None, init=True, full=now)
                    if hw["bl"] != cfg["backlight"]:
                        tx("LCDBL %d" % (1 if cfg["backlight"] else 0))
                        hw["bl"] = cfg["backlight"]
                    for i in range(8):
                        if S["slots"][i] and hw["slots"][i] != S["slots"][i]:
                            tx("LCDCHR %d %s" % (i, bytes(S["slots"][i]).hex()))
                            hw["slots"][i] = S["slots"][i]
                    for r in range(2):
                        if hw["rows"][r] != rows[r]:
                            tx("LCDROW %d %s" % (r, bytes(rows[r] + [32] * (16 - len(rows[r]))).hex()))
                            hw["rows"][r] = list(rows[r])
                    st["error"], S["hw"] = False, "LCD ok"
                except Exception as e:
                    hw["init"] = False
                    hw["retry"] = now + 3
                    S["hw"] = "no LCD found (I2C)" if "no_lcd" in str(e) else "Arduino offline: %s" % e
                    st["error"] = "no_lcd" not in str(e)
            elif not cfg["enabled"]:
                S["hw"] = "display disabled"
        except Exception as e:
            ctx["log"].warning("display loop: %s", e)
            time.sleep(1)
        time.sleep(1.0 / max(1, min(8, S["cfg"]["fps"])))


# ---------------- Flask ----------------
def init(app, send_command, live_status, state_lock, base_dir, log):
    ctx.update(send=send_command, live=live_status, lock=state_lock, log=log, base=base_dir,
               file=os.path.join(base_dir, "display_config.json"))
    load()

    @app.route("/display")
    def display_page():
        return send_from_directory(base_dir, "display.html")

    @app.route("/display/api/config", methods=["GET", "POST"])
    def display_config():
        if request.method == "POST":
            new = merge(DEFAULT, request.get_json(force=True))
            for k, t in new["tanks"].items():           # levels are owned by the server
                t["level"] = S["cfg"]["tanks"][k]["level"]
            new["fps"] = max(1, min(8, int(new["fps"])))
            S["cfg"] = new
            save()
        return jsonify({"config": S["cfg"], "stats": S["stats"], "moods": MOODS, "bars": list(BARS)})

    @app.route("/display/api/frame")
    def display_frame():
        f = dict(S["frame"])
        f["levels"] = {k: pct(t) for k, t in S["cfg"]["tanks"].items()}
        return jsonify(f)

    @app.route("/display/api/action", methods=["POST"])
    def display_action():
        d = request.get_json(force=True)
        cfg = S["cfg"]
        if d.get("do") == "refill":
            ids = [str(d["tank"])] if d.get("tank") else list(cfg["tanks"])
            for i in ids:
                cfg["tanks"][i]["level"] = float(cfg["tanks"][i]["capacity"])
        elif d.get("do") == "reset_stats":
            S["stats"].update(pours_today=0, ml_today=0.0, total=0)
        save()
        return jsonify({"ok": True})

    threading.Thread(target=loop, daemon=True).start()
    log.info("LCD display controller ready: /display")
