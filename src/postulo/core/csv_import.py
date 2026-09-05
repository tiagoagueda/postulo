"""Bringing a spreadsheet in.

Most people track a job search in a spreadsheet until it hurts, and the day they arrive
here they should not have to retype their history. This reads a CSV — any delimiter, any
of the encodings Excel produces — guesses which column is which from the header names in
English, French and Portuguese, lets the person correct the guess and see how the first
rows will be read, and then imports in one transaction: rows with an applied date become
applications, rows without one become listings, companies are matched by name as the
forms match them, and every imported application carries a timeline entry saying which
file it came from, so provenance is never in doubt.

Rows that would duplicate what is already recorded — the same address, or the same company,
role and date — are reported, not created.
"""

from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

#: Files above this are refused: a spreadsheet of one job search is kilobytes.
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
PREVIEW_ROWS = 10

#: What a column can be mapped to. ``ignore`` drops it; ``notes`` collects free text.
FIELDS: tuple[tuple[str, str], ...] = (
    ("ignore", _("Ignore this column")),
    ("company", _("Company")),
    ("wikidata", _("Company's Wikidata id")),
    ("role", _("Role / job title")),
    ("url", _("Posting URL")),
    ("location", _("Location")),
    ("status", _("Status")),
    ("applied_at", _("Date applied")),
    ("deadline", _("Deadline")),
    ("salary_min", _("Salary from")),
    ("salary_max", _("Salary to")),
    ("salary", _("Salary (one figure or a range)")),
    ("channel", _("Applied through")),
    ("source", _("Found via")),
    ("tags", _("Tags")),
    ("description", _("Description")),
    ("notes", _("Notes")),
)
FIELD_KEYS = {key for key, _label in FIELDS}

#: Header words, lower-cased and stripped, that mean each field. English, French and
#: Portuguese, plus what trackers and people commonly write.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "company": (
        "company",
        "employer",
        "organisation",
        "organization",
        "entreprise",
        "société",
        "societe",
        "employeur",
        "empresa",
        "organização",
        "organizacao",
        "empregador",
    ),
    "wikidata": ("wikidata", "wikidata id", "qid", "wikidata company"),
    "role": (
        "role",
        "title",
        "job title",
        "job",
        "position",
        "poste",
        "intitulé",
        "intitule",
        "titre",
        "cargo",
        "função",
        "funcao",
        "vaga",
        "título",
        "titulo",
        "posição",
        "posicao",
    ),
    "url": (
        "url",
        "link",
        "posting url",
        "job url",
        "lien",
        "adresse",
        "ligação",
        "ligacao",
        "endereço",
        "endereco",
    ),
    "location": (
        "location",
        "city",
        "place",
        "where",
        "lieu",
        "ville",
        "localisation",
        "local",
        "localização",
        "localizacao",
        "cidade",
    ),
    "status": (
        "status",
        "stage",
        "state",
        "statut",
        "état",
        "etat",
        "étape",
        "etape",
        "estado",
        "situação",
        "situacao",
        "fase",
    ),
    "applied_at": (
        "applied",
        "date applied",
        "applied on",
        "application date",
        "date",
        "sent",
        "date de candidature",
        "candidature",
        "postulé",
        "postule",
        "envoyé",
        "envoye",
        "data",
        "data da candidatura",
        "candidatura",
        "enviado",
        "enviada",
    ),
    "deadline": (
        "deadline",
        "closes",
        "closing date",
        "due",
        "échéance",
        "echeance",
        "date limite",
        "prazo",
        "data limite",
    ),
    "salary_min": (
        "salary min",
        "salary from",
        "min salary",
        "salaire min",
        "salário mín",
        "salario min",
        "salário mínimo",
    ),
    "salary_max": (
        "salary max",
        "salary to",
        "max salary",
        "salaire max",
        "salário máx",
        "salario max",
        "salário máximo",
    ),
    "salary": (
        "salary",
        "pay",
        "compensation",
        "salaire",
        "rémunération",
        "remuneration",
        "salário",
        "salario",
        "remuneração",
        "remuneracao",
    ),
    "channel": (
        "channel",
        "applied through",
        "applied via",
        "how",
        "canal",
        "via",
        "moyen",
        "meio",
    ),
    "source": (
        "source",
        "found via",
        "found on",
        "board",
        "job board",
        "origin",
        "origine",
        "trouvé via",
        "trouve via",
        "site",
        "origem",
        "encontrado em",
        "plataforma",
    ),
    "tags": (
        "tags",
        "tag",
        "labels",
        "label",
        "étiquettes",
        "etiquettes",
        "mots-clés",
        "etiquetas",
        "rótulos",
        "rotulos",
    ),
    "description": (
        "description",
        "details",
        "descriptif",
        "détails",
        "descrição",
        "descricao",
        "detalhes",
    ),
    "notes": (
        "notes",
        "note",
        "comments",
        "comment",
        "remarks",
        "remarques",
        "commentaires",
        "observations",
        "notas",
        "comentários",
        "comentarios",
        "observações",
        "observacoes",
    ),
}

