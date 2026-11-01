import os
import sys
import tempfile
from pathlib import Path

# isolate every test run in a throwaway data dir + SQLite DB, with no API keys
_tmp = Path(tempfile.mkdtemp(prefix="journal-test-"))
os.environ["DATA_DIR"] = str(_tmp)
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["GOOGLE_CLIENT_ID"] = ""
sys.path.insert(0, str(Path(__file__).parent))
