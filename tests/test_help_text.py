"""Every field that needs a sentence has one, and the ones that do not are written down (#205).

A count over every form class in the application found **240 fields, 73 with a help text**.
Where a field had one it was good; where it did not, the label was doing all the work — and
a label can name a field, it cannot say what goes in it, in what form, or what Postulo does
with it afterwards.

`EXCUSED` is the other half. *Name*, *First name*, *Title* need nothing, and a sentence
under each of them would be noise that teaches people to stop reading the ones that matter.
So the fields without help are listed here by hand, which makes each one a decision
somebody made rather than a field nobody got to.

**Forms are built, not read off the class.** Several fields are given their sentence in
`__init__` — allauth builds the signup fields from a setting, the server's mail form adds
transport fields at run time, an interview's *ends at* is filled in there — so reading
`base_fields` reports them as bare when a person sees the sentence. That was how the issue
counted, and it undercounts.
"""

from __future__ import annotations

import importlib
import inspect

import pytest
from django import forms

pytestmark = pytest.mark.django_db

#: Where the forms are. Taken as a list because it is the thing that has to be complete;
#: a module missing from here is a whole form nobody checks.
MODULES = (
    "postulo.accounts.forms",
    "postulo.applications.forms",
    "postulo.core.server_forms",
    "postulo.documents.forms",
    "postulo.jobs.forms",
    "postulo.plugins.forms",
    "postulo.resume.forms",
)

#: `Form.field` → why it says nothing, for the fields where a label is the whole answer.
#:
#: Three kinds are in here. A field whose label *is* the sentence (*Name*, *Title*). A
#: field whose control explains itself (a password confirmation, a tick box whose label is
#: a sentence). And a field somebody else's library owns, where the words are theirs.
EXCUSED = {
    # ----------------------------------------------------------- a label is enough
    "AddLanguageForm.language": "the label and the list are the whole of it",
    "ApplicationDetailsForm.status": "the choices are the answer, and they are named",
    "ApplicationForm.status": "the choices are the answer, and they are named",
    "ApplicationIntakeForm.status": "the choices are the answer, and they are named",
    "ApplicationIntakeForm.company_name": "who the employer is",
    "ApplicationIntakeForm.title": "what the job is called",
    "CertificationForm.name": "what the certificate is called",
    "CertificationForm.issued_on": "when it was awarded",
    "CompanyForm.name": "what the company is called",
    "ContactForm.name": "what the person is called",
    "EducationForm.institution": "where you studied",
    "ExperienceForm.role": "what the job was called",
    "ExperienceForm.organisation": "who you did it for",
    "IndustryForm.name": "what the industry is called",
    "InterviewForm.kind": "the choices are the answer, and they are named",
    "JobPostingForm.title": "what the job is called",
    "JobPostingForm.company": "who the employer is",
    "LanguageSkillForm.name": "which language",
    "LinkForm.title": "what to call the link",
    "LinkForm.url": "where it goes",
    "PostingIntakeForm.company_name": "who the employer is",
    "PostingIntakeForm.title": "what the job is called",
    "ProjectForm.name": "what the project is called",
    "SkillForm.name": "what the skill is called",
    "SkillGroupForm.name": "what the group is called",
    "StatusChangeForm.status": "the choices are the answer, and they are named",
    "StatusChangeForm.note": "what to write on the timeline about this change",
    "TagForm.name": "what the tag is called",
    "TestEmailForm.to": "where to send the test",
    "SendDocumentsForm.cv": "which CV, from your own",
    "SendDocumentsForm.cover_letter": "which letter, from your own",
    # --------------------------------------------- the control says it, or somebody else does
    "AddPasskeyForm.credential": "the browser fills it; nobody types here",
    "ChangePasswordForm.password2": "a confirmation of the box above it",
    "SignupForm.password2": "a confirmation of the box above it",
    "LoginForm.login": "allauth's, and the label names both things it accepts",
    "ResetPasswordKeyForm.password2": "a confirmation of the box above it",
    "SetPasswordForm.password2": "a confirmation of the box above it",
    "SocialSignupForm.username": "allauth's, on a page Postulo does not compose",
    "SocialSignupForm.email": "allauth's, on a page Postulo does not compose",
    "SocialSignupForm.first_name": "a person's own name",
    "SocialSignupForm.last_name": "a person's own name",
    "SignupForm.first_name": "a person's own name",
    "SignupForm.last_name": "a person's own name",
    "ProfileForm.first_name": "a person's own name",
    "ProfileForm.last_name": "a person's own name",
    "ProfileForm.remove_picture": "a tick box whose label is the sentence",
    "LoginForm.remember": "a tick box whose label is the sentence",
    "AppearanceForm.theme": "three named choices, shown as they will look",
    "PluginRepositoryForm.enabled": "a tick box whose label is the sentence",
    "ConnectionForm.enabled": "a tick box whose label is the sentence",
    "CVItemForm.order": "a number whose label says which way it sorts",
    "CompanyIdentifierForm.scheme": "the register, named in the list",
    "CompanyIdentifierForm.value": "the number itself",
}


