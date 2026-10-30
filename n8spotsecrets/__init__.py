"""n8spotsecrets — one address, one report, from free public data.

    from spotsecrets import build_report
    r = build_report(address="548 S Spring St, Los Angeles, CA 90013", census_key=os.environ.get("CENSUS_API_KEY"))

Stdlib only. No keys except the free Census one for the price section.
"""
from .report import build_report, VERSION  # noqa: F401

__all__ = ["build_report", "VERSION"]
