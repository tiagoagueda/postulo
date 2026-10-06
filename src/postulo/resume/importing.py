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

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from postulo.accounts import identifiers
from postulo.core import languages, phone_numbers, phones, web_links

from . import ordering


@dataclass
class Record:
    """A career record, in Postulo's terms rather than Europass's.

    The intermediate shape every reader produces. Everything downstream works on this, so
    another format is another front door and not another mapping.
    """

    #: Personal details, to fill blanks on the profile and never to overwrite.
    person: dict = field(default_factory=dict)
    experience: list[dict] = field(default_factory=list)
    education: list[dict] = field(default_factory=list)
    languages: list[dict] = field(default_factory=list)
    skill_groups: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    #: Which format this came out of, as the importer names it. Europass says ``"candidate"``
    #: for the XML europass.europa.eu writes, ``"xml"`` and ``"json"`` for the format of the
    #: editor before it, and puts ``"pdf-"`` in front of the one it found attached to a PDF.
    source: str = ""
    #: The language the file says it was written in, as it wrote it. Every Europass export
    #: carries one and Postulo read none of them, so a career typed in Portuguese arrived
    #: with `record_language` blank -- which means "the same as the interface", and the
    #: fallback warnings from #131 then fired on every entry of a CV that needed no
    #: translation at all (#235).
    locale: str = ""
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


def _is_an_address(parts: dict) -> bool:
    """Whether what a file said about where somebody lives is enough to keep as an address."""
    return any((parts.get(key) or "").strip() for key in ("street", "postcode", "municipality"))


def _new_address(profile, owner, parts: dict):
    """The address a file would give a profile that has none, or nothing.

    Unsaved. Nothing where the file has no address, where the profile already has one, or
    where the account already lists this one anywhere -- for a contact, say: an address is
    listed once per account, whoever it is for, and saving a second would fail the import
    (#616). The last of the three is the one the caller says in words.
    """
    from postulo.core import postal
    from postulo.core.models import PostalAddress

    if not _is_an_address(parts) or postal.for_holder(profile).exists():
        return None
    return PostalAddress(
        owner=owner,
        holder=profile,
        street=(parts.get("street") or "")[:400],
        postcode=(parts.get("postcode") or "")[:20],
        municipality=(parts.get("municipality") or "")[:120],
        region=(parts.get("region") or "")[:120],
        country=(parts.get("country") or "")[:2],
        is_primary=True,
    )


def _listed_already(owner, address) -> bool:
    from postulo.core.models import PostalAddress

    key = address.comparable_form()
    return bool(key) and PostalAddress.objects.filter(owner=owner, comparable=key).exists()


def _write_address(profile, owner, parts: dict, report: Report) -> bool:
    """Put a read address on a profile that has none. Returns whether anything was written.

    Only where the profile has no address at all: an import fills blanks and never argues
    with what somebody entered. An address is not verified and never will be -- Postulo is
    not going to post anything -- so there is nothing here to earn or to lose (#92).
    """
    address = _new_address(profile, owner, parts)
    if address is None:
        return False
    if _listed_already(owner, address):
        report.skipped.append(
            str(
                _(
                    "The address %(address)s is already listed for somebody you deal with, "
                    "so it was not added."
                )
                % {"address": address.one_line()}
            )
        )
        return False
    address.save()
    return True


def _clean_website(owner, site: str, report: Report) -> str:
    """The address as the link form would keep it, or nothing, with a sentence saying why.

    Only an http(s) address is a website: the form's URL field also lets ``ftp`` through,
    and a record is no place for that.
    """
    if not site:
        return ""
    from postulo.core.web_links import WebLink, WebLinkForm

    data = {"url": site}
    form = WebLinkForm(data=data, instance=WebLink(owner=owner, kind=web_links.Kind.WEBSITE))
    scheme = site.partition(":")[0].lower() if "://" in site else ""
    if (scheme and scheme not in ("http", "https")) or not form.is_valid():
        report.skipped.append(
            str(
                _("The website “%(value)s” is not a valid address, so it was not added.")
                % {"value": site[:80]}
            )
        )
        return ""
    return form.cleaned_data["url"]


def language_code_of(entry: dict) -> str:
    """The code a language of a record is chosen by, or nothing.

    What an importer says, where it is shaped like a tag and Postulo has a name for it: a
    code nobody here can name -- a sign language -- would print as itself, lower case, where
    the file's own name read better. Otherwise the language the name is exactly, which is
    what lets a reader that only has names (the older Europass formats) give the code too
    (#689). Never a guess: a name two languages share is nothing.
    """
    from postulo.core import language_names

    code = str(entry.get("code") or "")
    if languages.well_formed(code) and language_names.known(code):
        return languages.tag(code)
    return language_names.match(entry.get("name", ""))


