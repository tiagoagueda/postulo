"""The career record an importer fills in, and writing it onto somebody's profile.

Postulo's half of an import, and deliberately not the importer's. An importer -- Europass
or anybody else's -- turns bytes into a record and touches no database; this takes the
record and adds it. That is what keeps every importer, including one somebody else writes,
from needing ownership scoping of its own (#129).

**Only ever adds.** Blank fields on the profile are filled, because a blank is not an
opinion; a field that already says something is left exactly as it is, because somebody's
own words about themselves beat a form they filled in years ago. Nothing is overwritten and
nothing is removed: a duplicate is the person's to delete, and far better than something
lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils.translation import gettext as _

from postulo.accounts import identifiers
from postulo.core import phone_numbers


@dataclass
class Record:
    """A career record, in Postulo's terms rather than Europass's.

    The intermediate shape both readers produce. Everything downstream works on this, so
    the JSON reader is a second front door and not a second mapping.
    """

    #: Personal details, to fill blanks on the profile and never to overwrite.
    person: dict = field(default_factory=dict)
    experience: list[dict] = field(default_factory=list)
    education: list[dict] = field(default_factory=list)
    languages: list[dict] = field(default_factory=list)
    skill_groups: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    #: Which of the two formats this came out of: ``"xml"`` or ``"json"``.
    source: str = ""
    #: What was in the file and could not be read, in words, to be shown before importing.
    skipped: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.experience, self.education, self.languages, self.skill_groups, self.projects)
        )

    def counts(self) -> dict[str, int]:
        return {
            "experience": len(self.experience),
            "education": len(self.education),
            "languages": len(self.languages),
            "skills": sum(len(group["skills"]) for group in self.skill_groups),
            "projects": len(self.projects),
        }


@dataclass
class Report:
    """What an import actually added."""

    added: dict[str, int] = field(default_factory=dict)
    profile_filled: list[str] = field(default_factory=list)
    #: Entries that were read but not written, in words, so nothing goes missing quietly.
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.added.values())


def _write_address(profile, owner, parts: dict) -> bool:
    """Put a read address on a profile that has none. Returns whether anything was written.

    Only where the profile has no address at all: an import fills blanks and never argues
    with what somebody entered. An address is not verified and never will be -- Postulo is
    not going to post anything -- so there is nothing here to earn or to lose (#92).
    """
    from postulo.core import postal
    from postulo.core.models import PostalAddress

    if not any((parts.get(key) or "").strip() for key in ("street", "postcode", "municipality")):
        return False
    if postal.for_holder(profile).exists():
        return False
    PostalAddress.objects.create(
        owner=owner,
        holder=profile,
        street=(parts.get("street") or "")[:400],
        postcode=(parts.get("postcode") or "")[:20],
        municipality=(parts.get("municipality") or "")[:120],
        region=(parts.get("region") or "")[:120],
        country=(parts.get("country") or "")[:2],
        is_primary=True,
    )
    return True


def apply(owner, record: Record) -> Report:
    """Write what was found. Only ever adds; nothing existing is changed or removed.

    Blank fields on the profile are filled, because a blank is not an opinion. A field
    that already says something is left exactly as it is — somebody's own words about
    themselves beat a form they filled in years ago.
    """
    from postulo.accounts.models import PersonIdentifier

    from .models import Education, Experience, LanguageSkill, Project, Skill, SkillGroup

    report = Report()

    profile = getattr(owner, "profile", None)
    if profile is not None and record.person:
        changed = []
        for field_name in ("headline", "location", "website"):
            value = record.person.get(field_name)
            if value and not getattr(profile, field_name, ""):
                setattr(profile, field_name, value[:200])
                changed.append(field_name)
        # The telephone number is a row of its own now, and the same rule applies to it:
        # filled in only where there is nothing there, so an import never overwrites what
        # somebody typed. A number this instance already holds is left alone rather than
        # failing the whole import over one field of a CV.
        number = (record.person.get("phone") or "").strip()[:40]
        wrote_number = False
        if (
            number
            and phone_numbers.primary_for(profile) is None
            and not phone_numbers.taken_elsewhere(number)
        ):
            # Saved on a row of its own, so deliberately not in `changed`: that list names
            # columns for `update_fields`, and there is no phone column any more.
            phone_numbers.save_only_number(profile, owner, number)
            wrote_number = True
        if changed:
            fields = [*changed, "updated_at"] if hasattr(profile, "updated_at") else changed
            profile.save(update_fields=fields)
        # The address, which until #92 was thrown away on the way in: a Europass file
        # carries a street and a postcode and Postulo kept only the town and the country.
        # Same rule as everything else here -- written only where there is nothing, so an
        # import never overwrites an address somebody typed themselves.
        wrote_address = _write_address(profile, owner, record.person.get("address") or {})
        report.profile_filled = [
            *changed,
            *(["phone"] if wrote_number else []),
            *(["address"] if wrote_address else []),
        ]

        # An ORCID somebody already has is theirs; a second one is not an improvement.
        orcid = record.person.get("orcid")
        if orcid and not profile.identifiers.filter(scheme=identifiers.ORCID).exists():
            PersonIdentifier.objects.create(profile=profile, scheme=identifiers.ORCID, value=orcid)
            report.profile_filled.append("orcid")

    for field_name in ("first_name", "last_name"):
        value = record.person.get(field_name)
        if value and not getattr(owner, field_name, ""):
            setattr(owner, field_name, value[:150])
            owner.save(update_fields=[field_name])
            report.profile_filled.append(field_name)

    for entry in record.experience:
        if not entry.get("start_date"):
            # Experience needs a start; without one there is nothing to order it by. Said
            # out loud rather than dropped, so a half-dated file does not lose a job
            # quietly.
            report.skipped.append(
                str(
                    _("%(role)s: no start date, so it was not added.")
                    % {"role": entry["role"] or entry["organisation"]}
                )
            )
            continue
        Experience.objects.create(
            owner=owner,
            role=entry["role"][:200],
            organisation=entry["organisation"][:200],
            location=entry["location"][:200],
            start_date=entry["start_date"],
            end_date=entry["end_date"],
            summary=entry["summary"],
        )
        report.added["experience"] = report.added.get("experience", 0) + 1

    for entry in record.education:
        Education.objects.create(
            owner=owner,
            qualification=entry["qualification"][:200],
            institution=entry["institution"][:200],
            location=entry["location"][:200],
            start_date=entry["start_date"],
            end_date=entry["end_date"],
            grade=entry["grade"][:100],
            highlights=entry["highlights"],
        )
        report.added["education"] = report.added.get("education", 0) + 1

    for entry in record.languages:
        LanguageSkill.objects.create(
            owner=owner,
            name=entry["name"][:100],
            proficiency=entry["proficiency"] or "b1",
        )
        report.added["languages"] = report.added.get("languages", 0) + 1

    for group in record.skill_groups:
        group_name = group["name"][:100]
        # A heading somebody already has is reused. Two "Languages" headings on one CV is
        # a mess they then have to tidy, and adding the skills under the existing one is
        # still only ever adding.
        made = SkillGroup.objects.filter(owner=owner, name=group_name).first()
        if made is None:
            made = SkillGroup.objects.create(owner=owner, name=group_name)
            report.added["skill_groups"] = report.added.get("skill_groups", 0) + 1
        for name in group["skills"]:
            Skill.objects.create(owner=owner, group=made, name=name[:100])
            report.added["skills"] = report.added.get("skills", 0) + 1

    for entry in record.projects:
        Project.objects.create(
            owner=owner, name=entry["name"][:200], summary=entry.get("summary", "")
        )
        report.added["projects"] = report.added.get("projects", 0) + 1

    return report
