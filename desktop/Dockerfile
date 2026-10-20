# The desktop box: a Linux desktop of Ahab's own, for computer use.
#
# A virtual 1024x768 screen (Xvfb), XFCE, and a handful of apps, driven with
# xdotool. It runs as the unprivileged "pip", with no network unless the owner
# lists hosts (then only through its proxy, ../workshop/proxy.py). How it's
# locked down is in ahab/desktop.py; what a command can do is in driver.py.

FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb xauth dbus dbus-x11 x11-xserver-utils \
        xfwm4 xfce4-panel xfdesktop4 xfce4-settings xfconf thunar \
        xfce4-terminal mousepad ristretto galculator \
        firefox-esr libreoffice-writer libreoffice-calc libreoffice-gtk3 \
        xdotool wmctrl xclip imagemagick python3 python3-pil \
        fonts-dejavu fonts-liberation adwaita-icon-theme \
        ca-certificates curl tar procps less nano file \
        libnss3 libgbm1 libasound2 libsecret-1-0 \
    && rm -rf /var/lib/apt/lists/*

# Obsidian, pinned and checked, unpacked (AppImages want FUSE, which the box
# hasn't got). It runs with --no-sandbox: the box is the sandbox.
ARG OBSIDIAN_VERSION=1.14.4
ARG OBSIDIAN_SHA256=6362ddbeeeebb7bbccb48fae009572cf2284ef92f5c919c1332aba48de6ffeaa
RUN cd /tmp && curl -fsSLo obsidian.AppImage \
        "https://github.com/obsidianmd/obsidian-releases/releases/download/v${OBSIDIAN_VERSION}/Obsidian-${OBSIDIAN_VERSION}.AppImage" \
    && echo "${OBSIDIAN_SHA256}  obsidian.AppImage" | sha256sum -c - \
    && chmod +x obsidian.AppImage && ./obsidian.AppImage --appimage-extract >/dev/null \
    && mv squashfs-root /opt/obsidian && chmod -R a+rX /opt/obsidian && rm obsidian.AppImage

# The panel's default layout as the starting config, so there's no first-run
# question; Firefox without updates, telemetry or first-run pages.
RUN mkdir -p /etc/xdg/xfce4/xfconf/xfce-perchannel-xml \
    && cp /etc/xdg/xfce4/panel/default.xml /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml \
    && mkdir -p /usr/lib/firefox-esr/distribution \
    && dbus-uuidgen > /etc/machine-id
COPY policies.json /usr/lib/firefox-esr/distribution/policies.json

RUN useradd -m -u 1000 -s /bin/bash pip
COPY start.sh session.sh driver.py fence_probe.py /app/

USER pip
WORKDIR /home/pip
CMD ["sh", "/app/start.sh"]
