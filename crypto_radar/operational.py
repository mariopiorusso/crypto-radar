import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from contextlib import contextmanager


def setup_logging(cfg):
    path = Path(cfg["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(), RotatingFileHandler(path, maxBytes=cfg["max_bytes"],
                backupCount=cfg["backup_count"], encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers, force=True)
    # HTTP client logs may include Telegram bot URLs; keep transport logging disabled.
    for name in ("httpx", "httpx2", "httpcore", "urllib3", "openai"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


@contextmanager
def process_lock(database_path):
    import os
    path = Path(str(database_path) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