#: Spreadsheet words for a status, lower-cased, to Postulo's statuses. Anything else
#: becomes "applied" with the original kept in a note.
STATUS_ALIASES: dict[str, str] = {
    "draft": "draft",
    "to apply": "draft",
    "wishlist": "draft",
    "brouillon": "draft",
    "à postuler": "draft",
    "a postuler": "draft",
    "rascunho": "draft",
    "a candidatar": "draft",
    "applied": "applied",
    "sent": "applied",
    "submitted": "applied",
    "pending": "applied",
    "open": "applied",
    "postulé": "applied",
    "postule": "applied",
    "envoyé": "applied",
    "envoyee": "applied",
    "envoyée": "applied",
    "candidature envoyée": "applied",
    "en attente": "applied",
    "candidatei": "applied",
    "enviada": "applied",
    "enviado": "applied",
    "pendente": "applied",
    "em espera": "applied",
    "acknowledged": "acknowledged",
    "received": "acknowledged",
    "confirmed": "acknowledged",
    "accusé de réception": "acknowledged",
    "confirmé": "acknowledged",
    "recebida": "acknowledged",
    "confirmado": "acknowledged",
    "screening": "screening",
    "phone screen": "screening",
    "screen": "screening",
    "recruiter call": "screening",
    "préqualification": "screening",
    "prequalification": "screening",
    "triagem": "screening",
    "pré-seleção": "screening",
    "interview": "interviewing",
    "interviewing": "interviewing",
    "interviews": "interviewing",
    "in interview": "interviewing",
    "entretien": "interviewing",
    "entretiens": "interviewing",
    "en entretien": "interviewing",
    "entrevista": "interviewing",
    "entrevistas": "interviewing",
    "em entrevista": "interviewing",
    "assessment": "assessment",
    "test": "assessment",
    "take-home": "assessment",
    "technical test": "assessment",
    "épreuve": "assessment",
    "épreuve technique": "assessment",
    "teste": "assessment",
    "avaliação": "assessment",
    "avaliacao": "assessment",
    "offer": "offer",
    "offered": "offer",
    "offre": "offer",
    "oferta": "offer",
    "proposta": "offer",
    "accepted": "accepted",
    "hired": "accepted",
    "accepté": "accepted",
    "accepte": "accepted",
    "embauché": "accepted",
    "aceite": "accepted",
    "aceito": "accepted",
    "contratado": "accepted",
    "rejected": "rejected",
    "declined": "rejected",
    "refused": "rejected",
    "no": "rejected",
    "refusé": "rejected",
    "refuse": "rejected",
    "rejeté": "rejected",
    "rejete": "rejected",
    "rejeitado": "rejected",
    "rejeitada": "rejected",
    "recusado": "rejected",
    "recusada": "rejected",
    "withdrawn": "withdrawn",
    "withdrew": "withdrawn",
    "retiré": "withdrawn",
    "retire": "withdrawn",
    "abandonné": "withdrawn",
    "retirada": "withdrawn",
    "desisti": "withdrawn",
    "desistência": "withdrawn",
    "ghosted": "ghosted",
    "no answer": "ghosted",
    "no response": "ghosted",
    "no reply": "ghosted",
    "silence": "ghosted",
    "sans réponse": "ghosted",
    "sans reponse": "ghosted",
    "sem resposta": "ghosted",
}

