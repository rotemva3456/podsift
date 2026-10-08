# Security policy

## Reporting a vulnerability

Please report security issues through GitHub's private vulnerability reporting on this
repository (the "Security" tab -> "Report a vulnerability"), not as a public issue.
We'll acknowledge within a few days and let you know before any public disclosure.

If the issue is in PodFetch itself rather than something this fork added, please also
consider reporting it privately to [SamTV12345/PodFetch](https://github.com/SamTV12345/PodFetch)
the same way — a fix there reaches every PodFetch-based project, including this one.

Please don't publish anyone's personal email address or other contact details when
filing a report, here or upstream.

## Give accounts only to people you trust

PodFetch's server-side features trust signed-in users. **Only give accounts to people
you already trust**, the same way you would for any self-hosted app with a login.

## Scope

This is self-hosted software you run on your own server. We treat the following as
in scope for a report: a way to read or write another account's data through the
companion API, a way to escape the server-side download allow-list
(`companion/engine/net.py`'s `safe_fetch`, which already refuses loopback, private,
link-local and cloud-metadata addresses after DNS and after every redirect), a stored
or reflected script-injection path in the UI, or a way to see another user's AI
provider key. A feed you deliberately subscribe to fetching its own declared enclosure
URL, or a trusted admin account doing what an admin account can do, is not a
vulnerability by itself.

## Supported versions

Only the latest released version is supported with security fixes. There is no LTS
branch.
