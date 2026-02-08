"""
Jurisdiction-specific portals for video evidence and primary-source document discovery.

Maps regions to:
- Police department transparency portals
- Official YouTube channels
- Court video systems
- FOIA/public records portals
- Local news stations with crime coverage
- Court clerk / docket search systems
- 911 dispatch / CAD record portals
"""

from urllib.parse import urlparse

JURISDICTION_PORTALS = {
    # ==========================================================================
    # CALIFORNIA
    # ==========================================================================
    "SF": {
        "name": "San Francisco",
        "state": "CA",
        "agencies": [
            {
                "name": "San Francisco Police Department",
                "abbrev": "SFPD",
                "youtube": "https://www.youtube.com/@SFPDMedia",
                "transparency_portal": "https://www.sanfranciscopolice.org/your-sfpd/published-reports/officer-involved-shootings",
                "foia_portal": "https://sanfranciscopolice.org/records-requests",
            }
        ],
        "courts": [
            {
                "name": "San Francisco Superior Court",
                "website": "https://www.sfsuperiorcourt.org/",
                "has_video": False,
                "notes": "Limited public video access",
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@KRONon",
            "https://www.youtube.com/@abc7news",
            "https://www.youtube.com/@KPIXCBSBayArea",
        ],
        "search_domains": [
            "sfchronicle.com",
            "sfgate.com",
            "kron4.com",
            "abc7news.com",
        ],
    },
    "SDP": {
        "name": "San Diego",
        "state": "CA",
        "agencies": [
            {
                "name": "San Diego Police Department",
                "abbrev": "SDPD",
                "youtube": "https://www.youtube.com/@SanDiegoPD",
                "transparency_portal": "https://www.sandiego.gov/police/data-transparency/critical-incident-videos",
                "foia_portal": "https://www.sandiego.gov/police/services/records",
            },
            {
                "name": "San Diego County Sheriff",
                "abbrev": "SDSO",
                "youtube": None,
                "transparency_portal": "https://www.sdsheriff.gov/about-us/transparency",
            },
        ],
        "courts": [
            {
                "name": "San Diego Superior Court",
                "website": "https://www.sdcourt.ca.gov/",
                "has_video": False,
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@CBS8SanDiego",
            "https://www.youtube.com/@CBS8SanDiego",
        ],
        "search_domains": [
            "sandiegouniontribune.com",
            "cbs8.com",
            "fox5sandiego.com",
        ],
    },
    "VJ": {
        "name": "Vallejo",
        "state": "CA",
        "agencies": [
            {
                "name": "Vallejo Police Department",
                "abbrev": "VPD",
                "youtube": None,
                "transparency_portal": "https://www.cityofvallejo.net/our_city/departments_divisions/police/transparency_portal",
                "foia_portal": None,
                "notes": "History of bodycam issues - check Open Vallejo",
            }
        ],
        "courts": [
            {
                "name": "Solano County Superior Court",
                "website": "https://www.solano.courts.ca.gov/",
                "has_video": False,
            }
        ],
        "news_channels": [],
        "search_domains": [
            "timesheraldonline.com",
            "openvallejo.org",
        ],
        "special_sources": [
            "https://openvallejo.org/",
        ],
    },
    "OC": {
        "name": "Orange County",
        "state": "CA",
        "agencies": [
            {
                "name": "Orange County Sheriff's Department",
                "abbrev": "OCSD",
                "youtube": "https://www.youtube.com/@OCSheriff",
                "transparency_portal": "https://www.ocsheriff.gov/about-ocsd/transparency",
            },
            {
                "name": "Anaheim Police Department",
                "abbrev": "APD",
                "youtube": None,
                "transparency_portal": None,
            },
        ],
        "courts": [
            {
                "name": "Orange County Superior Court",
                "website": "https://www.occourts.org/",
                "has_video": False,
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@ABC7",
        ],
        "search_domains": [
            "ocregister.com",
            "latimes.com",
            "voiceofoc.org",
        ],
    },
    "LC": {
        "name": "Los Angeles County",
        "state": "CA",
        "agencies": [
            {
                "name": "Los Angeles Police Department",
                "abbrev": "LAPD",
                "youtube": "https://www.youtube.com/@LAPDHQ",
                "transparency_portal": "https://www.lapdonline.org/office-of-the-chief-of-police/professional-standards-bureau/critical-incident-videos/",
                "foia_portal": "https://www.lapdonline.org/records/",
                "notes": "~3-4 BWC videos/month. 45-day release. Year-by-year archives.",
            },
            {
                "name": "Los Angeles County Sheriff",
                "abbrev": "LASD",
                "youtube": None,
                "transparency_portal": "https://lasd.org/transparency/",
            },
        ],
        "courts": [
            {
                "name": "Los Angeles Superior Court",
                "website": "https://www.lacourt.org/",
                "has_video": True,
                "video_portal": "https://www.lacourt.org/livestream/",
                "notes": "Some trials livestreamed",
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@ABC7",
            "https://www.youtube.com/@CBSLA",
            "https://www.youtube.com/@KTLA5",
        ],
        "search_domains": [
            "latimes.com",
            "abc7.com",
            "ktla.com",
            "laist.com",
        ],
    },
    # ==========================================================================
    # FLORIDA
    # ==========================================================================
    "BC": {
        "name": "Broward County",
        "state": "FL",
        "agencies": [
            {
                "name": "Broward County Sheriff's Office",
                "abbrev": "BSO",
                "youtube": "https://www.youtube.com/@BrowardSheriff",
                "transparency_portal": None,
                "foia_portal": "https://www.sheriff.org/FAQ/Pages/Public-Records-Request.aspx",
                "notes": "Florida has strong public records laws - bodycam often available",
            },
            {
                "name": "Fort Lauderdale Police",
                "abbrev": "FLPD",
                "youtube": None,
            },
        ],
        "courts": [
            {
                "name": "Broward County Courts",
                "website": "https://www.browardclerk.org/",
                "has_video": True,
                "notes": "Florida courts often televised",
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@WPLGLocal10",
            "https://www.youtube.com/@7NewsMiami",
        ],
        "search_domains": [
            "sun-sentinel.com",
            "local10.com",
            "wsvn.com",
        ],
        "special_notes": "Florida Sunshine Law = strong public records access",
    },
    "MD": {
        "name": "Miami-Dade County",
        "state": "FL",
        "agencies": [
            {
                "name": "Miami-Dade Police Department",
                "abbrev": "MDPD",
                "youtube": None,
                "transparency_portal": None,
                "foia_portal": "https://www.miamidade.gov/global/service.page?Mduid_service=ser1529499498882149",
            },
            {
                "name": "Miami Police Department",
                "abbrev": "MPD",
                "youtube": None,
            },
        ],
        "courts": [
            {
                "name": "Miami-Dade County Courts",
                "website": "https://www.miami-dadeclerk.com/",
                "has_video": True,
                "notes": "High-profile trials often televised",
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@7NewsMiami",
            "https://www.youtube.com/@WPLGLocal10",
            "https://www.youtube.com/@CBSMiami",
        ],
        "search_domains": [
            "miamiherald.com",
            "wsvn.com",
            "local10.com",
        ],
        "special_notes": "Florida Sunshine Law = strong public records access",
    },
    "OCS": {
        "name": "Orange County",
        "state": "FL",
        "agencies": [
            {
                "name": "Orange County Sheriff's Office",
                "abbrev": "OCSO",
                "youtube": "https://www.youtube.com/@OrangeCountySheriffsOffice",
                "transparency_portal": None,
            },
            {
                "name": "Orlando Police Department",
                "abbrev": "OPD",
                "youtube": None,
            },
        ],
        "courts": [
            {
                "name": "Orange County Courts",
                "website": "https://www.ninthcircuit.org/",
                "has_video": True,
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@WFTV9Orlando",
            "https://www.youtube.com/@ClickOrlando",
        ],
        "search_domains": [
            "orlandosentinel.com",
            "wftv.com",
            "clickorlando.com",
        ],
    },
    "JS": {
        "name": "Jacksonville",
        "state": "FL",
        "agencies": [
            {
                "name": "Jacksonville Sheriff's Office",
                "abbrev": "JSO",
                "youtube": "https://www.youtube.com/@JaxSheriff",
                "transparency_portal": "https://www.jaxsheriff.org/Resources/Records-and-Reports.aspx",
            }
        ],
        "courts": [
            {
                "name": "Duval County Courts",
                "website": "https://www.duvalclerk.com/",
                "has_video": True,
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@ActionNewsJax",
            "https://www.youtube.com/@FirstCoastNews",
        ],
        "search_domains": [
            "jacksonville.com",
            "news4jax.com",
            "firstcoastnews.com",
        ],
    },
    # ==========================================================================
    # ARIZONA
    # ==========================================================================
    "PPD": {
        "name": "Phoenix",
        "state": "AZ",
        "agencies": [
            {
                "name": "Phoenix Police Department",
                "abbrev": "PHX PD",
                "youtube": "https://www.youtube.com/@PhoenixPolice",
                "transparency_portal": "https://www.phoenix.gov/police/ois-videos",
                "foia_portal": "https://www.phoenix.gov/police/records",
                "notes": "PPD releases critical incident videos on YouTube regularly",
            }
        ],
        "courts": [
            {
                "name": "Maricopa County Superior Court",
                "website": "https://superiorcourt.maricopa.gov/",
                "has_video": True,
                "video_portal": "https://www.youtube.com/@MaricopaCountySuperiorCourt",
                "notes": "Many trials streamed on YouTube",
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@12News",
            "https://www.youtube.com/@abc15",
            "https://www.youtube.com/@FOX10Phoenix",
        ],
        "search_domains": [
            "azcentral.com",
            "12news.com",
            "fox10phoenix.com",
        ],
    },
    "MPD": {
        "name": "Mesa",
        "state": "AZ",
        "agencies": [
            {
                "name": "Mesa Police Department",
                "abbrev": "Mesa PD",
                "youtube": "https://www.youtube.com/@MesaPolice",
                "transparency_portal": "https://www.mesaaz.gov/residents/police/about/transparency",
            }
        ],
        "courts": [
            {
                "name": "Maricopa County Superior Court",
                "website": "https://superiorcourt.maricopa.gov/",
                "has_video": True,
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@12News",
            "https://www.youtube.com/@abc15",
        ],
        "search_domains": [
            "azcentral.com",
            "abc15.com",
        ],
    },
    "MCS": {
        "name": "Maricopa County",
        "state": "AZ",
        "agencies": [
            {
                "name": "Maricopa County Sheriff's Office",
                "abbrev": "MCSO",
                "youtube": None,
                "transparency_portal": None,
            }
        ],
        "courts": [
            {
                "name": "Maricopa County Superior Court",
                "website": "https://superiorcourt.maricopa.gov/",
                "has_video": True,
                "video_portal": "https://www.youtube.com/@MaricopaCountySuperiorCourt",
            }
        ],
        "news_channels": [],
        "search_domains": [
            "azcentral.com",
        ],
    },
    # ==========================================================================
    # WASHINGTON
    # ==========================================================================
    "SPD": {
        "name": "Seattle",
        "state": "WA",
        "agencies": [
            {
                "name": "Seattle Police Department",
                "abbrev": "SPD",
                "youtube": "https://www.youtube.com/@SeattlePolice",
                "transparency_portal": "https://www.seattle.gov/police/information-and-data/videos",
                "foia_portal": "https://www.seattle.gov/police/information-and-data/public-disclosure",
                "notes": "SPD publishes bodycam/dashcam to YouTube",
            }
        ],
        "courts": [
            {
                "name": "King County Superior Court",
                "website": "https://www.kingcounty.gov/courts/superior-court.aspx",
                "has_video": False,
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@KING5Seattle",
            "https://www.youtube.com/@KOMONews",
        ],
        "search_domains": [
            "seattletimes.com",
            "king5.com",
            "komonews.com",
        ],
    },
    "KCS": {
        "name": "King County",
        "state": "WA",
        "agencies": [
            {
                "name": "King County Sheriff's Office",
                "abbrev": "KCSO",
                "youtube": None,
                "transparency_portal": "https://kingcounty.gov/depts/sheriff/about-us/oversight.aspx",
            }
        ],
        "courts": [
            {
                "name": "King County Superior Court",
                "website": "https://www.kingcounty.gov/courts/superior-court.aspx",
                "has_video": False,
            }
        ],
        "news_channels": [],
        "search_domains": [
            "seattletimes.com",
        ],
    },
    # ==========================================================================
    # COLORADO
    # ==========================================================================
    "APD": {
        "name": "Aurora",
        "state": "CO",
        "agencies": [
            {
                "name": "Aurora Police Department",
                "abbrev": "Aurora PD",
                "youtube": "https://www.youtube.com/@AuroraPolice",
                "transparency_portal": "https://www.auroragov.org/residents/public_safety/police/transparency",
            }
        ],
        "courts": [
            {
                "name": "Arapahoe County District Court",
                "website": "https://www.courts.state.co.us/Courts/District/Index.cfm?District_ID=18",
                "has_video": False,
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@DenverChannel",
            "https://www.youtube.com/@9NEWS",
        ],
        "search_domains": [
            "denverpost.com",
            "thedenverchannel.com",
            "9news.com",
        ],
    },
    "CSPD": {
        "name": "Colorado Springs",
        "state": "CO",
        "agencies": [
            {
                "name": "Colorado Springs Police Department",
                "abbrev": "CSPD",
                "youtube": "https://www.youtube.com/channel/UC6547e-x50KWl7FGnr9-fVg",
                "transparency_portal": "https://coloradosprings.gov/police-department/page/cases-interest",
                "notes": "Proactive BWC release within 21 days. Significant Event Briefings.",
            }
        ],
        "courts": [
            {
                "name": "El Paso County District Court",
                "website": "https://www.courts.state.co.us/Courts/District/Index.cfm?District_ID=4",
                "has_video": False,
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@KRDO",
        ],
        "search_domains": [
            "gazette.com",
            "krdo.com",
        ],
    },
    "DPD": {
        "name": "Denver",
        "state": "CO",
        "agencies": [
            {
                "name": "Denver Police Department",
                "abbrev": "DPD",
                "youtube": "https://www.youtube.com/user/DenverPoliceDept",
                "transparency_portal": "https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Police-Department/Publicly-Released-Recordings",
                "notes": "~1 week press briefing cycle after critical incidents. Dedicated recordings page.",
            }
        ],
        "courts": [
            {
                "name": "Denver District Court",
                "website": "https://www.courts.state.co.us/Courts/District/Index.cfm?District_ID=2",
                "has_video": False,
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@DenverChannel",
            "https://www.youtube.com/@9NEWS",
        ],
        "search_domains": [
            "denverpost.com",
            "thedenverchannel.com",
            "9news.com",
        ],
    },
    # ==========================================================================
    # TEXAS
    # ==========================================================================
    "ATXPD": {
        "name": "Austin",
        "state": "TX",
        "agencies": [
            {
                "name": "Austin Police Department",
                "abbrev": "APD",
                "youtube": "https://www.youtube.com/@AustinPolice",
                "transparency_portal": "https://www.austintexas.gov/department/police/transparency",
                "foia_portal": "https://www.austintexas.gov/page/public-information-requests",
            }
        ],
        "courts": [
            {
                "name": "Travis County District Court",
                "website": "https://www.traviscountytx.gov/courts",
                "has_video": False,
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@KVUE",
            "https://www.youtube.com/@KXAN",
        ],
        "search_domains": [
            "statesman.com",
            "kvue.com",
            "kxan.com",
        ],
    },
    "HPD": {
        "name": "Houston",
        "state": "TX",
        "agencies": [
            {
                "name": "Houston Police Department",
                "abbrev": "HPD",
                "youtube": "https://www.youtube.com/@HoustonPolice",
                "transparency_portal": "https://www.houstontx.gov/police/ois/",
                "notes": "HPD releases officer-involved shooting videos",
            }
        ],
        "courts": [
            {
                "name": "Harris County District Court",
                "website": "https://www.hcdistrictclerk.com/",
                "has_video": True,
                "notes": "Some high-profile cases streamed",
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@ABC13Houston",
            "https://www.youtube.com/@KHOU",
        ],
        "search_domains": [
            "houstonchronicle.com",
            "abc13.com",
            "khou.com",
        ],
    },
    "DPDT": {
        "name": "Dallas",
        "state": "TX",
        "agencies": [
            {
                "name": "Dallas Police Department",
                "abbrev": "DPD",
                "youtube": None,
                "transparency_portal": "https://dallaspolice.net/resources/transparency",
            }
        ],
        "courts": [
            {
                "name": "Dallas County District Court",
                "website": "https://www.dallascounty.org/departments/districtclerk/",
                "has_video": True,
                "notes": "Some trials televised",
            }
        ],
        "news_channels": [
            "https://www.youtube.com/@WFAA",
            "https://www.youtube.com/@NBCDFWNews",
        ],
        "search_domains": [
            "dallasnews.com",
            "wfaa.com",
            "nbcdfw.com",
        ],
    },
}

# ==========================================================================
# TRUE CRIME VIDEO CHANNELS (search these for existing coverage)
# ==========================================================================

TRUE_CRIME_CHANNELS = [
    {"name": "JCS - Criminal Psychology", "youtube": "https://www.youtube.com/@JCSCriminalPsychology", "type": "interrogation"},
    {"name": "Matt Orchard", "youtube": "https://www.youtube.com/@MattOrchard", "type": "interrogation"},
    {"name": "Dreading", "youtube": "https://www.youtube.com/@Dreading", "type": "interrogation"},
    {"name": "Law&Crime Network", "youtube": "https://www.youtube.com/@LawCrimeNetwork", "type": "court"},
    {"name": "Court TV", "youtube": "https://www.youtube.com/@CourtTV", "type": "court"},
    {"name": "CourtRoom Consequences", "youtube": "https://www.youtube.com/@CourtroomConsequences", "type": "court"},
    {"name": "Police Activity", "youtube": "https://www.youtube.com/@PoliceActivity", "type": "bodycam"},
    {"name": "Real World Police", "youtube": "https://www.youtube.com/@RealWorldPolice", "type": "bodycam"},
    {"name": "Bodycam Watch", "youtube": "https://www.youtube.com/@BodyCamWatch", "type": "bodycam"},
    {"name": "That Chapter", "youtube": "https://www.youtube.com/@ThatChapter", "type": "documentary"},
    {"name": "Coffeehouse Crime", "youtube": "https://www.youtube.com/@CoffeehouseCrime", "type": "documentary"},
    {"name": "Explore With Us", "youtube": "https://www.youtube.com/@ExploreWithUs", "type": "documentary"},
]

# ==========================================================================
# HELPER FUNCTIONS
# ==========================================================================


def get_jurisdiction_config(region_id: str) -> dict:
    """Get portal configuration for a region."""
    return JURISDICTION_PORTALS.get(region_id, {})


def get_search_domains_for_region(region_id: str) -> list:
    """Get all searchable domains for a region."""
    config = get_jurisdiction_config(region_id)
    domains = config.get("search_domains", []).copy()

    for agency in config.get("agencies", []):
        if agency.get("youtube") and "youtube.com" not in domains:
            domains.append("youtube.com")
            break

    return domains


def get_agency_youtube_channels(region_id: str) -> list:
    """Get official YouTube channels for agencies in region."""
    config = get_jurisdiction_config(region_id)
    channels = []

    for agency in config.get("agencies", []):
        if agency.get("youtube"):
            channels.append({
                "name": agency["name"],
                "abbrev": agency.get("abbrev", ""),
                "youtube": agency["youtube"],
            })

    for court in config.get("courts", []):
        if court.get("video_portal") and "youtube" in court.get("video_portal", ""):
            channels.append({
                "name": court["name"],
                "youtube": court["video_portal"],
            })

    return channels


def get_transparency_portals(region_id: str) -> list:
    """Get transparency/FOIA portals for a region."""
    config = get_jurisdiction_config(region_id)
    portals = []

    for agency in config.get("agencies", []):
        if agency.get("transparency_portal"):
            portals.append({
                "name": agency["name"],
                "type": "transparency",
                "url": agency["transparency_portal"],
            })
        if agency.get("foia_portal"):
            portals.append({
                "name": agency["name"],
                "type": "foia",
                "url": agency["foia_portal"],
            })

    return portals


def build_jurisdiction_queries(region_id: str, defendant: str,
                               incident_year: str = None) -> dict:
    """Build targeted search queries using jurisdiction knowledge.

    Returns query buckets for bodycam, interrogation, court, news,
    docket (primary-source documents), and dispatch (911/CAD records).
    """
    config = get_jurisdiction_config(region_id)
    queries = {
        "bodycam": [], "interrogation": [], "court": [],
        "news": [], "docket": [], "dispatch": [],
    }

    if not config:
        return queries

    agencies = config.get("agencies", [])
    agency_names = [a.get("abbrev", a["name"]) for a in agencies]

    year_str = f" {incident_year}" if incident_year else ""

    # --- Bodycam: direct evidence retrieval language ---
    for agency in agency_names[:2]:
        queries["bodycam"].append(f"{agency} bodycam {defendant}{year_str}")
        queries["bodycam"].append(f"{defendant} {agency} body camera release evidence")

    # --- Interrogation: direct retrieval, not FOIA request ---
    queries["interrogation"].append(f"{defendant} interrogation video full")
    queries["interrogation"].append(f"{defendant} police interview recording released")

    # --- Court ---
    queries["court"].append(f"{defendant} trial court video")
    queries["court"].append(f"{defendant} sentencing hearing")

    state = config.get("state", "")
    if state:
        queries["court"].append(f"{defendant} {state} trial verdict")

    # --- Docket / primary-source documents ---
    queries["docket"].append(f"{defendant} probable cause affidavit{year_str}")
    queries["docket"].append(f"{defendant} criminal complaint filing{year_str}")
    queries["docket"].append(f"{defendant} arrest affidavit incident report")

    for court in config.get("courts", []):
        court_name = court.get("name", "")
        if court_name:
            queries["docket"].append(f"{defendant} {court_name} docket case number")

    # CourtListener / PACER scoped
    queries["docket"].append(f"site:courtlistener.com {defendant} {config.get('name', '')}")

    # --- Dispatch / 911 ---
    for agency in agency_names[:1]:
        queries["dispatch"].append(f"{defendant} 911 call audio released{year_str}")
        queries["dispatch"].append(f"{agency} dispatch audio {defendant}")

    # --- News ---
    for domain in config.get("search_domains", [])[:2]:
        queries["news"].append(f"site:{domain} {defendant}")

    return queries


# Domains that host primary-source court and docket records
RECORDS_DOMAINS = [
    "courtlistener.com",
    "unicourt.com",
    "pacermonitor.com",
    "law.justia.com",
    "casetext.com",
]

# Domains that host 911 / dispatch audio
DISPATCH_DOMAINS = [
    "broadcastify.com",
    "openmhz.com",
]


def is_florida_case(region_id: str) -> bool:
    """Check if region is in Florida (stronger public records)."""
    config = get_jurisdiction_config(region_id)
    return config.get("state") == "FL"


# States with the loosest public records access for law enforcement artifacts.
# These jurisdictions make bodycam, dashcam, interrogation, and court footage
# significantly easier to obtain than average.
SUNSHINE_STATES = {"FL", "TX", "AZ", "WA", "OH", "GA", "UT"}


def is_sunshine_state(region_id: str) -> bool:
    """Check if region is in a sunshine state (loose artifact access laws)."""
    config = get_jurisdiction_config(region_id)
    return config.get("state", "") in SUNSHINE_STATES


def has_court_video(region_id: str) -> bool:
    """Check if jurisdiction typically has court video."""
    config = get_jurisdiction_config(region_id)
    return any(court.get("has_video") for court in config.get("courts", []))


def extract_domain(url: str) -> str:
    """Extract domain from a URL for site filtering."""
    if not url:
        return ""
    parsed = urlparse(url)
    return parsed.netloc.replace("www.", "")
