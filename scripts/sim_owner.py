"""The scripted owner for simulator runs with a real model.

Facts are drawn from small pools with a seeded random, told once in plain words, sometimes changed
later, and asked about after each fold of the session. A probe's answer is scored by code
(substring match on the distinctive words), so "did it remember?" is a number per probe.
Chatter between the facts never contains a fact's words. Same seed, same owner."""
from __future__ import annotations

import random

CITIES = ["Lisbon", "Kyoto", "Oaxaca", "Tallinn", "Porto", "Valparaiso", "Hanoi", "Ljubljana", "Cusco", "Tbilisi",
          "Marrakesh", "Bergen"]
MONTHS = ["March", "April", "May", "June", "July", "September", "October"]
TITLES = ["The Overstory", "Piranesi", "Gilead", "Middlemarch", "Kitchen Confidential", "The Dispossessed",
          "Austerlitz", "Stoner", "Annihilation", "The Passenger"]
LENDERS = ["Dana", "Marcus", "Priya", "Tomas", "Ingrid", "Beto", "Hana", "Wilhelm", "Odalys", "Femi"]
AREAS = ["Alfama", "Baixa", "Gion", "Jericoacoara", "Kazimierz", "Trastevere", "Montmartre", "Plaka"]
PLACES = ["Oakline Dental", "Harbor Vet Clinic", "Dr. Reyes's office", "Eastside Eye Care", "Brightwater Physio",
          "Corner Street Clinic", "the Maple Avenue clinic", "Lakeview Orthodontics"]
KINDS = ["dentist", "physio", "eye test", "orthodontist", "check-up"]
PETS = ["Biscuit", "Juniper", "Waffles", "Ozzy", "Pickle", "Mochi", "Clementine", "Bruno", "Noodle", "Pepper"]
ALLERGENS = ["chicken", "wheat", "beef", "dust mites", "grass pollen", "fleas", "corn", "soy"]
SISTERS = ["Marlene", "Colette", "Odette", "Greta", "Noor", "Yvette", "Rosalind", "Anneke", "Philippa", "Zadie"]
PROJECTS = ["Heron", "Quillback", "Lantern", "Marrow", "Tamarind", "Cobalt", "Fennel", "Sorrel", "Thistle", "Basalt"]
HOURS = [8, 9, 10, 11]
TIMES = [("9:30", ["9:30", "9.30"]), ("11:15", ["11:15", "11.15"]), ("2pm", ["2pm", "2 pm", "14:00", "2:00", "2 p.m."]),
         ("4:45", ["4:45", "4.45", "16:45"])]

CHATTER = [
    "Morning. How's it going over there?",
    "Can you explain the difference between a mortgage rate and an APR, briefly?",
    "I can't decide what to cook tonight. Something with eggs and not too much washing up.",
    "Give me a one-line pep talk, I've got a dull afternoon of spreadsheets ahead.",
    "What's a good way to stop my laptop fan from spinning up all the time?",
    "Is it worth learning to touch type at my age? Be honest.",
    "Look up whether the local library still does the Saturday book sale.",
    "Quick one: how many ounces in a cup, roughly?",
    "I keep waking up at 4am. Any sane suggestions that aren't just 'sleep hygiene'?",
    "Draft a polite two-line email asking my landlord to fix the hallway light.",
    "What's the capital of Australia? I'm losing an argument.",
    "Summarize the plot of Hamlet in three sentences, as if you were bored by it.",
    "How long does it take to hard-boil an egg so the yolk isn't gray?",
    "Thanks, that helped.",
    "Any ideas for a cheap birthday gift for a coworker who likes plants?",
    "Tell me something interesting about octopuses.",
    "My bike chain keeps slipping. Is that the chain or the cassette?",
    "Can you check what's the weather doing this weekend?",
    "Rewrite this to sound less grumpy: 'Per my last email, the report is late again.'",
    "I need a name for a playlist of rainy-day songs.",
    "What's a reasonable amount to tip at a cafe where you order at the counter?",
    "How do I get a red wine stain out of a cotton shirt?",
    "Remind me what a Roth IRA is. Just the gist.",
    "Do you think I should repaint the kitchen or just live with it?",
    "Find me a decent recipe for lentil soup that doesn't take two hours.",
    "What are the odds, roughly, of rolling at least one six on three dice?",
    "I'm bored. Give me a riddle.",
    "Can you convert 72 degrees Fahrenheit to Celsius?",
    "Why do cats knead blankets?",
    "Suggest three podcasts for a long drive, one of them funny.",
    "Is it normal for a phone battery to lose 10 percent overnight?",
    "Help me word a text declining a party invite without sounding rude.",
    "What's the difference between baking soda and baking powder?",
    "Give me a five-minute stretching routine for a sore neck.",
    "Look up when the next full moon is.",
    "How do you pronounce 'quinoa' and why is it like that?",
    "Short pep talk again please, the spreadsheets won.",
    "What should I know before buying a used electric kettle? Anything?",
    "I've got twenty minutes. What can I tidy that makes the biggest difference?",
    "Explain what an API is to someone who's only used toasters.",
    "Is there a word for the smell of rain on dry ground?",
    "Good night, talk tomorrow.",
]


