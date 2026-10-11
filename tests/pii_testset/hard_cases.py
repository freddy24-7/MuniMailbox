"""Emails that are hard to redact, as templates. All values are generated at test time.

When a redaction miss is found: add a template here that reproduces it, watch the recall
test report it, then fix the recognizer (CLAUDE.md, "Testing expectations").

Slots starting with `other_` describe a second person. The sender's own details are also
known from their citizen record; a second person's are not, so those slots test the
recognizers on their own.
"""

from app.db.models import Topic, Urgency
from scripts.generate_synthetic import EmailTemplate

HARD_CASES: tuple[EmailTemplate, ...] = (
    # Personal data in the subject line.
    EmailTemplate(
        Topic.BENEFITS,
        Urgency.NORMAL,
        "Uitkering {name} BSN {bsn}",
        "Geachte heer/mevrouw,\n\nZie het onderwerp. Wanneer hoor ik iets?\n\nGroet,\n{name}",
    ),
    # Identifiers written in groups.
    EmailTemplate(
        Topic.BENEFITS,
        Urgency.NORMAL,
        "Wijziging rekeningnummer",
        "Beste gemeente,\n\n"
        "Mijn burgerservicenummer is {bsn_dotted}. Wilt u mijn uitkering voortaan overmaken "
        "op {iban_spaced}? U kunt mij bellen op {phone_mobile}.\n\n"
        "Met vriendelijke groet,\n{name}",
    ),
    # A second person: partner.
    EmailTemplate(
        Topic.ADDRESS_CHANGE,
        Urgency.NORMAL,
        "Verhuizing samen met partner",
        "Geachte heer/mevrouw,\n\n"
        "Ik verhuis samen met mijn partner {other_name} (BSN {other_bsn}, geboren op "
        "{other_date_of_birth}) naar {street_address}, {postcode} {city}. "
        "Kunt u ons beiden inschrijven?\n\n"
        "Met vriendelijke groet,\n{name}",
    ),
    # A second person: complaint about a neighbour.
    EmailTemplate(
        Topic.COMPLAINTS,
        Urgency.NORMAL,
        "Overlast van de buren",
        "Geachte gemeente,\n\n"
        "Al maanden heb ik geluidsoverlast van mijn buurman {other_name}, die woont op "
        "{other_street_address}. Ik heb hem gebeld op {other_phone} maar hij neemt niet op. "
        "Wat kan de gemeente hieraan doen?\n\n"
        "Hoogachtend,\n{name}",
    ),
    # Writing on behalf of someone else.
    EmailTemplate(
        Topic.BENEFITS,
        Urgency.HIGH,
        "Namens mijn moeder",
        "Beste mensen,\n\n"
        "Ik schrijf namens mijn moeder, {other_name}. Zij woont op {other_street_address}, "
        "{other_postcode} en haar uitkering is niet gestort op {other_iban}. "
        "Haar geboortedatum is {other_date_of_birth}. U kunt haar mailen op {other_email} "
        "of mij bellen op {phone}.\n\n"
        "Vriendelijke groet,\n{name}",
    ),
    # A street name without a typical ending.
    EmailTemplate(
        Topic.WASTE_COLLECTION,
        Urgency.LOW,
        "Kapotte container",
        "Hallo,\n\n"
        "De papiercontainer bij mijn woning is kapot. Mijn adres is {plain_street_address} "
        "in {city}. Kan er een nieuwe komen?\n\n"
        "Groeten,\n{first_name}",
    ),
    # Sloppy typing: no capitals, postcode in lowercase, no closing formula.
    EmailTemplate(
        Topic.PERMITS,
        Urgency.LOW,
        "parkeervergunning",
        "hallo ik wil een parkeervergunning aanvragen. ik woon op postcode {postcode_lower} "
        "en mijn bsn is {bsn}. mijn nummer is {phone_mobile}\n\n{name_lower}",
    ),
    # A name at the start of the email and nowhere near a closing formula.
    EmailTemplate(
        Topic.OTHER,
        Urgency.NORMAL,
        "Afspraak maken",
        "Goedemorgen,\n\n"
        "Mijn naam is {name} en ik wil een afspraak maken voor het verlengen van mijn "
        "paspoort. Ik ben bereikbaar via {email}.\n\n"
        "Alvast bedankt.",
    ),
    # The second person signs too.
    EmailTemplate(
        Topic.PERMITS,
        Urgency.NORMAL,
        "Gezamenlijke aanvraag dakkapel",
        "Geachte heer/mevrouw,\n\n"
        "Samen met onze buren van {other_street_address} vragen wij een vergunning aan voor "
        "een doorlopende dakkapel. Ons adres is {street_address}.\n\n"
        "Met vriendelijke groeten,\n{name} en {other_name}",
    ),
    # A surname that is also an ordinary word ("Bos", "Koning"), in running text as in a
    # case note. Found in Stage 4: "Voorbeeld" was left standing after the first name.
    EmailTemplate(
        Topic.PERMITS,
        Urgency.NORMAL,
        "Aanvraag namens de buren",
        "Geachte heer/mevrouw,\n\n"
        "Ik dien de aanvraag voor de dakkapel in namens {other_word_name}, die naast mij "
        "woont. Kunt u de ontvangst bevestigen?\n\n"
        "Met vriendelijke groet,\n{name}",
    ),
    # A second person's town, once as part of an address and once in running text. The
    # sender's own town is known from their record; this one only the recognizers can find.
    EmailTemplate(
        Topic.ADDRESS_CHANGE,
        Urgency.NORMAL,
        "Inschrijving van mijn vader",
        "Geachte heer/mevrouw,\n\n"
        "Mijn vader woont nu op {other_street_address}, {other_postcode} {other_city} en "
        "komt bij mij wonen. Hij heeft zijn hele leven in {other_city} gewoond. Hoe schrijf "
        "ik hem in op mijn adres?\n\n"
        "Met vriendelijke groet,\n{name}",
    ),
    # A nine-digit number that is not a BSN must survive (precision, not recall), next
    # to data that must go.
    EmailTemplate(
        Topic.COMPLAINTS,
        Urgency.NORMAL,
        "Zaaknummer 200400600",
        "Geachte heer/mevrouw,\n\n"
        "Over zaak 200400600 heb ik nog niets gehoord. Mijn telefoonnummer is {phone}.\n\n"
        "Met vriendelijke groet,\n{name}",
    ),
)
