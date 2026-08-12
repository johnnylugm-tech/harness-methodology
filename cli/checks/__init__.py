"""The check commands, one module per question they ask.

cli/check_cmds.py held 24 `cmd_*` functions and a 295-line `register()`.
R49-B moved the functions here in six families and left check_cmds.py as the
CLI wiring that imports and registers them — so a command gaining a flag
touches its family and the wiring, not a file the other 23 also live in.

Deliberately empty of code. The families share nothing but stdlib and a few
core imports (measured before the split: no command depended on another
except `cmd_verify_gate`, which calls two that travelled with it), so a
shared base here would be a place for coupling to accumulate rather than a
place that removes any.
"""
