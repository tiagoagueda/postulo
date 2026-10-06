"""The kinds of thing a career is made of.

Many models with near-identical editing screens would mean as many near-identical view
classes. A registry keeps one set of views and one set of templates. A new kind is an entry
here and a dozen other places; ``tests/test_career_sections.py`` is the list of those
places, and fails naming the section and the list a new kind is missing from.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from . import forms as resume_forms
from . import models as resume_models


@dataclass(frozen=True)
class SectionSpec:
    slug: str
    model: type
    form: type
    label: str
    plural: str
    blurb: str = ""


SECTIONS: dict[str, SectionSpec] = {
    "experience": SectionSpec(
        "experience",
        resume_models.Experience,
        resume_forms.ExperienceForm,
        _("Experience"),
        _("Experience"),
        _("Every role you have held. Write the highlights once; tailor them per CV later."),
    ),
    "education": SectionSpec(
        "education",
        resume_models.Education,
        resume_forms.EducationForm,
        _("Education"),
        _("Education"),
    ),
    "project": SectionSpec(
        "project", resume_models.Project, resume_forms.ProjectForm, _("Project"), _("Projects")
    ),
    "publication": SectionSpec(
        "publication",
        resume_models.Publication,
        resume_forms.PublicationForm,
        _("Publication"),
        _("Publications"),
        _("Papers, books, chapters, theses, datasets and software, as a bibliography lists them."),
    ),
    "skill-group": SectionSpec(
        "skill-group",
        resume_models.SkillGroup,
        resume_forms.SkillGroupForm,
        _("Skill group"),
        _("Skills"),
        _("Group related skills under a heading, such as “Languages” or “Infrastructure”."),
    ),
    "skill": SectionSpec(
        "skill", resume_models.Skill, resume_forms.SkillForm, _("Skill"), _("Skills")
    ),
    "certification": SectionSpec(
        "certification",
        resume_models.Certification,
        resume_forms.CertificationForm,
        _("Certification"),
        _("Certifications"),
    ),
    "driving-licence": SectionSpec(
        "driving-licence",
        resume_models.DrivingLicence,
        resume_forms.DrivingLicenceForm,
        _("Driving licence"),
        _("Driving licences"),
        _(
            "The categories you hold, as codes. Postulo never keeps the licence number, a "
            "photograph or anything else a licence carries."
        ),
    ),
    "link": SectionSpec(
        "link",
        resume_models.Link,
        resume_forms.LinkForm,
        _("Link"),
        _("Links"),
        _("A portfolio, a profile, a video: work of yours that already lives somewhere."),
    ),
    "language": SectionSpec(
        "language",
        resume_models.LanguageSkill,
        resume_forms.LanguageSkillForm,
        _("Language"),
        _("Languages"),
    ),
}

#: The sections shown on the overview page, in the order a CV usually reads.
OVERVIEW_ORDER = (
    "experience",
    "education",
    "project",
    "publication",
    "link",
    "skill-group",
    "certification",
    "driving-licence",
    "language",
)
