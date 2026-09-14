"""Countries CALL-E can place calls to.

Six real calls to Morocco were dialled, charged and reported as "failed" with
no reason, because `MA` is not a destination CALL-E serves. Nothing in the
request is malformed, so the API accepts it with `201 Created` and the failure
only surfaces per attempt — which makes this the one configuration error the
integration cannot discover by reading its own payload.

Source: CALL-E Integrations, "Supported Regions and Languages", 13 September 2026.
"""

from __future__ import annotations

#: Country code -> calling code. `Local` and `International` lines alike; an
#: international line is placed from a CALL-E number and is meant for testing.
SUPPORTED_REGIONS: dict[str, str] = {
    "AE": "+971",
    "AU": "+61",
    "BD": "+880",
    "BR": "+55",
    "BW": "+267",
    "CA": "+1",
    "CM": "+237",
    "DE": "+49",
    "EG": "+20",
    "ES": "+34",
    "FI": "+358",
    "FR": "+33",
    "GB": "+44",
    "GH": "+233",
    "HN": "+504",
    "ID": "+62",
    "IE": "+353",
    "IL": "+972",
    "IN": "+91",
    "JP": "+81",
    "KE": "+254",
    "LK": "+94",
    "MX": "+52",
    "MY": "+60",
    "MZ": "+258",
    "NA": "+264",
    "NG": "+234",
    "NL": "+31",
    "OM": "+968",
    "PH": "+63",
    "PK": "+92",
    "PL": "+48",
    "SA": "+966",
    "SG": "+65",
    "TH": "+66",
    "TN": "+216",
    "TR": "+90",
    "TW": "+886",
    "UA": "+380",
    "US": "+1",
    "VN": "+84",
    "ZA": "+27",
}


#: Calling code -> country, longest prefix first. Several countries share +1;
#: the number alone cannot separate them, and both are supported anyway.
_BY_PREFIX: list[tuple[str, str]] = sorted(
    ((code, region) for region, code in SUPPORTED_REGIONS.items()),
    key=lambda pair: -len(pair[0]),
)


def region_of(phone: str | None) -> str | None:
    """The destination a number actually reaches, from its E.164 prefix.

    A candidate can declare `region: "US"` and carry a `+212` number. Checking
    the declared field validates a label; only the prefix says where the call
    goes — which is what CALL-E routes on, and what silently failed six times.
    Returns None when the prefix belongs to no supported country.
    """
    if not phone:
        return None
    digits = "+" + "".join(ch for ch in phone if ch.isdigit())
    for code, region in _BY_PREFIX:
        if digits.startswith(code):
            return region
    return None


def is_supported(region: str | None) -> bool:
    return bool(region) and region.upper() in SUPPORTED_REGIONS


def can_dial(phone: str | None) -> bool:
    """Whether CALL-E can route to this number at all."""
    return region_of(phone) is not None


def dialling_prefix(phone: str | None) -> str:
    """The country part of an E.164 number, for naming a destination we cannot
    reach. Best effort: without a match in the supported table there is no
    authoritative split, so the first three digits are shown."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return f"+{digits[:3]}" if digits else "an unknown destination"


def unsupported_region_message(region: str | None, phone: str = "") -> str:
    """Say what is wrong and what to do, not just that it failed."""
    where = f"{dialling_prefix(phone)} ({phone})" if phone else f"region {region!r}"
    return (
        f"CALL-E does not place calls to {where}. Supported destinations: "
        f"{', '.join(sorted(SUPPORTED_REGIONS))}. Set CALLE_DEFAULT_REGION and the "
        "DEMO_CARRIER_* numbers to one of them."
    )
