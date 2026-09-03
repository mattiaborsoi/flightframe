"""Airline brand colours, mapped onto the six available inks.

A deliberate limit: this uses brand *colours*, which are facts, not livery
artwork, which is trademarked and copyrighted. Reproducing Emirates' tail or
Cathay's brushwing would mean either licensing the artwork or copying it, and
neither belongs in an open repository. A silhouette in the right colour reads
as the airline at three metres without pretending to be the livery.

The palette collapses a lot: Finnair, KLM, Korean and Air Europa are all
"blue". That is honest — the panel has six inks and no purple or orange at all.
Where an airline's colour has no ink (easyJet orange, Wizz purple), it maps to
the nearest available.

`accent` is used for a cheatline or wing wash where a renderer wants a second
colour; `body` is the main silhouette fill.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Brand:
    name: str
    body: str      # ink name, see palette.INKS
    accent: str


DEFAULT = Brand("", "white", "black")

# Keyed by ICAO operator code, which adsbdb returns as airline.icao.
BRANDS: dict[str, Brand] = {
    # British and Irish
    "BAW": Brand("British Airways", "white", "blue"),
    "EZY": Brand("easyJet", "red", "white"),        # orange has no ink
    "EJU": Brand("easyJet Europe", "red", "white"),
    "RYR": Brand("Ryanair", "blue", "yellow"),
    "EIN": Brand("Aer Lingus", "green", "white"),
    "VIR": Brand("Virgin Atlantic", "red", "white"),
    "EXS": Brand("Jet2", "red", "white"),
    "TOM": Brand("TUI Airways", "blue", "red"),
    "BEE": Brand("Loganair", "blue", "white"),
    "LOG": Brand("Loganair", "blue", "white"),
    # Western Europe
    "KLM": Brand("KLM", "blue", "white"),
    "AFR": Brand("Air France", "blue", "red"),
    "DLH": Brand("Lufthansa", "yellow", "blue"),
    "SWR": Brand("Swiss", "red", "white"),
    "AUA": Brand("Austrian", "red", "white"),
    "BEL": Brand("Brussels Airlines", "blue", "red"),
    "IBE": Brand("Iberia", "red", "yellow"),
    "VLG": Brand("Vueling", "yellow", "white"),
    "TAP": Brand("TAP Air Portugal", "green", "red"),
    "AZA": Brand("ITA Airways", "blue", "white"),
    "ITY": Brand("ITA Airways", "blue", "white"),
    "SAS": Brand("SAS", "blue", "white"),
    "FIN": Brand("Finnair", "blue", "white"),
    "NAX": Brand("Norwegian", "red", "white"),
    "NSZ": Brand("Norwegian", "red", "white"),
    "LOT": Brand("LOT Polish", "blue", "white"),
    "WZZ": Brand("Wizz Air", "blue", "red"),        # purple has no ink
    "AEE": Brand("Aegean", "blue", "white"),
    "TVF": Brand("Transavia", "green", "white"),
    "EWG": Brand("Eurowings", "yellow", "blue"),
    # Middle East and Africa
    "UAE": Brand("Emirates", "red", "green"),
    "QTR": Brand("Qatar Airways", "red", "white"),
    "ETD": Brand("Etihad", "yellow", "black"),
    "MSR": Brand("Egyptair", "blue", "red"),
    "ELY": Brand("El Al", "blue", "white"),
    "RJA": Brand("Royal Jordanian", "red", "white"),
    "ETH": Brand("Ethiopian", "green", "yellow"),
    "SAA": Brand("South African", "blue", "red"),
    "MSC": Brand("Air Cairo", "blue", "white"),
    "THY": Brand("Turkish Airlines", "red", "white"),
    # Asia and Pacific
    "SIA": Brand("Singapore Airlines", "blue", "yellow"),
    "CPA": Brand("Cathay Pacific", "green", "white"),
    "CCA": Brand("Air China", "red", "yellow"),
    "CES": Brand("China Eastern", "blue", "red"),
    "CSN": Brand("China Southern", "blue", "red"),
    "CHH": Brand("Hainan Airlines", "red", "white"),
    "GCR": Brand("Tianjin Airlines", "blue", "red"),
    "ANA": Brand("All Nippon", "blue", "white"),
    "JAL": Brand("Japan Airlines", "red", "white"),
    "KAL": Brand("Korean Air", "blue", "red"),
    "AAR": Brand("Asiana", "red", "white"),
    "THA": Brand("Thai Airways", "blue", "yellow"),
    "AIC": Brand("Air India", "red", "white"),
    "QFA": Brand("Qantas", "red", "white"),
    "ANZ": Brand("Air New Zealand", "black", "white"),
    "MAS": Brand("Malaysia Airlines", "blue", "red"),
    "GIA": Brand("Garuda Indonesia", "blue", "green"),
    "PAL": Brand("Philippine Airlines", "blue", "red"),
    # Americas
    "AAL": Brand("American Airlines", "white", "blue"),
    "UAL": Brand("United", "blue", "white"),
    "DAL": Brand("Delta", "blue", "red"),
    "ACA": Brand("Air Canada", "red", "white"),
    "WJA": Brand("WestJet", "blue", "white"),
    "JBU": Brand("JetBlue", "blue", "white"),
    "AMX": Brand("Aeroméxico", "blue", "white"),
    "AVA": Brand("Avianca", "red", "white"),
    "TAM": Brand("LATAM", "blue", "red"),
    "LAN": Brand("LATAM", "blue", "red"),
    # Freight
    "FDX": Brand("FedEx", "blue", "red"),
    "UPS": Brand("UPS", "yellow", "black"),
    "GTI": Brand("Atlas Air", "blue", "white"),
    "CLX": Brand("Cargolux", "red", "white"),
    "GEC": Brand("Lufthansa Cargo", "yellow", "blue"),
    # Group subsidiaries and regionals flying the parent's colours
    "SHT": Brand("British Airways", "white", "blue"),
    "CFE": Brand("BA CityFlyer", "white", "blue"),
    "EFW": Brand("BA Euroflyer", "white", "blue"),
    "SZS": Brand("SAS Connect", "blue", "white"),
    "SVS": Brand("SAS Link", "blue", "white"),
    "RUK": Brand("Ryanair UK", "blue", "yellow"),
    "MAY": Brand("Malta Air", "blue", "yellow"),
    "LDM": Brand("Lauda Europe", "red", "white"),
    "EZS": Brand("easyJet Switzerland", "red", "white"),
    "WUK": Brand("Wizz Air UK", "blue", "red"),
    "WMT": Brand("Wizz Air Malta", "blue", "red"),
    "NRK": Brand("Norra", "blue", "white"),
    "DLA": Brand("Air Dolomiti", "blue", "white"),
    "JZA": Brand("Air Canada Express", "red", "white"),
    "ROU": Brand("Air Canada Rouge", "red", "white"),
    # More of Europe
    "VOE": Brand("Volotea", "red", "white"),
    "AEA": Brand("Air Europa", "blue", "white"),
    "TRA": Brand("Transavia", "green", "white"),
    "BTI": Brand("airBaltic", "green", "white"),
    "ICE": Brand("Icelandair", "blue", "yellow"),
    "FPY": Brand("PLAY", "red", "white"),
    "PGT": Brand("Pegasus", "yellow", "red"),
    "SXS": Brand("SunExpress", "red", "yellow"),
    "CFG": Brand("Condor", "yellow", "red"),
    "CTN": Brand("Croatia Airlines", "blue", "red"),
    "ROT": Brand("TAROM", "blue", "red"),
    "ASL": Brand("Air Serbia", "blue", "red"),
    "LGL": Brand("Luxair", "blue", "white"),
    "WIF": Brand("Widerøe", "green", "white"),
    "TFL": Brand("TUI fly Netherlands", "blue", "red"),
    "TUI": Brand("TUI fly Belgium", "blue", "red"),
    "CAI": Brand("Corendon", "red", "white"),
    "CND": Brand("Corendon Dutch", "red", "white"),
    "CSA": Brand("Czech Airlines", "blue", "white"),
    "TVS": Brand("Smartwings", "blue", "white"),
    "BUL": Brand("Bulgaria Air", "blue", "white"),
    "OAL": Brand("Olympic Air", "blue", "white"),
    "SEH": Brand("Sky Express", "blue", "white"),      # purple has no ink
    "ISR": Brand("Israir", "blue", "white"),
    "AIZ": Brand("Arkia", "blue", "white"),
    "AHY": Brand("Azerbaijan Airlines", "blue", "white"),
    "AUR": Brand("Aurigny", "yellow", "black"),
    # More of Asia
    "CAL": Brand("China Airlines", "white", "red"),    # plum blossom: red is nearest
    "EVA": Brand("EVA Air", "green", "white"),
    "SJX": Brand("Starlux", "black", "yellow"),
    "JJA": Brand("Jeju Air", "red", "white"),          # orange has no ink
    "TWB": Brand("T'way Air", "red", "white"),
    "JNA": Brand("Jin Air", "green", "white"),
    "ABL": Brand("Air Busan", "blue", "white"),
    "ESR": Brand("Eastar Jet", "red", "white"),
    "APJ": Brand("Peach", "red", "white"),             # pink has no ink
    "JJP": Brand("Jetstar Japan", "red", "white"),
    "JST": Brand("Jetstar", "red", "white"),
    "HVN": Brand("Vietnam Airlines", "blue", "yellow"),
    "VJC": Brand("VietJet", "red", "yellow"),
    "AXM": Brand("AirAsia", "red", "white"),
    "XAX": Brand("AirAsia X", "red", "white"),
    "TGW": Brand("Scoot", "yellow", "white"),
    "CEB": Brand("Cebu Pacific", "yellow", "blue"),
    "BKP": Brand("Bangkok Airways", "blue", "white"),
    "CXA": Brand("Xiamen Airlines", "blue", "white"),
    "CSZ": Brand("Shenzhen Airlines", "red", "white"),
    "CQH": Brand("Spring Airlines", "green", "white"),
    "HKE": Brand("HK Express", "red", "white"),        # purple has no ink
    "CRK": Brand("Hong Kong Airlines", "red", "white"),
    "IGO": Brand("IndiGo", "blue", "white"),
    "AXB": Brand("Air India Express", "red", "white"),
    # More of the Middle East and Africa
    "SVA": Brand("Saudia", "green", "white"),
    "GFA": Brand("Gulf Air", "yellow", "white"),
    "OMA": Brand("Oman Air", "blue", "white"),
    "FDB": Brand("flydubai", "blue", "white"),
    "ABY": Brand("Air Arabia", "red", "white"),
    "KAC": Brand("Kuwait Airways", "blue", "white"),
    "MEA": Brand("Middle East Airlines", "green", "white"),
    "PIA": Brand("PIA", "green", "white"),
    "RAM": Brand("Royal Air Maroc", "red", "white"),
    "KQA": Brand("Kenya Airways", "red", "white"),
    "RWD": Brand("RwandAir", "blue", "white"),
    # More of the Americas
    "SWA": Brand("Southwest", "blue", "yellow"),
    "ASA": Brand("Alaska Airlines", "blue", "white"),
    "NKS": Brand("Spirit", "yellow", "black"),
    "FFT": Brand("Frontier", "green", "white"),
    "AAY": Brand("Allegiant", "blue", "yellow"),
    "HAL": Brand("Hawaiian", "blue", "white"),         # purple has no ink
    "POE": Brand("Porter", "blue", "white"),
    "TSC": Brand("Air Transat", "blue", "white"),
    "CMP": Brand("Copa", "blue", "white"),
    "AZU": Brand("Azul", "blue", "white"),
    "GLO": Brand("GOL", "red", "white"),               # orange has no ink
    "ARG": Brand("Aerolíneas Argentinas", "blue", "white"),
    "VOI": Brand("Volaris", "blue", "white"),          # purple has no ink
    "VIV": Brand("Viva Aerobus", "green", "white"),
}

# Fuzzy fallbacks for when only a company name is known, which happens whenever
# adsbdb has the airframe but no flight route.
_BY_NAME = [(b.name.lower(), b) for b in BRANDS.values()]


def lookup(icao: str | None, name: str | None = None) -> Brand:
    if icao:
        hit = BRANDS.get(icao.strip().upper())
        if hit:
            return hit
    if name:
        low = name.lower()
        for needle, brand in _BY_NAME:
            if needle and needle in low:
                return brand
    return Brand(name or "", DEFAULT.body, DEFAULT.accent)
