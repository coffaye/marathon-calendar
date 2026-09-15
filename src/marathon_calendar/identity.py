from __future__ import annotations

import hashlib
import re
import unicodedata


_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_KNOWN_ALIASES = {
    "nanchang": "南昌",
    "nan chang": "南昌",
    "shanghai": "上海",
    "beijing": "北京",
    "chengdu": "成都",
    "xiamen": "厦门",
    "chongqing": "重庆",
    "nanjing": "南京",
    "meishan": "眉山",
    "wuhan": "武汉",
    "wuxi": "无锡",
    "shijiazhuang": "石家庄",
    "yancheng": "盐城",
    "yangzhou": "扬州",
    "qingdao": "青岛",
    "dalian": "大连",
    "lanzhou": "兰州",
    "jilin": "吉林",
    "guiyang": "贵阳",
    "shenyang": "沈阳",
    "harbin": "哈尔滨",
    "taiyuan": "太原",
    "xian": "西安",
    "dongying": "东营",
    "changdu": "成都",
    "changzhou": "常州",
    "hangzhou": "杭州",
    "yichang": "宜昌",
    "xichang": "西昌",
    "gaochun": "高淳",
    "guilin": "桂林",
    "fangchenggang": "防城港",
    "huangshi": "黄石",
    "yiwu": "义乌",
    "guangzhou": "广州",
    "nanning": "南宁",
    "shenzhen": "深圳",
    "fuzhou": "福州",
    "miyun": "密云",
}

# The database keeps the existing Phase 2 alpha-3 representation.  These
# helpers make source adapters explicit about conversions and refuse to
# invent a country for an unknown value.
ALPHA2_TO_ALPHA3 = {
    "CN": "CHN", "JP": "JPN", "US": "USA", "GB": "GBR", "DE": "DEU",
    "KR": "KOR", "HK": "HKG", "TW": "TPE", "MO": "MAC", "TH": "THA",
    "AU": "AUS", "FR": "FRA", "IT": "ITA", "ES": "ESP", "CA": "CAN",
    "BR": "BRA", "IN": "IND", "SG": "SGP", "MY": "MAS", "ID": "INA",
    "CH": "SUI", "AT": "AUT", "NL": "NED", "BE": "BEL", "PT": "POR",
    "SE": "SWE", "NO": "NOR", "DK": "DEN", "FI": "FIN", "ZA": "RSA",
}
ALPHA3_TO_ALPHA2 = {value: key for key, value in ALPHA2_TO_ALPHA3.items()}
ALPHA3_TO_ALPHA2.update({"GER": "DE", "SUI": "CH", "RSA": "ZA", "GRE": "GR", "KSA": "SA"})
KNOWN_ALPHA3 = set(ALPHA3_TO_ALPHA2) | {
    "GER", "KSA", "QAT", "UAE", "COL", "CZE", "POL", "IRL", "RWA", "ARG", "TUR", "ROU", "MAR", "KEN", "ETH", "UGA", "MEX", "NZL", "ISL", "EST", "LTU", "LVA", "SVK", "SVN", "CRO", "BUL", "SRB", "UKR", "GRE", "IND", "MAS", "INA", "TPE", "HKG", "MAC", "DJI", "GAB", "LAT", "NGR", "PAK", "PER", "SLO", "SWE", "ZAM", "BRU", "KGZ", "VIE", "DZA", "AGO", "BAR", "BHU", "BOT", "CAM", "CHI", "CRC", "CUB", "CUW", "CYP", "DOM", "ECU", "EGY", "FRO", "GRL", "HUN", "ISR", "JAM", "KAZ", "KUW", "LAO", "LBN", "LUX", "MDV", "MLT", "MKD", "MDA", "NEP", "OMA", "PAN", "PUR", "SRI", "TJK", "TAN", "TTO", "TUN", "VEN", "ZIM", "UZB", "JOR", "GRC", "THA", "CHN", "JPN", "USA", "GBR", "DEU", "AUS", "AUT", "BRA", "CAN", "FRA", "ITA", "NED", "PRT", "SUI", "BEL", "DNK", "FIN", "NOR", "NZL", "SGP", "ZAF", "ZWE", "RUS", "SRB", "MYS", "IDN", "KOR", "VNM", "TWN", "MNG", "ARM", "GEO", "MNE", "BIH", "ALB", "BHR", "IRN", "IRQ", "LKA", "PAK", "PSE", "TZA", "TTO", "ZMB", "ZWE", "GHA", "UGA", "SEN", "CMR", "NGA", "GAB", "BFA", "MLI", "CIV", "GHA", "SOM", "SDN", "SYR", "LBN", "YEM", "OMN", "TJK", "TKM", "KGZ", "AFG", "BGD", "BTN", "MDG", "MUS", "SYC", "PRY", "URY", "BOL", "PER", "ECU", "VEN", "PAN", "CRI", "GTM", "HND", "SLV", "NIC", "CUB", "JAM", "HTI", "DOM", "PRI", "CAN", "USA", "MEX", "BRA", "CHL", "ARG", "GUY", "SUR", "FJI", "WSM", "PNG", "NZL",
}


def country_to_alpha3(value: str | None) -> str | None:
    if not value:
        return None
    code = value.strip().upper()
    if len(code) == 2:
        return ALPHA2_TO_ALPHA3.get(code)
    if len(code) == 3:
        if code == "GER":
            return "DEU"
        return code if code in KNOWN_ALPHA3 else None
    return None


def country_to_alpha2(value: str | None) -> str | None:
    if not value:
        return None
    code = value.strip().upper()
    if len(code) == 2:
        return code if code in ALPHA2_TO_ALPHA3 else None
    return ALPHA3_TO_ALPHA2.get(code)
_EVENT_SUFFIXES = (
    "half marathon",
    "marathon",
    "road race",
    "international",
    "race",
    "赛事",
    "马拉松",
    "半程",
    "全程",
    "赛",
)


def normalize_name(value: str) -> str:
    """Normalize a display name while preserving a human-auditable rule path."""

    value = unicodedata.normalize("NFKC", value).casefold().strip()
    value = _YEAR_RE.sub("", value)
    value = value.replace("–", "-").replace("—", "-")
    value = _PUNCT_RE.sub(" ", value)
    value = " ".join(value.split())
    for alias, replacement in sorted(_KNOWN_ALIASES.items(), key=lambda item: -len(item[0])):
        value = value.replace(alias, replacement)
    for suffix in _EVENT_SUFFIXES:
        value = value.replace(suffix, " ")
    return "".join(value.split())


def canonical_identity_key(
    *, name: str, country: str, city: str | None, race_type: str = "road_race"
) -> str:
    """Return a stable identity hash that deliberately excludes date and year."""

    name_part = normalize_name(name)
    city_part = normalize_location(city or "")
    canonical = "|".join((country.upper().strip(), city_part, name_part, race_type))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_location(value: str) -> str:
    """Normalize common Chinese administrative suffixes for city matching."""

    normalized = normalize_name(value)
    for suffix in ("特别行政区", "自治区", "市", "区", "县"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


def identity_explanation(name_a: str, name_b: str, *, country: str, city: str | None) -> str:
    left = normalize_name(name_a)
    right = normalize_name(name_b)
    if left == right:
        return f"normalized event names match: {left!r}; date/year excluded"
    return f"normalized names differ ({left!r} vs {right!r}); requires review"
