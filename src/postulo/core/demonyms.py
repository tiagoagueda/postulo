"""Nationalities named as nationalities -- "Portuguese", not "Portugal" (#865).

A nationality is held as a country's ISO 3166-1 alpha-2 code (`core.personal`), and was printed
as the country's English name, which is the country and not what somebody who holds its
citizenship is called. This is the table that says what they are called, in the languages a CV
is written in here: English, French and Portuguese. A language without a table, and a country
without a word in a language that has one, fall back to the country's name as it always was,
so a CV is never left blank and nothing that was printed before stops being.

**Where the words come from.** CLDR (Babel) has territory names and no demonyms. The words
are Wikidata's *demonym* property (P1549), taken once and read through by hand rather than
copied: Wikidata mixes singulars and plurals, capitals and lower case, several words for one
country, and does not say which form is the feminine. The query that was run, on 2026-10-11:

    SELECT ?code ?d (LANG(?d) AS ?lang) WHERE {
      ?c wdt:P297 ?code . ?c p:P1549 ?st . ?st ps:P1549 ?d .
      FILTER(LANG(?d) IN ("en","fr","pt","pt-br","pt-pt")) }

It had English for 236 of the 242 codes it knew, French for 215, and Portuguese for 29, so
the Portuguese is mostly written by hand. The last column of every row below names the
languages in which at least one of the row's words is **not** among Wikidata's, so
the table says where it stands on someone else's word and where on ours: those are the words
to check first, and `tests/test_demonyms.py` holds the table to its shape so that a row
cannot be added without saying.

**Gender.** French and Portuguese agree with the person: *portugais* / *portugaise*,
*português* / *portuguesa*. The form is the feminine when the person's gender is the list's
first answer (*Female*, *Féminin*, *Feminino*) and the masculine, which is the dictionary form,
in every other case -- unset, non-binary and text typed under *Other…* included. Nothing is
worked out from a name, and nothing here sets or suggests a gender. English has one form.

A word is written as a dictionary writes it: English with a capital, French and Portuguese in
lower case, since both write a nationality that way. The list is not every word a country's
people use for themselves; it is the one a form or a CV would print.
"""

from __future__ import annotations

from . import languages, phones

#: The languages a table is kept in, and the columns of a row: the code, English, French
#: (masculine, feminine), Portuguese (masculine, feminine), and the languages in which the row
#: has a word Wikidata does not (see the module's note). An empty word is no word.
LANGUAGES = ("en", "fr", "pt")

