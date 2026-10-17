# Contributing

Use Python 3.12's standard library. Run `python3 -m unittest discover -s tests -v`
and `python3 scripts/verify-agent-build.py`. Coordinate report field or parser
changes with the receiving platform and its schema/intake tests.
Add sanitized fixture evidence for every metadata/parser change. Never commit
credentials, full user-data responses or customer host identifiers. Version the
schema/profile when compatibility or workload behavior changes. Explain duration,
resource bounds, cleanup and public/private field effects in a change request.
Real provider qualification needs a disposable VM, recorded versions, final
report and cleanup evidence. A synthetic fixture does not satisfy that gate.
