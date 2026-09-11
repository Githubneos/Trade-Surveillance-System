"""Realistic names for accounts and securities.

Every name here is invented. The combinations are deliberately fictional so nothing in
this dataset can be mistaken for a real issuer or a real account holder -- the tickers are
generated from the invented company names rather than drawn from any listing.

Names matter more than they look. "Retail Account 00042" trading "ZAV Holdings" reads as a
test fixture, and a reader discounts everything shown next to it. A surveillance case is
read by humans who need to hold the parties in their head while they follow the trades.
"""

from __future__ import annotations

import numpy as np

from surveillance.db.enums import AccountType

# --------------------------------------------------------------------------------------
# Securities
# --------------------------------------------------------------------------------------

_COMPANY_ROOTS = [
    "Halcyon", "Nexadyne", "Cobalt Ridge", "Kestrel", "Meridian", "Ironwood", "Sable Pine",
    "Lumenar", "Ardent", "Verdana", "Basalt", "Northwind", "Quarry Hill", "Silverbrook",
    "Tessera", "Marrow Creek", "Clarion", "Pinnacle Rock", "Auralite", "Bramble",
    "Copperline", "Dunmore", "Elmgrove", "Fathom", "Glasswater", "Harrowgate", "Ivory Lane",
    "Juniper Flats", "Kinsale", "Larkspur", "Mossvale", "Nightingale", "Orchard Bay",
    "Palisade", "Quillon", "Ravenscroft", "Stonebridge", "Thistledown", "Umbercrest",
    "Vantage Point", "Westmarch", "Yarrowfield", "Zephyr Hollow", "Alderwick", "Bellfount",
    "Cindermere", "Drakeholm", "Everly", "Fernhollow", "Grangemouth", "Hollowtree",
    "Inglewood", "Jessamine", "Kirkstall", "Lindenmoor", "Marchwood", "Netherby",
    "Oakhaven", "Pemberton", "Rookwood", "Saltmarsh", "Thornbury", "Wychwood",
]

#: Sector-appropriate suffixes. A utility is not called "Semiconductor".
_SECTOR_SUFFIX: dict[str, list[str]] = {
    "Technology": ["Systems", "Semiconductor", "Microsystems", "Data", "Compute"],
    "Financials": ["Financial", "Capital Group", "Bancorp", "Holdings", "Trust"],
    "Healthcare": ["Biosciences", "Therapeutics", "Health", "Medical", "Diagnostics"],
    "Energy": ["Energy", "Petroleum", "Resources", "Power", "Drilling"],
    "Industrials": ["Industries", "Manufacturing", "Aerospace", "Logistics", "Engineering"],
    "Consumer": ["Brands", "Foods", "Retail Group", "Consumer", "Beverage"],
    "Materials": ["Materials", "Mining", "Chemicals", "Metals", "Minerals"],
    "Utilities": ["Utilities", "Water", "Grid", "Electric", "Gas & Light"],
    "Real Estate": ["Properties", "Realty Trust", "Estates", "Development", "REIT"],
    "Communications": ["Communications", "Media", "Networks", "Broadcasting", "Telecom"],
}


def company_name(rng: np.random.Generator, sector: str, used: set[str]) -> str:
    suffixes = _SECTOR_SUFFIX[sector]
    for _ in range(200):
        name = f"{rng.choice(_COMPANY_ROOTS)} {rng.choice(suffixes)}"
        if name not in used:
            used.add(name)
            return name
    # Exhausted the pairings: fall back to a numbered variant rather than loop forever.
    n = len(used) + 1
    name = f"{rng.choice(_COMPANY_ROOTS)} {rng.choice(suffixes)} {n}"
    used.add(name)
    return name


def ticker_from(name: str, used: set[str]) -> str:
    """Derive a plausible ticker from the company name, as a listing venue would."""
    words = [w for w in name.replace("&", " ").split() if w]
    first, last = words[0], words[-1]
    # Ordered by how a real listing reads: a recognisable stem beats bare initials, and
    # two-letter tickers look like placeholders, so three or four characters only.
    candidates = [
        first[:4],
        first[:3],
        first[:3] + last[0],
        first[:2] + last[:2],
        "".join(w[0] for w in words)[:4],
        first[:2] + last[-1],
        first[0] + last[:3],
    ]
    for raw in candidates:
        c = "".join(ch for ch in raw if ch.isalpha()).upper()
        if 3 <= len(c) <= 4 and c not in used:
            used.add(c)
            return c
    base = "".join(ch for ch in first[:3].upper() if ch.isalpha()) or "XYZ"
    for i in range(1, 100):
        c = f"{base}{i}"[:4]
        if c not in used:
            used.add(c)
            return c
    raise RuntimeError("ticker space exhausted")