ROWS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    ("AD", "Andorran", "andorran", "andorrane", "andorrano", "andorrana", "pt"),
    ("AE", "Emirati", "émirien", "émirienne", "emiradense", "emiradense", "pt"),
    ("AF", "Afghan", "afghan", "afghane", "afegão", "afegã", "pt"),
    (
        "AG",
        "Antiguan and Barbudan",
        "antiguais et barbudien",
        "antiguaise et barbudienne",
        "antiguano e barbudano",
        "antiguana e barbudana",
        "en,fr,pt",
    ),
    ("AI", "Anguillan", "anguillais", "anguillaise", "anguilano", "anguilana", "fr,pt"),
    ("AL", "Albanian", "albanais", "albanaise", "albanês", "albanesa", "pt"),
    ("AM", "Armenian", "arménien", "arménienne", "arménio", "arménia", "pt"),
    ("AO", "Angolan", "angolais", "angolaise", "angolano", "angolana", "pt"),
    ("AR", "Argentine", "argentin", "argentine", "argentino", "argentina", "pt"),
    ("AS", "American Samoan", "", "", "", "", ""),
    ("AT", "Austrian", "autrichien", "autrichienne", "austríaco", "austríaca", "pt"),
    ("AU", "Australian", "australien", "australienne", "australiano", "australiana", "pt"),
    ("AW", "Aruban", "arubais", "arubaise", "arubano", "arubana", "fr,pt"),
    ("AX", "Ålander", "ålandais", "ålandaise", "alandês", "alandesa", "en,fr"),
    ("AZ", "Azerbaijani", "azerbaïdjanais", "azerbaïdjanaise", "azerbaijano", "azerbaijana", "pt"),
    ("BA", "Bosnian", "bosnien", "bosnienne", "bósnio", "bósnia", "pt"),
    ("BB", "Barbadian", "barbadien", "barbadienne", "barbadense", "barbadense", "pt"),
    ("BD", "Bangladeshi", "bangladais", "bangladaise", "bangladechiano", "bangladechiana", "pt"),
    ("BE", "Belgian", "belge", "belge", "belga", "belga", "pt"),
    ("BF", "Burkinabé", "burkinabé", "burkinabé", "burquinense", "burquinense", "fr,pt"),
    ("BG", "Bulgarian", "bulgare", "bulgare", "búlgaro", "búlgara", "pt"),
    ("BH", "Bahraini", "bahreïni", "bahreïnie", "bareinita", "bareinita", "fr,pt"),
    ("BI", "Burundian", "burundais", "burundaise", "burundiano", "burundiana", "pt"),
    ("BJ", "Beninese", "béninois", "béninoise", "beninense", "beninense", "pt"),
    ("BL", "", "barthélemois", "barthélemoise", "", "", "fr"),
    ("BM", "Bermudian", "bermudien", "bermudienne", "bermudense", "bermudense", "fr,pt"),
    ("BN", "Bruneian", "brunéien", "brunéienne", "bruneano", "bruneana", "en,pt"),
    ("BO", "Bolivian", "bolivien", "bolivienne", "boliviano", "boliviana", "pt"),
    ("BQ", "", "", "", "", "", ""),
    ("BR", "Brazilian", "brésilien", "brésilienne", "brasileiro", "brasileira", ""),
    ("BS", "Bahamian", "bahaméen", "bahaméenne", "baamiano", "baamiana", "pt"),
    ("BT", "Bhutanese", "bhoutanais", "bhoutanaise", "butanês", "butanesa", "pt"),
    ("BW", "Botswanan", "botswanais", "botswanaise", "botsuano", "botsuana", "pt"),
    ("BY", "Belarusian", "biélorusse", "biélorusse", "bielorrusso", "bielorrussa", "pt"),
    ("BZ", "Belizean", "bélizien", "bélizienne", "belizenho", "belizenha", "pt"),
    ("CA", "Canadian", "canadien", "canadienne", "canadiano", "canadiana", ""),
    ("CD", "Congolese (DRC)", "congolais", "congolaise", "congolês", "congolesa", "en,pt"),
    (
        "CF",
        "Central African",
        "centrafricain",
        "centrafricaine",
        "centro-africano",
        "centro-africana",
        "pt",
    ),
    (
        "CG",
        "Congolese (Republic of the Congo)",
        "congolais",
        "congolaise",
        "congolês",
        "congolesa",
        "en,pt",
    ),
    ("CH", "Swiss", "suisse", "suissesse", "suíço", "suíça", "pt"),
    ("CI", "Ivorian", "ivoirien", "ivoirienne", "marfinense", "marfinense", "pt"),
    ("CK", "Cook Islander", "cookien", "cookienne", "cookiano", "cookiana", "fr,pt"),
    ("CL", "Chilean", "chilien", "chilienne", "chileno", "chilena", "pt"),
    ("CM", "Cameroonian", "camerounais", "camerounaise", "camaronês", "camaronesa", "pt"),
    ("CN", "Chinese", "chinois", "chinoise", "chinês", "chinesa", "pt"),
    ("CO", "Colombian", "colombien", "colombienne", "colombiano", "colombiana", "pt"),
    ("CR", "Costa Rican", "costaricien", "costaricienne", "costa-riquenho", "costa-riquenha", "pt"),
    ("CU", "Cuban", "cubain", "cubaine", "cubano", "cubana", "pt"),
    ("CV", "Cape Verdean", "cap-verdien", "cap-verdienne", "cabo-verdiano", "cabo-verdiana", "pt"),
    ("CW", "Curaçaoan", "curaçaoan", "curaçaoane", "curaçauense", "curaçauense", "fr,pt"),
    ("CY", "Cypriot", "chypriote", "chypriote", "cipriota", "cipriota", "pt"),
    ("CZ", "Czech", "tchèque", "tchèque", "checo", "checa", "pt"),
    ("DE", "German", "allemand", "allemande", "alemão", "alemã", ""),
    ("DJ", "Djiboutian", "djiboutien", "djiboutienne", "jibutiano", "jibutiana", "pt"),
    ("DK", "Danish", "danois", "danoise", "dinamarquês", "dinamarquesa", "pt"),
    ("DM", "Dominican", "dominiquais", "dominiquaise", "dominicano", "dominicana", "pt"),
    ("DO", "Dominican", "dominicain", "dominicaine", "dominicano", "dominicana", "pt"),
    ("DZ", "Algerian", "algérien", "algérienne", "argelino", "argelina", "pt"),
    ("EC", "Ecuadorian", "équatorien", "équatorienne", "equatoriano", "equatoriana", "pt"),
    ("EE", "Estonian", "estonien", "estonienne", "estónio", "estónia", "pt"),
    ("EG", "Egyptian", "égyptien", "égyptienne", "egípcio", "egípcia", "pt"),
    ("EH", "Sahrawi", "sahraoui", "sahraouie", "saarauí", "saarauí", "en,fr,pt"),
    ("ER", "Eritrean", "érythréen", "érythréenne", "eritreu", "eritreia", "pt"),
    ("ES", "Spanish", "espagnol", "espagnole", "espanhol", "espanhola", ""),
    ("ET", "Ethiopian", "éthiopien", "éthiopienne", "etíope", "etíope", "pt"),
    ("FI", "Finnish", "finlandais", "finlandaise", "finlandês", "finlandesa", "pt"),
    ("FJ", "Fijian", "fidjien", "fidjienne", "fijiano", "fijiana", "pt"),
    ("FK", "Falkland Islander", "", "", "", "", "en"),
    ("FM", "Micronesian", "micronésien", "micronésienne", "micronésio", "micronésia", "pt"),
    ("FO", "Faroese", "féroïen", "féroïenne", "feroês", "feroesa", "fr,pt"),
    ("FR", "French", "français", "française", "francês", "francesa", ""),
    ("GA", "Gabonese", "gabonais", "gabonaise", "gabonês", "gabonesa", "pt"),
    ("GB", "British", "britannique", "britannique", "britânico", "britânica", "pt"),
    ("GD", "Grenadian", "grenadien", "grenadienne", "granadino", "granadina", "pt"),
    ("GE", "Georgian", "géorgien", "géorgienne", "georgiano", "georgiana", "pt"),
    ("GF", "French Guianese", "guyanais", "guyanaise", "guianense", "guianense", "fr,pt"),
    ("GG", "", "", "", "", "", ""),
    ("GH", "Ghanaian", "ghanéen", "ghanéenne", "ganês", "ganesa", "pt"),
    ("GI", "Gibraltarian", "gibraltarien", "gibraltarienne", "gibraltarino", "gibraltarina", "pt"),
    ("GL", "Greenlandic", "groenlandais", "groenlandaise", "gronelandês", "gronelandesa", "pt"),
    ("GM", "Gambian", "gambien", "gambienne", "gambiano", "gambiana", "pt"),
    ("GN", "Guinean", "guinéen", "guinéenne", "guineense", "guineense", "pt"),
    (
        "GP",
        "Guadeloupean",
        "guadeloupéen",
        "guadeloupéenne",
        "guadalupense",
        "guadalupense",
        "fr,pt",
    ),
    (
        "GQ",
        "Equatorial Guinean",
        "équatoguinéen",
        "équatoguinéenne",
        "guineu-equatoriano",
        "guineu-equatoriana",
        "pt",
    ),
    ("GR", "Greek", "grec", "grecque", "grego", "grega", "pt"),
    ("GT", "Guatemalan", "guatémaltèque", "guatémaltèque", "guatemalteco", "guatemalteca", "pt"),
    ("GU", "Guamanian", "guamien", "guamienne", "guamense", "guamense", "fr,pt"),
    (
        "GW",
        "Bissau-Guinean",
        "bissau-guinéen",
        "bissau-guinéenne",
        "guineense",
        "guineense",
        "fr,pt",
    ),
    ("GY", "Guyanese", "guyanien", "guyanienne", "guianense", "guianense", "pt"),
    ("HK", "Hong Konger", "hongkongais", "hongkongaise", "hongconguês", "hongconguesa", "en,pt"),
    ("HN", "Honduran", "hondurien", "hondurienne", "hondurenho", "hondurenha", "pt"),
    ("HR", "Croatian", "croate", "croate", "croata", "croata", "pt"),
    ("HT", "Haitian", "haïtien", "haïtienne", "haitiano", "haitiana", "pt"),
    ("HU", "Hungarian", "hongrois", "hongroise", "húngaro", "húngara", "pt"),
    ("ID", "Indonesian", "indonésien", "indonésienne", "indonésio", "indonésia", "pt"),
    ("IE", "Irish", "irlandais", "irlandaise", "irlandês", "irlandesa", "pt"),
    ("IL", "Israeli", "israélien", "israélienne", "israelita", "israelita", ""),
    ("IM", "Manx", "mannois", "mannoise", "", "", "fr"),
    ("IN", "Indian", "indien", "indienne", "indiano", "indiana", "pt"),
    ("IO", "", "", "", "", "", ""),
    ("IQ", "Iraqi", "irakien", "irakienne", "iraquiano", "iraquiana", "pt"),
    ("IR", "Iranian", "iranien", "iranienne", "iraniano", "iraniana", "pt"),
    ("IS", "Icelandic", "islandais", "islandaise", "islandês", "islandesa", "pt"),
    ("IT", "Italian", "italien", "italienne", "italiano", "italiana", "pt"),
    ("JE", "", "jersiais", "jersiaise", "", "", "fr"),
    ("JM", "Jamaican", "jamaïcain", "jamaïcaine", "jamaicano", "jamaicana", "pt"),
    ("JO", "Jordanian", "jordanien", "jordanienne", "jordano", "jordana", "pt"),
    ("JP", "Japanese", "japonais", "japonaise", "japonês", "japonesa", "pt"),
    ("KE", "Kenyan", "kényan", "kényane", "queniano", "queniana", "pt"),
    ("KG", "Kyrgyz", "kirghiz", "kirghize", "quirguiz", "quirguiza", "pt"),
    ("KH", "Cambodian", "cambodgien", "cambodgienne", "cambojano", "cambojana", "pt"),
    ("KI", "I-Kiribati", "kiribatien", "kiribatienne", "quiribatiano", "quiribatiana", "fr,pt"),
    ("KM", "Comorian", "comorien", "comorienne", "comorense", "comorense", "pt"),
    (
        "KN",
        "Kittitian and Nevisian",
        "kittitien et névicien",
        "kittitienne et névicienne",
        "são-cristovense",
        "são-cristovense",
        "en,fr,pt",
    ),
    ("KP", "North Korean", "nord-coréen", "nord-coréenne", "norte-coreano", "norte-coreana", "pt"),
    ("KR", "South Korean", "sud-coréen", "sud-coréenne", "sul-coreano", "sul-coreana", "pt"),
    ("KW", "Kuwaiti", "koweïtien", "koweïtienne", "cuaitiano", "cuaitiana", "pt"),
    ("KY", "Caymanian", "caïmanien", "caïmanienne", "caimanense", "caimanense", "fr,pt"),
    ("KZ", "Kazakh", "kazakh", "kazakhe", "cazaque", "cazaque", "fr,pt"),
    ("LA", "Laotian", "laotien", "laotienne", "laosiano", "laosiana", "pt"),
    ("LB", "Lebanese", "libanais", "libanaise", "libanês", "libanesa", "pt"),
    (
        "LC",
        "Saint Lucian",
        "saint-lucien",
        "saint-lucienne",
        "santa-lucense",
        "santa-lucense",
        "fr,pt",
    ),
    (
        "LI",
        "Liechtensteiner",
        "liechtensteinois",
        "liechtensteinoise",
        "listenstaino",
        "listenstaina",
        "pt",
    ),
    ("LK", "Sri Lankan", "sri-lankais", "sri-lankaise", "cingalês", "cingalesa", "fr,pt"),
    ("LR", "Liberian", "libérien", "libérienne", "liberiano", "liberiana", "pt"),
    ("LS", "Basotho", "lesothan", "lesothane", "lesotiano", "lesotiana", "fr,pt"),
    ("LT", "Lithuanian", "lituanien", "lituanienne", "lituano", "lituana", "pt"),
    (
        "LU",
        "Luxembourger",
        "luxembourgeois",
        "luxembourgeoise",
        "luxemburguês",
        "luxemburguesa",
        "pt",
    ),
    ("LV", "Latvian", "letton", "lettone", "letão", "letã", "fr,pt"),
    ("LY", "Libyan", "libyen", "libyenne", "líbio", "líbia", "pt"),
    ("MA", "Moroccan", "marocain", "marocaine", "marroquino", "marroquina", "pt"),
    ("MC", "Monégasque", "monégasque", "monégasque", "monegasco", "monegasca", "pt"),
    ("MD", "Moldovan", "moldave", "moldave", "moldavo", "moldava", "pt"),
    ("ME", "Montenegrin", "monténégrin", "monténégrine", "montenegrino", "montenegrina", "pt"),
    ("MF", "", "", "", "", "", ""),
    ("MG", "Malagasy", "malgache", "malgache", "malgaxe", "malgaxe", "pt"),
    ("MH", "Marshallese", "marshallais", "marshallaise", "marshalês", "marshalesa", "pt"),
    ("MK", "North Macedonian", "macédonien", "macédonienne", "macedónio", "macedónia", "en,pt"),
    ("ML", "Malian", "malien", "malienne", "maliano", "maliana", "pt"),
    ("MM", "Burmese", "birman", "birmane", "birmanês", "birmanesa", "pt"),
    ("MN", "Mongolian", "mongol", "mongole", "mongol", "mongol", "pt"),
    ("MO", "Macanese", "macanais", "macanaise", "macaense", "macaense", "fr"),
    ("MP", "Northern Mariana Islander", "", "", "", "", ""),
    ("MQ", "Martinican", "martiniquais", "martiniquaise", "martinicano", "martinicana", "en,fr,pt"),
    ("MR", "Mauritanian", "mauritanien", "mauritanienne", "mauritano", "mauritana", "pt"),
    (
        "MS",
        "Montserratian",
        "montserratien",
        "montserratienne",
        "monserratino",
        "monserratina",
        "fr,pt",
    ),
    ("MT", "Maltese", "maltais", "maltaise", "maltês", "maltesa", "pt"),
    ("MU", "Mauritian", "mauricien", "mauricienne", "mauriciano", "mauriciana", "pt"),
    ("MV", "Maldivian", "maldivien", "maldivienne", "maldivo", "maldiva", "pt"),
    ("MW", "Malawian", "malawite", "malawite", "malauiano", "malauiana", "pt"),
    ("MX", "Mexican", "mexicain", "mexicaine", "mexicano", "mexicana", "pt"),
    ("MY", "Malaysian", "malaisien", "malaisienne", "malaio", "malaia", "pt"),
    ("MZ", "Mozambican", "mozambicain", "mozambicaine", "moçambicano", "moçambicana", "pt"),
    ("NA", "Namibian", "namibien", "namibienne", "namibiano", "namibiana", "pt"),
    (
        "NC",
        "New Caledonian",
        "néo-calédonien",
        "néo-calédonienne",
        "neocaledónio",
        "neocaledónia",
        "pt",
    ),
    ("NE", "Nigerien", "nigérien", "nigérienne", "nigerino", "nigerina", "pt"),
    ("NF", "Norfolk Islander", "", "", "", "", ""),
    ("NG", "Nigerian", "nigérian", "nigériane", "nigeriano", "nigeriana", "pt"),
    ("NI", "Nicaraguan", "nicaraguayen", "nicaraguayenne", "nicaraguense", "nicaraguense", "pt"),
    ("NL", "Dutch", "néerlandais", "néerlandaise", "neerlandês", "neerlandesa", "fr,pt"),
    ("NO", "Norwegian", "norvégien", "norvégienne", "norueguês", "norueguesa", "pt"),
    ("NP", "Nepalese", "népalais", "népalaise", "nepalês", "nepalesa", "pt"),
    ("NR", "Nauruan", "nauruan", "nauruane", "nauruano", "nauruana", "pt"),
    ("NU", "Niuean", "niuéen", "niuéenne", "niuano", "niuana", "fr,pt"),
    (
        "NZ",
        "New Zealander",
        "néo-zélandais",
        "néo-zélandaise",
        "neozelandês",
        "neozelandesa",
        "fr,pt",
    ),
    ("OM", "Omani", "omanais", "omanaise", "omanense", "omanense", "pt"),
    ("PA", "Panamanian", "panaméen", "panaméenne", "panamenho", "panamenha", ""),
    ("PE", "Peruvian", "péruvien", "péruvienne", "peruano", "peruana", "pt"),
    ("PF", "French Polynesian", "polynésien", "polynésienne", "polinésio", "polinésia", "pt"),
    (
        "PG",
        "Papua New Guinean",
        "papou-néo-guinéen",
        "papou-néo-guinéenne",
        "papuásio",
        "papuásia",
        "fr,pt",
    ),
    ("PH", "Filipino", "philippin", "philippine", "filipino", "filipina", "pt"),
    ("PK", "Pakistani", "pakistanais", "pakistanaise", "paquistanês", "paquistanesa", "pt"),
    ("PL", "Polish", "polonais", "polonaise", "polaco", "polaca", ""),
    ("PM", "", "saint-pierrais", "saint-pierraise", "", "", "fr"),
    ("PR", "Puerto Rican", "portoricain", "portoricaine", "porto-riquenho", "porto-riquenha", "pt"),
    ("PS", "Palestinian", "palestinien", "palestinienne", "palestiniano", "palestiniana", "pt"),
    ("PT", "Portuguese", "portugais", "portugaise", "português", "portuguesa", ""),
    ("PW", "Palauan", "palaosien", "palaosienne", "palauano", "palauana", "fr,pt"),
    ("PY", "Paraguayan", "paraguayen", "paraguayenne", "paraguaio", "paraguaia", "pt"),
    ("QA", "Qatari", "qatarien", "qatarienne", "catariano", "catariana", "pt"),
    ("RE", "Réunionese", "réunionnais", "réunionnaise", "reunionense", "reunionense", "en,fr,pt"),
    ("RO", "Romanian", "roumain", "roumaine", "romeno", "romena", "pt"),
    ("RS", "Serbian", "serbe", "serbe", "sérvio", "sérvia", "pt"),
    ("RU", "Russian", "russe", "russe", "russo", "russa", "pt"),
    ("RW", "Rwandan", "rwandais", "rwandaise", "ruandês", "ruandesa", "pt"),
    ("SA", "Saudi", "saoudien", "saoudienne", "saudita", "saudita", "pt"),
    ("SB", "Solomon Islander", "salomonais", "salomonaise", "salomonense", "salomonense", "pt"),
    ("SC", "Seychellois", "seychellois", "seychelloise", "seichelense", "seichelense", "pt"),
    ("SD", "Sudanese", "soudanais", "soudanaise", "sudanês", "sudanesa", "pt"),
    ("SE", "Swedish", "suédois", "suédoise", "sueco", "sueca", "pt"),
    ("SG", "Singaporean", "singapourien", "singapourienne", "singapurense", "singapurense", "pt"),
    (
        "SH",
        "Saint Helenian",
        "sainte-hélénien",
        "sainte-hélénienne",
        "santa-helenense",
        "santa-helenense",
        "en,fr,pt",
    ),
    ("SI", "Slovenian", "slovène", "slovène", "esloveno", "eslovena", "pt"),
    ("SJ", "", "", "", "", "", ""),
    ("SK", "Slovak", "slovaque", "slovaque", "eslovaco", "eslovaca", "pt"),
    (
        "SL",
        "Sierra Leonean",
        "sierra-léonais",
        "sierra-léonaise",
        "serra-leonês",
        "serra-leonesa",
        "fr,pt",
    ),
    (
        "SM",
        "Sammarinese",
        "saint-marinais",
        "saint-marinaise",
        "sanmarinense",
        "sanmarinense",
        "pt",
    ),
    ("SN", "Senegalese", "sénégalais", "sénégalaise", "senegalês", "senegalesa", "pt"),
    ("SO", "Somali", "somalien", "somalienne", "somali", "somali", "pt"),
    ("SR", "Surinamese", "surinamien", "surinamienne", "surinamês", "surinamesa", "pt"),
    ("SS", "South Sudanese", "sud-soudanais", "sud-soudanaise", "sul-sudanês", "sul-sudanesa", ""),
    ("ST", "São Toméan", "santoméen", "santoméenne", "são-tomense", "são-tomense", "pt"),
    ("SV", "Salvadoran", "salvadorien", "salvadorienne", "salvadorenho", "salvadorenha", "pt"),
    ("SX", "Sint Maartener", "", "", "", "", "en"),
    ("SY", "Syrian", "syrien", "syrienne", "sírio", "síria", "pt"),
    ("SZ", "Swazi", "swazi", "swazie", "suazi", "suazi", "fr,pt"),
    ("TC", "Turks and Caicos Islander", "", "", "", "", ""),
    ("TD", "Chadian", "tchadien", "tchadienne", "chadiano", "chadiana", "pt"),
    ("TG", "Togolese", "togolais", "togolaise", "togolês", "togolesa", "pt"),
    ("TH", "Thai", "thaïlandais", "thaïlandaise", "tailandês", "tailandesa", "pt"),
    ("TJ", "Tajik", "tadjik", "tadjike", "tajique", "tajique", "en,pt"),
    ("TK", "Tokelauan", "tokélaouan", "tokélaouane", "toquelauano", "toquelauana", "fr,pt"),
    ("TL", "Timorese", "timorais", "timoraise", "timorense", "timorense", "en,pt"),
    ("TM", "Turkmen", "turkmène", "turkmène", "turcomeno", "turcomena", "pt"),
    ("TN", "Tunisian", "tunisien", "tunisienne", "tunisino", "tunisina", "pt"),
    ("TO", "Tongan", "tongien", "tongienne", "tonganês", "tonganesa", "pt"),
    ("TR", "Turkish", "turc", "turque", "turco", "turca", "pt"),
    (
        "TT",
        "Trinidadian and Tobagonian",
        "trinidadien",
        "trinidadienne",
        "trinitino-tobaguense",
        "trinitino-tobaguense",
        "en,pt",
    ),
    ("TV", "Tuvaluan", "tuvaluan", "tuvaluane", "tuvaluano", "tuvaluana", "fr,pt"),
    ("TW", "Taiwanese", "taïwanais", "taïwanaise", "taiwanês", "taiwanesa", "pt"),
    ("TZ", "Tanzanian", "tanzanien", "tanzanienne", "tanzaniano", "tanzaniana", "pt"),
    ("UA", "Ukrainian", "ukrainien", "ukrainienne", "ucraniano", "ucraniana", "pt"),
    ("UG", "Ugandan", "ougandais", "ougandaise", "ugandês", "ugandesa", "pt"),
    ("US", "American", "américain", "américaine", "americano", "americana", ""),
    ("UY", "Uruguayan", "uruguayen", "uruguayenne", "uruguaio", "uruguaia", "pt"),
    ("UZ", "Uzbek", "ouzbek", "ouzbèke", "usbeque", "usbeque", "fr,pt"),
    ("VA", "Vatican", "vaticane", "vaticane", "vaticano", "vaticana", "fr,pt"),
    ("VC", "Vincentian", "vincentin", "vincentine", "são-vicentino", "são-vicentina", "fr,pt"),
    ("VE", "Venezuelan", "vénézuélien", "vénézuélienne", "venezuelano", "venezuelana", "pt"),
    ("VG", "", "", "", "", "", ""),
    ("VI", "", "", "", "", "", ""),
    ("VN", "Vietnamese", "vietnamien", "vietnamienne", "vietnamita", "vietnamita", "pt"),
    ("VU", "Vanuatuan", "vanuatuan", "vanuatuane", "vanuatuense", "vanuatuense", "fr,pt"),
    ("WF", "Wallis and Futuna Islander", "wallisien", "wallisienne", "", "", "en,fr"),
    ("WS", "Samoan", "samoan", "samoane", "samoano", "samoana", "fr,pt"),
    ("XK", "Kosovar", "kosovar", "kosovare", "kosovar", "kosovar", "pt"),
    ("YE", "Yemeni", "yéménite", "yéménite", "iemenita", "iemenita", "pt"),
    ("YT", "Mahorais", "mahorais", "mahoraise", "maiotense", "maiotense", "en,fr,pt"),
    ("ZA", "South African", "sud-africain", "sud-africaine", "sul-africano", "sul-africana", "pt"),
    ("ZM", "Zambian", "zambien", "zambienne", "zambiano", "zambiana", "pt"),
    ("ZW", "Zimbabwean", "zimbabwéen", "zimbabwéenne", "zimbabueano", "zimbabueana", "pt"),
)

