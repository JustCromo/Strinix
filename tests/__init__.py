"""I test non toccano mai i file veri dell'utente: configurazione e stato vanno in una cartella temporanea."""

import atexit
import os
import shutil
import tempfile

_CASA_FINTA = tempfile.mkdtemp(prefix="strinix-test-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_CASA_FINTA, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(_CASA_FINTA, "state")
atexit.register(shutil.rmtree, _CASA_FINTA, True)
