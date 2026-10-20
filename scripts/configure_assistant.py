"""Run with DATABASE_URL loaded. Save generated local reader credentials in .env."""
import os
from pathlib import Path

from careflow.reader import configure_reader


def main():
    path = Path(".env")
    existing = os.getenv("CAREFLOW_ASSISTANT_DATABASE_URL")
    content = path.read_text() if path.exists() else ""
    if not existing and any(line.startswith("CAREFLOW_ASSISTANT_DATABASE_URL=") for line in content.splitlines()):
        raise SystemExit("Load the existing .env before configuring the assistant; credentials were preserved")
    url = configure_reader(os.environ["DATABASE_URL"],
                           os.getenv("CAREFLOW_ANALYTICS_SCHEMA", "analytics"), existing_url=existing)
    if not existing:
        with path.open("a") as stream:
            stream.write("\nCAREFLOW_ASSISTANT_DATABASE_URL=" + url + "\n")
        path.chmod(0o600)
    print("Reader permissions configured; local credentials preserved in ignored .env")


if __name__ == "__main__":
    main()