_BY_CODE = {row[0]: row for row in ROWS}
_COLUMNS = {"en": (1, 1), "fr": (2, 3), "pt": (4, 5)}


def table_language(language: str) -> str:
    """Which table serves ``language``, or nothing: ``pt-PT`` and ``pt-BR`` both take
    Portuguese's, ``fr-CA`` French's, and a language with none takes none."""
    return languages.match(language or "", LANGUAGES) or ""


def demonym(code: str, language: str, *, feminine: bool = False) -> str:
    """The word for a person of the country ``code`` in ``language``, or nothing where there
    is none (the table has no language, or has no word for that country in it)."""
    own = table_language(language)
    row = _BY_CODE.get((code or "").upper())
    if not own or not row:
        return ""
    masculine, female = _COLUMNS[own]
    return row[female if feminine else masculine]


def is_feminine(gender: str) -> bool:
    """Whether a person's gender, as the profile holds it, asks for the feminine form: the
    concept ``female``, and nothing else (#864, #865, #921).

    A profile not yet migrated, an archive and a candidate file may still hold the word some
    language's list had for it -- *Féminin*, or *Woman* from before #864 -- and
    `addressing.concept_of` reads those as the concept, so they are honoured too."""
    from postulo.accounts import addressing

    return addressing.concept_of(addressing.GENDER, gender) == "female"