def apply(owner, record: Record) -> Report:
    """Write what was found. Only ever adds; nothing existing is changed or removed.

    Blank fields on the profile are filled, because a blank is not an opinion. A field
    that already says something is left exactly as it is — somebody's own words about
    themselves beat a form they filled in years ago.
    """
    from postulo.accounts.models import PersonIdentifier
    from postulo.core import postal

    from .models import Education, Experience, LanguageSkill, Project, Skill, SkillGroup

    report = Report()

    profile = getattr(owner, "profile", None)
    # The locale is worth writing even where a file carries no personal details at all: it
    # is about the career record, not about the person (#235).
    if profile is not None and (record.person or record.locale.strip()):
        changed = []
        # A blank location is an answer since #309: it prints the town and country of the
        # primary address, and follows that address when it changes. So the location is
        # left blank wherever an address will say it -- one the profile already has, or the
        # one this same file is about to give it below. Filling it pinned the location to
        # text the person never typed, which printed less than the blank would have
        # ("Lisboa" where the address gives "Lisboa, Portugal") and stayed behind when the
        # address moved. Only a file with a place and no address still writes one.
        address = record.person.get("address") or {}
        new_address = _new_address(profile, owner, address)
        an_address_says_where = postal.primary_for(profile) is not None or (
            new_address is not None and not _listed_already(owner, new_address)
        )
        for field_name in ("headline", "location"):
            if field_name == "location" and an_address_says_where:
                continue
            value = record.person.get(field_name)
            if value and not getattr(profile, field_name, ""):
                setattr(profile, field_name, value[:200])
                changed.append(field_name)
        # What language the career itself is written in, which the file has always said and
        # nothing here read. Same rule as every other field: only where it is blank (#235).
        # Held to the shape of a tag here as well as by whichever importer read it: a
        # plugin fills this, and what is not a language is left out rather than cut to
        # fit a column (#337).
        if languages.well_formed(record.locale) and not profile.record_language:
            profile.record_language = languages.tag(record.locale)
            changed.append("record_language")
        # The website is a row of its own now (#189), filled on the same terms as the
        # number below: only where there is nothing, so an import never overwrites what
        # somebody typed.
        #
        # Read the way the page reads it (#610): a record's website is whatever the file
        # said, and a plugin's is whatever it likes. What the link form would refuse is
        # reported and not stored, since it would be printed on the CV.
        site = _clean_website(owner, (record.person.get("website") or "").strip()[:500], report)
        wrote_site = False
        if site and web_links.primary_for(profile, web_links.Kind.WEBSITE) is None:
            # A link is unique on its holder and address whatever its kind, so one the
            # profile keeps as its social profile or repository is not added again as its
            # website: the insert would fail the whole import (#616).
            same = web_links.same_address(site)
            if any(web_links.same_address(row.url) == same for row in profile.web_links.all()):
                report.skipped.append(
                    str(
                        _("The website %(site)s is already among your links, so it was not added.")
                        % {"site": site}
                    )
                )
            else:
                web_links.save_only_link(profile, owner, web_links.Kind.WEBSITE, site)
                wrote_site = True
        # The telephone number is a row of its own now, and the same rule applies to it:
        # filled in only where there is nothing there, so an import never overwrites what
        # somebody typed. A number this instance already holds is left alone rather than
        # failing the whole import over one field of a CV.
        #
        # Read against its country's numbering plan on the way in (#304), as the field on
        # the page reads one: a file that wrote the national form after the dialling code
        # -- `+44` and then `07911 123456` -- is kept as the number that can be dialled,
        # and Italy's zero stays where Italy puts it. A number the plan calls impossible is
        # not a reason to refuse a CV: it is kept as the file has it, and *Your details*
        # marks it.
        number = phones.combine((record.person.get("phone") or "").strip(), "")[:40]
        wrote_number = False
        if number and phone_numbers.primary_for(profile) is None:
            # Asked the way the form asks it (#615): a number held elsewhere spends one of
            # this account's answers, and once they are spent a free number is refused in
            # the same words, because writing one and not the other would be the answer.
            # The rest of the import goes on.
            refused = phone_numbers.refusal(owner, number)
            if refused:
                report.skipped.append(f"{number}: {refused}")
            else:
                # Saved on a row of its own, so deliberately not in `changed`: that list
                # names columns for `update_fields`, and there is no phone column any more.
                phone_numbers.save_only_number(profile, owner, number)
                wrote_number = True
        if changed:
            fields = [*changed, "updated_at"] if hasattr(profile, "updated_at") else changed
            profile.save(update_fields=fields)
        # The address, which until #92 was thrown away on the way in: a Europass file
        # carries a street and a postcode and Postulo kept only the town and the country.
        # Same rule as everything else here -- written only where there is nothing, so an
        # import never overwrites an address somebody typed themselves.
        wrote_address = _write_address(profile, owner, address, report)
        report.profile_filled = [
            *changed,
            *(["website"] if wrote_site else []),
            *(["phone"] if wrote_number else []),
            *(["address"] if wrote_address else []),
        ]

        # An ORCID somebody already has is theirs; a second one is not an improvement.
        orcid = record.person.get("orcid")
        if orcid and not profile.identifiers.filter(scheme=identifiers.ORCID).exists():
            try:
                orcid = identifiers.clean(identifiers.ORCID, str(orcid))
            except ValidationError:
                report.skipped.append(
                    str(
                        _("The ORCID iD “%(value)s” is not valid, so it was not added.")
                        % {"value": str(orcid)[:40]}
                    )
                )
            else:
                PersonIdentifier.objects.create(
                    profile=profile, scheme=identifiers.ORCID, value=orcid
                )
                report.profile_filled.append("orcid")

    for field_name in ("first_name", "last_name"):
        value = record.person.get(field_name)
        if value and not getattr(owner, field_name, ""):
            setattr(owner, field_name, value[:150])
            owner.save(update_fields=[field_name])
            report.profile_filled.append(field_name)

    # Each section is placed as an entry typed by hand would be (#203, #618): by its date
    # where it has one, last where it has not, and dense numbers after. The rows are built
    # here and written once per section by `ordering.place_many`, which calls no `save`.
    from .companies import existing as company_named

    experience = []
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
        experience.append(
            Experience(
                owner=owner,
                role=entry["role"][:200],
                organisation=entry["organisation"][:200],
                # Linked where the name is one of the person's companies, and never added:
                # a file must not fill the list (#683).
                company=company_named(owner, entry["organisation"][:200]),
                location=entry["location"][:200],
                start_date=entry["start_date"],
                end_date=entry["end_date"],
                summary=entry["summary"],
            )
        )
    ordering.place_many(Experience, owner, experience)
    if experience:
        report.added["experience"] = len(experience)

    education = []
    for entry in record.education:
        education.append(
            Education(
                owner=owner,
                qualification=entry["qualification"][:200],
                institution=entry["institution"][:200],
                location=entry["location"][:200],
                start_date=entry["start_date"],
                end_date=entry["end_date"],
                grade=entry["grade"][:100],
                eqf_level=entry.get("eqf_level"),
                highlights=entry["highlights"],
            )
        )
    ordering.place_many(Education, owner, education)
    if education:
        report.added["education"] = len(education)

    spoken = []
    for entry in record.languages:
        # A level the file did not state is left unset rather than guessed at. `or "b1"`
        # meant that anybody whose Europass CV listed a language without CEFR levels -- which
        # is most of them, because the editor never made the five boxes compulsory -- ended
        # up claiming B1 in it, on a CV, without ever having said so (#235).
        spoken.append(
            LanguageSkill(
                owner=owner,
                name=entry["name"][:100],
                code=language_code_of(entry),
                proficiency=entry["proficiency"],
            )
        )
    ordering.place_many(LanguageSkill, owner, spoken)
    if spoken:
        report.added["languages"] = len(spoken)

    skills = []
    for group in record.skill_groups:
        group_name = group["name"][:100]
        # A heading somebody already has is reused. Two "Languages" headings on one CV is
        # a mess they then have to tidy, and adding the skills under the existing one is
        # still only ever adding.
        made = SkillGroup.objects.filter(owner=owner, name=group_name).first()
        if made is None:
            made = SkillGroup(owner=owner, name=group_name)
            ordering.place_many(SkillGroup, owner, [made])
            report.added["skill_groups"] = report.added.get("skill_groups", 0) + 1
        for name in group["skills"]:
            skill = Skill(owner=owner, group=made, name=name[:100])
            # `bulk_create` calls no `save`, which is where a skill works out which ESCO
            # skill its name is.
            skill.match()
            skills.append(skill)
    ordering.place_many(Skill, owner, skills)
    if skills:
        report.added["skills"] = len(skills)

    projects = [
        Project(owner=owner, name=entry["name"][:200], summary=entry.get("summary", ""))
        for entry in record.projects
    ]
    ordering.place_many(Project, owner, projects)
    if projects:
        report.added["projects"] = len(projects)

    return report
