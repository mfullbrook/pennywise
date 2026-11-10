import pytest

from pennywise.engine.merchants import (
    Alias,
    alias_table_hash,
    clean,
    desc_key,
    normalise,
    suffix_variants,
)


def test_desc_key_only_uppercases_and_collapses_whitespace():
    assert desc_key("  Tesco   Stores 2345 ") == "TESCO STORES 2345"


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("AMZN MKTP UK*AB12CD34", "AMZN MKTP"),  # reference with digits, then country
        ("PAYPAL *NETFLIX", "PAYPAL *NETFLIX"),  # the merchant after the star is kept
        ("TESCO STORES 2345", "TESCO STORES"),  # store number
        ("PRET A MANGER LONDON GB", "PRET A MANGER"),  # trailing location words
        ("SHELL #1234", "SHELL"),
        ("LONDON", "LONDON"),  # never strip a descriptor down to nothing
        ("12345678", "12345678"),
    ],
)
def test_clean(raw, cleaned):
    assert clean(raw) == cleaned


def test_without_aliases_the_merchant_is_the_cleaned_descriptor():
    assert normalise("TESCO STORES 2345") == normalise("Tesco Stores 9876") == "TESCO STORES"


def test_longest_prefix_alias_wins():
    aliases = (
        Alias("TESCO", "prefix", "Tesco"),
        Alias("TESCO MOBILE", "prefix", "Tesco Mobile"),
    )
    assert normalise("TESCO MOBILE 0800", aliases) == "Tesco Mobile"
    assert normalise("TESCO STORES 2345", aliases) == "Tesco"


def test_prefix_aliases_beat_regex_aliases():
    aliases = (
        Alias("UBER", "prefix", "Uber"),
        Alias(r"\bEATS\b", "regex", "Takeaway"),
        Alias(r"NETFLIX", "regex", "Netflix"),
    )
    assert normalise("UBER *EATS", aliases) == "Uber"
    assert normalise("PAYPAL *NETFLIX", aliases) == "Netflix"


def test_seeds_keep_sub_brands_apart():
    import csv

    from pennywise.layers import SEED_ALIASES

    with SEED_ALIASES.open() as seed_file:
        aliases = tuple(
            Alias(desc_key(r["pattern"]), r["match_type"], r["merchant"])
            for r in csv.DictReader(seed_file)
        )
    assert normalise("TESCO STORES 2345", aliases) == "Tesco"
    assert normalise("TESCO PFS 4021", aliases) == "Tesco Petrol"
    assert normalise("UBER *TRIP", aliases) == "Uber"
    assert normalise("UBER *EATS", aliases) == "Uber Eats"
    assert normalise("AMAZON PRIME*1A2B3", aliases) == "Amazon Prime"


def test_alias_table_hash_ignores_order():
    a, b = Alias("AMZN", "prefix", "Amazon"), Alias("TESCO", "prefix", "Tesco")
    assert alias_table_hash([a, b]) == alias_table_hash([b, a])
    assert alias_table_hash([a]) != alias_table_hash([a, b])


def test_suffix_variants_map_town_suffixes_to_the_longest_known_merchant():
    known = ["THE RED LION", "THE RED", "Google", "PAYPAL", "TK MAXX"]
    names = [
        "THE RED LION BRISTOL",
        "THE RED LION",
        "THE REDWOOD INN",
        "GOOGLE CLOUD LONDON",
        "GOOGLE *YOUTUBE",
        "PAYPAL GIFTS",
        "TK MAXX LEEDS WEST YORKSHIRE",
    ]
    assert suffix_variants(names, known) == {
        "THE RED LION BRISTOL": "THE RED LION",
        "GOOGLE CLOUD LONDON": "Google",
        "TK MAXX LEEDS WEST YORKSHIRE": "TK MAXX",
    }
