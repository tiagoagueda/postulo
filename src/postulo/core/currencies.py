"""The currencies a job advert carries, as three-letter codes. One list, read twice.

The spreadsheet importer reads a salary column with it, and a source reading a page with no
structured data asks whether a three-letter word is one (#267) -- through the plugin
surface, which hands out this very object. It is a module of its own because the importer
reads the models, and the models reach the registry of plugins: a built-in source asking the
surface for the list closed a loop through them (#248). `csv_import` still hands it out.
"""

from __future__ import annotations

#: Codes recognised without a symbol. Not all of ISO 4217: a three-letter word in a salary
#: column is more often an abbreviation than a currency, and guessing wrong writes somebody
#: else's money into the record. These are the ones a job advert actually carries.
CURRENCY_CODES = frozenset(
    """EUR USD GBP CHF SEK NOK DKK PLN CZK HUF RON BGN HRK ISK JPY CNY INR AUD CAD NZD
    SGD HKD ZAR BRL MXN ARS CLP COP TRY ILS AED SAR KRW THB MYR IDR PHP VND UAH RSD
    MAD TND EGP NGN KES GHS""".split()
)
