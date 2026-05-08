#!/bin/bash
set -euo pipefail

PKGLIST_DIR="$HOME/.config/pkglists"
HOST=$(hostnamectl hostname)

mkdir -p "$PKGLIST_DIR"
pacman -Qqen > "$PKGLIST_DIR/${HOST}-native.txt"
pacman -Qqem > "$PKGLIST_DIR/${HOST}-aur.txt"

echo "Updated pkglists for $HOST in $PKGLIST_DIR"