CHANNEL_ALIASES: dict[str, str] = {
    "company site": "company_site",
    "company website": "company_site",
    "website": "company_site",
    "site": "company_site",
    "site de l'entreprise": "company_site",
    "site da empresa": "company_site",
    "job board": "job_board",
    "board": "job_board",
    "linkedin": "job_board",
    "indeed": "job_board",
    "welcome to the jungle": "job_board",
    "jobboard": "job_board",
    "portail": "job_board",
    "portal": "job_board",
    "email": "email",
    "e-mail": "email",
    "mail": "email",
    "courriel": "email",
    "referral": "referral",
    "referred": "referral",
    "friend": "referral",
    "recommandation": "referral",
    "cooptation": "referral",
    "indicação": "referral",
    "indicacao": "referral",
    "referência": "referral",
    "recruiter": "recruiter",
    "agency": "recruiter",
    "headhunter": "recruiter",
    "chasseur de têtes": "recruiter",
    "cabinet": "recruiter",
    "recrutador": "recruiter",
    "recrutadora": "recruiter",
    "agência": "recruiter",
    "event": "event",
    "fair": "event",
    "job fair": "event",
    "salon": "event",
    "forum": "event",
    "feira": "event",
    "evento": "event",
}


class SheetError(Exception):
    """The file could not be read as a spreadsheet."""


@dataclass
class Sheet:
    filename: str
    headers: list[str]
    rows: list[list[str]]
    delimiter: str
    encoding: str

    @property
    def row_count(self) -> int:
        return len(self.rows)


def read_sheet(data: bytes, filename: str = "import.csv") -> Sheet:
    """Decode and split a CSV, whatever Excel did to it.

    UTF-8 with or without a BOM first, then Latin-1, which decodes anything and is what
    an old Excel on a European machine writes. The delimiter is sniffed from the header
    and first rows, with a semicolon tried explicitly because ``csv.Sniffer`` and French
    Excel disagree often enough to matter.
    """
    if not data.strip():
        raise SheetError(str(_("The file is empty.")))
    if len(data) > MAX_BYTES:
        raise SheetError(str(_("The file is over 2 MB. A job search is not that big.")))
    text, encoding = _decode(data)
    sample = "\n".join(text.splitlines()[:20])
    delimiter = _delimiter(sample)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise SheetError(str(_("The file holds no rows.")))
    headers = [cell.strip() for cell in rows[0]]
    body = [[cell.strip() for cell in row] + [""] * (len(headers) - len(row)) for row in rows[1:]]
    if len(body) > MAX_ROWS:
        raise SheetError(str(_("More than %(limit)s rows; split the file.") % {"limit": MAX_ROWS}))
    return Sheet(
        filename=filename, headers=headers, rows=body, delimiter=delimiter, encoding=encoding
    )


