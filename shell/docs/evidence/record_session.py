"""Record a real O-jarvis Shell session, as the transcripts in terminal/ were recorded.

Each line given after the folder is typed at the prompt; every plan Jarvis proposes is
approved with "o". The output is kept as is (run with NO_COLOR, so no colors).

    python3 record_session.py ~/demo-jarvis out.txt "How much disk space is left?" exit

Needs pexpect (`sudo apt install python3-pexpect`) and o-jarvis-shell on the PATH, or its
path in OJARVIS_SHELL.
"""

import os
import shutil
import sys

import pexpect

PROMPT = "❯ "
APPROVAL = "Valider \\?.*› "


def record(folder: str, out: str, lines: list[str]) -> None:
    shell = os.environ.get("OJARVIS_SHELL") or shutil.which("o-jarvis-shell")
    if shell is None:
        sys.exit("o-jarvis-shell not found: install the .deb or set OJARVIS_SHELL.")
    env = dict(os.environ, NO_COLOR="1", COLUMNS="96", LINES="50")
    child = pexpect.spawn(
        shell, cwd=folder, env=env, encoding="utf-8", timeout=300, dimensions=(50, 96)
    )
    with open(out, "w", encoding="utf-8") as log:
        child.logfile_read = log
        child.expect(PROMPT)
        for line in lines:
            child.sendline(line)
            if line == "exit":
                child.expect(pexpect.EOF)
                return
            while child.expect([PROMPT, APPROVAL]) == 1:
                child.sendline("o")


if __name__ == "__main__":
    record(sys.argv[1], sys.argv[2], sys.argv[3:])
