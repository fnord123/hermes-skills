#!/usr/bin/env bash
# fetch-weather.sh — Fetch a 2-day forecast as plain text from the
# US National Weather Service.
#
# Usage:
#   bash fetch-weather.sh
#
# Output (one line per day):
#   Today: Sunny high 75F low 50F, rain 0%
#   Tomorrow: Partly cloudy high 72F low 48F, rain 20%
#
# Environment:
#   NWS_GRIDPOINT — required, format <OFFICE>/<X>,<Y>. Find yours at:
#     curl -s 'https://api.weather.gov/points/<lat>,<lon>' | jq -r .properties.forecastGridData
#     The response URL ends with /gridpoints/<OFFICE>/<X>,<Y> — that's the value.
#   NWS_USER_AGENT — optional, the User-Agent the NWS API requires. Defaults
#     to "hermes-skills/daily-briefing"; NWS recommends including a contact
#     email or URL so they can reach you if your client misbehaves.
#
# Requires: curl, jq
# Note: NWS only covers US locations. For non-US, swap this script for one
# of the open-meteo / openweathermap variants and emit the same one-line
# format.
set -euo pipefail

for arg in "$@"; do
  case "$arg" in
    --help)
      awk 'NR==1{next} /^#/{sub(/^#[[:space:]]?/,""); print; next} /^[^#[:space:]]/{exit}' "$0"
      exit 0 ;;
    *)
      echo "ERROR: Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

GRIDPOINT="${NWS_GRIDPOINT:-}"
if [[ -z "$GRIDPOINT" ]]; then
  echo "ERROR: NWS_GRIDPOINT not set in .env (e.g. NWS_GRIDPOINT=PQR/113,104)" >&2
  exit 1
fi

USER_AGENT="${NWS_USER_AGENT:-hermes-skills/daily-briefing}"

DATA=$(curl -s -m 10 -H "User-Agent: ${USER_AGENT}" \
    "https://api.weather.gov/gridpoints/${GRIDPOINT}/forecast" 2>/dev/null) \
    || { echo "Weather fetch failed"; exit 1; }

echo "$DATA" | jq -r '
  .properties.periods as $p |
  [range(0; ($p|length)-1)
    | . as $i
    | if $p[$i].isDaytime and ($p[$i+1].isDaytime == false) then
        {day: $p[$i], night: $p[$i+1]}
      else empty end] |
  .[0:2] | to_entries[] |
  (if .key == 0 then "Today" else "Tomorrow" end) as $label |
  .value as $entry |
  "\($label): \($entry.day.shortForecast) high \($entry.day.temperature)F low \($entry.night.temperature)F, rain \($entry.day.probabilityOfPrecipitation.value // 0)%"
'
