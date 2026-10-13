"""Check that both Claude Line servers are answering."""
import sys
import urllib.request

SERVERS = {
    "voice line": "http://localhost:8700",
    "terminal": "http://localhost:8701",
}

# The Mac routes traffic through a proxy; local calls must skip it.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_up(url: str) -> bool:
    try:
        with opener.open(url, timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


def main() -> int:
    down = []
    for name, url in SERVERS.items():
        up = is_up(url)
        print(f"{name:<12} {url}  {'up' if up else 'DOWN'}")
        if not up:
            down.append(name)
    return 1 if down else 0


if __name__ == "__main__":
    sys.exit(main())