def _decode(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def _delimiter(sample: str) -> str:
    counts = {sep: sample.count(sep) for sep in (",", ";", "\t", "|")}
    try:
        sniffed = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        if counts.get(sniffed, 0) > 0:
            return sniffed
    except csv.Error:
        pass
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


# ------------------------------------------------------------------- mapping


def normalise_header(header: str) -> str:
    return re.sub(r"[\s_\-]+", " ", header.strip().lower().strip(":*")).strip()


def guess_mapping(headers: list[str]) -> list[str]:
    """One field key per header, ``ignore`` where nothing fits. Every guess is editable."""
    mapping: list[str] = []
    taken: set[str] = set()
    for header in headers:
        name = normalise_header(header)
        guess = "ignore"
        for key, words in SYNONYMS.items():
            if key in taken and key != "notes":
                continue
            if name in words or any(
                name.startswith(word + " ") or name.endswith(" " + word) for word in words
            ):
                guess = key
                break
        if guess != "ignore":
            taken.add(guess)
        mapping.append(guess)
    return mapping


def clean_mapping(values: list[str], headers: list[str]) -> list[str]:
    """Only known fields, one column each except notes and ignore."""
    mapping: list[str] = []
    taken: set[str] = set()
    for index, _header in enumerate(headers):
        value = values[index] if index < len(values) else "ignore"
        if value not in FIELD_KEYS or (value in taken and value not in ("ignore", "notes")):
            value = "ignore"
        taken.add(value)
        mapping.append(value)
    return mapping


# ------------------------------------------------------------------- parsing

_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "janv", "january", "janvier", "janeiro"),
            ("feb", "fév", "fev", "february", "février", "fevereiro"),
            ("mar", "mars", "march", "março", "marco"),
            ("apr", "avr", "april", "avril", "abril", "abr"),
            ("may", "mai", "maio"),
            ("jun", "juin", "june", "junho"),
            ("jul", "juil", "july", "juillet", "julho"),
            ("aug", "août", "aout", "august", "agosto", "ago"),
            ("sep", "sept", "september", "septembre", "setembro", "set"),
            ("oct", "october", "octobre", "outubro", "out"),
            ("nov", "november", "novembre", "novembro"),
            ("dec", "déc", "dec", "december", "décembre", "dezembro", "dez"),
        ],
        start=1,
    )
    for name in names
}


def parse_date(text: str, *, day_first: bool = True) -> dt.date | None:
    """A date from the ways people type them; ``None`` when there is none.

    ISO first, because it is unambiguous. Then numeric forms with ``/``, ``-`` or ``.``,
    read day-first or month-first as chosen once for the whole file. Then a month name,
    in the three languages.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})(?:\s.*)?", text)
    if match:
        a, b, c = (int(part) for part in match.groups())
        try:
            if a > 31:
                return dt.date(a, b, c)
            year = c if c > 99 else 2000 + c
            day, month = (a, b) if day_first else (b, a)
            if month > 12 and day <= 12:
                day, month = month, day
            return dt.date(year, month, day)
        except ValueError:
            return None
    words = re.findall(r"[A-Za-zÀ-ÿ]+|\d+", text.lower())
    numbers = [int(word) for word in words if word.isdigit()]
    month = next((_MONTHS[word.rstrip(".")] for word in words if word.rstrip(".") in _MONTHS), None)
    if month and len(numbers) >= 2:
        day = next((n for n in numbers if n <= 31), None)
        year = next((n for n in numbers if n > 31), None)
        if day and year:
            try:
                return dt.date(year if year > 99 else 2000 + year, month, day)
            except ValueError:
                return None
    return None


def parse_money(text: str) -> Decimal | None:
    """``55 000 €``, ``$55,000``, ``55k`` and friends as a number; ``None`` otherwise."""
    text = (text or "").strip().lower()
    if not text:
        return None
    multiplier = 1000 if re.search(r"\d\s*k\b", text) else 1
    digits = re.sub(r"[^\d.,]", "", text)
    if not digits:
        return None
    # Thousands separators: a comma or dot followed by exactly three digits and more.
    digits = re.sub(r"[.,](?=\d{3}(?:\D|$))", "", digits)
    digits = digits.replace(",", ".")
    try:
        return Decimal(digits) * multiplier
    except InvalidOperation:
        return None


def parse_salary_range(text: str) -> tuple[Decimal | None, Decimal | None]:
    parts = re.split(r"\s*(?:-|–|—|to|à|a|/)\s*", (text or "").strip(), maxsplit=1)
    if len(parts) == 2:
        low, high = parse_money(parts[0]), parse_money(parts[1])
        if low is not None and high is not None:
            return (low, high) if low <= high else (high, low)
    single = parse_money(text)
    return single, single


def map_status(text: str) -> tuple[str, bool]:
    """Postulo's status for a spreadsheet word, and whether it was recognised."""
    key = " ".join((text or "").lower().split())
    if not key:
        return "applied", True
    if key in STATUS_ALIASES:
        return STATUS_ALIASES[key], True
    for alias, status in STATUS_ALIASES.items():
        if alias in key:
            return status, True
    return "applied", False


