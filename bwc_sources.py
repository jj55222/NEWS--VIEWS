"""
BWC Source Registry: agencies that proactively publish body-worn camera footage.

Each source has a scrape_type that tells bwc_monitor.py how to check for new content:
  - "youtube_channel" — poll YouTube Data API for recent uploads
  - "html_listing"    — scrape an HTML page for links to incident videos
  - "rss"             — parse an RSS/Atom feed (future)
  - "manual"          — no automated monitoring; included for reference only

Tier system:
  1 = Dedicated portal/page, mandated release cadence, active YouTube
  2 = Regular releases but via press releases or social media, less predictable
  3 = Sporadic / request-only / supplementary
"""

BWC_SOURCES = [
    # =========================================================================
    # COLORADO  (priority: CSPD, DPD, APD)
    # =========================================================================
    {
        "source_id": "cspd_portal",
        "agency": "Colorado Springs Police Department",
        "abbrev": "CSPD",
        "region_id": "CSPD",
        "state": "CO",
        "tier": 1,
        "scrape_type": "html_listing",
        "url": "https://coloradosprings.gov/police-department/page/cases-interest",
        "release_cadence": "Within 21 days of significant event, automatic",
        "notes": "First CO agency with proactive release. Significant Event Briefings.",
    },
    {
        "source_id": "cspd_youtube",
        "agency": "Colorado Springs Police Department",
        "abbrev": "CSPD",
        "region_id": "CSPD",
        "state": "CO",
        "tier": 1,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/channel/UC6547e-x50KWl7FGnr9-fVg",
        "youtube_channel_id": "UC6547e-x50KWl7FGnr9-fVg",
        "release_cadence": "Same as portal — videos posted to both",
    },
    {
        "source_id": "dpd_portal",
        "agency": "Denver Police Department",
        "abbrev": "DPD",
        "region_id": "DPD",
        "state": "CO",
        "tier": 1,
        "scrape_type": "html_listing",
        "url": "https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Police-Department/Publicly-Released-Recordings",
        "release_cadence": "~1 week press briefing after critical incident",
    },
    {
        "source_id": "dpd_youtube",
        "agency": "Denver Police Department",
        "abbrev": "DPD",
        "region_id": "DPD",
        "state": "CO",
        "tier": 1,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/user/DenverPoliceDept",
        "youtube_channel_id": "UCRLOqkTweTNsp2z8VvXxVYA",
        "release_cadence": "Critical incident briefings posted here",
    },
    {
        "source_id": "apd_portal",
        "agency": "Aurora Police Department",
        "abbrev": "Aurora PD",
        "region_id": "APD",
        "state": "CO",
        "tier": 2,
        "scrape_type": "html_listing",
        "url": "https://www.auroragov.org/residents/public_safety/police/APD_news",
        "release_cadence": "~30 days after critical incident (target)",
        "notes": "Critical incident videos posted as news items. Editing controversies.",
    },
    {
        "source_id": "apd_youtube",
        "agency": "Aurora Police Department",
        "abbrev": "Aurora PD",
        "region_id": "APD",
        "state": "CO",
        "tier": 2,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@AuroraPolice",
        "youtube_channel_id": "",  # needs lookup via API
        "release_cadence": "Critical incident community briefings",
    },
    # =========================================================================
    # WASHINGTON  (KCS has no proactive publishing; SPD does)
    # =========================================================================
    {
        "source_id": "spd_blotter",
        "agency": "Seattle Police Department",
        "abbrev": "SPD",
        "region_id": "SPD",
        "state": "WA",
        "tier": 1,
        "scrape_type": "html_listing",
        "url": "https://spdblotter.seattle.gov/significant-incident-reports/",
        "release_cadence": "Within 72 hours of OIS",
    },
    {
        "source_id": "spd_youtube",
        "agency": "Seattle Police Department",
        "abbrev": "SPD",
        "region_id": "SPD",
        "state": "WA",
        "tier": 1,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@SeattlePolice",
        "youtube_channel_id": "",  # needs lookup via API
        "release_cadence": "OIS BWC videos posted regularly",
    },
    {
        "source_id": "kcso_note",
        "agency": "King County Sheriff's Office",
        "abbrev": "KCSO",
        "region_id": "KCS",
        "state": "WA",
        "tier": 3,
        "scrape_type": "manual",
        "url": "https://kingcounty.gov/en/dept/sheriff",
        "release_cadence": "No proactive publishing. BWC deployed 2023, request-only.",
        "notes": "KCSO serves as IIT for SPD incidents. Use SPD sources for metro coverage.",
    },
    # =========================================================================
    # ARIZONA
    # =========================================================================
    {
        "source_id": "ppd_youtube",
        "agency": "Phoenix Police Department",
        "abbrev": "PHX PD",
        "region_id": "PPD",
        "state": "AZ",
        "tier": 1,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@PhoenixPolice",
        "youtube_channel_id": "",
        "release_cadence": "Every OIS within 14 days. CIB format. Prolific.",
    },
    {
        "source_id": "mesa_youtube",
        "agency": "Mesa Police Department",
        "abbrev": "Mesa PD",
        "region_id": "MPD",
        "state": "AZ",
        "tier": 1,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@MesaPolice",
        "youtube_channel_id": "",
        "release_cadence": "All OIS (fatal or not) within 45 days",
    },
    {
        "source_id": "mesa_portal",
        "agency": "Mesa Police Department",
        "abbrev": "Mesa PD",
        "region_id": "MPD",
        "state": "AZ",
        "tier": 1,
        "scrape_type": "html_listing",
        "url": "https://www.mesaaz.gov/Public-Safety/Mesa-Police/Community/Transparency-In-Policing/Community-Briefings",
        "release_cadence": "Same as YouTube — linked from transparency page",
    },
    # =========================================================================
    # TEXAS
    # =========================================================================
    {
        "source_id": "atxpd_portal",
        "agency": "Austin Police Department",
        "abbrev": "APD-TX",
        "region_id": "ATXPD",
        "state": "TX",
        "tier": 1,
        "scrape_type": "html_listing",
        "url": "https://www.austintexas.gov/page/critical-incident-briefing-videos",
        "release_cadence": "Within 10 business days — fastest mandate nationally",
    },
    {
        "source_id": "atxpd_youtube",
        "agency": "Austin Police Department",
        "abbrev": "APD-TX",
        "region_id": "ATXPD",
        "state": "TX",
        "tier": 1,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@AustinPolice",
        "youtube_channel_id": "",
        "release_cadence": "Critical incident videos linked from city website",
    },
    {
        "source_id": "hpd_portal",
        "agency": "Houston Police Department",
        "abbrev": "HPD",
        "region_id": "HPD",
        "state": "TX",
        "tier": 1,
        "scrape_type": "html_listing",
        "url": "https://www.houstontx.gov/police/ois/",
        "release_cadence": "Within 30 days of critical incident",
    },
    {
        "source_id": "hpd_youtube",
        "agency": "Houston Police Department",
        "abbrev": "HPD",
        "region_id": "HPD",
        "state": "TX",
        "tier": 1,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@HoustonPolice",
        "youtube_channel_id": "",
        "release_cadence": "OIS videos posted and linked from OIS page",
    },
    # =========================================================================
    # FLORIDA  (Sunshine Law — strong public records)
    # =========================================================================
    {
        "source_id": "ocso_fl_youtube",
        "agency": "Orange County Sheriff's Office",
        "abbrev": "OCSO",
        "region_id": "OCS",
        "state": "FL",
        "tier": 1,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@OrangeCountySheriffsOffice",
        "youtube_channel_id": "",
        "release_cadence": "Every OIS within 30 days. Consistent track record.",
    },
    {
        "source_id": "jso_youtube",
        "agency": "Jacksonville Sheriff's Office",
        "abbrev": "JSO",
        "region_id": "JS",
        "state": "FL",
        "tier": 2,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@JaxSheriff",
        "youtube_channel_id": "",
        "release_cadence": "Event-driven, General Order 505",
    },
    {
        "source_id": "bso_youtube",
        "agency": "Broward County Sheriff's Office",
        "abbrev": "BSO",
        "region_id": "BC",
        "state": "FL",
        "tier": 2,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@BrowardSheriff",
        "youtube_channel_id": "",
        "release_cadence": "Case-by-case, social media driven",
    },
    # =========================================================================
    # CALIFORNIA  (SB 1421 / AB 748 — mandatory release for use-of-force)
    # =========================================================================
    {
        "source_id": "lapd_portal",
        "agency": "Los Angeles Police Department",
        "abbrev": "LAPD",
        "region_id": "LC",
        "state": "CA",
        "tier": 1,
        "scrape_type": "html_listing",
        "url": "https://www.lapdonline.org/office-of-the-chief-of-police/professional-standards-bureau/critical-incident-videos/",
        "release_cadence": "Within 45 days. ~3-4 new videos per month.",
    },
    {
        "source_id": "lapd_youtube",
        "agency": "Los Angeles Police Department",
        "abbrev": "LAPD",
        "region_id": "LC",
        "state": "CA",
        "tier": 1,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@LAPDHQ",
        "youtube_channel_id": "",
        "release_cadence": "Critical Incident Community Briefings",
    },
    {
        "source_id": "sdpd_portal",
        "agency": "San Diego Police Department",
        "abbrev": "SDPD",
        "region_id": "SDP",
        "state": "CA",
        "tier": 1,
        "scrape_type": "html_listing",
        "url": "https://www.sandiego.gov/police/data-transparency/critical-incident-videos",
        "release_cadence": "Aims for <10 days",
    },
    {
        "source_id": "sdpd_youtube",
        "agency": "San Diego Police Department",
        "abbrev": "SDPD",
        "region_id": "SDP",
        "state": "CA",
        "tier": 1,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@SanDiegoPD",
        "youtube_channel_id": "",
        "release_cadence": "Critical incident videos cross-posted",
    },
    {
        "source_id": "sfpd_portal",
        "agency": "San Francisco Police Department",
        "abbrev": "SFPD",
        "region_id": "SF",
        "state": "CA",
        "tier": 1,
        "scrape_type": "html_listing",
        "url": "https://www.sanfranciscopolice.org/news",
        "release_cadence": "Town hall within 10 days of OIS. Raw footage on Vimeo.",
        "notes": "Videos hosted on Vimeo (raw) and SFGovTV YouTube (edited town hall).",
    },
    {
        "source_id": "ocsd_ca_youtube",
        "agency": "Orange County Sheriff's Department",
        "abbrev": "OCSD",
        "region_id": "OC",
        "state": "CA",
        "tier": 2,
        "scrape_type": "youtube_channel",
        "url": "https://www.youtube.com/@OCSheriff",
        "youtube_channel_id": "",
        "release_cadence": "45-60 day lag, per AB 748",
    },
]


# =========================================================================
# HELPER FUNCTIONS
# =========================================================================

def get_sources_by_region(region_id: str) -> list:
    """Get all BWC sources for a given region."""
    return [s for s in BWC_SOURCES if s["region_id"] == region_id]


def get_sources_by_tier(tier: int) -> list:
    """Get all BWC sources at a given tier."""
    return [s for s in BWC_SOURCES if s["tier"] == tier]


def get_scrapable_sources(scrape_types: list = None) -> list:
    """Get sources that can be automatically monitored.

    Args:
        scrape_types: Filter to specific types. Default: all non-manual.
    """
    if scrape_types is None:
        scrape_types = ["youtube_channel", "html_listing", "rss"]
    return [s for s in BWC_SOURCES if s["scrape_type"] in scrape_types]


def get_youtube_sources() -> list:
    """Get all YouTube channel sources."""
    return [s for s in BWC_SOURCES if s["scrape_type"] == "youtube_channel"]


def get_portal_sources() -> list:
    """Get all HTML listing portal sources."""
    return [s for s in BWC_SOURCES if s["scrape_type"] == "html_listing"]
