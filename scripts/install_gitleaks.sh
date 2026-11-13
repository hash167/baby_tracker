#!/bin/sh
# Install pinned gitleaks. Verifies the release checksum. No secrets.
set -eu

VERSION=8.30.1

os="$(uname -s)"
arch="$(uname -m)"
case "${os}-${arch}" in
  Linux-x86_64)
    asset="gitleaks_${VERSION}_linux_x64.tar.gz"
    sha256="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
    ;;
  Linux-aarch64 | Linux-arm64)
    asset="gitleaks_${VERSION}_linux_arm64.tar.gz"
    sha256="e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080"
    ;;
  Darwin-arm64)
    asset="gitleaks_${VERSION}_darwin_arm64.tar.gz"
    sha256="b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5"
    ;;
  Darwin-x86_64)
    asset="gitleaks_${VERSION}_darwin_x64.tar.gz"
    sha256="dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709"
    ;;
  *)
    echo "unsupported platform: ${os}-${arch}" >&2
    exit 2
    ;;
esac

if command -v gitleaks >/dev/null 2>&1; then
  current="$(gitleaks version 2>/dev/null || true)"
  if [ "$current" = "$VERSION" ]; then
    exit 0
  fi
fi

dest="${GITLEAKS_INSTALL_DIR:-${HOME}/.local/bin}"
mkdir -p "$dest"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
url="https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}/${asset}"
curl -fsSL -o "${tmp}/gitleaks.tar.gz" "$url"
echo "${sha256}  ${tmp}/gitleaks.tar.gz" | sha256sum -c -
tar -xzf "${tmp}/gitleaks.tar.gz" -C "$tmp" gitleaks
install -m 0755 "${tmp}/gitleaks" "${dest}/gitleaks"

if [ -n "${GITHUB_PATH:-}" ]; then
  echo "$dest" >> "$GITHUB_PATH"
fi

echo "installed gitleaks ${VERSION} to ${dest}/gitleaks"
