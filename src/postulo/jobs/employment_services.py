"""The public employment services, by country: the office a job seeker is registered with.

France Travail, the Bundesagentur für Arbeit, IEFP, SEPE, Jobcentre Plus. Each is a place
somebody deals with throughout a search -- an adviser, appointments, a monthly declaration,
the report Postulo produces -- and none is an employer. A company of the *employment
service* kind (#202) is how Postulo records one, and this registry is what makes adding it
a choice rather than a typing exercise: the company form offers the list, and picking a
row fills the name and the website where the person left them blank.

Listed by country and picked by the person, because the language they read Postulo in is a
poor proxy: a Belgian reads French and deals with Forem, VDAB or Actiris. The country names
come from the same table the telephone numbers use, in English, for the same reason that
table gives: everyone recognises their country in English, and a grouped list is read by
its headings.

Nothing here is looked up anywhere. A row is a name and a public address, both of which
the person can change on the form afterwards, and nothing is sent to any of them.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.text import slugify

from postulo.core import phones


@dataclass(frozen=True)
class Service:
    #: ISO 3166-1 alpha-2, upper case, as `phones.COUNTRIES` keys it.
    country: str
    name: str
    website: str

    @property
    def key(self) -> str:
        """What the form posts: the country and the slug of the name, ``fr:france-travail``."""
        return f"{self.country.lower()}:{slugify(self.name)}"

    @property
    def country_name(self) -> str:
        return phones.country_name(self.country)


#: Every service Postulo knows, in no particular order; `grouped` sorts them. One row per
#: office that a person is registered with, not per board -- EURES is a portal and a job
#: board recipe, not somebody's office. Regional services are listed where the region is
#: the office: Belgium has three, Switzerland's cantonal offices share one front door.
SERVICES: tuple[Service, ...] = (
    Service("AT", "Arbeitsmarktservice (AMS)", "https://www.ams.at"),
    Service("AU", "Workforce Australia", "https://www.workforceaustralia.gov.au"),
    Service("BE", "Actiris", "https://www.actiris.brussels"),
    Service("BE", "Forem", "https://www.leforem.be"),
    Service("BE", "VDAB", "https://www.vdab.be"),
    Service("BG", "Агенция по заетостта", "https://www.az.government.bg"),
    Service("CA", "Service Canada (Job Bank)", "https://www.jobbank.gc.ca"),
    Service("CH", "RAV / ORP / URC", "https://www.arbeit.swiss"),
    Service("CY", "Δημόσια Υπηρεσία Απασχόλησης (ΔΥΑ)", "https://www.pes.mlsi.gov.cy"),
    Service("CZ", "Úřad práce ČR", "https://www.uradprace.cz"),
    Service("DE", "Bundesagentur für Arbeit", "https://www.arbeitsagentur.de"),
    Service("DK", "Jobcenter (Jobnet)", "https://www.jobnet.dk"),
    Service("EE", "Töötukassa", "https://www.tootukassa.ee"),
    Service("ES", "Servicio Público de Empleo Estatal (SEPE)", "https://www.sepe.es"),
    Service("FI", "Työmarkkinatori", "https://tyomarkkinatori.fi"),
    Service("FR", "France Travail", "https://www.francetravail.fr"),
    Service("GB", "Jobcentre Plus", "https://www.gov.uk/contact-jobcentre-plus"),
    Service("GR", "Δημόσια Υπηρεσία Απασχόλησης (ΔΥΠΑ)", "https://www.dypa.gov.gr"),
    Service("HR", "Hrvatski zavod za zapošljavanje (HZZ)", "https://www.hzz.hr"),
    Service("HU", "Nemzeti Foglalkoztatási Szolgálat", "https://nfsz.munka.hu"),
    Service("IE", "Intreo", "https://www.gov.ie/intreo"),
    Service("IS", "Vinnumálastofnun", "https://www.vinnumalastofnun.is"),
    Service("IT", "Centro per l'impiego", "https://www.anpal.gov.it"),
    Service("LT", "Užimtumo tarnyba", "https://uzt.lt"),
    Service("LU", "ADEM", "https://adem.public.lu"),
    Service("LV", "Nodarbinātības valsts aģentūra (NVA)", "https://www.nva.gov.lv"),
    Service("MT", "Jobsplus", "https://jobsplus.gov.mt"),
    Service("NL", "UWV", "https://www.uwv.nl"),
    Service("NO", "NAV", "https://www.nav.no"),
    Service("NZ", "Work and Income", "https://www.workandincome.govt.nz"),
    Service("PL", "Powiatowy Urząd Pracy", "https://psz.praca.gov.pl"),
    Service("PT", "Instituto do Emprego e Formação Profissional (IEFP)", "https://www.iefp.pt"),
    Service(
        "RO", "Agenția Națională pentru Ocuparea Forței de Muncă (ANOFM)", "https://www.anofm.ro"
    ),
    Service("SE", "Arbetsförmedlingen", "https://arbetsformedlingen.se"),
    Service("SI", "Zavod RS za zaposlovanje", "https://www.ess.gov.si"),
    Service("SK", "Úrad práce, sociálnych vecí a rodiny", "https://www.upsvr.gov.sk"),
    Service("US", "American Job Center", "https://www.careeronestop.org"),
)

_BY_KEY: dict[str, Service] = {service.key: service for service in SERVICES}


def by_key(key: str) -> Service | None:
    """The service a form posted, or ``None`` for a key Postulo does not know."""
    return _BY_KEY.get((key or "").strip().lower())


def grouped() -> list[tuple[str, list[tuple[str, str]]]]:
    """The registry as a grouped choice list: country name, then (key, name) rows under it.

    Countries in English alphabetical order, services in alphabetical order within each,
    which is the shape a `<select>` with `<optgroup>` wants and the order a person reads a
    list of countries in.
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    for service in sorted(SERVICES, key=lambda s: (s.country_name, s.name)):
        groups.setdefault(service.country_name, []).append((service.key, service.name))
    return list(groups.items())
