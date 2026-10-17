# n8spotsecrets

![n8spotsecrets field report](docs/field-report.png)

**Ask the address.** A rental listing is an ad. n8spotsecrets is the other half:
what has happened at and around that address, pulled from free public data.
No keys, no account, no login.

Built in Los Angeles for the rentals section of [n8dog.com](https://n8dog.com/rentals/).

## Status: prototype

The reports in this repo are hand-built mockups for three downtown Los Angeles
addresses. They show what the finished report looks like. The Python package
that builds one from live data is the next thing to land here.

## What is in the folder

| path | what it is |
|---|---|
| `living-insider-548-spring.html` | prototype report, 548 S Spring St |
| `living-insider-671-macarthur.html` | prototype report, MacArthur Park |
| `living-insider-midnight-mission.html` | prototype report, Skid Row |
| `raw/` | the raw versions of the same three reports |
| `schema-skidrow.md`, `schema-westside.md` | the data model, two neighborhoods |
| `json_snippet.json` | a sample of the report data shape |
| `docs/` | images for this page |

Open any of the HTML files in a browser. That is the whole demo.

## What a report covers

| section | what it answers | source, free and keyless |
|---|---|---|
| Safety | crimes within 500 m, last 90 days, by type | LA City open data |
| Noise | distance to freeways and rail, bars and clubs within 250 m | OpenStreetMap |
| Building | year built, units, permits, open code cases, rent-stabilized or not | LA County assessor, LADBS, LAHD |
| Complaints | 311 requests at the address, last 12 months | MyLA311 |
| Hazards | flood zone, fire severity zone, fault zone | FEMA, CAL FIRE, CA Geological Survey |
| Price check | asking rent vs the tract median | Census ACS |
| Walkability | grocery, transit, pharmacy within 800 m | OpenStreetMap |

## Why not just read the listing

The listing is written by the person who wants your deposit. It has the
photos. It does not have the code enforcement cases, the freeway 90 m away,
or the fact that the building is rent-stabilized and the ad forgot to say so.
n8spotsecrets prints that part. The listing wins on photos. We print those too.

## How it compares

![n8spotsecrets vs the majors](docs/compare.png)

Read off each product's own site in October 2026. DwellCheck is the closest
in depth and covers New York City. Lucid Rents covers Los Angeles with RSO,
REAP and violations, and zip-level crime. RentSift covers the DC area. The
listing sites carry hazard and noise scores on the listing page. None of them
publish their code. n8spotsecrets is the Los Angeles report, with the geometry
(crime radius, noise, hazard zones, price vs the tract), open source, and
placed on the rental card instead of on its own site.

## Rules of the road

- No API keys, credentials or paid services. Every source stays public and keyless.
- Be polite to the open data portals: cache, rate-limit, identify the project in the user agent.
- Informational only. Not a tenant screening report and not legal advice.

## Add your city

One file per data source. If your city publishes crime, permits, 311 and
parcel data, it can have a report. Open an issue with the dataset links.

## License

MIT. See LICENSE.
