"""The kinds of thing a career is made of.

Six models with near-identical editing screens would mean twenty-four near-identical
view classes. A registry keeps one set of views and one set of templates, and adding a
seventh kind later means adding one entry here.
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
    "language": SectionSpec(
        "language",
        resume_models.LanguageSkill,
        resume_forms.LanguageSkillForm,
        _("Language"),
        _("Languages"),
    ),
}

#: The sections shown on the overview page, in the order a CV usually reads.
OVERVIEW_ORDER = ("experience", "education", "project", "skill-group", "certification", "language")
