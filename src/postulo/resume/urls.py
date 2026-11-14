from django.urls import path

from postulo.core import modules

from . import views, views_answers, views_candidate, views_references

#: Bibliography files are a module (#879): while it is off the address answers as if there
#: were nothing there.
bibliography = modules.required("format-bibliography")

app_name = "resume"

urlpatterns = [
    path("", views.ResumeOverviewView.as_view(), name="overview"),
    path("preview/", views.ResumePreviewView.as_view(), name="preview"),
    path("import/", views.EuropassImportView.as_view(), name="europass_import"),
    # One person's own record as a file (#181). Neither address names a record: what is
    # downloaded and what is added to are the account of whoever is signed in.
    path("file/", views_candidate.CandidateFileView.as_view(), name="candidate_file"),
    path(
        "file/download/",
        views_candidate.CandidateDownloadView.as_view(),
        name="candidate_download",
    ),
    # What the skill box offers as somebody types: names from the ESCO classification (#266).
    path("skills/suggestions/", views.SkillSuggestionsView.as_view(), name="skill_suggestions"),
    path("links/check/", views.LinkCheckView.as_view(), name="link_check_all"),
    path("links/<int:pk>/check/", views.LinkCheckView.as_view(), name="link_check"),
    # *Look up* beside a publication's DOI, and *Suggest from my ORCID record* (#688).
    path(
        "publication/lookup/",
        views.PublicationLookupNewView.as_view(),
        name="publication_lookup_new",
    ),
    path(
        "publication/<int:pk>/lookup/",
        views.PublicationLookupView.as_view(),
        name="publication_lookup",
    ),
    path("publications/orcid/", views.PublicationOrcidView.as_view(), name="publication_orcid"),
    path(
        "publications/references/",
        bibliography(views_references.ReferencesView.as_view()),
        name="references",
    ),
    # The answers kept for application forms (#1092), before the sections' own addresses.
    path("answers/", views_answers.SavedAnswerListView.as_view(), name="answer_list"),
    path("answers/new/", views_answers.SavedAnswerCreateView.as_view(), name="answer_create"),
    path(
        "answers/<int:pk>/edit/",
        views_answers.SavedAnswerUpdateView.as_view(),
        name="answer_update",
    ),
    path(
        "answers/<int:pk>/delete/",
        views_answers.SavedAnswerDeleteView.as_view(),
        name="answer_delete",
    ),
    path("<slug:section>/new/", views.ResumeItemCreateView.as_view(), name="item_create"),
    path("<slug:section>/<int:pk>/edit/", views.ResumeItemUpdateView.as_view(), name="item_update"),
    path(
        "<slug:section>/<int:pk>/delete/", views.ResumeItemDeleteView.as_view(), name="item_delete"
    ),
    path(
        "<slug:section>/<int:pk>/languages/",
        views.ResumeItemTranslationsView.as_view(),
        name="item_languages",
    ),
    path(
        "<slug:section>/<int:pk>/move/<str:direction>/",
        views.ResumeItemMoveView.as_view(),
        name="item_move",
    ),
]
