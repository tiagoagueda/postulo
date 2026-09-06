"""Fill an account with a believable, obviously fictional job search.

For trying Postulo out, showing it to somebody, or reviewing a change against data that
looks like the real thing. Everything is fictional — the employers are the usual film
and television companies — and the shape is deliberate: enough applications, spread over
enough months and ending enough different ways, that the board, the timeline and the
Insights page all have something to say.

The timelines are scripted rather than random. Insights reads the event log, so the log
has to be coherent: applied, then acknowledged some days later, then interviewed, then
rejected. A random walk would produce nonsense figures and prove nothing.

Deterministic for a given ``--seed``, so two people running it get the same search.
"""

from __future__ import annotations

import datetime as dt
import random
import secrets
from dataclasses import dataclass

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from postulo.applications.models import (
    Application,
    Channel,
    EventKind,
    Interview,
    InterviewKind,
    Priority,
    Reminder,
    Status,
)
from postulo.applications.services import change_status, record_event, schedule_interview
from postulo.core.models import Tag
from postulo.documents.models import (
    CV,
    CoverLetter,
    CVItem,
    DocumentKind,
    LetterKind,
    RenderedDocument,
    Theme,
    UploadedDocument,
)
from postulo.documents.pdf import PDFBackendUnavailable, get_pdf_backend
from postulo.documents.rendering import snapshot_cv, snapshot_letter
from postulo.jobs.models import (
    Capture,
    Company,
    CompanyIdentifier,
    Contact,
    DiscardReason,
    Industry,
    JobPosting,
    ListingState,
    RemoteType,
)
from postulo.plugins.base import JobPostingData
from postulo.resume.models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Link,
    LinkKind,
    Project,
    Skill,
    SkillGroup,
)

# --------------------------------------------------------------------- material

#: Identifiers for a few of the companies, so the block on the company page and the
#: table's optional columns have something to show. The ids are made up, as the
#: companies are.
DEMO_IDENTIFIERS = {
    "Aperture Science": (("wikidata", "Q4779874"), ("linkedin", "aperture-science")),
    "Black Mesa": (("wikidata", "Q2313543"),),
    "Initech": (("lei", "HWUPKR0MPOU8FGXBT394"), ("crunchbase", "initech")),
    "Globex Corporation": (("wikidata", "Q5570047"), ("register", "DE HRB 12345")),
    "Wayne Enterprises": (("wikidata", "Q2586409"), ("opencorporates", "us_de/2345678")),
}

COMPANIES = [
    # name, industries, location, website
    ("Aperture Science", ("Research", "Software"), "Paris", "https://aperture.example"),
    ("Black Mesa", ("Research", "Energy"), "Lyon", "https://blackmesa.example"),
    ("Initech", ("Software",), "Paris", "https://initech.example"),
    (
        "Vandelay Industries",
        ("Transport and logistics", "Retail"),
        "Lisbon",
        "https://vandelay.example",
    ),
    ("Globex Corporation", ("Energy", "Software", "Finance"), "Berlin", "https://globex.example"),
    ("Hooli", ("Software", "Advertising and marketing"), "Paris", "https://hooli.example"),
    ("Pied Piper", ("Software",), "Remote", "https://piedpiper.example"),
    ("Wayne Enterprises", ("Finance", "Defence", "Research"), "London", "https://wayne.example"),
    ("Stark Industries", ("Engineering", "Defence"), "Amsterdam", "https://stark.example"),
    ("Tyrell Corporation", ("Biotechnology",), "Porto", "https://tyrell.example"),
    ("Cyberdyne Systems", ("Engineering", "Software"), "Paris", "https://cyberdyne.example"),
    (
        "Wonka Industries",
        ("Manufacturing", "Food and agriculture"),
        "Lyon",
        "https://wonka.example",
    ),
    ("Acme Corporation", ("Manufacturing",), "Remote", "https://acme.example"),
    (
        "Umbrella Corporation",
        ("Pharmaceuticals", "Biotechnology"),
        "Berlin",
        "https://umbrella.example",
    ),
]

