from __future__ import annotations

from app.config import settings
from app.scrapers.amazon import AmazonScraper
from app.scrapers.cadence import CadenceScraper
from app.scrapers.capgemini import CapgeminiIndiaScraper, CapgeminiScraper
from app.scrapers.concentrix import ConcentrixScraper
from app.scrapers.deloitte import DeloitteScraper
from app.scrapers.eightfold import HSBCScraper, MicrosoftScraper
from app.scrapers.exl import EXLScraper
from app.scrapers.google import GoogleScraper
from app.scrapers.greenhouse import MedallionScraper
from app.scrapers.jpmorgan import JPMorganScraper
from app.scrapers.models import BaseScraper
from app.scrapers.successfactors import DeloitteIndiaScraper, EYScraper

SCRAPER_CLASSES: dict[str, type[BaseScraper]] = {
    "jpmorgan": JPMorganScraper,
    "deloitte": DeloitteScraper,
    "capgemini": CapgeminiScraper,
    "google": GoogleScraper,
    "deloitte_india": DeloitteIndiaScraper,
    "capgemini_india": CapgeminiIndiaScraper,
    "exl": EXLScraper,
    "concentrix": ConcentrixScraper,
    "amazon": AmazonScraper,
    "microsoft": MicrosoftScraper,
    "medallion": MedallionScraper,
    "cadence": CadenceScraper,
    "hsbc": HSBCScraper,
    "ey": EYScraper,
}

COMPANY_NAMES = {
    "jpmorgan": "JPMorgan Chase",
    "deloitte": "Deloitte",
    "capgemini": "Capgemini",
    "google": "Google",
    "deloitte_india": "Deloitte India",
    "capgemini_india": "Capgemini India",
    "exl": "EXL",
    "concentrix": "Concentrix",
    "amazon": "Amazon",
    "microsoft": "Microsoft",
    "medallion": "Medallion",
    "cadence": "Cadence",
    "hsbc": "HSBC",
    "ey": "EY",
}


def get_scraper(source: str) -> BaseScraper:
    source = source.strip().lower()
    try:
        return SCRAPER_CLASSES[source]()
    except KeyError as exc:
        raise ValueError(
            f"Unknown source '{source}'. Choose from: {', '.join(SCRAPER_CLASSES)}"
        ) from exc


def enabled_source_names() -> list[str]:
    names = []
    for name in settings.enabled_sources:
        if name in SCRAPER_CLASSES:
            names.append(name)
    return names