class Owner:
    """One simulated owner. next(folds) gives the next message and what it was; on_fold() queues questions."""

    def __init__(self, seed: int, p_fact: float = 0.2, p_update: float = 0.06, probes_per_fold: int = 4):
        self.rng = random.Random(seed * 7919 + 13)
        self.p_fact, self.p_update, self.probes_per_fold = p_fact, p_update, probes_per_fold
        self.facts: list[dict] = []
        self.queue: list[dict] = []
        self.sent = 0
        self.sources = {"city": CITIES, "title": TITLES, "lender": LENDERS, "area": AREAS, "place": PLACES,
                        "pet": PETS, "allergen": ALLERGENS, "sister": SISTERS, "project": PROJECTS}
        self.pools = {name: [] for name in self.sources}
        self.kinds = ["trip", "book", "quiet", "lodging", "appointment", "pet", "birthday", "project"]
        self.rng.shuffle(self.kinds)
        self.chatter = list(CHATTER)
        self.rng.shuffle(self.chatter)

    # --- drawing ------------------------------------------------------------------------

    def _pick(self, name: str) -> str:
        """Without replacement: a pool is only reshuffled once every entry in it has been used."""
        if not self.pools[name]:
            self.pools[name] = list(self.sources[name])
            self.rng.shuffle(self.pools[name])
        return self.pools[name].pop()

    def _day(self, avoid=()):
        while True:
            d = self.rng.randint(3, 27)
            if d not in avoid:
                return d

    def _new_fact(self) -> dict:
        kind = self.kinds[len([f for f in self.facts]) % len(self.kinds)]
        r = self.rng
        n = len(self.facts)
        if kind == "trip":
            city, month = self._pick("city"), r.choice(MONTHS)
            s = self._day()
            e = s + r.randint(4, 8)
            e = e if e <= 30 else 30
            s2 = self._day(avoid=(s, e))
            e2 = min(30, s2 + r.randint(4, 8))
            return {"id": n, "kind": kind, "told": f"I'm planning a trip to {city} in {month}, probably the {s}th to the "
                    f"{e}th. Can you keep that in mind?",
                    "probe": r.choice(["Remind me where I'm going on that trip, and the dates?",
                                       "What were the dates of my big trip, and where to?"]),
                    "expect": [[city.lower()], [str(s)], [str(e)]],
                    "update_told": f"Change of plan on the {city} trip: now the {s2}th to the {e2}th.",
                    "update_expect": [[city.lower()], [str(s2)], [str(e2)]]}
        if kind == "book":
            title, lender, month = self._pick("title"), self._pick("lender"), r.choice(MONTHS)
            d, d2 = self._day(), self._day()
            return {"id": n, "kind": kind, "told": f"I borrowed '{title}' from my neighbor {lender}. I have to give it back "
                    f"by {month} {d}.",
                    "probe": r.choice(["Who did I borrow that book from, and what's the book called?",
                                       "What's the deal with the book I borrowed: title, who from?"]),
                    "expect": [[title.lower()], [lender.lower()]],
                    "update_told": f"{lender} says there's no rush on '{title}'. The new return date is {month} {d2}.",
                    "update_expect": [[title.lower()], [lender.lower()], [str(d2)]]}
        if kind == "quiet":
            h = r.choice(HOURS)
            h2 = r.choice([x for x in HOURS if x != h])
            ways = lambda x: [f"{x}am", f"{x} am", f"{x}:00", f"{x} a.m.", f"{x} o'clock"]
            return {"id": n, "kind": kind, "told": f"From now on, please don't message me before {h}am unless something is "
                    f"on fire.", "probe": "What are my rules about when you're allowed to message me?",
                    "expect": [ways(h)],
                    "update_told": f"Actually make that {h2}am for the quiet hours, not {h}.",
                    "update_expect": [ways(h2)]}
        if kind == "lodging":
            area, price = self._pick("area"), r.choice([90, 110, 120, 140, 160])
            return {"id": n, "kind": kind, "told": f"When I travel I want a small hotel in {area}, under {price} euros a "
                    f"night. No hostels.", "probe": "What lodging do I like when I travel: where and what budget?",
                    "expect": [[area.lower()], [str(price)]], "update_told": None, "update_expect": None}
        if kind == "appointment":
            place, k, month = self._pick("place"), r.choice(KINDS), r.choice(MONTHS)
            d = self._day()
            label, forms = r.choice(TIMES)
            return {"id": n, "kind": kind, "told": f"I have a {k} appointment at {place} on {month} {d} at {label}.",
                    "probe": f"When's my {k} appointment, and where?",
                    "expect": [[place.lower()], [str(d)], forms], "update_told": None, "update_expect": None}
        if kind == "pet":
            pet, thing = self._pick("pet"), self._pick("allergen")
            return {"id": n, "kind": kind, "told": f"My dog is called {pet} and she's allergic to {thing}.",
                    "probe": "What's my dog's name again, and what is she allergic to?",
                    "expect": [[pet.lower()], [thing.lower()]], "update_told": None, "update_expect": None}
        if kind == "birthday":
            name, month = self._pick("sister"), r.choice(MONTHS)
            d, d2 = self._day(), self._day()
            return {"id": n, "kind": kind, "told": f"My sister {name}'s birthday is on {month} {d}. I always forget.",
                    "probe": "When is my sister's birthday, and what's her name?",
                    "expect": [[name.lower()], [str(d)]],
                    "update_told": f"Correction: {name}'s birthday is actually on {month} {d2}, I had it wrong.",
                    "update_expect": [[name.lower()], [str(d2)]]}
        word = self._pick("project")
        return {"id": n, "kind": "project", "told": f"We're calling the garden project '{word}' from now on. The old name "
                f"is retired.", "probe": "What did we decide to call the garden project?",
                "expect": [[word.lower()]], "update_told": None, "update_expect": None}

    # --- the owner's turn ---------------------------------------------------------------

    def next(self, folds: int) -> tuple[str, dict]:
        """The next message and a description of it: {"kind": chat|tell|update|probe, "fact": id or None}."""
        self.sent += 1
        if self.queue:
            fact = self.queue.pop(0)
            return fact["probe"], {"kind": "probe", "fact": fact["id"], "expect": fact["expect"],
                                   "age_folds": folds - fact["told_fold"], "updated": fact["updated"]}
        if self.sent == 1:
            return self.chatter.pop(0), {"kind": "chat", "fact": None}
        roll = self.rng.random()
        changeable = [f for f in self.facts if f["update_told"] and not f["updated"] and f["told_fold"] < folds]
        if changeable and roll < self.p_update:
            fact = self.rng.choice(changeable)
            fact["updated"] = True
            fact["expect"], fact["told_fold"] = fact["update_expect"], folds
            return fact["update_told"], {"kind": "update", "fact": fact["id"]}
        if roll < self.p_update + self.p_fact:
            fact = self._new_fact()
            fact.update(told_fold=folds, updated=False, probes=0)
            self.facts.append(fact)
            return fact["told"], {"kind": "tell", "fact": fact["id"]}
        if not self.chatter:
            self.chatter = list(CHATTER)
            self.rng.shuffle(self.chatter)
        return self.chatter.pop(0), {"kind": "chat", "fact": None}

    def on_fold(self, folds: int) -> None:
        """After a fold, queue questions about facts told before it: the oldest least-asked ones, and the newest."""
        asked = {f["id"] for f in self.queue}
        old = sorted((f for f in self.facts if f["told_fold"] < folds and f["id"] not in asked),
                     key=lambda f: (f["probes"], f["id"]))
        picks = old[:max(0, self.probes_per_fold - 1)]
        recent = [f for f in self.facts if f["told_fold"] < folds and f not in picks and f["id"] not in asked]
        if recent:
            picks.append(recent[-1])
        for fact in picks[:self.probes_per_fold]:
            fact["probes"] += 1
            self.queue.append(fact)


def score(expect: list[list[str]], text: str) -> bool:
    """Every group needs at least one of its spellings somewhere in the answer."""
    low = (text or "").lower()
    return bool(expect) and all(any(s.lower() in low for s in group) for group in expect)
