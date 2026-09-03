"""Human-facing names, printed one way everywhere.

Cities reach the board from three sources in three spellings: the schedule
API's municipality ("Seoul-si", "Taipei City", "Roissy-en-France"), the
route database's municipality ("Seoul", "Paris") and, for the tracker, the
airport record itself. Left alone, one trip read "Copenhagen → Seoul" on
one row and "Seoul-si → Taipei City" on the next — the same two cities,
spelt by whichever source happened to answer. Every renderer therefore
prints cities through `city()` here, which decides in this order:

  1. the airport override — the city an airport serves, not the suburb
     the runway sits in (Incheon → Seoul, Kloten → Zurich, Fiumicino →
     Rome). Distant fields marketed under a metropolis (Bergamo,
     Beauvais, Charleroi) are deliberately NOT here: the passenger lands
     in Bergamo, and the frame is not the airline's brochure;
  2. English exonyms for the local spellings the APIs prefer;
  3. generic cleanup of administrative dressing — Korean "-si", Japanese
     "-shi", "Metropolitan City", a bare " City" (except where it is the
     name: Mexico City), and the "-am-Main" / "upon Tyne" connectives.

Aircraft names get the same treatment in `aircraft()`: the manufacturers'
own styling ("A320neo", no marketing suffixes), so six queue rows read as
if one hand wrote them. New cities need nothing; a new quirk needs one
table line, here, and every design follows.
"""
from __future__ import annotations

import re

