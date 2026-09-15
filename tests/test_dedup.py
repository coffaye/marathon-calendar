from marathon_calendar.identity import canonical_identity_key, identity_explanation, normalize_name


def test_chinese_and_english_variants_have_same_identity():
    names = ["2026南昌马拉松", "南昌马拉松", "2026 Nanchang Marathon"]
    keys = {canonical_identity_key(name=name, country="CHN", city="南昌") for name in names}
    assert len(keys) == 1
    assert normalize_name(names[0]) == normalize_name(names[2]) == "南昌"


def test_half_marathon_suffix_does_not_break_name_normalization():
    assert normalize_name("2026 Nanchang Half Marathon") == "南昌"


def test_date_is_not_part_of_identity():
    old = canonical_identity_key(name="南昌马拉松", country="CHN", city="南昌")
    new = canonical_identity_key(name="2026南昌马拉松", country="CHN", city="南昌")
    assert old == new
    assert "date/year excluded" in identity_explanation("2026南昌马拉松", "南昌马拉松", country="CHN", city="南昌")


def test_different_city_is_not_automatically_same():
    assert canonical_identity_key(name="马拉松", country="CHN", city="南昌") != canonical_identity_key(
        name="马拉松", country="CHN", city="广州"
    )