# --------------------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------------------

_FIRST = [
    "Amara", "Benedict", "Celeste", "Dmitri", "Eleanor", "Farhan", "Giulia", "Hassan",
    "Imogen", "Jonas", "Keiko", "Lucas", "Mariam", "Niall", "Olivia", "Priya", "Quentin",
    "Rosalind", "Sofia", "Tomasz", "Ursula", "Viktor", "Wren", "Xiomara", "Yusuf", "Zara",
    "Adaeze", "Bjorn", "Camila", "Desmond", "Elias", "Freya", "Gabriel", "Helena", "Idris",
    "Jasmine", "Kwame", "Lorenzo", "Mei", "Nadia", "Oscar", "Paloma", "Rafael", "Saoirse",
    "Theo", "Valentina", "Wendell", "Yara", "Anton", "Beatriz", "Caleb", "Delphine",
]
_LAST = [
    "Okonkwo", "Lindqvist", "Marchetti", "Volkov", "Whitfield", "Rahman", "Ferraro",
    "El-Amin", "Ashworth", "Bergstrom", "Nakamura", "Oyelaran", "Haddad", "Kowalski",
    "Delacroix", "Sundaram", "Voss", "Pemberton", "Castellanos", "Nowak", "Brandt",
    "Petrov", "Calloway", "Vasquez", "Demir", "Larsen", "Achebe", "Sorensen", "Reyes",
    "Fitzgerald", "Novak", "Bianchi", "Mwangi", "Halvorsen", "Costa", "Ibrahim",
    "Lindgren", "Moreau", "Tanaka", "Abernathy", "Quintero", "Steiner", "Oduya",
    "Ravenscroft", "Lindholm", "Serrano", "Beaumont", "Adeyemi", "Kaczmarek", "Rossi",
]

_FIRM_ROOTS = [
    "Northbridge", "Calloway Ridge", "Sable Point", "Ironvale", "Tessellate", "Quaystone",
    "Blackcomb", "Greymoor", "Highfield", "Ashgrove", "Stonefield", "Westbourne",
    "Clearwater", "Arcadia", "Bellweather", "Cranbrook", "Dunhaven", "Eastgate",
    "Fairhaven", "Goldcrest", "Harborview", "Inverness", "Kingsley", "Lancaster",
    "Montrose", "Newgate", "Oakmont", "Pallister", "Redstone", "Sterling Row",
    "Thackeray", "Uplands", "Vantage", "Whitmore", "Yorkfield", "Ambervale",
    "Brookhurst", "Carrington", "Drayton", "Ellesmere",
]

#: Firm suffix by account type. A pension trust does not call itself a "Prop Desk".
_FIRM_SUFFIX: dict[AccountType, list[str]] = {
    AccountType.INSTITUTIONAL: [
        "Pension Trust", "Asset Management", "Investment Trust", "Endowment Fund",
        "Insurance Group", "Sovereign Fund",
    ],
    AccountType.HEDGE_FUND: [
        "Capital", "Partners", "Global Macro", "Absolute Return", "Capital Partners",
        "Alternatives",
    ],
    AccountType.PROP_DESK: [
        "Trading", "Proprietary Trading", "Trading Group", "Principal Trading",
    ],
    AccountType.MARKET_MAKER: [
        "Markets", "Liquidity Partners", "Securities", "Market Making", "Execution Services",
    ],
}


def account_name(rng: np.random.Generator, account_type: AccountType, used: set[str]) -> str:
    """Retail accounts are people; everything else is a firm."""
    if account_type is AccountType.RETAIL:
        for _ in range(200):
            name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
            if name not in used:
                used.add(name)
                return name
        name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)} {len(used)}"
        used.add(name)
        return name

    suffixes = _FIRM_SUFFIX[account_type]
    for _ in range(200):
        name = f"{rng.choice(_FIRM_ROOTS)} {rng.choice(suffixes)}"
        if name not in used:
            used.add(name)
            return name
    name = f"{rng.choice(_FIRM_ROOTS)} {rng.choice(suffixes)} {len(used)}"
    used.add(name)
    return name
