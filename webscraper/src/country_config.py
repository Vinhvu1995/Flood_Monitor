"""Country configs (UK/US/IT) — mirrors src/country_config.py."""
from dataclasses import dataclass, field

DATA_DIR = "/nfs/develop_webscraper/flood-webscraper/data"

@dataclass
class CountryConfig:
    code: str
    country_name: str
    geo_suffix: str
    tz: str
    target_epsg: int
    seeds: list = field(default_factory=list)
    query_templates: list = field(default_factory=list)
    discovery_queries: list = field(default_factory=list)
    address_format: str = ""
    rss_lang: tuple = ("en-GB", "GB", "GB:en")
    rasters: list = field(default_factory=list)
    local_geocoder: bool = True
    foreign_tld_block: tuple = ()
    historical_events: list = field(default_factory=list)
    nws_lsr_states: tuple = ()

UK_TEMPLATES = ["Flooding at {loc}, UK", "{loc} flooding", "{loc} flash flood",
                "{loc} flooded homes evacuated", "{loc} river burst its banks"]
US_TEMPLATES = ["Flooding at {loc}, US", "{loc} flooding", "{loc} flash flood",
                "{loc} street flooded", "{loc} subway station flooded",
                "{loc} expressway closed flooding"]
IT_TEMPLATES = ["alluvione a {loc}", "esondazione a {loc}", "allagamenti a {loc}"]

COUNTRIES = {
    "UK": CountryConfig(
        code="UK", country_name="United Kingdom", geo_suffix="UK",
        tz="Europe/London", target_epsg=27700,
        seeds=["London", "York", "Carlisle"],
        query_templates=UK_TEMPLATES,
        discovery_queries=["UK flooding", "England flooding", "Scotland flooding",
                           "Wales flooding", "flash flood UK", "river flooding UK town"],
        address_format="Street/Area, Town/Village, Administrative Division",
        rss_lang=("en-GB", "GB", "GB:en"),
        rasters=[("UK_SWF_5m_V4.tif", "Surface water risk"),
                 ("UK_SWF_5m_V4_CC.tif", "Surface water risk CC"),
                 ("UK_RF_5m_V3.tif", "Fluvial flood risk")],
        local_geocoder=True, foreign_tld_block=("ie", "au", "ca", "nz", "us", "mil", "gov"),
        historical_events=["Bert", "Babet", "Darragh", "Isha", "Jocelyn"],
    ),
    "US": CountryConfig(
        code="US", country_name="United States", geo_suffix="United States",
        tz="America/New_York", target_epsg=5070,
        seeds=["New York", "Newark", "Hartford"],
        query_templates=US_TEMPLATES,
        discovery_queries=["NY flooding", "NJ flooding", "CT flooding", "tri-state flooding"],
        address_format="Street/Area, City/Town, State",
        rss_lang=("en-US", "US", "US:en"),
        rasters=[], local_geocoder=True,
        historical_events=["Helene", "Milton", "Sandy", "Katrina", "Ian"],
        nws_lsr_states=("NY", "NJ", "CT"),
    ),
    "IT": CountryConfig(
        code="IT", country_name="Italy", geo_suffix="Italy",
        tz="Europe/Rome", target_epsg=32633,
        seeds=["Faenza", "Ravenna", "Bologna"],
        query_templates=IT_TEMPLATES,
        discovery_queries=["alluvione Italia", "esondazione Italia"],
        address_format="Via/Localita, Comune, Regione",
        rss_lang=("it-IT", "IT", "IT:it"),
        rasters=[], local_geocoder=False,
        historical_events=["Romagna 2023"],
    ),
}

def get_country(code: str) -> CountryConfig:
    try:
        return COUNTRIES[code.upper()]
    except KeyError:
        raise ValueError(f"unknown country {code!r}")
