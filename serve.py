# /// script
# requires-python = ">=3.14"
# dependencies = ["vgi-python[http]>=0.55.2,<0.56", "httpx>=0.27,<1", "pyarrow>=18,<26"]
# ///
"""HTTP entry point for the NCAA college-football VGI worker."""

from vgi_ncaa_football.worker import main_http

if __name__ == "__main__":
    main_http()
