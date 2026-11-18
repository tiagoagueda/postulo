# Where `eurostat-earnings.json` came from, and on what terms

**What it is.** Gross earnings of employees by occupation (ISCO-08 major group, and the three
wider groups Eurostat publishes) for each country of the EU, EFTA and the candidate countries
the survey covers, from Eurostat's **Structure of Earnings Survey 2022**, with the median and
the first and ninth deciles of the 2018 and 2022 waves, and the **monthly minimum wage** of
the latest half-year. Read by the bundled `earnings` plugin (#1257).

**Who publishes it.** Eurostat, the statistical office of the European Union, through its
dissemination API (<https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/>,
JSON-stat 2.0, no key). The datasets, and the slice of each that was kept:

| Data code | What | Slice kept |
| --- | --- | --- |
| `earn_ses22_28` | Mean annual earnings by sex, age and occupation, 2022 | sex `T`, age `TOTAL`, `indic_se=ERN`, enterprises of ten or more (`GE10`), euro and national currency |
| `earn_ses22_21` | Mean monthly earnings, the same breakdown | the same |
| `earn_ses22_14` | Mean hourly earnings, the same breakdown | the same |
| `earn_ses_annual` | Structure of earnings survey: annual earnings | `nace_r2=B-S_X_O`, `worktime=TOT_FTE`, sex `T`, age `TOTAL`, median, first and ninth decile in euro, 2018 and 2022 |
| `earn_ses_monthly` | Structure of earnings survey: monthly earnings | the same |
| `earn_ses_hourly` | Structure of earnings survey: hourly earnings | the same, `worktime=TOTAL` |
| `earn_mw_cur` | Monthly minimum wages, bi-annual data | the latest half-year, euro and national currency |

Each dataset's DOI is `10.2908/` followed by its code in capitals (`10.2908/EARN_SES22_28`).
The day it was retrieved, and the date Eurostat last updated each dataset, are inside the
file (`retrieved`, `datasets`).

**Under what licence.** Eurostat's copyright notice
(<https://ec.europa.eu/eurostat/help/copyright-notice>) allows reuse of its statistical data,
commercial or not, under the Commission's reuse policy (**Commission Decision 2011/833/EU**),
whose default licence is **Creative Commons Attribution 4.0 International (CC BY 4.0)**, with
the source acknowledged and any change stated. Its one exception that applies here: data on
countries **other than** the EU Member States, the EFTA countries and the acceding and
candidate countries may not be reused for commercial purposes. Postulo is AGPL and anyone may
run it commercially, so no such country is in the file: the **United Kingdom** (in the 2018
tables and the minimum wages) and the **United States** (in the minimum wages) were left out.

**Attribution, as required.**

> Source: Eurostat, Structure of Earnings Survey 2022 (earn_ses22_28, earn_ses22_21,
> earn_ses22_14, earn_ses_annual, earn_ses_monthly, earn_ses_hourly) and minimum wages
> (earn_mw_cur), retrieved on the date the file records. © European Union. Licensed under
> CC BY 4.0. Figures unchanged.

The card that draws these figures names Eurostat, the data codes, the retrieval date, the
licence and a link to the dataset under every set of figures.

**Changes made, as required.** Only selection and shape, never a value:

- reduced to the slices in the table above;
- Eurostat's aggregates (EU27_2020, EA20 and the like) left out;
- the United Kingdom and the United States left out, for the reason above;
- Eurostat's country code `EL` stored as `GR` (ISO 3166-1);
- each amount keyed by the ISO 4217 code it was published in: the national-currency figure
  under the code the country used in the reference period (October 2022 for the survey: kuna
  for Croatia, lev for Bulgaria; the half-year's start for the minimum wage), read from CLDR,
  and left out where it equals the euro figure, which is then the same euro amount;
- no amount converted, rounded, averaged or added.

**Keeping it current.** `manage.py fetch_earnings` repeats the seven requests and writes the
same shape into `POSTULO_EARNINGS_DIR`; the plugin reads whichever copy was retrieved later.
The minimum wage changes twice a year. The survey is four-yearly: its 2026 wave is due about
2028, under codes that will carry the year (`earn_ses26_*`), asked for with `--wave 2026`.
