from __future__ import annotations

SUPPORTED_STATE_VALUES = {
    "AL": "AL - Alabama",
    "AK": "AK - Alaska",
    "AS": "AS - American Samoa",
    "AZ": "AZ - Arizona",
    "AR": "AR - Arkansas",
    "CA": "CA - California",
    "CO": "CO - Colorado",
    "CT": "CT - Connecticut",
    "DE": "DE - Delaware",
    "DC": "DC - District of Columbia",
    "FL": "FL - Florida",
    "FM": "FM - Federated States of Micronesia",
    "GA": "GA - Georgia",
    "GU": "GU - Guam",
    "HI": "HI - Hawaii",
    "ID": "ID - Idaho",
    "IL": "IL - Illinois",
    "IN": "IN - Indiana",
    "IA": "IA - Iowa",
    "KS": "KS - Kansas",
    "KY": "KY - Kentucky",
    "LA": "LA - Louisiana",
    "ME": "ME - Maine",
    "MH": "MH - Marshall Islands",
    "MD": "MD - Maryland",
    "MA": "MA - Massachusetts",
    "MI": "MI - Michigan",
    "MN": "MN - Minnesota",
    "MS": "MS - Mississippi",
    "MO": "MO - Missouri",
    "MT": "MT - Montana",
    "NE": "NE - Nebraska",
    "NV": "NV - Nevada",
    "NH": "NH - New Hampshire",
    "NJ": "NJ - New Jersey",
    "NM": "NM - New Mexico",
    "NY": "NY - New York",
    "NC": "NC - North Carolina",
    "ND": "ND - North Dakota",
    "MP": "MP - Northern Mariana Islands",
    "OH": "OH - Ohio",
    "OK": "OK - Oklahoma",
    "OR": "OR - Oregon",
    "PW": "PW - Palau",
    "PA": "PA - Pennsylvania",
    "PR": "PR - Puerto Rico",
    "RI": "RI - Rhode Island",
    "SC": "SC - South Carolina",
    "SD": "SD - South Dakota",
    "TN": "TN - Tennessee",
    "TX": "TX - Texas",
    "UT": "UT - Utah",
    "VT": "VT - Vermont",
    "VA": "VA - Virginia",
    "VI": "VI - U.S. Virgin Islands",
    "WA": "WA - Washington",
    "WV": "WV - West Virginia",
    "WI": "WI - Wisconsin",
    "WY": "WY - Wyoming",
}

SUPPORTED_STATE_CODES = tuple(SUPPORTED_STATE_VALUES.keys())


def normalize_state_code(code: str) -> str:
    normalized = code.strip().upper()
    if normalized not in SUPPORTED_STATE_VALUES:
        raise ValueError(f"Unsupported state/territory code: {code}")
    return normalized


def state_payload_value(code: str) -> str:
    return SUPPORTED_STATE_VALUES[normalize_state_code(code)]