def every_form() -> list[tuple[str, type]]:
    found = []
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        for name, value in vars(module).items():
            if (
                inspect.isclass(value)
                and issubclass(value, forms.BaseForm)
                and value.__module__ == module_name
            ):
                found.append((name, value))
    return sorted(found)


def fields_of(name: str, form_class: type, user) -> dict:
    """The fields as a person meets them, which means building the form.

    Several are given their sentence in `__init__`. Where a form cannot be built without
    something this test has no way to invent, its declared fields are read instead — that
    undercounts rather than overcounts, so nothing slips through by being unbuildable.
    """
    for attempt in ({"user": user}, {}):
        try:
            return form_class(**attempt).fields
        except Exception:  # noqa: S112 - any refusal means try the next way in, and
            continue  # the fallback below is what makes "none of them worked" safe
    return form_class.base_fields


@pytest.fixture
def forms_on_the_site(user):
    built = {}
    for name, form_class in every_form():
        for field_name, field in fields_of(name, form_class, user).items():
            built[f"{name}.{field_name}"] = field
    return built


def test_there_are_forms_to_check(forms_on_the_site):
    """A sanity check on the check: an empty list would make everything below pass."""
    assert len(forms_on_the_site) > 150


def test_every_field_either_explains_itself_or_is_excused(forms_on_the_site):
    bare = sorted(name for name, field in forms_on_the_site.items() if not field.help_text)

    unexplained = [name for name in bare if name not in EXCUSED]

    assert not unexplained, (
        "These fields say nothing beyond their label. Give each a sentence — what goes in "
        "it, in what form, or what Postulo does with it — or add it to EXCUSED with the "
        f"reason a label is enough: {unexplained}"
    )


def test_nothing_excused_has_quietly_been_given_a_sentence(forms_on_the_site):
    """A stale excuse would hide the next field that needs one."""
    stale = sorted(
        name for name in EXCUSED if name in forms_on_the_site and forms_on_the_site[name].help_text
    )

    assert not stale, f"These have help now. Take them out of EXCUSED: {stale}"


def test_nothing_is_excused_for_a_field_that_no_longer_exists(forms_on_the_site):
    gone = sorted(name for name in EXCUSED if name not in forms_on_the_site)

    assert not gone, f"EXCUSED names fields that are gone: {gone}"


def test_most_of_the_fields_explain_themselves(forms_on_the_site):
    """The number the issue was opened about, kept from sliding back.

    It was 73 of 240. A ratio rather than a list, because the list is the two tests above;
    this is the one that notices a hundred new fields arriving with nothing said about any
    of them.
    """
    explained = sum(1 for field in forms_on_the_site.values() if field.help_text)

    assert explained / len(forms_on_the_site) > 0.7, (
        f"only {explained} of {len(forms_on_the_site)} fields say anything"
    )


def test_no_sentence_starts_by_telling_somebody_to_enter_something():
    """The voice, held to (#205). Postulo's help says the consequence, not the ceremony."""
    offenders = []
    for name, form_class in every_form():
        for field_name, field in form_class.base_fields.items():
            words = str(field.help_text or "")
            if words.lower().startswith(("enter ", "type ", "input ", "please ")):
                offenders.append(f"{name}.{field_name}: {words[:60]}")

    assert not offenders, offenders
