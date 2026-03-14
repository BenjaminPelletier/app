#!/usr/bin/env bash

# This script is intended to be run on the host machine (not inside container), and generally during a CI run in GitHub
# Actions.

set -eo pipefail

# Make sure we're in a known working folder
OS=$(uname)
if [[ $OS == "Darwin" ]]; then
	# OSX uses BSD readlink
	BASEDIR="$(dirname "$0")"
else
	BASEDIR=$(readlink -e "$(dirname "$0")")
fi
cd "${BASEDIR}" || exit

# Build repo_hygiene image
docker image build . -t ropewiki/repo_hygiene

# Move down to overall repo and run
cd .. || exit
docker container run -v "$(pwd):/repo" ropewiki/repo_hygiene /repo
