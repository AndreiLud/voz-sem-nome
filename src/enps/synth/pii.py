"""Fake personal data planted in the synthetic comments.

Every value is built so that it cannot point to a real person:
* CPFs always have a wrong first check digit, so no CPF validator accepts them;
* e-mails use the reserved domains example.com and example.org or the fake talvana.example;
* mobile numbers use a valid area code but start with 90, a prefix not used by mobile lines;
* names are common first names and surnames drawn at random, never a real employee list.
"""
from __future__ import annotations

import random
import string

from .bank import fold

VALID_DDDS = (11, 12, 13, 15, 16, 17, 19, 21, 24, 27, 31, 32, 34, 35, 41, 43, 47, 48, 51, 54,
              61, 62, 65, 67, 71, 73, 81, 85, 91, 92)
EMAIL_DOMAINS = ("example.com", "example.org", "talvana.example")
PLATE_LETTERS = "".join(c for c in string.ascii_uppercase if c not in "IOQ")
RG_STATES = ("MG", "SP", "RJ", "BA", "PR")

# Name forms by writing style when the template does not force one.
NAME_FORMS = {
    "formal": {"full": 0.6, "first": 0.4},
    "standard": {"full": 0.35, "first": 0.65},
    "casual": {"full": 0.2, "first": 0.5, "lower": 0.2, "lower_full": 0.1},
    "informal": {"full": 0.1, "first": 0.35, "lower": 0.4, "lower_full": 0.15},
}


def cpf_check_digits(base: list[int]) -> tuple[int, int]:
    """The two CPF check digits for nine base digits."""
    first = sum(d * w for d, w in zip(base, range(10, 1, -1))) * 10 % 11 % 10
    second = sum(d * w for d, w in zip([*base, first], range(11, 1, -1))) * 10 % 11 % 10
    return first, second


def is_valid_cpf(text: str) -> bool:
    """True only for a CPF whose check digits are right (and not all digits equal)."""
    digits = [int(c) for c in text if c.isdigit()]
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    return cpf_check_digits(digits[:9]) == (digits[9], digits[10])


