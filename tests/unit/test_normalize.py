from core.normalize import (
    normalize_domain,
    normalize_name,
    normalize_phone,
    tokens_match_one_to_one,
)


def test_normalize_name_strips_suffixes_and_punctuation():
    assert normalize_name("Acme Corp.") == "acme"
    assert normalize_name("Acme Corporation") == "acme"
    assert normalize_name("Blue Peak Systems, Inc.") == "blue peak systems"
    assert normalize_name("A&B Logistics") == "a and b logistics"
    assert normalize_name(None) is None


def test_normalize_domain():
    assert normalize_domain("https://www.Acme.com/about") == "acme.com"
    assert normalize_domain("WWW.ACME.COM") == "acme.com"
    assert normalize_domain("app.acme.com") == "app.acme.com"
    assert normalize_domain(None) is None


def test_normalize_phone():
    assert normalize_phone("(512) 555-0134") == "5125550134"
    assert normalize_phone("+1 512 555 0134") == "5125550134"
    assert normalize_phone("call us") is None


def test_token_containment_exact_and_suffix_variants():
    assert tokens_match_one_to_one("Acme Corp.", "Acme Corporation")
    assert tokens_match_one_to_one("Northwind Systems Inc", "Northwind Systems LLC")


def test_token_containment_tolerates_typos():
    assert tokens_match_one_to_one("Nortwhind Systems", "Northwind Systems")


def test_token_containment_rejects_subsidiaries_regardless_of_brand_length():
    # Long brand: a scalar ratio threshold would pass this pair; token logic must not.
    assert not tokens_match_one_to_one("Northwind", "Northwind Labs")
    assert not tokens_match_one_to_one("Continental", "Continental Media Inc")
    # Short brand.
    assert not tokens_match_one_to_one("Acme", "Acme Media")


def test_token_containment_rejects_different_names():
    assert not tokens_match_one_to_one("Acme Software", "Acme Logistics")
    assert not tokens_match_one_to_one("Blue Peak", "Iron Peak")
