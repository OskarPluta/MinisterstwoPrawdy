"""Default fact-check domains for live lookup (rag/live.py).

The database table `trusted_domains` is seeded once from this list (rag/db.py) and then
managed in the admin panel; live lookup reads the table, not this list.
"""

from __future__ import annotations

# Fact-checkers whose ClaimReview we accept (IFCN/EFCSN signatories and the Polish desks the crawler
# already uses). A review on any other domain is dropped. Subdomains match (factcheck.afp.com).
DEFAULT_TRUSTED_FACTCHECK_DOMAINS = (
    "demagog.org.pl", "sprawdzam.afp.com", "fakenews.pl", "oko.press", "konkret24.tvn24.pl",
    "pravda.org.pl", "afp.com", "reuters.com", "apnews.com", "fullfact.org", "correctiv.org",
    "politifact.com", "factcheck.org", "leadstories.com", "snopes.com", "maldita.es", "newtral.es",
    "euvsdisinfo.eu", "stopfake.org", "demagog.cz", "demagog.sk", "dpa-factchecking.com",
    "faktisk.no", "healthfeedback.org", "sciencefeedback.co", "climatefeedback.org", "africacheck.org",
    "chequeado.com", "aosfatos.org", "teyit.org", "factly.in", "boomlive.in", "lemonde.fr",
    "liberation.fr", "bbc.co.uk", "bbc.com", "usatoday.com", "washingtonpost.com", "voxukraine.org",
    "lrt.lt", "delfi.lt", "re-baltica.lv", "propastop.org", "mythdetector.com", "factcheck.ge",
)