def nationality(code: str, language: str, gender: str = "") -> str:
    """The nationality to print for ``code``: the word, else the country's name as before."""
    country = phones.BY_CODE.get((code or "").upper())
    return demonym(code, language, feminine=is_feminine(gender)) or (country[2] if country else "")


def nationalities(codes, language: str, gender: str = "") -> list[str]:
    """The nationalities to print for a list of codes, in the order held; a code the country
    list lacks is left out, as `personal.country_names` always did."""
    return [
        nationality(code, language, gender)
        for code in codes or []
        if (code or "").upper() in phones.BY_CODE
    ]


def menu_label(code: str, language: str, gender: str = "") -> str:
    """What the nationality menu shows for ``code``: *Portuguese (Portugal)*, so that it can be
    found by either, or the country's name alone where there is no word to put before it."""
    country = phones.BY_CODE.get((code or "").upper())
    name = country[2] if country else str(code)
    word = demonym(code, language, feminine=is_feminine(gender))
    return f"{word} ({name})" if word else name


def choices(language: str, gender: str = "") -> list[tuple[str, str]]:
    """Every country as a menu choice, labelled by `menu_label` and in the order those read."""
    return sorted(
        ((code, menu_label(code, language, gender)) for code, _prefix, _name in phones.COUNTRIES),
        key=lambda row: row[1].casefold(),
    )