class FakeData:
    """Draws fake values for the template slots. Lists are cycled so names rarely repeat."""

    def __init__(self, rng: random.Random, people: dict[str, tuple[str, ...]],
                 places: dict[str, tuple[str, ...]]) -> None:
        self._rng = rng
        self._people = people
        self._places = places
        self._queues: dict[str, list[str]] = {}
        # Last name written in the template (surname None for a first name alone), reused by
        # an e-mail so "falem com a Priscila no ..." gets priscila...@ and not someone else.
        self._person: tuple[str, str | None] | None = None

    def forget_person(self) -> None:
        """Called at the start of each template, so an e-mail only reuses a name from the
        same sentence."""
        self._person = None

    def value(self, slot: str, variant: str | None, style: str) -> str:
        draw = {
            "PESSOA_F": lambda: self.person("female", variant, style),
            "PESSOA_M": lambda: self.person("male", variant, style),
            "PESSOA_WN": lambda: self.person("word_names", variant, style),
            "CIDADE": lambda: self._place(self._next("cities", self._places["cities"]), style),
            "BAIRRO": lambda: self._place(self._next("neighborhoods", self._places["neighborhoods"]), style),
            "RUA": lambda: self.street(style),
            "EMAIL": self.email,
            "TELEFONE": self.phone,
            "CPF": self.cpf,
            "RG": self.rg,
            "CEP": self.cep,
            "MATRICULA": self.matricula,
            "PLACA": lambda: self.plate(style),
        }
        return draw[slot]()

    def _next(self, name: str, items: tuple[str, ...]) -> str:
        queue = self._queues.get(name)
        if not queue:
            queue = list(items)
            self._rng.shuffle(queue)
            self._queues[name] = queue
        return queue.pop()

    def _pick(self, weights: dict[str, float]) -> str:
        keys = list(weights)
        return self._rng.choices(keys, weights=[weights[k] for k in keys])[0]

    def person(self, pool: str, variant: str | None, style: str) -> str:
        first = self._next(pool, self._people[pool])
        form = variant or self._pick(NAME_FORMS[style])
        if form in ("first", "lower"):
            self._person = (first, None)
            return first if form == "first" else fold(first)
        surname = self._next("surnames", self._people["surnames"])
        self._person = (first, surname)
        full = f"{first} {surname}"
        if self._rng.random() < 0.2:
            full += f" {self._next('surnames', self._people['surnames'])}"
        return fold(full) if form == "lower_full" else full

    def _place(self, name: str, style: str) -> str:
        if style == "informal" and self._rng.random() < 0.4:
            return fold(name)
        return name

    def street(self, style: str) -> str:
        kind = self._rng.choice(self._places["street_kinds"])
        name = self._next("streets", self._places["street_names"])
        number = self._rng.randint(12, 2400)
        if style in ("casual", "informal") and self._rng.random() < 0.35:
            return fold(f"{kind} {name} {number}")
        return f"{kind} {name}, {number}"

    def email(self) -> str:
        if self._person:
            first = fold(self._person[0]).replace(" ", "")
            surname = self._person[1] or self._next("email_last", self._people["surnames"])
            last = fold(surname).replace(" ", "")
        else:
            first = fold(self._next("email_first", self._people["female"] + self._people["male"]))
            first = first.replace(" ", "")
            last = fold(self._next("email_last", self._people["surnames"]))
        pattern = self._rng.randrange(5)
        local = (
            f"{first}.{last}",
            f"{first}{last}{self._rng.randint(10, 99)}",
            f"{first[0]}.{last}",
            f"{first}_{last}",
            f"{first}{self._rng.randint(1, 999)}",
        )[pattern]
        return f"{local}@{self._rng.choice(EMAIL_DOMAINS)}"

    def phone(self) -> str:
        ddd = self._rng.choice(VALID_DDDS)
        head = f"90{self._rng.randint(0, 999):03d}"
        tail = f"{self._rng.randint(0, 9999):04d}"
        form = self._rng.choices(range(4), weights=(4, 3, 1, 2))[0]
        return (f"({ddd}) {head}-{tail}", f"{ddd} {head}-{tail}", f"({ddd}){head}-{tail}",
                f"{ddd}{head}{tail}")[form]

    def cpf(self) -> str:
        while True:
            base = [self._rng.randrange(10) for _ in range(9)]
            if len(set(base)) > 1:
                break
        right_first, _ = cpf_check_digits(base)
        wrong_first = (right_first + self._rng.randint(1, 9)) % 10
        digits = "".join(map(str, [*base, wrong_first, self._rng.randrange(10)]))
        if self._rng.random() < 0.6:
            return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
        return digits

    def rg(self) -> str:
        number = f"{self._rng.randint(10, 59)}{self._rng.randint(0, 999999):06d}"
        check = "X" if self._rng.random() < 0.1 else str(self._rng.randrange(10))
        dotted = f"{number[:2]}.{number[2:5]}.{number[5:]}"
        form = self._rng.choices(range(3), weights=(5, 3, 2))[0]
        return (f"{dotted}-{check}", f"{number}-{check}",
                f"{self._rng.choice(RG_STATES)}-{dotted}")[form]

    def cep(self) -> str:
        head = f"{self._rng.randint(1000, 99999):05d}"
        tail = f"{self._rng.randint(0, 999):03d}"
        return f"{head}-{tail}" if self._rng.random() < 0.75 else f"{head}{tail}"

    def matricula(self) -> str:
        number = str(self._rng.randint(10000, 99999))
        return f"0{number}" if self._rng.random() < 0.3 else number

    def plate(self, style: str) -> str:
        letters = "".join(self._rng.choice(PLATE_LETTERS) for _ in range(3))
        if self._rng.random() < 0.5:
            value = f"{letters}-{self._rng.randint(1000, 9999)}"
        else:
            value = (f"{letters}{self._rng.randrange(10)}{self._rng.choice(PLATE_LETTERS)}"
                     f"{self._rng.randint(0, 99):02d}")
        if style in ("casual", "informal") and self._rng.random() < 0.2:
            return value.lower()
        return value
