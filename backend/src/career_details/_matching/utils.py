import re
import unicodedata

from src.career_details._matching.views import EmploymentEvidence, IdentityEvidence


def normalized(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def same_person(left: IdentityEvidence, right: IdentityEvidence) -> bool:
    """Require compatible names plus email or DOB; reject a contradictory DOB."""
    left_first, right_first = normalized(left.first_name).split(), normalized(right.first_name).split()
    last = normalized(left.last_name)
    if not left_first or not right_first or not last or last != normalized(right.last_name):
        return False
    if left_first[0] != right_first[0]:
        return False
    # Additional given names may be omitted, but two stated middle names cannot conflict.
    if any(a != b for a, b in zip(left_first, right_first)):
        return False
    if left.date_of_birth and right.date_of_birth and left.date_of_birth != right.date_of_birth:
        return False
    email_matches = bool(normalized(left.email)) and normalized(left.email) == normalized(right.email)
    dob_matches = left.date_of_birth is not None and left.date_of_birth == right.date_of_birth
    return email_matches or dob_matches


def employer_key(value: str | None) -> str:
    words = re.findall(r"\w+", normalized(value))
    # Suffix equivalence only filters candidates; it is never sufficient to match a role.
    suffixes = {"ag", "gmbh", "ltd", "limited", "inc", "incorporated", "llc", "corp", "corporation", "plc"}
    while words and words[-1] in suffixes:
        words.pop()
    return " ".join(words)


def employment_candidate(left: EmploymentEvidence, right: EmploymentEvidence) -> bool:
    if not employer_key(left.organization) or employer_key(left.organization) != employer_key(right.organization):
        return False
    if left.start_year is None or right.start_year is None:
        return False
    if left.start_month is not None and right.start_month is not None:
        delta = abs((left.start_year - right.start_year) * 12 + left.start_month - right.start_month)
        if delta > 1:
            return False
    elif left.start_year != right.start_year:
        return False
    # A known end before the other's start contradicts the match. Missing months
    # remain unknown, and an older CV may have an earlier end date or no end date.
    if left.end_year is not None and left.end_year < right.start_year:
        return False
    if right.end_year is not None and right.end_year < left.start_year:
        return False
    return True


def exact_employment(left: EmploymentEvidence, right: EmploymentEvidence) -> bool:
    return (
        employment_candidate(left, right)
        and bool(normalized(left.title))
        and normalized(left.title) == normalized(right.title)
        and (not left.city or not right.city or normalized(left.city) == normalized(right.city))
        and left.start_year == right.start_year
        and (left.start_month is None or right.start_month is None or left.start_month == right.start_month)
        and left.end_year == right.end_year
        and (left.end_month is None or right.end_month is None or left.end_month == right.end_month)
    )


def employment_identity_unchanged(left: EmploymentEvidence, right: EmploymentEvidence) -> bool:
    # Tailoring bullet points does not invalidate a known link. Role, employer,
    # location and dates do; those changes go back through matching.
    return left.model_dump(exclude={"description"}) == right.model_dump(exclude={"description"})