# -- 1. airports whose municipality record is not the city people fly to ----
AIRPORT_CITY: dict[str, str] = {
    # East Asia: city airports carry the district, hubs the county
    "GMP": "Seoul", "ICN": "Seoul", "PUS": "Busan", "CJU": "Jeju",
    "TSA": "Taipei", "TPE": "Taipei", "KHH": "Kaohsiung",
    "NRT": "Tokyo", "HND": "Tokyo", "KIX": "Osaka", "ITM": "Osaka",
    "NGO": "Nagoya", "CTS": "Sapporo", "FUK": "Fukuoka", "OKA": "Okinawa",
    "PEK": "Beijing", "PKX": "Beijing", "PVG": "Shanghai", "SHA": "Shanghai",
    "CAN": "Guangzhou", "SZX": "Shenzhen", "HKG": "Hong Kong", "MFM": "Macau",
    # South-East and South Asia
    "BKK": "Bangkok", "DMK": "Bangkok", "KUL": "Kuala Lumpur",
    "SIN": "Singapore", "CGK": "Jakarta", "MNL": "Manila", "CEB": "Cebu",
    "SGN": "Ho Chi Minh City", "HAN": "Hanoi", "DAD": "Da Nang",
    "DEL": "Delhi", "BOM": "Mumbai", "BLR": "Bengaluru", "CMB": "Colombo",
    # Middle East
    "DXB": "Dubai", "DWC": "Dubai", "AUH": "Abu Dhabi", "DOH": "Doha",
    "IST": "Istanbul", "SAW": "Istanbul", "TLV": "Tel Aviv", "CAI": "Cairo",
    "JED": "Jeddah", "RUH": "Riyadh", "KWI": "Kuwait City", "BAH": "Bahrain",
    # Europe: the field is named for its village
    "CDG": "Paris", "ORY": "Paris", "AMS": "Amsterdam", "BRU": "Brussels",
    "ZRH": "Zurich", "GVA": "Geneva", "BSL": "Basel", "MLH": "Basel",
    "EAP": "Basel", "VIE": "Vienna", "MUC": "Munich", "FRA": "Frankfurt",
    "CPH": "Copenhagen", "ARN": "Stockholm", "BMA": "Stockholm",
    "OSL": "Oslo", "HEL": "Helsinki", "KEF": "Reykjavik",
    "FCO": "Rome", "CIA": "Rome", "MXP": "Milan", "LIN": "Milan",
    "VCE": "Venice", "VRN": "Verona", "FLR": "Florence", "NAP": "Naples",
    "TRN": "Turin", "GOA": "Genoa", "TRS": "Trieste", "CTA": "Catania",
    "PMO": "Palermo", "CAG": "Cagliari", "BLQ": "Bologna",
    "MAD": "Madrid", "BCN": "Barcelona", "PMI": "Palma", "AGP": "Malaga",
    "SVQ": "Seville", "VLC": "Valencia", "ALC": "Alicante", "IBZ": "Ibiza",
    "LPA": "Las Palmas", "TFS": "Tenerife", "TFN": "Tenerife",
    "LIS": "Lisbon", "OPO": "Porto", "ATH": "Athens", "SKG": "Thessaloniki",
    "HER": "Heraklion", "CFU": "Corfu", "RHO": "Rhodes",
    "LYS": "Lyon", "NCE": "Nice", "MRS": "Marseille", "TLS": "Toulouse",
    "BOD": "Bordeaux", "NTE": "Nantes", "SXB": "Strasbourg",
    "LEJ": "Leipzig", "DUS": "Dusseldorf", "CGN": "Cologne", "HAJ": "Hanover",
    "PRG": "Prague", "BUD": "Budapest", "WAW": "Warsaw", "KRK": "Krakow",
    "OTP": "Bucharest", "BEG": "Belgrade", "KBP": "Kyiv", "RIX": "Riga",
    "SEN": "Southend", "EDI": "Edinburgh", "GLA": "Glasgow", "PIK": "Glasgow",
    # Americas
    "JFK": "New York", "LGA": "New York", "EWR": "Newark",
    "ORD": "Chicago", "MDW": "Chicago", "IAD": "Washington", "DCA": "Washington",
    "DFW": "Dallas", "DAL": "Dallas", "IAH": "Houston", "HOU": "Houston",
    "LAX": "Los Angeles", "SFO": "San Francisco", "MIA": "Miami",
    "TPA": "Tampa", "MCO": "Orlando", "PHL": "Philadelphia", "BOS": "Boston",
    "ATL": "Atlanta", "SEA": "Seattle", "DEN": "Denver", "LAS": "Las Vegas",
    "PHX": "Phoenix", "SLC": "Salt Lake City", "MSP": "Minneapolis",
    "DTW": "Detroit", "CLT": "Charlotte", "YYZ": "Toronto", "YTZ": "Toronto",
    "YUL": "Montreal", "YVR": "Vancouver", "MEX": "Mexico City",
    "NLU": "Mexico City", "CUN": "Cancún", "GRU": "São Paulo",
    "CGH": "São Paulo", "GIG": "Rio de Janeiro", "SDU": "Rio de Janeiro",
    "EZE": "Buenos Aires", "AEP": "Buenos Aires", "SCL": "Santiago",
    "LIM": "Lima", "BOG": "Bogotá", "PTY": "Panama City",
    # Africa and Oceania
    "JNB": "Johannesburg", "CPT": "Cape Town", "NBO": "Nairobi",
    "ADD": "Addis Ababa", "CMN": "Casablanca", "RAK": "Marrakesh",
    "SYD": "Sydney", "MEL": "Melbourne", "BNE": "Brisbane", "PER": "Perth",
    "AKL": "Auckland",
}

