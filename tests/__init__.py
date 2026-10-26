import os

# The tests never ask GitHub for a newer program version (hmaps/api/appupdate.py): an unreachable
# local address fails at once. Tests of the app update set their own fake release server.
os.environ.setdefault("HMAPS_APP_MANIFEST", "http://127.0.0.1:9/app-manifest.json")
