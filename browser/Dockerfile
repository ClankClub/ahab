# The browser box: Chromium, driven by Playwright, for pages that need a real
# browser (JavaScript, clicking) rather than fetch_page's plain reading.
#
# Microsoft's Playwright image, which carries the browsers and everything they
# need; the Python package goes on top at the matching version. It runs as the
# image's unprivileged "pwuser", on a Docker network with no way out except
# its proxy (../workshop/proxy.py, in its any-public-site mode). How it's
# locked down is in ahab/boxes/browser.py; what the browser may do is in driver.py.

# Pinned so the browser behaves the same until we choose to move it. The
# image's browsers are built for this Playwright version; keep the two equal.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
ARG PLAYWRIGHT_VERSION=1.63.0

RUN pip install --no-cache-dir --break-system-packages "playwright==${PLAYWRIGHT_VERSION}"

COPY driver.py fence_probe.py /app/

USER pwuser
WORKDIR /tmp
CMD ["python", "-u", "/app/driver.py", "serve"]
