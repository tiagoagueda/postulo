"""Earnings statistics: Eurostat's Structure of Earnings Survey, beside a listing (#1257).

The first source behind the `earnings-source` kind. For a listing's ISCO-08 unit group and
country it answers with what Eurostat published: the mean earnings of the major group the unit
group belongs to, the median and the first and ninth deciles of the wider group that holds it,
and the national minimum wage, for every country of the EU, EFTA and the candidate countries
the survey covers. That is the grain Eurostat publishes, the major group (ten for the whole
labour market), and each figure names its group so the card does not claim more.

**Bundled and off by default** (a migration writes the instance's "off" row): a statistic
beside a salary is an addition an administrator chooses, as the issue asked. It reads a file
that ships with Postulo (`data/eurostat-earnings.json`, with its licence note beside it) and
asks nobody anything while anybody works; ``manage.py fetch_earnings`` refreshes it from
Eurostat's API, the one request it makes and only when an administrator runs it.

**Switched off, nothing is kept or lost**: it holds no rows, so off is the card gone.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from postulo.plugins.api import EarningsSource, declares, shipped

#: The identifier the registry and the policy rows key on.
EARNINGS = "earnings"


@declares(
    shipped(
        name=EARNINGS,
        label=_("Earnings statistics"),
        kind="earnings-source",
        description=_(
            "Shows, beside a listing and its application, what Eurostat published about the "
            "earnings of the listing's group of occupations in its country: a mean, a median "
            "and deciles, gross, with the year and the source, and the national minimum wage. "
            "A statistic about many jobs, never a recommendation. It reads a file that ships "
            "with Postulo, refreshed by an administrator with manage.py fetch_earnings, and "
            "asks no service while you work."
        ),
    )
)
class Earnings:
    """A source of earnings statistics. Postulo asks it; it asks nobody."""

    @property
    def sources(self) -> tuple:
        """Eurostat's figures, or none where no readable file is here."""
        from . import reader

        countries = reader.countries()
        if not countries:
            return ()
        return (
            EarningsSource(
                key="eurostat",
                label="Eurostat",
                countries=countries,
                offline=True,
                licence=reader.LICENCE,
                figures=reader.figures,
                scope=reader.SCOPE,
                citation=reader.citation(),
                # The first table the file names: the wave's yearly means.
                url=next(iter(reader.document()["datasets"].values()))["url"],
            ),
        )
