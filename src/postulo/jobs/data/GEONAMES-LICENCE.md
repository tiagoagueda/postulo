# GeoNames — the city table the map resolves a location from (#108)

`data/geonames-cities1000.txt` and `data/geonames-countryinfo.txt` are the "cities
above a thousand" table and the country table, published by
[GeoNames](https://www.geonames.org) and downloaded in place by
`manage.py fetch_geonames`, the way the ESCO classification is. They are not
committed: a table of a few megabytes is reference data, not a repository, and a
fresh download is how it is replaced.

**The terms they are carried on are the GeoNames database licence:**
[Creative Commons Attribution 3.0 Unported](https://creativecommons.org/licenses/by/3.0/)
(https://download.geonames.org/export/dump/). The attribution the licence asks for is
this note, kept beside the files, and the line in `THIRD-PARTY.md` beside it. The
cities are matched against when a company's location is saved, and nothing in a
request path sends any part of a person's record to geonames.org.

The world outline the map draws under the points — `src/postulo/static/map/` — is
[Natural Earth](https://www.naturalearthdata.com), public domain: a note beside it
says so, and `THIRD-PARTY.md` says the same.