TITLES = [
    "Senior Backend Engineer",
    "Backend Engineer",
    "Platform Engineer",
    "Software Engineer, Infrastructure",
    "Staff Engineer",
    "Site Reliability Engineer",
    "Python Developer",
    "Lead Developer",
    "Engineering Manager",
    "Data Platform Engineer",
    "API Engineer",
    "DevOps Engineer",
]

SOURCES = ["LinkedIn", "Welcome to the Jungle", "Referral", "Company site", "Indeed", ""]
SOURCE_WEIGHTS = [5, 3, 2, 3, 2, 1]

CONTACT_NAMES = [
    ("Marie Dubois", "Talent acquisition"),
    ("João Ferreira", "Engineering manager"),
    ("Anna Schmidt", "Recruiter"),
    ("Tom Bennett", "Head of engineering"),
    ("Inês Carvalho", "HR business partner"),
    ("Luc Moreau", "CTO"),
]

#: How an application unfolds: (status, days after applying). A note of None on the
#: first entry means it was never sent at all.
SCENARIOS: dict[str, list[tuple[str, int]]] = {
    "draft": [],
    "ghosted": [(Status.APPLIED, 0), (Status.GHOSTED, 45)],
    "rejected_early": [(Status.APPLIED, 0), (Status.REJECTED, 9)],
    "rejected_after_screening": [
        (Status.APPLIED, 0),
        (Status.ACKNOWLEDGED, 2),
        (Status.SCREENING, 8),
        (Status.REJECTED, 14),
    ],
    "rejected_after_interview": [
        (Status.APPLIED, 0),
        (Status.ACKNOWLEDGED, 3),
        (Status.SCREENING, 7),
        (Status.INTERVIEWING, 15),
        (Status.REJECTED, 26),
    ],
    "withdrawn": [(Status.APPLIED, 0), (Status.ACKNOWLEDGED, 2), (Status.WITHDRAWN, 11)],
    "applied": [(Status.APPLIED, 0)],
    "acknowledged": [(Status.APPLIED, 0), (Status.ACKNOWLEDGED, 4)],
    "screening": [(Status.APPLIED, 0), (Status.ACKNOWLEDGED, 2), (Status.SCREENING, 9)],
    "interviewing": [
        (Status.APPLIED, 0),
        (Status.ACKNOWLEDGED, 3),
        (Status.SCREENING, 7),
        (Status.INTERVIEWING, 14),
    ],
    "assessment": [
        (Status.APPLIED, 0),
        (Status.ACKNOWLEDGED, 1),
        (Status.SCREENING, 6),
        (Status.INTERVIEWING, 12),
        (Status.ASSESSMENT, 18),
    ],
    "offer": [
        (Status.APPLIED, 0),
        (Status.ACKNOWLEDGED, 2),
        (Status.SCREENING, 6),
        (Status.INTERVIEWING, 13),
        (Status.ASSESSMENT, 20),
        (Status.OFFER, 31),
    ],
}

#: The mix of a search that has been running for a few months. Old ones have settled;
#: recent ones are still moving. Roughly thirty in all.
MIX: list[tuple[str, int, int]] = [
    # scenario, how many, how many days ago they were sent (upper bound)
    ("ghosted", 5, 170),
    ("rejected_early", 4, 160),
    ("rejected_after_screening", 3, 140),
    ("rejected_after_interview", 2, 120),
    ("withdrawn", 2, 100),
    ("offer", 1, 60),
    ("assessment", 1, 40),
    ("interviewing", 2, 35),
    ("screening", 2, 25),
    ("acknowledged", 2, 15),
    ("applied", 3, 9),
    ("draft", 3, 0),
]


@dataclass
class Report:
    companies: int = 0
    listings: int = 0
    applications: int = 0
    events: int = 0
    reminders: int = 0
    interviews: int = 0
    snapshots: int = 0
    notes: list[str] | None = None


