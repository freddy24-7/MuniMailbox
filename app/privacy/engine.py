from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider

from app.privacy.entities import EntityType
from app.privacy.recognizers import (
    LANGUAGE,
    DutchBsnRecognizer,
    DutchPhoneRecognizer,
    numeric_date_recognizer,
    postcode_recognizer,
    signature_recognizer,
    street_address_recognizer,
)

# Presidio entity name -> our placeholder type. Anything not listed is not redacted
# (organisations, relative dates), because it does not identify the citizen.
ENTITY_MAP: dict[str, EntityType] = {
    "PERSON": EntityType.PERSON,
    "NL_SIGNATURE_NAME": EntityType.PERSON,
    "LOCATION": EntityType.ADDRESS,
    "NL_STREET_ADDRESS": EntityType.ADDRESS,
    "EMAIL_ADDRESS": EntityType.EMAIL,
    "PHONE_NUMBER": EntityType.PHONE,
    "NL_PHONE": EntityType.PHONE,
    "IBAN_CODE": EntityType.IBAN,
    "NL_BSN": EntityType.BSN,
    "NL_POSTCODE": EntityType.POSTCODE,
    "NL_DATE": EntityType.DATE_OF_BIRTH,
}


def build_analyzer(spacy_model: str) -> AnalyzerEngine:
    """Loads the spaCy model, which takes seconds: call once at startup, not per email."""
    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": LANGUAGE, "model_name": spacy_model}],
        }
    ).create_engine()
    registry = RecognizerRegistry(supported_languages=[LANGUAGE])
    registry.load_predefined_recognizers(languages=[LANGUAGE], nlp_engine=nlp_engine)
    for recognizer in (
        DutchBsnRecognizer(),
        DutchPhoneRecognizer(),
        street_address_recognizer(),
        postcode_recognizer(),
        numeric_date_recognizer(),
        signature_recognizer(),
    ):
        registry.add_recognizer(recognizer)
    return AnalyzerEngine(registry=registry, nlp_engine=nlp_engine, supported_languages=[LANGUAGE])