def map_channel(text: str) -> str:
    key = " ".join((text or "").lower().split())
    if not key:
        return ""
    if key in CHANNEL_ALIASES:
        return CHANNEL_ALIASES[key]
    for alias, channel in CHANNEL_ALIASES.items():
        if alias in key:
            return channel
    return "other"


# ------------------------------------------------------------------- rows


@dataclass
class ParsedRow:
    number: int
    company: str = ""
    wikidata: str = ""
    role: str = ""
    url: str = ""
    location: str = ""
    status: str = "applied"
    status_text: str = ""
    status_known: bool = True
    applied_at: dt.date | None = None
    deadline: dt.date | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    channel: str = ""
    source: str = ""
    tags: list[str] = field(default_factory=list)
    description: str = ""
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def becomes(self) -> str:
        if self.problems:
            return "skipped"
        return "application" if self.applied_at else "listing"


def parse_rows(sheet: Sheet, mapping: list[str], *, day_first: bool = True) -> list[ParsedRow]:
    parsed: list[ParsedRow] = []
    for number, cells in enumerate(sheet.rows, start=2):
        row = ParsedRow(number=number)
        for index, key in enumerate(mapping):
            value = cells[index] if index < len(cells) else ""
            if key == "ignore" or not value:
                continue
            if key == "company":
                row.company = value[:200]
            elif key == "wikidata":
                row.wikidata = value[:200]
            elif key == "role":
                row.role = value[:250]
            elif key == "url":
                row.url = value[:500] if value.startswith(("http://", "https://")) else ""
                if not row.url:
                    row.notes.append(f"{sheet.headers[index]}: {value}")
            elif key == "location":
                row.location = value[:200]
            elif key == "status":
                row.status_text = value
                row.status, row.status_known = map_status(value)
            elif key == "applied_at":
                row.applied_at = parse_date(value, day_first=day_first)
                if row.applied_at is None:
                    row.notes.append(f"{sheet.headers[index]}: {value}")
            elif key == "deadline":
                row.deadline = parse_date(value, day_first=day_first)
            elif key == "salary_min":
                row.salary_min = parse_money(value)
            elif key == "salary_max":
                row.salary_max = parse_money(value)
            elif key == "salary":
                row.salary_min, row.salary_max = parse_salary_range(value)
            elif key == "channel":
                row.channel = map_channel(value)
            elif key == "source":
                row.source = value[:120]
            elif key == "tags":
                row.tags = [tag.strip() for tag in re.split(r"[;,|/]", value) if tag.strip()]
            elif key == "description":
                row.description = value
            elif key == "notes":
                row.notes.append(value)
        if not row.company:
            row.problems.append(str(_("no company")))
        if not row.role:
            row.problems.append(str(_("no role")))
        if not row.status_known:
            row.notes.append(
                str(_("Status in the spreadsheet: %(text)s") % {"text": row.status_text})
            )
        parsed.append(row)
    return parsed


# ------------------------------------------------------------------- importing


@dataclass
class CsvReport:
    filename: str
    rows: int = 0
    applications: int = 0
    listings: int = 0
    companies_created: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_lines(self) -> list[str]:
        lines = [
            f"{self.rows} rows read from {self.filename}",
            f"  {self.applications} applications and {self.listings} listings imported",
            f"  {self.companies_created} companies created",
        ]
        if self.skipped:
            lines.append(f"  {len(self.skipped)} rows skipped")
        return lines