# -- 2. local spellings -> the English name ---------------------------------
# Identity entries protect names the generic rules would otherwise clip
# ("Rio de Janeiro" is not "Rio"). Keyed on the first comma/slash segment.
CITY_EN: dict[str, str] = {
    # German-speaking
    "Bâle": "Basel", "Genève": "Geneva", "Zürich": "Zurich",
    "München": "Munich", "Köln": "Cologne", "Nürnberg": "Nuremberg",
    "Düsseldorf": "Dusseldorf", "Hannover": "Hanover", "Wien": "Vienna",
    # Benelux and Nordics
    "Bruxelles": "Brussels", "Brussel": "Brussels", "Antwerpen": "Antwerp",
    "Den Haag": "The Hague", "København": "Copenhagen",
    "Göteborg": "Gothenburg", "Malmö": "Malmo", "Reykjavík": "Reykjavik",
    # Central and Eastern Europe
    "Praha": "Prague", "Warszawa": "Warsaw", "Kraków": "Krakow",
    "Gdańsk": "Gdansk", "Wrocław": "Wroclaw", "Poznań": "Poznan",
    "Łódź": "Lodz", "Bucureşti": "Bucharest", "București": "Bucharest",
    "Beograd": "Belgrade", "Moskva": "Moscow", "Sankt-Peterburg": "St Petersburg",
    "Kiev": "Kyiv", "Athina": "Athens", "Athínai": "Athens",
    "Thessaloníki": "Thessaloniki", "Irakleio": "Heraklion",
    "Iraklion": "Heraklion", "Kerkyra": "Corfu", "Rodos": "Rhodes",
    "İstanbul": "Istanbul", "İzmir": "Izmir",
    # Iberia and Italy
    "Lisboa": "Lisbon", "Sevilla": "Seville", "Málaga": "Malaga",
    "Córdoba": "Cordoba", "València": "Valencia", "Alacant": "Alicante",
    "Eivissa": "Ibiza", "Maó": "Mahon", "Donostia": "San Sebastian",
    "Donostia-San Sebastián": "San Sebastian", "Palma de Mallorca": "Palma",
    "Roma": "Rome", "Milano": "Milan", "Venezia": "Venice",
    "Napoli": "Naples", "Firenze": "Florence", "Torino": "Turin",
    "Genova": "Genoa", "Padova": "Padua", "Mantova": "Mantua",
    # Britain
    "Kingston upon Hull": "Hull",
    # Middle East and Asia
    "Tel Aviv-Yafo": "Tel Aviv", "Al Qāhirah": "Cairo", "Al Qahirah": "Cairo",
    "Jiddah": "Jeddah", "Al Kuwayt": "Kuwait City", "Bayrūt": "Beirut",
    "Hà Nội": "Hanoi", "Ha Noi": "Hanoi", "Hồ Chí Minh": "Ho Chi Minh City",
    "Thành phố Hồ Chí Minh": "Ho Chi Minh City", "Đà Nẵng": "Da Nang",
    "Krung Thep": "Bangkok", "Krung Thep Maha Nakhon": "Bangkok",
    "Bombay": "Mumbai", "Peking": "Beijing", "Pusan": "Busan",
    "Tōkyō": "Tokyo", "Ōsaka": "Osaka", "Kyōto": "Kyoto",
    "Xianggang": "Hong Kong", "Aomen": "Macau", "Macao": "Macau",
    "Taibei": "Taipei", "Gaoxiong": "Kaohsiung",
    # Americas: names the connective rule must leave whole
    "Rio de Janeiro": "Rio de Janeiro", "Mar del Plata": "Mar del Plata",
    "Ciudad de México": "Mexico City", "Ciudad de Panamá": "Panama City",
}

# " City" is dressing on "Taipei City" and the name itself on these.
_KEEP_CITY = {
    "Mexico City", "Ho Chi Minh City", "Kansas City", "Salt Lake City",
    "Oklahoma City", "Panama City", "Kuwait City", "Guatemala City",
    "Belize City", "Quezon City", "Jersey City", "Carson City",
    "Atlantic City", "Vatican City", "Dodge City", "Iowa City",
}

# -- 3. generic dressing -----------------------------------------------------
# Administrative suffixes: Korean -si/-gun/-gu, Japanese -shi/-ku, Chinese
# " Shi", the Korean "Metropolitan"/"Special" city forms, and a bare
# " City". Anchored at the end; the loop below strips repeatedly so
# "Busan Metropolitan City" and "Seoul Special City" both come clean.
_SUFFIX = re.compile(
    r"(?:[-\s](?:si|gun|gu|shi|ku|cho|machi)"
    r"|\s+(?:special\s+self-governing|special|metropolitan)\s+city"
    r"|\s+shi|\s+city)\s*$", re.IGNORECASE)

