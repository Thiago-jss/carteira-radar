"""Exercise the running read-only API and Angular build using the trusted local URL."""

import json
from urllib.request import urlopen


def main():
    origin = "http://127.0.0.1:8084"

    def read(path):
        with urlopen(origin + path, timeout=20) as response:
            return response.read()

    assert json.loads(read("/health/ready"))["status"] == "ready"
    benchmark = json.loads(read("/api/v1/benchmark"))
    assert benchmark["provenance"]["rows"] > 0
    page = json.loads(read("/api/v1/predictions?limit=10"))
    assert page["total"] > 0 and page["predictions"]
    assert b"<radar-app" in read("/")
    print(f"PASS: {page['month']}; {page['total']} snapshots; HTML and API from same origin.")


if __name__ == "__main__":
    main()