def perform(user, sheet: Sheet, mapping: list[str], *, day_first: bool = True) -> CsvReport:
    """Import the sheet in one transaction. Duplicates are reported, never created."""
    from postulo.applications.models import Application, EventKind
    from postulo.applications.services import (
        change_status,
        create_application,
        create_listing,
        get_or_create_company,
        record_event,
    )
    from postulo.core.models import Tag
    from postulo.jobs.models import Company, JobPosting

    report = CsvReport(filename=sheet.filename, rows=sheet.row_count)
    provenance = str(_("Imported from %(file)s") % {"file": sheet.filename})

    with transaction.atomic():
        for row in parse_rows(sheet, mapping, day_first=day_first):
            if row.problems:
                report.skipped.append(f"row {row.number}: {', '.join(row.problems)}")
                continue

            if row.url and JobPosting.objects.for_user(user).filter(url=row.url).exists():
                report.skipped.append(f"row {row.number}: already recorded (same address)")
                continue
            existed = (
                Company.objects.for_user(user).filter(name__iexact=row.company.strip()).exists()
                or Company.by_identifier(user, "wikidata", row.wikidata) is not None
            )
            company = get_or_create_company(user, row.company, wikidata=row.wikidata)
            if not existed:
                report.companies_created += 1
            if row.applied_at is not None:
                twin = Application.objects.for_user(user).filter(
                    posting__company=company,
                    posting__title__iexact=row.role,
                    applied_at__date=row.applied_at,
                )
                if twin.exists():
                    report.skipped.append(
                        f"row {row.number}: already recorded "
                        f"({row.role} at {company.name}, {row.applied_at})"
                    )
                    continue

            posting_data = {
                "title": row.role,
                "url": row.url,
                "location": row.location,
                "source": row.source,
                "description": row.description,
                "salary_min": row.salary_min,
                "salary_max": row.salary_max,
                "salary_currency": "EUR" if row.salary_min or row.salary_max else "",
                "closes_at": None,
            }
            notes = "\n".join(row.notes)

            if row.applied_at is None:
                listing = create_listing(user, company=company, posting_data=posting_data)
                if notes:
                    listing.description = (listing.description + "\n\n" + notes).strip()
                    listing.save(update_fields=["description", "updated_at"])
                report.listings += 1
                continue

            applied_moment = timezone.make_aware(
                dt.datetime.combine(row.applied_at, dt.time(12, 0)), timezone.get_current_timezone()
            )
            application = create_application(
                user,
                company=company,
                posting_data=posting_data,
                application_data={
                    "status": "draft",
                    "channel": row.channel,
                    "priority": 2,
                    "deadline": row.deadline,
                },
                actor=provenance,
            )
            change_status(application, "applied", occurred_at=applied_moment, actor=provenance)
            if row.status not in ("applied", "draft"):
                change_status(application, row.status, occurred_at=applied_moment, actor=provenance)
            record_event(
                application,
                kind=EventKind.OTHER,
                summary=provenance,
                body=notes,
                occurred_at=applied_moment,
                actor=provenance,
            )
            if row.tags:
                tags = []
                for name in row.tags:
                    tag = Tag.objects.for_user(user).filter(name__iexact=name).first()
                    tags.append(tag or Tag.objects.create(owner=user, name=name[:60]))
                application.tags.set(tags)
            report.applications += 1
    return report


# ------------------------------------------------------------------- template


TEMPLATE_HEADERS = (
    "Company",
    "Role",
    "URL",
    "Location",
    "Status",
    "Date applied",
    "Deadline",
    "Salary",
    "Applied through",
    "Found via",
    "Tags",
    "Notes",
)
TEMPLATE_EXAMPLE = (
    "Aperture Science",
    "Research Engineer",
    "https://aperture.example/jobs/42",
    "Cambridge",
    "Interviewing",
    "2026-09-01",
    "",
    "55000-65000",
    "Job board",
    "LinkedIn",
    "remote; dream job",
    "Spoke to Cave on the phone first.",
)


def template_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(TEMPLATE_HEADERS)
    writer.writerow(TEMPLATE_EXAMPLE)
    return out.getvalue()


# -------------------------------------------------------------- session store

SESSION_KEY = "csv_import"


def stash(session, data: bytes, filename: str) -> None:
    session[SESSION_KEY] = {"filename": filename, "data": base64.b64encode(data).decode("ascii")}


def unstash(session) -> Sheet | None:
    stored = session.get(SESSION_KEY)
    if not stored:
        return None
    try:
        return read_sheet(base64.b64decode(stored["data"]), stored["filename"])
    except (SheetError, KeyError, ValueError):
        return None


def forget(session) -> None:
    session.pop(SESSION_KEY, None)
