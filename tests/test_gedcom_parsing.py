from pathlib import Path
import tempfile

from gedcom import parse_gedcom


def test_parse_gedcom_returns_people_and_families():
    fixture_path = Path(__file__).parent / "fixtures" / "sample.ged"

    data = parse_gedcom(str(fixture_path))

    assert "people" in data
    assert "families" in data
    assert len(data["people"]) == 3
    assert len(data["families"]) == 2

    first_person = data["people"][0]
    assert first_person["gedcom_id"] == "I1"
    assert first_person["first_name"] == "John"
    assert first_person["last_name"] == "Doe"
    assert first_person["sex"] == "M"
    assert first_person["birth_date"] == "1 JAN 2000"
    assert first_person["birth_place"] == "Springfield"
    assert first_person["occupation"] == "Engineer"
    assert first_person["note"] == "Test note"
    assert first_person["famc"] == ["F1"]
    assert first_person["fams"] == ["F2"]

    second_person = data["people"][1]
    assert second_person["death_date"] == "2 JAN 2020"
    assert second_person["death_place"] == "Metropolis"

    family = data["families"][0]
    assert family["gedcom_id"] == "F1"
    assert family["husband"] == "I1"
    assert family["wife"] == "I2"
    assert family["children"] == ["I3"]


def test_parse_gedcom_populates_name_fields_from_name_tag():
    gedcom = """0 HEAD
1 SOUR Test
0 @I1@ INDI
1 NAME John Doe
1 SEX M
1 BIRT
2 DATE 1 JAN 2000
0 @I2@ INDI
1 NAME Jane Smith
1 SEX F
1 DEAT
2 DATE 2 JAN 2020
"""

    with tempfile.NamedTemporaryFile("w", suffix=".ged", delete=False, encoding="utf-8") as handle:
        handle.write(gedcom)
        temp_path = handle.name

    try:
        data = parse_gedcom(temp_path)
        assert len(data["people"]) == 2
        assert data["people"][0]["first_name"] == "John"
        assert data["people"][0]["last_name"] == "Doe"
        assert data["people"][0]["birth_date"] == "1 JAN 2000"
        assert data["people"][1]["first_name"] == "Jane"
        assert data["people"][1]["last_name"] == "Smith"
        assert data["people"][1]["death_date"] == "2 JAN 2020"
    finally:
        Path(temp_path).unlink(missing_ok=True)