def placeholder_pdf(title: str) -> bytes:
    """A small but valid single-page PDF, so the placeholder opens in a viewer."""
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        None,  # the content stream, filled in below
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    text = title.replace("(", "").replace(")", "")
    stream = f"BT /F1 18 Tf 72 760 Td ({text}) Tj ET".encode()
    objects[3] = b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    )
    return bytes(out)


class Command(BaseCommand):
    help = "Fill an account with a fictional but believable job search."

    def add_arguments(self, parser) -> None:
        parser.add_argument("email", help="The account to fill. Created if it does not exist.")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete everything the account already holds first.",
        )
        parser.add_argument("--seed", type=int, default=2026, help="Random seed.")
        parser.add_argument(
            "--no-pdf",
            action="store_true",
            help="Skip rendering snapshot PDFs even if a renderer is available.",
        )
        parser.add_argument("--password", help="Set this password if the account is created.")

    def handle(self, *args, **options) -> None:
        User = get_user_model()
        user = User.objects.filter(email__iexact=options["email"]).first()
        created = user is None
        if created:
            user = User.objects.create_user(
                email=options["email"],
                password=options["password"] or secrets.token_urlsafe(24),
            )
        # Nobody will click a verification link for a fictional account; the seeder
        # vouches for the address so the account can sign in straight away.
        EmailAddress.objects.update_or_create(
            user=user,
            email__iexact=user.email,
            defaults={"email": user.email, "verified": True, "primary": True},
        )

        has_data = any(
            model.objects.for_user(user).exists()
            for model in (Application, Company, CV, Experience)
        )
        if has_data and not options["reset"]:
            raise CommandError(
                "That account already holds a job search. Pass --reset to delete it first, "
                "or use a different account."
            )

        with transaction.atomic():
            if has_data:
                self._reset(user)
            # Deterministic fixtures, not secrets: the same seed must give the same search.
            rng = random.Random(options["seed"])  # noqa: S311
            report = self._seed(user, rng, not options["no_pdf"])

        self.stdout.write(self.style.SUCCESS(f"Seeded {user.email}"))
        for name, value in vars(report).items():
            if isinstance(value, int):
                self.stdout.write(f"  {value} {name}")
        for note in report.notes or []:
            self.stdout.write(self.style.WARNING(f"  {note}"))

    # ------------------------------------------------------------------ reset

    @staticmethod
    def _reset(user) -> None:
        for model in (
            Capture,
            RenderedDocument,
            UploadedDocument,
            CVItem,
            CV,
            CoverLetter,
            Interview,
            Reminder,
            Application,
            JobPosting,
            Contact,
            Company,
            Industry,
            Tag,
            Skill,
            SkillGroup,
            Experience,
            Education,
            Project,
            Certification,
            LanguageSkill,
            Link,
        ):
            model.objects.for_user(user).delete()

    # ------------------------------------------------------------------- seed

    def _seed(self, user, rng: random.Random, want_pdf: bool) -> Report:
        report = Report(notes=[])
        now = timezone.now()

        # ---- profile ---------------------------------------------------------
        if not user.first_name:
            user.first_name, user.last_name = "Alex", "Morgan"
            user.save(update_fields=["first_name", "last_name"])
        profile = user.profile
        profile.headline = "Backend engineer"
        profile.phone = "+33 6 00 00 00 00"
        profile.location = "Paris, France"
        profile.website = "https://alexmorgan.example"
        profile.linkedin_url = "https://www.linkedin.com/in/alex-morgan-example"
        profile.source_repo_url = "https://source.example/alexmorgan"
        profile.save()

        # ---- career ----------------------------------------------------------
        experiences = [
            Experience.objects.create(
                owner=user,
                organisation="Weyland-Yutani",
                role="Senior Backend Engineer",
                location="Paris",
                start_date=dt.date(2022, 4, 1),
                summary="Platform team for the logistics product.",
                highlights=(
                    "Cut p95 API latency from 900 ms to 180 ms by moving hot paths off the ORM.\n"
                    "Led the migration of forty services from a hand-rolled deploy to Kubernetes.\n"
                    "Mentored four engineers through their first year."
                ),
                order=0,
            ),
            Experience.objects.create(
                owner=user,
                organisation="Massive Dynamic",
                role="Backend Engineer",
                location="Lisbon",
                start_date=dt.date(2019, 9, 1),
                end_date=dt.date(2022, 3, 31),
                summary="Payments and billing.",
                highlights=(
                    "Built the invoicing pipeline that handled €40M a year without a single "
                    "missed run.\n"
                    "Introduced contract testing between six teams' services.\n"
                    "Halved on-call pages by fixing the three alerts that caused most of them."
                ),
                order=1,
            ),
            Experience.objects.create(
                owner=user,
                organisation="Soylent Corporation",
                role="Software Developer",
                location="Porto",
                start_date=dt.date(2016, 2, 1),
                end_date=dt.date(2019, 8, 31),
                summary="Full-stack work on the ordering system.",
                highlights=(
                    "Rewrote the order pipeline in Django, retiring a PHP application.\n"
                    "Set up the first automated test suite the company had."
                ),
                order=2,
            ),
        ]
        Education.objects.create(
            owner=user,
            institution="Universidade do Porto",
            qualification="MSc Informatics Engineering",
            field_of_study="Distributed systems",
            location="Porto",
            start_date=dt.date(2014, 9, 1),
            end_date=dt.date(2016, 7, 1),
            order=0,
        )
        Education.objects.create(
            owner=user,
            institution="Universidade do Porto",
            qualification="BSc Computer Science",
            location="Porto",
            start_date=dt.date(2011, 9, 1),
            end_date=dt.date(2014, 7, 1),
            order=1,
        )
        groups = {}
        for order, (name, skills) in enumerate(
            [
                ("Languages", ["Python", "Go", "SQL", "TypeScript"]),
                ("Infrastructure", ["Kubernetes", "PostgreSQL", "Redis", "Terraform", "AWS"]),
                ("Practices", ["Observability", "Incident response", "Contract testing"]),
            ]
        ):
            group = SkillGroup.objects.create(owner=user, name=name, order=order)
            groups[name] = group
            for index, skill in enumerate(skills):
                Skill.objects.create(owner=user, group=group, name=skill, order=index)
        projects = [
            Project.objects.create(
                owner=user,
                name="Postulo",
                role="Author",
                url="https://source.example/alexmorgan/postulo",
                summary="A self-hosted job application manager.",
                highlights="Django, htmx, WeasyPrint.\nRuns on a Raspberry Pi.",
                order=0,
            )
        ]
        certifications = [
            Certification.objects.create(
                owner=user,
                name="Certified Kubernetes Administrator",
                issuer="CNCF",
                issued_on=dt.date(2023, 5, 1),
                expires_on=dt.date(2026, 5, 1),
                order=0,
            )
        ]
        languages = [
            LanguageSkill.objects.create(
                owner=user, name="Portuguese", proficiency="native", order=0
            ),
            LanguageSkill.objects.create(owner=user, name="English", proficiency="c2", order=1),
            LanguageSkill.objects.create(owner=user, name="French", proficiency="b2", order=2),
        ]

        links = [
            Link.objects.create(
                owner=user,
                title="Portfolio",
                url="https://alexmorgan.example/work",
                kind=LinkKind.PORTFOLIO,
                description="Six projects, with what each one was actually for.",
                order=0,
            ),
            Link.objects.create(
                owner=user,
                title="Source repositories",
                url="https://source.example/alexmorgan",
                kind=LinkKind.CODE,
                description="Everything public, including the failures.",
                order=1,
            ),
            Link.objects.create(
                owner=user,
                title="Two minutes about me",
                url="https://video.example/w/alexmorgan-intro",
                kind=LinkKind.VIDEO,
                description="Unlisted; the short version of this CV, spoken.",
                order=2,
            ),
        ]

        # ---- tags ------------------------------------------------------------
        tags = {
            name: Tag.objects.create(owner=user, name=name, colour=colour)
            for name, colour in [
                ("Remote", "sky"),
                ("Dream job", "amber"),
                ("Backup plan", "slate"),
                ("Via a friend", "emerald"),
                ("Relocation", "rose"),
            ]
        }

        # ---- companies and people --------------------------------------------
        companies = []
        contacts: dict[int, list[Contact]] = {}
        for name, fields, location, website in COMPANIES:
            company = Company.objects.create(
                owner=user,
                name=name,
                location=location,
                website=website,
                careers_url=f"{website}/careers",
                notes=rng.choice(
                    [
                        "",
                        "",
                        "Friend of a friend works here.",
                        "Glassdoor is mixed. Ask about on-call.",
                        "Hiring freeze rumoured, but the posting is still up.",
                    ]
                ),
            )
            company.industries.set(Industry.named(user, fields))
            for scheme, value in DEMO_IDENTIFIERS.get(name, ()):
                CompanyIdentifier.objects.create(
                    owner=user, company=company, scheme=scheme, value=value
                )
            companies.append(company)
            report.companies += 1
            if rng.random() < 0.4:
                contact_name, role = rng.choice(CONTACT_NAMES)
                contacts[company.pk] = [
                    Contact.objects.create(
                        owner=user,
                        company=company,
                        name=contact_name,
                        role=role,
                        email=f"{contact_name.split()[0].lower()}@{website.removeprefix('https://')}",
                    )
                ]

        # ---- applications, with coherent timelines ---------------------------
        applications: list[Application] = []
        for scenario, count, max_days_ago in MIX:
            for _ in range(count):
                company = rng.choice(companies)
                title = rng.choice(TITLES)
                source = rng.choices(SOURCES, weights=SOURCE_WEIGHTS)[0]
                remote = rng.choice([RemoteType.REMOTE, RemoteType.HYBRID, RemoteType.ONSITE, ""])
                low = rng.choice([55000, 60000, 65000, 70000, 80000, 90000, None])
                posting = JobPosting.objects.create(
                    owner=user,
                    company=company,
                    title=title,
                    location=company.location if remote != RemoteType.REMOTE else "Remote",
                    remote_type=remote,
                    employment_type="full_time",
                    url=f"{company.website}/careers/{rng.randint(1000, 9999)}",
                    source=source,
                    salary_min=low,
                    salary_max=(low + rng.choice([10000, 15000, 20000])) if low else None,
                    salary_currency="EUR",
                    description=(
                        f"{company.name} is looking for a {title.lower()} to join the team in "
                        f"{company.location}.\n\nYou will work on services that matter, "
                        "with people "
                        "who care about doing it well.\n\nWhat we need:\n- Python or Go\n- "
                        "Experience running things in production\n- Curiosity"
                    ),
                )
                application = Application.objects.create(
                    owner=user,
                    posting=posting,
                    status=Status.DRAFT,
                    channel=rng.choice(list(Channel)) if scenario != "draft" else "",
                    priority=rng.choice(
                        [Priority.LOW, Priority.NORMAL, Priority.NORMAL, Priority.HIGH]
                    ),
                    contact=rng.choice(contacts[company.pk]) if company.pk in contacts else None,
                )
                applications.append(application)
                report.applications += 1

                chosen_tags = [
                    tag
                    for tag, chance in [
                        (tags["Remote"], 0.9 if remote == RemoteType.REMOTE else 0.05),
                        (tags["Dream job"], 0.15),
                        (tags["Backup plan"], 0.2),
                        (tags["Via a friend"], 0.6 if source == "Referral" else 0.05),
                        (
                            tags["Relocation"],
                            0.5 if company.location not in ("Paris", "Remote") else 0.0,
                        ),
                    ]
                    if rng.random() < chance
                ]
                application.tags.set(chosen_tags)

                steps = SCENARIOS[scenario]
                if not steps:
                    record_event(
                        application,
                        summary="Application created",
                        occurred_at=now - dt.timedelta(days=rng.randint(1, 6)),
                    )
                    report.events += 1
                    continue

                sent_days_ago = rng.randint(max(1, max_days_ago - 20), max_days_ago)
                sent_at = now - dt.timedelta(days=sent_days_ago, hours=rng.randint(8, 18))
                record_event(
                    application,
                    summary="Application created",
                    occurred_at=sent_at - dt.timedelta(hours=2),
                )
                report.events += 1
                for status, offset in steps:
                    when = sent_at + dt.timedelta(days=offset, hours=rng.randint(0, 9))
                    if when > now:
                        break
                    change_status(
                        application, status, occurred_at=when, note=self._note_for(rng, status)
                    )
                    report.events += 1
                    flavour = self._flavour(rng, status)
                    if flavour:
                        kind, summary = flavour
                        record_event(
                            application,
                            kind=kind,
                            summary=summary,
                            occurred_at=when + dt.timedelta(hours=rng.randint(1, 30)),
                        )
                        report.events += 1

        # ---- reminders -------------------------------------------------------
        live = [a for a in applications if a.is_open and a.status != Status.DRAFT]
        for summary, delta, done in [
            ("Chase the recruiter", dt.timedelta(days=-3), False),
            ("Prepare for the technical interview", dt.timedelta(hours=6), False),
            ("Send the take-home task", dt.timedelta(days=4), False),
            ("Ask about the salary band", dt.timedelta(days=9), False),
            ("Thank the interviewer", dt.timedelta(days=-12), True),
        ]:
            reminder = Reminder.objects.create(
                owner=user,
                application=rng.choice(live) if live else None,
                summary=summary,
                due_at=now + delta,
            )
            if done:
                reminder.done_at = now + delta + dt.timedelta(hours=3)
                reminder.save(update_fields=["done_at"])
            report.reminders += 1

        # ---- interviews: one held, one in the diary ---------------------------
        talking = [a for a in applications if a.status == Status.INTERVIEWING]
        for index, application in enumerate(talking[:2]):
            company = application.posting.company
            people = contacts.get(company.pk, [])
            schedule_interview(
                application,
                kind=InterviewKind.PHONE,
                starts_at=now - dt.timedelta(days=6 + index, hours=2),
                location="",
                contacts=people,
            )
            report.interviews += 1
            report.events += 2  # held, and the status catching up
            upcoming = schedule_interview(
                application,
                kind=InterviewKind.VIDEO if index else InterviewKind.ONSITE,
                starts_at=(now + dt.timedelta(days=2 + 3 * index)).replace(
                    hour=10, minute=0, second=0, microsecond=0
                ),
                location=f"{company.website}/meet/{rng.randint(1000, 9999)}"
                if index
                else company.location,
                notes="Ask about the team's on-call rota and how they run retros.",
                contacts=people,
            )
            report.interviews += 1
            report.reminders += int(upcoming.reminder_id is not None)

        # ---- documents -------------------------------------------------------
        cvs = []
        for name, headline, theme, summary in [
            (
                "Backend, English",
                "Backend engineer",
                Theme.PLAIN,
                "Ten years of building services that stay up, and of tidying up after the "
                "ones that did not.",
            ),
            (
                "Platform, English",
                "Platform engineer",
                Theme.CLASSIC,
                "I make deployments boring.",
            ),
        ]:
            cv = CV.objects.create(
                owner=user, name=name, headline=headline, summary=summary, theme=theme
            )
            for index, item in enumerate(
                [
                    *experiences,
                    *languages,
                    groups["Languages"],
                    groups["Infrastructure"],
                    *projects,
                    *certifications,
                ]
            ):
                CVItem.objects.create(
                    owner=user,
                    cv=cv,
                    content_type=ContentType.objects.get_for_model(item),
                    object_id=item.pk,
                    order=index,
                    override_highlights=(
                        "Ran the platform team: Kubernetes, Terraform, and the on-call rota.\n"
                        "Cut deploy time from forty minutes to four."
                        if name.startswith("Platform") and item is experiences[0]
                        else ""
                    ),
                )
            cvs.append(cv)

        letters = [
            CoverLetter.objects.create(
                owner=user,
                name="General backend",
                subject="Application for {{ role }} at {{ company }}",
                body=(
                    "Dear {{ company }} team,\n\n"
                    "I am writing about the {{ role }} position. I have spent the last ten years "
                    "building backend systems that other teams depend on, most recently at "
                    "Weyland-Yutani, where I led the move to Kubernetes.\n\n"
                    "I would welcome the chance to talk about how I could help in "
                    "{{ location }}.\n\n"
                    "Yours sincerely,\n{{ name }}"
                ),
                is_template=True,
            ),
            CoverLetter.objects.create(
                owner=user,
                name="Referral",
                subject="{{ role }} — introduced by a colleague",
                body=(
                    "Dear {{ company }} team,\n\n"
                    "A former colleague suggested I write to you about the {{ role }} role.\n\n"
                    "{{ name }}"
                ),
                is_template=True,
                theme=Theme.CLASSIC,
            ),
            CoverLetter.objects.create(
                owner=user,
                name="Why this work",
                kind=LetterKind.MOTIVATION,
                subject="{{ role }} — {{ company }}",
                body=(
                    "I am applying for the {{ role }} role at {{ company }}.\n\n"
                    "Why this work\n"
                    "I started with systems nobody else wanted to touch, and found that I "
                    "liked making them legible again.\n\n"
                    "Why {{ company }}\n"
                    "You publish your incident write-ups. That tells me more about how you "
                    "work than any job advert could.\n\n"
                    "What I bring\n"
                    "Ten years of backend work, four of them on systems other teams "
                    "depended on, and the habit of writing things down.\n\n"
                    "{{ name }}\n{{ date }}"
                ),
                is_template=True,
                theme=Theme.CLASSIC,
            ),
            CoverLetter.objects.create(
                owner=user,
                name="After the interview",
                kind=LetterKind.FOLLOW_UP,
                subject="Thank you — {{ role }}",
                body=(
                    "Dear [name],\n\n"
                    "Thank you for your time today.\n\n"
                    "One thing I did not say well: the migration I described took four "
                    "months, and the reason it worked was the rollback we never needed.\n\n"
                    "Yours sincerely,\n{{ name }}"
                ),
                is_template=True,
            ),
        ]

        upload = UploadedDocument(
            owner=user,
            title="Designed CV (2024)",
            kind=DocumentKind.CV,
            notes="The one a designer friend laid out. Looks better than it reads.",
        )
        upload.file.save(
            "designed-cv-2024.pdf",
            ContentFile(placeholder_pdf("Alex Morgan — CV 2024")),
            save=False,
        )
        upload.save()

        # ---- what was sent, frozen -------------------------------------------
        sent_with_documents = [
            a for a in applications if a.applied_at is not None and a.status != Status.DRAFT
        ][:4]
        if want_pdf:
            try:
                backend = get_pdf_backend()
            except PDFBackendUnavailable as exc:
                report.notes.append(f"No PDF snapshots: {exc}")
                backend = None
            if backend is not None:
                for index, application in enumerate(sent_with_documents):
                    document = snapshot_cv(cvs[index % 2], application=application, backend=backend)
                    document.rendered_at = application.applied_at
                    document.save(update_fields=["rendered_at"])
                    report.snapshots += 1
                    if index == 0:
                        letter = snapshot_letter(
                            letters[0], application=application, backend=backend
                        )
                        letter.rendered_at = application.applied_at
                        letter.save(update_fields=["rendered_at"])
                        report.snapshots += 1
                    application.sent_uploads.add(upload)
                    application.sent_links.add(links[0], links[2])
                    record_event(
                        application,
                        summary="Documents sent",
                        body=f"{cvs[index % 2].name} — CV",
                        occurred_at=application.applied_at,
                    )
                    report.events += 1

        # ---- listings not yet decided about ------------------------------------
        # The stage before applications: noticed, not applied to. A few new, one
        # shortlisted, one discarded, and closing dates so the dashboard has a nudge.
        undecided = [
            (companies[1], "Platform Engineer", ListingState.NEW, 5, ""),
            (companies[3], "Senior Backend Developer", ListingState.NEW, 12, ""),
            (companies[5], "Engineering Manager", ListingState.SHORTLISTED, 20, ""),
            (companies[7], "Data Engineer", ListingState.NEW, None, ""),
            (companies[2], "Junior Developer", ListingState.DISCARDED, None, DiscardReason.PAY),
        ]
        for company, title, state, closes_in, reason in undecided:
            JobPosting.objects.create(
                owner=user,
                company=company,
                title=title,
                location=company.location,
                employment_type="full_time",
                url=f"{company.website}/careers/{rng.randint(1000, 9999)}",
                source=rng.choices(SOURCES, weights=SOURCE_WEIGHTS)[0],
                closes_at=(now.date() + dt.timedelta(days=closes_in)) if closes_in else None,
                description=f"{company.name} is hiring a {title.lower()}. Noticed, not decided.",
                state=state,
                discard_reason=reason,
                noted_at=now - dt.timedelta(days=rng.randint(1, 10)),
                decided_at=(now - dt.timedelta(days=1)) if state != ListingState.NEW else None,
            )
            report.listings += 1

        # ---- captures waiting for review -------------------------------------
        for company, title in [
            (companies[6], "Backend Engineer"),
            (companies[8], "Staff Engineer"),
        ]:
            Capture.objects.create(
                owner=user,
                url=f"{company.website}/careers/{rng.randint(1000, 9999)}",
                source_name="schema.org",
                source_version="1.0",
                origin=rng.choice(["web", "api"]),
                data=JobPostingData(
                    title=title,
                    company_name=company.name,
                    location=company.location,
                    employment_type="full_time",
                    description=(
                        f"{company.name} needs a {title.lower()}. Captured, not yet reviewed."
                    ),
                    url=f"{company.website}/careers/",
                    source=company.website.removeprefix("https://"),
                ).model_dump(mode="json"),
            )

        return report

    # ---------------------------------------------------------------- flavour

    @staticmethod
    def _note_for(rng: random.Random, status: str) -> str:
        return rng.choice(
            {
                Status.APPLIED: [
                    "Sent through the careers page.",
                    "Applied via LinkedIn Easy Apply.",
                    "",
                ],
                Status.ACKNOWLEDGED: [
                    "Automated acknowledgement.",
                    "Recruiter replied personally.",
                    "",
                ],
                Status.SCREENING: ["Thirty-minute call booked.", ""],
                Status.INTERVIEWING: [
                    "Technical interview with the team lead.",
                    "Panel of three.",
                    "",
                ],
                Status.ASSESSMENT: [
                    "Take-home: a small service with tests. Four hours suggested.",
                    "",
                ],
                Status.OFFER: ["Verbal offer; written one to follow.", ""],
                Status.REJECTED: [
                    "Went with an internal candidate.",
                    "Not enough Go experience, apparently.",
                    "No reason given.",
                    "",
                ],
                Status.WITHDRAWN: [
                    "Salary band was well below the posting.",
                    "Accepted elsewhere.",
                    "",
                ],
                Status.GHOSTED: ["Chased twice. Nothing.", "Gave up waiting.", ""],
            }.get(status, [""])
        )

    @staticmethod
    def _flavour(rng: random.Random, status: str) -> tuple[str, str] | None:
        options = {
            Status.ACKNOWLEDGED: [(EventKind.EMAIL_RECEIVED, "Acknowledgement email")],
            Status.SCREENING: [(EventKind.CALL, "Screening call with the recruiter")],
            Status.INTERVIEWING: [
                (EventKind.INTERVIEW, "Technical interview"),
                (EventKind.NOTE, "Asked good questions about the on-call rota."),
            ],
            Status.ASSESSMENT: [(EventKind.NOTE, "Sent the take-home back a day early.")],
            Status.APPLIED: [(EventKind.FOLLOW_UP, "Followed up by email"), None, None],
        }
        choice = rng.choice(options.get(status, [None]))
        return choice
