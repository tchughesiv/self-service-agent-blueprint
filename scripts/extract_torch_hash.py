#!/usr/bin/env python3
"""Extract torch hash from uv.lock for Linux x86_64 wheel."""
import platform
import re
import subprocess
import sys


def compute_url_hash(wheel_url):
    """Compute SHA256 hash by streaming URL directly to hashing tool.

    Uses curl to stream the download directly to sha256sum/shasum,
    avoiding any temporary files or memory usage.
    """
    # Use sha256sum on Linux, shasum -a 256 on macOS
    if platform.system() == "Darwin":
        # macOS: curl | shasum -a 256
        # Use shell=True to allow pipe
        result = subprocess.run(
            f'curl -sSL "{wheel_url}" | shasum -a 256',
            shell=True,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for large downloads
        )
        if result.returncode == 0:
            # shasum outputs: <hash>  -
            hash_value = result.stdout.split()[0]
            return f"sha256:{hash_value}"
    else:
        # Linux: curl | sha256sum
        # Use shell=True to allow pipe
        result = subprocess.run(
            f'curl -sSL "{wheel_url}" | sha256sum',
            shell=True,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for large downloads
        )
        if result.returncode == 0:
            # sha256sum outputs: <hash>  -
            hash_value = result.stdout.split()[0]
            return f"sha256:{hash_value}"
    return None


def extract_torch_hash(lockfile_path, python_version):
    """Extract hash for torch+cpu wheel matching the Python version.

    Note: uv.lock doesn't include hashes for wheels from custom indexes.
    We always download the Linux x86_64 wheel directly (regardless of local platform)
    since we need the hash for Docker containers. We stream the download directly
    to a hashing tool using curl, avoiding any temporary files or memory usage.
    """
    try:
        with open(lockfile_path, "r") as f:
            content = f.read()

        # Extract wheel URL from lockfile (hashes aren't stored for custom index wheels)
        url_pattern = (
            r'url = "https://download\.pytorch\.org/whl/cpu/torch-([\d.]+)%2Bcpu-cp'
            + python_version
            + r"-cp"
            + python_version
            + r'-manylinux_2_28_x86_64\.whl"'
        )

        url_match = re.search(url_pattern, content)
        if url_match:
            version = url_match.group(1)
            wheel_url = f"https://download.pytorch.org/whl/cpu/torch-{version}%2Bcpu-cp{python_version}-cp{python_version}-manylinux_2_28_x86_64.whl"

            # Verify URL exists remotely with HEAD request
            head_result = subprocess.run(
                ["curl", "-sSL", "-I", "-f", wheel_url],
                capture_output=True,
                timeout=30,
            )
            if head_result.returncode != 0:
                # URL doesn't exist or isn't accessible
                return ""

            # Stream download directly to hashing tool
            # This avoids any temporary files or memory usage
            return compute_url_hash(wheel_url)
    except Exception:
        pass

    return ""


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(1)

    lockfile_path = sys.argv[1]
    python_version = sys.argv[2]
    hash_value = extract_torch_hash(lockfile_path, python_version)
    if hash_value:
        print(hash_value)
    else:
        sys.exit(1)
