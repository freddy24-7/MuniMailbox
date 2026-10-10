from app.db.models import Citizen
from app.privacy.entities import EntityType
from app.privacy.hints import known_values_for


def test_citizen_record_becomes_known_values() -> None:
    citizen = Citizen(
        name="Xyq van der Zzyzx",
        address="Kerkstraat 12, 1234 AB Teststad",
        bsn="000000000",
        email="xyq@example.org",
        phone="06-00000000",
    )
    known = {(value.entity_type, value.value) for value in known_values_for(citizen)}
    assert known == {
        (EntityType.PERSON, "Xyq van der Zzyzx"),
        (EntityType.PERSON, "Xyq"),
        (EntityType.PERSON, "van der Zzyzx"),
        (EntityType.ADDRESS, "Kerkstraat 12"),
        (EntityType.POSTCODE, "1234 AB"),
        (EntityType.BSN, "000000000"),
        (EntityType.EMAIL, "xyq@example.org"),
        (EntityType.PHONE, "06-00000000"),
    }
