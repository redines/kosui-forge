#!/usr/bin/env bash
set -euo pipefail

: "${SONAR_HOST_URL:?SONAR_HOST_URL is required}"
: "${SONAR_TOKEN:?SONAR_TOKEN is required}"
: "${SONAR_PROJECT_KEY:?SONAR_PROJECT_KEY is required}"

scanner_version="7.2.0.5079"
scanner_archive="sonar-scanner-${scanner_version}-linux-x64.zip"
scanner_base_url="https://repo1.maven.org/maven2/org/sonarsource/scanner/cli/sonar-scanner-cli/${scanner_version}"
scanner_dir=".sonar/scanner-${scanner_version}"
scanner_home="${scanner_dir}/sonar-scanner-${scanner_version}-linux-x64"

mkdir -p .sonar
if [[ ! -x "${scanner_home}/bin/sonar-scanner" ]]; then
  curl --fail --location --silent --show-error --output ".sonar/${scanner_archive}" "${scanner_base_url}/${scanner_archive}"
  curl --fail --location --silent --show-error --output ".sonar/${scanner_archive}.sha256" "${scanner_base_url}/${scanner_archive}.sha256"
  (cd .sonar && sha256sum --check "${scanner_archive}.sha256")
  rm -rf "$scanner_dir"
  mkdir -p "$scanner_dir"
  unzip -q ".sonar/${scanner_archive}" -d "$scanner_dir"
fi

project_version="${SONAR_PROJECT_VERSION:-$(python -c 'import kosui_forge; print(kosui_forge.__version__)')}"
"${scanner_home}/bin/sonar-scanner" \
  -Dsonar.projectKey="${SONAR_PROJECT_KEY}" \
  -Dsonar.projectVersion="${project_version}" \
  -Dsonar.host.url="${SONAR_HOST_URL}" \
  -Dsonar.token="${SONAR_TOKEN}" \
  -Dsonar.qualitygate.wait=true \
  -Dsonar.qualitygate.timeout=300
