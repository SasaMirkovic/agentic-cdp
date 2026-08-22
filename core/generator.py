"""Seeded synthetic data generator: four GTM-shaped sources, deliberate mess,
hard negatives, CRM notes, ground truth, and an enrichment reference.

Everything derives from random.Random(seed) — identical output on every machine.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field

import pandas as pd

from core.config import SOURCE_LOADED_AT, Settings

ADJECTIVES = [
    "blue", "vertex", "north", "apex", "prime", "silver", "golden", "iron", "crystal",
    "rapid", "solid", "bright", "clear", "swift", "grand", "royal", "urban", "delta",
    "omega", "alpha", "nova", "terra", "aero", "hydro", "quantum", "stellar", "summit",
    "pioneer", "frontier", "beacon", "harbor", "cascade", "granite", "cobalt", "amber",
    "crimson", "emerald", "onyx", "zenith", "atlas", "orion", "pacific", "atlantic",
    "meridian", "polaris", "aurora", "catalyst", "keystone", "lattice", "monarch",
]

NOUNS = [
    "systems", "dynamics", "solutions", "technologies", "analytics", "networks",
    "software", "industries", "logistics", "materials", "energy", "capital",
    "partners", "ventures", "labs", "works", "manufacturing", "robotics",
    "biotech", "pharma", "media", "digital", "consulting", "engineering",
    "aerospace", "automotive", "textiles", "foods", "beverages", "retail",
    "commerce", "security", "insurance", "financial", "health", "medical",
    "packaging", "chemicals", "plastics", "metals", "mining", "agriculture",
]

SHORT_BRANDS = [
    "acme", "zylo", "kova", "nexo", "vast", "aria", "bolt", "flux", "echo", "onyx",
    "lumo", "rift", "sage", "tide", "volt", "wren", "axis", "core", "dune", "fern",
]

INDUSTRIES = [
    "Software", "Manufacturing", "Logistics", "Financial Services", "Healthcare",
    "Retail", "Energy", "Telecommunications", "Media", "Construction",
    "Pharmaceuticals", "Agriculture",
]

CITIES = [
    ("Austin", "TX"), ("Denver", "CO"), ("Seattle", "WA"), ("Portland", "OR"),
    ("Chicago", "IL"), ("Boston", "MA"), ("Atlanta", "GA"), ("Phoenix", "AZ"),
    ("Dallas", "TX"), ("Columbus", "OH"), ("Raleigh", "NC"), ("Tampa", "FL"),
    ("Minneapolis", "MN"), ("Detroit", "MI"), ("Nashville", "TN"), ("Baltimore", "MD"),
    ("Sacramento", "CA"), ("Pittsburgh", "PA"), ("Cincinnati", "OH"), ("Omaha", "NE"),
]

STREETS = [
    "Main", "Oak", "Maple", "Cedar", "Pine", "Elm", "Washington", "Lake", "Hill",
    "Park", "River", "Spring", "Ridge", "Sunset", "Highland", "Franklin", "Union",
]

STREET_TYPES = ["Street", "Avenue", "Road", "Boulevard", "Drive"]

LEGAL_SUFFIX_VARIANTS = ["Inc.", "Inc", "Corp.", "Corporation", "LLC", "Ltd.", "Co."]

SUBSIDIARY_WORDS = ["Labs", "Logistics", "Media", "Consulting", "Digital", "Services"]

SOURCE_PRESENCE = {"salesforce": 0.90, "sixsense": 0.60, "marketo": 0.55, "xactly": 0.45}

GENERIC_NOTES = [
    "Q{q} renewal discussion scheduled. Champion is the VP of Operations.",
    "Evaluated against two competitors last cycle; pricing was the main objection.",
    "Expansion opportunity flagged by CSM. Waiting on security review.",
    "Procurement requires annual invoicing. Legal review completed in Q{q}.",
    "Intent spike on data-integration keywords over the last 30 days.",
    "Executive sponsor changed roles; re-establishing relationship.",
]


@dataclass
class Entity:
    entity_id: str
    name: str
    domain: str | None
    street: str
    city: str
    state: str
    phone: str
    industry: str
    employees: int
    revenue: float
    kind: str = "normal"  # normal | franchise | shared_domain | same_name
    note_hint: str | None = None
    former_name: str | None = None


@dataclass
class GeneratedData:
    bronze: dict[str, pd.DataFrame] = field(default_factory=dict)
    ground_truth: pd.DataFrame | None = None
    enrichment: pd.DataFrame | None = None
    hard_negative_pairs: pd.DataFrame | None = None


def _mk_domain(name: str, tld: str) -> str:
    return name.lower().replace(" ", "").replace(".", "") + "." + tld


def _mk_phone(rng: random.Random) -> str:
    return f"{rng.randint(201, 989)}{rng.randint(200, 999)}{rng.randint(1000, 9999)}"


def _typo(rng: random.Random, word: str) -> str:
    if len(word) < 4:
        return word
    i = rng.randint(1, len(word) - 3)
    return word[:i] + word[i + 1] + word[i] + word[i + 2:]


def build_entities(settings: Settings, rng: random.Random) -> tuple[list[Entity], list[tuple[str, str]]]:
    """Returns entities plus the list of hard-negative pair (entity_id, entity_id)."""
    entities: list[Entity] = []
    hard_negative_pairs: list[tuple[str, str]] = []
    n = settings.n_companies
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"E{counter:05d}"

    def base_entity(name: str, domain: str | None, kind: str, industry: str | None = None) -> Entity:
        city, state = rng.choice(CITIES)
        return Entity(
            entity_id=next_id(),
            name=name,
            domain=domain,
            street=f"{rng.randint(100, 9999)} {rng.choice(STREETS)} {rng.choice(STREET_TYPES)}",
            city=city,
            state=state,
            phone=_mk_phone(rng),
            industry=industry or rng.choice(INDUSTRIES),
            employees=rng.randint(20, 20000),
            revenue=round(rng.uniform(1.0, 900.0), 1) * 1_000_000,
            kind=kind,
        )

    used_names: set[str] = set()

    def fresh_name() -> str:
        # Two-word combos exhaust below ~2100 names; fall back to three-word
        # forms so any n_companies stays reachable without an unbounded loop.
        for _ in range(20):
            name = f"{rng.choice(ADJECTIVES).title()} {rng.choice(NOUNS).title()}"
            if name not in used_names:
                used_names.add(name)
                return name
        while True:
            n1, n2 = rng.sample(NOUNS, 2)
            name = f"{rng.choice(ADJECTIVES).title()} {n1.title()} {n2.title()}"
            if name not in used_names:
                used_names.add(name)
                return name

    n_franchise_groups = max(2, n // 100)      # ~1% of companies become 2-3 entity groups
    n_shared_domain = max(2, n // 100)
    n_same_name = max(2, n // 100)

    # Franchise groups: same brand, different domains and cities. NOT the same company.
    for _ in range(n_franchise_groups):
        brand = fresh_name()
        group = []
        # Distinct cities per group: a repeated city would mint the same domain
        # for two different entities and silently break stage-1 precision.
        group_cities = rng.sample(CITIES, rng.randint(2, 3))
        for city, state in group_cities:
            e = base_entity(brand, None, "franchise")
            e.city, e.state = city, state
            e.domain = _mk_domain(f"{brand} {city}", "com")
            e.note_hint = f"Franchise location in {city}; not affiliated with other {brand} branches."
            group.append(e)
            entities.append(e)
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                hard_negative_pairs.append((group[i].entity_id, group[j].entity_id))

    # Shared-domain parent/subsidiary: same domain, brand token shared, NOT the same company.
    # Brand lengths deliberately mixed so the Stage-1 guard is tested on both.
    for k in range(n_shared_domain):
        if k % 2 == 0:
            brand = rng.choice(SHORT_BRANDS).title()
            if brand in used_names:
                brand = fresh_name()
            used_names.add(brand)
        else:
            brand = fresh_name()
        domain = _mk_domain(brand, "com")
        parent = base_entity(brand, domain, "shared_domain")
        child_name = f"{brand} {rng.choice(SUBSIDIARY_WORDS)}"
        while child_name in used_names:
            child_name = f"{brand} {rng.choice(SUBSIDIARY_WORDS)} {rng.choice(NOUNS).title()}"
        used_names.add(child_name)
        child = base_entity(child_name, domain, "shared_domain")
        child.city, child.state = parent.city, parent.state
        parent.note_hint = f"Parent entity of the {brand} group; subsidiaries billed separately."
        child.note_hint = f"Subsidiary of {brand}; separate account per policy, shares the {domain} domain."
        entities.extend([parent, child])
        hard_negative_pairs.append((parent.entity_id, child.entity_id))

    # Same name stem, different industry and domain. NOT the same company.
    # Composed names must be registered in used_names, or a later fresh_name()
    # (or another stem group) could mint an identical name for a different entity.
    for _ in range(n_same_name):
        while True:
            stem = fresh_name().split(" ")[0]
            w1, w2 = rng.sample(NOUNS, 2)
            name_a, name_b = f"{stem} {w1.title()}", f"{stem} {w2.title()}"
            if name_a not in used_names and name_b not in used_names:
                used_names.update([name_a, name_b])
                break
        a = base_entity(name_a, _mk_domain(f"{stem}{w1}", "com"), "same_name")
        b = base_entity(name_b, _mk_domain(f"{stem}{w2}", "io"), "same_name")
        a.note_hint = f"No relation to {stem} {w2.title()} — different company."
        entities.append(a)
        entities.append(b)
        hard_negative_pairs.append((a.entity_id, b.entity_id))

    # Normal companies fill the rest.
    while counter < n:
        name = fresh_name()
        e = base_entity(name, _mk_domain(name, rng.choice(["com", "com", "com", "io", "co"])), "normal")
        if rng.random() < 0.05:
            e.former_name = fresh_name()
        entities.append(e)

    return entities, hard_negative_pairs


def _mess_name(rng: random.Random, entity: Entity) -> str:
    roll = rng.random()
    name = entity.name
    if roll < 0.35:
        return name
    if roll < 0.60:
        return f"{name} {rng.choice(LEGAL_SUFFIX_VARIANTS)}"
    if roll < 0.75:
        tokens = name.split(" ")
        i = rng.randrange(len(tokens))
        tokens[i] = _typo(rng, tokens[i])
        return " ".join(tokens)
    if roll < 0.90:
        return name.upper() if rng.random() < 0.5 else name.lower()
    return f"{name} {rng.choice(LEGAL_SUFFIX_VARIANTS)}"


def _mess_domain(rng: random.Random, entity: Entity) -> str | None:
    if entity.domain is None:
        return None
    roll = rng.random()
    if roll < 0.15:
        return None
    if roll < 0.20:
        return f"{rng.choice(['app', 'shop', 'www2'])}.{entity.domain}"
    if roll < 0.25:
        stem = entity.domain.rsplit(".", 1)[0]
        return f"{stem}.{rng.choice(['net', 'org'])}"
    return entity.domain


def _mess_phone(rng: random.Random, phone: str) -> str | None:
    roll = rng.random()
    if roll < 0.20:
        return None
    a, b, c = phone[:3], phone[3:6], phone[6:]
    fmt = rng.choice([f"({a}) {b}-{c}", f"{a}-{b}-{c}", f"+1 {a} {b} {c}", phone])
    return fmt


def _mess_address(rng: random.Random, entity: Entity) -> str | None:
    if rng.random() < 0.25:
        return None
    street = entity.street
    if rng.random() < 0.4:
        street = (
            street.replace("Street", "St").replace("Avenue", "Ave")
            .replace("Road", "Rd").replace("Boulevard", "Blvd").replace("Drive", "Dr")
        )
    return f"{street}, {entity.city}, {entity.state}"


def _make_note(rng: random.Random, entity: Entity) -> str | None:
    if rng.random() >= 0.40:
        return None
    parts = []
    if entity.note_hint and rng.random() < 0.8:
        parts.append(entity.note_hint)
    if entity.former_name and rng.random() < 0.8:
        parts.append(f"Formerly known as {entity.former_name}; rebranded last year.")
    parts.append(rng.choice(GENERIC_NOTES).format(q=rng.randint(1, 4)))
    return " ".join(parts)


def generate(settings: Settings) -> GeneratedData:
    rng = random.Random(settings.seed)
    entities, hard_negative_pairs = build_entities(settings, rng)

    rows_per_source: dict[str, list[dict]] = {s: [] for s in SOURCE_PRESENCE}
    truth_rows: list[dict] = []

    for entity in entities:
        present = [s for s, p in SOURCE_PRESENCE.items() if rng.random() < p]
        if not present:
            present = ["salesforce"]
        for source in present:
            source_record_id = f"{source[:2].upper()}-{len(rows_per_source[source]) + 1:06d}"
            employees = entity.employees if rng.random() > 0.30 else None
            if employees is not None and rng.random() < 0.5:
                employees = int(employees * rng.uniform(0.9, 1.1))
            revenue = entity.revenue if rng.random() > 0.35 else None
            if revenue is not None and rng.random() < 0.5:
                revenue = round(revenue * rng.uniform(0.92, 1.08), 2)
            industry = entity.industry if rng.random() > 0.15 else None
            row = {
                "source_system": source,
                "source_record_id": source_record_id,
                "company_name": _mess_name(rng, entity),
                "domain": _mess_domain(rng, entity),
                "address": _mess_address(rng, entity),
                "phone": _mess_phone(rng, entity.phone),
                "industry": industry,
                "employees": employees,
                "revenue": revenue,
                "notes": _make_note(rng, entity) if source == "salesforce" else None,
                "loaded_at": SOURCE_LOADED_AT[source],
            }
            row["raw_payload"] = json.dumps(
                {k: v for k, v in row.items() if k not in ("raw_payload",)}, default=str
            )
            rows_per_source[source].append(row)
            truth_rows.append(
                {"record_id": f"{source}:{source_record_id}", "entity_id": entity.entity_id}
            )

    bronze = {s: pd.DataFrame(rows) for s, rows in rows_per_source.items()}

    enrichment_rows = []
    for entity in entities:
        if entity.domain is None:
            continue
        h = int(hashlib.sha256(entity.entity_id.encode()).hexdigest(), 16)
        enrichment_rows.append(
            {
                "domain": entity.domain,
                "industry_code": f"SIC-{2000 + h % 800}",
                "employee_band": ["1-50", "51-200", "201-1000", "1001-5000", "5000+"][
                    min(4, entity.employees // 1500)
                ],
                "tech_tags": ",".join(
                    sorted({["crm", "erp", "cloud", "analytics", "ecommerce", "iot"][(h >> s) % 6] for s in (3, 7, 11)})
                ),
            }
        )

    hn = pd.DataFrame(hard_negative_pairs, columns=["entity_a", "entity_b"])
    # Shared-domain subsidiaries produce duplicate domains: enrichment is keyed
    # by domain, so keep one row per domain (first entity wins deterministically).
    enrichment_df = pd.DataFrame(enrichment_rows).drop_duplicates(subset="domain", keep="first")
    return GeneratedData(
        bronze=bronze,
        ground_truth=pd.DataFrame(truth_rows),
        enrichment=enrichment_df,
        hard_negative_pairs=hn,
    )
