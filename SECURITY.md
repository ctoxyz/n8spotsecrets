# Security

n8spotsecrets reads public open data. It holds no user data, no accounts and no credentials.

## Reporting a problem

Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability").
Please do not open a public issue for a security problem.

## What never goes in this repo

- API keys, tokens, passwords, `.env` files, `.pem` / `.key` files. `.gitignore` blocks the common ones and CI fails on known key patterns.
- Anything about a deployment: account ids, bucket names, endpoints, server addresses.
- Data about private people. Test addresses are public buildings.

## Keys

The report needs no keys. The price check can use a free US Census key from your own environment
(`CENSUS_API_KEY`); without it that section is skipped. Never commit it.