# "Frankfurt-am-Main", "Newcastle upon Tyne", "Aix-en-Provence",
# "Santa Cruz de Tenerife": keep the head, drop the geography lesson.
_CONNECTIVE = re.compile(
    r"^(.{3,}?)[-\s](?:am|an\s+der|an|im|auf|bei|upon|on|sur|en|de)[-\s].+$",
    re.IGNORECASE)


def _first_segment(raw: str) -> str:
    """Municipality fields carry things like "Paisley, Renfrewshire" and
    "Bâle/Mulhouse (Euroairport)"."""
    return raw.split(",")[0].split("/")[0].split(" (")[0].strip()


def city(raw: str | None, iata: str | None = None) -> str:
    """The city as the board prints it; "" when nothing is known."""
    code = (iata or "").strip().upper()
    if code in AIRPORT_CITY:
        return AIRPORT_CITY[code]
    if not raw:
        return ""
    name = " ".join(str(raw).split())
    if not name:
        return ""
    name = _first_segment(name)
    if name in CITY_EN:
        return CITY_EN[name]
    if name in _KEEP_CITY:
        return name
    # Strip dressing, then look the bare name up again: "Seoul-si" and
    # "Taibei City" both reach their exonym only once undressed.
    for _ in range(3):
        bare = _SUFFIX.sub("", name).strip(" -")
        if bare == name or len(bare) < 2:
            break
        name = bare
        if name in _KEEP_CITY:
            return name
    m = _CONNECTIVE.match(name)
    if m and name not in _KEEP_CITY:
        name = m.group(1).strip(" -")
    return CITY_EN.get(name, name)


def short_city(raw: str | None, limit: int = 14,
               iata: str | None = None) -> str:
    """`city()` bounded for a queue row. Cuts at a word boundary when one
    leaves enough behind ("Frankfurt" over "Frankfurt-a…"). Bounded: a
    lone connective in an older shortener once looped forever and pinned
    the droplet at 100% CPU for an hour."""
    name = city(raw, iata)
    if len(name) <= limit:
        return name
    head = name[: limit - 1]
    best = ""
    for sep in (" ", "-"):
        if sep in head:
            best = max(best, head.rsplit(sep, 1)[0].rstrip(" -"), key=len)
    return best if len(best) >= 4 else head.rstrip() + "…"


# -- aircraft ---------------------------------------------------------------
# Schedule APIs shout ("Airbus A320 NEO") and sell ("Boeing 787-9
# Dreamliner"); the manufacturers write A320neo and 787-9.
_PAREN = re.compile(r"\s*\([^)]*\)")
_MARKETING = re.compile(r"\s+(?:Dreamliner|Superjumbo|Intercontinental)\b",
                        re.IGNORECASE)
_NEO = re.compile(r"\b(A3\d{2}(?:-\d{3,4})?)[\s-]*NEO\b", re.IGNORECASE)
_MAX = re.compile(r"\bMAX[\s-]*(\d+)\b", re.IGNORECASE)


def aircraft(model: str | None) -> str | None:
    """"Airbus A320 NEO" -> "Airbus A320neo"; None stays None."""
    if not model:
        return None
    s = " ".join(str(model).split())
    s = _PAREN.sub("", s)
    s = _MARKETING.sub("", s)
    # "A320 NEO" -> "A320neo"; a dash variant ("A330-900 NEO") already
    # names the neo generation, so the suffix is simply redundant.
    s = _NEO.sub(lambda m: m.group(1) if "-" in m.group(1)
                 else m.group(1) + "neo", s)
    s = _MAX.sub(lambda m: f"MAX {m.group(1)}", s)
    s = s.replace("Airbus Industrie", "Airbus")
    return s.strip() or None
