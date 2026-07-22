#!/usr/bin/env bash
set -euo pipefail

: "${SONAR_HOST_URL:?SONAR_HOST_URL is required}"
: "${SONAR_TOKEN:?SONAR_TOKEN is required}"
: "${SONAR_PROJECT_KEY:?SONAR_PROJECT_KEY is required}"

scanner_version="7.2.0.5079"
scanner_archive="sonar-scanner-cli-${scanner_version}-linux-x64.zip"
scanner_base_url="https://repo1.maven.org/maven2/org/sonarsource/scanner/cli/sonar-scanner-cli/${scanner_version}"
scanner_dir=".sonar/scanner-${scanner_version}"
scanner_home="${scanner_dir}/sonar-scanner-${scanner_version}-linux-x64"

mkdir -p .sonar
if [[ ! -x "${scanner_home}/bin/sonar-scanner" ]]; then
  curl --fail --location --silent --show-error --output ".sonar/${scanner_archive}" "${scanner_base_url}/${scanner_archive}"
  curl --fail --location --silent --show-error --output ".sonar/${scanner_archive}.sha256" "${scanner_base_url}/${scanner_archive}.sha256"
  expected_checksum="$(tr -d '[:space:]' < ".sonar/${scanner_archive}.sha256")"
  if [[ ! "${expected_checksum}" =~ ^[0-9a-fA-F]{64}$ ]]; then
    echo "Malformed checksum file for ${scanner_archive}" >&2
    exit 1
  fi

  actual_checksum="$(sha256sum ".sonar/${scanner_archive}" | cut -d ' ' -f1)"
  if [[ "${actual_checksum}" != "${expected_checksum}" ]]; then
    echo "Checksum mismatch for ${scanner_archive}" >&2
    exit 1
  fi

  rm -rf "$scanner_dir"
  mkdir -p "$scanner_dir"
  unzip -q ".sonar/${scanner_archive}" -d "$scanner_dir"
fi

project_version="${SONAR_PROJECT_VERSION:-$(python -c 'import kosui_forge; print(kosui_forge.__version__)')}"
"${scanner_home}/bin/sonar-scanner" \
  -Dsonar.projectKey="${SONAR_PROJECT_KEY}" \
  -Dsonar.projectVersion="${project_version}" \
  -Dsonar.qualitygate.wait=true \
  -Dsonar.qualitygate.timeout=300
