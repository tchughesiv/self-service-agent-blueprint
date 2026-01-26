#!/usr/bin/env python3
"""Update torch hash in requirements.txt file."""
import re
import sys

if len(sys.argv) < 3:
    sys.exit(1)

req_file = sys.argv[1]
torch_hash = sys.argv[2]

with open(req_file, "r") as f:
    content = f.read()

# Remove any existing hash line after torch+cpu
content = re.sub(
    r"(torch==[0-9.]+\+cpu ; sys_platform != 'darwin')(\s*\\?\n)(\s+--hash=sha256:[a-f0-9]+\n)",
    r"\1\n",
    content,
)

# Add hash before comment line if present
content = re.sub(
    r"(torch==[0-9.]+\+cpu ; sys_platform != 'darwin')(\n)(\s+#)",
    r"\1 \\\n    --hash=" + torch_hash + r"\n\3",
    content,
)

# If still no hash (no comment matched), add it after torch line
torch_match = re.search(r"torch==[0-9.]+\+cpu.*\n.*\n", content)
if torch_match and "--hash=" not in torch_match.group(0):
    content = re.sub(
        r"(torch==[0-9.]+\+cpu ; sys_platform != 'darwin')(\n)",
        r"\1 \\\n    --hash=" + torch_hash + r"\n",
        content,
        count=1,
    )

with open(req_file, "w") as f:
    f.write(content)
